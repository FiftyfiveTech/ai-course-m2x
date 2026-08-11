"""Domain vocabulary, read from disk and shaped for a transcription request.

Whisper has no reason to know that "NATS" is a message broker or that "Yash" is a
person, so it guesses — and it guesses the same wrong way every run. The provider's
``prompt`` parameter biases decoding toward terms it is shown, which is the cheapest
accuracy lever in the pipeline: no model change, no retraining, one extra field.

Two files, on purpose. ``eval/vocab.txt`` is committed and therefore carries only names
a public repo may hold. ``eval/vocab.local.txt`` sits beside it, is git-ignored, and
carries the client identifiers the corpus genuinely contains. The loader appends the
second when it is there, so a clone runs with a smaller vocabulary rather than failing,
and the authors measure against the real one.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_VOCAB_PATH = Path("eval/vocab.txt")
"""Where the committed vocabulary lives, relative to the repo root."""


def local_sibling(path: Path) -> Path:
    """Return the git-ignored companion of a vocabulary file.

    Args:
        path: The committed vocabulary file.

    Returns:
        The same directory and stem with ``.local`` inserted before the suffix.
    """
    return path.with_name(f"{path.stem}.local{path.suffix}")


def load_vocab(path: Path = DEFAULT_VOCAB_PATH) -> list[str]:
    """Read the vocabulary, appending the local sibling when it exists.

    Args:
        path: The committed vocabulary file. One term per line; blank lines and lines
            beginning with ``#`` are ignored.

    Returns:
        Terms in file order, the local sibling's last, with case-insensitive
        duplicates removed.

    Raises:
        FileNotFoundError: ``path`` does not exist. The sibling's absence is normal and
            silent; the named file's absence is a typo worth surfacing.
    """
    if not path.is_file():
        raise FileNotFoundError(f"no such vocabulary file: {path}")

    terms: list[str] = []
    seen: set[str] = set()
    for source in (path, local_sibling(path)):
        if not source.is_file():
            continue
        for line in source.read_text(encoding="utf-8").splitlines():
            term = line.strip()
            if not term or term.startswith("#") or term.casefold() in seen:
                continue
            seen.add(term.casefold())
            terms.append(term)
    return terms


def as_prompt(terms: list[str]) -> str | None:
    """Join terms into the string Whisper's ``prompt`` parameter expects.

    Args:
        terms: Vocabulary terms.

    Returns:
        A comma-joined string, or ``None`` when there are no terms — an empty string is
        still a parameter, and would split the cache from a run that sent nothing.
    """
    return ", ".join(terms) or None
