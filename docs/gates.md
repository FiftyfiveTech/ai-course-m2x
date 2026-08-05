# Gate records

A gate passes when the listed command, re-run by the supervisor on a fresh clone, prints the
listed number. Claimed ≠ verified.

| date | phase | command | git SHA | number | pass/fail |
|------|-------|---------|---------|--------|-----------|
| 2026-08-04 | Phase 0 | `make run` then `make run-local` | `85f80d4` | hosted 395 ms vs local 141,339 ms — **358×** | **PASS** |

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
