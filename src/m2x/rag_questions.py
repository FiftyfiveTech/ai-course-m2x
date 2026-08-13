"""The Phase 2 gate instrument: thirty questions and what counts as answering them.

Same discipline as the Phase 1B ground truth (M2X-033), and the same split of concerns.
A question is **public** — the system has to be asked it, so hiding it would only stop the
harness running. An expected answer is **sealed**, because a builder who can read the
expected segments can tune retrieval until they come back, and a retrieval metric tuned
against its own answer key measures nothing.

## The corpus is `eval/tiron/`, and that is a decision

`data/` is git-ignored, so a fresh clone has no meeting transcripts and the only corpus
`m2x ask` has ever been verified against is this repository's own markdown — every citation
in `docs/design/day4-ask.md` is a `§ heading`, not an `mm:ss`. Writing the gate set against
documents would leave the meeting citation path, which is the whole product promise,
unmeasured at the gate.

The tiron reference transcripts are committed, carry human speaker turns and real
timestamps, and are already the substrate for the extraction labels. Questions written
against them exercise `[meeting · speaker · mm:ss–mm:ss]` on every clone, with no audio and
no provider.

## Ground truth is turn ranges, never rendered citations

An expected answer names ``(meeting_id, first_turn, last_turn)`` into the reference
transcript. Not an `mm:ss` string — that would score the renderer rather than the
retrieval — and not a chunk id, which is a function of the chunking parameters and changes
the moment anyone tunes them.

Turn indices are stable: they are positions in a committed file. A retrieved chunk records
the segment range it covers, so citation accuracy is an **overlap** test between the two,
exactly as the ticket specifies ("the cited segment id is among the ground-truth segments,
or overlaps its time range").
"""

from __future__ import annotations

import json
from collections import Counter
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from m2x.errors import ConfigError
from m2x.reference_transcript import DEFAULT_REFERENCE_DIR, load_reference_segments

DEFAULT_RAG_EVAL_DIR = Path("eval/rag")
"""Root of the Phase 2 gate instrument."""

QUESTIONS_FILENAME = "questions.jsonl"
"""Public half: the questions, committed in plaintext because the system must be asked them."""

EXPECTED_DIRNAME = "expected"
"""Sealed half: one file per question. Plaintext git-ignored, ciphertext and digests committed."""

SINGLE_MEETING_COUNT = 20
CROSS_MEETING_COUNT = 5
UNANSWERABLE_COUNT = 5
"""The ticket's mix: 20 single-meeting, 5 cross-meeting, 5 that must abstain.

Pinned as constants so the set cannot quietly drift toward whichever kind the system
happens to be good at. :func:`validate_question_set` fails on any other distribution.
"""


class QuestionKind(str, Enum):
    """What a question is testing.

    Kept as three values rather than an ``answerable`` boolean because the three score
    differently: a cross-meeting question that retrieves one meeting well is a *failure*
    the single-meeting bucket cannot express, and an unanswerable question is scored on
    abstention rather than on content at all.
    """

    SINGLE_MEETING = "single_meeting"
    """Answerable from one meeting."""

    CROSS_MEETING = "cross_meeting"
    """Needs facts from two or more meetings; retrieving only one is a miss."""

    UNANSWERABLE = "unanswerable"
    """Not in the corpus. Correct only if the system abstains."""


