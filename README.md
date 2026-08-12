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

The prompt itself comes from `prompts/extraction/v<N>.md`, not from the code. The version
used is stamped into the record and onto every run-log line, so a reported score can name
the exact text that earned it; `--prompt-version v1` pins an older one, and shipping a
new version file switches the default with no code change. Versions are append-only —
editing one an eval has cited fails the suite. Reasoning:
`docs/design/day3-prompts.md`; what each version changed and scored:
`prompts/CHANGELOG.md`.

## Building and querying the index

```bash
uv run m2x index build                              # transcripts + tracked project docs
uv run m2x index build --no-docs                    # meetings only
uv run m2x index query "what did we decide about migration" -k 5
uv run m2x index query "scope" --source-type doc    # documents only
```

Chunks transcripts into whole segments packed to ~1200 characters with one segment of
overlap, chunks markdown docs on headings, embeds each chunk through `ModelAdapter`
(`nomic-ai/nomic-embed-text-v1.5`, served locally by Ollama — zero spend) and upserts
into Chroma at `data/index/`. Every chunk carries what a citation needs: source, segment
range, `t_start`/`t_end`, speakers.

**Rebuilds are idempotent.** Chunk ids are hashes of the source and segment range, so
building twice overwrites in place rather than duplicating; a source that shrank has its
orphaned chunks deleted. The collection records which embedding model built it and
refuses to open with another — vectors from two models are comparable and unrelated, so
mixing them returns confident nonsense rather than an error.

`index query` prints distances, not confidence: the nearest chunk to a question nobody
discussed is still a chunk. Design and measured results: `docs/design/day4-index.md`.

## Asking a question

```bash
uv run m2x ask "what are the three RAG gate metrics"
uv run m2x ask "what did we decide about migration" -k 8 --source-type meeting
uv run m2x ask "who owns the audit" --max-distance 0.55   # widen the abstention gate
uv run m2x ask "..." --prompt-version v1                  # pin the prompt
```

Retrieves the top-k chunks, hands them to the model inside a delimited data block —
retrieved content is untrusted data, exactly like a transcript — and returns an answer
whose every claim cites the passage it came from:

```
Context precision, Faithfulness, and Citation accuracy

  [m2x-week1-handbook · § 4.4 RAGAS and the three gate metrics]  distance 0.2979
     "Citation accuracy (≥0.90): does the cited segment actually contain the claim?"
  prompt    rag/v2
```

**Fabricated citations are structurally impossible.** The model cites passage labels
(`C1`), never timestamps, so the `[meeting · speaker · mm:ss–mm:ss]` reference is rendered
from the chunk's stored metadata rather than typed by the model. Each citation also carries
a quote that must appear verbatim in the passage it cites, which catches a real passage
cited for a claim it does not support. Both checks run inside the retry loop; one retry,
then the answer abstains.

**Abstention is a feature, and exits 0.** When nothing is retrieved, nothing is retrieved
nearer than `--max-distance`, or the model cannot ground an answer, the command prints
`Not found in the meeting corpus` and the reason. Never a guess. The default threshold
(0.48) is **provisional** and measured on this corpus with this embedding model — see
`docs/design/day4-ask.md`.

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
