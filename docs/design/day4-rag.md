# Day 4 — RAG architecture (M2X-042)

**Ticket:** M2X-042 (Odoo 4943) · **Date:** 2026-08-13 · **Phase:** 2

The Phase 2 architecture record: what the retrieval stack is, why each unit was chosen,
and what happens when retrieval is weak. It is the document M2X-045 reads to know what a
citation looks like before writing thirty questions against it.

## Deviation the ticket cannot avoid: this doc is a record, not a plan

M2X-042's acceptance criterion is *"`docs/design/day4-rag.md` committed **before** index
code"*. That ordering is impossible now and pretending otherwise would be the first lie in
the file: **M2X-043 (chunking + Chroma index) and M2X-044 (cited Q&A with abstention) were
both built and merged before this ticket was picked up.** Phase 2's build half ran ahead of
its measurement half.

So this is a design *record*: the decisions as they were actually taken, each traced to the
ticket that took it, plus the questions M2X-042 asks that the built code does **not**
answer. Those are marked **OPEN** and are the useful part — a doc that only ratifies
existing code earns nothing.

The rationale for each built decision is not restated here; it is in
[`day4-index.md`](day4-index.md) (M2X-043) and [`day4-ask.md`](day4-ask.md) (M2X-044).
This file is the single table a reader needs, and the two questions the ticket says a
pairing is incomplete without.

## The stack, as decided

| what the ticket asks to decide | decided | where |
|---|---|---|
| chunking unit | whole transcript segments packed to **1200 chars**, **1 segment** of overlap | M2X-043 |
| document chunking | markdown headings, heading carried into the chunk text | M2X-043 |
| embedding model | `nomic-ai/nomic-embed-text-v1.5`, routed to Ollama, through `ModelAdapter.embed()` | M2X-043 |
| vector store | Chroma, embedded, `data/index/`, collection `m2x`, **cosine** distance | M2X-043 |
| metadata schema | `meeting_id` · `segment_id` (first/last index) · `speaker` · `t_start` · `t_end` · `source_type` | M2X-043 |
| top-k | **5** | M2X-044 |
| abstention threshold | **0.48** cosine distance, `--max-distance` | M2X-044 |
| citation format | `[meeting · speaker · mm:ss–mm:ss]`, rendered from metadata, never typed by the model | M2X-044 |

### Why this chunk unit

*(The ticket names this as one of the two questions a pairing is incomplete without.)*

A segment is the smallest unit that carries a **real** `t_start`/`t_end`. Every other
candidate makes a chunk's time range an estimate, and the product promise — *this decision,
at 14:32 in Thursday's meeting* — is only as good as that range.

- **Fixed-second windows** would cut mid-sentence and split a claim from the turn that
  makes it attributable.
- **Five-minute chapters** were the right retrieval unit for summarisation (M2X-023) and
  are wrong here: five minutes is several topics, and one vector over several topics
  matches none of them sharply.
- **Sub-segment splitting** was rejected even for a segment that overflows the budget. An
  oversized chunk and an approximate citation are both costs; only one of them is visibly
  wrong, and the invisible one is the one that corrupts a gate.

1200 characters is roughly a paragraph of speech — enough context for the embedding to
carry a topic, short enough that a hit points somewhere specific. One segment of overlap
keeps an answer that straddles a boundary intact in at least one chunk.

### What happens on a low retrieval score

