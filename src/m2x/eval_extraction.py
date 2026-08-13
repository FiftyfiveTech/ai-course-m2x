"""Field-level precision, recall and F1 for extracted meeting records.

The rules this implements are written down first, in ``eval/README.md``, and argued in
``docs/design/day3-schema.md`` §The frozen contract. That ordering is deliberate: a
matching rule settled after seeing scores is a rule chosen to produce those scores, which
is how a previous run's 0.8063 ended up settling nothing.

Three properties carry the design:

* **Determinism.** No network, no embeddings, no clock. Two runs over the same records
  pair the same items and print the same number, including the tie-breaks — otherwise a
  gate figure cannot be re-derived by anyone.
* **Null is an answer.** ``None`` matches ``None`` and nothing else, so a model that
  admits ignorance can outscore one that guesses. Losing that inverts the incentive the
  schema was built around.
* **Evidence is not content.** A citation either resolves or it does not; it is already
  enforced inside the extraction retry loop and is reported here as schema-validity
  rather than averaged into a content score, where good citations could paper over wrong
  items.
"""

from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from m2x.adapter import ModelAdapter
from m2x.extraction import DEFAULT_EXTRACT_MODEL, extract_record
from m2x.labels import DEFAULT_LABELS_DIR, LabelledCase, load_label_set
from m2x.reference_transcript import DEFAULT_REFERENCE_DIR
from m2x.run_log import RunContext
from m2x.schema import ActionItem, MeetingRecord
from m2x.types import Provider

DEFAULT_RESULTS_PATH = Path("eval/results/extraction.jsonl")
"""Append-only record of every eval run.

Tracked rather than git-ignored: the point of the file is that a number quoted in a gate
record can be found again next to the prompt version and SHA that produced it.
"""

EVAL_PHASE = "phase-1b"
"""Run-log phase eval extractions are attributed to."""

DESCRIPTION_MATCH_THRESHOLD = 0.60
"""Token-set F1 at or above which two descriptions are the same item.

Fixed before any data existed to tune it against, which is the only honest time to pick
it. Changing it is a contract change — a new row in ``eval/README.md`` and a re-run of
every number computed under the old value — never a quiet edit.
"""

STOPWORDS = frozenset(
    """
    a an the and or but if then than that this these those of in on at to for from by
    with without into onto up down out over under again further is are was were be been
    being am do does did doing have has had having will would shall should can could may
    might must it its it's we our us you your they them their he she his her i me my
    as so not no nor too very just about
    """.split()
)
"""Words dropped before comparing descriptions.

Deliberately small and fixed. A longer list would raise scores by deleting the words that
distinguish items, and the point of the threshold is to be crude and stable rather than
generous.
"""

TITLES = frozenset({"mr", "mrs", "ms", "miss", "dr", "prof", "sir"})
"""Honorifics stripped before comparing owners."""

