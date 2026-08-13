#!/usr/bin/env python3
"""Seal the held-out label set so a gate run on it can be believed.

`eval/labels/README.md` recorded the seal as a *convention*: ten plaintext cases,
git-ignored, covered by an agreed do-not-open rule. Two things that convention cannot do,
both of which `CLAUDE.md` asks a gate for:

1. **Reproduce.** A fresh clone has no held-out set, so the supervisor cannot re-run the
   M2X-040 command and see the same number. An ignored directory is invisible, not shared.
2. **Prove.** Nothing shows the ten cases were unedited between the freeze and the gate.
   "It was followed" is a claim about people; a gate is supposed to rest on artefacts.

This script closes both with two artefacts that do different jobs:

* **`<case>.json.gpg`** — the case, symmetrically encrypted. Committed, so a fresh clone
  *has* the set and can run the gate once the passphrase holder unseals it. Recoverability.
* **`seal-manifest.json`** — SHA-256 of each plaintext, plus the case ids and the sealing
  date. Committed, readable by anyone, and **deterministic**. Integrity.

Both are needed, and the second is the one that actually proves anything. GPG's symmetric
mode salts every run, so re-encrypting identical plaintext produces different ciphertext:
a `.gpg` diff tells you a file was re-sealed, never whether its contents changed. The
manifest is a pure function of the plaintext, so an edited case shows up as a manifest
diff that no re-seal can hide. Ciphertext without digests would have let a case be edited
and re-encrypted silently.

**What this seal does not claim.** It was applied on 2026-08-13, after the cases were
written on 2026-08-12 and after dev iteration had already run on this machine. It proves
nothing about that window — only that from the sealing commit forward, an edit is visible.
That is strictly weaker than sealing at freeze time and is recorded rather than glossed:
see `eval/labels/heldout/README.md`. It is also unrelated to the deeper caveat that the
labels share an author with the prompt (`eval/labels/README.md` §"not independent").

Usage::

    uv run python scripts/seal_heldout.py manifest   # digests only, no passphrase
    uv run python scripts/seal_heldout.py seal       # encrypt + digests (passphrase)
    uv run python scripts/seal_heldout.py verify     # plaintext vs manifest, no passphrase
    uv run python scripts/seal_heldout.py unseal     # decrypt + verify (passphrase)

The passphrase is read from ``M2X_SEAL_PASSPHRASE`` when set, otherwise prompted for. It
is never written to disk, never passed as an argv element (which would put it in the
process table), and never recorded in the manifest.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

DEFAULT_HELDOUT_DIR = Path("eval/labels/heldout")
"""The sealed set. Plaintext here is git-ignored; ``*.gpg`` and the manifest are not."""

MANIFEST_NAME = "seal-manifest.json"
"""Committed integrity record. Named explicitly in ``.gitignore``'s whitelist."""

CIPHER_ALGO = "AES256"
"""Symmetric cipher. Named in the manifest so a reader need not guess at unseal time."""

MANIFEST_SCHEMA = 1
"""Bumped if the manifest layout changes, so an old manifest fails loudly."""


class SealError(RuntimeError):
    """A seal operation could not be completed honestly.

    Raised rather than returning a status because every caller of this script treats a
    partial seal as a failure: a set that is half-encrypted or half-verified certifies
    nothing, and continuing past it produces exactly the false confidence the seal exists
    to prevent.
    """


