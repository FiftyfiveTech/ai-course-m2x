"""Tests for `.env`-shaped inputs to :class:`~m2x.settings.Settings`.

Separate from the adapter tests because what is under test here is the *setup path* a
fresh clone follows: `cp .env.example .env`, keys filled, everything else left blank.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from m2x.settings import Settings
from m2x.types import Provider


def test_blank_provider_override_is_no_override() -> None:
    """`.env.example` ships `M2X_PROVIDER_OVERRIDE=`; copying it must still boot."""
    settings = Settings(_env_file=None, M2X_PROVIDER_OVERRIDE="")

    assert settings.provider_override is None


def test_whitespace_provider_override_is_no_override() -> None:
    """A stray space after the `=` is the same mistake with a different shape."""
    assert Settings(_env_file=None, M2X_PROVIDER_OVERRIDE="   ").provider_override is None


def test_named_provider_override_still_applies() -> None:
    """The blank-tolerance must not swallow a real value."""
    settings = Settings(_env_file=None, M2X_PROVIDER_OVERRIDE="ollama")

    assert settings.provider_override is Provider.OLLAMA


def test_unknown_provider_override_still_fails() -> None:
    """A typo'd backend must not degrade silently into "no override"."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, M2X_PROVIDER_OVERRIDE="openai")


def test_env_example_is_loadable_as_an_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shipped example, keys filled in, loads without a single edit elsewhere.

    This is the fresh-clone contract asserted directly against the tracked file, so a
    future edit to `.env.example` that reintroduces an unparseable blank fails here
    rather than at the gate. The developer's own exported keys are cleared first —
    environment variables outrank an env file, and a machine that happens to export
    one would otherwise mask a broken example.
    """
    for name in ("GROQ_API_KEY", "NVIDIA_API_KEY", "HF_TOKEN", "M2X_PROVIDER_OVERRIDE"):
        monkeypatch.delenv(name, raising=False)

    example = Path(__file__).resolve().parents[1] / ".env.example"
    env_file = tmp_path / ".env"
    env_file.write_text(
        example.read_text(encoding="utf-8").replace("GROQ_API_KEY=", "GROQ_API_KEY=filled"),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.provider_override is None
    assert settings.groq_api_key is not None
    assert settings.groq_api_key.get_secret_value() == "filled"
