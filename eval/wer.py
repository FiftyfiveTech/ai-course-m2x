"""Informal WER and entity capture against a hand-written reference (M2X-021).

Two numbers, one reference. **WER** is word error rate: how much of what was said the
route got wrong. **Entity capture** is narrower and more useful — of the vocabulary
terms actually spoken in the window, how many survived transcription. A route can post
a mediocre WER and still capture every name, which is the trade the pipeline cares
about, so the two are reported side by side and never averaged into one score.

Run it::

    uv run python eval/wer.py --reference eval/snippets/mtg-001.txt \\
        --hypothesis data/comparison/large-v3-auto/snippet-mtg-001-2min.json

    uv run python eval/wer.py ... --window 211 331     # score a full-meeting transcript

The reference must be hand-written by ear. This module refuses to score a snippet still
marked ``NOT YET TRANSCRIBED`` rather than returning a number computed against an empty
file: a reference derived from — or missing under — the system being measured makes the
measurement meaningless, and a loud failure is the only honest response.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

DEFAULT_VOCAB = Path("eval/vocab.txt")
LOCAL_VOCAB = Path("eval/vocab.local.txt")
"""Client hotwords, git-ignored. Appended to the tracked vocabulary when present, so
the public file carries no client identifiers while the measurement still uses them."""

_UNTRANSCRIBED = "NOT YET TRANSCRIBED"

_SPEAKER_PREFIX = re.compile(r"^\s*S\d+\s*:\s*", re.MULTILINE)
"""``S1:`` turn markers. Speaker attribution is M2X-022's metric, not WER's."""

_BRACKETED = re.compile(r"\[(?:inaudible|crosstalk)\]", re.IGNORECASE)
"""Transcriber annotations. They mark what the *reference* could not resolve, so
counting them as words the route failed to produce would penalise it for our limits."""

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_NO_REFERENCE = 2


def normalise(text: str) -> list[str]:
    """Lowercase, drop punctuation and annotations, split on whitespace.

    Unicode-aware on purpose: the Hinglish meetings mix Devanagari and Latin in one
    line, and a naive ``string.punctuation`` filter would leave Devanagari danda marks
    attached to words and score them as misses.
    """
    text = _BRACKETED.sub(" ", _SPEAKER_PREFIX.sub("", text))
    kept = [
        char
        for char in unicodedata.normalize("NFKC", text.lower())
        if not unicodedata.category(char).startswith("P") or char in "'-"
    ]
    return "".join(kept).split()


def edit_distance(reference: list[str], hypothesis: list[str]) -> tuple[int, int, int]:
    """Word-level Levenshtein, returning ``(substitutions, deletions, insertions)``.

    Full DP rather than a library: the matrix has to be reproducible by anyone with a
    Python install and no lockfile, since Yash re-runs this to check the numbers.
    """
    rows, cols = len(reference) + 1, len(hypothesis) + 1
    cost = [[0] * cols for _ in range(rows)]
    back: list[list[str]] = [[""] * cols for _ in range(rows)]

    for i in range(1, rows):
        cost[i][0], back[i][0] = i, "d"
    for j in range(1, cols):
        cost[0][j], back[0][j] = j, "i"

    for i in range(1, rows):
        for j in range(1, cols):
            if reference[i - 1] == hypothesis[j - 1]:
                cost[i][j], back[i][j] = cost[i - 1][j - 1], "="
                continue
            sub, dele, ins = cost[i - 1][j - 1] + 1, cost[i - 1][j] + 1, cost[i][j - 1] + 1
            best = min(sub, dele, ins)
            cost[i][j] = best
            back[i][j] = "s" if best == sub else ("d" if best == dele else "i")

    subs = dels = inss = 0
    i, j = len(reference), len(hypothesis)
    while i > 0 or j > 0:
        move = back[i][j]
        if move == "=":
            i, j = i - 1, j - 1
        elif move == "s":
            subs, i, j = subs + 1, i - 1, j - 1
        elif move == "d":
            dels, i = dels + 1, i - 1
        else:
            inss, j = inss + 1, j - 1
    return subs, dels, inss


def word_error_rate(reference: str, hypothesis: str) -> dict[str, float | int]:
    """WER of ``hypothesis`` against ``reference``.

    Returns the rate *and* its three components. The breakdown matters: a route that
    deletes half the audio and one that hallucinates an extra half both score ~0.5, and
    only the components say which failure you are looking at.
    """
    ref_words, hyp_words = normalise(reference), normalise(hypothesis)
    if not ref_words:
        raise ValueError("reference is empty after normalisation — nothing to score against")
    subs, dels, inss = edit_distance(ref_words, hyp_words)
    return {
        "wer": (subs + dels + inss) / len(ref_words),
        "substitutions": subs,
        "deletions": dels,
        "insertions": inss,
        "reference_words": len(ref_words),
        "hypothesis_words": len(hyp_words),
    }


def load_vocab(path: Path = DEFAULT_VOCAB, local: Path | None = LOCAL_VOCAB) -> list[str]:
    """Read vocabulary terms, appending the git-ignored local overlay when present."""
    terms: list[str] = []
    for source in (path, local):
        if source is None or not source.is_file():
            continue
        for line in source.read_text(encoding="utf-8").splitlines():
            term = line.strip()
            if term and not term.startswith("#"):
                terms.append(term)
    return terms


def entity_capture(reference: str, hypothesis: str, vocab: list[str]) -> dict[str, object]:
    """Fraction of vocabulary terms *spoken in the reference* that the route reproduced.

    The denominator is the reference, never the whole vocabulary file. Scoring against
    the file would make the metric improve every time someone adds a term nobody says,
    which is the opposite of a measurement.
    """
    ref_norm, hyp_norm = " ".join(normalise(reference)), " ".join(normalise(hypothesis))

    spoken, captured = [], []
    for term in vocab:
        needle = " ".join(normalise(term))
        if not needle:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(needle)}(?!\w)")
        if pattern.search(ref_norm):
            spoken.append(term)
            if pattern.search(hyp_norm):
                captured.append(term)

    return {
        "capture": len(captured) / len(spoken) if spoken else None,
        "spoken": spoken,
        "captured": captured,
        "missed": [term for term in spoken if term not in captured],
    }


def read_reference(path: Path) -> str:
    """Read a hand snippet, stripping ``#`` comments.

    Raises:
        ValueError: The file is still a template, or holds no transcript lines. Both
            mean there is no reference — and a WER computed against nothing would look
            exactly like a real number in the matrix.
    """
    raw = path.read_text(encoding="utf-8")
    if _UNTRANSCRIBED in raw:
        raise ValueError(
            f"{path} is still marked '{_UNTRANSCRIBED}' — it has to be written by ear "
            "before anything can be scored against it"
        )
    body = "\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("#"))
    if not body.strip():
        raise ValueError(f"{path} contains no transcript lines")
    return body


def read_hypothesis(path: Path, window: tuple[float, float] | None = None) -> str:
    """Read a route's output — a transcript JSON, or plain text.

    Args:
        path: Transcript JSON written by the pipeline, or a ``.txt`` dump.
        window: ``(start_s, end_s)`` to score only part of a full-meeting transcript.
            A segment is included when it overlaps the window at all; clipping mid-word
            on a timestamp boundary would invent errors the route did not make.
    """
    if path.suffix != ".json":
        return path.read_text(encoding="utf-8")

    data = json.loads(path.read_text(encoding="utf-8"))
    segments = data.get("segments") or []
    if window is None:
        return data.get("text") or " ".join(str(seg.get("text", "")) for seg in segments)

    start, end = window
    inside = [
        str(seg.get("text", ""))
        for seg in segments
        if float(seg.get("t_end", 0)) > start and float(seg.get("t_start", 0)) < end
    ]
    return " ".join(inside)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="informal WER + entity capture (M2X-021)")
    parser.add_argument("--reference", type=Path, required=True, help="hand snippet")
    parser.add_argument("--hypothesis", type=Path, required=True, help="transcript JSON or text")
    parser.add_argument(
        "--window",
        type=float,
        nargs=2,
        metavar=("START", "END"),
        help="seconds; score only this slice of a full-meeting transcript",
    )
    parser.add_argument("--vocab", type=Path, default=DEFAULT_VOCAB)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    try:
        reference = read_reference(args.reference)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_NO_REFERENCE
    except OSError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    try:
        hypothesis = read_hypothesis(
            args.hypothesis, tuple(args.window) if args.window else None
        )
        scores = word_error_rate(reference, hypothesis)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    entities = entity_capture(reference, hypothesis, load_vocab(args.vocab))
    report = {"hypothesis": str(args.hypothesis), **scores, "entities": entities}

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return EXIT_OK

    capture = entities["capture"]
    print(f"{args.hypothesis}")
    print(
        f"  WER {scores['wer']:.1%}  "
        f"(sub {scores['substitutions']}  del {scores['deletions']}  ins {scores['insertions']}"
        f"  ref {scores['reference_words']} words)"
    )
    if capture is None:
        print("  entity capture: no vocabulary terms spoken in this window")
    else:
        print(
            f"  entity capture {capture:.0%}  "
            f"({len(entities['captured'])}/{len(entities['spoken'])})"
        )
        if entities["missed"]:
            print(f"  missed: {', '.join(entities['missed'])}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
