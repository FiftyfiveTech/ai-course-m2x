#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["notebooklm-py>=0.8"]
# ///
"""Sync this repo's learning docs into the shared NotebookLM notebook, and pull
Video Overviews back down for the coach pages.

Why this exists: the coach pages embed short Video Overviews, and until now getting
them meant opening the notebook by hand, uploading whichever docs had changed, clicking
generate twice, and saving the files under exactly the names the pages expect. That is
four manual steps that silently rot. This script is the same work, reproducible, using
the unofficial NotebookLM client (https://github.com/teng-lin/notebooklm-py).

Auth is *not* handled here and cannot be: NotebookLM has no API keys, so the client
reuses a Google browser session. Run once, interactively, on your own machine::

    uv tool install "notebooklm-py[browser]"
    notebooklm login          # opens a browser; saves ~/.notebooklm/profiles/default

Then this script is offline-ish and repeatable::

    uv run tools/coach/notebooklm_sync.py --dry-run     # show what it would do
    uv run tools/coach/notebooklm_sync.py               # upload docs + build videos

Nothing here writes to the repo except ``docs/learning/coach/videos/`` (git-ignored).

Note on what leaves the machine: every path in :data:`SOURCE_DOCS` is uploaded to the
shared notebook, which is a Google product. They are all tracked design/learning docs
written for exactly that purpose — no transcripts, no audio, nothing from ``data/``.
Keep it that way: the corpus itself must never become a NotebookLM source.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from notebooklm import NotebookLMClient, VideoStyle

REPO_ROOT = Path(__file__).resolve().parents[2]

NOTEBOOK_ID = "1085a4a5-5505-4fe4-97b9-b1a8b8293026"
"""Shared "M2X — Course Concepts" notebook (both developers have editor access).

Indexed in ``docs/learning/README.md``; override with ``--notebook``.
"""

SOURCE_DOCS = (
    ("docs/m2x-week1-handbook.md", "m2x-week1-handbook.md"),
    ("docs/corpus.md", "m2x-000-corpus-record.md"),
    ("docs/design/day1-adapter.md", "day1-adapter-design.md"),
    ("docs/design/phase0-local-path.md", "phase0-local-path-design.md"),
    ("docs/learning/m2x-000-corpus-concepts.md", "m2x-000-corpus-concepts.md"),
    ("docs/learning/m2x-011-adapter-concepts.md", "m2x-day1-concepts-primer.md"),
    ("docs/learning/m2x-012-013-pipeline-concepts.md", "m2x-012-013-pipeline-concepts.md"),
    ("docs/learning/retros.md", "m2x-retros.md"),
)
"""``(repo-relative path, notebook source title)`` pairs making up the source set.

The title is explicit rather than derived from the filename because the notebook was
first populated by hand: ``docs/design/day1-adapter.md`` is already in there as
``day1-adapter-design.md`` and the M2X-011 primer as ``m2x-day1-concepts-primer.md``.
Matching those names is what makes re-running this a no-op instead of a second copy of
the same doc competing in retrieval.

Missing files are skipped with a warning rather than fatal: several live on ticket
branches that are not merged yet (``docs/corpus.md`` on M2X-000,
``docs/design/phase0-local-path.md`` on M2X-013), so the useful behaviour on ``main``
is "upload what exists".
"""

VIDEOS = (
    {
        "filename": "m2x-recap-corpus-adapter.mp4",
        "style": VideoStyle.WHITEBOARD,
        "instructions": (
            "A tight technical explainer for the two engineers who built this, not an "
            "audience of beginners. Cover: why the pilot corpus needed an English AMI "
            "control set alongside the Hinglish internal meetings; why exclusions are "
            "listed with reasons; then the provider adapter — one interface over "
            "Groq/NIM/Ollama, models named by Hugging Face repo id, routing as data in "
            "config/models.toml — and the content-addressed cache key, including why "
            "omitting provider or sampling params silently corrupts a comparison. "
            "Be concrete and name the pitfalls. Under 6 minutes."
        ),
    },
    {
        "filename": "m2x-recap-pipeline-local.mp4",
        "style": VideoStyle.WHITEBOARD,
        "instructions": (
            "A tight technical explainer for the two engineers who built this. Cover: "
            "the Phase-0 vertical slice (CLI parses, pipeline decides, adapter is the "
            "only thing that talks to a provider) and why timestamped segments are the "
            "foundation every later citation depends on; then the hosted-versus-local "
            "measurement — the same repo id meta-llama/Llama-3.1-8B-Instruct served by "
            "Groq at 721 ms and by Ollama at 189,200 ms, 262x slower, compute-bound not "
            "memory-bound — and what that implies about where local inference belongs. "
            "Finish on why --provider was scoped to the summary step instead of the "
            "whole pipeline, and why silent provider fallback would ruin attribution. "
            "Under 6 minutes."
        ),
    },
)
"""Video Overviews to build, keyed by the filename the coach pages already expect.

