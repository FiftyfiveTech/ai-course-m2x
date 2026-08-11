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
| v1 | 2026-08-11 | `c25354a2dc6e` | First version. Lifted byte-for-byte out of the `EXTRACTION_SYSTEM_PROMPT` constant: injection boundary, the four categories, and the citation / owner / deadline / empty-list rules. | A prompt that lives in code cannot be cited by an eval number. Moved unchanged so the numbers either side of the move stay comparable. | not yet run — first F1 lands with M2X-036 |
