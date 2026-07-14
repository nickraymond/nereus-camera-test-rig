# Nereus camera test rig — developer tasks.
# Host-side (Mac) targets only; hardware smoke tests live under scripts/.

PYTHON ?= python3
VENV   ?= .venv
PIP     = $(VENV)/bin/pip
PY      = $(VENV)/bin/python

.PHONY: help venv install test lint fmt clean

help:
	@echo "Targets:"
	@echo "  make install  - create $(VENV) and install the package (editable) with dev extras"
	@echo "  make test     - run the host-side unit tests (pytest)"
	@echo "  make lint     - run ruff checks"
	@echo "  make fmt      - auto-format/fix with ruff"
	@echo "  make clean    - remove venv and caches"

$(VENV):
	$(PYTHON) -m venv $(VENV)

venv: $(VENV)

install: venv
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check .

fmt:
	$(PY) -m ruff check --fix .

clean:
	rm -rf $(VENV) .pytest_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
