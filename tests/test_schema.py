"""Schema contract tests.

Every test here is about a *rejection*. The schema's value is not that it accepts good
records — anything accepts good records — but that a fabricated citation or a
half-parsed date fails loudly instead of entering the eval as data.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from m2x.schema import (
    SEGMENT_CONTEXT_KEY,
    ActionItem,
    Decision,
    Evidence,
    MeetingRecord,
)

SEGMENTS = {"seg-0001": (0.0, 10.0), "seg-0002": (10.0, 20.0)}
CONTEXT = {SEGMENT_CONTEXT_KEY: SEGMENTS}


def evidence_data(segment_id: str = "seg-0001", t_start: float = 1.0, t_end: float = 5.0) -> dict:
    """Build evidence payload as the model would emit it."""
    return {"segment_id": segment_id, "t_start": t_start, "t_end": t_end}


def test_evidence_resolves_against_a_real_segment() -> None:
    evidence = Evidence.model_validate(evidence_data(), context=CONTEXT)

    assert evidence.segment_id == "seg-0001"


def test_evidence_rejects_an_unknown_segment_id() -> None:
    with pytest.raises(ValidationError, match="does not exist in this transcript"):
        Evidence.model_validate(evidence_data(segment_id="seg-9999"), context=CONTEXT)


def test_evidence_derives_the_time_range_from_the_segment() -> None:
    """The citation-drift fix: the model names a segment, the code writes the times.

    Replaces a rejection test, and that is the point — the failure mode it guarded is now
    unreachable rather than caught. Nothing the model puts in these fields can be wrong,
    because nothing it puts there is kept.
    """
    evidence = Evidence.model_validate(evidence_data(segment_id="seg-0002"), context=CONTEXT)

    assert (evidence.t_start, evidence.t_end) == SEGMENTS["seg-0002"]


def test_a_supplied_range_is_discarded_rather_than_validated() -> None:
    """The measured drift: seg-0033 cited with the *previous* line's timestamps.

    Under the old contract that failed validation and burned the case's whole retry
    budget. Now the wrong numbers are simply replaced by the right ones.
    """
    evidence = Evidence.model_validate(
        evidence_data(segment_id="seg-0002", t_start=0.0, t_end=9.0), context=CONTEXT
    )

    assert (evidence.t_start, evidence.t_end) == SEGMENTS["seg-0002"]


def test_evidence_omitting_the_range_entirely_is_valid() -> None:
    """What the prompt now asks for: a segment id and nothing else."""
    evidence = Evidence.model_validate({"segment_id": "seg-0001"}, context=CONTEXT)

    assert (evidence.t_start, evidence.t_end) == SEGMENTS["seg-0001"]


def test_an_unknown_segment_id_is_still_the_check_that_catches_invention() -> None:
    """Deriving times removes one check; this is the one that mattered, and it stays.

    A model that invents a decision invents a segment id to go with it, and no amount of
    deriving timestamps would make that citation real.
    """
    with pytest.raises(ValidationError, match="does not exist in this transcript"):
        Evidence.model_validate({"segment_id": "seg-9999"}, context=CONTEXT)


def test_evidence_without_context_skips_resolution_but_keeps_structure() -> None:
    """Reading a record back off disk has no transcript in hand; structure still holds."""
    evidence = Evidence.model_validate(evidence_data(segment_id="seg-9999"))

    assert evidence.segment_id == "seg-9999"

    with pytest.raises(ValidationError, match="precedes t_start"):
        Evidence.model_validate(evidence_data(t_start=5.0, t_end=1.0))


def test_action_accepts_an_iso_deadline_and_null() -> None:
    dated = ActionItem.model_validate(
        {"description": "ship it", "deadline": "2026-08-12", "evidence": evidence_data()},
        context=CONTEXT,
    )
    undated = ActionItem.model_validate(
        {"description": "ship it", "evidence": evidence_data()}, context=CONTEXT
    )

    assert dated.deadline == "2026-08-12"
    assert undated.deadline is None
    assert undated.owner is None


def test_action_rejects_a_relative_deadline() -> None:
    """'Next friday' is the canonical half-parsed date: plausible, unusable."""
    with pytest.raises(ValidationError, match="is not an ISO-8601 date"):
        ActionItem.model_validate(
            {"description": "ship it", "deadline": "next friday", "evidence": evidence_data()},
            context=CONTEXT,
        )


def test_items_reject_an_empty_description() -> None:
    with pytest.raises(ValidationError):
        Decision.model_validate({"description": "", "evidence": evidence_data()}, context=CONTEXT)


def test_items_reject_invented_fields() -> None:
    """A model that adds `confidence` is retried, not silently trusted."""
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Decision.model_validate(
            {"description": "ship it", "confidence": 0.9, "evidence": evidence_data()},
            context=CONTEXT,
        )


def test_empty_record_is_valid() -> None:
    """A meeting with no risks must be expressible, or the model will invent one."""
    record = MeetingRecord.model_validate({}, context=CONTEXT)

    assert record.item_count == 0


def test_record_propagates_evidence_context_into_nested_items() -> None:
    """The context has to reach items nested two levels down, or the guard is decorative."""
    payload = {
        "actions": [{"description": "ship it", "evidence": evidence_data(segment_id="seg-9999")}]
    }

    with pytest.raises(ValidationError, match="does not exist in this transcript"):
        MeetingRecord.model_validate(payload, context=CONTEXT)


def test_record_counts_items_across_kinds() -> None:
    payload = {
        "decisions": [{"description": "adopt whisper-large-v3", "evidence": evidence_data()}],
        "actions": [
            {
                "description": "write the snippets",
                "owner": "Yash",
                "deadline": "2026-08-12",
                "evidence": evidence_data(segment_id="seg-0002", t_start=11.0, t_end=12.0),
            }
        ],
        "risks": [{"description": "hinglish has no WER route", "evidence": evidence_data()}],
        "open_questions": [{"description": "who maps the speakers?", "evidence": evidence_data()}],
    }

    record = MeetingRecord.model_validate(payload, context=CONTEXT)

    assert record.item_count == 4
    assert record.actions[0].owner == "Yash"
