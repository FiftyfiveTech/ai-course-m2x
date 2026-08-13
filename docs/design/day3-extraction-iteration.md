# Day 3 — the first extraction F1, and why 0.90 is not reachable under the frozen contract

M2X-036. The record of what the first real dev number was, what moved it, and the one
finding that matters more than any of the numbers: **the target the ticket sets cannot be
earned against the ground truth as it is defined**, and the reason is a contract detail
rather than a model weakness. That needs a human decision before Thursday, so it is written
up here rather than buried in a changelog cell.

## The numbers

All runs: dev set, 15 cases, `--provider nim`, temperature 0, matching rules exactly as
frozen in `eval/README.md`. Nothing under `eval/labels/` was touched;
`DESCRIPTION_MATCH_THRESHOLD` was not changed.

| prompt | model | scored | micro-F1 | schema-valid | TP | FP |
|---|---|---|---|---|---|---|
| v1 | Llama-3.1-8B | 10/15 | 0.0364 | 0.6667 | 1 | 73 |
| v1 (after transport fix) | Llama-3.1-8B | 14/15 | 0.0104 | 0.9333 | 1 | 114 |
| v2 | Llama-3.1-8B | 13/15 | **0.0972** | 0.8667 | 7 | 65 |
| v3 | Llama-3.1-8B | 15/15 | 0.0769 | **1.0000** | 5 | 62 |

Injection suite on v3: **3/3 PASS** (`m2x eval injections`), so the hardening in v2/v3 did
not weaken the data/instruction boundary.

**A v3 × 70B run was attempted and is not obtainable today.** Every case failed with
`nim: transport failure: The read operation timed out`. Not a code fault — NIM's 70B
endpoint is queueing hard on the free tier. Measured directly, one call each, 8-token reply:

| model | provider | latency |
|---|---|---|
| `meta-llama/Llama-3.1-8B-Instruct` | nim | **0.4s** |
| `meta-llama/Llama-3.3-70B-Instruct` | nim | **159.0s** |

At 159s for eight output tokens, `M2X_REQUEST_TIMEOUT_S=120` guarantees a timeout on every
70B call, and raising it would put a 15-case run into the hours with three retries per case.
Groq is not the escape either: its `llama-3.3-70b-versatile` sits behind the same 6,000 TPM
ceiling that 413s the five large cases. So the "not the model" conclusion below rests on the
**v1 × 70B** run, which did complete at 15/15 — that measurement stands and is sufficient for
the claim. A v3 × 70B figure would refine it, not change its direction.

**Read the v2 → v3 row carefully: those two numbers are not comparable.** v2 scored 13
cases, v3 scored 15, and the two cases v3 added are the two hardest in the set. Per-case
false positives fell 5.0 → 4.1 and schema-validity reached the 100% the Phase 1B gate
requires; micro-F1 fell because two unscored cases became scored ones carrying more
labelled items to miss. A micro-F1 computed over a varying denominator is not a series, and
the harness does not currently warn about that — see "Follow-ups".

## What moved it, and why

**v2 — state the description convention (0.0104 → 0.0972, TP 1 → 7).** v1 stated none, so
the model quoted: 31 of 75 descriptions were verbatim copies of the turn they cited, median
5 normalised tokens. The labeller writes abstractive, context-completed third-person
summaries, median 10 tokens. Item identity is symmetric token-set F1 ≥ 0.60. A *perfect*
5-token subset of a 10-token label scores 2·5/15 = 0.667, and one substituted word puts it
under the threshold — so terse quoting cannot clear the bar arithmetically, however correct
the content. The evidence that content was never the problem: for labelled items unmatched
at 0.60, an extracted item cited the **same segment id** in 61% of cases. The facts were
being found and then described in a register the harness cannot match.

Paired with a schema change, because the schema *is* prompt text: `description` and `owner`
carried their conventions in attribute docstrings, which Pydantic omits from the JSON schema
unless `use_attribute_docstrings` is set. Instructor was injecting
`{"minLength": 1, "title": "Description", "type": "string"}`. Class docstrings did reach the
model ("Work someone committed to."), field semantics never did. Same failure shape as
`rag` v2's `passage` → `passage_ref` rename.

**v3 — precision discipline (schema-validity 0.8667 → 1.0000, FP/case 5.0 → 4.1).** Three
clauses, each aimed at a counted shape: unaccepted suggestions read as actions (7 of 7
extracted actions on `tiron-ES2004a-c02` were floated ideas the labeller's own note records
as never accepted), one commitment split across turns (11 duplicate pairs among 16 items on
`tiron-EN2002a-c02`), and fragments manufactured from passages that are correctly empty
(8 items from `tiron-Bro021-c03`, whose label is four empty lists). Plus an ordered
kind-routing procedure, because a kind-partition ablation showed that at a relaxed 0.40
threshold ignoring the kind boundary nearly doubles the score — about half the residual at
that point is right content in the wrong category.

## The finding: 0.90 is unreachable, and the threshold is not the reason

The ticket asks for dev ≥ 0.90, aiming high because a dev score barely over 0.85 will fail
the held-out gate. Neither is achievable against this ground truth, and the constraint is
structural.

**It is not the model.** Prompt v1 on `meta-llama/Llama-3.3-70B-Instruct`, the strongest
chat model in `config/models.toml`, scores 0.0546 with schema-validity 1.0000 — five times
the 8B on the same prompt, and still two orders of magnitude from target. A 70B model with
perfect schema compliance does not approach 0.90 here.

