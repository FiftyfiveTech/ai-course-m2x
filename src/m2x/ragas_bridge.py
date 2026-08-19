"""RAGAS, driven through `ModelAdapter` instead of around it.

RAGAS wants to own its own LLM client. The project's oldest rule says otherwise: *every
model call goes through `ModelAdapter`*, because a direct call is invisible to the run log
and makes the cost report lie. Context precision and faithfulness are two judge calls per
question over thirty questions — comfortably the largest language-model workload in Phase
2, and exactly the spend that must not vanish.

So :class:`AdapterRagasLLM` implements RAGAS's `BaseRagasLLM` over
:meth:`~m2x.adapter.ModelAdapter.complete`. Every judge call is cached, logged, costed and
attributed to `phase-2` like any other.

Kept in its own module so :mod:`m2x.eval_rag` imports cleanly without RAGAS installed. The
harness takes its judge as a callable, so the metrics, the citation checker and the whole
report are testable with no judge model and no optional dependency present — which is why
the sanity cases in `tests/test_eval_rag.py` run on a fresh clone.

## Two things RAGAS does that need watching

**It is async and it nests.** RAGAS's metric API is `async`, and its internals use
`nest_asyncio`. The adapter is synchronous, so `agenerate_text` simply delegates to the
sync path: there is no concurrency to gain — the adapter serialises anyway — and pretending
otherwise would put a thread pool between the call and the run log.

**It asks for a temperature.** RAGAS passes `temperature=0.01` by default. The adapter does
not expose one and the project pins 0.0 everywhere, so the argument is accepted and
dropped. Recorded here rather than silently ignored: a judge at 0.01 and a judge at 0.0 are
not quite the same judge, and if a judged number ever fails to reproduce this is the first
place to look.
"""

from __future__ import annotations

import typing as t

from langchain_core.outputs import Generation, LLMResult

from m2x.adapter import ModelAdapter
from m2x.ask import AskOutcome
from m2x.rag_questions import ExpectedAnswer
from m2x.run_log import RunContext
from m2x.types import Message, Provider, Role

JUDGE_MAX_TOKENS = 1024
"""Output cap per judge call.

Faithfulness decomposes an answer into statements and emits a verdict per statement, so its
reply is longer than the answer it judges. 1024 is comfortably above the longest reply seen
on the thirty-question set and well under the runaway ceiling that cost M2X-036 six minutes
on one case.
"""


class AdapterRagasLLM:
    """RAGAS's LLM interface, served by :class:`~m2x.adapter.ModelAdapter`.

    Deliberately not a subclass of ``BaseRagasLLM``: RAGAS's base is a Pydantic-flavoured
    dataclass whose ``__init__`` signature moves between releases, and inheriting from it
    would couple this module to a version.

    **That choice has a cost, and the first live run collected it.** An earlier version of
    this class implemented only ``generate_text``/``agenerate_text``/``is_finished`` on the
    reasoning that RAGAS called nothing else. It calls ``generate`` — the base class's
    async wrapper — and the run failed on 8 of 22 judged questions with
    ``'AdapterRagasLLM' object has no attribute 'generate'``. Duck typing against a base
    class means re-implementing everything RAGAS's metrics reach for, not everything the
    base class declares abstract, and the two sets are different.

    Recorded rather than quietly fixed because it generalises: the unit tests could not
    have caught it. They inject a judge callable, so no RAGAS code ran until the live
    command did. **A wrapper around a third-party interface is only tested by the third
    party.**
    """

    def __init__(
        self,
        adapter: ModelAdapter,
        *,
        model_repo_id: str,
        provider: Provider | None = None,
        context: RunContext | None = None,
    ) -> None:
        """Wrap an adapter as a RAGAS judge.

        Args:
            adapter: Adapter performing every judge call.
            model_repo_id: Hugging Face repo id of the judge model.
            provider: Force a backend. ``None`` routes by the registry.
            context: Run-log provenance. Defaults to a ``phase-2`` eval context.
        """
        self._adapter = adapter
        self._model_repo_id = model_repo_id
        self._provider = provider
        self._context = context or RunContext(phase="phase-2", command="m2x eval rag")
        self.calls = 0
        """Judge calls made. Read by the harness so a run can report its own judge cost."""

    def generate_text(
        self,
        prompt: t.Any,
        n: int = 1,
        temperature: float = 0.01,
        stop: list[str] | None = None,
        callbacks: t.Any = None,
    ) -> LLMResult:
        """Serve one judge call.

        Args:
            prompt: RAGAS ``PromptValue``; only its rendered string is used.
            n: Completions requested. Only 1 is supported — see Raises.
            temperature: Accepted and dropped; the adapter pins 0.0.
            stop: Accepted and dropped; the adapter exposes no stop sequences.
            callbacks: LangChain callbacks, unused.

        Returns:
            A LangChain ``LLMResult`` holding the single completion.

        Raises:
            ValueError: ``n > 1``. RAGAS uses that for self-consistency sampling, which
                needs a temperature above zero to mean anything — silently returning one
                completion n times would look like unanimous agreement.
            M2XError: Any routing or provider failure.
        """
        if n > 1:
            raise ValueError(
                f"n={n} requested. Self-consistency sampling needs temperature > 0, which "
                "the adapter pins to 0; returning one completion n times would read as "
                "unanimous agreement between judges that were never consulted."
            )
        self.calls += 1
        response = self._adapter.complete(
            [Message(role=Role.USER, content=prompt.to_string())],
            self._model_repo_id,
            provider=self._provider,
            max_tokens=JUDGE_MAX_TOKENS,
            context=self._context,
        )
        return LLMResult(generations=[[Generation(text=response.text)]])

    async def agenerate_text(
        self,
        prompt: t.Any,
        n: int = 1,
        temperature: float | None = 0.01,
        stop: list[str] | None = None,
        callbacks: t.Any = None,
    ) -> LLMResult:
        """Async entry point, delegating straight to the sync path.

        No thread pool: the adapter serialises regardless, so the only thing concurrency
        would add here is a hop between the call and the run log that recorded it.

        Args:
            prompt: RAGAS ``PromptValue``.
            n: Completions requested.
            temperature: Accepted and dropped.
            stop: Accepted and dropped.
            callbacks: LangChain callbacks, unused.

        Returns:
            A LangChain ``LLMResult``.

        Raises:
            ValueError: ``n > 1``.
            M2XError: Any routing or provider failure.
        """
        return self.generate_text(prompt, n, temperature or 0.0, stop, callbacks)

    async def generate(
        self,
        prompt: t.Any,
        n: int = 1,
        temperature: float | None = 0.01,
        stop: list[str] | None = None,
        callbacks: t.Any = None,
    ) -> LLMResult:
        """The entry point RAGAS's metrics actually call.

        On ``BaseRagasLLM`` this wraps ``agenerate_text`` in RAGAS's own async retry and
        then checks ``is_finished``. Both belong to the adapter here — it already owns
        retries for every other call in the project — so this delegates straight through
        rather than layering a second retry budget that would double the call count
        invisibly and make the cost report wrong.

        Args:
            prompt: RAGAS ``PromptValue``.
            n: Completions requested.
            temperature: Accepted and dropped.
            stop: Accepted and dropped.
            callbacks: LangChain callbacks, unused.

        Returns:
            A LangChain ``LLMResult``.

        Raises:
            ValueError: ``n > 1``.
            M2XError: Any routing or provider failure.
        """
        return await self.agenerate_text(prompt, n, temperature, stop, callbacks)

    def is_finished(self, response: LLMResult) -> bool:
        """Whether generation completed.

        The adapter raises on a failed call rather than returning a partial one, so a
        response reaching here always finished.

        Args:
            response: The result to inspect.

        Returns:
            Always True.
        """
        return True

    def get_temperature(self, n: int) -> float:
        """The temperature RAGAS would sample at. Always 0.0 here.

        On ``BaseRagasLLM`` this returns 0.3 when ``n > 1``, to make self-consistency
        sampling produce different completions. The adapter pins 0.0 and
        :meth:`generate_text` refuses ``n > 1`` outright, so returning anything else would
        advertise a capability this bridge does not have.

        Found by the surface test rather than by a live failure — which is the point of
        having one.

        Args:
            n: Completions RAGAS is about to request.

        Returns:
            0.0.
        """
        return 0.0

    def set_run_config(self, run_config: t.Any) -> None:
        """Accept RAGAS's retry/timeout config and ignore it.

        Retries and timeouts belong to the adapter, which already owns them for every
        other call in the project. Letting RAGAS layer its own on top would double the
        retry count invisibly and make the cost report wrong in the direction that
        matters.

        Args:
            run_config: RAGAS ``RunConfig``, unused.
        """
        return None


