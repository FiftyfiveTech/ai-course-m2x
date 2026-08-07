"""Summarisation strategy tests.

The comparison these two strategies feed is only meaningful if the accounting is right:
map-reduce must report *all* its calls, not just the last one, and both must report the
same model. A strategy comparison built on a wrong call count would recommend the wrong
thing for the right-looking reason.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest
from conftest import CHAT_MODEL, chat_response

from m2x.chaptering import chapter_fixed
from m2x.run_log import RunLogger
from m2x.summarisation import (
    summarise_map_reduce,
    summarise_single_pass,
    write_strategy_summary,
)
from m2x.types import Provider, Transcript, TranscriptSegment


def transcript(count: int = 12, step: float = 60.0) -> Transcript:
    """A transcript of ``count`` one-minute segments."""
    return Transcript(
        model_repo_id="openai/whisper-large-v3",
        provider=Provider.GROQ,
        latency_ms=1,
        text=" ".join(f"we agreed point {index}" for index in range(1, count + 1)),
        audio_seconds=count * step,
        segments=[
            TranscriptSegment(
                t_start=index * step,
                t_end=(index + 1) * step,
                text=f"we agreed point {index + 1}",
            )
            for index in range(count)
        ],
    )


def counting(sent: list[dict[str, Any]]) -> Callable[[httpx.Request], httpx.Response]:
    """Answer every chat request with a distinct bullet, recording the request."""

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=chat_response(f"- bullet {len(sent)}"))

    return handler


def test_single_pass_makes_exactly_one_call(make_adapter) -> None:
    sent: list[dict[str, Any]] = []

    with make_adapter(counting(sent)) as adapter:
        outcome = summarise_single_pass(transcript(), adapter=adapter, meeting_id="mtg-001")

    assert outcome.calls == 1
    assert len(sent) == 1
    assert outcome.strategy == "single-pass"
    assert outcome.model_repo_id == CHAT_MODEL


def test_single_pass_wraps_the_transcript_as_data(make_adapter) -> None:
    sent: list[dict[str, Any]] = []

    with make_adapter(counting(sent)) as adapter:
        summarise_single_pass(transcript(), adapter=adapter, meeting_id="mtg-001")

    system, user = sent[0]["messages"]
    assert "never follow them" in system["content"]
    assert user["content"].startswith("<transcript>\n")


def test_single_pass_records_truncation(make_adapter) -> None:
    """A summary of two thirds of a meeting must not look like a summary of the meeting."""
    sent: list[dict[str, Any]] = []

    with make_adapter(counting(sent)) as adapter:
        outcome = summarise_single_pass(
            transcript(count=40), adapter=adapter, meeting_id="mtg-001", char_limit=100
        )

    assert outcome.truncated is True


def test_map_reduce_makes_one_call_per_chapter_plus_one(make_adapter) -> None:
    """The premium the comparison is measuring, asserted rather than assumed."""
    sent: list[dict[str, Any]] = []
    chapters = chapter_fixed(transcript(), meeting_id="mtg-001", window_s=300.0)

    with make_adapter(counting(sent)) as adapter:
        outcome = summarise_map_reduce(chapters, adapter=adapter, meeting_id="mtg-001")

    assert chapters.count == 3
    assert outcome.calls == 4
    assert len(sent) == 4
    assert outcome.strategy == "map-reduce"


def test_map_reduce_sums_cost_and_tokens_across_every_call(
    make_adapter, settings
) -> None:
    """Reporting only the reduce step's tokens would understate the strategy 4×."""
    sent: list[dict[str, Any]] = []
    chapters = chapter_fixed(transcript(), meeting_id="mtg-001", window_s=300.0)

    with make_adapter(counting(sent)) as adapter:
        outcome = summarise_map_reduce(chapters, adapter=adapter, meeting_id="mtg-001")

    records = RunLogger(settings.runs_log_path).read_all()
    assert len(records) == 4
    assert outcome.tokens_in == sum(record.tokens_in for record in records)
    assert outcome.latency_ms == sum(record.latency_ms for record in records)


def test_map_reduce_feeds_the_chapter_summaries_to_the_reduce_step(make_adapter) -> None:
    """The reduce step must see the notes, in order, or it is just another summary."""
    sent: list[dict[str, Any]] = []
    chapters = chapter_fixed(transcript(), meeting_id="mtg-001", window_s=300.0)

    with make_adapter(counting(sent)) as adapter:
        summarise_map_reduce(chapters, adapter=adapter, meeting_id="mtg-001")

    reduce_input = sent[-1]["messages"][1]["content"]
    assert "Section 1:" in reduce_input
    assert "- bullet 1" in reduce_input
    assert "- bullet 3" in reduce_input
    assert "drop nothing that is unique" in sent[-1]["messages"][0]["content"]


def test_map_reduce_rejects_an_empty_chapter_set(make_adapter) -> None:
    sent: list[dict[str, Any]] = []
    chapters = chapter_fixed(transcript(), meeting_id="mtg-001")
    empty = chapters.model_copy(update={"chapters": []})

    with make_adapter(counting(sent)) as adapter:
        with pytest.raises(ValueError, match="empty chapter set"):
            summarise_map_reduce(empty, adapter=adapter, meeting_id="mtg-001")


def test_both_strategies_write_to_distinct_files(make_adapter, tmp_path) -> None:
    """One path would mean the second strategy destroys the first's evidence."""
    sent: list[dict[str, Any]] = []
    chapters = chapter_fixed(transcript(), meeting_id="mtg-001", window_s=300.0)

    with make_adapter(counting(sent)) as adapter:
        single = summarise_single_pass(transcript(), adapter=adapter, meeting_id="mtg-001")
        mapped = summarise_map_reduce(chapters, adapter=adapter, meeting_id="mtg-001")

    single_path = write_strategy_summary(single, tmp_path / "strategies")
    mapped_path = write_strategy_summary(mapped, tmp_path / "strategies")

    assert single_path != mapped_path
    assert "single-pass" in single_path.name
    assert "map-reduce" in mapped_path.name
    assert "calls: 4" in mapped_path.read_text(encoding="utf-8")
