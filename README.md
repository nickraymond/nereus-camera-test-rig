# Nereus Multi-Camera Test Rig

An **evaluation platform** for comparing camera compute options for Nereus Vision:
Raspberry Pi IMX708 (CSI), OpenMV N6, and OpenMV AE3 (USB). A Raspberry Pi is the
central coordinator that captures stills/short video, organizes results into
timestamped experiment folders with full metadata + checksums, runs an
AprilTag/reference-card detect-and-crop pipeline, and serves a local web UI to
review and download results.

This is **not** a production runtime — no Bristlemouth, Spotter, cellular, or
field-deployment code. See [`docs/SPEC_nereus_camera_test_rig.md`](docs/SPEC_nereus_camera_test_rig.md)
(the source of truth) and [`CLAUDE.md`](CLAUDE.md) (engineering philosophy).

## Status

Early scaffolding. The build proceeds phase by phase per the Spec's Build Plan (§4).
See [`docs/implementation_plan.md`](docs/implementation_plan.md) for what is being
ported/adapted/rewritten and [`docs/open_questions.md`](docs/open_questions.md) for
unverified assumptions gating later phases.

## Repository layout

```
src/nereus_camera_test_rig/   host + Pi application (cameras, capture, storage, analysis, web)
openmv/                       MicroPython apps for the N6 / AE3 boards
host_tools/                   Mac-side experiment / analysis / reporting tools
configs/                      rig, per-camera, and per-experiment YAML
scripts/                      install + hardware smoke-test scripts
tests/                        unit (Mac) / integration / hardware / fixtures
docs/                         spec, prior-art review, implementation plan, open questions
```

## Developer quick start (host / Mac)

```bash
make install     # create .venv and install the package (editable) + dev extras
make test        # run the host-side unit tests
make lint        # ruff checks
```

Working in a git worktree: `data/` and `.venv` exist only in the primary checkout. Point
make at the shared venv (`make test VENV=../../../.venv`, or an absolute path); pytest's
`pythonpath` makes the tests import the worktree's own `src/`, not the primary checkout's.

Runtime dependencies are minimal by default (PyYAML). Analysis, serial, and web
dependencies are optional extras installed per phase (see `pyproject.toml`).

## Pool mode (nereus002)

An unattended 3-camera RAW capture loop for underwater tests. It runs `scripts/pool_capture.py`
under `scripts/pool_mode.sh` (arm / start / stop / status / disarm), with a fail-soft Bar30
depth read per set. The Mac side is `host_tools/pool_pull.py` + `host_tools/pool_render.py`.

The green LED uses Nick's codes, precedence B > A > C:

- **C constant on** = OK.
- **A one short flash + long pause** = a camera not working.
- **B rapid flashing** = low battery (< 3.20 V on battery).

**I2C budget** (bus 1 is shared by the UPS at 0x43 and the Bar30 at 0x76; Nick: polling the
Bar30 faster than every few seconds can wedge the bus). Every pool-mode access goes through one
lock (`scripts/pool_led.py`):

- **Bar30:** ≤ 1 session per 3 s (enforced). In practice 2 per set, ~1 per 22 s.
- **LED:** ≤ 0.5 writes/s (pattern A); otherwise 1 write per state change.
- **UPS:** 4 registers per 10 s (power log) plus per set.
- Outside our control: the root `lifepo4wered-daemon` polls `PI_RUNNING` once a second.

The full legend, start/stop and download steps are in
[docs/POOL_DAY_CARD_nereus002.md](docs/POOL_DAY_CARD_nereus002.md).

