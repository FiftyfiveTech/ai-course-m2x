"""Tests for :mod:`m2x.cli`.

The command is exercised the way the gate runs it — through :func:`m2x.cli.main` with
an argument vector — but against a mock transport, so the whole slice is provable
offline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import httpx
import pytest

from m2x.adapter import ModelAdapter
from m2x.cli import EXIT_FAILURE, EXIT_OK, EXIT_USAGE, build_parser, main
from m2x.pipeline import load_transcript
from m2x.types import Provider
from conftest import CHAT_MODEL, TRANSCRIBE_MODEL, chat_response, transcription_response

AdapterFactory = Callable[..., ModelAdapter]


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    """A stand-in audio file."""
    path = tmp_path / "mtg-002-course-scope.wav"
    path.write_bytes(b"RIFF....fake wav bytes")
    return path


def _ok_handler(request: httpx.Request) -> httpx.Response:
    """Answer both pipeline endpoints with valid payloads."""
    if request.url.path.endswith("/audio/transcriptions"):
        return httpx.Response(200, json=transcription_response())
    return httpx.Response(200, json=chat_response(text="- one\n- two\n- three"))


def test_process_writes_a_transcript_and_reports_it(
    make_adapter: AdapterFactory,
    audio_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The happy path: exit 0, transcript on disk, summary on stdout."""
    transcripts = tmp_path / "transcripts"

    code = main(
        [
            "process",
            str(audio_file),
            "--model",
            TRANSCRIBE_MODEL,
            "--transcripts-dir",
            str(transcripts),
        ],
        adapter_factory=lambda: make_adapter(_ok_handler),
    )

    assert code == EXIT_OK
    written = transcripts / "mtg-002-course-scope.json"
    assert load_transcript(written).text == "the meeting begins"

    out = capsys.readouterr().out
    assert "mtg-002-course-scope: 2 segments" in out
    assert str(written) in out


def test_repeat_run_reports_the_cache_hit(
    make_adapter: AdapterFactory,
    audio_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The gate reads "was it cached?" straight off the command's output."""
    argv = [
        "process",
        str(audio_file),
        "--model",
        TRANSCRIBE_MODEL,
        "--transcripts-dir",
        str(tmp_path / "transcripts"),
    ]
    factory = lambda: make_adapter(_ok_handler)  # noqa: E731 - one-liner test seam

    assert main(argv, adapter_factory=factory) == EXIT_OK
    assert "(provider," in capsys.readouterr().out

    assert main(argv, adapter_factory=factory) == EXIT_OK
    assert "(cache," in capsys.readouterr().out


def test_missing_audio_exits_with_a_usage_code(
    make_adapter: AdapterFactory,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A path that does not exist is the user's mistake, not a run failure."""
    code = main(
        ["process", str(tmp_path / "nope.wav")],
        adapter_factory=lambda: make_adapter(_ok_handler),
    )

    assert code == EXIT_USAGE
    assert "nope.wav" in capsys.readouterr().err


def test_provider_failure_exits_without_a_traceback(
    make_adapter: AdapterFactory,
    audio_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A refused call prints the project's own error text, not a stack trace."""

    def refuse(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    code = main(
        [
            "process",
            str(audio_file),
            "--model",
            TRANSCRIBE_MODEL,
            "--transcripts-dir",
            str(tmp_path / "transcripts"),
        ],
        adapter_factory=lambda: make_adapter(refuse),
    )

    assert code == EXIT_FAILURE
    captured = capsys.readouterr()
    assert captured.err.startswith("error: ")
    assert captured.out == ""


def test_unknown_model_is_a_run_failure(
    make_adapter: AdapterFactory,
    audio_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Models are named by HF repo id; an unregistered one fails loudly."""
    code = main(
        [
            "process",
            str(audio_file),
            "--model",
            "acme/not-a-real-model",
            "--transcripts-dir",
            str(tmp_path / "transcripts"),
        ],
        adapter_factory=lambda: make_adapter(_ok_handler),
    )

    assert code == EXIT_FAILURE
    assert "acme/not-a-real-model" in capsys.readouterr().err


def test_parser_accepts_every_provider() -> None:
    """``--provider`` covers all three backends and parses to the enum."""
    parser = build_parser()

    for provider in Provider:
        args = parser.parse_args(["process", "clip.wav", "--provider", provider.value])
        assert args.provider is provider


def test_parser_rejects_an_unknown_provider() -> None:
    """A typo'd backend is rejected at parse time rather than at request time."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["process", "clip.wav", "--provider", "openai"])
