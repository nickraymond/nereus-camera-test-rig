# Reference Card V2 — test fixture

The **Nereus Vision Reef Reference Card V2** — the canonical target for the Phase 2
reference-card pipeline (Spec §13) and the project's primary success metric (Spec §1).
Sourced from `bm_cam_legacy/tools/reference_card_color_correction/reference_card_template_v2`.

## Files

| File | What it is |
|---|---|
| `Nereus_Reef_Reference_Card_V2.pdf` | Vector design / print-master (11×17 in, RGB, crop+bleed). Source of truth. |
| `Nereus_Reef_Reference_Card_V2.png` | 3000×1941 raster render of the PDF (the full card, incl. bleed) — detection input for tests. |
| `reference_card_template_3000x1000.png` | The **canonical rectified card** (post-warp), 3000×1000. Patch coords in `template_layout.json` are in this space. |
| `template_layout.json` | Canonical geometry + every gray/color patch (coords + `target_srgb`). |
| `reference_card_template_notes.md` | Upstream notes on how the template was generated. |

## Canonical geometry (from `template_layout.json`, tag IDs verified by detection)

- **AprilTag family:** `DICT_APRILTAG_36h11` (confirmed; 25h9/16h5 detect nothing).
- **Corner map:** `tl:0, tr:1, bl:2, br:3` — matches config `expected_tag_ids: [0,1,2,3]`.
- **Card boundary:** the four tag *centers* form a quad, expanded by
  `card_expand_x = 1.25`, `card_expand_y = 2.0`.
- **Canonical rectified size:** `3000 × 1000 px`.
- Detected tag centers in the 3000×1941 render: TL(232,652) TR(2767,652) BL(232,1288) BR(2767,1288).

## Card features

Grayscale ramp (white / light / mid / dark / black), centre focus/white-balance target,
6×2 colour-correction patches, and a **0–300 mm scale bar**. Gray balance uses
`gray_light` / `gray_mid` / `gray_dark`; white & black are QA-only.

## V1 vs V2

V1 (the earlier card) had a 0–**400** mm scale bar and a `1000×420` canonical warp.
**V2 is the card we standardize on** — 0–300 mm scale bar, `3000×1000` canonical warp,
and the full patch layout above. Do not use V1.

## Note

`target_srgb` values are sampled from the vector render — good for software smoke
testing. For research-grade colour work, replace them with measured values from the
**printed** card under controlled light. Real captured photos of the printed card
(for detection under real optics/lighting/underwater) still need to be collected
(`docs/open_questions.md` OQ-13) and will live alongside this file.
