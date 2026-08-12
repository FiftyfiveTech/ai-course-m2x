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

| version | date | digest | what changed | why | dev-F1 |
|---|---|---|---|---|---|
| v1 | 2026-08-11 | `c25354a2dc6e` | First version. Lifted byte-for-byte out of the `EXTRACTION_SYSTEM_PROMPT` constant: injection boundary, the four categories, and the citation / owner / deadline / empty-list rules. | A prompt that lives in code cannot be cited by an eval number. Moved unchanged so the numbers either side of the move stay comparable. | **0.0312** (dev, nim, 13/15 schema-valid) |
| v2 | 2026-08-12 | `cc09b2a2b129` | Descriptions must be summaries in the model's own words, never verbatim transcript. Sharper tests for the four kinds (a proposal nobody accepted is not a decision; a rejection is). An explicit not-an-item list: back-channels, fragments, room/recording talk, completed work. Dedup rules for a restated or revised commitment. Citation rule now says to copy the two printed numbers exactly and never span two segments. Injection paragraph unchanged byte for byte. | v1 scored 0.0312 because it quoted the transcript instead of summarising it, so almost nothing matched however correct the finding was. It also returned fragments as items, confused decisions with actions, and drifted citations by one segment — burning the retry budget on two cases. Each change targets one of those four classes. | see `eval/results/extraction.jsonl` |

## rag

Used by `m2x ask` (`src/m2x/ask.py`).

| version | date | digest | what changed | why | abstention rate |
|---|---|---|---|---|---|
| v1 | 2026-08-12 | `cb3a6c0b0e4e` | First version. Injection boundary for retrieved passages, cite-by-reference (`C1`) carrying a verbatim quote, no model-written timestamps, and the abstention rule. | Retrieval reopens the data/instruction boundary from a new direction, and abstention rate is prompt-sensitive enough that a rate which cannot name its prompt is not a measurement. | 4/4 abstained on Llama-3.1-8B — superseded, see v2 |
| v2 | 2026-08-12 | `8a5f2f86c274` | Says the reference field takes the label only ("C1"), never the passage text, and shows a worked citation. Paired with renaming the schema field `passage` → `passage_ref` with a description, since Instructor renders field names into the prompt. | Under v1, Llama-3.1-8B put the whole passage text in the reference field. The validator rejected it and the system abstained on a question the corpus answers (distance 0.2963) — a false abstention, and the reason a prompt-sensitive metric needs a version. | 1/4 abstained on Llama-3.1-8B (the unanswerable one) — see `docs/design/day4-ask.md` |
