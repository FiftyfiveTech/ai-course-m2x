"""The routing table: Hugging Face repo id -> provider endpoint.

This module exists so that provider choice is *data*, not control flow. Feature code
names a model by its HF repo id and nothing else; the registry decides which endpoint
serves it and what that endpoint calls it. There is deliberately no
``if provider == "groq"`` branch anywhere in this project — when one appears, the
switching cost that this design pays for has been lost.

Loading is strict and eager: a malformed ``config/models.toml`` raises
:class:`~m2x.errors.ConfigError` at import-time-ish rather than surfacing as a
confusing 400 from a provider midway through a run.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from m2x.errors import (
    BannedModelError,
    ConfigError,
    ProviderNotConfiguredError,
    UnknownModelError,
)
from m2x.settings import Settings
from m2x.types import ModelKind, Provider

_BANNED_SUBSTRINGS: tuple[tuple[str, str], ...] = (
    ("gemini", "Gemini is not published under a Hugging Face repo id"),
)
"""Case-insensitive substrings that disqualify a model id."""

_BANNED_PREFIXES: tuple[tuple[str, str], ...] = (
    ("groq/compound", "Groq compound meta-models have no Hugging Face repo id"),
)
"""Case-insensitive prefixes that disqualify a model id."""


def assert_not_banned(model_repo_id: str) -> None:
    """Reject models the project rules forbid.

    The course constraint is that every model must be addressable by a Hugging Face
    repo id — that is what makes a run reproducible by someone else. Gemini and
    Groq's ``compound*`` meta-models fail that test.

    This check runs *before* the registry lookup on purpose: adding a banned model to
    ``config/models.toml`` must not be enough to use it.

    Args:
        model_repo_id: The id to check.

    Raises:
        BannedModelError: The id matches a banned pattern.
    """
    lowered = model_repo_id.lower()
    for prefix, reason in _BANNED_PREFIXES:
        if lowered.startswith(prefix):
            raise BannedModelError(model_repo_id, reason)
    for substring, reason in _BANNED_SUBSTRINGS:
        if substring in lowered:
            raise BannedModelError(model_repo_id, reason)


class ProviderSpec(BaseModel):
    """How to reach one serving backend."""

    model_config = ConfigDict(frozen=True)

    name: Provider
    base_url: str
    """OpenAI-compatible API root, including the ``/v1`` suffix, without a trailing slash."""

    api_key_setting: str = ""
    """Name of the :class:`~m2x.settings.Settings` field holding this provider's key.

    Empty string means the provider is unauthenticated — the local Ollama case.
    """


class ModelSpec(BaseModel):
    """Everything the registry knows about one model.

    Prices are per model rather than per provider. On free tiers they are all 0.0,
    and the arithmetic is verified by tests that inject a fake non-zero price. If a
    provider ever charges a different rate for the same weights, this is the field
    that has to grow a provider dimension.
    """

    model_config = ConfigDict(frozen=True)

    repo_id: str
    kind: ModelKind
    default_provider: Provider
    served_as: Mapping[Provider, str]
    """Provider -> the model name that provider's own API expects.

    The key set is also the authoritative answer to "who can serve this model": a
    provider absent from this mapping cannot be selected, even if it is configured.
    """

    price_per_1k_tokens_in: float = Field(default=0.0, ge=0.0)
    price_per_1k_tokens_out: float = Field(default=0.0, ge=0.0)
    price_per_audio_minute: float = Field(default=0.0, ge=0.0)
    """Cost basis for transcription models, which do not report token usage."""

    @property
    def providers(self) -> list[Provider]:
        """Providers able to serve this model, default first."""
        others = sorted(p for p in self.served_as if p is not self.default_provider)
        return [self.default_provider, *others]


class ResolvedTarget(BaseModel):
    """A model plus the concrete endpoint that will serve this particular call.

    Produced by :meth:`ModelRegistry.resolve`; consumed by the adapter, which needs
    no other routing knowledge.
    """

    model_config = ConfigDict(frozen=True)

    model: ModelSpec
    provider: Provider
    served_as: str
    """Provider-specific model name to put on the wire."""

    base_url: str
    api_key_setting: str

    @property
    def alternatives(self) -> list[str]:
        """Other providers that could serve this model, for rate-limit fallback hints."""
        return [p.value for p in self.model.providers if p is not self.provider]


class ModelRegistry(BaseModel):
    """Immutable, validated view of ``config/models.toml``."""

    model_config = ConfigDict(frozen=True)

    providers: Mapping[Provider, ProviderSpec]
    models: Mapping[str, ModelSpec]

    # -- Construction ------------------------------------------------------------

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> ModelRegistry:
        """Build a registry from already-parsed config data.

        Kept separate from :meth:`from_settings` so tests can construct a two-model
        registry inline without touching the filesystem.

        Args:
            data: Mapping with optional ``providers`` and ``models`` keys, shaped as
                in ``config/models.toml``.

        Returns:
            A validated registry.

        Raises:
            ConfigError: A provider or model entry is malformed, or a model routes to
                a provider that is not declared.
        """
        try:
            providers = {
                Provider(name): ProviderSpec(
                    name=Provider(name),
                    base_url=str(spec["base_url"]).rstrip("/"),
                    api_key_setting=str(spec.get("api_key_setting", "")),
                )
                for name, spec in dict(data.get("providers", {})).items()
            }
            models = {
                repo_id: ModelSpec(
                    repo_id=repo_id,
                    kind=ModelKind(spec["kind"]),
                    default_provider=Provider(spec["default_provider"]),
                    served_as={
                        Provider(p): str(alias)
                        for p, alias in dict(spec.get("served_as", {})).items()
                    },
                    price_per_1k_tokens_in=float(spec.get("price_per_1k_tokens_in", 0.0)),
                    price_per_1k_tokens_out=float(spec.get("price_per_1k_tokens_out", 0.0)),
                    price_per_audio_minute=float(spec.get("price_per_audio_minute", 0.0)),
                )
                for repo_id, spec in dict(data.get("models", {})).items()
            }
        except (KeyError, ValueError, TypeError) as exc:
            raise ConfigError(f"Malformed model registry: {exc}") from exc

        registry = cls(providers=providers, models=models)
        registry._assert_consistent()
        return registry

    @classmethod
    def from_settings(cls, settings: Settings) -> ModelRegistry:
        """Load the registry named by ``settings.models_config_path``.

        Also applies the ``OLLAMA_HOST`` override, which is the one piece of provider
        configuration allowed to come from the environment: the port a laptop serves
        on is machine-local, so forcing a config-file edit for it would put a
        machine-specific value into version control.

        Args:
            settings: Loaded settings.

        Returns:
            A validated registry.

        Raises:
            ConfigError: The file is missing or malformed.
        """
        path = settings.models_config_path
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ConfigError(
                f"Cannot read model registry at {path}: {exc}. "
                "Run from the repository root, or set M2X_MODELS_CONFIG."
            ) from exc

        try:
            data = tomllib.loads(raw.decode("utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

        registry = cls.from_mapping(data)

        ollama = registry.providers.get(Provider.OLLAMA)
        if ollama is not None and settings.ollama_host:
            patched = dict(registry.providers)
            patched[Provider.OLLAMA] = ollama.model_copy(
                update={"base_url": f"{settings.ollama_host.rstrip('/')}/v1"}
            )
            registry = cls(providers=patched, models=registry.models)

        return registry

    def _assert_consistent(self) -> None:
        """Fail fast on a registry that parses but cannot possibly work.

        Catches the two mistakes that are easy to make by hand: routing a model to a
        provider with no ``[providers]`` entry, and forgetting to give a model's own
        default provider a ``served_as`` alias.

        Raises:
            ConfigError: The registry is internally inconsistent.
        """
        for repo_id, model in self.models.items():
            if not model.served_as:
                raise ConfigError(
                    f"Model {repo_id!r} lists no served_as entries, so no provider can serve it."
                )
            if model.default_provider not in model.served_as:
                raise ConfigError(
                    f"Model {repo_id!r} defaults to provider "
                    f"{model.default_provider.value!r} but has no served_as alias for it."
                )
            for provider in model.served_as:
                if provider not in self.providers:
                    raise ConfigError(
                        f"Model {repo_id!r} routes to undeclared provider "
                        f"{provider.value!r}. Add a [providers.{provider.value}] section."
                    )

    # -- Lookup ------------------------------------------------------------------

    def resolve(
        self,
        model_repo_id: str,
        *,
        provider: Provider | None = None,
    ) -> ResolvedTarget:
        """Pick the endpoint that will serve ``model_repo_id``.

        Precedence is: explicit ``provider`` argument, then the model's
        ``default_provider``. A caller-supplied provider that cannot serve the model
        is an error rather than a silent downgrade to the default — a demo that
        claims to compare three providers must fail loudly if one of them did not
        actually run.

        Args:
            model_repo_id: Canonical Hugging Face repo id.
            provider: Force a specific backend. Usually the value of
                ``settings.provider_override``.

        Returns:
            The resolved target, ready for the adapter to call.

        Raises:
            BannedModelError: The model is on the project's banned list.
            UnknownModelError: The model is absent from the registry.
            ProviderNotConfiguredError: The requested provider cannot serve it.
        """
        assert_not_banned(model_repo_id)

        model = self.models.get(model_repo_id)
        if model is None:
            raise UnknownModelError(model_repo_id, list(self.models))

        chosen = provider or model.default_provider
        alias = model.served_as.get(chosen)
        if alias is None:
            servable = ", ".join(p.value for p in model.providers)
            raise ProviderNotConfiguredError(
                f"Provider {chosen.value!r} cannot serve {model_repo_id!r}. "
                f"Providers configured for this model: {servable}."
            )

        spec = self.providers[chosen]
        return ResolvedTarget(
            model=model,
            provider=chosen,
            served_as=alias,
            base_url=spec.base_url,
            api_key_setting=spec.api_key_setting,
        )
