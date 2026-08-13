# Day 4 — the Phase 2 measurement harness (M2X-046)

**Ticket:** M2X-046 (Odoo 4947) · **Date:** 2026-08-13 · **Depends on:** M2X-044, M2X-045

One command — `uv run m2x eval rag` — produces the three PRD gate numbers plus abstention.

## The four figures are not the same kind of claim

This is the design's organising idea, and the report prints it rather than leaving it to a
reader's memory:

| metric | floor | computed by | what it is |
|---|---|---|---|
| context precision | ≥0.75 | RAGAS, judge LLM | a model's opinion |
| faithfulness | ≥0.80 | RAGAS, judge LLM | a model's opinion |
| citation accuracy | ≥0.90 | this module | mechanical |
| abstention accuracy | — | this module | mechanical |

The two RAGAS metrics ask a language model to judge a language model. That is the standard
instrument and it is what the ticket specifies, but on the zero-spend stack **the judge
shares the answerer's weights** — both default to `meta-llama/Llama-3.1-8B-Instruct`,
because there is no stronger free judge available. A judge with the answerer's blind spots
inflates faithfulness in exactly the cases where the answerer is confidently wrong.

So `--judge-model` is a flag, the judge id is on every results row, and the report ends with
a sentence saying which two numbers to trust first. A gate record quoting four figures as
though they were one kind of claim is the failure this project keeps repairing after the
fact.

## Citation accuracy: ours, and text never enters it

The ticket names the property the reviewer must check: *"it must fail a citation whose text
merely sounds right but points at the wrong segment."* No similarity metric can do that —
sounding right is what a similarity metric rewards.

A citation is correct **iff** the chunk it names covers turns that overlap a ground-truth
span for that question, in the same meeting. That is it. The quote, the phrasing and the
answer's quality are all irrelevant to this number.

The sanity case that demonstrates it (`test_a_wrong_citation_fails_however_plausible_its_quote`)
lifts the quote verbatim from the *correct* passage and attaches it to the *wrong* chunk.
Any check that consults the text passes it; only resolving the chunk to its turn range
catches it.

Three details that each move the number if wrong:

**Micro, not macro.** Correct citations over all citations emitted. Macro would let one
sloppy answer with six citations hide behind a careful one with one.

**Uncited answers are counted separately.** An answer with no citations contributes to
neither numerator nor denominator, so a system that stopped citing entirely would score
0.0 for having no denominator — indistinguishable from one whose every citation was wrong.
The count is printed beside the ratio.

**The 1-based/0-based boundary is crossed in exactly one function.** `Chunk.segment_start`
is 1-based, an `EvidenceSpan` turn index is 0-based. `chunk_turn_range` is the only
converter, and it is tested at both edges — an off-by-one here shifts every citation score
in the gate by up to a whole chunk.

## Abstention is correctness, not a rate

The five must-abstain questions are correct only when the system abstains; the twenty-five
answerable ones are correct only when it does not. An undifferentiated abstention *rate*
would let a system that declines everything score 5/30 and read as cautious rather than
useless.

Unanswerable questions are **never sent to RAGAS**. Context precision asks whether each
retrieved passage was needed to reach the reference answer; with no reference answer it is
undefined, and judging it anyway would score the model on an impossible task and drag the
headline down for behaving correctly.

## Deviations and dependency notes

**1. RAGAS needed `langchain-community` pinned below 0.4.** `ragas==0.4.3` imports
`langchain_community.chat_models.vertexai`, which 0.4.x removed when the package was
sunset. Pinned to `<0.4` in the optional `ragas` group. Two things worth recording: the
import chain reaches a **Vertex AI (Gemini)** integration, and Gemini is banned by
`CLAUDE.md` — nothing calls it, but the dependency is there. And a sunset package under the
gate's dependency tree is a maintenance liability that should be revisited before Phase 2's
numbers are relied on beyond this week.

