# The held-out set — OPEN, not sealed

> **Read this before quoting any Phase 1B number.** These ten cases are committed in
> plaintext. Everyone on the project can read them, including the Builder. **The set is
> therefore not blind, and a score computed against it is not a held-out score.**

## What changed, and when

M2X-041 (2026-08-13) sealed this directory with gpg ciphertext plus a SHA-256 manifest, so
that a reviewer could confirm the cases existed and were unedited while remaining unable to
read them.

Later the same day the supervisor decided the sets should be readable by the whole team.
Two ways to do that were on the table:

1. Seal them and publish the passphrase in the repo.
2. Do not seal them.

**Option 2 was chosen, and it is the better of the two.** Ciphertext plus a published key
reads as *sealed* to anyone checking the repository — a reviewer sees `.gpg` files and a
manifest and concludes the set was blind. It would not have been. That is a false artefact,
and a false artefact is worse than an honest absence, because only one of the two misleads
the person checking.

So: no encryption, no claim of encryption, and this note.

## What was given up

| property | before | now |
|---|---|---|
| the Builder cannot read the cases | claimed (by convention) | **no** |
| a Phase 1B number is a held-out number | claimed | **no** |
| the set can certify a prompt change | once, then burnt | **no — nothing to burn** |
| the ten cases are provably unedited | yes (manifest) | **yes (manifest)** |
| a fresh clone can run the gate | no | **yes** |

The bottom two rows are why this is not a pure loss. Confidentiality is gone; **integrity
is not**. `seal-manifest.json` still records a SHA-256 per case, and
`scripts/seal_heldout.py verify` still fails loudly if any case is edited, added or
removed. That is the property which stops a label being quietly adjusted after somebody has
seen a score — arguably the more important of the two for gate honesty, and the one an
ignored directory never had.

```bash
uv run python scripts/seal_heldout.py verify            # anyone, any time
uv run python scripts/seal_heldout.py manifest          # re-digest after adding cases
```

**Re-digest whenever a case legitimately changes**, and let the diff to
`seal-manifest.json` be the record that it did.

## What this means for the gate

M2X-040 was already recorded as **NOT RUN** for unrelated reasons — dev micro-F1 sits at
0.3882 against a 0.85 bar. This change adds a second, independent reason it cannot be run
as written: *there is no longer a held-out set to open once.*

**Certifying Phase 1B for real now requires fresh cases**, written by someone who has not
read the prompt, and kept genuinely private until the run. That is M2X-041's second half,
which was already outstanding. See [`../../../docs/gates.md`](../../../docs/gates.md).

## What was already true, and still is

These labels share an author with the prompt and the schema. Every number computed against
them was **an upper bound rather than a measurement** long before this change — see
[`../README.md`](../README.md) §"these labels are not independent". Opening the set does not
create that problem; it removes the one mitigation that was left.

## The tooling is kept

`scripts/seal_heldout.py` still works and is still tested. Nothing in this repository is
sealed with it today, but M2X-041's fresh five-case retry will need exactly it, and
deleting a working tool because it is temporarily unused is how the next person ends up
writing it again.
