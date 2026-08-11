"""Value types crossing the adapter boundary.

Everything here is a Pydantic model or a :class:`~enum.StrEnum`, for three reasons:

* **Validation at the seam.** A provider that changes its response shape fails loudly
  here instead of producing a plausible-looking ``None`` three layers up.
* **Serialisation for free.** The cache writes these straight to JSON and reads them
  back without hand-written converters.
* **One vocabulary.** Downstream phases (extraction, RAG, planning) all speak in
  ``Transcript`` / ``Response``, so no phase invents its own dict shape.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Provider(StrEnum):
    """A serving backend.

    Values are the strings used in config files, environment variables, and the run
    log, so a provider name never needs translating between layers.
    """

    GROQ = "groq"
    NIM = "nim"
    OLLAMA = "ollama"


class ModelKind(StrEnum):
    """What a model is for.

    Used to reject calling a chat model through ``transcribe()`` and vice versa,
    which otherwise surfaces as an opaque provider-side 400.
    """

    CHAT = "chat"
    TRANSCRIBE = "transcribe"
    EMBED = "embed"


class Role(StrEnum):
    """Speaker role in a chat exchange, matching the OpenAI-compatible wire format."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class Message(BaseModel):
    """One turn of a chat conversation."""

    model_config = ConfigDict(frozen=True)

    role: Role
    content: str


class Usage(BaseModel):
    """Token counts for a single call.

    Both counters are 0 for transcription: Whisper-style endpoints do not report token
    usage, and fabricating an estimate would silently corrupt the cost report. Audio
    cost is derived from :attr:`Transcript.audio_seconds` instead.
    """

    model_config = ConfigDict(frozen=True)

    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        """Sum of input and output tokens."""
        return self.tokens_in + self.tokens_out


class AdapterResult(BaseModel):
    """Fields common to every adapter return value.

    These are exactly the fields the run logger needs in order to attribute a call,
    which is why they live on the result rather than being threaded back separately.
    """

    model_config = ConfigDict(frozen=True)

    model_repo_id: str
    """Canonical Hugging Face repo id. Never a provider-specific alias."""

    provider: Provider
    """Which backend actually served this call."""

    latency_ms: int = Field(ge=0)
    """Wall-clock duration. For a cache hit this is the cache read, not the original call."""

    cached: bool = False
    """True when the result was served from the on-disk cache without any network I/O."""

    cost_usd: float = Field(default=0.0, ge=0.0)
    """Computed from the registry price table. Always 0.0 for a cache hit."""


class Response(AdapterResult):
    """The result of a chat completion."""

    text: str
    """Assistant message content, already unwrapped from the provider envelope."""

    usage: Usage = Field(default_factory=Usage)
    """Token accounting for this call. Zeroed on a cache hit — see the cache module."""

    finish_reason: str | None = None
    """Provider-reported stop reason, e.g. ``"stop"`` or ``"length"``.

    Worth surfacing because a silently truncated completion (``"length"``) is a
    common cause of downstream JSON-parse failures in the extraction phase.
    """


class Embeddings(AdapterResult):
    """The result of embedding one batch of texts.

    Vectors come back in request order, and the adapter checks that — a provider that
    reorders or drops one would otherwise attach the wrong vector to the wrong chunk,
    which is unrecoverable later and looks like bad retrieval rather than a bug.
    """

    vectors: list[list[float]]
    """One vector per input text, in the order the texts were submitted."""

    usage: Usage = Field(default_factory=Usage)
    """Token accounting. Embedding endpoints report input tokens only."""

    @property
    def dimensions(self) -> int:
        """Length of each vector. ``0`` for an empty batch."""
        return len(self.vectors[0]) if self.vectors else 0


class TranscriptSegment(BaseModel):
    """A timestamped span of transcribed speech.

    Timestamps are the product's spine: every extracted decision and every RAG
    citation ultimately points at a ``t_start``–``t_end`` range, so segments are
    preserved verbatim rather than being flattened into plain text.
    """

    model_config = ConfigDict(frozen=True)

    t_start: float = Field(ge=0.0)
    """Segment start, in seconds from the beginning of the audio."""

    t_end: float = Field(ge=0.0)
    """Segment end, in seconds from the beginning of the audio."""

    text: str

    speaker: str | None = None
    """Speaker label, populated only once diarisation runs. ``None`` from plain STT."""


class Transcript(AdapterResult):
    """The result of a transcription call."""

    text: str
    """Full transcript as one string, provider-supplied rather than re-joined."""

    segments: list[TranscriptSegment] = Field(default_factory=list)
    """Timestamped segments. Empty if the provider was asked for plain-text output."""

    audio_seconds: float = Field(default=0.0, ge=0.0)
    """Duration of the submitted audio, used as the cost basis for audio models."""

    language: str | None = None
    """Detected or requested language code."""

    usage: Usage = Field(default_factory=Usage)
    """Present for interface symmetry with :class:`Response`; both counters stay 0."""
