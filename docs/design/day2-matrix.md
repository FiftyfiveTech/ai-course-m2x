# Day 2 comparison matrix (M2X-020)

Agreed **before** any comparison run, so the playground produces numbers rather than
impressions. Git order is the evidence: this file's commit precedes every M2X-021 /
022 / 023 / 024 run commit.

Filled in by the ticket that owns each row. Empty cells are honest — a blank means not
run yet, never "roughly the same".

## What gets compared

| row | strategy | ticket | owner | meetings |
|---|---|---|---|---|
| **T-A** | `openai/whisper-large-v3` @ groq | M2X-021 | Saurabh | mtg-001, mtg-002, ami-001 |
| **T-B** | `openai/whisper-large-v3-turbo` @ groq | M2X-021 | Saurabh | mtg-001, mtg-002, ami-001 |
| **D-1** | pyannote speaker-diarization (gated HF repo) | M2X-022 | Saurabh | all 3 |
| **D-1c** | same, clustering constrained to the manifest participant count | M2X-022 | Saurabh | ami-001 |
| ~~**D-2**~~ | ~~heuristic fallback (silence-gap turns + clustering)~~ — not built, pyannote access cleared | M2X-022 | Saurabh | — |
| **C-1 / C-2** | chaptering, two strategies | M2X-023 | Yash | Yash picks in AM pairing |
| **S-1 / S-2** | summarisation, two strategies | M2X-023 | Yash | Yash picks in AM pairing |
| **V-1** | vocabulary file on vs off | M2X-024 | Yash | mtg-001, mtg-002 |

**Meeting selection.** Three meetings, chosen to span both registers rather than to be
convenient: `mtg-001` (Hinglish, 17m34s, screen-share), `mtg-002` (Hinglish, 6m38s),
`ami-001` (English, 29m48s). `ami-001` is the only one carrying **both** a hand snippet
and reference speaker turns, so it is the sole row where diarisation accuracy is
measured against ground truth rather than spot-checked. `ami-002`/`ami-003` stay out of
Day 2 — they are held for M2X-033's labelling pool and spending them here would shrink it.

## Metric columns — and what "better" means

| column | definition | better is | decides? |
|---|---|---|---|
| **WER-ish %** | word error rate of the route's output over the snippet's time window, against the hand transcript. `eval/wer.py`: lowercase, strip punctuation, collapse whitespace, word-level Levenshtein ÷ reference word count | **lower** | primary |
| **Entity capture %** | of the vocabulary terms *actually spoken in the snippet window*, the fraction the hypothesis reproduces. Denominator is the hand transcript, **not** the whole vocab file | **higher** | secondary |
| **Latency** | ms per audio-minute, from `data/runs/runs.jsonl`, **live (uncached) calls only** — a cache hit would report the cache's speed under the provider's name | **lower** | tiebreak only |
| **Quality note** | one line of prose. Deliberately unscored | — | never |

**Decision rule.** WER decides. If two routes land within **2 points** — noise on a
120-second sample — entity capture decides. If that is also within 2 points, latency
decides. The quality note never overrides a number; its job is to flag something the
numbers missed and so provoke a *new* metric, not to break a tie.

Normalising latency per audio-minute matters because the three meetings differ by 4.5×
in length; raw milliseconds would rank the shortest meeting fastest and teach nothing.

## The Devanagari confound — and how the runs falsified the plan

**Written before the runs.** Whisper auto-detects `mtg-001`/`mtg-002` as Hindi and
returns Devanagari; the hand snippets keep Hindi in Devanagari and English in Latin, so
the mismatch was expected on the transliterated English terms ("जीपीटी" for GPT). The
plan was to run each route twice — auto-detect, and forced `--language en` — and compute
WER on the forced run, where both sides would be Latin script.

**What actually happened.** `--language en` does not transliterate. It **translates**.

| | first line of `mtg-002` |
|---|---|
| auto | `कि यहां चैनल कंसेप्ट से ठीक है अब अपने को हर कंसेप्ट के ऊपर एक प्रोजेक्ट बनाना है` |
| forced `en` | `We have six concepts here. We have to make one project on each concept.` |

The second is fluent English that nobody said. As a transcript it is unusable: it
destroys the code-switching that is the corpus's whole reason for existing, and every
timestamp now points at speech whose words were never uttered. Scoring WER on it would
have measured translation quality while labelled "transcription accuracy".

