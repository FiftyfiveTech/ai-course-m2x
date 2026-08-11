# Ticket retros

Newest first. One entry per ticket (or paired tickets), appended at close — same
content as the Odoo completion comment, kept in-repo so it survives the course.

## M2X-043 — chunking + Chroma index (2026-08-11)

**Executed**

- `src/m2x/indexing.py`: transcripts chunk into whole segments packed to 1200 chars with
  one segment of overlap; markdown docs chunk on headings and carry the heading into the
  text. Chunk ids hash source + range, never text.
- `src/m2x/vector_store.py`: Chroma at `data/index/`, cosine, telemetry off. A source is
  written as a unit — upsert, then delete its orphans — so a source that shrank stops
  serving removed text.
- `ModelAdapter.embed()` + `ModelKind.EMBED` + registry entry
  `nomic-ai/nomic-embed-text-v1.5` on Ollama. The parser re-orders by the payload's own
  `index` field and checks the returned count.
- `m2x index build` / `m2x index query`. 395 tests green (60 new).
- Verified live: double build → 80 chunks both times; 11 embedding calls logged under
  `phase-2`, 4 cache hits on the rebuild, $0.00. Details: `docs/design/day4-index.md`.

**Deviations (recorded in the design doc)**

1. The adapter grew a third capability rather than letting Chroma embed. The ticket did
   not say through what; going around the adapter would have been shorter and would have
   broken the project's oldest rule.
2. `chromadb` is a main dependency, not an optional group like `diarize` — it pulls no
   deep-learning runtime, and M2X-044 cannot run without it. ~200 MB on a fresh sync.
3. Chroma's telemetry is explicitly disabled. It phones home by default, and the suite
   asserts no network happens.

**Lessons**

- The unit that was right for one job is wrong for the next. Fixed five-minute chapters
  won the summarisation comparison and make a bad retrieval unit for the same reason they
  won: they cover a lot of meeting.
- Content-addressed ids turn "rebuild" into "reconcile" — but only for content that still
  exists. The orphan case (a source that shrank) needs an explicit delete, and it stays
  invisible until someone edits a document.
- A test that failed for a *setup* reason still found a real bug: the CLI was resolving
  transcripts from the default directory and ignoring `--transcripts-dir`.
- Three hand queries are a smoke test, not a metric. One of the three returned a mediocre
  top hit; it is written down rather than tuned away — context precision is M2X-045's
  number to produce.

## M2X-032 — versioned prompt library (2026-08-11)

**Executed**

- `prompts/extraction/v1.md` + `src/m2x/prompts.py`: prompts load by name and version —
  markdown with `## system` / `## user`, `{{placeholder}}` rendering, numeric version
  ordering, latest-by-default and pinnable.
- `v1` carries the old `EXTRACTION_SYSTEM_PROMPT` byte-for-byte, verified against the
  constant before it was deleted, so numbers either side of the move stay comparable.
- The resolved version is stamped onto `ExtractionOutcome` *and* every run-log line, both
  from one value resolved in `extract_record` — callers cannot make them disagree.
- `prompts/CHANGELOG.md`: one row per version with a content digest. `m2x extract` gained
  `--prompt-version` / `--prompts-dir` and prints the prompt beside the model.
- 24 new tests, 335 green (311 on main). Reasoning: `docs/design/day3-prompts.md`.

**Deviations (written down, not implicit)**

1. The run log grew a twelfth field, against a docstring that says eleven and a test that
   enforces it — argued in the design doc rather than edited quietly. Defaults to `null`,
   so day-one records still parse and transcription says "no prompt" honestly.
2. The append-only rule is a failing test, not only the ticket's "convention + review":
   the changelog digest is compared against the file in both directions. The digest covers
   the model-visible text, not the file bytes, so prose fixes stay free.
3. Extraction calls had been reaching the run log as `phase`/`command` "unknown"; they now
   default to `phase-1b` / `m2x extract`. Outside the ticket's literal scope, but an
   unattributed line cannot agree with a record, which is the acceptance criterion.

**Lessons**

- Strict rendering has to run in *both* directions. A placeholder with no value is the
  obvious bug; the expensive one is a value naming no placeholder — a renamed slot hands
  the model an empty transcript, the empty `MeetingRecord` that comes back is valid, and
  the eval blames the model for an F1 of 0.0.
- "Enforced by convention" is a plan to be broken under deadline. The mechanical version
  cost about fifteen lines of test.
- Moving a prompt and improving a prompt are two changes. Doing both at once destroys the
  comparability that motivated the move.
- A guard is only deliberate if something fails when you cross it: the run log's field-set
  test is what turned "add a field" into a written argument.

## M2X-031 — Pydantic schema + Instructor extractor (2026-08-07)

**Executed**

- `src/m2x/schema.py`: `MeetingRecord` (decisions / actions / risks / open questions),
  nullable `owner` and `deadline`, ISO-date validation, `extra="forbid"`, and an
  `Evidence` validator that resolves `segment_id` *and* the cited time range against the
  transcript actually passed in.
- `src/m2x/extraction.py`: Instructor wired **over** `ModelAdapter` via
  `from_litellm(create)`, so every attempt — retries included — keeps its cache entry,
  run-log record and cost. Synthetic positional segment ids (`seg-0001`), transcript
  rendered as citable lines inside `<transcript>` tags with the data-not-instructions
  rule stated first.
- `uv run m2x extract <meeting-id>` → `data/records/<id>.json`, preferring the diarised
  transcript. No valid record after the attempt budget = exit 1, not an empty file.
