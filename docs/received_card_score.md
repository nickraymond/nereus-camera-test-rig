# Received-image V3 card score (`host_tools/received_card_score.py`)

Scores the V3 c1 reference card in images **as received from the backend**. It is a Mac-side, one-shot tool with no cloud code. It was built 2026-10-08 so the first weekend images with the card (Fri 10/9 afternoon) can be scored the same day.

```bash
python -m host_tools.received_card_score <image> [<image> ...] [--output-dir DIR] \
    [--truth provisional|yaml|design] [--calib card_calib.json]
```

## Inputs

| input | how to get it | colour paths scored |
|---|---|---|
| **Linear DNG** (B3a, LinearRaw, profile v2) | backend media page → "Linear DNG (.dng)" (admin), or `nrjxl_dng.build_dng(blob)` on the `.nrjxl` | `dng_camera`, `dng_cardwb`, and `dng_calib` with `--calib` |
| **display JPEG** (or any 8-bit sRGB JPEG/PNG) | backend display image | `display` |

**Truth** (`--truth`) is written into every output:
- **`provisional`** (default): the IMX708 measurement of V3 c1 from 2026-10-06 (`docs/card_truth/truth_20261006/truth_alsc.json`). It had no flat-field, so card L\* is uncertain by up to ~10–15.
- **`yaml`**: the card YAML's `measured: lab_d50` block, once the paper flat-field is done.
- **`design`**: the print file.

## What it does, per image

1. **Finds the card:** tag25h9 tags 0–3 on an 8-bit view, using the rig's tested `locate_frame`.
   - A DNG's view is its linear green, normalised and gamma-encoded.
   - A dim JPEG gets a contrast-stretched retry.
2. **Samples** the 12 V3 patches (the central 60 % of each canonical box).
3. **Converts** each patch to Lab D50 by each path. Every path gets one exposure scale (gray_light's Y set to the truth's) and no other colour change:
   - **`dng_camera`:** camera RGB → XYZ D65 by the DNG's own `ColorMatrix1` (the unit's libcamera CCM + AWB, as the backend wrote it) → Bradford D50. This is the transmitted colour as the backend interprets it.
   - **`dng_cardwb`:** the same matrix, white-balanced on the V3 greys instead of the camera's AWB.
   - **`dng_calib`** (IMX708 only): white balance on the V3 greys, then the 2026-10-06 bench root-poly matrix (`scripts/s28_card_calib.py`).
   - **`display`:** the 8-bit sRGB image.
4. **Scores** the 8 colour patches against the truth:
   - ΔE2000;
   - hue-chroma ΔE2000 (L\* set to the truth's);
   - ΔE2000 vs the design.

   The greys are anchors, so they are reported per patch but not averaged.

## Outputs

These go to `results/received_card_<UTC date>/` by default:

| file | contents |
|---|---|
| `received_scores.csv` / `.json` | one row per image × path: mean / median / worst ΔE00 and the worst patch, the hue-chroma mean, ΔE00 vs design, the largest grey chroma, clipped patches, the truth used, any error |
| `patches.csv` | every patch: L\* a\* b\*, ΔE00, hue-chroma, vs design |
| `<stem>_overlay.jpg` | the patch boxes drawn on the image |
| `index.html` | the cut sheet: overlay + truth \| measured swatches per path |

A card that is not found is a reported failure; the batch continues and the tool exits 1.

## Assumptions and limits

- **Card:** V3 c1 (tags 0–3), seen whole.
  - V2 cards use bm_cam_legacy `tools/bm_reference_card_*`.
  - CFA DNGs (B3 v1, bayer4) use the rig's raw pipeline instead.
- **No lens-distortion model:** a flat card at 1600×900 is near-projective. The overlay shows whether the boxes sit inside the patches.
- **Very dark display JPEGs** (Lux < 1 bench) can be too noisy for tag decoding. The DNG of the same frame still locates.
- **`dng_calib` is IMX708-specific:** the matrix was fitted on nereus002's `imx708_wide` under 5300 K LEDs. Elsewhere, read it as indicative.

## Smoke (2026-10-08)

The input was five nereus002 sunrise-sweep frames encoded by bm #134 (bac017f). They were decoded with the backend's own unmodified `nrjxl_dng.build_dng` / `raw_render.render_jpeg` (nereus-vision-dev staging d181d37, numpy 2.5.1, imagecodecs 2026.8.16) into the Linear DNG and display JPEG a user downloads.

| frame | dng_camera | dng_cardwb | dng_calib | display |
|---|---|---|---|---|
| 07:30 sunrise | 7.44 | 4.86 | 5.41 | 7.44 |
| 08:10 daylight | 6.11 | 4.88 | 5.79 | 6.18 |
| 10:20 LEDs, stock | 5.11 | 2.41 | 2.03 | 5.15 |
| 06:00 dark | 44.6 | 12.6 | 10.9 | card not found |

All values are mean ΔE00 on the 8 colour patches, vs the provisional truth.

**Cross-checks:**
- The display JPEG matches `dng_camera`. The backend renders with the same matrix.
- `dng_calib` 2.03 matches the Pi-side RAW analysis of the same frame before JPEG XL (1.98).

**Test:** `tests/unit/test_received_card_score.py` scores the synthetic V3 scene against the design and checks that a missing card is a reported failure.