_PUNCTUATION = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def normalise_description(text: str) -> frozenset[str]:
    """Reduce a description to the token set used for matching.

    Casefold, strip punctuation, collapse whitespace, drop stopwords. A *set*, not a
    multiset: repeating a word does not make an item more itself.

    Args:
        text: Raw description.

    Returns:
        The tokens that carry meaning, deduplicated.
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    stripped = _PUNCTUATION.sub(" ", folded)
    tokens = _WHITESPACE.sub(" ", stripped).strip().split(" ")
    return frozenset(token for token in tokens if token and token not in STOPWORDS)


def token_set_f1(left: str, right: str) -> float:
    """Similarity of two descriptions, as the F1 of their token sets.

    Args:
        left: One description.
        right: The other.

    Returns:
        ``0.0``–``1.0``. Two descriptions that normalise to nothing score ``0.0`` rather
        than ``1.0`` — a pair of empty strings is not evidence of a match.
    """
    first, second = normalise_description(left), normalise_description(right)
    if not first or not second:
        return 0.0
    overlap = len(first & second)
    if not overlap:
        return 0.0
    precision = overlap / len(second)
    recall = overlap / len(first)
    return 2 * precision * recall / (precision + recall)


def canonical_owner(owner: str | None) -> str | None:
    """Reduce an owner to the form compared for equality.

    Args:
        owner: Owner as extracted or labelled, or ``None``.

    Returns:
        Canonical owner, or ``None``. ``None`` is preserved rather than becoming an empty
        string, because ``None`` is a meaningful answer and ``""`` is not.
    """
    if owner is None:
        return None
    folded = unicodedata.normalize("NFKC", owner).casefold().strip()
    words = [word for word in _PUNCTUATION.sub(" ", folded).split() if word not in TITLES]
    return " ".join(words) or None


class Counts(BaseModel):
    """True positives, false positives and false negatives for one axis."""

    model_config = ConfigDict(frozen=True)

    true_positive: int = Field(default=0, ge=0)
    false_positive: int = Field(default=0, ge=0)
    false_negative: int = Field(default=0, ge=0)

    def __add__(self, other: Counts) -> Counts:
        """Sum two count sets.

        Args:
            other: Counts to add.

        Returns:
            The summed counts.
        """
        return Counts(
            true_positive=self.true_positive + other.true_positive,
            false_positive=self.false_positive + other.false_positive,
            false_negative=self.false_negative + other.false_negative,
        )

    @property
    def precision(self) -> float:
        """Of what was extracted, the fraction that was right. ``1.0`` if nothing was."""
        predicted = self.true_positive + self.false_positive
        return self.true_positive / predicted if predicted else 1.0

    @property
    def recall(self) -> float:
        """Of what was labelled, the fraction found. ``1.0`` if there was nothing to find."""
        actual = self.true_positive + self.false_negative
        return self.true_positive / actual if actual else 1.0

    @property
    def f1(self) -> float:
        """Harmonic mean of precision and recall."""
        precision, recall = self.precision, self.recall
        if precision + recall == 0.0:
            return 0.0
        return 2 * precision * recall / (precision + recall)

    @property
    def total(self) -> int:
        """Every item this axis saw, from either side."""
        return self.true_positive + self.false_positive + self.false_negative


Similarity = Callable[[str, str], float]
"""How two descriptions are compared. ``0.0``-``1.0``, symmetric.

Pluggable because the lexical default is known to be inadequate: it measures phrasing,
and two correct summaries of one fact routinely share few content words (M2X-036,
``docs/design/day3-iteration.md``). Swapping in an embedding-backed similarity is the
documented replacement; keeping the seam explicit means the choice is visible in the
call rather than buried in a helper.
"""


def match_items(
    labelled: Sequence[object],
    extracted: Sequence[object],
    *,
    threshold: float = DESCRIPTION_MATCH_THRESHOLD,
    similarity: Similarity = token_set_f1,
) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Pair extracted items to labelled ones, greedily and one-to-one.

    All candidate pairs scoring at or above ``threshold`` are sorted by descending
    similarity and taken in order, each item used at most once. Ties break by labelled
    index then extracted index, so the pairing is identical on every run — without that,
    the same output can score differently twice in a row.

    Args:
        labelled: Ground-truth items of one kind. Each needs a ``description``.
        extracted: Extracted items of the same kind.
        threshold: Minimum similarity for a pair to be eligible.
        similarity: How two descriptions are compared.

    Returns:
        ``(pairs, unmatched_labelled, unmatched_extracted)`` where pairs are
        ``(labelled_index, extracted_index)``.
    """
    candidates: list[tuple[float, int, int]] = []
    for label_index, label_item in enumerate(labelled):
        for extract_index, extract_item in enumerate(extracted):
            score = similarity(
                label_item.description,  # type: ignore[attr-defined]
                extract_item.description,  # type: ignore[attr-defined]
            )
            if score >= threshold:
                candidates.append((score, label_index, extract_index))

    # Negative score so a plain ascending sort puts the best first while the index
    # tie-breaks stay ascending — one sort key, no ambiguity about ordering.
    candidates.sort(key=lambda candidate: (-candidate[0], candidate[1], candidate[2]))

    pairs: list[tuple[int, int]] = []
    used_labels: set[int] = set()
    used_extracts: set[int] = set()
    for _, label_index, extract_index in candidates:
        if label_index in used_labels or extract_index in used_extracts:
            continue
        pairs.append((label_index, extract_index))
        used_labels.add(label_index)
        used_extracts.add(extract_index)

    unmatched_labelled = [index for index in range(len(labelled)) if index not in used_labels]
    unmatched_extracted = [index for index in range(len(extracted)) if index not in used_extracts]
    return sorted(pairs), unmatched_labelled, unmatched_extracted


