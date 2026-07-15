# Down-Select Criteria & Comparison Metrics

Evidence to collect across the candidate platforms (Raspberry Pi IMX708, OpenMV N6,
OpenMV AE3) to support the hardware down-select (Spec §17). This file accumulates
concrete, measured findings as the evaluation proceeds — not opinions. Define the
final scoring method before the selection (Spec §17).

Status per finding: `MEASURED` (real data) · `OBSERVED` (seen but not quantified) ·
`TODO` (to collect).

---

## Video encoding efficiency — hardware vs. software H.264

**[OBSERVED — 2026-07-14]** A real, platform-differentiating result.

- **Raspberry Pi 5 has *no* hardware H.264 encoder.** (The Pi 4 and earlier did; the
  Pi 5 dropped it.) On `nereus000`, `rpicam-vid` was also built without `libav`, so
  H.264 fails outright and the rig captures video as **MJPEG**.
- **MJPEG vs H.264 trade-off on the Pi:**
  - MJPEG = every frame an independent JPEG → *large files*, but *low CPU* and each
    frame is independently decodable (good for frame extraction / analysis).
  - H.264 = temporal compression → *~10–50× smaller files*, but on the Pi 5 it must
    run in **software (libx264)** = *high CPU/power*.
  - Net on Pi 5: you trade storage for CPU. There is no low-CPU + small-file option.
- **OpenMV N6 has *hardware* H.264 acceleration** (per hardware spec — to be verified
  against OpenMV docs / a real board in Phase 3, OQ-5). If confirmed, the N6 gets
  *both* small files *and* low CPU/power at once — an efficiency advantage the Pi 5
  structurally cannot match.

**Why it matters for the down-select:** for any workflow that stores or transmits
video (or runs long recording sessions on limited power), the N6's hardware H.264
is a concrete efficiency edge. The Pi 5 either burns CPU/power (software H.264) or
storage/bandwidth (MJPEG).

**To measure (TODO, Phase 3 / Phase 7):**
- Pi 5 software-H.264 (once `libav` is installed): encode time, CPU %, power draw,
  file size vs. MJPEG at 1080p for a fixed clip.
- N6 hardware-H.264: same metrics, plus max resolution/framerate and supported
  container/codec profile.
- Put the numbers side by side: file size (MB/s), CPU load, power (W), and any
  quality difference at matched bitrate.

---

## Other comparison axes (to populate as evidence lands)

Per Spec §17 — collect for each platform:

- Image quality: sharpness, contrast, colour accuracy, low-light, above vs. underwater.
- AprilTag detectability & reference-card crop success (the primary metric, Spec §1).
- Startup time, capture latency.
- Power draw (idle, capture, encode).
- Storage behaviour and output sizes.
- Inference capability (models, latency, memory) — Spec §18.
- Development + deployment complexity, maintainability.
- Hardware cost, physical size, production fit.
