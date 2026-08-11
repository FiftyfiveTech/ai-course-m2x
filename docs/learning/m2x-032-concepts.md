# Concepts Behind a Versioned Prompt Library — Primer (M2X-032)

The six concepts the M2X-032 work exercises: the extraction prompt stops being a string
constant and becomes a tracked, versioned artefact that every number can be traced back
to. Each section: what it is, why it matters here, the pitfall.

## 1. A prompt is an input, not source code

The model, the transcript and the prompt are the three inputs to an extraction. Two of
them are already treated as data: the model is a repo id resolved through
`config/models.toml`, the transcript is a file on disk. The prompt was the odd one out —
a Python string literal, changeable in a commit that looks like a refactor.

That asymmetry is what versioning fixes. A prompt lives in `prompts/<name>/v<N>.md`,
is read at runtime, and is named by version wherever its output is recorded. Changing
what the model is asked becomes a visible, dated, reviewable event rather than a diff
buried in `extraction.py`.

Pitfall: treating the move as cosmetic and rewording the prompt in the same change. The
first version file must carry the *existing* text byte-for-byte, or the extraction
numbers before and after the refactor are not comparable and nobody can tell whether the
library or the rewording moved them.

## 2. Attribution: a score belongs to a (model, prompt) pair

Phase 1B's gate is an F1 number on the dev set. An F1 of 0.87 is not a property of the
system; it is a property of *this model* asked *this way*. Report it without the prompt
version and it cannot be reproduced, defended, or improved on — a later 0.91 might be a
better prompt or might be the same prompt scored on a different day.

So the version is stamped in three places that must agree:

| where | what it answers |
|---|---|
| the record's metadata (`prompt_version`) | which prompt produced *this artefact* |
| the run-log line | which prompt was behind *this model call and its cost* |
| `prompts/CHANGELOG.md` | what that version says, why it changed, what it scored |

Two of the three agreeing is the dangerous state: it looks audited and isn't.

Pitfall: stamping only the artefact. The run log is what the cost report and the latency
percentiles are built from, so a prompt-shaped regression ("v3 doubled retries") is
invisible unless the version is on the log line too.

## 3. Append-only versioning, because eval results cite versions

Once a number has been reported against `v1`, `v1` is frozen. An edit to it is not an
improvement, it is a falsification: every record, log line and changelog row that names
`v1` now describes a file that no longer exists, and nothing in the repo says so.

The rule is therefore mechanical — **editing a prompt means adding `v2`, never touching
`v1`.** Old versions are kept, not deleted, for the same reason a lab notebook keeps the
failed run.

Pitfall: "it was only a typo fix." A whitespace change is a prompt change if the model
sees it. The only safe question is whether the bytes differ, not whether a human judges
the difference meaningful.

## 4. Templates and the silent-drop failure

A prompt file carries placeholders (`{{transcript}}`) that the loader substitutes. The
interesting failure is not a crash — it is the substitution that quietly does nothing:
`v2` renames the placeholder, the code still passes `transcript=…`, and the model
receives a prompt with an empty transcript block. It answers with an empty record, which
is a *valid* `MeetingRecord`. The gate then measures an F1 of 0.0 and blames the model.

The defence is to make both directions an error: a placeholder in the template with no
value supplied, and a value supplied that the template never mentions. Rendering is
strict in both directions or it is not a safety net.

## 5. The injection boundary now travels with the version

The paragraph telling the model that everything inside `<transcript>` is data and never
an instruction is the single rule M2X-035 attacks on purpose. Moving it into the prompt
file means an injection result is attributable to the exact wording that was defended
with — and that a weakening of it shows up as a new version in review rather than as a
line change inside a function.

Pitfall: assuming the paragraph is the mitigation. Delimiting reduces the rate; it does
not prove anything. Writes still go through a human approval gate later.

## 6. Reproducible defaults: latest-by-default, pinnable

With no version given, the loader takes the highest version present in the directory.
This is what makes the acceptance criterion true — shipping `v2.md` switches the system
onto it with no code change — and it stays reproducible because prompts are *tracked*:
a fresh clone at a given commit sees exactly one highest version.

Pinning (`--prompt-version v1`) exists for the other job: re-running an old number, or
scoring two versions against each other on the same transcript.

Pitfall: expecting "latest" to be stable across time rather than across clones. It is
the commit that makes a run reproducible, never the word "latest".
