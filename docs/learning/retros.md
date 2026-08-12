# Ticket retros

Newest first. One entry per ticket (or paired tickets), appended at close — same
content as the Odoo completion comment, kept in-repo so it survives the course.

## M2X-037 — `m2x extract` fenced-JSON parse failure (2026-08-12)

**Executed**

- **One word.** `src/m2x/extraction.py` selected `instructor.Mode.JSON`, which hands raw
  assistant content to Pydantic's JSON validator. Providers return the record inside a
  ` ```json ` fence, sometimes behind "Here is the corrected JSON response:". Switched to
  `Mode.MD_JSON`, which routes through `extract_json_from_codeblock()` and takes the first
  balanced JSON span, so a fence and a preamble both survive.
- **A regression test that fails on the old code**, parametrised over three reply shapes —
  bare, fenced, prefaced-fenced. On `Mode.JSON`: bare passes, the other two raise
  `InstructorRetryException`. On `Mode.MD_JSON`: all three pass. 449 tests green.
- **Split out of M2X-036**, which it blocked. No F1 can be scored on records the extractor
  cannot produce.

**Deviations**

- None. The ticket was written after the diagnosis, so its spec and the fix agree.

**Lessons**

1. **A retry loop disguises a systematic error as a flaky one.** Three attempts failed
   identically for days and it read as provider trouble. Variance produces three
   *different* errors; three identical failures means stop retrying and read the parse path.
2. **"Needs a live provider to reproduce" was false, and cost the most.** This sat in a
   session handoff note as network-only, so nobody reproduced it. The failure is in
   *parsing* a response — and a response is just a string. `tests/test_extraction.py`
   already scripts the model over `httpx.MockTransport`; the repro was one parametrised
   test away the whole time.
3. **The defect had no ticket and no failing test**, so it was invisible to everyone
   except the session that hit it, and resurfaced as a surprise each time. A defect that
   exists only in prose is not tracked.
4. **Library modes are behaviour, not labels.** `Mode.JSON` and `Mode.MD_JSON` read as a
   formatting nicety and are in fact the entire parsing contract.

## M2X-034 — field-level F1 harness (2026-08-12)

**Executed**

- `eval/README.md` written **before** the code and before any score existed to tune
  against. That ordering is as much the deliverable as the harness.
- `src/m2x/eval_extraction.py`: normalisation, token-set F1, greedy 1:1 matching with
  deterministic tie-breaks, per-kind and owner counts, micro-F1, schema-validity.
- `uv run m2x eval extraction --set dev|heldout`, appending to `eval/results/` with
  prompt version, git SHA, model and set. `--set heldout` warns that it burns the set.
- 26 new tests, 498 green.

**Verified twice, deliberately**

1. **Three synthetic pairs with the arithmetic written out in the docstrings** land on
   exactly 1.0, 0.0 and 0.5 — the ticket's acceptance criterion.
2. **The real dev labels scored against themselves**: 1.0000 on every field, 15/15
   schema-valid, 22/22 owner. The same identity-check proof `wer.py` and
   `diarization_score.py` were given in M2X-021 — synthetic pairs prove the code, real
   data proves the plumbing.

**Deviation**

**`deadline` is reported, not scored**, against the ticket's "field-level F1" wording.
The ground truth contains zero deadlines, so the field has no positive examples: any
emitted deadline is a false positive and no true positive can be earned. Folding that
into micro-F1 averages in a field that cannot be earned and drags the headline toward
whatever the null rate happens to be. It gets its own abstention line instead. If a dated
corpus ever arrives, the field rejoins micro-F1 and every number under this rule is
re-run.

**Lessons**

- **The order of writing is a control, not a formality.** Pinning the matching rules
  before the scorer existed meant the 0.60 threshold was chosen with no results to
  flatter — the one moment it could be chosen honestly.
- **Identity checks are cheap and catch what synthetic tests cannot.** Scoring the labels
  against themselves needed no model and would have caught any drift between the label
  format, the segment derivation and the matcher in one command.
- **A broad `except` is right when the count is the measurement.** One case that never
  validates is a schema-validity failure the gate needs counted, not a crash that costs
  the other fourteen.

## M2X-033 — hand-labelled ground truth, 25 cases (2026-08-12, PR #29)

**Executed**

- **25 cases, 133 items** — 29 decisions, 35 actions (28 with a named owner), 34 risks,
  35 open questions — across all three tiron corpora. Every citation re-resolved against
  the segments it was written from, using the validator the extractor is held to.
- **Transcripts derived from the committed tiron reference pair**, not from pipeline
  output: `data/` is git-ignored, so a fresh clone has no transcripts and the ground
  truth could never be rebuilt. `src/m2x/reference_transcript.py`, whose load-bearing
  part is a turn/line alignment check.
- **Selection and split are scripts**, both reproducible:
  `scripts/select_label_cases.py` and `scripts/split_labels.py` (seeded, seed recorded).
- 472 tests green. `eval/labels/README.md` carries the labelling rules and the
  independence caveat.

**The finding that changes M2X-034**

Zero of 35 actions carry a deadline. Every deadline spoken anywhere in the corpus is
relative and no tiron meeting has a date to resolve against — the conditional rule frozen
in M2X-030, meeting real data. So the `deadline` field has **no positive examples**: it
can only be scored as "correctly stayed null", any emitted deadline is a false positive,
and no true positive is available to earn. Averaging it into micro-F1 would fold in a
field that cannot be earned.

**Deviations**

1. **The labels are not independent** — same author as the prompt and the schema, by the
   user's explicit and reaffirmed decision. Every Phase 1B number is an upper bound, not
   a measurement.
2. **The seal is by convention, not encryption** (the ticket's second option). Held-out
   plaintext is git-ignored and never committed, so a fresh clone cannot reproduce the
   M2X-040 gate number, and nothing proves the set went unedited between freeze and gate.
3. **`extract_record` and friends now accept a bare segment list** as well as a
   `Transcript`. Human-annotated words have no provider or cost, and `Transcript` is an
   `AdapterResult` that demands both — inventing one would put a lie in the run log.
   Touches M2X-031's module from inside this ticket; no call site changed.
4. **`pythonpath` gains `scripts/`** so the split can be unit-tested.

**Lessons**

- **The near-miss was the seal, and it happened before the seal existed.**
  `eval/labels/staging/` was not covered by any ignore rule, and committing work in
  progress would have put the ten held-out cases into git history as plaintext — broken
  before it was ever applied, with rewriting published history as the only remedy. A
  protection has to cover the window *before* it is switched on.
- **Freedom in what you measure is freedom to flatter yourself.** With one author for
  both labels and prompt, the dangerous degrees of freedom are choosing which passages
  get labelled and re-rolling the split. Both were removed by making them scripts with
  recorded parameters — not because a script is more honest, but because it is checkable.
- **Write the rule down and the corpus will tell you it is unexecutable.** The deadline
  rule survived design review twice and died on contact with `corpus.json`, which has no
  dates. Same shape as M2X-030's finding, one layer further in.
- **Consistency beats correctness on contested calls.** "Announced into the meeting" vs
  "settled by the meeting" is genuinely arguable; applying one reading to all 25 cases
  and writing it down beats getting each case individually right, because the extractor
  is graded against the whole set.

## M2X-030 — schema freeze and the Day 3 rituals (2026-08-12)

**Executed**

- **The schema is frozen.** Shapes unchanged from M2X-031 — four item kinds,
  `owner`/`deadline` nullable, `YYYY-MM-DD` only, one resolved citation per item — so the
  freeze cost no relabel and M2X-033 is unblocked. The open question standing in
  `docs/reviews.md` since 2026-08-05 is answered.
- **What the freeze added** is the rules *around* the shapes, which until now existed only
  as implementation behaviour: dedup, conditional date resolution, and the per-field F1
  matching rules including the 0.60 description threshold. `docs/design/day3-schema.md`
  §The frozen contract.
- **The seal moved** to the tickets' `eval/labels/heldout/`, with plaintext git-ignored and
  `*.age` ciphertext committed. `.gitignore`, `CLAUDE.md` and the stray `eval/dev/.gitkeep`
  reconciled. 446 tests green (docs-and-config ticket; no code changed).

**Two rules could not be taken from the ticket as written**

1. **"Relative dates resolved against meeting date" is not executable on the graded
   corpus.** All three AMI meetings carry `date: null` and the tiron manifest has no `date`
   field at all. The rule became conditional — resolve only where a meeting date exists,
   null otherwise, *identically on both sides*. Under the literal wording, labels resolving
   "next Friday" while the extractor nulls it makes every relative deadline a guaranteed
   field miss, and the resulting F1 measures the mismatch rather than the model.
2. **Token-set F1 at 0.60, not embedding similarity.** The harness must be deterministic
   and offline. An embedding threshold changes meaning silently when the model is upgraded,
   which makes two gate numbers taken months apart incomparable with nothing in the diff to
   explain why.

**Deviations**

1. **M2X-030's scope check cannot pass and cannot be repaired.** It verifies by git history
   that the schema doc precedes `src/m2x/schema.py`; the code landed in `104c8e7`, the doc
   three commits later in `ff4ba14`, both 2026-08-07. The *property* it protects — contract
   frozen before labelling — does hold, since `eval/labels/` is created empty here.
2. **Labels will be written by the same author as the extractor**, by the user's explicit
   and reaffirmed decision. Every Phase 1B F1 is therefore an upper bound, not an
   independent measurement, and says so in the design doc and must say so in `gates.md`.
3. **The evening cross-review is not held.** It requires each developer to explain the
   *other's* work; with one operator there is no second party to explain anything back.
   Left pending rather than written up from what it would have concluded.

**Lessons**

- **Writing the rule down is the cheapest way to find out it is unexecutable.** "Relative
  dates resolved against meeting date" reads as obviously correct and survives any amount
  of discussion. It died the moment someone opened `corpus.json` and looked at whether the
  dates were actually there. Three of five local meetings and all seventeen tiron meetings
  have none.
- **A matching rule is a contract, and a threshold picked after seeing scores is not a
  measurement.** 0.60 is a judgement call made before any data exists to tune against,
  which is the only time it can be made honestly. Last run's 0.8063 was arguable partly
  because the rules were settled late.
- **Dedup pulls citation and content to different segments.** A restated commitment cites
  the *earliest* segment; a revised one records the *final* state. Each rule is obvious
  alone; together they are the easy thing to get quietly wrong.
- **"Agreement" and "absence of objection" are different, and only one of them is worth
  recording as agreement.** The freeze had no second opinion on the shapes — that is
  written into `reviews.md` as agreement-by-default, because a review log that flatters
  itself is worse than none.

## M2X-024 + M2X-025 — vocabulary experiment and the Phase 1 gate (2026-08-12, PR #23)

**Executed**

- References for all three pilot meetings, so every WER and entity cell in
  `docs/design/day2-matrix.md` is filled. `ami-001` is AMI's own human annotation;
  `mtg-001` and `mtg-002` are Deepgram `nova-3` transcripts, reviewed and accepted
  without a by-ear pass.
- **M2X-024:** the vocabulary file is not adopted as a pipeline default. `mtg-002` WER
  64.0% → 83.8% with the prompt attached, against `ami-001`'s 48.5% → 57.4%; entity
  capture 0% (0/2) on both legs.
- **M2X-025:** Phase 1 gate PASS. `eval/validate_transcripts.py` (12 tests, exits 1 on
  failure) reports 3/3 speaker-attributed and schema-valid; adopted pipeline run
  end-to-end from a cold cache. `docs/phase1-comparison.md` + a `gates.md` record.

**What the experiment actually found**

The lesson under test was "pipeline beats model tuning" — a vocabulary file moving entity
capture 76.2% → 90.5% on a prior project. It does not reproduce here, and the sharpest
evidence is not the WER regression. Only two vocabulary terms are spoken anywhere in the
`mtg-002` window, `RAG` and `PRD`, and Whisper misses both *with the file attached*. The
Deepgram reference transcribed both in Latin script, which rules out the script confound
that was expected to explain a zero: on these two terms the failure is capture.

A 0/2 denominator supports "no evidence the vocabulary helps", not "proof it cannot" — so
the adoption decision (don't make it a default) and the lesson (is the experiment fair on
this corpus?) were recorded as **two questions with two answers**, rather than one
verdict. Our corpus is half code-switched and our only English meeting contains none of
our 56 terms; that is a fact about the test, not about the technique.

**Three published numbers came out weaker, and none of them was the ticket's job**

1. **Coverage was partly an artifact.** Whisper emits segments with a real time range and
   empty text; duration-based coverage counted them. `mtg-001` carries 112 seconds of
   them — 10.6% of the meeting. Voiced coverage cuts T-A's published lead by roughly two
   thirds (9.1 → 2.9 points).
2. **The pipeline is not reproducible run-to-run.** The cold gate run returned 53 segments
   / 629 words where the 2026-08-05 leg returned 64 / 780: same audio, model, provider,
   flags, one week apart. WER moved only 64.0% → 64.9%.
3. **Two of three references are inter-vendor agreement, not accuracy.**

**The lesson**

*A metric invented mid-comparison and promoted the same day deserves the most scrutiny,
not the least.* Coverage was added on Day 2 because word counts diverged more than error
could explain, was immediately called "the discriminating measurement of the whole
comparison", and was partly counting silence with a timestamp on it. Nothing in the
comparison caught that — it took a **different** metric arriving a week later, on a
reference the comparison had been waiting for all along.

The corollary is the more useful half. Word count and coverage swing 19% run-to-run while
WER moves 0.9 points on the same audio, so the metric that needed the expensive missing
input was the stable one, and the two cheap proxies that filled in for it while it was
unavailable were the noisy ones. Cheap proxies are not free; they are borrowing against a
number you have not measured yet.

**Deviation recorded rather than resolved:** M2X-025 asks that "every segment has …
speaker". Six segments across the corpus have none, because `dominant_speaker` returns
`None` rather than guessing when no diarisation turn overlaps. The literal reading fails a
corpus that is working correctly, so the validator checks a 95% floor and the gate record
says so. Writing a checker that passes is easy; writing one that fails for the right
reasons is the work.

## M2X-044 — citation-based Q&A with abstention (2026-08-12, PR #21)

**Executed**

- `src/m2x/ask.py` + `m2x ask "question"`: retrieve top-k → answer via the adapter with
  the passages in a delimited data block (retrieved content is untrusted data, same rule
  as a transcript) → Pydantic `AnswerDraft` of `answer` / `citations[]` / `abstained`.
- Fabricated citations impossible by construction: passages are labelled `C1..Ck` and the
  model cites labels, so it never types a timestamp — `[meeting · speaker · mm:ss–mm:ss]`
  is rendered from chunk metadata. Each citation also carries a quote that must appear
  verbatim in the cited passage, which catches a real passage cited for an unsupported
  claim. Both validated inside the Instructor retry loop via validation context.
- Four abstention reasons (`no_match`, `below_threshold`, `model_abstained`,
  `ungrounded`), exit 0. `--max-distance` default 0.48, measured and provisional.
- `prompts/rag/v1` + `v2`, changelog rows with digests; version stamped on the outcome and
  on every run-log line, including the embedding leg (optional `RunContext` on
  `query_index`).
- 434 tests green (+24). Live acceptance on Ollama: 3 answerable → correct with 6
  resolving citations, 1 unanswerable → abstained. `docs/design/day4-ask.md`.
- Also fixed, on its own PR (#20): `m2x index build` took filenames from
  `--transcripts-dir` but content from the global `data/diarization/`.

**Deviations (documented in the design doc)**

1. `extract` raises when its retry budget is gone; `ask` abstains. A meeting with no valid
   record is a gate failure to look at, but a question the system cannot ground has a true
   thing left to say.
2. One retry rather than the extractor's two. An unresolvable citation means the model is
   reaching for something not in front of it; a second retry buys a more confident reach.

**Lessons**

- **A validator strict in the safe direction still costs you the feature.** Everything was
  unit-green before the first live call. Then `rag/v1` put the passage *text* in the
  reference field, and once fixed, the model quoted `Citation accuracy` where the handbook
  writes `**Citation accuracy**`. Two false abstentions on answerable questions — nothing
  fabricated, nothing unsupported printed, the guard working exactly as designed and the
  feature not working. "Refused" and "answered wrongly" are both Friday failures; only the
  second is dangerous, but only the first is invisible in a mocked test.
- **The schema field name is part of the prompt.** Instructor renders field names and
  descriptions into the request, and `passage` reads as an invitation to paste a passage.
  Renaming it `passage_ref` did more than the prompt sentence that said the same thing.
- **Fold formatting, never wording.** Quote comparison drops whitespace, case and markdown
  emphasis, because the corpus is markdown and the model quotes what it reads as prose.
  Folding any further would turn a substring test into a similarity test.
- **Measure the threshold, then say how weak the measurement is.** Answerable questions
  land 0.2963–0.4414 and unanswerable ones 0.5241–0.5589, which looks like a clean gap
  until you notice it is eight questions and that the 0.4414 hit is an answerable question
  retrieved at the wrong section.
- Cite by reference, not by string. Parsing a model-written citation can only ever be a
  filter after the fact; a timestamp the model cannot type is one it cannot invent.

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
