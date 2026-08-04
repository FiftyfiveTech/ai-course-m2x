"""Tests for the corpus manifest.

The tracked ``data/corpus.json`` is asserted against directly. That is deliberate: the
manifest is the project's claim about what it runs on, and a claim nobody checks drifts.
These tests fail the moment the manifest stops describing a usable corpus — before Day 2
discovers it the expensive way.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from m2x.corpus import DEFAULT_MANIFEST, Corpus, load_corpus

MINIMAL = {
    "schema": 1,
    "generated": "2026-08-04",
    "meetings": [
        {
            "id": "mtg-001",
            "file": "data/raw/mtg-001.wav",
            "origin": "internal",
            "date": "2026-07-27",
            "duration_s": 1054.5,
            "participants": 4,
            "language": "hinglish",
            "screen_share": True,
            "consent_confirmed": True,
        }
    ],
}


def _write(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestLoading:
    def test_loads_a_valid_manifest(self, tmp_path: Path) -> None:
        corpus = load_corpus(_write(tmp_path, MINIMAL))

        assert corpus.schema_version == 1
        assert corpus.meetings[0].id == "mtg-001"
        assert corpus.meetings[0].screen_share is True

    def test_missing_manifest_is_a_distinct_failure(self, tmp_path: Path) -> None:
        """Absent manifest means an incomplete clone; absent audio does not."""
        with pytest.raises(FileNotFoundError):
            load_corpus(tmp_path / "nope.json")

    def test_malformed_manifest_names_the_file(self, tmp_path: Path) -> None:
        path = tmp_path / "corpus.json"
        path.write_text("{oops", encoding="utf-8")

        with pytest.raises(ValueError, match="not a valid corpus manifest"):
            load_corpus(path)

    def test_unknown_origin_is_rejected(self, tmp_path: Path) -> None:
        """Origins drive per-origin reporting, so a typo must not create a third one."""
        payload = json.loads(json.dumps(MINIMAL))
        payload["meetings"][0]["origin"] = "interanl"

        with pytest.raises(ValueError):
            load_corpus(_write(tmp_path, payload))

    def test_a_meeting_with_no_participants_is_rejected(self, tmp_path: Path) -> None:
        payload = json.loads(json.dumps(MINIMAL))
        payload["meetings"][0]["participants"] = 0

        with pytest.raises(ValueError):
            load_corpus(_write(tmp_path, payload))


class TestLookups:
    def test_by_id_finds_a_meeting(self, tmp_path: Path) -> None:
        corpus = load_corpus(_write(tmp_path, MINIMAL))

        assert corpus.by_id("mtg-001").duration_s == pytest.approx(1054.5)

    def test_unknown_id_lists_the_known_ones(self, tmp_path: Path) -> None:
        """The usual cause is a typo, so the error should show what was available."""
        corpus = load_corpus(_write(tmp_path, MINIMAL))

        with pytest.raises(KeyError, match="mtg-001"):
            corpus.by_id("mtg-009")

    def test_missing_reports_declared_but_absent_audio(self, tmp_path: Path) -> None:
        """A fresh clone declares five meetings and holds none — a state, not an error."""
        corpus = load_corpus(_write(tmp_path, MINIMAL))

        assert [meeting.id for meeting in corpus.missing()] == ["mtg-001"]


class TestTheRealManifest:
    """Assertions about the corpus this project actually ships with."""

    @pytest.fixture
    def corpus(self) -> Corpus:
        return load_corpus(DEFAULT_MANIFEST)

    def test_the_tracked_manifest_is_valid(self, corpus: Corpus) -> None:
        """It is tracked precisely so a fresh clone can read it — so it must parse."""
        assert corpus.meetings

    def test_meeting_ids_are_unique(self, corpus: Corpus) -> None:
        ids = [meeting.id for meeting in corpus.meetings]

        assert len(ids) == len(set(ids))

    def test_corpus_meets_the_ticket_minimum(self, corpus: Corpus) -> None:
        """M2X-015 asks for at least three normalised meetings."""
        assert len(corpus.meetings) >= 3

    def test_both_origins_are_represented(self, corpus: Corpus) -> None:
        """One register alone cannot separate "the model is wrong" from "the audio is
        code-switched" — the reason the AMI set was added at all."""
        assert corpus.with_origin("internal")
        assert corpus.with_origin("ami")

    def test_every_meeting_has_confirmed_consent(self, corpus: Corpus) -> None:
        """The data boundary is a gate on admission, not a note in a document."""
        assert all(meeting.consent_confirmed for meeting in corpus.meetings)

    def test_a_screen_share_meeting_exists_for_phase_five(self, corpus: Corpus) -> None:
        """M2X-070 needs one; discovering on Sunday that none was admitted is too late."""
        assert any(meeting.screen_share for meeting in corpus.meetings)

    def test_ami_meetings_declare_their_reference(self, corpus: Corpus) -> None:
        """The reference speaker turns are what make M2X-022 measurable."""
        assert all(meeting.reference is not None for meeting in corpus.with_origin("ami"))

    def test_clips_point_at_real_meetings(self, corpus: Corpus) -> None:
        for clip in corpus.clips:
            corpus.by_id(clip.source)
