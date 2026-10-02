# Pool Test: Raw Capture, Compression and Colour Correction
## Test specification

**Status:** Draft, revised with Nick's decisions (2026-10-01)
**Parent:** `docs/SPEC_nereus_camera_test_rig.md` §4, "Raw compression study"; lab results in
`compression_study/REPORT.md` ("hydrium vs wl53 on the OpenMV boards", PR #85).
**Cameras:** IMX708 (Pi), OpenMV N6, OpenMV AE3 — all three in one housing with the Pi, aimed at
the same card, plus a depth sensor.
**Scope:** capture raw on the Pi; all compression, decoding and colour correction run on the Mac
afterwards. No on-board encoding in the pool, no backend integration.

---

## 1. Question

On real water, how much better are the colours from **raw data compressed to the field-link
budget and colour-corrected on the server** than from **today's camera JPEG** — and which encoder
(hydrium or wl53) keeps more of that benefit at ~50 kB?

The lab answer (simulated water): raw + hydrium at 0.4 bpp has about half wl53's card colour error,
and both beat today's JPEG by a wide margin. The pool checks it with real optics (scattering,
caustics, the housing port, real colour loss with depth) and gives customer-ready before/after
pages.

## 2. Why post-process on the Mac is valid

The encoders on the boards and on the Mac are byte-identical (hydrium and wl53: 72/72 board
planes and 20/20 Pi planes matched the Mac, PR #85). Encoding a saved raw frame on the Mac
therefore produces exactly the bytes a camera would send. Two things it does **not** cover — the
encoders' time, memory and stability inside the housing — were measured on the bench and are
re-checked later (§8).

Every comparison is **paired**: one saved raw frame → lossless reference, hydrium, wl53 and
today's JPEG (the study's M1, rendered from the same raw). Pool light and caustics change between
frames but never between the methods being compared.

## 3. Rig (Nick, 2026-10-01; resolves OQ-26)

- One housing: Pi Zero 2 W + IMX708 + N6 + AE3 on the powered hub, all aimed at the same card.
- Depth sensor in the housing; its reading is saved with every capture set (§5).
- Card: V1 (the rig card) at ~0.5 m; V1 colour patches ≥ 40 px on the N6/AE3 at that distance.
  A second card is not needed for this test.
- Ports: note the port type (flat or dome) and check each camera's view of the card through it
  in air before sealing.

## 4. Conditions

| Factor | Levels | Notes |
|---|---|---|
| Depth | 3 / 6 / 9 ft (0.9 / 1.8 / 2.7 m) | From the depth sensor, plus a tape check once |
| Card distance | ~0.5 m, fixed | Tape-measure and note |
| Light | daylight (note sun/cloud), plus one dusk set at 9 ft | Dusk = more gain and noise, the harder case for compression |
| Exposure | locked per depth, metered on the card (§6 step 2) | Sensor defaults otherwise: denoise + lens shading ON (Nick) |
| Repeats | 10 capture sets per depth, ~30 s apart | 3 depths × 10 = 30 sets (+10 at dusk) = 120 raw frames |
| In-air control | 10 sets at the surface before going in | Same card, same housing |

Optional (free, Mac only): apply the lab's simulated red loss (`compression_study/sim.py`) to the
pool frames as well. A 9 ft pool loses far less red than 15 m of ocean, and the extra loss is
where the codecs differed most.

## 5. What to build (capture side only)

Most of it exists: `nereus-rig experiment --raw` captures all three cameras into one §13 folder
(IMX708 DNG + JPEG, N6/AE3 8-bit Bayer + sidecar, SHA-256-checked; 111 s per set on `nereus002`
in the S3 demo). To add:

1. **Depth sensor reader** (`cameras/` style adapter or a small `sensors/depth.py`): read depth
   (m) and water temperature at the start and end of each capture set; write both into
   `experiment.json`. Sensor model and interface (I2C?) are not decided yet (§9).
2. **Locked, card-metered exposure for the experiment command:** `experiment --raw` locks the
   boards' own auto-exposure today. Pass the per-camera exposure/gain from a metering step (the
   `scripts/capture_raw_openmv.py` recipe for N6/AE3; `scripts/capture_raw_imx708.py` for the
   IMX708) so the card is bright and unclipped at every depth.
3. **A pool profile** `configs/experiments/pool_raw.yaml`: raw on, analysis off on the Pi
   (`--no-analysis`; the Pi Zero only captures), environment label `pool`, notes field for depth
   / distance / sun.
4. **A capture-loop command** that takes N sets ~30 s apart and prints one line per set (per
   camera: OK, file sizes, card brightness, depth). `scripts/hil_soak.py` already does the loop and
   health line; reuse it with the pool profile.
5. **Storage check:** ~26 MB per set (IMX708 DNG ~24 MB + 2 × 1 MB) → ~1 GB for the day. Check
   free space on the Pi's SD card before the pool day.

## 6. Pool-day run sheet

