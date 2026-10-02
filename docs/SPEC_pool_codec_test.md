# Pool Test: On-Board Raw Compression (hydrium vs wl53)
## Test specification

**Status:** Draft for Nick's review (2026-10-01)
**Parent:** `docs/SPEC_nereus_camera_test_rig.md` §4, "Raw compression study"; results so far in
`compression_study/REPORT.md` ("hydrium vs wl53 on the OpenMV boards", PR #85).
**Cameras:** OpenMV N6 and AE3 (primary); IMX708 on the Pi (secondary arm, optional).
**Scope:** packing/compression and decoding only. No backend integration, no new colour
correction. The Mac tool is the reference decoder (a stand-in for the backend).

---

## 1. Question

On real water, does the camera-side encoder chosen in the lab (hydrium, JPEG XL) still give
better colour than wl53 at the field-link budget, and does the full chain (encode on the board →
transfer → decode on a server → card colour correction) work without failures?

The lab result was on simulated water (red ×0.14 at capture): at 0.4 bpp hydrium had about half
wl53's card colour error and 2–4× lower whole-frame colour error; wl53 was better at 0.8 bpp under
water and faster. The pool checks this with real optics: scattering, caustics, the housing port,
real depth-dependent colour loss, and the boards running in a housing.

## 2. Design principle: paired captures

Every capture is **one exposure**. From that same frame the board produces:

1. the **lossless** planes (the existing `nrpack` packer, bit-exact) = ground truth;
2. **hydrium** at 0.4 bpp and 0.8 bpp;
3. **wl53** at 0.4 bpp and 0.8 bpp.

Codec error = (decoded image) vs (lossless image of the same frame), after the same card
correction. Water, light, caustics and pose are identical across codecs, so the comparison stays
fair no matter how the pool light changes. Today's camera JPEG is computed on the Mac from the
lossless frame (study method M1) so it is paired too; one camera-made JPEG per set is also
captured right after, as an unpaired reference of what the camera ships today.

## 3. Conditions

| Factor | Levels | Notes |
|---|---|---|
| Depth (camera) | 3 / 6 / 9 ft (0.9 / 1.8 / 2.7 m) | Same as the Phase 8 pool sweep (P4); run both tests in one session if the rig allows |
| Card distance | ~0.5 m | V1 card (the rig card, OQ-50); V1 colour patches ≥ 40 px on N6/AE3 at 0.5 m (S4) |
| Light | daylight (note sun/cloud), plus one dusk set | Dusk = higher gain = more noise, the hard case for codecs |
| Bitrate | 0.4 bpp (51,200 B per HD frame), 0.8 bpp (102,400 B) | 0.4 bpp is the field-link budget |
| Exposure | locked, metered on the card (`scripts/capture_raw_openmv.py` recipe) | Sensor defaults otherwise: denoise + lens shading ON (Nick) |
| Repeats | 10 captures per depth per camera, ~30 s apart | 3 depths × 2 cameras × 10 = 60 paired captures (+ dusk 2 × 10) |

Optional, only if time allows:
- **Pool + simulated depth:** apply the study's binomial thinning (`compression_study/sim.py`) to
  the pool lossless frames to reach the 15 m-like red loss the lab used. Real pool optics + the
  lab's red starvation; costs nothing on site.
- **IMX708 field crop:** the Pi encodes the 1600×900 crop to 50 kB with hydrium (C on the Pi,
  1.3 s) and wl53 (0.36 s), paired with its DNG.

## 4. Pass criteria (pre-registered, per camera)

**Reliability (must all hold):**
- R1. 0 transfer failures: every stream arrives with a matching SHA-256 (the rig protocol's framed
  transfer), across all captures.
- R2. 0 decode failures: every stream decodes with the reference decoder (§6).
- R3. 0 board failures that lose a capture (MemoryError, reset, USB drop). A recovered retry
  counts as a failure in the log, not as a lost capture.
- R4. Card found (all 4 AprilTags) on the decoded raw in every capture where it is found on the
  lossless raw, for both codecs at 0.4 bpp.
- R5. Size within ±5 % of the target in ≥ 95 % of captures (the on-board rate search).

**Colour (decides the codec):**
- C1. At 0.4 bpp, hydrium's card colour error (stress ΔE00 after card correction, decoded vs
  lossless) has a median ≥ 30 % lower than wl53's, and hydrium wins ≥ 70 % of paired captures.
  Sweep-level bootstrap 95 % CI of the paired difference excludes 0.
