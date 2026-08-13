# Cross-reviews

Newest first. One entry per day's ritual ticket. The rule is that each of us explains
the *other's* work — you cannot cross-review your own code, and an entry written by the
person who wrote the thing under review is not a review.

Entries are written after the conversation happens. A pending marker stays pending until
then; it is never filled in from what the review was expected to conclude.

## Day 3 — 2026-08-05 (M2X-030)

**AM — schema-design pairing.** *Not held.* M2X-031 needed the schema, so it was drafted
rather than paired: `docs/design/day3-schema.md` carries the shapes, the nullability and
evidence decisions, and the open point below. Drafting is not agreeing — this needs
Yash's answer before he labels anything:

1. **Does the schema hold as frozen?** Specifically: four item kinds (decision / action /
   risk / open question); `owner` and `deadline` nullable, with *null as the correct
   answer* when the meeting named neither; `deadline` restricted to `YYYY-MM-DD`; every
   item citing exactly one segment, resolved against the transcript. A field renamed
   after labelling starts costs a relabel of everything already written, not an edit —
   so this is blocking for M2X-033, not for M2X-031.

*Status: schema drafted and implemented, pairing not yet held.*

### Resolved 2026-08-12 — schema FROZEN, but by decision rather than by pairing

The open question above is answered: **the schema holds as frozen.** Four item kinds,
`owner`/`deadline` nullable with null as the correct answer, `deadline` restricted to
`YYYY-MM-DD`, one resolved citation per item — all unchanged from the draft. Nothing was
renamed, so no relabel cost was incurred, and M2X-033 is unblocked.

What the freeze *added* is the rules around the shapes, which had existed only as
implementation behaviour: dedup (commitment not utterance, earliest citation, final
state), conditional date resolution, and the per-field F1 matching rules including the
0.60 description threshold. All in
[`day3-schema.md` §The frozen contract](design/day3-schema.md).

**This was not the pairing the ticket describes, and the difference is worth naming.**
The ticket wants two people arguing the contract before either has code or labels
invested in it. What happened is that Claude drafted the rules and the user decided the
two points that were genuinely open — who writes the labels, and where the sealed set
lives. On the schema shapes themselves there was no second opinion, only an absence of
objection. Recorded as agreement-by-default, not as agreement.

Two questions the pairing would have been the right place to catch, surfaced here by
writing the rules down instead:

1. **The ticket's "relative dates resolved against meeting date" is not executable on the
   graded corpus.** All three AMI meetings carry `date: null` and the tiron manifest has
   no date field at all. The rule had to become conditional — resolve only where a
   meeting date exists, null everywhere else, *on both sides*. Had labelling started
   under the ticket's literal wording, every relative deadline would have been a
   guaranteed field miss.
2. **Dedup pulls the citation and the content toward different segments.** A restated
   commitment cites the *earliest* segment; a revised one records the *final* state. Both
   rules are obvious alone and contradict each other if written carelessly.

**PM — cross-review.** *Still not held, and cannot be held as specified.* The ritual asks
Yash to explain the extraction loop and Saurabh to explain the dev/held-out split — each
explaining the other's work, which is what makes it a review rather than a summary. With
one operator driving Claude through every ticket, there is no second party to explain
anything back. Left pending rather than written up from what the review would have
concluded, per this file's own rule.

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

   **Superseded by the runs, before the pairing happened.** Forced `--language en`
   *translates* rather than transliterates, so it fabricates evidence and was withdrawn
   as a scoring route (see `docs/design/day2-matrix.md`). The question Yash still has to
   answer is therefore a different one: WER on the Hinglish meetings now has no valid
   route at all, so either the hand snippets get written in Devanagari, or those two
   meetings are scored on entity capture only. Recorded here, not resolved — it is his
   call and the pairing has still not been held.

*Status: drafted, pairing not yet held.* The two open points above have both moved since
drafting (point 2 superseded, D-1 numbers now in), so the pairing has more to cover than
it did, not less.

**PM — cross-review.** *Pending.* Yash explains the diarisation code path (M2X-022);
Saurabh explains the comparison doc's conclusions and defends the adopted route.

---

## Day 4 — RAG architecture (M2X-042)

**AM — architecture pairing.** *Recorded, not paired.* The ticket asks the pair to decide
the chunk unit, embedding model, vector store, metadata schema, top-k, abstention threshold
and citation format together, and to commit the design doc *before* the index code. Neither
happened in that order: M2X-043 and M2X-044 were built and merged first, so every item on
that list was already decided in code by the time this ticket was opened.

[`docs/design/day4-rag.md`](design/day4-rag.md) therefore records the decisions as taken,
traces each to the ticket that took it, and answers the two questions the ticket says a
pairing is incomplete without — *why this chunk unit* and *what happens on a low retrieval
score*. Its five **OPEN** items are what a pairing would actually have argued about:

1. `--max-distance = 0.48` rests on eight questions, and its answerable band spans 0.15 of
   distance with no relation to answer quality. M2X-045's thirty questions re-derive it.
2. `top_k = 5` has never been varied, and context precision is a direct function of it.
3. No `mm:ss` citation has been produced against a real meeting — every verified citation to
   date is a `§ heading` against this repo's own markdown.
4. The RAG prompt default is still `latest_version('rag')` — the exact unpinned-default trap
   that silently moved the extraction default from v3 to v5 on a merge.
5. Nobody has measured the cost side of the 1200-character chunk.

**PM — cross-review.** *Pending.* Yash explains the indexing and retrieval code path
(M2X-043/044); Saurabh explains the 30-question set's design and what each RAGAS metric
actually measures — context precision vs faithfulness vs citation accuracy.

Not written up from what it would have concluded. One agent authoring both halves produces
something shaped like a review with no second opinion in it, which is the same failure mode
as one agent writing the labels, the extractor and the score.
