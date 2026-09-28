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
- **[NEEDS-HARDWARE] OQ-20 — AE3 stale-AWB green cast: physical lights-off re-verification.**
  Observed 2026-07-16: after lights-off runs the AE3 produced a strong green cast (grey ΔE
  39.7, green-excess ~35 vs normal ~3) on *every* subsequent capture until a manual
  `mpremote reset`; the N6 recovered on its own. Hardware recon (2026-07-17): the PAG7936 is
  corrected by a **firmware software AWB** — the N6 (fw 1.26.0) exposes its gains
  (`get_rgb_gain_db()` ≈ R +4.9 dB / G 0 / B +4.8 dB, i.e. compensating native Bayer green
  dominance) and reconverges per frame, while the AE3 (fw 1.25.0-preview) exposes **no AWB
  control at all** (`set_auto_whitebal`/`get_rgb_gain_db` → "not supported"), so when its AWB
  state goes stale nothing callable from MicroPython can fix it short of `machine.reset()`.
  The fix (`reset_board` command + coordinator `reset_before_capture`) automates the exact
  manual remedy that cured the field failure, and was verified end-to-end against a
  software-poisoned sensor (stuck auto-exposure surviving autos+2 s settle; post-reset
  green-excess 0.2–2.8, well under the <8 gate). The *true* lights-off green cast could not
  be reproduced remotely (needs physically dark scene for minutes) — re-run the real
  scenario once someone is at the rig: lights-off experiment → lights-on experiment, confirm
  the lights-on captures have green-excess < 8 with no manual reset. Also note the OQ-19
  v5.0.0 firmware exposes AWB controls on the AE3 line and may make it self-recover like the
  N6 — re-test after that update.

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
  *Update 2026-09-26:* `pillow-heif` was dropped from the `[analysis]` extra — nothing
  imported it (the planned HEIC-encode port was never built) and its wheels carry a GPLv2
  classifier (SPEC §20 licence policy). If HEIC is needed later, add it to an internal-only
  extra with a `configs/licenses.yaml` review; it must not enter the shipped closure.
- **[RESOLVED] OQ-15 — Target Pi model / OS version.** `nereus000` = Raspberry Pi 5
  (BCM2712), Debian 13 "trixie", aarch64, Python 3.13, kernel 6.18. More memory/CPU than the
  Pi Zero 2W the prior art was tuned for, so the isolated-subprocess memory workarounds are
  less critical here (keep them anyway — cheap insurance).
- **[OPEN] OQ-16 — `opencv-contrib-python` on the Pi.** ArUco requires the contrib build;
  confirm it installs cleanly on the target Pi OS/arch (wheels availability).

## Phase 8 — Edge color correction

From the design brief §11 (`docs/DESIGN_edge_color_correction.md`) plus gaps found while
planning (2026-09-26). "Blocks" names the Phase 8 sprint (SPEC §4) that needs the answer.
Items for Nick are marked **(Nick)**.

