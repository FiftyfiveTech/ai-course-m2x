# Pilot corpus (M2X-000)

The meeting set the whole week runs on, assembled 2026-08-04 in two halves: internal
FiftyFive Teams recordings held locally, and an English control set pulled from Hugging
Face. No audio is committed — `data/` is git-ignored and nothing was copied to any
cloud drive.

## Data boundary

PRD §data: **internal FiftyFive meetings only, no client calls, consent notice
given.** A recording is admitted only if all three hold. Exclusions are listed below
with the reason, so the boundary is auditable rather than implied by absence.

## Admitted meetings

| id | file (`data/raw/`) | date | participants | duration | consent | screen-share |
|---|---|---|---|---|---|---|
| `mtg-001` | `mtg-001-fe-uiux.wav` | 2026-07-27 | 4 | 17m34s | yes | **yes** |
| `mtg-002` | `mtg-002-course-scope.wav` | 2026-07-28 | 3 | 6m38s | yes | yes |

- **`mtg-001`** — internal product scoping, frontend × UI/UX × backend. Four FiftyFive
  engineers, no client present. Screen-share is a live CMS/design-tool walkthrough —
  this is the meeting Phase 5 (M2X-070, key-frame extraction + OCR) runs on.
- **`mtg-002`** — AI course scope alignment: the two course participants and their
  supervisor. Screen-share is a slide deck.

Participants are recorded as counts and roles rather than names. This repository is
public, and a corpus record does not need to identify who spoke to establish that the
data boundary held.

**Consent basis:** both are Microsoft Teams recordings made inside the FiftyFive tenant,
where Teams shows every participant the in-call recording notification before capture
starts and names the recording in the chat afterwards. The corpus owner attended both.

## Fast-iteration clip

| file | source | duration |
|---|---|---|
| `data/clips/clip-mtg-002-5min.wav` | `mtg-002` | 5m00s |

**Deviation from the ticket.** M2X-000 asks for a ~10-minute clip cut from the smallest
meeting. The smallest admitted meeting is 6m38s, so a 10-minute cut would return the
whole file and the clip would be a byte-duplicate of the meeting it came from — no
faster to iterate on, twice the disk. Cut 5 minutes instead: still the intended
cheap-iteration artefact, and genuinely smaller than every corpus member.

## Excluded recordings

| file | reason |
|---|---|
| client call, 2026-07-31 (26m49s) | **Client call** — a client representative present, German-language helpdesk ticket review with client screen-share. Barred by the data boundary. |
| `ScreenRecording_05-04-2026 08-16-19_1.MP4` (1.5 MB) | Not a meeting — a desktop screen capture with no call audio. |

## Shortfall on internal recordings — and how it was closed

Every meeting recording on this machine was reviewed (`~/Downloads` and `~/meetings`;
the latter holds no media). Only three were meetings, and one of those is a client call
the boundary excludes, leaving the internal corpus one to two meetings short of the
ticket minimum — and entirely Hinglish.

Closed by adding an English set from Hugging Face — see below.

## English set — AMI corpus from Hugging Face

The two internal meetings are both spoken **Hinglish**, and Whisper returns Devanagari
with English technical terms transliterated phonetically ("जीपीटी" for GPT). Two
meetings in one language register, 24 minutes of speech: too thin for M2X-033's 25
hand-labelled cases, and no English control to separate "the model is wrong" from "the
audio is code-switched".

The obvious English recordings on hand are client calls, which the boundary
excludes. So the English set comes from Hugging Face instead — the same sourcing rule
the project already applies to models.

| id | duration | speakers | reference segments |
|---|---|---|---|
| `ami-001` | 29.8 min | 4 | 490 |
| `ami-002` | 25.1 min | 4 | 242 |
| `ami-003` | 38.9 min | 4 | 497 |

- **Source:** `diarizers-community/ami`, config `ihm`, split `test` — whole meetings,
  not utterance fragments. Ungated, **CC BY 4.0**.
- **Fetch:** `uv run --group corpus python scripts/fetch_ami.py --count 3`. Writes
  16 kHz mono WAV to `data/raw/` and reference speaker segmentation to
  `eval/ami/<id>.speakers.json`, plus `eval/ami/manifest.json`.
- **Bonus that was not the reason for choosing it:** AMI ships reference speaker turns,
  so M2X-022 gets a diarisation ground truth for free instead of hand-labelling one.
- Verified end to end: a 5-minute clip of `ami-001` transcribes to 93 segments,
  language detected **English**, clean output.

`scripts/fetch_ami.py` deliberately reads the encoded audio bytes rather than letting
`datasets` decode them — `datasets` 4.x routes audio decoding through `torchcodec`, and
installing a deep-learning runtime to read a WAV is not a dependency this project should
carry. `datasets` + `soundfile` sit in an optional `corpus` dependency group, so a
normal `uv sync` stays lean and the fetch stays a one-off.

