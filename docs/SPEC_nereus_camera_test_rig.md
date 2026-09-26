# Nereus Multi-Camera Test Rig
## Specification & Build Plan

**Status:** Development MVP
**Repo:** `nereus-camera-test-rig`
**Controller:** Raspberry Pi
**Candidate cameras:** Raspberry Pi IMX708, OpenMV N6, OpenMV AE3
**Purpose:** Controlled above-water and underwater camera hardware comparison to support a future down-select.

This is an **evaluation platform**, not a production runtime. No Bristlemouth, Spotter, cellular, or field-deployment code (see §2).

---

## How to Use This Spec

This document is both the source of truth and a live progress tracker.

- Work the **Build Plan (§4)** top to bottom. Take the first unchecked item, implement it on its own branch, open a PR, and mark it done **in the same PR**.
- Status markers: `- [ ]` todo · `- [~]` in progress · `- [x]` done.
- If reality diverges from the spec, fix the spec in the same PR — do not silently expand scope. Record unknowns in `docs/open_questions.md` instead of inventing behavior.
- Phases 5+ are **reference detail** for the Build Plan. Read the phase you're on; don't re-read everything each session.

---

## 1. Objective

Build a rig where a Raspberry Pi is the central coordinator and:

- captures stills and short video from the local IMX708 (CSI);
- sends simple USB commands to the OpenMV N6 and AE3 and collects their output files;
- organizes captures into timestamped experiment folders with full metadata;
- runs the existing AprilTag / reference-card detection + crop pipeline on stills;
- serves a simple local web UI to review and download results.

**Primary success metric:** *Can each camera reliably capture an image in which the reference card and AprilTags can be detected, localized, and cropped?* Inference and biological analysis come later.

**Evaluation targets (informing the down-select):** fish detection/counting, biofouling, underwater vehicle detection, coral bleaching, general object detection, small-object inference (e.g. purple balls), and image-quality characterization (color, contrast, sharpness, low light, above vs. underwater).

---

## 2. Scope

### In scope (MVP)
- Pi controller app; IMX708 still + short-video capture.
- USB command interface + still capture on N6 and AE3; video where practical; file collection.
- Timestamped experiment folders, per-capture metadata, per-camera config.
- Sequential three-camera capture (~1–2 s spread is fine); manual CLI capture and web-triggered capture.
- Reference-card + AprilTag detection, card crop, result summaries, logs.
- Graceful partial operation when a camera is disconnected.
- Mac-hosted test/analysis tools.

### In scope (Phase 8 — added 2026-09-26)
- **Automatic underwater color correction** ("Nereus physics v0"), as Phase 8 (§4, §20). Built first as a Mac-side, re-runnable tool on the TG-7 Channel Islands RAW dataset; rig work (RAW capture, calibration, pool sweep, soak) follows **only if** the S2 decision gate says go. It lives in its own `color/` module and must not destabilize the Phase 0–6 capture path.
- The Phase 8 pool soak (P5) runs as a **simple foreground experiment loop** (`nereus-rig soak`: capture → correct → append metrics, sleep, repeat). It is **not** a daemon: no service unit, watchdog, auto-restart or production scheduler.

### Out of scope (do not build)
Bristlemouth UART/transport, Spotter integration/time sync, cellular/cloud upload, production scheduling or daemon/watchdog (the Phase 8 soak loop above is not one), auth, remote firmware update, HEIC transmission, full fish-counting or coral-bleaching pipelines, production model training, cross-camera geometric calibration, hardware-trigger sync. From Phase 8: color correction running on the N6/AE3 themselves, OpenMV ISP register programming, neural-network parameter prediction (log its training inputs only), per-pixel monocular depth maps (stretch goal only).

Create *extension points* for future analysis; do not implement speculative complexity.

---

## 3. Prior Art

Inspect these before writing new code. Reuse proven capture/analysis logic; do **not** copy production/BM/Spotter structure.

| Repo | Reuse for | Do not copy |
|------|-----------|-------------|
| `github.com/nickraymond/bm_cam_legacy` | IMX708 capture, crop/resize, JPEG/HEIC, device profiles, AprilTag/reference-card tools, cut-sheet gen, config-driven capture, logging/locking | Production deployment structure, BM-specific behavior |
| `github.com/nickraymond/bm_rpi_camera_module` | Modular camera handlers, image/video command patterns, adapter concepts, status responses | BM daemon architecture |
| `github.com/appliedoceansciences/borealis_sbc` | *Lessons only:* separating acquisition from processing, clear interface boundaries, testable modules | BM serial functionality |
| `bm_cam_legacy/device_profiles/bmcam001/camera_schedule.yaml` | *Phase 8:* the `bmcam001` field recipe, replicated as Arm A of the rig A/B (design brief §9) | — |
| `nereus-vision-dev` `backend/app/services/processing/grvi.py` | *Phase 8:* the current backend correction (GRVI, processor `cheeca_v3`) as the **S2 baseline**, called unmodified from a local checkout on the Mac (OQ-31) | Do not vendor, port or re-tune it here |

---

## 4. Build Plan

Ordered deliverables. Each phase ends with **Exit criteria** that must pass before moving on. Detailed specs are referenced per item.

