"""The three Phase 2 gate numbers, plus abstention, from one command.

`m2x eval rag` asks the thirty M2X-045 questions, scores the answers, and appends a row
carrying enough provenance to find the number again. Four figures come out and they are
**not** the same kind of claim, which is the thing to hold on to when reading them:

| metric | who computes it | how mechanical |
|---|---|---|
| context precision ≥0.75 | RAGAS, judge LLM | a model's opinion |
| faithfulness ≥0.80 | RAGAS, judge LLM | a model's opinion |
| citation accuracy ≥0.90 | **this module** | fully mechanical |
| abstention | **this module** | fully mechanical |

The two RAGAS metrics ask a language model to judge a language model. That is the standard
instrument and it is what the ticket asks for, but it is not measurement in the sense the
citation checker is: a judge that shares the answerer's weights shares its blind spots, and
here it *does* share them — both default to `meta-llama/Llama-3.1-8B-Instruct`, because the
project's zero-spend rule leaves no stronger free judge. So the bottom two rows are the
ones to trust when the four disagree, and the gate record must say which is which.

## Citation accuracy is ours on purpose

The ticket is explicit, and the reason is worth keeping: *"it must fail a citation whose
text merely sounds right but points at the wrong segment."* No similarity metric can do
that — sounding right is exactly what a similarity metric rewards. So a citation is correct
iff the chunk it names covers turns that **overlap** a ground-truth span for that question,
in the same meeting. Textual plausibility never enters.

The one subtlety is an off-by-one that would silently shift every score: `Chunk.segment_start`
is **1-based**, while an :class:`~m2x.rag_questions.EvidenceSpan` records **0-based** turn
indices into the reference transcript. :func:`chunk_turn_range` is the single place that
converts, and it is tested at both boundaries.

## Abstention is scored as correctness, not as a rate

The five must-abstain questions are correct only when the system abstains; the twenty-five
answerable ones are correct only when it does not. Reporting an undifferentiated abstention
*rate* would let a system that abstains on everything score 5/30 and look cautious rather
than useless, and one that never abstains look confident rather than reckless.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from m2x.adapter import ModelAdapter
from m2x.ask import DEFAULT_ASK_MODEL, DEFAULT_MAX_DISTANCE, DEFAULT_TOP_K, AskOutcome, ask
from m2x.indexing import chunk_segments
from m2x.rag_questions import (
    DEFAULT_RAG_EVAL_DIR,
    EXPECTED_DIRNAME,
    QUESTIONS_FILENAME,
    ExpectedAnswer,
    QuestionKind,
    RagQuestion,
    load_expected,
    load_questions,
)
from m2x.reference_transcript import DEFAULT_REFERENCE_DIR, load_reference_segments
from m2x.run_log import RunContext
from m2x.types import Provider
from m2x.vector_store import VectorStore, embed_texts

PHASE = "phase-2"
"""Run-log phase these calls are attributed to."""

DEFAULT_RAG_RESULTS_PATH = Path("eval/results/rag.jsonl")
"""Append-only record of every RAG eval run. Tracked, like the extraction results."""

DEFAULT_JUDGE_MODEL = DEFAULT_ASK_MODEL
"""Model RAGAS judges with.

