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

### Licences (SPEC §20 — permissive-only for shipped code)

- **rawpy** bundles LibRaw (LGPL-2.1 / CDDL). It is `internal_only`: Mac analysis tools
  under `host_tools/tg7/`, never imported from `src/` (a test enforces this).
- **exiftool** is an external Perl CLI (Artistic / GPL), called as a subprocess on the Mac
  only; it is never shipped.
- **OpenCV from PyPI** bundles FFmpeg (LGPL) and GPL codecs (x264, x265, …). It is
  `dev_only`: fine on the Mac, but a shipped Pi / backend image needs an OpenCV build
  without FFmpeg (OQ-36). `make license-check-shipped` proves it — on the Mac it is
  expected to fail with "built with FFmpeg".
- Adding a dependency to base or `[color]` means adding a reviewed entry to
  `configs/licenses.yaml`; `make test` fails until you do.
