# Day 3 — the extraction schema and the Instructor loop (M2X-031)

**Status:** implemented; schema **frozen pending Yash's sign-off** (see Deviation 1).
**Code:** `src/m2x/schema.py`, `src/m2x/extraction.py`. **Command:** `uv run m2x extract <meeting-id>`.

## What this decides

The shape of a `MeetingRecord`, how an extracted item proves where it came from, and how
a transcript becomes one through a model without leaving the adapter's cost envelope.

The schema is a contract between three parties that never meet: the extractor targets
it, Yash's hand labels follow it, the F1 harness compares within it. Any of the three
drifting from the other two shows up as a low score with no bug to find, so the shape
below is frozen for the phase.

## The schema

```
Evidence      segment_id: str · t_start: float · t_end: float
Decision      description · evidence
ActionItem    description · owner: str|None · deadline: str|None · evidence
Risk          description · evidence
OpenQuestion  description · evidence
MeetingRecord decisions[] · actions[] · risks[] · open_questions[]
```

Deliberate choices, in descending order of how much they affect the score:

1. **`owner` and `deadline` are nullable, and null is the correct answer when the
   meeting named nobody / no date.** Under field-level F1 a guessed owner is a false
   positive; `null` is simply not matched. A model that admits ignorance must be able to
   outscore one that confabulates, or the eval rewards the behaviour the product cannot
   ship.
2. **`deadline` is `YYYY-MM-DD` or null — validated, not merely typed `str`.** "Next
   friday" is rejected. A half-parsed deadline is worse than an absent one because it
   looks actionable.
3. **Every item carries `evidence`, and evidence is resolved against the transcript.**
   The validator rejects a `segment_id` that does not exist, *and* a cited time range
   that falls outside the segment it names. Checking existence alone would let a model
   cite a real segment for a claim made nowhere near it. Tolerance is ±0.5 s, because
   the prompt renders timestamps to one decimal and the model echoes them back rounded.
4. **`extra="forbid"`.** A model that invents `confidence` is retried with the error,
   not quietly trusted. Strictness costs a retry now and saves an unexplainable F1 later.
5. **Empty lists are valid.** "This meeting contained no risks" must be expressible, or
   the model fills the category to satisfy the schema — a hallucination the schema
   itself would have caused.
6. **Four kinds stay four classes** even where their fields coincide today, because the
   harness matches per kind and a shared base class invites a shared field that only one
   kind should have.

### Segment ids are synthetic and positional

`TranscriptSegment` has no id of its own — segments arrive from the provider as an
ordered list. Ids are therefore derived: `seg-0001`, 1-based, in transcript order,
defined once in `extraction.segment_ids()` and read by both the prompt renderer and the
validator. Two independent derivations of the same id would eventually disagree, and the
failure would be indistinguishable from model hallucination.

Consequence to know about: **ids are stable within a transcript, not across
transcripts.** Re-transcribing a meeting with different settings renumbers everything,
so labels and records are only comparable against the transcript they were made from.
Same property the diarisation labels have (Day 2), same reason.

## The extraction loop

```
transcript → render citable lines → Instructor(create=ModelAdapter.complete)
           → parse → validate (schema + evidence, with context)
           → on failure: retry with the validation error fed back (2 retries)
           → ExtractionOutcome → data/records/<meeting-id>.json
```

**Instructor wraps the adapter; it does not replace it.** The client is built with
`instructor.from_litellm(create)` over a `create` callable backed by
`ModelAdapter.complete()`, so every attempt — first and each retry — goes through the
cache, the run log and the price table. `instructor.from_openai(...)` pointed at Groq
would have been three lines shorter and would have made retries invisible to the cost
report, which is the Day-1 lesson being deliberately re-applied.

Evidence is validated **inside** the loop via Pydantic validation context, so a
fabricated citation is an error the model is asked to fix rather than an item dropped
afterwards. The retry the model actually receives reads:

> Correct your JSON ONLY RESPONSE, based on the following errors: Value error,
> segment_id 'seg-9999' does not exist in this transcript…

