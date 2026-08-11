"""Tests for :mod:`m2x.vocab`.

The loader's contract is written down in ``eval/vocab.txt``'s own header, so these
tests are that header restated as assertions: comments and blanks drop out, the
git-ignored ``.local`` sibling is appended when present, and the result converts to the
comma-joined string Whisper's prompt parameter takes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from m2x.vocab import as_prompt, load_vocab, local_sibling


@pytest.fixture
def vocab_file(tmp_path: Path) -> Path:
    """A vocabulary file with every ignorable line shape in it."""
    path = tmp_path / "vocab.txt"
    path.write_text(
        "# a header comment\n"
        "\n"
        "FiftyFive\n"
        "   \n"
        "# --- stack\n"
        "NestJS\n"
        "  Postgres  \n",
        encoding="utf-8",
    )
    return path


class TestLoading:
    def test_comments_and_blank_lines_are_dropped(self, vocab_file: Path) -> None:
        assert load_vocab(vocab_file) == ["FiftyFive", "NestJS", "Postgres"]

    def test_terms_keep_their_file_order(self, vocab_file: Path) -> None:
        """Order is the author's grouping — organisation, then people, then stack."""
        assert load_vocab(vocab_file)[0] == "FiftyFive"

    def test_a_missing_vocabulary_file_is_an_error(self, tmp_path: Path) -> None:
        """Asking for a file that is not there is a typo, not an empty vocabulary."""
        with pytest.raises(FileNotFoundError, match="nope.txt"):
            load_vocab(tmp_path / "nope.txt")


class TestLocalSibling:
    def test_local_terms_are_appended(self, vocab_file: Path) -> None:
        """Client hotwords live outside the public file but must reach the model."""
        local_sibling(vocab_file).write_text("ACME\n", encoding="utf-8")

        assert load_vocab(vocab_file) == ["FiftyFive", "NestJS", "Postgres", "ACME"]

    def test_an_absent_local_sibling_is_not_an_error(self, vocab_file: Path) -> None:
        """The public repo's clone has no ``.local`` file and must still run."""
        assert local_sibling(vocab_file).exists() is False
        assert load_vocab(vocab_file) == ["FiftyFive", "NestJS", "Postgres"]

    def test_a_term_in_both_files_appears_once(self, vocab_file: Path) -> None:
        """A duplicate would spend prompt budget twice and bias nothing extra."""
        local_sibling(vocab_file).write_text("nestjs\nACME\n", encoding="utf-8")

        assert load_vocab(vocab_file) == ["FiftyFive", "NestJS", "Postgres", "ACME"]

    def test_the_sibling_sits_next_to_the_file_it_extends(self, tmp_path: Path) -> None:
        assert local_sibling(tmp_path / "eval" / "vocab.txt") == (
            tmp_path / "eval" / "vocab.local.txt"
        )


class TestPromptFormatting:
    def test_terms_join_into_the_whisper_prompt_string(self) -> None:
        assert as_prompt(["FiftyFive", "NestJS"]) == "FiftyFive, NestJS"

    def test_an_empty_vocabulary_is_no_prompt_at_all(self) -> None:
        """An empty string would still be sent as a parameter and change the cache key."""
        assert as_prompt([]) is None
