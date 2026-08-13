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
| v1 | 2026-08-11 | `c25354a2dc6e` | First version. Lifted byte-for-byte out of the `EXTRACTION_SYSTEM_PROMPT` constant: injection boundary, the four categories, and the citation / owner / deadline / empty-list rules. | A prompt that lives in code cannot be cited by an eval number. Moved unchanged so the numbers either side of the move stay comparable. | **0.0104** (14/15 scored, NIM, Llama-3.1-8B) — earlier 0.0364 was 10/15 on Groq before the transport fix |
| v2 | 2026-08-13 | `aaac9567bcbe` | States the description convention v1 omitted: one self-contained third-person sentence, pronouns and deixis resolved from surrounding turns, subject and object named, ~12–20 words, never a quote. Two worked wrong/right examples. Cite-the-acceptance-turn made explicit. Paired with `Field(description=...)` on `description` and `owner` in `schema.py`. | v1 stated no convention at all, so the extractor quoted: 31 of 75 descriptions were verbatim copies of their cited turn, median 5 normalised tokens against the labeller's 10. At the frozen 0.60 token-set threshold a perfect 5-token subset of a 10-token label caps at 0.667, so terse quoting cannot clear the bar arithmetically. 61% of unmatched labelled items already had an extracted item on the same segment id — right facts, unmatched register. The schema half matters because attribute docstrings never reach the model without `use_attribute_docstrings`; Instructor was injecting a bare `{"minLength": 1, ...}`. | **0.0972** (13/15 scored, NIM, Llama-3.1-8B) — TPs 1 → 7, action FPs 48 → 22 |

## rag

Used by `m2x ask` (`src/m2x/ask.py`).

| version | date | digest | what changed | why | abstention rate |
|---|---|---|---|---|---|
| v1 | 2026-08-12 | `cb3a6c0b0e4e` | First version. Injection boundary for retrieved passages, cite-by-reference (`C1`) carrying a verbatim quote, no model-written timestamps, and the abstention rule. | Retrieval reopens the data/instruction boundary from a new direction, and abstention rate is prompt-sensitive enough that a rate which cannot name its prompt is not a measurement. | 4/4 abstained on Llama-3.1-8B — superseded, see v2 |
| v2 | 2026-08-12 | `8a5f2f86c274` | Says the reference field takes the label only ("C1"), never the passage text, and shows a worked citation. Paired with renaming the schema field `passage` → `passage_ref` with a description, since Instructor renders field names into the prompt. | Under v1, Llama-3.1-8B put the whole passage text in the reference field. The validator rejected it and the system abstained on a question the corpus answers (distance 0.2963) — a false abstention, and the reason a prompt-sensitive metric needs a version. | 1/4 abstained on Llama-3.1-8B (the unanswerable one) — see `docs/design/day4-ask.md` |
