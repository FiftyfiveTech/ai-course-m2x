"""Runtime settings, loaded from the environment and `.env`.

**The security boundary of this project is this module.** Credentials enter here and
nowhere else; the adapter asks :meth:`Settings.credential_for` for a key at request
time and never stores one on an instance. Every secret is typed :class:`SecretStr`,
so an accidental ``print(settings)``, an exception ``repr``, or a Pydantic
``model_dump()`` emits ``SecretStr('**********')`` rather than the key itself.

Non-secret configuration deliberately does *not* live here — providers, model
routing, and prices are in the tracked ``config/models.toml``. The split is: if a
supervisor cloning the repo needs it to reproduce a number, it goes in the tracked
config; if it would compromise an account, it goes in `.env`.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from m2x.errors import MissingCredentialError
from m2x.types import Provider


class Settings(BaseSettings):
    """Process-wide configuration.

    Field names are lower_snake_case; the environment variables they read are the
    upper-case equivalents. The ``M2X_``-prefixed knobs are project behaviour, and
    the bare names match the pre-existing ``.env.example`` contract.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # -- Credentials ---------------------------------------------------------------
    # Optional by design: a local-only Ollama run must work on a machine with no
    # hosted keys at all, so a missing key is an error only when a provider that
    # needs it is actually called.

    hf_token: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    nvidia_api_key: SecretStr | None = None
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://us.cloud.langfuse.com"

    # -- Provider selection --------------------------------------------------------

    provider_override: Provider | None = Field(
        default=None,
        validation_alias="M2X_PROVIDER_OVERRIDE",
    )
    """Force every call onto one provider, regardless of each model's default.

    This is the "switching provider is a config flag, not a refactor" lever: the
    three-provider demo runs the same prompt three times, changing only this value.
    A model that has no ``served_as`` entry for the overridden provider raises
    :class:`~m2x.errors.ProviderNotConfiguredError` rather than falling back
    silently, because a silent fallback would make the demo prove nothing.
    """

    @field_validator("provider_override", mode="before")
    @classmethod
    def _blank_override_means_unset(cls, value: object) -> object:
        """Treat an empty ``M2X_PROVIDER_OVERRIDE=`` as "no override".

        ``.env.example`` ships the key with an empty value, and the documented setup
        step is to copy that file. Without this, every fresh clone dies on a Pydantic
        enum error before it can make a single call — the exact failure the Phase 0
        gate's "zero undocumented steps" criterion is there to catch.

        Args:
            value: Raw value from the environment or `.env`.

        Returns:
            ``None`` for an empty or whitespace-only string; the value unchanged
            otherwise, so a genuinely invalid name still fails loudly.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    ollama_host: str = Field(
        default="http://localhost:11434",
        validation_alias="OLLAMA_HOST",
    )
    """Base host for local inference, without the ``/v1`` suffix.

    Overrides the ``ollama`` entry in the model registry so a laptop serving on a
    non-default port needs no config-file edit.
    """

    # -- Paths ---------------------------------------------------------------------
    # Relative by default so nothing depends on one machine's directory layout; the
    # fresh-clone gate is what enforces that.

    models_config_path: Path = Field(
        default=Path("config/models.toml"),
        validation_alias="M2X_MODELS_CONFIG",
    )
    cache_dir: Path = Field(
        default=Path("data/cache"),
        validation_alias="M2X_CACHE_DIR",
    )
    runs_log_path: Path = Field(
        default=Path("data/runs/runs.jsonl"),
        validation_alias="M2X_RUNS_LOG",
    )

    # -- Behaviour -----------------------------------------------------------------

    cache_enabled: bool = Field(default=True, validation_alias="M2X_CACHE_ENABLED")
    """Set false to force real network calls, e.g. when measuring cold latency."""

    max_attempts: int = Field(default=3, ge=1, validation_alias="M2X_MAX_ATTEMPTS")
    """Total tries per call, including the first. 3 means one call plus two retries."""

    retry_base_delay_s: float = Field(
        default=0.5,
        ge=0.0,
        validation_alias="M2X_RETRY_BASE_DELAY_S",
    )
    """First backoff delay; doubles per attempt. A ``Retry-After`` header wins over it."""

    request_timeout_s: float = Field(
        default=120.0,
        gt=0.0,
        validation_alias="M2X_REQUEST_TIMEOUT_S",
    )
    """Generous by default: local Ollama transcription of a long clip is slow."""

    def credential_for(self, provider: Provider, setting_name: str) -> str | None:
        """Return the plaintext credential for ``provider``, or ``None`` if none is needed.

        Args:
            provider: Provider being called, used only for the error message.
            setting_name: Name of the field holding the key, as declared by
                ``api_key_setting`` in the model registry. An empty string means the
                provider is unauthenticated (local Ollama), and ``None`` is returned.

        Returns:
            The secret's plaintext value, or ``None`` when no credential is required.

        Raises:
            MissingCredentialError: The provider requires a credential and the
                corresponding setting is unset or empty.

        Note:
            This is the only place a secret is unwrapped. Callers should use the
            return value immediately and never persist it.
        """
        if not setting_name:
            return None

        secret: SecretStr | None = getattr(self, setting_name, None)
        if secret is None or not secret.get_secret_value():
            raise MissingCredentialError(provider.value, setting_name)
        return secret.get_secret_value()
