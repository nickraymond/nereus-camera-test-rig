# Hardware & Tool Setup

Rig bring-up (Pi, cameras, OpenMV boards) is scripted in `scripts/install_pi.sh`,
`scripts/configure_pi_camera.sh` and `host_tools/deploy_openmv.py`; see the README.
This page covers the Mac-side tools added for Phase 8 (SPEC §4, §20).

## Phase 8 — edge color correction (Mac)

```bash
make install-color     # package + [color] extra: numpy, OpenCV, tifffile
make install-tg7       # + [tg7] extra: rawpy (Mac-only TG-7 tools)
brew install exiftool  # TG-7 EXIF (WaterDepth, DateTimeUTC, BlackLevel2, ColorMatrix)
make license-check     # shipped dependency closure vs configs/licenses.yaml
```

Verified versions (2026-09-26): exiftool 13.55, tifffile 2026.9.20, OpenCV 5.0.0.93,
numpy 2.5.1, rawpy 0.27.1 (LibRaw 0.22.1 — decodes the TG-7 ORF), Python 3.13.

Real-data tests are opt-in (the dataset is git-ignored and lives in the primary checkout):
`NEREUS_TG7_DATASET=<path to data/tg7_channel_islands> make test`.

In a git worktree, pass the shared venv: `make install-color VENV=../../../.venv`. Note that
`pip install -e` from a worktree re-points the shared venv's editable install at that
worktree; the tests don't depend on it (pytest `pythonpath`), but other tools do — re-run
`make install-color` from the primary checkout afterwards.

### GRVI baseline (S2a baseline a, OQ-31)

The backend's GRVI `cheeca_v3` correction is scored as a baseline, **unmodified and in its own
environment** — this repo never imports it. `make grvi-env` builds `.venv-grvi/` with the
backend's own pins for the four packages GRVI imports (numpy, opencv-python-headless, scipy,
Pillow), read from `backend/requirements.txt` at `BACKEND_REF`; the backend checkout, its
branch and its venv are not touched. The `grvi` stage exports `backend/app` at the resolved
commit with `git archive` and records the SHA in `stage.json`.

```bash
make grvi-env BACKEND=~/Documents/GitHub/nereus-vision-dev          # BACKEND_REF=origin/staging
python -m host_tools.color grvi results/color/<dataset_id>/locate --config <dataset.yaml> \
    --backend ~/Documents/GitHub/nereus-vision-dev --python .venv-grvi/bin/python
```

Verified 2026-09-27: backend `03272be` (staging), numpy 2.5.1, OpenCV 5.0.0, scipy 1.18.1,
Pillow 12.3.0; GRVI peaks at ~4.2 GB per 12 MP frame, so the stage runs 4 frames at a time.

### Linear JPEG XL transport (prototype, `color/linear_jxl.py`)

```bash
brew install jpeg-xl   # cjxl / djxl (libjxl) — Mac
python -m host_tools.color jxl-check <file.orf|file.dng> [--distance 1.0] [--wb r,g,b]
```

Verified 2026-09-28: libjxl 0.11.1 (Homebrew `jpeg-xl` 0.11.1_3), 10-bit PPM round trip
exact. `jxl-check` writes `results/color/jxl_check/<stem>/{lossless,d…}.jxl` + sidecar
`.json` + `summary.json`; expect the lossless run's block-mean p99 < 0.2 % and a d1 file of
~40–50 KB for a 1600×900 crop. On the field Pi the package is expected to be Debian's
`libjxl-tools` — **not verified** (OQ-49).

### Licences (SPEC §20 — permissive-only for shipped code)

- **rawpy** bundles LibRaw (LGPL-2.1 / CDDL). It is `internal_only`: Mac analysis tools
  under `host_tools/tg7/`, never imported from `src/` (a test enforces this).
- **exiftool** is an external Perl CLI (Artistic / GPL), called as a subprocess on the Mac
  only; it is never shipped.
- **OpenCV from PyPI** bundles FFmpeg (LGPL) and GPL codecs (x264, x265, …). It is
  `dev_only`: fine on the Mac, but a shipped Pi / backend image needs an OpenCV build
  without FFmpeg (OQ-36). `make license-check-shipped` proves it — on the Mac it is
  expected to fail with "built with FFmpeg".
- **libjxl** (`cjxl` / `djxl`) is BSD-3-Clause with a patent grant; `color/linear_jxl.py`
  calls the command-line tools as a subprocess (no Python binding, nothing bundled).
- Adding a dependency to base or `[color]` means adding a reviewed entry to
  `configs/licenses.yaml`; `make test` fails until you do.
