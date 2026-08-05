.PHONY: run eval test

# The Phase 0 demo command. CLIP is overridable so the gate can point at any file.
CLIP ?= data/clips/clip-mtg-002-5min.wav

run:
	uv run m2x process $(CLIP)

eval:
	@echo "not implemented — eval harness lands in Phase 1B"
	@exit 1

test:
	uv run pytest -q
