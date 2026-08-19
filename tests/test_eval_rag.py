"""Tests for the Phase 2 harness, built around the ticket's three known-outcome cases.

M2X-046 asks for a sanity test on *"3 hand-constructed cases with known outcomes (perfect
answer, unfaithful answer, wrong citation)"*, and names the property the reviewer must
check: citation accuracy **must fail a citation whose text merely sounds right but points
at the wrong segment**. That is what
:func:`test_a_wrong_citation_fails_however_plausible_its_quote` exists for, and it is why
the wrong-citation case quotes the *correct* answer while citing the *wrong* chunk — a
checker that consulted the text at all would pass it.

Everything here runs with no judge model and no ``ragas`` installed. The harness takes its
judge as a callable for that reason: an optional dependency that pulls the whole langchain
stack must not decide whether the mechanical half of the gate can be tested.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from m2x.ask import AskOutcome, ResolvedCitation
from m2x.eval_rag import (
    CITATION_ACCURACY_FLOOR,
    QuestionScore,
    RagReport,
    append_rag_result,
    chunk_turn_range,
    format_rag_report,
    run_rag_eval,
    score_citations,
)
from m2x.rag_questions import EvidenceSpan, ExpectedAnswer, QuestionKind, RagQuestion
from m2x.types import Provider
from m2x.vector_store import Hit

MEETING = "tiron-MTG_32185"
FIXED_TIME = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

# Ground truth: the answer lives in turns 23-28 (0-based).
RIGHT_CHUNK = "chunk-right"
WRONG_CHUNK = "chunk-wrong"


def _hit(chunk_id: str, *, first_turn: int, last_turn: int, text: str) -> Hit:
    """A retrieved chunk covering a turn range.

    Args:
        chunk_id: Chunk id the citation will name.
        first_turn: First covered turn, 0-based — converted to the 1-based form the
            indexer stores, so the fixture exercises the real boundary.
        last_turn: Last covered turn, 0-based inclusive.
        text: Passage text.

    Returns:
        The hit.
    """
    return Hit(
        chunk_id=chunk_id,
        text=text,
        distance=0.3,
        metadata={
            "source_id": MEETING,
            "source_type": "meeting",
            "segment_start": first_turn + 1,
            "segment_end": last_turn + 1,
            "ordinal": 0,
        },
    )


def _outcome(
    *,
    answer: str,
    cited_chunks: list[str],
    quote: str,
    abstained: bool = False,
) -> AskOutcome:
    """Build an answered outcome over two retrieved chunks, one right and one wrong.

    Args:
        answer: The answer text.
        cited_chunks: Chunk ids the citations name.
        quote: Verbatim quote carried by every citation.
        abstained: Whether the system declined.

    Returns:
        The outcome.
    """
    return AskOutcome(
        question="What is holding up the aquarium according to the legal department?",
        answer=answer,
        citations=[
            ResolvedCitation(
                passage_ref=f"C{index + 1}",
                quote=quote,
                reference=f"[{MEETING} · John · 1:40–1:58]",
                chunk_id=chunk_id,
                source_id=MEETING,
                distance=0.3,
            )
            for index, chunk_id in enumerate(cited_chunks)
        ],
        abstained=abstained,
        retrieved=2,
        hits=[
            _hit(RIGHT_CHUNK, first_turn=23, last_turn=28, text="John: we need a lot of legal documents"),
            _hit(WRONG_CHUNK, first_turn=60, last_turn=68, text="John: the parking lot is finished"),
        ],
        nearest_distance=0.3,
        max_distance=0.48,
        prompt_name="rag",
        prompt_version="v2",
        model_repo_id="meta-llama/Llama-3.1-8B-Instruct",
        provider=Provider.OLLAMA,
        attempts=1,
        latency_ms=10,
    )


def _expected(*, must_abstain: bool = False) -> ExpectedAnswer:
    """Ground truth naming turns 23-28 of the mall meeting.

    Args:
        must_abstain: Build the unanswerable variant instead.

    Returns:
        The expected answer.
    """
    if must_abstain:
        return ExpectedAnswer(question_id="q26")
    return ExpectedAnswer(
        question_id="q01",
        gist="Legal documents are needed to bring in the water and the fish.",
        evidence=[EvidenceSpan(meeting_id=MEETING, first_turn=23, last_turn=28)],
    )


# --------------------------------------------------------------------------------------
# The ticket's three known-outcome cases
# --------------------------------------------------------------------------------------


def test_a_perfect_answer_scores_every_citation_correct() -> None:
    """Case 1: right answer, right citation. The baseline the other two are read against."""
    outcome = _outcome(
        answer="Legal documents are needed to bring in the water and the fish.",
        cited_chunks=[RIGHT_CHUNK],
        quote="we need a lot of legal documents",
    )

    assert score_citations(outcome, _expected()) == (1, 1)


def test_an_unfaithful_answer_still_scores_its_citation_correct() -> None:
    """Case 2: wrong answer, right citation — and citation accuracy must not notice.

    This is the separation the four metrics exist to maintain. An answer that contradicts
    the passage it cites is a **faithfulness** failure; the citation itself still points
    where it claims. A citation checker that also punished unfaithfulness would double-count
    one error and leave faithfulness unable to move independently.
    """
    outcome = _outcome(
        answer="The aquarium is fully approved and opens next week.",
        cited_chunks=[RIGHT_CHUNK],
        quote="we need a lot of legal documents",
    )

    assert score_citations(outcome, _expected()) == (1, 1)


def test_a_wrong_citation_fails_however_plausible_its_quote() -> None:
    """Case 3: right answer, wrong segment. The property the ticket asks to be reviewed.

    The quote is lifted verbatim from the *correct* passage while the citation names the
    *parking lot* chunk. Any check that consulted the text — substring, embedding,
    anything — passes this. Only resolving the chunk to its turn range catches it.
    """
    outcome = _outcome(
        answer="Legal documents are needed to bring in the water and the fish.",
        cited_chunks=[WRONG_CHUNK],
        quote="we need a lot of legal documents",
    )

    assert score_citations(outcome, _expected()) == (1, 0)


# --------------------------------------------------------------------------------------
# The 1-based/0-based boundary
# --------------------------------------------------------------------------------------


def test_chunk_turn_range_converts_off_the_indexers_one_based_form() -> None:
    """The single conversion point. An error here shifts every citation score by one."""
    assert chunk_turn_range({"segment_start": 1, "segment_end": 6}) == (0, 5)


def test_a_document_chunk_has_no_turn_range() -> None:
    """Docs carry no segment range and can never satisfy a meeting span."""
    assert chunk_turn_range({"source_id": "readme", "heading": "Scope"}) is None


@pytest.mark.parametrize(
    ("first", "last", "correct"),
    [(18, 22, 0), (18, 23, 1), (28, 33, 1), (29, 33, 0)],
)
def test_citation_overlap_is_inclusive_at_both_boundaries(
    first: int, last: int, correct: int
) -> None:
    """A chunk that just touches the ground-truth span counts; one turn further does not.

    Chunks overlap by a segment, so the touching case is the common one rather than an
    edge case, and getting it wrong moves the headline by a whole chunk per question.
    """
    outcome = _outcome(
        answer="whatever", cited_chunks=["chunk-edge"], quote="whatever"
    )
    edge = _hit("chunk-edge", first_turn=first, last_turn=last, text="edge")
    outcome = outcome.model_copy(update={"hits": [*outcome.hits, edge]})

    assert score_citations(outcome, _expected())[1] == correct


def test_a_citation_naming_a_passage_that_was_not_retrieved_is_incorrect() -> None:
    """It cannot be verified, so it cannot be counted correct.

    `ask` already rejects an unresolvable reference inside its retry loop, so this should
    be unreachable — but scoring it as correct by default would make the checker's
    strictest guarantee depend on another module staying strict.
    """
    outcome = _outcome(answer="x", cited_chunks=["chunk-never-retrieved"], quote="x")

    assert score_citations(outcome, _expected()) == (1, 0)


def test_a_citation_into_the_right_turns_of_the_wrong_meeting_is_incorrect() -> None:
    """Turn indices are only meaningful inside a meeting.

    Turns 23-28 exist in every meeting in the corpus; without the meeting check, a
    citation into an unrelated meeting would score correct on the numbers alone.
    """
    outcome = _outcome(answer="x", cited_chunks=[RIGHT_CHUNK], quote="x")

    other = _expected().model_copy(
        update={"evidence": [EvidenceSpan(meeting_id="tiron-MTG_32092", first_turn=23, last_turn=28)]}
    )

    assert score_citations(outcome, other) == (1, 0)


# --------------------------------------------------------------------------------------
# Abstention, aggregation and the record
# --------------------------------------------------------------------------------------


def _run(questions, expected, outcomes):  # type: ignore[no-untyped-def]
    """Drive the harness with a stubbed ask and a stubbed judge.

    Args:
        questions: Questions to run.
        expected: Ground truth by id.
        outcomes: Outcome per question id.

    Returns:
        The report.
    """
    import m2x.eval_rag as module

    original = module.ask
    module.ask = lambda question, **kwargs: outcomes[question]  # type: ignore[assignment]
    try:
        report, _ = run_rag_eval(
            questions,
            expected,
            store=None,  # type: ignore[arg-type]
            adapter=None,  # type: ignore[arg-type]
            judge=lambda outcome, answer: (0.9, 0.8),
        )
    finally:
        module.ask = original
    return report


def test_abstaining_on_an_unanswerable_question_is_correct() -> None:
    """The must-abstain half of the abstention score."""
    question = RagQuestion(question_id="q26", kind=QuestionKind.UNANSWERABLE, question="cost?")
    outcome = _outcome(answer="", cited_chunks=[], quote="", abstained=True).model_copy(
        update={"question": "cost?"}
    )

    report = _run([question], {"q26": _expected(must_abstain=True)}, {"cost?": outcome})

    assert report.abstention_accuracy == pytest.approx(1.0)


def test_answering_an_unanswerable_question_is_incorrect() -> None:
    """The failure the bucket exists to catch: a confident answer to nothing.

    Four of the five must-abstain questions sit on top of a strongly matching passage, so
    retrieval succeeds and only the model's judgement stands between the corpus and a
    fabrication.
    """
    question = RagQuestion(question_id="q26", kind=QuestionKind.UNANSWERABLE, question="cost?")
    outcome = _outcome(
        answer="It cost about two million.", cited_chunks=[RIGHT_CHUNK], quote="x"
    ).model_copy(update={"question": "cost?"})

    report = _run([question], {"q26": _expected(must_abstain=True)}, {"cost?": outcome})

    assert report.abstention_accuracy == pytest.approx(0.0)


def test_abstaining_on_an_answerable_question_is_incorrect() -> None:
    """A system that abstains on everything must not score well."""
    question = RagQuestion(question_id="q01", kind=QuestionKind.SINGLE_MEETING, question="why?")
    outcome = _outcome(answer="", cited_chunks=[], quote="", abstained=True).model_copy(
        update={"question": "why?"}
    )

    report = _run([question], {"q01": _expected()}, {"why?": outcome})

    assert report.abstention_accuracy == pytest.approx(0.0)


def test_an_unanswerable_question_is_never_judged_by_ragas() -> None:
    """Context precision is undefined with no reference contexts.

    Judging it anyway scores the model on an impossible task and drags the headline down
    for behaving correctly — the question has no relevant passage to have retrieved.
    """
    question = RagQuestion(question_id="q26", kind=QuestionKind.UNANSWERABLE, question="cost?")
    outcome = _outcome(answer="two million", cited_chunks=[], quote="").model_copy(
        update={"question": "cost?"}
    )

    report = _run([question], {"q26": _expected(must_abstain=True)}, {"cost?": outcome})

    assert report.scores[0].context_precision is None
    assert report.scores[0].faithfulness is None


def test_citation_accuracy_is_micro_not_macro() -> None:
    """One sloppy answer with many citations must not hide behind a careful one.

    Macro would average 1.0 and 0.25 to 0.625 here; micro gives 2/5.
    """
    report = RagReport(
        scores=[
            QuestionScore(
                question_id="q01",
                kind=QuestionKind.SINGLE_MEETING,
                abstained=False,
                abstention_correct=True,
                citations=1,
                citations_correct=1,
            ),
            QuestionScore(
                question_id="q02",
                kind=QuestionKind.SINGLE_MEETING,
                abstained=False,
                abstention_correct=True,
                citations=4,
                citations_correct=1,
            ),
        ]
    )

    assert report.citation_accuracy == pytest.approx(0.4)


def test_uncited_answers_are_counted_separately() -> None:
    """A system that stopped citing would score 0.0 for having no denominator.

    That reads identically to a system whose every citation was wrong, so the count is
    reported beside the ratio.
    """
    report = RagReport(
        scores=[
            QuestionScore(
                question_id="q01",
                kind=QuestionKind.SINGLE_MEETING,
                abstained=False,
                abstention_correct=True,
                citations=0,
            )
        ]
    )

    assert report.uncited_answers == 1
    assert report.citation_accuracy == pytest.approx(0.0)


def test_a_judge_failure_is_recorded_not_averaged_as_zero() -> None:
    """A judge that errored produced no evidence, and a zero is evidence of badness."""
    question = RagQuestion(question_id="q01", kind=QuestionKind.SINGLE_MEETING, question="why?")
    outcome = _outcome(answer="a", cited_chunks=[RIGHT_CHUNK], quote="x").model_copy(
        update={"question": "why?"}
    )

    import m2x.eval_rag as module

    original = module.ask
    module.ask = lambda q, **kwargs: outcome  # type: ignore[assignment]
    try:

        def explode(outcome, answer):  # type: ignore[no-untyped-def]
            raise RuntimeError("judge exploded")

        report, _ = run_rag_eval(
            [question],
            {"q01": _expected()},
            store=None,  # type: ignore[arg-type]
            adapter=None,  # type: ignore[arg-type]
            judge=explode,
        )
    finally:
        module.ask = original

    assert report.judge_failures == ["q01: judge exploded"]
    assert report.faithfulness == pytest.approx(0.0)
    # Still scored mechanically: a judge failure must not cost the citation check.
    assert report.citation_accuracy == pytest.approx(1.0)


def test_passes_marks_each_leg_against_its_prd_floor() -> None:
    """The gate's binary criteria, so the exit code can read them."""
    report = RagReport(
        scores=[
            QuestionScore(
                question_id="q01",
                kind=QuestionKind.SINGLE_MEETING,
                abstained=False,
                abstention_correct=True,
                citations=10,
                citations_correct=9,
                context_precision=0.80,
                faithfulness=0.70,
            )
        ]
    )

    assert report.passes() == {
        "context_precision": True,
        "faithfulness": False,
        "citation_accuracy": True,
    }
    assert report.citation_accuracy >= CITATION_ACCURACY_FLOOR


