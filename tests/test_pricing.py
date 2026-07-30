"""Tests for cost arithmetic.

Every real price in this project is 0.0, so these tests inject deliberately non-zero
fake prices. A cost report that has only ever computed $0.00 has verified nothing —
the multiplication has to be proven independently of the free tier.
"""

from __future__ import annotations

import pytest

from m2x.model_registry import ModelSpec
from m2x.pricing import compute_cost
from m2x.types import ModelKind, Provider, Usage


def _chat_model(*, price_in: float = 0.0, price_out: float = 0.0) -> ModelSpec:
    """Build a chat model spec with the given per-1k-token prices."""
    return ModelSpec(
        repo_id="fake/chat-model",
        kind=ModelKind.CHAT,
        default_provider=Provider.GROQ,
        served_as={Provider.GROQ: "fake-chat"},
        price_per_1k_tokens_in=price_in,
        price_per_1k_tokens_out=price_out,
    )


def _audio_model(*, price_per_minute: float = 0.0) -> ModelSpec:
    """Build a transcription model spec with the given per-minute price."""
    return ModelSpec(
        repo_id="fake/audio-model",
        kind=ModelKind.TRANSCRIBE,
        default_provider=Provider.GROQ,
        served_as={Provider.GROQ: "fake-audio"},
        price_per_audio_minute=price_per_minute,
    )


class TestTokenCost:
    def test_free_tier_costs_nothing(self) -> None:
        cost = compute_cost(_chat_model(), usage=Usage(tokens_in=1_000, tokens_out=500))

        assert cost == 0.0

    def test_input_and_output_are_priced_separately(self) -> None:
        """Output is usually dearer than input; a single blended rate would be wrong."""
        model = _chat_model(price_in=0.5, price_out=1.5)

        cost = compute_cost(model, usage=Usage(tokens_in=2_000, tokens_out=1_000))

        # 2.0 * 0.5 (2k in) + 1.0 * 1.5 (1k out)
        assert cost == pytest.approx(2.5)

    def test_prices_are_per_thousand_tokens(self) -> None:
        model = _chat_model(price_in=1.0)

        assert compute_cost(model, usage=Usage(tokens_in=1)) == pytest.approx(0.001)

    def test_sub_cent_costs_are_not_rounded_away(self) -> None:
        """Rounding here would make many small calls sum to zero across a week."""
        model = _chat_model(price_in=0.0001)

        cost = compute_cost(model, usage=Usage(tokens_in=1))

        assert cost > 0.0

    def test_missing_usage_is_treated_as_zero_tokens(self) -> None:
        assert compute_cost(_chat_model(price_in=99.0)) == 0.0


class TestAudioCost:
    def test_audio_is_priced_per_minute(self) -> None:
        model = _audio_model(price_per_minute=0.02)

        cost = compute_cost(model, audio_seconds=120.0)

        assert cost == pytest.approx(0.04)

    def test_partial_minutes_are_prorated(self) -> None:
        model = _audio_model(price_per_minute=0.6)

        assert compute_cost(model, audio_seconds=30.0) == pytest.approx(0.3)

    def test_token_counters_do_not_contribute_for_audio_models(self) -> None:
        """Whisper reports no token usage, so cost must come from duration alone."""
        model = _audio_model(price_per_minute=0.02)

        with_tokens = compute_cost(model, usage=Usage(tokens_in=5_000), audio_seconds=60.0)
        without = compute_cost(model, audio_seconds=60.0)

        assert with_tokens == without == pytest.approx(0.02)

    def test_negative_duration_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="audio_seconds"):
            compute_cost(_audio_model(), audio_seconds=-1.0)
