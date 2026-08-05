"""Command-line entry point.

``argparse`` rather than a CLI framework: one command with five flags does not justify
a dependency, and the Phase 0 gate is run from a fresh clone, where every extra package
is another way for the clone to fail.

The module keeps a hard split — parse and print here, decide nothing. Anything a test
would want to assert on lives in :mod:`m2x.pipeline`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Sequence

from m2x.adapter import ModelAdapter
from m2x.corpus import DEFAULT_MANIFEST, load_corpus
# Constants only — this module keeps its torch imports inside the diarize handler, so
# `m2x process` still runs where the optional `diarize` group was never installed.
from m2x.diarization import DEFAULT_DIARIZATION_DIR
from m2x.errors import M2XError
from m2x.pipeline import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_SUMMARIES_DIR,
    DEFAULT_TRANSCRIBE_MODEL,
    DEFAULT_TRANSCRIPTS_DIR,
    ProcessOutcome,
    process_meeting,
)
from m2x.run_log import RunLogger
from m2x.run_summary import DEFAULT_RUN_LOG, format_summary, summarise
from m2x.settings import Settings
from m2x.types import Provider, Transcript

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_FAILURE = 1


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser.

    Returns:
        A parser exposing the ``process`` subcommand.
    """
    parser = argparse.ArgumentParser(
        prog="m2x",
        description="Turn a recorded meeting into a structured execution record.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    process = subcommands.add_parser(
        "process",
        help="transcribe a meeting and write the transcript JSON",
    )
    process.add_argument("audio", type=Path, help="path to the meeting audio file")
    process.add_argument(
        "--provider",
        type=Provider,
        choices=list(Provider),
        default=None,
        help=(
            "backend for the summary step — this is the hosted-vs-local switch; "
            "transcription routes separately (see --transcribe-provider)"
        ),
    )
    process.add_argument(
        "--transcribe-provider",
        type=Provider,
        choices=list(Provider),
        default=None,
        help="backend for transcription; default routes by the model's registry entry",
    )
    process.add_argument(
        "--model",
        default=DEFAULT_TRANSCRIBE_MODEL,
        help=f"Hugging Face repo id of the transcription model (default: {DEFAULT_TRANSCRIBE_MODEL})",
    )
    process.add_argument(
        "--chat-model",
        default=DEFAULT_CHAT_MODEL,
        help=f"Hugging Face repo id of the summarising model (default: {DEFAULT_CHAT_MODEL})",
    )
    process.add_argument(
        "--no-summary",
        action="store_true",
        help="stop after transcription, skipping the language-model step",
    )
    process.add_argument(
        "--meeting-id",
        default=None,
        help="stable meeting id; defaults to the audio filename stem",
    )
    process.add_argument(
        "--language",
        default=None,
        help="ISO-639-1 language hint; omit to let the model detect it",
    )
    process.add_argument(
        "--transcripts-dir",
        type=Path,
        default=DEFAULT_TRANSCRIPTS_DIR,
        help=f"where transcript JSON is written (default: {DEFAULT_TRANSCRIPTS_DIR})",
    )
    process.add_argument(
        "--summaries-dir",
        type=Path,
        default=DEFAULT_SUMMARIES_DIR,
        help=f"where summaries are written (default: {DEFAULT_SUMMARIES_DIR})",
    )

    diarize = subcommands.add_parser(
        "diarize",
        help="label who spoke when, and write a speaker-attributed transcript",
    )
    diarize.add_argument("audio", type=Path, help="path to the meeting audio file")
    diarize.add_argument(
        "--transcript",
        type=Path,
        required=True,
        help="transcript JSON to attribute; segments are matched by timestamp overlap",
    )
    diarize.add_argument(
        "--meeting-id",
        default=None,
        help="stable meeting id; defaults to the audio filename stem",
    )
    diarize.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_DIARIZATION_DIR,
        help=f"where turns and the attributed transcript land (default: {DEFAULT_DIARIZATION_DIR})",
    )
    diarize.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="corpus manifest supplying the speaker-label to name mapping",
    )

    runs = subcommands.add_parser("runs", help="report on the run log")
    run_actions = runs.add_subparsers(dest="action", required=True)
    runs_summary = run_actions.add_parser(
        "summary",
        help="totals and p50/p95 latency per phase, provider and model",
    )
    runs_summary.add_argument(
        "--log",
        type=Path,
        default=DEFAULT_RUN_LOG,
        help=f"run log to read (default: {DEFAULT_RUN_LOG})",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    adapter_factory: Callable[[], ModelAdapter] = ModelAdapter,
) -> int:
    """Run the CLI.

    Args:
        argv: Argument vector excluding the program name. Defaults to ``sys.argv[1:]``.
        adapter_factory: Builds the adapter. Injected so the command can be exercised
            end to end against a mock transport; the gate still runs the real thing.

    Returns:
        Process exit code: 0 on success, 1 on a run failure, 2 on bad input.
    """
    args = build_parser().parse_args(argv)

    if args.command == "runs":
        return _run_summary(args)

    if args.command == "diarize":
        return _run_diarize(args)

    try:
        with adapter_factory() as adapter:
            outcome = process_meeting(
                args.audio,
                adapter=adapter,
                meeting_id=args.meeting_id,
                model_repo_id=args.model,
                chat_model_repo_id=args.chat_model,
                provider=args.provider,
                transcribe_provider=args.transcribe_provider,
                language=args.language,
                summarize=not args.no_summary,
                transcripts_dir=args.transcripts_dir,
                summaries_dir=args.summaries_dir,
            )
    except FileNotFoundError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_USAGE
    except M2XError as error:
        # M2XError is the project's own failure vocabulary — a bad model id, a missing
        # key, a provider that refused. The message is already written for a human, so
        # printing a traceback on top of it would only bury it.
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(_format_outcome(outcome))
    return EXIT_OK


