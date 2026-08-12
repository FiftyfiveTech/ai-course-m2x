"""Tests for the field-level F1 harness.

The ticket's acceptance criterion is that a hand-computed F1 on synthetic pairs matches
the harness exactly, so the three headline cases below are built to be checkable by eye:
they come out at 1.0, 0.0 and 0.5, and the arithmetic is written out in each docstring.

The rest of the file is about the properties that make a gate number trustworthy rather
than merely computed — determinism of the pairing, null treated as an answer, and
over-extraction actually costing precision.
"""

from __future__ import annotations

import pytest

from m2x.eval_extraction import (
    DESCRIPTION_MATCH_THRESHOLD,
    Counts,
    aggregate,
    canonical_owner,
    format_report,
    match_items,
    score_case,
    token_set_f1,
)
from m2x.schema import ActionItem, Decision, Evidence, MeetingRecord, OpenQuestion, Risk


def _ev(segment: str = "seg-0001") -> Evidence:
    """Evidence stub; citations are not scored, so the values only need to validate."""
    return Evidence(segment_id=segment, t_start=0.0, t_end=1.0)


def _action(description: str, owner: str | None = None, deadline: str | None = None) -> ActionItem:
    """Build an action item."""
    return ActionItem(
        description=description, owner=owner, deadline=deadline, evidence=_ev()
    )


# --------------------------------------------------------------------------------------
# The three hand-computed pairs the ticket asks for
# --------------------------------------------------------------------------------------


def test_hand_computed_perfect_match_is_1_0() -> None:
    """Everything found, nothing invented, owner right.

    items: TP=2 FP=0 FN=0 -> P=1.0 R=1.0
    owner: TP=1 FP=0 FN=0
    micro over items+owner: TP=3 FP=0 FN=0 -> F1 = 1.0
    """
    labelled = MeetingRecord(
        decisions=[Decision(description="ship the API by Friday", evidence=_ev())],
        actions=[_action("deploy the service", owner="Beth")],
    )

    score = score_case("c01", labelled, labelled)
    report = aggregate("dev", [score])

    assert report.items == Counts(true_positive=2)
    assert report.owner == Counts(true_positive=1)
    assert report.micro_f1 == pytest.approx(1.0)


def test_hand_computed_miss_plus_hallucination_is_0_0() -> None:
    """One labelled item missed, two extracted items unsupported.

    "adopt postgres" vs "adopt mysql": token sets {adopt,postgres} and {adopt,mysql},
    overlap 1 -> P=0.5 R=0.5 F1=0.5, below the 0.60 threshold, so no pair.
    decisions: TP=0 FP=1 FN=1
    actions:   TP=0 FP=1 FN=0
    micro: TP=0 FP=2 FN=1 -> P=0.0 R=0.0 -> F1 = 0.0
    """
    labelled = MeetingRecord(
        decisions=[Decision(description="adopt postgres", evidence=_ev())]
    )
    extracted = MeetingRecord(
        decisions=[Decision(description="adopt mysql", evidence=_ev())],
        actions=[_action("write the migration")],
    )

    report = aggregate("dev", [score_case("c01", labelled, extracted)])

    assert report.items == Counts(true_positive=0, false_positive=2, false_negative=1)
    assert report.micro_f1 == pytest.approx(0.0)


def test_hand_computed_right_item_wrong_owner_is_0_5() -> None:
    """The item is found but attributed to the wrong person.

    items: TP=1 FP=0 FN=0
    owner: named the wrong person -> FP=1 and FN=1, not half a hit
    micro: TP=1 FP=1 FN=1 -> P=0.5 R=0.5 -> F1 = 0.5
    """
    labelled = MeetingRecord(actions=[_action("update the deployment script", owner="Beth")])
    extracted = MeetingRecord(actions=[_action("update the deployment script", owner="Tom")])

    report = aggregate("dev", [score_case("c01", labelled, extracted)])

    assert report.items == Counts(true_positive=1)
    assert report.owner == Counts(false_positive=1, false_negative=1)
    assert report.micro_f1 == pytest.approx(0.5)


# --------------------------------------------------------------------------------------
# Nullability: the honesty incentive
# --------------------------------------------------------------------------------------


