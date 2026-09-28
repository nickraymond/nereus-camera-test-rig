# Nereus camera test rig — developer tasks.
# Host-side (Mac) targets only; hardware smoke tests live under scripts/.

PYTHON ?= python3
VENV   ?= .venv
PIP     = $(VENV)/bin/pip
PY      = $(VENV)/bin/python

.PHONY: help venv install install-color install-tg7 grvi-env test lint fmt license-check license-check-shipped clean

help:
	@echo "Targets:"
	@echo "  make install  - create $(VENV) and install the package (editable) with dev extras"
	@echo "  make install-color - add the Phase 8 [color] extra (numpy, OpenCV, tifffile)"
	@echo "  make install-tg7    - add the Mac-only TG-7 tools extra (rawpy); needs exiftool too"
	@echo "  make grvi-env BACKEND=<nereus-vision-dev> - separate env for the GRVI baseline"
	@echo "  make license-check  - check the shipped dependency closure (configs/licenses.yaml)"
	@echo "  make license-check-shipped - same, plus prove dev_only deps were replaced (Pi/backend)"
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

install-color: venv
	$(PIP) install -e ".[dev,color]"

install-tg7: venv
	$(PIP) install -e ".[dev,color,tg7]"

# GRVI baseline (Phase 8 S2a, OQ-31): the backend's own pins for the packages GRVI imports,
# read from its requirements.txt at BACKEND_REF — never this repo's env, never the backend's
# own checkout venv. Used by `python -m host_tools.color grvi --python $(GRVI_VENV)/bin/python`.
BACKEND ?= ../nereus-vision-dev
BACKEND_REF ?= origin/staging
GRVI_VENV ?= .venv-grvi
grvi-env:
	$(PYTHON) -m venv $(GRVI_VENV)
	git -C $(BACKEND) show $(BACKEND_REF):backend/requirements.txt \
		| grep -iE '^(numpy|opencv-python-headless|scipy|pillow)==' > $(GRVI_VENV)/requirements-grvi.txt
	$(GRVI_VENV)/bin/pip install -r $(GRVI_VENV)/requirements-grvi.txt

license-check:
	$(PY) -m host_tools.license_check

license-check-shipped:
	$(PY) -m host_tools.license_check --shipped

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check .

fmt:
	$(PY) -m ruff check --fix .

clean:
	rm -rf $(VENV) .pytest_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
