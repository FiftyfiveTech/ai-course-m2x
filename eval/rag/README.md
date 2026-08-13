# The Phase 2 gate instrument (M2X-045)

Thirty questions — **20 single-meeting, 5 cross-meeting, 5 that must abstain** — written
blind against the corpus, never against system output. The Friday gate (M2X-050) scores
context precision ≥0.75, faithfulness ≥0.80 and citation accuracy ≥0.90 on this set;
M2X-046 is the harness that computes them.

## Two halves, held differently

| | in git? | why |
|---|---|---|
| `questions.jsonl` | **yes**, plaintext | the system has to be asked the questions; hiding them would only stop the harness running |
| `expected/<qid>.json` | **never** — git-ignored | a builder who can read the expected segments can tune retrieval until they come back |
| `expected/<qid>.json.gpg` | yes | recoverability, so a fresh clone can score the gate |
| `expected/seal-manifest.json` | yes | integrity — digests, so an edit is visible |

Same tooling and the same reasoning as the held-out labels; see
[`../labels/heldout/README.md`](../labels/heldout/README.md) for why both artefacts are
needed and why the manifest, not the ciphertext, is the one that proves anything.

```bash
uv run python scripts/seal_heldout.py verify --dir eval/rag/expected   # no passphrase
uv run python scripts/seal_heldout.py unseal --dir eval/rag/expected   # gate day
uv run python scripts/validate_rag_questions.py                        # needs it unsealed
```

**The same caveat as the labels applies.** One operator writes the questions, the expected
answers and the system under test, so the seal is a discipline rather than an enforced
boundary — see [`../labels/README.md`](../labels/README.md) §"these labels are not
independent". Unlike the extraction labels, though, these questions were written against
*human-annotated reference transcripts nobody in this project authored*, so what a question
asks about is at least not downstream of the system's own vocabulary.

## The corpus is `eval/tiron/`, and that is a decision

`data/` is git-ignored, so a fresh clone has no meeting transcripts. Every citation `m2x
ask` has ever produced is a `§ heading` against this repository's own markdown — the
`[meeting · speaker · mm:ss–mm:ss]` path, which is the entire product promise, has **never
been exercised against a real meeting** (`docs/design/day4-ask.md` §"What is not verified
here").

The tiron reference transcripts are committed, carry human speaker turns and real
timestamps, and already ground the Phase 1B labels. A gate written against them runs on
every clone with no audio and no transcription provider, and exercises the meeting citation
path on every question.

Four meetings are covered — a mall aquarium project, a marketing meeting for outdoor sports
equipment, a company retreat to Mexico, and a robotic-nurse feature with a contrarian Q&A.
They share a speaker pool, which is deliberate: it makes the cross-meeting questions hard
in the way cross-meeting questions are supposed to be hard.

## Ground truth is turn ranges, never rendered citations

An expected answer names `(meeting_id, first_turn, last_turn)` into the reference
transcript.

- **Not an `mm:ss` string** — that scores the renderer, not the retrieval.
- **Not a chunk id** — that is a function of the chunking parameters and changes the moment
  anyone tunes them, silently invalidating the whole set.

Turn indices are positions in a committed file. A retrieved chunk records the segment range
it covers, so citation accuracy is an **overlap** test between the two — which is the
ticket's rule ("the cited segment id is among the ground-truth segments, or overlaps its
time range"). Overlap rather than containment, because chunks are packed to a character
budget and a correct citation routinely covers the answer plus its neighbours; demanding
containment would fail a chunk for being the size the indexer chose.

## How the questions were chosen

Written by reading the four transcripts end to end and picking facts that are **stated**,
not inferable. Recurring shapes, all deliberate:

- **Answers split across interrupted turns.** `q05` — *"I'm gonna come back on Tuesday
  with a"* / *(Sarah interrupts)* / *"follow up on this"*. A retrieval that returns only the
  first turn has a day and no object.
- **A claim and its qualifier in one span.** `q13` asks whether family can come; spouses
  yes, children explicitly no, eight turns apart. Returning half is a confidently wrong
  answer, which is worse than an abstention.
- **Speaker distractors.** `q10` — Ron proposes Kim Kardashian, Rachel asks Sarah a
  *different* Kim Kardashian question, and only then does Sarah answer. Matching on the
  name alone lands on the wrong turn.

### The must-abstain five are near misses on purpose

Four of the five sit right on top of a strongly matching passage:

| | why it is unanswerable |
|---|---|
| `q26` total aquarium cost | Rachel **asks for exactly this** and is never answered. Highly relevant context retrieves; the system still has to decline. |
| `q27` name of the equipment company | The marketing meeting says "our products" throughout and never names the company. |
| `q28` Nursing Bot's annual revenue | The company is named and described at length; the number is absent. |
| `q29` which hotel the retreat is booked with | The retreat centre's studio, restaurant and pool are all described; it is never named. |
| `q30` 2026 cricket world cup | The control — no lexical overlap with anything. If this one does not abstain, nothing else in the bucket is interpretable. |

A must-abstain set of `q30`s would be trivially passed by the distance threshold alone and
would measure nothing about the model's judgement. These four force the *model* to abstain
on passages the retriever was right to return.

## The re-read pass

The ticket asks for a second pass hours later: *can each answerable question really be
answered from the recorded segment?* Both halves were done.

**Mechanical** — `scripts/validate_rag_questions.py`. Ids unique and paired across both
halves, the 20/5/5 mix, answerable questions carrying both evidence and a gist, unanswerable
ones carrying neither, cross-meeting questions genuinely spanning ≥2 meetings, and every
cited turn resolving against the reference. Exits non-zero on any problem.

**By reading** — every cited span printed back beside its question and checked to contain
the answer. One correction resulted: `q10`'s span ended one turn past Sarah's reply.

The judgement itself is recorded per question in the sealed `notes` field, so at the gate an
adjudicator can see *why* a span was chosen and where the call was close.

## Known limitation

**`gist` is prose and is graded by a judge, not by string equality.** Two correct answers to
the same question rarely share wording — the same finding that replaced token-set F1 with
embedding cosine in M2X-036. That means the answer-correctness half of this set inherits
whatever the judge model's biases are, and only citation accuracy and abstention are
mechanically checkable. Read the gate numbers with that split in mind.