**Consequence.** The forced-`en` legs are **withdrawn as a scoring route**. They stay in
the matrix as a recorded negative result, because the next person to hit Devanagari
output will reach for exactly that flag. WER for the Hinglish meetings will be computed
on the **auto** run against the mixed-script reference, where a transliterated English
term counts as the error it genuinely is — which is the number M2X-024's vocabulary file
is supposed to move.

This is the pre-agreed rule working as intended: the matrix was committed first, so the
falsified hypothesis is on the record rather than quietly edited out.

## A metric the plan did not have: coverage

The quality-note column exists to expose a missing metric, and it did so immediately.
Word counts diverged far more than WER-style error would explain — so **coverage**
(summed segment duration ÷ audio duration) was added as a column. It turned out to be
the discriminating measurement of the whole comparison, and unlike WER it needs no hand
reference, so it is available today.

### Coverage was partly an artifact — correction, 2026-08-12

Whisper emits segments with a real time range and **empty text**, and duration-based
coverage counts them. Found while validating the transcripts for the Phase 1 gate:
`mtg-001` carries 5 such segments spanning **112 seconds** — 10.6% of the meeting — and
`mtg-002` carries 2 spanning 33s. Subtracting them gives *voiced* coverage:

| route | meeting | coverage as published | voiced | empty segments |
|---|---|---|---|---|
| T-A | mtg-001 | 98.8% | **88.2%** | 5 (112s) |
| T-A | mtg-002 | 97.6% | **89.3%** | 2 (33s) |
| T-A | ami-001 | 83.7% | 83.7% | 1 (0s) |
| T-B | mtg-001 | 89.7% | **85.3%** | 1 (47s) |
| T-B | mtg-002 | 80.3% | 80.3% | 0 |
| T-B | ami-001 | 80.4% | 80.4% | 0 |

**T-A's lead shrinks by roughly two thirds** — 9.1 → 2.9 points on `mtg-001`, 17.3 → 9.0
on `mtg-002`. The direction is unchanged and the adoption decision is unaffected, but the
margin that decision was originally argued on was overstated, and it should not be quoted
as published again.

Two things follow. The first is that the metric which "turned out to be the discriminating
measurement of the whole comparison" was measuring, in part, silence with a timestamp on
it — worth remembering the next time a metric arrives mid-comparison and immediately
decides it. The second is that WER, which arrived later and was expected only to confirm
what coverage already showed, is now carrying more of the decision than coverage is: it
separates the routes by 16.8 and 31.1 points where voiced coverage separates them by 9.0
and 2.9.

The empty segments are upstream of diarisation — they are present in
`data/comparison/large-v3-auto/*.json` before any merge — so this is Whisper's behaviour
on long pauses, not a defect in the join. Left in place rather than filtered: a segment
that says "these 27 seconds produced no words" is information, and dropping it would
silently shorten the timeline that every citation indexes into.

## Matrix

### Transcription (M2X-021)

Run 2026-08-05, `data/comparison/<route>-<mode>/<meeting>.json`, all legs live (no cache
hits), $0.0000 throughout. WER and entity cells were filled 2026-08-12, once references
existed — see **Reference provenance** below for what each number is measured against,
because the three references are not all the same kind of thing.

| route | meeting | lang mode | detected | coverage % | words | WER-ish % | entity % | latency ms/audio-min |
|---|---|---|---|---|---|---|---|---|
| T-A | mtg-001 | auto | Hindi | **99** | 1956 | **63.5** | n/a | 1197 |
| T-A | mtg-002 | auto | Hindi | **98** | 780 | **64.0** | 0% (0/2) | 1918 |
| T-A | ami-001 | auto | English | 84 | 4404 | **48.5** | n/a | 462 |
| T-B | mtg-001 | auto | Hindi | 90 | 1211 | **94.6** | n/a | 440 |
| T-B | mtg-002 | auto | English | 80 | 261 | **80.8** | 0% (0/2) | 372 |
| T-B | ami-001 | auto | English | 80 | 4324 | **48.9** | n/a | 331 |
| T-A | mtg-001 | forced en | English | 93 | 1948 | withdrawn — translates | | 360 |
| T-A | mtg-002 | forced en | English | 88 | 764 | withdrawn — translates | | 428 |
| T-B | mtg-001 | forced en | English | 89 | 1203 | withdrawn — translates | | 374 |
| T-B | mtg-002 | forced en | English | 70 | 160 | withdrawn — translates | | 329 |

