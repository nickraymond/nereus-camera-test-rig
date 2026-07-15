# Nereus Vision — Reef Reference Card V1

The canonical reference target for this evaluation rig. Every camera's ability to
resolve, localize, and crop this card is the project's **primary success metric**
(Spec §1, §13). This directory holds the ground-truth design file so the analysis
pipeline can be built and tuned against a known target.

## Files

| File | What it is |
|---|---|
| `Nereus_Reef_Reference_Card_V1.pdf` | The vector design file (11×17 in / tabloid, RGB, with crop + bleed marks). Source of truth. |
| `Nereus_Reef_Reference_Card_V1.png` | A 3000×1941 raster render of the PDF, for quick viewing and as a synthetic detection reference. Regenerate from the PDF; do not hand-edit. |

Both were carried over from the `bm_cam_legacy` prior-art repo
(`tools/Nereus_Reef_Reference_Card_V1_11x17_RGB_vector_crop_bleed.pdf`).

## Card layout

- **Title:** "Nereus Vision - Reef Reference Card V1".
- **Four corner AprilTags** for localization (see below).
- **Grayscale ramp** — 5 patches, white → black (exposure/contrast reference).
- **Center focus / white-balance target** — black/white quadrant disc, plus two small focus dots.
- **Coral + colour-correction patches** — two rows of colour references.
- **Scale bar** — 0–400 mm.

## AprilTags (verified by detection on the render)

- **Family:** `DICT_APRILTAG_36h11`.
- **Corner map (confirmed):**

  | Corner | Tag ID |
  |---|---|
  | Top-left | **0** |
  | Top-right | **1** |
  | Bottom-left | **2** |
  | Bottom-right | **3** |

- Matches the rig config default `analysis.apriltag.expected_tag_ids: [0, 1, 2, 3]`
  (Spec §12) and the legacy recommended corner map (`tl:0, tr:1, bl:2, br:3`).

These IDs were confirmed by running OpenCV ArUco (`DICT_APRILTAG_36h11`) on
`Nereus_Reef_Reference_Card_V1.png` — all four detected, one per corner. See
`docs/open_questions.md` OQ-11.

## Regenerating the PNG

Rasterize the PDF (any of these works):

```bash
# poppler
pdftoppm -png -r 200 Nereus_Reef_Reference_Card_V1.pdf card
# or macOS Quick Look
qlmanage -t -s 3000 -o . Nereus_Reef_Reference_Card_V1.pdf
```

## Note on fixtures vs. this asset

This is the **design** (ground truth). Real **captured photos** of the printed card
(for detection tests under real optics, lighting, and underwater) belong in
`tests/fixtures/reference_card_images/` and are still to be collected (OQ-13).
