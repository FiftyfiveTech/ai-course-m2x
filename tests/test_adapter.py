"""Tests for :class:`~m2x.adapter.ModelAdapter`.

All HTTP is mocked, so these assert on the exact request the adapter would have sent —
URL, model alias, and headers. That is the only way to prove "switching provider is
configuration" without three live accounts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from m2x.adapter import ModelAdapter
from m2x.errors import (
    BannedModelError,
    CapabilityMismatchError,
    MissingCredentialError,
    ProviderNotConfiguredError,
    ProviderRequestError,
    RateLimitError,
    UnknownModelError,
)
from m2x.run_log import RunContext, RunLogger
from m2x.settings import Settings
from m2x.types import Message, Provider, Role
from conftest import CHAT_MODEL, TRANSCRIBE_MODEL, chat_response, transcription_response

AdapterFactory = Callable[..., ModelAdapter]

_PROMPT = [Message(role=Role.USER, content="summarise the meeting")]
_AUDIO = b"RIFF....fake wav bytes"


class _Recorder:
    """Mock transport handler that records requests and replays queued responses.

    The last queued response repeats once exhausted, so a test that only cares about
    the happy path can queue exactly one.
    """

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = list(responses) or [httpx.Response(200, json=chat_response())]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self._responses) - 1)
        outcome = self._responses[index]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @property
    def call_count(self) -> int:
        """How many HTTP attempts were made."""
        return len(self.requests)


def _ok(payload: dict[str, Any] | None = None) -> _Recorder:
    """A recorder that always answers 200 with a chat completion."""
    return _Recorder(httpx.Response(200, json=payload or chat_response()))


class TestRouting:
    """The same call, three backends, no code change."""

    @pytest.mark.parametrize(
        ("provider", "expected_url", "expected_alias"),
        [
            (
                Provider.GROQ,
                "https://groq.test/openai/v1/chat/completions",
                "llama-3.1-8b-instant",
            ),
            (Provider.NIM, "https://nim.test/v1/chat/completions", "meta/llama-3.1-8b-instruct"),
            (
                Provider.OLLAMA,
                "http://localhost:11434/v1/chat/completions",
                "hf.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF",
            ),
        ],
    )
    def test_each_provider_gets_its_own_url_and_alias(
        self,
        make_adapter: AdapterFactory,
        provider: Provider,
        expected_url: str,
        expected_alias: str,
    ) -> None:
        handler = _ok()
        adapter = make_adapter(handler)

        response = adapter.complete(_PROMPT, CHAT_MODEL, provider=provider)

        request = handler.requests[0]
        assert str(request.url) == expected_url
        assert expected_alias.encode() in request.content
        # The canonical repo id is what surfaces to callers and to the log.
        assert response.model_repo_id == CHAT_MODEL
        assert response.provider is provider

    def test_hosted_providers_send_a_bearer_token(self, make_adapter: AdapterFactory) -> None:
        handler = _ok()

        make_adapter(handler).complete(_PROMPT, CHAT_MODEL, provider=Provider.GROQ)

        assert handler.requests[0].headers["Authorization"] == "Bearer test-groq-key"

    def test_local_provider_sends_no_authorization_header(
        self, make_adapter: AdapterFactory
    ) -> None:
        """An empty bearer token is worse than none: some servers reject it."""
        handler = _ok()

        make_adapter(handler).complete(_PROMPT, CHAT_MODEL, provider=Provider.OLLAMA)

        assert "Authorization" not in handler.requests[0].headers

    def test_provider_override_setting_redirects_every_call(
        self, make_adapter: AdapterFactory, settings: Settings
    ) -> None:
        """The config flag the three-provider comparison actually uses."""
        handler = _ok()
        adapter = make_adapter(
            handler,
            settings_override=settings.model_copy(update={"provider_override": Provider.NIM}),
        )

        assert adapter.complete(_PROMPT, CHAT_MODEL).provider is Provider.NIM

    def test_explicit_provider_beats_the_override(
        self, make_adapter: AdapterFactory, settings: Settings
    ) -> None:
        handler = _ok()
        adapter = make_adapter(
            handler,
            settings_override=settings.model_copy(update={"provider_override": Provider.NIM}),
        )

        assert (
            adapter.complete(_PROMPT, CHAT_MODEL, provider=Provider.OLLAMA).provider
            is Provider.OLLAMA
        )

    def test_accepts_plain_dict_messages(self, make_adapter: AdapterFactory) -> None:
        handler = _ok()

        make_adapter(handler).complete([{"role": "user", "content": "hi"}], CHAT_MODEL)

        assert b'"role":"user"' in handler.requests[0].content

    def test_invalid_message_role_fails_before_any_request(
        self, make_adapter: AdapterFactory
    ) -> None:
        handler = _ok()

        with pytest.raises(ValueError):
            make_adapter(handler).complete([{"role": "wizard", "content": "hi"}], CHAT_MODEL)

        assert handler.call_count == 0


class TestGuardrails:
    """Every rejection happens before any quota is spent."""

    @pytest.mark.parametrize(
        "model_repo_id", ["google/gemini-1.5-pro", "groq/compound-beta"]
    )
    def test_banned_models_never_reach_the_network(
        self, make_adapter: AdapterFactory, model_repo_id: str
    ) -> None:
        handler = _ok()

        with pytest.raises(BannedModelError):
            make_adapter(handler).complete(_PROMPT, model_repo_id)

        assert handler.call_count == 0

    def test_unknown_model_is_rejected(self, make_adapter: AdapterFactory) -> None:
        with pytest.raises(UnknownModelError):
            make_adapter(_ok()).complete(_PROMPT, "nobody/nothing")

    def test_unroutable_provider_is_rejected(self, make_adapter: AdapterFactory) -> None:
        with pytest.raises(ProviderNotConfiguredError):
            make_adapter(_ok()).transcribe(_AUDIO, TRANSCRIBE_MODEL, provider=Provider.OLLAMA)

    def test_chat_method_rejects_a_transcription_model(
        self, make_adapter: AdapterFactory
    ) -> None:
        with pytest.raises(CapabilityMismatchError, match="complete"):
            make_adapter(_ok()).complete(_PROMPT, TRANSCRIBE_MODEL)

    def test_transcribe_method_rejects_a_chat_model(
        self, make_adapter: AdapterFactory
    ) -> None:
        with pytest.raises(CapabilityMismatchError, match="transcribe"):
            make_adapter(_ok()).transcribe(_AUDIO, CHAT_MODEL)

    def test_missing_credential_names_the_variable_not_the_value(
        self, make_adapter: AdapterFactory, settings: Settings
    ) -> None:
        handler = _ok()
        adapter = make_adapter(
            handler, settings_override=settings.model_copy(update={"groq_api_key": None})
        )

        with pytest.raises(MissingCredentialError, match="GROQ_API_KEY"):
            adapter.complete(_PROMPT, CHAT_MODEL, provider=Provider.GROQ)

        assert handler.call_count == 0

    def test_secrets_are_not_exposed_by_settings_repr(self, settings: Settings) -> None:
        """The PM cross-review claim — 'keys are never logged' — as an assertion."""
        assert "test-groq-key" not in repr(settings)
        assert "test-groq-key" not in str(settings.model_dump())


class TestCaching:
    def test_second_identical_call_is_a_cache_hit_with_no_network(
        self, make_adapter: AdapterFactory
    ) -> None:
        handler = _ok()
        adapter = make_adapter(handler)

        first = adapter.complete(_PROMPT, CHAT_MODEL)
        second = adapter.complete(_PROMPT, CHAT_MODEL)

        assert handler.call_count == 1
        assert first.cached is False
        assert second.cached is True
        assert second.text == first.text

    def test_cache_hit_costs_nothing_but_keeps_token_counts(
        self, make_adapter: AdapterFactory
    ) -> None:
        """cost_usd is money spent; tokens describe the payload. See run_log docs."""
        handler = _ok()
        adapter = make_adapter(handler)

        adapter.complete(_PROMPT, CHAT_MODEL)
        cached = adapter.complete(_PROMPT, CHAT_MODEL)

        assert cached.cost_usd == 0.0
        assert (cached.usage.tokens_in, cached.usage.tokens_out) == (11, 7)

    def test_switching_provider_does_not_hit_the_cache(
        self, make_adapter: AdapterFactory
    ) -> None:
        """The acceptance criterion that forces provider into the cache key.

        Three providers, same prompt: three real calls. If the cache ignored provider,
        this would be one call and two hits, and the comparison would be meaningless.
        """
        handler = _ok()
        adapter = make_adapter(handler)

        results = [
            adapter.complete(_PROMPT, CHAT_MODEL, provider=provider)
            for provider in (Provider.GROQ, Provider.NIM, Provider.OLLAMA)
        ]

        assert handler.call_count == 3
        assert all(result.cached is False for result in results)

    def test_changing_temperature_does_not_hit_the_cache(
        self, make_adapter: AdapterFactory
    ) -> None:
        handler = _ok()
        adapter = make_adapter(handler)

        adapter.complete(_PROMPT, CHAT_MODEL, temperature=0.0)
        adapter.complete(_PROMPT, CHAT_MODEL, temperature=0.7)

        assert handler.call_count == 2

    def test_disabled_cache_always_calls_out(
        self, make_adapter: AdapterFactory, settings: Settings
    ) -> None:
        handler = _ok()
        adapter = make_adapter(
            handler, settings_override=settings.model_copy(update={"cache_enabled": False})
        )

        adapter.complete(_PROMPT, CHAT_MODEL)
        adapter.complete(_PROMPT, CHAT_MODEL)

        assert handler.call_count == 2

    def test_transcription_is_cached_by_audio_content(
        self, make_adapter: AdapterFactory
    ) -> None:
        handler = _Recorder(httpx.Response(200, json=transcription_response()))
        adapter = make_adapter(handler)

        adapter.transcribe(_AUDIO, TRANSCRIBE_MODEL)
        cached = adapter.transcribe(_AUDIO, TRANSCRIBE_MODEL)

        assert handler.call_count == 1
        assert cached.cached is True
        assert len(cached.segments) == 2

    def test_different_audio_is_a_separate_entry(
        self, make_adapter: AdapterFactory
    ) -> None:
        handler = _Recorder(httpx.Response(200, json=transcription_response()))
        adapter = make_adapter(handler)

        adapter.transcribe(b"audio-one", TRANSCRIBE_MODEL)
        adapter.transcribe(b"audio-two", TRANSCRIBE_MODEL)

        assert handler.call_count == 2


class TestRetries:
    def test_retries_429_then_succeeds(
        self, make_adapter: AdapterFactory, sleep_calls: list[float]
    ) -> None:
        handler = _Recorder(
            httpx.Response(429, json={"error": "rate limited"}),
            httpx.Response(429, json={"error": "rate limited"}),
            httpx.Response(200, json=chat_response("recovered")),
        )

        response = make_adapter(handler).complete(_PROMPT, CHAT_MODEL)

        assert handler.call_count == 3
        assert response.text == "recovered"
        assert len(sleep_calls) == 2

    def test_exhausted_429_names_the_fallback_provider(
        self, make_adapter: AdapterFactory
    ) -> None:
        """Free-tier exhaustion is the likeliest run-killer, so the error is actionable."""
        handler = _Recorder(httpx.Response(429, json={"error": "rate limited"}))

        with pytest.raises(RateLimitError) as caught:
            make_adapter(handler).complete(_PROMPT, CHAT_MODEL, provider=Provider.GROQ)

        assert handler.call_count == 3
        message = str(caught.value)
        assert "M2X_PROVIDER_OVERRIDE=nim" in message
        assert caught.value.alternatives == ["nim", "ollama"]

    def test_retry_after_header_overrides_local_backoff(
        self, make_adapter: AdapterFactory, sleep_calls: list[float]
    ) -> None:
        """The provider is authoritative about when quota resets."""
        handler = _Recorder(
            httpx.Response(429, headers={"Retry-After": "7"}, json={}),
            httpx.Response(200, json=chat_response()),
        )

        make_adapter(handler).complete(_PROMPT, CHAT_MODEL)

        assert sleep_calls == [7.0]

    def test_unparseable_retry_after_falls_back_to_backoff(
        self, make_adapter: AdapterFactory, sleep_calls: list[float]
    ) -> None:
        handler = _Recorder(
            httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, json={}),
            httpx.Response(200, json=chat_response()),
        )

        make_adapter(handler).complete(_PROMPT, CHAT_MODEL)

        assert sleep_calls == [0.0]

    def test_server_errors_are_retried(self, make_adapter: AdapterFactory) -> None:
        handler = _Recorder(
            httpx.Response(503, text="upstream unavailable"),
            httpx.Response(200, json=chat_response()),
        )

        make_adapter(handler).complete(_PROMPT, CHAT_MODEL)

        assert handler.call_count == 2

    def test_client_errors_are_not_retried(self, make_adapter: AdapterFactory) -> None:
        """Retrying a malformed request just burns quota to fail again."""
        handler = _Recorder(httpx.Response(400, text="bad request"))

        with pytest.raises(ProviderRequestError) as caught:
            make_adapter(handler).complete(_PROMPT, CHAT_MODEL)

        assert handler.call_count == 1
        assert caught.value.status_code == 400

    def test_transport_errors_are_retried_then_reported(
        self, make_adapter: AdapterFactory
    ) -> None:
        handler = _Recorder(httpx.ConnectError("connection refused"))

        with pytest.raises(ProviderRequestError, match="transport failure"):
            make_adapter(handler).complete(_PROMPT, CHAT_MODEL)

        assert handler.call_count == 3

    def test_a_failed_call_writes_no_cache_entry(
        self, make_adapter: AdapterFactory
    ) -> None:
        """Otherwise a transient failure would be memoised forever."""
        handler = _Recorder(httpx.Response(400, text="bad request"))
        adapter = make_adapter(handler)

        with pytest.raises(ProviderRequestError):
            adapter.complete(_PROMPT, CHAT_MODEL)

        handler_two = _ok()
        make_adapter(handler_two).complete(_PROMPT, CHAT_MODEL)
        assert handler_two.call_count == 1


class TestResponseParsing:
    def test_text_and_usage_are_extracted(self, make_adapter: AdapterFactory) -> None:
        response = make_adapter(_ok(chat_response("the answer"))).complete(
            _PROMPT, CHAT_MODEL
        )

        assert response.text == "the answer"
        assert response.usage.tokens_in == 11
        assert response.usage.tokens_out == 7
        assert response.usage.total == 18

    def test_finish_reason_is_surfaced(self, make_adapter: AdapterFactory) -> None:
        """A truncated completion is the usual cause of downstream JSON-parse failures."""
        response = make_adapter(_ok(chat_response(finish_reason="length"))).complete(
            _PROMPT, CHAT_MODEL
        )

        assert response.finish_reason == "length"

    def test_missing_usage_block_is_tolerated(self, make_adapter: AdapterFactory) -> None:
        """Local Ollama builds do not always report usage."""
        payload = {"choices": [{"message": {"content": "hi"}}]}

        response = make_adapter(_ok(payload)).complete(_PROMPT, CHAT_MODEL)

        assert response.usage.tokens_in == 0

    def test_null_content_becomes_empty_string(self, make_adapter: AdapterFactory) -> None:
        payload = {"choices": [{"message": {"content": None}}]}

        assert make_adapter(_ok(payload)).complete(_PROMPT, CHAT_MODEL).text == ""

    def test_response_without_choices_is_an_error(
        self, make_adapter: AdapterFactory
    ) -> None:
        with pytest.raises(ProviderRequestError, match="no usable choice"):
            make_adapter(_ok({"choices": []})).complete(_PROMPT, CHAT_MODEL)

    def test_non_json_body_is_an_error(self, make_adapter: AdapterFactory) -> None:
        handler = _Recorder(httpx.Response(200, text="<html>gateway</html>"))

        with pytest.raises(ProviderRequestError, match="not valid JSON"):
            make_adapter(handler).complete(_PROMPT, CHAT_MODEL)

    def test_error_body_is_truncated(self, make_adapter: AdapterFactory) -> None:
        handler = _Recorder(httpx.Response(400, text="x" * 5_000))

        with pytest.raises(ProviderRequestError) as caught:
            make_adapter(handler).complete(_PROMPT, CHAT_MODEL)

        assert len(caught.value.body) < 1_000


class TestTranscription:
    def test_requests_verbose_json_multipart(self, make_adapter: AdapterFactory) -> None:
        """Timestamps are the product's spine, so plain text is never requested."""
        handler = _Recorder(httpx.Response(200, json=transcription_response()))

        make_adapter(handler).transcribe(_AUDIO, TRANSCRIBE_MODEL)

        request = handler.requests[0]
        assert str(request.url) == "https://groq.test/openai/v1/audio/transcriptions"
        assert request.headers["content-type"].startswith("multipart/form-data")
        assert b"verbose_json" in request.content
        assert b"whisper-large-v3" in request.content
        assert _AUDIO in request.content

    def test_segments_and_duration_are_parsed(self, make_adapter: AdapterFactory) -> None:
        handler = _Recorder(httpx.Response(200, json=transcription_response()))

        transcript = make_adapter(handler).transcribe(_AUDIO, TRANSCRIBE_MODEL)

        assert transcript.text == "the meeting begins"
        assert transcript.audio_seconds == pytest.approx(120.0)
        assert transcript.language == "en"
        assert [(s.t_start, s.t_end) for s in transcript.segments] == [
            (0.0, 2.5),
            (2.5, 4.0),
        ]

    def test_segment_without_end_timestamp_is_dropped(
        self, make_adapter: AdapterFactory
    ) -> None:
        """A citation reading 0.0–0.0 is worse than no citation: it looks valid."""
        handler = _Recorder(httpx.Response(200, json=transcription_response()))

        transcript = make_adapter(handler).transcribe(_AUDIO, TRANSCRIBE_MODEL)

        assert len(transcript.segments) == 2
        assert all("dropped" not in segment.text for segment in transcript.segments)

    def test_reads_audio_from_a_path(
        self, make_adapter: AdapterFactory, tmp_path: Path
    ) -> None:
        clip = tmp_path / "mtg-001.wav"
        clip.write_bytes(_AUDIO)
        handler = _Recorder(httpx.Response(200, json=transcription_response()))

        make_adapter(handler).transcribe(clip, TRANSCRIBE_MODEL)

        assert b"mtg-001.wav" in handler.requests[0].content

    def test_language_hint_is_forwarded(self, make_adapter: AdapterFactory) -> None:
        handler = _Recorder(httpx.Response(200, json=transcription_response()))

        make_adapter(handler).transcribe(_AUDIO, TRANSCRIBE_MODEL, language="en")

        assert b"en" in handler.requests[0].content

    def test_tokens_stay_zero_for_transcription(
        self, make_adapter: AdapterFactory
    ) -> None:
        handler = _Recorder(httpx.Response(200, json=transcription_response()))

        transcript = make_adapter(handler).transcribe(_AUDIO, TRANSCRIBE_MODEL)

        assert (transcript.usage.tokens_in, transcript.usage.tokens_out) == (0, 0)

    def test_response_without_text_is_an_error(self, make_adapter: AdapterFactory) -> None:
        handler = _Recorder(httpx.Response(200, json={"duration": 1.0}))

        with pytest.raises(ProviderRequestError, match="no 'text' field"):
            make_adapter(handler).transcribe(_AUDIO, TRANSCRIBE_MODEL)