**2. RAGAS is an optional dependency group, not a main one.** `uv sync --group ragas` pulls
the whole langchain stack, which nothing else in this repo needs. The harness therefore
takes its judge as an **injected callable**, and `m2x.ragas_bridge` imports RAGAS lazily.
The consequence that matters: the citation checker, the abstention scoring, the aggregation
and all three ticket-mandated sanity cases are testable on a fresh clone with no optional
dependency and no judge model.

**3. `AskOutcome` grew a `hits` field.** It recorded `retrieved` as a *count*, and
`ResolvedCitation` carries a `chunk_id` but no turn range — so the only way to learn which
turns a citation pointed at was to query the index again by id. A second lookup that could
disagree with the first would silently move citation accuracy, and RAGAS needs the passage
*text* anyway, which no id supplies. Additive and defaulted, so nothing else changed.

**4. `chunk_segments` was split out of `chunk_transcript`.** A `Transcript` is an
`AdapterResult` carrying a provider, a latency and a cost — facts about a call. The tiron
reference transcripts were written by human annotators, so there is no such call, and
`m2x.reference_transcript` deliberately refuses to invent one. Chunking only ever needed the
segments.

**5. RAGAS's `temperature=0.01` is accepted and dropped.** The adapter pins 0.0 and exposes
no temperature. A judge at 0.01 and one at 0.0 are not quite the same judge; if a judged
number ever fails to reproduce, this is the first place to look. `n > 1` raises rather than
being quietly satisfied — RAGAS uses it for self-consistency sampling, and returning one
completion n times would read as unanimous agreement between judges never consulted.

## What the harness deliberately does not do

**It does not sweep `top_k`.** M2X-042 records `top_k = 5` as an unmeasured default and
notes that context precision is a direct function of it. The knob is a flag and is written
onto every results row, so the sweep is one loop away — but running it and choosing a value
is a decision for the gate ticket, not something this harness should quietly bake in.

**It does not re-derive `--max-distance`.** Same reasoning, but the raw material is now
there: every results row carries `nearest_distance` per question, so the thirty questions
can replace the eight that 0.48 currently rests on without another live run.

**It does not freeze the answers.** M2X-041 gave the extraction eval `--fixtures replay` so
its gate number reproduces on a fresh clone. The RAG eval has the identical problem —
sampled answers, git-ignored cache — and does **not** have the fix. Recorded as open rather
than half-built: the fixture layout here has to hold retrieval *and* generation *and* judge
verdicts, which is a larger design than porting three functions, and no RAG number is
gate-bearing until Friday.

## Verified

Unit and sanity coverage is in `tests/test_eval_rag.py` — 21 tests, no network, no judge
model, including the ticket's three known-outcome cases (perfect answer, unfaithful answer,
wrong citation) and both boundaries of the overlap test. `tests/test_ragas_bridge.py` adds
6 more against the RAGAS surface.

### Defect found live, which no unit test could have caught

The first `m2x eval rag` run failed on 8 of 22 judged questions with
`'AdapterRagasLLM' object has no attribute 'generate'`.

The bridge implemented the three methods `BaseRagasLLM` declares **abstract**
(`generate_text`, `agenerate_text`, `is_finished`). RAGAS's metrics call `generate` — the
base class's async wrapper around them. Duck typing against a base class means
re-implementing everything the library's *callers* reach for, and that is not the same set
as the methods the base declares abstract.

The unit suite could not have found it. The harness takes its judge as an injected
callable — deliberately, so the mechanical half of the gate is testable without the
optional dependency — so no RAGAS code ran until the live command did. **A wrapper around a
third-party interface is only tested by the third party.**

The fix is a `generate` method. The *lesson* is a **surface test**:
`test_the_bridge_covers_every_public_entry_point_ragas_exposes` compares the bridge's public
callables against `BaseRagasLLM`'s and fails on any gap. It found a second one immediately —
`get_temperature`, which RAGAS uses to pick a sampling temperature for self-consistency and
which this bridge must pin to 0.0. That one never reached a live run at all, which is the
whole argument for testing surfaces rather than guessing which methods matter.

