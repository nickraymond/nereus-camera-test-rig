# Edge Color Correction — Design Brief

| | |
|---|---|
| **Status** | Draft v0.3 — input for a Claude Code session to turn into a spec update + sprint plan. v0.2 added the sorted TG-7 dataset summary, a dataset-prep step and the TG-7 reshoot list (§7 P1.0–P1.5). v0.3 adds the MVP framing (§1a: build a reusable tool, decision gate vs the backend correction) and the reference-card water-damage finding with a time-boxed handling rule (§7 P1.0, P1.1) |
| **Owner** | Nick Buemond (Nereus Vision) |
| **Date** | 2026-09-26 |
| **Repo / path** | `nereus-camera-test-rig/docs/DESIGN_edge_color_correction.md` |
| **Builds on** | `docs/SPEC_nereus_camera_test_rig.md` (Phases 0–6 done), `bm_cam_legacy/device_profiles/bmcam001/camera_schedule.yaml`, Nereus BM Camera project note *Underwater Color Correction — Research Brief* (Sep 2026; key points are summarized in this brief) |

---

## 0. How to use this brief (for the Claude Code session)

1. Read `CLAUDE.md` and the SPEC first. This brief **adds a new phase** to that SPEC. It does not replace it. Also read `docs/open_questions.md` OQ-19 (firmware v5 / `csi` migration) and OQ-20 (AE3 stale AWB).
2. **Scope change to record in the SPEC (same PR as the first sprint):**
   - SPEC §2 lists "automatic underwater color correction" as out of scope. This brief brings it **in**, as **Phase 8 — Edge color correction**. It is a separate module that must not destabilize the Phase 0–6 capture path.
   - SPEC §2 excludes "production scheduling or daemon/watchdog." The 5-day soak (P5) needs only a **simple experiment loop** (`nereus-rig soak`), not a daemon. Say so explicitly.
3. Follow the repo rules: small PRs, one concern each, raw captures never overwritten, unknowns go in `docs/open_questions.md` (continue the OQ numbering).
4. Section 12 is a suggested sprint ladder. Section 11 lists open questions.
5. **Licensing:** runtime dependencies must be MIT / BSD / Apache-2.0. This code is headed for a paid feature. `rawpy` (LGPL) is allowed only in Mac-side analysis tools, not the shipped pipeline.

---

## 1. The question this phase answers

> **How good can color correction be at the edge, on each of our three cameras, when we control the whole pipeline from RAW — and how much better is that than what `bmcam001` sends today?**

**In scope**

- A physics-based color correction algorithm ("Nereus physics v0"), built and validated first on the Channel Islands **OM System TG-7** RAW dataset (depth in EXIF).
- Running it on the rig Pi on RAW / Bayer data from the IMX708, OpenMV N6 and OpenMV AE3. **The Pi does all processing**, so device compute is not a variable.
- Above-water card characterization under several lights and distances.
- Pool depth sweep at 3 / 6 / 9 ft, then a **5-day soak at ~9 ft**.
- Replicating `bmcam001`'s field config on the bench IMX708 and running a three-arm A/B against the new RAW pipeline.

**Out of scope (for now)**

- BM / Spotter transport, link budget, power budget, image byte caps. JPEG quality only needs to be sensible and matched between arms.
- Running correction on the N6/AE3 themselves; OpenMV ISP register programming.
- Neural-network / hybrid parameter prediction. It comes later, trained on the data this phase logs (§5.7).
- Per-pixel distance maps from monocular depth models. Stretch goal only.

---

## 1a. MVP framing — build a reusable tool, not a one-off result

This phase is a **proof of concept**. If it shows a marked improvement over the color correction the Nereus backend applies today, Nick will run a new dive with a better reference card and **repeat the whole analysis**. So:

**The main deliverable is a tool that can be re-run on a new dataset with no code changes.** The TG-7 Channel Islands set is its first input, not its purpose.

Tool requirements:

1. **Dataset-agnostic input.** A dataset is a folder of RAW (+ optional JPEG) files plus a `manifest.csv` with fixed columns (file, camera, category, depth, time UTC, exposure, ISO, flash, notes; distance and card corners filled in by the tool). One command builds the manifest from EXIF (`nereus-rig color ingest <folder>`), as was done by hand for the TG-7 set.
2. **Card is a config file, not code.** Patch layout, tag IDs, physical size and truth values live in `configs/cards/<card>.yaml`. Card V2 is the first entry; a future V3 is a new file.
3. **Camera is a config file.** Intrinsics, CCM, black/white level per camera in `configs/calibration/<camera>.yaml`, produced by a `calibrate` command from calibration shots.
4. **Each stage is a separate command with saved outputs:** `ingest` → `locate` (card corners + distance, with manual-click fallback) → `qc` (patch quality) → `fit` → `correct` → `report`. Re-running one stage doesn't redo the others.
5. **QC excludes bad data. It doesn't repair it.** Anything flagged (damaged patch, clipped, too small, blurred, flash) is excluded with a reason in the output. It is never "corrected."
6. **Every run writes one HTML report** with the same layout (dataset summary, QC exclusions, fitted parameters, metrics, before/after sheets). Runs on different datasets can then be compared side by side.

**Decision gate (end of S2):** compare Nereus physics v0 against **(a)** the current backend correction and **(b)** the TG-7's own Olympus underwater preset. Use the same images, the card metrics where the card is usable, and a side-by-side visual sheet.

- "Marked improvement" = a clear drop in grey-patch error ψ on usable card frames, plus before/after images that are visibly better on the off-center and no-card scenes.
- Needs the backend algorithm as a runnable baseline (Claude Code: locate it — likely in `nereus-vision-dev` — or call it; see OQ 6).
- **Go** → new dive with card V3, re-run the tool. **No-go** → stop and write up why.

**Time box:** effort on working around TG-7 dataset defects (e.g. the card water damage in §7 P1.0) is capped at excluding bad data by simple rules. If a result depends on data the rules exclude, note it as "needs V3 dataset" and move on.

## 2. Success metrics

Every image containing the V2 card is scored the same way, whatever the method.

