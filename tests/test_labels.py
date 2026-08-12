"""Tests for the hand-written ground-truth format.

The load-bearing behaviour is that a label cannot quietly outlive the transcript it was
written against. Bounds are stored rather than text, so if the reference changes the
label must fail loudly instead of scoring against different words than the labeller saw.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from m2x.errors import ConfigError
from m2x.labels import (
    LabelledCase,
    load_label_set,
    load_labelled_case,
    save_labelled_case,
)
from m2x.schema import ActionItem, Decision, Evidence, MeetingRecord


def _write_reference(directory: Path, meeting_id: str, count: int) -> None:
    """Write a reference pair of `count` one-second turns.

    Args:
        directory: Destination directory.
        meeting_id: Reference meeting id.
        count: How many turns.
    """
    directory.mkdir(parents=True, exist_ok=True)
    turns = [
        {"t_start": float(i), "t_end": float(i) + 0.5, "speaker": f"spk-{i % 2}"}
        for i in range(count)
    ]
    (directory / f"{meeting_id}.speakers.json").write_text(
        json.dumps({"meeting_id": meeting_id, "segments": turns}), encoding="utf-8"
    )
    (directory / f"{meeting_id}.txt").write_text(
        "\n".join(f"line {i}" for i in range(count)) + "\n", encoding="utf-8"
    )


def _case(case_id: str = "ref-001-c01", **overrides: object) -> LabelledCase:
    """Build a small labelled case citing its own first segment.

    Args:
        case_id: Case id.
        overrides: Fields to replace.

    Returns:
        The case.
    """
    defaults: dict = {
        "case_id": case_id,
        "meeting_id": "ref-001",
        "first_turn": 2,
        "last_turn": 5,
        "label": MeetingRecord(
            decisions=[
                Decision(
                    description="ship it",
                    evidence=Evidence(segment_id="seg-0001", t_start=2.0, t_end=2.5),
                )
            ]
        ),
    }
    defaults.update(overrides)
    return LabelledCase(**defaults)


def test_round_trip_preserves_the_label(tmp_path: Path) -> None:
    """A saved case reads back equal to what was written."""
    written = save_labelled_case(_case(), tmp_path)

    assert load_labelled_case(written) == _case()


def test_segments_are_re_derived_from_the_reference(tmp_path: Path) -> None:
    """The label stores bounds; the words come from the one committed copy."""
    _write_reference(tmp_path, "ref-001", 10)

    segments = _case().segments(reference_dir=tmp_path)

    assert [segment.text for segment in segments] == ["line 2", "line 3", "line 4", "line 5"]


def test_citations_validate_against_the_case_not_the_meeting(tmp_path: Path) -> None:
    """``seg-0001`` means the case's first turn, so the citation resolves."""
    _write_reference(tmp_path, "ref-001", 10)

    _case().validate_citations(reference_dir=tmp_path)


def test_a_citation_outside_the_case_is_rejected(tmp_path: Path) -> None:
    """A hand-written citation is as fallible as a model's and is checked the same way."""
    _write_reference(tmp_path, "ref-001", 10)
    bad = _case(
        label=MeetingRecord(
            actions=[
                ActionItem(
                    description="do the thing",
                    owner=None,
                    deadline=None,
                    evidence=Evidence(segment_id="seg-0099", t_start=2.0, t_end=2.5),
                )
            ]
        )
    )

    with pytest.raises(ConfigError, match="seg-0099"):
        bad.validate_citations(reference_dir=tmp_path)


def test_a_citation_with_the_wrong_time_range_is_rejected(tmp_path: Path) -> None:
    """Citing a real segment for words spoken elsewhere in it still fails."""
    _write_reference(tmp_path, "ref-001", 10)
    bad = _case(
        label=MeetingRecord(
            decisions=[
                Decision(
                    description="ship it",
                    evidence=Evidence(segment_id="seg-0001", t_start=90.0, t_end=95.0),
                )
            ]
        )
    )

    with pytest.raises(ConfigError, match="falls outside"):
        bad.validate_citations(reference_dir=tmp_path)


def test_bounds_past_the_end_of_a_changed_reference_are_rejected(tmp_path: Path) -> None:
    """If the reference shrank, the label is stale and must not score silently."""
    _write_reference(tmp_path, "ref-001", 4)

    with pytest.raises(ConfigError, match="the reference changed"):
        _case().segments(reference_dir=tmp_path)


def test_load_label_set_is_ordered_by_case_id(tmp_path: Path) -> None:
    """A run over the set is reproducible, so two runs pair items the same way."""
    for case_id in ("ref-001-c03", "ref-001-c01", "ref-001-c02"):
        save_labelled_case(_case(case_id), tmp_path)

    assert [case.case_id for case in load_label_set(tmp_path)] == [
        "ref-001-c01",
        "ref-001-c02",
        "ref-001-c03",
    ]


def test_an_empty_record_is_a_valid_label(tmp_path: Path) -> None:
    """"Nothing was decided here" has to be expressible, or every case invents items."""
    empty = _case(label=MeetingRecord())
    _write_reference(tmp_path, "ref-001", 10)

    written = save_labelled_case(empty, tmp_path)
    empty.validate_citations(reference_dir=tmp_path)

    assert load_labelled_case(written).label.item_count == 0
