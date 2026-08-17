# Gate records

A gate passes when the listed command, re-run by the supervisor on a fresh clone, prints the
listed number. Claimed ≠ verified.

| date | phase | command | git SHA | number | pass/fail |
|------|-------|---------|---------|--------|-----------|
| 2026-08-04 | Phase 0 | `make run` then `make run-local` | `85f80d4` | hosted 395 ms vs local 141,339 ms — **358×** | **PASS** |
| 2026-08-12 | Phase 1 | `uv run python eval/validate_transcripts.py` | `e392922` | **3/3** speaker-attributed and schema-valid | **PASS** |
| 2026-08-13 | Phase 1B | *not run — and the set is now open, so it cannot be* | `066253b` | dev 0.3882 against a 0.85 bar; the held-out set was later committed in plaintext | **NOT RUN** |

## Phase 1 — 2026-08-12, SHA `e392922`

**Criterion (PRD §5):** "Speaker-attributed, timestamped transcript on ≥3 sample meetings;
differences documented."

The comparison doc, the five adoption decisions and the four known limitations are in
[`phase1-comparison.md`](phase1-comparison.md). This record is the evidence.

```
uv run python eval/validate_transcripts.py

PASS  mtg-001  104 segments · 5 speakers ·  99.0% attributed · 1050s · 5 empty-text (112s)
PASS  mtg-002   64 segments · 4 speakers · 100.0% attributed ·  397s · 2 empty-text (33s)
PASS  ami-001  582 segments · 9 speakers ·  99.0% attributed · 1785s · 1 empty-text (0s)

3/3 speaker-attributed, schema-valid
```

Exit 1 on any failure, so the gate cannot be recorded green by reading past the output.

**Adopted pipeline, run end-to-end from a cold cache** (`data/cache` moved aside for the
run, then restored):

| step | model (HF repo id) | provider | latency | tokens | cost |
|---|---|---|---|---|---|
| transcribe | `openai/whisper-large-v3` | groq | 8,327 ms | — | $0.0000 |
| summarise | `meta-llama/Llama-3.1-8B-Instruct` | groq | 545 ms | 1612 / 103 | $0.0000 |

No flags were needed to select the adopted route — a plain `m2x process` runs it.

**SHA note.** `e392922` is the SHA the evidence was produced against; no `src/` file
changed in the commit that carries this record, so the pipeline behaviour is that SHA's.
`eval/validate_transcripts.py` is new in this commit and is the only part of the evidence
that a fresh clone of `e392922` will not have.

### Deviation from the ticket's wording, resolved in the design's favour

M2X-025 step 2 says "every segment has text, t_start, t_end, speaker". Six segments across
the corpus (1 in `mtg-001`, 6 in `ami-001`, 0 in `mtg-002`) have no speaker, so the literal
reading fails. `TranscriptSegment.speaker` is nullable **on purpose**: `dominant_speaker`
returns `None` when no diarisation turn overlaps a segment, on the documented grounds that a
visible gap beats attribution no audio supports. The validator therefore checks attribution
against a **95% floor** — measured values are 99.0–100% — rather than demanding 100%, which
would ask the pipeline to guess. Recorded here rather than settled quietly, because the
alternative reading of the criterion is defensible and this is the supervisor's to overturn.

### Three published numbers this gate weakens

Found while assembling the evidence, all detailed in `phase1-comparison.md`:

1. **Coverage was partly an artifact** — Whisper emits empty-text segments with real time
   ranges (112s in `mtg-001`, 10.6% of the meeting); voiced coverage cuts T-A's published
   lead by roughly two thirds. Decision unchanged, margin overstated.
2. **The pipeline is not reproducible run-to-run** — the cold run above returned 53 segments
   / 629 words against the comparison leg's 64 / 780, same audio and flags, one week apart.
   WER moved only 64.0% → 64.9%, so quality is stable where verbosity is not.
3. **Two of three references are Deepgram, not human** — WER on the internal meetings is
   inter-vendor agreement, not accuracy.

## Phase 0 — 2026-08-04, SHA `85f80d4`

**Criterion (PRD §5):** "One command processes a meeting clip both ways; cost/latency/model logged."

Run on a **fresh clone** of `main`, not on the development checkout:

```
git clone https://github.com/FiftyfiveTech/ai-course-m2x.git
cp .env.example .env          # keys pasted in
mkdir -p data/clips && cp <clip> data/clips/
uv sync
make run                      # hosted
make run-local                # local
```

| leg | step | model (HF repo id) | provider | latency | tokens | cost |
|---|---|---|---|---|---|---|
| hosted | transcribe | `openai/whisper-large-v3` | groq | 4,935 ms | — | $0.0000 |
| hosted | summarise | `meta-llama/Llama-3.1-8B-Instruct` | groq | 395 ms | 1413 / 88 | $0.0000 |
| local | transcribe | `openai/whisper-large-v3` | groq | 0 ms (cache hit) | — | $0.0000 |
| local | summarise | `meta-llama/Llama-3.1-8B-Instruct` | ollama | 141,339 ms | 1393 / 72 | $0.0000 |

`data/runs/runs.jsonl` — 4 records, one per call, cache hit included. Both providers
present; every record carries `latency_ms`, tokens, `cost_usd` and `model_repo_id`.

**The number:** the same model repo id, served two ways, differs by **358×** on the
summary step. Nothing but configuration changed between the legs. Cache behaviour is
visible in the same log — the local run's transcription cost 0 ms because the hosted
run had already produced it.

