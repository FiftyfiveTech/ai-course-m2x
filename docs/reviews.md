# Cross-reviews

Newest first. One entry per day's ritual ticket. The rule is that each of us explains
the *other's* work — you cannot cross-review your own code, and an entry written by the
person who wrote the thing under review is not a review.

Entries are written after the conversation happens. A pending marker stays pending until
then; it is never filled in from what the review was expected to conclude.

## Day 2 — 2026-08-04 (M2X-020)

**AM — comparison matrix.** `docs/design/day2-matrix.md` drafted by Saurabh as the input
to the pairing: rows, metric definitions, what "better" means per column, the decision
rule, and the meeting each comparison runs on. Two points need Yash's call before the
first run:

1. **His four rows are unfilled** — C-1/C-2 chaptering and S-1/S-2 summarisation
   strategies, and which meeting each runs on (M2X-023).
2. **The Devanagari confound.** Hinglish meetings return Devanagari against
   Latin-script hand snippets, so word-level WER is meaningless there. The draft's
   answer is to run each route twice — auto-detect and forced `--language en` — and
   compute WER only on the forced run. This changes what M2X-024's vocabulary
   experiment is measured on too, so it needs agreeing, not assuming.

*Status: drafted, pairing not yet held.*

**PM — cross-review.** *Pending.* Yash explains the diarisation code path (M2X-022);
Saurabh explains the comparison doc's conclusions and defends the adopted route.
