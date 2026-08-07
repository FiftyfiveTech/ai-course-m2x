"""Chaptering tests.

The fixed strategy is arithmetic and is tested exactly. The LLM strategy is tested for
what it does with a *bad* answer — a boundary past the end of the transcript, a boundary
in text the detector never saw — because that is the behaviour that decides whether a
chaptering can be trusted.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest
from conftest import CHAT_MODEL, chat_response

from m2x.chaptering import (
    SEGMENT_PREVIEW_CHARS,
    chapter_fixed,
    chapter_llm,
    load_chapters,
    write_chapters,
)
from m2x.types import Provider, Transcript, TranscriptSegment


def transcript(count: int = 12, step: float = 60.0) -> Transcript:
    """A transcript of ``count`` one-minute segments."""
    return Transcript(
        model_repo_id="openai/whisper-large-v3",
        provider=Provider.GROQ,
        latency_ms=1,
        text=" ".join(f"segment {index}" for index in range(1, count + 1)),
        audio_seconds=count * step,
        segments=[
            TranscriptSegment(
                t_start=index * step,
                t_end=(index + 1) * step,
                text=f"segment {index + 1} talking about something",
            )
            for index in range(count)
        ],
    )


def replying(text: str, sent: list[dict[str, Any]]) -> Callable[[httpx.Request], httpx.Response]:
    """Answer any chat request with ``text``, recording the request body."""

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=chat_response(text))

    return handler


def test_fixed_cuts_on_segment_boundaries_at_each_window() -> None:
    """12 one-minute segments, 5-minute windows: cuts at minute 5 and minute 10."""
    chapters = chapter_fixed(transcript(), meeting_id="mtg-001", window_s=300.0)

    assert chapters.count == 3
    assert [chapter.first_segment for chapter in chapters.chapters] == [1, 6, 11]
    assert [chapter.last_segment for chapter in chapters.chapters] == [5, 10, 12]
    assert chapters.chapters[0].t_start == 0.0
    assert chapters.chapters[-1].t_end == 720.0


def test_fixed_costs_nothing_and_names_no_model() -> None:
    """The cheap strategy has to be visibly cheap, or the comparison is meaningless."""
    chapters = chapter_fixed(transcript(), meeting_id="mtg-001")

    assert chapters.cost_usd == 0.0
    assert chapters.latency_ms == 0
    assert chapters.model_repo_id is None


def test_fixed_covers_every_segment_exactly_once() -> None:
    """No gaps and no overlaps: a segment in two chapters is summarised twice."""
    chapters = chapter_fixed(transcript(count=25, step=45.0), meeting_id="mtg-001")

    covered: list[int] = []
    for chapter in chapters.chapters:
        covered.extend(range(chapter.first_segment, chapter.last_segment + 1))

    assert covered == list(range(1, 26))


def test_fixed_on_a_transcript_shorter_than_one_window_is_one_chapter() -> None:
    chapters = chapter_fixed(transcript(count=3), meeting_id="mtg-001", window_s=600.0)

    assert chapters.count == 1


def test_fixed_rejects_an_empty_transcript() -> None:
    empty = transcript(count=0)

    with pytest.raises(ValueError, match="no segments"):
        chapter_fixed(empty, meeting_id="mtg-001")


def test_llm_builds_chapters_from_the_returned_boundaries(make_adapter) -> None:
    sent: list[dict[str, Any]] = []

    with make_adapter(replying("4, 9", sent)) as adapter:
        chapters = chapter_llm(transcript(), adapter=adapter, meeting_id="mtg-001")

    assert [chapter.first_segment for chapter in chapters.chapters] == [1, 4, 9]
    assert chapters.model_repo_id == CHAT_MODEL
    assert chapters.provider is Provider.GROQ


def test_llm_drops_boundaries_that_do_not_resolve(make_adapter) -> None:
    """A boundary past the end is the model telling you it guessed. Do not repair it."""
    sent: list[dict[str, Any]] = []

    with make_adapter(replying("4, 999, 1, 7", sent)) as adapter:
        chapters = chapter_llm(transcript(), adapter=adapter, meeting_id="mtg-001")

    assert [chapter.first_segment for chapter in chapters.chapters] == [1, 4, 7]


def test_llm_sends_a_previewed_outline_covering_every_segment(make_adapter) -> None:
    """The defect this guards: a truncated outline leaves the tail unmarkable."""
    sent: list[dict[str, Any]] = []
    long_transcript = transcript(count=40)

    with make_adapter(replying("5", sent)) as adapter:
        chapter_llm(long_transcript, adapter=adapter, meeting_id="mtg-001")

    outline = sent[0]["messages"][1]["content"]
    assert outline.startswith("1. ")
    assert "\n40. " in outline
    assert all(len(line.split(". ", 1)[1]) <= SEGMENT_PREVIEW_CHARS for line in outline.splitlines())


def test_llm_ignores_boundaries_past_a_truncated_outline(make_adapter) -> None:
    """If the budget cut the outline, a number past the cut refers to unseen text."""
    sent: list[dict[str, Any]] = []

    with make_adapter(replying("3, 30", sent)) as adapter:
        chapters = chapter_llm(
            transcript(count=40), adapter=adapter, meeting_id="mtg-001", char_limit=120
        )

    seen = len(sent[0]["messages"][1]["content"].splitlines())
    assert seen < 40
    assert all(chapter.first_segment <= seen for chapter in chapters.chapters)


def test_llm_survives_a_reply_with_no_usable_numbers(make_adapter) -> None:
    """One chapter is a defensible answer; a crash in the middle of a comparison is not."""
    sent: list[dict[str, Any]] = []

    with make_adapter(replying("I could not find any topics.", sent)) as adapter:
        chapters = chapter_llm(transcript(), adapter=adapter, meeting_id="mtg-001")

    assert chapters.count == 1


def test_chapters_round_trip_through_disk(tmp_path) -> None:
    chapters = chapter_fixed(transcript(), meeting_id="mtg-001")

    path = write_chapters(chapters, tmp_path / "chapters")

    assert path.name == "mtg-001.fixed.json"
    assert load_chapters(path) == chapters


def test_strategy_is_in_the_filename(tmp_path) -> None:
    """Running both strategies must not have the second destroy the first's evidence."""
    fixed = chapter_fixed(transcript(), meeting_id="mtg-001")
    written = write_chapters(fixed, tmp_path / "chapters")

    assert written.name != "mtg-001.json"
    assert "fixed" in written.name
