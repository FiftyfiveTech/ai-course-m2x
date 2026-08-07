# Concepts Behind Chaptering + Summarisation Strategies — Primer (M2X-023)

The five concepts the M2X-023 comparison exercises. Each section: what it is, why it
matters here, the pitfall.

> Written at ticket close rather than at ticket start — the ticket's own first step is
> "write the judgement questions before reading any output", and that came first. The
> ordering deviation is recorded in `docs/learning/retros.md`.

## 1. Chunking: choosing the unit before choosing the model

Chaptering picks the unit everything downstream operates on. Map-reduce summarises per
chapter; retrieval later cites within one. Get the unit wrong and no model choice
rescues it — a chapter spanning two thirds of a meeting cannot produce a useful citation
whatever summarises it.

Two strategies, deliberately far apart in cost: fixed windows (arithmetic, free,
topic-blind) versus LLM topic-shift detection (one call, sighted). The ticket exists to
find out whether the sighted one is worth its call.

## 2. Map-reduce vs single-pass: paying calls to buy attention

Single-pass sends the whole transcript in one prompt. Map-reduce summarises each chapter,
then summarises the summaries — N+1 calls. Two things make map-reduce worth it: it
handles transcripts longer than the context window, and it gives every part of the
meeting its own turn at the model's attention.

That second one is the measurable claim. A single-pass summary of a 30-minute meeting
compresses the tail hardest, so late decisions vanish first. If that is true, a question
drawn from the last third should separate the strategies while one drawn from the first
third does not.

Pitfall: letting the reduce step re-compress. Without an explicit "keep everything unique
to any one section" instruction, reduce summarises the summaries and throws away exactly
the detail the N map calls paid for.

## 3. Write the judgement questions first

The methodological core, and the reason this ticket is worth more than its output.

Once you have read a summary, you unconsciously write questions it can pass. The
comparison then measures your memory of one output rather than the two strategies. So the
questions are written from the *transcript* — the source is fair game, the outputs are not
— and committed before any strategy runs. The git log is the evidence, which is why they
are their own commit.

Same discipline as the dev/held-out split (M2X-033): a number measured on data you tuned
against is not a measurement.

Second-order trick: spread the questions across the meeting on purpose. A question set
drawn from one region measures *position*, not strategy.

## 4. Positional attention: a model's failure can be structural, not promptable

The LLM chaptering returned exactly the 12 boundaries it was allowed — and put every one
in the first 9 minutes of a 30-minute meeting. Three iterations (fixing our own outline
truncation, then capping the count) changed how many boundaries came back and never
changed *where* they landed.

The lesson generalises past this ticket: when a model's error has the same shape across
prompt changes, it is a property of the model over that input, not a wording problem.
Knowing when to stop prompt-engineering and write down the limitation is the skill —
"the same wrong turn twice" is the Day-2 diarisation lesson repeating.

## 5. Free-tier admissibility is a design constraint

Single-pass on a 30-minute meeting is ~5.5k input tokens, and Groq's free tier refuses it
with HTTP 413 (6 000 tokens-per-minute). The cheap single-call strategy is the one that
does not fit; map-reduce's small per-chapter calls sail under the same cap.

Cost is not the only axis a strategy is judged on. *Admissibility* — can this request be
made at all, on the tier we actually have — is one too, and it can invert the ranking that
a cost table alone would give you.

## 6. Precision of status, not just of facts

Both summaries were checked for invention and neither invented anything. But single-pass
rendered "we could probably even use like XSLT" as "**The team will decide to use** XSLT
to transform the XML".

The entity is real; its status is inflated. That is a worse failure mode than a
hallucinated fact, because nothing about the sentence looks wrong — you cannot catch it
by fact-checking, only by comparing against the source. A prompt that asks for decisions
will get the grammar of decisions applied to whatever the model found. Watch for it in
Phase 1B, where the extractor is asked for exactly that.

---

Related: `docs/design/day2-matrix.md` (the filled rows and the recommendation) ·
`eval/judgement/m2x-023-questions.md` and `-answers.md`.
