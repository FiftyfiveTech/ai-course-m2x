"""Tests for cited answers and abstention (``m2x ask``).

The load-bearing ones are the citation tests. A fabricated citation is the failure this
feature exists to prevent, so the suite comes at it from four directions: a reference that
was never retrieved, a quote that is not in the passage it cites, a quote too short to
evidence anything, and an answer that cites nothing at all. Each is rejected inside the
retry loop, and each ends in an abstention rather than an answer when the retry does not
fix it.

Distances from :func:`conftest.fake_vector` are arbitrary by construction, so no test here
asserts on a particular distance. Threshold behaviour is exercised by passing
``max_distance`` explicitly — 0.0 to force the gate shut, 2.0 (the cosine maximum) to hold
it open — which is also the honest thing to do given the threshold is provisional.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
from pydantic import ValidationError

from m2x.adapter import ModelAdapter
from m2x.ask import (
    ABSTAIN_TEXT,
    PASSAGE_CONTEXT_KEY,
    AbstentionReason,
    AnswerDraft,
    Citation,
    ask,
    normalise,
    render_reference,
    render_passages,
)
from m2x.indexing import SourceType
from m2x.run_log import RunLogger
from m2x.types import Provider, Transcript, TranscriptSegment
from m2x.vector_store import Hit, VectorStore, build_index
from conftest import CHAT_MODEL, EMBED_MODEL, chat_response, embeddings_response, fake_vector

AdapterFactory = Callable[..., ModelAdapter]

MIGRATION_TEXT = "we agreed to postpone the postgres migration until the audit clears"
"""One distinctive sentence, long enough to quote from and short enough to read."""


def _transcript() -> Transcript:
    """A two-segment diarised transcript with quotable content."""
    return Transcript(
        model_repo_id="openai/whisper-large-v3",
        provider=Provider.GROQ,
        latency_ms=1,
        text="meeting",
        audio_seconds=120.0,
        segments=[
            TranscriptSegment(t_start=0.0, t_end=60.0, text=MIGRATION_TEXT, speaker="Yash"),
            TranscriptSegment(
                t_start=60.0, t_end=120.0, text="the sprint demo moves to friday", speaker="Saurabh"
            ),
        ],
    )


def _hit(**metadata: Any) -> Hit:
    """A hit carrying only the metadata a citation renderer reads."""
    return Hit(chunk_id="abc", text=MIGRATION_TEXT, distance=0.3, metadata=metadata)


def _draft(answer: str, citations: list[dict[str, str]], *, abstained: bool = False) -> str:
    """Render what the model would return, as JSON."""
    return json.dumps({"answer": answer, "citations": citations, "abstained": abstained})


def _handler(*replies: str) -> Callable[[httpx.Request], httpx.Response]:
    """Answer embeddings deterministically and chat with the supplied replies in order.

    One handler serves both legs because ``ask`` uses one adapter for both, exactly as the
    command does — a separate embedding stub would not exercise that wiring.
    """
    remaining = list(replies)

    def handle(request: httpx.Request) -> httpx.Response:
        if "embeddings" in request.url.path:
            return httpx.Response(200, json=embeddings_response(json.loads(request.content)["input"]))
        reply = remaining.pop(0) if remaining else remaining_exhausted()
        return httpx.Response(200, json=chat_response(reply))

    return handle


def remaining_exhausted() -> str:
    """Fail loudly when the model is called more often than the test scripted."""
    raise AssertionError("the model was called more times than the test supplied replies")


@pytest.fixture
def store(make_adapter: AdapterFactory, tmp_path: Path) -> VectorStore:
    """An index holding one two-segment meeting."""
    store = VectorStore(tmp_path / "index", collection="test", embed_model_repo_id=EMBED_MODEL)
    with make_adapter(_handler()) as adapter:
        build_index(store, adapter, transcripts=[("mtg-001", _transcript())])
    return store


# --- citation validation ------------------------------------------------------------


def _context(**passages: str) -> dict[str, dict[str, str]]:
    """Validation context mapping passage references to their normalised text.

    Built with :func:`normalise`, the same function ``ask`` builds it with. A test helper
    that folded differently would be testing a validator nothing else uses.
    """
    return {PASSAGE_CONTEXT_KEY: {ref: normalise(text) for ref, text in passages.items()}}


def test_a_citation_to_a_passage_that_was_not_retrieved_is_rejected() -> None:
    """The fabricated-citation path, in its simplest form."""
    with pytest.raises(ValidationError, match="was not retrieved"):
        Citation.model_validate(
            {"passage_ref": "C9", "quote": MIGRATION_TEXT},
            context=_context(C1=MIGRATION_TEXT),
        )


def test_a_quote_that_is_not_in_the_cited_passage_is_rejected() -> None:
    """The harder fabrication: a real passage cited for a claim it does not make."""
    with pytest.raises(ValidationError, match="not in passage C1"):
        Citation.model_validate(
            {"passage_ref": "C1", "quote": "we agreed to cancel the audit entirely"},
            context=_context(C1=MIGRATION_TEXT),
        )


def test_a_quote_too_short_to_evidence_anything_is_rejected() -> None:
    """Without a floor the substring check is theatre — 'the' is in every passage."""
    with pytest.raises(ValidationError, match="too short"):
        Citation.model_validate(
            {"passage_ref": "C1", "quote": "the"},
            context=_context(C1=MIGRATION_TEXT),
        )


def test_a_quote_is_matched_across_whitespace_and_case() -> None:
    """A model re-typing a phrase across a line break is quoting correctly."""
    citation = Citation.model_validate(
        {"passage_ref": "C1", "quote": "Postpone   the\n  Postgres migration"},
        context=_context(C1=MIGRATION_TEXT),
    )
    assert citation.passage_ref == "C1"


def test_a_quote_is_matched_through_markdown_emphasis() -> None:
    """The corpus is markdown; the model quotes what it reads as prose.

    Not hypothetical. The first live run of this command abstained on an answerable
    question because the handbook says ``**Citation accuracy**`` and Llama-3.1-8B quoted
    "Citation accuracy" — a formatting difference rejected as a fabricated quote.
    """
    citation = Citation.model_validate(
        {"passage_ref": "C1", "quote": "Citation accuracy (>=0.90): does the cited segment"},
        context=_context(C1="**Citation accuracy** (>=0.90): does the cited segment contain it"),
    )
    assert citation.passage_ref == "C1"


def test_wording_is_still_checked_after_normalisation() -> None:
    """The folding must not quietly turn the substring test into a similarity test."""
    with pytest.raises(ValidationError, match="not in passage C1"):
        Citation.model_validate(
            {"passage_ref": "C1", "quote": "the postgres migration was approved"},
            context=_context(C1=MIGRATION_TEXT),
        )


def test_citations_degrade_to_structural_checks_without_context() -> None:
    """An outcome read back off disk has no retrieved set to resolve against."""
    citation = Citation.model_validate({"passage_ref": "C1", "quote": "anything long enough"})
    assert citation.passage_ref == "C1"


def test_an_answer_that_cites_nothing_is_rejected() -> None:
    """An uncited answer is the ungrounded answer this module exists to prevent."""
    with pytest.raises(ValidationError, match="must cite at least one passage"):
        AnswerDraft.model_validate({"answer": "they postponed it", "citations": []})


def test_an_abstention_needs_no_citations() -> None:
    """Abstention is a result, and a result with no sources is the correct shape for it."""
    draft = AnswerDraft.model_validate({"answer": "", "citations": [], "abstained": True})
    assert draft.abstained


# --- citation rendering -------------------------------------------------------------


def test_a_meeting_citation_reads_meeting_speaker_timestamps() -> None:
    """The format the ticket specifies, built from metadata rather than model output."""
    reference = render_reference(
        _hit(
            source_id="mtg-001",
            source_type=SourceType.MEETING.value,
            speakers="Yash",
            t_start=872.0,
            t_end=887.0,
        )
    )
    assert reference == "[mtg-001 · Yash · 14:32–14:47]"


def test_a_document_citation_reads_its_heading_rather_than_a_timestamp() -> None:
    """Documents have no clock. Inventing one would be the failure being prevented."""
    reference = render_reference(
        _hit(source_id="readme", source_type=SourceType.DOC.value, heading="Scope")
    )
    assert reference == "[readme · § Scope]"


def test_passages_are_labelled_for_the_model_to_cite() -> None:
    """The reference the model cites has to be visible in the block it reads."""
    block = render_passages([_hit(source_id="mtg-001", source_type=SourceType.DOC.value)])
    assert block.startswith("[C1] [mtg-001]")
    assert MIGRATION_TEXT in block


# --- the command --------------------------------------------------------------------


def test_an_answerable_question_comes_back_cited(
    make_adapter: AdapterFactory, store: VectorStore
) -> None:
    """The happy path: an answer, and a citation that resolves to a real timestamp."""
    reply = _draft(
        "The migration was postponed until the audit clears.",
        [{"passage_ref": "C1", "quote": "postpone the postgres migration"}],
    )
    with make_adapter(_handler(reply)) as adapter:
        outcome = ask("what happened to the migration", store=store, adapter=adapter, max_distance=2.0)

    assert not outcome.abstained
    assert outcome.attempts == 1
    assert outcome.prompt_name == "rag" and outcome.prompt_version
    # Both speakers: the chunk spans both segments, and the citation names who is in it.
    assert [citation.reference for citation in outcome.citations] == [
        "[mtg-001 · Yash, Saurabh · 0:00–2:00]"
    ]
    assert outcome.citations[0].source_id == "mtg-001"


def test_a_far_nearest_passage_abstains_without_calling_the_model(
    make_adapter: AdapterFactory, store: VectorStore
) -> None:
    """Below the threshold there is nothing to ground an answer in, so nothing is spent."""
    # No chat replies supplied: a model call would raise rather than pass quietly.
    with make_adapter(_handler()) as adapter:
        outcome = ask("what is the airspeed of a swallow", store=store, adapter=adapter, max_distance=0.0)

    assert outcome.abstained
    assert outcome.answer == ABSTAIN_TEXT
    assert outcome.abstention_reason is AbstentionReason.BELOW_THRESHOLD
    assert outcome.attempts == 0
    assert outcome.nearest_distance is not None


def test_an_empty_index_abstains_rather_than_erroring(
    make_adapter: AdapterFactory, tmp_path: Path
) -> None:
    """'The corpus does not answer this' is also the right answer to an empty corpus."""
    empty = VectorStore(tmp_path / "empty", collection="test", embed_model_repo_id=EMBED_MODEL)
    with make_adapter(_handler()) as adapter:
        outcome = ask("anything at all", store=empty, adapter=adapter)

    assert outcome.abstained
    assert outcome.abstention_reason is AbstentionReason.NO_MATCH
    assert outcome.nearest_distance is None


def test_the_model_may_read_the_passages_and_decline(
    make_adapter: AdapterFactory, store: VectorStore
) -> None:
    """The interesting abstention: near enough to retrieve, not enough to answer."""
    with make_adapter(_handler(_draft("", [], abstained=True))) as adapter:
        outcome = ask("who owns the audit", store=store, adapter=adapter, max_distance=2.0)

    assert outcome.answer == ABSTAIN_TEXT
    assert outcome.abstention_reason is AbstentionReason.MODEL_ABSTAINED
    assert outcome.attempts == 1


def test_a_fabricated_citation_is_retried_and_then_abstained(
    make_adapter: AdapterFactory, store: VectorStore
) -> None:
    """The acceptance criterion: there is no path from a fabricated citation to an answer.

    Both attempts cite a passage that was never retrieved. The result is an abstention
    with the reason that says so — never the model's fluent, uncited sentence.
    """
    fabricated = _draft(
        "The migration was cancelled outright.",
        [{"passage_ref": "C9", "quote": "cancelled the migration outright"}],
    )
    with make_adapter(_handler(fabricated, fabricated)) as adapter:
        outcome = ask("what happened to the migration", store=store, adapter=adapter, max_distance=2.0)

    assert outcome.abstained
    assert outcome.answer == ABSTAIN_TEXT
    assert outcome.abstention_reason is AbstentionReason.UNGROUNDED
    assert outcome.attempts == 2
    assert "cancelled" not in outcome.answer


def test_the_retry_can_rescue_a_bad_citation(
    make_adapter: AdapterFactory, store: VectorStore
) -> None:
    """The retry is a real repair path, not a formality before abstaining."""
    bad = _draft("Postponed.", [{"passage_ref": "C9", "quote": "postpone the postgres migration"}])
    good = _draft(
        "The migration was postponed until the audit clears.",
        [{"passage_ref": "C1", "quote": "postpone the postgres migration"}],
    )
    with make_adapter(_handler(bad, good)) as adapter:
        outcome = ask("what happened to the migration", store=store, adapter=adapter, max_distance=2.0)

    assert not outcome.abstained
    assert outcome.attempts == 2
    assert outcome.citations[0].passage_ref == "C1"


def test_the_prompt_version_reaches_the_answer_and_the_run_log(
    make_adapter: AdapterFactory, settings: Any, store: VectorStore
) -> None:
    """Two of the three agreeing is the dangerous state, so both are asserted together."""
    reply = _draft(
        "The migration was postponed.",
        [{"passage_ref": "C1", "quote": "postpone the postgres migration"}],
    )
    with make_adapter(_handler(reply)) as adapter:
        outcome = ask("what happened to the migration", store=store, adapter=adapter, max_distance=2.0)

    logged = RunLogger(settings.runs_log_path).read_all()
    asked = [record for record in logged if record.command == "m2x ask"]
    assert asked, "the ask produced no run-log lines"
    assert {record.prompt_version for record in asked} == {outcome.prompt_version}
    # Both legs, not just the answering call: the embedding is part of the question.
    assert {record.model_repo_id for record in asked} == {EMBED_MODEL, CHAT_MODEL}


def test_a_blank_question_is_a_usage_error(make_adapter: AdapterFactory, store: VectorStore) -> None:
    """Retrieval on an empty string returns the corpus's centre of mass, not an answer."""
    with make_adapter(_handler()) as adapter:
        with pytest.raises(ValueError, match="non-empty question"):
            ask("   ", store=store, adapter=adapter)


def test_retrieval_can_be_restricted_to_meetings(
    make_adapter: AdapterFactory, store: VectorStore
) -> None:
    """The filter reaches retrieval; a doc-only search over a meeting index finds nothing."""
    with make_adapter(_handler()) as adapter:
        outcome = ask(
            "what happened to the migration",
            store=store,
            adapter=adapter,
            source_type=SourceType.DOC,
        )

    assert outcome.abstention_reason is AbstentionReason.NO_MATCH


def test_fake_vectors_stay_deterministic() -> None:
    """Guards the assumption every distance-free assertion above rests on."""
    assert fake_vector("same text") == fake_vector("same text")
