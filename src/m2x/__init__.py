"""m2x — meeting to execution.

Public surface of the package. Importing from ``m2x`` directly keeps feature code
insulated from module layout, so the internals can be reorganised without a
repo-wide import rewrite::

    from m2x import Message, ModelAdapter, Role, RunContext

The one rule that matters for anything built on top of this package: all model calls
go through :class:`~m2x.adapter.ModelAdapter`. A direct HTTP call to a provider is
invisible to the run log, and an invisible call makes the cost report wrong.
"""

from __future__ import annotations

from m2x.adapter import ModelAdapter
from m2x.cache import ResponseCache, build_cache_key, digest_bytes
from m2x.errors import (
    BannedModelError,
    CapabilityMismatchError,
    ConfigError,
    M2XError,
    MissingCredentialError,
    ProviderNotConfiguredError,
    ProviderRequestError,
    RateLimitError,
    UnknownModelError,
)
from m2x.model_registry import ModelRegistry, ModelSpec, ProviderSpec, ResolvedTarget
from m2x.pipeline import (
    DEFAULT_TRANSCRIBE_MODEL,
    DEFAULT_TRANSCRIPTS_DIR,
    ProcessOutcome,
    load_transcript,
    process_meeting,
    write_transcript,
)
from m2x.pricing import compute_cost
from m2x.run_log import RunContext, RunLogger, RunRecord
from m2x.settings import Settings
from m2x.types import (
    AdapterResult,
    Message,
    ModelKind,
    Provider,
    Response,
    Role,
    Transcript,
    TranscriptSegment,
    Usage,
)

__version__ = "0.1.0"

__all__ = [
    # Adapter
    "ModelAdapter",
    # Configuration
    "Settings",
    "ModelRegistry",
    "ModelSpec",
    "ProviderSpec",
    "ResolvedTarget",
    # Pipeline
    "process_meeting",
    "ProcessOutcome",
    "load_transcript",
    "write_transcript",
    "DEFAULT_TRANSCRIBE_MODEL",
    "DEFAULT_TRANSCRIPTS_DIR",
    # Values
    "AdapterResult",
    "Message",
    "ModelKind",
    "Provider",
    "Response",
    "Role",
    "Transcript",
    "TranscriptSegment",
    "Usage",
    # Caching
    "ResponseCache",
    "build_cache_key",
    "digest_bytes",
    # Observability
    "RunContext",
    "RunLogger",
    "RunRecord",
    "compute_cost",
    # Errors
    "M2XError",
    "BannedModelError",
    "CapabilityMismatchError",
    "ConfigError",
    "MissingCredentialError",
    "ProviderNotConfiguredError",
    "ProviderRequestError",
    "RateLimitError",
    "UnknownModelError",
    "__version__",
]
