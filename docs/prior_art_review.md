# Prior-Art Review

**Phase 0 deliverable (Spec §4, §3).** This document records what was inspected in the
three prior-art repositories, what is reusable, the hardware-specific assumptions baked
into that code, and the code that must **not** be copied into this evaluation rig.

Method: all three repos were shallow-cloned and read directly (not summarized from
memory). Findings below cite concrete files and functions so a reviewer can verify them.
Anything that could not be verified from the source is recorded in
[`open_questions.md`](open_questions.md) rather than guessed.

---

## Headline findings

1. **No OpenMV prior art exists.** A search for `openmv | micropython | ttyACM | N6 | AE3`
   across all three repos returns nothing. The entire OpenMV side of this rig — the USB
   JSON command protocol (Spec §8), the MicroPython capture service on N6/AE3, and file
   transfer (Spec §10) — has **no proven code to reuse** and must be written from scratch
   against official OpenMV documentation. Every OpenMV API is currently unverified. This is
   the project's single biggest risk; specific unknowns are logged in `open_questions.md`.

2. **Two different Pi capture backends exist in the prior art.** They disagree, and the
   spec settles it:
   - `bm_cam_legacy` → shells out to **`rpicam-still` / `libcamera-still`** (CLI).
   - `bm_rpi_camera_module` → uses **Picamera2** (Python API).
   - **Spec §9 prefers the `rpicam-still`/`libcamera-still` CLI path.** So the *capture
     mechanism* to adopt is `bm_cam_legacy`'s; the *module structure* (adapters, handlers,
     config/logging/paths, encoder, lock, result schema) to adopt is
     `bm_rpi_camera_module`'s. They compose cleanly.

3. **AprilTag detection is OpenCV ArUco, not pupil/dt_apriltags.** `bm_cam_legacy` uses
   `cv2.aruco` with `DICT_APRILTAG_36h11` (requires `opencv-contrib-python`). Its
   reference-card pass rule already matches Spec §13. The card *geometry* is hardcoded to
   the Nereus card and must be re-parameterized, not copied verbatim.

4. **`borealis_sbc` contributes lessons only.** It is entirely shell/systemd/TOML plus a
   compiled C binary — no Python. Nothing to port.

---

## Repo 1 — `nickraymond/bm_cam_legacy`

Richest source of reusable logic: IMX708 CLI capture, crop/resize, HEIC encode, and the
AprilTag/reference-card pipeline.

### Reuse for — Pi capture pipeline (`BM_Devel_Pi/`)

| Component | File · function | Reuse verdict |
|---|---|---|
| Camera-binary selection (`rpicam-still` → `libcamera-still` via `shutil.which`) | `process_image_v2.py` · `_select_camera_command()` (L766) | **Adapt** — matches Spec §9 exactly |
| Native capture command builder + timeout/retry/progressive-fallback | `process_image_v2.py` · `_run_native_full_capture()` (L1171), `_run_camera_command_with_timeout()` (L859) | **Adapt** — keep structure, strip BM telemetry calls |
| Camera-control CLI flags (focus/WB/exposure/gain/image-proc) | `process_image_v2.py` · `_camera_controls_from_settings()` (L984), `_focus_camera_controls_from_settings()` (L915) | **Adapt** — this is the §9 control surface |
| libcamera `--metadata` JSON parsing | `process_image_v2.py` · `_load_libcamera_metadata_json()` (L318) | **Adapt** |
| Capture-metadata sidecar (save/load/update, requested-vs-actual) | `process_image_v2.py` · `save_capture_metadata()` etc. (L271–316) | **Adapt** — informs Spec §5 metadata contract |
| Crop → downsample (Pillow, atomic temp write, Pi-Zero mem discipline) | `crop_downsample_helper.py` · `main()` | **Port** ~as-is (standalone, no BM deps) |
| HEIC encode (Pillow + `pillow_heif`, atomic write) | `heic_encode_helper.py` · `main()` | **Port** ~as-is |
| Config→settings flattening + validation, CLI flag template | `main_pi_camera.py` · `_build_image_pipeline_settings()` (L160), argparse (L578+) | **Adapt** |

**Capture-command facts (verified):** base command is
`[cmd, "-n", "--timeout","2000", "--width",W, "--height",H, "--quality",Q, "--metadata",path, <controls>, "-o",out]`.
Control flags built on demand: `--autofocus-mode`, `--lens-position`, `--autofocus-range`,
`--autofocus-speed`, `--awb <mode>`, `--awbgains R,B`, `--shutter <µs>`, `--gain <float>`,
`--sharpness/--contrast/--saturation/--brightness/--denoise/--hdr`.

**`image_pipeline` YAML schema (verified):** `enabled`, `capture_backend`,
`source:{width,height,jpeg_quality}`, `crop:{mode,x,y,w,h}`,
`spatial:{output_width,output_height,resample}`, `heic:{quality}`, and an optional nested
`camera_controls:{focus,white_balance,exposure,image_processing}` block (defined only in
the Python parser — present as a commented stub in the shipped YAML).

