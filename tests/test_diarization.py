"""Tests for speaker attribution and its scorer.

Everything here is hermetic. ``load_pipeline`` and ``diarize`` need torch and a gated
checkpoint, so they are covered by the ticket's real run rather than by a mock that
would only assert that the mock was called. What *is* tested is the part that decides
correctness: which speaker a transcript segment is assigned to, and how that assignment
is scored against a reference.
"""

from __future__ import annotations

from diarization_score import best_mapping, overlap_matrix, score
from m2x.diarization import SpeakerTurn, assign_speakers, coverage, dominant_speaker
from m2x.types import Provider, Transcript, TranscriptSegment


def _turns(*rows: tuple[float, float, str]) -> list[SpeakerTurn]:
    return [SpeakerTurn(t_start=start, t_end=end, speaker=speaker) for start, end, speaker in rows]


def _transcript(*rows: tuple[float, float, str]) -> Transcript:
    return Transcript(
        model_repo_id="openai/whisper-large-v3",
        provider=Provider.GROQ,
        latency_ms=1,
        text=" ".join(text for _, _, text in rows),
        segments=[
            TranscriptSegment(t_start=start, t_end=end, text=text) for start, end, text in rows
        ],
        audio_seconds=100.0,
    )


class TestDominantSpeaker:
    def test_picks_the_speaker_holding_most_of_the_segment(self):
        # Starts during a short interjection, continues into a long answer: the segment
        # belongs to whoever said most of it, not to whoever spoke first.
        turns = _turns((0.0, 2.0, "A"), (2.0, 10.0, "B"))
        assert dominant_speaker(1.0, 10.0, turns) == "B"

    def test_returns_none_when_nothing_overlaps(self):
        # An unattributed segment is a visible gap. Guessing the nearest speaker would
        # manufacture attribution no audio supports.
        assert dominant_speaker(50.0, 60.0, _turns((0.0, 10.0, "A"))) is None

    def test_sums_repeated_turns_from_the_same_speaker(self):
        turns = _turns((0.0, 3.0, "A"), (3.0, 5.0, "B"), (5.0, 8.0, "A"))
        assert dominant_speaker(0.0, 8.0, turns) == "A"

    def test_touching_boundaries_do_not_count_as_overlap(self):
        assert dominant_speaker(10.0, 20.0, _turns((0.0, 10.0, "A"))) is None


class TestAssignSpeakers:
    def test_fills_speaker_on_every_segment(self):
        result = assign_speakers(
            _transcript((0.0, 5.0, "hello"), (5.0, 9.0, "hi")),
            _turns((0.0, 5.0, "SPEAKER_00"), (5.0, 9.0, "SPEAKER_01")),
        )
        assert [segment.speaker for segment in result.segments] == ["SPEAKER_00", "SPEAKER_01"]

    def test_applies_the_name_mapping(self):
        result = assign_speakers(
            _transcript((0.0, 5.0, "hello")),
            _turns((0.0, 5.0, "SPEAKER_00")),
            names={"SPEAKER_00": "Chair"},
        )
        assert result.segments[0].speaker == "Chair"

    def test_unmapped_labels_keep_their_identity(self):
        # Dropping an unmapped label would merge two people into one downstream.
        result = assign_speakers(
            _transcript((0.0, 5.0, "a"), (5.0, 9.0, "b")),
            _turns((0.0, 5.0, "SPEAKER_00"), (5.0, 9.0, "SPEAKER_01")),
            names={"SPEAKER_00": "Chair"},
        )
        assert [segment.speaker for segment in result.segments] == ["Chair", "SPEAKER_01"]

    def test_does_not_mutate_the_input_transcript(self):
        transcript = _transcript((0.0, 5.0, "hello"))
        assign_speakers(transcript, _turns((0.0, 5.0, "SPEAKER_00")))
        assert transcript.segments[0].speaker is None

    def test_segments_outside_all_turns_stay_unattributed(self):
        result = assign_speakers(
            _transcript((0.0, 5.0, "a"), (90.0, 95.0, "b")),
            _turns((0.0, 5.0, "SPEAKER_00")),
        )
        assert [segment.speaker for segment in result.segments] == ["SPEAKER_00", None]


class TestCoverage:
    def test_fraction_of_segments_with_a_speaker(self):
        result = assign_speakers(
            _transcript((0.0, 5.0, "a"), (90.0, 95.0, "b")),
            _turns((0.0, 5.0, "SPEAKER_00")),
        )
        assert coverage(result.segments) == 0.5

    def test_empty_transcript_is_zero_not_an_error(self):
        assert coverage([]) == 0.0


class TestScorer:
    def test_label_names_do_not_affect_the_score(self):
        # A system that segments perfectly but calls the chair SPEAKER_02 has made no
        # error; without label mapping every score would be near zero.
        reference = [(0.0, 10.0, "FEO072"), (10.0, 20.0, "MEE071")]
        hypothesis = [(0.0, 10.0, "SPEAKER_02"), (10.0, 20.0, "SPEAKER_00")]
        assert score(reference, hypothesis)["accuracy"] == 1.0

    def test_mapping_is_one_predicted_label_per_reference_speaker(self):
        # Splitting one speaker into two must not score as if it had not happened.
        totals = overlap_matrix(
            [(0.0, 20.0, "A")],
            [(0.0, 10.0, "S0"), (10.0, 20.0, "S1")],
        )
        mapping = best_mapping(totals)
        assert len(set(mapping.values())) == len(mapping)

    def test_wrong_attribution_lowers_accuracy(self):
        reference = [(0.0, 10.0, "A"), (10.0, 20.0, "B")]
        hypothesis = [(0.0, 20.0, "S0")]
        result = score(reference, hypothesis)
        assert result["accuracy"] == 0.5

    def test_missed_speech_and_wrong_speech_are_distinguishable(self):
        # Attributed nothing at all: accuracy low, precision undefined rather than 0.
        result = score([(0.0, 10.0, "A")], [])
        assert result["accuracy"] == 0.0
        assert result["precision"] is None

    def test_reports_speaker_counts_from_both_sides(self):
        result = score(
            [(0.0, 10.0, "A"), (10.0, 20.0, "B")],
            [(0.0, 10.0, "S0"), (10.0, 15.0, "S1"), (15.0, 20.0, "S2")],
        )
        assert result["reference_speakers"] == 2
        assert result["hypothesis_speakers"] == 3
