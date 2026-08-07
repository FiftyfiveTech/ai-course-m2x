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

## Chaptering and summarising

```bash
uv run m2x chapter ami-001 --strategy fixed                 # 5-min windows, free
uv run m2x chapter ami-001 --strategy llm --provider nim    # LLM topic-shift, one call
uv run m2x summarise ami-001 --strategy single-pass --provider nim
uv run m2x summarise ami-001 --strategy map-reduce --provider nim \
    --chapters data/chapters/ami-001.fixed.json
```

Chapters land in `data/chapters/<id>.<strategy>.json`, summaries in
`data/comparison/strategies/<id>.<strategy>.md` — strategy in the filename so running
both leaves both results on disk.

**Adopted: fixed chaptering + map-reduce summarisation.** LLM topic-shift detection put
all 12 of its boundaries in the first 9 minutes of a 30-minute meeting, leaving 69% of it
as one chapter. Map-reduce answered a late-meeting question single-pass missed entirely,
for 7 calls against 1. Note the `--provider nim`: single-pass on a half-hour meeting is
~5.5k tokens and **Groq's free tier refuses it with HTTP 413** (6 000 TPM), so the cheap
strategy is the one that does not fit. Numbers, iterations and recommendation:
`docs/design/day2-matrix.md`; judgement sheet: `eval/judgement/`.

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
