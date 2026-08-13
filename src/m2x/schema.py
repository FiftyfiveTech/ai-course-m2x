"""The extraction contract: what a meeting record is, enforced.

This schema is a contract between three parties that never talk to each other
directly — the extractor targets it, the hand labels follow it, and the field-level F1
harness compares within it. A field renamed here after labelling has begun does not
cause a merge conflict; it silently scores every item of that type as a miss. Treat the
shapes below as frozen for the phase (see ``docs/design/day3-schema.md``).

Two design decisions carry more weight than they look:

* **Nullability is an honesty incentive.** An unknown ``owner`` is ``None``, never a
  plausible guess, because under field-level F1 a guessed owner costs precision while
  ``None`` simply is not matched. A model that admits ignorance has to be able to score
  better than one that confabulates.
* **Evidence is machine-checked.** Every item cites a ``segment_id`` and the time range
  it was read from, and :class:`Evidence` resolves that reference against the transcript
  actually passed in. A citation to nowhere is a validation error, not a shrug — an
  unverified citation is decoration, and a model that invents a decision will invent a
  segment id to go with it.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

SEGMENT_CONTEXT_KEY = "segments"
"""Validation-context key holding ``{segment_id: (t_start, t_end)}``.

Passed through Instructor as ``context=`` so evidence resolution happens *inside* the
retry loop: a fabricated segment id becomes an error the model is asked to fix, exactly
like a malformed date, rather than a post-hoc filter that drops the item silently.
"""

TIME_TOLERANCE_S = 0.5
"""Slack allowed when checking a cited range against its segment's bounds.

Segment timestamps come back from the provider rounded, and a model asked to echo them
rounds again. Without a tolerance the validator would reject citations that are correct
to within a rounding step, and the retry loop would burn attempts fixing arithmetic
noise instead of real fabrication.
"""


class Evidence(BaseModel):
    """Where in the transcript an extracted item was read from.

    Validation is deliberately two-sided: the segment must exist, *and* the cited range
    must fall inside that segment. Checking only existence would let a model cite a real
    segment for a claim made nowhere near it.
    """

    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(min_length=1)
    """Id of the citing segment, as rendered into the prompt (e.g. ``seg-0007``)."""

    t_start: float = Field(ge=0.0)
    """Start of the cited span, in seconds from the beginning of the audio."""

    t_end: float = Field(ge=0.0)
    """End of the cited span, in seconds from the beginning of the audio."""

    @model_validator(mode="after")
    def _resolve_against_transcript(self, info: ValidationInfo) -> Evidence:
        """Check the citation points at a real place in the transcript.

        Args:
            info: Validation context. ``info.context[SEGMENT_CONTEXT_KEY]`` maps segment
                id to its ``(t_start, t_end)`` bounds.

        Returns:
            The validated evidence.

        Raises:
            ValueError: The range is inverted, the segment id is unknown, or the cited
                range lies outside the segment's bounds.
        """
        if self.t_end < self.t_start:
            raise ValueError(f"t_end {self.t_end} precedes t_start {self.t_start}")

        segments = (info.context or {}).get(SEGMENT_CONTEXT_KEY)
        if segments is None:
            # No context supplied — a record read back from disk for inspection, rather
            # than one being extracted. Structural checks still apply; resolution cannot,
            # because the transcript is not in hand. Callers that need the guarantee pass
            # the context; see extraction.extract_record.
            return self

        bounds = segments.get(self.segment_id)
        if bounds is None:
            raise ValueError(
                f"segment_id {self.segment_id!r} does not exist in this transcript; "
                "cite one of the segment ids shown in the transcript block"
            )

        start, end = bounds
        if self.t_start < start - TIME_TOLERANCE_S or self.t_end > end + TIME_TOLERANCE_S:
            raise ValueError(
                f"cited range {self.t_start}-{self.t_end} falls outside segment "
                f"{self.segment_id} ({start}-{end})"
            )
        return self


class _Item(BaseModel):
    """Fields every extracted item shares.

    Not exported as a schema type in its own right: the harness matches per item kind,
    so the kinds stay separate classes even where their fields currently coincide.
    """

    model_config = ConfigDict(extra="forbid")

    description: str = Field(
        min_length=1,
        description=(
            "One self-contained third-person sentence stating the resolved fact, not a "
            "quote of what was said. Resolve pronouns and 'this'/'that' against the "
            "surrounding turns, and name both the subject and the object so the sentence "
            "stands alone away from the transcript. Roughly 12-20 words."
        ),
    )
    """One self-contained sentence, in the meeting's own terms.

    The guidance is duplicated into ``Field(description=...)`` on purpose. A bare
    attribute docstring is invisible to the model: Pydantic only folds it into the JSON
    schema under ``use_attribute_docstrings``, which is not set, so what Instructor
    injected was ``{"minLength": 1, "title": "Description", "type": "string"}`` — the
    convention existed for readers of this file and for nobody else. Class docstrings do
    reach the model, which is why the kinds were described and the fields were not.
    """

    evidence: Evidence
    """The transcript span this item was read from."""


class Decision(_Item):
    """Something the meeting settled."""


class Risk(_Item):
    """A stated threat to the plan."""


class OpenQuestion(_Item):
    """Something raised and left unresolved."""


class ActionItem(_Item):
    """Work someone committed to."""

    owner: str | None = Field(
        default=None,
        description=(
            "Who accepted the work, named as the meeting names them. null when nobody was "
            "named. Never the speaker of the cited segment merely because they spoke it, "
            "and never a pronoun."
        ),
    )
    """Who owns it, exactly as named in the meeting. ``None`` when nobody was named.

    Never inferred from who happened to be speaking: "we should do X" said by Yash is
    not an action owned by Yash.
    """

    deadline: str | None = None
    """Due date as an ISO-8601 calendar date (``YYYY-MM-DD``), or ``None``.

    "Next Friday" is not a date. Either it resolves to a calendar date, or the field is
    ``None`` — a half-parsed deadline is worse than an absent one, because it looks
    actionable.
    """

    @model_validator(mode="after")
    def _deadline_is_iso_date(self, _info: ValidationInfo) -> ActionItem:
        """Reject anything that is not a plain ISO-8601 date.

        Returns:
            The validated action item.

        Raises:
            ValueError: ``deadline`` is set but does not parse as ``YYYY-MM-DD``.
        """
        if self.deadline is None:
            return self
        try:
            date.fromisoformat(self.deadline)
        except ValueError:
            raise ValueError(
                f"deadline {self.deadline!r} is not an ISO-8601 date; use YYYY-MM-DD, "
                "or null when the meeting named no resolvable date"
            ) from None
        return self


class MeetingRecord(BaseModel):
    """The structured record extracted from one meeting.

    The four lists are the units the eval scores. Every list defaults to empty because
    "this meeting contained no risks" is a legitimate — and, when true, correct —
    answer; forcing the model to populate every category is how you buy hallucinations.
    """

    model_config = ConfigDict(extra="forbid")

    decisions: list[Decision] = Field(default_factory=list)
    actions: list[ActionItem] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)

    @property
    def item_count(self) -> int:
        """Total number of extracted items across all four kinds."""
        return len(self.decisions) + len(self.actions) + len(self.risks) + len(self.open_questions)
