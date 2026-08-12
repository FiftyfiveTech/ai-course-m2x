#!/usr/bin/env python3
"""Choose the 25 M2X-033 label cases deterministically.

Why this is a script and not a judgement call: the labeller here is also the author of
the extraction prompt, so any freedom in *which* passages get labelled is freedom to
pick passages the extractor happens to handle well. Selecting by a fixed rule removes
that degree of freedom — rerunning this script on the same corpus reproduces the same 25
cases, and a reviewer can check that nobody went shopping for easy transcript.

The rule, in full:

* **NOTSOFAR meetings** (``tiron-MTG_*``) are ~5.8 minutes end to end, which the ticket
  admits as a case on its own ("a coherent transcript chunk, ~2-5 min, or a full short
  meeting"). Each becomes exactly one case.
* **AMI and ICSI meetings** are 12-60 minutes. Each is cut into ``n`` equal blocks and
  one window of about :data:`TARGET_CASE_S` is taken from the centre of each block, so
  the cases spread across the whole meeting instead of clustering at the start where the
  agenda-setting lives.

``n`` per meeting is proportional to length, fixed in :data:`LONG_MEETING_CASES`.

Usage::

    uv run python scripts/select_label_cases.py            # write the manifest
    uv run python scripts/select_label_cases.py --print tiron-ES2004a-c01   # read a case
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from m2x.reference_transcript import (
    DEFAULT_REFERENCE_DIR,
    load_reference_segments,
    slice_case,
)

TARGET_CASE_S = 180.0
"""Aim for three minutes: mid-range of the ticket's 2-5, so rounding to whole turns
cannot push a case outside the band in either direction."""

LONG_MEETING_CASES = {
    "tiron-Bmr013": 3,
    "tiron-Bmr018": 3,
    "tiron-Bro021": 3,
    "tiron-EN2002a": 2,
    "tiron-ES2004a": 2,
    "tiron-IS1009a": 1,
    "tiron-TS3003a": 1,
}
"""How many windows to cut from each long meeting, roughly proportional to length.

Sums to 15; the ten NOTSOFAR meetings supply the other 10, for 25 total. Spread across
three source corpora on purpose — a ground truth drawn from one recording setup measures
that setup as much as it measures the extractor."""

MANIFEST_PATH = Path("eval/labels/cases.json")
"""Committed so the case bounds are pinned even if this script later changes."""


def window_around(
    segments: list, centre: int, target_s: float = TARGET_CASE_S
) -> tuple[int, int]:
    """Grow a turn window outward from a centre until it spans ``target_s``.

    Args:
        segments: The meeting's segments.
        centre: Index to grow around.
        target_s: Desired span in seconds.

    Returns:
        ``(first_turn, last_turn)``, inclusive, clamped to the meeting.
    """
    first = last = centre
    while last - first + 1 < len(segments):
        span = segments[last].t_end - segments[first].t_start
        if span >= target_s:
            break
        # Grow toward whichever side still has room, preferring forward so a case that
        # starts mid-sentence at least ends on a completed thought.
        if last + 1 < len(segments):
            last += 1
        elif first > 0:
            first -= 1
        else:
            break
    return first, last


def select_cases(reference_dir: Path = DEFAULT_REFERENCE_DIR) -> list[dict]:
    """Build the case manifest.

    Args:
        reference_dir: Directory holding the reference pairs.

    Returns:
        Manifest entries, ordered by case id.
    """
    entries: list[dict] = []

    for meeting_id in sorted(
        path.name[: -len(".speakers.json")] for path in reference_dir.glob("*.speakers.json")
    ):
        segments = load_reference_segments(meeting_id, reference_dir=reference_dir)
        windows: list[tuple[int, int]] = []

        if meeting_id.startswith("tiron-MTG_"):
            windows.append((0, len(segments) - 1))
        elif meeting_id in LONG_MEETING_CASES:
            count = LONG_MEETING_CASES[meeting_id]
            for block in range(count):
                centre = int(len(segments) * (block + 0.5) / count)
                windows.append(window_around(segments, min(centre, len(segments) - 1)))
        else:
            continue

        for number, (first, last) in enumerate(windows, start=1):
            case = slice_case(
                meeting_id, segments, case_number=number, first_turn=first, last_turn=last
            )
            entries.append(
                {
                    "case_id": case.case_id,
                    "meeting_id": meeting_id,
                    "first_turn": first,
                    "last_turn": last,
                    "turns": len(case.segments),
                    "duration_s": round(case.duration_s, 1),
                    "speakers": len({segment.speaker for segment in case.segments}),
                    "chars": sum(len(segment.text) for segment in case.segments),
                }
            )

    return sorted(entries, key=lambda entry: entry["case_id"])


def print_case(case_id: str, reference_dir: Path = DEFAULT_REFERENCE_DIR) -> int:
    """Print one case exactly as the extractor will see it.

    Args:
        case_id: Case to print.
        reference_dir: Directory holding the reference pairs.

    Returns:
        Process exit code.
    """
    from m2x.extraction import render_transcript

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entry = next((e for e in manifest["cases"] if e["case_id"] == case_id), None)
    if entry is None:
        print(f"no such case: {case_id}")
        return 1

    segments = load_reference_segments(entry["meeting_id"], reference_dir=reference_dir)
    case = slice_case(
        entry["meeting_id"],
        segments,
        case_number=int(case_id.rsplit("-c", 1)[1]),
        first_turn=entry["first_turn"],
        last_turn=entry["last_turn"],
    )
    block, _ = render_transcript(case.segments)
    print(block)
    return 0


def main() -> int:
    """Write the manifest, or print one case.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print", dest="case_id", default=None, help="print one case and exit")
    parser.add_argument("--reference-dir", type=Path, default=DEFAULT_REFERENCE_DIR)
    args = parser.parse_args()

    if args.case_id:
        return print_case(args.case_id, reference_dir=args.reference_dir)

    cases = select_cases(args.reference_dir)
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        json.dumps(
            {
                "note": (
                    "M2X-033 case bounds, chosen by scripts/select_label_cases.py. "
                    "Segments are re-derived from eval/tiron/ rather than copied, so a "
                    "label and the extractor always see identical text."
                ),
                "target_case_s": TARGET_CASE_S,
                "cases": cases,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    total = sum(entry["duration_s"] for entry in cases)
    print(f"{len(cases)} cases, {total / 60:.0f} min total -> {MANIFEST_PATH}")
    for entry in cases:
        print(
            f"  {entry['case_id']:24} {entry['turns']:4} turns "
            f"{entry['duration_s']:6.0f}s {entry['speakers']} spk {entry['chars']:5} chars"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