def _run_diarize(args: argparse.Namespace) -> int:
    """Diarise one meeting and write a speaker-attributed transcript.

    Imported here rather than at module scope: the pyannote stack lives in the optional
    ``diarize`` group, and importing torch at CLI start would make ``m2x process`` fail
    on a machine that never installed it.

    Args:
        args: Parsed ``diarize`` arguments.

    Returns:
        Process exit code.
    """
    from m2x.diarization import assign_speakers, coverage, diarize, load_pipeline

    if not args.audio.is_file():
        print(f"error: no such audio file: {args.audio}", file=sys.stderr)
        return EXIT_USAGE
    if not args.transcript.is_file():
        print(f"error: no such transcript: {args.transcript}", file=sys.stderr)
        return EXIT_USAGE

    meeting_id = args.meeting_id or args.audio.stem
    names: dict[str, str] = {}
    if args.corpus.is_file():
        try:
            names = load_corpus(args.corpus).by_id(meeting_id).speakers
        except (KeyError, ValueError):
            # A meeting absent from the manifest still diarises; it just keeps the
            # SPEAKER_xx labels. Refusing here would block the first run on a meeting,
            # which is exactly when the mapping cannot exist yet.
            names = {}

    try:
        transcript = Transcript.model_validate_json(args.transcript.read_text(encoding="utf-8"))
        hf_token = Settings().hf_token
        pipeline = load_pipeline(token=hf_token.get_secret_value() if hf_token else None)
        result = diarize(args.audio, pipeline=pipeline, meeting_id=meeting_id)
    except M2XError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    attributed = assign_speakers(transcript, result.turns, names=names)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    turns_path = args.out_dir / f"{meeting_id}.turns.json"
    transcript_path = args.out_dir / f"{meeting_id}.json"
    turns_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    transcript_path.write_text(attributed.model_dump_json(indent=2), encoding="utf-8")

    print(
        f"{meeting_id}: {len(result.turns)} turns, {result.speakers} speakers "
        f"({result.latency_ms} ms)"
    )
    print(f"  attributed  {coverage(attributed.segments):.0%} of {len(attributed.segments)} segments")
    print(f"  mapping     {'yes' if names else 'no — labels kept as SPEAKER_xx'}")
    print(f"  written     {transcript_path}")
    return EXIT_OK


def _run_summary(args: argparse.Namespace) -> int:
    """Print the run-log summary.

    Args:
        args: Parsed ``runs summary`` arguments.

    Returns:
        Process exit code. A corrupt log is a failure, not an empty report — the
        alternative is a cost figure that is quietly missing calls.
    """
    try:
        records = RunLogger(args.log).read_all()
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(format_summary(summarise(records)))
    return EXIT_OK


def _format_outcome(outcome: ProcessOutcome) -> str:
    """Render a completed run as a short human-readable block.

    The cache flag and latency are on the first line by design: the Phase 0 gate is
    read off this output, and "was the second run a cache hit?" is the question it has
    to answer at a glance.

    Args:
        outcome: Result of the run.

    Returns:
        Text to print to stdout.
    """
    transcript = outcome.transcript
    source = "cache" if transcript.cached else "provider"
    lines = [
        f"{outcome.meeting_id}: {len(transcript.segments)} segments "
        f"from {transcript.audio_seconds:.0f}s of audio "
        f"({source}, {transcript.latency_ms} ms)",
        f"  model     {transcript.model_repo_id} via {transcript.provider.value}",
        f"  language  {transcript.language or 'auto-detected'}",
        f"  cost      ${transcript.cost_usd:.4f}",
        f"  written   {outcome.transcript_path}",
    ]

    summary = outcome.summary
    if summary is not None:
        summary_source = "cache" if summary.cached else "provider"
        lines += [
            f"summary via {summary.provider.value} "
            f"({summary_source}, {summary.latency_ms} ms)",
            f"  model     {summary.model_repo_id}",
            f"  tokens    {summary.usage.tokens_in} in / {summary.usage.tokens_out} out",
            f"  cost      ${summary.cost_usd:.4f}",
            f"  written   {outcome.summary_path}",
        ]
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
