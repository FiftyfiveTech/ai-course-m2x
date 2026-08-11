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

from instructor.core import InstructorRetryException
from pydantic import ValidationError

from m2x.adapter import ModelAdapter
from m2x.chaptering import (
    DEFAULT_CHAPTER_MODEL,
    DEFAULT_CHAPTERS_DIR,
    DEFAULT_WINDOW_S,
    ChapterSet,
    chapter_fixed,
    chapter_llm,
    load_chapters,
    write_chapters,
)
from m2x.corpus import DEFAULT_MANIFEST, load_corpus
# Constants only — this module keeps its torch imports inside the diarize handler, so
# `m2x process` still runs where the optional `diarize` group was never installed.
from m2x.diarization import DEFAULT_DIARIZATION_DIR
from m2x.errors import M2XError
from m2x.extraction import (
    DEFAULT_EXTRACT_MODEL,
    DEFAULT_RECORDS_DIR,
    ExtractionOutcome,
    extract_record,
    write_record,
)
from m2x.pipeline import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_SUMMARIES_DIR,
    DEFAULT_TRANSCRIBE_MODEL,
    DEFAULT_TRANSCRIPTS_DIR,
    ProcessOutcome,
    load_transcript,
    process_meeting,
)
from m2x.prompts import DEFAULT_PROMPTS_DIR
from m2x.run_log import RunLogger
from m2x.run_summary import DEFAULT_RUN_LOG, format_summary, summarise
from m2x.settings import Settings
from m2x.summarisation import (
    DEFAULT_STRATEGY_SUMMARIES_DIR,
    DEFAULT_SUMMARY_MODEL,
    SummaryOutcome,
    summarise_map_reduce,
    summarise_single_pass,
    write_strategy_summary,
)
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
    diarize.add_argument(
        "--num-speakers",
        type=int,
        default=None,
        help=(
            "fix the speaker count instead of letting clustering choose; unconstrained "
            "runs over-cluster on this corpus (see docs/design/day2-matrix.md)"
        ),
    )

    extract = subcommands.add_parser(
        "extract",
        help="extract a validated meeting record from a transcript",
    )
    extract.add_argument("meeting_id", help="meeting id; also the transcript filename stem")
    extract.add_argument(
        "--transcript",
        type=Path,
        default=None,
        help=(
            "transcript JSON to extract from; default prefers the diarised transcript "
            f"in {DEFAULT_DIARIZATION_DIR} and falls back to {DEFAULT_TRANSCRIPTS_DIR}"
        ),
    )
    extract.add_argument(
        "--model",
        default=DEFAULT_EXTRACT_MODEL,
        help=f"Hugging Face repo id of the extraction model (default: {DEFAULT_EXTRACT_MODEL})",
    )
    extract.add_argument(
        "--provider",
        type=Provider,
        choices=list(Provider),
        default=None,
        help="backend for extraction; default routes by the model's registry entry",
    )
    extract.add_argument(
        "--prompt-version",
        default=None,
        help=(
            "prompt library version to extract with, e.g. 'v1'; default is the latest "
            "on disk. Pin it to reproduce a number that was reported with an older one"
        ),
    )
    extract.add_argument(
        "--prompts-dir",
        type=Path,
        default=DEFAULT_PROMPTS_DIR,
        help=f"root of the prompt library (default: {DEFAULT_PROMPTS_DIR})",
    )
    extract.add_argument(
        "--records-dir",
        type=Path,
        default=DEFAULT_RECORDS_DIR,
        help=f"where record JSON is written (default: {DEFAULT_RECORDS_DIR})",
    )

    chapter = subcommands.add_parser(
        "chapter",
        help="cut a transcript into chapters by one of two strategies",
    )
    chapter.add_argument("meeting_id", help="meeting id; also the transcript filename stem")
    chapter.add_argument(
        "--strategy",
        choices=("fixed", "llm"),
        default="fixed",
        help="fixed windows (free, topic-blind) or LLM topic-shift detection (one call)",
    )
    chapter.add_argument(
        "--transcript",
        type=Path,
        default=None,
        help="transcript JSON; defaults to the diarised one, then the plain one",
    )
    chapter.add_argument(
        "--window",
        type=float,
        default=DEFAULT_WINDOW_S,
        help=f"fixed-window length in seconds (default: {DEFAULT_WINDOW_S:.0f})",
    )
    chapter.add_argument(
        "--model",
        default=DEFAULT_CHAPTER_MODEL,
        help=f"Hugging Face repo id for the llm strategy (default: {DEFAULT_CHAPTER_MODEL})",
    )
    chapter.add_argument(
        "--provider",
        type=Provider,
        choices=list(Provider),
        default=None,
        help="backend for the llm strategy",
    )
    chapter.add_argument(
        "--chapters-dir",
        type=Path,
        default=DEFAULT_CHAPTERS_DIR,
        help=f"where chapter JSON is written (default: {DEFAULT_CHAPTERS_DIR})",
    )

    summarise_cmd = subcommands.add_parser(
        "summarise",
        help="summarise a meeting by one of two strategies",
    )
    summarise_cmd.add_argument("meeting_id", help="meeting id; also the transcript filename stem")
    summarise_cmd.add_argument(
        "--strategy",
        choices=("single-pass", "map-reduce"),
        default="single-pass",
        help="whole transcript in one call, or per-chapter summaries then a merge",
    )
    summarise_cmd.add_argument(
        "--transcript",
        type=Path,
        default=None,
        help="transcript JSON; defaults to the diarised one, then the plain one",
    )
    summarise_cmd.add_argument(
        "--chapters",
        type=Path,
        default=None,
        help="chapter JSON for map-reduce; defaults to the fixed chaptering of this meeting",
    )
    summarise_cmd.add_argument(
        "--model",
        default=DEFAULT_SUMMARY_MODEL,
        help=f"Hugging Face repo id of the summarising model (default: {DEFAULT_SUMMARY_MODEL})",
    )
    summarise_cmd.add_argument(
        "--provider",
        type=Provider,
        choices=list(Provider),
        default=None,
        help="backend for the summary calls",
    )
    summarise_cmd.add_argument(
        "--summaries-dir",
        type=Path,
        default=DEFAULT_STRATEGY_SUMMARIES_DIR,
        help=f"where summaries are written (default: {DEFAULT_STRATEGY_SUMMARIES_DIR})",
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

    if args.command == "extract":
        return _run_extract(args, adapter_factory=adapter_factory)

    if args.command == "chapter":
        return _run_chapter(args, adapter_factory=adapter_factory)

    if args.command == "summarise":
        return _run_summarise(args, adapter_factory=adapter_factory)

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
        result = diarize(
            args.audio,
            pipeline=pipeline,
            meeting_id=meeting_id,
            num_speakers=args.num_speakers,
        )
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


def _run_extract(
    args: argparse.Namespace,
    *,
    adapter_factory: Callable[[], ModelAdapter],
) -> int:
    """Extract one meeting record and write it.

    Args:
        args: Parsed ``extract`` arguments.
        adapter_factory: Builds the adapter performing the calls.

    Returns:
        Process exit code. A record that never validated is a failure, not an empty
        file — the Phase 1B gate counts schema validity, so a silently empty record
        would be a failure disguised as a pass.
    """
    path = args.transcript or _default_transcript_path(args.meeting_id)
    if not path.is_file():
        print(f"error: no such transcript: {path}", file=sys.stderr)
        return EXIT_USAGE

    try:
        transcript = load_transcript(path)
    except (OSError, ValidationError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_USAGE

    try:
        with adapter_factory() as adapter:
            outcome = extract_record(
                transcript,
                adapter=adapter,
                meeting_id=args.meeting_id,
                model_repo_id=args.model,
                provider=args.provider,
                prompt_version=args.prompt_version,
                prompts_dir=args.prompts_dir,
            )
    except M2XError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    except InstructorRetryException as error:
        # Every attempt failed validation. The last error is the useful one: it names
        # the field or the citation the model could not get right.
        print(f"error: extraction never validated after retries: {error}", file=sys.stderr)
        return EXIT_FAILURE

    record_path = write_record(outcome, args.records_dir)
    print(_format_extraction(outcome, source=path, written=record_path))
    return EXIT_OK


def _default_transcript_path(meeting_id: str) -> Path:
    """Locate a transcript for a meeting.

    Shared by ``extract``, ``chapter`` and ``summarise`` — one rule, so the three commands
    cannot disagree about which transcript a meeting id means.

    Prefers the diarised transcript: speaker labels are what let the extractor attribute
    an action to the person who accepted it rather than leaving every owner ``None``, and
    what makes a chapter boundary legible to a reader.

    Args:
        meeting_id: Meeting whose transcript is wanted.

    Returns:
        The diarised transcript if one exists, otherwise the plain one.
    """
    diarised = DEFAULT_DIARIZATION_DIR / f"{meeting_id}.json"
    return diarised if diarised.is_file() else DEFAULT_TRANSCRIPTS_DIR / f"{meeting_id}.json"


def _format_extraction(outcome: ExtractionOutcome, *, source: Path, written: Path) -> str:
    """Render a completed extraction as a short human-readable block.

    Args:
        outcome: The extraction.
        source: Transcript it was extracted from.
        written: File the record was written to.

    Returns:
        Text to print to stdout.
    """
    record = outcome.record
    lines = [
        f"{outcome.meeting_id}: {record.item_count} items from {source} "
        f"({outcome.attempts} attempt{'s' if outcome.attempts != 1 else ''}, "
        f"{outcome.latency_ms} ms)",
        f"  decisions {len(record.decisions)}  actions {len(record.actions)}  "
        f"risks {len(record.risks)}  questions {len(record.open_questions)}",
        f"  model     {outcome.model_repo_id} via {outcome.provider.value}",
        f"  prompt    {outcome.prompt_name} {outcome.prompt_version}",
        f"  cost      ${outcome.cost_usd:.4f}",
    ]
    if outcome.truncated:
        lines.append("  warning   transcript truncated; the tail was not extracted from")
    lines.append(f"  written   {written}")
    return "\n".join(lines)


def _load_transcript_or_exit(path: Path) -> Transcript | int:
    """Read a transcript, or return the exit code to use.

    Args:
        path: Transcript JSON file.

    Returns:
        The transcript, or an exit code when it could not be read.
    """
    if not path.is_file():
        print(f"error: no such transcript: {path}", file=sys.stderr)
        return EXIT_USAGE
    try:
        return load_transcript(path)
    except (OSError, ValidationError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_USAGE


def _run_chapter(
    args: argparse.Namespace,
    *,
    adapter_factory: Callable[[], ModelAdapter],
) -> int:
    """Chapter one meeting and write the result.

    Args:
        args: Parsed ``chapter`` arguments.
        adapter_factory: Builds the adapter, used only by the ``llm`` strategy.

    Returns:
        Process exit code.
    """
    loaded = _load_transcript_or_exit(args.transcript or _default_transcript_path(args.meeting_id))
    if isinstance(loaded, int):
        return loaded

    try:
        if args.strategy == "fixed":
            chapters = chapter_fixed(loaded, meeting_id=args.meeting_id, window_s=args.window)
        else:
            with adapter_factory() as adapter:
                chapters = chapter_llm(
                    loaded,
                    adapter=adapter,
                    meeting_id=args.meeting_id,
                    model_repo_id=args.model,
                    provider=args.provider,
                )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_USAGE
    except M2XError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    path = write_chapters(chapters, args.chapters_dir)
    print(_format_chapters(chapters, written=path))
    return EXIT_OK


def _run_summarise(
    args: argparse.Namespace,
    *,
    adapter_factory: Callable[[], ModelAdapter],
) -> int:
    """Summarise one meeting by the requested strategy.

    Args:
        args: Parsed ``summarise`` arguments.
        adapter_factory: Builds the adapter performing the calls.

    Returns:
        Process exit code.
    """
    loaded = _load_transcript_or_exit(args.transcript or _default_transcript_path(args.meeting_id))
    if isinstance(loaded, int):
        return loaded

    chapters: ChapterSet | None = None
    if args.strategy == "map-reduce":
        chapters_path = args.chapters or DEFAULT_CHAPTERS_DIR / f"{args.meeting_id}.fixed.json"
        if not chapters_path.is_file():
            print(
                f"error: no chapters at {chapters_path}; run `m2x chapter {args.meeting_id}` first",
                file=sys.stderr,
            )
            return EXIT_USAGE
        try:
            chapters = load_chapters(chapters_path)
        except (OSError, ValidationError) as error:
            print(f"error: {error}", file=sys.stderr)
            return EXIT_USAGE

    try:
        with adapter_factory() as adapter:
            if chapters is None:
                outcome = summarise_single_pass(
                    loaded,
                    adapter=adapter,
                    meeting_id=args.meeting_id,
                    model_repo_id=args.model,
                    provider=args.provider,
                )
            else:
                outcome = summarise_map_reduce(
                    chapters,
                    adapter=adapter,
                    meeting_id=args.meeting_id,
                    model_repo_id=args.model,
                    provider=args.provider,
                )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_USAGE
    except M2XError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    path = write_strategy_summary(outcome, args.summaries_dir)
    print(_format_summary_outcome(outcome, written=path))
    return EXIT_OK


def _format_chapters(chapters: ChapterSet, *, written: Path) -> str:
    """Render a chaptering as a short human-readable block.

    Args:
        chapters: The chapter set.
        written: File it was written to.

    Returns:
        Text to print to stdout.
    """
    durations = [chapter.duration_s for chapter in chapters.chapters]
    lines = [
        f"{chapters.meeting_id}: {chapters.count} chapters by {chapters.strategy} "
        f"({chapters.latency_ms} ms)",
        f"  length    min {min(durations):.0f}s / median {sorted(durations)[len(durations) // 2]:.0f}s "
        f"/ max {max(durations):.0f}s",
    ]
    if chapters.model_repo_id:
        lines.append(f"  model     {chapters.model_repo_id} via {chapters.provider.value}")
        lines.append(f"  cost      ${chapters.cost_usd:.4f}")
    lines.append(f"  written   {written}")
    return "\n".join(lines)


def _format_summary_outcome(outcome: SummaryOutcome, *, written: Path) -> str:
    """Render a summarisation run as a short human-readable block.

    Args:
        outcome: The summary.
        written: File it was written to.

    Returns:
        Text to print to stdout.
    """
    lines = [
        f"{outcome.meeting_id}: {outcome.strategy} in {outcome.calls} call"
        f"{'s' if outcome.calls != 1 else ''} ({outcome.latency_ms} ms)",
        f"  model     {outcome.model_repo_id} via {outcome.provider.value}",
        f"  tokens    {outcome.tokens_in} in / {outcome.tokens_out} out",
        f"  cost      ${outcome.cost_usd:.4f}",
    ]
    if outcome.truncated:
        lines.append("  warning   input truncated; the tail was not summarised")
    lines.append(f"  written   {written}")
    return "\n".join(lines)


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
