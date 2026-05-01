# trading-bot — make targets
# Phase 0 bootstrap: lint / typecheck / test / traceability gate.

PYTHON ?= python3

.PHONY: help install lint format typecheck test trace trace-check check backtest run clean

help:
	@echo "make targets:"
	@echo "  install       install package + dev dependencies (editable)"
	@echo "  lint          ruff check + format check"
	@echo "  format        ruff fix + format"
	@echo "  typecheck     mypy --strict"
	@echo "  test          pytest"
	@echo "  trace         tools/traceability.py --report"
	@echo "  trace-check   tools/traceability.py --check (CI gate)"
	@echo "  check         lint + typecheck + trace-check + test"
	@echo "  backtest      run main in backtest mode (Phase 5+)"
	@echo "  run           run main (Phase 5+)"
	@echo "  clean         remove build artifacts and caches"

install:
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	$(PYTHON) -m ruff check trading_system tests tools
	$(PYTHON) -m ruff format --check trading_system tests tools

format:
	$(PYTHON) -m ruff format trading_system tests tools
	$(PYTHON) -m ruff check --fix trading_system tests tools

typecheck:
	$(PYTHON) -m mypy --strict trading_system tests tools

test:
	$(PYTHON) -m pytest

trace:
	$(PYTHON) tools/traceability.py --report

trace-check:
	$(PYTHON) tools/traceability.py --check

check: lint typecheck trace-check test
	@echo "All gates passed."

backtest:
	$(PYTHON) -m trading_system.main --mode backtest

run:
	$(PYTHON) -m trading_system.main

clean:
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
