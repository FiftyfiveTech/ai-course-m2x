# Making the Phase 1B gate worth running (M2X-041)

**Ticket:** M2X-041 · **Branch:** `feature/m2x-041-gate-preconditions` · **Date:** 2026-08-13

## The ticket conflict, and how it was resolved

M2X-041 is written as *"ONLY if M2X-040 fails"*. Followed literally, that means unsealing
the held-out set — which certifies exactly one run and is burnt afterwards — in order to
unlock the ticket that repairs the thing the run would have failed on.

The state before this work:

| gate leg | required | actual |
|---|---|---|
| held-out F1 | ≥ 0.85 | dev best 0.4279 |
| schema-valid | 10/10 | 14/15 on dev |
| injections | 3/3 | **3/3** on the pinned v3 |

Only the injection leg passed. Running the gate would have spent the single available
certification on a configuration already known to fail on the other two, leaving nothing
to certify the fix with — and M2X-041's own rule is that a burnt set never re-certifies.

**Decision, taken by the supervisor on 2026-08-13: fix first, gate later.** M2X-041's
substance runs *before* M2X-040 rather than after it. This is a deviation from the
ticket's literal trigger and is recorded here, on the Odoo tickets, and in the retro
rather than left implicit.

Two further problems surfaced while preparing, and neither is a tuning gap — both are
reasons the gate could not have *meant* anything regardless of the number it printed.

## 1. The seal did not exist

`CLAUDE.md` says the held-out seal is physical: *"plaintext there is git-ignored,
ciphertext (`*.age`) is committed so a reviewer can verify the cases existed and were
unedited between the freeze and the gate."*

`git ls-files eval/labels/heldout/` returned `.gitkeep` and nothing else. Ten plaintext
cases existed on one machine; git held no evidence they existed at all. So a fresh clone
could not run the gate, and nothing could show the cases were unedited — the two
properties the seal is for.

**Closed with two artefacts, because they answer different questions.** `<case>.json.gpg`
makes the set recoverable. `seal-manifest.json` — SHA-256 per case — makes it checkable,
and it is the artefact that actually proves something: gpg's symmetric mode salts every
invocation, so encrypting identical plaintext twice yields different ciphertext and a
`.gpg` diff cannot distinguish a re-seal from a content change. The manifest is a pure
function of the plaintext bytes, so an edit shows as a manifest diff no re-seal can hide.

Digests leak nothing, so `scripts/seal_heldout.py verify` runs **without the passphrase**:
a reviewer can confirm the set is unedited while remaining unable to read it.

**gpg symmetric rather than age**, per the supervisor's decision — `age` is not installed
on this machine and gpg ships with Git for Windows. The `*.age` whitelist stays in
`.gitignore` so the literal wording of `CLAUDE.md` still works for anyone who has age.

**What the seal does not claim.** It was applied on 2026-08-13, after the cases were
written on 2026-08-12 and after dev iteration had already run here. It makes edits visible
from that commit forward and says nothing about the window before it. Sealing at freeze
time would have been strictly stronger, and that history cannot be retrofitted.

## 2. The number was not reproducible by anyone

M2X-040 prep found it and referred it up: v3 scored **0.3086** on one checkout and
**0.4279** on the supervisor's at `3cc59a8` — same prompt, same matcher, same threshold,
same 15/15 case set. `temperature` is already 0.0, no seed is sent, and `data/cache/` is
git-ignored, so each clone samples afresh. `docs/gates.md` requires a number the
supervisor reproduces on a fresh clone; that number could not be reproduced by anyone,
including the person who produced it.

Three options were on the table. **Committed fixtures was the supervisor's choice**, over
sending a seed (silently useless if the provider ignores it, and still needs live quota to
reproduce) and reporting an interval over N runs (honest about sampling, but the
acceptance criterion is a binary threshold, so it makes PASS/FAIL ambiguous near the line).

`--fixtures record` freezes each case's extraction under
`eval/fixtures/extraction/<prompt>/<model>/`; `--fixtures replay` scores the frozen
outcomes and makes no extraction call at all.

**Verified end to end on the real dev set**, not only in tests:

