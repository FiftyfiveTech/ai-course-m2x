"""Tests for run-log aggregation.

The summary is the number that ends up in gate records and, on Sunday, in the cost
report. Two properties matter more than the formatting:

* **Nothing is dropped or double-counted.** Group totals must reconstruct the overall
  totals, whatever the mix of phases, providers and models.
* **Latency describes live calls.** Cache hits return in microseconds; letting them
  into the percentiles would report the cache's speed under the provider's name.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from m2x.run_log import RunLogger, RunRecord
from m2x.run_summary import format_summary, percentile, summarise
from m2x.types import Provider

CHAT_MODEL = "meta-llama/Llama-3.1-8B-Instruct"
WHISPER_MODEL = "openai/whisper-large-v3"


def _record(
    *,
    phase: str = "phase-0",
    provider: Provider = Provider.GROQ,
    model_repo_id: str = CHAT_MODEL,
    latency_ms: int = 100,
    tokens_in: int = 10,
    tokens_out: int = 5,
    cost_usd: float = 0.0,
    cached: bool = False,
) -> RunRecord:
    """Build a record with only the aggregated fields varying."""
    return RunRecord(
        ts="2026-01-01T12:00:00+00:00",
        phase=phase,
        command="m2x process",
        model_repo_id=model_repo_id,
        provider=provider,
        latency_ms=latency_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
        cached=cached,
        meeting_id="mtg-001",
    )


class TestPercentile:
    def test_nearest_rank_returns_a_sample_that_occurred(self) -> None:
        """Never interpolates — a reported latency is always one that happened."""
        assert percentile([10, 20, 30, 40], 0.5) == 20

    def test_p95_of_a_small_sample_is_the_slowest(self) -> None:
        """With four calls, p95 has to land on the top one; anything else invents data."""
        assert percentile([10, 20, 30, 40], 0.95) == 40

    def test_single_sample_is_its_own_percentile(self) -> None:
        assert percentile([7], 0.5) == 7
        assert percentile([7], 0.95) == 7

    def test_input_order_does_not_matter(self) -> None:
        assert percentile([40, 10, 30, 20], 0.5) == percentile([10, 20, 30, 40], 0.5)

    def test_empty_sample_has_no_percentile(self) -> None:
        assert percentile([], 0.5) is None

    def test_zero_fraction_is_the_minimum(self) -> None:
        """Rank clamps to 1 rather than indexing at -1 and silently returning the max."""
        assert percentile([10, 20, 30], 0.0) == 10

    def test_fraction_outside_the_unit_interval_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="fraction must be in"):
            percentile([1], 95.0)


class TestGrouping:
    def test_calls_split_by_phase_provider_and_model(self) -> None:
        """Three routes, three rows — the report's whole purpose is per-route numbers."""
        summary = summarise(
            [
                _record(provider=Provider.GROQ),
                _record(provider=Provider.OLLAMA),
                _record(provider=Provider.GROQ, model_repo_id=WHISPER_MODEL),
                _record(provider=Provider.GROQ, phase="phase-1"),
            ]
        )

        routes = {(g.phase, g.provider, g.model_repo_id) for g in summary.groups}
        assert routes == {
            ("phase-0", Provider.GROQ, CHAT_MODEL),
            ("phase-0", Provider.OLLAMA, CHAT_MODEL),
            ("phase-0", Provider.GROQ, WHISPER_MODEL),
            ("phase-1", Provider.GROQ, CHAT_MODEL),
        }

    def test_same_route_collapses_into_one_group(self) -> None:
        summary = summarise([_record(), _record(), _record()])

        assert len(summary.groups) == 1
        assert summary.groups[0].calls == 3

    def test_group_order_is_deterministic(self) -> None:
        """Two runs over one log must print identically, or diffing gate records is noise."""
        records = [
            _record(phase="phase-1", provider=Provider.OLLAMA),
            _record(phase="phase-0", provider=Provider.NIM),
            _record(phase="phase-0", provider=Provider.GROQ),
        ]

        first = [(g.phase, g.provider.value) for g in summarise(records).groups]
        second = [(g.phase, g.provider.value) for g in summarise(list(reversed(records))).groups]

        assert first == second == [
            ("phase-0", "groq"),
            ("phase-0", "nim"),
            ("phase-1", "ollama"),
        ]

    def test_empty_log_summarises_to_zeros_not_an_error(self) -> None:
        """A fresh clone has no log; that is a state, not a failure."""
        summary = summarise([])

        assert summary.groups == []
        assert (summary.calls, summary.cost_usd) == (0, 0.0)