Before leaving:
- [ ] In-air rehearsal in the housing (sealed), 10 sets: all three cameras OK, card found on every
      RAW, depth sensor reads sensibly in air. Then a bucket/bathtub dry run, 5 sets.
- [ ] Nick's workbench recipe stopped (`curl -X POST localhost:8088/api/stop`); HIL soak not running.
- [ ] Card clean, photographed in air; tape measure; notes slate.

At each depth:
1. Lower the rig, card at ~0.5 m. Wait 2 min for bubbles and the housing to settle.
2. Meter on the card once and lock exposure/gain for all three cameras.
3. Run 10 capture sets ~30 s apart (~20 min). Watch the per-set line; stop if a camera fails
   twice in a row.
4. Note: time, sun/cloud, clarity, pool surface colour, depth reading, anything that moved.

After: copy the experiment folders from the Pi (never edit them in place); run the Mac
post-process (§7).

## 7. Mac post-process (the analysis)

For every saved raw frame (`compression_study/pool_score.py`, new; reuses the study's methods):

1. **Lossless reference** = the raw frame as saved.
2. **Encode** at the field budgets: hydrium and wl53 at 0.4 bpp (≈ 51 kB per OpenMV frame) and
   0.8 bpp; for the IMX708, the 1600×900 field crop at 50 kB (hydrium, wl53, and JPEG XL D2 as
   the Pi-side option). **Today's JPEG** (M1) at the same sizes from the same raw.
3. **Decode** each stream (§7a) back to raw.
4. **Colour-correct** lossless and every decode the same way: card located on the raw, card white
   balance (Phase 8 v0.2, the look Nick chose), then the card's colour patches scored.
5. **Report** per camera, depth and light:
   - colour benefit: corrected raw (lossless and each codec) vs today's JPEG, card ΔE00 against
     the card's measured colours;
   - codec cost: each codec's corrected colour vs the corrected lossless frame (stress ΔE00, block
     ΔE00, AprilTag-region SSIM);
   - card found on every decode (all four tags) where it is found on the lossless frame;
   - bytes per frame.
6. **Slider pages** (`before-after-report` skill): today's JPEG → corrected raw, and lossless →
   hydrium → wl53, at the whole frame, the card and an AprilTag at 1:1.

### 7a. Decoding checks (the files must be usable with only the bytes)

1. Reference decode: `raw_planes.decode` (djxl for hydrium, `methods/wl53.c` for wl53).
2. A second, independent JPEG XL decoder (jxl-oxide; check its licence against §20) on every
   hydrium stream: matches djxl within JPEG XL conformance tolerance (record the max difference).
3. Decode on the Pi (libjxl-tools is installed on `nereus002`): same result, record the time.
4. Self-describing files: the container header carries size, CFA, black/white level, codec and
   knob; decode a sample from a clean checkout with only the files.
5. Damaged copies (truncated, bit-flipped; 50 per codec) fail loudly — checksum mismatch or the
   decoder's own error — never a silently wrong image.

## 8. Pass criteria (pre-registered)

**Capture (must hold):**
- 30 of 30 pool sets (plus dusk) with all three RAWs saved and checksum-verified; a retry that
  recovers is logged as a failure but does not lose data.
- Card found (all four tags) on every lossless frame, every camera, every depth.
- Depth recorded for every set.

**Colour benefit (headline):** corrected raw at 0.4 bpp (best codec per camera) has ≥ 30 % lower
median card ΔE00 than today's JPEG at the same bytes, in every depth, for each camera.

**Codec choice:** at 0.4 bpp hydrium's codec error (corrected decode vs corrected lossless) is
≥ 30 % lower in median than wl53's, wins ≥ 70 % of paired frames, and its sweep-level bootstrap
95 % CI excludes 0 — per OpenMV camera. Holds → hydrium for the OpenMV link; fails → wl53 (smaller,
faster).

**Decoding:** every check in §7a passes.

**Later, on the boards (not pool day):** a short on-board run of the chosen encoder inside the
sealed housing (time, heap, temperature, 50 frames) with the existing probe, before field use.

## 9. Open items

- **Depth sensor:** model, interface and mounting (pressure port through the housing), and its
  accuracy; plus a tape check at one depth.
- **Housing thermal:** Pi Zero + 3 cameras in a sealed housing; log the Pi's temperature per set
  (the HIL health line has it).
- **Pool ≠ ocean:** at 2.7 m red loss is small; the optional simulated-depth analysis (§4) and a
  later sea deployment cover the deep case.
- **Card:** V1 until V3 is printed (OQ-44/45); fine here because the codec comparison is paired
  and the colour benefit is scored against the card's measured colours.

## 10. Deliverables

- This spec, updated with results; a decision line in the main SPEC.
- Code: depth reader, locked-exposure pass-through, `pool_raw` profile, `compression_study/pool_score.py`.
- Data: the §13 experiment folders (raw evidence, never edited); Mac rows + report section; slider
  pages.