### Phase 0 — Prior-art review & scaffolding
- [x] Inspect the three repos (§3); write `docs/prior_art_review.md` listing reusable files, hardware-specific assumptions, and code that must NOT be copied.
- [x] Write `docs/implementation_plan.md` naming exact modules to port / adapt / rewrite.
- [x] Scaffold the repo structure (§6) with placeholder interfaces and empty tests.
- [x] Config loader, common data models, CLI skeleton, logging, `pytest` set up (§5, §11).
- [x] Dev install instructions (`pyproject.toml`, `requirements-dev.txt`, `Makefile`).
- **Exit:** Mac-side unit tests run green on an otherwise empty rig. No hardware APIs implemented yet.

### Phase 1 — IMX708 baseline
- [x] Pi camera discovery; detect `rpicam-still`/`rpicam-vid` (fallback `libcamera-*`), clear error if absent (§9).
- [x] Still + short-video capture; configurable resolution, JPEG quality, exposure, gain, white balance, focus, warm-up, timeout. *(Video is MJPEG @1080p on the Pi 5 — no H.264 encoder, see OQ-17. Crop/resize moved to Phase 2, where the card crop lives.)*
- [x] Defaults derived from tested Pi profiles (full-auto exposure/WB/focus per OQ-10); timestamped output + metadata; CLI capture command.
- **Exit:** `./scripts/test_imx708.sh` produces a valid image + metadata; output validated (file exists, plausible size, opens, correct dimensions). ✅ verified on `nereus000`.

### Phase 2 — Reference-card pipeline
- [x] Port AprilTag detection; report tag IDs + corners. *(OpenCV ArUco `DICT_APRILTAG_36h11`, multi-scale.)*
- [x] Card localization from configured tags, optional rectify, crop, annotated image, machine-readable JSON (§13). *(V2 geometry: tag map `tl:0,tr:1,bl:2,br:3`, expand 1.25/2.0, canonical rectify 3000×1000.)*
- [ ] Still crop/resize helper (Pillow) — capture-side helper (not the card crop, which is done via OpenCV in `analysis/crop.py`). Remains for the capture owner; not an analysis exit blocker.
- [x] Tests against known fixture images with expected detections. *(Real V2 card fixture in `tests/fixtures/reference_card/`.)*
- **Exit:** Fixture image yields correct `tags_detected`, a nonempty saved crop, and a pass/fail result matching the rule in §13. ✅ verified on the V2 fixture (tags `[0,1,2,3]`, 3000×1000 crop, status `pass`).

### Phase 3 — OpenMV N6
- [x] N6 MicroPython service: boots, identifies board+firmware, listens for newline-delimited JSON commands (§10), validates against an allowlist. *(Verified 2026-07-14: MicroPython 1.26.0, sensor PAG7936, legacy `sensor` API — resolved OQ-1/5.)*
- [x] USB discovery + handshake (identify by USB identity/handshake, **not** fixed `/dev/ttyACM*`). *(By USB serial number; VID `0x37C5` — resolved OQ-2. N6 + AE3 both enumerate, so discovery requires an explicit serial.)*
- [x] Still capture + file retrieval (§10) + metadata; checksum verified. *(Capture to `/flash` + length-framed serial retrieval, SHA-256 verified end to end — resolved OQ-3.)*
- [x] Repeated-capture and invalid-command-rejection tests. *(`scripts/test_openmv_n6.sh` + `tests/hardware/test_openmv_n6.py`; unit tests via a fake-loopback board.)*
- **Exit:** Pi discovers the N6, captures N stills in a row, retrieves each with a matching checksum, and rejects a bad command cleanly. ✅ verified on `nereus000` (serial `005537493543`).
- *Bonus (§2 "video where practical"):* live browser focus stream (`host_tools/focus_stream.py`) for manual M12 lens adjustment — HD MJPEG + a sharpness readout, validated in use. `capture_video`-to-file (OQ-4) remains deferred.