*(The ticket's second required question.)*

Nothing is answered on a weak retrieval. `m2x ask` has **four** abstention doors, and they
are recorded separately because they are not equally good news:

| reason | meaning | model called? |
|---|---|---|
| `no_match` | retrieval returned nothing — empty or unbuilt index | no |
| `below_threshold` | nearest passage further than `--max-distance` (0.48) | no |
| `model_abstained` | passages were near enough, the model read them and declined | yes |
| `ungrounded` | the model answered and its citations never validated | yes |

`below_threshold` is the cheap door and the model does most of the real abstaining, because
it can read the passage and the distance cannot. An abstention exits 0 — it is a result the
Phase 2 gate grades, not an error.

Grounding is enforced *inside* the retry loop, as Pydantic validators with the retrieved
set in validation context — the same wiring `m2x extract` uses for evidence resolution. An
invalid citation returns to the model as an error to fix rather than being dropped after
the fact. One retry, not two: a citation that will not resolve means the model is reaching
for something not in front of it, and a second retry buys a more confident version of the
same reach.

### The citation format, for M2X-045's benefit

`[meeting · speaker · mm:ss–mm:ss]` for meetings; `[readme · § Scope]` for documents, which
have no clock. **The model never types either.** It cites opaque labels — `[C1]`, `[C2]` —
and the reader-facing string is rendered from metadata stored on the chunk at index time.

This is the same principle M2X-041 has since applied to extraction evidence: a timestamp
the model cannot type is one it cannot invent. Citing `C9` when five passages were supplied
is not a wrong citation, it is not a citation — it fails structurally.

Each citation additionally carries a verbatim quote which must appear in the passage it
cites (`MIN_QUOTE_CHARS = 12`, so `"the"` cannot pass). Comparison folds whitespace, case
and markdown emphasis and **nothing else** — folding wording would turn a substring test
into a similarity test, which is precisely what this validator exists not to be.

**What M2X-045 must take from this:** ground truth is recorded as **segment ids**, because
that is what a citation resolves to. Recording an expected `mm:ss` string would score the
renderer, not the retrieval.

## OPEN — what this architecture has not settled

These are M2X-042's real output. Each is a decision the ticket asks for that the built code
currently guesses at, and each has a ticket that can close it.

**1. `--max-distance = 0.48` is provisional on eight questions.** Answerable questions
topped out at 0.4414 and unanswerable started at 0.5241; 0.48 sits in that gap. Eight
questions is not a separation — one more could close it, and the answerable end already
spreads across 0.15 of distance with no relationship to answer quality (0.4414 retrieved
the *wrong section* of a question the docs do answer). **M2X-045's thirty questions are the
first honest chance to re-derive it**, and the number that would justify a value is context
precision, which arrives with M2X-046. Any abstention rate reported before then must quote
both the threshold and the prompt version.

**2. `top_k = 5` was never measured.** It is a default nobody has varied. Context precision
is a direct function of it — a larger k can only lower precision while raising the chance
the answer is present at all — so M2X-046 should report it at more than one k before the
Friday gate treats 0.75 as a property of the system rather than of an unexamined constant.

**3. No `mm:ss` citation has ever been produced against a real meeting.** `data/` is
git-ignored, so a fresh clone has documents and no transcripts, and every verified citation
to date is a `§ heading` against this repo's own markdown. The meeting path is unit-tested
and structurally identical — the timestamp comes from the same stored metadata — but it is
untested against real audio. **This is why M2X-045 is written against `eval/tiron/`**: those
reference transcripts are committed, carry real speaker turns and timestamps, and exercise
the meeting citation path on every clone.

**4. The RAG prompt default is unpinned.** `src/m2x/ask.py` resolves through
`load_prompt(name, None)` → `latest_version('rag')`, which is the exact trap that silently
moved the extraction default from `v3` to `v5` on a merge (M2X-040 prep). No RAG number is
gate-bearing *yet*; the moment M2X-046 produces one, this must become a constant the way
`DEFAULT_EXTRACTION_PROMPT_VERSION` did. Left open here rather than fixed, because widening
M2X-042 into code is out of its scope — but it should be closed before the Friday gate, not
after.

**5. Chunk size versus the "one line inside a longer section" miss.** M2X-043 recorded it:
*"which models are banned and why"* returns the right section second because the ban list
is one line inside a longer one. That is the trade 1200 characters buys, and nobody has
measured the other side of it.

## Cross-review — NOT DONE

The ticket's PM half asks that Yash explain indexing and retrieval, Saurabh explain the
question-set design and what each RAGAS metric measures, and both be logged in
`docs/reviews.md`.

**This has not happened and is not recorded as though it had.** A cross-review needs two
people in a room; one agent writing both halves produces a document that looks like a
review and contains no second opinion — which is the same failure mode as one agent writing
the labels, the extractor and the score. Days 1–3 cross-reviews were left pending for this
reason and this one joins them.

So M2X-042's acceptance criteria are **partially met**: the design doc exists and answers
both required questions; the review entries do not exist. Recorded plainly so the gap is
visible rather than papered over.
