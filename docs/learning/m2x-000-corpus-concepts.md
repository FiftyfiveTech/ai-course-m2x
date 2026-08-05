# Concepts Behind the Pilot Corpus — Primer (M2X-000)

The concepts the corpus ticket exercises. Each section: what it is, why it matters here,
the pitfall. Source of record for the decisions themselves: [docs/corpus.md](../corpus.md).

## 1. A data boundary is a written rule, not an absence

The PRD admits *internal FiftyFive meetings only, no client calls, consent given*. The
engineering move is that exclusions are **listed with their reason**, not silently left
out. A boundary you can audit ("the German-language helpdesk call with a client representative present was
excluded because a client was on it") is a control; a boundary implied by which files
happen to be in a folder is a coincidence that nobody can verify later.

Pitfall: treating "I didn't include it" as documentation. Nothing downstream can tell
the difference between excluded-on-purpose and forgotten.

## 2. Consent basis has to name a mechanism

"Consent given" is worthless as an assertion. What makes it real here: both recordings
are Teams captures inside the FiftyFive tenant, where Teams shows every participant the
recording notification before capture and names the recording in chat afterwards, and
Saurabh attended both. That is a *mechanism* a third party can check.

## 3. Register, and why one language register is a broken corpus

The two internal meetings are spoken **Hinglish**. Whisper auto-detects Hindi and
returns Devanagari with English technical terms transliterated phonetically ("जीपीटी"
for GPT). With only that register in the corpus, every later failure is unattributable:
you cannot tell "the model is weak" from "the audio is code-switched".

Fix: an English **control set** — 3 AMI meetings from Hugging Face
(`diarizers-community/ami`, config `ihm`, split `test`, CC BY 4.0), whole meetings, not
utterance fragments. Corpus went from 24 minutes / 1 register to ~118 minutes / 2.

The generalisable idea: a corpus is a *designed instrument*. You choose its axes of
variation deliberately, because the axes you left out are exactly the confounds you
won't be able to separate at the gate.

Pitfall: treating a control set as a substitute for real domain data. AMI is clean
English with speaker ground truth — it is not FiftyFive's vocabulary, accents, or
screen-shares. Still worth adding more internal meetings.

## 4. Ground truth that arrives for free

AMI ships **reference speaker turns** (490 / 242 / 497 segments for the three meetings),
so M2X-022's diarisation gets a ground truth without hand-labelling one. Noted honestly
in the design record as a *bonus, not the reason for choosing it* — because "we picked it
for the free labels" and "we picked it for the English control and got labels" are
different claims, and only one of them is true.

## 5. Verify instrumentally, not by vibes

Every claim about the audio was checked with a tool, not an ear:

- `ffprobe`-level checks for 16 kHz mono PCM WAV and duration.
- Usability measured as mean volume over a 60-second sample: −18.4 dB (`mtg-001`) and
  −19.3 dB (`mtg-002`) with peaks near full scale. A dead track sits far lower. This
  replaces "sounds fine to me" with a number someone else can reproduce.
- Screen-share confirmed by extracting frames at 25% / 55% / 85% and looking at them.
- The AMI leg verified end to end: a 5-minute clip of `ami-001` → 93 segments, language
  detected English.

Pitfall: "I listened to it, it's fine." Not reproducible, not reviewable, and it doesn't
survive the fresh-clone gate.

## 6. Dependency hygiene at the corpus edge

`scripts/fetch_ami.py` reads the **encoded audio bytes** rather than letting `datasets`
decode them, because `datasets` 4.x routes audio decoding through `torchcodec` — a
deep-learning runtime installed to read a WAV. `datasets` + `soundfile` live in an
optional `corpus` dependency group, so a normal `uv sync` stays lean and the fetch is a
one-off.

The rule: a one-time data-acquisition step must not tax every future `uv sync`. Optional
dependency groups exist for exactly this.

## 7. Deviating from a spec, in the open

The ticket asked for a ~10-minute clip cut from the smallest meeting. The smallest
admitted meeting is 6m38s, so a 10-minute cut returns the whole file — a byte-duplicate
that is no faster to iterate on and twice the disk. Cut **5 minutes** instead, and wrote
down why.

Same pattern as M2X-011's cache-key deviation: the spec was self-defeating on this
input, so the *intent* (a cheap-iteration artefact) was honoured and the deviation was
recorded where a reviewer will trip over it.
