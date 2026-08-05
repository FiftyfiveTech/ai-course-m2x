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

## The Devanagari confound — read before filling T-A/T-B

Whisper auto-detects `mtg-001`/`mtg-002` as Hindi and returns **Devanagari**, with
English technical terms transliterated phonetically ("जीपीटी" for GPT). The hand
snippets are written in Latin script. A word-level WER across two scripts is ~100%
regardless of transcription quality — it measures the script, not the system.

So each route runs **twice** on the Hinglish meetings:

- **auto** — no `--language`. WER recorded as `n/a (script mismatch)`, never as a number.
  Entity capture is still meaningful and still recorded.
- **forced `en`** — `--language en`. This is the run WER is computed on.

`ami-001` is English; one run per route, no variant. Total: 6 required transcripts plus
4 language variants, all free after the first pass (content-hash cache).

If forcing `en` turns out to *raise* WER on Hinglish audio, that is a finding worth the
row, not a failed experiment — it would say the model transcribes code-switched speech
better when left to detect, and the reference format is what needs to change.

## Matrix

### Transcription (M2X-021)

| route | meeting | lang mode | WER-ish % | entity % | latency ms/audio-min | quality note |
|---|---|---|---|---|---|---|
| T-A | mtg-001 | auto | n/a (script) | | | |
| T-A | mtg-001 | forced en | | | | |
| T-A | mtg-002 | auto | n/a (script) | | | |
| T-A | mtg-002 | forced en | | | | |
| T-A | ami-001 | en | | | | |
| T-B | mtg-001 | auto | n/a (script) | | | |
| T-B | mtg-001 | forced en | | | | |
| T-B | mtg-002 | auto | n/a (script) | | | |
| T-B | mtg-002 | forced en | | | | |
| T-B | ami-001 | en | | | | |

**Adoption decision:** _(one paragraph, M2X-021 — which route the pipeline uses and why)_

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

## Open dependency

**The three hand snippets are not written yet.** `eval/snippets/{mtg-001,mtg-002,ami-001}.txt`
are still templates marked `NOT YET TRANSCRIBED`. Every WER cell and every entity cell
above is blocked on them, and they must be written **by ear** — a reference derived from
model output measures the system against itself.

Latency, transcript counts and the diarisation reference comparison on `ami-001` do not
depend on the snippets and can be filled first.
