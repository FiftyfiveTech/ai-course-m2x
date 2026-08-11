"""The provider-neutral model adapter.

Every model call in this project goes through :class:`ModelAdapter`. That is a hard
rule, not a preference, and it buys three things:

1. **Switching providers is configuration.** Groq, NVIDIA NIM, and Ollama all speak
   the OpenAI-compatible wire format, so one implementation covers all three by
   varying a base URL and a credential. Feature code names a Hugging Face repo id and
   never learns which backend served it.
2. **Nothing escapes measurement.** The adapter is the only code that performs HTTP to
   a model, so "every call is logged" is guaranteed structurally rather than by
   remembering to log.
3. **Quota survival.** A content-addressed cache sits in front of every call, so
   iterating on downstream code costs no tokens.

Composition over inheritance throughout: routing, caching, pricing, and logging are
separate collaborators injected at construction. Tests replace the HTTP transport and
the clock, so the whole surface is exercisable with no network and no sleeping.

Typical use::

    with ModelAdapter(context=RunContext(phase="phase-0", command="m2x process")) as adapter:
        response = adapter.complete(
            [Message(role=Role.USER, content="Summarise this meeting.")],
            "meta-llama/Llama-3.3-70B-Instruct",
        )
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import httpx

from m2x.cache import ResponseCache, build_cache_key, digest_bytes
from m2x.errors import (
    CapabilityMismatchError,
    ProviderRequestError,
    RateLimitError,
)
from m2x.model_registry import ModelRegistry, ResolvedTarget
from m2x.pricing import compute_cost
from m2x.run_log import RunContext, RunLogger, _utc_now
from m2x.settings import Settings
from m2x.types import (
    AdapterResult,
    Embeddings,
    Message,
    ModelKind,
    Provider,
    Response,
    Transcript,
    TranscriptSegment,
    Usage,
)

_CHAT_PATH = "/chat/completions"
_TRANSCRIBE_PATH = "/audio/transcriptions"
_EMBED_PATH = "/embeddings"

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
"""Statuses worth retrying.

