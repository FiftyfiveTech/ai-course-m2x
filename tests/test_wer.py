"""Tests for the M2X-021 scorer.

The metric is the instrument every Day-2 adoption decision rests on, so it gets the
same treatment as production code. Two properties matter most and are tested directly:
a perfect transcription scores 0.0, and the entity denominator is the reference rather
than the vocabulary file — the second is what stops the number being improved by
editing a word list.
"""

from __future__ import annotations

import json

import pytest
from wer import (
    EXIT_NO_REFERENCE,
    edit_distance,
    entity_capture,
    load_vocab,
    main,
    normalise,
    read_hypothesis,
    read_reference,
    word_error_rate,
)


class TestNormalise:
    def test_lowercases_and_strips_punctuation(self):
        assert normalise("Hello, World!") == ["hello", "world"]

    def test_keeps_intraword_apostrophes_and_hyphens(self):
        assert normalise("don't re-run") == ["don't", "re-run"]

    def test_strips_speaker_prefixes(self):
        assert normalise("S1: yes\nS2: no") == ["yes", "no"]

    def test_drops_transcriber_annotations(self):
        # [inaudible] marks what the reference could not resolve. Counting it as a word
        # the route failed to produce would penalise the route for our limitation.
        assert normalise("the [inaudible] plan") == ["the", "plan"]

    def test_handles_devanagari_and_latin_on_one_line(self):
        # The Hinglish meetings code-switch mid-sentence; the snippet convention keeps
        # each word in the script it was spoken in.
        assert normalise("हाँ, that's GPT.") == ["हाँ", "that's", "gpt"]


class TestEditDistance:
    def test_identical_sequences_have_no_edits(self):
        assert edit_distance(["a", "b"], ["a", "b"]) == (0, 0, 0)

    def test_counts_substitution_deletion_insertion_separately(self):
        assert edit_distance(["a", "b", "c"], ["a", "x", "c", "d"]) == (1, 0, 1)
        assert edit_distance(["a", "b", "c"], ["a", "c"]) == (0, 1, 0)


class TestWordErrorRate:
    def test_perfect_transcription_scores_zero(self):
        assert word_error_rate("the plan is fine", "The plan is fine!")["wer"] == 0.0

    def test_components_distinguish_deletion_from_hallucination(self):
        # Both routes score ~0.5; only the breakdown says which failure it is.
        dropped = word_error_rate("a b c d", "a b")
        invented = word_error_rate("a b c d", "a b c d e f")
        assert dropped["wer"] == invented["wer"] == 0.5
        assert dropped["deletions"] == 2 and dropped["insertions"] == 0
        assert invented["insertions"] == 2 and invented["deletions"] == 0

    def test_empty_reference_raises_rather_than_returning_a_number(self):
        with pytest.raises(ValueError, match="nothing to score against"):
            word_error_rate("   ", "anything")


class TestEntityCapture:
    def test_denominator_is_the_reference_not_the_vocabulary(self):
        # "Chroma" is in the vocabulary but nobody said it, so it cannot be missed.
        result = entity_capture("we used Postgres", "we used Postgres", ["Postgres", "Chroma"])
        assert result["spoken"] == ["Postgres"]
        assert result["capture"] == 1.0

    def test_reports_which_terms_were_lost(self):
        result = entity_capture("Postgres and NATS", "postgres and nuts", ["Postgres", "NATS"])
        assert result["capture"] == 0.5
        assert result["missed"] == ["NATS"]

    def test_multiword_terms_match_as_a_phrase(self):
        result = entity_capture("ask FiftyFive Technologies", "ask FiftyFive", ["FiftyFive Technologies"])
        assert result["missed"] == ["FiftyFive Technologies"]

    def test_no_spoken_terms_yields_none_not_zero(self):
        # 0% would read as "captured nothing"; None reads as "nothing to capture".
        assert entity_capture("hello there", "hello there", ["Postgres"])["capture"] is None

    def test_substrings_do_not_count_as_matches(self):
        assert entity_capture("the natsuki demo", "the natsuki demo", ["NATS"])["spoken"] == []


