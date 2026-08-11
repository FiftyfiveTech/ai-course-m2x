"""Chunking tests: determinism, exact timestamps, and complete metadata.

No vector store here — chunking is pure, and the properties that matter (same input,
same ids; a chunk's time range is a real segment's) are provable without a database.
"""

from __future__ import annotations

import pytest
from conftest import TRANSCRIBE_MODEL

from m2x.indexing import (
    CHUNK_CHAR_BUDGET,
    Chunk,
    SourceType,
    chunk_document,
    chunk_id,
    chunk_transcript,
    load_document,
)
from m2x.types import Provider, Transcript, TranscriptSegment


def transcript(count: int = 6, *, words: int = 4, speakers: bool = False) -> Transcript:
    """A transcript of ``count`` ten-second segments."""
    segments = [
        TranscriptSegment(
            t_start=float(index * 10),
            t_end=float((index + 1) * 10),
            text=" ".join(f"word{index}-{position}" for position in range(words)),
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


class TestTranscriptChunking:
    def test_chunking_is_deterministic(self) -> None:
        """Same corpus, same ids — the property the whole idempotent rebuild rests on."""
        first = chunk_transcript(transcript(), "mtg-001", char_budget=80)
        second = chunk_transcript(transcript(), "mtg-001", char_budget=80)

        assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
        assert first == second

    def test_ids_are_namespaced_by_meeting(self) -> None:
        """Identical text in two meetings must not collapse onto one id."""
        one = chunk_transcript(transcript(), "mtg-001", char_budget=80)
        two = chunk_transcript(transcript(), "mtg-002", char_budget=80)

        assert {chunk.chunk_id for chunk in one}.isdisjoint(chunk.chunk_id for chunk in two)

    def test_timestamps_come_from_real_segments(self) -> None:
        """A chunk's range is a segment boundary, never an interpolation."""
        chunks = chunk_transcript(transcript(count=6), "mtg-001", char_budget=80)
        boundaries = {0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0}

        assert chunks[0].t_start == 0.0
        assert chunks[-1].t_end == 60.0
        for chunk in chunks:
            assert chunk.t_start in boundaries and chunk.t_end in boundaries
            assert chunk.t_end > chunk.t_start

    def test_neighbouring_chunks_overlap_by_a_segment(self) -> None:
        """An answer straddling a boundary is otherwise in neither chunk in full."""
        chunks = chunk_transcript(
            transcript(count=6), "mtg-001", char_budget=80, overlap_segments=1
        )

        assert len(chunks) > 1
        for earlier, later in zip(chunks, chunks[1:]):
            assert later.segment_start == earlier.segment_end

    def test_overlap_can_be_switched_off(self) -> None:
        chunks = chunk_transcript(
            transcript(count=6), "mtg-001", char_budget=80, overlap_segments=0
        )

        for earlier, later in zip(chunks, chunks[1:]):
            assert later.segment_start == earlier.segment_end + 1

    def test_segments_are_never_split(self) -> None:
        """One oversized chunk beats a severed timestamp: only one of them is visibly wrong."""
        long_transcript = transcript(count=2, words=200)

        chunks = chunk_transcript(long_transcript, "mtg-001", char_budget=50)

        assert len(chunks) == 2
        assert chunks[0].segment_start == chunks[0].segment_end
        assert len(chunks[0].text) > 50

    def test_every_segment_reaches_at_least_one_chunk(self) -> None:
        chunks = chunk_transcript(transcript(count=7), "mtg-001", char_budget=80)
        covered = {
            index
            for chunk in chunks
            for index in range(chunk.segment_start or 0, (chunk.segment_end or 0) + 1)
        }

        assert covered == set(range(1, 8))

    def test_speakers_are_recorded_in_first_appearance_order(self) -> None:
        chunks = chunk_transcript(
            transcript(count=4, speakers=True), "mtg-001", char_budget=CHUNK_CHAR_BUDGET
        )

        assert chunks[0].speakers == ("SPEAKER_00", "SPEAKER_01")
        assert "SPEAKER_00:" in chunks[0].text

    def test_an_empty_transcript_produces_no_chunks(self) -> None:
        assert chunk_transcript(transcript(count=0), "mtg-001") == []

    @pytest.mark.parametrize("budget,overlap", [(0, 1), (-1, 1), (100, -1)])
    def test_nonsense_settings_are_rejected(self, budget: int, overlap: int) -> None:
        with pytest.raises(ValueError):
            chunk_transcript(transcript(), "mtg-001", char_budget=budget, overlap_segments=overlap)


class TestDocumentChunking:
    DOC = """Intro paragraph before any heading.

## Scope

In scope: the pipeline.

## Out of scope

Not the capstone.
"""

    def test_sections_split_on_headings(self) -> None:
        chunks = chunk_document(self.DOC, "brief")

        assert [chunk.heading for chunk in chunks] == [None, "Scope", "Out of scope"]
        assert all(chunk.source_type is SourceType.DOC for chunk in chunks)

    def test_the_heading_travels_with_its_text(self) -> None:
        """A paragraph reads as generic without the heading naming its subject."""
        scope = chunk_document(self.DOC, "brief")[1]

        assert scope.text.startswith("## Scope")
        assert "In scope: the pipeline." in scope.text

    def test_content_before_the_first_heading_is_kept(self) -> None:
        assert chunk_document(self.DOC, "brief")[0].text == "Intro paragraph before any heading."

    def test_a_long_section_splits_on_blank_lines(self) -> None:
        body = "## Scope\n\n" + "\n\n".join(f"paragraph {index} " + "x" * 60 for index in range(6))

        chunks = chunk_document(body, "brief", char_budget=200)

        assert len(chunks) > 1
        assert all(chunk.heading == "Scope" for chunk in chunks)
        assert all(chunk.text.startswith("## Scope") for chunk in chunks)

    def test_document_chunks_carry_no_timestamps(self) -> None:
        """0.0 is a real timestamp; using it for "not applicable" would fake a citation."""
        for chunk in chunk_document(self.DOC, "brief"):
            assert chunk.t_start is None
            assert chunk.t_end is None
            assert "t_start" not in chunk.to_metadata()

    def test_loading_a_file_uses_its_stem_and_records_the_path(self, tmp_path) -> None:
        path = tmp_path / "course-brief.md"
        path.write_text(self.DOC, encoding="utf-8")

        chunks = load_document(path)

        assert all(chunk.source_id == "course-brief" for chunk in chunks)
        assert chunks[0].source_path == str(path)

    def test_an_empty_document_produces_no_chunks(self) -> None:
        assert chunk_document("\n\n   \n", "brief") == []


class TestMetadata:
    def test_a_meeting_chunk_carries_everything_a_citation_needs(self) -> None:
        chunk = chunk_transcript(transcript(count=2, speakers=True), "mtg-001")[0]

        metadata = chunk.to_metadata()

        assert metadata["source_id"] == "mtg-001"
        assert metadata["source_type"] == "meeting"
        assert metadata["t_start"] == 0.0
        assert metadata["t_end"] == 20.0
        assert metadata["segment_start"] == 1
        assert metadata["speakers"] == "SPEAKER_00, SPEAKER_01"

    def test_metadata_values_are_scalars_only(self) -> None:
        """Chroma rejects lists and None, and rejects them at write time, mid-build."""
        chunks = chunk_transcript(transcript(speakers=True), "mtg-001") + chunk_document(
            TestDocumentChunking.DOC, "brief"
        )

        for chunk in chunks:
            for key, value in chunk.to_metadata().items():
                assert isinstance(value, (str, int, float, bool)), key

    def test_citation_renders_a_time_range_for_meetings(self) -> None:
        chunk = chunk_transcript(transcript(count=2), "mtg-001")[0]

        assert chunk.citation == "mtg-001 0:00–0:20"

    def test_citation_renders_a_heading_for_documents(self) -> None:
        assert chunk_document(TestDocumentChunking.DOC, "brief")[1].citation == "brief § Scope"

    def test_ids_depend_on_the_range_not_the_text(self) -> None:
        assert chunk_id(SourceType.MEETING, "mtg-001", 1, 3) == chunk_id(
            SourceType.MEETING, "mtg-001", 1, 3
        )
        assert chunk_id(SourceType.MEETING, "mtg-001", 1, 3) != chunk_id(
            SourceType.MEETING, "mtg-001", 1, 4
        )

    def test_chunks_are_frozen(self) -> None:
        """A chunk whose text drifts from its id would be indexed under the wrong key."""
        chunk = chunk_document(TestDocumentChunking.DOC, "brief")[0]

        with pytest.raises(Exception):
            chunk.text = "edited"  # type: ignore[misc]

        assert isinstance(chunk, Chunk)
