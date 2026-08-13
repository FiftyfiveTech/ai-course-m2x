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
* **Evidence is machine-checked, and half of it is machine-*written*.** Every item cites
  a ``segment_id``, and :class:`Evidence` resolves that reference against the transcript
  actually passed in. A citation to nowhere is a validation error, not a shrug — an
  unverified citation is decoration, and a model that invents a decision will invent a
  segment id to go with it. The time range is then derived from the named segment rather
  than asked for; see :class:`Evidence` for the drift that made that necessary.
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

DERIVED_TIME_NOTE = (
    "Do not emit this field. It is derived from segment_id, and any value supplied is "
    "discarded."
)
"""What the model is told about the two timestamp fields.

Duplicated into ``Field(description=...)`` because that is the only channel Instructor
renders into the request; an attribute docstring reaches readers of this file and nobody
else.
"""


class Evidence(BaseModel):
    """Where in the transcript an extracted item was read from.

    **The model names a segment; the timestamps are computed here.** Until M2X-041 the
    model was asked to echo the ``t_start``/``t_end`` it saw on the rendered line, and the
    validator checked the echo fell inside the segment's real bounds. It systematically
    did not: the extractor paired a segment id with the *previous* line's timestamps —
    ``seg-0033`` cited as ``580.3-581.4`` when it runs ``581.44-586.445`` — which failed
    validation, consumed the whole retry budget on that case, and made citation drift the
    single largest contributor to schema-validity failures. An explicit prompt rewrite
    (v3) did not move it, which pointed at the model rather than at the wording.

    So the field the model kept getting wrong is no longer a field the model fills.
    ``segment_id`` resolves against the transcript exactly as before — that is the check
    which catches invention — and the time range is then read off the segment. The
    principle is M2X-044's, already load-bearing for RAG citations: *a timestamp the model
    cannot type is one it cannot invent.*

    What this gives up, deliberately: an item can no longer cite a sub-span of a segment.
    Segments are single speaker turns, so the sub-span was never used and the labels never
    wrote one — all 84 cited items in the dev set name a whole turn.
    """

    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(
        min_length=1,
        description=(
            "Id of the segment this item was read from, exactly as shown in the "
            "transcript block (e.g. seg-0007). Must be one of the ids in that block."
        ),
    )
    """Id of the citing segment, as rendered into the prompt (e.g. ``seg-0007``)."""

    t_start: float | None = Field(default=None, ge=0.0, description=DERIVED_TIME_NOTE)
    """Start of the cited segment, in seconds. Derived, not supplied.

    ``None`` only on a record read back from disk that was written before the value could
    be resolved — with a transcript in hand this is always populated.
    """

    t_end: float | None = Field(default=None, ge=0.0, description=DERIVED_TIME_NOTE)
    """End of the cited segment, in seconds. Derived, not supplied."""

    @model_validator(mode="after")
    def _resolve_against_transcript(self, info: ValidationInfo) -> Evidence:
        """Resolve the segment id, then read its time range off the transcript.

        Args:
            info: Validation context. ``info.context[SEGMENT_CONTEXT_KEY]`` maps segment
                id to its ``(t_start, t_end)`` bounds.

        Returns:
            The validated evidence, with timestamps filled in from the named segment.

        Raises:
            ValueError: The segment id is unknown, or — with no transcript in hand — a
                stored range is inverted.
        """
        segments = (info.context or {}).get(SEGMENT_CONTEXT_KEY)
        if segments is None:
            # No context supplied — a record read back from disk for inspection, rather
            # than one being extracted. Nothing can be resolved or derived because the
            # transcript is not in hand, so only the structural check survives. Callers
            # that need the guarantee pass the context; see extraction.extract_record.
            if self.t_start is not None and self.t_end is not None and self.t_end < self.t_start:
                raise ValueError(f"t_end {self.t_end} precedes t_start {self.t_start}")
            return self

        bounds = segments.get(self.segment_id)
        if bounds is None:
            raise ValueError(
                f"segment_id {self.segment_id!r} does not exist in this transcript; "
                "cite one of the segment ids shown in the transcript block"
            )

        # Overwritten rather than checked. A supplied range is not evidence of anything —
        # it is a copy the model made of a number already on the line it cited, and the
        # copy is what kept going wrong.
        self.t_start, self.t_end = bounds
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
