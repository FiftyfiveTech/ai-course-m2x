# Concepts Behind a Gate You Can Trust — Primer (M2X-040)

The concepts the M2X-040 prep work exercises. The ticket is not "make the number bigger" —
it is "make the number *mean* something before the sealed set is spent". Each section: what
it is, why it matters here, the pitfall.

## 1. An implicit default is an unreviewed decision

`latest_version()` resolving to the highest file on disk looks like convenience: ship
`v4.md`, everything switches, no code change. What it actually does is move a decision out
of the diff. Nobody wrote "use v5"; a filename did.

The pitfall is that it fails *silently and in the direction of whoever committed last*.
Merging PR #33 renumbered a parallel lineage onto `v4`/`v5` — correct for provenance — and
the extractor followed the numbers rather than the measurements, onto the prompt scoring
0.3645 / 1-of-3 injections instead of 0.4279 / 3-of-3.

**Rule of thumb.** If a value is a *claim* — this prompt is better, this threshold
separates, this model is the one — it belongs in a line someone reviews. Convenience
defaults are fine for things that are not claims (a cache directory, a timeout).

## 2. A metric is defined by its denominator, not just its formula

Micro-F1 is a sum over cases. Change which cases are in the sum and you have changed the
quantity, even though the formula and the code are identical. This is why the same commit,
prompt, matcher and threshold reported 0.3645 and 0.4279: fourteen cases versus fifteen.

The pitfall is that both figures are *correct*. Neither run had a bug. The defect was in
the reporting: a number that cannot name its case set cannot be compared with anything, so
"v6 beats v3, 0.4174 to 0.3086" is not a sentence with a truth value until you notice that
0.4174 was over thirteen cases and 0.3086 over fifteen.

**Rule of thumb.** Report the *support* next to every aggregate — n, and which n.

## 3. Failure classes are not interchangeable

"It didn't work" bundles two opposite facts. The model produced output that never validated
(the model's failure — this is the Phase 1B "schema-valid 100%" leg). Or the provider
returned a 429 and the case was never attempted properly (nothing about the model at all).

Collapse them and two things break at once: schema-validity blames the model for a network,
and the F1 denominator moves with the free tier's mood.

The pitfall specific to this codebase: Instructor wraps *everything* that escapes its retry
loop in `InstructorRetryException`, chained off the real cause. `except InstructorRetryException`
therefore tells you nothing about which happened — you have to walk `__cause__`.

**Rule of thumb.** When classifying an error, classify the *cause chain*, and pick the
conservative default for anything unrecognised.

## 4. Determinism has layers, and a response cache is only one of them

`temperature=0.0` is not reproducibility. This project's numbers are stable *within* a
checkout because the response cache replays the same provider answers; they are not stable
*across* checkouts, because `data/cache/` is git-ignored and a fresh clone samples afresh.
v3 measures 0.3086 here and 0.4279 on the supervisor's clone with everything else identical.

`docs/gates.md` asks for a number the supervisor reproduces on a fresh clone. That is a
stronger property than "the same command twice on my machine gives the same answer", and
noticing the difference is most of the work.

**Rule of thumb.** Ask what a *fresh* clone has. If the answer is "an empty cache and a
sampled model", the number is an estimate and should be reported as one.

## 5. A prompt version is a hypothesis, and most hypotheses fail

v6 was a reasonable design: keep v3's precision block, take v5's one measured fix. It lost
on both legs — schema-validity 0.8667 against 1.0000, injections 0/3 against 3/3.

Two pitfalls to name:

- **Numbering suggests progress.** v6 > v3 as integers says nothing about quality. The
  temptation to ship the higher number because it is the newer number is exactly what the
  pinned default now blocks.
- **A win that comes from a smaller denominator is not a win.** v6's F1 looked better
  *because* it dropped the two hardest cases.

And a mechanism worth remembering: adding instructions is not free. v6's system prompt is
~360 characters longer than v3's, and the cases it lost were lost to schema failures, not
content errors. For an 8B model, prompt length competes with format adherence.

**Rule of thumb.** Keep the failed version on disk with its numbers. A lineage that only
records wins is a lineage that will retry the same loss.

## 6. Combining two texts can produce a contradiction neither parent had

v5 says cite "the one line that states your item most directly". v3 says cite the line
where the fact was settled or accepted, and its dedup rule depends on that. Importing v5's
rule into v3's body would leave the prompt instructing two different things about one
choice — worse than either parent, and invisible in a diff that only shows lines added.

**Rule of thumb.** When merging instruction sets, look for the rule you are *duplicating
with different wording*, not just the rule you are adding.
