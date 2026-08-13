"""Tests for the held-out seal.

What is under test is the *integrity claim*, not encryption: gpg is a dependency, not our
code, and a test that shells out to it would measure whether the machine has gpg. So the
gpg calls are driven through a stub binary and the assertions concentrate on the one thing
the seal exists to do — make an edited case impossible to hide.

The failure this guards against is a seal that reports OK on a set that changed, which is
worse than no seal at all: it converts an unenforced convention into a false artefact.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from seal_heldout import (
    MANIFEST_NAME,
    MANIFEST_SCHEMA,
    SealError,
    build_manifest,
    digest,
    main,
    plaintext_cases,
    read_manifest,
    verify,
    write_manifest,
)

SEALED_AT = "2026-08-13"
"""Fixed date, so the manifest under test is not a function of the clock."""


def _case(directory: Path, case_id: str, *, body: str = "settled") -> Path:
    """Write one plausible plaintext case.

    Args:
        directory: Destination, created if absent.
        case_id: Case id, used as the file stem.
        body: Content that distinguishes one case from another.

    Returns:
        The path written.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{case_id}.json"
    path.write_text(json.dumps({"case_id": case_id, "notes": body}) + "\n", encoding="utf-8")
    return path


def _sealed(directory: Path, count: int = 3) -> Path:
    """Build a sealed directory: `count` cases plus a manifest over them.

    Args:
        directory: Destination.
        count: How many cases.

    Returns:
        The directory.
    """
    for index in range(count):
        _case(directory, f"tiron-MTG_{index:05d}-c01")
    write_manifest(build_manifest(directory, sealed_at=SEALED_AT), directory)
    return directory


def test_verify_passes_on_an_untouched_set(tmp_path: Path) -> None:
    """The baseline: seal then verify, nothing changed, no problems."""
    assert verify(_sealed(tmp_path)) == []


def test_verify_catches_an_edited_case(tmp_path: Path) -> None:
    """The whole point. A case edited after sealing must be named, not averaged away."""
    directory = _sealed(tmp_path)

    _case(directory, "tiron-MTG_00001-c01", body="quietly reworded")

    problems = verify(directory)
    assert len(problems) == 1
    assert "tiron-MTG_00001-c01" in problems[0]
    assert "edited" in problems[0]


def test_verify_catches_a_whitespace_only_edit(tmp_path: Path) -> None:
    """Digests cover bytes, not parsed JSON.

    Re-serialising would normalise whitespace and key order, so a file could be rewritten
    without moving its digest. A reformat is not a content change, but the seal is a claim
    about the file and must not quietly decide which edits count.
    """
    directory = _sealed(tmp_path)
    path = directory / "tiron-MTG_00000-c01.json"

    path.write_text(path.read_text(encoding="utf-8").replace(", ", ",  "), encoding="utf-8")

    assert [problem for problem in verify(directory) if "edited" in problem]


def test_verify_catches_a_removed_case(tmp_path: Path) -> None:
    """A set that shrank certifies a different denominator than the one recorded."""
    directory = _sealed(tmp_path)

    (directory / "tiron-MTG_00002-c01.json").unlink()

    assert verify(directory) == ["tiron-MTG_00002-c01.json: sealed but missing from disk"]


def test_verify_catches_an_added_case(tmp_path: Path) -> None:
    """An unsealed case appearing later is the cheapest way to dilute a held-out set."""
    directory = _sealed(tmp_path)

    _case(directory, "tiron-MTG_09999-c01")

    assert verify(directory) == ["tiron-MTG_09999-c01.json: on disk but not in the manifest"]


def test_manifest_is_deterministic(tmp_path: Path) -> None:
    """Two runs over the same plaintext produce byte-identical manifests.

    This is why the manifest carries the integrity claim and the ciphertext does not: gpg
    salts every run, so a `.gpg` diff cannot distinguish a re-seal from a content change.
    """
    directory = _sealed(tmp_path)

    first = json.dumps(build_manifest(directory, sealed_at=SEALED_AT), indent=2)
    second = json.dumps(build_manifest(directory, sealed_at=SEALED_AT), indent=2)

    assert first == second


def test_manifest_excludes_itself(tmp_path: Path) -> None:
    """Digesting the manifest into the manifest cannot terminate; it must be skipped."""
    directory = _sealed(tmp_path)

    files = {entry["file"] for entry in build_manifest(directory, sealed_at=SEALED_AT)["cases"]}

    assert MANIFEST_NAME not in files
    assert MANIFEST_NAME not in {path.name for path in plaintext_cases(directory)}


def test_manifest_refuses_an_empty_directory(tmp_path: Path) -> None:
    """An empty manifest verifies against an empty directory and proves nothing.

    A gate that reports OK because there was nothing to check is the exact failure this
    apparatus exists to stop, so it fails loudly instead.
    """
    tmp_path.mkdir(exist_ok=True)

    with pytest.raises(SealError, match="nothing to seal"):
        build_manifest(tmp_path, sealed_at=SEALED_AT)


def test_verify_refuses_an_unsealed_directory(tmp_path: Path) -> None:
    """No manifest means no claim — not a silent pass."""
    _case(tmp_path, "tiron-MTG_00000-c01")

    with pytest.raises(SealError, match="never sealed"):
        verify(tmp_path)


def test_verify_refuses_a_stale_manifest_schema(tmp_path: Path) -> None:
    """A manifest from another layout is not evidence about this one."""
    directory = _sealed(tmp_path)
    path = directory / MANIFEST_NAME
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["schema"] = MANIFEST_SCHEMA + 1
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SealError, match="re-seal"):
        read_manifest(directory)


def test_digest_is_stable_across_reads(tmp_path: Path) -> None:
    """Hashing is a pure function of the bytes, which is what makes the manifest checkable."""
    path = _case(tmp_path, "tiron-MTG_00000-c01")

    assert digest(path) == digest(path)


def test_cli_exits_non_zero_when_a_case_was_edited(tmp_path: Path) -> None:
    """Exit code, not printed text, is what a reviewer's shell reads.

    A seal that prints FAIL and exits 0 gets recorded green by any script that checks the
    status, so the exit code is part of the contract.
    """
    directory = _sealed(tmp_path)
    _case(directory, "tiron-MTG_00000-c01", body="changed")

    assert main(["verify", "--dir", str(directory)]) == 1


def test_cli_verify_passes_on_an_untouched_set(tmp_path: Path) -> None:
    """The green path, through the same entry point a reviewer uses."""
    assert main(["verify", "--dir", str(_sealed(tmp_path))]) == 0


def test_cli_manifest_mode_needs_no_passphrase(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Digesting must work for someone who cannot open the set.

    Anyone reviewing the repository should be able to confirm the cases are unedited
    without being able to read them — that separation is what makes the seal reviewable.
    """
    for index in range(2):
        _case(tmp_path, f"tiron-MTG_{index:05d}-c01")
    monkeypatch.delenv("M2X_SEAL_PASSPHRASE", raising=False)
    monkeypatch.setattr(
        "getpass.getpass",
        lambda *_: pytest.fail("manifest mode must not ask for a passphrase"),
    )

    assert main(["manifest", "--dir", str(tmp_path), "--sealed-at", SEALED_AT]) == 0
    assert read_manifest(tmp_path)["case_count"] == 2
