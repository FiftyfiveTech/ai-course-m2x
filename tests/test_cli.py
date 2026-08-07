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
from m2x.types import Provider, Transcript, TranscriptSegment
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


def test_runs_summary_tabulates_the_log(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Seeded fixture data in, correct table out — the acceptance criterion for M2X-014."""
    log_path = tmp_path / "runs.jsonl"
    log_path.write_text(
        "\n".join(
            [
                _run_line(provider="groq", latency_ms=721, tokens_in=1433, tokens_out=99),
                _run_line(provider="ollama", latency_ms=189_200, tokens_in=1413, tokens_out=86),
            ]
        )
        + "\n"
    )

    assert main(["runs", "summary", "--log", str(log_path)]) == EXIT_OK

    out = capsys.readouterr().out
    assert "groq" in out and "ollama" in out
    assert "721" in out and "189200" in out
    assert "TOTAL" in out
    assert "2846" in out  # tokens_in summed across both routes


def test_runs_summary_on_a_missing_log_is_not_an_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A fresh clone has no log yet; the report says so and exits clean."""
    code = main(["runs", "summary", "--log", str(tmp_path / "absent.jsonl")])

    assert code == EXIT_OK
    assert "no runs logged yet" in capsys.readouterr().out


def test_runs_summary_on_a_corrupt_log_fails_loudly(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A cost report that silently skips unparseable calls is worse than no report."""
    log_path = tmp_path / "runs.jsonl"
    log_path.write_text("{not json}\n")

    code = main(["runs", "summary", "--log", str(log_path)])

    assert code == EXIT_FAILURE
    assert "not a valid run record" in capsys.readouterr().err


def _run_line(
    *,
    provider: str,
    latency_ms: int,
    tokens_in: int,
    tokens_out: int,
) -> str:
    """One JSONL run record, written the way the logger writes it."""
    import json

    return json.dumps(
        {
            "ts": "2026-08-04T09:00:00+00:00",
            "phase": "phase-0",
            "command": "m2x process",
            "model_repo_id": CHAT_MODEL,
            "provider": provider,
            "latency_ms": latency_ms,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": 0.0,
            "cached": False,
            "meeting_id": "mtg-002",
        }
    )


def _strategy_transcript(path: Path) -> Path:
    """Persist a twelve-segment transcript for the strategy commands."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        Transcript(
            model_repo_id=TRANSCRIBE_MODEL,
            provider=Provider.GROQ,
            latency_ms=1,
            text="we agreed to ship",
            audio_seconds=720.0,
            segments=[
                TranscriptSegment(
                    t_start=index * 60.0,
                    t_end=(index + 1) * 60.0,
                    text=f"we agreed point {index + 1}",
                )
                for index in range(12)
            ],
        ).model_dump_json(),
        encoding="utf-8",
    )
    return path


def test_chapter_fixed_needs_no_provider(
    make_adapter: AdapterFactory,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The free strategy must not touch the network — that is the whole point of it."""

    def refuse(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("fixed chaptering must not call a provider")

    transcript_path = _strategy_transcript(tmp_path / "transcripts" / "mtg-001.json")

    code = main(
        [
            "chapter",
            "mtg-001",
            "--strategy",
            "fixed",
            "--transcript",
            str(transcript_path),
            "--chapters-dir",
            str(tmp_path / "chapters"),
        ],
        adapter_factory=lambda: make_adapter(refuse),
    )

    assert code == EXIT_OK
    assert (tmp_path / "chapters" / "mtg-001.fixed.json").is_file()
    assert "3 chapters by fixed" in capsys.readouterr().out


def test_map_reduce_without_chapters_is_a_usage_error(
    make_adapter: AdapterFactory,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Better to name the missing step than to silently chapter it a second way."""
    transcript_path = _strategy_transcript(tmp_path / "transcripts" / "mtg-001.json")

    code = main(
        [
            "summarise",
            "mtg-001",
            "--strategy",
            "map-reduce",
            "--transcript",
            str(transcript_path),
            "--chapters",
            str(tmp_path / "nope.json"),
        ],
        adapter_factory=lambda: make_adapter(_ok_handler),
    )

    assert code == EXIT_USAGE
    assert "m2x chapter mtg-001" in capsys.readouterr().err


def test_summarise_single_pass_writes_a_summary(
    make_adapter: AdapterFactory,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transcript_path = _strategy_transcript(tmp_path / "transcripts" / "mtg-001.json")
    summaries = tmp_path / "strategies"

    code = main(
        [
            "summarise",
            "mtg-001",
            "--strategy",
            "single-pass",
            "--transcript",
            str(transcript_path),
            "--model",
            CHAT_MODEL,
            "--summaries-dir",
            str(summaries),
        ],
        adapter_factory=lambda: make_adapter(
            lambda _request: httpx.Response(200, json=chat_response("- one\n- two"))
        ),
    )

    assert code == EXIT_OK
    assert "single-pass in 1 call" in capsys.readouterr().out
    assert (summaries / "mtg-001.single-pass.md").is_file()
