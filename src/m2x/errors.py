"""Exception hierarchy for the m2x adapter layer.

Every failure a caller can reasonably be expected to handle gets its own type, so
feature code never has to parse an error message to decide what went wrong. All of
them derive from :class:`M2XError`, which means ``except M2XError`` is a safe outer
catch that will not accidentally swallow a ``KeyboardInterrupt`` or a bug-shaped
``TypeError``.
"""

from __future__ import annotations


class M2XError(Exception):
    """Base class for every error raised by this project."""


class ConfigError(M2XError):
    """The model registry or settings are missing, malformed, or self-inconsistent.

    Raised at load time rather than call time wherever possible — a typo in
    ``config/models.toml`` should fail immediately, not halfway through a run.
    """


class UnknownModelError(ConfigError):
    """The requested Hugging Face repo id is not present in the model registry.

    Carries the known ids so the message can tell the caller what *is* available
    instead of only what is not.
    """

    def __init__(self, model_repo_id: str, known: list[str]) -> None:
        self.model_repo_id = model_repo_id
        self.known = known
        listed = ", ".join(sorted(known)) or "<registry is empty>"
        super().__init__(
            f"Model {model_repo_id!r} is not in the registry. "
            f"Add it to config/models.toml. Known models: {listed}"
        )


class BannedModelError(M2XError):
    """The requested model is on the project's banned list.

    The course rule is that every model must be addressable by a Hugging Face repo
    id, which rules out Gemini and Groq's ``compound`` meta-models. This is checked
    before the registry lookup, so adding a banned model to the config file does not
    smuggle it past the rule.
    """

    def __init__(self, model_repo_id: str, reason: str) -> None:
        self.model_repo_id = model_repo_id
        self.reason = reason
        super().__init__(f"Model {model_repo_id!r} is banned: {reason}")


class ProviderNotConfiguredError(ConfigError):
    """A provider was requested that cannot serve the given model.

    Either the provider is absent from ``[providers]`` entirely, or the model's
    ``served_as`` table has no entry for it.
    """


class MissingCredentialError(ConfigError):
    """The provider needs an API key and none was found in the environment.

    Deliberately does not include the setting's value — only its name — so the
    message stays safe to paste into a ticket.
    """

    def __init__(self, provider: str, setting_name: str) -> None:
        self.provider = provider
        self.setting_name = setting_name
        super().__init__(
            f"Provider {provider!r} requires a credential but "
            f"{setting_name.upper()} is unset. Add it to your .env "
            f"(see .env.example for the variable names)."
        )


class CapabilityMismatchError(M2XError):
    """A model was used through the wrong method.

    Calling :meth:`~m2x.adapter.ModelAdapter.complete` with a transcription model
    (or vice versa) is a programming error, and the resulting provider-side failure
    is obscure enough to be worth catching ourselves.
    """


class ProviderRequestError(M2XError):
    """The provider returned a non-retryable HTTP error, or the response was unusable.

    Attributes:
        provider: Which provider failed.
        status_code: HTTP status, or ``None`` for transport-level failures.
        body: Truncated response body, useful in logs and safe to display.
    """

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        status_code: int | None = None,
        body: str = "",
    ) -> None:
        self.provider = provider
        self.status_code = status_code
        self.body = body
        status = f" (HTTP {status_code})" if status_code is not None else ""
        super().__init__(f"{provider}{status}: {message}")


class RateLimitError(ProviderRequestError):
    """The provider kept returning HTTP 429 after every retry was exhausted.

    Free-tier quota exhaustion is the single most likely way a run dies, so the
    message names the concrete escape hatch — switching providers via
    ``M2X_PROVIDER_OVERRIDE`` — rather than just reporting the status code.
    """

    def __init__(
        self,
        provider: str,
        attempts: int,
        *,
        alternatives: list[str] | None = None,
        body: str = "",
    ) -> None:
        self.attempts = attempts
        self.alternatives = alternatives or []
        if self.alternatives:
            hint = (
                "Fall back with M2X_PROVIDER_OVERRIDE="
                f"{self.alternatives[0]} (also available: "
                f"{', '.join(self.alternatives)})."
            )
        else:
            hint = "No alternative provider is configured for this model."
        super().__init__(
            provider,
            f"rate limited after {attempts} attempt(s). {hint}",
            status_code=429,
            body=body,
        )
