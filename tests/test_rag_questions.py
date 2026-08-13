"""Tests for the Phase 2 question set and its validator.

The validator is the only thing standing between a broken question and a gate number that
looks fine, so what is under test is its **refusals**. A validator that passes a
cross-meeting question answerable from one meeting, or a citation into a turn that does not
exist, is worse than no validator: it converts an unchecked set into a checked-looking one.

The committed set is exercised too. Those tests fail if anyone edits `questions.jsonl`
without re-running the generator, which is exactly when the two halves drift apart.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from m2x.rag_questions import (
    CROSS_MEETING_COUNT,
    DEFAULT_RAG_EVAL_DIR,
    EXPECTED_DIRNAME,
    QUESTIONS_FILENAME,
    SINGLE_MEETING_COUNT,
    UNANSWERABLE_COUNT,
    EvidenceSpan,
    ExpectedAnswer,
    QuestionKind,
    RagQuestion,
    load_questions,
    save_expected,
    validate_question_set,
)

MEETING = "ref-001"
OTHER = "ref-002"


def _write_reference(directory: Path, meeting_id: str = MEETING, count: int = 40) -> None:
    """Write a reference pair the spans can resolve against.

    Args:
        directory: Reference directory, created if absent.
        meeting_id: Meeting the pair describes.
        count: How many turns.
    """
    directory.mkdir(parents=True, exist_ok=True)
    turns = [
        {"t_start": float(i), "t_end": float(i) + 0.5, "speaker": f"spk-{i % 2}"}
        for i in range(count)
    ]
    (directory / f"{meeting_id}.speakers.json").write_text(
        json.dumps({"meeting_id": meeting_id, "segments": turns}), encoding="utf-8"
    )
    (directory / f"{meeting_id}.txt").write_text(
        "\n".join(f"turn {i}" for i in range(count)) + "\n", encoding="utf-8"
    )


def _span(meeting_id: str = MEETING, first: int = 1, last: int = 3) -> EvidenceSpan:
    """Build one evidence span."""
    return EvidenceSpan(meeting_id=meeting_id, first_turn=first, last_turn=last)


def _set(*, cross_spans: list[EvidenceSpan] | None = None) -> tuple[list, dict]:
    """Build a well-formed set of the ticket's exact shape.

    Args:
        cross_spans: Override the evidence on the first cross-meeting question, which is
            how the cross-meeting refusal is exercised.

    Returns:
        ``(questions, expected)``.
    """
    questions: list[RagQuestion] = []
    expected: dict[str, ExpectedAnswer] = {}
    index = 0

    def add(kind: QuestionKind, spans: list[EvidenceSpan]) -> None:
        nonlocal index
        index += 1
        qid = f"q{index:02d}"
        questions.append(RagQuestion(question_id=qid, kind=kind, question=f"question {qid}?"))
        expected[qid] = ExpectedAnswer(
            question_id=qid, gist="the answer" if spans else "", evidence=spans
        )

    for _ in range(SINGLE_MEETING_COUNT):
        add(QuestionKind.SINGLE_MEETING, [_span()])
    for position in range(CROSS_MEETING_COUNT):
        spans = (
            cross_spans
            if position == 0 and cross_spans is not None
            else [_span(), _span(OTHER, 2, 4)]
        )
        add(QuestionKind.CROSS_MEETING, spans)
    for _ in range(UNANSWERABLE_COUNT):
        add(QuestionKind.UNANSWERABLE, [])
    return questions, expected


@pytest.fixture
def reference(tmp_path: Path) -> Path:
    """A reference directory holding both test meetings."""
    _write_reference(tmp_path, MEETING)
    _write_reference(tmp_path, OTHER)
    return tmp_path


def test_a_well_formed_set_validates(reference: Path) -> None:
    """The baseline, so the refusals below mean something."""
    questions, expected = _set()

    assert validate_question_set(questions, expected, reference_dir=reference) == []


def test_a_cross_meeting_question_answerable_from_one_meeting_is_rejected(reference: Path) -> None:
    """The refusal that matters most.

    A cross-meeting question whose evidence sits in one meeting measures single-meeting
    retrieval under a cross-meeting label, and it would inflate the hardest bucket in the
    set without anything in the file looking wrong.
    """
    questions, expected = _set(cross_spans=[_span(MEETING, 1, 3), _span(MEETING, 10, 12)])

    problems = validate_question_set(questions, expected, reference_dir=reference)

    assert any("cross-meeting" in problem for problem in problems)


def test_a_span_past_the_end_of_the_reference_is_rejected(reference: Path) -> None:
    """If the reference changed, the question's evidence points at different words.

    Same failure the labelled cases guard against: turn indices are positions in a file,
    and a file that shrank silently re-aims every question after the edit.
    """
    questions, expected = _set()
    expected["q01"] = ExpectedAnswer(
        question_id="q01", gist="the answer", evidence=[_span(MEETING, 900, 950)]
    )

    problems = validate_question_set(questions, expected, reference_dir=reference)

    assert any("the reference changed" in problem for problem in problems)


def test_an_unknown_meeting_is_rejected(reference: Path) -> None:
    """A typo in a meeting id must fail loudly, not silently score zero citations."""
    questions, expected = _set()
    expected["q01"] = ExpectedAnswer(
        question_id="q01", gist="the answer", evidence=[_span("ref-does-not-exist")]
    )

    problems = validate_question_set(questions, expected, reference_dir=reference)

    assert any("does not exist" in problem for problem in problems)


def test_an_answerable_question_with_no_evidence_is_rejected(reference: Path) -> None:
    """Empty evidence means must-abstain, so this would flip a scored case silently."""
    questions, expected = _set()
    expected["q01"] = ExpectedAnswer(question_id="q01", gist="the answer", evidence=[])

    problems = validate_question_set(questions, expected, reference_dir=reference)

    assert any("names no evidence" in problem for problem in problems)


def test_an_unanswerable_question_carrying_evidence_is_rejected(reference: Path) -> None:
    """The inverse: a must-abstain case that is secretly answerable grades abstention wrong."""
    questions, expected = _set()
    expected["q30"] = ExpectedAnswer(question_id="q30", evidence=[_span()])

    problems = validate_question_set(questions, expected, reference_dir=reference)

    assert any("marked unanswerable but carries evidence" in problem for problem in problems)


def test_the_wrong_mix_is_rejected(reference: Path) -> None:
    """20/5/5 is the ticket's number, not a preference.

    Without this the set could drift toward whichever kind the system handles well, and
    the gate figure would move without any question changing.
    """
    questions, expected = _set()
    dropped = questions.pop()
    expected.pop(dropped.question_id)

    problems = validate_question_set(questions, expected, reference_dir=reference)

    assert any("the ticket asks for" in problem for problem in problems)


def test_a_question_without_an_expected_answer_is_rejected(reference: Path) -> None:
    """The two halves live in different files and one is sealed, so they can drift."""
    questions, expected = _set()
    expected.pop("q01")

    problems = validate_question_set(questions, expected, reference_dir=reference)

    assert any("no expected answer" in problem for problem in problems)


def test_must_abstain_is_derived_not_stored() -> None:
    """One fact, one field.

    A separate boolean beside the evidence list would let the two disagree, and the
    disagreement would turn a must-abstain case into a scored one without looking wrong.
    """
    assert ExpectedAnswer(question_id="q01").must_abstain
    assert not ExpectedAnswer(question_id="q01", gist="x", evidence=[_span()]).must_abstain


def test_an_inverted_span_is_rejected() -> None:
    """Bounds are checked at construction, so a bad span cannot reach the scorer."""
    with pytest.raises(ValueError, match="inverted"):
        EvidenceSpan(meeting_id=MEETING, first_turn=9, last_turn=2)


@pytest.mark.parametrize(
    ("first", "last", "expected_overlap"),
    [(0, 0, False), (0, 1, True), (2, 2, True), (3, 9, True), (4, 9, False), (0, 99, True)],
)
def test_overlap_is_inclusive_at_both_ends(first: int, last: int, expected_overlap: bool) -> None:
    """Citation accuracy is an overlap test, so its boundaries are the metric.

    An off-by-one here shifts every citation score in the gate by up to one chunk.
    """
    assert _span(MEETING, 1, 3).overlaps(first, last) is expected_overlap


def test_the_committed_set_is_the_shape_the_ticket_asks_for() -> None:
    """Guards the real file, not a fixture.

    The expected answers are sealed, so this half is what a reviewer without the
    passphrase can still check.
    """
    questions = load_questions(DEFAULT_RAG_EVAL_DIR / QUESTIONS_FILENAME)
    counts = {kind: sum(1 for q in questions if q.kind is kind) for kind in QuestionKind}

    assert len(questions) == SINGLE_MEETING_COUNT + CROSS_MEETING_COUNT + UNANSWERABLE_COUNT
    assert counts[QuestionKind.SINGLE_MEETING] == SINGLE_MEETING_COUNT
    assert counts[QuestionKind.CROSS_MEETING] == CROSS_MEETING_COUNT
    assert counts[QuestionKind.UNANSWERABLE] == UNANSWERABLE_COUNT
    assert len({q.question_id for q in questions}) == len(questions)


def test_the_committed_questions_never_leak_their_answers() -> None:
    """The public half must stay public-safe.

    `questions.jsonl` is committed in plaintext; if a gist or an evidence span ever
    appeared in it the seal on `expected/` would be decorative.
    """
    text = (DEFAULT_RAG_EVAL_DIR / QUESTIONS_FILENAME).read_text(encoding="utf-8")

    assert "gist" not in text
    assert "evidence" not in text
    assert "first_turn" not in text


def test_round_trip_preserves_an_expected_answer(tmp_path: Path) -> None:
    """A sealed answer must read back identical, or the digest check is meaningless."""
    answer = ExpectedAnswer(
        question_id="q01", gist="the answer", evidence=[_span()], notes="why"
    )

    written = save_expected(answer, tmp_path)

    assert ExpectedAnswer.model_validate_json(written.read_text(encoding="utf-8")) == answer
