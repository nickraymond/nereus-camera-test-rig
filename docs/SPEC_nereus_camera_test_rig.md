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
- **Automatic underwater color correction** ("Nereus physics v0"), as Phase 8 (§4, §20). Built first as a Mac-side, re-runnable tool on the TG-7 Channel Islands RAW dataset; rig work (RAW capture, calibration, pool sweep, soak) follows **only if** the S2a decision gate says go. Goal: realistic, colorimetric color for all customers, not a site look. It lives in its own `color/` module and must not destabilize the Phase 0–6 capture path.
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
| `nereus-vision-dev` `backend/app/services/processing/grvi.py` | *Phase 8:* the current backend correction (GRVI, processor `cheeca_v3`) as an **S2a baseline**, called unmodified from a local checkout on the Mac (OQ-31) | Do not vendor, port or re-tune it here |

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
Detail: [`docs/DESIGN_edge_color_correction.md`](DESIGN_edge_color_correction.md) (v0.3, "the brief") + §20 below (§20 wins where they differ). Phase 8 does not depend on Phase 7; S0–S2 run on the Mac with no rig hardware.

**Product intent (Nick, 2026-09-26):** realistic, colorimetric color correction for every customer — as close to the true scene color as possible. Not a site "look": the Cheeca Reef GRVI rendering was a proof of concept and is not a target to imitate or beat on style.

**Main deliverable (brief §1a):** a tool that re-runs on a new dataset with **no code changes** — card and camera as config files, one saved-output stage per step (`ingest → locate → qc → fit → correct → report`), one standard HTML report per run. The TG-7 Channel Islands set is its first input. **S2a ends in the go/no-go decision gate**; S3–S8 (rig work) wait for it. S2b (after the TG-7 reshoot) measures how much better calibration data improves the result.

Plan reviewed 2026-09-26 by three independent reviewers (color science, architecture, fact-check); their verified findings are folded into the items below and §20.

