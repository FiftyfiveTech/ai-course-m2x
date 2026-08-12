"""Read hand-annotated reference transcripts as citable segments.

Why this exists: M2X-033 needs 25 transcript cases to label against, and a label is only
valid against *the transcript it was made from* — segment ids are positional, so
re-transcribing renumbers everything and silently invalidates every citation. The
obvious source, the pipeline's own ASR output, is not on a fresh clone: ``data/`` is
git-ignored, so a machine that has just cloned the repo has no transcripts at all and
the ground truth could never be rebuilt.

The tiron corpus solves this. ``eval/tiron/tiron-<meeting>.speakers.json`` carries human
speaker turns and ``eval/tiron/tiron-<meeting>.txt`` the words for those same turns, one
line per turn, both committed. Together they are a transcript that every clone already
has, produced by human annotators rather than by a model — which is also the right thing
to score extraction against, because an F1 measured on ASR output confounds two failure
modes and only one of them is Phase 1B's.

These segments carry no :class:`~m2x.types.Provider` and no cost, and deliberately do not
become a :class:`~m2x.types.Transcript`: nothing served them, so there is no provenance
to record and inventing one would put a lie in the run log. Callers pass the segments
straight to :func:`m2x.extraction.extract_record`, which accepts them.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from m2x.errors import ConfigError
from m2x.types import TranscriptSegment

DEFAULT_REFERENCE_DIR = Path("eval/tiron")
"""Where the committed reference turns and words live. Tracked, unlike ``data/``."""


class ReferenceCase(BaseModel):
    """A contiguous slice of one reference transcript, as a labelling unit.

    A "case" in M2X-033 is a coherent chunk of roughly 2-5 minutes rather than a whole
    meeting, so 25 of them can be drawn from a handful of meetings without labelling the
    same passage twice.
    """

    model_config = ConfigDict(frozen=True)

    case_id: str
    """Stable id, ``<meeting>-c<NN>``. Names the meeting so an overlap is visible."""

    meeting_id: str
    """Which reference transcript this was cut from, e.g. ``tiron-ES2004a``."""

    first_turn: int = Field(ge=0)
    """Index of the first reference turn in the slice, 0-based and inclusive."""

    last_turn: int = Field(ge=0)
    """Index of the last reference turn, 0-based and inclusive."""

    segments: list[TranscriptSegment]
    """The slice itself. Segment ids are positional *within this list*, so a case is
    the unit a citation resolves against — never the whole meeting."""

    @property
    def duration_s(self) -> float:
        """Wall-clock span of the case, in seconds."""
        if not self.segments:
            return 0.0
        return self.segments[-1].t_end - self.segments[0].t_start


def load_reference_segments(
    meeting_id: str, *, reference_dir: Path = DEFAULT_REFERENCE_DIR
) -> list[TranscriptSegment]:
    """Read one meeting's reference turns and words as segments.

    Args:
        meeting_id: Reference meeting id, e.g. ``tiron-ES2004a``.
        reference_dir: Directory holding the ``.speakers.json`` / ``.txt`` pair.

    Returns:
        Segments in transcript order, one per annotated turn.

    Raises:
        ConfigError: A file is missing, or the turns and the words disagree on how many
            turns there are — which would silently shift every later line onto the wrong
            timestamps.
        OSError: A file could not be read.
    """
    turns_path = reference_dir / f"{meeting_id}.speakers.json"
    words_path = reference_dir / f"{meeting_id}.txt"
    for path in (turns_path, words_path):
        if not path.exists():
            raise ConfigError(f"reference transcript {path} does not exist")

    turns = json.loads(turns_path.read_text(encoding="utf-8"))["segments"]
    # `splitlines()` on the stripped text: the file ends with a trailing newline, and
    # counting that as an empty final turn is exactly the off-by-one this check exists
    # to catch.
    lines = words_path.read_text(encoding="utf-8").strip("\n").split("\n")
    if len(turns) != len(lines):
        raise ConfigError(
            f"{meeting_id}: {len(turns)} reference turns but {len(lines)} lines of text; "
            "the pair must align one-to-one or every citation after the mismatch is wrong"
        )

    return [
        TranscriptSegment(
            t_start=float(turn["t_start"]),
            t_end=float(turn["t_end"]),
            text=line.strip(),
            speaker=str(turn["speaker"]),
        )
        for turn, line in zip(turns, lines, strict=True)
    ]


def slice_case(
    meeting_id: str,
    segments: list[TranscriptSegment],
    *,
    case_number: int,
    first_turn: int,
    last_turn: int,
) -> ReferenceCase:
    """Cut one labelling case out of a meeting's segments.

    Args:
        meeting_id: Reference meeting the slice comes from.
        segments: That meeting's full segment list.
        case_number: 1-based ordinal, used in the case id.
        first_turn: Index of the first turn to include, 0-based inclusive.
        last_turn: Index of the last turn to include, 0-based inclusive.

    Returns:
        The case.

    Raises:
        ConfigError: The bounds are inverted or fall outside the meeting.
    """
    if first_turn > last_turn:
        raise ConfigError(f"case bounds inverted: {first_turn} > {last_turn}")
    if last_turn >= len(segments):
        raise ConfigError(
            f"case ends at turn {last_turn} but {meeting_id} has only {len(segments)} turns"
        )
    return ReferenceCase(
        case_id=f"{meeting_id}-c{case_number:02d}",
        meeting_id=meeting_id,
        first_turn=first_turn,
        last_turn=last_turn,
        segments=segments[first_turn : last_turn + 1],
    )
