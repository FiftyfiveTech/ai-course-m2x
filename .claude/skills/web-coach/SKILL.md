---
name: web-coach
description: Run an interactive coach learning session for an m2x ticket — serve the lesson page, watch the chat file, grade DONE submissions, reply into the page. Use when the user says "start web coach session", "coach session", "learning session", or at ticket start per the CLAUDE.md learning loop.
---

# web-coach — lesson page ↔ Claude bridge (m2x)

Static lesson page + stdlib server (`tools/coach/server.py`). Page POSTs answers /
questions to `chat.jsonl`; Claude watches the file, grades, replies via
`tools/coach/say.py`; page polls `chat.jsonl` every 3s.

## Start a session

1. Serve dir = `docs/learning/coach/`. Reset state at session start only:
   `: > docs/learning/coach/chat.jsonl`
2. Start server (background task; check `lsof -i :8765` first):
   `python3 tools/coach/server.py --dir docs/learning/coach --port 8765`
3. Smoke test: `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8765/<page>.html`
   → 200, then POST `{"text":"ping"}` to `/send` → 204 (then reset chat.jsonl again).
4. Seed a greeting: `python3 tools/coach/say.py docs/learning/coach/chat.jsonl "<greeting>"`
5. Arm the watcher (background task — one-shot, exits on new user message; **re-arm
   after every wake**):

```bash
CHAT=docs/learning/coach/chat.jsonl; LAST=$(grep -c '"role": "user"' "$CHAT" || true)
while :; do sleep 3; NOW=$(grep -c '"role": "user"' "$CHAT" || true)
  if [ "$NOW" -gt "$LAST" ]; then echo "NEW:"; grep '"role": "user"' "$CHAT" | tail -n $((NOW-LAST)); exit 0; fi
done
```

6. Tell the user the URL: `http://127.0.0.1:8765/<page>.html`

## On messages

- `tag:"chat"` — answer in the page chat via `say.py`. Short, concrete, coach tone.
- `tag:"session-done"` — grade free-text answers, coach wrong MCQ picks, challenge
  weak reasoning; write results into the ticket's learning artifacts
  (`docs/learning/retros.md` or the primer); tell the user what the next session
  covers. Reply into the page chat.

## Lesson pages

- Live in `docs/learning/coach/`; one page per ticket/topic. Template:
  `docs/learning/coach/m2x-day1.html`.
- Contract: concept cards from the ticket's primer (`docs/learning/<m2x-nnn>-concepts.md`);
  quiz answers stay local until a DONE button bundles everything into ONE
  `tag:'session-done'` POST ending with explicit TASKS for Claude; chat panel polls
  `/chat.jsonl` every 3s (Enter=send, Shift+Enter=newline); **dark mode always**
  (CSS-var palette, `html.dark` override, 🌙/☀️ toggle, localStorage +
  `prefers-color-scheme`); video cards `fetch(src,{method:'HEAD'})`-gated so pages
  work before videos exist.
- Videos: short NotebookLM Video Overviews from the shared notebook (link in
  `docs/learning/README.md`) saved into `docs/learning/coach/videos/` (git-ignored).

## Notes

- `chat.jsonl` is append-only session state; never commit it.
- Server binds 127.0.0.1 — local only, nothing exposed.
- New topic page → also write/update the ticket primer first; the page derives from
  it, not the other way round.