**Quality notes.**

- **T-B collapses on code-switched speech.** On `mtg-002` it returns 261 words against
  T-A's 780 — a third of the content — opening with `I am foreign foreign and I'm
  serious`, which is what the model emits when it cannot decode the audio at all. On
  `mtg-001`, 1211 words against 1956. Not a transcription that is merely worse; large
  stretches of the meeting are simply absent.
- **T-B is fine on clean English.** `ami-001`: 4324 words against T-A's 4404 (−1.8%),
  same coverage band, and **1.4× faster**. The degradation is specific to Hinglish.
- **T-A's `mtg-002` latency (1918 ms/audio-min) is the slowest cell and is not
  trustworthy** — it was the first call of the session and carries connection setup that
  every later leg amortised. Worth one re-run before anyone cites it.
- Latency separates the routes by ~2–3× while quality separates them by a third of the
  content. Per the decision rule, latency does not get a vote here.
- **WER agrees with coverage, and sharpens it.** The Hinglish gap is 16.8 pts on
  `mtg-002` and 31.1 pts on `mtg-001`; on English the two routes are a tie (48.5 vs
  48.9, +0.4). T-B's loss is almost entirely **deletions** — 190 of `mtg-001`'s 260
  reference words and 187 of `mtg-002`'s 328 — which is the same finding coverage
  reported, now with a denominator. A route whose error is deletion does not produce a
  worse sentence; it produces no sentence.

#### Reference provenance — three references, two kinds

The WER cells above are not all measured against the same quality of ground truth, and
the difference matters more than the numbers do.

| reference | kind | in the repo? | what a WER against it means |
|---|---|---|---|
| `ami-001` | AMI's own **manual human annotation**, verbatim | yes | Whisper vs. truth |
| `mtg-002` | **Deepgram `nova-3`** (language=multi), reviewed and accepted 2026-08-12, no by-ear pass | yes | Whisper vs. Deepgram **agreement** |
| `mtg-001` | same | **no — `eval/snippets/mtg-001.local.txt`, git-ignored** | same |

**`mtg-001`'s reference is not in this repository, and its two WER cells are therefore not
reproducible from a fresh clone.** That window is an internal discussion of client delivery
work — module names, per-tenant configuration, five colleague first names — and this repo is
public. `eval/vocab.local.txt` already establishes the rule for terms that cannot ship; a
verbatim reference is the same exposure with no redaction available, because redacting a
reference destroys the word-for-word correspondence that makes it a reference. The numbers
ship, the words do not. The tracked `eval/snippets/mtg-001.txt` is a stub that says so and
exits 2 rather than scoring nothing.

For the two internal meetings this measures inter-vendor agreement, not accuracy. Where
Deepgram and Whisper share a failure mode — and on code-switched Hinglish they plausibly
do — the error is invisible to the metric. Deepgram is at least not the system under
test (`openai/whisper-large-v3` via Groq and Ollama), so the one circularity it avoids is
measuring the pipeline against itself.

**Read the deltas, not the absolutes.** A shared bias cancels in T-A vs T-B and in V-1
off vs on; it does not cancel in "64.0%". Two independent reasons the absolute numbers
are inflated anyway: `ami-001` is AMI IHM, per-headset audio where 94% of utterances
overlap, and the Hinglish references are mixed-script, so a Devanagari/Latin spelling
disagreement scores as a substitution whether or not the word was heard correctly.

Correcting the two internal references by ear later would make their absolutes
meaningful and would move every cell scored against them. That is a known open
improvement, not a defect in the table.

**Adoption decision.** The pipeline stays on **`openai/whisper-large-v3` with language
auto-detection** (T-A, auto). Turbo is 1.4–2.7× faster and costs the same $0.00, but it
loses roughly a third of the words on both Hinglish meetings, and this corpus is half
Hinglish — a route that silently drops content is not a faster version of the same
thing, it is a different and worse instrument, and every downstream phase (extraction,
citations, contradiction detection) inherits whatever it dropped. The forced-`en` variant
is rejected outright: it translates rather than transcribes, which would fabricate
evidence. Turbo remains a reasonable choice for English-only audio and is worth
revisiting if the corpus ever shifts that way; the run records are on disk to re-check
against. **The provisional caveat is now discharged.** This decision was recorded on
coverage and inspection alone because no reference existed; the 2026-08-12 WER run
confirms it and widens it — T-A leads by 16.8 and 31.1 points on the Hinglish meetings
and ties on English, which is the coverage story with a denominator rather than a
different story. What WER did *not* do is upgrade the confidence to accuracy: see
Reference provenance above.

### Diarisation (M2X-022)

`pyannote/speaker-diarization-3.1`, unconstrained clustering, CPU. **D-2 was not built:**
pyannote access cleared, so the heuristic fallback had no reason to exist. The row is
struck rather than left blank — an empty cell reads as "not measured yet", and this one
will never be measured.

| approach | meeting | all speech | single-speaker speech | speakers | latency |
|---|---|---|---|---|---|
| D-1 pyannote | ami-001 | 77.0% | **90.7%** | 9 / 4 | 46.1 min |
| D-1c constrained | ami-001 | 75.2% | **84.1%** | 4 / 4 | 34.3 min |
| D-1 pyannote | mtg-001 | — | — | 8 / 4 in manifest | 27.9 min |
| D-1 pyannote | mtg-002 | — | — | 4 / 3 in manifest | 10.3 min |
| ~~D-2 heuristic~~ | — | not built | — | — | — |

Measured against `eval/ami/ami-001.speakers.json` — 490 reference turns, 1943.4s of
summed speaker-time. **Adopted: D-1, unconstrained.**

Segment attribution held on all three: 99% of 582 segments on ami-001, 99% of 104 on
mtg-001, 100% of 64 on mtg-002. So the *join* works — the transcript and the turns share
a usable time axis, which was the thing M2X-012 kept segment timestamps for.

Cost, for planning: RTF ≈ 1.55 on this CPU, so diarisation is roughly 1.5× wall-clock per
minute of audio. ami-001 took 46 min for 29.8 min of speech. Diarisation is **not** in
`data/runs/runs.jsonl` (it does not go through the adapter), so these latencies live here
and in the artefact JSON only.

#### Two numbers, because one of them is a ceiling

**People talk over each other.** The reference holds 1943.4s of summed speaker-time across
only 1476.8s of wall clock; 389.8s of that wall clock (**26%**) has more than one speaker
active, and 856.5s of speaker-time falls inside those stretches. A system emitting one
speaker per instant therefore cannot exceed **76.0%** (1476.8 ÷ 1943.4) against the full
total, however well it performs. That is a property of the metric, not the model, and
77.0% quoted alone reports a ceiling as if it were an error.

Scored on single-speaker stretches only — the question that actually matters downstream,
*when one person is talking, do we know who?* — pyannote gets **90.7%**.

Voice activity is close to exact: the hypothesis covers 1477.2s of wall clock against the
reference's 1476.8s, a 0.4s difference. The system knows when someone is speaking. What it
gets wrong is which of several simultaneous voices, plus turn boundaries.

`precision` (58.4%) is reported but **is not a probability**: its denominator sums every
(reference, hypothesis) overlap, so one hypothesis second covering two overlapping
reference turns counts twice, and on ami-001 that denominator (2561.7s) exceeds all
referenced speech. Useful for comparing two runs against the same reference; meaningless
as an absolute.

#### Over-clustering is cosmetic — the constraint made attribution worse

The obvious diagnosis was that 9-speakers-where-4-exist drags accuracy down, and that
pyannote's `num_speakers` — with the count already sitting in the manifest — was the fix.
**The run refuted it.** Constraining to 4 gets the count exactly right and runs 26% faster,
and costs **6.6 points** of single-speaker accuracy (90.7% → 84.1%).

The mechanism is visible in the artefacts: segmentation is *identical* between the two runs
(1785.5s of hypothesis speech, 618 vs 620 turns) — the constraint changed only the labels.
Unconstrained, the five phantoms hold 7.9% of speech and map to no reference speaker, so
they score zero but pollute nothing. Constrained, that same speech is forced into the four
real clusters, and it lands on the wrong one.

So a wrong speaker count is the *cheaper* error. Left unconstrained by default, now with
evidence rather than caution as the reason. `--num-speakers` stays in the CLI because the
comparison should be re-runnable, not because it should be used.

| speaker | share | avg turn | maps to |
|---|---|---|---|
| SPEAKER_00 | 38.1% | 4.15s | MEE073 |
| SPEAKER_06 | 24.4% | 2.89s | FEO072 |
| SPEAKER_01 | 20.1% | 2.89s | MEE071 |
| SPEAKER_03 | 9.5% | 2.92s | FEO070 |
| SPEAKER_07 | 2.4% | 0.66s | — |
| SPEAKER_05 | 1.6% | 0.91s | — |
| SPEAKER_08 | 1.5% | 1.88s | — |
| SPEAKER_04 | 1.4% | 4.87s | — |
| SPEAKER_02 | 1.0% | 3.59s | — |

#### A filter that looked right on two meetings, and died on the third

Worth recording because the mistake is the instructive part. On mtg-001 and mtg-002 the
speakers split cleanly by average turn length — real speakers ≥1.84s, phantoms ≤0.86s,
nothing in between — and a ≥1.5s cut recovered the manifest participant count *exactly*
in both (8→4, 4→3). It looked like a rule.

On ami-001, the only meeting with ground truth, the same cut keeps **7** speakers, not 4:
SPEAKER_04 averages 4.87s and SPEAKER_02 3.59s while holding ~1% of speech each. The rule
was fitted to the two meetings that could not contradict it. A share-based cut (≥5%) fares
no better — it gets ami-001 and mtg-001 right and puts mtg-002 at 4 against a manifest
that says 3.

The conclusion is not "find a better threshold". No post-hoc statistic separates real from
spurious across all three, because the information needed is not in the output.

The D-1c run then made the question moot: dropping the phantoms would not have bought
accuracy anyway, since they map to nothing and score zero either way. Two hours went into
chasing a symptom. The reason it is written down is that both wrong turns had the same
shape — a plausible mechanism, adopted before the one meeting that could test it was
consulted.

#### What is actually left on the table

Not the speaker count, and not voice activity. The remaining ~9 points on single-speaker
speech are turn boundaries and confusions between similar voices; the 24% overlapped
portion needs a system that emits concurrent speakers, which is a different capability
rather than a tuning knob. Neither is worth Day 2 time: 90.7% single-speaker attribution
is enough for extraction and citations, which is what Day 3 consumes.

The honest summary for a reader in a hurry: **diarisation works well enough to build on,
the headline 77% understates it, and the two obvious levers (speaker count, filtering) were
both tried and both failed.**

### Chaptering + summarisation (M2X-023) · Vocabulary (M2X-024) — Yash

| row | meeting | metric | value | note |
|---|---|---|---|---|
| C-1 fixed 5-min | ami-001 | chapters · calls · cost | 6 · 0 · $0 | 281–312s each; uniform by construction |
| C-2 LLM topic-shift | ami-001 | chapters · calls · latency | 13 · 1 · 558 ms | **all 12 boundaries inside the first 9.2 min of 29.7**; final chapter = 69% of the meeting |
| S-1 single-pass | ami-001 | questions · calls · tokens | **3/5** · 1 · 5538+283 | misses the late-meeting question entirely |
| S-2 map-reduce | ami-001 | questions · calls · tokens | **3.5/5** · 7 · 6808+775 | +0.5 for 7× the calls and 1.23× the tokens |
| V-1 off | ami-001 | WER · entity % | **48.5%** · n/a | sub 54 · del 150 · ins 18 · ref 458 words |
| V-1 on | ami-001 | WER · entity % | **57.4%** · n/a | sub 91 · del 159 · ins 13 — **+8.9 pts worse**; entity capture undefined, no vocab term is spoken |
| V-1 off | mtg-002 | WER · entity % | **64.0%** · **0%** (0/2) | sub 68 · del 132 · ins 10 · ref 328 words; missed `RAG`, `PRD` |
| V-1 on | mtg-002 | WER · entity % | **83.8%** · **0%** (0/2) | sub 111 · del 161 · ins 3 — **+19.8 pts worse**; the same two terms still missed |

Both summarisation rows run the same model (`meta-llama/Llama-3.1-8B-Instruct`) on the
same provider, so the delta is the strategy and nothing else. Judgement sheet, with the
quoted evidence line behind every score: `eval/judgement/m2x-023-answers.md` — its five
questions were committed before any output existed.

#### C-2 does not work, and the failure is positional

The detector was asked for at most 12 boundaries on a 582-segment meeting. It returned
12 — and put every one inside the first 172 segments. The last 410 segments, 20.5 minutes
and 69% of the meeting, come back as one chapter. Fixed windows are topic-blind, but
blind and uniform beats sighted and concentrated: feeding C-2's output to map-reduce
would give the meeting's whole second half a single summarisation call, which is the
exact failure map-reduce exists to prevent.

Three iterations, recorded because the shape never changed:

| iteration | chapters | boundaries land in | what changed |
|---|---|---|---|
| 1 | 68 | first 75% | outline sent full segment text, overflowed the budget, tail unseen |
| 2 | 57 | early-weighted | per-segment preview (60 chars) so the whole meeting fits |
| 3 | 13 | first 31% of segments | prompt caps the answer at 12 |

Iteration 1 was a defect in our code, not the model's fault: the budget silently cut the
outline, and a detector cannot mark a boundary in text it was never shown. Fixing it
changed the count and not the distribution. Capping the answer fixed compliance and not
the distribution either. **The bias is the model's attention over a long numbered list,
and no prompt in this ticket moved it.**

#### S-2 wins the one question that discriminates, at 7× the calls

The questions were drawn from across the meeting on purpose. Q1/Q3/Q4 — early and middle
— were answered by both. **Q2, a late-meeting decision about the search-results UI, was
missed entirely by single-pass and caught in full by map-reduce**: the
compression-of-the-tail effect showing up exactly where it was predicted to.

Q5 was missed by *both*, which is the useful caveat. Map-reduce buys attention to the
tail, not comprehension. It is not a completeness guarantee.

The other difference never reaches the score. Single-pass writes "The team will decide to
use XSLT to transform the XML" where the transcript has "we could probably even use like
XSLT". The entity is real and its *status* is inflated — a worse failure mode than
invention, because nothing about the sentence looks wrong. Six of S-1's twenty lines open
with "The team will decide to…". Map-reduce's short per-section inputs kept a musing
recognisable as a musing.

#### The free tier picks the provider, not us

Single-pass on this meeting is ~5.5k input tokens and **Groq's free tier refuses it with
HTTP 413** (6 000 tokens-per-minute cap), so the whole comparison ran on NIM. Worth
recording as an architectural fact rather than an annoyance: on the free tier the cheap
single-call strategy is the one that does not fit, while map-reduce's small per-chapter
calls sail under the same cap. Admissibility is an axis alongside cost.

#### Recommendation

**Chaptering: fixed 5-minute windows (C-1).** Free, deterministic, uniform — and the
alternative concentrates 12 of 12 boundaries in the first third. Revisit only with a
model whose attention holds across a long list; this is not a prompt problem.

**Summarisation: map-reduce (S-2), over fixed chapters.** It answers the question
single-pass loses, stays closer to what was actually said, and fits the free tier where
single-pass does not. The price is 7 calls against 1 and 1.23× the tokens — on a
zero-cost tier, latency only (7.8s vs 6.0s). If cost ever becomes real this is the first
trade to revisit: the margin is one question out of five.

V-1's denominator is the same hand snippet as T-A/T-B, so M2X-021's entity column and
M2X-024's are directly comparable — the vocabulary delta is readable straight off the
two tables.

#### V-1 is measurable on `ami-001` now, and the vocabulary makes it worse

`ami-001` has a reference: **AMI's own manual annotation**, not a by-ear snippet. The
meeting is AMI **EN2002b** and the clip is its 357–477s, verified rather than assumed —
see the provenance header in `eval/snippets/ami-001.txt`. Nothing from the system under
test contributed a word.

| leg | WER | sub | del | ins |
|---|---|---|---|---|
| V-1 off | **48.5%** | 54 | 150 | 18 |
| V-1 on | **57.4%** | 91 | 159 | 13 |

**Feeding Whisper the vocabulary costs 8.9 points of WER on the English meeting**, and
the damage is concentrated in substitutions — 54 → 91, up 68%. The prompt is not adding
terms the model was missing; it is pulling the decode toward words that were never said.
That is the same failure `mtg-002` shows in a different currency (780 words → 404, with
hallucinated all-caps tokens), so the two meetings now agree.

**Entity capture stays undefined here**, and the scorer says so itself: *"no vocabulary
terms spoken in this window"*. AMI is a third-party corpus about remote-control design;
none of our 56 terms occur in it.

So on the two meetings where the experiment can run at all, the vocabulary file makes
transcription worse and cannot be scored on the metric it was built for. **The course
lesson does not reproduce here.** Whether that is the finding or the experiment needs a
different corpus is still the evaluator's call.

**Read the absolute numbers with care.** 150 of 458 reference words are deletions because
AMI's IHM annotation is per-headset: 94% of the utterances in this window overlap another
speaker, and 48% are one- or two-word backchannels that a mixed recording cannot
reproduce. That inflates WER for both legs equally — the **off-versus-on delta is the
trustworthy part**, since the reference, the audio and the model are identical across it.

#### `mtg-002` scored 2026-08-12: same verdict, twice the damage

With a reference in place the Hinglish meeting runs, and it reproduces `ami-001`'s
result in a stronger form:

| leg | WER | sub | del | ins | entity capture |
|---|---|---|---|---|---|
| V-1 off | **64.0%** | 68 | 132 | 10 | **0%** (0/2) |
| V-1 on | **83.8%** | 111 | 161 | 3 | **0%** (0/2) |

**+19.8 points worse**, against +8.9 on English — and again concentrated in
substitutions (68 → 111, up 63%), the signature of a decode pulled toward words that
were never said. Two meetings, two languages, same direction, larger on the harder one.

**Entity capture is 0% on both legs, and that is the experiment's actual answer.** Only
two vocabulary terms are spoken in the window — `RAG` and `PRD` — and Whisper misses
both **with the vocabulary file attached**. This is the metric M2X-024 exists to move,
measured on the one meeting where its denominator is non-zero, and the file moved it by
nothing. A 0/2 denominator is thin: it supports "no evidence the vocabulary helps", not
"proof it cannot".

Three things from the staging still shape what the number means, and none is fixed by
having a reference:

1. **The vocabulary is not spoken in `ami-001` at all.** Zero of the 56 terms occur in
   its 4,404 words — AMI is a third-party scenario corpus about note-taking and
   drawings, not our stack. Entity capture's denominator there is zero, so the metric is
   undefined on the one meeting whose script it could read.
2. **On the Hinglish meetings the terms are spoken but come back in Devanagari.**
   `mtg-002` off is 1,939 Devanagari characters against 23 Latin tokens, and every Latin
   token is an ordinary English word (`use`, `next`, `phase`) — not one vocabulary term.
   A Latin-script term list cannot match transliterated speech, so capture scores near
   zero for a reason that is script, not entity loss. Same shape as the WER problem that
   withdrew forced `--language en`.

   **Partly answered by the 2026-08-12 reference.** Deepgram `nova-3` transcribed the
   same audio with `RAG` and `PRD` in Latin, so the reference proves both terms were
   spoken *and* that a transcriber can emit them in the script the vocabulary file uses.
   Whisper produces neither, prompted or not. On these two terms the failure is
   therefore capture, not script — which is a narrower and more damning result than the
   script confound alone. It does not clear the confound for the other 54 terms.
3. **The prompt makes `mtg-002` worse, not better.** Same audio, same model, same
   provider, prompt the only difference: 780 words → 404, Devanagari 1,939 chars → 802,
   and the Latin tokens that appear are hallucinated shouting — `SCREENSHOT` ×5,
   `SMILE`, `FLOOR`, `PROMPT`, `ENGINEERING`, `FUELS` — with the final segment reading
   `STOPS,`. `ami-001` shows no such damage (4,404 → 4,664 words), so this is the
   prompt interacting with code-switched speech, not a general regression.

So the honest reading is that the course lesson does not reproduce on this corpus as
specified: on our English meeting the vocabulary has nothing to capture, and on our own
meetings the prompt degrades the transcript.

**Adoption decision (V-1): the vocabulary file is NOT adopted as a default pipeline
input.** `--vocab` stays an opt-in flag and no route passes it by default. Three
independent measurements point the same way and none points the other:

| evidence | off | on | direction |
|---|---|---|---|
| WER, `ami-001` (English, human reference) | 48.5% | 57.4% | −8.9 |
| WER, `mtg-002` (Hinglish) | 64.0% | 83.8% | −19.8 |
| entity capture, `mtg-002` | 0% (0/2) | 0% (0/2) | no change |
| `mtg-001` "on" leg | — | unobtainable (Groq 500) | n/a |

The metric the file exists to move did not move, the metric it was not supposed to touch
got worse on both meetings, and the one meeting left is blocked by the provider. Adopting
a default that costs 9–20 WER points to buy nothing measurable is not a close call.

**What stays open, and it is a real question, not a formality.** This decides the
*default*, not the *lesson*. Whether the experiment as specified was a fair test of
"pipeline beats model tuning" is the evaluator's call: our corpus is half code-switched
Hinglish, our only English meeting is third-party and contains none of our terms, and
the entity denominator that survived is 2. A Devanagari term list, a
transliteration-aware matcher, or a corpus whose English half is our own would each be a
different experiment — and a defensible one. Recorded here as unresolved rather than
folded into the adoption decision, because the two questions have different answers.

**`mtg-001`'s "on" leg could not be produced.** Its second chunk returns Groq HTTP 500
whenever a prompt is attached — 5 invocations, up to 8 attempts each, against an
opaque `{"error":{"message":"Internal Server Error"}}`. The same chunk transcribes fine
unprompted and fine with a five-term prompt, and the meeting's first chunk accepts the
full prompt, so this is neither the audio nor the documented 224-token cap (the full
prompt is ~153 tokens). Failures are not monotonic in prompt length — 50 terms passed
where 30 failed — which points at provider-side instability on the prompted whisper
path rather than anything in the request. Recorded as a provider constraint; M2X-024's
step 1 asks for one meeting, and `mtg-002` is that meeting.

## Reproducing the numbers

Coverage, words and latency come straight from the transcript JSON:

```bash
uv run m2x process data/raw/mtg-002-course-scope.wav --model openai/whisper-large-v3 \
    --no-summary --meeting-id mtg-002 --transcripts-dir data/comparison/large-v3-auto
