"""Tests for the pluggable description similarity, including the embedding backend.

Offline: the embedding calls go through a mock transport, so nothing here needs a running
model. The vectors are scripted rather than realistic — what is under test is the seam,
the batching and the cosine, not whether a real embedding model agrees with a human.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from conftest import EMBED_MODEL, embeddings_response
from m2x.adapter import ModelAdapter
from m2x.eval_extraction import (
    Counts,
    EmbeddingSimilarity,
    aggregate,
    match_items,
    score_case,
    token_set_f1,
)
from m2x.schema import Decision, Evidence, MeetingRecord


def _ev() -> Evidence:
    """Evidence stub."""
    return Evidence(segment_id="seg-0001", t_start=0.0, t_end=1.0)


def _decisions(*descriptions: str) -> list[Decision]:
    """Build decisions from descriptions."""
    return [Decision(description=text, evidence=_ev()) for text in descriptions]


def _vector_handler(
    vectors: dict[str, list[float]],
) -> tuple[Callable[[httpx.Request], httpx.Response], list[list[str]]]:
    """Serve scripted vectors, recording each batch of texts requested."""
    batches: list[list[str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content.decode("utf-8"))["input"]
        batches.append(list(texts))
        payload = embeddings_response(texts)
        for item, text in zip(payload["data"], texts, strict=True):
            item["embedding"] = vectors[text]
        return httpx.Response(200, json=payload)

    return handle, batches


# --------------------------------------------------------------------------------------
# The seam
# --------------------------------------------------------------------------------------


def test_matching_uses_the_similarity_it_is_given() -> None:
    """A pair the lexical metric rejects can be matched by another measure.

    This is the whole point of the seam: "adopt postgres" and "adopt mysql" score 0.5 on
    token overlap and must not match, but the matcher itself must not hard-code that
    judgement.
    """
    labelled = _decisions("adopt postgres")
    extracted = _decisions("adopt mysql")

    assert match_items(labelled, extracted)[0] == []
    assert match_items(labelled, extracted, similarity=lambda a, b: 1.0)[0] == [(0, 0)]


def test_the_default_similarity_is_unchanged() -> None:
    """Existing numbers stay reproducible unless a caller opts into something else."""
    labelled = _decisions("adopt the new schema")
    extracted = _decisions("adopt the new schema")

    assert match_items(labelled, extracted)[0] == [(0, 0)]
    assert token_set_f1("adopt the new schema", "adopt the new schema") == 1.0


def test_score_case_threads_the_similarity_through() -> None:
    """Otherwise the option would be silently ignored at the level people call."""
    labelled = MeetingRecord(decisions=_decisions("ship the release"))
    extracted = MeetingRecord(decisions=_decisions("cut the tag and publish"))

    lexical = aggregate("dev", [score_case("c01", labelled, extracted)])
    generous = aggregate(
        "dev", [score_case("c01", labelled, extracted, similarity=lambda a, b: 0.9)]
    )

    assert lexical.per_kind["decisions"] == Counts(false_positive=1, false_negative=1)
    assert generous.per_kind["decisions"] == Counts(true_positive=1)


# --------------------------------------------------------------------------------------
# The embedding backend
# --------------------------------------------------------------------------------------


def test_identical_vectors_score_one(make_adapter: Callable[..., ModelAdapter]) -> None:
    """Cosine of a vector with itself is 1."""
    handler, _ = _vector_handler({"a": [1.0, 0.0, 0.0], "b": [1.0, 0.0, 0.0]})
    similarity = EmbeddingSimilarity(make_adapter(handler), model_repo_id=EMBED_MODEL)

    assert similarity("a", "b") == pytest.approx(1.0)


def test_orthogonal_vectors_score_zero(make_adapter: Callable[..., ModelAdapter]) -> None:
    """Unrelated text must not match."""
    handler, _ = _vector_handler({"a": [1.0, 0.0, 0.0], "b": [0.0, 1.0, 0.0]})
    similarity = EmbeddingSimilarity(make_adapter(handler), model_repo_id=EMBED_MODEL)

    assert similarity("a", "b") == pytest.approx(0.0)


def test_opposed_vectors_clamp_to_zero(make_adapter: Callable[..., ModelAdapter]) -> None:
    """A negative cosine must not sort above an unrelated pair.

    Without the clamp the greedy matcher would rank an item's opposite as a better
    candidate than something merely unrelated, which is worse than not matching at all.
    """
    handler, _ = _vector_handler({"a": [1.0, 0.0], "b": [-1.0, 0.0]})
    similarity = EmbeddingSimilarity(make_adapter(handler), model_repo_id=EMBED_MODEL)

    assert similarity("a", "b") == 0.0


def test_descriptions_are_embedded_once_and_reused(
    make_adapter: Callable[..., ModelAdapter],
) -> None:
    """The matcher compares every label against every extraction.

    Embedding pair by pair would issue O(n*m) requests for O(n+m) distinct texts, so a
    text already seen must never be sent again.
    """
    handler, batches = _vector_handler(
        {"a": [1.0, 0.0], "b": [0.0, 1.0], "c": [1.0, 1.0]}
    )
    similarity = EmbeddingSimilarity(make_adapter(handler), model_repo_id=EMBED_MODEL)

    similarity.warm(["a", "b", "c"])
    similarity("a", "b")
    similarity("a", "c")
    similarity("b", "c")

    assert len(batches) == 1
    assert sorted(batches[0]) == ["a", "b", "c"]


def test_warm_skips_texts_it_already_has(
    make_adapter: Callable[..., ModelAdapter],
) -> None:
    """A second warm over overlapping text only asks for what is new."""
    handler, batches = _vector_handler({"a": [1.0, 0.0], "b": [0.0, 1.0]})
    similarity = EmbeddingSimilarity(make_adapter(handler), model_repo_id=EMBED_MODEL)

    similarity.warm(["a"])
    similarity.warm(["a", "b"])

    assert batches == [["a"], ["b"]]


def test_the_model_is_reported_for_the_results_record(
    make_adapter: Callable[..., ModelAdapter],
) -> None:
    """The original objection to embeddings was an unrecorded model, so it is recorded."""
    handler, _ = _vector_handler({"a": [1.0], "b": [1.0]})
    similarity = EmbeddingSimilarity(make_adapter(handler), model_repo_id=EMBED_MODEL)

    assert similarity.model_repo_id == EMBED_MODEL


def test_embedding_similarity_drives_a_real_match(
    make_adapter: Callable[..., ModelAdapter],
) -> None:
    """End to end: a paraphrase the lexical metric rejects, matched by embeddings."""
    label = "Find somebody to shoot the testimonial videos and edit them properly"
    extract = "Linda will find someone to take the video and edit it properly"
    handler, _ = _vector_handler({label: [1.0, 0.1], extract: [0.99, 0.14]})
    similarity = EmbeddingSimilarity(make_adapter(handler), model_repo_id=EMBED_MODEL)

    assert token_set_f1(label, extract) < 0.6
    pairs, _, _ = match_items(
        _decisions(label), _decisions(extract), similarity=similarity
    )

    assert pairs == [(0, 0)]
