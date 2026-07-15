# Open Questions

Unknowns and unverified assumptions, per CLAUDE.md §32 ("do not make things up") and Spec
§4. Nothing here should be treated as fact until confirmed against official documentation, a
working example, or the hardware itself. Each item lists what we need and when it blocks.

Status: `OPEN` · `NEEDS-HARDWARE` · `NEEDS-DOCS` · `RESOLVED`

---

## OpenMV (highest risk — no prior art exists)

These block Phases 3–4. **No OpenMV API below is verified.** Do not write OpenMV code until
the relevant item is resolved against official OpenMV docs or a working board example.

- **[RESOLVED-N6] OQ-1 — MicroPython camera/snapshot API.** N6 (verified 2026-07-14):
  legacy `sensor` API works (`sensor.reset()` → `set_pixformat` → `set_framesize` →
  `skip_frames` → `snapshot()` → `img.save()`); `csi` (modern OO API) is also present.
  Sensor **PAG7936**; supported framesizes QVGA=320×200, VGA=640×400, **HD=1280×800 (max)**,
  `B320X320` unsupported. The §8 example fields (`framesize:"native"`, `pixel_format:"rgb565"`)
  were illustrative — real values are in `openmv/n6/board_config.py`. *AE3 API to confirm in
  Phase 4.*
- **[RESOLVED-N6] OQ-2 — USB serial transport.** N6 exposes a USB CDC-ACM port (VID:PID
  `37c5:1206`, serial `005537493543`); the host enumerates by VID + serial number, never a
  fixed `ttyACM*` (`host_tools/discover_openmv.py`). The board reads/writes via
  `pyb.USB_VCP`. The AE3 also enumerates (`37c5:16e3`) — both under VID `0x37C5`, so discovery
  requires an explicit serial.
- **[RESOLVED-N6] OQ-3 — File transfer mechanism.** Chose priority-order (1): capture to
  `/flash`, then stream the bytes back over the same CDC serial, length-framed
  (`JSON header → N bytes → JSON completion`, §10). SHA-256 computed on-board and verified
  host-side — round-trips exactly. USB mass-storage is **not** mounted (avoids concurrent-FS
  corruption).
- **[PARTIAL] OQ-4 — Video capture on N6 / AE3.** N6 (2026-07-14): a live JPEG **focus stream**
  is practical (`start_stream` → framed MJPEG, ~29 fps VGA / ~6.5 fps HD; `host_tools/focus_stream.py`).
  Short-clip **video-to-file** is still deferred; the host adapter reports `capture_video` as
  `not_supported`. AE3 unknown (Phase 4).
- **[RESOLVED-N6] OQ-5 — Board firmware versions in hand.** N6: MicroPython **1.26.0**
  (`v1.26.0-77`, 2025-12-22), build `OPENMV_N6`, STM32N657X0. AE3: `OpenMV-AE3`, MicroPython
  **1.25.0-preview** (reported at discovery; full validation in Phase 4).
- **[NEEDS-DOCS] OQ-6 — On-board AprilTag capability.** Whether N6/AE3 can/should run any
  detection on-device, or whether all analysis stays host-side (Pi). Affects nothing in the
  MVP (analysis is host-side) but relevant to the down-select (Spec §17–18).
- **[NEEDS-HARDWARE] OQ-18 — AE3 sensor mount rotation.** The AE3 carries the same PAG7936
  sensor as the N6 but on a different PCB, so its physical mount rotation is not necessarily
  the N6's 90°. The bring-up recon shot (2026-07-15) was a ceiling scene with no reliable
  gravity cue, so `openmv/ae3/board_config.py` sets `MOUNT_ROTATION_DEG = 0` as a placeholder.
  This is metadata only — raw frames are stored un-rotated and it does not affect capture,
  checksums, or the Phase 4 exit criteria — but the down-select side-by-side wants it right.
  Resolve with a known-orientation reference capture; do **not** assume it matches the N6.
- **[DEFERRED] OQ-19 — Firmware update to v5.0.0 + `sensor`→`csi` migration.** Both boards run
  pre-v5.0.0 firmware (N6 MicroPython `1.26.0`; AE3 `1.25.0-preview`). OpenMV **v5.0.0** (2026-07-02)
  takes the N6/AE3 out of beta and lists "Fix Apriltags on the AE3", but bundles MicroPython 1.28
  with API changes: the legacy `sensor` module (used by `openmv/common/capture_service.py`) is
  **deprecated** in favor of a new class-based `csi` module. Decision (owner: Nick, 2026-07-15):
  **defer** — bring-up works today on the current firmware via the legacy `sensor` path, and the
  AE3 flash needs OpenMV IDE + physical access (done on a Windows PC later). When scheduled: flash
  **both** boards to v5.0.0 together, then migrate the shared capture path to `csi` in one
  coordinated PR (re-verify both boards' framesizes/pixformats on the new API — do not assume the
  legacy allowlist carries over). Note the Alif AE3 has **no `pyb`** on any firmware, so the
  `sys.stdin/stdout` USB shim in `openmv/ae3/main.py` stays regardless of firmware.

## IMX708 / Pi capture

