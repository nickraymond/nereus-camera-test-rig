# Proposal for the camera build (bm_cam_legacy #120): pick the distance from the byte target

**For:** the Sprint28 camera build session (owner of bm #120, `rc_raw_jxl.py`). **From:** the
nereus-camera-test-rig study, 2026-10-03, via the EM. **Status:** prototype measured on nereus002
(Pi Zero 2 W, same OS / kernel / rpicam / RAM / CMA as bmcam003/004, see `r0/ENV_COMPARE.md`). No
PR to bm_cam_legacy — this is a proposal with numbers.

## The ask (Nick)

"Tell the camera the ROI and the message cap; it picks the best quality that fits."

## Today (bm #120)

`rung_walk` encodes `still.raw.distances` = [3.8, 4.6, 5.95, 8.25] in order and sends the first
rung that fits `still.message_cap`. Those rungs are calibrated for the 1600×900 field crop. For
any other ROI they either waste most of the budget or never fit:

| ROI (card-centred, 3 frames) | cap | fixed rungs: encodes → d, fill of the cap | byte-target: encodes → d, fill |
|---|---|---|---|
| 800×450 | 180 | 1 → d 3.8, **43 %** | 3 → d 1.07, 97 % |
| 1200×676 | 180 | 1 → d 3.8, **68 %** | 2 → d 2.28, 99 % |
| 1600×900 | 180 | 1 → d 3.8, 99 % | 1 → d 3.87, 97.5 % |
| 1600×900 | 195 | 1 → d 3.8, 91 % | 1 → d 3.50, 97 % |
| 2000×1124 | 180 | **4** → d 8.25, 82 % | 1 → d 6.75, 95 % |
| 2000×1124 | 195 | 3 → d 5.95, 96 % | 1 → d 6.11, 94.5 % |
| 2304×1296 | 180 / 195 | 4 encodes, **nothing fits → pjpg** | 2 → d 10.3 / 9.35, 97 % |
| 3072×1728 | 180 / 195 | 4 encodes, nothing fits → pjpg | **0 encodes**: quality floor → pjpg in < 1 s |

(Fixed-rung column: desk encodes with the production encoder, byte-identical to the Pi.
Byte-target column: measured on nereus002, `byte_target_bench.sh`.) Lower distance = better image;
unused budget is quality thrown away (d 3.8 at 800×450 vs d 1.07 at the same cost).

## The prototype (`compression_study/presets/byte_target.py`, ~140 lines, stdlib + numpy)

Uses only bm #120's own functions (`read_dng_crop`, `code_planes`, `encode_rung` under
`run_capped`, `build_params`, `seal_container`, `message_count`), so the bytes it measures are
exactly what would be sent. At most **3 encodes**:

1. **Prior** — bytes ≈ 51 kB · (area / 1.44 MP) · (d / 3.8)^−0.8 (fitted on the 2026-10-03 sweep).
   Encode at the distance that lands at 97 % of the cap's bytes.
2. **Correct** with the prior slope (log bytes vs log d), aiming at 97 % again.
3. **Secant** through the two measured points (log–log), aiming at 97 % again.

It stops as soon as a result fits and fills ≥ 93 % of the cap, else keeps the largest result that
fits. Distances stay inside the production range 0.1–15.

**Quality floor:** if the planned distance exceeds `d_max` (10.4), it stops before encoding and
reports `fallback`, so the caller sends pjpg or a smaller ROI. On these frames, card-area SSIM vs
RAW falls to today's pjpg level (0.935) at d ≈ 10.5. The floor is a distance because SSIM vs RAW
cannot be computed cheaply on the unit (it needs a decode plus a reference render).

## Measured on nereus002 (3 frames × 6 ROIs × caps 180 / 195, effort 5; plus 2000×1124 at e7 / 195)

- **Accuracy:** all 33 searches that encoded fit the cap, none over. Fill was 94.4–99.0 % (median
  97 %), against a target of 97 %. The other 6 runs (3072×1728) stopped at the quality floor.
- **Encodes:** 1 for 1600×900 and 2000×1124, 2 for 1200×676 and 2304×1296, 3 for 800×450. The
  prior over-predicts bytes for small card-filled crops; see "Next" below.
- **Time** (process start to result, incl. the DNG read of ~0.9 s): 800×450 6.9 s;
  1200×676 9.1 s; 1600×900 8.0 s; 2000×1124 12.0 s (e7/195: 27.5 s, 2 encodes); 2304×1296 30.3 s;
  3072×1728 0.9 s to fallback.
- **Memory:** cjxl peak 27–63 MiB under the production 250 MiB guard; the Python process peaks at
  27–65 MiB (65 MiB includes reading the 3072×1728 crop). Every run was additionally capped at
  700 MiB with `ulimit -v`, and nereus002's other services kept running.
- **Same frames → same bytes:** the Mac desk test and the Pi give identical sizes per distance.

## Proposed change in bm #120 (small)

- New key `still.raw.target_fill` (default 0.97), keeping `still.message_cap` as the cap. The
  `rung_walk` loop becomes this 3-step search; `still.raw.distances` stays as a fallback list if
  the search fails.
- New key `still.raw.d_max` (default 10.4, range 0.1–15) for the quality floor. On a floor hit,
  `RawFallback("floor")` sends pjpg with START `rfb=floor`.
- **Seed from the last wake:** store (ROI, d, bytes) in the state file. The scene and ROI rarely
  change between wakes, so step 1 starts from the last result and usually needs 1 encode. That
  would bring 2304×1296 from 30 s to ~16 s (ESTIMATE from the per-encode times).
- The per-rung timeout logic stays as it is (time budget minus the pjpg fallback's send).

## Limits / not done

- One scene (indoor, card at ~1.5 m); the prior constant and `d_max` are scene-dependent and
  should be re-checked on the R4 field frames. The floor uses distance as a proxy for detail; the
  2000×1124 / 2304×1296 WARN in Step A was about tag-edge acutance, which a distance floor does
  not see.
- Not run on bmcam003/004 (busy until ~Sunday); R0 proxy and environment comparison in
  `runs/r0_nereus002_20261003T210546Z/` and `r0/ENV_COMPARE.md`.
