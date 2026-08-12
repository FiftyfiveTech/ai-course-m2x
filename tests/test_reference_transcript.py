"""Tests for reading hand-annotated reference transcripts as citable segments.

The alignment check carries most of the weight here. Turns and words live in two files,
and a one-line drift between them does not raise anything on its own — it shifts every
later line onto the wrong timestamps, so labels made from it cite real segment ids for
words spoken somewhere else. That failure is invisible in the output and fatal to the
ground truth, which is why it is an error rather than a warning.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from m2x.errors import ConfigError
from m2x.reference_transcript import (
    load_reference_segments,
    slice_case,
)


def _write_reference(directory: Path, meeting_id: str, turns: list[dict], lines: list[str]) -> None:
    """Write a reference pair to disk.

    Args:
        directory: Destination directory.
        meeting_id: Reference meeting id.
        turns: Speaker turns to write.
        lines: Text lines to write, one per turn.
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{meeting_id}.speakers.json").write_text(
        json.dumps({"meeting_id": meeting_id, "segments": turns}), encoding="utf-8"
    )
    (directory / f"{meeting_id}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _turns(count: int) -> list[dict]:
    """Build `count` non-overlapping one-second turns.

    Args:
        count: How many turns.

    Returns:
        Turn dictionaries in the reference file's shape.
    """
    return [
        {"t_start": float(index), "t_end": float(index) + 0.5, "speaker": f"spk-{index % 2}"}
        for index in range(count)
    ]


def test_load_pairs_each_turn_with_its_line(tmp_path: Path) -> None:
    """Turns and words are zipped into segments in transcript order."""
    _write_reference(tmp_path, "ref-001", _turns(3), ["first", "second", "third"])

    segments = load_reference_segments("ref-001", reference_dir=tmp_path)

    assert [segment.text for segment in segments] == ["first", "second", "third"]
    assert segments[1].t_start == 1.0
    assert segments[1].speaker == "spk-1"


def test_load_rejects_a_turn_count_that_disagrees_with_the_line_count(tmp_path: Path) -> None:
    """A drifted pair is refused rather than silently mis-citing every later line."""
    _write_reference(tmp_path, "ref-001", _turns(3), ["first", "second"])

    with pytest.raises(ConfigError, match="3 reference turns but 2 lines"):
        load_reference_segments("ref-001", reference_dir=tmp_path)


def test_load_does_not_count_the_trailing_newline_as_a_turn(tmp_path: Path) -> None:
    """The file ends with a newline; that is formatting, not an empty final turn."""
    _write_reference(tmp_path, "ref-001", _turns(2), ["first", "second"])
    path = tmp_path / "ref-001.txt"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    assert len(load_reference_segments("ref-001", reference_dir=tmp_path)) == 2


def test_load_reports_a_missing_file_by_name(tmp_path: Path) -> None:
    """A missing half of the pair names the path, not just the meeting."""
    with pytest.raises(ConfigError, match="ref-404.speakers.json"):
        load_reference_segments("ref-404", reference_dir=tmp_path)


def test_slice_case_cuts_an_inclusive_range_and_names_the_meeting(tmp_path: Path) -> None:
    """A case id carries its meeting so a corpus overlap is visible in the filename."""
    _write_reference(tmp_path, "ref-001", _turns(10), [f"line {index}" for index in range(10)])
    segments = load_reference_segments("ref-001", reference_dir=tmp_path)

    case = slice_case("ref-001", segments, case_number=3, first_turn=2, last_turn=5)

    assert case.case_id == "ref-001-c03"
    assert len(case.segments) == 4
    assert case.segments[0].text == "line 2"
    assert case.segments[-1].text == "line 5"


def test_slice_case_rejects_bounds_past_the_end_of_the_meeting(tmp_path: Path) -> None:
    """Silently returning a short slice would make a case shorter than it claims."""
    _write_reference(tmp_path, "ref-001", _turns(4), [f"line {index}" for index in range(4)])
    segments = load_reference_segments("ref-001", reference_dir=tmp_path)

    with pytest.raises(ConfigError, match="only 4 turns"):
        slice_case("ref-001", segments, case_number=1, first_turn=0, last_turn=9)


def test_slice_case_rejects_inverted_bounds(tmp_path: Path) -> None:
    """An inverted range is a caller bug, and empty output would hide it."""
    _write_reference(tmp_path, "ref-001", _turns(4), [f"line {index}" for index in range(4)])
    segments = load_reference_segments("ref-001", reference_dir=tmp_path)

    with pytest.raises(ConfigError, match="bounds inverted"):
        slice_case("ref-001", segments, case_number=1, first_turn=3, last_turn=1)


def test_case_segments_are_cited_relative_to_the_case(tmp_path: Path) -> None:
    """Ids restart per case: a citation resolves against the case, not the meeting.

    This is the property that makes a case a self-contained labelling unit — the
    extractor sees only the case, so ``seg-0001`` must mean the case's first turn.
    """
    from m2x.extraction import segment_ids

    _write_reference(tmp_path, "ref-001", _turns(10), [f"line {index}" for index in range(10)])
    segments = load_reference_segments("ref-001", reference_dir=tmp_path)
    case = slice_case("ref-001", segments, case_number=1, first_turn=4, last_turn=6)

    ids = segment_ids(case.segments)

    assert list(ids) == ["seg-0001", "seg-0002", "seg-0003"]
    assert ids["seg-0001"] == (4.0, 4.5)


def test_the_committed_tiron_reference_loads_and_aligns() -> None:
    """The real corpus pair actually satisfies the alignment contract.

    A synthetic fixture proves the code; this proves the data every clone ships with.
    """
    segments = load_reference_segments("tiron-ES2004a")

    assert len(segments) == 260
    assert all(segment.speaker for segment in segments)
    assert segments[0].t_start == 0.0
