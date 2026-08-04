# Phase 0 — the local inference path (M2X-013)

**Date:** 2026-08-04 · **Ticket:** M2X-013 · **Branch:** `feature/m2x-013-local-inference-path`

## What was decided

M2X-013 offers a choice: run *transcription* locally via faster-whisper, or keep
transcription hosted and run a *language-model* step locally via Ollama. **Second
option taken.**

Reason: the point of the ticket is to measure hosted-versus-local on the same work.
Local Whisper would compare two different STT implementations, whereas moving the
summary step compares one model repo id — `meta-llama/Llama-3.1-8B-Instruct` — served
two ways. That is a comparison; the other is two measurements.

Consequence: the pipeline gained a second step. `m2x process` now transcribes, then
asks a chat model for a three-bullet summary. The summary is disposable — Phase 1B
replaces it with schema-driven extraction — but it puts an LLM call in the run log, so
Phase 0's numbers cover more than speech-to-text.

## Deviation: `--provider` steers the summary, not the whole pipeline

The ticket's acceptance criterion reads "`--provider ollama` completes the same
pipeline with zero code changes." Taken literally — one flag forcing *every* call onto
Ollama — it cannot pass: `config/models.toml` routes `openai/whisper-large-v3` to Groq
alone, and the project rule (rightly) makes a forced provider that cannot serve a model
raise rather than silently fall back. `--provider ollama` would die at transcription
and the local leg would never run.

So the flags split:

- `--provider` — backend for the summary step. **This is the hosted-vs-local switch.**
- `--transcribe-provider` — backend for transcription; defaults to the registry route.

Nothing falls back silently: each step still raises if the provider it was given cannot
serve its model. The flag simply stopped claiming authority over a step it was never
about. `--provider ollama` now does what the criterion intends — the same command, the
same code path, a local model doing the language work.

## Measured — 2026-08-04, `clip-mtg-002-5min.wav` (5m00s)

| leg | step | model | latency | tokens (in/out) | cost |
|---|---|---|---|---|---|
| hosted | transcribe | `openai/whisper-large-v3` @ groq | 6,729 ms | — | $0.00 |
| hosted | summarise | `meta-llama/Llama-3.1-8B-Instruct` @ groq | **721 ms** | 1433 / 99 | $0.00 |
| local | summarise | `meta-llama/Llama-3.1-8B-Instruct` @ ollama | **189,200 ms** | 1413 / 86 | $0.00 |

Second run of either command: cache hit, 0 ms, no request.

**Local is 262× slower than hosted for the same model on the same input.** The box is
an 11th-gen i5-1135G7 (8 threads) with Intel Iris Xe graphics, which llama.cpp does not
offload to — so a Q4 8B runs entirely on CPU at roughly 0.45 output tokens/second.
Memory was never the limit (4.9 GB model, ~12 GiB free).

Both summaries are substantively right, and neither cost anything. That is the whole
lesson in one table: **local inference is a fallback that keeps the pipeline runnable
without a key or a network, not an instrument you iterate on.** Anything interactive
belongs on the hosted leg or on a much smaller local model.

## Notes for Day 2

- Whisper auto-detects the corpus clip as **Hindi** and returns Devanagari, because the
  meeting is spoken Hinglish. Technical terms come back phonetically transliterated
  ("जीपीटी" for GPT). Downstream extraction will suffer from this long before the model
  choice matters — the transcription comparison (M2X-021) and the vocabulary experiment
  (M2X-024) should treat forcing `--language en` as one of the strategies under test.
- The summary prompt caps its input at `SUMMARY_INPUT_CHAR_LIMIT` (6000 chars). Not a
  token budget — a portability floor, so a quantised local model's smaller default
  context cannot make the two legs incomparable for the wrong reason.
