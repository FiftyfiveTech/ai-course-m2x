# The sealed held-out set

Ten labelled cases that certify Phase 1B exactly once, at the M2X-040 gate, and are
**burnt** afterwards. Which ten is public — [`../cases.json`](../cases.json) pins every
case id and its bounds. What they contain is not.

This directory holds three kinds of file and treats them differently:

| | in git? | what it is for |
|---|---|---|
| `<case>.json` | **never** — git-ignored | the plaintext label, on the Evaluator's machine only |
| `<case>.json.gpg` | **yes** | the same case, encrypted. Recoverability |
| `seal-manifest.json` | **yes** | SHA-256 per case. Integrity |

## Why both artefacts

They answer different questions and neither answers the other's.

The **ciphertext** makes the set recoverable. Without it, a fresh clone has no held-out
cases at all, so the supervisor cannot re-run the gate command and see the same number —
which [`CLAUDE.md`](../../../CLAUDE.md) requires before a gate counts.

The **manifest** makes the set checkable, and it is the artefact that actually proves
something. GPG's symmetric mode salts every invocation, so encrypting identical plaintext
twice produces different ciphertext: a `.gpg` diff tells you a file was re-sealed, never
whether its contents changed. A case could be edited, re-encrypted, and committed with a
diff indistinguishable from a routine re-seal. The manifest is a pure function of the
plaintext bytes, so an edit shows as a manifest diff that no re-seal can hide.

Digests leak nothing, so `verify` runs **without the passphrase**. Anyone reviewing the
repository can confirm the ten cases are unedited while remaining unable to read them.

## What this seal does not claim

It was applied on **2026-08-13** (M2X-041), after the cases were written on 2026-08-12 and
after dev iteration had already run on this machine. It proves nothing about that window.
From the sealing commit forward, an edit is visible; before it, the set rested on the
convention recorded in [`../README.md`](../README.md), and that history cannot be
retrofitted. Sealing at freeze time would have been strictly stronger.

This is separate from — and smaller than — the caveat that these labels share an author
with the prompt and the schema. See [`../README.md`](../README.md) §"these labels are not
independent". A perfect seal on a non-independent set still yields an upper bound.

## Commands

```bash
uv run python scripts/seal_heldout.py verify     # no passphrase; anyone can run this
uv run python scripts/seal_heldout.py manifest   # re-digest after adding fresh cases
uv run python scripts/seal_heldout.py seal       # encrypt + digest (Evaluator only)
uv run python scripts/seal_heldout.py unseal     # gate day: decrypt, then verify
```

`gpg` ships with Git for Windows and is absent from PowerShell's `PATH`; pass
`--gpg "C:\Program Files\Git\usr\bin\gpg.exe"` there. The passphrase is read from
`M2X_SEAL_PASSPHRASE` when set, otherwise prompted for — never in argv, never on disk,
never in the manifest.

**The passphrase is held by the Evaluator alone.** If it is lost the ciphertext is
unopenable and the set is gone; that is the intended failure mode, and it is why the
manifest is committed separately in plaintext.

## Protocol

1. Freeze the prompt version and SHA under test. Nothing after this point counts for the
   run.
2. `unseal` — and read the verify result before the score. A set that fails verification
   certifies nothing.
3. Run the eval **once**. Record the number in [`../../../docs/gates.md`](../../../docs/gates.md)
   whatever it is, beside the scored-case set (a provider failure changes the denominator —
   see [`../../README.md`](../../README.md) §6).
4. Delete the decrypted plaintext. Mark the set burnt.

After step 4 these ten cases can never certify a later prompt change: a failure teaches
you what the set contains, and iterating against that is the failure mode the whole
apparatus exists to prevent. Certifying any subsequent fix needs *fresh* cases, sealed the
same way, which is M2X-041's other half.
