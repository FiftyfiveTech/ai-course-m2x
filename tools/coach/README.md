# Coach — interactive learning sessions

Tiny stdlib-only bridge (no deps) between a lesson HTML page and Claude Code. The page
serves concept cards + a quiz; answers POST to a chat file; Claude grades and replies
into the page's chat panel.

## Run a session (any machine)

```bash
# 1. serve the lesson pages (from the repo root)
python3 tools/coach/server.py --dir docs/learning/coach --port 8765

# 2. open the session page
#    http://127.0.0.1:8765/m2x-day1.html

# 3. in a Claude Code session in this repo, say: "start web coach session"
#    Claude arms a watcher on docs/learning/coach/chat.jsonl, grades your DONE
#    submission, and coaches in the page's chat panel.
```

`chat.jsonl` is per-session state (git-ignored). Reset with `: > docs/learning/coach/chat.jsonl`.

## Claude side

- Watcher (Claude runs this as a background task; it exits when you send something):

```bash
CHAT=docs/learning/coach/chat.jsonl; LAST=$(grep -c '"role": "user"' "$CHAT" || true)
while :; do sleep 3; NOW=$(grep -c '"role": "user"' "$CHAT" || true)
  if [ "$NOW" -gt "$LAST" ]; then grep '"role": "user"' "$CHAT" | tail -n $((NOW-LAST)); exit 0; fi
done
```

- Reply into the page: `python3 tools/coach/say.py docs/learning/coach/chat.jsonl "text"`

## Topic videos

Lesson pages embed short NotebookLM Video Overviews from `docs/learning/coach/videos/`
(git-ignored — mp4s don't belong in the repo). Get them from the shared notebook
"M2X — Course Concepts" (link in [docs/learning/README.md](../../docs/learning/README.md)):
open the notebook → download the Video Overview → save as the filename the page
expects (for day 1: `m2x-011-adapter-caching.mp4`, `m2x-011-reliability-observability.mp4`).
The video cards auto-appear once the files exist; the page works fine without them.

## Adding a session page

Copy `docs/learning/coach/m2x-day1.html` as the template. Contract: answers stay local
until the DONE button bundles everything into one `tag:'session-done'` POST ending
with explicit TASKS for Claude; chat panel polls `/chat.jsonl` every 3s; dark mode
toggle included.
