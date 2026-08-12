# Day 4 — cited answers and abstention (M2X-044)

**Ticket:** M2X-044 (Odoo 4645) · **Status:** built · **Depends on:** M2X-043

A question in, an answer whose every claim points at the passage it came from — or
"Not found in the meeting corpus".

## Problem

M2X-043 stopped one step short on purpose: *"a rank is not a confidence: the nearest chunk
to a question nobody discussed is still a chunk."* Retrieval always returns something.
Turning that into a Q&A command has two failure modes, and only one of them looks like a
failure:

* **A fabricated citation.** The model writes `[mtg-001 · Yash · 14:32]` for a claim
  nobody made. It reads as more trustworthy than an uncited answer, which makes it worse.
* **A confident answer to an unanswerable question.** The corpus does not cover it, the
  nearest chunk is retrieved anyway, and the model writes something plausible over the top.

## Decisions

### 1. The model cites references, never timestamps

Retrieved passages are labelled `[C1]`, `[C2]`… in the prompt. The model cites those
labels. The reader-facing `[meeting · speaker · mm:ss–mm:ss]` is rendered from the
metadata already stored on that chunk at index time.

The alternative — let the model write the citation string and parse it back — can only
ever be a filter after the fact. Here a timestamp the model cannot type is a timestamp it
cannot invent, and citing `C9` when five passages were supplied is not a wrong citation, it
is not a citation: it fails structurally in the validator.

Documents have no clock, so they cite `[readme · § Scope]`. On a fresh clone the corpus is
documents only (`data/` is git-ignored), and a document citation that pretended to a
timestamp would be exactly the failure being designed out.

### 2. A reference check is not enough — quotes are checked too

A model can cite a real passage for a claim that passage does not support. So each
citation carries a short verbatim quote, and the quote must appear in the passage it cites.
`MIN_QUOTE_CHARS = 12` stops the check being theatre: `"the"` is a substring of every
passage.

