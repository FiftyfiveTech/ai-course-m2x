# M2X — meeting to execution

Week-1 track of the FiftyFive AI engineering course. Turns a recorded meeting into a
structured, cited, human-approved execution record. Hand-built by Saurabh (Builder) and
Yash (Evaluator), phase-gated — see `docs/gates.md`.

## Quickstart

```bash
git clone https://github.com/FiftyfiveTech/ai-course-m2x
cd ai-course-m2x
uv sync
cp .env.example .env   # fill with your own keys — see .env.example for names
make test              # full suite, no network required
make run               # Phase 0: transcribe the corpus clip end to end
```

## Processing a meeting

```bash
uv run m2x process data/clips/clip-mtg-002-5min.wav          # default route (Groq)
uv run m2x process data/raw/mtg-001-fe-uiux.wav --provider groq
uv run m2x process <audio> --meeting-id mtg-001 --language en
```

Writes `data/transcripts/<meeting-id>.json` — a `Transcript` with timestamped segments
— and appends one record per call to `data/runs/runs.jsonl`. Re-running the same file
is a content-hash cache hit: no request, no cost, sub-second. The meeting id defaults
to the audio filename stem. Corpus provenance and consent: `docs/corpus.md`.

## Architecture (target — PRD §4)

```
audio/video  ->  transcribe + diarize  ->  structured extraction (validated JSON)
                                                     |
project docs + past meetings  ->  multimodal index  <-+
                                                     |
                                        RAG w/ timestamp citations
                                                     |
                                        planner  ->  proposed actions
                                                     |
                                     human approval  ->  meeting record
```

**Stack:** Python · `uv` · FastAPI (thin API) · Pydantic + Instructor (schemas) ·
Chroma (vectors) · RAGAS (eval) · LangGraph (stateful workflow) · Langfuse (tracing) ·
Docker (capstone only). **All models and datasets come from Hugging Face by repo id** —
served on Groq / NVIDIA NIM free tiers or locally via Ollama (`hf.co/<repo>`). Zero spend.
Gemini and `groq/compound*` are banned (no HF repo id).

## Layout

```
src/m2x/            application code
prompts/            versioned prompt library (prompts are code)
eval/dev/           Builder tunes here
eval/heldout/       Evaluator-only until the gate — git-ignored, sealed by Wednesday
data/               raw meetings, transcripts, sandbox — git-ignored, never committed
docs/gates.md       gate records: date, command, git SHA, number, PASS/FAIL
tests/              pytest; gate checks land here as they are built
```

## Rules that survive from the reset

- A gate number counts only when the supervisor re-runs the command and sees the same output.
- The Builder never touches `eval/heldout/`.
- Adversarial transcript content is data, never instructions.