**Metadata fields parsed:** `ExposureTime`, `AnalogueGain`, `DigitalGain`, `ColourGains`,
`ColourTemperature`, `LensPosition`, `AfState`, `AfMode`, `FocusFoM`, `Lux`,
`FrameDuration`, `SensorTemperature`.

### Reuse for — reference-card / AprilTag pipeline (`tools/`)

Proven core: `tools/bm_reference_card_quality_v2.py`. Reusable **pure** functions:
`detect_tags()`, `infer_card_corners_from_tags()`, `expand_quad()`, `rectify_quad()`
(perspective warp via `cv2.getPerspectiveTransform`/`warpPerspective`), `tag_side_lengths()`,
sharpness/contrast metrics (`variance_laplacian`, `tenengrad`, `contrast_p95_p05`),
`compute_card_metrics()`, `compare_to_reference()`, `load_image_bgr()`, `parse_corner_map()`.

- **Detector:** `cv2.aruco.getPredefinedDictionary(DICT_APRILTAG_36h11)` +
  `CORNER_REFINE_APRILTAG`; multi-scale detection (upscale, keep best).
- **Pass rule (strict variant, matches Spec §13):** all expected tag IDs present, min tag
  side ≥ threshold (10 px fail / 18 px warn), card boundary computable, nonempty crop saved.

### Hardware-specific assumptions (bm_cam_legacy)

- Hardcoded `/home/pi/...` paths (`IMAGE_DIRECTORY`, `LOG_FILE`, `SOFTWARE_REPO_PATH`, schedule path).
- `vcgencmd measure_temp`, `hwclock -r`, `/proc/meminfo` — Pi/Linux-specific.
- `--timeout 2000` warm-up is **hardcoded**, not config-driven.
- IMX708 source dims `4608×2592` are hardcoded defaults (matches our target sensor, so OK).
- **No libcamera `--roi`** anywhere — "crop" is always a *post-capture Pillow crop in native
  JPEG coordinates*. True sensor ROI would be a rewrite, not a port.
- Card geometry is Nereus-specific: tag IDs `1,2,3` (+optional `0`) → TR/BL/BR/TL;
  `expand_quad` factors `x=1.25, y=2.0`; rectified size `1000×420`; thresholds `10/18 px`.
  **Re-parameterize for our card; do not hardcode.**

### Do NOT copy (bm_cam_legacy)

- `bm_serial.py`, `spotter_time_sync.py`, and every `bm_serial` call in `process_image_v2.py`
  (`_get_bm_serial`, `send_wake_status`, `send_compact_text_message`, …).
- The transmit/chunking path: `compress_and_send_image()`, `send_buffers()`,
  `split_image_*()`, `_build_start/end_image_message`, all `_send_*_status` telemetry.
- Scheduling / Spotter-time gating in `main_pi_camera.py` (`should_transmit_now_from_schedule`,
  `USE_SPOTTER_TIME_WINDOW`, `--transmit`).
- The macOS-`sips`-based HEIC cellular-cost sweep (300B/900B message-count model) in the
  reference-card tools — a BM transmission concern. Use `pillow_heif` for HEIC I/O instead.
- **Coupling caveat:** the capture functions call `_send_capture_status` telemetry *inline,
  interleaved with the retry logic*. Porting requires stubbing/removing those calls, not a
  clean lift.

---

## Repo 2 — `nickraymond/bm_rpi_camera_module`

Source of the **modular handler / adapter / config patterns** and the capture-result schema.
Note: many files have a large commented-out legacy block at the top; the live code is the
uncommented block at the bottom.

### Reuse for — module structure

| Component | File | Reuse verdict |
|---|---|---|
| Plugin/handler contract: `topics` + `handle(msg, *, ctx)` | `bm_daemon/pluginspec/handler.py`, `handlers/capture_*_cmd.py` | **Adapt** — maps onto our `CameraDevice`/handler rig |
| Command token parsing (`res=…,q=…,burst=…`) | `handlers/capture_image_cmd.py` (L179–214) | **Reference** — informs CLI/USB request parsing |
| Result/status field set (`op,file,res,bytes,idx,burst / dur,fps,br, tx, reason`, kind ∈ ACK/OK/BUSY/ERR) | `handlers/status_util.py`, `capture_*_cmd.py` | **Adapt** — build a structured dict, not the flat text line |
| Still/video capture (Picamera2) | `bm_camera/capture/{image_capture,video_capture}.py` | **Reference only** — we use the rpicam CLI path (Spec §9), not Picamera2 |
| JPEG/HEIF encoders w/ graceful pillow_heif fallback + `-c` suffix naming | `bm_camera/encode/file_encoder.py` | **Port/Adapt** (clean Pillow) |
| In-process camera lock (`threading.Lock`, `CameraLock(timeout_s=…)`) | `bm_camera/utils/camera_lock.py` | **Port** |
| Cross-process camera lock (`fcntl.flock` on lockfile + PID) | `bm_daemon/io/camera_lock.py` | **Port** — stronger guarantee if multiple processes touch the camera |
| Config loader + `resolve_resolution` / `get_camera_defaults` | `bm_daemon/common/config.py` | **Adapt** |
| Rotating-file logging setup | `bm_daemon/common/logging_config.py` | **Port/Adapt** |
| Data-root path helpers (`image_dir`/`video_dir`) | `bm_daemon/common/paths.py` | **Adapt** |