def test_correctly_declining_to_name_an_owner_is_a_hit() -> None:
    """None == None scores as a true positive, or admitting ignorance would cost."""
    labelled = MeetingRecord(actions=[_action("book the venue", owner=None)])

    score = score_case("c01", labelled, labelled)

    assert score.owner == Counts(true_positive=1)


def test_guessing_an_owner_the_meeting_never_named_costs_precision() -> None:
    """A model that invents a plausible owner must score worse than one that abstains."""
    labelled = MeetingRecord(actions=[_action("book the venue", owner=None)])
    extracted = MeetingRecord(actions=[_action("book the venue", owner="Beth")])

    score = score_case("c01", labelled, extracted)

    assert score.owner == Counts(false_positive=1)


def test_missing_a_named_owner_costs_recall() -> None:
    """Dropping an owner the meeting did name is a miss, not an abstention."""
    labelled = MeetingRecord(actions=[_action("book the venue", owner="Beth")])
    extracted = MeetingRecord(actions=[_action("book the venue", owner=None)])

    score = score_case("c01", labelled, extracted)

    assert score.owner == Counts(false_negative=1)


def test_owner_comparison_ignores_case_and_titles() -> None:
    """`Dr. Beth` and `beth` are the same person; the eval should not say otherwise."""
    assert canonical_owner("Dr. Beth") == canonical_owner("beth")
    assert canonical_owner(None) is None
    assert canonical_owner("  ") is None


# --------------------------------------------------------------------------------------
# Deadline: reported, never scored (eval/README.md section 5)
# --------------------------------------------------------------------------------------


def test_deadline_is_excluded_from_micro_f1() -> None:
    """An emitted deadline must not move the headline number.

    The labels contain no deadlines at all, so the field has no positive examples and
    scoring it would average in something that cannot be earned.
    """
    labelled = MeetingRecord(actions=[_action("ship it", owner="Beth", deadline=None)])
    with_deadline = MeetingRecord(
        actions=[_action("ship it", owner="Beth", deadline="2026-08-14")]
    )

    clean = aggregate("dev", [score_case("c01", labelled, labelled)])
    dirty = aggregate("dev", [score_case("c01", labelled, with_deadline)])

    assert clean.micro_f1 == dirty.micro_f1 == pytest.approx(1.0)


def test_deadline_abstention_is_reported_separately() -> None:
    """It is visible as its own rate, so the behaviour is not simply invisible."""
    labelled = MeetingRecord(
        actions=[_action("ship it", owner="Beth"), _action("book the venue", owner="Tom")]
    )
    extracted = MeetingRecord(
        actions=[
            _action("ship it", owner="Beth", deadline="2026-08-14"),
            _action("book the venue", owner="Tom"),
        ]
    )

    report = aggregate("dev", [score_case("c01", labelled, extracted)])

    assert report.matched_actions == 2
    assert report.deadline_abstention == pytest.approx(0.5)


# --------------------------------------------------------------------------------------
# Matching behaviour
# --------------------------------------------------------------------------------------


def test_kinds_never_match_across_lists() -> None:
    """A risk filed as a decision is wrong twice, which is the intended accounting."""
    labelled = MeetingRecord(risks=[Risk(description="the vendor may slip", evidence=_ev())])
    extracted = MeetingRecord(
        decisions=[Decision(description="the vendor may slip", evidence=_ev())]
    )

    report = aggregate("dev", [score_case("c01", labelled, extracted)])

    assert report.per_kind["risks"] == Counts(false_negative=1)
    assert report.per_kind["decisions"] == Counts(false_positive=1)
    assert report.micro_f1 == pytest.approx(0.0)


def test_duplicate_descriptions_pair_one_to_one() -> None:
    """Two labelled items and one extracted leaves exactly one false negative.

    An extractor cannot satisfy two labels with a single item, which is what stops
    dedup failures from being scored as successes.
    """
    labelled = MeetingRecord(
        decisions=[
            Decision(description="adopt the new schema", evidence=_ev()),
            Decision(description="adopt the new schema", evidence=_ev("seg-0002")),
        ]
    )
    extracted = MeetingRecord(
        decisions=[Decision(description="adopt the new schema", evidence=_ev())]
    )

    report = aggregate("dev", [score_case("c01", labelled, extracted)])

    assert report.per_kind["decisions"] == Counts(true_positive=1, false_negative=1)


