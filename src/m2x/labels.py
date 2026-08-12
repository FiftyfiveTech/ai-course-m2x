"""Hand-written ground truth: what a labelled case is, and how it stays valid.

A label is only meaningful against *the exact segments it was written from*. Segment ids
are positional, so a case stores its **bounds** rather than a copy of the text: the
meeting id plus the first and last reference turn. The segments are then re-derived from
the committed reference transcript whenever they are needed, which makes it impossible
for the labeller's view and the extractor's view to drift apart — there is only one copy
of the words, and both sides slice it identically.

The alternative — pasting the transcript into the label file — looks safer and is not.
Two copies of the same text diverge the first time one is touched, and the resulting
mismatch shows up as a low F1 with no bug to find, which is precisely the failure mode
the whole dev/held-out apparatus exists to avoid.

Labels are written blind, per M2X-033: from the transcript only, never from extractor
output. See ``eval/labels/README.md`` for the labelling rules and
``docs/design/day3-schema.md`` for the frozen contract they follow.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from m2x.errors import ConfigError
from m2x.reference_transcript import DEFAULT_REFERENCE_DIR, load_reference_segments
from m2x.schema import SEGMENT_CONTEXT_KEY, MeetingRecord
from m2x.types import TranscriptSegment

DEFAULT_LABELS_DIR = Path("eval/labels")
"""Root of the ground truth. ``dev/`` iterates, ``heldout/`` certifies once."""


class LabelledCase(BaseModel):
    """One hand-labelled transcript case.

    Attributes are deliberately minimal: everything that can be derived from the
    reference transcript is derived, so the only thing this file asserts is the human
    judgement that cannot be computed.
    """

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(min_length=1)
    """Stable id, ``<meeting>-c<NN>``."""

    meeting_id: str = Field(min_length=1)
    """Reference meeting the case was cut from, e.g. ``tiron-ES2004a``."""

    first_turn: int = Field(ge=0)
    """First reference turn in the case, 0-based inclusive."""

    last_turn: int = Field(ge=0)
    """Last reference turn in the case, 0-based inclusive."""

    label: MeetingRecord
    """The ground truth for this case, following the frozen contract."""

    notes: str = ""
    """Edge-case reasoning worth keeping — why an item was or was not recorded.

    Written for the adjudicator, not for the harness. When a labelling call was close,
    the reason belongs here rather than in a commit message nobody reads at the gate.
    """

    def segments(self, *, reference_dir: Path = DEFAULT_REFERENCE_DIR) -> list[TranscriptSegment]:
        """Re-derive the segments this case was labelled from.

        Args:
            reference_dir: Directory holding the reference pair.

        Returns:
            The case's segments, in order. ``seg-0001`` is the first of these.

        Raises:
            ConfigError: The bounds fall outside the meeting, which means the reference
                changed under a label that was written against the old one.
            OSError: The reference could not be read.
        """
        whole = load_reference_segments(self.meeting_id, reference_dir=reference_dir)
        if self.last_turn >= len(whole):
            raise ConfigError(
                f"{self.case_id}: bounds end at turn {self.last_turn} but "
                f"{self.meeting_id} now has {len(whole)} turns — the reference changed "
                "under this label and the citations can no longer be trusted"
            )
        return whole[self.first_turn : self.last_turn + 1]

    def validate_citations(self, *, reference_dir: Path = DEFAULT_REFERENCE_DIR) -> None:
        """Re-run the schema's evidence resolution against this case's segments.

        Writing a citation by hand is exactly as error-prone as a model inventing one,
        and the failure looks the same from the outside. Running the label back through
        the same validator the extractor is held to is what makes "schema-valid labels"
        a fact rather than an intention.

        Args:
            reference_dir: Directory holding the reference pair.

        Raises:
            ConfigError: A citation does not resolve. The message names the case.
            OSError: The reference could not be read.
        """
        from m2x.extraction import segment_ids

        bounds = segment_ids(self.segments(reference_dir=reference_dir))
        try:
            MeetingRecord.model_validate(
                self.label.model_dump(), context={SEGMENT_CONTEXT_KEY: bounds}
            )
        except ValueError as error:
            raise ConfigError(f"{self.case_id}: {error}") from error


def load_labelled_case(path: Path) -> LabelledCase:
    """Read one label file.

    Args:
        path: Label JSON file.

    Returns:
        The validated case.

    Raises:
        OSError: The file could not be read.
        pydantic.ValidationError: The file is not a valid labelled case.
    """
    return LabelledCase.model_validate_json(path.read_text(encoding="utf-8"))


def save_labelled_case(case: LabelledCase, directory: Path) -> Path:
    """Write one label file.

    Args:
        case: Case to write.
        directory: Destination, created if absent.

    Returns:
        The path written.

    Raises:
        OSError: The file could not be written.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{case.case_id}.json"
    path.write_text(
        json.dumps(case.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_label_set(directory: Path) -> list[LabelledCase]:
    """Read every label file in a directory, ordered by case id.

    Args:
        directory: ``eval/labels/dev`` or the unsealed held-out directory.

    Returns:
        The cases, sorted by ``case_id`` so a run is reproducible.

    Raises:
        OSError: A file could not be read.
        pydantic.ValidationError: A file is not a valid labelled case.
    """
    return sorted(
        (load_labelled_case(path) for path in directory.glob("*.json")),
        key=lambda case: case.case_id,
    )
