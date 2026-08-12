# Gate records

A gate passes when the listed command, re-run by the supervisor on a fresh clone, prints the
listed number. Claimed ≠ verified.

| date | phase | command | git SHA | number | pass/fail |
|------|-------|---------|---------|--------|-----------|
| 2026-08-04 | Phase 0 | `make run` then `make run-local` | `85f80d4` | hosted 395 ms vs local 141,339 ms — **358×** | **PASS** |
| 2026-08-12 | Phase 1 | `uv run python eval/validate_transcripts.py` | `e392922` | **3/3** speaker-attributed and schema-valid | **PASS** |

## Phase 1 — 2026-08-12, SHA `e392922`

**Criterion (PRD §5):** "Speaker-attributed, timestamped transcript on ≥3 sample meetings;
differences documented."

The comparison doc, the five adoption decisions and the four known limitations are in
[`phase1-comparison.md`](phase1-comparison.md). This record is the evidence.

```
uv run python eval/validate_transcripts.py

PASS  mtg-001  104 segments · 5 speakers ·  99.0% attributed · 1050s · 5 empty-text (112s)
PASS  mtg-002   64 segments · 4 speakers · 100.0% attributed ·  397s · 2 empty-text (33s)
PASS  ami-001  582 segments · 9 speakers ·  99.0% attributed · 1785s · 1 empty-text (0s)

3/3 speaker-attributed, schema-valid
```

Exit 1 on any failure, so the gate cannot be recorded green by reading past the output.

**Adopted pipeline, run end-to-end from a cold cache** (`data/cache` moved aside for the
run, then restored):

| step | model (HF repo id) | provider | latency | tokens | cost |
|---|---|---|---|---|---|
| transcribe | `openai/whisper-large-v3` | groq | 8,327 ms | — | $0.0000 |
| summarise | `meta-llama/Llama-3.1-8B-Instruct` | groq | 545 ms | 1612 / 103 | $0.0000 |

No flags were needed to select the adopted route — a plain `m2x process` runs it.

**SHA note.** `e392922` is the SHA the evidence was produced against; no `src/` file
changed in the commit that carries this record, so the pipeline behaviour is that SHA's.
`eval/validate_transcripts.py` is new in this commit and is the only part of the evidence
that a fresh clone of `e392922` will not have.

### Deviation from the ticket's wording, resolved in the design's favour

M2X-025 step 2 says "every segment has text, t_start, t_end, speaker". Six segments across
the corpus (1 in `mtg-001`, 6 in `ami-001`, 0 in `mtg-002`) have no speaker, so the literal
reading fails. `TranscriptSegment.speaker` is nullable **on purpose**: `dominant_speaker`
returns `None` when no diarisation turn overlaps a segment, on the documented grounds that a
visible gap beats attribution no audio supports. The validator therefore checks attribution
against a **95% floor** — measured values are 99.0–100% — rather than demanding 100%, which
would ask the pipeline to guess. Recorded here rather than settled quietly, because the
alternative reading of the criterion is defensible and this is the supervisor's to overturn.

### Three published numbers this gate weakens

Found while assembling the evidence, all detailed in `phase1-comparison.md`:

1. **Coverage was partly an artifact** — Whisper emits empty-text segments with real time
   ranges (112s in `mtg-001`, 10.6% of the meeting); voiced coverage cuts T-A's published
   lead by roughly two thirds. Decision unchanged, margin overstated.
2. **The pipeline is not reproducible run-to-run** — the cold run above returned 53 segments
   / 629 words against the comparison leg's 64 / 780, same audio and flags, one week apart.
   WER moved only 64.0% → 64.9%, so quality is stable where verbosity is not.
3. **Two of three references are Deepgram, not human** — WER on the internal meetings is
   inter-vendor agreement, not accuracy.

## Phase 0 — 2026-08-04, SHA `85f80d4`

**Criterion (PRD §5):** "One command processes a meeting clip both ways; cost/latency/model logged."

Run on a **fresh clone** of `main`, not on the development checkout:

```
git clone https://github.com/FiftyfiveTech/ai-course-m2x.git
cp .env.example .env          # keys pasted in
mkdir -p data/clips && cp <clip> data/clips/
uv sync
make run                      # hosted
make run-local                # local
```

| leg | step | model (HF repo id) | provider | latency | tokens | cost |
|---|---|---|---|---|---|---|
| hosted | transcribe | `openai/whisper-large-v3` | groq | 4,935 ms | — | $0.0000 |
| hosted | summarise | `meta-llama/Llama-3.1-8B-Instruct` | groq | 395 ms | 1413 / 88 | $0.0000 |
| local | transcribe | `openai/whisper-large-v3` | groq | 0 ms (cache hit) | — | $0.0000 |
| local | summarise | `meta-llama/Llama-3.1-8B-Instruct` | ollama | 141,339 ms | 1393 / 72 | $0.0000 |

`data/runs/runs.jsonl` — 4 records, one per call, cache hit included. Both providers
present; every record carries `latency_ms`, tokens, `cost_usd` and `model_repo_id`.

**The number:** the same model repo id, served two ways, differs by **358×** on the
summary step. Nothing but configuration changed between the legs. Cache behaviour is
visible in the same log — the local run's transcription cost 0 ms because the hosted
run had already produced it.

**Deviation from the ticket's "zero undocumented steps".** A fresh clone cannot run
`make run` unaided, because `data/` is git-ignored and the clip is therefore absent.
Copying the clip in is a real step and is recorded above rather than glossed. That is a
property of the data boundary, not a packaging defect: the alternative is committing
meeting audio, which the PRD forbids. `data/corpus.json` (M2X-015) makes the expectation
machine-readable so a clone can at least name what it is missing.
