# Learning loop

Every ticket in this repo doubles as course material. The full protocol lives in
[CLAUDE.md](../../CLAUDE.md) (§ Learning loop); this folder holds the artifacts.

## What lives here

| File | What it is | When it's written |
|------|-----------|-------------------|
| `<m2x-nnn>-concepts.md` | Concept primer for a ticket: each concept it exercises, why it matters here, the pitfall | At ticket start, before implementation |
| `retros.md` | Per-ticket retro ledger: what was executed, deviations + why, lessons | At ticket close, after the Odoo comment |
| `coach/` | Lesson pages served by the coach bridge; one per ticket, plus `m2x-week1-recap.html` spanning everything so far | With the primer |

## Shared NotebookLM notebook

**Notebook:** [M2X — Course Concepts](https://notebooklm.google.com/notebook/1085a4a5-5505-4fe4-97b9-b1a8b8293026)
— shared with both developers (editor access).

Sources are the design docs (`docs/design/`) and the concept primers in this folder.
Short Video Overviews are generated per topic and indexed below.

**Syncing it is scripted** — [tools/coach/notebooklm_sync.py](../../tools/coach/notebooklm_sync.py)
uploads the primers/design docs as sources and downloads Video Overviews straight into
`coach/videos/`, using the unofficial [notebooklm-py](https://github.com/teng-lin/notebooklm-py)
client:

```bash
uv tool install "notebooklm-py[browser]"
notebooklm login                                    # once, opens a browser (no API keys exist)
uv run --script tools/coach/notebooklm_sync.py --dry-run
uv run --script tools/coach/notebooklm_sync.py
```

Auth is a Google browser session, so `notebooklm login` has to be run by a human on
their own machine — the script cannot do it. Only tracked docs are uploaded; the corpus
(`data/`, transcripts, audio) must never become a notebook source.

### Video index

| Topic | Primer | Video |
|-------|--------|-------|
| Provider adapter, content-addressed caching | [m2x-011-adapter-concepts.md](m2x-011-adapter-concepts.md) | Video Overview in notebook |
| Retry/backoff, run logging, secrets, hermetic tests | [m2x-011-adapter-concepts.md](m2x-011-adapter-concepts.md) | Video Overview in notebook |
| Corpus design + adapter (recap) | [m2x-000-corpus-concepts.md](m2x-000-corpus-concepts.md) | `coach/videos/m2x-recap-corpus-adapter.mp4` |
| Phase-0 slice + hosted-vs-local (recap) | [m2x-012-013-pipeline-concepts.md](m2x-012-013-pipeline-concepts.md) | `coach/videos/m2x-recap-pipeline-local.mp4` |

## Running a session

The interactive session tooling is bundled in this repo — works on any machine with
Python 3 and Claude Code, no extra deps:

```bash
python3 tools/coach/server.py --dir docs/learning/coach --port 8765
# open http://127.0.0.1:8765/m2x-day1.html
# then in Claude Code: "start web coach session"
```

Full instructions: [tools/coach/README.md](../../tools/coach/README.md). Lesson pages
live in `coach/`; the ticket primer file stays the source of truth, so the session
works the same on any developer's machine. Topic videos: download from the shared
notebook into `coach/videos/` (filenames in the page; git-ignored) — pages work
without them.
