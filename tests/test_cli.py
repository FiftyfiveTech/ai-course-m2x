"""Tests for :mod:`m2x.cli`.

The command is exercised the way the gate runs it — through :func:`m2x.cli.main` with
an argument vector — but against a mock transport, so the whole slice is provable
offline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import httpx
import pytest

from m2x.adapter import ModelAdapter
from m2x.cli import EXIT_FAILURE, EXIT_OK, EXIT_USAGE, build_parser, main
from m2x.extraction import load_record
from m2x.pipeline import load_transcript
from m2x.prompts import latest_version
from m2x.run_log import RunLogger
from m2x.types import Provider, Transcript, TranscriptSegment
from conftest import (
    CHAT_MODEL,
    EMBED_MODEL,
    TRANSCRIBE_MODEL,
    chat_response,
    embeddings_response,
    transcription_response,
)

AdapterFactory = Callable[..., ModelAdapter]
REPO_ROOT = Path(__file__).resolve().parents[1]


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


def _write_transcript(path: Path) -> Path:
    """Persist a two-segment transcript for the extract command to read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        Transcript(
            model_repo_id=TRANSCRIBE_MODEL,
            provider=Provider.GROQ,
            latency_ms=1,
            text="we ship on friday",
            audio_seconds=20.0,
            segments=[
                TranscriptSegment(t_start=0.0, t_end=10.0, text="we ship on friday"),
                TranscriptSegment(t_start=10.0, t_end=20.0, text="yash writes the snippets"),
            ],
        ).model_dump_json(),
        encoding="utf-8",
    )
    return path


def _record_reply(segment_id: str = "seg-0001") -> str:
    """A model reply carrying one decision citing ``segment_id``."""
    return json.dumps(
        {
            "decisions": [
                {
                    "description": "ship on friday",
                    "evidence": {"segment_id": segment_id, "t_start": 1.0, "t_end": 5.0},
                }
            ],
            "actions": [],
            "risks": [],
            "open_questions": [],
        }
    )