EMBEDDING_MATCH_THRESHOLD = 0.675
"""Cosine at or above which two descriptions are the same item.

**Calibrated against same/different judgements, never against the resulting F1.** Fifteen
pairs were written down as SAME or DIFFERENT by reading them — the SAME set being the
near-miss band found in M2X-036 error analysis, the DIFFERENT set the pathological cases
that disqualified containment plus genuinely unrelated items and same-topic-different-claim
pairs. Only then were the cosines computed:

* lowest SAME ``0.6928`` ("Whether this corpus is the right one to attempt this on at all"
  vs "Is the corpus the right one to try to find a correspondence…")
* highest DIFFERENT ``0.6586`` ("Children are not coming on the retreat" vs "Horseback
  riding is included in the retreat programme")

This value is the midpoint of that gap. The one-word fragment ``"adopt"``, which scores
``1.00`` under containment and is the reason containment was rejected, sits at ``0.5013``.

**The gap is 0.034 wide on fifteen pairs, which is separation rather than comfort.**
Same-topic-different-claim pairs are the ones that crowd it from below, and a larger
calibration set would very likely narrow it further. Treat this as a working threshold
that a future ticket should re-derive on more pairs, not as a settled constant.
"""

DEFAULT_EMBED_MODEL = "nomic-ai/nomic-embed-text-v1.5"
"""Embedding model backing :class:`EmbeddingSimilarity`.

Pinned, and recorded on every results row. The original objection to embeddings
(``eval/README.md``) was that an upgrade would silently change what a score means; that
holds only if the model is *unrecorded*, so it is written into the result alongside the
prompt version and SHA and a change shows up in the diff.
"""


class EmbeddingSimilarity:
    """Cosine similarity between description embeddings.

    The lexical default measures phrasing. Two correct summaries of one fact — "Find
    somebody to shoot the testimonial videos and edit them properly" and "Linda will find
    someone to take the video and edit it properly" — score 0.43 on token overlap, and no
    deterministic lexical metric closes that gap (M2X-036).

    Determinism is preserved by the cache rather than by the metric: every batch goes
    through :meth:`~m2x.adapter.ModelAdapter.embed`, so a re-run over unchanged text is a
    cache hit and returns identical vectors. Two runs on one machine agree; two runs on
    different embedding-model versions do not, which is why the model id travels with the
    number.

    Descriptions are embedded in one batch per call site rather than pair by pair: the
    matcher compares every label against every extraction, so pairwise embedding would
    issue O(n*m) requests for O(n+m) distinct texts.
    """

    def __init__(
        self,
        adapter: ModelAdapter,
        *,
        model_repo_id: str = DEFAULT_EMBED_MODEL,
        provider: Provider | None = None,
        context: RunContext | None = None,
    ) -> None:
        """Build a similarity backed by an embedding model.

        Args:
            adapter: Adapter performing the embedding calls.
            model_repo_id: Hugging Face repo id of the embedding model.
            provider: Force a backend. ``None`` routes by the registry.
            context: Provenance for the run log.
        """
        self._adapter = adapter
        self._model_repo_id = model_repo_id
        self._provider = provider
        self._context = context
        self._vectors: dict[str, tuple[float, ...]] = {}

    @property
    def model_repo_id(self) -> str:
        """Which model produced the vectors, for the results record."""
        return self._model_repo_id

    def warm(self, texts: Sequence[str]) -> None:
        """Embed a batch of texts up front.

        Args:
            texts: Descriptions that will be compared. Duplicates and already-known
                texts are skipped.

        Raises:
            M2XError: The embedding call failed.
        """
        pending = sorted({text for text in texts if text not in self._vectors})
        if not pending:
            return
        result = self._adapter.embed(
            pending,
            self._model_repo_id,
            provider=self._provider,
            context=self._context,
        )
        for text, vector in zip(pending, result.vectors, strict=True):
            self._vectors[text] = tuple(vector)

    def __call__(self, left: str, right: str) -> float:
        """Cosine similarity of two descriptions, clamped to ``0.0``-``1.0``.

        Args:
            left: One description.
            right: The other.

        Returns:
            Similarity. Negative cosines clamp to ``0.0``: an item that is the *opposite*
            of another is not a match, and letting a negative through would make the
            greedy sort prefer it over an unrelated pair.

        Raises:
            M2XError: The embedding call failed.
        """
        self.warm([left, right])
        first, second = self._vectors[left], self._vectors[right]
        dot = sum(a * b for a, b in zip(first, second, strict=True))
        norm_first = sum(a * a for a in first) ** 0.5
        norm_second = sum(b * b for b in second) ** 0.5
        if not norm_first or not norm_second:
            return 0.0
        return max(0.0, min(1.0, dot / (norm_first * norm_second)))


ITEM_KINDS = ("decisions", "actions", "risks", "open_questions")
"""The four lists scored, in report order."""


