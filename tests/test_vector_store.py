"""Index tests: idempotent rebuilds, complete metadata, and one model per index.

Chroma runs for real against ``tmp_path`` — it is an embedded database, so there is no
network and no service to stand up, and mocking it would only prove the mock works.
The *embeddings* are mocked, because those are the part that would go over the wire.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
from conftest import EMBED_MODEL, TRANSCRIBE_MODEL, embeddings_response

from m2x.adapter import ModelAdapter
from m2x.errors import ConfigError
from m2x.indexing import SourceType, chunk_transcript
from m2x.run_log import RunLogger
from m2x.settings import Settings
from m2x.types import Provider, Transcript, TranscriptSegment
from m2x.vector_store import (
    VectorStore,
    build_index,
    embed_texts,
    query_index,
)

AdapterFactory = Callable[..., ModelAdapter]

DOC = """The course brief.

## Scope

Meetings become execution records.

## Out of scope

The capstone.
"""


def transcript(count: int = 6, *, speakers: bool = False, long: bool = False) -> Transcript:
    """A transcript of ``count`` ten-second segments.

    ``long`` pads each segment past a third of the default chunk budget, so a handful of
    segments span several chunks — needed by the tests that care about chunk *counts*
    rather than content.
    """
    filler = " padding to push this segment past the chunk budget" * 10 if long else ""
    segments = [
        TranscriptSegment(
            t_start=float(index * 10),
            t_end=float((index + 1) * 10),
            text=f"segment {index} says something worth retrieving later on{filler}",
            speaker=f"SPEAKER_0{index % 2}" if speakers else None,
        )
        for index in range(count)
    ]
    return Transcript(
        model_repo_id=TRANSCRIBE_MODEL,
        provider=Provider.GROQ,
        latency_ms=1,
        text=" ".join(segment.text for segment in segments),
        audio_seconds=float(count * 10),
        segments=segments,
    )


def embedding_handler() -> Callable[[httpx.Request], httpx.Response]:
    """Answer any embeddings request with one deterministic vector per input."""

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        texts = json.loads(request.content)["input"]
        return httpx.Response(200, json=embeddings_response(texts))

    return handler


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    """A throwaway index."""
    return VectorStore(tmp_path / "index")


@pytest.fixture
def doc(tmp_path: Path) -> Path:
    """A markdown document on disk."""
    path = tmp_path / "course-brief.md"
    path.write_text(DOC, encoding="utf-8")
    return path


class TestIdempotence:
    def test_a_double_build_leaves_the_index_the_same_size(
        self, make_adapter: AdapterFactory, store: VectorStore, doc: Path
    ) -> None:
        """The ticket's acceptance criterion, and the reason ids are content addresses."""
        adapter = make_adapter(embedding_handler())

        first = build_index(store, adapter, transcripts=[("mtg-001", transcript())], documents=[doc])
        second = build_index(store, adapter, transcripts=[("mtg-001", transcript())], documents=[doc])

        assert first.index_size == second.index_size
        assert first.sources == second.sources
        assert store.count() == first.index_size

    def test_rebuilding_replaces_a_chunk_rather_than_adding_one(
        self, make_adapter: AdapterFactory, store: VectorStore
    ) -> None:
        adapter = make_adapter(embedding_handler())
        build_index(store, adapter, transcripts=[("mtg-001", transcript())])
        before = store.count()

        build_index(store, adapter, transcripts=[("mtg-001", transcript())])

        assert store.count() == before

    def test_a_shrunken_source_drops_its_orphaned_chunks(
        self, make_adapter: AdapterFactory, store: VectorStore
    ) -> None:
        """An edited doc that lost a section must stop serving the removed text."""
        adapter = make_adapter(embedding_handler())
        build_index(store, adapter, transcripts=[("mtg-001", transcript(count=8, long=True))])
        long_size = store.count()

        build_index(store, adapter, transcripts=[("mtg-001", transcript(count=2, long=True))])

        remaining = query_index(store, adapter, "segment", k=50)
        assert long_size > store.count()
        assert store.source_ids() == {"mtg-001"}
        assert max(int(hit.metadata["segment_end"]) for hit in remaining) == 2

    def test_rebuilding_one_source_leaves_the_others_alone(
        self, make_adapter: AdapterFactory, store: VectorStore
    ) -> None:
        adapter = make_adapter(embedding_handler())
        build_index(
            store,
            adapter,
            transcripts=[("mtg-001", transcript()), ("mtg-002", transcript())],
        )
        full = store.count()

        build_index(store, adapter, transcripts=[("mtg-001", transcript())])

        assert store.count() == full
        assert store.source_ids() == {"mtg-001", "mtg-002"}

    def test_the_index_survives_being_reopened(
        self, make_adapter: AdapterFactory, tmp_path: Path
    ) -> None:
        adapter = make_adapter(embedding_handler())
        path = tmp_path / "index"
        build_index(VectorStore(path), adapter, transcripts=[("mtg-001", transcript())])

        assert VectorStore(path).count() > 0


