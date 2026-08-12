"""Question in, a cited answer out — or an honest refusal.

The retrieval substrate (:mod:`m2x.vector_store`) ends at "here are the nearest chunks".
This module is what turns that into an answer a reader can falsify, and it is built around
one asymmetry: a fluent ungrounded answer is worse than no answer at all, because it costs
the reader the ability to tell the two apart.

Three mechanisms carry that:

* **Citations are unforgeable by construction, then checked anyway.** Retrieved passages
  are labelled ``C1``, ``C2``… in the prompt and the model cites those references. It never
  types a timestamp, so it cannot invent one — the human-readable
  ``[meeting · speaker · mm:ss–mm:ss]`` is rendered here from the metadata already stored on
  the cited chunk. That closes the *reference*. It does not close the *claim*, since a real
  passage can be cited for a sentence it does not support, so each citation also carries a
  quote that must appear verbatim in that passage.
* **Validation happens inside the retry loop**, via Pydantic validation context, exactly as
  evidence resolution does in :mod:`m2x.extraction`. A citation to ``C9`` when five passages
  were supplied comes back to the model as an error to fix, not as an item quietly dropped.
* **Abstention is a result, not an error path.** It has three doors: nothing retrieved,
  nothing retrieved *near enough* (:data:`DEFAULT_MAX_DISTANCE`), or the model could not
  ground an answer — including the case where it exhausted its one retry still citing
  things that do not check out. All three end in the same sentence and a reason code, and
  the command exits 0. It is graded as a feature.

The distance threshold is the weakest number here and is deliberately exposed rather than
buried; see :data:`DEFAULT_MAX_DISTANCE`.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path

import instructor
from instructor.core import InstructorRetryException
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from m2x.adapter import ModelAdapter
from m2x.indexing import SourceType, format_timestamp
from m2x.prompts import DEFAULT_PROMPTS_DIR, Prompt, load_prompt
from m2x.run_log import RunContext
from m2x.types import Message, Provider, Role
from m2x.vector_store import Hit, VectorStore, query_index

PHASE = "phase-2"
"""Run-log phase. The same phase the index build and query are attributed to."""

DEFAULT_ASK_MODEL = "meta-llama/Llama-3.1-8B-Instruct"
"""Hugging Face repo id of the answering model. The extractor's model, so the two
Phase 1B/2 numbers are earned by the same weights on the same corpus."""

RAG_PROMPT_NAME = "rag"
"""Prompt library entry: ``prompts/rag/v<N>.md``. The name is code; the text is data."""

QUESTION_PLACEHOLDER = "question"
PASSAGES_PLACEHOLDER = "passages"

DEFAULT_TOP_K = 5
"""Passages retrieved per question."""

MAX_ATTEMPTS = 2
"""Total model calls: the first, plus the one retry the ticket allows.

The extractor gets two retries because a malformed date is worth asking about twice. A
citation that does not resolve is a different failure — the model is reaching for something
that is not in front of it, and a second retry mostly buys a more confident version of the
same reach. When this budget is gone the answer abstains rather than raising.
"""

DEFAULT_MAX_DISTANCE = 0.48
"""Cosine distance beyond which the corpus is treated as not holding the answer.

**Provisional, and a property of this corpus and this embedding model — not a constant.**

Measured on the tracked doc corpus (README, handbook, corpus.md — 82 chunks,
nomic-embed-text-v1.5), nearest-hit distance over eight questions:

===================================  ========
question                             nearest
===================================  ========
three RAG gate metrics                 0.2963
what the corpus is made of             0.3693
how a gate number is verified          0.3929
how the response cache key works       0.4166
which models are banned and why        0.4414
office wifi password                   0.5241
how to file a reimbursement claim      0.5446
who won the 2026 cricket world cup     0.5589
===================================  ========

The five the docs answer top out at 0.4414; the three they do not start at 0.5241. This
value sits in that gap. **Eight questions is not a separation** — it is a sample small
enough that one more question could close it, and the answerable end is already spread
across 0.15 of distance with no relationship to how well the passage actually answers
(0.4414 retrieves the *wrong* section of a question the docs do answer). Treat it as the
point past which a model call is not worth making, not as a correctness boundary.

Exposed as ``--max-distance`` because it will move. The number that would justify a value
is context precision, and that belongs to M2X-045/046.
"""

MIN_QUOTE_CHARS = 12
"""Shortest quote accepted as evidence for a claim.

