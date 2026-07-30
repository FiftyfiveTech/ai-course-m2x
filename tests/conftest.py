"""Shared fixtures.

Two principles hold across every test in this suite:

* **No network.** The adapter's HTTP client is backed by :class:`httpx.MockTransport`,
  so tests assert on the exact request that would have gone out.
* **No real clocks.** Time and sleep are injected. Retry tests therefore complete
  instantly instead of actually backing off, and latency assertions are exact rather
  than "greater than zero".
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterator

import httpx
import pytest

from m2x.adapter import ModelAdapter
from m2x.model_registry import ModelRegistry
from m2x.run_log import RunContext
from m2x.settings import Settings

FIXED_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
"""Pinned wall clock, so run-log timestamps are assertable."""

CHAT_MODEL = "meta-llama/Llama-3.1-8B-Instruct"
TRANSCRIBE_MODEL = "openai/whisper-large-v3"

REGISTRY_DATA: dict[str, Any] = {
    "providers": {
        "groq": {
            "base_url": "https://groq.test/openai/v1",
            "api_key_setting": "groq_api_key",
        },
        "nim": {
            "base_url": "https://nim.test/v1",
            "api_key_setting": "nvidia_api_key",
        },
        # No credential: exercises the unauthenticated local path.
        "ollama": {"base_url": "http://localhost:11434/v1", "api_key_setting": ""},
    },
    "models": {
        CHAT_MODEL: {
            "kind": "chat",
            "default_provider": "groq",
            "served_as": {
                "groq": "llama-3.1-8b-instant",
                "nim": "meta/llama-3.1-8b-instruct",
                "ollama": "hf.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF",
            },
            "price_per_1k_tokens_in": 0.0,
            "price_per_1k_tokens_out": 0.0,
        },
        TRANSCRIBE_MODEL: {
            "kind": "transcribe",
            # Only Groq serves this, which makes it the natural subject for
            # "provider cannot serve this model" assertions.
            "default_provider": "groq",
            "served_as": {"groq": "whisper-large-v3"},
            "price_per_audio_minute": 0.0,
        },
    },
}


class FakeClock:
    """Monotonic clock that advances a fixed amount per reading.

    The adapter reads the clock twice per timed operation, so a 1 ms step makes every
    measured latency exactly 1 ms — deterministic enough to assert on directly.
    """

    def __init__(self, step_s: float = 0.001) -> None:
        self._step_s = step_s
        self._value = 0.0

    def __call__(self) -> float:
        """Return the current value, then advance."""
        current = self._value
        self._value += self._step_s
        return current


@pytest.fixture
def registry() -> ModelRegistry:
    """A two-model registry covering chat and transcription."""
    return ModelRegistry.from_mapping(REGISTRY_DATA)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings pointing at throwaway paths, with credentials present.

    ``_env_file=None`` is essential: without it a developer's real `.env` would leak
    into the test run and make results machine-dependent.
    """
    return Settings(
        _env_file=None,
        groq_api_key="test-groq-key",
        nvidia_api_key="test-nim-key",
        cache_dir=tmp_path / "cache",
        runs_log_path=tmp_path / "runs" / "runs.jsonl",
        retry_base_delay_s=0.0,
    )


@pytest.fixture
def sleep_calls() -> list[float]:
    """Collects every backoff delay the adapter asked for."""
    return []


@pytest.fixture
def make_adapter(
    settings: Settings,
    registry: ModelRegistry,
    sleep_calls: list[float],
) -> Iterator[Callable[..., ModelAdapter]]:
    """Factory building adapters wired to a mock transport.

    Yields:
        A callable taking an httpx handler plus optional overrides for ``settings``
        and ``context``, and returning a ready adapter. Every adapter built is closed
        when the test ends.
    """
    built: list[ModelAdapter] = []

    def _build(
        handler: Callable[[httpx.Request], httpx.Response],
        *,
        settings_override: Settings | None = None,
        context: RunContext | None = None,
    ) -> ModelAdapter:
        adapter = ModelAdapter(
            settings=settings_override or settings,
            registry=registry,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            context=context or RunContext(phase="test", command="pytest"),
            sleep=sleep_calls.append,
            monotonic=FakeClock(),
            now=lambda: FIXED_NOW,
        )
        built.append(adapter)
        return adapter

    yield _build

    for adapter in built:
        adapter.close()


def chat_response(
    text: str = "hello",
    *,
    tokens_in: int = 11,
    tokens_out: int = 7,
    finish_reason: str = "stop",
) -> dict[str, Any]:
    """Build an OpenAI-compatible chat completion payload.

    Args:
        text: Assistant content to return.
        tokens_in: Prompt token count to report.
        tokens_out: Completion token count to report.
        finish_reason: Stop reason to report.

    Returns:
        A JSON-serialisable response body.
    """
    return {
        "choices": [
            {"message": {"role": "assistant", "content": text}, "finish_reason": finish_reason}
        ],
        "usage": {"prompt_tokens": tokens_in, "completion_tokens": tokens_out},
    }


def transcription_response(
    text: str = "the meeting begins",
    *,
    duration: float = 120.0,
) -> dict[str, Any]:
    """Build a ``verbose_json`` transcription payload.

    Includes one segment missing its ``end`` timestamp, so every test that parses this
    body also proves incomplete segments are dropped rather than defaulted to zero.

    Args:
        text: Full transcript text.
        duration: Audio duration in seconds.

    Returns:
        A JSON-serialisable response body.
    """
    return {
        "text": text,
        "duration": duration,
        "language": "en",
        "segments": [
            {"start": 0.0, "end": 2.5, "text": "the meeting"},
            {"start": 2.5, "end": 4.0, "text": " begins"},
            {"start": 4.0, "text": " dropped: no end timestamp"},
        ],
    }
