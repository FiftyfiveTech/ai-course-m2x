"""Speaker diarisation: who spoke when, merged onto the transcript's time axis.

Whisper answers *what was said*. This answers *who said it*, and the two are joined by
timestamp overlap — which is the reason M2X-012 refused to discard segment times at
ingest. Every later phase depends on the join: a decision needs an owner, a citation
needs a speaker, contradiction detection needs to know whether the same person changed
their mind or two people disagreed.

Unlike every other model call in this project, this one runs **in-process** rather than
through :class:`~m2x.adapter.ModelAdapter`. The adapter's job is HTTP providers — one
request, one cache entry, one run record — and pyannote is a local torch pipeline with
none of those properties. Rather than widen the adapter to accommodate a second kind of
thing, diarisation keeps its own module and records its latency on the artefact it
writes. The consequence is honest and worth stating: **diarisation does not appear in
`data/runs/runs.jsonl`**, so the cost report covers provider calls only.

The optional ``diarize`` dependency group carries torch. It is not installed by a plain
``uv sync``, and this module is imported lazily by the CLI so the rest of the pipeline
runs on a machine that never installs it.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from m2x.errors import M2XError
from m2x.types import Transcript, TranscriptSegment

DEFAULT_DIARIZATION_DIR = Path("data/diarization")
"""Where turns and speaker-attributed transcripts land. Git-ignored like all of ``data/``."""

DEFAULT_DIARIZATION_MODEL = "pyannote/speaker-diarization-3.1"
"""Hugging Face repo id, named the same way models are named everywhere else here.

