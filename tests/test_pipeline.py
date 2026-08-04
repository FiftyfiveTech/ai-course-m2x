"""Tests for :mod:`m2x.pipeline`.

The vertical slice's contract, asserted without a network: audio goes in, a validated
transcript lands on disk, and exactly one run record is written per call — including
the second run, which must be served from cache.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import httpx
import pytest

from m2x.adapter import ModelAdapter
from m2x.pipeline import PHASE, load_transcript, process_meeting, write_transcript
from m2x.run_log import RunLogger
from m2x.settings import Settings
from m2x.types import Provider
from conftest import TRANSCRIBE_MODEL, transcription_response

AdapterFactory = Callable[..., ModelAdapter]


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    """A stand-in audio file. Content is irrelevant — only its bytes are hashed."""
    path = tmp_path / "mtg-001-fe-uiux.wav"
    path.write_bytes(b"RIFF....fake wav bytes")
    return path


class _Recorder:
    """Mock transport that records requests and always answers with one payload."""

    def __init__(self, response: httpx.Response | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._response = response or httpx.Response(200, json=transcription_response())

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._response

    @property
    def call_count(self) -> int:
        """Number of HTTP attempts made."""
        return len(self.requests)


def test_writes_transcript_named_for_the_meeting(
    make_adapter: AdapterFactory, audio_file: Path, tmp_path: Path
) -> None:
    """The transcript lands at ``<dir>/<meeting-id>.json`` and re-validates."""
    adapter = make_adapter(_Recorder())
    transcripts = tmp_path / "transcripts"

    outcome = process_meeting(
        audio_file,
        adapter=adapter,
        model_repo_id=TRANSCRIBE_MODEL,
        transcripts_dir=transcripts,
    )

    assert outcome.transcript_path == transcripts / "mtg-001-fe-uiux.json"
    assert outcome.transcript_path.is_file()
    reloaded = load_transcript(outcome.transcript_path)
    assert reloaded.text == "the meeting begins"
    assert reloaded.model_repo_id == TRANSCRIBE_MODEL


def test_meeting_id_defaults_to_the_filename_stem(
    make_adapter: AdapterFactory, audio_file: Path, tmp_path: Path
) -> None:
    """No explicit id needed: the corpus filenames already carry one."""
    outcome = process_meeting(
        audio_file,
        adapter=make_adapter(_Recorder()),
        model_repo_id=TRANSCRIBE_MODEL,
        transcripts_dir=tmp_path / "transcripts",
    )

    assert outcome.meeting_id == "mtg-001-fe-uiux"


def test_explicit_meeting_id_overrides_the_stem(
    make_adapter: AdapterFactory, audio_file: Path, tmp_path: Path
) -> None:
    """A corpus id may differ from whatever the file happens to be called."""
    outcome = process_meeting(
        audio_file,
        adapter=make_adapter(_Recorder()),
        meeting_id="mtg-001",
        model_repo_id=TRANSCRIBE_MODEL,
        transcripts_dir=tmp_path / "transcripts",
    )

    assert outcome.meeting_id == "mtg-001"
    assert outcome.transcript_path.name == "mtg-001.json"


def test_segments_are_timestamped_and_speakerless(
    make_adapter: AdapterFactory, audio_file: Path, tmp_path: Path
) -> None:
    """Phase 0 produces times but no speakers — diarisation is Day 2 (M2X-022)."""
    outcome = process_meeting(
        audio_file,
        adapter=make_adapter(_Recorder()),
        model_repo_id=TRANSCRIBE_MODEL,
        transcripts_dir=tmp_path / "transcripts",
    )

    segments = outcome.transcript.segments
    assert [(s.t_start, s.t_end) for s in segments] == [(0.0, 2.5), (2.5, 4.0)]
    assert all(segment.speaker is None for segment in segments)


def test_run_is_attributed_to_the_meeting_and_phase(
    make_adapter: AdapterFactory,
    audio_file: Path,
    tmp_path: Path,
    settings: Settings,
) -> None:
    """Every call is logged against phase-0 and the meeting it processed."""
    process_meeting(
        audio_file,
        adapter=make_adapter(_Recorder()),
        meeting_id="mtg-001",
        model_repo_id=TRANSCRIBE_MODEL,
        transcripts_dir=tmp_path / "transcripts",
    )

    records = RunLogger(settings.runs_log_path).read_all()
    assert len(records) == 1
    assert records[0].phase == PHASE
    assert records[0].command == "m2x process"
    assert records[0].meeting_id == "mtg-001"


def test_second_run_is_a_cache_hit_with_no_network(
    make_adapter: AdapterFactory,
    audio_file: Path,
    tmp_path: Path,
    settings: Settings,
) -> None:
    """Re-running the same command costs nothing and touches no provider.

    This is the acceptance criterion that protects the free-tier quota for the whole
    week, so it is asserted at the HTTP boundary rather than on a flag.
    """
    recorder = _Recorder()
    transcripts = tmp_path / "transcripts"
    kwargs = dict(model_repo_id=TRANSCRIBE_MODEL, transcripts_dir=transcripts)

    first = process_meeting(audio_file, adapter=make_adapter(recorder), **kwargs)
    second = process_meeting(audio_file, adapter=make_adapter(recorder), **kwargs)

    assert recorder.call_count == 1
    assert first.transcript.cached is False
    assert second.transcript.cached is True
    assert second.transcript.cost_usd == 0.0
    assert second.transcript.text == first.transcript.text

    records = RunLogger(settings.runs_log_path).read_all()
    assert [record.cached for record in records] == [False, True]


def test_forced_provider_reaches_that_backend(
    make_adapter: AdapterFactory, audio_file: Path, tmp_path: Path
) -> None:
    """``--provider`` is honoured as routing, not as a hint."""
    recorder = _Recorder()

    process_meeting(
        audio_file,
        adapter=make_adapter(recorder),
        model_repo_id=TRANSCRIBE_MODEL,
        provider=Provider.GROQ,
        transcripts_dir=tmp_path / "transcripts",
    )

    assert recorder.requests[0].url.host == "groq.test"


def test_missing_audio_names_the_file(
    make_adapter: AdapterFactory, tmp_path: Path
) -> None:
    """A typo'd path fails on the path the user typed, not inside the HTTP layer."""
    with pytest.raises(FileNotFoundError, match="nope.wav"):
        process_meeting(
            tmp_path / "nope.wav",
            adapter=make_adapter(_Recorder()),
            model_repo_id=TRANSCRIBE_MODEL,
            transcripts_dir=tmp_path / "transcripts",
        )


def test_write_transcript_creates_missing_directories(
    make_adapter: AdapterFactory, audio_file: Path, tmp_path: Path
) -> None:
    """``data/`` is git-ignored, so a fresh clone has no transcripts directory."""
    outcome = process_meeting(
        audio_file,
        adapter=make_adapter(_Recorder()),
        model_repo_id=TRANSCRIBE_MODEL,
        transcripts_dir=tmp_path / "transcripts",
    )

    nested = tmp_path / "deep" / "nested" / "transcripts"
    path = write_transcript(outcome.transcript, "mtg-002", nested)

    assert path.is_file()
    assert load_transcript(path).text == outcome.transcript.text