```

Re-runs are cache hits, so the *first* run is the one that produces a latency number.
`m2x runs summary` groups the run log by model and provider.

WER and entity capture come from the scorer. The window must match the reference's
window, which differs per meeting (`mtg-001` 211–331, `mtg-002` 80–200, `ami-001`
357–477):

```bash
uv run python eval/wer.py --reference eval/snippets/mtg-002.txt \
    --hypothesis data/comparison/large-v3-auto/mtg-002.json --window 80 200
```

It **refuses** to score a snippet still marked `NOT YET TRANSCRIBED` (exit 2) rather
than returning a number computed against an empty file.

The Deepgram references were produced with:

```bash
curl -s -X POST "https://api.deepgram.com/v1/listen?model=nova-3&language=multi\
&diarize=true&utterances=true&punctuate=true&smart_format=true" \
    -H "Authorization: Token $DEEPGRAM_API_KEY" \
    -H "Content-Type: audio/wav" \
    --data-binary @data/clips/snippet-mtg-002-2min.wav
```

Utterances renumbered `S1`/`S2` in order of first speaking; nothing else changed.

Diarisation and its score:

```bash
uv sync --group diarize     # torch; not installed by a plain `uv sync`
uv run --group diarize m2x diarize data/raw/ami-001.wav \
    --transcript data/comparison/large-v3-auto/ami-001.json --meeting-id ami-001

