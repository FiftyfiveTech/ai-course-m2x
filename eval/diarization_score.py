"""Scoring diarisation against reference speaker turns (M2X-022).

The metric is **attribution accuracy over time**: of the seconds where the reference
says someone was speaking, what fraction did we attribute to the right person?

Diarisation labels are arbitrary — a system that segments a meeting perfectly but calls
the chair ``SPEAKER_02`` instead of ``FEO072`` has made no error. So predicted labels are
first mapped onto reference labels by the assignment that maximises agreed time, and
only then is accuracy computed. Without that step every score would be near zero and the
number would measure naming, not diarisation.

The mapping is greedy rather than optimal (Hungarian). With four speakers the two agree
in every case observed here, and a greedy pass has no dependency and can be re-derived
by hand from the printed overlap table — which matters more for a number Yash has to be
able to reproduce than the last fraction of a point would.

Run it::

    uv run python eval/diarization_score.py \\
        --reference eval/ami/ami-001.speakers.json \\
        --hypothesis data/diarization/ami-001.turns.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

EXIT_OK = 0
EXIT_FAILURE = 1

Turn = tuple[float, float, str]


def load_turns(path: Path) -> list[Turn]:
    """Read turns from either a reference file or a :class:`DiarizationResult`.

    Both shapes carry a ``segments``/``turns`` list of ``t_start``/``t_end``/``speaker``,
    so one reader covers reference and hypothesis and there is no chance of the two
    being parsed by subtly different code.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("segments") if "segments" in data else data.get("turns")
    if rows is None:
        raise ValueError(f"{path} has neither 'segments' nor 'turns'")
    return [(float(r["t_start"]), float(r["t_end"]), str(r["speaker"])) for r in rows]


def overlap_matrix(reference: list[Turn], hypothesis: list[Turn]) -> dict[tuple[str, str], float]:
    """Seconds of overlap between every (reference speaker, predicted speaker) pair."""
    totals: dict[tuple[str, str], float] = defaultdict(float)
    for ref_start, ref_end, ref_speaker in reference:
        for hyp_start, hyp_end, hyp_speaker in hypothesis:
            if hyp_start >= ref_end:
                continue
            if hyp_end <= ref_start:
                continue
            totals[(ref_speaker, hyp_speaker)] += min(ref_end, hyp_end) - max(ref_start, hyp_start)
    return dict(totals)


def best_mapping(totals: dict[tuple[str, str], float]) -> dict[str, str]:
    """Greedily map predicted labels to reference labels by most shared time.

    Each predicted label is claimed at most once, so two predicted speakers cannot both
    become the same person — that would let a system that split one speaker in two score
    as if it had not.
    """
    mapping: dict[str, str] = {}
    claimed_reference: set[str] = set()
    for (ref_speaker, hyp_speaker), _ in sorted(totals.items(), key=lambda kv: -kv[1]):
        if hyp_speaker in mapping or ref_speaker in claimed_reference:
            continue
        mapping[hyp_speaker] = ref_speaker
        claimed_reference.add(ref_speaker)
    return mapping


def speaking_time(turns: list[Turn]) -> float:
    """Total referenced speech, counting overlapping speech once per speaker."""
    return sum(end - start for start, end, _ in turns)


def solo_intervals(reference: list[Turn]) -> list[Turn]:
    """Reference stretches where exactly one speaker is active.

    Meetings have people talking over each other — on ``ami-001``, 466.6s of the 1943.4s
    of referenced speech is simultaneous. Scored against the full total, a system that
    emits one speaker per instant cannot exceed 76.0% no matter how well it performs,
    because a quarter of the reference is two voices at once. That ceiling is a property
    of the metric, not of the model, and reporting only the diluted figure makes a good
    diarisation look mediocre.

    Splitting the reference on every boundary and keeping the single-speaker instants
    gives the number that actually answers "when one person is talking, do we know who?".
    """
    points = sorted({value for start, end, _ in reference for value in (start, end)})
    intervals: list[Turn] = []
    for left, right in zip(points, points[1:]):
        if right <= left:
            continue
        active = {speaker for start, end, speaker in reference if start < right and end > left}
        if len(active) == 1:
            intervals.append((left, right, active.pop()))
    return intervals


