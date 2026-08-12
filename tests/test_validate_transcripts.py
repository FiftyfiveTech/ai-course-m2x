"""Tests for the Phase 1 gate validator (M2X-025).

The validator's job is to be the thing a gate record can be written from, which means the
property under test is not "does it parse JSON" but **what it refuses to pass**. A gate
checker that returns 0 on a broken corpus is worse than no checker, because the record it
produces looks identical to a real one.

The interesting case is the 95% attribution floor. ``TranscriptSegment.speaker`` is
nullable by design, so the naive check — every segment has a speaker — fails a corpus that
is working correctly. The floor is the deliberate compromise, and both of its edges are
tested.
"""

from __future__ import annotations

import json

import pytest
from validate_transcripts import MIN_ATTRIBUTION, check, main


def _segment(t_start: float, t_end: float, text: str, speaker: str | None) -> dict:
    return {"t_start": t_start, "t_end": t_end, "text": text, "speaker": speaker}


def _transcript(segments: list[dict], **overrides) -> dict:
    return {
        "text": " ".join(seg["text"] for seg in segments),
        "segments": segments,
        "audio_seconds": 120.0,
        "model_repo_id": "openai/whisper-large-v3",
        "provider": "groq",
        "latency_ms": 1,
        **overrides,
    }


def _write(tmp_path, meeting: str, payload: dict):
    path = tmp_path / f"{meeting}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def good(tmp_path):
    return _write(
        tmp_path,
        "mtg-001",
        _transcript(
            [
                _segment(0.0, 5.0, "we should ship it", "SPEAKER_00"),
                _segment(5.0, 9.0, "next week", "SPEAKER_01"),
            ]
        ),
    )


class TestPasses:
    def test_a_fully_attributed_transcript_passes(self, good):
        ok, report = check(good)
        assert ok
        assert "2 speakers" in report
        assert "100.0% attributed" in report

    def test_an_empty_text_segment_is_reported_but_does_not_fail(self, tmp_path):
        """Whisper emits these on long pauses. They inflate duration-based coverage, so
        the count has to be visible — but they are the transcriber's behaviour, not a
        broken transcript, and failing on them would fail the real corpus."""
        path = _write(
            tmp_path,
            "mtg-001",
            _transcript(
                [
                    _segment(0.0, 5.0, "we should ship it", "SPEAKER_00"),
                    _segment(5.0, 32.0, "", "SPEAKER_00"),
                ]
            ),
        )
        ok, report = check(path)
        assert ok
        assert "1 empty-text (27s)" in report


class TestAttributionFloor:
    def test_a_few_unattributed_segments_pass(self):
        """The design returns None rather than guessing when no turn overlaps. A corpus
        at 99% attributed is working as intended, not failing."""
        assert MIN_ATTRIBUTION < 0.99

    def test_passes_at_the_floor(self, tmp_path):
        segments = [_segment(i, i + 1.0, "word", "SPEAKER_00") for i in range(19)]
        segments.append(_segment(19.0, 20.0, "word", None))
        ok, report = check(_write(tmp_path, "mtg-001", _transcript(segments)))
        assert ok, report
        assert "95.0% attributed" in report

    def test_fails_below_the_floor(self, tmp_path):
        segments = [_segment(i, i + 1.0, "word", "SPEAKER_00") for i in range(18)]
        segments += [_segment(18.0, 19.0, "word", None), _segment(19.0, 20.0, "w", None)]
        ok, report = check(_write(tmp_path, "mtg-001", _transcript(segments)))
        assert not ok
        assert "below the 95% attribution floor" in report


class TestRefuses:
    def test_a_transcript_with_no_speakers_at_all_fails(self, tmp_path):
        """Plain STT output parses perfectly and is not what the gate asks for."""
        path = _write(
            tmp_path,
            "mtg-001",
            _transcript([_segment(0.0, 5.0, "we should ship it", None)]),
        )
        ok, report = check(path)
        assert not ok
        assert "0.0% attributed" in report

    def test_zero_segments_fails_rather_than_scoring_a_perfect_100(self, tmp_path):
        ok, report = check(_write(tmp_path, "mtg-001", _transcript([])))
        assert not ok
        assert "zero segments" in report

    def test_a_backwards_time_range_fails(self, tmp_path):
        path = _write(
            tmp_path,
            "mtg-001",
            _transcript([_segment(9.0, 5.0, "out of order", "SPEAKER_00")]),
        )
        ok, report = check(path)
        assert not ok
        assert "t_end < t_start" in report

    def test_a_missing_file_fails_without_raising(self, tmp_path):
        ok, report = check(tmp_path / "absent.json")
        assert not ok
        assert "unreadable" in report

    def test_a_schema_violation_fails(self, tmp_path):
        path = tmp_path / "mtg-001.json"
        path.write_text(json.dumps({"segments": [{"text": "no timestamps"}]}))
        ok, report = check(path)
        assert not ok
        assert "INVALID" in report or "unreadable" in report


class TestExitCode:
    def test_exits_zero_when_every_meeting_passes(self, good, capsys):
        assert main(["mtg-001", "--diarization-dir", str(good.parent)]) == 0
        assert "1/1 speaker-attributed" in capsys.readouterr().out

    def test_exits_one_so_a_gate_cannot_be_recorded_green(self, tmp_path, capsys):
        _write(tmp_path, "mtg-001", _transcript([_segment(0.0, 5.0, "hi", None)]))
        assert main(["mtg-001", "--diarization-dir", str(tmp_path)]) == 1
        assert "0/1 speaker-attributed" in capsys.readouterr().out