uv run python eval/diarization_score.py \
    --reference eval/ami/ami-001.speakers.json \
    --hypothesis data/diarization/ami-001.turns.json
```

The scorer prints the label mapping it chose alongside the accuracy, so the number can be
checked by hand against the overlap table rather than taken on trust. Add
`--num-speakers 4` to the `diarize` call for the D-1c row.

## Open dependency — closed 2026-08-12, with a named substitution

**All three references now exist and every WER and entity cell above is filled.** The
dependency did not close the way it was written, and the difference is recorded rather
than glossed: the plan called for three by-ear snippets, and what shipped is one human
annotation (`ami-001`, AMI's own) plus two accepted Deepgram `nova-3` transcripts
(`mtg-001`, `mtg-002`). Whisper-vs-Deepgram agreement is not Whisper-vs-truth — see
**Reference provenance** under the transcription matrix for what that costs and which
numbers survive it. A by-ear pass over the two internal references remains the open
improvement.

**T-A and T-B disagreed about the language of `mtg-002`** (Hindi vs English). The
reference settles it: the meeting is **code-switched**, Hindi in Devanagari with English
technical vocabulary in Latin, in the same sentence. Neither route was right. T-B's
"English" detection is the label it puts on a decode that dropped two thirds of the
words.

**Diarisation on the internal meetings stays unmeasurable, and the references do not
change that.** `mtg-001` and `mtg-002` have no speaker ground truth — AMI is the only
meeting that does. Deepgram supplied speaker turns, but a diarisation reference produced
by a diariser scores agreement between two diarisers, which is not what the cell claims.
The references give a spot-check, not a score. Treat the 90.7% as an English-clean-audio
number and assume Hinglish is worse until something measures it — `mtg-001`'s 8 detected
speakers against 4 participants is the hint that it will be, and the reference's own
2-speaker read of that window says the same thing from the other side.
