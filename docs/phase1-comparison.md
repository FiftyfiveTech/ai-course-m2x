# Phase 1 comparison — what we adopted, and why

**Ticket:** M2X-025 (Odoo 4633) · **Gate:** PRD §5 Phase 1 · **Date:** 2026-08-12

Phase 1 asked one question five times: given two or more ways to do a step, which one does
this product use? This is the answer to each, with the measurement that decided it and the
limitation that survives it. The full working — every leg, every cell, every failed
approach — is in [`design/day2-matrix.md`](design/day2-matrix.md); this document is the
part that has to be defensible out loud.

## Adoption decisions

| # | Step | Adopted | Rejected | Decided on |
|---|---|---|---|---|
| 1 | Transcription | `openai/whisper-large-v3`, language auto-detect, via Groq | `whisper-large-v3-turbo`; forced `--language en` | WER + voiced coverage |
| 2 | Diarisation | `pyannote/speaker-diarization-3.1`, unconstrained clustering | fixing `num_speakers=4` | attribution accuracy on `ami-001` |
| 3 | Chaptering | fixed 5-minute chapters | LLM topic-shift detection | boundary placement |
| 4 | Summarisation | map-reduce over fixed chapters | single-pass | judgement questions answered |
| 5 | Vocabulary file | **not adopted** as a default; `--vocab` stays opt-in | adopting it as a pipeline default | WER + entity capture, both directions |

### 1. Transcription — why `whisper-large-v3` and not turbo

Turbo is 1.4–2.7× faster at the same $0.00 and is genuinely competitive on clean English:
on `ami-001` the two routes tie (WER 48.5% vs 48.9%, +0.4). On our own meetings it
collapses.

| meeting | language | T-A `large-v3` | T-B `turbo` | gap |
|---|---|---|---|---|
| `ami-001` | English | **48.5%** | 48.9% | +0.4 |
| `mtg-002` | Hinglish | **64.0%** | 80.8% | **+16.8** |
| `mtg-001` | Hinglish | **63.5%** | 94.6% | **+31.1** |

The failure mode is what settles it, not the size of the gap. Turbo's error is almost
entirely **deletion** — 190 of `mtg-001`'s 260 reference words and 187 of `mtg-002`'s 328.
A route that deletes does not return a worse sentence; it returns no sentence, and every
downstream phase (extraction, citation, contradiction detection) inherits the silence
without any signal that something was lost. On `mtg-002` turbo opens with `I am foreign
foreign and I'm serious`, which is what the model emits when it cannot decode the audio at
all — and then reports the language as English.

This corpus is half Hinglish. A route that is excellent on the other half and blind on this
one is not a faster version of the same instrument.

**Forced `--language en` is rejected outright** for a different reason: it *translates*
rather than transliterates. It scores better on a Latin-script reference while fabricating
the evidence every citation will later point at. It was withdrawn as a scoring route
mid-comparison, before any number was published from it.

### 2. Diarisation — unconstrained clustering, and one number that does not exist

`pyannote/speaker-diarization-3.1` on `ami-001` against AMI's own speaker ground truth:
**90.7% attribution accuracy** unconstrained, **84.1%** with `num_speakers=4`. Constraining
the count to the true number of participants made attribution *worse* — the over-clustering
it fixes is cosmetic (9 labels for 4 people), and the merge it forces is not.

**`mtg-001` and `mtg-002` have no diarisation accuracy number and cannot have one.**
Neither has speaker ground truth; AMI is the only meeting in the corpus that does. The
Deepgram references written for WER supply speaker turns, but scoring a diariser against a
diariser measures agreement, not accuracy. Treat 90.7% as an English-clean-audio figure and
**assume Hinglish is worse until something measures it** — `mtg-001` yielding 8 detected
speakers for 4 participants is the hint that it will be.

### 3–4. Chaptering and summarisation

**Fixed 5-minute chapters, not LLM topic-shift detection.** C-2's failure is positional, not
marginal: all 12 detected boundaries fall inside the first 9.2 minutes of a 29.7-minute
meeting, leaving a final "chapter" that is 69% of the audio. Uniform-by-construction beats a
detector that stops detecting.

**Map-reduce summarisation over single-pass.** S-2 answers 3.5 of 5 judgement questions
against S-1's 3/5 — specifically the late-meeting question single-pass loses entirely, which
is consistent with C-2's positional failure and with attention thinning over a long context.
The price is 7 calls against 1 and 1.23× the tokens; on a zero-cost tier that is 7.8s vs
6.0s. **This is the first decision to revisit if cost becomes real** — the margin is one
question out of five.

### 5. Vocabulary file — not adopted

The course lesson under test was "pipeline beats model tuning": a prior project moved entity
capture 76.2% → 90.5% by feeding the transcriber a vocabulary file. It does not reproduce
here, and the evidence points one way only.

| evidence | off | on | direction |
|---|---|---|---|
| WER, `ami-001` (English, human reference) | 48.5% | 57.4% | **−8.9** |
| WER, `mtg-002` (Hinglish) | 64.0% | 83.8% | **−19.8** |
| entity capture, `mtg-002` | 0% (0/2) | 0% (0/2) | no change |
| `mtg-001`, "on" leg | — | unobtainable | Groq 500s on prompted audio |

The metric the file exists to move did not move; the metric it should not have touched got
worse on both meetings, concentrated in **substitutions** (54→91 and 68→111) — the signature
of a decode pulled toward words that were never spoken. `--vocab` remains available as an
opt-in flag; no route passes it by default.

