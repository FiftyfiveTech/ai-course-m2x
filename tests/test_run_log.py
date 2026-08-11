"""Tests for the run log.

Two things are pinned here on purpose:

* **The record shape.** The eleven field names are asserted literally, so widening the
  record becomes a deliberate act with a failing test attached rather than a quiet
  drift that breaks the cost report's parser.
* **The cost/token asymmetry on cache hits.** ``cost_usd`` is money spent and goes to
  zero; token counts describe the payload and stay real. That is what makes cache
  savings recoverable from the log.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from m2x.run_log import RunContext, RunLogger, RunRecord
from m2x.types import Provider, Response, Transcript, Usage

EXPECTED_FIELDS = {
    "ts",
    "phase",
    "command",
    "model_repo_id",
    "provider",
    "latency_ms",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "cached",
    "meeting_id",
    # Twelfth field, added deliberately for M2X-032 — the module docstring carries the
    # argument. This set is the gate that makes "deliberately" mean something.
    "prompt_version",
}

FIXED_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _response(*, cached: bool = False, cost_usd: float = 0.25) -> Response:
    """Build a response with realistic accounting fields."""
    return Response(
        model_repo_id="meta-llama/Llama-3.1-8B-Instruct",
        provider=Provider.GROQ,
        latency_ms=1234,
        cached=cached,
        cost_usd=cost_usd,
        text="hello",
        usage=Usage(tokens_in=11, tokens_out=7),
    )


@pytest.fixture
def logger(tmp_path: Path) -> RunLogger:
    """A logger writing to a nested path that does not exist yet."""
    return RunLogger(tmp_path / "runs" / "runs.jsonl", now=lambda: FIXED_NOW)


class TestRecordShape:
    def test_record_has_exactly_the_agreed_fields(self) -> None:
        record = RunRecord.from_result(
            _response(), RunContext(phase="phase-0", command="m2x process")
        )

        assert set(record.model_dump().keys()) == EXPECTED_FIELDS

    def test_fields_are_populated_from_result_and_context(self) -> None:
        record = RunRecord.from_result(
            _response(),
            RunContext(phase="phase-0", command="m2x process", meeting_id="mtg-001"),
            now=lambda: FIXED_NOW,
        )

        assert record.ts == FIXED_NOW.isoformat()
        assert record.phase == "phase-0"
        assert record.command == "m2x process"
        assert record.model_repo_id == "meta-llama/Llama-3.1-8B-Instruct"
        assert record.provider is Provider.GROQ
        assert record.latency_ms == 1234
        assert record.tokens_in == 11
        assert record.tokens_out == 7
        assert record.cost_usd == pytest.approx(0.25)
        assert record.cached is False
        assert record.meeting_id == "mtg-001"

    def test_prompt_version_travels_from_the_context(self) -> None:
        record = RunRecord.from_result(
            _response(),
            RunContext(phase="phase-1b", command="m2x extract", prompt_version="v1"),
        )

        assert record.prompt_version == "v1"

    def test_prompt_version_is_null_for_a_call_with_no_versioned_prompt(self) -> None:
        """Transcription has no prompt. Null is the honest answer, not a placeholder."""
        record = RunRecord.from_result(_response(), RunContext(phase="phase-0"))

        assert record.prompt_version is None

    def test_a_record_written_before_the_field_existed_still_reads(self) -> None:
        """Day-one JSONL has to keep parsing, or the cost report loses its history."""
        legacy = {
            "ts": FIXED_NOW.isoformat(),
            "phase": "phase-0",
            "command": "m2x process",
            "model_repo_id": "meta-llama/Llama-3.1-8B-Instruct",
            "provider": "groq",
            "latency_ms": 1234,
        }

        assert RunRecord.model_validate(legacy).prompt_version is None

    def test_timestamp_is_timezone_aware_utc(self) -> None:
        """A naive timestamp makes cross-machine run comparison ambiguous."""
        record = RunRecord.from_result(_response(), RunContext(), now=lambda: FIXED_NOW)

        assert datetime.fromisoformat(record.ts).tzinfo is not None

    def test_meeting_id_is_optional(self) -> None:
        record = RunRecord.from_result(_response(), RunContext())

        assert record.meeting_id is None

    def test_negative_latency_is_rejected(self) -> None:
        """Malformed records must be impossible by construction, not by discipline."""
        with pytest.raises(ValueError):
            RunRecord(
                ts=FIXED_NOW.isoformat(),
                phase="p",
                command="c",
                model_repo_id="m",
                provider=Provider.GROQ,
                latency_ms=-1,
            )

    def test_transcription_result_logs_zero_tokens(self) -> None:
        """Whisper reports no token usage; inventing one would corrupt the report."""
        transcript = Transcript(
            model_repo_id="openai/whisper-large-v3",
            provider=Provider.GROQ,
            latency_ms=5300,
            cost_usd=0.04,
            text="the meeting begins",
            audio_seconds=120.0,
        )

        record = RunRecord.from_result(transcript, RunContext())

        assert (record.tokens_in, record.tokens_out) == (0, 0)
        assert record.cost_usd == pytest.approx(0.04)


class TestCacheHitAccounting:
    def test_cache_hit_logs_zero_cost_but_real_token_counts(self) -> None:
        """The asymmetry that makes "what did the cache save us" answerable.

        Zeroing cost is correct — no money changed hands. Zeroing tokens would destroy
        the only record of how large the call was, and with it any way to price what
        the cache avoided.
        """
        record = RunRecord.from_result(
            _response(cached=True, cost_usd=0.0), RunContext()
        )

        assert record.cached is True
        assert record.cost_usd == 0.0
        assert (record.tokens_in, record.tokens_out) == (11, 7)


class TestAppendAndRead:
    def test_append_creates_missing_directories(self, logger: RunLogger) -> None:
        """`data/` is git-ignored, so a fresh clone has no runs directory at all."""
        assert not logger.path.parent.exists()

        logger.log_result(_response(), RunContext())

        assert logger.path.exists()

    def test_each_call_appends_exactly_one_line(self, logger: RunLogger) -> None:
        for _ in range(3):
            logger.log_result(_response(), RunContext())

        lines = logger.path.read_text(encoding="utf-8").strip().splitlines()

        assert len(lines) == 3
        for line in lines:
            assert set(json.loads(line).keys()) == EXPECTED_FIELDS

    def test_provider_serialises_as_a_plain_string(self, logger: RunLogger) -> None:
        """The log is read by tooling that should not need our enum."""
        logger.log_result(_response(), RunContext())

        payload = json.loads(logger.path.read_text(encoding="utf-8").strip())

        assert payload["provider"] == "groq"

    def test_read_all_round_trips(self, logger: RunLogger) -> None:
        logger.log_result(_response(), RunContext(phase="phase-0", command="a"))
        logger.log_result(_response(), RunContext(phase="phase-1", command="b"))

        records = logger.read_all()

        assert [r.phase for r in records] == ["phase-0", "phase-1"]

    def test_read_all_on_missing_log_is_empty_not_an_error(self, logger: RunLogger) -> None:
        """The normal state of a fresh clone."""
        assert logger.read_all() == []

    def test_blank_lines_are_tolerated(self, logger: RunLogger) -> None:
        logger.log_result(_response(), RunContext())
        with logger.path.open("a", encoding="utf-8") as handle:
            handle.write("\n")

        assert len(logger.read_all()) == 1

    def test_corrupt_line_is_loud(self, logger: RunLogger) -> None:
        """Unlike the cache, a damaged log must never pass silently."""
        logger.log_result(_response(), RunContext())
        with logger.path.open("a", encoding="utf-8") as handle:
            handle.write("{not json\n")

        with pytest.raises(ValueError, match="not a valid run record"):
            logger.read_all()

    def test_usage_override_wins(self, logger: RunLogger) -> None:
        record = logger.log_result(
            _response(), RunContext(), usage=Usage(tokens_in=1, tokens_out=2)
        )

        assert (record.tokens_in, record.tokens_out) == (1, 2)