**Capture-result field set (verified):** image → `op="image", file, res, idx, burst, bytes, tx`;
video → `op="video", file, res, dur, fps, br, bytes`; error → `reason=<ExceptionClassName>`.
Wire form is a flat `KIND k=v` line — we will emit a structured dict instead but reuse the
field names.

### Hardware-specific assumptions (bm_rpi_camera_module)

- Picamera2 backend pinned (`picamera2==0.3.12`); `create_still_configuration`,
  `H264Encoder`, libcamera `HorizontalFlip`/`VerticalFlip` controls — Pi-only.
- `resolutions` map and defaults are Pi-camera oriented.
- Two different timestamp formats between the image and video modules (`%Y-%m-%dT%H:%M:%SZ`
  vs compact `%Y%m%dT%H%M%SZ`) — standardize one for our naming module.
- Video FPS is a hint (records by sleeping for `duration_s`), not enforced.

### Do NOT copy (bm_rpi_camera_module)

- `bm_daemon/io/bm_serial.py` (`BristlemouthSerial`: COBS/CRC framing, `spotter_tx`/`spotter_print`).
- `bm_daemon/transport/spotter.py` (base64 chunking, `<START IMG>`/`<END IMG>` framing).
- `bm_daemon/agent/{bus,run,publish,dispatcher,plugin_loader}.py` and `agent/handlers/*`
  (UART bus lifecycle, RTC/clock/Spotter handlers, daemon main loop).
- `status_util.py` as-written and the `send_via_spotter` transport branch of the capture
  handlers (coupled via `ctx["bm"]` / `bm.spotter_print` / `pub_text`).

---

## Repo 3 — `appliedoceansciences/borealis_sbc`

**Lessons only.** No Python. Contents: shell scripts, systemd units, `chrony.conf`,
`gateway.toml`, and a compiled C binary (`cobs_to_shm`) sourced from another repo. It is
Bristlemouth/gpsd/COBS payload-gateway infrastructure for the BOREALIS SBC.

### Reuse for — lessons / reference only

- **Separation of acquisition from processing** (`cobs_to_shm` ingests framed packets to a
  shared-memory ring buffer; other services consume) — the clean-interface-boundary lesson
  the spec calls out.
- **Systemd service boundaries** — one concern per unit; a model for how a future
  production Pi service would be split (not something the MVP builds).
- **Headless Pi bring-up procedure** (`readme.md`) — a reference when writing
  `scripts/install_pi.sh`.

### Do NOT copy (borealis_sbc)

- Everything else: `bm_sbc_gateway.service`, `gpsd.service`, `chrony.conf`, `gateway.toml`,
  COBS/Bristlemouth UART logic, wifi-restore units. All Bristlemouth/field-deployment
  concerns explicitly out of scope (Spec §2).

---

## Dependency implications (verified from source)

Reused code implies these host/Pi dependencies (finalized in `pyproject.toml` during scaffolding):

- `opencv-contrib-python` (ArUco AprilTag detection — **must** be the contrib build).
- `numpy` (corner math, image arrays).
- `Pillow` (crop/downsample, annotation, cut sheets).
- `pillow-heif` (HEIC I/O — replaces the macOS `sips` path).
- `PyYAML` (config).
- `pyserial` (Pi↔OpenMV USB — new code, not from prior art).

`picamera2` is intentionally **not** required: Spec §9 selects the `rpicam-still` CLI path.

---

## Net reuse summary

- **IMX708 capture:** adapt `bm_cam_legacy`'s rpicam CLI capture + control-flag builder +
  metadata parsing; port its crop/HEIC helpers. Strip all BM telemetry/scheduling.
- **Reference-card pipeline:** reuse `bm_reference_card_quality_v2.py` pure functions;
  re-parameterize card geometry; drop the `sips`/cellular HEIC layer.
- **Module skeleton:** adopt `bm_rpi_camera_module`'s handler contract, config/logging/paths
  helpers, encoder, and camera lock; reuse its result field names as a structured dict.
- **OpenMV:** nothing to reuse — build from official docs, log every assumption.
- **borealis_sbc:** lessons only.