**Prompt-injection boundary.** The transcript enters inside `<transcript>` tags and the
system prompt's first paragraph states that everything between them is data spoken by
participants, never an instruction. Mitigation, not proof — M2X-035 attacks it on
purpose, and the write path gets a human approval gate later in the course.

**Attempt budget: 3 (initial + 2 retries).** Two retries clear what is worth clearing —
a prose preamble, a relative date, one invented citation. A model still failing on the
third attempt is failing at comprehension, and further attempts buy cost, not validity.
Exhausting the budget raises `InstructorRetryException` and the CLI exits 1: a meeting
with no valid record is a gate failure to look at, not an empty record to score.

**Transcript budget: 24 000 characters**, larger than the summary step's 6 000. A summary
of the first act is still a summary; a record extracted from a truncated meeting is
missing decisions and looks identical to a model that failed to find them. When the
limit does bite, `ExtractionOutcome.truncated` records it in the artefact.

## Deviations from the ticket spec

1. **There is no prior schema doc, because the M2X-030 pairing has not been held.** The
   ticket says "exactly per the schema doc"; the schema doc is this file. The shapes are
   taken from the handbook (ch. 3.1), which is the only written spec that exists, and
   extended where the handbook shows only `ActionItem`. **This needs Yash's explicit
   sign-off before he labels anything** — a field renamed after labelling begins costs a
   relabel, not an edit. Recorded as the open item in `docs/reviews.md`.
2. **`response_format` is dropped.** Instructor passes
   `response_format={"type": "json_object"}`; `ModelAdapter.complete()` does not expose
   it, and the shim ignores it rather than widening the adapter's signature (and its
   cache key) for this ticket. JSON-only output is carried by the schema instructions
   Instructor injects into the system message. If a provider is later observed wrapping
   JSON in prose, the fix is to add the parameter to the adapter — where the cache key
   can account for it — not to bypass the adapter here.
3. **The record is re-validated into a clean instance after extraction.** Instructor
   attaches the raw provider response to the model it returns as a private attribute,
   and Pydantic compares private attributes in `__eq__` — so the in-memory record would
   never equal the same record read back off disk, which is precisely the comparison the
   eval harness makes.
4. **`m2x extract` prefers the diarised transcript** (`data/diarization/<id>.json`) over
   the plain one, falling back automatically. Speaker labels are what let the extractor
   attribute an action to the person who accepted it; without them every `owner` is
   `null` by construction. `--transcript` overrides.
5. **New runtime dependency: `instructor` (and `openai`, which it requires).** `openai`
   is used only for its response *types* when the shim builds the object Instructor
   parses — no OpenAI client, no key, no request. Provider routing stays in
   `config/models.toml`.

## Traps found while building this (worth not rediscovering)

- **`validation_context=` silently does nothing** in instructor 1.15.4's v2 path; the
  kwarg is **`context=`**. Passing the documented name is accepted without complaint,
  the validator sees `info.context is None`, skips resolution, and every fabricated
  citation passes. A guard that fails open is worse than no guard — hence
  `tests/test_extraction.py::test_extract_retries_with_the_validation_error_when_a_citation_is_invented`,
  which fails loudly if the wiring ever reverts.
- **`max_retries` counts retries, not attempts**: `max_retries=3` makes four calls. The
  code passes `max_attempts - 1` so the ticket's "max 2 retries" is what actually runs.

## Verification

```bash
uv run pytest -q                          # 288 tests
uv run pytest tests/test_schema.py -q     # 13 — every one asserts a rejection
uv run pytest tests/test_extraction.py -q # 12 — retry loop, run-log coverage, round trip
```

The scope check the ticket asks for (Yash feeds a hand-corrupted record through the
validators, bad `segment_id` and bad date, both rejected) is covered by
`test_evidence_rejects_an_unknown_segment_id` and `test_action_rejects_a_relative_deadline`
and can be reproduced by hand against `Evidence.model_validate(..., context=...)`.

Not yet run against real meetings: the acceptance criterion "extraction runs on all 3
meetings producing schema-valid JSON" needs the transcripts on disk and a live provider,
and lands with the first F1 run (M2X-036).