- C2. Whole-frame colour error (median 16×16-block ΔE00) at 0.4 bpp: hydrium ≤ wl53.
- C3. Report, not gate: the same at 0.8 bpp; tag-region SSIM; per-depth breakdown; how well the
  lab's simulated-water numbers predicted the pool.

**Cost (report, gate only on fit):**
- K1. Encode + rate search per frame and per codec; heap free before/after; board temperature if
  readable. Gate: the whole capture (lossless + 4 streams + transfer) fits in 60 s per board.

**Decision:**
- R1–R5 and C1 hold → hydrium is the OpenMV field encoder; wl53 stays as the fallback.
- R1–R5 hold, C1 fails → wl53 is the default (smaller, faster, simpler); hydrium is shelved
  with the numbers.
- Any R fails → fix and re-run that part before deciding (a reliability failure is a bug, not a
  codec verdict).

## 5. What has to be built first (bench, before the pool)

The lab probe (`openmv/probes/hyd_probe_v5.py`) proved the codecs but moves data over the USB
console (base64, CRC, 3 copies): ~6 min per frame, too slow for 60+ captures. Pool runs go through
the rig service instead.

1. **Board service command `compress_raw`** (allowlisted, `openmv/common/capture_service.py`):
   take the last `capture_raw` frame from RAM; run nrpack (lossless) and the requested codecs at
   the requested byte targets (on-board rate search, hydrium knob 0.05–6, wl53 Q 0.5–4000); return
   each plane stream through the existing framed, SHA-256-checked transfer, plus a JSON sidecar
   (codec, knob, bytes per plane, encode ms per plane, search steps, heap free). Native modules
   `nrhyd.mpy`, `nrwl53.mpy`, `nrpack.mpy` deployed to `/flash` by `host_tools/deploy_openmv`.
   The 768 KB hydrium tile buffer is allocated once at service start (AE3 heap, OQ-56).
2. **Pi side:** `nereus-rig experiment --raw --codecs hyd:51200,hyd:102400,wl53:51200,wl53:102400`
   (or a profile `configs/experiments/pool_codec_test.yaml`). The Pi wraps each codec's 4 plane
   streams in the study container (`compression_study/common.py` `Header` + `pack`: dims, CFA,
   black/white, codec, plane lengths) and stores them next to the RAW in the §13 folder:
   `captures/<camera>/{raw.bayer, lossless.nr, hyd_T1.nr, hyd_T2.nr, wl53_T1.nr, wl53_T2.nr,
   compress.json}`. Raw evidence is never overwritten.
3. **Mac scorer** `compression_study/pool_score.py`: decode every container, check SHA-256s,
   rebuild the lossless frame, run the card locate + correction on lossless and on each decode,
   write per-capture rows (`work/pool/rows.json`) and the report section; a slider page from the
   same frames (`before-after-report` skill).
4. **Bench gate (in air, rig as installed in the housing, before going wet):** 20 captures per
   board through the whole chain; R1–R5 must hold, and the in-air colour numbers must reproduce
   the S4 lab values within the repeat noise. Also run `compress_raw` with a GC forced (heap
   filled) on the AE3, as `openmv/probes/hyd_stress_v5.py` did.
5. **Wet dry-run:** a bucket or bathtub with the housing, 10 captures — checks the housing, port
   reflections, cable and card mounting before the pool day.

## 6. Decoding test plan