**What this does not settle.** Whether the *experiment* was a fair test of the lesson is a
separate question and stays open. Our corpus is half code-switched, our only English meeting
is third-party and contains none of our 56 terms, and the entity denominator that survived
is 2. A Devanagari term list, a transliteration-aware matcher, or a corpus whose English
half is our own would each be a defensible re-run.

## Gate evidence

**Criterion (PRD §5):** *"Speaker-attributed, timestamped transcript on ≥3 sample meetings;
differences documented."*

### 3/3 speaker-attributed and schema-valid

```
uv run python eval/validate_transcripts.py

PASS  mtg-001  104 segments · 5 speakers ·  99.0% attributed · 1050s · 5 empty-text (112s)
PASS  mtg-002   64 segments · 4 speakers · 100.0% attributed ·  397s · 2 empty-text (33s)
PASS  ami-001  582 segments · 9 speakers ·  99.0% attributed · 1785s · 1 empty-text (0s)

3/3 speaker-attributed, schema-valid
```

**Attribution is checked against a 95% floor, not against "every segment".**
`TranscriptSegment.speaker` is nullable on purpose: `dominant_speaker` returns `None` when
no diarisation turn overlaps a segment, because a visible gap beats attribution that no
audio supports. Requiring 100% would ask the pipeline to guess, and the six unattributed
segments across the corpus are exactly the boundary backchannels the design intends to leave
unclaimed. Recorded as a **deviation from the ticket's wording** ("every segment has …
speaker"), resolved in favour of the design decision rather than by weakening it.

### Adopted pipeline, end-to-end, cold

Cache moved aside so the transcription leg could not be served from it, then restored:

```
uv run m2x process data/raw/mtg-002-course-scope.wav --meeting-id mtg-002 \
    --transcripts-dir data/gate-phase1/transcripts --summaries-dir data/gate-phase1/summaries

mtg-002: 53 segments from 398s of audio (provider, 8327 ms)
  model     openai/whisper-large-v3 via groq
  language  Hindi
  summary via groq (545 ms) · meta-llama/Llama-3.1-8B-Instruct · 1612 in / 103 out · $0.0000
```

Four run-log records, both providers named, every record carrying latency, tokens and cost.
The adopted route is what a plain `m2x process` runs — no flags were needed to select it.

## Known limitations

Five, and three of them were found while writing this document.

**0. `mtg-001`'s reference is not in this repository.** Its window is an internal
discussion of client delivery work — module names, per-tenant configuration, colleague
first names — and this repository is public. The reference lives at
`eval/snippets/mtg-001.local.txt`, git-ignored on the same rule as `eval/vocab.local.txt`;
the tracked file is a stub that explains the gap and exits 2 rather than scoring nothing.
A verbatim reference is the one artifact that cannot be redacted and still work, so the
choice is ship the words or ship the numbers, and we ship the numbers. **Consequence: the
two `mtg-001` WER cells cannot be reproduced from a fresh clone.** Every other cell can.

**1. Two of the three references are not human.** `ami-001` is AMI's own manual annotation.
`mtg-001` and `mtg-002` are Deepgram `nova-3` transcripts, reviewed and accepted 2026-08-12
without a by-ear pass. A WER against those two is **Whisper-vs-Deepgram agreement, not
accuracy**: a failure mode the two vendors share is invisible to it. Deepgram is at least not
the system under test, so the one circularity avoided is measuring the pipeline against
itself. Read the deltas — T-A vs T-B, V-1 off vs on — where a shared bias cancels; do not
read "64.0%" as an accuracy. A by-ear pass is the open improvement.

**2. Absolute WER is inflated on every leg, for two unrelated reasons.** On `ami-001`, AMI's
IHM annotation is per-headset: 94% of utterances in the scored window overlap another
speaker and 48% are one- or two-word backchannels a mixed recording cannot reproduce — 150
of 458 reference words are deletions before the model makes a single mistake. On the Hinglish
meetings the references are mixed-script, so a Devanagari-vs-Latin spelling disagreement
scores as a substitution whether or not the word was heard. **Never quote 48.5% as a clean
Whisper accuracy figure.**

**3. Coverage was partly an artifact.** Whisper emits segments with a real time range and
empty text, and duration-based coverage counted them: `mtg-001` carries 112 seconds of them,
10.6% of the meeting. Voiced coverage cuts T-A's published lead by roughly two thirds — 9.1 →
2.9 points on `mtg-001`, 17.3 → 9.0 on `mtg-002`. The direction and the decision are
unchanged, but the margin the decision was originally argued on was overstated and should not
be quoted as published. The metric that "turned out to be the discriminating measurement of
the whole comparison" was, in part, measuring silence with a timestamp on it.

**4. The pipeline is not reproducible run-to-run, and the variance is large.** The cold gate
run above returned **53 segments and 629 words** where the 2026-08-05 comparison leg returned
**64 and 780** — same audio, same model, same provider, same flags, one week apart. That is
19% fewer words, a swing larger than several deltas the matrix draws conclusions from, and it
means every word-count and coverage cell carries unmeasured run-to-run noise.

The reassuring half: **WER barely moved** — 64.0% → 64.9%, +0.9 points. Quality is stable
even when verbosity is not, which is the third independent reason to prefer WER over
coverage as the discriminator, and it puts the T-A/T-B gaps (16.8 and 31.1) far outside run
noise. One re-run is not a variance estimate; whether this is sampling non-determinism or
provider drift over a week cannot be told from n=2.

## Verdict

**PASS.** 3/3 transcripts speaker-attributed and schema-valid, five adoption decisions
recorded with the measurement behind each, differences documented — including three that
weaken numbers this project has already published. Gate record: [`gates.md`](gates.md).