Without a floor the substring check is theatre: ``"the"`` appears in every passage, so a
one-word quote would satisfy the validator while supporting nothing. A dozen characters is
a few words — short enough for a real phrase, long enough that finding one by accident in a
passage that does not support the claim is unlikely.
"""

EMPHASIS_PATTERN = re.compile(r"[*_`]+")
"""Markdown emphasis and code markers, dropped before a quote is compared.

The corpus is markdown and the model quotes what it reads as prose, so ``**bold**`` comes
back as ``bold``. Treating that as a failed quote checks the formatting, not the claim.
"""

PASSAGE_REF_TEMPLATE = "C{index}"
"""How a retrieved passage is labelled in the prompt and cited back. 1-based."""

PASSAGE_CONTEXT_KEY = "passages"
"""Validation-context key holding ``{passage_ref: normalised_text}``.

Passed through Instructor as ``context=`` so citation checking happens inside the retry
loop. ``validation_context=`` is accepted in silence and never reaches the validators,
which would let every fabricated citation through.
"""

ABSTAIN_TEXT = "Not found in the meeting corpus"
"""The one sentence every abstention prints, whichever door it came through.

One string rather than three shades of "I'm not sure": Friday grades whether the system
declined, and a caller should not have to pattern-match prose to find out.
"""


class AbstentionReason(StrEnum):
    """Why an answer was withheld. Recorded because the three are not equally good news."""

    NO_MATCH = "no_match"
    """Retrieval returned nothing at all — usually an empty or unbuilt index."""

    BELOW_THRESHOLD = "below_threshold"
    """The nearest passage was further than the distance threshold. No model call was made."""

    MODEL_ABSTAINED = "model_abstained"
    """Passages were near enough, and the model read them and said they do not answer it."""

    UNGROUNDED = "ungrounded"
    """The model answered, but its citations never validated within the retry budget.

    The most interesting of the four: retrieval found something, the model believed it had
    an answer, and the grounding did not hold up. Worth looking at rather than aggregating.
    """


class Citation(BaseModel):
    """A model's pointer at the passage a claim came from.

    Validation is two-sided for the same reason :class:`~m2x.schema.Evidence` is: the
    passage must exist, *and* the quote must actually be in it. Checking only existence
    would let a model cite a real passage for a claim made nowhere near it.
    """

    model_config = ConfigDict(extra="forbid")

    passage_ref: str = Field(
        min_length=1,
        description="Reference label of the passage, exactly as shown, e.g. 'C1'. Never the passage text itself.",
    )
    """Reference exactly as shown in the retrieved block, e.g. ``"C1"``.

    Named ``passage_ref`` rather than ``passage`` because the field name is rendered into
    the prompt as part of the schema, and ``passage`` invited the model to paste the whole
    passage into it — which the validator then correctly rejected, abstaining on a question
    the corpus answered. The name is part of the prompt; see ``prompts/rag/v2.md``.
    """

    quote: str = Field(
        min_length=1,
        description="Words copied verbatim from that passage, supporting the claim.",
    )
    """Words copied verbatim from that passage, supporting the claim."""

    @model_validator(mode="after")
    def _resolve_against_retrieved(self, info: ValidationInfo) -> Citation:
        """Check the citation points into the passages that were actually supplied.

        Args:
            info: Validation context. ``info.context[PASSAGE_CONTEXT_KEY]`` maps passage
                reference to that passage's normalised text.

        Returns:
            The validated citation.

        Raises:
            ValueError: The quote is too short to be evidence, the reference was not
                supplied, or the quote does not appear in the passage it names. The
                messages are written to the model, not to a log: each one says what to do
                instead, because it is fed back as the retry prompt.
        """
        if len(self.quote.strip()) < MIN_QUOTE_CHARS:
            raise ValueError(
                f"quote {self.quote!r} is too short to evidence a claim; copy at least "
                f"{MIN_QUOTE_CHARS} characters verbatim from the passage"
            )

        passages = (info.context or {}).get(PASSAGE_CONTEXT_KEY)
        if passages is None:
            # No context supplied — an outcome read back off disk for inspection rather
            # than one being generated. Structural checks still apply; resolution cannot,
            # because the retrieved set is not in hand. Same degradation as Evidence.
            return self

        text = passages.get(self.passage_ref)
        if text is None:
            raise ValueError(
                f"passage {self.passage_ref!r} was not retrieved; cite one of "
                f"{sorted(passages)}, or set abstained if none of them answer the question"
            )

        if normalise(self.quote) not in text:
            raise ValueError(
                f"the quote is not in passage {self.passage_ref}; copy the words verbatim from "
                "the passage, or cite the passage that actually contains them"
            )
        return self


class AnswerDraft(BaseModel):
    """What the model is asked to return: an answer with its citations, or an abstention."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(default="")
    """The answer text. Ignored when ``abstained`` — the caller prints one fixed sentence."""

    citations: list[Citation] = Field(default_factory=list)
    """Every passage the answer drew on. Empty only when abstaining."""

    abstained: bool = False
    """True when the passages do not answer the question."""

    @model_validator(mode="after")
    def _an_answer_is_cited(self) -> AnswerDraft:
        """Require every non-abstaining answer to cite something.

        Returns:
            The validated draft.

        Raises:
            ValueError: The model answered without citing, which is the ungrounded answer
                this whole module exists to prevent. Raised inside the retry loop, so the
                model is asked to either cite its source or abstain.
        """
        if self.abstained:
            return self
        if not self.answer.strip():
            raise ValueError("answer is empty; either answer the question or set abstained")
        if not self.citations:
            raise ValueError(
                "an answer must cite at least one passage; cite the passages it came from, "
                "or set abstained if the passages do not answer the question"
            )
        return self