def attributed_time(intervals: list[Turn], hypothesis: list[Turn], mapping: dict[str, str]) -> float:
    """Seconds of ``intervals`` covered by a hypothesis turn mapped to the right speaker."""
    return sum(
        min(right, end) - max(left, start)
        for left, right, speaker in intervals
        for start, end, label in hypothesis
        if start < right and end > left and mapping.get(label) == speaker
    )


def score(reference: list[Turn], hypothesis: list[Turn]) -> dict[str, object]:
    """Attribution accuracy after label mapping, plus the diagnostics behind it.

    Two accuracy figures, because one of them is misleading on its own:

    ``accuracy`` divides by *all* referenced speech, including stretches where two people
    talk at once. It is the conservative number and the one to quote if only one is quoted.

    ``solo_accuracy`` divides by single-speaker speech only, and is the number that answers
    "when one person is talking, do we know who?". On a four-person meeting the two differ
    by more than ten points, and the gap is overlap, not error.

    ``precision`` is deliberately **not** described as a probability. Its denominator sums
    every (reference, hypothesis) overlap, so a hypothesis second covering two overlapping
    reference turns is counted twice — on ``ami-001`` that denominator (2561.7s) exceeds
    all referenced speech (1943.4s). It is useful for comparing two runs on the same
    reference and meaningless as an absolute.
    """
    totals = overlap_matrix(reference, hypothesis)
    mapping = best_mapping(totals)

    correct = sum(
        seconds
        for (ref_speaker, hyp_speaker), seconds in totals.items()
        if mapping.get(hyp_speaker) == ref_speaker
    )
    attributed = sum(totals.values())
    reference_s = speaking_time(reference)

    solo = solo_intervals(reference)
    solo_s = sum(end - start for start, end, _ in solo)
    solo_correct = attributed_time(solo, hypothesis, mapping)

    return {
        "accuracy": correct / reference_s if reference_s else None,
        "solo_accuracy": solo_correct / solo_s if solo_s else None,
        "precision": correct / attributed if attributed else None,
        "correct_s": round(correct, 1),
        "attributed_s": round(attributed, 1),
        "reference_s": round(reference_s, 1),
        "solo_correct_s": round(solo_correct, 1),
        "solo_reference_s": round(solo_s, 1),
        # Speaker-time, not wall-clock: two people talking for one second contribute two
        # seconds here, the same way they contribute two to reference_s. Naming it
        # `overlapped_s` invited reading it as a duration, which it is not.
        "overlapped_speaker_s": round(reference_s - solo_s, 1),
        "reference_speakers": len({speaker for _, _, speaker in reference}),
        "hypothesis_speakers": len({speaker for _, _, speaker in hypothesis}),
        "mapping": mapping,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="diarisation attribution accuracy (M2X-022)")
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--hypothesis", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = score(load_turns(args.reference), load_turns(args.hypothesis))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    if args.json:
        print(json.dumps(result, indent=2))
        return EXIT_OK

    accuracy, solo, precision = result["accuracy"], result["solo_accuracy"], result["precision"]
    print(f"{args.hypothesis}")
    print(
        f"  speakers   {result['hypothesis_speakers']} found / "
        f"{result['reference_speakers']} in reference"
    )
    print(
        f"  accuracy   {accuracy:.1%} "
        f"({result['correct_s']}s of {result['reference_s']}s referenced speech)"
        if accuracy is not None
        else "  accuracy   n/a"
    )
    if solo is not None:
        print(
            f"  solo       {solo:.1%} "
            f"({result['solo_correct_s']}s of {result['solo_reference_s']}s single-speaker "
            f"speech; {result['overlapped_speaker_s']}s of speaker-time is simultaneous)"
        )
    if precision is not None:
        print(f"  precision  {precision:.1%} (run-to-run comparison only, not a probability)")
    print(f"  mapping    {result['mapping']}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