Comparison folds whitespace, case and markdown emphasis, and nothing else — see
[Defect found live](#defect-found-live-v1-abstained-on-an-answerable-question). Wording and
word order are untouched, because folding those would turn a substring test into a
similarity test, which is the thing this validator exists to not be.

### 3. Validation happens inside the retry loop; abstention is the floor

Citation checks run as Pydantic validators with the retrieved set in validation context —
the same wiring `m2x extract` uses for evidence resolution, so an invalid citation comes
back to the model as an error to fix rather than being silently dropped.

Where this **deviates from the extractor**: when the retry budget is gone, `extract`
raises and `ask` abstains. A meeting with no valid record is a gate failure to look at; a
question the system cannot ground has a true thing left to say. One retry, not two — a
citation that does not resolve means the model is reaching for something not in front of
it, and a second retry buys a more confident version of the same reach.

Four abstention doors, each recorded as a reason because they are not equally good news:

| reason | meaning | model called |
|---|---|---|
| `no_match` | retrieval returned nothing — empty or unbuilt index | no |
| `below_threshold` | nearest passage further than `--max-distance` | no |
| `model_abstained` | passages were near enough; the model read them and declined | yes |
| `ungrounded` | the model answered, and its citations never validated | yes |

An abstention exits 0. It is a result the gate grades on Friday, not an error.

### 4. The threshold is provisional, measured, and a flag

Nearest-hit distance over eight questions on the tracked doc corpus (82 chunks,
`nomic-embed-text-v1.5`):

| question | nearest | docs answer it? |
|---|---|---|
| what are the three RAG gate metrics | 0.2963 | yes |
| what is the corpus made of | 0.3693 | yes |
| how is a gate number verified | 0.3929 | yes |
| how does the response cache key work | 0.4166 | yes |
| which models are banned and why | 0.4414 | yes (wrong section on top) |
| what is the office wifi password | 0.5241 | no |
| how do I file a reimbursement claim | 0.5446 | no |
| who won the 2026 cricket world cup | 0.5589 | no |

Answerable tops out at 0.4414, unanswerable starts at 0.5241. `DEFAULT_MAX_DISTANCE = 0.48`
sits in that gap.

**Eight questions is not a separation.** One more question could close it, and the
answerable end already spreads across 0.15 of distance with no relationship to answer
quality — 0.4414 retrieves the *wrong section* of a question the docs do answer. The
threshold is best read as "past here a model call is not worth making", not as a
correctness boundary, and the model does most of the real abstaining because it can read
the passage. The number that would justify a value is context precision (M2X-045/046).

M2X-043 recorded 0.30 for a good hit and 0.44 for a mediocre one and concluded there was no
clean separation. That still holds *within* the answerable population; what these
measurements add is that the unanswerable population sits clearly beyond it, on this
sample.

### 5. The prompt is versioned, and both legs carry the version

`prompts/rag/`, via the M2X-032 library. The resolved version is stamped onto `AskOutcome`
and onto every run-log line — including the *embedding* call, which required threading an
optional `context` through `query_index`. Retrieval is half of what produced the answer, and
a question whose retrieval leg is unversioned is only half-attributable.

Abstention rate is prompt-sensitive enough that a rate which cannot name its prompt is not
a measurement. The next section is that claim being demonstrated rather than asserted.

## Defect found live: v1 abstained on an answerable question

Everything above was unit-tested green before the first live call. Two things then failed
against Llama-3.1-8B on Ollama, neither visible to a mocked test:

1. **`v1` put the passage text in the reference field.** Asked for the three RAG gate
   metrics — a question the handbook answers, retrieved at distance 0.2963 — the model
   returned citations whose `passage` field held the whole passage. The validator rejected
   all three, the retry did the same, and the command abstained.
2. **The quote check rejected markdown.** With that fixed, the model quoted
   `Citation accuracy (≥0.90)` where the handbook writes `**Citation accuracy** (≥0.90)`.
   A formatting difference, rejected as a fabricated quote — and again an abstention on an
   answerable question.

Both were false abstentions, and both are the *safe* direction to fail in: nothing was
fabricated and nothing unsupported was printed. The guard worked; the prompt and the
comparison were wrong.

Fixes: `prompts/rag/v2.md` states that the reference field takes the label only and shows a
worked citation; the schema field was renamed `passage` → `passage_ref` with a description,
because Instructor renders field names into the prompt and `passage` reads as an invitation
to paste one; and `normalise` now folds markdown emphasis alongside whitespace and case.
`v1` stays on disk with its changelog row — it is the version the false abstention was
measured on.

**Generalises:** a validator that is strict in the safe direction still costs you the
feature. "Refused to answer" and "answered wrongly" are both failures on Friday; only the
second is dangerous, but only the first is invisible in a unit test.

## Verified

Live, `--provider ollama`, `nomic-embed-text-v1.5` + `Llama-3.1-8B-Instruct`, against the
tracked doc corpus. The ticket's acceptance criteria are three answerable questions and one
unanswerable:

```
$ uv run m2x ask "what are the three RAG gate metrics"
Context precision, Faithfulness, and Citation accuracy
  [m2x-week1-handbook · § 4.4 RAGAS and the three gate metrics]  distance 0.2979
     "Context precision (≥0.75): of the retrieved chunks, what fraction were relevant?"
  [m2x-week1-handbook · § 4.4 RAGAS and the three gate metrics]  distance 0.2979
     "Faithfulness (≥0.80): what fraction of the answer's claims are supported by the retrieved chunks?"
  [m2x-week1-handbook · § 4.4 RAGAS and the three gate metrics]  distance 0.2979
     "Citation accuracy (≥0.90): does the cited segment actually contain the claim?"
  prompt    rag/v2

$ uv run m2x ask "what is the corpus made of"
The corpus is made of internal FiftyFive Teams recordings and an English control set
pulled from Hugging Face.
  [corpus · § Pilot corpus (M2X-000)]  distance 0.3693
  [corpus · § English set — AMI corpus from Hugging Face]  distance 0.4273
  prompt    rag/v2

$ uv run m2x ask "how is a gate number verified"
A gate number is verified when the supervisor re-runs the command and sees the same output.
  [README · § Rules that survive from the reset]  distance 0.3929
     "A gate number counts only when the supervisor re-runs the command and sees the same output."
  prompt    rag/v2

$ uv run m2x ask "who won the 2026 cricket world cup"
Not found in the meeting corpus
  abstained below_threshold  (nearest 0.5589, threshold 0.4800)
  prompt    rag/v2
```

Three correct answers, six citations, every one of them resolving to a passage that
contains the quoted words. One abstention on the unanswerable question.

**What is not verified here.** These are documents, not meetings: no `mm:ss` citation has
been produced against real meeting audio, because `data/` is git-ignored and a fresh clone
has no transcripts. The meeting-citation path is unit-tested
(`test_a_meeting_citation_reads_meeting_speaker_timestamps`) and structurally identical —
the timestamp comes from the same stored metadata — but the ticket's "Yash clicks through
five citations at mm:ss" check needs a transcript in `data/`. Four questions is also not an
abstention *rate*; the eval set is 30 questions and belongs to M2X-045/046.

## Consequences

- Retrieval quality is now visible as a product behaviour rather than a debug command. A
  weak retrieval no longer degrades quietly into a plausible answer; it degrades into a
  refusal, which is loud.
- `query_index` takes an optional `RunContext`, so any caller can attribute the retrieval
  leg to what it is really doing.
- The eval set (M2X-045/046) has something to score, and `abstention_reason` gives it four
  buckets rather than one. `ungrounded` is the bucket worth reading first: it means
  retrieval found something and the grounding still did not hold.
- `--max-distance` will move once context precision exists. Any abstention rate reported
  before then must quote both the threshold and the prompt version.