def test_extract_writes_a_record_and_reports_it(
    make_adapter: AdapterFactory,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The happy path: exit 0, record on disk, counts on stdout."""
    transcript_path = _write_transcript(tmp_path / "transcripts" / "mtg-001.json")
    records = tmp_path / "records"

    code = main(
        [
            "extract",
            "mtg-001",
            "--transcript",
            str(transcript_path),
            "--model",
            CHAT_MODEL,
            "--records-dir",
            str(records),
        ],
        adapter_factory=lambda: make_adapter(
            lambda _request: httpx.Response(200, json=chat_response(_record_reply()))
        ),
    )

    assert code == EXIT_OK
    assert load_record(records / "mtg-001.json").record.item_count == 1
    assert "decisions 1" in capsys.readouterr().out


def test_extract_agrees_with_the_run_log_on_the_prompt_version(
    make_adapter: AdapterFactory,
    tmp_path: Path,
    settings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The acceptance check, run the way the evaluator runs it.

    Record metadata, run log and the printed report all name one version — and it is
    the version the tracked library actually ships, not a value the command invented.
    The changelog is the third leg, enforced in ``tests/test_prompts.py``.
    """
    transcript_path = _write_transcript(tmp_path / "transcripts" / "mtg-001.json")
    records = tmp_path / "records"
    shipped = latest_version("extraction", prompts_dir=REPO_ROOT / "prompts")

    code = main(
        [
            "extract",
            "mtg-001",
            "--transcript",
            str(transcript_path),
            "--model",
            CHAT_MODEL,
            "--records-dir",
            str(records),
        ],
        adapter_factory=lambda: make_adapter(
            lambda _request: httpx.Response(200, json=chat_response(_record_reply()))
        ),
    )
    logged = RunLogger(settings.runs_log_path).read_all()

    assert code == EXIT_OK
    assert load_record(records / "mtg-001.json").prompt_version == shipped
    assert [record.prompt_version for record in logged] == [shipped]
    assert f"prompt    extraction {shipped}" in capsys.readouterr().out


def test_extract_can_pin_an_older_prompt_version(
    make_adapter: AdapterFactory,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Switching versions is a file and a flag — no code change, which is the criterion."""
    transcript_path = _write_transcript(tmp_path / "transcripts" / "mtg-001.json")
    library = tmp_path / "prompts" / "extraction"
    library.mkdir(parents=True)
    for version, system in (("v1", "older wording"), ("v2", "newer wording")):
        (library / f"{version}.md").write_text(
            f"## system\n\n{system}\n\n## user\n\n<transcript>\n{{{{transcript}}}}\n</transcript>\n",
            encoding="utf-8",
        )
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=chat_response(_record_reply()))

    code = main(
        [
            "extract",
            "mtg-001",
            "--transcript",
            str(transcript_path),
            "--model",
            CHAT_MODEL,
            "--records-dir",
            str(tmp_path / "records"),
            "--prompts-dir",
            str(tmp_path / "prompts"),
            "--prompt-version",
            "v1",
        ],
        adapter_factory=lambda: make_adapter(handler),
    )

    assert code == EXIT_OK
    assert sent[0]["messages"][0]["content"].startswith("older wording")
    assert "prompt    extraction v1" in capsys.readouterr().out


def test_extract_with_an_unknown_prompt_version_is_a_run_failure(
    make_adapter: AdapterFactory,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A typo costs one error, not three attempts against the wrong prompt."""
    transcript_path = _write_transcript(tmp_path / "transcripts" / "mtg-001.json")

    code = main(
        [
            "extract",
            "mtg-001",
            "--transcript",
            str(transcript_path),
            "--prompt-version",
            "v99",
        ],
        adapter_factory=lambda: make_adapter(_ok_handler),
    )

    assert code == EXIT_FAILURE
    assert "Cannot read prompt extraction/v99" in capsys.readouterr().err


def test_extract_on_a_missing_transcript_exits_with_a_usage_code(
    make_adapter: AdapterFactory,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        ["extract", "mtg-404", "--transcript", str(tmp_path / "nope.json")],
        adapter_factory=lambda: make_adapter(_ok_handler),
    )

    assert code == EXIT_USAGE
    assert "nope.json" in capsys.readouterr().err


def test_extract_that_never_validates_is_a_run_failure(
    make_adapter: AdapterFactory,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No valid record is a failure to look at, not an empty file to score."""
    transcript_path = _write_transcript(tmp_path / "transcripts" / "mtg-001.json")
    records = tmp_path / "records"

    code = main(
        [
            "extract",
            "mtg-001",
            "--transcript",
            str(transcript_path),
            "--model",
            CHAT_MODEL,
            "--records-dir",
            str(records),
        ],
        adapter_factory=lambda: make_adapter(
            lambda _request: httpx.Response(
                200, json=chat_response(_record_reply(segment_id="seg-9999"))
            )
        ),
    )

    assert code == EXIT_FAILURE
    assert "never validated" in capsys.readouterr().err
    assert not (records / "mtg-001.json").exists()


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


def _index_transcript(path: Path, *, segments: int = 6) -> Path:
    """Persist a transcript for the index command to read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        Transcript(
            model_repo_id=TRANSCRIBE_MODEL,
            provider=Provider.GROQ,
            latency_ms=1,
            text="indexable meeting content",
            audio_seconds=float(segments * 10),
            segments=[
                TranscriptSegment(
                    t_start=float(index * 10),
                    t_end=float((index + 1) * 10),
                    text=f"segment {index} discusses the migration plan in some detail",
                )
                for index in range(segments)
            ],
        ).model_dump_json(),
        encoding="utf-8",
    )
    return path


def _embedding_handler(request: httpx.Request) -> httpx.Response:
    """Answer any embeddings request with one deterministic vector per input."""
    return httpx.Response(200, json=embeddings_response(json.loads(request.content)["input"]))


def test_index_build_reports_what_it_wrote(
    make_adapter: AdapterFactory,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The command Yash runs before querying anything."""
    _index_transcript(tmp_path / "transcripts" / "mtg-001.json")
    doc = tmp_path / "brief.md"
    doc.write_text("# Brief\n\nThe project indexes meetings.\n", encoding="utf-8")

    code = main(
        [
            "index",
            "build",
            "--transcripts-dir",
            str(tmp_path / "transcripts"),
            "--doc",
            str(doc),
            "--index-dir",
            str(tmp_path / "index"),
        ],
        adapter_factory=lambda: make_adapter(_embedding_handler),
    )

    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "mtg-001" in out and "brief" in out
    assert EMBED_MODEL in out


def test_index_build_twice_leaves_the_same_count(
    make_adapter: AdapterFactory,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The acceptance criterion, run the way it is verified: build, build, compare."""
    _index_transcript(tmp_path / "transcripts" / "mtg-001.json")
    argv = [
        "index",
        "build",
        "--transcripts-dir",
        str(tmp_path / "transcripts"),
        "--no-docs",
        "--index-dir",
        str(tmp_path / "index"),
    ]

    assert main(argv, adapter_factory=lambda: make_adapter(_embedding_handler)) == EXIT_OK
    first = capsys.readouterr().out
    assert main(argv, adapter_factory=lambda: make_adapter(_embedding_handler)) == EXIT_OK
    second = capsys.readouterr().out

    assert first == second


def test_index_build_with_nothing_to_index_is_a_usage_error(
    make_adapter: AdapterFactory,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A build that indexed nothing and exited 0 is how a gate runs against an empty store."""
    code = main(
        [
            "index",
            "build",
            "--transcripts-dir",
            str(tmp_path / "absent"),
            "--no-docs",
            "--index-dir",
            str(tmp_path / "index"),
        ],
        adapter_factory=lambda: make_adapter(_embedding_handler),
    )

    assert code == EXIT_USAGE
    assert "nothing to index" in capsys.readouterr().err


def test_index_query_prints_scores_and_citations(
    make_adapter: AdapterFactory,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _index_transcript(tmp_path / "transcripts" / "mtg-001.json")
    common = ["--index-dir", str(tmp_path / "index")]
    main(
        ["index", "build", "--transcripts-dir", str(tmp_path / "transcripts"), "--no-docs", *common],
        adapter_factory=lambda: make_adapter(_embedding_handler),
    )
    capsys.readouterr()

    code = main(
        ["index", "query", "what about the migration", "-k", "2", *common],
        adapter_factory=lambda: make_adapter(_embedding_handler),
    )

    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "distance" in out
    assert "mtg-001 0:00" in out


def test_index_query_on_an_empty_index_says_so(
    make_adapter: AdapterFactory,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        ["index", "query", "anything", "--index-dir", str(tmp_path / "index")],
        adapter_factory=lambda: make_adapter(_embedding_handler),
    )

    assert code == EXIT_OK
    assert "no chunks matched" in capsys.readouterr().out


def test_index_opened_with_the_wrong_model_is_a_run_failure(
    make_adapter: AdapterFactory,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A same-dimension mismatch would otherwise return plausible nonsense, silently."""
    _index_transcript(tmp_path / "transcripts" / "mtg-001.json")
    common = ["--index-dir", str(tmp_path / "index")]
    main(
        ["index", "build", "--transcripts-dir", str(tmp_path / "transcripts"), "--no-docs", *common],
        adapter_factory=lambda: make_adapter(_embedding_handler),
    )
    capsys.readouterr()

    code = main(
        ["index", "query", "anything", "--model", "BAAI/bge-small-en-v1.5", *common],
        adapter_factory=lambda: make_adapter(_embedding_handler),
    )

    assert code == EXIT_FAILURE
    assert "was built with" in capsys.readouterr().err