### The live baseline

Two runs, both against the four tiron meetings, `--provider ollama`, Llama-3.1-8B
answering *and* judging, `top_k=5`, `max_distance=0.48`. The second is after the `generate`
fix; the first is kept because the difference between them is the size of the defect.

| metric | run 1 (broken judge) | **run 2** | floor | |
|---|---|---|---|---|
| context precision | 0.0000 | **0.4649** | 0.75 | FAIL |
| faithfulness | 0.0000 | **0.6667** | 0.80 | FAIL |
| citation accuracy | 0.7857 | **0.7857** | 0.90 | FAIL |
| abstention accuracy | 0.4333 | **0.4333** | — | — |
| judge failures | 8 | **1** | | |

Citation accuracy and abstention are identical across both runs — as they must be, since
neither touches the judge. That is a useful accident: it confirms the mechanical half is
independent of the RAGAS half in practice, not just in the design.

| kind | correct |
|---|---|
| single_meeting | 7/20 |
| cross_meeting | 1/5 |
| **unanswerable** | **5/5** |

**All four legs fail.** That is the honest Phase 2 baseline going into Friday, and it is
worth having a day early rather than discovering it at the gate.

The one remaining judge failure is `q14: ollama: transport failure: timed out` — recorded
as a failure and excluded from both means rather than counted as a zero, which is the
design working. A zero there would have dragged faithfulness down by a question the judge
never actually assessed.

**The headline finding is the shape, not the numbers.** The system abstained on most
answerable questions while abstaining correctly on every unanswerable one. That is the safe
direction to fail in — nothing was fabricated — but it is a failure, and it is exactly what
M2X-045's corpus choice was meant to surface.

The visible cause in the run log is the quote check: `the quote is not in passage C2; copy
the words verbatim`. `docs/design/day4-ask.md` already recorded this failure mode against
markdown documents and fixed it by folding markdown emphasis. Meeting transcripts break it
again and harder — reference turns carry `<FILL/>`, `<UNKNOWN/>`, `<PName>` tags and doubled
spaces from disfluency annotation, so a model quoting what it *reads* cannot reproduce the
stored string byte for byte.

This is a real defect in `m2x ask`, not in the harness, and it belongs to the Friday gate
ticket rather than to this one. The three candidate directions, none taken here: fold the
annotation tags in `normalise` the way markdown emphasis already is; strip them at index
time so the model never sees them; or relax the quote check to a token-subsequence test —
which `day4-ask.md` argues against, because it turns a substring test into a similarity
test.

**Citation accuracy of 0.7857 is the one number to carry forward.** It is mechanical, it is
computed over the answers that *did* get through, and it says that roughly one citation in
five points at the wrong segment even when the answer survives grounding.

### What Friday inherits

Ranked by how much they move the gate, and none of them is a harness change:

1. **The quote check against transcript text** — costs 13 of 20 single-meeting questions
   and 4 of 5 cross-meeting ones. Nothing else on this list matters until it is fixed.
2. **`top_k = 5`, never varied.** Context precision at 0.4649 is a direct function of it:
   five retrieved passages for a question one turn answers is four irrelevant contexts by
   construction. This is the cheapest experiment available and it may be most of the gap.
3. **`max_distance = 0.48`, resting on eight questions.** Every results row now carries
   `nearest_distance` per question, so thirty measurements can replace the eight without
   another live run.
4. **The judge shares the answerer's weights**, and a local 8B judging its own output is
   the weakest link in the two RAGAS figures. `--judge-model` is the flag.

The `rag/v2` prompt default is still resolved by `latest_version`, which M2X-042 flagged as
the same trap that moved the extraction default from v3 to v5 on a merge. A gate-bearing
number now exists, so that should be pinned before Friday rather than after.