- Concept primer `docs/learning/m2x-031-concepts.md`; design record
  `docs/design/day3-schema.md`; 288 tests green (was 260).

**Deviations (all in the design doc)**

1. No prior schema doc existed — the M2X-030 pairing was never held. Schema drafted from
   handbook ch. 3.1 and implemented; **needs Yash's sign-off before labelling starts**
   (`docs/reviews.md`). Blocking for M2X-033, not for this ticket.
2. Instructor's `response_format` kwarg is dropped rather than widening the adapter's
   signature and cache key; JSON-only output rides on the injected schema instructions.
3. The record is re-validated into a clean instance after extraction — Instructor
   attaches the raw response as a private attribute and Pydantic compares those in
   `__eq__`, which would break every record-vs-record comparison the harness makes.

**Lessons**

- A guard that fails open is worse than no guard: `validation_context=` is accepted in
  silence by instructor 1.15.4 and never reaches the validators — the kwarg is
  `context=`. Every fabricated citation would have passed, and the run would have
  reported success. A test now fails loudly if that wiring reverts.
- `max_retries` counts retries, not attempts. Read the budget off a test, not the name.
- Structured output is a *validation* feature, not a parsing one. The retry-with-error
  loop only earns its keep if the validators encode the things you actually care about —
  which is why evidence resolution runs inside the loop rather than after it.

## M2X-023 — chaptering + summarisation strategy comparison (2026-08-07)

**Executed**

- Five judgement questions written from the transcript and **committed before any
  strategy code existed** (`eval/judgement/m2x-023-questions.md`) — the git log is the
  evidence for the ordering.
- `src/m2x/chaptering.py`: fixed 5-minute windows (free, deterministic) and LLM
  topic-shift detection (one call, boundaries that do not resolve are dropped, never
  repaired). `src/m2x/summarisation.py`: single-pass and map-reduce, same model both ways.
- `m2x chapter` / `m2x summarise` subcommands so any one strategy is re-runnable from
  committed code. All four run on ami-001 (29.7 min, the longest transcript), outputs on
  disk, cost and latency from the run log.
- Matrix rows C-1/C-2/S-1/S-2 filled with the recommendation; answers sheet with the
  quoted evidence line behind every score. 283 tests green (was 260).

**Results**

- **C-2 (LLM chaptering) does not work.** Asked for ≤12 boundaries on 582 segments, it
  returned 12 — all inside the first 172. 69% of the meeting came back as one chapter.
- **S-2 (map-reduce) 3.5/5 vs S-1 (single-pass) 3/5.** The single discriminating question
  was late-meeting content, exactly as predicted when the questions were written.
- Groq's free tier (6 000 TPM) refuses single-pass on this meeting with HTTP 413. The
  whole comparison ran on NIM.
- Adopted: fixed chaptering + map-reduce summarisation.

**Deviations**

1. Map-reduce was measured over the *fixed* chapters, not C-2's. Pairing it with a
   chaptering that puts 69% of the meeting in one chapter would have measured the
   chaptering, not the summarisation.
2. The concept primer was written at ticket close, not at start — the ticket's own first
   step (questions before any output) took precedence, and writing the primer first would
   have meant reading strategy material before the questions were locked.

**Lessons**

- **When the same wrong shape survives three iterations, stop prompting and write the
  limitation down.** Fixing our outline truncation changed the boundary *count*; capping
  the answer changed compliance; neither moved the *distribution*. That is a property of
  the model over a long list, not a wording problem. Same shape as the Day-2 diarisation
  wrong turn.
- **Admissibility is an axis, not an inconvenience.** On the free tier the cheapest
  strategy is the one that cannot be sent. A cost table alone would have ranked these
  backwards.
- **Watch status, not just facts.** Neither summary invented anything, but single-pass
  turned "we could probably use XSLT" into "the team will decide to use XSLT". A prompt
  that asks for decisions gets the grammar of decisions applied to whatever it found —
  directly relevant to Phase 1B extraction.

## M2X-010 + M2X-011 — adapter design + implementation (2026-07-30, PR #1)

**Executed**

- Pair-designed the `ModelAdapter` interface before code; design record with 9
  reasoned decisions in `docs/design/day1-adapter.md`.
- Built `ModelAdapter.complete()/transcribe()` for Groq / NIM / Ollama by HF repo id;
  routing is data (`config/models.toml`), no provider branches in feature code.
- Content-hash response cache, 11-field JSONL run log, retry with backoff honouring
  `Retry-After`, banned-model guardrails, `SecretStr` security boundary.
- 21 files, +4,266 lines; 129 tests green — no network, no real clocks.
- Roles decision: Builder/Evaluator rotation dropped — pairing on everything.

**Deviations (documented in the design doc, agreed in review)**

1. Price table in tracked `config/models.toml`, not `.env` — git-ignored prices make
   the cost report unreproducible on the fresh-clone gate.
2. Provider + sampling params added to the cache key — the ticket's literal
   `sha256(model_id + messages)` makes its own "3 providers + 1 cached entry"
   acceptance criterion unsatisfiable.

**Lessons**

- Design-first pairing caught a spec self-contradiction at the whiteboard, not in the
  debugger.
- A cache key must include every input that changes the output — completeness is a
  correctness property, not a performance detail.
- Asymmetric failure policy: cache best-effort, run-log write raises — a measurement
  tool must never silently lose a record.
- Test cost arithmetic with fake non-zero prices; free-tier $0.00 verifies nothing.
- Scope honestly: `make run` exits 1 rather than pretending; human review lines stay
  human-written.