**Deviation from the ticket's "zero undocumented steps".** A fresh clone cannot run
`make run` unaided, because `data/` is git-ignored and the clip is therefore absent.
Copying the clip in is a real step and is recorded above rather than glossed. That is a
property of the data boundary, not a packaging defect: the alternative is committing
meeting audio, which the PRD forbids. `data/corpus.json` (M2X-015) makes the expectation
machine-readable so a clone can at least name what it is missing.

## Phase 1B — 2026-08-13 — **NOT RUN**

**Criterion (PRD 1B):** schema-valid 100%; ≥0.85 field-level F1 on the sealed held-out
cases; adversarial transcripts treated as data. The held-out set certifies exactly one run
and is burnt afterwards.

**The set was not unsealed.** This is a gate record for a gate that was deliberately not
run, which is the honest form of it — M2X-040's acceptance criteria say "gate record
committed regardless of outcome", and "we chose not to spend the seal, here is why" is an
outcome.

### Why

| precondition | required | state on 2026-08-13 |
|---|---|---|
| dev micro-F1 | ≥0.85 on held-out | **0.3882** on dev |
| schema-valid | 10/10 | 14/15 on dev |
| injections | 3/3 | **3/3** ✅ |
| number reproduces on a fresh clone (`docs/gates.md`) | required | **was: no** → now yes |
| held-out set exists in git (`CLAUDE.md`) | required | **was: no** → now yes |

Only the injection leg passed. Unsealing would have spent the single available
certification on a configuration already known to fail the other two, and M2X-041's own
rule is that a burnt set never re-certifies — so the fix would have had nothing left to be
certified against.

The two structural rows were the more serious finding, because they mean the gate could not
have *meant* anything regardless of the number it printed:

1. **The number was not reproducible by anyone.** v3 scored 0.4279 on one checkout and
   0.3086 on another — same prompt, matcher, threshold and 15/15 case set, differing only
   in which sampled outputs each git-ignored `data/cache/` held.
2. **The seal did not exist.** `git ls-files eval/labels/heldout/` returned `.gitkeep` and
   nothing else, so a fresh clone had no set to run and nothing showed the ten cases were
   unedited between the freeze and the gate.

Both are closed by M2X-041 (`docs/design/day4-gate-recovery.md`): `--fixtures replay` makes
the number a function of tracked files, and the set is now committed as ciphertext plus a
digest manifest.

### Decision

**Fix first, gate later** — the supervisor's call on 2026-08-13. This deviates from
M2X-041's literal trigger ("ONLY if M2X-040 fails"), which as written requires burning the
set to unlock the ticket that repairs it. Recorded here, in the design record, on the Odoo
tickets and in the retro rather than left implicit.

The held-out set remains **sealed and unburnt**. It is still available to certify Phase 1B
once dev F1 is credible.

### What is reproducible today

```
uv run m2x eval extraction --set dev --prompt-version v3 --fixtures replay

MICRO-F1             0.3882
schema-valid:        0.9333 (14/15 answered)
scored cases (14): tiron-Bmr013-c01, … (full list in the results row)
schema failures (1): tiron-Bmr018-c01
```

No provider is contacted, so this reproduces on a fresh clone — with one caveat stated
plainly: the *matcher* still embeds, so the clone needs `nomic-embed-text` served. An
embedding is a single forward pass with no sampling and reproduces given the same model;
generation samples, which is why only the generation half is frozen.

```
uv run m2x eval injections --provider nim   →   3/3 PASS
```

The seal was exercised as a real round trip rather than only against the stubbed gpg the
unit tests use — seal, delete the plaintext, unseal, verify:

```
uv run python scripts/seal_heldout.py seal   --dir <scratch>   → 2 cases encrypted with AES256
(plaintext deleted)
uv run python scripts/seal_heldout.py unseal --dir <scratch>   → OK, every sealed case
                                                                 present and unedited
```

Decrypted content was byte-identical to what went in.

**Every Phase 1B number remains an upper bound.** The labels share an author with the
prompt and the schema (`eval/labels/README.md` §"these labels are not independent"). A
perfect seal on a non-independent set is still a perfect seal on a non-independent set, and
0.3882 against a 0.85 bar is not a tuning gap.

### Amendment, same day: the held-out set is now OPEN

Hours after the above was written, the supervisor decided both answer keys should be
readable by the whole team, and `eval/labels/heldout/` was committed in **plaintext**.

That adds a **second, independent reason this gate cannot be run as written**, and it is
the more permanent of the two. The first reason — dev F1 at 0.3882 — is a quality problem
that better work can fix. This one is structural: *there is no longer a held-out set to
open once.* The Builder can read all ten cases, so no score against them is a held-out
score, and there is nothing left to burn.

Sealing while publishing the passphrase was considered and rejected as worse than not
sealing at all: a repository holding ciphertext plus the key to open it reads as *sealed*
to anyone checking it, which misleads in a way an honest absence does not.

**What survives is integrity, not confidentiality.** `seal-manifest.json` still carries a
SHA-256 per case and `scripts/seal_heldout.py verify` still fails if any case is edited,
added or removed — the property that stops a label being adjusted after somebody has seen a
score, and the one an ignored directory never had. It also means a fresh clone can now
actually run the command, which it could not before.

**Certifying Phase 1B for real now requires fresh cases**, hand-written by someone who has
not read the prompt and kept genuinely private until the run. That was already M2X-041's
outstanding second half; it is now the only remaining route.

The same decision applies to `eval/rag/expected/`, so the Phase 2 gate (M2X-050, Friday)
inherits the identical caveat: its answer key is public and its numbers can be optimised
against it. Recorded here so Friday's record does not have to discover it.
