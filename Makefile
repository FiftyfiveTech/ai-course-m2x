.PHONY: run run-local eval test

# The Phase 0 demo command. CLIP is overridable so the gate can point at any file.
CLIP ?= data/clips/clip-mtg-002-5min.wav

run:
	uv run m2x process $(CLIP)

# The local leg of the Phase 0 comparison: same clip, same model repo id, Ollama serving.
run-local:
	uv run m2x process $(CLIP) --provider ollama

eval:
	@echo "not implemented — eval harness lands in Phase 1B"
	@exit 1

test:
	uv run pytest -q
