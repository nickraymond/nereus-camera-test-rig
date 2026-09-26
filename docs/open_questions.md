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
    16-bit container. The N6 flash is only ~4.2 MB in total (PR #16), so one raw frame fits
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
  in S7).* No throughput has been measured on either board (nothing in the repo records
  transfer timing). Expected payload is 1.0 MB (8-bit) or 2.0 MB (16-bit) per HD frame over
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
- **[OPEN] OQ-27 — X-Rite ColorChecker Classic available? (Nick).** *Nice-to-have for S2
  (P1.4 row 6) and S4.* Enables card-truth Option B (brief §5.3). Not a blocker: design
  values (Option A) are used until then, and the water layer uses camera-relative
  normalization.
- **[OPEN] OQ-28 — `bmcam001` field JPEG quality rung + retained metadata (Nick).**
  *Blocks S5.* Which rung of the 90…9 ladder do field images usually land on (backend logs)?
  Is any EXIF (colour gains, exposure) kept on the stored originals?
- **[OPEN] OQ-29 — TG-7 above-water / checkerboard reshoot (Nick).** *Blocks S2.* When can
  the brief §7 P1.4 reshoot happen (~1 h)? Was the flat front glass the only port on the
  trip (no dome / wet lens)? New shots go in new folders
  (`raw/5_above_water_calibration/`, `raw/6_pool_distance_check/`) — the tool picks them up
  by folder; nothing already in the dataset is touched.
- **[OPEN] OQ-30 — Leak sensor for the soak? (Nick).** *Blocks S7.*
- **[RESOLVED-LOCATION] OQ-31 — Backend color correction as the S2 baseline.** *Blocks S2.*
  (The brief §1a cites "OQ 6" for this, but its §11 item 6 is the pool-housing question; this
  item replaces that reference.) Found by reading `nereus-vision-dev` (local checkout on
  `staging`, 2026-09-26): the backend's only image filter is **GRVI**, processor
  `cheeca_v3`, in `backend/app/services/processing/grvi.py`.
  `correct(image_rgb, profile, layout)` takes an RGB uint8 frame and returns the corrected
  frame plus a sidecar, or `None` when no card is found. It wraps a byte-identical vendored
  copy of `bm_cam_legacy` (commit `8b7cf97`) with profile `profiles/cheeca_v3.json`. Deps:
  cv2, numpy, scipy, Pillow. Production detects AprilTags at native scale only and needs
  ≥ 3 of 4 tags. **Plan for S2:** call it unmodified from a local checkout (path in config,
  Mac-side only, commit SHA pinned in the report) on the TG-7 camera JPEGs. Consequences to
  report, not work around: GRVI is card-anchored with no table mode, so on no-card or
  unlocated frames the baseline is "no correction"; its water prior is a display prior, not
  colorimetry — score it as production runs it. Remaining for Nick: confirm `staging` (vs
  `main`) is the right baseline branch.
- **[PARTIAL] OQ-32 — Physical V2 card dimensions (for distance `z`).** *Blocks S1 distance.*
  The print master is 11×17 in with bleed (`tests/fixtures/reference_card/README.md`). In the
  3000×1941 px render (= the full 17×11 in page) the tag centers are 2535 px apart
  horizontally and 636 px vertically, i.e. ≈ 365 × 92 mm **if the card was printed at 100 %
  scale** (no fit-to-page, no trim beyond the bleed) — an assumption until measured. The card also carries a 0–300 mm scale bar. **Ask (Nick):** tape-measure
  the tag-center spacing (horizontal and vertical) and one tag's edge length on the physical
  card → `configs/cards/nereus_v2.yaml`.
- **[OPEN] OQ-33 — Licence allowlist details (Nick).** *Blocks S0 license check.* Installed
  package metadata (checked 2026-09-26 in the Mac `.venv`):
  - Pillow declares **`MIT-CMU`** (an HPND-family permissive license), not literally
    MIT/BSD/Apache. It is already a runtime dependency (`analysis`, `web` extras) and the
    brief suggests it for L3 JPEG.
  - numpy declares a compound SPDX expression (`BSD-3-Clause AND 0BSD AND MIT AND Zlib AND
    CC0-1.0`) — the check must parse SPDX expressions, not match one string.
  - **`pillow-heif` (in the existing `analysis` extra) declares `BSD-3-Clause` but carries a
    `GPLv2` classifier** — its wheels bundle GPL/LGPL HEIF codecs. So the `[color]` extra must
    list its own dependencies and **not** pull in `[analysis]` wholesale. Whether the
    existing `analysis` extra should drop `pillow-heif` (HEIC is optional, OQ-14) is a
    separate decision.
  - Proposal: allowlist MIT, MIT-CMU/HPND, BSD-0/2/3-Clause, Apache-2.0, ISC, PSF, Zlib and
    CC0-1.0. Otherwise `color/` stays Pillow-free and uses `cv2.imencode`.
  - The repo has **no CI** (no `.github/`), so the "license check in CI" runs as a pytest
    test via `make test` until a CI workflow is added as its own PR — does Nick want that PR?
- **[OPEN] OQ-34 — Does `rawpy`'s bundled LibRaw decode TG-7 ORF?** *Blocks S0 ORF reader.*
  The TG-7 (2023) is newer than some LibRaw releases. Verify on one ORF in the S0 nibble;
  fallback is Adobe DNG Converter → DNG (then the shipped `tifffile` reader applies). Note
  from exiftool (2026-09-26): ORF is 4040×3016, 16-bit container, CFA GRBG, black level 257
  — the decoded frame must agree.
- **[OPEN] OQ-35 — How many TG-7 dives: 4 or 5? (Nick).** *Blocks S1 `dive_id`, S2
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
  groups is really two dives, say where to split. Plan: `ingest` assigns `dive_id` by time
  gap, with an optional per-dataset override file (in `results/`, not in the dataset).

---

*When an item is resolved, change its status to `RESOLVED`, add the source (doc URL, commit,
or test), and — if it changes scope — update the spec in the same PR (CLAUDE.md §27).*
