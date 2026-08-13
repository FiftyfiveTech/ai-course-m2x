#!/usr/bin/env python3
"""Check the RAG question set before it is trusted to grade anything.

The ticket asks for a second pass a few hours later: *can each answerable question really
be answered from the recorded segment?* Half of that is a judgement no script can make, and
it is recorded per question in the sealed `notes` field. The other half is mechanical and
belongs here — the judgement has to point at something real, and a pointer into a turn that
does not exist corrupts the gate exactly as badly as a wrong judgement does.

What it checks: ids unique and paired across both halves, the 20/5/5 mix the ticket
specifies, every answerable question carrying evidence and a gist, every unanswerable one
carrying neither, cross-meeting questions genuinely spanning two meetings, and every cited
turn range resolving against the committed reference transcripts.

Needs the expected answers **unsealed**, so it is an Evaluator command. Exits non-zero on
any problem, so a set cannot be recorded sound by reading past the output.

Usage::

    uv run python scripts/validate_rag_questions.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m2x.errors import ConfigError
from m2x.rag_questions import (
    DEFAULT_RAG_EVAL_DIR,
    EXPECTED_DIRNAME,
    QUESTIONS_FILENAME,
    QuestionKind,
    load_expected,
    load_questions,
    validate_question_set,
)
from m2x.reference_transcript import DEFAULT_REFERENCE_DIR


def main(argv: list[str] | None = None) -> int:
    """Validate the set.

    Args:
        argv: Arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 sound, 1 problems found, 2 the set could not be read.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", type=Path, default=DEFAULT_RAG_EVAL_DIR, help="root of the eval set")
    parser.add_argument(
        "--reference-dir",
        type=Path,
        default=DEFAULT_REFERENCE_DIR,
        help="where the reference transcripts live",
    )
    args = parser.parse_args(argv)

    try:
        questions = load_questions(args.dir / QUESTIONS_FILENAME)
        expected = load_expected(args.dir / EXPECTED_DIRNAME)
    except (ConfigError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    problems = validate_question_set(questions, expected, reference_dir=args.reference_dir)
    if problems:
        for problem in problems:
            print(f"FAIL {problem}", file=sys.stderr)
        return 1

    spans = sum(len(expected[question.question_id].evidence) for question in questions)
    meetings = sorted(
        {span.meeting_id for answer in expected.values() for span in answer.evidence}
    )
    by_kind = {kind: sum(1 for q in questions if q.kind is kind) for kind in QuestionKind}

    print(f"OK  {len(questions)} questions, {spans} evidence spans, every turn resolves")
    for kind, count in by_kind.items():
        print(f"    {kind.value:<16} {count}")
    print(f"    meetings covered: {len(meetings)} — {', '.join(meetings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