**It is not simply the 0.60 threshold either.** Swept offline without touching the committed
constant: at 0.30 the 8B is 0.2261 and the 70B 0.3483; at 0.10 the 70B reaches 0.5534. Even
with matching relaxed almost to nothing, the score does not approach the target, because the
residual is then precision — 62 false positives. Lowering the threshold is not the fix and
I recommend against it as a first move.

**It is the interaction of two rules that are each individually sound.** Labelling rule 8
says cite the turn where a commitment was *accepted*; the description states the resolved
fact, completed from surrounding context. Item identity is *symmetric* token-set F1 against
that description. Put together: **0 of 84 labelled descriptions reach 0.60 token overlap
with the turn they themselves cite**, median ≈0.30, and 15 of 84 are below 0.15. The
extreme case is a labelled action reading *"Give the others a tutorial on accessing the
corpus and walk them through the existing code and set-up"* whose cited segment says, in
full, *"Yeah. Yeah, I can do that."*

That is not a labelling error — it is the correct way to label an acceptance. But it means
the harness demands the extractor reproduce a human's abstractive phrasing to within 60%
token overlap. A second independent human labeller would frequently miss that bar. The
number being measured is phrasing agreement, and only incidentally extraction quality.

## The decision that belongs to a human

Recommended for Thursday, in preference order. Both are `eval/README.md` §4 changes, so both
need sign-off and a re-run of every number taken under the old rule — not a quiet edit.

1. **Replace symmetric token-set F1 for `description` with a length-tolerant measure** —
   containment, or token-set *recall* of the labelled description. This removes the
   abstractive-vs-terse length penalty specifically, which is the thing that is broken,
   rather than loosening matching in general. It leaves over-extraction fully punished,
   because unpaired extracted items stay false positives.
2. **Restate the Phase 1B gate threshold against a measured baseline.** If the matching rule
   stands as written, then ≥0.85 held-out is not a bar this contract can clear and the honest
   move is to re-derive the target from what the corpus permits — a number set before any data
   existed is exactly the kind of number this project re-derives rather than defends.

Doing neither is also a defensible choice; it just means recording M2X-040 as a documented
fail with this analysis attached, which is the risk note the ticket's own acceptance criteria
provide for.

## Transport, fixed on the way

The first run scored 10 of 15 cases. `failed: 5` was all the harness said, so the diagnosis
needed a throwaway harness to recover what the loop already held. Both halves are fixed:

- **Groq `HTTP 413` is a TPM cap in disguise, and permanent.** Body:
  `Request too large ... tokens per minute (TPM): Limit 6000, Requested 10710`, `code`
  `rate_limit_exceeded`. Headers on a live 413: `x-ratelimit-limit-tokens: 6000`,
  `x-ratelimit-remaining-tokens: 6000` — the bucket is *full* and the request is still
  refused, because a request larger than one minute's entire allowance can never be admitted
  at any bucket level. `retry-after: 27` is actively misleading. Correctly excluded from
  `_RETRYABLE_STATUS`. Fixed by routing: `--provider nim` accepted the same 9,682-token
  prompt without complaint. `default_provider` in `config/models.toml` deliberately left on
  groq, so the Phase 0 summarisation numbers measured there are not silently re-routed.
- **`HTTP 429` was a genuine rolling-window exhaustion**, and underneath it a runaway
  generation: `extract_record` was the only model call site in `src/m2x/` leaving
  `max_tokens` unset. On `tiron-Bmr018-c01` the model never emits a stop token — Groq
  truncated at 2048, the JSON stopped parsing, Instructor reasked with the parse error
  appended, and a 3,734-token prompt became 5,849, which is what tripped the 429. On NIM,
  with no default cap, the same case burned three 120s read timeouts. Now capped at
  `MAX_OUTPUT_TOKENS = 2048`. This invalidates every cached extraction response, since
  `max_tokens` enters `build_cache_key` — a one-time cost, stated rather than discovered.
- **`ProviderRequestError.__str__` dropped `self.body`**, so the provider's own diagnosis was
  captured, attached, and never printed by anything. That single omission is why the 413
  looked mysterious. `run_extraction_eval` additionally swallowed the exception whole; it now
  names the case and the error on stderr and still counts the failure for the gate.

Result: 15/15 cases complete on v3, up from 10/15.

## Follow-ups, not done here

- **`schema_validity` conflates provider uptime with model capability.** `run_extraction_eval`
  scores a `ProviderRequestError` identically to an exhausted retry budget. The gate reads
  this metric as "did the model produce a valid record"; today a rate limit lowers it.
- **micro-F1 across a varying scored-case count is not a series.** The v2/v3 comparison above
  is the live example. The harness should report the denominator alongside the figure, or
  refuse to compare runs whose case counts differ.
- **`Counts.precision` prints `1.0000` for a kind with 0 TP and 0 FP**, which reads as
  perfect when it means "nothing happened".
- **The owner vocab mapping in the contract is unimplemented.** `eval/README.md` §4 and
  `day3-schema.md` §4 both specify canonicalising `owner` through `eval/vocab.txt`;
  `eval_extraction.canonical_owner()` never reads it, and `eval/vocab.txt` is an ASR hotword
  list with no owner aliases, so the rule is unimplementable against that file as written.
  Near-zero impact today (1–3 matched actions); it will matter at the gate. Doc wins by its
  own rule, so either the code gains the mapping or the doc is corrected.
- **Reported to the Evaluator, not acted on** (labels are not the Builder's to edit): a
  possible hedged-intent decision on `tiron-Bmr013-c01` inconsistent with the labeller's own
  exclusions elsewhere; a held-out case discussed by name in the `notes` of a committed dev
  file; and `eval/README.md` §5's claim about all 35 actions being deadline-null, which is
  unverifiable without opening the sealed half (dev alone has 22, all null).
