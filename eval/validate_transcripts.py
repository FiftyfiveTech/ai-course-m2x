"""Validate diarised transcripts against the Transcript model (M2X-025 gate step 2).

The Phase 1 gate is "speaker-attributed, timestamped transcript on >=3 sample meetings".
Three things are checked, and only the first two are binary:

1. **Schema.** The file parses as a ``Transcript``: every segment carries ``text``,
   ``t_start``, ``t_end``, and no range runs backwards.
2. **Attribution coverage** — the fraction of segments carrying a speaker, against
   ``MIN_ATTRIBUTION``. Not "every segment", because ``TranscriptSegment.speaker`` is
   nullable *on purpose*: ``dominant_speaker`` returns ``None`` when no diarisation turn
   overlaps a segment, on the grounds that a visible gap beats attribution no audio
   supports. Demanding 100% here would ask the pipeline to guess.
3. **Voiced segments** — reported, never failed. Whisper emits long segments with empty
   text; they inflate any duration-based coverage number, so the count is printed where
   whoever reads the gate will see it.

Exits 1 if any meeting fails 1 or 2, so the gate cannot be recorded green by reading past
it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from m2x.diarization import coverage  # noqa: E402
from m2x.types import Transcript  # noqa: E402

DEFAULT_MEETINGS = ("mtg-001", "mtg-002", "ami-001")
DEFAULT_DIR = Path("data/diarization")
MIN_ATTRIBUTION = 0.95
"""Floor for the fraction of segments carrying a speaker. Set against measured values —
the three pilot meetings land at 99.0-100% — not chosen as a round number."""


def check(path: Path) -> tuple[bool, str]:
    """Return whether the file passes the gate, and a one-line report."""
    try:
        transcript = Transcript.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as error:
        return False, f"schema INVALID — {error.error_count()} error(s)"
    except (OSError, json.JSONDecodeError) as error:
        return False, f"unreadable — {error}"

    segments = transcript.segments
    if not segments:
        return False, "schema valid but zero segments"

    backwards = [seg for seg in segments if seg.t_end < seg.t_start]
    voiceless = [seg for seg in segments if not seg.text.strip()]
    speakers = sorted({seg.speaker for seg in segments if seg.speaker})
    attribution = coverage(segments)
    silent_s = sum(seg.t_end - seg.t_start for seg in voiceless)

    report = (
        f"{len(segments):>3} segments · {len(speakers)} speakers · "
        f"{attribution:.1%} attributed · {segments[-1].t_end:.0f}s"
    )
    if voiceless:
        report += f" · {len(voiceless)} empty-text ({silent_s:.0f}s)"

    if backwards:
        return False, f"{report} — {len(backwards)} segment(s) with t_end < t_start"
    if attribution < MIN_ATTRIBUTION:
        return False, f"{report} — below the {MIN_ATTRIBUTION:.0%} attribution floor"
    return True, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("meetings", nargs="*", default=list(DEFAULT_MEETINGS))
    parser.add_argument("--diarization-dir", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args(argv)

    meetings = args.meetings or DEFAULT_MEETINGS
    failures = 0
    for meeting in meetings:
        ok, report = check(args.diarization_dir / f"{meeting}.json")
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {meeting:<8} {report}")

    print(f"\n{len(meetings) - failures}/{len(meetings)} speaker-attributed, schema-valid")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