def test_the_report_names_which_metrics_are_a_models_opinion() -> None:
    """Two of the four figures are a judge's view, and the table must say so.

    A gate record quoting four numbers as though they were the same kind of claim is the
    thing this project keeps having to repair after the fact.
    """
    text = format_rag_report(RagReport(scores=[]))

    assert "RAGAS (judge LLM)" in text
    assert "mechanical" in text


def test_the_results_row_records_every_knob_that_moves_the_number(tmp_path: Path) -> None:
    """top_k and max_distance are unmeasured defaults (M2X-042 §OPEN).

    A row that cannot name them is a number about an unknown configuration.
    """
    path = append_rag_result(
        RagReport(scores=[]),
        prompt_version="v2",
        model_repo_id="meta-llama/Llama-3.1-8B-Instruct",
        judge_model_repo_id="meta-llama/Llama-3.1-8B-Instruct",
        embed_model_repo_id="nomic-ai/nomic-embed-text-v1.5",
        top_k=5,
        max_distance=0.48,
        path=tmp_path / "rag.jsonl",
        git_sha="abc1234",
        now=lambda: FIXED_TIME,
    )

    row = json.loads(path.read_text(encoding="utf-8").strip())

    assert row["top_k"] == 5
    assert row["max_distance"] == 0.48
    assert row["judge_model_repo_id"] == "meta-llama/Llama-3.1-8B-Instruct"
    assert row["embed_model_repo_id"] == "nomic-ai/nomic-embed-text-v1.5"
    assert row["git_sha"] == "abc1234"
    assert row["timestamp"] == FIXED_TIME.isoformat()
