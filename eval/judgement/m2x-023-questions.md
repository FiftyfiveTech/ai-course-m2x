# M2X-023 judgement sheet — questions

**Meeting:** ami-001 (29.7 min, 582 segments, diarised — the longest transcript we have).
**Written:** 2026-08-07, from the transcript only.

> **These five questions were written before any chaptering or summarisation output
> existed.** That ordering is the method, not a formality: once you have read a summary
> you unconsciously write questions it can pass, and the comparison becomes a mirror. The
> commit that adds this file precedes the commit that adds any strategy output — check
> the log if you doubt it.

Each question is answered **yes / no / partial** against each summary, with the evidence
line quoted from the summary. A question is "yes" only if the summary states the thing;
inferring it on the summary's behalf is a "no".

## The questions

**Q1 — Dropped scope.** Does the summary state that processing/removing things from the
*audio* files was dropped as out of scope (too big / not enough time), rather than
listing it as work still planned?

**Q2 — Search-results UI.** Does the summary record the decision about how search results
are shown — one window with a result pane per selected type (topics / summaries /
transcripts) rather than separate windows — including ordering by meeting or colour-coding
by result type?

**Q3 — Schedule reality.** Does the summary name at least one concrete schedule fact: the
March 8th development-complete target, or that speaker characterisation was already past
its due date, or the ~6 days left for topic segmentation?

**Q4 — Report logistics.** Does the summary capture how the written report gets produced —
one person collates the document, the others send their sections in, final review at the
end?

**Q5 — Disfluency output format.** Does the summary mention that disfluency removal writes
back *selective XML* (printing only the wanted segments) rather than a flat text file, and
why (keeping it as XML preserves what you can do with it)?

## Why these five

They are spread across the meeting on purpose — Q1 and Q4 land in the first third, Q2 and
Q5 in the last third, Q3 in the middle. A single-pass summary over a 23 000-character
transcript tends to compress the tail hardest, so a question set drawn from one region
would measure position rather than strategy. If map-reduce beats single-pass, the
difference should show up mostly on the late questions; if it does not, that is a result
worth having too.

Q3 is the honesty check: schedule facts are the most tempting thing for a model to smooth
into "the team discussed timelines".

## Scoring

| | Q1 | Q2 | Q3 | Q4 | Q5 | total |
|---|---|---|---|---|---|---|
| S-1 single-pass | | | | | | |
| S-2 map-reduce | | | | | | |

Answers land in `eval/judgement/m2x-023-answers.md` after the runs, with the quoted
evidence line for every yes.