class ResolvedCitation(BaseModel):
    """A validated citation with the reader-facing reference filled in from the index.

    The ``reference`` string is built here rather than accepted from the model. That is the
    point of the whole scheme: the timestamp, the speaker and the source name come from the
    chunk's stored metadata, so they describe where the passage really is.
    """

    model_config = ConfigDict(frozen=True)

    passage_ref: str
    """Passage reference as cited, e.g. ``"C1"``."""

    quote: str
    """The verbatim supporting words, as the model copied them."""

    reference: str
    """Reader-facing citation, e.g. ``"[mtg-001 · Yash · 14:32–14:47]"``."""

    chunk_id: str
    source_id: str
    distance: float = Field(ge=0.0)
    """Retrieval distance of the cited chunk. Kept so a weak citation stays visible."""


class AskOutcome(BaseModel):
    """One answered (or declined) question, with the provenance to reproduce it."""

    model_config = ConfigDict(frozen=True)

    question: str
    answer: str
    """The answer, or :data:`ABSTAIN_TEXT` when ``abstained``."""

    citations: list[ResolvedCitation] = Field(default_factory=list)
    abstained: bool = False
    abstention_reason: AbstentionReason | None = None
    """Which door the abstention came through. ``None`` when an answer was given."""

    retrieved: int = Field(default=0, ge=0)
    """Passages retrieved for this question."""

    nearest_distance: float | None = None
    """Distance of the nearest passage. ``None`` when nothing was retrieved."""

    max_distance: float
    """Threshold this question was judged against. Recorded because it is provisional and
    will move — an abstention rate is not comparable across two different values."""

    prompt_name: str
    prompt_version: str
    """Version of the RAG prompt, e.g. ``"v1"``. Required for the same reason the
    extractor's is: abstention rate is prompt-sensitive, so a rate that cannot name its
    prompt is not a measurement."""

    model_repo_id: str | None = None
    """Answering model. ``None`` when abstention happened before any model call."""

    provider: Provider | None = None
    attempts: int = Field(default=0, ge=0)
    """Model calls made. ``0`` for a retrieval-only abstention, ``2`` when a retry ran."""

    latency_ms: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)


def normalise(text: str) -> str:
    """Fold text to the form quotes are compared in.

    Whitespace, case and markdown emphasis are dropped, because a model re-typing a phrase
    across a line break, or quoting ``**Citation accuracy**`` as "Citation accuracy", is
    quoting correctly — and a validator that says otherwise burns the retry budget on
    formatting and then abstains on an answerable question. That is not hypothetical: it
    is what the first live run of this command did.

    Word order and wording are untouched. Those are the parts being checked, and folding
    them would turn the substring test into a similarity test, which is the thing this
    validator exists to not be.

    Args:
        text: Passage or quote.

    Returns:
        The comparable form.
    """
    return EMPHASIS_PATTERN.sub("", " ".join(text.split()).casefold())


def passage_ref(index: int) -> str:
    """Label for the nth retrieved passage, 1-based.

    Args:
        index: Position in the retrieved list, starting at 1.

    Returns:
        The reference, e.g. ``"C1"``.
    """
    return PASSAGE_REF_TEMPLATE.format(index=index)