| Metric | What it measures | Notes |
|---|---|---|
| **ψ — grey-patch angular error** (degrees) | Angle between measured linear RGB of each grey patch and neutral (1,1,1) | **Primary metric** (Sea-thru paper). 0° = perfectly neutral. Uses the 5 grey-ramp patches. |
| **ΔE2000 on the 12 color patches** | Distance from the card's reference values, in perceptual units | Median and 90th percentile. Upgrade from the CIE76 ΔE in `web/color_check.py`. |
| **Red SNR on white / grey 200** | Usable red signal before correction (mean ÷ std in linear RAW) | Red < ~5% of full scale ⇒ unrecoverable. |
| **Clipping fraction** | Share of patch pixels at the ceiling, per channel | Catches exposure mistakes. |
| **Leave-card-out ψ / ΔE** | Error when correction is computed **without** looking at the card (table mode), then scored on the card | The product number: customers won't have a card in frame. |
| **Pi processing time per image** | Wall-clock time per camera | Informational only. |

**Provisional targets** (revise after S2 on TG-7 data):

- Card mode: ψ ≤ 3°
- Table mode: ψ ≤ 6°
- Arm C beats Arm A (field config) on ψ in ≥ 90% of paired frames

---

## 3. Hardware under test (as the rig exists today)

Controller: **Pi 5, hostname `nereus000`**, reached over Tailscale. Existing coordinator captures IMX708 → N6 → AE3 sequentially (~1–2 s spread) into one experiment folder (SPEC §12–13).

| | **IMX708** (Camera Module 3) | **OpenMV N6** | **OpenMV AE3** |
|---|---|---|---|
| Sensor | Sony IMX708, 12 MP 4608×2592, rolling shutter, **autofocus** | PixArt PAG7936, 1 MP 1280×800, global shutter | PixArt PAG7936 (same sensor) |
| ISP | Pi 5 ISP via libcamera; algorithms from tuning file `imx708.json` | STM32N6 hardware ISP + hardware JPEG | **No hardware ISP** |
| Current capture path | `rpicam-still` adapter (`cameras/imx708.py`); already supports `--lens-position`, `--awb custom --awbgains`, `--shutter`, `--gain` | MicroPython 1.26.0, legacy `sensor` API, capture to `/flash` + length-framed USB retrieval (SHA-256) | Firmware 1.25.0-preview, no `pyb` (stdin/stdout USB shim); same protocol |
| RAW access (to build) | `rpicam-still --raw` → **DNG from the same frame as the JPEG** (simplest, fits existing adapter) | `sensor.set_pixformat(sensor.BAYER)` via a new allowlisted command. The PAG7936 is corrected by a **firmware software AWB** (N6 reports ≈ R +4.9 dB / B +4.8 dB, OQ-20); RAW bypasses it | `openmv/ae3/board_config.py` notes **BAYER works** on the AE3 but is left out of the allowlist. The AE3 on 1.25.0-preview has **no AWB control at all** (OQ-20), so Bayer is the *only* way to get WB-locked data from it |
| RAW bit depth | 10-bit | **Unknown — verify.** OpenMV `BAYER` has historically been 8 bits/pixel. | Same question |
| Firmware note | — | OQ-19 (v5.0.0 + `sensor`→`csi` migration) is deferred. **Bayer / bit-depth support may force it.** Decide in S3. | Same |

