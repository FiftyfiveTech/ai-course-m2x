#!/usr/bin/env python3
"""Split the labelled cases into the dev and held-out sets.

The ticket asks for a random 15/10 split. Random, but *seeded*: an unseeded shuffle
cannot be checked by anyone later, and "trust me, it was random" is exactly the kind of
claim this phase exists to stop accepting. With :data:`SPLIT_SEED` recorded here, a
reviewer re-runs this script and gets the same fifteen and the same ten.

Seeding also removes a temptation that matters given who wrote the labels. An unseeded
split can be re-rolled until the held-out set looks favourable, and nothing in the
repository would show it. One fixed seed, chosen before any score existed, cannot be.

Usage::

    uv run python scripts/split_labels.py            # write dev/ and heldout/
    uv run python scripts/split_labels.py --dry-run  # show the split only
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from m2x.labels import DEFAULT_LABELS_DIR, load_label_set, save_labelled_case

SPLIT_SEED = 20260805
"""Fixed seed for the dev/held-out shuffle: M2X-033's ticket date, chosen for being
arbitrary and unrelated to anything measurable rather than for the split it produces."""

DEV_SIZE = 15
"""Cases for iteration. Saurabh may read these freely."""

HELDOUT_SIZE = 10
"""Cases sealed until the M2X-040 gate, then burnt."""


def split(case_ids: list[str], *, seed: int = SPLIT_SEED) -> tuple[list[str], list[str]]:
    """Draw the dev/held-out split.

    Args:
        case_ids: All case ids, in a stable order.
        seed: Shuffle seed.

    Returns:
        ``(dev_ids, heldout_ids)``, each sorted.

    Raises:
        ValueError: The number of cases does not match ``DEV_SIZE + HELDOUT_SIZE``.
    """
    if len(case_ids) != DEV_SIZE + HELDOUT_SIZE:
        raise ValueError(
            f"expected {DEV_SIZE + HELDOUT_SIZE} cases, found {len(case_ids)}; "
            "the split sizes are the ticket's, not a preference"
        )
    shuffled = sorted(case_ids)
    random.Random(seed).shuffle(shuffled)
    return sorted(shuffled[:DEV_SIZE]), sorted(shuffled[DEV_SIZE:])


def main() -> int:
    """Write the split.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels-dir", type=Path, default=DEFAULT_LABELS_DIR)
    parser.add_argument("--dry-run", action="store_true", help="print the split, write nothing")
    args = parser.parse_args()

    staging = args.labels_dir / "staging"
    cases = {case.case_id: case for case in load_label_set(staging)}
    if not cases:
        print(f"no labelled cases in {staging}")
        return 1

    dev_ids, heldout_ids = split(list(cases))

    print(f"seed {SPLIT_SEED} -> {len(dev_ids)} dev / {len(heldout_ids)} held-out\n")
    print("dev (readable, iterate freely):")
    for case_id in dev_ids:
        record = cases[case_id].label
        print(
            f"  {case_id:24} {len(record.decisions)}d {len(record.actions)}a "
            f"{len(record.risks)}r {len(record.open_questions)}q"
        )
    print("\nheld-out (SEALED until M2X-040 — do not open):")
    for case_id in heldout_ids:
        print(f"  {case_id}")

    if args.dry_run:
        return 0

    for case_id in dev_ids:
        save_labelled_case(cases[case_id], args.labels_dir / "dev")
    for case_id in heldout_ids:
        save_labelled_case(cases[case_id], args.labels_dir / "heldout")

    print(f"\nwritten to {args.labels_dir / 'dev'} and {args.labels_dir / 'heldout'}")
    print("held-out item counts deliberately not printed: the totals leak what is in the set.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
