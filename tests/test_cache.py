"""Tests for the content-addressed cache.

The key-composition tests are the important ones. Each asserts that some input changes
the address, and each maps to a concrete bug that omitting it would cause — those bugs
are named in the test docstrings, because "provider is in the key" is not obviously
necessary until you see what breaks without it.
"""

from __future__ import annotations

from pathlib import Path

from m2x.cache import ResponseCache, build_cache_key, digest_bytes
from m2x.types import Provider

_MESSAGES = [{"role": "user", "content": "summarise the meeting"}]


def _key(**overrides: object) -> str:
    """Build a cache key, defaulting every component."""
    base: dict[str, object] = {
        "kind": "chat",
        "model_repo_id": "meta-llama/Llama-3.1-8B-Instruct",
        "provider": Provider.GROQ,
        "payload": _MESSAGES,
        "params": {"temperature": 0.0},
    }
    base.update(overrides)
    return build_cache_key(**base)  # type: ignore[arg-type]


class TestKeyComposition:
    def test_identical_inputs_give_identical_keys(self) -> None:
        assert _key() == _key()

    def test_key_is_a_sha256_hex_digest(self) -> None:
        key = _key()

        assert len(key) == 64
        assert set(key) <= set("0123456789abcdef")

    def test_provider_changes_the_key(self) -> None:
        """Without this, the three-provider comparison proves nothing.

        The same repo id served by Groq and by a quantised local build gives different
        output, and measuring that gap is a deliverable. If provider were absent from
        the key, runs two and three would hit the cache and return run one's answer —
        three identical results, presented as a comparison.
        """
        assert _key(provider=Provider.GROQ) != _key(provider=Provider.NIM)
        assert _key(provider=Provider.NIM) != _key(provider=Provider.OLLAMA)

    def test_sampling_params_change_the_key(self) -> None:
        """Without this, a caller silently receives output from the wrong settings.

        Ask for temperature 0.7, get a cached temperature 0.0 answer, with nothing in
        the log to say it happened.
        """
        assert _key(params={"temperature": 0.0}) != _key(params={"temperature": 0.7})
        assert _key(params={"seed": 1}) != _key(params={"seed": 2})

    def test_payload_changes_the_key(self) -> None:
        assert _key() != _key(payload=[{"role": "user", "content": "something else"}])

    def test_model_changes_the_key(self) -> None:
        assert _key() != _key(model_repo_id="meta-llama/Llama-3.3-70B-Instruct")

    def test_kind_changes_the_key(self) -> None:
        """Keeps chat and transcription address spaces from ever colliding."""
        assert _key() != _key(kind="transcribe")

    def test_message_order_changes_the_key(self) -> None:
        """Conversation order is semantic, so it must not be normalised away."""
        reversed_messages = [
            {"role": "user", "content": "second"},
            {"role": "user", "content": "first"},
        ]
        forward_messages = list(reversed(reversed_messages))

        assert _key(payload=forward_messages) != _key(payload=reversed_messages)

    def test_param_declaration_order_does_not_change_the_key(self) -> None:
        """Keys sort, so dict construction order cannot fragment the cache."""
        assert _key(params={"temperature": 0.0, "seed": 7}) == _key(
            params={"seed": 7, "temperature": 0.0}
        )

    def test_absent_param_differs_from_explicit_none_free_default(self) -> None:
        """Dropping unset params keeps existing keys stable when a new option is added."""
        assert _key(params={}) != _key(params={"temperature": 0.0})


class TestDigestBytes:
    def test_same_bytes_same_digest(self) -> None:
        assert digest_bytes(b"audio") == digest_bytes(b"audio")

    def test_different_bytes_different_digest(self) -> None:
        assert digest_bytes(b"audio-a") != digest_bytes(b"audio-b")


class TestStorage:
    def test_round_trip(self, tmp_path: Path) -> None:
        cache = ResponseCache(tmp_path)
        cache.put("abc123", {"result": {"text": "hello"}})

        assert cache.get("abc123") == {"result": {"text": "hello"}}

    def test_miss_returns_none(self, tmp_path: Path) -> None:
        assert ResponseCache(tmp_path).get("never-written") is None

    def test_construction_touches_no_filesystem(self, tmp_path: Path) -> None:
        """Building an adapter must not create directories as a side effect."""
        root = tmp_path / "not-yet"
        ResponseCache(root)

        assert not root.exists()

    def test_entries_are_sharded_by_key_prefix(self, tmp_path: Path) -> None:
        """Avoids a single directory accumulating thousands of files."""
        cache = ResponseCache(tmp_path)
        cache.put("ab" + "c" * 62, {"result": {}})

        assert (tmp_path / "ab").is_dir()
        assert cache.path_for("ab" + "c" * 62).parent.name == "ab"

    def test_no_temp_files_survive_a_write(self, tmp_path: Path) -> None:
        """Proves the atomic rename completed rather than leaving debris behind."""
        cache = ResponseCache(tmp_path)
        cache.put("abc123", {"result": {"text": "hello"}})

        assert [p.name for p in cache.path_for("abc123").parent.iterdir()] == [
            "abc123.json"
        ]

    def test_corrupt_entry_reads_as_a_miss(self, tmp_path: Path) -> None:
        """A damaged cache file should cost one API call, not break the pipeline."""
        cache = ResponseCache(tmp_path)
        path = cache.path_for("abc123")
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")

        assert cache.get("abc123") is None

    def test_non_object_entry_reads_as_a_miss(self, tmp_path: Path) -> None:
        cache = ResponseCache(tmp_path)
        path = cache.path_for("abc123")
        path.parent.mkdir(parents=True)
        path.write_text("[1, 2, 3]", encoding="utf-8")

        assert cache.get("abc123") is None

    def test_overwriting_a_key_is_allowed(self, tmp_path: Path) -> None:
        """Concurrent writers of the same key write identical content, so this is safe."""
        cache = ResponseCache(tmp_path)
        cache.put("abc123", {"result": {"n": 1}})
        cache.put("abc123", {"result": {"n": 2}})

        assert cache.get("abc123") == {"result": {"n": 2}}


class TestDisabled:
    def test_disabled_cache_never_reads(self, tmp_path: Path) -> None:
        enabled = ResponseCache(tmp_path, enabled=True)
        enabled.put("abc123", {"result": {"text": "hello"}})

        assert ResponseCache(tmp_path, enabled=False).get("abc123") is None

    def test_disabled_cache_never_writes(self, tmp_path: Path) -> None:
        disabled = ResponseCache(tmp_path, enabled=False)
        disabled.put("abc123", {"result": {"text": "hello"}})

        assert not disabled.path_for("abc123").exists()
        assert ResponseCache(tmp_path, enabled=True).get("abc123") is None

    def test_enabled_flag_is_exposed(self, tmp_path: Path) -> None:
        assert ResponseCache(tmp_path).enabled is True
        assert ResponseCache(tmp_path, enabled=False).enabled is False