```
--fixtures record   MICRO-F1 0.3882   schema-valid 0.9333 (14/15)
--fixtures replay   MICRO-F1 0.3882   schema-valid 0.9333 (14/15)
```

Identical to four decimal places, with identical per-field TP/FP/FN and the same single
schema failure named (`tiron-Bmr018-c01`).

### What replay does and does not remove

It removes the **generation** dependency, which is the one that was non-deterministic. It
does not remove the **embedding** dependency: the matcher embeds every description through
`nomic-ai/nomic-embed-text-v1.5`, so a fresh clone still needs that model served.

That asymmetry is the whole justification. An embedding is a single forward pass with no
sampling, so it reproduces given the same model and input (up to floating-point
associativity). Generation samples, and no amount of caching makes a sample shared between
two machines. Freezing the sampled half is therefore sufficient, and freezing the
deterministic half would only have made the fixture set larger.

Three refusals make replay trustworthy, each closing a way the number could otherwise lie:

| refusal | what it prevents |
|---|---|
| a missing fixture aborts the run | a case leaving the micro-F1 denominator invisibly |
| a transcript-digest mismatch aborts the run | scoring an old answer against edited `eval/tiron/` words |
| provider failures are never recorded | a 429 becoming a permanent gate number |

Schema failures **are** recorded, unlike provider failures, because 100% schema validity is
a gate leg and a fixture set holding only successes would report that leg green by
construction. The recorded `tiron-Bmr018-c01` failure is exactly that case working.

## 3. Citation drift: the model no longer types timestamps

The largest single contributor to schema-validity failures. The extractor paired a segment
id with the **previous** line's timestamps — `seg-0033` cited as `580.3-581.4` when it runs
`581.44-586.445` — which failed evidence validation, consumed that case's entire retry
budget, and took the case out of the denominator. v3's explicit citation rule did not move
it, which pointed at the model rather than at the wording.

**The fix is structural, and it is M2X-044's principle applied to extraction:** *a
timestamp the model cannot type is one it cannot invent.* `Evidence.t_start` and `t_end`
are now derived from `segment_id` in code; any value the model supplies is discarded rather
than validated. `segment_id` still resolves against the transcript, and that is the check
which catches invention — a model that fabricates a decision fabricates a segment id to go
with it, and no amount of deriving timestamps makes that citation real.

What this gives up, deliberately: an item can no longer cite a sub-span of a segment.
Segments are single speaker turns, so the sub-span was never used — all 84 cited items
across the dev labels name a whole turn.

It also removes `TIME_TOLERANCE_S`. That constant existed because the prompt renders
timestamps to one decimal and every label copied the rendered value rather than the
segment's exact float bounds (83 of 84 cited items). With the range derived, rounding has
nothing to disagree about.

### The schema half did the work; the prompt half was measured separately

Instructor renders `Field(description=...)` into the request, so `"Do not emit this field.
It is derived from segment_id, and any value supplied is discarded"` reaches the model
without any prompt change at all. That showed up immediately: on the v3 run above — a
prompt that still *asks* for `t_start`/`t_end` — every citation the model emitted was
`{"segment_id": "seg-0001"}` and nothing else.

`v7.md` is v3 with one rule line changed to match. It exists so the claim could be tested
rather than assumed, and whether the constant moves to it is decided by the measurement
below, not by the version number.

## Measured: v3 vs v7 on dev, this commit

Both runs: NIM, `meta-llama/Llama-3.1-8B-Instruct`, embedding cosine @ 0.675,
`--fixtures record`.

| | micro-F1 | scored | schema-valid |
|---|---|---|---|
| v3 (pinned) | 0.3882 | 14/15 | 0.9333 |
| v7 | 0.3750 | **15/15** | **1.0000** |

Those two F1s are **not comparable** — different denominators, which is the malformed
comparison the M2X-040 harness fix exists to expose. Re-aggregated over the fourteen cases
both prompts answered:

| | micro-F1 over the common 14 | decisions | actions | risks | open_questions |
|---|---|---|---|---|---|
| v3 | **0.3882** | 0.4000 | 0.3673 | 0.2778 | 0.3784 |
| v7 | 0.3704 | 0.3226 | 0.3256 | **0.3889** | 0.2632 |

### Decision: v3 stays pinned

v7 is **not** measurably better. Like for like it is 0.0178 lower, and its one apparent
win — 15/15 schema-valid against v3's 14/15 — does not survive scrutiny: v3 scored
**1.0000 schema-valid** on an earlier run of the same prompt at `d486372`. So v3's single
failure here (`tiron-Bmr018-c01`, an unbalanced JSON array, *not* citation drift) is
sampling noise, and one sample per prompt cannot distinguish a fix from a lucky draw.

`DEFAULT_EXTRACTION_PROMPT_VERSION` therefore stays at `v3`. Same rule the v6 row applied:
a version number is not an improvement. v7 stays on disk with a changelog row recording
that it was measured and did not win, for the reason a lab notebook keeps a failed run.

**This costs nothing, because the fix is not in the prompt.** `Evidence` derives the
timestamps whatever prompt is pinned, so v3 gets the citation-drift fix for free. v7 only
ever tested whether *also* saying so in the prompt helped, and the answer is no —
measurably.

The corroborating observation is on the v3 run itself: v3 still contains the sentence
asking for `t_start` and `t_end`, and every citation the model emitted was
`{"segment_id": "seg-0001"}` and nothing else. Instructor renders `Field(description=...)`
into the request, so `"Do not emit this field"` reached the model through the schema and
overrode the prompt's own instruction. Worth remembering: **the schema is a prompt**, and
when the two disagree the schema won here.

### The injection leg re-measured under the schema change

A change to `Evidence` changes what the model is asked for on every item, so the one gate
leg that was passing had to be re-run rather than assumed:

```
uv run m2x eval injections --provider nim   →   injection suite: 3/3 PASS
```

All four effect-level checks hold on all three cases — valid record produced, not emptied,
no injected owner, no obeyed phrase — and `content_preserved` passes on each, with
`inject-01` *gaining* similarity to its control rather than losing it. The leg that was
green stays green.

## Where Phase 1B now stands

| gate leg | required | before | after |
|---|---|---|---|
| dev micro-F1 | ≥ 0.85 (on held-out) | 0.4279 / 0.3086 (unreproducible) | **0.3882, reproducible** |
| schema-valid | 10/10 | 14/15 | 14/15 (v3) · 15/15 (v7) |
| injections | 3/3 | 3/3 | **3/3** |
| number reproduces on a fresh clone | required by `docs/gates.md` | **no** | **yes** (`--fixtures replay`) |
| held-out set exists in git | required by `CLAUDE.md` | **no** | **yes** (ciphertext + digests) |

The two rows that were structurally broken are fixed. The F1 row is not, and this branch
never claimed it would be.

## Deviations from the ticket spec, recorded

1. **The trigger.** M2X-041 says "ONLY if M2X-040 fails". Run before M2X-040 instead, for
   the reason at the top of this document — the supervisor's decision.
2. **The fresh 5-case held-out retry is not in this branch.** M2X-041's second half asks
   for five fresh sealed cases. Writing them is Evaluator work that has to happen *after*
   the fixes are settled, or they are labelled against a moving target; the sealing tooling
   they need is what this branch delivers. Called out rather than quietly dropped.
3. **`age` → `gpg`.** Above.

## What is still open

- **The gate has not been run.** M2X-040 stays unsealed and its Odoo ticket carries the
  reason. `docs/gates.md` records it as deferred, not as a pass.
- **F1 is nowhere near 0.85.** 0.3882 against a 0.85 bar is not a tuning gap, and none of
  this work was aimed at closing it — it was aimed at making the eventual number mean
  something. The honest reading is that Phase 1B's content quality is the open problem and
  the measurement apparatus is no longer the thing in the way.
- **Every Phase 1B number remains an upper bound.** The labels share an author with the
  prompt and the schema (`eval/labels/README.md` §"these labels are not independent"). A
  perfect seal on a non-independent set is still a perfect seal on a non-independent set.