class EvidenceSpan(BaseModel):
    """A range of reference turns that contains the answer.

    Attributes:
        meeting_id: Reference meeting, e.g. ``tiron-MTG_32185``.
        first_turn: First turn, 0-based inclusive.
        last_turn: Last turn, 0-based inclusive.
    """

    model_config = ConfigDict(frozen=True)

    meeting_id: str = Field(min_length=1)
    first_turn: int = Field(ge=0)
    last_turn: int = Field(ge=0)

    @model_validator(mode="after")
    def _bounds_are_ordered(self) -> EvidenceSpan:
        """Reject an inverted range.

        Returns:
            The validated span.

        Raises:
            ValueError: ``last_turn`` precedes ``first_turn``.
        """
        if self.last_turn < self.first_turn:
            raise ValueError(f"turn range inverted: {self.first_turn} > {self.last_turn}")
        return self

    def overlaps(self, first: int, last: int) -> bool:
        """Whether a retrieved chunk's turn range intersects this span.

        Overlap rather than containment: chunks are packed to a character budget and a
        correct citation routinely covers the answer plus its neighbours. Demanding
        containment would fail a chunk for being the size the indexer chose.

        Args:
            first: Chunk's first turn, 0-based inclusive.
            last: Chunk's last turn, 0-based inclusive.

        Returns:
            True when the ranges intersect.
        """
        return first <= self.last_turn and last >= self.first_turn


class RagQuestion(BaseModel):
    """The public half of one eval case.

    Attributes:
        question_id: Stable id, ``q01``…``q30``.
        kind: What the question tests.
        question: The question as it is asked of the system, verbatim.
    """

    model_config = ConfigDict(frozen=True)

    question_id: str = Field(min_length=1)
    kind: QuestionKind
    question: str = Field(min_length=1)


class ExpectedAnswer(BaseModel):
    """The sealed half of one eval case.

    Attributes:
        question_id: The question this answers.
        gist: What a correct answer must convey, in one sentence. Graded by a judge, never
            by string equality — two correct answers to the same question rarely share
            wording, which is the same finding that replaced token-set F1 in M2X-036.
        evidence: Turn ranges containing the answer. Empty exactly when the question is
            unanswerable.
        notes: Why this is the answer, and where it was nearly something else. Written for
            the adjudicator at the gate, not for the harness.
    """

    model_config = ConfigDict(frozen=True)

    question_id: str = Field(min_length=1)
    gist: str = ""
    evidence: list[EvidenceSpan] = Field(default_factory=list)
    notes: str = ""

    @property
    def must_abstain(self) -> bool:
        """Whether the only correct behaviour is abstention.

        Derived from ``evidence`` being empty rather than stored as its own flag: two
        fields encoding one fact drift, and the drift would silently turn a must-abstain
        case into a scored one.

        Returns:
            True when no passage in the corpus answers the question.
        """
        return not self.evidence

    def meeting_ids(self) -> set[str]:
        """Meetings this answer draws on.

        Returns:
            The distinct meeting ids across every evidence span.
        """
        return {span.meeting_id for span in self.evidence}