The point: prove the files can be used by someone who only has the bytes.

1. **Reference decode** (Mac, study code): `compression_study.methods.raw_planes.decode(blob)` —
   hydrium planes with libjxl `djxl` (float output, inverse sRGB → linear), wl53 with
   `methods/wl53.c`. Output: the Bayer mosaic in sensor counts.
2. **Second, independent JPEG XL decoder** on every hydrium stream: jxl-oxide (Rust; a separate
   implementation from libjxl; check its licence against §20 before use). Pass: max per-pixel difference to djxl within the
   JPEG XL conformance tolerance (record the observed max; expect well under 1 DN). This backs the
   "any stock decoder" claim. (Not yet tried here: verify the tool's CLI before relying on it.)
3. **Decode on the Pi** (libjxl-tools 0.11 is installed on `nereus002`): same output as the Mac
   within the same tolerance; record decode time per frame (a cheap stand-in for a small server).
4. **Self-description:** every container decodes with no out-of-band knowledge (dims, CFA,
   black/white, codec, knob all in the header). Test: decode a random sample from a clean
   checkout with only the files.
5. **Determinism:** wl53 decodes bit-identically on Mac and Pi (integer codec); hydrium decodes
   within tolerance across decoders/platforms (floating point).
6. **Damage handling (offline, on pool files):** truncate streams and flip bits in a copy; every
   case must fail loudly (SHA-256 mismatch first; the decoder's own error where it gets that far),
   never decode silently into a wrong image. 50 damaged copies per codec.
7. **Downstream use:** the card is located and the correction runs on every decoded frame (R4);
   colour error vs lossless reported per capture (C1–C3).

## 7. Pool-day run sheet

Before leaving:
- [ ] Bench gate (§5.4) and wet dry-run (§5.5) passed; firmware, modules and the branch SHA
      recorded in `experiment.json`.
- [ ] Workbench recipe stopped (`curl -X POST localhost:8088/api/stop`), HIL soak not running.
- [ ] Card cleaned and photographed in air (pre-dive reference); tape measure; slate for notes.

At the pool, per depth:
1. Mount at depth, card at ~0.5 m (tape-measure, note it). Wait 2 min for bubbles to clear.
2. Meter on the card once (locked exposure); record exposure/gain.
3. Run 10 captures per board (~30 s apart). Watch the per-capture summary line (sizes, SHA OK,
   card found, encode s). Stop and note if any R-criterion fails.
4. Log: time, sun/cloud, water clarity, pool surface colour, depth, distance, anything moved.

After: pull the experiment folders from the Pi (never edit them), run `pool_score.py` on the Mac,
then the report + slider page.

## 8. Limits and open items

- **Housing (OQ-26, open, blocks the pool):** cameras + Pi in one housing, or cameras housed and
  cabled to a poolside Pi? USB cable length and hub power matter for the N6 (OQ-53).
- **Pool ≠ ocean:** at 2.7 m in clear water red loss is far smaller than at 15 m, so the pool
  alone may not reproduce the red starvation where hydrium won most. Hence the optional "pool +
  simulated depth" analysis (§3) and the follow-up sea deployment.
- **Card:** V1 card; V3 is not printed yet (OQ-44/45). The codec comparison needs only a stable
  card, not its absolute colours (paired design).
- **Time on site:** ~45 s per capture per board through the service (estimate: 1 MB raw transfer
  ~1 s, encodes + searches ~25 s on the AE3) → ~10 min per depth per board.
- **Not tested here:** backend integration, satellite/cellular link, long soak (Phase 8 S7).

## 9. Deliverables

- `docs/SPEC_pool_codec_test.md` (this file), updated with results.
- Code: `compress_raw` service command + tests on hardware, Pi `--codecs` option,
  `compression_study/pool_score.py`.
- Data: §13 experiment folders on the Pi; rows + report section; slider page.
- A one-line decision recorded in the main SPEC (compression study list).
