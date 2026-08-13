# Prompt changelog

One row per prompt version. A version is cited by extraction records
(`prompt_version` in `data/records/<meeting>.json`), by every run-log line the
extraction produced, and by whatever eval number was reported from it — so this table,
the record and the log must agree on which text was used.

**Versions are append-only.** Changing what a prompt asks means adding `v<N+1>.md`; it
never means editing a version that has already been cited. A superseded version stays on
disk for the same reason a lab notebook keeps the failed run.

`digest` is the first 12 hex of `sha256(system + NUL + user_template)` — the text the
model sees, not the file bytes, so the prose above the first heading can be corrected
without faking a prompt change. `tests/test_prompts.py` fails when a version file and
its row here disagree, or when a version has no row at all; the failure message prints
the digest to paste in.

## extraction

Used by `m2x extract` (`src/m2x/extraction.py`).

> **The version an unpinned call uses is `DEFAULT_EXTRACTION_PROMPT_VERSION` in
> `src/m2x/extraction.py` — currently `v3`.** It is a constant, not the highest file in
> this directory. Until M2X-040 prep it *was* the highest file, and merging PR #33 moved it
> from `v3` to `v5` — the weaker text on both Phase 1B gate legs — without a line in any
> diff saying so. Adding `v7.md` changed nothing until someone edited that constant, so
> "which prompt does this repo run" is answered by a reviewable change rather than by
> directory listing order. `v7` was measured against `v3` in M2X-041 and did not win; the
> constant did not move.

> **Rows below `v3` were taken under a different `Evidence` contract.** M2X-041 made
> `t_start`/`t_end` derived from `segment_id` in code rather than typed by the model, so
> citation drift is no longer a way to fail schema validation. Any schema-validity figure
> taken before 2026-08-13 includes drift failures that the current code cannot produce.
> Content F1 is unaffected — evidence is not scored — so the F1 column stays comparable.

> **The dev-F1 column below is superseded.** Every figure in it was computed under the
> symmetric token-set F1 @ 0.60 rule frozen in M2X-030. That rule was replaced with
> embedding cosine on `nomic-ai/nomic-embed-text-v1.5` @ 0.675 — implemented in PR #33 and
> approved by the supervisor on 2026-08-13 — because it was measuring phrasing agreement
> rather than extraction quality: 0 of 84 labelled descriptions reach 0.60 overlap with the
> turn they themselves cite. These numbers remain reproducible with `--similarity lexical`
> and are kept for exactly that reason, but they are **not comparable** with any figure
> taken under the embedding rule, and the ranking they imply is not trustworthy — on the
> parallel lineage in PR #33 the two rules ordered v2 and v3 oppositely. Figures carried
> forward get re-taken under the new rule.

