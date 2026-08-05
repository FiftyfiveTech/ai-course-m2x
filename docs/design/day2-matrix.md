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
| **D-2** | heuristic fallback (silence-gap turns + clustering) | M2X-022 | Saurabh | all 3 |
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

| approach | meeting | attribution accuracy | measured against | note |
|---|---|---|---|---|
| D-1 pyannote | ami-001 | | `eval/ami/ami-001.speakers.json` (490 reference segments) | |
| D-1 pyannote | mtg-001 | | snippet spot-check | |
| D-1 pyannote | mtg-002 | | snippet spot-check | |
| D-2 heuristic | ami-001 | | same reference | |
| D-2 heuristic | mtg-001 | | snippet spot-check | |
| D-2 heuristic | mtg-002 | | snippet spot-check | |

D-2 is only built if pyannote access has not cleared. If it is built, it ships **with its
quality documented as a known limitation**, not quietly.

### Chaptering + summarisation (M2X-023) · Vocabulary (M2X-024) — Yash

| row | meeting | metric | value | note |
|---|---|---|---|---|
| C-1 | | | | |
| C-2 | | | | |
| S-1 | | | | |
| S-2 | | | | |
| V-1 off | | entity % | | |
| V-1 on | | entity % | | |

V-1's denominator is the same hand snippet as T-A/T-B, so M2X-021's entity column and
M2X-024's are directly comparable — the vocabulary delta is readable straight off the
two tables.

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

## Open dependency

**The three hand snippets are not written yet.** `eval/snippets/{mtg-001,mtg-002,ami-001}.txt`
are still templates marked `NOT YET TRANSCRIBED`. Every WER cell and every entity cell
above is blocked on them, and they must be written **by ear** — a reference derived from
model output measures the system against itself.

Everything not needing a reference is now filled: coverage, word counts, detected
language, latency, and the adoption decision. The diarisation reference comparison on
`ami-001` (M2X-022) is likewise unblocked, since AMI ships its own speaker ground truth.

One note for whoever writes the `mtg-002` snippet: T-A and T-B **disagree about the
language of that meeting** (Hindi vs English). The hand reference settles it, and that
alone makes it worth the twenty minutes.