429 is the one the free tiers actually produce. The 5xx family is included because a
transient gateway failure is indistinguishable from a retryable one from here, and a
retry costs nothing but a short wait.
"""

_DEFAULT_AUDIO_FILENAME = "audio.wav"
"""Filename sent when audio arrives as raw bytes; providers key off the extension."""


class ModelAdapter:
    """One entry point to every model, hosted or local.

    The adapter holds no credentials. It asks :class:`~m2x.settings.Settings` for one
    at request time and lets it go immediately, so a key never lives on an instance
    that might end up in a traceback or a log line.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        registry: ModelRegistry | None = None,
        cache: ResponseCache | None = None,
        run_logger: RunLogger | None = None,
        context: RunContext | None = None,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        """
        Args:
            settings: Loaded configuration. Read from the environment and `.env` when
                omitted.
            registry: Routing table. Loaded from ``settings.models_config_path`` when
                omitted.
            cache: Response cache. Built from ``settings.cache_dir`` when omitted.
            run_logger: Run log sink. Built from ``settings.runs_log_path`` when omitted.
            context: Default provenance for calls. Individual calls may override it.
            client: HTTP client. Tests inject one backed by
                :class:`httpx.MockTransport`; when omitted the adapter creates and
                owns one, and :meth:`close` disposes of it.
            sleep: Delay function, injected so retry tests do not actually wait.
            monotonic: Monotonic clock for latency measurement. Monotonic rather than
                wall-clock so an NTP correction mid-call cannot produce a negative
                duration.
            now: Wall clock for run-log timestamps.
        """
        self._settings = settings or Settings()
        self._registry = registry or ModelRegistry.from_settings(self._settings)
        self._cache = cache or ResponseCache(
            self._settings.cache_dir, enabled=self._settings.cache_enabled
        )
        self._run_logger = run_logger or RunLogger(self._settings.runs_log_path, now=now)
        self._context = context or RunContext()
        self._sleep = sleep
        self._monotonic = monotonic

        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=self._settings.request_timeout_s)

    # -- Lifecycle -------------------------------------------------------------------

    def close(self) -> None:
        """Release the HTTP client, but only if this adapter created it.

        An injected client belongs to the caller; closing it here would break a test
        that reuses one across adapters.
        """
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> ModelAdapter:
        """Support ``with ModelAdapter() as adapter:``."""
        return self

    def __exit__(self, *_exc_info: object) -> None:
        """Close the owned HTTP client on scope exit."""
        self.close()

    # -- Public API ------------------------------------------------------------------

    def complete(
        self,
        messages: Sequence[Message] | Sequence[Mapping[str, str]],
        model_repo_id: str,
        *,
        provider: Provider | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        seed: int | None = None,
        context: RunContext | None = None,
    ) -> Response:
        """Run a chat completion.

        Args:
            messages: Conversation turns, as :class:`~m2x.types.Message` objects or
                plain ``{"role", "content"}`` mappings. Mappings are validated into
                messages, so a bad role fails here rather than at the provider.
            model_repo_id: Canonical Hugging Face repo id.
            provider: Force a backend, overriding both the model default and
                ``M2X_PROVIDER_OVERRIDE``.
            temperature: Sampling temperature. Defaults to 0.0 because every
                measurement in this project wants reproducibility first.
            max_tokens: Output cap. ``None`` leaves it to the provider.
            seed: Sampling seed where the provider honours it.
            context: Provenance override for this one call.

        Returns:
            The completion, with ``cached``, ``latency_ms``, and ``cost_usd`` populated.

        Raises:
            BannedModelError: The model is on the project's banned list.
            UnknownModelError: The model is not in the registry.
            ProviderNotConfiguredError: The chosen provider cannot serve the model.
            CapabilityMismatchError: The model is not a chat model.
            MissingCredentialError: The provider needs a key that is not set.
            RateLimitError: Rate limited after every retry.
            ProviderRequestError: Any other provider or transport failure.
        """
        target = self._resolve(model_repo_id, provider)
        self._assert_kind(target, ModelKind.CHAT, "complete")

        turns = [
            message if isinstance(message, Message) else Message.model_validate(message)
            for message in messages
        ]
        wire_messages = [
            {"role": turn.role.value, "content": turn.content} for turn in turns
        ]
        params = _compact(
            {"temperature": temperature, "max_tokens": max_tokens, "seed": seed}
        )

        key = build_cache_key(
            kind=ModelKind.CHAT.value,
            model_repo_id=target.model.repo_id,
            provider=target.provider,
            payload=wire_messages,
            params=params,
        )

        cached = self._read_cache(key, Response)
        if cached is not None:
            return self._finish(cached, context)

        body: dict[str, Any] = {
            "model": target.served_as,
            "messages": wire_messages,
            **params,
        }

        started = self._monotonic()
        http_response = self._send_with_retry(
            target, lambda: self._build_json_request(target, _CHAT_PATH, body)
        )
        latency_ms = self._elapsed_ms(started)

        response = self._parse_completion(target, http_response, latency_ms)
        self._write_cache(key, response)
        return self._finish(response, context)

    def transcribe(
        self,
        audio: Path | bytes,
        model_repo_id: str,
        *,
        provider: Provider | None = None,
        language: str | None = None,
        prompt: str | None = None,
        context: RunContext | None = None,
    ) -> Transcript:
        """Transcribe audio to timestamped segments.

        Always requests ``verbose_json``. Plain text would be smaller, but timestamps
        are the product's spine — every extracted decision and every citation
        eventually points at a segment's time range — so discarding them at the
        adapter would be unrecoverable later.

        Args:
            audio: Path to an audio file, or the raw bytes. Bytes are sent as
                ``audio.wav``, since providers infer the format from the filename.
            model_repo_id: Canonical Hugging Face repo id of a transcription model.
            provider: Force a backend.
            language: ISO-639-1 hint. ``None`` lets the model auto-detect.
            prompt: Decoding bias — a comma-joined vocabulary, per :mod:`m2x.vocab`.
                Part of the cache key, so a run with the vocabulary and one without are
                separate entries rather than the second silently reading the first.
            context: Provenance override for this one call.

        Returns:
            The transcript, including segments and the audio duration used for costing.

        Raises:
            CapabilityMismatchError: The model is not a transcription model.
            OSError: ``audio`` is a path that cannot be read.
            RateLimitError: Rate limited after every retry.
            ProviderRequestError: Any other provider or transport failure.
        """
        target = self._resolve(model_repo_id, provider)
        self._assert_kind(target, ModelKind.TRANSCRIBE, "transcribe")

        if isinstance(audio, Path):
            payload = audio.read_bytes()
            filename = audio.name
        else:
            payload = audio
            filename = _DEFAULT_AUDIO_FILENAME

        params = _compact(
            {"response_format": "verbose_json", "language": language, "prompt": prompt}
        )

        # Audio is hashed rather than embedded: a ten-minute clip is megabytes, and
        # the digest is a perfectly good content address.
        key = build_cache_key(
            kind=ModelKind.TRANSCRIBE.value,
            model_repo_id=target.model.repo_id,
            provider=target.provider,
            payload=digest_bytes(payload),
            params=params,
        )

        cached = self._read_cache(key, Transcript)
        if cached is not None:
            return self._finish(cached, context)

        started = self._monotonic()
        http_response = self._send_with_retry(
            target,
            lambda: self._build_multipart_request(
                target,
                _TRANSCRIBE_PATH,
                files={"file": (filename, payload, "application/octet-stream")},
                data={"model": target.served_as, **params},
            ),
        )
        latency_ms = self._elapsed_ms(started)

        transcript = self._parse_transcript(target, http_response, latency_ms)
        self._write_cache(key, transcript)
        return self._finish(transcript, context)

    def embed(
        self,
        texts: Sequence[str],
        model_repo_id: str,
        *,
        provider: Provider | None = None,
        context: RunContext | None = None,
    ) -> Embeddings:
        """Embed a batch of texts into vectors.

        One request per batch rather than one per text: the wire format takes a list,
        and a hundred separate round trips to embed a hundred chunks would dominate the
        index build for no benefit. The batch is also the cache unit — re-indexing an
        unchanged corpus in the same batches is a hit, not a re-embedding.

        Args:
            texts: Texts to embed, in the order the vectors are wanted back.
            model_repo_id: Canonical Hugging Face repo id of an embedding model.
            provider: Force a backend. ``None`` uses the model's default route.
            context: Provenance override for this one call.

        Returns:
            The vectors, in submission order, with cost and latency populated.

        Raises:
            ValueError: ``texts`` is empty. An empty request is a caller bug, and
                providers answer it with an unhelpful 400.
            CapabilityMismatchError: The model is not an embedding model.
            RateLimitError: Rate limited after every retry.
            ProviderRequestError: The provider failed, or returned a batch that does
                not line up with the request.
        """
        if not texts:
            raise ValueError("embed() needs at least one text")

        target = self._resolve(model_repo_id, provider)
        self._assert_kind(target, ModelKind.EMBED, "embed")

        payload = list(texts)
        key = build_cache_key(
            kind=ModelKind.EMBED.value,
            model_repo_id=target.model.repo_id,
            provider=target.provider,
            payload=payload,
            params={},
        )

        cached = self._read_cache(key, Embeddings)
        if cached is not None:
            return self._finish(cached, context)

        body: dict[str, Any] = {"model": target.served_as, "input": payload}

        started = self._monotonic()
        http_response = self._send_with_retry(
            target, lambda: self._build_json_request(target, _EMBED_PATH, body)
        )
        latency_ms = self._elapsed_ms(started)

        embeddings = self._parse_embeddings(target, http_response, latency_ms, len(payload))
        self._write_cache(key, embeddings)
        return self._finish(embeddings, context)

    # -- Routing ---------------------------------------------------------------------

    def _resolve(self, model_repo_id: str, provider: Provider | None) -> ResolvedTarget:
        """Choose the endpoint for a call.

        Precedence: explicit argument, then ``M2X_PROVIDER_OVERRIDE``, then the
        model's own default. The override is what makes the three-provider comparison
        a config change rather than a code change.

        Args:
            model_repo_id: Canonical repo id.
            provider: Explicit provider, or ``None`` to fall through to settings.

        Returns:
            The resolved target.
        """
        return self._registry.resolve(
            model_repo_id,
            provider=provider or self._settings.provider_override,
        )

    @staticmethod
    def _assert_kind(target: ResolvedTarget, expected: ModelKind, method: str) -> None:
        """Reject using a model through the wrong method.

        Without this check, calling ``complete()`` on Whisper produces an opaque
        provider-side 400 that costs real time to diagnose.

        Args:
            target: Resolved target.
            expected: Kind the calling method requires.
            method: Method name, for the error message.

        Raises:
            CapabilityMismatchError: The model's kind does not match.
        """
        if target.model.kind is not expected:
            raise CapabilityMismatchError(
                f"{target.model.repo_id!r} is a {target.model.kind.value} model and "
                f"cannot be used with {method}(). Expected a {expected.value} model."
            )

    # -- HTTP ------------------------------------------------------------------------

    def _auth_headers(self, target: ResolvedTarget) -> dict[str, str]:
        """Build request headers, including the bearer token when one is required.

        The credential is fetched here and used immediately. Local Ollama needs none,
        so no ``Authorization`` header is sent at all rather than an empty one.

        Args:
            target: Resolved target.

        Returns:
            Headers for the request.

        Raises:
            MissingCredentialError: The provider requires a key that is not set.
        """
        headers = {"Accept": "application/json"}
        credential = self._settings.credential_for(target.provider, target.api_key_setting)
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        return headers

    def _build_json_request(
        self, target: ResolvedTarget, path: str, body: Mapping[str, Any]
    ) -> httpx.Request:
        """Construct a JSON POST.

        Built fresh per attempt rather than once and reused, because a consumed
        request body cannot be re-sent.

        Args:
            target: Resolved target.
            path: Path relative to the provider's API root.
            body: JSON body.

        Returns:
            An unsent request.
        """
        return self._client.build_request(
            "POST",
            f"{target.base_url}{path}",
            json=dict(body),
            headers={**self._auth_headers(target), "Content-Type": "application/json"},
        )

    def _build_multipart_request(
        self,
        target: ResolvedTarget,
        path: str,
        *,
        files: Mapping[str, Any],
        data: Mapping[str, Any],
    ) -> httpx.Request:
        """Construct a multipart POST for file upload.

        ``Content-Type`` is left to httpx, which has to generate the multipart
        boundary; setting it by hand produces a request the provider cannot parse.

        Args:
            target: Resolved target.
            path: Path relative to the provider's API root.
            files: httpx ``files`` mapping.
            data: Additional form fields.

        Returns:
            An unsent request.
        """
        return self._client.build_request(
            "POST",
            f"{target.base_url}{path}",
            files=dict(files),
            data=dict(data),
            headers=self._auth_headers(target),
        )

    def _send_with_retry(
        self,
        target: ResolvedTarget,
        build_request: Callable[[], httpx.Request],
    ) -> httpx.Response:
        """Send a request, retrying transient failures with exponential backoff.

        Retries cover HTTP 429 and the 5xx family plus transport errors. A 4xx other
        than 429 is a bug in the request and is raised immediately — retrying a
        malformed body just wastes quota.

        Args:
            target: Resolved target, used for error messages and fallback hints.
            build_request: Factory called once per attempt.

        Returns:
            The first successful response.

        Raises:
            RateLimitError: Every attempt returned 429.
            ProviderRequestError: A non-retryable status, or transport failure on the
                final attempt.
        """
        attempts = self._settings.max_attempts
        last_body = ""

        for attempt in range(1, attempts + 1):
            try:
                response = self._client.send(build_request())
            except httpx.TransportError as exc:
                if attempt == attempts:
                    raise ProviderRequestError(
                        target.provider.value, f"transport failure: {exc}"
                    ) from exc
                self._sleep(self._backoff_delay(attempt))
                continue

            if response.status_code < 400:
                return response

            last_body = _truncate(response.text)

            if response.status_code in _RETRYABLE_STATUS and attempt < attempts:
                self._sleep(self._backoff_delay(attempt, response))
                continue

            if response.status_code == 429:
                # Terminal quota exhaustion. The error names the fallback provider,
                # because the useful next action is switching backends, not retrying.
                raise RateLimitError(
                    target.provider.value,
                    attempts,
                    alternatives=target.alternatives,
                    body=last_body,
                )

            raise ProviderRequestError(
                target.provider.value,
                "request failed",
                status_code=response.status_code,
                body=last_body,
            )

        # Unreachable: the loop either returns or raises on its final iteration.
        raise ProviderRequestError(
            target.provider.value, "exhausted retries", body=last_body
        )

    def _backoff_delay(self, attempt: int, response: httpx.Response | None = None) -> float:
        """Return how long to wait before the next attempt.

        A provider-supplied ``Retry-After`` always wins: it is authoritative about
        when quota resets, and ignoring it in favour of a shorter local guess is how a
        soft rate limit turns into a hard ban.

        Args:
            attempt: 1-based number of the attempt that just failed.
            response: Failed response, if there was one.

        Returns:
            Delay in seconds.
        """
        if response is not None:
            raw = response.headers.get("Retry-After")
            if raw:
                try:
                    return max(0.0, float(raw))
                except ValueError:
                    # Retry-After may be an HTTP date; fall through to backoff rather
                    # than growing a date parser for a header providers rarely send.
                    pass
        return self._settings.retry_base_delay_s * (2 ** (attempt - 1))

    # -- Response parsing ------------------------------------------------------------

    def _parse_completion(
        self, target: ResolvedTarget, response: httpx.Response, latency_ms: int
    ) -> Response:
        """Turn an OpenAI-compatible completion payload into a :class:`Response`.

        Args:
            target: Resolved target, supplying the price table and canonical repo id.
            response: Successful HTTP response.
            latency_ms: Measured call duration.

        Returns:
            A populated response with cost computed.

        Raises:
            ProviderRequestError: The payload is not JSON, or lacks a usable choice.
        """
        data = _parse_json(target.provider, response)

        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderRequestError(
                target.provider.value,
                f"response contained no usable choice: {exc}",
                status_code=response.status_code,
                body=_truncate(response.text),
            ) from exc

        raw_usage = data.get("usage") or {}
        usage = Usage(
            tokens_in=int(raw_usage.get("prompt_tokens") or 0),
            tokens_out=int(raw_usage.get("completion_tokens") or 0),
        )

        return Response(
            model_repo_id=target.model.repo_id,
            provider=target.provider,
            latency_ms=latency_ms,
            cached=False,
            cost_usd=compute_cost(target.model, usage=usage),
            text=text,
            usage=usage,
            finish_reason=choice.get("finish_reason"),
        )

    def _parse_embeddings(
        self,
        target: ResolvedTarget,
        response: httpx.Response,
        latency_ms: int,
        expected: int,
    ) -> Embeddings:
        """Turn an OpenAI-compatible embeddings payload into :class:`Embeddings`.

        The payload carries an ``index`` per vector, and it is honoured rather than
        trusted to arrive in order. Vectors are matched to texts positionally by every
        caller, so a reordered batch would attach each vector to the wrong chunk — and
        nothing downstream could detect it. The count is checked for the same reason.

        Args:
            target: Resolved target, supplying the price table and canonical repo id.
            response: Successful HTTP response.
            latency_ms: Measured call duration.
            expected: How many texts were submitted.

        Returns:
            Vectors in submission order, with cost computed.

        Raises:
            ProviderRequestError: The payload is not JSON, has no usable ``data``, or
                does not return exactly one vector per submitted text.
        """
        data = _parse_json(target.provider, response)

        raw_items = data.get("data")
        if not isinstance(raw_items, list) or not raw_items:
            raise ProviderRequestError(
                target.provider.value,
                "embeddings response contained no data",
                status_code=response.status_code,
                body=_truncate(response.text),
            )

        try:
            ordered = sorted(raw_items, key=lambda item: int(item.get("index", 0)))
            vectors = [[float(value) for value in item["embedding"]] for item in ordered]
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderRequestError(
                target.provider.value,
                f"embeddings response was not usable: {exc}",
                status_code=response.status_code,
                body=_truncate(response.text),
            ) from exc

        if len(vectors) != expected:
            raise ProviderRequestError(
                target.provider.value,
                f"asked for {expected} embeddings and got {len(vectors)}",
                status_code=response.status_code,
                body=_truncate(response.text),
            )

        raw_usage = data.get("usage") or {}
        usage = Usage(tokens_in=int(raw_usage.get("prompt_tokens") or 0))

        return Embeddings(
            model_repo_id=target.model.repo_id,
            provider=target.provider,
            latency_ms=latency_ms,
            cached=False,
            cost_usd=compute_cost(target.model, usage=usage),
            vectors=vectors,
            usage=usage,
        )

    def _parse_transcript(
        self, target: ResolvedTarget, response: httpx.Response, latency_ms: int
    ) -> Transcript:
        """Turn a ``verbose_json`` transcription payload into a :class:`Transcript`.

        Segments missing timestamps are dropped rather than defaulted to zero: a
        citation pointing at 0.0–0.0 is worse than no citation, because it looks valid.

        Args:
            target: Resolved target.
            response: Successful HTTP response.
            latency_ms: Measured call duration.

        Returns:
            A populated transcript with cost computed from audio duration.

        Raises:
            ProviderRequestError: The payload is not JSON or has no ``text`` field.
        """
        data = _parse_json(target.provider, response)

        if "text" not in data:
            raise ProviderRequestError(
                target.provider.value,
                "transcription response had no 'text' field",
                status_code=response.status_code,
                body=_truncate(response.text),
            )

        segments: list[TranscriptSegment] = []
        for raw in data.get("segments") or []:
            if not isinstance(raw, Mapping):
                continue
            start, end = raw.get("start"), raw.get("end")
            if start is None or end is None:
                continue
            segments.append(
                TranscriptSegment(
                    t_start=float(start),
                    t_end=float(end),
                    text=str(raw.get("text", "")),
                    speaker=raw.get("speaker"),
                )
            )

        audio_seconds = float(data.get("duration") or 0.0)

        return Transcript(
            model_repo_id=target.model.repo_id,
            provider=target.provider,
            latency_ms=latency_ms,
            cached=False,
            cost_usd=compute_cost(target.model, audio_seconds=audio_seconds),
            text=str(data["text"]),
            segments=segments,
            audio_seconds=audio_seconds,
            language=data.get("language"),
        )

    # -- Cache -----------------------------------------------------------------------

    def _read_cache[ResultT: AdapterResult](
        self, key: str, model_type: type[ResultT]
    ) -> ResultT | None:
        """Look up a cached result and re-stamp its call-specific fields.

        Three fields are overwritten on a hit:

        * ``cached=True`` — so the run log can distinguish a hit from a real call.
        * ``latency_ms`` — the measured cache read, not the original network call. The
          original number is already in the log from when it was first made; reporting
          it again would make a cached run look as slow as a cold one.
        * ``cost_usd=0.0`` — a hit spends no money. Token counts are deliberately left
          at their real values, which is what makes "what did the cache save us"
          answerable by replaying prices over ``cached=true`` records.

        Args:
            key: Cache key.
            model_type: Result type to validate into.

        Returns:
            The cached result, or ``None`` on a miss or an entry of the wrong shape.
        """
        started = self._monotonic()
        stored = self._cache.get(key)
        if stored is None:
            return None

        payload = stored.get("result")
        if not isinstance(payload, Mapping):
            return None

        try:
            return model_type.model_validate(
                {
                    **payload,
                    "cached": True,
                    "latency_ms": self._elapsed_ms(started),
                    "cost_usd": 0.0,
                }
            )
        except Exception:
            # A stale entry written by an older schema is a miss, not a failure.
            return None

    def _write_cache(self, key: str, result: AdapterResult) -> None:
        """Store a fresh result.

        Args:
            key: Cache key.
            result: Result to persist.
        """
        self._cache.put(key, {"result": result.model_dump(mode="json")})

    # -- Logging -----------------------------------------------------------------------

    def _finish[ResultT: AdapterResult](
        self, result: ResultT, context: RunContext | None
    ) -> ResultT:
        """Log a result and return it unchanged.

        Every exit path from :meth:`complete` and :meth:`transcribe` routes through
        here — cache hits included — which is what makes "one record per call" true by
        construction rather than by remembering.

        Args:
            result: Result about to be returned.
            context: Per-call provenance override, or ``None`` to use the adapter's.

        Returns:
            ``result``, unmodified.

        Raises:
            OSError: The run log could not be written. Deliberately not swallowed — an
                unmeasured run has failed at its purpose.
        """
        self._run_logger.log_result(result, context or self._context)
        return result

    def _elapsed_ms(self, started: float) -> int:
        """Return whole milliseconds elapsed since a monotonic start point.

        Args:
            started: Value previously returned by the injected monotonic clock.

        Returns:
            Non-negative duration in milliseconds.
        """
        return max(0, int((self._monotonic() - started) * 1000))


