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
uv run m2x process data/clips/clip-mtg-002-5min.wav                  # transcribe + summarise
uv run m2x process data/clips/clip-mtg-002-5min.wav --provider ollama # same run, local summary
uv run m2x process <audio> --meeting-id mtg-001 --language en --no-summary
```

Two steps, both logged: hosted Whisper transcription, then a three-bullet summary from
a chat model. Writes `data/transcripts/<meeting-id>.json` (a `Transcript` with
timestamped segments) and `data/summaries/<meeting-id>.<provider>.md`, and appends one
record per call to `data/runs/runs.jsonl`. Re-running the same input is a content-hash
cache hit: no request, no cost, sub-second. The meeting id defaults to the audio
filename stem. Corpus provenance and consent: `docs/corpus.md`.

**Hosted vs local.** `--provider` selects the backend for the *summary* step — that is
the switch the Phase 0 comparison flips. Transcription has its own `--transcribe-provider`
because Whisper is served by Groq alone in `config/models.toml`; one flag driving both
would make `--provider ollama` fail at transcription and the local leg could never run.
Rationale and measured numbers: `docs/design/phase0-local-path.md`.

```bash
make run        # hosted leg  (Groq summary)
make run-local  # local leg   (Ollama summary, same clip, same model repo id)
```

## Extracting a record

```bash
uv run m2x extract mtg-001                              # transcript -> validated JSON
uv run m2x extract mtg-001 --transcript path/to/it.json # explicit source
uv run m2x extract mtg-001 --provider ollama            # same schema, local model
```

Reads the diarised transcript if one exists (`data/diarization/<id>.json`), else the
plain one, and writes `data/records/<meeting-id>.json`: decisions, actions, risks and
open questions, each citing the transcript segment it came from. Instructor drives the
loop — schema into the prompt, reply parsed and validated, and on a validation failure a
retry with the error fed back (two retries). It runs *over* `ModelAdapter`, so every
attempt including retries lands in `data/runs/runs.jsonl` with its cost.

A citation to a segment that does not exist, or a deadline that is not `YYYY-MM-DD`, is
an error the model is asked to fix — not an item that quietly enters the eval. If no
attempt validates, the command exits 1 rather than writing an empty record. Schema,
design decisions and deviations: `docs/design/day3-schema.md`.

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
