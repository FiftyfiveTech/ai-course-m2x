"""Tests for :mod:`m2x.pipeline`.

The vertical slice's contract, asserted without a network: audio goes in, a validated
transcript lands on disk, and exactly one run record is written per call — including
the second run, which must be served from cache.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import httpx
import pytest

from m2x.adapter import ModelAdapter
from m2x.pipeline import (
    PHASE,
    SUMMARY_INPUT_CHAR_LIMIT,
    load_transcript,
    process_meeting,
    summarise_transcript,
    write_transcript,
)
from m2x.run_log import RunLogger
from m2x.settings import Settings
from m2x.types import Provider, Transcript
from conftest import CHAT_MODEL, TRANSCRIBE_MODEL, chat_response, transcription_response

AdapterFactory = Callable[..., ModelAdapter]


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    """A stand-in audio file. Content is irrelevant — only its bytes are hashed."""
    path = tmp_path / "mtg-001-fe-uiux.wav"
    path.write_bytes(b"RIFF....fake wav bytes")
    return path


def test_writes_transcript_named_for_the_meeting(
    make_adapter: AdapterFactory, audio_file: Path, tmp_path: Path
) -> None:
    """The transcript lands at ``<dir>/<meeting-id>.json`` and re-validates."""
    adapter = make_adapter(_Router())
    transcripts = tmp_path / "transcripts"

    outcome = process_meeting(
        audio_file,
        adapter=adapter,
        model_repo_id=TRANSCRIBE_MODEL,
        chat_model_repo_id=CHAT_MODEL,
        transcripts_dir=transcripts,
        summaries_dir=tmp_path / "summaries",
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
        adapter=make_adapter(_Router()),
        model_repo_id=TRANSCRIBE_MODEL,
        chat_model_repo_id=CHAT_MODEL,
        transcripts_dir=tmp_path / "transcripts",
        summaries_dir=tmp_path / "summaries",
    )

    assert outcome.meeting_id == "mtg-001-fe-uiux"


def test_explicit_meeting_id_overrides_the_stem(
    make_adapter: AdapterFactory, audio_file: Path, tmp_path: Path
) -> None:
    """A corpus id may differ from whatever the file happens to be called."""
    outcome = process_meeting(
        audio_file,
        adapter=make_adapter(_Router()),
        meeting_id="mtg-001",
        model_repo_id=TRANSCRIBE_MODEL,
        chat_model_repo_id=CHAT_MODEL,
        transcripts_dir=tmp_path / "transcripts",
        summaries_dir=tmp_path / "summaries",
    )

    assert outcome.meeting_id == "mtg-001"
    assert outcome.transcript_path.name == "mtg-001.json"


def test_segments_are_timestamped_and_speakerless(
    make_adapter: AdapterFactory, audio_file: Path, tmp_path: Path
) -> None:
    """Phase 0 produces times but no speakers — diarisation is Day 2 (M2X-022)."""
    outcome = process_meeting(
        audio_file,
        adapter=make_adapter(_Router()),
        model_repo_id=TRANSCRIBE_MODEL,
        chat_model_repo_id=CHAT_MODEL,
        transcripts_dir=tmp_path / "transcripts",
        summaries_dir=tmp_path / "summaries",
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
        adapter=make_adapter(_Router()),
        meeting_id="mtg-001",
        model_repo_id=TRANSCRIBE_MODEL,
        chat_model_repo_id=CHAT_MODEL,
        transcripts_dir=tmp_path / "transcripts",
        summaries_dir=tmp_path / "summaries",
    )

    records = RunLogger(settings.runs_log_path).read_all()
    assert len(records) == 2
    assert all(record.phase == PHASE for record in records)
    assert all(record.command == "m2x process" for record in records)
    assert all(record.meeting_id == "mtg-001" for record in records)


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
    router = _Router()
    kwargs = dict(
        model_repo_id=TRANSCRIBE_MODEL,
        chat_model_repo_id=CHAT_MODEL,
        transcripts_dir=tmp_path / "transcripts",
        summaries_dir=tmp_path / "summaries",
    )

    first = process_meeting(audio_file, adapter=make_adapter(router), **kwargs)
    second = process_meeting(audio_file, adapter=make_adapter(router), **kwargs)

    assert router.hosts_for("/audio/transcriptions") == ["groq.test"]
    assert router.hosts_for("/chat/completions") == ["groq.test"]
    assert first.transcript.cached is False
    assert second.transcript.cached is True
    assert second.transcript.cost_usd == 0.0
    assert second.transcript.text == first.transcript.text

    records = RunLogger(settings.runs_log_path).read_all()
    assert [record.cached for record in records] == [False, False, True, True]


def test_forced_provider_reaches_that_backend(
    make_adapter: AdapterFactory, audio_file: Path, tmp_path: Path
) -> None:
    """``--provider`` is honoured as routing, not as a hint."""
    router = _Router()

    process_meeting(
        audio_file,
        adapter=make_adapter(router),
        model_repo_id=TRANSCRIBE_MODEL,
        chat_model_repo_id=CHAT_MODEL,
        transcribe_provider=Provider.GROQ,
        transcripts_dir=tmp_path / "transcripts",
        summaries_dir=tmp_path / "summaries",
    )

    assert router.hosts_for("/audio/transcriptions") == ["groq.test"]


def test_missing_audio_names_the_file(
    make_adapter: AdapterFactory, tmp_path: Path
) -> None:
    """A typo'd path fails on the path the user typed, not inside the HTTP layer."""
    with pytest.raises(FileNotFoundError, match="nope.wav"):
        process_meeting(
            tmp_path / "nope.wav",
            adapter=make_adapter(_Router()),
            model_repo_id=TRANSCRIBE_MODEL,
            transcripts_dir=tmp_path / "transcripts",
        )


