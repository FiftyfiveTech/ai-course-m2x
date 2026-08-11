# Ticket retros

Newest first. One entry per ticket (or paired tickets), appended at close — same
content as the Odoo completion comment, kept in-repo so it survives the course.

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