def render_reference(hit: Hit) -> str:
    """Build the reader-facing citation for a retrieved chunk.

    ``[meeting · speaker · mm:ss–mm:ss]`` for a meeting, as the ticket specifies. Documents
    have neither speakers nor timestamps, so they cite their heading instead — the corpus
    on a fresh clone is documents only, and a document citation that pretended to a
    timestamp would be the exact failure this module is built to prevent.

    Args:
        hit: The retrieved chunk.

    Returns:
        The bracketed citation.
    """
    parts = [str(hit.metadata.get("source_id", "unknown"))]
    speakers = hit.metadata.get("speakers")
    if speakers:
        parts.append(str(speakers))

    start, end = hit.metadata.get("t_start"), hit.metadata.get("t_end")
    heading = hit.metadata.get("heading")
    if hit.metadata.get("source_type") == SourceType.MEETING.value and start is not None and end is not None:
        parts.append(f"{format_timestamp(float(start))}–{format_timestamp(float(end))}")
    elif heading:
        parts.append(f"§ {heading}")

    return f"[{' · '.join(parts)}]"


def render_passages(hits: list[Hit]) -> str:
    """Render the retrieved set as the data block the model reads.

    Each passage is headed by its reference and its source, so the model can cite it and a
    human reading the prompt can see what was in front of it.

    Args:
        hits: Retrieved chunks, nearest first.

    Returns:
        The block that fills the prompt's ``{{passages}}`` slot.
    """
    blocks = []
    for index, hit in enumerate(hits, start=1):
        blocks.append(f"[{passage_ref(index)}] {render_reference(hit)}\n{hit.text.strip()}")
    return "\n\n".join(blocks)


def build_messages(question: str, hits: list[Hit], prompt: Prompt) -> list[Message]:
    """Build the conversation for one question.

    Args:
        question: The user's question.
        hits: Retrieved passages, nearest first.
        prompt: Prompt version supplying the system message and user template.

    Returns:
        The messages to send.

    Raises:
        ConfigError: The template does not expose exactly the ``{{question}}`` and
            ``{{passages}}`` slots.
    """
    return [
        Message(role=Role.SYSTEM, content=prompt.system),
        Message(
            role=Role.USER,
            content=prompt.render_user(
                **{
                    QUESTION_PLACEHOLDER: question.strip(),
                    PASSAGES_PLACEHOLDER: render_passages(hits),
                }
            ),
        ),
    ]