**Known baseline from the Phase 6 color check (above water, auto settings):** AE3 strong green cast (grey ΔE 39.7), N6 underexposed (mean luma 95), IMX708 mounted inverted. The AE3 cast was traced to stale software-AWB state and is worked around by `reset_board` before captures (OQ-20). Board-reported `exposure_us` / `gain_db` are now carried into capture metadata (main, PR #15). Phase 8 should reproduce these as its "before" state.

**Built-in experiment:** N6 and AE3 share a sensor but differ in ISP. RAW from both should be near-identical. If not, the difference is lens / IR-cut / sensor register settings. Their ISP outputs differ by exactly "what the N6's hardware ISP adds."

**Per-camera color factors to track:** field of view (card patch size in pixels; keep ≥ ~40×40 px per patch on the smallest-coverage camera), IR-cut filters, flat-port refraction underwater (≈1.33× focal length), IMX708 autofocus (**lock it**: `mode: manual`, fixed `lens_position`), IMX708 inverted mount (rotate before card detection; irrelevant to color).

---

## 4. Background the implementer needs (short)

**RAW vs Bayer vs DNG.** RAW = sensor values before the ISP, one linear number per pixel. On a color sensor that's a **Bayer** mosaic (RGGB), so OpenMV's `BAYER` mode *is* RAW. **DNG** is a file format (Adobe, TIFF-based) that wraps Bayer data with its metadata (black/white level, CFA pattern, color matrices, as-shot WB). The TG-7's `.ORF` is the Olympus equivalent. OpenMV gives bare pixels, so we write the metadata into `capture.json` (§8).

**What the ISP does.** Black level → defect pixels → lens shading → demosaic → WB gains → 3×3 color matrix → denoise/sharpen → gamma → JPEG. It also runs the auto-exposure and auto-white-balance loops. Capturing RAW skips the processing stages but **not** the auto loops, so AE/AWB must be locked for comparable RAW.

**Why `bmcam001` is green (hypothesis to confirm in P3).** libcamera's AWB searches along a color-temperature curve (the range of blackbody lights, warm to blue). Underwater light, with red missing and green dominant, isn't on that curve. AWB settles on the least-bad temperature and leaves a green cast. No AWB *mode* fixes that. Fixed `--awbgains` or RAW + physics does.

**Order matters more than format.** Correct in linear space first, then gamma + JPEG. JPEG q85 on an already-corrected image is near-invisible. Boosting red *after* JPEG is where damage happens. PNG is not needed.

---

## 5. The algorithm — "Nereus physics v0"

### 5.1 Layers

```
RAW ─► L0 decode ─► linear camera RGB
    ─► L1 camera  ─► linear camera-neutral RGB        (per-camera calibration, above water)
    ─► L2 water   ─► linear "scene without water" RGB  (physics; uses depth d, distance z, card if present)
    ─► L3 output  ─► neutralize on card grey, sRGB gamma, resize, JPEG
```

### 5.2 L0 — RAW decode

- Subtract black level. Normalize by white level. Mask clipped pixels.
- **Measurement path:** 2×2 superpixel binning (R, mean(G1,G2), B). No demosaic artifacts, better SNR. Use this for all card-patch sampling.
- **Image path:** bilinear demosaic (OpenCV Bayer conversion) for full-resolution output.
- Lens shading: divide by a per-camera flat-field (from P2). Optional in v0; log whether applied.
- Card location: reuse `analysis/reference_card.py` (AprilTag homography, rectify to canonical 3000×1000) and the patch coordinates in `web/color_check.py`. Map patch boxes back into RAW coordinates through the homography, rather than sampling the gamma-encoded JPEG crop.
- TG-7 `.ORF`: decode with `rawpy` in a Mac-side tool (LGPL, not shipped), or convert to DNG with Adobe DNG Converter first.

### 5.3 L1 — Camera characterization

**Card truth.** V2 patch values are the **printed design values** already in `web/color_check.py`: white, grey 200/128/74, black, and 12 colors. Print error is unknown (typically a few ΔE).

| Option | Pros | Cons |
|---|---|---|
| A. Design values (have today) | Free, already in code | Print error goes straight into ΔE |
| B. Reference shot: V2 card beside an X-Rite ColorChecker Classic (published values in `colour`) in daylight; calibrate the camera on the X-Rite, then solve for V2 patch values | ~1–2 ΔE, cheap | Needs an X-Rite chart; one careful session |
| C. Handheld spectrophotometer | Best absolute truth | Buy / borrow a device |

**Recommendation: A now, B when possible.** Store truth as versioned `configs/cards/nereus_v2_truth.yaml` with a `source:` field so metrics can be recomputed.

**Key point: the water layer doesn't need absolute card truth.** Fit L2 as "underwater card ÷ the *same camera's* above-water card in daylight," and print errors cancel. This includes the printed "black," which really reflects a few percent. Absolute truth only matters for L1 and for ΔE reporting, so it doesn't block algorithm work.

**Per-camera calibration file** (`configs/calibration/<camera_id>.yaml`):

- `ccm` — 3×3 camera RGB → linear sRGB (D65), fit on above-water card shots (`colour.characterisation`; try linear and root-polynomial). Fit under ≥ 2 illuminants (daylight + warm LED) and store both, DNG-style.
- `card_reference_daylight` — this camera's own linear patch values in daylight at ~1 m. This is the L2 normalizer.
- `black_level`, `white_level`, `noise` (dark frames + gain sweep).
- `flat_field` (per build), `intrinsics_air`, `intrinsics_water` (checkerboard; water set from the pool).
- `camera_id`, `sensor`, `lens`, `serial`, `calib_date`.

CCM per **sensor model** is usually enough. Flat-field and intrinsics are **per build**.

### 5.4 L2 — Water model

Per channel *c*, in L1 space:

```
I_c = ρ_c · E_c(0) · exp(−K_c · d) · exp(−βD_c · z)   +   B∞_c · (1 − exp(−βB_c · z))
      └────────── light reflected off the target ─────────┘   └────── backscatter (haze) ──────┘
```

| Symbol | Meaning | Source |
|---|---|---|
| `I_c` | What the camera measured | Image |
| `ρ_c` | True reflectance (what we want back) | Unknown; known for card patches |
| `E_c(0)` | Light color at the surface | Above-water card / daylight reference |
| `d` | Water depth (m) | TG-7 EXIF; pool: known mount depth; later: Keller sensor |
| `K_c` | Downwelling attenuation: light fading on its way **down** | Fit across depths |
| `z` | Camera → target distance (m) | Meter stick / tape / card length scale |
| `βD_c` | Attenuation of reflected light over the view path | Fit from grey/white patches across distances |
| `B∞_c`, `βB_c` | Haze color at infinity; how fast haze builds with distance | Fit from the **black** patch across distances |

About 12 numbers per water condition (4 per channel). This is the Akkaynak–Treibitz revised model with the downwelling term made explicit, so depth and distance separate cleanly.

**Fitting (TG-7 first):**

1. Per image: find card, sample binned-RAW patch means, read `d` from EXIF (confirm the Olympus maker-note tag with `exiftool`), get `z` from the meter stick / card scale.
2. **Backscatter:** fit `B∞(1 − e^{−βB z})` to black-patch values vs distance, per depth.
3. **View-path attenuation:** subtract backscatter from grey/white patches, then fit `e^{−βD z}` vs distance. Constant βD in v0. Add a 2-term distance dependence (Sea-thru) only if residuals demand it.
4. **Downwelling:** at fixed distance, fit `e^{−K d}` vs depth, normalized by the above-water card.
5. **Validate** on the 12 color patches and held-out dives.

**Pitfall:** a card at one distance can't separate "haze is bright" from "haze builds fast." The TG-7 sweeps have multiple distances. For the fixed pool rig, use **two cards at different distances**.

**Correction:**

```
ρ_c = (I_c − B∞_c(1 − e^{−βB_c z})) / (E_c(0) · e^{−K_c d} · e^{−βD_c z})
```

Then neutralize on the card greys (card mode) or on the predicted illuminant (table mode).

| Mode | Parameters from | Use |
|---|---|---|
| **Card mode** | Solved live from the card(s) in this frame | Upper bound on quality |
| **Table mode** | Fitted curves / lookup vs (d, z) from prior data (TG-7, then pool), **card ignored** | Product path; scored leave-card-out |

**Scene distance in v0:** one `z` per image (card distance, or mount-to-floor/wall distance). Per-pixel range (Depth Anything V2 **Small**, Apache-2.0 only) is a stretch goal.

### 5.5 L3 — Output

1. Clip negatives. Set brightness so the grey 128 patch lands at its target.
2. Linear sRGB → sRGB gamma, 8-bit.
3. Crop and downscale to the output geometry (match `bmcam001` for the A/B, §9). Downscaling averages away red noise.
4. JPEG (Pillow is fine; jpegli/MozJPEG later), 4:2:0.
5. Write the correction parameters into `capture.json` so the backend can audit or redo the correction.

### 5.6 Research question RQ-1: is the water model camera-agnostic?

Water coefficients measured in one camera's RGB aren't perfectly portable, because each sensor's color filters average the spectrum differently (Akkaynak 2017). Per-camera L1 should remove most of the difference.

**RQ-1:** after per-camera L1, how much error does using **TG-7-fit** water parameters on the IMX708 / N6 / AE3 add, versus parameters fit on each camera's own pool data? The pool rig measures this directly.

### 5.7 Log for the future hybrid model

Don't build the network now. For every frame, store inputs (downscaled image with the card masked, `d`, `z`, timestamp, sun elevation, camera_id, exposure, gain) and targets (card-solved L2 parameters). A later small network predicts the ~12 parameters, and the physics still does the correction.

---

## 6. Where the code goes (fit to the existing package)

```
src/nereus_camera_test_rig/
  color/                     # NEW — Phase 8. Pure numpy/OpenCV; no capture or web imports.
    raw_io.py                # DNG / Bayer+meta / (Mac-only) ORF → linear RGB, binned + full
    patches.py               # patch sampling in RAW coords via reference_card homography
                             #   (graduate patch table from web/color_check.py)
    calib.py                 # L1: CCM, flat-field, noise, intrinsics → configs/calibration/
    water_model.py           # L2: fit + invert; card mode + table mode
    output.py                # L3: tone, gamma, crop/resize, JPEG
    pipeline.py              # correct(raw, meta, calib, params|None) → image + params
    metrics.py               # ψ, ΔE2000, red SNR, clipping, leave-card-out
  cameras/imx708.py          # EXTEND: raw=True → rpicam-still --raw (DNG alongside JPEG)
  cameras/openmv_usb.py      # EXTEND: capture_raw request/response
  capture/coordinator.py     # EXTEND: arms A/B/C per capture set (§7 P3)
openmv/common/               # EXTEND: allowlisted capture_raw (BAYER + exposure/gain/WB readback)
configs/
  cameras/imx708_bmcam001_field.yaml   # replicated field recipe (Arm A)
  cameras/*_locked.yaml                # locked recipes (Arm C)
  cards/nereus_v2_truth.yaml
  calibration/<camera_id>.yaml
  experiments/card_calibration_above_water.yaml, pool_depth_sweep.yaml, pool_soak.yaml
host_tools/ or tools/tg7/    # Mac-side TG-7 ingest (rawpy + exiftool), fits, reports
CLI (extend `nereus-rig`): `correct`, `calibrate`, `eval`, `soak`
```

- `color/` must run both on the Mac (TG-7, analysis) and on the Pi (live soak). Keep it importable without the web or USB stack.
- Keep it portable: this module later moves to the website backend (upload-and-correct feature) and possibly to OpenMV C.

---

## 7. Experiment plan

### P0 — RAW capture bring-up (bench, dry)

- IMX708: `--raw` DNG + JPEG from one exposure, locked controls, `lens_position` fixed.
- N6 / AE3: new `capture_raw` command returning Bayer + width/height/CFA/bit depth/black level + read-back exposure/gain/WB. Also return an ISP JPEG from the **same** exposure if the firmware allows. If not, capture it back-to-back and record that.
- **Verify Bayer bit depth.** If only 8-bit linear is available, record it as a finding (red SNR underwater will suffer) and an input to the firmware roadmap / OQ-19.
- Lock check: two consecutive captures of a static scene → identical exposure/gain/WB metadata, patch means within noise.

### P1 — Algorithm v0 on TG-7 data (Mac only)

#### P1.0 — What the TG-7 dataset contains (sorted 2026-09-26)

Location: `data/tg7_channel_islands/` (git-ignored). `manifest.csv` has one row per shot. `README.md` describes the folders. Sorting used the camera metadata (exposure program, scene mode, flash) plus a visual check of every frame.

| Folder | Shots | Contents | Role in this phase |
|---|---|---|---|
| `raw/0_surface_card/` | 10 | Card on the boat / at the waterline, 0.1–0.5 m. 6 in A mode ISO 100, 4 in P auto | Weak surface reference only (see "gaps" below) |
| `raw/1_reference_A_iso100/` | 206 | A mode, f/2.0, ISO 100, auto WB, no flash, card roughly centered. **37 distance sweeps** at fixed depths from 4.1 to 16.6 m, over 5 dives | **Fitting set** for L2 |
| `raw/2_underwater_preset/` | 45 | Olympus Underwater Snapshot / Wide1 preset, ISO 125–400, card in frame. **Flash fired on 4 of these** (P9150429, P9160434, P9160435, P9160649) | Olympus-vs-Nereus comparison |
| `raw/3_scene_card_offcenter/` | 14 | A mode, card deliberately off-center in kelp / reef / lobster scenes | Realistic test set, **still scorable** because the card is visible |
| `raw/4_no_card/` | 34 | Kelp, fish, divers, no card (30 A, 3 preset, 1 P). 3 have a diver's torch in frame | Hard-mode visual test, table mode only |

Facts that shape the design:

- **One lens state for the whole set.** Every shot is at 4.5 mm (widest zoom). Every A-mode shot is at f/2.0. So a single set of TG-7 intrinsics covers the dataset.
- **Depth is in EXIF; distance is not.** `WaterDepth` (m) and water temperature come from the TG-7's own sensors. Camera-to-card distance must be computed from the card's known size in the image (§5.4 `z`).
- **AprilTags fail on far frames.** On 133 of 309 frames the tag detector finds nothing, although in most of them the card is present (tiny, hazy or motion-blurred at the far end of a sweep). Card localization needs a fallback (below).
- **Shutter speed varies a lot**, from 1/800 to 1/4 s: A mode fixes the aperture and lets the camera choose the shutter. 104 reference frames are slower than 1/30 s, so expect motion blur. Patch means are robust to blur, but edge-based card detection is not. RAW values must be normalized by exposure time before comparing frames.
- **Camera clock was set to UTC−8.** EXIF `DateTimeUTC` is correct; local time fields are PST, not PDT. Use `DateTimeUTC` for sun position.
- **The five dives cover very different sun angles** (converted to PDT: the evening of Sep 15, then early morning, mid-morning and midday Sep 16). The Sep 15 dive (7 sweeps descending 15 → 4.5 m) looks to have been close to sunset, so surface light was falling and reddening during the dive. That's confounded with depth, so **flag it before trusting a K fit from that dive**.

#### Known defect: the V2 card took on water (Nick, 2026-09-26)

The V2 card is a printed sheet in a laminated pouch with only a few mm of seal at the cut edges. Water leaked in during the trip. Close-ups across the dataset (`_review/card_over_time.jpg`) show:

- **Onset:** clean at the start of the Sep 15 dive (P9150343). Blotches appear by the end of that dive (~P9150409–P9150425). **Every Sep 16 frame is affected.**
- **Where:** mostly the **light patches on the left end** of the card (white, grey 200, and the left side of grey 128). These patches sit nearest the leaking edge. They show irregular dark wet blotches.
- **Less affected (by eye):** grey 74, black, the right side of grey 128, and the 12 color patches on the right half. These look intact but still need the QC check below.

Why it matters: wet paper is darker and more see-through than dry paper, so an affected patch no longer has its known reflectance. The white/light-grey patches are exactly the ones the model uses for attenuation and white balance.

**Handling rule (time-boxed — do not try to model or repair the staining):**

1. **Patch-level QC** (step 5 of P1.1). A patch is "damaged" if its within-patch variation (std/mean in binned RAW, over the central 60% of the patch) is well above the same patch on a clean frame at a similar distance. Also flag a patch whose mean shifts relative to its neighbors over time. Damaged patches are excluded from that frame.
2. **Use the remaining patches.** Fit backscatter from black. Fit attenuation from grey 74, the clean part of grey 128, and the color patches, using each patch's *clean* reflectance measured on the Sep 15 frames (camera-relative, §5.3).
3. **Split the analysis by card condition:**
   - **Dive 1 (Sep 15, 7 sweeps, 4.5–15.5 m): card mostly clean.** This is the gold subset for fits that need the white/light patches. Caveat: this was the near-sunset dive (P1.0).
   - **Sep 16 dives: card damaged.** Use only QC-passing patches. Report metrics separately.
4. If a fit can't be done well from the patches that survive, **don't work around it**. Record "needs V3 dataset" in the report and move on.

#### Card V3 recommendations (for the repeat dive)

- **Rigid, waterproof substrate.** Print directly on a rigid board (e.g. UV-cured print on aluminum composite or PVC), or encapsulate with ≥ 10 mm sealed margin on every edge. No paper in a pouch.
- **Matte surface** to cut glare, with patches ≥ 5× larger than now where possible. A large mid-grey patch helps white balance at distance.
- **Keep AprilTags and the length scale.** Keep the black patch (backscatter).
- **Measure the finished card** (spectrophotometer, or next to an X-Rite ColorChecker) and store the values in `configs/cards/nereus_v3.yaml` before the dive.
- **Photograph the card above water in daylight before and after each dive.** This catches damage early and gives a fresh daylight reference.

#### P1.1 — Dataset prep (new, before any fitting)

1. **Sweep grouping.** Group reference frames into sweeps: consecutive frames, < 90 s apart, depth within ±1.5 m. A first pass gives 37 sweeps. Store `sweep_id` and `dive_id` in the manifest.
2. **Card localization with fallback.**
   - (a) AprilTag homography when ≥ 3 tags are found.
   - (b) Otherwise, reuse the card position from the nearest detected frame in the same sweep as a search window, then find the white card edge within it.
   - (c) Otherwise, a tiny manual tool: click the 4 card corners, saved to `card_corners.json`. Budget ~1 h for the ~90 frames left over.
3. **Distance per frame.** `z = f_px · W_card / w_card_px`, with the card pose from the 4 corners (PnP). `f_px` in water ≈ 1.33 × the in-air value (flat front glass). Start from the spec value, then refine from the checkerboard shots in P1.4.
4. **Quality filter.** Drop or flag frames where any grey patch is < ~30×30 RAW pixels, any patch clips, or blur is extreme. Keep them in the manifest with a reason, so nothing silently disappears.
5. **Patch-level QC for card damage** (see "Known defect" above). Store a per-frame, per-patch `usable` flag and reason. All fits and metrics use only usable patches, and every report states how many were excluded.

#### P1.2 — How to fit the model with this data

The sweep structure is well suited to the model, because each sweep holds **depth fixed and varies distance**:

- **Per sweep (fixed d, varying z):** fit backscatter (`B∞`, `βB`) from the black patch, and view-path attenuation (`βD`) from the grey/white patches. That's 37 independent estimates, which gives an error bar for free.
- **Across sweeps within one dive (varying d):** fit downwelling `K` from the near-distance frames of each sweep. Fit K **per dive**, because the surface light differed per dive. Compare dives to see whether K is stable.
- **Validation:** leave-one-sweep-out for the view-path terms, leave-one-dive-out for table mode.

#### P1.3 — How to use the other folders

| Folder | Use | Metric |
|---|---|---|
| `2_underwater_preset` | **Same-exposure comparison:** each preset shot has its own RAW. Run Nereus physics v0 on that RAW and compare with Olympus's JPEG from the same exposure. Same light, same instant, only the processing differs. Also compare against the nearest A-mode sweep frame at the same site. | ψ, ΔE2000 on the card; side-by-side sheet |
| — flash-fired 4 | Report separately. Flash adds red light the ambient-only model doesn't expect, so they aren't a fair comparison. | — |
| `3_scene_card_offcenter` | Run **table mode** (ignore the card), then score on the card anyway. This tests correction when the card isn't the subject. Also produce before/after images for the website feature. | Leave-card-out ψ / ΔE; visual |
| `4_no_card` | Table mode only, using EXIF depth and an assumed scene distance (e.g. 2–3 m). Visual review; no numeric score is possible. Skip the 3 torch frames. | Visual only |
| `0_surface_card` | Sanity check only. Not enough to calibrate the camera (see P1.4). | — |

#### P1.4 — TG-7 above-water reshoot (Nick, before S2)

**Gap:** L1 (camera characterization) and L2's "underwater ÷ same camera in daylight" normalization both need clean above-water card shots in known light. The 10 surface shots are hand-held at the waterline, partly in the water, with mixed light and 4 in P mode. That isn't enough. A reshoot takes about an hour and unblocks both.

Settings for every shot: **A mode, f/2.0, ISO 100, widest zoom (4.5 mm — do not zoom), RAW+JPEG, flash off, auto WB** (fine, since we use the RAW). Card centered, roughly perpendicular to the lens, tilted ~10–15° away from the sun to avoid glare.

| # | Light | Distance | Shots |
|---|---|---|---|
| 1 | Direct midday sun | 0.5 m, 1 m, 2 m | 3 each + one at −1 EV and +1 EV |
| 2 | Open shade (blue sky only) | 1 m | 3 |
| 3 | Overcast | 1 m | 3 |
| 4 | Low sun (within ~1 h of sunset or sunrise), to match the dusk and early-morning dives | 1 m | 3 |
| 5 | Indoor warm LED / tungsten (second illuminant for the CCM) | 1 m | 3 |
| 6 | X-Rite ColorChecker beside the V2 card, if available (card-truth Option B) | 1 m | 3 in sun or shade |
| 7 | Flat-field: plain white paper or a diffuser filling the frame, open shade | touching / close | 3 |
| 8 | Dark frames: lens fully covered, at 1/250, 1/30, 1/4 s | — | 1 each |
| 9 | **Checkerboard, in air**: printed and flat, 15–20 angles and positions | 0.5–1 m | 15–20 |
| 10 | **Checkerboard, underwater** (bathtub, pool or bucket; TG-7 is waterproof): 15–20 angles | 0.3–1 m | 15–20 |
| 11 | **Card at taped distances underwater** in the pool (validates the distance-from-card math) | 0.5, 1, 2, 3 m | 2 each |

Rows 9–11 matter most for distance, which EXIF doesn't record. Rows 1–5 matter most for color. Put the new shots in `data/tg7_channel_islands/raw/5_above_water_calibration/` (and `6_pool_distance_check/` for row 11). Add them to the manifest.

#### P1.5 — Algorithm build and deliverables

- Build L0–L3 in card mode on `1_reference_A_iso100`, following §5.
- Deliverables:
  - per-sweep and per-dive parameters with confidence intervals
  - residual plots vs depth and distance
  - before/after contact sheets per sweep
  - ψ/ΔE tables
  - leave-one-sweep-out and leave-one-dive-out table-mode scores
  - Olympus-preset comparison (P1.3)
  - off-center and no-card before/after sheets

### P2 — Above-water characterization (rig cameras)

| Factor | Levels |
|---|---|
| Illuminant | Direct sun, open shade, overcast, 5000–6500 K LED, warm 2700–3000 K LED/tungsten |
| Distance | 0.5, 1, 2 m (patch ≥ 40 px on the widest camera) |
| Exposure | Nominal (grey 128 at ~18–25% of RAW full scale) and ±1 stop |

Plus per build: flat-field, dark frames at each gain used in P4, checkerboard in air. Output: `configs/calibration/<camera_id>.yaml`. Also the Option B card-truth session if an X-Rite chart is available.

### P3 — `bmcam001` replication + three-arm A/B

`bmcam001`'s real config is known (§9). Replicate it as `configs/cameras/imx708_bmcam001_field.yaml`, then run three arms on every paired capture in P4/P5:

| Arm | Pipeline |
|---|---|
| **A — Field config** | Exactly `bmcam001`: AE/AWB auto, manual focus, ISP JPEG q95 → center crop → Lanczos → re-encode at the field quality |
| **B — Field JPEG + post-correction** | Arm A output, then card-based correction on the de-gammaed JPEG (what a backend fix on field images could do) |
| **C — New pipeline** | Locked exposure/gain/WB → RAW → physics v0 → same crop, size and quality as A |

B vs C isolates "correct from RAW before JPEG" versus "correct after JPEG." A vs C is the headline number.

A and C need different camera settings, so capture them back-to-back and **alternate order** (A→C, C→A) to cancel light drift. B is computed from A.

**Quick-win side test:** Arm A′ = field config but with fixed `--awbgains` measured from the card at depth. This shows how much of the fix is available to `bmcam001` *today* by config change alone.

### P4 — Pool depth sweep (3 / 6 / 9 ft ≈ 0.9 / 1.8 / 2.7 m)

- Fixed frame. **Two cards** at different distances (e.g. ~0.5 m and ~1.5 m). Tape-measure distances; cross-check with card length scale.
- Per depth: ≥ 5 repeats of arms A, A′, C (+ B computed) for all cameras where applicable.
- **Gain / ISO sweep** (Arm C, IMX708 + N6): gain 0 / 6 / 12 dB × exposure {5, 10, 20, 40 ms}. Measure red SNR on grey/white from RAW. This answers "does low ISO help underwater on my hardware?" A fixed mount allows longer exposures than handheld TG-7 shooting did.
- Checkerboard in water once → `intrinsics_water`.
- Log pool conditions: time, sun/cloud, clarity, pool surface color (plaster/tile reflects light sideways).

### P5 — 5-day soak at ~9 ft

- Same rig and cards. Cadence: **every 15 min in daylight**, **hourly at night** (dark frames + light-leak check).
- Per capture: the Pi runs arms A, C (and computes B) for all cameras, writes outputs + RAWs + `capture.json`, and appends a row to `metrics.csv`.
- Daily summary: captures OK/failed per camera, disk free, Pi temperature, leak sensor if fitted. Pull data off the Pi daily.
- Questions: ψ/ΔE per arm vs sun elevation / cloud / time; does table mode hold across the light range; per-camera differences; RQ-1.

### P6 — Report

One HTML report per phase plus a final summary covering:

- the A vs C improvement per camera
- best recipe per camera
- the RQ-1 answer
- the gain-sweep answer
- a go/no-go on physics v0 as the default field pipeline
- **config changes `bmcam001` can take immediately** (e.g. fixed AWB gains)

### Honest limits of the pool

- Clear, shallow pool water ≠ the ocean. At 2.7 m, red loss and backscatter are small, so fitted K/βD/βB will be far below Channel Islands values. **The pool proves the pipeline and method, not ocean coefficients.**
- Walls and floor reflect light sideways/up, which breaks the "light from above" assumption. Expect larger residuals near walls.
- The next step is a sea deployment with the same software.

---

## 8. Data layout

Keep the SPEC §13 layout and extend it:

```
results/<date>/exp_<ts>_<profile>/
  experiment.json                    # + depth_m, card_distances_m, pool notes
  captures/<camera>/
    raw.dng | raw.bayer              # never overwritten
    isp.jpg                          # camera's own ISP JPEG, same exposure
    armA.jpg  armAprime.jpg  armB.jpg  armC.jpg
    capture.json                     # + fields below
  analysis/<camera>/{detection.json, card_crop.jpg, color_metrics.json}
metrics.csv                          # one row per (capture set, camera, arm, mode)
data/tg7_channel_islands/           # git-ignored source dataset: raw/<0..4 category>/*.orf+.JPG, manifest.csv, README.md (§7 P1.0)
```

New `capture.json` fields:

```json
{
  "recipe": "imx708_locked_v1",
  "raw": {"file": "raw.bayer", "width": 1280, "height": 800, "cfa": "RGGB",
          "bit_depth": 8, "black_level": 0, "white_level": 255},
  "exposure_us": 20000, "gain_db": 0.0, "wb_gains": [1.0, 1.0, 1.0],
  "ae_locked": true, "awb_locked": true,
  "focus": {"mode": "manual", "lens_position": 1.82},
  "depth_m": 2.74, "card_distances_m": [0.52, 1.49],
  "correction": {"arm": "C", "mode": "card",
                 "params": {"K": [], "betaD": [], "betaB": [], "Binf": []},
                 "calibration": "configs/calibration/n6-01.yaml", "code_version": "<git sha>"},
  "jpeg": {"quality": 0, "bytes": 0, "width": 1000, "height": 562}
}
```

`.gitignore` already ignores `/results/*`, `/data/*` and RAW extensions (`*.orf`, `*.dng`, `*.raw`, `*.bayer`).

---

## 9. `bmcam001` field config (from `bm_cam_legacy/device_profiles/bmcam001/camera_schedule.yaml`, rebuilt 2026-07-31)

| Setting | Value |
|---|---|
| Backend | `rpicam` (libcamera), IMX708, **default tuning** (`image_processing.enabled: false`) |
| Source capture | 4608×2592 ISP JPEG, **quality 95** |
| White balance | **auto** |
| Exposure | **auto** |
| Focus | **manual, `lens_position: 1.82`** (tuned for the reef housing) |
| Capture mode | `progressive_jpeg` |
| Crop → output | fixed center crop 1600×900 at (1504, 846) → Lanczos → **1000×562** |
| Output quality | adaptive ladder 90, 80 … 15, 13, 11, 9 — steps down until the frame fits the 195-message BM cap |
| Timezone / window | America/New_York, RTC time source |

Observations for the design:

- The field image is **JPEG-encoded twice** (ISP q95, then re-encoded at the ladder quality), with AWB-chosen colors baked in before either encode. Arm C avoids both losses.
- For a fair A/B, use the **same 1600×900 → 1000×562 geometry** and a fixed quality equal to what `bmcam001` usually lands on in the field (OQ below). Also run one "unconstrained" quality (q85) as a ceiling.
- Nothing is known yet about the colour gains AWB picks underwater. If the backend or logs kept EXIF/metadata, pull it (OQ).

---

## 10. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| OpenMV Bayer is 8-bit only / `BAYER` fails on the AE3 | Weak RAW advantage on PAG7936 cameras | Verify in P0; decide on OQ-19 firmware update; record as a hardware-roadmap finding |
| Card truth = design values | ΔE biased | L2 uses camera-relative normalization (unaffected); do Option B; versioned truth file |
| Pool ≠ ocean | Parameters don't transfer | Scope pool as a pipeline test; TG-7 fit is the ocean prior |
| Leak during 5-day soak | Lose hardware + data | Pressure-test housing with a moisture indicator before electronics; leak sensor; daily data pull |
| IMX708 12 MP RAW processing load on the Pi 5 | Missed cadence | Log processing time; fall back to binned sensor mode (2304×1296) for the soak if needed |
| Clouds change light between Arm A and C captures | Noisy A/B | Alternate order; many repeats; compare distributions |
| AprilTag detection fails at distance / in haze (confirmed: 133/309 TG-7 frames) | Missing card frames | Sweep-neighbor search window → manual 4-corner clicks (P1.1); fixed rig → saved homography per session |
| TG-7 distance-to-card not in EXIF | No `z` for the physics model | Card-size distance with underwater intrinsics from the checkerboard reshoot; validate at taped pool distances (P1.4) |
| Sep 15 dive near sunset — surface light changing during the depth sweep | Biased K (depth vs light confounded) | Fit K per dive; flag / down-weight the dusk dive; use `DateTimeUTC` for sun angle |
| Only 10 weak surface card shots from the trip | L1 CCM and daylight normalizer poorly constrained | TG-7 above-water reshoot (P1.4) before S2 |
| Sun glint / caustics on the card at 3–9 ft | Patch noise | Patch medians; flag high-variance patches; favor overcast / low sun in P4 |
| V2 card water damage (white / light-grey patches, from end of dive 1) | Wrong reflectance for the key neutral patches → biased attenuation and WB | Patch-level QC exclusion; clean dive-1 subset; report metrics split by card condition; **no repair attempts** (time box §1a); card V3 for the repeat dive |
| Scope creep into the Phase 0–6 capture path | Destabilizes working rig | `color/` isolated; capture changes behind `raw: true` flags; small PRs |
| Licenses (rawpy LGPL; colour-checker-detection YOLO path AGPL) | Can't ship | rawpy Mac-only; don't use YOLO detector (we use AprilTags anyway); license check in CI |

---

## 11. Open questions

**For Claude Code (answer from repo / hardware; log in `docs/open_questions.md`):**

1. `sensor.BAYER` is noted as working on the AE3 (`board_config.py`). Confirm on the N6. For both: what bit depth and resolutions? Can WB be neutral/off in Bayer mode? Does anything require the v5.0.0 / `csi` migration (OQ-19)?
2. Can the N6 return RAW and ISP JPEG from the same exposure?
3. Transfer time for a 1 MP Bayer frame over the existing length-framed USB path.
4. Does `rpicam-still --raw` on the Pi 5 produce a DNG that matches the JPEG frame and records the applied gains/exposure?
5. Pi 5 processing time for full-res IMX708 RAW through physics v0.

**For Nick:**

6. Pool housing plan: all cameras + Pi in one housing, or cameras housed and cabled to a dry poolside Pi?
7. X-Rite ColorChecker Classic available for the Option B card-truth session?
8. What JPEG quality rung does `bmcam001` typically land on in the field (from backend logs)? Is any metadata (colour gains, exposure) retained for its images?
9. ~~TG-7 dataset contents~~ — answered in §7 P1.0. Remaining: when can the P1.4 above-water / checkerboard reshoot happen? Is the flat front glass the only port (no dome / wet lens used on the trip)?
10. Leak sensor available for the soak?

---

## 12. Suggested sprint ladder

| Sprint | Goal | Demo |
|---|---|---|
| **S0** | SPEC amendment (Phase 8, scope notes); `color/` scaffold; license CI; `raw_io` for DNG + ORF (Mac) | Load a TG-7 ORF and an IMX708 DNG → linear image + metadata printout |
| **S1** | TG-7 dataset prep (P1.1): sweep/dive IDs, card localization with fallback + manual-corner tool, distance per frame, quality filter; patch sampling in RAW coords; metrics ψ / ΔE2000 / red SNR | Manifest with sweep, distance and card corners for all 206 reference frames; scored contact sheet per sweep |
| **S2** | Physics v0 on TG-7: per-sweep / per-dive fits, card mode, table mode, leave-one-sweep/dive-out; Olympus-preset same-RAW comparison; backend-correction baseline; off-center + no-card sheets. Uses the P1.4 reshoot for L1 | **Decision gate (§1a):** one HTML report comparing Nereus vs backend vs Olympus, with clean-card and damaged-card subsets reported separately |
| **S3** | RAW capture on rig (P0): IMX708 `--raw`; OpenMV `capture_raw`; locked recipes; OQ-19 decision | One command → 3 cameras × RAW + ISP JPEG + metadata |
| **S4** | Above-water calibration (P2) + card truth v1 | `configs/calibration/*.yaml`; CCM residuals |
| **S5** | `bmcam001` recipe + arms A / A′ / B / C in the coordinator (P3) | Bench A/B/C sheet on the card |
| **S6** | Pool sweep profile + gain sweep; run P4 | Depth-sweep report |
| **S7** | `nereus-rig soak` loop, daily summary + data pull; run P5 | Live daily summary during the soak |
| **S8** | Final analysis + report (P6) | A-vs-C result, RQ-1, gain sweep, `bmcam001` quick wins |

S0–S2 are built as the reusable tool (§1a): card and camera as config files, stage-by-stage CLI, standard report. **Recommendation: hold S3–S8 (rig work) until the S2 decision gate** unless the rig is needed for something else. A no-go at S2 would change what the rig should test.

S0–S1 need no rig hardware and can start now. S2 needs the P1.4 TG-7 reshoot (about an hour of shooting) for camera calibration and underwater intrinsics.

---

## References

- Akkaynak & Treibitz: *A Revised Underwater Image Formation Model* (CVPR 2018); *Sea-thru* (CVPR 2019); *What is the space of attenuation coefficients in underwater computer vision?* (CVPR 2017).
- Nereus BM Camera project note *Underwater Color Correction — Research Brief* (Sep 2026): methods survey, licensing verdicts, card-distance math. Not in this repo; this brief carries the parts needed here.
- OpenMV `sensor` API: https://docs.openmv.io/library/omv.sensor.html · N6 quick reference: https://docs.openmv.io/openmvcam/quickref/openmv-n6.html
- Raspberry Pi camera software docs (`rpicam-still --raw`, tuning files): https://www.raspberrypi.com/documentation/computers/camera_software.html
- colour-science (`colour.characterisation`): https://github.com/colour-science/colour

---

## Appendix A — Kickoff prompt for the Claude Code session

```text
You're starting Phase 8 (Edge color correction) in the nereus-camera-test-rig repo.

Read first, in this order:
1. CLAUDE.md
2. docs/SPEC_nereus_camera_test_rig.md
3. docs/DESIGN_edge_color_correction.md (v0.3) — the design brief for this phase. §1a (MVP framing: build a
   reusable tool + decision gate) and §7 P1.0–P1.5 (TG-7 dataset, card water damage, reshoot) matter most.
4. docs/open_questions.md, especially OQ-19 and OQ-20.
5. data/tg7_channel_islands/README.md and manifest.csv (git-ignored, local only — 309 TG-7 shots sorted into
   raw/0_surface_card … raw/4_no_card).

Your first job is planning, not code:
A. Open a PR that amends the SPEC:
   - add "Phase 8 — Edge color correction" to the Build Plan, with checklist items and exit criteria for
     S0, S1, S2 in full, and S3–S8 as an outline marked "gated on the S2 decision";
   - update §2 scope: automatic underwater color correction is now in scope (as Phase 8), and the P5 soak
     is a simple experiment loop, not a daemon;
   - add new open questions from the brief's §11 to docs/open_questions.md, continuing the OQ numbering.
     Resolve any you can answer from the code (e.g. BAYER support in openmv/*/board_config.py) and say how.
B. In the same PR description, give a sprint outline for S0 broken into ~300-LoC nibbles, each with a
   demo/exit check.
C. Stop and wait for my approval before writing any Phase 8 code.

Ground rules for all Phase 8 work:
- color/ is its own module (src/nereus_camera_test_rig/color/). Don't destabilize the Phase 0–6 capture path.
- Runtime dependencies must be MIT/BSD/Apache-2.0. rawpy (LGPL) only in Mac-side tools. Add a license check.
- Card and camera are config files (configs/cards/, configs/calibration/), not code.
- Never move, rename, edit or delete anything under data/ — read it, and write outputs to results/.
- The V2 card took on water: exclude damaged patches by a simple QC rule, never try to repair or model the
  staining. If a result needs the excluded data, write "needs V3 dataset" and move on.
- S0–S2 run on the Mac with no rig hardware. Rig sprints (S3+) wait for the S2 decision gate.
- Small PRs, one concern each, feature branches; I review and merge.
```
