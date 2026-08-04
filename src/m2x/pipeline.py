"""The processing pipeline: audio in, persisted transcript out.

This is the seam between the CLI and the adapter, and it exists so the vertical slice
is testable without a terminal. The CLI parses arguments and prints; everything that
decides *what happens to a meeting* lives here.

One rule shapes the module: **the pipeline never talks to a provider.** It is handed a
:class:`~m2x.adapter.ModelAdapter` and asks it for work, which keeps every model call
inside the adapter's cache-and-log envelope. A convenience helper that quietly
constructed its own HTTP client would be the exact hole the run log is meant to close.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from m2x.adapter import ModelAdapter
from m2x.run_log import RunContext
from m2x.types import Provider, Transcript

PHASE = "phase-0"
"""Pipeline phase these calls are attributed to in the run log."""

DEFAULT_TRANSCRIBE_MODEL = "openai/whisper-large-v3"
"""Hugging Face repo id of the default speech-to-text model.

Named by repo id, never by a provider's own alias — the registry does that translation.
"""

DEFAULT_TRANSCRIPTS_DIR = Path("data/transcripts")
"""Where transcripts land. Under git-ignored ``data/``, created on demand."""


class ProcessOutcome(BaseModel):
    """What one ``m2x process`` run produced.

    Returned instead of a bare :class:`~m2x.types.Transcript` because the caller needs
    to report *where* the artefact was written, and re-deriving that path in two places
    is how the two copies eventually disagree.
    """

    model_config = ConfigDict(frozen=True)

    meeting_id: str
    """Stable id for this meeting; also the transcript filename stem."""

    transcript: Transcript
    """The transcript as returned by the adapter, cache metadata included."""

    transcript_path: Path
    """File the transcript JSON was written to."""


def process_meeting(
    audio_path: Path,
    *,
    adapter: ModelAdapter,
    meeting_id: str | None = None,
    model_repo_id: str = DEFAULT_TRANSCRIBE_MODEL,
    provider: Provider | None = None,
    language: str | None = None,
    transcripts_dir: Path = DEFAULT_TRANSCRIPTS_DIR,
) -> ProcessOutcome:
    """Transcribe one meeting and persist the result.

    Args:
        audio_path: Audio file to process.
        adapter: Adapter that performs — and therefore caches and logs — the call.
        meeting_id: Stable id for the meeting. Defaults to the audio filename stem,
            which is why corpus files are named ``mtg-001-...``: the id then falls out
            of the filesystem instead of needing a second registry to look it up in.
        model_repo_id: Hugging Face repo id of the transcription model.
        provider: Force a backend. ``None`` uses the model's default route.
        language: ISO-639-1 hint, or ``None`` to let the model detect it.
        transcripts_dir: Directory for transcript JSON.

    Returns:
        The transcript together with the path it was written to.

    Raises:
        FileNotFoundError: ``audio_path`` does not exist. Checked here rather than
            being left to the adapter, so the failure names the file the user typed
            instead of surfacing from inside an HTTP helper.
        M2XError: Any configuration, routing, or provider failure.
        OSError: The transcript could not be written.
    """
    if not audio_path.is_file():
        raise FileNotFoundError(f"no such audio file: {audio_path}")

    resolved_id = meeting_id or audio_path.stem

    transcript = adapter.transcribe(
        audio_path,
        model_repo_id,
        provider=provider,
        language=language,
        context=RunContext(phase=PHASE, command="m2x process", meeting_id=resolved_id),
    )

    transcript_path = write_transcript(transcript, resolved_id, transcripts_dir)
    return ProcessOutcome(
        meeting_id=resolved_id,
        transcript=transcript,
        transcript_path=transcript_path,
    )


def write_transcript(
    transcript: Transcript,
    meeting_id: str,
    transcripts_dir: Path = DEFAULT_TRANSCRIPTS_DIR,
) -> Path:
    """Write a transcript to ``<transcripts_dir>/<meeting_id>.json``.

    The whole :class:`~m2x.types.Transcript` model is dumped, not just its segments.
    Provider, model repo id and cache flag travel with the artefact so a transcript
    found on disk months later can still answer "what produced this?" — the same
    question the run log answers for calls, kept with the output for files.

    Args:
        transcript: Transcript to persist.
        meeting_id: Filename stem.
        transcripts_dir: Destination directory, created if absent.

    Returns:
        The path written.

    Raises:
        OSError: The directory could not be created or the file could not be written.
    """
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    path = transcripts_dir / f"{meeting_id}.json"
    path.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_transcript(path: Path) -> Transcript:
    """Read a transcript back and validate it.

    Args:
        path: Transcript JSON file.

    Returns:
        The validated transcript.

    Raises:
        OSError: The file could not be read.
        pydantic.ValidationError: The file is not a valid transcript.
    """
    return Transcript.model_validate_json(path.read_text(encoding="utf-8"))