def ask(
    question: str,
    *,
    store: VectorStore,
    adapter: ModelAdapter,
    k: int = DEFAULT_TOP_K,
    model_repo_id: str = DEFAULT_ASK_MODEL,
    provider: Provider | None = None,
    embed_provider: Provider | None = None,
    source_type: SourceType | None = None,
    max_distance: float = DEFAULT_MAX_DISTANCE,
    max_attempts: int = MAX_ATTEMPTS,
    prompt_version: str | None = None,
    prompts_dir: Path = DEFAULT_PROMPTS_DIR,
    command: str = "m2x ask",
) -> AskOutcome:
    """Answer one question from the index, with citations, or abstain.

    Args:
        question: Natural-language question.
        store: Index to retrieve from.
        adapter: Adapter performing both the embedding and the answering call.
        k: Passages to retrieve.
        model_repo_id: Hugging Face repo id of the answering model.
        provider: Force a backend for the answering call.
        embed_provider: Force a backend for the query embedding.
        source_type: Restrict retrieval to meetings or documents.
        max_distance: Abstain without calling the model when the nearest passage is
            further than this. See :data:`DEFAULT_MAX_DISTANCE` — provisional.
        max_attempts: Total model calls, including the retry.
        prompt_version: RAG prompt version. ``None`` takes the latest on disk.
        prompts_dir: Root of the prompt library.
        command: Run-log command label.

    Returns:
        The answer, or an abstention with the reason it was withheld. An unanswerable
        question is a normal return, never an exception.

    Raises:
        ValueError: ``question`` is blank.
        M2XError: Configuration, routing or provider failure — including a missing or
            malformed prompt version, which fails before any call is made.
    """
    if not question.strip():
        raise ValueError("ask needs a non-empty question")

    # Resolved before retrieval so the version is stamped on the outcome and on the
    # embedding's log line even when the question abstains before the answering call.
    prompt = load_prompt(RAG_PROMPT_NAME, prompt_version, prompts_dir=prompts_dir)
    context = RunContext(phase=PHASE, command=command, prompt_version=prompt.version)

    hits = query_index(
        store,
        adapter,
        question,
        k=k,
        provider=embed_provider,
        source_type=source_type,
        context=context,
    )

    def abstain(reason: AbstentionReason, **extra: object) -> AskOutcome:
        """Build the abstaining outcome shared by all four doors."""
        return AskOutcome(
            question=question,
            answer=ABSTAIN_TEXT,
            abstained=True,
            abstention_reason=reason,
            retrieved=len(hits),
            nearest_distance=hits[0].distance if hits else None,
            max_distance=max_distance,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            **extra,  # type: ignore[arg-type]
        )

    if not hits:
        return abstain(AbstentionReason.NO_MATCH)
    if hits[0].distance > max_distance:
        return abstain(AbstentionReason.BELOW_THRESHOLD)

    messages = build_messages(question, hits, prompt)
    by_ref = {passage_ref(index): hit for index, hit in enumerate(hits, start=1)}
    validation_context = {
        PASSAGE_CONTEXT_KEY: {ref: normalise(hit.text) for ref, hit in by_ref.items()}
    }
    attempts: list[tuple[int, float, Provider, str]] = []

    def create(**kwargs: object) -> ChatCompletion:
        """Serve one Instructor attempt through the adapter.

        Same wiring as the extractor's, and for the same reason: an Instructor client
        pointed straight at a provider would be N invisible calls and a cost report that
        quietly understates the phase.
        """
        turns = [Message.model_validate(message) for message in kwargs["messages"]]  # type: ignore[union-attr]
        response = adapter.complete(turns, model_repo_id, provider=provider, context=context)
        attempts.append(
            (response.latency_ms, response.cost_usd, response.provider, response.model_repo_id)
        )
        return ChatCompletion(
            id=f"ask-attempt-{len(attempts)}",
            model=response.model_repo_id,
            object="chat.completion",
            created=0,
            choices=[
                Choice(
                    index=0,
                    finish_reason="stop",
                    message=ChatCompletionMessage(role="assistant", content=response.text),
                )
            ],
        )

    client = instructor.from_litellm(create, mode=instructor.Mode.JSON)
    try:
        returned = client.create(
            model=model_repo_id,
            messages=[{"role": turn.role.value, "content": turn.content} for turn in messages],
            response_model=AnswerDraft,
            max_retries=max_attempts - 1,
            context=validation_context,
        )
    except InstructorRetryException:
        # The retry budget went on citations that never resolved. Unlike the extractor,
        # which raises here, there is a true thing left to say: the answer is not grounded.
        return abstain(
            AbstentionReason.UNGROUNDED,
            model_repo_id=attempts[-1][3] if attempts else None,
            provider=attempts[-1][2] if attempts else None,
            attempts=len(attempts),
            latency_ms=sum(attempt[0] for attempt in attempts),
            cost_usd=sum(attempt[1] for attempt in attempts),
        )

    spent = {
        "model_repo_id": attempts[-1][3],
        "provider": attempts[-1][2],
        "attempts": len(attempts),
        "latency_ms": sum(attempt[0] for attempt in attempts),
        "cost_usd": sum(attempt[1] for attempt in attempts),
    }

    if returned.abstained:
        return abstain(AbstentionReason.MODEL_ABSTAINED, **spent)

    # Re-validate into a clean instance: Instructor attaches the raw provider response as a
    # private attribute, and Pydantic compares private attributes in `__eq__`, so the
    # returned object would never equal the same draft parsed from disk.
    draft = AnswerDraft.model_validate(returned.model_dump(), context=validation_context)

    return AskOutcome(
        question=question,
        answer=draft.answer.strip(),
        citations=[
            ResolvedCitation(
                passage_ref=citation.passage_ref,
                quote=citation.quote,
                reference=render_reference(by_ref[citation.passage_ref]),
                chunk_id=by_ref[citation.passage_ref].chunk_id,
                source_id=str(by_ref[citation.passage_ref].metadata.get("source_id", "unknown")),
                distance=by_ref[citation.passage_ref].distance,
            )
            for citation in draft.citations
        ],
        retrieved=len(hits),
        nearest_distance=hits[0].distance,
        max_distance=max_distance,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
        **spent,  # type: ignore[arg-type]
    )