``style`` must be a :class:`~notebooklm.VideoStyle` member, not the string the CLI
accepts — the library reads ``.value`` off it and an ``AttributeError`` is all you get
otherwise.

``docs/learning/coach/m2x-week1-recap.html`` HEAD-probes these paths and reveals its
video cards only when the files exist, so a failed or skipped generation degrades to a
page without videos rather than a broken one.
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed namespace with ``notebook``, ``videos_dir``, ``timeout``, ``dry_run``,
        ``skip_sources`` and ``skip_videos``.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--notebook", default=NOTEBOOK_ID, help="notebook id (or a unique prefix)")
    ap.add_argument(
        "--videos-dir",
        type=Path,
        default=REPO_ROOT / "docs" / "learning" / "coach" / "videos",
        help="where downloaded Video Overviews land (git-ignored)",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=1800.0,
        help="seconds to wait per video generation (default: 1800)",
    )
    ap.add_argument("--dry-run", action="store_true", help="print the plan, touch nothing")
    ap.add_argument("--skip-sources", action="store_true", help="do not upload docs")
    ap.add_argument("--skip-videos", action="store_true", help="do not generate videos")
    return ap.parse_args(argv)


def resolve_sources() -> tuple[list[tuple[Path, str]], list[str]]:
    """Split :data:`SOURCE_DOCS` into files that exist and paths that do not.

    Returns:
        ``(present, missing)`` — ``(absolute path, notebook title)`` pairs, and
        repo-relative strings for the docs that are not on this branch.
    """
    present: list[tuple[Path, str]] = []
    missing: list[str] = []
    for rel, title in SOURCE_DOCS:
        path = REPO_ROOT / rel
        if path.is_file():
            present.append((path, title))
        else:
            missing.append(rel)
    return present, missing


async def sync(args: argparse.Namespace) -> int:
    """Upload sources and build videos.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Process exit code — 0 on success, 1 if any video failed to generate.
    """
    present, missing = resolve_sources()
    for rel in missing:
        print(f"  skip (not on this branch): {rel}", file=sys.stderr)

    if args.dry_run:
        print(f"notebook: {args.notebook}")
        print("would upload:")
        for path, title in present:
            print(f"  + {path.relative_to(REPO_ROOT).as_posix()} -> {title}")
        print("would generate:")
        for spec in VIDEOS:
            print(f"  + {spec['filename']} (style={spec['style'].name.lower()})")
        return 0

    args.videos_dir.mkdir(parents=True, exist_ok=True)
    failures = 0

    async with NotebookLMClient.from_storage() as client:
        if not args.skip_sources:
            existing = {s.title for s in await client.sources.list(args.notebook) if s.title}
            for path, title in present:
                # NotebookLM has no "replace source" — re-uploading a same-titled doc
                # would leave two copies competing in retrieval. Skipping titles that are
                # already there keeps re-runs idempotent; delete the source in the UI (or
                # `notebooklm source delete`) when a doc has changed materially.
                if title in existing:
                    print(f"  = already a source: {title}")
                    continue
                await client.sources.add_file(args.notebook, path, wait=True, title=title)
                print(f"  + uploaded: {title}")

        if not args.skip_videos:
            for spec in VIDEOS:
                out = args.videos_dir / spec["filename"]
                if out.exists():
                    print(f"  = video already downloaded: {out.name}")
                    continue
                print(f"  ~ generating {out.name} (this takes minutes)")
                status = await client.artifacts.generate_video(
                    args.notebook,
                    instructions=spec["instructions"],
                    video_style=spec["style"],
                )
                status = await client.artifacts.wait_for_completion(
                    args.notebook, status.task_id, timeout=args.timeout
                )
                if not status.is_complete:
                    print(f"  ! failed ({status.status}): {status.error}", file=sys.stderr)
                    failures += 1
                    continue
                await client.artifacts.download_video(
                    args.notebook, str(out), artifact_id=status.task_id
                )
                print(f"  > saved {out.relative_to(REPO_ROOT).as_posix()}")

    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Returns:
        Process exit code. Authentication problems exit 2 with the fix printed, because
        the fix is a command the human has to run in their own browser session.
    """
    args = parse_args(argv)
    try:
        return asyncio.run(sync(args))
    except Exception as exc:  # noqa: BLE001 — surface the fix, not a traceback
        blob = f"{type(exc).__name__}: {exc}"
        if any(word in blob.lower() for word in ("auth", "login", "credential", "storage")):
            print(f"{blob}\n\nRun `notebooklm login` once, then retry.", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(main())