Pinned to the 3.x line deliberately: pyannote 4 redirects this id to
``pyannote/speaker-diarization-community-1``, a separately gated repo. A pipeline that
silently resolves to a *different* model than the one you named is exactly the kind of
drift the repo-id convention exists to prevent.
"""


class SpeakerTurn(BaseModel):
    """One continuous stretch of speech attributed to one speaker."""

    model_config = ConfigDict(frozen=True)

    t_start: float = Field(ge=0.0)
    t_end: float = Field(ge=0.0)
    speaker: str
    """Diarisation label (``SPEAKER_00``) or a real name once mapped."""


class DiarizationResult(BaseModel):
    """Turns for one meeting, plus what it cost to produce them."""

    model_config = ConfigDict(frozen=True)

    meeting_id: str
    model_repo_id: str
    turns: list[SpeakerTurn]
    latency_ms: int = Field(ge=0)
    speakers: int = Field(ge=0)
    """Distinct speakers found. Compared against the corpus's participant count — a
    mismatch is a finding, not something to quietly re-cluster until it agrees."""


def load_pipeline(model_repo_id: str = DEFAULT_DIARIZATION_MODEL, *, token: str | None = None) -> Any:
    """Load the pyannote pipeline, allowlisting the globals its checkpoint contains.

    torch 2.6 flipped ``torch.load`` to ``weights_only=True``, which rejects any
    pickled object that is not a plain tensor. pyannote's published checkpoints carry
    three of their own classes plus ``TorchVersion``, so loading them needs those names
    explicitly allowlisted.

    This is a real security boundary, not a formality: allowlisting says "I trust this
    checkpoint's author to execute code on load". It is acceptable here because the
    weights come from pyannote's own gated repo over an authenticated download. It
    would not be acceptable for a checkpoint from an arbitrary URL, and the allowlist
    is deliberately narrow rather than a blanket ``weights_only=False``.

    Raises:
        M2XError: The optional ``diarize`` dependency group is not installed, or the
            gated repo is not accessible with this token.
    """
    try:
        import torch
        from pyannote.audio import Pipeline
        from pyannote.audio.core.task import Problem, Resolution, Specifications
        from torch.torch_version import TorchVersion
    except ImportError as error:  # pragma: no cover - depends on optional group
        raise M2XError(
            "diarisation needs the optional dependency group: uv sync --group diarize"
        ) from error

    torch.serialization.add_safe_globals([TorchVersion, Specifications, Problem, Resolution])

    try:
        pipeline = Pipeline.from_pretrained(model_repo_id, use_auth_token=token)
    except Exception as error:  # pragma: no cover - network/auth dependent
        raise M2XError(f"could not load {model_repo_id}: {error}") from error

    if pipeline is None:
        raise M2XError(
            f"{model_repo_id} returned no pipeline — the usual cause is an HF_TOKEN "
            "without accepted terms for this gated repo"
        )
    return pipeline


def diarize(audio_path: Path, *, pipeline: Any, meeting_id: str | None = None) -> DiarizationResult:
    """Run diarisation over a whole audio file.

    Audio is read with ``soundfile`` and handed over as a waveform rather than a path.
    pyannote would happily open the file itself, but it routes that through torchaudio's
    deprecated backend shims, and reading it ourselves keeps one decoder in play for
    both this and the transcription path.

    Args:
        audio_path: Meeting audio, 16 kHz mono as the corpus is normalised.
        pipeline: A loaded pyannote pipeline.
        meeting_id: Stable id; defaults to the filename stem.

    Returns:
        Turns in start order, with the wall-clock cost of producing them.
    """
    try:
        import soundfile
        import torch
    except ImportError as error:  # pragma: no cover - depends on optional group
        raise M2XError(
            "diarisation needs the optional dependency group: uv sync --group diarize"
        ) from error

    waveform, sample_rate = soundfile.read(audio_path, dtype="float32")
    started = time.monotonic()
    annotation = pipeline(
        {"waveform": torch.from_numpy(waveform).unsqueeze(0), "sample_rate": sample_rate}
    )
    latency_ms = int((time.monotonic() - started) * 1000)

    turns = [
        SpeakerTurn(t_start=segment.start, t_end=segment.end, speaker=label)
        for segment, _, label in annotation.itertracks(yield_label=True)
    ]
    turns.sort(key=lambda turn: (turn.t_start, turn.t_end))

    return DiarizationResult(
        meeting_id=meeting_id or audio_path.stem,
        model_repo_id=DEFAULT_DIARIZATION_MODEL,
        turns=turns,
        latency_ms=latency_ms,
        speakers=len({turn.speaker for turn in turns}),
    )


def dominant_speaker(
    segment_start: float, segment_end: float, turns: list[SpeakerTurn]
) -> str | None:
    """Speaker holding the most of ``[segment_start, segment_end)``, or ``None``.

    Most overlap rather than "whoever started first": a transcript segment that begins
    during a short interjection and continues into a long answer belongs to the person
    who said most of it. Returning ``None`` when nothing overlaps is deliberate — an
    unattributed segment is a visible gap, whereas guessing the nearest speaker would
    manufacture attribution that no audio supports.
    """
    totals: dict[str, float] = {}
    for turn in turns:
        overlap = min(segment_end, turn.t_end) - max(segment_start, turn.t_start)
        if overlap > 0:
            totals[turn.speaker] = totals.get(turn.speaker, 0.0) + overlap
    if not totals:
        return None
    return max(totals.items(), key=lambda item: item[1])[0]


def assign_speakers(
    transcript: Transcript,
    turns: list[SpeakerTurn],
    *,
    names: dict[str, str] | None = None,
) -> Transcript:
    """Return a copy of ``transcript`` with ``speaker`` filled on every segment.

    Args:
        transcript: Transcript whose segments carry timestamps.
        turns: Diarisation output on the same time axis.
        names: Optional ``{diarisation label: real name}`` mapping. Labels with no
            entry keep their ``SPEAKER_xx`` form rather than being dropped — an
            unmapped speaker is still a distinct speaker, and losing that would merge
            two people into one in every downstream count.
    """
    names = names or {}
    relabelled = [
        segment.model_copy(
            update={
                "speaker": (
                    names.get(label, label)
                    if (label := dominant_speaker(segment.t_start, segment.t_end, turns))
                    else None
                )
            }
        )
        for segment in transcript.segments
    ]
    return transcript.model_copy(update={"segments": relabelled})


def coverage(segments: list[TranscriptSegment]) -> float:
    """Fraction of segments that got a speaker.

    The one number that says whether the join worked. Reported next to attribution
    accuracy because they fail differently: low coverage means diarisation missed
    speech the transcriber heard; low accuracy means it heard the wrong person.
    """
    if not segments:
        return 0.0
    return sum(1 for segment in segments if segment.speaker) / len(segments)