class TestMetadataCompleteness:
    def test_every_meeting_chunk_arrives_with_its_timestamps(
        self, make_adapter: AdapterFactory, store: VectorStore
    ) -> None:
        """Metadata not attached at index time cannot be recovered at query time."""
        build_index(
            store,
            make_adapter(embedding_handler()),
            transcripts=[("mtg-001", transcript(speakers=True))],
        )

        hits = query_index(store, make_adapter(embedding_handler()), "something", k=5)

        assert hits
        for hit in hits:
            assert hit.metadata["source_id"] == "mtg-001"
            assert hit.metadata["source_type"] == "meeting"
            assert isinstance(hit.metadata["t_start"], float)
            assert isinstance(hit.metadata["t_end"], float)
            assert hit.metadata["speakers"]

    def test_document_chunks_carry_headings_and_no_timestamps(
        self, make_adapter: AdapterFactory, store: VectorStore, doc: Path
    ) -> None:
        build_index(store, make_adapter(embedding_handler()), documents=[doc])

        hits = query_index(
            store,
            make_adapter(embedding_handler()),
            "scope",
            k=5,
            source_type=SourceType.DOC,
        )

        assert hits
        for hit in hits:
            assert hit.metadata["source_type"] == "doc"
            assert "t_start" not in hit.metadata
            assert hit.metadata["source_path"].endswith("course-brief.md")

    def test_a_hit_renders_a_timestamped_citation(
        self, make_adapter: AdapterFactory, store: VectorStore
    ) -> None:
        build_index(store, make_adapter(embedding_handler()), transcripts=[("mtg-001", transcript())])

        hit = query_index(store, make_adapter(embedding_handler()), "segment", k=1)[0]

        assert hit.citation.startswith("mtg-001 ")
        assert "–" in hit.citation

    def test_source_type_filters_the_search(
        self, make_adapter: AdapterFactory, store: VectorStore, doc: Path
    ) -> None:
        build_index(
            store,
            make_adapter(embedding_handler()),
            transcripts=[("mtg-001", transcript())],
            documents=[doc],
        )
        adapter = make_adapter(embedding_handler())

        meetings = query_index(store, adapter, "anything", k=10, source_type=SourceType.MEETING)
        docs = query_index(store, adapter, "anything", k=10, source_type=SourceType.DOC)

        assert {hit.metadata["source_type"] for hit in meetings} == {"meeting"}
        assert {hit.metadata["source_type"] for hit in docs} == {"doc"}


class TestModelPinning:
    def test_reopening_with_a_different_model_is_refused(self, tmp_path: Path) -> None:
        """Same-dimension mismatch returns plausible nonsense and no error at all."""
        path = tmp_path / "index"
        VectorStore(path, embed_model_repo_id=EMBED_MODEL)

        with pytest.raises(ConfigError, match="was built with"):
            VectorStore(path, embed_model_repo_id="BAAI/bge-small-en-v1.5")

    def test_the_query_uses_the_model_the_index_was_built_with(
        self, make_adapter: AdapterFactory, store: VectorStore, settings: Settings
    ) -> None:
        build_index(store, make_adapter(embedding_handler()), transcripts=[("mtg-001", transcript())])

        query_index(store, make_adapter(embedding_handler()), "who is shipping", k=3)

        embedded = [
            record for record in RunLogger(settings.runs_log_path).read_all()
            if record.model_repo_id == EMBED_MODEL
        ]
        assert embedded
        assert {record.model_repo_id for record in embedded} == {store.embed_model_repo_id}