- **[PARTIAL] OQ-21 — OpenMV Bayer (RAW) support, bit depth, WB.** *Blocks S3.* What the
  repo already tells us:
  - **AE3:** `sensor.BAYER` was accepted by `set_pixformat` during the 2026-07-15 bring-up
    probe (fw 1.25.0-preview), recorded in the `PIXEL_FORMATS` comment in
    `openmv/ae3/board_config.py`. It was left out of the allowlist; no Bayer frame content
    was inspected.
  - **N6:** never probed. `openmv/n6/board_config.py` allowlists only RGB565/GRAYSCALE and
    records no BAYER test.
  - **No existing command can return Bayer.** `capture_service.capture_image` always writes
    with `img.save(path, quality=...)` (JPEG) and reports `"format": "jpeg"`. Bayer needs a
    new allowlisted `capture_raw` that writes raw bytes.
  - **Storage:** an HD (1280×800) Bayer frame is 1,024,000 B at 8 bit/px, 2,048,000 B in a
    16-bit container. The N6 flash is only 4,169,728 B in total (measured in PR #16's description, not recorded in a repo file), so one raw frame fits
    only because `delete_file` cleans up after each retrieval. Streaming from RAM instead of
    via `/flash` may be needed.
  - Still unknown (needs hardware): Bayer bit depth, Bayer resolutions, whether N6 AWB gains
    apply to Bayer output or can be set neutral, and whether any of this needs the OQ-19
    v5.0.0 / `csi` migration. Decide together with OQ-19 in S3.
- **[NEEDS-HARDWARE] OQ-22 — N6 RAW + ISP JPEG from the same exposure.** *Blocks S3.* Not
  answerable from code: the current path configures one pixformat and takes one
  `snapshot()`. If one exposure can't give both, capture back-to-back and record it
  (brief §7 P0).
- **[NEEDS-HARDWARE] OQ-23 — USB transfer time for a raw frame.** *Blocks S3 (soak cadence
  in S7).* Transfer is not timed separately, but the host's capture `duration_seconds`
  includes `_retrieve_file` (`cameras/openmv_usb.py`), and `TRANSFER_TIMEOUT = 30.0` s bounds
  it — a 2 MB frame must finish inside that. Expected payload is 1.0 MB (8-bit) or 2.0 MB (16-bit) per HD frame over
  the existing length-framed path (512 B board-side chunks, SHA-256 verified). Measure on
  both boards; log it in `capture.json`.
- **[NEEDS-HARDWARE] OQ-24 — `rpicam-still --raw` on the Pi 5.** *Blocks S3; the S0 DNG
  demo needs one sample.* Does it write a DNG from the same frame as the JPEG, and do the
  DNG's tags match the exposure/gains in `--metadata`? The adapter never passes `--raw`
  today (`cameras/imx708.py` builds `--width/--height/--metadata` + optional controls), but
  it already records ExposureTime / AnalogueGain / ColourGains from `--metadata` (OQ-10),
  so there is a ready comparison. Also unverified: that `tifffile` reads the rpicam DNG.
  For S0: one manual `rpicam-still --raw` capture on `nereus000` (no code change) gives the
  sample; the full answer is S3.
- **[NEEDS-HARDWARE] OQ-25 — Pi 5 processing time for full-res IMX708 RAW through physics
  v0.** *Informational, S3+.* Measure once `color/pipeline.py` exists. Fallback if too slow:
  the 2304×1296 binned sensor mode (brief §10).
- **[OPEN] OQ-26 — Pool housing plan (Nick).** *Blocks S6.* All cameras + Pi in one housing,
  or cameras housed and cabled to a dry poolside Pi?
- **[OPEN] OQ-27 — X-Rite ColorChecker Classic available? (Nick).** *S2b and S4.* Enables
  card-truth Option B (brief §5.3). More valuable than before: the V2 card is water-damaged,
  so a reshoot of it gives an unreliable daylight reference for its light patches. Not a
  blocker for the S2a gate (design values + the TG-7's embedded colour matrix).
- **[OPEN] OQ-28 — `bmcam001` field JPEG quality rung + retained metadata (Nick).**
  *Blocks S5.* Which rung of the 90…9 ladder do field images usually land on (backend logs)?
  Is any EXIF (colour gains, exposure) kept on the stored originals?
- **[OPEN] OQ-29 — TG-7 above-water / checkerboard reshoot (Nick).** *Blocks S2b, not the
  S2a gate* (decision, Nick 2026-09-26: gate first, then measure what the reshoot adds). When can
  the brief §7 P1.4 reshoot happen (~1 h)? Was the flat front glass the only port on the
  trip (no dome / wet lens)? New shots go in new folders
  (`raw/5_above_water_calibration/`, `raw/6_pool_distance_check/`) — the tool picks them up
  by folder; nothing already in the dataset is touched.
- **[OPEN] OQ-30 — Leak sensor for the soak? (Nick).** *Blocks S7.*
- **[RESOLVED] OQ-31 — Backend color correction as an S2a baseline.** *Blocks S2a.*
  (The brief §1a cites "OQ 6" for this, but its §11 item 6 is the pool-housing question; this
  item replaces that reference.) Found by reading `nereus-vision-dev` (local checkout on
  `staging`, 2026-09-26): the backend's only image filter is **GRVI**, processor
  `cheeca_v3`, in `backend/app/services/processing/grvi.py`.
  `correct(image_rgb, profile, layout)` takes an RGB uint8 frame and returns a tuple
  `(corrected_rgb, sidecar)`; `corrected_rgb` is `None` when no card is found. It wraps a byte-identical vendored
  copy of `bm_cam_legacy` (commit `8b7cf97`) with profile `profiles/cheeca_v3.json`. Deps:
  cv2, numpy, scipy, Pillow. Production detects AprilTags at native scale only (overridable by the
  `PROCESSING_DETECT_SCALES` env var — record that it is unset) and needs ≥ 3 of 4 tags.
  scipy is not in the rig venv, so it runs in the backend's own environment. **Plan for
  S2a:** call it unmodified from a local checkout (path in config, Mac-side only, commit SHA
  pinned in the report) on the TG-7 camera JPEGs, scored with the SPEC §20 protocol. Its
  output targets a reef look (e.g. linear white target [0.71, 1.05, 1.34]), which is not
  neutral: under the realistic-color product intent (Nick, 2026-09-26) that is a fair
  finding, not a scoring artefact. Consequences to
  report, not work around: GRVI is card-anchored with no table mode, so on no-card or
  unlocated frames the baseline is "no correction"; its water prior is a display prior, not
  colorimetry — score it as production runs it. Its card truth (`profiles/template_layout_v2.json`,
  SVG design fills) becomes the V2 truth in `configs/cards/nereus_v2.yaml`. Remaining for Nick: confirm `staging` (vs
  `main`) is the right baseline branch.
  **Run 2026-09-27 (`grvi` stage, SPEC §4 S2a):** backend `03272be` (origin/staging), exported
  with `git archive`, run in `.venv-grvi` (`make grvi-env`: the backend's own pins, numpy 2.5.1,
  OpenCV 5.0.0, scipy 1.18.1, Pillow 12.3.0), `PROCESSING_DETECT_SCALES` unset (native only).
  GRVI found the card on **128 of 269** located TG-7 frames (the rig's RAW-first locate + clicks:
  270). On the 118 scored frames where it found the card: ΔE00 median **34.4** vs 39.6 camera
  JPEG and **18.1 RAW + card WB** (RAW + card WB better on 118/118). Its `cheeca_v3` render
  targets sit ΔE00 12–25 from the V2 design values (grey 128 itself 12.4, a warm tint), so part
  of the gap is the look by design; the rest is its JPEG input (red already clipped to 0 under
  water) — deep frames come out washed-out cyan. Near the surface it is close (ΔE00 ~15).
- **[RESOLVED] OQ-32 — Physical V2 card dimensions (for distance `z`).** Measured from the
  vector print master (Nick's direction, 2026-09-26) by reading the PDF drawing operators:
  tag-centre spacing **364.900 × 91.566 mm**, tag edge **31.980 mm**, card **410.000 ×
  136.667 mm** (the PDF is drawn at true scale). Recorded in `configs/cards/nereus_v2.yaml`;
  derivation in `docs/reference_card_v2.md`. Two notes: the printed "0–300 mm" bar is
  298.96 mm in the vector file with uneven ticks — never use it for scale; and the values
  assume the card was printed at 100 % (a tape check of the 410 mm width would confirm).
- **[RESOLVED-DECISION] OQ-33 — Licence policy.** Decision (Nick, 2026-09-26), recorded in
  SPEC §20: shipped and hosted code is permissive-only (MIT / BSD / Apache-2.0 and
  equivalents such as Pillow's `MIT-CMU`); no GPL / LGPL / AGPL, including native libraries
  bundled in wheels; LGPL tools only in internal Mac tools. Evidence behind it (2026-09-26):
  - Pillow declares `MIT-CMU`; numpy a compound SPDX expression (`BSD-3-Clause AND 0BSD AND
    MIT AND Zlib AND CC0-1.0`).
  - `pillow-heif` declares BSD-3-Clause but carries a GPLv2 classifier and is imported
    nowhere in `src/`, `host_tools/` or `tests/` → dropped from `[analysis]`.
  - `rawpy` declares **MIT** on PyPI; its LGPL part is the bundled LibRaw — so metadata alone
    can't enforce the rule.
  - The installed `opencv-contrib-python-headless` 5.0.0.93 wheel declares Apache 2.0 but
    bundles `libavcodec`/`libavformat`/`libswscale` (FFmpeg, LGPL) and `libx264`, `libx265`,
    `libpostproc` (GPL) in `cv2/.dylibs` (checked in its RECORD) → OQ-36.
  - Enforcement: a reviewed `configs/licenses.yaml` table + a test (not SPDX parsing of
    metadata). No CI exists; the test runs under `make test`; a CI workflow is a later PR.
  Not legal advice — counsel review before the first commercial device ships.
- **[RESOLVED] OQ-34 — Does `rawpy`'s bundled LibRaw decode TG-7 ORF?** **Yes** — rawpy
  0.27.1 / LibRaw 0.22.1 decodes all 308 TG-7 ORFs (2026-09-26, S0.5), agreeing with exiftool
  on CFA (GRBG), per-channel black level (Olympus `BlackLevel2` order is R, G1, G2, B), white
  level 4095 and as-shot WB on every file. The DNG Converter fallback is not needed. Original
  question: *Blocks S0 ORF reader.*
  The TG-7 (2023) is newer than some LibRaw releases. Verify on one ORF in the S0 nibble;
  fallback is Adobe DNG Converter → DNG. Caveat: the converter's default output is (from
  memory, unverified) lossless-JPEG-compressed, which `tifffile` decodes only with
  `imagecodecs` — check its compression before relying on the fallback. (`read_dng`, S0.4,
  rejects compressed CFA data with a clear error rather than half-reading it.) From exiftool
  (2026-09-26, all 308 ORFs): 4040×3016, CFA GRBG, 12-bit, black level per frame and
  channel (256–260) — the decoded frame must agree.
- **[RESOLVED] OQ-35 — How many TG-7 dives: 4 or 5?** **Four** (Nick, 2026-09-26): dives 1–2
  at Santa Cruz Island, 3–4 at Anacapa — recorded with UTC windows in
  `configs/datasets/tg7_channel_islands.yaml`. Original analysis: *Blocks S1 `dive_id`, S2a
  leave-one-dive-out.* The brief (§7 P1.0) says "5 dives", but splitting the 309 shots at
  gaps > 45 min (camera-local time from `manifest.csv`, checked 2026-09-26) gives **4**
  groups, and the brief's own sun-angle list names four (Sep 15 evening; Sep 16 early
  morning, mid-morning, midday):

  | Group | Camera-local time | Shots | Depth (m) | Reference frames |
  |---|---|---|---|---|
  | 1 | Sep 15 17:32–18:30 | 86 | 0.1–15.6 | 47 |
  | 2 | Sep 16 07:50–08:30 | 44 | 8.3–16.6 | 38 |
  | 3 | Sep 16 09:59–10:52 | 97 | 0.3–11.4 | 63 |
  | 4 | Sep 16 12:15–13:04 | 82 | 0.1–16.6 | 58 |

  The largest in-dive gap is 12.8 min (Sep 15 18:16 → 18:29, 5.1 → 8.4 m). If one of these
  groups is really two dives, say where to split. Checked: every threshold from 15 to 60 min
  gives the same 4 groups, and no group returns to ~0 m mid-dive. The only 5th-dive
  candidate is the 6-frame tail P9150424–P9150429 at 8.4 m after that 12.8 min gap (it
  splits off only at a 10 min threshold). Plan: `ingest` assigns `dive_id` by time
  gap, with an optional per-dataset override file (in `results/`, not in the dataset).
- **[NEEDS-VERIFICATION] OQ-36 — A GPL-free OpenCV build for shipped installs.** *Blocks
  S0 licence item.* The PyPI OpenCV wheels bundle FFmpeg + GPL codecs (OQ-33). The color
  pipeline needs only core / imgproc / calib3d / objdetect (ArUco AprilTag) — no video.
  Options to verify: (a) build OpenCV from source with FFmpeg/video I/O off
  (`-DWITH_FFMPEG=OFF` etc.) for the Pi and backend images; (b) a distro package, only if
  its linked libraries check out. Each option must still import on the Pi 5 (aarch64,
  Python 3.13) and keep `DICT_APRILTAG_36h11`. Mac analysis tools may keep the PyPI wheel
  (internal use). The acceptance test exists: `make license-check-shipped` runs the
  `cv2_without_ffmpeg` probe (`cv2.getBuildInformation()` must not report `FFMPEG: YES`);
  on the Mac PyPI wheel it fails as expected. Also pending: run it on the Pi to review what
  the Linux numpy wheel bundles (OpenBLAS / gfortran runtime) — blocked 2026-09-26 by an
  SSH host-key mismatch for `nereus000` (not bypassed; owner to confirm the key).
- **[OPEN] OQ-37 — TG-7 `ShadingCompensation2: On`.** *S2a.* The ORFs report
  `ShadingCompensation: Off` but `ShadingCompensation2: On`. Unverified whether the camera
  JPEG is shading- (vignetting-) corrected while the RAW is not. Matters for comparing
  RAW-based correction against JPEG-based baselines near the frame edge; until a flat-field
  exists (S2b), fits stay within ~0.6 of the image half-diagonal (SPEC §4 S2a).
- **[RESOLVED] OQ-38 — Dive site coordinates.** No GPS was logged (Nick, 2026-09-26): dives 1–2
  were at **Santa Cruz Island**, 3–4 at **Anacapa Island**. Island-level coordinates
  (Santa Cruz ≈ 34.00° N, 119.75° W; Anacapa ≈ 34.01° N, 119.40° W) are in
  `configs/datasets/tg7_channel_islands.yaml`, used only for sun elevation — ±0.2° in position
  moves solar elevation by ≲0.3°, small next to the light changes being modelled.

- **[OPEN] OQ-39 — TG-7 colour matrix target space and the in-water colour floor.** *S2b.* The
  Olympus `ColorMatrix` (/256) is applied after white balance as camera → linear sRGB, as LibRaw
  does. Verified 2026-09-27 (colour review): the order is right, but every RAW method bottoms out
  at ΔE00 ≈ 19 under water (≈ 10 in air) — chroma loss under blue-green light that no single
  daylight matrix fixes (a matrix fitted in air: 6.4 in air, 19.2 under water). Next step: a
  light-dependent matrix fitted on the card per depth, validated leave-one-sweep/dive-out.
  **v0.3 tried (2026-09-27, SPEC §4 S2a):** a depth-dependent matrix fitted on the card's colour
  patches, leave-one-dive-out, brings card-anchored ΔE00 from 19.6 to 13.1 (100 % of frames), so
  most of the floor is the matrix, not the data. Remaining: blue / magenta stay at ~20; without a
  card the matrix amplifies white-balance error (ψ 9.8° → 19.1°); whether a matrix fitted on
  printed patches is right for scene colours (it warms rocks and algae strongly) needs the
  measured card and scene references of the V3 dive (OQ-40).
- **[OPEN] OQ-40 — True printed values of the reference card.** *S2b, V3 dive.* The V2 print is
  not its design: after white balance the grey ramp reads white 0.78, grey 200 0.47, grey 74
  0.114 (design 1, 0.578, 0.068), colours come out lighter (median ΔL* +5), and the black patch's
  reflectance is uncertain (0.027 on one near-surface frame, ≈ 0.076 on deep frames). Until the
  card is measured (daylight reference shots, X-Rite, OQ-27), ΔE00 is provisional and haze cannot
  be separated from print non-linearity. The V3 dive plan includes the dry reference shots.
  **Measured 2026-09-27 (in air, 3 deck frames, WB on grey, camera daylight matrix):** the print is
  far less saturated than the design file — yellow / orange / red-orange 30–40 C\* lower, earth
  tones 15–30. A colour matrix fitted to the design values adds that chroma back to the whole
  scene (the v0.3 "yellow cast" Nick rejected in the blind review); fitted to the in-air reading
  it does not. Until the card is measured with an instrument, the in-air reading is the better
  truth for fitting; the V3 dive's dry reference shots are the minimum, a spectro reading better.
- **[OPEN] OQ-41 — Light changes between frames.** *S2a fit.* Exposure-normalized brightness jumps
  up to 3× between consecutive frames on shallow sunny dives (caustics), and dives 1–2 drift
  through the dive (sunset, morning). The colour of the light is stable enough to use (ratios
  cancel it); absolute light levels are not transferable between frames or dives.

- **[RESOLVED] OQ-42 — The TG-7 JPEG is radially remapped from the RAW.** *S2a (JPEG baselines).*
  Found by Nick on the v0.2 cut sheet. Measured 2026-09-27 with the `jpeg-map` stage (AprilTag
  centres found separately on RAW and JPEG, 548 pairs on 156 frames): the JPEG is a fixed radial
  remap of the RAW, `jpeg = c_j + (raw − c_r)·(k0 + k1ρ² + k2ρ⁴)`, ρ = r/1000 px — median error
  0.33 px, max 2.8 px, the same map on every dive (leave-one-dive-out max 3.9 px); a point moves
  out by ~16 px at 950 px radius and ~130 px at 1800 px. EXIF says `DistortionCorrection: Off`
  and the maker notes carry no parameters (exiftool 13.55). **Which image is rectilinear depends
  on the medium:** a 16-corner card homography fits the RAW to ~1 px RMS under water but the JPEG
  to ~13 px on wide cards; in air (3 deck frames) the JPEG fits to 0.6–1.0 px and the RAW to up to
  11 px. So the camera applies an in-air lens correction to every JPEG, and under water the flat
  port's refraction cancels the lens barrel, leaving the RAW near-projective. **Fix (SPEC §4 S2a):**
  the map is dataset config (`jpeg_from_raw`); `locate` maps quads RAW → JPEG and JPEG-found tags
  back to RAW; `patches` samples the JPEG on the RAW card area through the map. Two more bugs found
  on the way: OpenCV applied EXIF Orientation to 2 card frames (P9150342, P9160565 are stored
  rotated 90°) — JPEGs are now read in stored orientation; and 5 frames whose missing tags came
  from the JPEG had RAW quads up to 37 px off. **Still unknown:** P9160648's JPEG is shifted 17 px
  from its RAW as a whole (preset mode, 1/30 s, sensor-shift IS on) — excluded from JPEG scoring
  (`exclude_frames`); whether the RAW needs a distortion model in air is left to the S2b
  checkerboards.
---

*When an item is resolved, change its status to `RESOLVED`, add the source (doc URL, commit,
or test), and — if it changes scope — update the spec in the same PR (CLAUDE.md §27).*