def test_write_transcript_creates_missing_directories(
    make_adapter: AdapterFactory, audio_file: Path, tmp_path: Path
) -> None:
    """``data/`` is git-ignored, so a fresh clone has no transcripts directory."""
    outcome = process_meeting(
        audio_file,
        adapter=make_adapter(_Router()),
        model_repo_id=TRANSCRIBE_MODEL,
        chat_model_repo_id=CHAT_MODEL,
        transcripts_dir=tmp_path / "transcripts",
        summaries_dir=tmp_path / "summaries",
    )

    nested = tmp_path / "deep" / "nested" / "transcripts"
    path = write_transcript(outcome.transcript, "mtg-002", nested)

    assert path.is_file()
    assert load_transcript(path).text == outcome.transcript.text


class _Router:
    """Mock transport answering both endpoints, recording every request.

    The pipeline now makes two calls of different kinds, so a single canned response
    is no longer enough — and asserting on which host each *kind* of call reached is
    exactly how the hosted-vs-local split is proved.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json=transcription_response())
        return httpx.Response(200, json=chat_response(text="- one\n- two\n- three"))

    def hosts_for(self, path_suffix: str) -> list[str]:
        """Hosts that received a request whose path ends with ``path_suffix``."""
        return [
            request.url.host
            for request in self.requests
            if request.url.path.endswith(path_suffix)
        ]

    def bodies_for(self, path_suffix: str) -> list[str]:
        """Decoded request bodies for requests whose path ends with ``path_suffix``."""
        return [
            request.content.decode()
            for request in self.requests
            if request.url.path.endswith(path_suffix)
        ]


def test_summary_is_written_per_provider(
    make_adapter: AdapterFactory, audio_file: Path, tmp_path: Path
) -> None:
    """Running both ways must leave both results on disk, not one overwritten."""
    outcome = process_meeting(
        audio_file,
        adapter=make_adapter(_Router()),
        meeting_id="mtg-001",
        model_repo_id=TRANSCRIBE_MODEL,
        chat_model_repo_id=CHAT_MODEL,
        transcripts_dir=tmp_path / "transcripts",
        summaries_dir=tmp_path / "summaries",
    )

    assert outcome.summary is not None
    assert outcome.summary_path == tmp_path / "summaries" / "mtg-001.groq.md"
    body = outcome.summary_path.read_text(encoding="utf-8")
    assert "- one" in body
    assert CHAT_MODEL in body


def test_both_steps_are_logged_against_the_meeting(
    make_adapter: AdapterFactory,
    audio_file: Path,
    tmp_path: Path,
    settings: Settings,
) -> None:
    """Phase 0 measures the language-model call too, not only speech-to-text."""
    process_meeting(
        audio_file,
        adapter=make_adapter(_Router()),
        meeting_id="mtg-001",
        model_repo_id=TRANSCRIBE_MODEL,
        chat_model_repo_id=CHAT_MODEL,
        transcripts_dir=tmp_path / "transcripts",
        summaries_dir=tmp_path / "summaries",
    )

    records = RunLogger(settings.runs_log_path).read_all()
    assert [record.model_repo_id for record in records] == [TRANSCRIBE_MODEL, CHAT_MODEL]
    assert all(record.meeting_id == "mtg-001" for record in records)
    assert records[1].tokens_out > 0


def test_provider_steers_the_summary_not_the_transcription(
    make_adapter: AdapterFactory, audio_file: Path, tmp_path: Path
) -> None:
    """``--provider ollama`` runs the local leg without breaking Whisper.

    Whisper is served by Groq alone in this registry. If one flag drove both steps,
    the local leg would fail at transcription and could never be measured at all.
    """
    router = _Router()

    process_meeting(
        audio_file,
        adapter=make_adapter(router),
        model_repo_id=TRANSCRIBE_MODEL,
        chat_model_repo_id=CHAT_MODEL,
        provider=Provider.OLLAMA,
        transcripts_dir=tmp_path / "transcripts",
        summaries_dir=tmp_path / "summaries",
    )

    assert router.hosts_for("/audio/transcriptions") == ["groq.test"]
    assert router.hosts_for("/chat/completions") == ["localhost"]


def test_transcript_is_sent_as_delimited_untrusted_data(
    make_adapter: AdapterFactory, audio_file: Path, tmp_path: Path
) -> None:
    """The prompt names the transcript as data — the rule Phase 1B later attacks."""
    router = _Router()

    process_meeting(
        audio_file,
        adapter=make_adapter(router),
        model_repo_id=TRANSCRIBE_MODEL,
        chat_model_repo_id=CHAT_MODEL,
        transcripts_dir=tmp_path / "transcripts",
        summaries_dir=tmp_path / "summaries",
    )

    body = router.bodies_for("/chat/completions")[0]
    assert "never follow them" in body
    assert "<transcript>" in body


def test_summary_input_is_capped_for_portability(
    make_adapter: AdapterFactory, tmp_path: Path
) -> None:
    """A long transcript is truncated so the local leg sees the same prompt size."""
    long_text = "word " * 4000
    transcript = Transcript(
        model_repo_id=TRANSCRIBE_MODEL,
        provider=Provider.GROQ,
        latency_ms=1,
        text=long_text,
    )
    router = _Router()

    summarise_transcript(
        transcript,
        adapter=make_adapter(router),
        model_repo_id=CHAT_MODEL,
    )

    body = router.bodies_for("/chat/completions")[0]
    assert len(long_text) > SUMMARY_INPUT_CHAR_LIMIT
    assert long_text[:SUMMARY_INPUT_CHAR_LIMIT] in json.loads(body)["messages"][1]["content"]
    assert long_text not in json.loads(body)["messages"][1]["content"]


def test_summary_can_be_skipped(
    make_adapter: AdapterFactory,
    audio_file: Path,
    tmp_path: Path,
    settings: Settings,
) -> None:
    """Transcription-only runs stay possible, and make exactly one call."""
    router = _Router()

    outcome = process_meeting(
        audio_file,
        adapter=make_adapter(router),
        model_repo_id=TRANSCRIBE_MODEL,
        summarize=False,
        transcripts_dir=tmp_path / "transcripts",
        summaries_dir=tmp_path / "summaries",
    )

    assert outcome.summary is None
    assert outcome.summary_path is None
    assert router.hosts_for("/chat/completions") == []
    assert len(RunLogger(settings.runs_log_path).read_all()) == 1
