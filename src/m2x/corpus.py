"""The corpus manifest: what audio the project expects to have.

``data/`` is git-ignored, so a fresh clone has the code but none of the meetings. The
manifest is the exception that is tracked, because "which meetings does this project
run on, and which of them carries the screen-share" is a fact about the project, not a
copy of the data. It also makes a missing file diagnosable — code can say *mtg-001 is
declared but absent* instead of failing on an open() three layers down.

Validated with Pydantic like everything else here: a typo'd meeting id in a hand-edited
JSON file would otherwise surface on Wednesday as a mysteriously empty comparison.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_MANIFEST = Path("data/corpus.json")
"""Tracked manifest location — see the ``!data/corpus.json`` rule in ``.gitignore``."""

Origin = Literal["internal", "ami"]
"""Where a meeting came from. ``internal`` is our own recordings; ``ami`` is the
English control set. Day 2 comparisons report per origin, since a result that only
holds on clean English is not a result about our meetings."""


class Meeting(BaseModel):
    """One admitted meeting."""

    model_config = ConfigDict(frozen=True)

    id: str
    file: Path
    origin: Origin
    date: str | None = None
    duration_s: float = Field(ge=0)
    participants: int = Field(ge=1)
    language: str
    screen_share: bool
    consent_confirmed: bool
    reference: Path | None = None
    """Ground-truth speaker turns, when the source ships them (AMI does)."""

    snippet: Path | None = None
    """Hand-written reference transcript for a 2-minute window, when one exists."""

    speakers: dict[str, str] = Field(default_factory=dict)
    """``{diarisation label: real name}``, written after listening to the first 30
    seconds once (M2X-022). Kept in the manifest rather than beside the transcript
    because the mapping is a property of the *meeting*, and re-running diarisation
    would otherwise discard it — labels are stable within a run, never across runs, so
    this needs re-checking whenever the diarisation model or its version changes."""

    notes: str = ""

    def exists(self) -> bool:
        """Whether the audio is actually on this machine.

        Returns:
            True when the declared file is present. False on a fresh clone, which is
            expected rather than broken — the audio is fetched or copied separately.
        """
        return self.file.is_file()


class Clip(BaseModel):
    """A shortened cut of a meeting, used to keep iteration cheap."""

    model_config = ConfigDict(frozen=True)

    file: Path
    source: str
    duration_s: float = Field(ge=0)
    purpose: str = ""


class Corpus(BaseModel):
    """The manifest as a whole."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = Field(alias="schema")
    generated: str
    meetings: list[Meeting]
    clips: list[Clip] = []

    def by_id(self, meeting_id: str) -> Meeting:
        """Look up one meeting.

        Args:
            meeting_id: Stable id, e.g. ``"mtg-001"``.

        Returns:
            The matching meeting.

        Raises:
            KeyError: No meeting carries that id. The message lists the ids that do
                exist, because the usual cause is a typo, not a missing meeting.
        """
        for meeting in self.meetings:
            if meeting.id == meeting_id:
                return meeting
        known = ", ".join(meeting.id for meeting in self.meetings)
        raise KeyError(f"no meeting {meeting_id!r} in the manifest — have: {known}")

    def with_origin(self, origin: Origin) -> list[Meeting]:
        """Return every meeting from one source, in manifest order."""
        return [meeting for meeting in self.meetings if meeting.origin == origin]

    def missing(self) -> list[Meeting]:
        """Return declared meetings whose audio is not on this machine."""
        return [meeting for meeting in self.meetings if not meeting.exists()]


def load_corpus(path: Path = DEFAULT_MANIFEST) -> Corpus:
    """Read and validate the manifest.

    Args:
        path: Manifest to load.

    Returns:
        The validated corpus.

    Raises:
        FileNotFoundError: The manifest is absent, which means the clone is incomplete
            rather than merely missing audio.
        ValueError: The manifest is present but malformed.
    """
    if not path.is_file():
        raise FileNotFoundError(f"corpus manifest not found: {path}")

    try:
        return Corpus.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        raise ValueError(f"{path} is not a valid corpus manifest: {exc}") from exc
