.PHONY: run eval test

run:
	@echo "not implemented — Phase 0 builds this (m2x process <clip>)"
	@exit 1

eval:
	@echo "not implemented — eval harness lands in Phase 1B"
	@exit 1

test:
	uv run pytest -q
