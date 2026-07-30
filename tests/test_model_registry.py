"""Tests for routing, the banned-model rule, and registry validation."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from m2x.errors import (
    BannedModelError,
    ConfigError,
    ProviderNotConfiguredError,
    UnknownModelError,
)
from m2x.model_registry import ModelRegistry, assert_not_banned
from m2x.types import ModelKind, Provider

from conftest import CHAT_MODEL, REGISTRY_DATA, TRANSCRIBE_MODEL


class TestRouting:
    """One repo id, three providers, chosen by data rather than branching."""

    def test_defaults_to_the_models_own_provider(self, registry: ModelRegistry) -> None:
        target = registry.resolve(CHAT_MODEL)

        assert target.provider is Provider.GROQ
        assert target.served_as == "llama-3.1-8b-instant"
        assert target.base_url == "https://groq.test/openai/v1"
        assert target.api_key_setting == "groq_api_key"

    @pytest.mark.parametrize(
        ("provider", "expected_alias", "expected_base_url"),
        [
            (Provider.GROQ, "llama-3.1-8b-instant", "https://groq.test/openai/v1"),
            (Provider.NIM, "meta/llama-3.1-8b-instruct", "https://nim.test/v1"),
            (
                Provider.OLLAMA,
                "hf.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF",
                "http://localhost:11434/v1",
            ),
        ],
    )
    def test_same_repo_id_routes_to_each_provider_alias(
        self,
        registry: ModelRegistry,
        provider: Provider,
        expected_alias: str,
        expected_base_url: str,
    ) -> None:
        """The core promise: one canonical id, provider-specific names resolved for us."""
        target = registry.resolve(CHAT_MODEL, provider=provider)

        assert target.provider is provider
        assert target.served_as == expected_alias
        assert target.base_url == expected_base_url
        # The canonical id is what the run log records, regardless of the alias used.
        assert target.model.repo_id == CHAT_MODEL

    def test_local_provider_declares_no_credential(self, registry: ModelRegistry) -> None:
        assert registry.resolve(CHAT_MODEL, provider=Provider.OLLAMA).api_key_setting == ""

    def test_alternatives_exclude_the_chosen_provider(self, registry: ModelRegistry) -> None:
        """Drives the rate-limit fallback hint."""
        target = registry.resolve(CHAT_MODEL, provider=Provider.GROQ)

        assert target.alternatives == ["nim", "ollama"]

    def test_unservable_provider_is_an_error_not_a_silent_downgrade(
        self, registry: ModelRegistry
    ) -> None:
        """A comparison that claims three providers must fail if one did not run."""
        with pytest.raises(ProviderNotConfiguredError, match="cannot serve"):
            registry.resolve(TRANSCRIBE_MODEL, provider=Provider.OLLAMA)

    def test_unknown_model_names_what_is_available(self, registry: ModelRegistry) -> None:
        with pytest.raises(UnknownModelError) as caught:
            registry.resolve("nonexistent/model")

        assert CHAT_MODEL in str(caught.value)

    def test_kind_is_carried_through(self, registry: ModelRegistry) -> None:
        assert registry.resolve(CHAT_MODEL).model.kind is ModelKind.CHAT
        assert registry.resolve(TRANSCRIBE_MODEL).model.kind is ModelKind.TRANSCRIBE


class TestBannedModels:
    """The project rule: no HF repo id, no entry."""

    @pytest.mark.parametrize(
        "model_repo_id",
        [
            "google/gemini-1.5-pro",
            "GOOGLE/GEMINI-2.0-FLASH",
            "gemini-pro",
            "groq/compound-beta",
            "groq/compound-beta-mini",
        ],
    )
    def test_banned_ids_are_rejected(self, model_repo_id: str) -> None:
        with pytest.raises(BannedModelError, match="banned"):
            assert_not_banned(model_repo_id)

    @pytest.mark.parametrize(
        "model_repo_id",
        [CHAT_MODEL, TRANSCRIBE_MODEL, "mistralai/Mistral-7B-Instruct-v0.3"],
    )
    def test_permitted_ids_pass(self, model_repo_id: str) -> None:
        assert_not_banned(model_repo_id)

    def test_ban_beats_the_registry(self, registry: ModelRegistry) -> None:
        """Adding a banned model to config must not be enough to use it.

        The ban is checked before the lookup, so a banned id that is absent from the
        registry still reports the ban rather than "unknown model" — the reason it
        cannot be used is the rule, not the config.
        """
        with pytest.raises(BannedModelError):
            registry.resolve("google/gemini-1.5-pro")

    def test_banned_model_present_in_config_is_still_refused(self) -> None:
        data = copy.deepcopy(REGISTRY_DATA)
        data["models"]["google/gemini-1.5-pro"] = {
            "kind": "chat",
            "default_provider": "groq",
            "served_as": {"groq": "gemini-1.5-pro"},
        }

        with pytest.raises(BannedModelError):
            ModelRegistry.from_mapping(data).resolve("google/gemini-1.5-pro")


class TestValidation:
    """Config mistakes fail at load time, not mid-run."""

    def _mutated(self, mutate: Any) -> dict[str, Any]:
        data = copy.deepcopy(REGISTRY_DATA)
        mutate(data)
        return data

    def test_route_to_undeclared_provider_is_rejected(self) -> None:
        def mutate(data: dict[str, Any]) -> None:
            del data["providers"]["nim"]

        with pytest.raises(ConfigError, match="undeclared provider"):
            ModelRegistry.from_mapping(self._mutated(mutate))

    def test_default_provider_without_alias_is_rejected(self) -> None:
        def mutate(data: dict[str, Any]) -> None:
            del data["models"][CHAT_MODEL]["served_as"]["groq"]

        with pytest.raises(ConfigError, match="no served_as alias"):
            ModelRegistry.from_mapping(self._mutated(mutate))

    def test_model_with_no_providers_is_rejected(self) -> None:
        def mutate(data: dict[str, Any]) -> None:
            data["models"][CHAT_MODEL]["served_as"] = {}

        with pytest.raises(ConfigError, match="no served_as entries"):
            ModelRegistry.from_mapping(self._mutated(mutate))

    def test_unknown_provider_name_is_rejected(self) -> None:
        def mutate(data: dict[str, Any]) -> None:
            data["providers"]["bedrock"] = {"base_url": "https://x/v1"}

        with pytest.raises(ConfigError, match="Malformed model registry"):
            ModelRegistry.from_mapping(self._mutated(mutate))

    def test_trailing_slash_in_base_url_is_normalised(self) -> None:
        """Otherwise every request URL would contain a double slash."""

        def mutate(data: dict[str, Any]) -> None:
            data["providers"]["groq"]["base_url"] = "https://groq.test/openai/v1/"

        registry = ModelRegistry.from_mapping(self._mutated(mutate))

        assert registry.resolve(CHAT_MODEL).base_url == "https://groq.test/openai/v1"


class TestShippedConfig:
    """The committed config/models.toml must itself be valid.

    Without this, a typo in the real registry would only surface on a live call — and
    the fresh-clone gate would fail for a reason no test explained.
    """

    def test_repository_config_loads_and_routes(self) -> None:
        from pathlib import Path

        from m2x.settings import Settings

        config_path = Path(__file__).resolve().parents[1] / "config" / "models.toml"
        settings = Settings(_env_file=None, models_config_path=config_path)

        registry = ModelRegistry.from_settings(settings)

        assert registry.models, "shipped registry declares no models"
        for repo_id in registry.models:
            assert registry.resolve(repo_id).served_as

    def test_ollama_host_setting_overrides_shipped_base_url(self) -> None:
        from pathlib import Path

        from m2x.settings import Settings

        config_path = Path(__file__).resolve().parents[1] / "config" / "models.toml"
        settings = Settings(
            _env_file=None,
            models_config_path=config_path,
            ollama_host="http://gpu-box.local:9999",
        )

        registry = ModelRegistry.from_settings(settings)

        assert registry.providers[Provider.OLLAMA].base_url == "http://gpu-box.local:9999/v1"