### Phase 4 — OpenMV AE3
- [x] Equivalent AE3 behavior, board-specific code isolated (no shared `if board == ...` branching). *(Reuses the Phase 3 shared protocol, `capture_service`, `device_info`, and host adapter unchanged; AE3-specifics live only in `openmv/ae3/`. The AE3 carries the same PAG7936 sensor as the N6 but on an **Alif** SoC with **no `pyb` module** — so `pyb.USB_VCP()` is replaced by a `sys.stdin/stdout` + `select.poll` USB shim in `openmv/ae3/main.py`, the one board-specific difference. `deploy_openmv` now chains file copies into one `mpremote` connection to dodge the Alif firmware's flaky raw-paste re-entry.)*
- **Exit:** Same as Phase 3, for the AE3. ✅ verified on hardware (AE3 serial `0829c14000000000`, firmware `1.25.0-preview`): discovery by USB identity, 3 back-to-back captures each retrieved with a matching SHA-256, and clean rejection of a bad command (`./scripts/test_openmv_ae3.sh`). *Firmware note: OpenMV v5.0.0 (out-of-beta, "Fix Apriltags on the AE3") is available; updating both boards to it — plus migrating the shared capture path from the deprecated `sensor` module to the new `csi` module — is deferred as a follow-up (OQ-19). The Alif has no `pyb` on any firmware, so the USB shim stays regardless.*

### Phase 5 — Three-camera coordination
- [x] Sequential capture across all connected cameras into one experiment folder (§12). *(`capture/coordinator.py`: fixed order IMX708 → N6 → AE3, each camera in its own guard. `storage/experiment_store.py` builds the §13 folder and never overwrites a prior run. `controller.build_camera()` is the single config→adapter factory and forwards the OpenMV USB serial/board — fixing a latent single-capture ambiguity now that N6 + AE3 both enumerate. CLI: `nereus-rig experiment`.)*
- [x] Checksums, raw metadata, then automatic reference-card analysis per still. *(Adapters checksum each artifact on retrieval; coordinator writes `capture.json` then runs `analyze_reference_card` per still. A pre-capture `get_device_info()` handshake records each board's firmware (§5). Analysis is lazy-loaded — a capture-only rig without the OpenCV extra still runs and logs a warning; the extra uses the **headless** OpenCV wheel so it imports on the Pi with no GUI system libs.)*
- [x] Partial-failure handling: one camera failing doesn't delete or block the others (§12). *(A failed/disconnected camera is marked failed in `experiment.json`, its slot keeps a failed `capture.json`, survivors keep their files, the folder is never deleted, and the run returns `partial`.)*
- **Exit:** One command produces one experiment dir with one output+metadata+analysis per available camera; a disconnected camera yields a clear partial-success result. ✅ verified on `nereus000` with all three cameras (N6 fw 1.26.0, AE3 fw 1.25.0-preview): full set → one §13 folder, every still checksum-verified end to end, analysis run per still; disconnected camera (bogus serial) → `partial`, survivors + folder retained. Repeatable via `./scripts/test_experiment.sh`.

### Phase 6 — Web interface
- [x] Local web app (§14): Rig Status, New Experiment, Experiment Results, Downloads. *(Flask, server-rendered, styled to the nereus-vision-dev dashboard brand; binds to `web.host`/`web.port` from config so it serves over Tailscale. New Experiment calls the Phase 5 coordinator unchanged.)*
- [x] Side-by-side outputs, AprilTag pass/fail, annotated + cropped images, per-file and full-ZIP download. *(Plus, at owner request: a view-time color-patch check — grey-ramp/color ΔE + color cast vs the V2 card design values sampled through the real rectification pipeline — and a shareable cut-sheet export as PNG/PDF.)*
- [x] **Exit:** A capture can be triggered and reviewed in the browser and downloaded as a ZIP. ✅ verified live on `nereus000` over Tailscale (2026-07-16): `http://nereus000:8080` reachable from the Mac; a browser-form capture produced `exp_20260716T224301Z_reference_card_above_water` with all three cameras completing (4/4 tags each), reviewed side-by-side, ZIP downloaded (18 files). *Also 13 Mac web tests green against coordinator-produced §13 folders. Live color check immediately surfaced real findings: AE3 strong green cast (grey ΔE 39.7), N6 underexposed (mean luma 95), IMX708 mounted inverted.*

### Phase 7 — Evaluation experiments
- [ ] Repeatable experiment profiles: above-water card, below-water card, artificial + ambient light, low light, turbidity, fixed-distance resolution, purple-ball dataset collection, static video clips.
- **Exit:** Each profile runs from a config file and produces a self-contained, comparable result folder.

### Phase 8 — Edge color correction
Detail: [`docs/DESIGN_edge_color_correction.md`](DESIGN_edge_color_correction.md) (v0.3, "the brief") + §20 below. Phase 8 does not depend on Phase 7; S0–S2 run on the Mac with no rig hardware.

**Main deliverable (brief §1a):** a tool that re-runs on a new dataset with **no code changes** — card and camera as config files, one saved-output command per stage (`ingest → locate → qc → fit → correct → report`), one standard HTML report per run. The TG-7 Channel Islands set is its first input. **S2 ends in a go/no-go decision gate**; S3–S8 (rig work) wait for it.

#### S0 — Foundations (Mac)
- [~] SPEC amendment: Phase 8 in the Build Plan, §2 scope, §20, open questions OQ-21…OQ-35. *(this PR)*
- [ ] `color/` package scaffold, import-isolated from the capture path (no `web`/`cameras`/`capture`/`controller`/serial imports — test-enforced).
- [ ] Card as config: `configs/cards/nereus_v2.yaml` (tag map, expand factors, canonical size, patch boxes, design truth with `source: design_values`), loaded by `color/card.py`. A test pins it to `template_layout.json` / `web/color_check.py` so the values can't drift. Physical dimensions left `null` until measured (OQ-32).
- [ ] Dependency split + license check: `[color]` runtime extra (MIT/BSD/Apache-2.0 only, OQ-33), `[tg7]` Mac-only extra (`rawpy`). `make license-check` walks the runtime dependency closure and fails on anything off the allowlist; a test fails if anything under `src/` imports `rawpy`.
- [ ] `color/raw_io.py`: the `RawFrame` contract (CFA mosaic, CFA pattern, black/white level, as-shot WB, exposure, ISO, source metadata) + L0 decode (black subtract, white normalize, clip mask, 2×2 superpixel binning, bilinear demosaic), all four Bayer patterns. DNG reader via `tifffile`.
- [ ] Mac-only ORF reader in `host_tools/tg7/` (`rawpy` + `exiftool`) → the same `RawFrame`, plus EXIF: `WaterDepth`, `DateTimeUTC`, exposure, ISO, f-number, focal length, flash fired.
- [ ] `inspect` stage command (`python -m host_tools.color inspect <file>`).
- **Exit:** `inspect` on a TG-7 ORF and on an IMX708 DNG each prints dimensions, CFA pattern, black/white level, exposure/ISO (+ depth and UTC time for the ORF), per-channel linear means and clip %, and writes a linear preview PNG under `results/`. License check and unit tests green. *(The DNG half needs one IMX708 DNG sample — OQ-24.)*

#### S1 — TG-7 dataset prep + metrics (Mac) — brief §7 P1.1
- [ ] `ingest <dataset_dir>`: build the tool manifest from EXIF with the fixed columns (file, camera, category, depth_m, time_utc, exposure_s, iso, fnumber, flash_fired, notes), category from the subfolder name, plus `dive_id` (time gaps, OQ-35) and `sweep_id` (consecutive frames < 90 s apart, depth within ±1.5 m). Written to `results/`, never into the dataset folder (§20).
- [ ] `locate`: card corners per frame with fallback — (a) AprilTag homography from ≥ 3 tags; (b) search window from the nearest located frame in the same sweep, then the card edge; (c) a small manual 4-corner click tool saving `card_corners.json` (~1 h of Nick's clicking for the leftovers). Every frame records its `locate_method` or an `unlocated` reason.
- [ ] Distance per frame: `z` from the card pose (PnP on the 4 corners) using the card's physical size (OQ-32) and TG-7 intrinsics (spec values now; refined from the P1.4 checkerboards in S2).
- [ ] `color/patches.py`: sample each patch in RAW coordinates — canonical patch boxes mapped through the homography onto the binned RAW, central 60 % — reporting pixel count, mean, std and per-channel clip fraction. Exposure-normalized.
- [ ] `qc`: frame filter (any grey patch < 30×30 RAW px, clipping, extreme blur, flash fired, diver torch) and patch-level card-damage QC (within-patch std/mean well above the same patch on a clean dive-1 frame at similar distance; mean drift vs neighbouring patches over time). Per-frame, per-patch `usable` flag + reason; nothing silently dropped. **Exclude only — never repair** (§20).
- [ ] `color/metrics.py`: ψ (grey-patch angular error), ΔE2000 (median, p90), red SNR, clipping fraction. ΔE2000 unit-tested against published reference pairs.
- [ ] `report` v1: standard HTML — dataset summary, QC exclusion table, scored contact sheet per sweep.
- **Exit:** on the TG-7 set, every `1_reference_A_iso100` frame has `dive_id`, `sweep_id`, card corners with method, and `z` — or an explicit reason why not; ~37 sweeps are found; the report shows per-sweep "before" scores and the QC split (dive 1 clean vs Sep 16 damaged). Re-running `report` alone does not redo `locate`. The whole chain also runs on a tiny synthetic dataset in the unit tests (proves "no code changes for a new dataset").

#### S2 — Physics v0 on TG-7 + decision gate (Mac) — brief §5, §7 P1.2–P1.5
Needs the P1.4 TG-7 above-water / checkerboard reshoot (Nick, OQ-29).
- [ ] `calibrate` (L1): black/white level, CCM (linear and root-polynomial, ≥ 2 illuminants) and `card_reference_daylight` from the reshoot → `configs/calibration/tg7.yaml`; intrinsics in air and in water from the checkerboards; `z` validated against the taped pool distances.
- [ ] `fit` (L2): per sweep, backscatter (`B∞`, `βB`) from black and view-path attenuation (`βD`) from QC-passing grey/colour patches normalized by their clean dive-1 reflectance; per dive, downwelling `K`. Confidence intervals, residuals vs depth and distance; the dusk dive flagged.
- [ ] `correct`: card mode and table mode (card ignored, parameters from the fitted curves vs `d`, `z`), L3 output (grey 128 to target, sRGB, JPEG), correction parameters in a JSON sidecar per image.
- [ ] Validation: leave-one-sweep-out (view path) and leave-one-dive-out (table mode), scored on the card.
- [ ] Baselines on the same frames: **(a)** backend GRVI `cheeca_v3`, unmodified, on the TG-7 camera JPEGs (OQ-31); **(b)** the Olympus underwater-preset JPEG from the same exposure (`2_underwater_preset`; the 4 flash-fired frames reported separately).
- [ ] Decision report (one HTML): Nereus v0 card + table mode vs GRVI vs Olympus — ψ/ΔE tables split clean card (dive 1) / damaged card (Sep 16); leave-card-out scores on `3_scene_card_offcenter`; before/after sheets for off-center and `4_no_card` (torch frames skipped); a "needs V3 dataset" list.
- **Exit (decision gate, brief §1a):** the report is produced by running the stage commands end to end with no code edits. Nick records **Go** (new dive with card V3, re-run the tool) or **No-go** (stop, write up why) in this spec with the reason. "Marked improvement" = a clear drop in grey-patch ψ on usable card frames vs both baselines, plus visibly better off-center and no-card sheets. Provisional targets (card ψ ≤ 3°, table ψ ≤ 6°) are revised after S2, not used as the gate.

#### S3–S8 — Rig work (outline, gated on the S2 decision)
Not started until S2 is **Go**; a no-go changes what the rig should test. Each gets full checklist items + exit criteria when scheduled.
- **S3 — RAW capture on the rig** (brief P0): IMX708 `rpicam-still --raw` (DNG + JPEG, one exposure); OpenMV allowlisted `capture_raw` (Bayer + width/height/CFA/bit depth/black level + read-back exposure/gain/WB); locked recipes; lock check; OQ-19 firmware decision (OQ-21…24). Capture changes behind `raw: true` flags. *Demo:* one command → 3 cameras × RAW + ISP JPEG + metadata.
- **S4 — Above-water calibration** (P2) + card truth v1 → `configs/calibration/<camera_id>.yaml`, CCM residuals (OQ-27).
- **S5 — `bmcam001` recipe + arms A / A′ / B / C** in the coordinator (P3) — bench A/B/C sheet on the card (OQ-28).
- **S6 — Pool depth sweep** (3/6/9 ft, two cards) + gain/exposure sweep (P4) — depth-sweep report (OQ-26).
- **S7 — `nereus-rig soak`** loop (foreground, §2), daily summary + data pull; run the 5-day soak (P5) (OQ-30).
- **S8 — Final analysis + report** (P6): A-vs-C per camera, RQ-1, gain-sweep answer, `bmcam001` quick wins.

### MVP acceptance (all phases)
- [ ] Pi detects IMX708, N6, AE3; one command runs one capture set; all available cameras produce a still.
- [ ] Images stored in one timestamped folder, each with metadata; reference card evaluated; AprilTags detected when resolvable; crop saved when required tags found.
- [ ] Results viewable in browser and downloadable as ZIP; disconnected camera → clear partial failure.
- [ ] Mac unit tests pass; hardware smoke tests documented and repeatable; no BM/Spotter/field dependencies.

---

## 5. Repository Foundation (Phase 0 detail)

Common camera interface — implement per device, register via a registry; no scattered platform `if`/`elif`:

```python
class CameraDevice:
    def get_device_info(self) -> dict: ...
    def configure(self, settings: dict) -> None: ...
    def capture_image(self, destination, request) -> dict: ...
    def capture_video(self, destination, request) -> dict: ...
    def health_check(self) -> dict: ...
```

Concrete: `Imx708Camera`, `OpenMvUsbCamera`. Contracts stay simple — the controller doesn't know OpenMV API details; the USB adapter takes a structured request and returns a structured result; analysis takes an image path and returns a result object.

Every experiment must preserve: experiment ID, timestamp, environment label, camera identity, board firmware, sensor config, image dimensions + format, exposure settings where available, capture duration, output size, SHA-256 checksum, AprilTag detections, card-crop result, errors/warnings.

---

## 6. Repository Structure

```text
nereus-camera-test-rig/
├── README.md  CLAUDE.md  LICENSE  .gitignore
├── pyproject.toml  requirements-dev.txt  Makefile
├── configs/
│   ├── rig.example.yaml
│   ├── experiments/{reference_card_above_water,reference_card_below_water,low_light,object_detection}.yaml
│   ├── cameras/{imx708,openmv_n6,openmv_ae3}.yaml
│   ├── cards/nereus_v2.yaml                  # Phase 8: card layout + truth (config, not code)
│   └── calibration/<camera_id>.yaml          # Phase 8: per-camera L1 calibration
├── src/nereus_camera_test_rig/
│   ├── cli.py  controller.py  models.py  config.py  logging_config.py
│   ├── cameras/{base,imx708,openmv_usb,registry}.py
│   ├── capture/{coordinator,image_capture,video_capture,naming}.py
│   ├── storage/{experiment_store,metadata,checksums}.py
│   ├── analysis/{apriltag_detector,reference_card,crop,image_metrics,result_writer}.py
│   ├── color/{card,raw_io,patches,metrics,calib,water_model,output,pipeline}.py   # Phase 8, §20
│   └── web/{app.py, templates/, static/}
├── openmv/
│   ├── common/{command_protocol,capture_service,device_info}.py
│   ├── n6/{boot,main,board_config}.py
│   └── ae3/{boot,main,board_config}.py
├── host_tools/{discover_openmv,deploy_openmv,verify_rig,run_experiment,collect_results,compare_cameras,generate_report}.py
├── host_tools/color.py  host_tools/tg7/     # Phase 8 Mac-side stage CLI; rawpy/exiftool live only here
├── scripts/{install_pi.sh,configure_pi_camera.sh,start_web.sh,test_imx708.sh,test_openmv_n6.sh,test_openmv_ae3.sh,collect_diagnostics.sh}
├── tests/{unit/, integration/, hardware/, fixtures/{reference_card_images,expected_detections}/, conftest.py}
├── experiments/.gitkeep
├── results/.gitkeep
├── data/.gitkeep                           # git-ignored source datasets (e.g. data/tg7_channel_islands/), read-only
└── docs/{architecture,hardware_setup,openmv_usb_protocol,camera_configuration,reference_card_pipeline,experiment_workflow,test_matrix,downselect_criteria,open_questions,prior_art_review,implementation_plan}.md
```

Do not commit generated images, videos, experiment results, or virtual environments.

---

## 7. Hardware Architecture

```text
Mac dev computer --(SSH / HTTP / download)--> Raspberry Pi controller
    Pi --CSI--> IMX708
    Pi --USB--> OpenMV N6
    Pi --USB--> OpenMV AE3
    Pi --> local storage for all experiment results
```

Sequential commands within ~1–2 s are acceptable; no GPIO/hardware-trigger sync. Each OpenMV board runs a small MicroPython app that boots, identifies itself, listens for newline-delimited USB commands, validates, captures, saves/returns output, responds with a structured result, and stays ready.

---

## 8. OpenMV USB Command Protocol

Newline-delimited JSON over USB serial. Command allowlist only — **no arbitrary remote Python execution**.

```json
// get_device_info request / response
{"version":1,"command_id":"abc-001","action":"get_device_info"}
{"version":1,"command_id":"abc-001","status":"completed",
 "device":{"platform":"openmv","board":"n6","device_id":"openmv-n6-001","firmware":"0.1.0"}}
```

```json
// capture_image
{"version":1,"command_id":"abc-002","action":"capture_image",
 "settings":{"framesize":"native","pixel_format":"rgb565","jpeg_quality":90,"warmup_frames":10}}
```

```json
// capture_video
{"version":1,"command_id":"abc-003","action":"capture_video","settings":{"duration_seconds":5}}
```

```json
// result / error
{"version":1,"command_id":"abc-002","status":"completed",
 "output":{"filename":"capture_20260714T180000Z.jpg","width":1280,"height":720,"size_bytes":123456}}
{"version":1,"command_id":"abc-002","status":"failed",
 "error":{"code":"capture_failed","message":"Sensor snapshot failed"}}
```

```json
// reset_board — ack then hard MCU reset (machine.reset); the board re-enumerates and the
// service returns in ~3.5 s. Clears firmware 3A (AWB) state that survives sensor.reset()
// (stale-AWB green cast on the AE3 after lights-off runs, 2026-07-16 — see OQ-20). The
// coordinator sends it before capture when the camera profile sets reset_before_capture.
{"version":1,"command_id":"abc-004","action":"reset_board"}
{"version":1,"command_id":"abc-004","status":"completed","output":{"resetting":true}}
```

```json
// delete_file — remove one file from board storage (same basename guard as get_file).
// The flash copy is only a transfer buffer; the authoritative raw evidence is the
// checksum-verified copy on the Pi (§10, §11). The host sends this best-effort after
// each verified retrieval — without it captures accumulate until the filesystem is full
// (the N6 hit 0 bytes free on 2026-07-17 and every capture failed with io_error).
// A missing file is a structured file_not_found failure; a failed delete never fails
// the capture. get_device_info additionally reports flash_free_bytes/flash_total_bytes
// so a filling flash is visible in health checks before captures start failing.
{"version":1,"command_id":"abc-005","action":"delete_file",
 "settings":{"filename":"capture_20260714T180000Z.jpg"}}
{"version":1,"command_id":"abc-005","status":"completed",
 "output":{"filename":"capture_20260714T180000Z.jpg","deleted":true,"size_bytes":123456}}
```

---

## 9. Pi Capture Behavior

Prefer `rpicam-still` / `rpicam-vid`; fall back to `libcamera-still` only where needed; detect available commands at setup; fail clearly if the camera stack is missing. Support: still, short video, resolution, crop, resize, JPEG quality, exposure time, analog gain, white-balance mode, color gains (where supported), autofocus mode, manual lens position (where supported), warm-up delay, capture timeout. Derive default IMX708 configs from existing tested profiles, not invented values.

---

## 10. OpenMV File Collection

Before implementing, verify the most reliable supported transfer per board, in this order: (1) capture to OpenMV storage and copy via supported file access; (2) capture then request via framed USB transfer; (3) official OpenMV tooling; (4) direct USB serial binary transfer only if required.

Do not mix text JSON and raw binary without framing. If binary transfer is used:

```text
JSON header line → exact binary length → binary bytes → JSON completion line
```

Validate: expected byte count, checksum, timeout behavior, interrupted-transfer recovery.

---

## 11. Capture Coordination (Phase 5 detail)

Three-camera still capture: create experiment record → timestamped capture-set dir → capture IMX708 → request N6 → request AE3 → collect outputs → checksums → write raw metadata → run reference-card analysis → write analysis results → update web UI.

OpenMV boards are hard-reset (`reset_board`, §8) before their capture when the camera profile sets `reset_before_capture` (~3.5 s/board) — boards stay powered between experiments and firmware AWB state can survive the per-capture `sensor.reset()`, so one extreme-lighting run could otherwise poison the next (OQ-20; CLAUDE.md §10). Best-effort: a board that can't reset still gets its capture attempt.

**Failure behavior:** if one camera fails, continue with the rest, mark the failed device in metadata, retain successful files, return partial success, and never delete the experiment folder.

---

## 12. Configuration

YAML on Pi and Mac. Identify OpenMV devices by USB identity or handshake — **not** solely by `/dev/ttyACM0`/`ttyACM1` (Linux numbering changes on reboot/reconnect).

```yaml
rig:
  id: "nereus-camera-rig-001"
  results_directory: "./results"
cameras:
  imx708:     {enabled: true, driver: "imx708", profile: "configs/cameras/imx708.yaml"}
  openmv_n6:  {enabled: true, driver: "openmv_usb", board: "n6",  serial_number: null, profile: "configs/cameras/openmv_n6.yaml"}
  openmv_ae3: {enabled: true, driver: "openmv_usb", board: "ae3", serial_number: null, profile: "configs/cameras/openmv_ae3.yaml"}
analysis:
  apriltag: {enabled: true, expected_tag_ids: [0, 1, 2, 3]}
web: {host: "0.0.0.0", port: 8080}
```

---

## 13. Experiment Data & Reference-Card Pipeline (Phase 2 detail)

Folder layout — never overwrite a prior experiment:

```text
results/2026-07-14/exp_20260714T180000Z_reference_card_above_water/
├── experiment.json
├── captures/{imx708,openmv_n6,openmv_ae3}/{image.jpg, capture.json}
├── analysis/{imx708,openmv_n6,openmv_ae3}/{detection.json, annotated.jpg, card_crop.jpg}
└── logs/experiment.log
```

Pipeline: load image → detect AprilTags → report IDs + corners → confirm expected card present → compute card boundary from configured tags → rectify if supported → crop card region → save annotated + cropped images → write JSON.

```json
{"status":"pass","tags_detected":[0,1,2,3],"expected_tags":[0,1,2,3],
 "all_expected_tags_found":true,"card_crop_created":true,
 "crop_width":1600,"crop_height":900,"processing_time_ms":184}
```

**Pass rule (MVP):** all required AprilTags detected, card boundary computable, nonempty crop produced and saved without error. Optional secondary metrics (tag pixel size, detection margin, blur/edge sharpness, brightness, clipping %, color-patch stats) are **not** blockers for bring-up.

---

## 14. Web Interface (Phase 6 detail)

Small local app hosted by the Pi (no auth on a trusted dev network):

- **Rig Status** — connected cameras, identity, health check, free storage, last capture status.
- **New Experiment** — experiment type, still/video, cameras to include, environment label, notes, camera profile.
- **Experiment Results** — side-by-side outputs, capture metadata, AprilTag pass/fail, annotated image, card crop, downloads, errors.
- **Downloads** — individual files, full experiment folder ZIP, metadata JSON, analysis JSON.

---

## 15. Mac-Hosted Tools

```bash
python -m host_tools.verify_rig
python -m host_tools.run_experiment    --config configs/experiments/reference_card_above_water.yaml
python -m host_tools.collect_results   --experiment-id exp_20260714T180000Z_reference_card_above_water
python -m host_tools.compare_cameras   --experiment-id exp_20260714T180000Z_reference_card_above_water
```

Mac tools may reach the Pi via HTTP or SSH, but the Pi remains the authoritative capture coordinator.

---

## 16. Logging & Diagnostics

Structured, readable logs including (where relevant): timestamp, experiment ID, camera ID, action, status, duration, error. Do not log raw image contents. Provide `./scripts/collect_diagnostics.sh` producing a bundle with: OS + Python version, installed camera commands, connected USB devices, Pi camera status, app version, config (secrets removed), recent logs, disk usage.

---

## 17. Down-Select Criteria (Phase 7+ output)

Support evidence-based comparison across: image quality, AprilTag detectability, card-crop success, low-light and underwater color performance, sharpness/contrast, video quality, startup time, capture latency, power, storage behavior, inference capability, development + deployment complexity, maintainability, hardware cost, physical size, production fit. **Define the scoring method before final selection.**

---

## 18. Future Inference (post-MVP, extension points only)

Create interfaces/folders now; do not make inference part of rig acceptance. Candidate workflows: YOLO-family on Pi/Mac, OpenMV-compatible models on N6/AE3, purple-ball / fish / biofouling / robot detection, coral color/bleaching. Do not assume one model binary runs on all three boards. Evaluate models on: size, latency, memory, power, precision/recall, minimum object size, supported operators, deployment complexity.

---

## 19. Backlog — Future Capabilities (not yet scheduled)

Requested capabilities beyond the current Build Plan (§4). Each is filed as a **GitHub
issue** (the traceable source of truth for discussion + status); this list exists only for
scope visibility. When an item is scheduled, promote it into §4 as a phase or sub-item and
reference its issue there.

- **Live multi-camera comparison view** — [#9](https://github.com/nickraymond/nereus-camera-test-rig/issues/9).
  3-up simultaneous live video (IMX708 + OpenMV N6 + AE3) in the browser, with native /
  on-device AprilTag detection overlaid per device and per-camera **accuracy + latency**
  metrics for head-to-head comparison. Extends Phase 6 (web) + on-device detection (OQ-6) +
  §17 down-select. Beyond MVP (current analysis is still-based). Needs refinement: precise
  "accuracy" definition, cross-camera frame-sync tolerance, on-device vs host detection per
  platform (candidate ADR).

---

## 20. Edge Color Correction (Phase 8 detail)

The design brief ([`docs/DESIGN_edge_color_correction.md`](DESIGN_edge_color_correction.md), v0.3) holds the algorithm, experiment plan and dataset notes. This section records only the rules and decisions the build must follow. Where the two differ, this section wins.

**Module boundaries**
- `src/nereus_camera_test_rig/color/` is pure numpy / OpenCV (+ `tifffile` for DNG). It may import `analysis/` (card geometry, AprilTags) but never `web`, `cameras`, `capture`, `controller` or the serial stack. It must import on both the Mac and the Pi. A unit test enforces the import rule.
- `analysis/` and `web/color_check.py` are **not** changed by S0–S2. Where Phase 8 needs different behaviour (e.g. card localization from 3 of 4 tags, which `analysis/reference_card.localize_card` does not allow), it lives in `color/`.
- **Mac-side stage CLI:** `python -m host_tools.color <stage>` (`inspect`, `ingest`, `locate`, `qc`, `fit`, `correct`, `report`, `calibrate`). *Deviation from the brief's `nereus-rig color ingest`:* ORF decoding needs `rawpy` (LGPL), which must stay out of `src/`, so the TG-7 stage commands live in `host_tools/` with the ORF reader in `host_tools/tg7/`. The Pi-side commands (`nereus-rig correct`, `nereus-rig soak`) arrive in S3+ and use only `color/` with DNG / Bayer input.
- Rig integration (S3+) goes into `cameras/` and `capture/coordinator.py` behind `raw: true` profile flags so the existing capture path is unchanged when the flag is off.

**Licensing.** Runtime dependencies (base + the `[color]` extra, i.e. anything that ships to the Pi or the backend) must be MIT, BSD or Apache-2.0 (edge cases — Pillow's MIT-CMU, numpy's compound SPDX expression — in OQ-33). The `[color]` extra lists its own dependencies and does not pull in `[analysis]`, whose `pillow-heif` carries a GPL classifier. `rawpy` goes only in the Mac-only `[tg7]` extra; `exiftool` is an external Mac-side CLI, never shipped. `make license-check` enforces the allowlist over the installed runtime closure; there is no CI in the repo yet, so it runs from `make test` and the PR checklist until one exists.

**Config, not code.** The card is `configs/cards/<card>.yaml` (tag map, geometry, patch boxes, physical size, truth values with a `source:` field). The camera is `configs/calibration/<camera_id>.yaml` (black/white level, CCM per illuminant, daylight card reference, intrinsics, noise, flat-field), written by `calibrate`. Card V3 is a new YAML file, not a code change.

**Datasets are read-only.**
- A dataset is a folder of RAW (+ optional JPEG) files, sorted into category subfolders. Tools read it and **never move, rename, edit or delete** anything in it. `data/` is git-ignored, so it exists only in the primary checkout: tools take the dataset path as an argument and never assume `./data` relative to a worktree.
- The tool builds its **own** manifest in `results/`. A hand-made `manifest.csv` inside the dataset (the TG-7 one has local-time `datetime`, no UTC column, no camera column) is optional input for cross-checking only.
- Outputs: `results/color/<dataset_id>/<stage>/…` (`manifest.csv`, `locate/card_corners.json`, `qc/qc.csv`, `fit/params.json`, `correct/<stem>/…`, `report/index.html`). Each stage reads the previous stage's saved outputs, so re-running one stage does not redo the others. Every output records the git SHA and config paths used.

**TG-7 EXIF facts (verified 2026-09-26 with exiftool 13.55 on `P9150344.orf`).** `WaterDepth` is the **standard EXIF tag** (ExifIFD), not an Olympus maker note. `DateTimeUTC` is in the Olympus maker note and is the only correct clock (the camera was set to UTC−8). CFA pattern is **GRBG**, black level 257 per channel, ORF 4040×3016. Flash fired comes from EXIF `Flash`, not the Olympus `InternalFlash` field (it reads "On" on this no-flash frame). Re-check these on a sample from each folder during `ingest`. **Dates:** the brief's "Sep 15 / Sep 16" dive labels are local (PDT) dates — in UTC the Sep 15 evening dive is already Sep 16 (P9150344 = `2026:09:16 01:45 UTC`). So `dive_id` is assigned from time gaps between shots, never from the UTC calendar date.

**Card water damage — exclude, never repair (brief §7 P1.0).** The V2 card leaked from the end of dive 1 (Sep 15); every Sep 16 frame is affected, mostly white / grey 200 / left of grey 128. `qc` flags a patch as damaged by a simple rule (within-patch std/mean vs a clean dive-1 frame at similar distance; mean drift vs neighbours) and excludes it with a reason. No staining model, no repair, no clever workaround. If a fit or metric needs the excluded patches, the report says **"needs V3 dataset"** and work moves on. All metrics are reported split by card condition (dive 1 clean / Sep 16 damaged).

**Decision gate (end of S2).** Compare Nereus physics v0 (card + table mode) against the backend GRVI correction (OQ-31) and the Olympus underwater preset, on the same frames. Go = a clear drop in grey-patch ψ on usable card frames plus visibly better off-center and no-card sheets → new dive with card V3, re-run the tool. No-go → stop and write up why. The outcome and reason are recorded in §4 Phase 8 S2.
