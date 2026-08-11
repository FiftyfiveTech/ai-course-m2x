"""Append-only JSONL log of every model call.

This is the project's measuring stick, and the reason the adapter is the only path to
a provider: a call that bypasses the adapter is not logged, and an unlogged call makes
the cost report a lie. The log is the input to the cost-attribution report, which
replays these records against the registry price table.

The record shape below is fixed by agreement — twelve fields, no more. Adding a field
is a decision to be made deliberately, not a convenience: downstream tooling reads
positionally-stable JSONL and the report has to be able to parse records written on
day one.

``prompt_version`` is the twelfth, added for M2X-032 and argued for rather than
assumed. Phase 1B's number is a property of a model *asked a particular way*, and the
log is what the cost and latency reports are built from — so a prompt-shaped
regression ("v3 doubled the retries") is invisible unless the version sits on the log
line and not only on the artefact. It defaults to ``None``, which keeps day-one records
parseable and is the honest value for a call made outside a versioned prompt, such as
transcription.

**Invariant worth stating explicitly.** ``cost_usd`` is money actually spent, so a
cache hit logs ``0.0``. The ``tokens_in``/``tokens_out`` counters describe the payload
and stay at their real values even for a cache hit. That asymmetry is intentional and
useful: replaying the price table over ``cached=true`` records yields exactly what the
cache saved.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from m2x.types import AdapterResult, Provider, Usage


def _utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime.

    Injected rather than called inline so tests can pin the clock and assert on exact
    record contents.
    """
    return datetime.now(UTC)


class RunContext(BaseModel):
    """Provenance for a call: which part of the pipeline made it, and about what.

    The adapter cannot infer any of this, so it is supplied by the caller — either
    once on the adapter (the common case, since a command usually stays in one phase)
    or per call for pipelines that cross phases.
    """

    model_config = ConfigDict(frozen=True)

    phase: str = "unknown"
    """Pipeline phase, e.g. ``"phase-0"``. Groups the cost report by stage of work."""

    command: str = "unknown"
    """Entry point that triggered the call, e.g. ``"m2x process"``."""

    meeting_id: str | None = None
    """Meeting this call relates to, e.g. ``"mtg-001"``. ``None`` for non-meeting work."""

    prompt_version: str | None = None
    """Prompt library version behind this call, e.g. ``"v1"``.

    Set by the step that resolved it (see :func:`m2x.extraction.extract_record`) rather
    than by the command, so the record on disk and every log line it produced quote one
    value. ``None`` where no versioned prompt was involved.
    """


class RunRecord(BaseModel):
    """One auditable line of the run log.

    Pydantic-validated before it is written, which makes a malformed record impossible
    by construction rather than by discipline.
    """

    model_config = ConfigDict(frozen=True)

    ts: str
    """ISO-8601 UTC timestamp. Stored as a string so the file needs no custom decoder."""

    phase: str
    command: str
    model_repo_id: str
    provider: Provider
    latency_ms: int = Field(ge=0)
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)
    cached: bool = False
    meeting_id: str | None = None
    prompt_version: str | None = None

    @classmethod
    def from_result(
        cls,
        result: AdapterResult,
        context: RunContext,
        *,
        usage: Usage | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> RunRecord:
        """Build a record from an adapter result.

        Args:
            result: The value the adapter is about to return.
            context: Provenance to attribute the call to.
            usage: Token counts. Falls back to ``result.usage`` when the result
                carries one, else zero.
            now: Clock, injectable for deterministic tests.

        Returns:
            A validated record ready to append.
        """
        counts = usage or getattr(result, "usage", None) or Usage()
        return cls(
            ts=now().isoformat(),
            phase=context.phase,
            command=context.command,
            model_repo_id=result.model_repo_id,
            provider=result.provider,
            latency_ms=result.latency_ms,
            tokens_in=counts.tokens_in,
            tokens_out=counts.tokens_out,
            cost_usd=result.cost_usd,
            cached=result.cached,
            meeting_id=context.meeting_id,
            prompt_version=context.prompt_version,
        )


class RunLogger:
    """Appends :class:`RunRecord` lines to a JSONL file.

    JSONL — one JSON object per line — is chosen because appending is a single write
    with no read-modify-write cycle, so concurrent writers cannot corrupt each other's
    records, and because the whole file loads into a dataframe in one call later.
    """

    def __init__(
        self,
        path: Path,
        *,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        """
        Args:
            path: Destination JSONL file. Parent directories are created on first
                write, which matters because ``data/`` is git-ignored and therefore
                absent from a fresh clone.
            now: Clock used when a record does not supply its own timestamp.
        """
        self._path = Path(path)
        self._now = now

    @property
    def path(self) -> Path:
        """Destination file."""
        return self._path

    def append(self, record: RunRecord) -> None:
        """Write one record.

        Unlike cache failures, logging failures are *not* swallowed. A run that cannot
        be measured has failed at its actual purpose, and a silently missing record
        would make the cost report quietly wrong — the worst possible outcome for an
        auditing tool.

        Args:
            record: Validated record to append.

        Raises:
            OSError: The log could not be written.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = record.model_dump_json()
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")

    def log_result(
        self,
        result: AdapterResult,
        context: RunContext,
        *,
        usage: Usage | None = None,
    ) -> RunRecord:
        """Build a record from an adapter result and append it.

        Args:
            result: Adapter return value.
            context: Provenance to attribute the call to.
            usage: Override for token counts.

        Returns:
            The record that was written, so callers can assert on it in tests.

        Raises:
            OSError: The log could not be written.
        """
        record = RunRecord.from_result(result, context, usage=usage, now=self._now)
        self.append(record)
        return record

    def read_all(self) -> list[RunRecord]:
        """Load every record currently in the log.

        Args:
            None.

        Returns:
            Records in file order. Empty list if the log does not exist yet, which is
            the normal state of a fresh clone rather than an error.

        Raises:
            ValueError: A line is present but not a valid record — that indicates the
                log has been corrupted or hand-edited, which should never pass silently.
        """
        if not self._path.exists():
            return []

        records: list[RunRecord] = []
        with self._path.open("r", encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    records.append(RunRecord.model_validate(json.loads(stripped)))
                except Exception as exc:
                    raise ValueError(
                        f"{self._path}:{lineno} is not a valid run record: {exc}"
                    ) from exc
        return records
