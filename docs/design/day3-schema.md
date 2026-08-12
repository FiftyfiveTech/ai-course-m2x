# Day 3 — the extraction schema and the Instructor loop (M2X-031)

**Status:** implemented; schema **FROZEN as of M2X-030** (2026-08-12) — see
[The frozen contract](#the-frozen-contract-m2x-030) for the rules that close the open
points, and Deviation 1 for how the freeze differs from the ticket's procedure.
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

## The frozen contract (M2X-030)

Everything above describes what `src/m2x/schema.py` enforces. This section closes the
three points the ticket asks the pairing to settle — nullability, date normalization,
dedup — plus the matching rules, so that labels and extractor target the same
definitions. **From this point a field rename costs a relabel, not an edit.**

The shapes are unchanged from M2X-031: four item kinds, `owner`/`deadline` nullable,
`YYYY-MM-DD` only, one `evidence` per item, `extra="forbid"`, empty lists valid. What
follows are the rules *around* the shapes, which existed only as implementation
behaviour until now.

### 1. Nullability — null is an answer, not a gap

`owner` is `null` when the meeting named nobody. It is **never** inferred from who was
speaking: "we should do X" said by Yash is not an action owned by Yash. `deadline` is
`null` when no calendar date is recoverable under rule 2.

For both fields the labeller and the extractor are held to the same standard: **`null`
is the correct answer**, and a label that guesses is as wrong as an extraction that
guesses. Under the matching rules below `null` matches only `null`, so a guess on either
side costs a field match.

### 2. Date normalization — resolve only against a known meeting date

The ticket asks for "relative dates resolved against meeting date". That is only possible
where a meeting date exists, and **on the graded corpus it does not**:

| meetings | `date` in `data/corpus.json` |
|---|---|
| `mtg-001`, `mtg-002` (internal, Hinglish) | present — `2026-07-27`, `2026-07-28` |
| `ami-001`, `ami-002`, `ami-003` | **`null`** |
| tiron (`eval/tiron/manifest-*.json`, 17 meetings) | **no `date` field at all** |

Since [`corpus.md`](../corpus.md) makes tiron the graded English corpus and the gate
number is quoted against it, the majority of scored cases carry no meeting date. The rule
therefore has to be conditional, and it is the *same* rule on both sides:

> **A `deadline` is `YYYY-MM-DD` when the meeting states an absolute date, or when the
> meeting date is known from `data/corpus.json` **and** the relative expression resolves
> unambiguously against it. Otherwise it is `null`.**
>
> Neither the labeller nor the extractor may invent a meeting date to resolve against.

Consequences, both deliberate:

- On the dateless English corpus every relative deadline ("next Friday", "end of the
  sprint") is `null` on **both** sides. Null matches null, so these score as agreement
  rather than as a mutual miss. The alternative — one side resolving and the other not —
  makes every relative deadline a guaranteed field miss and tells you nothing about the
  model.
- The current implementation already converges here without a code change: a relative
  deadline fails `_deadline_is_iso_date`, and the retry the model receives says *"use
  YYYY-MM-DD, or null when the meeting named no resolvable date"*.
- Where a date **is** known (`mtg-001`/`mtg-002`), resolution is permitted but the prompt
  does not yet carry the meeting date. Feeding it in is a prompt change and belongs to
  M2X-036, not here. Until then those two meetings behave like the dateless ones.

### 3. Dedup — one commitment, one item

A meeting restates itself. The unit is the **commitment, not the utterance**:

1. **Restatement is one item.** "You'll draft the PRD" / "Yes, I'll draft the PRD" /
   "So Yash has the PRD" is a single action, cited to the **earliest** segment where it
   is recognisable as a commitment.
2. **Revision is one item carrying the final state.** If the owner is reassigned or the
   deadline moves later in the meeting, the item records what it *settled* as, cited to
   the segment where it settled. A superseded intermediate state is not a second item.
3. **Same wording, different commitment = two items.** "Send the deck" about two
   different decks is two actions. Wording is evidence of identity, not identity itself.
4. **Across kinds, never deduped.** A risk that later becomes an action is a `Risk` *and*
   an `ActionItem`; both were true, and the harness matches within a kind anyway.
5. **Negated or abandoned items are not items.** "We could do X — no, drop it" yields
   nothing. An abandoned proposal is not a decision, and recording it inflates recall on
   both sides.

Rule 1 makes the citation deterministic (earliest), rule 2 makes the *content*
deterministic (final) — they point at different segments on purpose, and that is the one
place this is easy to get wrong.

### 4. Matching rules for field-level F1

Pinned here rather than in M2X-034 because the labeller has to know them *before*
labelling. M2X-034 implements exactly this and restates it in `eval/README.md`; if the
two ever disagree, this file is the contract.

| what | rule |
|---|---|
| **kind** | Hard partition. A `Risk` never matches an `ActionItem`, however similar the text. |
| **description** | Normalize (casefold, strip punctuation, collapse whitespace, drop a fixed stopword list), then **token-set F1 ≥ 0.60**. |
| **owner** | Exact after canonicalization (casefold, strip titles, map to the canonical name via `eval/vocab.txt`). `null` matches `null` only. |
| **deadline** | Exact string equality on `YYYY-MM-DD`. `null` matches `null` only. |
| **evidence** | **Not scored in matching.** It is a validity property, reported separately as schema-validity %. |
| **pairing** | Greedy 1:1 within a kind, by descending description similarity. Ties broken by (labelled index, extracted index) ascending, so the pairing is deterministic. |
| **unmatched** | Extracted-but-unpaired = FP. Labelled-but-unpaired = FN. |

**Token-set F1 rather than embedding similarity**, though the ticket offers both. The
harness must be deterministic and runnable offline — the suite takes no network, and an
embedding threshold silently changes meaning when the embedding model is upgraded, which
would make two gate numbers taken months apart incomparable without anything in the diff
to show why. The threshold **0.60** is a fixed, recorded constant; it is not tuned after
seeing scores, because a matching rule tuned against results is how 0.8063 became
arguable last run.

`0.60` is a judgement call made before any data exists to tune it against. If it proves
wrong, changing it is a **contract change** — a new row here, a rerun of every number
that used the old value, never a quiet edit.

### 5. Deviations recorded at the freeze

Three, and the first two weaken what the Phase 1B gate number can claim. Written here
rather than left implicit, per the project's deviation rule.

1. **M2X-030's scope check can no longer pass, and cannot be repaired.** The ticket
   verifies by git history that this doc precedes `src/m2x/schema.py`. It does not — the
   code landed in `104c8e7` and the doc three commits later in `ff4ba14`, both on
   2026-08-07, because M2X-031 was built before the pairing was held:

   ```
   104c8e7  feat(m2x-031): add the MeetingRecord schema with resolved evidence   ← code
      ...3 commits...
   ff4ba14  docs(m2x-031): record the schema design, the primer and the retro     ← doc
   ```

   No commit can reorder history that is already pushed. What the criterion protects —
   that the contract is fixed *before labelling* — is still intact, because no label
   exists yet: `eval/labels/` is created empty by this ticket. The freeze genuinely
   precedes M2X-033. The ordering check does not pass; the property it was checking for
   does.

2. **The ground-truth labels will be written by Claude, not by a human labelling blind.**
   Decided by the user on 2026-08-12, reaffirmed after being shown that M2X-033 exists
   *because* "the agent wrote labels, extractor, and score — F1 1.0000 tuned vs 0.5195
   real". Claude has already read `prompts/extraction/v1.md` and `src/m2x/schema.py`, so
   labels and extractor share an author.

   **Therefore every Phase 1B F1 — the dev ≥0.90 target and the M2X-040 held-out ≥0.85
   gate — is an upper bound, not an independent measurement,** and must be reported that
   way in `docs/gates.md` and in the Odoo record. The rules in this section are the
   partial mitigation: a labeller who has to follow written dedup, nullability and date
   rules has less room to drift toward what the extractor happens to do. Partial is the
   honest word — the rules do not restore independence, they only narrow the gap.

   The same caveat covers the *procedural* checks downstream: M2X-036 is verified by
   "Yash re-runs the final dev eval" and M2X-040 by "Saurabh confirms he never saw
   held-out contents". With one operator driving every ticket, neither is an independent
   check either.

3. **The sealed set lives at `eval/labels/heldout/`, not `eval/heldout/`.** The tickets
   name `eval/labels/`; `.gitignore` and `CLAUDE.md` predate them and name `eval/heldout/`.
   The ticket path wins, and the seal is now: plaintext under `eval/labels/heldout/` is
   git-ignored, ciphertext (`*.age`/`*.gpg`) is committed. That keeps the sealed set
   auditable in git — a reviewer can confirm ten encrypted cases exist and were not
   quietly edited before the gate — which an ignored directory cannot offer. The stray
   `eval/dev/.gitkeep` at the abandoned path is removed.

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

1. ~~**There is no prior schema doc, because the M2X-030 pairing has not been held.**~~
   **Closed by M2X-030 on 2026-08-12.** The shapes were taken from the handbook (ch. 3.1)
   and extended where the handbook shows only `ActionItem`; they are now frozen, with the
   surrounding rules written in [The frozen contract](#the-frozen-contract-m2x-030) and
   the open point in `docs/reviews.md` answered. No label had been written when the freeze
   landed, so the "before labelling" property the sign-off protects is intact — see
   Deviation 1 of that section for the part that is not.
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