# -- Module helpers ------------------------------------------------------------------


def _compact(params: Mapping[str, Any]) -> dict[str, Any]:
    """Drop ``None`` values from a parameter mapping.

    Keeps unset options out of both the wire payload and the cache key. The cache
    benefit is the real one: omitting unset keys means introducing a new optional
    parameter later does not change the key of every existing call.

    Args:
        params: Candidate parameters.

    Returns:
        The mapping without ``None`` values.
    """
    return {name: value for name, value in params.items() if value is not None}


def _truncate(body: str, limit: int = 500) -> str:
    """Shorten a response body for safe inclusion in an error message.

    Args:
        body: Raw response text.
        limit: Maximum characters to keep.

    Returns:
        The body, truncated with an ellipsis marker if it was longer than ``limit``.
    """
    return body if len(body) <= limit else f"{body[:limit]}…"


def _parse_json(provider: Provider, response: httpx.Response) -> dict[str, Any]:
    """Decode a JSON object from a response.

    Args:
        provider: Provider name, for the error message.
        response: HTTP response.

    Returns:
        The decoded object.

    Raises:
        ProviderRequestError: The body is not JSON, or is not a JSON object.
    """
    try:
        data = response.json()
    except ValueError as exc:
        raise ProviderRequestError(
            provider.value,
            f"response was not valid JSON: {exc}",
            status_code=response.status_code,
            body=_truncate(response.text),
        ) from exc

    if not isinstance(data, dict):
        raise ProviderRequestError(
            provider.value,
            f"expected a JSON object, got {type(data).__name__}",
            status_code=response.status_code,
            body=_truncate(response.text),
        )
    return data
