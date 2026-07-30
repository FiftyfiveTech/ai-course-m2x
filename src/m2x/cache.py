"""Content-addressed response cache.

Caching is not an optimisation here, it is quota survival. Free-tier rate limits are
the most likely way a run dies, and re-running the same prompt while iterating on
downstream code is the most common way quota gets burned. A cache hit costs no
tokens, no quota, and no waiting.

**What goes into the key, and why each part must.**

============  ==========================================================================
``kind``      Chat and transcription payloads are unrelated; keeping the kind in the
              key means their address spaces can never collide.
``model``     Different weights, different answer. Obvious.
``provider``  *Not* obvious, and the subtle one. The same repo id served by Groq and
              by a quantised local Ollama build gives measurably different output, and
              measuring that difference is a project deliverable. If provider were
              omitted, running the same prompt across three providers would hit the
              cache on calls two and three and silently return the first provider's
              answer — the comparison would show three identical results and prove
              nothing.
``payload``   The messages, or a digest of the audio bytes.
``params``    Sampling settings. Omitting these is a silent-correctness bug: a caller
              asking for ``temperature=0.7`` would be handed a cached ``temperature=0``
              answer with no signal that it happened.
============  ==========================================================================

Entries are immutable and never invalidated. A cache directory is therefore safe to
delete at any time, and safe to keep forever; ``data/`` is git-ignored, so nothing
here is ever committed.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from m2x.types import Provider

_SHARD_WIDTH = 2
"""Leading hex characters used as a subdirectory, to avoid one directory with 10k files."""


def _canonical_json(value: Any) -> str:
    """Serialise ``value`` so equal content always produces an identical string.

    ``sort_keys`` is what makes the hash independent of dict insertion order; without
    it, two semantically identical requests would land at different cache addresses.

    Args:
        value: Any JSON-serialisable value.

    Returns:
        Compact, key-sorted JSON.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_bytes(payload: bytes) -> str:
    """Return the SHA-256 hex digest of raw bytes.

    Used for audio, which is far too large to embed in a cache key directly.

    Args:
        payload: Bytes to hash.

    Returns:
        64-character lowercase hex digest.
    """
    return hashlib.sha256(payload).hexdigest()


def build_cache_key(
    *,
    kind: str,
    model_repo_id: str,
    provider: Provider,
    payload: Any,
    params: Mapping[str, Any] | None = None,
) -> str:
    """Compute the cache address for one call.

    Args:
        kind: ``"chat"`` or ``"transcribe"``.
        model_repo_id: Canonical Hugging Face repo id.
        provider: Backend that will serve the call. See the module docstring for why
            this is part of the key.
        payload: Request content — a list of message dicts, or an audio digest.
        params: Sampling or format options that change the output.

    Returns:
        64-character lowercase hex digest.
    """
    material = _canonical_json(
        {
            "kind": kind,
            "model_repo_id": model_repo_id,
            "provider": provider.value,
            "payload": payload,
            "params": dict(params or {}),
        }
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class ResponseCache:
    """On-disk JSON cache keyed by content hash.

    Writes are atomic via :func:`os.replace`, so a crash mid-write cannot leave a
    half-written entry that later parses as valid JSON. Beyond that there is no
    locking, and none is needed: two processes computing the same key write
    byte-identical content, so last-writer-wins is indistinguishable from success.
    """

    def __init__(self, root: Path, *, enabled: bool = True) -> None:
        """
        Args:
            root: Cache directory. Created lazily on first write, so constructing a
                cache has no filesystem side effect.
            enabled: When false every read misses and every write is dropped. Used to
                force real network calls when measuring cold latency.
        """
        self._root = Path(root)
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        """Whether this cache does anything at all."""
        return self._enabled

    @property
    def root(self) -> Path:
        """Directory backing this cache."""
        return self._root

    def path_for(self, key: str) -> Path:
        """Return the file path holding ``key``.

        Args:
            key: Cache key from :func:`build_cache_key`.

        Returns:
            Sharded path of the form ``<root>/<first two chars>/<key>.json``.
        """
        return self._root / key[:_SHARD_WIDTH] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        """Read a cached payload.

        A corrupt or unreadable entry is treated as a miss rather than an error: the
        worst case is one wasted API call, whereas raising would turn a damaged cache
        file into a hard pipeline failure.

        Args:
            key: Cache key.

        Returns:
            The stored payload, or ``None`` on a miss.
        """
        if not self._enabled:
            return None

        path = self.path_for(key)
        try:
            with path.open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None

        return loaded if isinstance(loaded, dict) else None

    def put(self, key: str, payload: Mapping[str, Any]) -> None:
        """Store a payload atomically.

        Failures are swallowed on purpose. The cache is an accelerator, not a system
        of record — a full disk or a read-only mount should slow the run down, not
        break it. The authoritative record of what happened is the run log.

        Args:
            key: Cache key.
            payload: JSON-serialisable content to store.
        """
        if not self._enabled:
            return

        path = self.path_for(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write to a sibling temp file, then rename: os.replace is atomic on the
            # same filesystem, so readers only ever observe a complete entry.
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{key[:8]}-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(_canonical_json(payload))
                temp_name = handle.name
            os.replace(temp_name, path)
        except OSError:
            return
