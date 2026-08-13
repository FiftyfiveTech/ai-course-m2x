# Eval harness — how a Phase 1B number is computed

The matching rules for field-level F1, written **before** the harness that implements
them and before any score existed to tune them against. That ordering is the point: a
matching rule settled after seeing results is a rule chosen to produce those results, and
it is part of why the previous run's 0.8063 settled nothing.

The contract these implement is
[`docs/design/day3-schema.md` §The frozen contract](../docs/design/day3-schema.md), agreed
in M2X-030. **If this file and that one ever disagree, the design doc wins** and this one
is the bug.

Related: [`labels/README.md`](labels/README.md) for how the ground truth was produced and
why it is not independent.

## Command

```bash
uv run m2x eval extraction --set dev        # iterate freely
uv run m2x eval extraction --set heldout    # M2X-040 only, once, then burnt
```

Runs the extractor over each case in the set through the adapter (so cached, logged and
costed like any other call), aligns what comes back against the labels, and prints a
per-field table plus the overall micro-F1. Every run appends a record to `results/`
carrying the prompt version, the git SHA and the set name, because a number that cannot
name the prompt that earned it is a rumour.

## What gets compared

An extracted `MeetingRecord` against a labelled one, per case, then summed across cases.

### 1. Kind is a hard partition

A `Risk` never matches an `ActionItem`, however similar the text. Items are matched only
within their own list. A model that files a decision as a risk is wrong twice — one false
positive and one false negative — and that is the intended accounting, because
mis-categorised output is not usable downstream.

### 2. Item identity: embedding cosine ≥ 0.675

Free text cannot be compared exactly. Descriptions are embedded with a pinned model and
two items are candidates for matching when their cosine is **≥ 0.675**.

> **Contract change, M2X-036.** This replaces token-set F1 ≥ 0.60, which was frozen in
> M2X-030 and is superseded. Every number computed under the old rule has been re-run;
> rows in `results/` carry `similarity`, `match_threshold` and `embed_model_repo_id`, so
> a figure produced under one rule is never silently compared with one produced under the
> other. `--similarity lexical` reproduces the old rule on demand.

**Why the old rule was replaced.** It measured phrasing. Of 72 labelled dev items, five
found any candidate above 0.60, while the band beneath was full of pairs any reader calls
identical — *"Find somebody to shoot the testimonial videos and edit them properly"*
against *"Linda will find someone to take the video and edit it properly"* scored 0.43.
Two correct summaries of one fact routinely share few content words.

Containment and stemming were both evaluated as repairs and both rejected. **Containment
is disqualified outright**: every subset scores 1.00, so the one-word fragment `"adopt"`
matches any item containing that word — with the extractor already over-extracting, that
converts its worst failure into free true positives. **Stemming is safe but
insufficient**, leaving five of six known-identical pairs below threshold. No
deterministic lexical metric closes a paraphrase gap.

**Why embeddings are acceptable now.** The original objection — that an upgrade silently
changes what a score means — holds only if the model is *unrecorded*. It is pinned
(`nomic-ai/nomic-embed-text-v1.5`), it goes through `ModelAdapter.embed()` so a re-run
over unchanged text is a cache hit returning identical vectors, and the model id is
written onto every results row. A change now appears in the diff.

**How 0.675 was chosen.** Calibrated against fifteen pairs written down as SAME or
DIFFERENT **by reading them, before any cosine was computed** — never against the
resulting F1. Lowest SAME landed at 0.6928, highest DIFFERENT at 0.6586; the threshold is
the midpoint of that gap. The fragment `"adopt"` sits at 0.5013.

**The gap is 0.034 wide on fifteen pairs — separation, not comfort.** Same-topic,
different-claim pairs crowd it from below, and a larger calibration set would likely
narrow it. Treat it as a working threshold a later ticket should re-derive, and note that
changing it remains a contract change: a new row here, and every affected number re-run.

### 3. Pairing: greedy, one-to-one, deterministic

Within a kind, all candidate pairs scoring ≥ 0.60 are sorted by descending similarity and
taken greedily; each item is used at most once. Ties break by (labelled index, extracted
index) ascending, so two runs over the same data pair the same items — without that, the
same output could score differently on consecutive runs.

Unpaired extracted items are **false positives**. Unpaired labelled items are **false
negatives**. This is what makes over-extraction and hallucination cost precision, and
misses cost recall.

### 4. Field scoring, for matched pairs only

| field | rule |
|---|---|
| `description` | The match itself. Pairing at ≥ 0.60 *is* the description being correct. |
| `owner` | Exact after canonicalisation: casefold, strip titles, map through `vocab.txt`. **`null` matches `null` only.** |
| `deadline` | Exact string equality on `YYYY-MM-DD`. **`null` matches `null` only.** See the caveat below. |
| `evidence` | **Not scored here.** Reported separately as schema-validity. |

`null` matching only `null` is the honesty incentive from the frozen contract: a guessed
owner costs precision, while admitting ignorance costs nothing. A model that says "I
don't know" must be able to outscore one that confabulates, or the eval rewards behaviour
the product cannot ship.

Evidence is excluded from matching deliberately. A citation is a **validity** property —
it either resolves against the transcript or it does not, and it is already enforced
inside the extraction retry loop. Averaging it into a content score would let good
citations paper over wrong content.

### 5. `deadline` is reported separately, not folded into micro-F1

**The ground truth contains zero deadlines.** All 35 labelled actions have
`deadline: null`, because every deadline spoken anywhere in the corpus is relative ("by
next week Wednesday", "Monday", "in two days") and no tiron meeting carries a date to
resolve against — the conditional rule in the frozen contract, meeting real data.

So the field has **no positive examples**. Precision and recall over it are degenerate:
any deadline the extractor emits is a false positive, and there is no true positive
available to earn. Averaging that into an overall micro-F1 folds in a field that cannot
be earned and quietly drags the number toward whatever the null-rate happens to be.

It is therefore reported as its own line — **deadline abstention rate**, the fraction of
matched actions where the extractor correctly emitted `null` — and excluded from the
headline figure. This is a deviation from the ticket's "field-level F1" wording, taken
because the literal reading produces a number that measures the corpus rather than the
model. Recorded here and in the gate record rather than left implicit.

If a future corpus supplies dated meetings, `deadline` rejoins micro-F1 and every number
computed under this rule is re-run.

## The headline number

**Micro-F1 over all matched/unmatched items across all four kinds, plus `owner` on
matched actions.** Micro rather than macro: macro would give a case with one item the
same weight as a case with twenty, and the set deliberately contains both.

Reported alongside, never folded in:

- **schema-validity** — the fraction of cases that produced a valid `MeetingRecord` at
  all. The Phase 1B gate wants 100%.
- **deadline abstention rate** — see above.
- **per-kind P/R/F1** — so iteration knows *where* to work, which is the whole point of
  the table in M2X-036.

## What this number is not

The labels were written by the same author as the extraction prompt (see
[`labels/README.md`](labels/README.md)). Every figure this harness produces is an **upper
bound, not an independent measurement**. A low score is still bad news; a high score is
not proof.
