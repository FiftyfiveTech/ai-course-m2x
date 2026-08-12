"""Tests for the dev/held-out split.

The split is a fairness claim, not a utility. Its whole value is that a reviewer can
re-derive it and get the same answer -- so what is under test is reproducibility and the
refusal to run on the wrong number of cases, not that shuffling works.
"""

from __future__ import annotations

import pytest
from split_labels import DEV_SIZE, HELDOUT_SIZE, SPLIT_SEED, split


def _case_ids(count: int = DEV_SIZE + HELDOUT_SIZE) -> list[str]:
    """Build `count` plausible case ids.

    Args:
        count: How many ids.

    Returns:
        The ids.
    """
    return [f"tiron-MTG_{index:05d}-c01" for index in range(count)]


def test_split_sizes_match_the_ticket() -> None:
    """15 dev and 10 held-out, which are the ticket's numbers rather than a preference."""
    dev, heldout = split(_case_ids())

    assert len(dev) == 15
    assert len(heldout) == 10


def test_split_is_reproducible_from_the_recorded_seed() -> None:
    """Re-running gives the same two sets, which is what makes the split checkable."""
    first_dev, first_heldout = split(_case_ids())
    second_dev, second_heldout = split(_case_ids())

    assert first_dev == second_dev
    assert first_heldout == second_heldout


def test_split_does_not_depend_on_input_order() -> None:
    """The same cases in a different order still split identically.

    Without this, re-running after a filesystem returns paths in another order would
    silently reshuffle which cases are sealed.
    """
    ids = _case_ids()

    from_sorted = split(sorted(ids))
    from_reversed = split(list(reversed(ids)))

    assert from_sorted == from_reversed


def test_a_different_seed_gives_a_different_split() -> None:
    """Proves the seed is actually driving the shuffle rather than being decorative."""
    dev, _ = split(_case_ids())
    other_dev, _ = split(_case_ids(), seed=SPLIT_SEED + 1)

    assert dev != other_dev


def test_every_case_lands_in_exactly_one_set() -> None:
    """No case is both iterated against and used to certify."""
    ids = _case_ids()

    dev, heldout = split(ids)

    assert set(dev) | set(heldout) == set(ids)
    assert not set(dev) & set(heldout)


def test_the_wrong_number_of_cases_is_refused() -> None:
    """A short set would silently produce a smaller held-out set and a weaker gate."""
    with pytest.raises(ValueError, match="expected 25 cases, found 24"):
        split(_case_ids(DEV_SIZE + HELDOUT_SIZE - 1))
