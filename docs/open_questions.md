# Open Questions

Unknowns and unverified assumptions, per CLAUDE.md §32 ("do not make things up") and Spec
§4. Nothing here should be treated as fact until confirmed against official documentation, a
working example, or the hardware itself. Each item lists what we need and when it blocks.

Status: `OPEN` · `NEEDS-HARDWARE` · `NEEDS-DOCS` · `RESOLVED`

---

## OpenMV (highest risk — no prior art exists)

These block Phases 3–4. **No OpenMV API below is verified.** Do not write OpenMV code until
the relevant item is resolved against official OpenMV docs or a working board example.

- **[NEEDS-DOCS] OQ-1 — MicroPython camera/snapshot API on N6 and AE3.** Exact
  `sensor`/`csi`/`camera` module and snapshot call, supported `framesize`/`pixel_format`
  values, and whether the two boards share one API. The spec's §8 example fields
  (`framesize:"native"`, `pixel_format:"rgb565"`, `jpeg_quality`, `warmup_frames`) are
  *illustrative* and must be checked against the real firmware.
- **[NEEDS-DOCS] OQ-2 — USB serial transport.** Whether the boards expose a USB CDC serial
  port for newline-delimited JSON, and how the host enumerates them by identity (USB
  serial number / VID:PID) rather than `/dev/ttyACM*` (Spec §12). Handshake design depends
  on this.
- **[NEEDS-DOCS] OQ-3 — File transfer mechanism (Spec §10, decide in priority order).**
  Which of (1) capture-to-storage + file access, (2) framed USB binary transfer,
  (3) official OpenMV tooling, (4) raw serial binary is actually reliable per board. Unknown
  until tested. Framing format (`JSON header → length → bytes → JSON completion`) only
  applies if (2)/(4) is chosen.
- **[NEEDS-HARDWARE] OQ-4 — Video capture support on N6 / AE3.** Whether short-clip video is
  practical on each board, in what container/codec. Spec §2 says "video where practical" —
  treat as unknown per board.
- **[NEEDS-HARDWARE] OQ-5 — Board firmware versions in hand.** The `firmware` field in the
  device-info response (§8) needs real values; also whether current firmware matches the docs.
- **[NEEDS-DOCS] OQ-6 — On-board AprilTag capability.** Whether N6/AE3 can/should run any
  detection on-device, or whether all analysis stays host-side (Pi). Affects nothing in the
  MVP (analysis is host-side) but relevant to the down-select (Spec §17–18).

## IMX708 / Pi capture

- **[NEEDS-HARDWARE] OQ-7 — `rpicam-still` vs `libcamera-still` availability on the target
  Pi OS.** The prior art selects via `shutil.which`; confirm which exists on our Pi image
  and that the control flags (`--autofocus-mode`, `--awbgains`, `--shutter`, `--gain`, …)
  are accepted by that build. Blocks Phase 1 exit.
- **[OPEN] OQ-8 — Warm-up timeout.** Prior art hardcodes `--timeout 2000` (2 s). We intend
  to make this configurable (Spec §9 lists warm-up delay as a supported setting). Confirm a
  sensible default and that the flag name is stable across `rpicam`/`libcamera`.
- **[OPEN] OQ-9 — Sensor ROI vs post-capture crop.** Prior art does **not** use libcamera
  `--roi`; it crops in Pillow after a full-frame capture. Decision: MVP will keep the
  post-capture crop (proven). True sensor ROI is deferred (would change capture time/FOV and
  is a rewrite). Recorded so the down-select can revisit.
- **[NEEDS-HARDWARE] OQ-10 — Default IMX708 profile values.** Spec §9 requires defaults from
  *tested* profiles, not invented. The shipped device profiles (`bmcam001/bmcam002`) use the
  legacy Picamera2 path and do **not** populate `image_pipeline`/`camera_controls`. We need
  a known-good `rpicam` control set (exposure/gain/WB/focus) for the eval rig — to be
  captured from a real run, not guessed. Blocks Phase 1 "defaults derived from tested profiles."

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
- **[NEEDS-HARDWARE] OQ-15 — Target Pi model / OS version.** Affects camera-stack commands,
  available memory (the prior art is tuned for Pi Zero 2W memory pressure), and USB behavior.
- **[OPEN] OQ-16 — `opencv-contrib-python` on the Pi.** ArUco requires the contrib build;
  confirm it installs cleanly on the target Pi OS/arch (wheels availability).

---

*When an item is resolved, change its status to `RESOLVED`, add the source (doc URL, commit,
or test), and — if it changes scope — update the spec in the same PR (CLAUDE.md §27).*
