# Pilot corpus (M2X-000)

The meeting set the whole week runs on, assembled 2026-08-04 in two halves: internal
FiftyFive Teams recordings held locally, and an English control set pulled from Hugging
Face. No audio is committed — `data/` is git-ignored and nothing was copied to any
cloud drive.

## Data boundary

PRD §data: **internal FiftyFive meetings only, no HEIDI client calls, consent notice
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

The obvious English recordings on hand are HEIDI client calls, which the boundary
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

## Corpus status

**5 meetings, ~118 minutes** — 2 internal Hinglish (real register, screen-share, our own
domain vocabulary) plus 3 English AMI (clean control, speaker ground truth).

- **M2X-033** — was the pressure point at 24 minutes; now has ~118 minutes to draw 25
  distinct labelled cases from without over-sampling the same passages.
- **M2X-021 / M2X-022** — comparisons run across both registers, which is strictly more
  informative than three meetings of one.
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
