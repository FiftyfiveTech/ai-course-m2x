"""Splitting long audio so it fits a provider's upload limit, and stitching it back.

Groq rejects uploads over 25 MB with HTTP 413. At the 16 kHz mono PCM the corpus is
normalised to, that is about 13 minutes of audio — shorter than most real meetings, and
shorter than two of the three in the Day-2 comparison set. Without this module the
comparison simply cannot run on them.

The design keeps the seams intact: this module knows about **files and timestamps**,
never about providers. It hands the adapter one chunk at a time and re-assembles the
results, so every chunk still passes through the adapter's cache-and-log envelope and
the run log shows exactly as many calls as were really made.

Timestamps are the reason merging is not a string concatenation. Each chunk comes back
with times relative to its own start, so they are shifted by the chunk's offset before
the segments are joined — otherwise every citation after the first chunk would point at
the wrong moment in the meeting.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

from m2x.errors import M2XError
from m2x.types import Transcript, TranscriptSegment, Usage

MAX_UPLOAD_BYTES = 24 * 1024 * 1024
"""Split anything larger than this. Groq's documented ceiling is 25 MB; the margin
covers the multipart envelope, which counts toward the request size."""

CHUNK_SECONDS = 600
"""Ten minutes — about 19 MB at 16 kHz mono, comfortably inside the limit.

Longer chunks mean fewer boundary artefacts, so this is set as high as the limit
safely allows rather than as small as convenient.
"""


def needs_splitting(audio_path: Path, limit: int = MAX_UPLOAD_BYTES) -> bool:
    """True when the file is too large to upload whole."""
    return audio_path.stat().st_size > limit


def split_audio(
    audio_path: Path, out_dir: Path, chunk_seconds: int = CHUNK_SECONDS
) -> list[tuple[float, Path]]:
    """Cut ``audio_path`` into fixed-length pieces, returning ``(offset_s, path)``.

    Cuts land on fixed time boundaries rather than on silence. A silence-aware split
    would avoid clipping the occasional word, but it makes the chunk list depend on the
    audio's content — so a re-run after any audio change would produce different chunks,
    different cache keys, and a comparison that is no longer reproducible. The cost is
    at most one damaged word per boundary (two boundaries on the longest meeting here),
    and it is recorded in the comparison doc rather than hidden.

    Args:
        audio_path: Source audio, any format ffmpeg reads.
        out_dir: Directory for the pieces. Created if absent; existing pieces are reused,
            which keeps re-runs cheap and idempotent.
        chunk_seconds: Length of each piece.

    Returns:
        Offsets and paths, in playback order.

    Raises:
        M2XError: ffmpeg is missing or the split failed.
    """
    if shutil.which("ffmpeg") is None:
        raise M2XError("ffmpeg is required to split audio over the upload limit")

    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / f"{audio_path.stem}-%03d{audio_path.suffix}"

    if not sorted(out_dir.glob(f"{audio_path.stem}-*{audio_path.suffix}")):
        command = [
            "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
            "-i", str(audio_path),
            "-f", "segment", "-segment_time", str(chunk_seconds),
            "-c", "copy", str(pattern),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise M2XError(f"ffmpeg failed to split {audio_path}: {result.stderr.strip()}")

    pieces = sorted(out_dir.glob(f"{audio_path.stem}-*{audio_path.suffix}"))
    if not pieces:
        raise M2XError(f"splitting {audio_path} produced no chunks")
    return [(index * float(chunk_seconds), piece) for index, piece in enumerate(pieces)]


def merge_transcripts(parts: list[tuple[float, Transcript]]) -> Transcript:
    """Join per-chunk transcripts into one, shifting timestamps by each chunk's offset.

    Aggregation rules, each chosen so the merged record cannot overstate what happened:

    * ``latency_ms`` is the **sum** of the parts. The wall-clock cost of transcribing
      the meeting really was all of them; reporting the max would flatter the route.
    * ``cached`` is true only when *every* chunk was a cache hit — one live call means
      the result is not a cached one.
    * ``cost_usd`` and ``audio_seconds`` sum, so the cost report stays additive.
    * ``language`` comes from the first chunk. Whisper detects per request, and a
      meeting that switches language mid-way is a finding for the comparison doc, not
      something to paper over by picking a majority.

    Raises:
        ValueError: ``parts`` is empty, or the chunks disagree about model or provider —
            which would mean two different routes were mixed into one artefact.
    """
    if not parts:
        raise ValueError("no transcript parts to merge")

    first = parts[0][1]
    if len({part.model_repo_id for _, part in parts}) > 1:
        raise ValueError("cannot merge chunks transcribed by different models")
    if len({part.provider for _, part in parts}) > 1:
        raise ValueError("cannot merge chunks transcribed by different providers")

    segments: list[TranscriptSegment] = []
    for offset, part in parts:
        segments.extend(
            TranscriptSegment(
                t_start=segment.t_start + offset,
                t_end=segment.t_end + offset,
                text=segment.text,
                speaker=segment.speaker,
            )
            for segment in part.segments
        )

    return Transcript(
        model_repo_id=first.model_repo_id,
        provider=first.provider,
        latency_ms=sum(part.latency_ms for _, part in parts),
        cached=all(part.cached for _, part in parts),
        cost_usd=sum(part.cost_usd for _, part in parts),
        text=" ".join(part.text.strip() for _, part in parts if part.text.strip()),
        segments=segments,
        audio_seconds=sum(part.audio_seconds for _, part in parts),
        language=first.language,
        usage=Usage(),
    )


def iter_chunks(
    audio_path: Path, out_dir: Path, chunk_seconds: int = CHUNK_SECONDS
) -> Iterator[tuple[float, Path]]:
    """Yield ``(offset_s, path)`` for the whole file, splitting only if it is too large.

    Small files yield themselves at offset 0, so callers have one code path whether or
    not a split was needed.
    """
    if not needs_splitting(audio_path):
        yield 0.0, audio_path
        return
    yield from split_audio(audio_path, out_dir, chunk_seconds)
