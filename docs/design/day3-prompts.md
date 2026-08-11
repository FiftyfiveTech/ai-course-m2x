# Day 3 — versioned prompt library (M2X-032)

**Ticket:** M2X-032 (Odoo 4636) · **Depends on:** M2X-031 · **Status:** built

Prompts are inputs to a run, not source code. This record is the reasoning behind the
library that makes them citable, and the three deviations from the ticket's literal
spec.

## Problem

An extraction has three inputs: the model, the transcript, and the prompt. Two were
already data — a Hugging Face repo id routed through `config/models.toml`, and a file on
disk. The third was a string constant in `src/m2x/extraction.py`, which meant the text
the model was asked could change in a commit that reads as a refactor, and no artefact
recorded which text produced it.

Phase 1B reports an F1 on the dev set. That number is a property of *a model asked a
particular way*. Reported without the prompt it cannot be reproduced or improved on: a
later score might be a better prompt, or the same prompt on a different day.

## Decisions

**1. `prompts/<name>/v<N>.md`, read at runtime.** A prompt directory per name, one file
per version. Markdown with `## system` and `## user` sections; text above the first
heading is prose for humans and is ignored by the loader, so a version file can explain
itself without that explanation reaching the model.

**2. The first version is byte-identical to the constant it replaced.** `v1.md` carries
the old `EXTRACTION_SYSTEM_PROMPT` exactly, verified against it before the constant was
deleted. Rewording in the same change would have made the numbers either side of the
move incomparable — the one thing a library like this exists to prevent. Its paragraphs
are single long lines for that reason; rewrapping is a prompt change.

**3. Latest-by-default, pinnable.** With no version given the loader takes the highest
`vN` present, which is what makes "switching versions needs no code change" true —
shipping `v2.md` moves the extractor onto it. `--prompt-version v1` pins, for re-running
a reported number or scoring two versions against each other. This stays reproducible
because prompts are tracked: a fresh clone at a given commit sees one highest version.
It is the commit that pins a run, never the word "latest".

**4. Versions are ordered numerically.** `v10` is newer than `v9`; lexical sorting says
otherwise and would silently pin the old one.

**5. One resolved version, written to both places.** `extract_record` resolves the
version, stamps it on the `ExtractionOutcome`, and copies it onto the `RunContext` so
every log line carries it. Callers are not asked to pass it. A record and a log line
disagreeing about which prompt ran is exactly the failure being prevented, so it is not
left to call sites to get right.

**6. `prompt_version` on the outcome is required, not optional.** A record that cannot
name its prompt is the thing this ticket exists to prevent. Records written before the
library do not load; `data/` is git-ignored and no F1 had been scored, so there was
nothing to migrate.

## Deviations from the ticket spec

**A. The run log grew a twelfth field, against its own docstring.** `run_log.py` states
the record shape is eleven fields and that adding one is a deliberate decision; a test
asserts the exact field set. The ticket requires the version in the run log, so the
field was added with the argument written down rather than by editing the guard quietly:
the log is what the cost and latency reports are built from, so a prompt-shaped
regression ("v3 doubled the retries") is invisible if the version lives only on the
artefact. It defaults to `null`, which keeps day-one records parseable and is the honest
value for transcription, which has no versioned prompt.

**B. The append-only rule is enforced by a test, not only by review.** The ticket says
"convention + review". `prompts/CHANGELOG.md` records a digest per version and
`tests/test_prompts.py` compares it against the file, failing in both directions — a
version with no changelog row, and a version whose text moved since its row was written.
Convention is what people follow until a deadline; the eval numbers this rule protects
are the project's whole output. The digest covers the `system` and `user` text rather
than the file bytes, so the human-facing prose above the first heading stays correctable
without faking a prompt change.

**C. Extraction calls were being logged as `phase`/`command` "unknown".** Not in the
ticket, but the acceptance criterion is that the record and the run log agree, and an
unattributed line agrees with nothing. `extract_record` now supplies a default
`RunContext` of `phase-1b` / `m2x extract` when the caller gives none.

## Consequences

- `prompts/CHANGELOG.md` is the third leg of the agreement Yash checks: record metadata,
  run-log line, changelog row.
- Editing a cited version fails the suite. The fix is always a new version file and a
  new row; the failure message prints the digest to paste in.
- Rendering is strict in both directions — a placeholder with no value, and a value
  naming no placeholder. The second is the dangerous one: a renamed placeholder would
  hand the model an empty transcript block, and an empty `MeetingRecord` is *valid*, so
  the eval would report 0.0 against the model instead of against the rename.
- M2X-036's first F1 has a version to cite. The `dev-F1` column in the changelog is
  `not yet run` until then, deliberately — an unmeasured number is not written down.