class TestRunLogging:
    def test_every_call_writes_exactly_one_record(
        self, make_adapter: AdapterFactory, settings: Settings
    ) -> None:
        adapter = make_adapter(_ok())

        adapter.complete(_PROMPT, CHAT_MODEL)
        adapter.complete(_PROMPT, CHAT_MODEL, temperature=0.5)

        assert len(RunLogger(settings.runs_log_path).read_all()) == 2

    def test_cache_hits_are_logged_too(
        self, make_adapter: AdapterFactory, settings: Settings
    ) -> None:
        """record count == call count, hits included. Otherwise the log undercounts work."""
        adapter = make_adapter(_ok())

        adapter.complete(_PROMPT, CHAT_MODEL)
        adapter.complete(_PROMPT, CHAT_MODEL)

        records = RunLogger(settings.runs_log_path).read_all()

        assert [record.cached for record in records] == [False, True]
        assert records[1].cost_usd == 0.0

    def test_retries_produce_one_record_not_one_per_attempt(
        self, make_adapter: AdapterFactory, settings: Settings
    ) -> None:
        """The log counts calls, not HTTP attempts."""
        handler = _Recorder(
            httpx.Response(429, json={}), httpx.Response(200, json=chat_response())
        )

        make_adapter(handler).complete(_PROMPT, CHAT_MODEL)

        assert handler.call_count == 2
        assert len(RunLogger(settings.runs_log_path).read_all()) == 1

    def test_failed_calls_write_no_record(
        self, make_adapter: AdapterFactory, settings: Settings
    ) -> None:
        handler = _Recorder(httpx.Response(400, text="bad request"))

        with pytest.raises(ProviderRequestError):
            make_adapter(handler).complete(_PROMPT, CHAT_MODEL)

        assert RunLogger(settings.runs_log_path).read_all() == []

    def test_context_is_recorded(
        self, make_adapter: AdapterFactory, settings: Settings
    ) -> None:
        adapter = make_adapter(
            _ok(), context=RunContext(phase="phase-0", command="m2x process", meeting_id="mtg-001")
        )

        adapter.complete(_PROMPT, CHAT_MODEL)

        record = RunLogger(settings.runs_log_path).read_all()[0]
        assert (record.phase, record.command, record.meeting_id) == (
            "phase-0",
            "m2x process",
            "mtg-001",
        )

    def test_per_call_context_overrides_the_adapter_default(
        self, make_adapter: AdapterFactory, settings: Settings
    ) -> None:
        adapter = make_adapter(_ok(), context=RunContext(phase="phase-0", command="default"))

        adapter.complete(
            _PROMPT, CHAT_MODEL, context=RunContext(phase="phase-1", command="override")
        )

        record = RunLogger(settings.runs_log_path).read_all()[0]
        assert (record.phase, record.command) == ("phase-1", "override")

    def test_transcription_is_logged(
        self, make_adapter: AdapterFactory, settings: Settings
    ) -> None:
        handler = _Recorder(httpx.Response(200, json=transcription_response()))

        make_adapter(handler).transcribe(_AUDIO, TRANSCRIBE_MODEL)

        record = RunLogger(settings.runs_log_path).read_all()[0]
        assert record.model_repo_id == TRANSCRIBE_MODEL
        assert record.provider is Provider.GROQ


class TestLifecycle:
    def test_context_manager_closes_an_owned_client(self, settings: Settings, registry) -> None:
        with ModelAdapter(settings=settings, registry=registry) as adapter:
            client = adapter._client

        assert client.is_closed

    def test_injected_client_is_left_open(
        self, make_adapter: AdapterFactory
    ) -> None:
        """It belongs to the caller; closing it would break a shared-client test."""
        handler = _ok()
        adapter = make_adapter(handler)
        adapter.complete(_PROMPT, CHAT_MODEL)

        adapter.close()

        assert not adapter._client.is_closed