class TestLoadVocab:
    def test_skips_comments_and_blanks(self, tmp_path):
        path = tmp_path / "vocab.txt"
        path.write_text("# header\n\nPostgres\n  NATS  \n")
        assert load_vocab(path, local=None) == ["Postgres", "NATS"]

    def test_appends_local_overlay_when_present(self, tmp_path):
        tracked, local = tmp_path / "v.txt", tmp_path / "v.local.txt"
        tracked.write_text("Postgres\n")
        local.write_text("# client hotwords\nAcme\n")
        assert load_vocab(tracked, local=local) == ["Postgres", "Acme"]

    def test_missing_overlay_is_not_an_error(self, tmp_path):
        tracked = tmp_path / "v.txt"
        tracked.write_text("Postgres\n")
        assert load_vocab(tracked, local=tmp_path / "absent.txt") == ["Postgres"]


class TestReadReference:
    def test_refuses_a_template_that_is_still_untranscribed(self, tmp_path):
        path = tmp_path / "snippet.txt"
        path.write_text("# Status: NOT YET TRANSCRIBED\n")
        with pytest.raises(ValueError, match="written by ear"):
            read_reference(path)

    def test_refuses_a_comment_only_file(self, tmp_path):
        path = tmp_path / "snippet.txt"
        path.write_text("# just a header\n#\n")
        with pytest.raises(ValueError, match="no transcript lines"):
            read_reference(path)

    def test_strips_comments_and_keeps_turns(self, tmp_path):
        path = tmp_path / "snippet.txt"
        path.write_text("# header\nS1: hello\nS2: bye\n")
        assert read_reference(path).split() == ["S1:", "hello", "S2:", "bye"]


class TestReadHypothesis:
    def _transcript(self, tmp_path):
        path = tmp_path / "t.json"
        path.write_text(
            json.dumps(
                {
                    "text": "one two three",
                    "segments": [
                        {"t_start": 0.0, "t_end": 10.0, "text": "one"},
                        {"t_start": 10.0, "t_end": 20.0, "text": "two"},
                        {"t_start": 20.0, "t_end": 30.0, "text": "three"},
                    ],
                }
            )
        )
        return path

    def test_whole_transcript_by_default(self, tmp_path):
        assert read_hypothesis(self._transcript(tmp_path)) == "one two three"

    def test_window_selects_overlapping_segments(self, tmp_path):
        assert read_hypothesis(self._transcript(tmp_path), window=(10.0, 20.0)).split() == ["two"]

    def test_partial_overlap_is_included_whole(self, tmp_path):
        # Clipping a segment at a timestamp boundary would invent errors the route
        # did not make, so an overlapping segment comes in entire.
        assert read_hypothesis(self._transcript(tmp_path), window=(5.0, 15.0)).split() == [
            "one",
            "two",
        ]

    def test_plain_text_hypothesis(self, tmp_path):
        path = tmp_path / "t.txt"
        path.write_text("one two")
        assert read_hypothesis(path) == "one two"


class TestCli:
    def test_untranscribed_reference_exits_two_and_scores_nothing(self, tmp_path, capsys):
        reference = tmp_path / "snippet.txt"
        reference.write_text("# Status: NOT YET TRANSCRIBED\n")
        hypothesis = tmp_path / "h.txt"
        hypothesis.write_text("anything")

        code = main(["--reference", str(reference), "--hypothesis", str(hypothesis)])

        assert code == EXIT_NO_REFERENCE
        assert "NOT YET TRANSCRIBED" in capsys.readouterr().err

    def test_json_output_carries_both_metrics(self, tmp_path, capsys):
        reference = tmp_path / "snippet.txt"
        reference.write_text("S1: we used Postgres today\n")
        hypothesis = tmp_path / "h.txt"
        hypothesis.write_text("we used postgres today")
        vocab = tmp_path / "vocab.txt"
        vocab.write_text("Postgres\n")

        code = main(
            [
                "--reference", str(reference),
                "--hypothesis", str(hypothesis),
                "--vocab", str(vocab),
                "--json",
            ]
        )

        assert code == 0
        report = json.loads(capsys.readouterr().out)
        assert report["wer"] == 0.0
        assert report["entities"]["capture"] == 1.0
