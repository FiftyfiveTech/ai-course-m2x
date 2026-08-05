"""Tests for splitting oversized audio and stitching the pieces back together.

The merge is where the risk lives. A wrong offset does not crash — it produces a
transcript that reads perfectly and points every citation after the first chunk at the
wrong moment in the meeting, which is exactly the kind of defect that survives to
production. So the timestamp arithmetic is tested directly rather than through the
pipeline.

No ffmpeg and no network here: ``split_audio`` is exercised only for its failure path,
and everything else works on constructed :class:`~m2x.types.Transcript` values.
"""

from __future__ import annotations

import pytest
from m2x.chunking import (
    MAX_UPLOAD_BYTES,
    iter_chunks,
    merge_transcripts,
    needs_splitting,
)
from m2x.types import Provider, Transcript, TranscriptSegment


def _transcript(
    *,
    segments: list[tuple[float, float, str]],
    latency_ms: int = 100,
    cached: bool = False,
    cost_usd: float = 0.0,
    audio_seconds: float = 600.0,
    model: str = "openai/whisper-large-v3",
    provider: Provider = Provider.GROQ,
    language: str | None = "en",
) -> Transcript:
    return Transcript(
        model_repo_id=model,
        provider=provider,
        latency_ms=latency_ms,
        cached=cached,
        cost_usd=cost_usd,
        text=" ".join(text for _, _, text in segments),
        segments=[
            TranscriptSegment(t_start=start, t_end=end, text=text)
            for start, end, text in segments
        ],
        audio_seconds=audio_seconds,
        language=language,
    )


class TestNeedsSplitting:
    def test_small_file_is_left_alone(self, tmp_path):
        path = tmp_path / "a.wav"
        path.write_bytes(b"x" * 1024)
        assert needs_splitting(path) is False

    def test_file_over_the_limit_is_split(self, tmp_path):
        path = tmp_path / "a.wav"
        path.write_bytes(b"x" * (MAX_UPLOAD_BYTES + 1))
        assert needs_splitting(path) is True


class TestIterChunks:
    def test_small_file_yields_itself_at_offset_zero(self, tmp_path):
        path = tmp_path / "a.wav"
        path.write_bytes(b"x" * 1024)
        assert list(iter_chunks(path, tmp_path / "chunks")) == [(0.0, path)]

    def test_small_file_does_not_create_a_chunks_directory(self, tmp_path):
        path = tmp_path / "a.wav"
        path.write_bytes(b"x" * 1024)
        chunks = tmp_path / "chunks"
        list(iter_chunks(path, chunks))
        assert not chunks.exists()


class TestMergeTranscripts:
    def test_shifts_timestamps_onto_the_meetings_own_axis(self):
        merged = merge_transcripts(
            [
                (0.0, _transcript(segments=[(0.0, 5.0, "first")])),
                (600.0, _transcript(segments=[(0.0, 4.0, "second")])),
            ]
        )
        assert [(s.t_start, s.t_end) for s in merged.segments] == [(0.0, 5.0), (600.0, 604.0)]

    def test_latency_sums_rather_than_taking_the_maximum(self):
        # Transcribing the meeting really did cost both calls; reporting the max would
        # flatter the route in the comparison matrix.
        merged = merge_transcripts(
            [
                (0.0, _transcript(segments=[(0.0, 1.0, "a")], latency_ms=1000)),
                (600.0, _transcript(segments=[(0.0, 1.0, "b")], latency_ms=1500)),
            ]
        )
        assert merged.latency_ms == 2500

    def test_cost_and_duration_are_additive(self):
        merged = merge_transcripts(
            [
                (0.0, _transcript(segments=[(0.0, 1.0, "a")], cost_usd=0.5, audio_seconds=600.0)),
                (600.0, _transcript(segments=[(0.0, 1.0, "b")], cost_usd=0.25, audio_seconds=120.0)),
            ]
        )
        assert merged.cost_usd == 0.75
        assert merged.audio_seconds == 720.0

    def test_cached_only_when_every_chunk_was_a_hit(self):
        parts = [
            (0.0, _transcript(segments=[(0.0, 1.0, "a")], cached=True)),
            (600.0, _transcript(segments=[(0.0, 1.0, "b")], cached=False)),
        ]
        assert merge_transcripts(parts).cached is False
        parts[1] = (600.0, _transcript(segments=[(0.0, 1.0, "b")], cached=True))
        assert merge_transcripts(parts).cached is True

    def test_text_joins_in_playback_order(self):
        merged = merge_transcripts(
            [
                (0.0, _transcript(segments=[(0.0, 1.0, "hello")])),
                (600.0, _transcript(segments=[(0.0, 1.0, "world")])),
            ]
        )
        assert merged.text == "hello world"

    def test_language_comes_from_the_first_chunk(self):
        merged = merge_transcripts(
            [
                (0.0, _transcript(segments=[(0.0, 1.0, "a")], language="hi")),
                (600.0, _transcript(segments=[(0.0, 1.0, "b")], language="en")),
            ]
        )
        assert merged.language == "hi"

    def test_refuses_to_mix_models(self):
        with pytest.raises(ValueError, match="different models"):
            merge_transcripts(
                [
                    (0.0, _transcript(segments=[(0.0, 1.0, "a")])),
                    (600.0, _transcript(segments=[(0.0, 1.0, "b")], model="openai/whisper-large-v3-turbo")),
                ]
            )

    def test_refuses_to_mix_providers(self):
        with pytest.raises(ValueError, match="different providers"):
            merge_transcripts(
                [
                    (0.0, _transcript(segments=[(0.0, 1.0, "a")])),
                    (600.0, _transcript(segments=[(0.0, 1.0, "b")], provider=Provider.NIM)),
                ]
            )

    def test_empty_input_raises(self):
        with pytest.raises(ValueError, match="no transcript parts"):
            merge_transcripts([])
