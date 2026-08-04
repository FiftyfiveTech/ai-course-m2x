#!/usr/bin/env bash
#
# Normalise a source recording into the corpus format: 16 kHz mono PCM WAV.
#
# Day 2 compares transcription strategies across meetings, so the audio has to stop
# being a variable: a 48 kHz stereo Teams MP4 and a 16 kHz mono WAV do not produce
# comparable word error rates even from the same model. 16 kHz mono is what every ASR
# model in the registry resamples to internally anyway, so converting once here means
# the conversion is not silently repeated per call with per-provider defaults.
#
# Usage:
#   scripts/prepare_audio.sh <source-media> <meeting-id-slug> [--loudnorm] [--force]
#
# Example:
#   scripts/prepare_audio.sh ~/Downloads/'Call with ....mp4' mtg-002-course-scope
#
# Writes data/raw/<meeting-id-slug>.wav and prints the measured loudness.
#
# --loudnorm applies EBU R128 loudness normalisation. It is OFF by default on purpose:
# the corpus is already level (see docs/corpus.md, "Verification performed"), and
# rewriting existing files would invalidate both the measurements recorded there and
# every response-cache key derived from the audio. Turn it on for a new source that
# measures far from the others.

set -euo pipefail

readonly TARGET_RATE=16000
readonly TARGET_CHANNELS=1
readonly OUTPUT_DIR="data/raw"

usage() {
    sed -n '3,25p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-2}"
}

main() {
    local source="" slug="" loudnorm=0 force=0

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --loudnorm) loudnorm=1 ;;
            --force) force=1 ;;
            -h|--help) usage 0 ;;
            -*) echo "unknown flag: $1" >&2; usage ;;
            *)
                if [[ -z "$source" ]]; then source="$1"
                elif [[ -z "$slug" ]]; then slug="$1"
                else echo "unexpected argument: $1" >&2; usage
                fi
                ;;
        esac
        shift
    done

    [[ -n "$source" && -n "$slug" ]] || usage
    command -v ffmpeg >/dev/null || { echo "ffmpeg not found — install it first" >&2; exit 1; }

    if [[ ! -f "$source" ]]; then
        echo "source not found: $source" >&2
        exit 1
    fi

    local output="${OUTPUT_DIR}/${slug}.wav"
    if [[ -f "$output" && $force -eq 0 ]]; then
        echo "$output exists — nothing to do (pass --force to rebuild)"
        report "$output"
        return 0
    fi

    mkdir -p "$OUTPUT_DIR"

    # -vn discards the video stream. Teams recordings are MP4 and the picture is only
    # needed for Phase 5 key-frame work, which reads the original file, not this one.
    local filters="aresample=${TARGET_RATE}"
    if [[ $loudnorm -eq 1 ]]; then
        filters="loudnorm=I=-16:TP=-1.5:LRA=11,${filters}"
    fi

    ffmpeg -hide_banner -loglevel error -y \
        -i "$source" \
        -vn \
        -af "$filters" \
        -ac "$TARGET_CHANNELS" \
        -ar "$TARGET_RATE" \
        -c:a pcm_s16le \
        "$output"

    echo "wrote $output"
    report "$output"
}

report() {
    # Loudness is printed rather than asserted: a quiet file is a judgement call, and
    # a script that refused a real meeting because it measured -31 dB would be worse
    # than one that says so and lets a human decide.
    local file="$1"
    local duration
    duration=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$file")
    local mean
    mean=$(ffmpeg -hide_banner -i "$file" -af volumedetect -f null /dev/null 2>&1 \
        | sed -n 's/.*mean_volume: \(.*\)/\1/p')
    printf '  %.0fs  %s Hz mono  mean volume %s\n' \
        "$duration" "$TARGET_RATE" "${mean:-unknown}"
}

main "$@"