- **[RESOLVED] OQ-7 — `rpicam-still` vs `libcamera-still` availability on the target Pi OS.**
  Target Pi (`nereus000`) is a Pi 5 on Debian 13 "trixie". `rpicam-still`/`rpicam-vid` are
  present at `/usr/bin/`; the old `libcamera-still`/`libcamera-vid` names are **absent**
  (dropped in favor of the `rpicam-*` apps). So the adapter uses `rpicam-still`. Verified by
  a live capture on 2026-07-14 (`--metadata` accepted; see OQ-10). Remaining sub-item:
  confirm each control flag (`--awbgains`, `--shutter`, `--gain`, `--autofocus-mode`) is
  accepted when we start passing non-auto controls — deferred until we leave the auto path.
- **[OPEN] OQ-8 — Warm-up timeout.** Prior art hardcodes `--timeout 2000` (2 s). We intend
  to make this configurable (Spec §9 lists warm-up delay as a supported setting). Confirm a
  sensible default and that the flag name is stable across `rpicam`/`libcamera`.
- **[OPEN] OQ-9 — Sensor ROI vs post-capture crop.** Prior art does **not** use libcamera
  `--roi`; it crops in Pillow after a full-frame capture. Decision: MVP will keep the
  post-capture crop (proven). True sensor ROI is deferred (would change capture time/FOV and
  is a rewrite). Recorded so the down-select can revisit.
- **[RESOLVED-APPROACH] OQ-10 — Default IMX708 profile values.** Decision (owner: Nick,
  2026-07-14): the first runs use **full auto** — no `--shutter`/`--gain`/`--awb`/
  `--autofocus-mode` flags, which is exactly the legacy default path (`camera_controls`
  disabled → `_camera_controls_from_settings` returns no control args → libcamera auto).
  Confirmed against the legacy source. A live auto capture on `nereus000` recorded the
  camera's own choices as a baseline: ExposureTime≈13539µs, AnalogueGain≈1.50,
  DigitalGain≈1.00, ColourGains≈[2.47,…], ColourTemperature≈5311K, LensPosition≈3.20
  (AF converged), Lux≈1410. Later phases can pin these as explicit controls if a fixed
  profile is wanted; for bring-up, auto is the tested default.

## Video (Pi)

- **[RESOLVED-CONSTRAINT] OQ-17 — Video codec on the Pi 5.** The Pi 5 has **no
  hardware H.264 encoder**, and `nereus000`'s `rpicam-vid` was built **without libav**
  (`--codec libav` → "Unrecognised codec"; `--codec h264` → "Unable to find an
  appropriate H.264 codec"). Working dependency-free codecs are `mjpeg` and `yuv420`.
  Decision (2026-07-14): Phase 1 video defaults to **MJPEG at 1080p** (verified: a 2 s
  clip produced a valid 1920×1080 motion-JPEG). To get real H.264/MP4, install libav
  encoder support for rpicam (needs sudo) — deferred follow-up; not an MVP blocker
  (Spec §2: "video where practical"). Full-sensor 4608×2592 also exceeds H.264 limits.

## Reference-card pipeline

- **[NEEDS-HARDWARE] OQ-11 — Nereus reference-card geometry.** The reusable code hardcodes
  tag IDs `1,2,3` (+optional `0`) → TR/BL/BR/TL, `expand_quad` factors `x=1.25,y=2.0`, and
  rectified size `1000×420`. Spec §12 config uses `expected_tag_ids:[0,1,2,3]`. Confirm the
  actual card's tag IDs, layout, and physical aspect ratio, then set these as config — do
  not inherit the legacy constants blindly. Blocks Phase 2 correctness on real cards.
- **[OPEN] OQ-12 — Tag-size pass thresholds.** Legacy thresholds are `10 px` (fail) /
  `18 px` (warn), empirically tuned for the IMX708 rig. Re-validate per camera (the OpenMV
  boards have different sensors/resolutions) before using them as a down-select metric.
- **[NEEDS-HARDWARE] OQ-13 — Test fixtures.** Phase 2 needs known reference-card images with
  expected detections (`tests/fixtures/`). None exist yet; must be captured/provided. Not a
  Phase 0 item.

## Cross-cutting / environment

- **[OPEN] OQ-14 — HEIC in the eval rig.** Prior art produces HEIC only for BM transmission.
  For evaluation we default to JPEG (raw evidence) and treat HEIC as optional via
  `pillow_heif`. Confirm whether HEIC output is needed for any down-select metric.
- **[RESOLVED] OQ-15 — Target Pi model / OS version.** `nereus000` = Raspberry Pi 5
  (BCM2712), Debian 13 "trixie", aarch64, Python 3.13, kernel 6.18. More memory/CPU than the
  Pi Zero 2W the prior art was tuned for, so the isolated-subprocess memory workarounds are
  less critical here (keep them anyway — cheap insurance).
- **[OPEN] OQ-16 — `opencv-contrib-python` on the Pi.** ArUco requires the contrib build;
  confirm it installs cleanly on the target Pi OS/arch (wheels availability).

---

*When an item is resolved, change its status to `RESOLVED`, add the source (doc URL, commit,
or test), and — if it changes scope — update the spec in the same PR (CLAUDE.md §27).*
