"""Turning transcripts and documents into citable chunks.

A chunk is the unit of *recall*: the smallest thing retrieval can hand a reader while
saying "this is where the answer is". That makes chunking a retrieval decision, not a
formatting one, and it is why the fixed five-minute windows adopted for summarisation
(M2X-023) are the wrong unit here — five minutes of a meeting is several topics, and one
vector over several topics matches none of them sharply.

Two properties this module exists to guarantee:

* **Chunks are built from whole transcript segments.** A segment is the smallest unit
  carrying a ``t_start`` and ``t_end`` from the transcriber, so packing whole segments
  means a chunk's time range is exact by construction. Splitting mid-segment would make
  every citation an interpolation — a guess that renders as a fact.
* **Chunk ids are content-addressed**, derived from what identifies the chunk rather
  than generated. Re-indexing the same corpus produces the same ids, so an upsert
  overwrites instead of appending and a rebuild is idempotent. Hashing the text alone
  would be worse than useless: two meetings that both contain "sounds good" would
  collide onto one id.

This module holds no vector-store dependency on purpose — chunking is deterministic,
pure, and worth testing without a database. Storage lives in :mod:`m2x.vector_store`.
"""

from __future__ import annotations

import re
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from m2x.types import Transcript, TranscriptSegment

CHUNK_CHAR_BUDGET = 1200
"""Target chunk size in characters.

Roughly a long paragraph: big enough to hold the reasoning that makes a passage an
answer, small enough that its embedding describes one topic. Whole segments are never
split to hit it, so a single long segment produces an oversized chunk rather than a
severed timestamp.
"""

CHUNK_OVERLAP_SEGMENTS = 1
"""Segments repeated between neighbouring transcript chunks.

An answer that straddles a boundary is otherwise in neither chunk in full. One segment
is enough to carry the sentence across, and costs a few percent of index size.
"""

CHUNK_ID_LENGTH = 16
"""Hex characters kept from the chunk-id digest. 64 bits — collision is not the risk here."""

HEADING_PATTERN = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)
"""Markdown ATX heading. Documents are chunked on these, per the ticket."""


class SourceType(StrEnum):
    """Where a chunk came from.

    Kept as metadata rather than inferred from the id, because a query may want to ask
    for one or the other: "what did the meeting decide" and "what does the brief say"
    are different questions over the same index.
    """

    MEETING = "meeting"
    DOC = "doc"


class Chunk(BaseModel):
    """One indexable passage, with everything a citation needs.

    Metadata that is not attached here cannot be recovered at query time — a vector
    search returns exactly what was put in, and nothing else.
    """

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    """Deterministic id. Same source and same range always produce the same value."""

    source_id: str
    """Meeting id, or document slug. Namespaces the chunk within the index."""

    source_type: SourceType
    text: str

    segment_start: int | None = None
    """1-based index of the first transcript segment in this chunk. ``None`` for docs."""

    segment_end: int | None = None
    """1-based index of the last segment, inclusive. ``None`` for docs."""

    t_start: float | None = None
    """Start time in seconds, taken from the first segment. ``None`` for docs."""

    t_end: float | None = None
    """End time in seconds, taken from the last segment. ``None`` for docs."""

    speakers: tuple[str, ...] = ()
    """Speakers appearing in this chunk, in first-appearance order. Empty when undiarised."""

    heading: str | None = None
    """Nearest markdown heading above this passage. ``None`` for meetings."""

    source_path: str | None = None
    """Path the chunk was read from, for documents."""

    ordinal: int = Field(default=0, ge=0)
    """Position of this chunk within its source, 0-based. Orders a document's chunks."""

    def to_metadata(self) -> dict[str, str | int | float | bool]:
        """Flatten to Chroma-compatible metadata.

        Chroma accepts only scalar values — no lists, no ``None``. So speakers are
        joined, and a field that does not apply to this source type is **omitted**
        rather than defaulted. That distinction matters: ``0.0`` is a real timestamp,
        and using it to mean "not applicable" would make a document look like something
        said in the first second of a meeting.

        Returns:
            Metadata with every inapplicable key absent.
        """
        metadata: dict[str, str | int | float | bool] = {
            "source_id": self.source_id,
            "source_type": self.source_type.value,
            "ordinal": self.ordinal,
        }
        if self.segment_start is not None:
            metadata["segment_start"] = self.segment_start
        if self.segment_end is not None:
            metadata["segment_end"] = self.segment_end
        if self.t_start is not None:
            metadata["t_start"] = self.t_start
        if self.t_end is not None:
            metadata["t_end"] = self.t_end
        if self.speakers:
            metadata["speakers"] = ", ".join(self.speakers)
        if self.heading:
            metadata["heading"] = self.heading
        if self.source_path:
            metadata["source_path"] = self.source_path
        return metadata

    @property
    def citation(self) -> str:
        """One-line human citation, e.g. ``mtg-001 12:30–14:05`` or ``brief § Scope``."""
        if self.source_type is SourceType.MEETING and self.t_start is not None:
            return f"{self.source_id} {format_timestamp(self.t_start)}–{format_timestamp(self.t_end or self.t_start)}"
        if self.heading:
            return f"{self.source_id} § {self.heading}"
        return f"{self.source_id} #{self.ordinal}"


