# Ladder: ColorChecker Classic → V3 c1 card truth (for 2026-10-06)

Nick's ColorChecker Classic (Calibrite, 24 patches) arrives Tue 10/6. Each rung has a pass rule.
Don't climb past a failed rung.

**Rig state:** nereus002 keeps its snapshot-4 placement ("placement OK", 2026-10-05 22:56Z).

**Before every capture:**
- Check the workbench is idle:

  ```bash
  curl -s http://nereus002:8088/api/runner
  ```

- Its state must be `idle`. Nick's services keep running.

**Commands:**
- Rig commands run on nereus002 from `~/nereus-camera-test-rig`, after deploying this branch.
- Mac commands run from the repo root.

## Rung 0: where the ColorChecker goes (~5 min, no rig change by me)

**Why not below the V3 card:**
- The ColorChecker's patch area is roughly twice the Pixel Perfect's: ~26 × 17 cm (measure it).
- Below the V3 card it would run ~4 cm past the bottom of the N6 frame.
- The board can't move up: the V3 card's top is only 36 px (~15 mm) inside the IMX708 ROI.

**Plan:**
- ColorChecker **landscape, to the LEFT of the V3 card**, ~2 cm gap, vertically centred on the
  card.
- Upright: dark skin top-left, white bottom-left.
- Same board, so the same plane.

From snapshot 4, the room left of the card is:

| camera | room left of the card | room right of the card |
|---|---|---|
| N6 | ~27 cm (tight but enough) | 20 cm (not enough) |
| AE3 | ~35 cm | 24 cm (not enough) |
| IMX708 | plenty | — |

**Check:** one snapshot per camera, as tonight:

```bash
nereus-rig experiment --type cc_placement --raw --no-analysis
```

**Pass when all hold on all 3 cameras:**
- V3 tags 4/4.
- The whole V3 card is in frame.
- The IMX708 card is inside the ROI [1504, 846, 1600, 900].
- All 24 ColorChecker patches are in frame, with no glare on any patch.

## Rung 1: acceptance of the ColorChecker (IMX708, ~10 min)

1. **Capture** with locked exposure, focus fixed at 1.09 dpt (0.914 m), lowest gain:

   ```bash
   .venv/bin/python scripts/capture_raw_imx708.py --card configs/cards/nereus_v3_c1.yaml --target 0.6 --lens-position 1.09 --gain 1.0 --stops 0 --repeat 3
   ```

   - `--target 0.6`, not 0.8: the ColorChecker white (L\* 95) is ~1.4× brighter than the V3
     card's brightest patch, so it would clip at 0.8.
2. **Check against the published values** (Mac):

   ```bash
   python -m host_tools.chart_vs_colorchecker --chart configs/charts/colorchecker_classic_24.yaml --only-extra --out results/card_truth/cc_acceptance.json --extra "cc_r0=<dng>=<stop json>=configs/cards/nereus_v3_c1.yaml=-3300,-400,-100,3100"
   ```

   This gives ΔE00 through the camera's factory CCM. The same tool gave 5–7 for the Pixel
   Perfect.
3. **Own-fit check:**

   ```bash
   python -m host_tools.card_truth <session> --dry-run
   ```

   Use the session template with chart `colorchecker_classic_24`. It prints the
   leave-one-patch-out ΔE00 of a 3×3 fit on the ColorChecker itself.

**Pass when:**
- 24/24 patches are found and none clips.
- The **held-out ΔE00 median ≤ 2.0** (3×3). This means the camera is linear and the chart
  matches its published values.
- The factory-CCM ΔE00 is clearly below the Pixel Perfect's 5–7 (informative).

**If it fails:** glare (move the lights out), clipping (lower `--target`), or a wrong
orientation or reference. Check the overlay PNG.

## Rung 2: the truth shoot (all 3 cameras, ~20 min)

**Setup:** same placement, lights at ~45° and ≥ 1 m, no room light or daylight. Dry card.

1. **IMX708**, a small bracket, 3 frames each:

   ```bash
   .venv/bin/python scripts/capture_raw_imx708.py --card configs/cards/nereus_v3_c1.yaml --target 0.6 --lens-position 1.09 --gain 1.0 --stops -0.5 0 --repeat 3
   ```

2. **N6** (locked at the lowest gain; the board is reset before each shot):

   ```bash
   .venv/bin/python scripts/capture_raw_openmv.py --board n6 --card configs/cards/nereus_v3_c1.yaml --target 0.6 --stops -0.5 0 --repeat 3
   ```

3. **AE3:**

   ```bash
   .venv/bin/python scripts/capture_raw_openmv.py --board ae3 --card configs/cards/nereus_v3_c1.yaml --target 0.6 --stops -0.5 0 --repeat 3
   ```

4. **Flat-field**, 3 frames per camera:
   - A matte board over both charts in the same plane.
   - Run the same commands without `--card` (IMX708) and with `--stops 0 --repeat 3`.
5. **Copy to the Mac**, one folder per camera:
   `data/v3_truth/c1_dry_20261006/{imx708,n6,ae3}/{cards,flat}/`.

**Pass when:**
- Every read-back check is PASS (exit 0).
- No V3 or ColorChecker patch clips at stop 0.

## Rung 3: fit and write the measured block (Mac, ~15 min)

1. **One session per camera**, copied from `configs/calibration/sessions/v3c1_truth_TEMPLATE.yaml`:
   - `frames` = that camera's `cards` folder; `flat` = its `flat` folder;
   - `region` stays left of the card.
2. **Run each with `--dry-run` first:**

   ```bash
   python -m host_tools.card_truth configs/calibration/sessions/v3c1_truth_dry_20261006_<camera>.yaml --dry-run
   ```

   It prints the held-out ΔE00 (3×3 and root-poly) and every V3 patch's Lab.
3. **Cross-check the cameras:**
   - Compare the V3 patch Lab between the three cameras.
   - **Pass:** median ΔE00 between cameras ≤ 2, and held-out ΔE00 median ≤ 2 on each.
4. **Write the block from the IMX708 session** (10-bit, best resolution):

   ```bash
   python -m host_tools.card_truth configs/calibration/sessions/v3c1_truth_dry_20261006_imx708.yaml
   ```

   - The N6 / AE3 values go in the PR as the cross-check.
   - If a camera disagrees by more than 2 ΔE00, flag it and don't average it in.
5. **Commit** `configs/cards/nereus_v3_c1.yaml` (the `measured:` block, with provenance) and the
   session files. The RAWs stay in `data/`.

**Wet:** repeat Rungs 2–3 with the card wet and `condition: wet` in the sessions. This writes
`measured_wet:`.
