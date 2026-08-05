#!/usr/bin/env python3
"""Fetch English meetings from the AMI corpus on Hugging Face.

Why this exists: the internal pilot corpus is two meetings, both spoken Hinglish, and
the ones that would balance it are client calls the PRD data boundary excludes. AMI is
real multi-party English meeting audio, ungated on the Hub, and it ships **reference
speaker segmentation** — so it closes the corpus gap and hands Phase 1 a diarisation
ground truth we would otherwise have to hand-label.

Source: ``diarizers-community/ami`` (config ``ihm``), rows are whole meetings, not
utterances. Named by Hugging Face repo id like every other model and dataset here.

Usage::

    uv run --group corpus python scripts/fetch_ami.py --count 3

Writes ``data/raw/ami-00N.wav`` (16 kHz mono) and
``eval/ami/ami-00N.speakers.json`` (reference segments). Both live under git-ignored
or evaluator-owned paths; nothing about this script commits audio.
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
from typing import Any, Iterator

DATASET_REPO_ID = "diarizers-community/ami"
CONFIG = "ihm"
TARGET_SAMPLE_RATE = 16_000


def iter_meetings(split: str, count: int) -> Iterator[dict[str, Any]]:
    """Stream meetings from the dataset.

    Streaming rather than a full download: the split is about a gigabyte and we want
    three meetings, so pulling the whole thing to discard most of it wastes bandwidth
    on a laptop that has to do this again on every clone.

    Args:
        split: Dataset split, e.g. ``"test"``.
        count: How many meetings to take.

    Yields:
        Raw dataset rows.
    """
    from datasets import Audio, load_dataset

    dataset = load_dataset(DATASET_REPO_ID, CONFIG, split=split, streaming=True)
    # Hand back the encoded bytes instead of a decoded array. `datasets` 4.x routes
    # decoding through torchcodec, which drags in the whole of torch for a job
    # soundfile already does — and this project has no business installing a deep
    # learning runtime to read a WAV.
    dataset = dataset.cast_column("audio", Audio(decode=False))
    for index, row in enumerate(dataset):
        if index >= count:
            return
        yield row


def write_meeting(row: dict[str, Any], meeting_id: str, audio_dir: Path, refs_dir: Path) -> dict[str, Any]:
    """Write one meeting's audio and reference segmentation.

    Args:
        row: Dataset row, carrying decoded audio plus parallel speaker/timestamp lists.
        meeting_id: Id to file it under.
        audio_dir: Destination for the WAV.
        refs_dir: Destination for the reference JSON.

    Returns:
        A manifest entry describing what was written.

    Raises:
        OSError: A file could not be written.
    """
    import soundfile

    audio_dir.mkdir(parents=True, exist_ok=True)
    refs_dir.mkdir(parents=True, exist_ok=True)

    samples, sample_rate = soundfile.read(
        io.BytesIO(row["audio"]["bytes"]), dtype="int16", always_2d=False
    )
    if samples.ndim > 1:
        # Belt and braces: the pipeline and every downstream comparison assume mono.
        samples = samples[:, 0]
    audio_path = audio_dir / f"{meeting_id}.wav"
    soundfile.write(audio_path, samples, sample_rate, subtype="PCM_16")

    segments = [
        {"t_start": float(start), "t_end": float(end), "speaker": speaker}
        for start, end, speaker in zip(
            row["timestamps_start"], row["timestamps_end"], row["speakers"]
        )
    ]
    reference_path = refs_dir / f"{meeting_id}.speakers.json"
    reference_path.write_text(
        json.dumps({"meeting_id": meeting_id, "segments": segments}, indent=2),
        encoding="utf-8",
    )

    duration_s = len(samples) / sample_rate
    return {
        "meeting_id": meeting_id,
        "file": str(audio_path),
        "reference": str(reference_path),
        "duration_s": round(duration_s, 1),
        "speakers": len({segment["speaker"] for segment in segments}),
        "segments": len(segments),
        "sampling_rate": sample_rate,
    }


def main() -> int:
    """Fetch meetings and print a manifest.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=3, help="how many meetings to fetch")
    parser.add_argument("--split", default="test", help="dataset split (default: test)")
    parser.add_argument("--audio-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--refs-dir", type=Path, default=Path("eval/ami"))
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("eval/ami/manifest.json"),
        help="where the fetch manifest is written",
    )
    args = parser.parse_args()

    entries = []
    for index, row in enumerate(iter_meetings(args.split, args.count), start=1):
        entry = write_meeting(row, f"ami-{index:03d}", args.audio_dir, args.refs_dir)
        entries.append(entry)
        print(
            f"{entry['meeting_id']}: {entry['duration_s']:.0f}s, "
            f"{entry['speakers']} speakers, {entry['segments']} reference segments "
            f"-> {entry['file']}"
        )

    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(
            {"dataset": DATASET_REPO_ID, "config": CONFIG, "split": args.split, "meetings": entries},
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"manifest -> {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
