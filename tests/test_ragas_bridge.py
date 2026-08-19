"""Tests for the RAGAS-to-`ModelAdapter` bridge.

These exist because the first live `m2x eval rag` run failed on 8 of 22 judged questions
with ``'AdapterRagasLLM' object has no attribute 'generate'``, and **nothing in the unit
suite could have caught it**: the harness takes its judge as an injected callable, so no
RAGAS code ran until the real command did.

The lesson is structural, not a one-off — duck typing against a third-party base class
means re-implementing everything that library's callers reach for, which is not the same
set as the methods the base declares abstract. So the load-bearing test here is a
*surface* test: whatever public callables `BaseRagasLLM` exposes, this class must expose
too. It fails the next time RAGAS adds an entry point rather than the next time someone
runs the gate.

Skipped when the optional ``ragas`` group is absent, which is the same reason the rest of
the harness is testable without it.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable

import httpx
import pytest

from conftest import chat_response
from m2x.adapter import ModelAdapter

ragas_base = pytest.importorskip(
    "ragas.llms.base", reason="optional 'ragas' dependency group is not installed"
)

# Below the skip on purpose: `m2x.ragas_bridge` imports langchain_core at module scope, so
# importing it above would raise before the skip could fire and take the whole collection
# down on a default `uv sync`. `m2x.cli` reaches for it inside the command for the same
# reason.
from m2x.ragas_bridge import JUDGE_MAX_TOKENS, AdapterRagasLLM  # noqa: E402

MODEL = "meta-llama/Llama-3.1-8B-Instruct"


class _Prompt:
    """The only part of a RAGAS ``PromptValue`` the bridge touches."""

    def __init__(self, text: str = "judge this") -> None:
        self._text = text

    def to_string(self) -> str:
        """Render the prompt.

        Returns:
            The prompt text.
        """
        return self._text


def _handler(reply: str = "verdict") -> Callable[[httpx.Request], httpx.Response]:
    """Mock transport returning one fixed completion.

    Args:
        reply: Completion text.

    Returns:
        The handler.
    """

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat_response(reply))

    return handle


def test_the_bridge_covers_every_public_entry_point_ragas_exposes() -> None:
    """The regression that cost a live run.

    An earlier version implemented the three methods `BaseRagasLLM` declares abstract and
    omitted `generate`, its async wrapper — which is what the metrics actually call. This
    compares surfaces rather than guessing which ones matter.
    """
    required = {
        name
        for name, value in inspect.getmembers(ragas_base.BaseRagasLLM, callable)
        if not name.startswith("_")
    }
    missing = {name for name in required if not hasattr(AdapterRagasLLM, name)}

    assert not missing, f"RAGAS can call these and the bridge does not implement them: {sorted(missing)}"


def test_a_judge_call_goes_through_the_adapter(
    make_adapter: Callable[..., ModelAdapter]
) -> None:
    """The project's oldest rule: a direct provider call makes the cost report lie.

    Two judge calls per question over thirty questions is the largest language-model
    workload in Phase 2, and it is exactly the spend that must not vanish from the log.
    """
    adapter = make_adapter(_handler("supported"))
    judge = AdapterRagasLLM(adapter, model_repo_id=MODEL)

    result = judge.generate_text(_Prompt())

    assert result.generations[0][0].text == "supported"
    assert judge.calls == 1


def test_generate_delegates_to_the_same_path_as_generate_text(
    make_adapter: Callable[..., ModelAdapter]
) -> None:
    """`generate` must not become a second implementation that drifts from the first."""
    import asyncio

    adapter = make_adapter(_handler("supported"))
    judge = AdapterRagasLLM(adapter, model_repo_id=MODEL)

    result = asyncio.run(judge.generate(_Prompt()))

    assert result.generations[0][0].text == "supported"
    assert judge.calls == 1


def test_self_consistency_sampling_is_refused_rather_than_faked(
    make_adapter: Callable[..., ModelAdapter]
) -> None:
    """`n > 1` asks for independent samples, which temperature 0 cannot produce.

    Returning one completion n times would read to RAGAS as unanimous agreement between
    judges that were never consulted — a confident number built from one opinion.
    """
    judge = AdapterRagasLLM(make_adapter(_handler()), model_repo_id=MODEL)

    with pytest.raises(ValueError, match="unanimous agreement"):
        judge.generate_text(_Prompt(), n=3)


def test_the_judge_call_is_capped(make_adapter: Callable[..., ModelAdapter]) -> None:
    """Faithfulness emits a verdict per statement, so its reply outgrows the answer.

    Uncapped, one runaway generation cost M2X-036 six minutes on a single case.
    """
    seen: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, json=chat_response("verdict"))

    AdapterRagasLLM(make_adapter(handle), model_repo_id=MODEL).generate_text(_Prompt())

    assert seen.get("max_tokens") == JUDGE_MAX_TOKENS


def test_run_config_is_accepted_and_ignored(
    make_adapter: Callable[..., ModelAdapter]
) -> None:
    """Retries belong to the adapter.

    Letting RAGAS layer its own on top would double the call count invisibly, which is the
    direction that makes a cost report wrong rather than merely imprecise.
    """
    judge = AdapterRagasLLM(make_adapter(_handler()), model_repo_id=MODEL)

    assert judge.set_run_config(object()) is None
