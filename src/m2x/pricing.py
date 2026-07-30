"""Cost arithmetic for a single model call.

Every price in this project is currently 0.0, because the whole week runs on free
tiers. The multiplication still has to be correct: the cost-attribution report
projects what the week *would* have cost at real prices, and it does that by
replaying the run log against this function. So the tests inject deliberately
non-zero fake prices — a "cost report" that only ever reads $0.00 has verified
nothing.
"""

from __future__ import annotations

from m2x.model_registry import ModelSpec
from m2x.types import Usage

_TOKENS_PER_PRICE_UNIT = 1_000
"""Prices in the registry are quoted per 1,000 tokens."""

_SECONDS_PER_MINUTE = 60.0
"""Audio prices in the registry are quoted per minute."""


def compute_cost(
    model: ModelSpec,
    *,
    usage: Usage | None = None,
    audio_seconds: float = 0.0,
) -> float:
    """Return the USD cost of one call.

    Token cost and audio cost are summed rather than switched on ``model.kind``, so a
    hypothetical model billed both ways needs no change here. In practice exactly one
    term is non-zero: chat models report tokens and no duration, transcription models
    report duration and no tokens.

    Args:
        model: Registry entry supplying the price table.
        usage: Token counts. ``None`` is treated as zero tokens.
        audio_seconds: Duration of submitted audio, for per-minute billing.

    Returns:
        Cost in USD. Not rounded — rounding is a presentation concern, and rounding
        here would make many small calls sum to zero.

    Raises:
        ValueError: ``audio_seconds`` is negative.
    """
    if audio_seconds < 0:
        raise ValueError(f"audio_seconds must be >= 0, got {audio_seconds}")

    counts = usage or Usage()

    token_cost = (
        counts.tokens_in * model.price_per_1k_tokens_in
        + counts.tokens_out * model.price_per_1k_tokens_out
    ) / _TOKENS_PER_PRICE_UNIT

    audio_cost = (audio_seconds / _SECONDS_PER_MINUTE) * model.price_per_audio_minute

    return token_cost + audio_cost
