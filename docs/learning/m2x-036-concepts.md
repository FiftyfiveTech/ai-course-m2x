# Concepts Behind Iterating on a Dev Set — Primer (M2X-036)

The six concepts the M2X-036 work exercises: the first real F1 exists, and the job is to
raise it on the dev set *without* spending the generalisation that Thursday's held-out
gate measures. Each section: what it is, why it matters here, the pitfall.

## 1. The dev/held-out split is a budget, not a formality

Two sets exist because a score you optimised against stops being a measurement. Every
look at the dev set spends a little of its information into the prompt; after enough
iterations the dev number describes the prompt's fit to those 15 cases and nothing else.
The held-out 10 have never been seen, so they still answer the only question that matters:
does this work on cases nobody tuned for?

That is why the split is 15/10 and sealed rather than 25 cases scored once. The dev set is
the thing you are allowed to burn.

Pitfall: treating held-out as "the same eval, run later". It is a single measurement with
no retries — once opened it is burnt, and a prompt changed afterwards has no certified
number at all.

## 2. The generalisation gap, and why the target is 0.90 not 0.85

The gate wants held-out ≥ 0.85. The ticket asks for dev ≥ 0.90. The 0.05 is not padding —
it is the expected drop from a set you iterated on to a set you didn't, and the previous
run measured that drop the hard way: **1.0000 tuned → 0.5195 real.**

Stopping at a dev number that merely clears the gate threshold means the gate is a
coin-flip. The margin is the whole strategy.

Pitfall: reading a dev score as a prediction. It is an upper bound. The right sentence is
"held-out will be *at most* this", never "held-out will be about this".

## 3. Error analysis beats prompt intuition

A micro-F1 is one number over four kinds and two failure directions. Iterating on the
number alone is guessing. The per-field table exists to localise the work: precision
collapsing means over-extraction (the model invents or fragments), recall collapsing means
misses, and a decision filed as a risk costs both at once because kind is a hard
partition.

So each iteration starts from a *classified* failure, not from a hunch: same fact
paraphrased below the match threshold, genuinely wrong content, right content under the
wrong kind, or fabricated outright. Those four have four different fixes, and only one of
them is "reword the prompt".

Pitfall: a near-zero F1 read as "the prompt is bad". At that magnitude the likelier causes
are wiring (the extractor fed different text than the labeller labelled) or a definition
mismatch. Rule out the plumbing before touching the wording.

## 4. Never fix a score by editing the labels

The tempting repair, when a labelled item and an extracted item plainly mean the same
thing, is to adjust the label. That converts an independent measurement into a mirror of
the output — exactly how the previous run reached 1.0000.

The labels are owned by the Evaluator. A suspected label bug is *reported* for adjudication
and the case stays scored as-is until it is ruled on. `eval/labels/` appearing in a Builder
commit is a process failure regardless of whether the edit was correct.

Pitfall: the softer version of the same move — quietly loosening the 0.60 match threshold
so paraphrases pass. The threshold is a frozen contract; changing it invalidates every
number taken under the old value and is a reviewed change, never a convenient one.

## 5. One version per iteration, or the numbers are rumours

Each prompt change is a new version file plus a changelog row carrying its dev-F1. No
in-place edits. This is what makes an iteration sequence readable afterwards: v2 fixed
over-extraction and moved 0.31 → 0.58, v3 tightened owner attribution and moved 0.58 →
0.71. Without the row, all that survives is the last number and no account of how it was
earned — and no way to tell an improvement from a lucky run.

Pitfall: batching five wording changes into one version. When the score moves you learn
nothing about which change moved it, and when it drops you cannot revert the one that hurt.

## 6. Hardening is a two-sided trade — re-run the injections

Most of what raises extraction F1 is instruction-tightening: be stricter about what counts
as an action, refuse to infer owners, ignore restated work. Those same edits touch the
paragraph that holds the data/instruction boundary, and a prompt made more obedient in
general is a prompt made more obedient to text inside the transcript.

Hence the rule: `m2x eval injections` runs after *every* prompt change, and 3/3 is a
release condition standing beside the F1, not a box ticked once at the start.

Pitfall: measuring the two on different prompt versions. An F1 from v4 quoted next to an
injection result from v2 is two facts about two systems.
