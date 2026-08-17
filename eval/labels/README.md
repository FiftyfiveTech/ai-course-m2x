# Ground truth for Phase 1B (M2X-033)

25 hand-labelled transcript cases: **15 in `dev/`** for iteration, **10 in `heldout/`**
sealed until the M2X-040 gate. The frozen contract these follow is
[`docs/design/day3-schema.md`](../../docs/design/day3-schema.md) §The frozen contract;
this file records how it was *applied*, and the calls that were close.

## Read this first: these labels are not independent

The ticket asks for a human labelling blind, because the previous run failed when one
agent wrote the labels, the extractor and the score — dev F1 1.0000 against a real
0.5195. **That separation does not hold here.** These labels were written by Claude, by
the user's explicit and reaffirmed decision, and Claude had already written
`prompts/extraction/v1.md` and `src/m2x/schema.py`.

So every number computed against this set — the M2X-036 dev target and the M2X-040
held-out gate — is an **upper bound, not a measurement**. It says what the extractor
scores when graded by something that shares its author's assumptions about what counts
as a decision. A lower number is still bad news; a high number is not proof.

The rules below are the mitigation, and they are partial by construction: a labeller
bound to written rules has less room to drift toward whatever the extractor happens to
do, but rules written by the same author encode the same assumptions. Anyone re-running
this gate for real needs fresh labels from someone who has not read the prompt.

## What a case is

A case is a coherent chunk of one reference transcript — 25 of them, ~105 minutes total,
drawn from all three tiron source corpora (AMI, ICSI, NOTSOFAR).

Cases are chosen by [`scripts/select_label_cases.py`](../../scripts/select_label_cases.py)
under a fixed rule, not by hand, and the bounds are pinned in [`cases.json`](cases.json).
This matters for the same reason the section above does: a labeller free to choose
passages is free to choose passages the extractor handles well. Rerunning the script
reproduces the same 25 cases.

- **NOTSOFAR** (`tiron-MTG_*`): the whole ~5.8-minute meeting is one case. Slightly over
  the ticket's "2–5 min", which it admits explicitly as "or a full short meeting" —
  cutting them would have split short meetings mid-topic for no gain.
- **AMI / ICSI**: each meeting is divided into equal blocks and a ~180 s window is taken
  from the centre of each, so cases spread across the meeting instead of clustering in
  the agenda-setting at the start.

A label file stores **bounds, not text** — `meeting_id` plus `first_turn`/`last_turn`.
The segments are re-derived from `eval/tiron/` on demand, so the labeller's view and the
extractor's view cannot drift; there is only one copy of the words. Segment ids are
**relative to the case**: `seg-0001` is the case's first turn, not the meeting's.

## Labelling rules applied

Beyond the frozen contract, these are the calls that came up repeatedly. They were
written down as they were decided, and applied backwards to earlier cases whenever a new
one changed the rule.

1. **Speech is not commitment.** "We should probably look at X" is an open question or
   nothing; it becomes an action only when someone accepts it. The grammar of a decision
   applied to a musing is the single most common way to inflate both sides of an F1.
2. **`owner` is who accepted the work, never who spoke.** If the acceptance is "yeah,
   I'll do that" the owner is that speaker; if nobody accepts, `owner` is `null` even
   when it is obvious who ought to do it.
3. **Speaker labels are the owner value.** The reference gives `ES2004a.A`, not names —
   so an owner is recorded as that label. Inventing a human name would make the owner
   field unscoreable against anything the extractor can see.
4. **`deadline` is almost always `null` here.** No tiron meeting carries a meeting date
   (see the frozen contract §2), so a relative deadline has nothing to resolve against.
   Only an absolute date spoken aloud becomes a value.
5. **Risks are stated, not inferred.** "This might slip" is a risk; a labeller noticing
   that a plan looks fragile is not. The transcript has to say it.
6. **Open questions must stay open within the case.** A question asked and answered
   thirty seconds later is not an open question. Since a case is a window, a question
   answered *outside* the window still counts as open — the extractor cannot see the
   answer either, and grading it on unseen text measures the window, not the model.
7. **Meta-talk about the recording is not content.** "Can we dim the lights", "is this
   thing on" — real speech, no decisions. Several cases are legitimately empty on some
   or all categories, and an empty list is a correct answer, not a gap.
8. **Cite the segment that carries the item**, and where a commitment is restated, the
   earliest segment where it is recognisable as one (frozen contract §3).

## The split

Drawn by [`scripts/split_labels.py`](../../scripts/split_labels.py) with the seed
recorded in that file, so a reviewer re-runs it and gets the same fifteen and the same
ten. Seeding is not fussiness: an unseeded split can be re-rolled until the held-out set
looks favourable and nothing in the repository would show it, which matters more than
usual when the labeller and the prompt author are the same.

## The seal — abandoned on 2026-08-13. The held-out set is OPEN

The ticket offered two forms: encrypt the set, **or** cover it with an agreed do-not-open
rule. This project first took the second, then M2X-041 replaced it with real encryption,
and then the supervisor decided the same day that every developer should be able to read
both answer keys.

**So there is no seal.** `heldout/` holds ten cases in committed plaintext. The Builder can
read them. **A Phase 1B number computed against this set is not a held-out number**, and the
protocol below is history rather than instruction.

The alternative — sealing and publishing the passphrase — was rejected as worse: ciphertext
plus a public key reads as *sealed* to a reviewer while being nothing of the sort, and a
false artefact misleads where an honest absence does not. The full accounting of what was
given up is in [`heldout/README.md`](heldout/README.md).

**What survives:** `heldout/seal-manifest.json` still carries a SHA-256 per case, and
`scripts/seal_heldout.py verify` still fails if one is edited, added or removed.
Confidentiality is gone; integrity is not, and integrity is what stops a label being
adjusted after somebody has seen a score.

**Consequence:** certifying Phase 1B for real now needs *fresh* cases, written by someone
who has not read the prompt and kept genuinely private until the run. That was already
M2X-041's outstanding second half.

**The protocol as originally written**, kept for the record — opened exactly once, at
M2X-040, after the prompt version and SHA under
test are frozen. The number goes into `docs/gates.md` whatever it is. After that the set
is **burnt**: it can never certify a later prompt change, because a failure teaches you
what the set contains. Certifying any subsequent fix requires *fresh* cases — which is
what recovery ticket M2X-041 exists for.

The Builder does not open `heldout/`. With one operator that rule is a convention rather
than an enforced boundary, which is recorded here rather than pretended away.

*(End of the original protocol. It no longer applies: the set is open, so there is nothing
to unseal and nothing to burn.)*
