# Concepts Behind the Retrieval Substrate — Primer (M2X-043)

The six concepts M2X-043 exercises: transcripts and project docs in, a queryable vector
index out, with every chunk carrying enough metadata to cite a timestamp. Each section:
what it is, why it matters here, the pitfall.

## 1. The chunk is the unit of recall, not the unit of storage

Retrieval never returns "the meeting". It returns chunks, and a chunk is the smallest
thing the system can hand a reader and say *this is where the answer is*. That makes
chunking a retrieval decision disguised as a formatting one: too large and the embedding
averages several topics into a vector that matches nothing sharply; too small and the
chunk no longer contains the reasoning that made it an answer.

Fixed 5-minute chapters were the right unit for summarisation (M2X-023) and are the
wrong unit here — a five-minute window of a meeting is several topics. Retrieval wants
something closer to a paragraph.

Pitfall: tuning chunk size against how the output *looks*. The only test that means
anything is whether the chunk that gets retrieved contains the answer.

## 2. A chunk boundary must never cost a timestamp

The product's promise is a citation: *this decision, at 14:32 in Thursday's meeting*.
That promise survives chunking only if chunks are built out of whole transcript segments,
because a segment is the smallest unit that carries a `t_start` and `t_end` from the
transcriber. Split mid-segment and the chunk's time range becomes an interpolation —
which is a guess that renders as a fact.

So chunks pack whole segments up to a character budget, and the chunk's range is the
first segment's start and the last segment's end. Exact, by construction.

## 3. Deterministic ids turn "rebuild" into "reconcile"

A vector store that generates its own ids grows every time you build: run the indexer
twice and every chunk is in there twice, retrieval returns the same text in two slots,
and top-5 silently becomes top-2.5.

The fix is content addressing: the id is a hash of what identifies the chunk
(`source_id` + segment range). Re-indexing the same content produces the same id, so an
upsert overwrites rather than appends, and a rebuild is idempotent — the acceptance
criterion for this ticket.

Pitfall: hashing the *text* alone. Two meetings that both contain "sounds good" would
collide onto one id and the second would overwrite the first.

## 4. Metadata is the citation, and the store constrains its shape

The vector gets you to the chunk; the metadata is what makes the chunk usable —
`source_type`, `meeting_id`, `t_start`, `t_end`, the speakers, the source path. If it
isn't attached at index time it cannot be recovered at query time, because all you get
back from a vector search is what you put in.

Chroma's metadata values must be scalars: `str`, `int`, `float`, `bool`. No lists, no
`None`. So a speaker list is joined into a string, and a field that does not apply to a
source type (a document has no `t_start`) is *omitted* rather than set to a placeholder.
Zero is a real timestamp; using it to mean "not applicable" makes a document look like
something said in the first second of a meeting.

## 5. One index, one embedding model — including at query time

An embedding is only meaningful relative to the model that produced it. Vectors from two
models are numerically comparable and semantically unrelated, so mixing them does not
error: it returns confident nonsense. The query has to be embedded by the same model that
embedded the corpus, which is why the model repo id is recorded on the collection and not
just passed to the build command.

This is also why embedding goes through `ModelAdapter` like every other model call: the
run log is where "which model built this index, and what did it cost" is answerable.

Pitfall: silently re-embedding a query with a default. A dimension mismatch at least
fails loudly; a same-dimension mismatch does not fail at all.

## 6. A distance is not a confidence

Chroma returns distances, and cosine distance is `1 - similarity` — smaller is closer.
Two things follow. First, ranking is not scoring: the top result is the *nearest* chunk,
and the nearest chunk to a question about something nobody discussed is still a chunk.
Second, any threshold is a property of this corpus and this model, not a universal
constant.

That gap is the whole subject of the next ticket: `m2x ask` has to be able to say "not in
the meetings" rather than dress up the nearest neighbour as an answer.