class TestTotals:
    def test_group_totals_reconstruct_the_overall_totals(self) -> None:
        """The invariant that makes the cost report trustworthy: nothing is lost."""
        summary = summarise(
            [
                _record(provider=Provider.GROQ, tokens_in=10, tokens_out=5, cost_usd=0.10),
                _record(provider=Provider.OLLAMA, tokens_in=20, tokens_out=7, cost_usd=0.20),
                _record(provider=Provider.GROQ, tokens_in=30, tokens_out=9, cost_usd=0.30),
            ]
        )

        assert summary.calls == sum(g.calls for g in summary.groups) == 3
        assert summary.tokens_in == sum(g.tokens_in for g in summary.groups) == 60
        assert summary.tokens_out == sum(g.tokens_out for g in summary.groups) == 21
        assert summary.cost_usd == pytest.approx(0.60)

    def test_cost_sums_a_fake_price_table_correctly(self) -> None:
        """Every real price this week is 0.00, so the arithmetic is proved on fakes —
        a report that can only ever print $0.0000 has verified nothing."""
        summary = summarise([_record(cost_usd=0.0125) for _ in range(8)])

        assert summary.cost_usd == pytest.approx(0.1)

    def test_cache_hits_count_as_calls_but_add_no_cost(self) -> None:
        summary = summarise(
            [
                _record(cached=False, cost_usd=0.25),
                _record(cached=True, cost_usd=0.0),
                _record(cached=True, cost_usd=0.0),
            ]
        )

        assert (summary.calls, summary.cached) == (3, 2)
        assert summary.cost_usd == pytest.approx(0.25)

    def test_cached_tokens_still_count(self) -> None:
        """Token counts describe the payload, so cache savings stay recoverable."""
        summary = summarise([_record(cached=True, tokens_in=100, tokens_out=50)])

        assert (summary.tokens_in, summary.tokens_out) == (100, 50)


class TestLatency:
    def test_percentiles_ignore_cache_hits(self) -> None:
        """A sub-millisecond hit must not drag the provider's median down."""
        summary = summarise(
            [
                _record(latency_ms=1000),
                _record(latency_ms=2000),
                _record(latency_ms=1, cached=True),
                _record(latency_ms=1, cached=True),
            ]
        )
        group = summary.groups[0]

        assert group.calls == 4
        assert group.cached == 2
        assert group.p50_latency_ms == 1000
        assert group.p95_latency_ms == 2000

    def test_an_all_cached_group_reports_no_percentile(self) -> None:
        """Better to print nothing than a flattering number the provider never earned."""
        summary = summarise([_record(latency_ms=1, cached=True)])
        group = summary.groups[0]

        assert group.calls == 1
        assert (group.p50_latency_ms, group.p95_latency_ms) == (None, None)

    def test_percentiles_are_per_route_not_global(self) -> None:
        """The hosted-vs-local gap is the Phase 0 finding; averaging it away hides it."""
        summary = summarise(
            [
                _record(provider=Provider.GROQ, latency_ms=700),
                _record(provider=Provider.OLLAMA, latency_ms=189_000),
            ]
        )
        by_provider = {g.provider: g.p50_latency_ms for g in summary.groups}

        assert by_provider == {Provider.GROQ: 700, Provider.OLLAMA: 189_000}


class TestFormatting:
    def test_table_carries_every_route_and_a_total_row(self) -> None:
        summary = summarise(
            [
                _record(provider=Provider.GROQ, latency_ms=700, cost_usd=0.25),
                _record(provider=Provider.OLLAMA, latency_ms=189_000),
            ]
        )
        table = format_summary(summary)

        assert "groq" in table
        assert "ollama" in table
        assert "TOTAL" in table
        assert "$0.2500" in table

    def test_missing_percentile_renders_as_a_dash(self) -> None:
        table = format_summary(summarise([_record(cached=True)]))

        assert " - " in table or table.rstrip().endswith("-")

    def test_columns_line_up(self) -> None:
        """Fixed-width because the table is read in a terminal next to the gate record."""
        summary = summarise(
            [
                _record(model_repo_id=CHAT_MODEL),
                _record(model_repo_id="x/y", provider=Provider.OLLAMA),
            ]
        )
        lines = format_summary(summary).splitlines()
        header, rule = lines[0], lines[1]

        assert len(rule) >= len(header) - 2
        assert set(rule) <= {"-", " "}

    def test_empty_log_says_so_instead_of_printing_an_empty_table(self) -> None:
        assert format_summary(summarise([])) == "no runs logged yet"


class TestReadsTheRealLog:
    def test_summarises_what_the_logger_wrote(self, tmp_path: Path) -> None:
        """End to end over a real file: the report parses what the writer produces."""
        log_path = tmp_path / "runs.jsonl"
        logger = RunLogger(log_path)
        logger.append(_record(latency_ms=700, cost_usd=0.25))
        logger.append(_record(latency_ms=900, cached=True))

        summary = summarise(RunLogger(log_path).read_all())

        assert (summary.calls, summary.cached) == (2, 1)
        assert summary.cost_usd == pytest.approx(0.25)
        assert summary.groups[0].p50_latency_ms == 700
