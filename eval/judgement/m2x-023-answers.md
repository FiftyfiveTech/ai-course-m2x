# M2X-023 judgement sheet — answers

**Meeting:** ami-001 · **Model:** `meta-llama/Llama-3.1-8B-Instruct` via NIM (both
strategies — a different model per strategy would measure the models).
**Outputs judged:** `data/comparison/strategies/ami-001.{single-pass,map-reduce}.md`.
**Questions:** `m2x-023-questions.md`, committed before any output existed.

## Scores

| | Q1 dropped scope | Q2 search UI | Q3 schedule | Q4 report logistics | Q5 XML format | total |
|---|---|---|---|---|---|---|
| S-1 single-pass | yes | **no** | yes | yes | no | **3 / 5** |
| S-2 map-reduce | yes | **yes** | yes | partial | no | **3.5 / 5** |

## Evidence

**Q1 — audio processing dropped.** Both yes; S-2 also carries the reason and where it
goes in the report.
- S-1: "The team will decide to skip the removal of things from the audio files."
- S-2: "The team will not attempt to remove things from the audio files." + "…will put
  the decision to skip removing things from the audio files under 'changes since the
  initial specification' and state that the time was too short."

**Q2 — search-results UI.** The one that separates the strategies, and it is late-meeting
content.
- S-1: **nothing.** Its nearest line is "The team will discuss the display of the text
  file", which is a different topic.
- S-2: "…considering having separate result panes for each type of result, or having a
  single window with tabs or icons…" + "The results should be ordered by meeting" +
  "The results should be color-coded to distinguish between transcript, topic, and
  summary results." All three elements.

**Q3 — schedule.** Both yes, from different facts.
- S-1: "The deadline for completing the topic segmentation is six days." + "…complete the
  development by March 8th."
- S-2: "The team aims to complete the development on March 8th." + "The speaker
  characterization is supposed to be done already, two days ago."

**Q4 — report logistics.** S-1 yes, S-2 partial.
- S-1: "The team will assign tasks to each other to complete the document." + "…send the
  summaries to one person for review."
- S-2: "The speaker will send the notes to someone else for review, who will review them
  during their working hours (12-3 pm)." Reviewer captured; the collation half is not.

**Q5 — disfluency output format.** Both **no**. Neither states the decision to write back
selective XML (printing only the wanted segments) rather than a flat text file. S-1
touches XML but gets it wrong (below); S-2 only notes that disfluency removal has not
been started.

## Accuracy notes (not scored, but they matter more than a point)

Two claims looked fabricated and were checked against the transcript. **Both are
grounded** — no invention in either summary:

- S-2's "The team will meet at 12:30 with Steve" ← "Yeah, 12.30 tomorrow with Steve."
- S-1's XSLT line ← "we could probably even use like XSLT to kind of just transform it".

But S-1 renders that last one as **"The team will decide to use XSLT to transform the
XML."** The transcript has a passing suggestion; the summary has a decision. That is the
more dangerous error class than a hallucinated fact, because it is unfalsifiable at a
glance — the entity is real, only its status was inflated. S-1's phrasing does this
repeatedly ("The team will decide to…" appears six times); the underlying prompt asked
for decisions, and the model supplied the grammar of decisions for whatever it found.

S-2 is not immune, but its per-section calls read short spans where a musing is still
recognisably a musing.

## Reading

The prediction in the questions file held: the questions drawn from the last third are
where the strategies diverged. Q2 is late-meeting content and single-pass missed it
entirely while map-reduce caught all three parts of it. Q1/Q3/Q4 — early and middle —
both answered.

Q5 is the honest counterweight: **both strategies missed it**, so map-reduce is not a
completeness guarantee. It buys attention to the tail, not comprehension.
