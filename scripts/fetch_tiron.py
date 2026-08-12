#!/usr/bin/env python3
"""Fetch English meetings from the Tiron evaluation set on Hugging Face.

Why this exists alongside ``fetch_ami.py``: ``diarizers-community/ami`` ships reference
speaker *turns* but no reference *words*, so every WER number still needed a snippet
written by ear — which is what blocked M2X-024/025. ``Trelis/tiron-eval-meetings``
ships both on one timeline, so a WER reference and a diarisation reference come from
the same human annotation and neither has to be hand-made.

Source: ``Trelis/tiron-eval-meetings``, splits ``ami`` / ``icsi`` / ``notsofar``, all
CC BY 4.0 and ungated. Rows are whole meetings of far-field single-channel audio.
Named by Hugging Face repo id like every other model and dataset here.

Usage::

    uv run --group corpus python scripts/fetch_tiron.py --split ami --count 2

Writes ``data/raw/tiron-<meeting>.wav`` (16 kHz mono), reference speaker segments to
``eval/tiron/tiron-<meeting>.speakers.json`` and reference words to
``eval/tiron/tiron-<meeting>.txt``. Meetings keep their real corpus id (``ES2004a``,
``Bmr013``, ``MTG_32040``) so an overlap with an already-fetched meeting is visible in
the filename rather than hidden behind a sequence number.
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
from typing import Any, Iterator

DATASET_REPO_ID = "Trelis/tiron-eval-meetings"
SPLITS = ("ami", "icsi", "notsofar")


def iter_meetings(split: str, count: int) -> Iterator[dict[str, Any]]:
    """Stream meetings from the dataset.

    Args:
        split: One of ``ami`` / ``icsi`` / ``notsofar``.
        count: How many meetings to take.

    Yields:
        Raw dataset rows.
    """
    from datasets import Audio, load_dataset

    dataset = load_dataset(DATASET_REPO_ID, split=split, streaming=True)
    # Encoded bytes, not a decoded array — same reason as fetch_ami.py: `datasets` 4.x
    # decodes through torchcodec, and soundfile already reads a WAV.
    dataset = dataset.cast_column("audio", Audio(decode=False))
    for index, row in enumerate(dataset):
        if index >= count:
            return
        yield row


def write_meeting(row: dict[str, Any], audio_dir: Path, refs_dir: Path) -> dict[str, Any]:
    """Write one meeting's audio, reference speaker turns and reference words.

    Args:
        row: Dataset row carrying audio bytes and ``utterances_json``.
        audio_dir: Destination for the WAV.
        refs_dir: Destination for the reference JSON and text.

    Returns:
        A manifest entry describing what was written.

    Raises:
        OSError: A file could not be written.
    """
    import soundfile

    audio_dir.mkdir(parents=True, exist_ok=True)
    refs_dir.mkdir(parents=True, exist_ok=True)

    meeting_id = f"tiron-{row['meeting_id']}"
    samples, sample_rate = soundfile.read(
        io.BytesIO(row["audio"]["bytes"]), dtype="int16", always_2d=False
    )
    if samples.ndim > 1:
        samples = samples[:, 0]
    audio_path = audio_dir / f"{meeting_id}.wav"
    soundfile.write(audio_path, samples, sample_rate, subtype="PCM_16")

    utterances = json.loads(row["utterances_json"])
    segments = [
        {
            "t_start": float(utterance["begin_time"]),
            "t_end": float(utterance["end_time"]),
            "speaker": str(utterance["speaker_id"]),
        }
        for utterance in utterances
    ]
    reference_path = refs_dir / f"{meeting_id}.speakers.json"
    reference_path.write_text(
        json.dumps({"meeting_id": meeting_id, "segments": segments}, indent=2),
        encoding="utf-8",
    )

    # eval/wer.py --reference reads plain text, so the words land as text rather than
    # as another JSON shape it would need teaching to read.
    text_path = refs_dir / f"{meeting_id}.txt"
    text_path.write_text(
        "\n".join(str(utterance["text"]).strip() for utterance in utterances) + "\n",
        encoding="utf-8",
    )

    unknown_spans = json.loads(row["unknown_spans_json"] or "[]")
    return {
        "meeting_id": meeting_id,
        "corpus": row["corpus"],
        "source_meeting_id": row["meeting_id"],
        "file": str(audio_path),
        "reference": str(reference_path),
        "reference_text": str(text_path),
        "duration_s": round(len(samples) / sample_rate, 1),
        "speakers": len({segment["speaker"] for segment in segments}),
        "segments": len(segments),
        "sampling_rate": sample_rate,
        # NOTSOFAR only: stretches the annotator marked <UNKNOWN/>. Scoring words
        # inside them measures the annotation, not the model.
        "unknown_spans": unknown_spans,
    }


def main() -> int:
    """Fetch meetings and print a manifest.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="ami", choices=SPLITS, help="which corpus to pull")
    parser.add_argument("--count", type=int, default=2, help="how many meetings to fetch")
    parser.add_argument("--audio-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--refs-dir", type=Path, default=Path("eval/tiron"))
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="where the fetch manifest is written (default: <refs-dir>/manifest-<split>.json)",
    )
    args = parser.parse_args()

    entries = []
    for row in iter_meetings(args.split, args.count):
        entry = write_meeting(row, args.audio_dir, args.refs_dir)
        entries.append(entry)
        print(
            f"{entry['meeting_id']}: {entry['duration_s']:.0f}s, "
            f"{entry['speakers']} speakers, {entry['segments']} reference utterances "
            f"-> {entry['file']}"
        )

    manifest_path = args.manifest or args.refs_dir / f"manifest-{args.split}.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {"dataset": DATASET_REPO_ID, "split": args.split, "meetings": entries}, indent=2
        ),
        encoding="utf-8",
    )
    print(f"manifest -> {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
