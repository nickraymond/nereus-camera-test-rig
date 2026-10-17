# Lean V3 card detector on the Pi Zero 2 W: where the memory goes (2026-10-09)

This is the starting point for Phase B (the camera chip). The T0.4 bar is **≤ 80 MB resident**. The lean path (`scripts/s28_card_wb.py`) peaks at **147–153 MB**.

**How it was measured:**
- Host: nereus002, a bench Pi Zero 2 W with the same Pi class as the bmcam units, but not a unit.
- Process: one process, run as the hand-off runs it (`ulimit -v 1,000,000`, `oom_score_adj 1000`).
- Frame: `s10091030_lowgain.dng`, 4/4 tags.
- Metric: `/proc/self/status` VmRSS / VmHWM after each stage.

## Breakdown

| stage | VmRSS | VmHWM | step |
|---|---|---|---|
| python started | 10 MB | 10 | |
| `import numpy` | 25 | 25 | +15 |
| `import cv2` | 42 | 42 | +17 |
| rig modules (`color.card`, `locate`, `raw_io`) | 52 | 52 | +10 |
| `read_dng`, full 12 MP uint16 mosaic (2592×4608) | 78 | 78 | +26 (the 24 MB mosaic) |
| crop the mosaic to the B3a 1600×900, free the full one | 58 | 80 | −20 |
| `normalize` (float32 crop + saturation mask) | 75 | 80 | +17 |
| `demosaic_bilinear`, float32 1600×900×3 + convolution temporaries | 111 | 111 | +36 |
| single-scale ArUco tag25h9 detect on the 8-bit green view | **147** | **147** | +36 |

**Where the memory goes:**
- **Imports:** a 52 MB floor (Python + numpy + OpenCV + rig code). OpenCV alone is 17 MB resident.
  - Its *virtual* size is the reason `ulimit -v 256000` (the units' encoder guard) cannot load `cv2` at all: the import's VmPeak is ~506 MiB.
- **Arrays:** about +60 MB at peak, mostly the 3-channel float demosaic (+36).
- **Detector:** about +36 MB inside OpenCV ArUco (adaptive threshold images, contours, candidates at 1600×900).

## Levers to reach ≤ 80 MB (estimates, not measured)

1. **Green only:** the detector needs a single grey view. The full RGB demosaic is only needed for the one grey patch sampled afterwards, which takes a few hundred pixels.
   - Build the 8-bit view from the green plane only, and sample the patch's R/G/B from the raw mosaic sites.
   - Estimate: −25 to −30 MB.
2. **Read only the crop from the DNG:** what `rc_raw_jxl.read_dng_crop` already does for B3a. It never holds the full 24 MB mosaic.
   - Estimate: −20 MB at the read peak; this matters only if it is the peak.
3. **Detector settings:** the ArUco temporaries scale with the image and the adaptive-threshold window steps.
   - Options: run on the card's last-known ROI (the card moves with current/tide, so pad generously), or narrow `adaptiveThreshWinSizeMin/Max/Step`.
   - Estimate: −10 to −20 MB. It needs a reliability check at dusk, where single-scale already finds only 3/4 tags at times.
4. **Imports:** the 52 MB floor stays with Python + OpenCV. Going under ~60 MB total would mean a non-Python detector, for example the AprilRobotics C library in a small helper binary. That is a design choice for the camera chip, not a tuning step.

**Expected with 1 + 2 + 3:** roughly 80–95 MB. Measure before relying on it.

## Related findings from the same runs

- **Detect time:** P90 1.7–2.4 s at single scale on the 1600×900 crop, within the ≤ 3 s bar.
  - Wake cost per slot is ~3–4 s (prep + detect) in a running process.
  - Add ~2 s once if numpy / OpenCV are imported only for this.
- **The full camera JPEG is not viable on the Pi:** single scale takes 13 s P50, VmHWM ~290 MB, and was OOM-killed 14× on 2026-10-08.
- **Reliability vs light:** 4/4 tags from ~5–20 Lux at dusk on the stock / 250 ms / EV+1 arms.
  - The gain-locked 60 ms arm needs more than ~16–20 Lux; on 2026-10-09 in fog it first located the card at 08:30, 16 Lux.
  - Below ~1 Lux, no arm finds tags.
