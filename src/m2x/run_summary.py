"""Aggregation over the run log.

The log answers "what did every call do"; this module answers "what did the week
cost, and how slow was each route". It is deliberately a separate module from
:mod:`m2x.run_log`: writing is on the hot path of every call and must stay trivial,
while reading is a reporting concern that will keep growing (M2X-075 builds the cost
report on top of this).

**Latency is measured over live calls only.** A cache hit returns in under a
millisecond, so mixing hits into the percentiles would report the cache's speed while
appearing to report the provider's. The ``calls``/``cached`` columns stay visible so a
reader can see how much of the work the percentiles are actually describing; a group
whose calls were all cache hits reports no percentile rather than a flattering one.
"""

from __future__ import annotations

import math
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from m2x.run_log import RunRecord
from m2x.types import Provider

DEFAULT_RUN_LOG = Path("data/runs/runs.jsonl")
"""Where :class:`m2x.run_log.RunLogger` writes by default."""


def percentile(values: list[int], fraction: float) -> int | None:
    """Return the nearest-rank percentile of ``values``.

    Nearest-rank rather than an interpolating method: every value returned is a
    latency that genuinely occurred, which matters when the number ends up in a gate
    record that someone may try to reproduce. With the handful of calls a phase
    produces, interpolation would also invent precision the sample cannot support.

    Args:
        values: Samples, in any order. Not mutated.
        fraction: Percentile to take, in ``[0, 1]`` — ``0.95`` for p95.

    Returns:
        The selected sample, or ``None`` when ``values`` is empty.

    Raises:
        ValueError: ``fraction`` is outside ``[0, 1]``.
    """
    if not 0.0 <= fraction <= 1.0:
        raise ValueError(f"fraction must be in [0, 1], got {fraction}")
    if not values:
        return None

    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


class GroupStats(BaseModel):
    """Aggregated numbers for one (phase, provider, model) route."""

    model_config = ConfigDict(frozen=True)

    phase: str
    provider: Provider
    model_repo_id: str

    calls: int
    """Records in this group, cache hits included."""

    cached: int
    """How many of ``calls`` were served from cache."""

    tokens_in: int
    tokens_out: int

    cost_usd: float
    """Money actually spent — cache hits contribute 0.0 by the log's invariant."""

    p50_latency_ms: int | None
    """Median latency over live calls; ``None`` when every call was cached."""

    p95_latency_ms: int | None
    """95th-percentile latency over live calls; ``None`` when every call was cached."""


class RunSummary(BaseModel):
    """Per-route breakdown plus the totals across every route."""

    model_config = ConfigDict(frozen=True)

    groups: list[GroupStats]
    """Sorted by phase, then provider, then model, so two runs of the report over the
    same log produce byte-identical output."""

    calls: int
    cached: int
    tokens_in: int
    tokens_out: int
    cost_usd: float


def summarise(records: list[RunRecord]) -> RunSummary:
    """Aggregate run records by (phase, provider, model).

    Args:
        records: Records to aggregate, typically from
            :meth:`m2x.run_log.RunLogger.read_all`.

    Returns:
        A summary whose ``groups`` are empty when ``records`` is empty — an empty log
        is the normal state of a fresh clone, not an error.
    """
    buckets: dict[tuple[str, Provider, str], list[RunRecord]] = {}
    for record in records:
        key = (record.phase, record.provider, record.model_repo_id)
        buckets.setdefault(key, []).append(record)

    groups: list[GroupStats] = []
    for (phase, provider, model_repo_id), grouped in sorted(
        buckets.items(), key=lambda item: (item[0][0], item[0][1].value, item[0][2])
    ):
        live = [record.latency_ms for record in grouped if not record.cached]
        groups.append(
            GroupStats(
                phase=phase,
                provider=provider,
                model_repo_id=model_repo_id,
                calls=len(grouped),
                cached=sum(1 for record in grouped if record.cached),
                tokens_in=sum(record.tokens_in for record in grouped),
                tokens_out=sum(record.tokens_out for record in grouped),
                cost_usd=sum(record.cost_usd for record in grouped),
                p50_latency_ms=percentile(live, 0.50),
                p95_latency_ms=percentile(live, 0.95),
            )
        )

    return RunSummary(
        groups=groups,
        calls=len(records),
        cached=sum(1 for record in records if record.cached),
        tokens_in=sum(record.tokens_in for record in records),
        tokens_out=sum(record.tokens_out for record in records),
        cost_usd=sum(record.cost_usd for record in records),
    )


_COLUMNS = ("phase", "provider", "model", "calls", "cached", "tok in", "tok out", "cost", "p50 ms", "p95 ms")


def format_summary(summary: RunSummary) -> str:
    """Render a summary as a fixed-width table.

    Args:
        summary: Aggregated records.

    Returns:
        Text to print. A log with no records renders a single explanatory line rather
        than an empty table, since "no calls yet" and "the report is broken" should not
        look the same.
    """
    if not summary.groups:
        return "no runs logged yet"

    rows = [
        (
            group.phase,
            group.provider.value,
            group.model_repo_id,
            str(group.calls),
            str(group.cached),
            str(group.tokens_in),
            str(group.tokens_out),
            f"${group.cost_usd:.4f}",
            _optional(group.p50_latency_ms),
            _optional(group.p95_latency_ms),
        )
        for group in summary.groups
    ]
    total = (
        "TOTAL",
        "",
        "",
        str(summary.calls),
        str(summary.cached),
        str(summary.tokens_in),
        str(summary.tokens_out),
        f"${summary.cost_usd:.4f}",
        "",
        "",
    )

    widths = [
        max(len(cell) for cell in column)
        for column in zip(_COLUMNS, *rows, total)
    ]
    lines = [
        _row(_COLUMNS, widths),
        "  ".join("-" * width for width in widths),
        *(_row(row, widths) for row in rows),
        "  ".join("-" * width for width in widths),
        _row(total, widths),
    ]
    return "\n".join(lines)


def _optional(value: int | None) -> str:
    """Render a percentile that may not exist."""
    return "-" if value is None else str(value)


def _row(cells: tuple[str, ...], widths: list[int]) -> str:
    """Pad one row's cells to the column widths."""
    return "  ".join(cell.ljust(width) for cell, width in zip(cells, widths)).rstrip()