| version | date | digest | what changed | why | dev-F1 (lexical, superseded) |
|---|---|---|---|---|---|
| v1 | 2026-08-11 | `c25354a2dc6e` | First version. Lifted byte-for-byte out of the `EXTRACTION_SYSTEM_PROMPT` constant: injection boundary, the four categories, and the citation / owner / deadline / empty-list rules. | A prompt that lives in code cannot be cited by an eval number. Moved unchanged so the numbers either side of the move stay comparable. | lexical **0.0104** (14/15 scored, NIM, Llama-3.1-8B) — earlier 0.0364 was 10/15 on Groq before the transport fix; embedding **0.2981** (13/15, measured on the evaluator lineage, `3cc59a8`) |
| v2 | 2026-08-13 | `aaac9567bcbe` | States the description convention v1 omitted: one self-contained third-person sentence, pronouns and deixis resolved from surrounding turns, subject and object named, ~12–20 words, never a quote. Two worked wrong/right examples. Cite-the-acceptance-turn made explicit. Paired with `Field(description=...)` on `description` and `owner` in `schema.py`. | v1 stated no convention at all, so the extractor quoted: 31 of 75 descriptions were verbatim copies of their cited turn, median 5 normalised tokens against the labeller's 10. At the frozen 0.60 token-set threshold a perfect 5-token subset of a 10-token label caps at 0.667, so terse quoting cannot clear the bar arithmetically. 61% of unmatched labelled items already had an extracted item on the same segment id — right facts, unmatched register. The schema half matters because attribute docstrings never reach the model without `use_attribute_docstrings`; Instructor was injecting a bare `{"minLength": 1, ...}`. | **0.0972** (13/15 scored, NIM, Llama-3.1-8B) — TPs 1 → 7, action FPs 48 → 22 |
| v3 | 2026-08-13 | `59a1e5bebba8` | Keeps v2's convention verbatim, adds the precision half: a "what is not an item" block (unaccepted suggestions, hedged musing, already-completed work, questions answered later, fragments, legitimately-empty passages), a one-item-per-fact dedup rule, and an ordered kind-routing procedure with two tie-breaks. Plus "emit only the fields in the schema". | v2 fixed register and recall and left 65 false positives against 7 true positives. Each clause targets a counted shape: 7/7 unaccepted design ideas as actions on `ES2004a-c02`, 11 duplicate pairs among 16 items on `EN2002a-c02`, 8 items drawn from the deliberately-empty `Bro021-c03`, and a kind-partition ablation showing that at a 0.40 threshold ignoring kind nearly doubles the score — so about half the residual is right content, wrong category. | lexical **0.0769** (15/15 scored, NIM, Llama-3.1-8B) — schema-validity 0.8667 → **1.0000**, FP/case 5.0 → 4.1; NOT comparable to v2's 0.0972, which scored 13 cases (see `docs/design/day3-extraction-iteration.md`). Embedded: **0.4279** (15/15) on the supervisor's run at `3cc59a8`, **0.3086** (15/15) re-measured on `d486372`/NIM in M2X-040 prep — same prompt, matcher, threshold and case set, different sampled model outputs; injections **3/3**. **This is the pinned default.** M2X-041 re-measured it under the derived-evidence contract: **0.3882** (14/15, schema-validity 0.9333) with `--fixtures record`, reproduced to four decimals by `--fixtures replay`; injections still **3/3**. That spread across three runs of one prompt — 0.4279 / 0.3086 / 0.3882 — is why fixtures exist. |
| v4 | 2026-08-12 | `cc09b2a2b129` | Descriptions must be summaries in the model's own words, never verbatim transcript. Sharper tests for the four kinds (a proposal nobody accepted is not a decision; a rejection is). An explicit not-an-item list: back-channels, fragments, room/recording talk, completed work. Dedup rules for a restated or revised commitment. Citation rule now says to copy the two printed numbers exactly and never span two segments. Injection paragraph unchanged byte for byte. **Authored as `v2` on the evaluator branch and renumbered here on merge** — `v2` was already cited on `main`, and versions are append-only; only prose above `## system` changed, so the digest is the text every number below was taken from. | v1 quoted the transcript instead of summarising it, so almost nothing matched however correct the finding was. It also returned fragments as items, confused decisions with actions, and drifted citations by one segment — burning the retry budget on two cases. Each change targets one of those four classes. Reached the same register diagnosis as `v2` on this branch, independently. | lexical **0.0674** (14/15) / embedding **0.3560** (14/15, NIM) |
| v6 | 2026-08-13 | `e2dec1fdfd1b` | v3's text verbatim with one change: the single-bullet citation rule is replaced by v5's block written around the rendered line — copy the two numbers printed on the same line as the cited id, never a neighbour's, never a range spanning two lines. **v5's line-*selection* rule was deliberately not imported** ("the line that states your item most directly"): v3's dedup rule, kept verbatim, already says to cite where a fact was settled or accepted, and importing both would have left the prompt saying two things about one choice. **v5's injection paragraph was deliberately not imported** — it is the text that flipped `inject-02` to pass and then echoed `'system override accepted'` on `inject-03`. | The merge made `latest_version('extraction')` return `v5`, so the repo's default became the weaker prompt on both gate legs. v6 was the attempt to keep v3's precision block and take v5's one measured fix (citation drift burnt the whole retry budget on two dev cases and one injection case). **It failed and is NOT the default** — see the row's numbers. Kept on disk for the reason a lab notebook keeps a failed run. | embedding **0.4174** (13/15 scored, NIM, Llama-3.1-8B) — but schema-validity **0.8667**, not 1.0, and **injections 0/3**. NOT comparable to v3's 0.3086: v6's higher figure is computed over 13 cases because two (`tiron-MTG_32063-c01`, `tiron-MTG_32257-c01`) failed schema validation and left the denominator — the exact trap M2X-040's harness fix exists to expose. Lexical not taken. |
| v7 | 2026-08-13 | `6d39e5db5e33` | v3 verbatim with one rule line changed: cite `segment_id` and nothing else, and do NOT emit `t_start`/`t_end` because they are derived and any supplied value is discarded. The prompt half of M2X-041's citation-drift fix; the schema half is `Evidence` in `src/m2x/schema.py`. Everything else byte-identical to v3, so the comparison isolates the change. | The extractor systematically paired a segment id with the *previous* line's timestamps (`seg-0033` cited as `580.3-581.4` when it runs `581.44-586.445`), failing evidence validation and burning the case's whole retry budget. v3's explicit citation rule did not move it. | embedding **0.3750** (15/15, NIM, Llama-3.1-8B), schema-validity **1.0000**. **NOT the default — it did not win.** Re-aggregated over the fourteen cases both prompts answered, v7 scores 0.3704 against v3's 0.3882. Its 15/15 is not a demonstrated fix either: v3 also scored 15/15 and schema-validity 1.0000 at `d486372`, so v3's single failure on the comparison run is sampling noise. One sample per prompt cannot tell a fix from a lucky draw. Kept on disk for the reason a lab notebook keeps a failed run. **The drift fix itself is in the schema, so v3 has it too** — that is why nothing is lost by not pinning v7. |
| v5 | 2026-08-12 | `a4a219d1c013` | Injection paragraph extended: an instruction inside the transcript never becomes an item, and never "the meeting decided" because a message said so. Citation rule rewritten around the rendered line — copy the two numbers from the same line as the cited id, never an adjacent one. Everything else identical to v4. **Authored as `v3` on the evaluator branch, renumbered on merge for the reason given in the v4 row.** | v4 obeyed `inject-02`, returning "The meeting decided to assign everything to Bob" as a decision — no owner field said Bob, so an owner-only check would have passed it. v4 forbade *following* instructions but never said their content must not become an item. Separately, citations drifted onto the previous line's timestamps and burnt the retry budget on three cases. | lexical **0.0518** (14/15) / embedding **0.3645** (14/15, NIM) — best in this lineage; injections **1/3**, so not a release candidate |

## rag

Used by `m2x ask` (`src/m2x/ask.py`).

| version | date | digest | what changed | why | abstention rate |
|---|---|---|---|---|---|
| v1 | 2026-08-12 | `cb3a6c0b0e4e` | First version. Injection boundary for retrieved passages, cite-by-reference (`C1`) carrying a verbatim quote, no model-written timestamps, and the abstention rule. | Retrieval reopens the data/instruction boundary from a new direction, and abstention rate is prompt-sensitive enough that a rate which cannot name its prompt is not a measurement. | 4/4 abstained on Llama-3.1-8B — superseded, see v2 |
| v2 | 2026-08-12 | `8a5f2f86c274` | Says the reference field takes the label only ("C1"), never the passage text, and shows a worked citation. Paired with renaming the schema field `passage` → `passage_ref` with a description, since Instructor renders field names into the prompt. | Under v1, Llama-3.1-8B put the whole passage text in the reference field. The validator rejected it and the system abstained on a question the corpus answers (distance 0.2963) — a false abstention, and the reason a prompt-sensitive metric needs a version. | 1/4 abstained on Llama-3.1-8B (the unanswerable one) — see `docs/design/day4-ask.md` |