def test_pairing_is_deterministic_across_runs() -> None:
    """Same records twice must pair identically, or a gate cannot be re-derived."""
    labelled = [
        Decision(description="adopt the new schema for storage", evidence=_ev()),
        Decision(description="adopt the new schema for transport", evidence=_ev()),
    ]
    extracted = [
        Decision(description="adopt the new schema", evidence=_ev()),
        Decision(description="adopt the new schema for transport", evidence=_ev()),
    ]

    first = match_items(labelled, extracted)
    second = match_items(labelled, extracted)

    assert first == second


def test_the_better_pair_wins_when_two_compete() -> None:
    """Greedy on descending similarity: the exact match takes its partner first."""
    labelled = [
        Decision(description="adopt the new schema for transport", evidence=_ev()),
    ]
    extracted = [
        Decision(description="adopt the new schema", evidence=_ev()),
        Decision(description="adopt the new schema for transport", evidence=_ev()),
    ]

    pairs, unmatched_labels, unmatched_extracts = match_items(labelled, extracted)

    assert pairs == [(0, 1)]
    assert unmatched_labels == []
    assert unmatched_extracts == [0]


def test_similarity_below_the_threshold_is_not_a_match() -> None:
    """The threshold is load-bearing, so its edge is tested rather than assumed."""
    assert token_set_f1("adopt postgres", "adopt mysql") == pytest.approx(0.5)
    assert 0.5 < DESCRIPTION_MATCH_THRESHOLD

    pairs, _, _ = match_items(
        [Decision(description="adopt postgres", evidence=_ev())],
        [Decision(description="adopt mysql", evidence=_ev())],
    )

    assert pairs == []


def test_two_empty_descriptions_do_not_match_each_other() -> None:
    """Normalising to nothing is not evidence of agreement."""
    assert token_set_f1("the", "and") == 0.0


# --------------------------------------------------------------------------------------
# Aggregation edge cases
# --------------------------------------------------------------------------------------


def test_a_correctly_empty_case_scores_perfectly() -> None:
    """`Nothing was decided here` has to be scoreable, since two cases are exactly that."""
    empty = MeetingRecord()

    report = aggregate("dev", [score_case("c01", empty, empty)])

    assert report.items.total == 0
    assert report.micro_f1 == pytest.approx(1.0)


def test_inventing_items_in_an_empty_case_costs_precision() -> None:
    """The whole reason the empty cases are in the set."""
    extracted = MeetingRecord(
        decisions=[Decision(description="the robot nurse will be adopted", evidence=_ev())]
    )

    report = aggregate("dev", [score_case("c01", MeetingRecord(), extracted)])

    assert report.items == Counts(false_positive=1)
    assert report.micro_f1 == pytest.approx(0.0)


def test_failed_cases_lower_schema_validity_without_touching_f1() -> None:
    """A case that produced no record is a gate failure, tracked on its own axis."""
    labelled = MeetingRecord(decisions=[Decision(description="ship it", evidence=_ev())])

    report = aggregate("dev", [score_case("c01", labelled, labelled)], failed=1)

    assert report.schema_validity == pytest.approx(0.5)
    assert report.micro_f1 == pytest.approx(1.0)


def test_totals_sum_across_cases() -> None:
    """Micro, not macro: a twenty-item case outweighs a one-item case, deliberately."""
    big = MeetingRecord(
        decisions=[
            Decision(description=f"decision number {index}", evidence=_ev())
            for index in range(4)
        ]
    )
    small = MeetingRecord(
        open_questions=[OpenQuestion(description="who owns the rollout", evidence=_ev())]
    )

    report = aggregate(
        "dev", [score_case("c01", big, big), score_case("c02", small, MeetingRecord())]
    )

    assert report.per_kind["decisions"] == Counts(true_positive=4)
    assert report.per_kind["open_questions"] == Counts(false_negative=1)
    assert report.micro_f1 == pytest.approx(2 * 4 / (2 * 4 + 0 + 1))


def test_report_renders_the_headline_and_the_caveat() -> None:
    """The deadline caveat travels with the number, not only in the docs."""
    labelled = MeetingRecord(actions=[_action("ship it", owner="Beth")])

    text = format_report(aggregate("dev", [score_case("c01", labelled, labelled)]))

    assert "MICRO-F1" in text
    assert "deadline abstention" in text
    assert "eval/README.md section 5" in text