def load_questions(path: Path) -> list[RagQuestion]:
    """Read the public question file.

    Args:
        path: ``questions.jsonl``.

    Returns:
        Questions in file order.

    Raises:
        OSError: The file could not be read.
        pydantic.ValidationError: A line is not a valid question.
    """
    return [
        RagQuestion.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_expected(directory: Path) -> dict[str, ExpectedAnswer]:
    """Read the unsealed expected answers.

    Args:
        directory: ``eval/rag/expected``, after unsealing.

    Returns:
        ``{question_id: ExpectedAnswer}``.

    Raises:
        ConfigError: The directory holds no expected answers, which would let a gate run
            score every question as unanswerable and report a perfect abstention rate.
        OSError: A file could not be read.
        pydantic.ValidationError: A file is not a valid expected answer.
    """
    files = sorted(path for path in directory.glob("*.json") if path.name != "seal-manifest.json")
    if not files:
        raise ConfigError(
            f"no expected answers in {directory} — the set is sealed, or was never "
            "written. Unseal with `uv run python scripts/seal_heldout.py unseal --dir "
            f"{directory}` before scoring."
        )
    answers = [ExpectedAnswer.model_validate_json(path.read_text(encoding="utf-8")) for path in files]
    return {answer.question_id: answer for answer in answers}


def save_expected(answer: ExpectedAnswer, directory: Path) -> Path:
    """Write one expected answer.

    Args:
        answer: Answer to persist.
        directory: Destination, created if absent.

    Returns:
        The path written.

    Raises:
        OSError: The file could not be written.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{answer.question_id}.json"
    path.write_text(
        json.dumps(answer.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def validate_question_set(
    questions: list[RagQuestion],
    expected: dict[str, ExpectedAnswer],
    *,
    reference_dir: Path = DEFAULT_REFERENCE_DIR,
) -> list[str]:
    """Check the set is well formed and every citation resolves.

    This is the ticket's "second pass a few hours later", made mechanical. The manual
    version — *can each answerable question really be answered from the recorded
    segment?* — is a judgement no code can make, and it is recorded per question in
    ``notes``. What code **can** check is that the judgement points somewhere real, and a
    broken pointer corrupts the gate exactly as badly as a broken judgement.

    Args:
        questions: The public questions.
        expected: Expected answers by question id.
        reference_dir: Where the reference transcripts live.

    Returns:
        Human-readable problems, empty when the set is sound.

    Raises:
        OSError: A reference transcript could not be read.
    """
    problems: list[str] = []
    ids = [question.question_id for question in questions]

    problems += [f"{qid}: duplicate question id" for qid, n in Counter(ids).items() if n > 1]
    problems += [f"{qid}: question has no expected answer" for qid in ids if qid not in expected]
    problems += [f"{qid}: expected answer has no question" for qid in expected if qid not in set(ids)]

    counts = Counter(question.kind for question in questions)
    for kind, wanted in (
        (QuestionKind.SINGLE_MEETING, SINGLE_MEETING_COUNT),
        (QuestionKind.CROSS_MEETING, CROSS_MEETING_COUNT),
        (QuestionKind.UNANSWERABLE, UNANSWERABLE_COUNT),
    ):
        if counts.get(kind, 0) != wanted:
            problems.append(f"{kind.value}: {counts.get(kind, 0)} questions, the ticket asks for {wanted}")

    # Cached per meeting: fifteen questions can name the same transcript, and re-reading it
    # per span would make validation slower than the eval it guards.
    lengths: dict[str, int] = {}
    for question in questions:
        answer = expected.get(question.question_id)
        if answer is None:
            continue

        if question.kind is QuestionKind.UNANSWERABLE and not answer.must_abstain:
            problems.append(f"{question.question_id}: marked unanswerable but carries evidence")
        if question.kind is not QuestionKind.UNANSWERABLE and answer.must_abstain:
            problems.append(f"{question.question_id}: answerable but names no evidence")
        if question.kind is not QuestionKind.UNANSWERABLE and not answer.gist:
            problems.append(f"{question.question_id}: answerable but records no expected gist")
        if question.kind is QuestionKind.CROSS_MEETING and len(answer.meeting_ids()) < 2:
            problems.append(
                f"{question.question_id}: marked cross-meeting but its evidence is all in "
                f"{answer.meeting_ids() or 'no meeting'} — a question one meeting answers "
                "measures single-meeting retrieval under a cross-meeting label"
            )
        if question.kind is QuestionKind.SINGLE_MEETING and len(answer.meeting_ids()) > 1:
            problems.append(
                f"{question.question_id}: marked single-meeting but draws on "
                f"{len(answer.meeting_ids())} meetings"
            )

        for span in answer.evidence:
            if span.meeting_id not in lengths:
                try:
                    lengths[span.meeting_id] = len(
                        load_reference_segments(span.meeting_id, reference_dir=reference_dir)
                    )
                except ConfigError as error:
                    lengths[span.meeting_id] = -1
                    problems.append(f"{question.question_id}: {error}")
            turns = lengths[span.meeting_id]
            if turns >= 0 and span.last_turn >= turns:
                problems.append(
                    f"{question.question_id}: cites turn {span.last_turn} of "
                    f"{span.meeting_id}, which has {turns} — the reference changed under "
                    "this question and its evidence can no longer be trusted"
                )
    return problems
