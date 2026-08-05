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
from m2x.errors import M2XError
from m2x.pipeline import (
    DEFAULT_TRANSCRIBE_MODEL,
    DEFAULT_TRANSCRIPTS_DIR,
    ProcessOutcome,
    process_meeting,
)
from m2x.types import Provider

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
        help="force a backend; default routes by the model's registry entry",
    )
    process.add_argument(
        "--model",
        default=DEFAULT_TRANSCRIBE_MODEL,
        help=f"Hugging Face repo id of the transcription model (default: {DEFAULT_TRANSCRIBE_MODEL})",
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

    try:
        with adapter_factory() as adapter:
            outcome = process_meeting(
                args.audio,
                adapter=adapter,
                meeting_id=args.meeting_id,
                model_repo_id=args.model,
                provider=args.provider,
                language=args.language,
                transcripts_dir=args.transcripts_dir,
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
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