## English set, second pass — Tiron evaluation meetings

AMI closed the language gap but not the **reference-words** gap. `diarizers-community/ami`
ships reference speaker turns and no reference text, so every WER number still needed a
snippet written by ear — the manual step M2X-024/025 are blocked on, and the reason
Hinglish accuracy is unmeasurable at all.

`Trelis/tiron-eval-meetings` ships speaker turns **and** the words, from the same human
annotation, on one timeline. That is the reason to adopt it; the extra corpora are a
bonus.

| split | meetings | source |
|---|---|---|
| `ami` | ES2004a, IS1009a, TS3003a, EN2002a | AMI, single distant microphone (Array1-01) |
| `icsi` | Bmr013, Bmr018, Bro021 | ICSI, mean of 4 distant PZM room microphones |
| `notsofar` | 10 `MTG_*` sessions | NOTSOFAR-1 eval set, one distant device per meeting |

- **Source:** `Trelis/tiron-eval-meetings` — 17 whole meetings, 612 MB, ungated,
  **CC BY 4.0** (AMI, ICSI and NOTSOFAR-1 each CC BY 4.0 upstream).
- **Fetch:** `uv run --group corpus python scripts/fetch_tiron.py --split ami --count 2`.
  Writes 16 kHz mono WAV to `data/raw/tiron-<meeting>.wav`, reference turns to
  `eval/tiron/tiron-<meeting>.speakers.json`, reference words to
  `eval/tiron/tiron-<meeting>.txt`, and `eval/tiron/manifest-<split>.json`.
- **Drops into the existing evaluators unchanged.** The turns file uses the same
  `segments` / `t_start` / `t_end` / `speaker` shape `eval/diarization_score.py` already
  reads; the words file is the plain text `eval/wer.py --reference` already reads. No
  evaluator code changed to adopt this.
- **Far-field, not per-headset.** The material difference from the AMI IHM set: IHM is
  one headset per speaker, where 94% of utterances in the measured window overlap another
  speaker and absolute WER is inflated. Tiron's AMI split is the distant microphone —
  harder audio, but a single realistic channel, which is what the product actually ingests.
- **Meetings keep their real corpus id** (`tiron-ES2004a`, not `tiron-001`) so an overlap
  with an already-fetched meeting shows up in the filename. `ami-001` is EN2002**b**
  (established in M2X-021) and the tiron `ami` split carries EN2002**a** — different
  meetings. The source ids behind `ami-002` / `ami-003` were never identified, so an
  overlap there is **not ruled out**: check before pooling those two with the tiron AMI
  split in one score.
- Verified end to end 2026-08-12 on `tiron-ES2004a`: 1049s, 4 speakers, 260 reference
  utterances, 2614 reference words. Fed back through both evaluators as their own
  reference — `wer.py` returns 0.0 and `diarization_score.py` returns 1.0 accuracy on a
  4/4 speaker mapping, the correct identity behaviour, which proves the plumbing.

**Scope note.** The internal meetings are Hinglish; the PRD scope is English. Tiron is now
the **graded** English corpus. The Hinglish meetings stay in as a robustness slice — real
register, real domain vocabulary, real screen-share — but a gate number is quoted against
tiron, not against them.

## Corpus status

**5 meetings, ~118 minutes** held locally — 2 internal Hinglish (real register,
screen-share, our own domain vocabulary) plus 3 English AMI (clean control, speaker
ground truth) — plus the tiron set on demand: 17 more English meetings, fetched per
split rather than kept locally, each carrying **both** references.

- **M2X-033** — was the pressure point at 24 minutes; now has ~118 minutes to draw 25
  distinct labelled cases from without over-sampling the same passages.
- **M2X-021 / M2X-022** — comparisons run across both registers, which is strictly more
  informative than three meetings of one.
- **M2X-024 / M2X-025** — the by-ear snippet is no longer the only route to a WER
  reference. Any tiron meeting scores without a manual transcription pass.
- **Phase 5 / M2X-070** — unaffected; `mtg-001` carries the screen-share.
- Still worth doing: one or two more **internal** FiftyFive meetings, so the real-world
  half grows too. AMI is a control, not a substitute for our own domain.

## Verification performed

- Both files probed: 16 kHz mono PCM WAV, durations as tabled.
- Audio usability checked instrumentally rather than by ear — a 60-second sample from
  each file measures mean volume −18.4 dB (`mtg-001`) and −19.3 dB (`mtg-002`), with
  peaks near full scale. That is active speech; a dead or near-silent track would sit
  far lower.
- Screen-share confirmed by extracting frames at 25% / 55% / 85% of each recording and
  inspecting them.
- Zero client-confidential content: the only client-facing recording available was
  excluded outright.
- AMI meetings verified from the fetch manifest (durations, speaker counts, reference
  segment counts) and end to end through `m2x process` on a 5-minute clip.
