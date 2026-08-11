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

## Matrix

### Transcription (M2X-021)

Run 2026-08-05, `data/comparison/<route>-<mode>/<meeting>.json`, all legs live (no cache
hits), $0.0000 throughout. WER and entity columns stay empty — the hand snippets are not
written yet, and a number computed against an empty reference would look identical to a
real one.

| route | meeting | lang mode | detected | coverage % | words | WER-ish % | entity % | latency ms/audio-min |
|---|---|---|---|---|---|---|---|---|
| T-A | mtg-001 | auto | Hindi | **99** | 1956 | | | 1197 |
| T-A | mtg-002 | auto | Hindi | **98** | 780 | | | 1918 |
| T-A | ami-001 | auto | English | 84 | 4404 | | | 462 |
| T-B | mtg-001 | auto | Hindi | 90 | 1211 | | | 440 |
| T-B | mtg-002 | auto | English | 80 | 261 | | | 372 |
| T-B | ami-001 | auto | English | 80 | 4324 | | | 331 |
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

**Adoption decision.** The pipeline stays on **`openai/whisper-large-v3` with language
auto-detection** (T-A, auto). Turbo is 1.4–2.7× faster and costs the same $0.00, but it
loses roughly a third of the words on both Hinglish meetings, and this corpus is half
Hinglish — a route that silently drops content is not a faster version of the same
thing, it is a different and worse instrument, and every downstream phase (extraction,
citations, contradiction detection) inherits whatever it dropped. The forced-`en` variant
is rejected outright: it translates rather than transcribes, which would fabricate
evidence. Turbo remains a reasonable choice for English-only audio and is worth
revisiting if the corpus ever shifts that way; the run records are on disk to re-check
against. This decision is provisional in one respect — it rests on coverage and
inspection, not WER, because the hand references do not exist yet. WER could still
change the *margin*; it is very unlikely to reverse a one-third content gap.

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
| V-1 off | mtg-002, ami-001 | entity % | | hypothesis staged; number blocked — no hand snippet |
| V-1 on | mtg-002, ami-001 | entity % | | hypothesis staged; number blocked — no hand snippet |

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

#### V-1: both legs are on disk, and the metric still has nowhere to run

`--vocab` now exists (M2X-024), so the "on" leg is runnable and was run. Both legs sit
in `data/comparison/` — `large-v3-auto/` off, `large-v3-vocab/` on — for `mtg-002` and
`ami-001`. The scoring command is unchanged; only the hand snippet is missing.

Three things turned up in the staging that change what the number will mean, and none
of them is fixed by writing the snippets:

1. **The vocabulary is not spoken in `ami-001` at all.** Zero of the 56 terms occur in
   its 4,404 words — AMI is a third-party scenario corpus about note-taking and
   drawings, not our stack. Entity capture's denominator there is zero, so the metric is
   undefined on the one meeting whose script it could read.
2. **On the Hinglish meetings the terms are spoken but come back in Devanagari.**
   `mtg-002` off is 1,939 Devanagari characters against 23 Latin tokens, and every Latin
   token is an ordinary English word (`use`, `next`, `phase`) — not one vocabulary term.
   A Latin-script term list cannot match transliterated speech, so capture would score
   near zero for a reason that is script, not entity loss. Same shape as the WER problem
   that withdrew forced `--language en`.
3. **The prompt makes `mtg-002` worse, not better.** Same audio, same model, same
   provider, prompt the only difference: 780 words → 404, Devanagari 1,939 chars → 802,
   and the Latin tokens that appear are hallucinated shouting — `SCREENSHOT` ×5,
   `SMILE`, `FLOOR`, `PROMPT`, `ENGINEERING`, `FUELS` — with the final segment reading
   `STOPS,`. `ami-001` shows no such damage (4,404 → 4,664 words), so this is the
   prompt interacting with code-switched speech, not a general regression.

So the honest reading available today is that the course lesson does not reproduce on
this corpus as specified: on our English meeting the vocabulary has nothing to capture,
and on our own meetings the prompt degrades the transcript. **Whether that is the
finding, or the experiment needs a different corpus, a Devanagari term list or a
transliteration-aware matcher, is the evaluator's call** — it is recorded here, not
resolved, exactly like point 2 above it.

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

Once a snippet exists, WER and entity capture come from the scorer:

```bash
uv run python eval/wer.py --reference eval/snippets/mtg-002.txt \
    --hypothesis data/comparison/large-v3-auto/mtg-002.json --window 80 200
```

It **refuses** to score a snippet still marked `NOT YET TRANSCRIBED` (exit 2) rather
than returning a number computed against an empty file.

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

## Open dependency

**The three hand snippets are not written yet.** `eval/snippets/{mtg-001,mtg-002,ami-001}.txt`
are still templates marked `NOT YET TRANSCRIBED`. Every WER cell and every entity cell
above is blocked on them, and they must be written **by ear** — a reference derived from
model output measures the system against itself.

Everything not needing a reference is now filled: coverage, word counts, detected
language, latency, and the adoption decision. The diarisation reference comparison on
`ami-001` (M2X-022) is **done** — AMI ships its own speaker ground truth, so that row
never depended on the snippets.

What the missing snippets still block on the diarisation side is narrower than it looks.
`mtg-001` and `mtg-002` have no speaker ground truth at all — AMI is the only meeting that
does — so their accuracy cells are not merely unwritten, they are **unmeasurable from the
corpus as it stands**. The snippets would give a spot-check, not a score. Treat the 90.7%
as an English-clean-audio number and assume Hinglish is worse until something measures it —
mtg-001's 8 detected speakers against 4 participants is the hint that it will be.

One note for whoever writes the `mtg-002` snippet: T-A and T-B **disagree about the
language of that meeting** (Hindi vs English). The hand reference settles it, and that
alone makes it worth the twenty minutes.
