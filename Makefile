.PHONY: help test test-all update-fixtures docs try demo reset bench pins pins-upgrade audit clean

help:
	@echo "To demonstrate the software:"
	@echo "Each demo runs in build/playground and removes the files at the end by default (KEEP=1 keeps them)"
	@echo "MESSY=1 starts the demo from messy raw data, which pprl clean repairs"
	@echo "  make demo [SIZE=medium|large] [MESSY=1] [KEEP=1]  Simulate a full PPRL run on 1,000 records per site (or 25,000, 125,000)"
	@echo "  make try [COMMAND=hash | CASE=hash/12]            List example inputs (for one COMMAND), or run one CASE and keep its files"
	@echo "  make reset                                        Delete everything in build/playground"
	@echo ""
	@echo "To run tests:"
	@echo "  make test                                         Run the basic test suite"
	@echo "  make test-all                                     Run the regular and exhaustive test suites"
	@echo "  make update-fixtures                              Rewrite stored outputs, after an intended change; then review the diff"
	@echo "  make bench                                        Measure time and memory at up to 100,000 records per site"
	@echo ""
	@echo "Maintenance tasks:"
	@echo "  make docs                                         Write build/input-handling.md, an input-handling report"
	@echo "  make pins                                         Record exact package versions in pyproject.toml and uv.lock, from [tool.pprl]"
	@echo "  make pins-upgrade                                 Record exact package versions, using the newest versions"
	@echo "  make audit                                        Check the locked packages for known vulnerabilities"
	@echo "  make clean                                        Delete every file matched by .gitignore"

test:
	uv run pytest

test-all:
	uv run pytest -m "extended or not extended"

update-fixtures:
	UPDATE_FIXTURES=1 uv run pytest -m "extended or not extended"

docs:
	uv run python -m tests.usability_cases

try:
ifdef CASE
	@uv run python -m tests.playground try $(CASE)
else
	@uv run python -m tests.playground list $(COMMAND)
endif

demo:
	@uv run python -m tests.playground demo --size $(or $(SIZE),small) $(if $(MESSY),--messy) $(if $(KEEP),--keep)

reset:
	@uv run python -m tests.playground reset

pins:
	uv run --frozen python -m tests.pins

pins-upgrade:
	uv run --frozen python -m tests.pins --upgrade

audit:
	uv audit --preview-features audit-command

# Each run is saved in build/benchmarks/
bench:
	uv run pytest tests/benchmarks -q -p no:cacheprovider -n 0 \
		--benchmark-autosave \
		--benchmark-json=build/benchmarks/latest.json
	uv run python -m tests.benchmarks.report build/benchmarks/latest.json

clean:
	git clean -fdX
