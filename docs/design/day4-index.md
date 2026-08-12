# Day 4 — chunking + Chroma index (M2X-043)

**Ticket:** M2X-043 (Odoo 4644) · **Status:** built

The retrieval substrate: transcripts and project docs in, a queryable index out, with
every chunk carrying what a timestamp citation needs.

## Problem

`m2x ask` (M2X-044) cannot paste every meeting into every prompt, and the citation
promise — *this decision, at 14:32 in Thursday's meeting* — has to survive whatever
splitting happens on the way into the index. Two failure modes decide the design: a
chunk whose time range is a guess, and an index that grows a duplicate set of chunks
every time it is rebuilt.

## Decisions

**1. Chunks are whole transcript segments packed to a character budget (1200), with one
segment of overlap.** A segment is the smallest unit carrying real `t_start`/`t_end`, so
building chunks out of whole segments makes a chunk's range exact by construction. A
segment longer than the budget becomes an oversized chunk rather than a split one:
an approximate citation is worse than a large chunk, because only one of the two is
visibly wrong. The overlap keeps an answer that straddles a boundary intact in one chunk.

**2. Five-minute chapters were rejected as the retrieval unit.** They were the right
call for summarisation (M2X-023) and are wrong here — five minutes of a meeting is
several topics, and one vector over several topics matches none of them sharply.

**3. Documents chunk on markdown headings, and carry the heading into the text.** A
paragraph of a scope document reads as generic without `## Out of scope` above it, and
the heading is often the only place the subject is named. Sections over the budget split
on blank lines, never mid-paragraph.

**4. Chunk ids hash the source and the range within it — never the text.** Content
addressing is what makes a rebuild idempotent: the same corpus produces the same ids and
upsert overwrites in place. Hashing text would collide two meetings that both contain
"sounds good" onto one id, and the second would overwrite the first.

**5. A source is written as a unit.** Ids alone do not cover a source that *shrank*: an
edited document with a section removed would leave its old tail chunk orphaned in the
index, still retrievable, quoting text that no longer exists. So `write_source` upserts
what the source has now, then deletes the ids it used to have.

**6. Embedding goes through `ModelAdapter`.** Chroma ships embedding functions that
would have been shorter. They leave the run log unable to say which model built the
index or what it cost — the same reason Instructor is wrapped around the adapter rather
than pointed at a provider. Registry entry is `nomic-ai/nomic-embed-text-v1.5`, default
route Ollama: an index build embeds the whole corpus, which is the last workload to put
on a free tier.

**7. The embedding model is recorded on the collection and checked on open.** Vectors
from two models are numerically comparable and semantically unrelated, so mixing them
returns confident nonsense rather than an error — and a same-dimension mismatch never
surfaces at all. Cosine distance over the default L2, because embedding models are
trained for it.

**8. Metadata omits inapplicable keys.** Chroma accepts scalars only — no lists, no
`None` — so speakers are joined into a string, and a document gets no `t_start` at all.
`0.0` is a real timestamp; using it for "not applicable" would make a document look like
something said in a meeting's first second.

## Deviations from the ticket spec

**A. The adapter grew a third capability (`embed`), and the model registry a third
kind.** The ticket says "embed with the chosen HF model (local, zero-spend)" without
saying through what. Going around the adapter would have been fewer lines and would have
broken the project's oldest rule; the run log now carries the index build like any other
spend.

**B. `chromadb` is a main dependency, not an optional group.** The `diarize` group
exists because torch is a deep-learning runtime nobody should install to run Phase 0.
Chroma pulls no such thing, the README already names it in the core stack, and M2X-044
cannot run without it. Cost is roughly 200 MB in a fresh clone's `uv sync`.

**C. Telemetry is explicitly disabled.** Chroma phones home by default. The test suite
asserts no network happens, and an index build should not report on a private meeting
corpus either.

## Verified

Run on this repo's own tracked docs (no meeting transcripts exist on a fresh clone):

```
uv run m2x index build     → indexed 80 chunks from 3 sources (80 in the index)
uv run m2x index build     → indexed 80 chunks from 3 sources (80 in the index)
```

Double build, identical counts — the acceptance criterion. The run log attributes 11
embedding calls to `phase-2` across `m2x index build` and `m2x index query`, four of
them cache hits from the rebuild, at $0.00 on the local route.

Spot queries: *"how does the response cache key work"* → §0.6 Caching by content hash at
distance 0.417; *"what are the three RAG gate metrics"* → §4.4 RAGAS at 0.296. *"which
models are banned and why"* returns §0.5 The zero-spend stack second and an unrelated
rubric section first — the ban list is one line inside a longer section, which is
exactly the kind of miss chunk size trades against.

**Retrieval quality is not measured here.** Three hand queries are a smoke test, not a
number; context precision arrives with the RAG eval set and RAGAS (M2X-045, M2X-046).

## Consequences

- `m2x index query` prints distances rather than a similarity percentage. A rank is not
  a confidence: the nearest chunk to a question nobody discussed is still a chunk. That
  gap is M2X-044's problem, and dressing it up here would have made it invisible there.
- An empty corpus exits 2 rather than reporting a successful build of nothing — that is
  how a gate ends up running against an empty store.
- Rebuilding one source leaves the others alone, so a re-index after one new meeting
  costs one meeting's embeddings.

## Defect fixed after merge: `index build` ignored `--transcripts-dir` for content

As merged, the build took transcript *filenames* from `--transcripts-dir` but loaded each
meeting's *content* from the hardcoded global `data/diarization/`. A build aimed at a
scratch directory silently indexed the real meeting corpus — a data-boundary crossing on
a public repo — and `test_index_query_prints_scores_and_citations` failed on any machine
that happened to hold `data/diarization/mtg-001.json`. The comment above the code claimed
it "never leaves the directory that was asked for"; that was false.

The diarised copy is now looked up in `--diarization-dir`, which defaults to the
`diarization/` directory **beside** `--transcripts-dir`. The default corpus layout is
unchanged (`data/transcripts` → `data/diarization`), a scratch build resolves inside its
own tree, and a caller who wants to mix the two has to say so. Preferring the diarised
copy was never the bug — reading it from a directory nobody asked for was.
