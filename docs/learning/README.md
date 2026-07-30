# Learning loop

Every ticket in this repo doubles as course material. The full protocol lives in
[CLAUDE.md](../../CLAUDE.md) (§ Learning loop); this folder holds the artifacts.

## What lives here

| File | What it is | When it's written |
|------|-----------|-------------------|
| `<m2x-nnn>-concepts.md` | Concept primer for a ticket: each concept it exercises, why it matters here, the pitfall | At ticket start, before implementation |
| `retros.md` | Per-ticket retro ledger: what was executed, deviations + why, lessons | At ticket close, after the Odoo comment |

## Shared NotebookLM notebook

**Notebook:** M2X — Course Concepts · _link pending — added on first share_

Sources are the design docs (`docs/design/`) and the concept primers in this folder.
Short Video Overviews are generated per topic and indexed below.

### Video index

| Topic | Primer | Video |
|-------|--------|-------|
| Provider adapter, content-addressed caching | [m2x-011-adapter-concepts.md](m2x-011-adapter-concepts.md) | _pending_ |
| Retry/backoff, run logging, secrets, hermetic tests | [m2x-011-adapter-concepts.md](m2x-011-adapter-concepts.md) | _pending_ |

## Running a session

At ticket start Claude offers a short interactive Q&A over the ticket's primer —
a web-coach browser session where the machine has one set up, otherwise plain
in-terminal questions. Either way the primer file is the source of truth, so the
session works the same on any developer's machine.