class CaseScore(BaseModel):
    """What one case contributed to the totals."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    per_kind: dict[str, Counts]
    """Item-level counts, keyed by the names in :data:`ITEM_KINDS`."""

    owner: Counts
    """Owner correctness over *matched* actions only."""

    deadline_correct_nulls: int = Field(default=0, ge=0)
    """Matched actions where both sides said ``None``."""

    deadline_emitted: int = Field(default=0, ge=0)
    """Matched actions where the extractor emitted a date. All false positives on this
    corpus, which has no labelled deadlines — see ``eval/README.md`` §5."""

    matched_actions: int = Field(default=0, ge=0)
    """Denominator for the owner and deadline figures."""


def score_case(
    case_id: str,
    labelled: MeetingRecord,
    extracted: MeetingRecord,
    *,
    similarity: Similarity = token_set_f1,
    threshold: float = DESCRIPTION_MATCH_THRESHOLD,
) -> CaseScore:
    """Score one extracted record against its label.

    Args:
        case_id: Case being scored, carried into the result for per-case reporting.
        labelled: Ground truth.
        extracted: What the extractor produced.
        similarity: How two descriptions are compared.
        threshold: Minimum similarity for a pair to be eligible.

    Returns:
        The case's contribution to the totals.
    """
    per_kind: dict[str, Counts] = {}
    owner_counts = Counts()
    correct_nulls = 0
    emitted = 0
    matched_actions = 0

    for kind in ITEM_KINDS:
        label_items = getattr(labelled, kind)
        extract_items = getattr(extracted, kind)
        pairs, unmatched_labels, unmatched_extracts = match_items(
            label_items, extract_items, threshold=threshold, similarity=similarity
        )
        per_kind[kind] = Counts(
            true_positive=len(pairs),
            false_positive=len(unmatched_extracts),
            false_negative=len(unmatched_labels),
        )

        if kind != "actions":
            continue

        matched_actions = len(pairs)
        for label_index, extract_index in pairs:
            label_action: ActionItem = label_items[label_index]
            extract_action: ActionItem = extract_items[extract_index]

            label_owner = canonical_owner(label_action.owner)
            extract_owner = canonical_owner(extract_action.owner)
            if label_owner == extract_owner:
                # Includes None == None: correctly declining to name an owner is a hit,
                # which is the whole point of the nullability rule.
                owner_counts += Counts(true_positive=1)
            elif extract_owner is None:
                owner_counts += Counts(false_negative=1)
            elif label_owner is None:
                owner_counts += Counts(false_positive=1)
            else:
                # Named the wrong person: wrong on both axes, not half right.
                owner_counts += Counts(false_positive=1, false_negative=1)

            if extract_action.deadline is None:
                correct_nulls += 1
            else:
                emitted += 1

    return CaseScore(
        case_id=case_id,
        per_kind=per_kind,
        owner=owner_counts,
        deadline_correct_nulls=correct_nulls,
        deadline_emitted=emitted,
        matched_actions=matched_actions,
    )


class EvalReport(BaseModel):
    """Aggregated scores for one run over one set."""

    model_config = ConfigDict(frozen=True)

    set_name: str
    cases_scored: int = Field(ge=0)
    cases_failed: int = Field(default=0, ge=0)
    """Cases that produced no valid record at all. The Phase 1B gate wants zero."""

    per_kind: dict[str, Counts]
    owner: Counts
    deadline_correct_nulls: int = Field(default=0, ge=0)
    deadline_emitted: int = Field(default=0, ge=0)
    matched_actions: int = Field(default=0, ge=0)
    per_case: list[CaseScore] = Field(default_factory=list)

    @property
    def items(self) -> Counts:
        """Item-level counts summed across all four kinds."""
        total = Counts()
        for kind in ITEM_KINDS:
            total += self.per_kind.get(kind, Counts())
        return total

    @property
    def micro_f1(self) -> float:
        """The headline number: items across all kinds, plus owner on matched actions.

        ``deadline`` is excluded on purpose — the corpus has no labelled deadlines, so
        the field has no positive examples and folding it in would average in something
        that cannot be earned. See ``eval/README.md`` §5.
        """
        return (self.items + self.owner).f1

    @property
    def schema_validity(self) -> float:
        """Fraction of cases that produced a valid record. The gate wants ``1.0``."""
        attempted = self.cases_scored + self.cases_failed
        return self.cases_scored / attempted if attempted else 1.0

    @property
    def deadline_abstention(self) -> float:
        """Fraction of matched actions where the extractor correctly emitted ``None``.

        Reported instead of a deadline F1, which is undefined on a corpus whose labels
        contain no deadlines.
        """
        if not self.matched_actions:
            return 1.0
        return self.deadline_correct_nulls / self.matched_actions


def aggregate(set_name: str, scores: Sequence[CaseScore], *, failed: int = 0) -> EvalReport:
    """Sum per-case scores into a report.

    Args:
        set_name: ``dev`` or ``heldout``, carried into the record.
        scores: Per-case scores.
        failed: Cases that produced no valid record.

    Returns:
        The aggregated report.
    """
    per_kind = {kind: Counts() for kind in ITEM_KINDS}
    owner = Counts()
    correct_nulls = emitted = matched_actions = 0

    for score in scores:
        for kind in ITEM_KINDS:
            per_kind[kind] += score.per_kind.get(kind, Counts())
        owner += score.owner
        correct_nulls += score.deadline_correct_nulls
        emitted += score.deadline_emitted
        matched_actions += score.matched_actions

    return EvalReport(
        set_name=set_name,
        cases_scored=len(scores),
        cases_failed=failed,
        per_kind=per_kind,
        owner=owner,
        deadline_correct_nulls=correct_nulls,
        deadline_emitted=emitted,
        matched_actions=matched_actions,
        per_case=list(scores),
    )


def format_report(report: EvalReport) -> str:
    """Render a report as the table printed at the end of a run.

    Args:
        report: Report to render.

    Returns:
        Text, no trailing newline.
    """
    lines = [
        f"set: {report.set_name}   cases scored: {report.cases_scored}"
        f"   failed: {report.cases_failed}",
        "",
        f"{'field':<16}{'P':>8}{'R':>8}{'F1':>8}{'TP':>6}{'FP':>6}{'FN':>6}",
        "-" * 58,
    ]
    for kind in ITEM_KINDS:
        counts = report.per_kind.get(kind, Counts())
        lines.append(
            f"{kind:<16}{counts.precision:>8.4f}{counts.recall:>8.4f}{counts.f1:>8.4f}"
            f"{counts.true_positive:>6}{counts.false_positive:>6}{counts.false_negative:>6}"
        )
    owner = report.owner
    lines.append(
        f"{'owner':<16}{owner.precision:>8.4f}{owner.recall:>8.4f}{owner.f1:>8.4f}"
        f"{owner.true_positive:>6}{owner.false_positive:>6}{owner.false_negative:>6}"
    )
    lines += [
        "-" * 58,
        f"{'MICRO-F1':<16}{report.micro_f1:>24.4f}",
        "",
        f"schema-valid:        {report.schema_validity:.4f} "
        f"({report.cases_scored}/{report.cases_scored + report.cases_failed})",
        f"deadline abstention: {report.deadline_abstention:.4f} "
        f"({report.deadline_correct_nulls}/{report.matched_actions} matched actions)",
        "",
        "deadline is reported, not scored: the labels contain none, so the field has no",
        "positive examples and cannot enter micro-F1. See eval/README.md section 5.",
    ]
    return "\n".join(lines)


def current_git_sha() -> str:
    """Read the working tree's HEAD, for stamping onto a result.

    Returns:
        The short SHA, or ``"unknown"`` when git is unavailable or this is not a
        checkout. Unknown is recorded rather than raised: losing a whole eval run
        because a SHA could not be read would be the worse failure, and the field
        being ``"unknown"`` is itself the warning that the number is unattributable.
    """
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return completed.stdout.strip() or "unknown"


def run_extraction_eval(
    set_name: str,
    *,
    adapter: ModelAdapter,
    labels_dir: Path = DEFAULT_LABELS_DIR,
    reference_dir: Path = DEFAULT_REFERENCE_DIR,
    model_repo_id: str = DEFAULT_EXTRACT_MODEL,
    provider: Provider | None = None,
    prompt_version: str | None = None,
    similarity: Similarity | None = None,
    threshold: float | None = None,
) -> tuple[EvalReport, str]:
    """Extract every case in a set and score it against the labels.

    Extraction runs through the adapter like any other call, so a re-run over an unchanged
    prompt is served from the cache — which is what makes the number reproducible without
    being free the first time.

    A case whose extraction raises is counted as a schema-validity failure rather than
    aborting the run: one unparseable case should not cost the other fourteen, and the
    Phase 1B gate needs the count of failures anyway.

    Args:
        set_name: ``dev`` or ``heldout``.
        adapter: Adapter performing the extractions.
        labels_dir: Root holding ``dev/`` and ``heldout/``.
        reference_dir: Where the reference transcripts live.
        model_repo_id: Hugging Face repo id of the extraction model.
        provider: Force a backend. ``None`` routes by the registry.
        prompt_version: Pin a prompt version. ``None`` takes the latest on disk.
        similarity: How descriptions are compared. ``None`` uses the lexical default.
        threshold: Match threshold. ``None`` uses the one matching the similarity.

    Returns:
        ``(report, resolved_prompt_version)``.

    Raises:
        FileNotFoundError: The set directory does not exist. For ``heldout`` this is the
            expected state on a fresh clone, since the sealed plaintext is never
            committed.
    """
    directory = labels_dir / set_name
    if not directory.is_dir():
        raise FileNotFoundError(
            f"no label set at {directory}. For 'heldout' on a fresh clone this is "
            "expected: the sealed set is not committed (see eval/labels/README.md)."
        )

    cases: list[LabelledCase] = load_label_set(directory)
    scores: list[CaseScore] = []
    failed = 0
    resolved_version = prompt_version or "unknown"

    for case in cases:
        context = RunContext(
            phase=EVAL_PHASE,
            command=f"m2x eval extraction --set {set_name}",
            meeting_id=case.case_id,
        )
        try:
            outcome = extract_record(
                case.segments(reference_dir=reference_dir),
                adapter=adapter,
                meeting_id=case.case_id,
                model_repo_id=model_repo_id,
                provider=provider,
                prompt_version=prompt_version,
                context=context,
            )
        except Exception:
            # Deliberately broad: any failure to produce a valid record is the same
            # fact for the gate, whether it was a provider error or an exhausted retry
            # budget. The count is what the criterion asks for.
            failed += 1
            continue
        resolved_version = outcome.prompt_version
        scores.append(
            score_case(
                case.case_id,
                case.label,
                outcome.record,
                similarity=similarity or token_set_f1,
                threshold=(
                    threshold
                    if threshold is not None
                    else DESCRIPTION_MATCH_THRESHOLD
                ),
            )
        )

    return aggregate(set_name, scores, failed=failed), resolved_version


def append_result(
    report: EvalReport,
    *,
    prompt_version: str,
    model_repo_id: str,
    path: Path = DEFAULT_RESULTS_PATH,
    git_sha: str | None = None,
    provider: Provider | None = None,
    similarity_kind: str = "token_set_f1",
    threshold: float = DESCRIPTION_MATCH_THRESHOLD,
    embed_model_repo_id: str | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Path:
    """Append one run's numbers to the results log.

    Args:
        report: The run's report.
        prompt_version: Prompt version that produced it.
        model_repo_id: Model that produced it.
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
        "set": report.set_name,
        "git_sha": git_sha if git_sha is not None else current_git_sha(),
        "prompt_version": prompt_version,
        "model_repo_id": model_repo_id,
        # Which backend served it, because the same repo id is not the same weights
        # everywhere: the ollama route for this model is a quantised GGUF build while
        # groq and nim serve full precision. Two rows naming one repo id can therefore
        # be two different models, and without this field nothing in the record says so.
        "provider": provider.value if provider is not None else None,
        # How agreement was measured travels with the number. A micro-F1 computed under
        # a different matcher is a different quantity, and M2X-036 changed the matcher —
        # so a row without these three fields cannot be compared with one that has them.
        "similarity": similarity_kind,
        "match_threshold": threshold,
        "embed_model_repo_id": embed_model_repo_id,
        "micro_f1": round(report.micro_f1, 4),
        "schema_validity": round(report.schema_validity, 4),
        "deadline_abstention": round(report.deadline_abstention, 4),
        "cases_scored": report.cases_scored,
        "cases_failed": report.cases_failed,
        "per_kind": {
            kind: {
                "precision": round(counts.precision, 4),
                "recall": round(counts.recall, 4),
                "f1": round(counts.f1, 4),
                "tp": counts.true_positive,
                "fp": counts.false_positive,
                "fn": counts.false_negative,
            }
            for kind, counts in report.per_kind.items()
        },
        "owner": {
            "precision": round(report.owner.precision, 4),
            "recall": round(report.owner.recall, 4),
            "f1": round(report.owner.f1, 4),
        },
        # Recorded on every row rather than assumed known: a reader three weeks later
        # must not have to find out from a design doc why deadline is missing.
        "note": "deadline excluded from micro_f1; labels contain none (eval/README.md s5)",
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    return path