class TestBuildAccounting:
    def test_the_outcome_reports_chunks_per_source(
        self, make_adapter: AdapterFactory, store: VectorStore, doc: Path
    ) -> None:
        outcome = build_index(
            store,
            make_adapter(embedding_handler()),
            transcripts=[("mtg-001", transcript())],
            documents=[doc],
        )

        assert set(outcome.sources) == {"mtg-001", "course-brief"}
        assert outcome.total_chunks == sum(outcome.sources.values())
        assert outcome.index_size == store.count()
        assert outcome.embed_model_repo_id == EMBED_MODEL

    def test_an_empty_source_is_reported_not_silently_skipped(
        self, make_adapter: AdapterFactory, store: VectorStore, tmp_path: Path
    ) -> None:
        empty = tmp_path / "blank.md"
        empty.write_text("\n\n", encoding="utf-8")

        outcome = build_index(
            store,
            make_adapter(embedding_handler()),
            transcripts=[("mtg-999", transcript(count=0))],
            documents=[empty],
        )

        assert outcome.sources == {}
        assert set(outcome.skipped) == {"mtg-999", "blank"}

    def test_indexing_is_attributed_in_the_run_log(
        self, make_adapter: AdapterFactory, store: VectorStore, settings: Settings
    ) -> None:
        """An index build is real spend; an unattributed one makes the phase look free."""
        build_index(store, make_adapter(embedding_handler()), transcripts=[("mtg-001", transcript())])

        records = RunLogger(settings.runs_log_path).read_all()

        assert records
        assert {record.phase for record in records} == {"phase-2"}
        assert {record.command for record in records} == {"m2x index build"}
        assert {record.meeting_id for record in records} == {"mtg-001"}


class TestBatching:
    def test_texts_are_embedded_in_batches_and_stay_in_order(
        self, make_adapter: AdapterFactory
    ) -> None:
        texts = [f"text number {index}" for index in range(7)]
        seen: list[list[str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            batch = json.loads(request.content)["input"]
            seen.append(batch)
            return httpx.Response(200, json=embeddings_response(batch))

        vectors = embed_texts(make_adapter(handler), texts, batch_size=3)

        assert [len(batch) for batch in seen] == [3, 3, 1]
        assert [text for batch in seen for text in batch] == texts
        assert len(vectors) == len(texts)

    def test_a_nonsense_batch_size_is_rejected(self, make_adapter: AdapterFactory) -> None:
        with pytest.raises(ValueError, match="batch_size"):
            embed_texts(make_adapter(embedding_handler()), ["x"], batch_size=0)


class TestQueryGuards:
    def test_querying_an_empty_index_returns_nothing(
        self, make_adapter: AdapterFactory, store: VectorStore
    ) -> None:
        assert query_index(store, make_adapter(embedding_handler()), "anything") == []

    def test_a_blank_question_is_rejected_before_embedding(
        self, make_adapter: AdapterFactory, store: VectorStore
    ) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            query_index(store, make_adapter(embedding_handler()), "   ")

    def test_k_must_be_positive(self, make_adapter: AdapterFactory, store: VectorStore) -> None:
        with pytest.raises(ValueError, match="k must be positive"):
            store.query([0.1] * 8, k=0)

    def test_k_is_capped_by_what_is_stored(
        self, make_adapter: AdapterFactory, store: VectorStore
    ) -> None:
        build_index(store, make_adapter(embedding_handler()), transcripts=[("mtg-001", transcript(count=2))])

        hits = query_index(store, make_adapter(embedding_handler()), "segment", k=50)

        assert 0 < len(hits) <= store.count()

    def test_mismatched_chunk_and_vector_counts_are_refused(self, store: VectorStore) -> None:
        """Positional alignment is the only thing tying a vector to its text."""
        chunks = chunk_transcript(transcript(count=3), "mtg-001", char_budget=60)

        with pytest.raises(ValueError, match="chunks but"):
            store.write_source("mtg-001", chunks, [[0.1] * 8])