def build_ragas_judge(
    adapter: ModelAdapter,
    *,
    model_repo_id: str,
    provider: Provider | None = None,
) -> t.Callable[[AskOutcome, ExpectedAnswer], tuple[float | None, float | None]]:
    """Build the judge callable :func:`~m2x.eval_rag.run_rag_eval` takes.

    Imports RAGAS lazily so the harness, its tests and the sanity cases all run without
    the optional ``ragas`` group installed.

    Args:
        adapter: Adapter performing the judge calls.
        model_repo_id: Judge model.
        provider: Force a backend.

    Returns:
        A callable returning ``(context_precision, faithfulness)`` for one answered
        question.

    Raises:
        ImportError: The ``ragas`` dependency group is not installed. The message names
            the command that installs it.
    """
    try:
        from ragas.dataset_schema import SingleTurnSample
        from ragas.metrics import Faithfulness, LLMContextPrecisionWithReference
    except ImportError as error:  # pragma: no cover - exercised by not installing ragas
        raise ImportError(
            "ragas is not installed. `uv sync --group ragas`. The group is optional "
            "because it pulls the whole langchain stack, which nothing else here needs."
        ) from error

    judge_llm = AdapterRagasLLM(adapter, model_repo_id=model_repo_id, provider=provider)
    precision_metric = LLMContextPrecisionWithReference(llm=judge_llm)
    faithfulness_metric = Faithfulness(llm=judge_llm)

    def judge(outcome: AskOutcome, expected: ExpectedAnswer) -> tuple[float | None, float | None]:
        """Score one answered question.

        Args:
            outcome: What ``ask`` returned.
            expected: The sealed ground truth.

        Returns:
            ``(context_precision, faithfulness)``.

        Raises:
            Exception: Anything RAGAS raises. The caller records it as a judge failure
                rather than as a zero — a judge that errored is missing evidence.
        """
        import asyncio

        sample = SingleTurnSample(
            user_input=outcome.question,
            response=outcome.answer,
            retrieved_contexts=[hit.text for hit in outcome.hits],
            # The gist, not the passages: context precision asks whether each retrieved
            # passage was needed to arrive at the reference answer, so handing it the
            # passages as the reference would ask whether the passages support themselves.
            reference=expected.gist,
        )
        return (
            asyncio.run(precision_metric.single_turn_ascore(sample)),
            asyncio.run(faithfulness_metric.single_turn_ascore(sample)),
        )

    return judge