def digest(path: Path) -> str:
    """Hash one label file's bytes.

    Bytes rather than parsed JSON: re-serialising would normalise away whitespace and key
    order, so a file could be rewritten without changing its digest. The seal is about the
    file, not about a value the file happens to encode.

    Args:
        path: File to hash.

    Returns:
        Lowercase hex SHA-256.

    Raises:
        OSError: The file could not be read.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plaintext_cases(directory: Path) -> list[Path]:
    """List the plaintext label files, in a stable order.

    Args:
        directory: Held-out directory.

    Returns:
        ``*.json`` paths sorted by name, excluding the manifest itself.
    """
    return sorted(path for path in directory.glob("*.json") if path.name != MANIFEST_NAME)


def sealed_cases(directory: Path) -> list[Path]:
    """List the committed ciphertext files, in a stable order.

    Args:
        directory: Held-out directory.

    Returns:
        ``*.json.gpg`` paths sorted by name.
    """
    return sorted(directory.glob("*.json.gpg"))


def build_manifest(directory: Path, *, sealed_at: str) -> dict[str, object]:
    """Compute the integrity record for every plaintext case.

    Args:
        directory: Held-out directory holding the plaintext.
        sealed_at: ISO date the seal was applied, recorded verbatim.

    Returns:
        The manifest, ready to serialise.

    Raises:
        SealError: No plaintext cases were found, which would otherwise write an empty
            manifest that verifies vacuously against an empty directory.
        OSError: A case could not be read.
    """
    cases = plaintext_cases(directory)
    if not cases:
        raise SealError(
            f"no plaintext cases in {directory} — nothing to seal. An empty manifest "
            "would verify against an empty directory and prove nothing."
        )
    return {
        "schema": MANIFEST_SCHEMA,
        "sealed_at": sealed_at,
        "cipher": CIPHER_ALGO,
        "case_count": len(cases),
        "cases": [
            {"case_id": path.stem, "file": path.name, "sha256": digest(path)}
            for path in cases
        ],
    }


def write_manifest(manifest: dict[str, object], directory: Path) -> Path:
    """Serialise the manifest deterministically.

    Args:
        manifest: Manifest to write.
        directory: Held-out directory.

    Returns:
        The path written.

    Raises:
        OSError: The file could not be written.
    """
    path = directory / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def read_manifest(directory: Path) -> dict[str, object]:
    """Load the committed manifest.

    Args:
        directory: Held-out directory.

    Returns:
        The manifest.

    Raises:
        SealError: The manifest is absent or was written by a different schema version.
        OSError: The file could not be read.
    """
    path = directory / MANIFEST_NAME
    if not path.exists():
        raise SealError(f"{path} is missing — the set was never sealed")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise SealError(
            f"{path} has schema {manifest.get('schema')}, this script writes "
            f"{MANIFEST_SCHEMA} — re-seal rather than trusting a stale layout"
        )
    return manifest


def verify(directory: Path) -> list[str]:
    """Check the plaintext on disk against the committed digests.

    Runs without the passphrase on purpose: anyone reviewing the repository should be able
    to confirm the set is unedited without being able to read it.

    Args:
        directory: Held-out directory.

    Returns:
        Human-readable problems, empty when the set verifies.

    Raises:
        SealError: The manifest is unusable.
        OSError: A case could not be read.
    """
    manifest = read_manifest(directory)
    recorded = {entry["file"]: entry["sha256"] for entry in manifest["cases"]}  # type: ignore[index,union-attr]
    present = {path.name: path for path in plaintext_cases(directory)}

    problems = [
        f"{name}: sealed but missing from disk"
        for name in sorted(recorded.keys() - present.keys())
    ]
    problems += [f"{name}: on disk but not in the manifest" for name in sorted(present.keys() - recorded.keys())]
    problems += [
        f"{name}: digest changed since sealing — this case was edited"
        for name in sorted(recorded.keys() & present.keys())
        if digest(present[name]) != recorded[name]
    ]
    return problems


def resolve_gpg(explicit: str | None) -> str:
    """Locate the gpg binary.

    Args:
        explicit: ``--gpg`` value, or ``None`` to search ``PATH``.

    Returns:
        Path to the executable.

    Raises:
        SealError: No binary was found. The message names Git for Windows' copy, because
            on this project's machine gpg ships with git-bash and is absent from
            PowerShell's ``PATH`` — the failure otherwise reads as "gpg not installed".
    """
    found = explicit or shutil.which("gpg")
    if not found:
        raise SealError(
            "gpg not found on PATH. Git for Windows ships one at "
            r"C:\Program Files\Git\usr\bin\gpg.exe — pass it with --gpg."
        )
    return found


def passphrase() -> str:
    """Obtain the sealing passphrase.

    Returns:
        The passphrase.

    Raises:
        SealError: An empty passphrase was supplied, which gpg accepts and which would
            produce ciphertext anyone can open.
    """
    supplied = os.environ.get("M2X_SEAL_PASSPHRASE") or getpass.getpass("Seal passphrase: ")
    if not supplied:
        raise SealError("empty passphrase — that seals nothing")
    return supplied


def run_gpg(gpg: str, arguments: list[str], secret: str) -> None:
    """Invoke gpg with the passphrase on stdin.

    ``--passphrase-fd 0`` rather than ``--passphrase``: an argv element is visible to any
    process listing on the machine for the lifetime of the call.

    Args:
        gpg: Path to the binary.
        arguments: Arguments after the shared batch flags.
        secret: Passphrase, written to stdin.

    Raises:
        SealError: gpg exited non-zero. Its stderr is included.
    """
    completed = subprocess.run(
        [gpg, "--batch", "--yes", "--quiet", "--passphrase-fd", "0", *arguments],
        input=secret,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SealError(f"gpg failed: {completed.stderr.strip() or completed.returncode}")


def seal(directory: Path, *, gpg: str, sealed_at: str) -> tuple[int, Path]:
    """Encrypt every plaintext case and write the manifest.

    The manifest is written **after** the last successful encryption, so an interrupted
    run leaves no manifest rather than one describing a set that was only partly sealed.

    Args:
        directory: Held-out directory.
        gpg: Path to the gpg binary.
        sealed_at: ISO date to record.

    Returns:
        ``(cases_sealed, manifest_path)``.

    Raises:
        SealError: No plaintext was found, or gpg failed on a case.
        OSError: A file could not be read or written.
    """
    manifest = build_manifest(directory, sealed_at=sealed_at)
    secret = passphrase()
    for path in plaintext_cases(directory):
        run_gpg(
            gpg,
            ["--symmetric", "--cipher-algo", CIPHER_ALGO, "--output", str(path) + ".gpg", str(path)],
            secret,
        )
    return len(manifest["cases"]), write_manifest(manifest, directory)  # type: ignore[arg-type]


def unseal(directory: Path, *, gpg: str) -> list[str]:
    """Decrypt the committed ciphertext and verify it against the manifest.

    This is the gate-day command, and the verification half is the point: decrypting into
    a directory nobody checks would reproduce the original problem one level down.

    Args:
        directory: Held-out directory.
        gpg: Path to the gpg binary.

    Returns:
        Problems found by :func:`verify`, empty when the set is intact.

    Raises:
        SealError: No ciphertext was found, or gpg failed.
        OSError: A file could not be written.
    """
    ciphertext = sealed_cases(directory)
    if not ciphertext:
        raise SealError(f"no *.json.gpg in {directory} — nothing to unseal")
    secret = passphrase()
    for path in ciphertext:
        run_gpg(gpg, ["--decrypt", "--output", str(path.with_suffix("")), str(path)], secret)
    return verify(directory)


def main(argv: list[str] | None = None) -> int:
    """Run one seal operation.

    Args:
        argv: Arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        Process exit code. Non-zero on any failure, so a caller cannot record a green
        seal by reading past the output.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=("manifest", "seal", "verify", "unseal"))
    parser.add_argument("--dir", type=Path, default=DEFAULT_HELDOUT_DIR, help="held-out directory")
    parser.add_argument("--gpg", default=None, help="path to the gpg binary")
    parser.add_argument(
        "--sealed-at",
        default=date.today().isoformat(),
        help="ISO date recorded in the manifest (default: today)",
    )
    args = parser.parse_args(argv)

    try:
        if args.mode == "manifest":
            manifest = build_manifest(args.dir, sealed_at=args.sealed_at)
            path = write_manifest(manifest, args.dir)
            print(f"manifest: {manifest['case_count']} cases digested -> {path}")
            return 0

        if args.mode == "seal":
            count, path = seal(args.dir, gpg=resolve_gpg(args.gpg), sealed_at=args.sealed_at)
            print(f"sealed: {count} cases encrypted with {CIPHER_ALGO} -> {path}")
            return 0

        problems = verify(args.dir) if args.mode == "verify" else unseal(args.dir, gpg=resolve_gpg(args.gpg))
    except SealError as error:
        print(f"seal error: {error}", file=sys.stderr)
        return 2

    if problems:
        for problem in problems:
            print(f"FAIL {problem}", file=sys.stderr)
        return 1
    print(f"{args.mode}: OK — every sealed case is present and unedited")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