#### S0 — Foundations (Mac)
- [x] SPEC amendment: Phase 8 in the Build Plan, §2 scope, §20, open questions OQ-21…OQ-38. *(PR #18)*
- [x] `color/` package scaffold. Import boundary checked **at runtime**: a test imports `nereus_camera_test_rig.color` in a subprocess and fails if `web`, `cameras`, `capture`, `controller`, `serial` or `rawpy` is in `sys.modules`. Worktree-safe tests: pytest `pythonpath = ["src", "."]` and `make test VENV=…`, so a branch's tests run the branch's code (today the shared venv's editable install points at the main checkout).
- [x] Card as config: `configs/cards/nereus_v2.yaml` is the **single source of truth** for the V2 card (§20): patch boxes from `tests/fixtures/reference_card/template_layout.json`; truth values from the backend's SVG design fills (`source: design_svg_2026-09-01`); tag IDs + tag centres/edge length in canonical coordinates; sub-patch boxes (grey 128 left/right) for the damage map; `physical_mm` fields `null` until measured (OQ-32). Loaded by `color/card.py`. A test pins the geometry to `template_layout.json`. `web/color_check.py` is not changed; its known differences (±1–2 counts, shifted colour names, centres up to ~19 px off) are recorded in the YAML header.
- [ ] Licence policy + check (§20): `configs/licenses.yaml` — a reviewed table (package, declared licence, bundled native libraries, decision). A test reads the base + `[color]` dependencies from `pyproject.toml`, walks their installed requirements, and fails on any package not in the table or with a GPL/AGPL classifier. `[color]` = numpy, OpenCV (**built without FFmpeg / GPL codecs**, OQ-36), tifffile — it lists its own deps and does not pull in `[analysis]`. Mac-only `[tg7]` = rawpy, matplotlib. `make install-color` / `make install-tg7`; exiftool + rawpy setup in `docs/hardware_setup.md`.
- [ ] Separate small PR: drop the unused `pillow-heif` from `[analysis]` (GPL classifier; nothing in `src/`, `host_tools/` or `tests/` imports it).
- [ ] `color/raw_io.py`: the `RawFrame` contract (CFA mosaic, CFA pattern, **per-frame, per-channel** black level, white level, valid crop, as-shot WB, embedded colour matrix if any, exposure time, ISO, f-number, source metadata) + L0 decode (black subtract, white normalize, clip mask, 2×2 superpixel binning, bilinear demosaic), all four Bayer patterns (the TG-7 is GRBG). Exposure normalization by `t · ISO / N²`. DNG reader via `tifffile`.
- [ ] Mac-only ORF reader in `host_tools/tg7/` (`rawpy` + `exiftool`) → the same `RawFrame`, plus EXIF: `WaterDepth`, `DateTimeOriginal` + `OffsetTimeOriginal` (cross-checked against Olympus `DateTimeUTC`), exposure, ISO, f-number, focal length, EXIF `Flash`, Olympus `BlackLevel2`, `ColorMatrix`, `WB_RBLevels*`.
- [ ] Stage framework + `inspect`: stage functions live in `color/stages.py` (rawpy-free; the file reader is chosen by extension from a small registry). `host_tools/color.py` is a thin argparse shell that registers the ORF reader and calls the stages. Every stage writes `stage.json` (git SHA + dirty flag, config file hashes, upstream `stage.json` hash); a downstream stage fails loudly if its input is stale. First stage: `inspect`.
- **Exit:** `python -m host_tools.color inspect <P9150344.orf>` prints dimensions, CFA pattern, per-channel black level, white level, exposure/ISO/f-number, depth and UTC time, per-channel linear means and clip %, and writes a linear preview PNG plus `stage.json` under `results/`. Licence check, import-boundary test and unit tests green. The ORF half may pass via DNG Converter if LibRaw can't decode the TG-7 (OQ-34). *Non-blocking:* the same on an IMX708 DNG once the OQ-24 sample exists.

#### S1 — TG-7 dataset prep + metrics (Mac) — brief §7 P1.1
- [ ] `ingest <dataset_dir> --config <dataset.yaml>`: build the tool manifest from EXIF with the fixed columns (file, camera, category, depth_m, time_utc, exposure_s, iso, fnumber, flash_fired, black_level, has_raw, notes), category from the subfolder name. The dataset config holds the site latitude/longitude (no GPS in the files, OQ-38) and optional `dive_id` overrides. `dive_id` from time gaps (OQ-35). `sweep_id`: chain each `1_reference` frame to the previous reference frame if < 90 s apart and depth within ±1.5 m (reproduces 37 sweeps, 4 of them single frames). JPG-only shots (P9160568) are kept with `has_raw = false`. Sun elevation per frame from UTC time + site. Written to `results/`, never into the dataset folder (§20).
- [ ] `locate`, for **every card-bearing frame** (`0_`–`3_`): (a) AprilTag homography from ≥ 3 tags — the 3-tag case uses bm_cam_legacy's `infer_card_corners_from_tags`, ported additively into `analysis/reference_card.py` with `min_tags=4` as the default so existing behaviour is unchanged (approved, Nick 2026-09-26); (b) search window from the nearest located frame in the same sweep; (c) manual clicks on the **tag centres** with a small matplotlib tool (`[tg7]`), saved to `locate/manual_corners.json`, which `locate` only reads and never overwrites. Budget: ~115 reference frames have < 3 tags, plus 7 off-center and 5 preset frames. Stored quad = tag-centre quad with `quad_type` and `locate_method`. JPEG↔RAW offset (8 px crop) applied when moving homographies between them.
- [ ] Distance per frame: `z` by PnP on the tag-centre quad (nominal ≈ 365 × 92 mm until measured, OQ-32), in-air focal length from EXIF, × 1.33 underwater (flat port) until the P1.4 checkerboards. `z_provisional = true` while either is nominal.
- [ ] `color/patches.py`: sample each patch (and sub-patch) in RAW coordinates — canonical boxes mapped through the homography onto the binned RAW, central 60 % — reporting pixel count, mean, std, per-channel clip fraction and a 3×3 cell-mean grid. Exposure-normalized.
- [ ] `qc`, exclude only (§20): frame filter (patch too small for its blur, clipping, flash fired, diver torch) and card-damage QC — (i) the known damage map: white, grey 200 and grey 128-left excluded from P9150409 onward; (ii) every other patch flagged if its 3×3 cell-mean max/min ratio in any channel exceeds the 99th percentile measured on the clean frames P9150343–P9150360; (iii) patches too small for the cell test get `damage_unknown`, never `clean`. Per-frame, per-patch `usable` + reason.
- [ ] `color/metrics.py` per the §20 scoring protocol: ψ on held-out greys after white balance + colour matrix, in linear sRGB; ΔE2000 after an L*-only match on grey 128, plus ΔC\*/ΔH\*; red SNR; clipping; channels gated on SNR; `n(frame, patch)` reported per method. ΔE2000 unit-tested against published reference pairs.
- [ ] `report` v1: standard HTML — dataset summary, QC exclusion table, and per-sweep "before" contact sheets scored on the as-shot camera JPEG.
- **Exit:** every card-bearing frame in `0_`–`3_` has a located tag-centre quad (auto or manual) or a stated reason it is unusable; every located frame has a `z` (flagged provisional while nominal); 37 sweeps with `dive_id`; the report shows "before" scores split **clean card (≤ P9150408) / damaged card**, with the count of usable patches per split. Re-running `report` alone does not redo `locate`, and a stale input fails loudly. The whole chain also runs on a tiny synthetic dataset in the unit tests.

#### S2a — Physics v0 + decision gate, before the reshoot (Mac) — brief §5, §7 P1.2–P1.5
Runs on data that exists today (Nick, 2026-09-26): shows how good v0 is before better reference data, and is the baseline S2b is measured against.
- [ ] `calibrate` (provisional L1): per-frame black level from the ORF, the TG-7's embedded `ColorMatrix` (rows sum to 256, so neutral stays neutral) and its daylight WB preset, nominal focal length × 1.33, nominal card size → `configs/calibration/tg7_provisional.yaml`.
- [ ] `fit` (L2), per dive, jointly over all usable patches: βD and βB shared per dive, B∞ per sweep, black-patch reflectance as a nuisance term, per-frame `d`. The black patch enters the backscatter fit only above a minimum pixel width. K fitted from dives 3–4 only, or with sun elevation as a covariate; dives 1 **and** 2 flagged (both ascend steadily while the light changes; dives numbered as in OQ-35). Intensity fits restricted to patches within ~0.6 of the image half-diagonal until a flat-field exists. Report confidence intervals and how many sweeps are identifiable (review estimate: ~11 of 37).
- [ ] `correct`: **card mode** (neutralize on grey 128; the other greys held out for scoring, or parameters from the other frames in the sweep) and **table mode** (card ignored; parameters from the fitted curves vs `d`, `z`), each table-mode frame run with the card's `z` ("oracle") and with the default scene `z` used for no-card frames. L3 output (grey 128 to target, sRGB, JPEG); parameters in a JSON sidecar per image.
- [ ] Validation: leave-one-sweep-out and leave-one-dive-out, reported per held-out dive, never pooled (only dives 3↔4 are a like-for-like light test).
- [ ] Baselines on the same frames, scored with the same protocol: **(a)** backend GRVI `cheeca_v3`, unmodified, on the TG-7 camera JPEGs, run in the backend's own environment (OQ-31); **(b)** the Olympus underwater-preset JPEG from the same exposure (flash-fired 4 reported separately), plus the nearest A-mode sweep frame at the same site; **(c)** the as-shot A-mode JPEG ("no correction" — also what GRVI outputs when it can't find the card).
- [ ] Decision report (one HTML): Nereus v0 vs baselines in two classes — card-anchored (Nereus card mode vs GRVI) and card-free (Nereus table mode vs Olympus preset vs as-shot) — with frame counts per method × dive and per card condition, blind side-randomized before/after sheets (off-center, no-card with torch frames skipped, and a sample of reference frames), and a "needs V3 dataset" list.
- **Exit (decision gate, §20):** the report is produced by running the stages end to end with no code edits. **Nick's blind visual review decides first; the pre-registered numeric rule supports it.** Nick records **Go** (new dive with card V3, re-run the tool) or **No-go** (stop, write up why) here with the reason.

#### S2b — Recalibrate with the TG-7 reshoot (Mac) — brief §7 P1.4
Needs the reshoot (OQ-29). Not a gate: it quantifies what better calibration buys, and can run alongside S3+ after a Go.
- [ ] Full `calibrate`: CCM (linear and root-polynomial, ≥ 2 illuminants), dark frames + noise model, flat-field, intrinsics in air and in water from the checkerboards; `z` validated against the taped pool distances → `configs/calibration/tg7.yaml`. The reshoot photographs the **water-damaged** V2 card, so the daylight card reference uses only patches that pass QC against P9150343; an X-Rite chart (OQ-27) gives better truth where available.
- [ ] Re-run the tool with the new calibration; the report shows S2b vs S2a per metric and per class.
- **Exit:** the S2b report quantifies the change from the reshoot calibration with the same frames and protocol as S2a.

#### S3–S8 — Rig work (outline, gated on the S2a decision)
Not started until S2a is **Go**; a no-go changes what the rig should test. Each gets full checklist items + exit criteria when scheduled.
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
│   ├── calibration/<camera_id>.yaml          # Phase 8: per-camera L1 calibration
│   └── licenses.yaml                         # Phase 8: reviewed licence table (§20)
├── src/nereus_camera_test_rig/
│   ├── cli.py  controller.py  models.py  config.py  logging_config.py
│   ├── cameras/{base,imx708,openmv_usb,registry}.py
│   ├── capture/{coordinator,image_capture,video_capture,naming}.py
│   ├── storage/{experiment_store,metadata,checksums}.py
│   ├── analysis/{apriltag_detector,reference_card,crop,image_metrics,result_writer}.py
│   ├── color/{card,raw_io,patches,metrics,calib,water_model,output,pipeline,stages}.py   # Phase 8, §20
│   └── web/{app.py, templates/, static/}
├── openmv/
│   ├── common/{command_protocol,capture_service,device_info}.py
│   ├── n6/{boot,main,board_config}.py
│   └── ae3/{boot,main,board_config}.py
├── host_tools/{discover_openmv,deploy_openmv,verify_rig,run_experiment,collect_results,compare_cameras,generate_report}.py
├── host_tools/color.py  host_tools/tg7/     # Phase 8: thin Mac CLI over color/stages.py; rawpy/exiftool live only here
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

The design brief ([`docs/DESIGN_edge_color_correction.md`](DESIGN_edge_color_correction.md), v0.3) holds the algorithm, experiment plan and dataset notes. This section records the rules and decisions the build must follow. **Where the two differ, this section wins.**

**Product intent.** Realistic, colorimetric correction for all customers: the goal is the true scene color, as accurately as possible. No site-specific "look" (GRVI's reef rendering was a proof of concept).

**Module boundaries**
- `src/nereus_camera_test_rig/color/` is pure numpy / OpenCV (+ `tifffile` for DNG) and holds all stage logic (`color/stages.py`), so the backend and the Pi can reuse it. It may import `analysis/` but never `web`, `cameras`, `capture`, `controller`, the serial stack or `rawpy` — checked at runtime by a test. It must import on the Mac and the Pi.
- `host_tools/color.py` is a thin Mac CLI (`python -m host_tools.color <stage>`) that registers the Mac-only ORF reader from `host_tools/tg7/` (rawpy + exiftool) and calls the stages. The Pi commands (`nereus-rig correct`, `nereus-rig soak`, S3+) are a second thin CLI over the same stages with DNG / Bayer input. *(Deviation from the brief's `nereus-rig color ingest`, to keep rawpy out of `src/`.)*
- `analysis/reference_card.py` gains 3-of-4-tag card inference (ported from bm_cam_legacy) behind `min_tags`, default 4 — no behaviour change for Phases 2–6. `web/color_check.py` is not changed.
- Rig integration (S3+) goes into `cameras/` and `capture/coordinator.py` behind `raw: true` profile flags so the existing capture path is unchanged when the flag is off.

**Licensing policy (Nick, 2026-09-26; not legal advice — counsel review before the first commercial device ships).** GPL/LGPL obligations are triggered by *distribution*; AGPL also by network use.

| Where the code runs | Rule |
|---|---|
| **Shipped** — `color/`, anything on the Pi, customer devices, anything that could be distributed | MIT / BSD / Apache-2.0 and equivalent permissive licences (e.g. Pillow's MIT-CMU) only. No GPL, LGPL or AGPL — including native libraries bundled inside wheels. |
| **Hosted service** (backend correction) | Same as shipped, so the code can move to devices. AGPL is always banned. |
| **Internal Mac tools** (`host_tools/tg7/`, analysis) | May use LGPL tools that never leave Nereus (rawpy/LibRaw, exiftool). Never imported from `src/`. |

- Package metadata is not enough: rawpy declares MIT (its LGPL part is the bundled LibRaw), and the PyPI OpenCV wheels declare Apache-2.0 while bundling FFmpeg (LGPL) and x264 / x265 / libpostproc (GPL). So the check is a reviewed table, `configs/licenses.yaml` (package, declared licence, bundled native libraries, decision), enforced by a test over the base + `[color]` dependencies.
- Shipped installs use an OpenCV build **without FFmpeg / video codecs** (the color pipeline needs no video; OQ-36). `pillow-heif` is dropped from `[analysis]`.
- There is no CI yet; the licence test runs under `make test`. A CI workflow is a separate, later PR.

**Config, not code.**
- The card is `configs/cards/<card>.yaml`, the single source of truth: tag IDs, tag centres + edge length (canonical and, once measured, mm), expand factors, canonical size, patch and sub-patch boxes, truth values with a `source:` field. V2 truth = the backend's SVG design fills (`nereus-vision-dev/.../profiles/template_layout_v2.json`, 2026-09-01), the same truth GRVI uses; the rig's raster-sampled fixture values are ~1 count off on 11 of 17 patches. Card V3 is a new YAML file.
- The camera is `configs/calibration/<camera_id>.yaml` (black/white level, colour matrix per illuminant, daylight card reference, intrinsics, noise, flat-field, `provisional:` flag), written by `calibrate`.
- A dataset config (`<dataset>.yaml`, kept with the results, not in the dataset) holds the site location, dive overrides and default scene distance.

**Datasets are read-only.**
- A dataset is a folder of RAW (+ optional JPEG) files in category subfolders. Tools read it and **never move, rename, edit or delete** anything in it. `data/` is git-ignored, so it exists only in the primary checkout: tools take the dataset path as an argument and never assume `./data` relative to a worktree.
- The tool builds its **own** manifest. A hand-made `manifest.csv` in the dataset is optional input for cross-checking only.
- Outputs: `results/color/<dataset_id>/<stage>/…`, where `dataset_id` = dataset folder name + a short hash of its file list. Each stage writes `stage.json` (git SHA + dirty flag, config hashes, upstream `stage.json` hash) and reads only the previous stage's saved outputs; stale inputs fail loudly. Manual work (`locate/manual_corners.json`) is never overwritten by a re-run.

**TG-7 facts (verified 2026-09-26 with exiftool 13.55 across all 308 ORFs / 309 JPGs).**
- `WaterDepth` is the **standard EXIF tag** (ExifIFD) in every file. Focal length 4.5 mm on every shot; every A-mode shot is f/2.0.
- Time: UTC = `DateTimeOriginal` + `OffsetTimeOriginal` (standard EXIF, −08:00 on every file), which matches the Olympus `DateTimeUTC` on 308/308. The camera clock was UTC−8 (PST), one hour behind true local time (PDT); the brief's "Sep 15 / Sep 16" labels are camera-local dates. `dive_id` is assigned from time gaps, never from calendar dates.
- RAW: 4040×3016, CFA **GRBG** in every ORF, 12-bit (`ValidBits 12`). The JPEG is the RAW cropped to 4000×3000 at (8, 8).
- **Black level varies per frame and per channel** (Olympus `BlackLevel2`, 256–260, ISO-dependent; 257×4 on 240 of 308): read it from each file, never use a constant.
- The ORF carries a colour matrix (`ColorMatrix`, rows sum to 256, so neutral maps to neutral) and WB presets (`WB_RBLevels…`). No GPS tags.
- Flash fired comes from EXIF `Flash` (5 fired, 4 of them preset card frames); the Olympus `InternalFlash` field reads "On" on every file and is useless.

**Card water damage — exclude, never repair (brief §7 P1.0).** Damage starts **inside dive 1** (~P9150409); every later frame is affected, mostly white / grey 200 / left of grey 128. The **clean-card subset is frames ≤ P9150408** (~14 frames with ≥ 3 tags — this is thin, and every report says so). `qc` excludes by the known damage map plus a within-patch 3×3 cell-ratio test (§4 S1); patches too small to test are `damage_unknown`, not clean. No staining model, no repair. If a result needs excluded data, the report says **"needs V3 dataset"** and work moves on. All metrics are reported split clean / damaged.

**Scoring protocol (all methods, S1 onward).**
- Every method is scored on its **final 8-bit sRGB output**, decoded with the sRGB transfer function, with the same patch boxes and the same frame set. Internal diagnostics on RAW use white balance + colour matrix first (ψ in raw camera RGB is meaningless).
- ψ (primary) on grey patches **not used for neutralization**; ΔE2000 after an L*-only match on grey 128, plus ΔC\* / ΔH\*. Channels gated on SNR. Every table states `n` (frames, patches) per method, per dive, per card condition.
- Two comparison classes, never mixed: **card-anchored** (Nereus card mode vs GRVI) and **card-free** (Nereus table mode vs Olympus preset vs as-shot JPEG). Table mode is reported with oracle `z` and default `z`.
- Frames within a sweep are correlated: confidence intervals come from a sweep-level bootstrap.

**Decision gate (end of S2a).** Nick's **blind, side-randomized visual review decides first**; the numbers support it. Pre-registered numeric rule (kept as a guide): within each class, Nereus median ψ ≥ 3° **and** ≥ 30 % lower than the best baseline, the sweep-bootstrap 95 % CI of the difference excludes 0, the paired per-frame win rate ≥ 70 %, and the blind A/B prefers Nereus in ≥ 70 % of pairs. Go → new dive with card V3, re-run the tool. No-go → stop and write up why. Outcome and reason are recorded in §4 Phase 8 S2a.