def format_timestamp(seconds: float) -> str:
    """Render seconds as ``mm:ss`` (or ``h:mm:ss`` past an hour)."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def chunk_id(source_type: SourceType, source_id: str, start: int, end: int) -> str:
    """Derive the deterministic id for a chunk.

    Keyed on the source and the range within it — never on the text. Identical text in
    two meetings is common ("sounds good"), and collapsing those onto one id would make
    the second overwrite the first.

    Args:
        source_type: Meeting or document.
        source_id: Meeting id or document slug.
        start: First unit in the range (segment index, or chunk ordinal for docs).
        end: Last unit in the range, inclusive.

    Returns:
        A short hex digest, stable across runs and machines.
    """
    seed = f"{source_type.value}|{source_id}|{start}|{end}"
    return sha256(seed.encode("utf-8")).hexdigest()[:CHUNK_ID_LENGTH]


def chunk_transcript(
    transcript: Transcript,
    meeting_id: str,
    *,
    char_budget: int = CHUNK_CHAR_BUDGET,
    overlap_segments: int = CHUNK_OVERLAP_SEGMENTS,
) -> list[Chunk]:
    """Pack a transcript's segments into overlapping chunks.

    Segments are never split. A segment longer than the budget becomes an oversized
    chunk of its own, which is the right trade: an approximate timestamp is worse than
    a large chunk, because only one of the two is visibly wrong.

    Args:
        transcript: Transcript to chunk. Segments are taken in order.
        meeting_id: Stable id for the meeting; namespaces the chunk ids.
        char_budget: Target chunk size in characters.
        overlap_segments: Segments repeated between neighbours. ``0`` disables overlap.

    Returns:
        Chunks in transcript order. Empty when the transcript has no segments.

    Raises:
        ValueError: ``char_budget`` is not positive, or ``overlap_segments`` is negative.
    """
    if char_budget <= 0:
        raise ValueError(f"char_budget must be positive, got {char_budget}")
    if overlap_segments < 0:
        raise ValueError(f"overlap_segments must be >= 0, got {overlap_segments}")

    segments = transcript.segments
    chunks: list[Chunk] = []
    start = 0

    while start < len(segments):
        end = start
        used = 0
        while end < len(segments):
            line = _segment_line(segments[end])
            # `end == start` guarantees progress: one segment always fits, budget or not.
            if used and used + len(line) + 1 > char_budget:
                break
            used += len(line) + 1
            end += 1

        window = segments[start:end]
        speakers: list[str] = []
        for segment in window:
            if segment.speaker and segment.speaker not in speakers:
                speakers.append(segment.speaker)

        chunks.append(
            Chunk(
                chunk_id=chunk_id(SourceType.MEETING, meeting_id, start + 1, end),
                source_id=meeting_id,
                source_type=SourceType.MEETING,
                text="\n".join(_segment_line(segment) for segment in window),
                segment_start=start + 1,
                segment_end=end,
                t_start=window[0].t_start,
                t_end=window[-1].t_end,
                speakers=tuple(speakers),
                ordinal=len(chunks),
            )
        )

        if end >= len(segments):
            break
        start = max(end - overlap_segments, start + 1)

    return chunks


def _segment_line(segment: TranscriptSegment) -> str:
    """Render one segment as the text that gets embedded.

    The speaker is kept inline because "who said it" is part of what a reader is
    matching on, and dropping it would make every diarised chunk anonymous in the index.
    """
    text = segment.text.strip()
    return f"{segment.speaker}: {text}" if segment.speaker else text


def chunk_document(
    text: str,
    source_id: str,
    *,
    source_path: str | None = None,
    char_budget: int = CHUNK_CHAR_BUDGET,
) -> list[Chunk]:
    """Split a markdown document into chunks, one per heading section.

    Each chunk carries its heading line, because the heading is often the only thing
    naming the subject the section is about — a paragraph of a scope document reads as
    generic without "## Out of scope" above it.

    Sections longer than the budget are split further on blank lines, keeping the
    heading on each part.

    Args:
        text: Document contents.
        source_id: Slug identifying the document in the index.
        source_path: Path to record for citation.
        char_budget: Target chunk size in characters.

    Returns:
        Chunks in document order. Empty when the document has no non-blank content.

    Raises:
        ValueError: ``char_budget`` is not positive.
    """
    if char_budget <= 0:
        raise ValueError(f"char_budget must be positive, got {char_budget}")

    chunks: list[Chunk] = []
    for heading, body in _sections(text):
        header_line = f"{'#' * heading[0]} {heading[1]}\n\n" if heading else ""
        for part in _split_to_budget(body, char_budget - len(header_line)):
            ordinal = len(chunks)
            chunks.append(
                Chunk(
                    chunk_id=chunk_id(SourceType.DOC, source_id, ordinal, ordinal),
                    source_id=source_id,
                    source_type=SourceType.DOC,
                    text=f"{header_line}{part}",
                    heading=heading[1] if heading else None,
                    source_path=source_path,
                    ordinal=ordinal,
                )
            )
    return chunks


def load_document(path: Path, *, char_budget: int = CHUNK_CHAR_BUDGET) -> list[Chunk]:
    """Read a markdown file and chunk it.

    Args:
        path: File to read.
        char_budget: Target chunk size in characters.

    Returns:
        The document's chunks, keyed by the file stem as source id.

    Raises:
        OSError: The file could not be read.
    """
    return chunk_document(
        path.read_text(encoding="utf-8"),
        path.stem,
        source_path=str(path),
        char_budget=char_budget,
    )


def _sections(text: str) -> list[tuple[tuple[int, str] | None, str]]:
    """Split markdown into ``(heading, body)`` pairs.

    Content before the first heading is returned with a ``None`` heading rather than
    dropped — a README's opening paragraph is often the only place the project describes
    itself.
    """
    matches = list(HEADING_PATTERN.finditer(text))
    sections: list[tuple[tuple[int, str] | None, str]] = []

    preamble = text[: matches[0].start()] if matches else text
    if preamble.strip():
        sections.append((None, preamble.strip()))

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip()
        if body:
            sections.append(((len(match.group(1)), match.group(2)), body))

    return sections


def _split_to_budget(body: str, budget: int) -> list[str]:
    """Break a section into parts of at most ``budget`` characters, on blank lines.

    A paragraph longer than the budget is emitted whole. Splitting mid-sentence to hit
    a number produces a chunk that reads as truncated to a human and embeds as noise.
    """
    if budget <= 0 or len(body) <= budget:
        return [body]

    parts: list[str] = []
    current: list[str] = []
    used = 0
    for paragraph in body.split("\n\n"):
        stripped = paragraph.strip()
        if not stripped:
            continue
        if current and used + len(stripped) + 2 > budget:
            parts.append("\n\n".join(current))
            current, used = [], 0
        current.append(stripped)
        used += len(stripped) + 2

    if current:
        parts.append("\n\n".join(current))
    return parts