The same weights that answer, which is a **known weakness** and not a preference. The
zero-spend rule leaves no stronger free judge, and a judge sharing the answerer's blind
spots inflates faithfulness in exactly the cases where the answerer is confidently wrong.
Recorded on every results row so a later run with a real judge is comparable.
"""

CONTEXT_PRECISION_FLOOR = 0.75
FAITHFULNESS_FLOOR = 0.80
CITATION_ACCURACY_FLOOR = 0.90
"""The M2X-050 gate thresholds, from the PRD. Here so the report can mark each leg."""


class QuestionScore(BaseModel):
    """What one question produced.

    Attributes:
        question_id: The question.
        kind: What it was testing.
        abstained: Whether the system declined to answer.
        abstention_correct: Whether abstaining (or not) was the right call.
        citations: How many citations the answer carried.
        citations_correct: How many pointed at a ground-truth span.
        context_precision: RAGAS score, ``None`` when not computed (abstentions, or a
            judge failure — the two are distinguished by ``abstained``).
        faithfulness: RAGAS score, ``None`` on the same terms.
        nearest_distance: Distance of the nearest retrieved passage, for re-deriving the
            abstention threshold from this run rather than from M2X-044's eight questions.
    """

    model_config = ConfigDict(frozen=True)

    question_id: str
    kind: QuestionKind
    abstained: bool
    abstention_correct: bool
    citations: int = 0
    citations_correct: int = 0
    context_precision: float | None = None
    faithfulness: float | None = None
    nearest_distance: float | None = None


class RagReport(BaseModel):
    """Aggregate over one run of the question set.

    Every headline is a property rather than a stored field, so a count and its ratio
    cannot drift apart — the failure the extraction harness had to be repaired for.
    """

    model_config = ConfigDict(frozen=True)

    scores: list[QuestionScore] = Field(default_factory=list)
    judge_failures: list[str] = Field(default_factory=list)
    """Questions where the judge itself errored. Reported, never silently averaged out —
    a judge failure is missing evidence, not a zero."""

    @property
    def context_precision(self) -> float:
        """Mean RAGAS context precision over questions where it was computed."""
        return _mean([score.context_precision for score in self.scores])

    @property
    def faithfulness(self) -> float:
        """Mean RAGAS faithfulness over questions where it was computed."""
        return _mean([score.faithfulness for score in self.scores])

    @property
    def citation_accuracy(self) -> float:
        """Correct citations over all citations emitted.

        Micro, not macro: an answer carrying six citations should weigh six times one
        carrying one, or a single sloppy answer with many citations hides behind a
        careful one with few.
        """
        total = sum(score.citations for score in self.scores)
        return sum(score.citations_correct for score in self.scores) / total if total else 0.0

    @property
    def abstention_accuracy(self) -> float:
        """Fraction of questions where abstaining, or not, was the right call."""
        return (
            sum(1 for score in self.scores if score.abstention_correct) / len(self.scores)
            if self.scores
            else 0.0
        )

    @property
    def uncited_answers(self) -> int:
        """Answers that were given with no citation at all.

        Worth its own line: an uncited answer contributes nothing to citation accuracy in
        either direction, so a system that stopped citing entirely would score 0.0 by
        having no denominator rather than by being wrong.
        """
        return sum(
            1 for score in self.scores if not score.abstained and score.citations == 0
        )

    def passes(self) -> dict[str, bool]:
        """Each gate leg against its PRD floor.

        Returns:
            ``{leg: passed}``. Abstention has no PRD floor and is excluded.
        """
        return {
            "context_precision": self.context_precision >= CONTEXT_PRECISION_FLOOR,
            "faithfulness": self.faithfulness >= FAITHFULNESS_FLOOR,
            "citation_accuracy": self.citation_accuracy >= CITATION_ACCURACY_FLOOR,
        }


def _mean(values: Iterable[float | None]) -> float:
    """Average the values that exist.

    Args:
        values: Scores, possibly with gaps.

    Returns:
        The mean, or 0.0 when nothing was computed.
    """
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else 0.0


def chunk_turn_range(metadata: dict[str, object]) -> tuple[int, int] | None:
    """Convert a retrieved chunk's stored segment range to 0-based turn indices.

    The single place the 1-based/0-based boundary is crossed. ``Chunk.segment_start`` is
    1-based because it is a position in a rendered transcript; an
    :class:`~m2x.rag_questions.EvidenceSpan` is 0-based because it is an index into a
    Python list of reference turns. Converting in two places would eventually converge on
    two answers, and the symptom would be a citation-accuracy score quietly off by one
    chunk everywhere.

    Args:
        metadata: A retrieved chunk's metadata.

    Returns:
        ``(first_turn, last_turn)`` 0-based inclusive, or ``None`` for a document chunk,
        which has no segment range and can never match a meeting span.
    """
    start, end = metadata.get("segment_start"), metadata.get("segment_end")
    if start is None or end is None:
        return None
    return int(start) - 1, int(end) - 1


def score_citations(outcome: AskOutcome, expected: ExpectedAnswer) -> tuple[int, int]:
    """Count how many of an answer's citations point at ground truth.

    A citation names a retrieved passage; that passage's chunk carries the meeting and the
    turn range it covers. Correct iff the meeting matches **and** the turn ranges overlap.
    Nothing about the citation's text is consulted, which is the requirement: a quote that
    sounds right against the wrong segment has to fail.

    Args:
        outcome: What ``ask`` returned.
        expected: The sealed ground truth for this question.

    Returns:
        ``(citations, citations_correct)``.
    """
    by_chunk = {hit.chunk_id: hit.metadata for hit in outcome.hits}
    correct = 0
    for citation in outcome.citations:
        metadata = by_chunk.get(citation.chunk_id)
        if metadata is None:
            continue
        turns = chunk_turn_range(metadata)
        if turns is None:
            continue
        source = str(metadata.get("source_id", ""))
        if any(
            span.meeting_id == source and span.overlaps(*turns) for span in expected.evidence
        ):
            correct += 1
    return len(outcome.citations), correct


def build_reference_index(
    store: VectorStore,
    adapter: ModelAdapter,
    meeting_ids: Iterable[str],
    *,
    reference_dir: Path = DEFAULT_REFERENCE_DIR,
    provider: Provider | None = None,
) -> int:
    """Index the tiron reference transcripts the question set draws on.

    Built from the committed reference rather than from ``data/``: the whole point of
    M2X-045's corpus choice is that a fresh clone can run this gate with no audio and no
    transcription provider.

    Args:
        store: Destination index.
        adapter: Adapter performing the embedding calls.
        meeting_ids: Reference meetings to index.
        reference_dir: Where the reference pairs live.
        provider: Force an embedding backend.

    Returns:
        Chunks written.

    Raises:
        ConfigError: A reference transcript is missing or malformed.
        M2XError: Any routing or provider failure.
    """
    written = 0
    for meeting_id in sorted(set(meeting_ids)):
        chunks = chunk_segments(
            load_reference_segments(meeting_id, reference_dir=reference_dir), meeting_id
        )
        if not chunks:
            continue
        vectors = embed_texts(
            adapter,
            [chunk.text for chunk in chunks],
            model_repo_id=store.embed_model_repo_id,
            provider=provider,
            context=RunContext(phase=PHASE, command="m2x eval rag", meeting_id=meeting_id),
        )
        written += store.write_source(meeting_id, chunks, vectors)
    return written


def run_rag_eval(
    questions: list[RagQuestion],
    expected: dict[str, ExpectedAnswer],
    *,
    store: VectorStore,
    adapter: ModelAdapter,
    judge: Callable[[AskOutcome, ExpectedAnswer], tuple[float | None, float | None]],
    model_repo_id: str = DEFAULT_ASK_MODEL,
    provider: Provider | None = None,
    top_k: int = DEFAULT_TOP_K,
    max_distance: float = DEFAULT_MAX_DISTANCE,
) -> tuple[RagReport, str]:
    """Ask every question and score what comes back.

    Args:
        questions: The public question set.
        expected: Sealed ground truth by question id.
        store: Index to retrieve from.
        adapter: Adapter for retrieval and answering.
        judge: Computes ``(context_precision, faithfulness)`` for one answered question.
            Injected so the harness is testable without a judge model — the RAGAS wiring
            lives in :func:`ragas_judge`.
        model_repo_id: Answering model.
        provider: Force a backend.
        top_k: Passages retrieved per question.
        max_distance: Abstention threshold.

    Returns:
        ``(report, resolved_prompt_version)``.

    Raises:
        M2XError: Any routing or provider failure that ``ask`` does not absorb.
    """
    scores: list[QuestionScore] = []
    judge_failures: list[str] = []
    prompt_version = "unknown"

    for question in questions:
        answer = expected[question.question_id]
        outcome = ask(
            question.question,
            store=store,
            adapter=adapter,
            model_repo_id=model_repo_id,
            provider=provider,
            k=top_k,
            max_distance=max_distance,
            command="m2x eval rag",
        )
        prompt_version = outcome.prompt_version

        # Abstaining is correct exactly when the corpus does not answer the question.
        abstention_correct = outcome.abstained == answer.must_abstain

        precision = faithfulness = None
        citations = correct = 0
        if not outcome.abstained:
            citations, correct = score_citations(outcome, answer)
            # A must-abstain question has no reference contexts, so RAGAS context
            # precision is undefined for it — there is no relevant passage to have
            # retrieved. Judging it anyway would score the model on an impossible task
            # and drag the headline down for behaving correctly.
            if not answer.must_abstain:
                try:
                    precision, faithfulness = judge(outcome, answer)
                except Exception as error:  # noqa: BLE001 - judge failures are data
                    judge_failures.append(f"{question.question_id}: {error}")

        scores.append(
            QuestionScore(
                question_id=question.question_id,
                kind=question.kind,
                abstained=outcome.abstained,
                abstention_correct=abstention_correct,
                citations=citations,
                citations_correct=correct,
                context_precision=precision,
                faithfulness=faithfulness,
                nearest_distance=outcome.nearest_distance,
            )
        )

    return RagReport(scores=scores, judge_failures=judge_failures), prompt_version


def format_rag_report(report: RagReport) -> str:
    """Render the report as the table the gate record quotes.

    Args:
        report: The run's report.

    Returns:
        The formatted table.
    """
    passes = report.passes()
    lines = [
        f"questions: {len(report.scores)}   uncited answers: {report.uncited_answers}",
        "",
        f"{'metric':<22}{'value':>8}{'floor':>8}  {'':<6}source",
        "-" * 62,
    ]
    for label, value, floor, source in (
        ("context precision", report.context_precision, CONTEXT_PRECISION_FLOOR, "RAGAS (judge LLM)"),
        ("faithfulness", report.faithfulness, FAITHFULNESS_FLOOR, "RAGAS (judge LLM)"),
        ("citation accuracy", report.citation_accuracy, CITATION_ACCURACY_FLOOR, "mechanical"),
    ):
        verdict = "PASS" if passes[label.replace(" ", "_")] else "FAIL"
        lines.append(f"{label:<22}{value:>8.4f}{floor:>8.2f}  {verdict:<6}{source}")
    lines += [
        "-" * 62,
        f"{'abstention accuracy':<22}{report.abstention_accuracy:>8.4f}{'—':>8}  {'':<6}mechanical (no PRD floor)",
        "",
    ]

    for kind in QuestionKind:
        of_kind = [score for score in report.scores if score.kind is kind]
        if not of_kind:
            continue
        right = sum(1 for score in of_kind if score.abstention_correct)
        lines.append(f"  {kind.value:<16} {right}/{len(of_kind)} answered-or-abstained correctly")

    if report.judge_failures:
        lines += ["", f"judge failed on {len(report.judge_failures)} question(s) — these are"]
        lines.append("missing evidence, not zeros; the two RAGAS means exclude them:")
        lines += [f"  {failure}" for failure in report.judge_failures]

    lines += [
        "",
        "The two RAGAS figures are a language model's opinion of a language model, and on",
        "the zero-spend stack the judge shares the answerer's weights. Citation accuracy",
        "and abstention are mechanical. Read them in that order.",
    ]
    return "\n".join(lines)


def current_git_sha() -> str:
    """Resolve the working tree's SHA.

    Returns:
        The short SHA, or ``"unknown"`` outside a repository.
    """
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return completed.stdout.strip() or "unknown"


def append_rag_result(
    report: RagReport,
    *,
    prompt_version: str,
    model_repo_id: str,
    judge_model_repo_id: str,
    embed_model_repo_id: str,
    top_k: int,
    max_distance: float,
    path: Path = DEFAULT_RAG_RESULTS_PATH,
    git_sha: str | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Path:
    """Append one run's numbers to the RAG results log.

    Every knob that moves a number is written down. ``top_k`` and ``max_distance`` in
    particular: M2X-042 records both as unmeasured defaults, so a row that cannot name
    them is a number about an unknown configuration.

    Args:
        report: The run's report.
        prompt_version: RAG prompt version that produced it.
        model_repo_id: Answering model.
        judge_model_repo_id: Model RAGAS judged with.
        embed_model_repo_id: Model the index was built with.
        top_k: Passages retrieved per question.
        max_distance: Abstention threshold.
        path: Results file, created with its parent if absent.
        git_sha: SHA under test. Resolved from the working tree when ``None``.
        now: Clock, injected so tests do not depend on one.

    Returns:
        The path written.

    Raises:
        OSError: The record could not be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": now().isoformat(),
        "git_sha": git_sha if git_sha is not None else current_git_sha(),
        "prompt_version": prompt_version,
        "model_repo_id": model_repo_id,
        "judge_model_repo_id": judge_model_repo_id,
        "embed_model_repo_id": embed_model_repo_id,
        "top_k": top_k,
        "max_distance": max_distance,
        "context_precision": round(report.context_precision, 4),
        "faithfulness": round(report.faithfulness, 4),
        "citation_accuracy": round(report.citation_accuracy, 4),
        "abstention_accuracy": round(report.abstention_accuracy, 4),
        "questions": len(report.scores),
        "uncited_answers": report.uncited_answers,
        "judge_failures": report.judge_failures,
        "passes": report.passes(),
        # Per question, so a later run can be diffed case by case rather than only on the
        # headline -- and so the abstention threshold can be re-derived from the distances
        # this run saw, which M2X-042 records as the open question 0.48 rests on.
        "per_question": [score.model_dump(mode="json") for score in report.scores],
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    return path


def load_question_set(
    directory: Path = DEFAULT_RAG_EVAL_DIR,
) -> tuple[list[RagQuestion], dict[str, ExpectedAnswer]]:
    """Read both halves of the eval set.

    Args:
        directory: Root of the set.

    Returns:
        ``(questions, expected)``.

    Raises:
        ConfigError: The expected answers are still sealed.
        OSError: A file could not be read.
    """
    return (
        load_questions(directory / QUESTIONS_FILENAME),
        load_expected(directory / EXPECTED_DIRNAME),
    )
