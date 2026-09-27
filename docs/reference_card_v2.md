# Nereus Reef Reference Card V2 — Reference

The machine-readable source of truth is [`configs/cards/nereus_v2.yaml`](../configs/cards/nereus_v2.yaml)
(SPEC §20). This page explains where each number comes from.

## Physical dimensions (from the vector print master)

Measured 2026-09-26 from `tests/fixtures/reference_card/Nereus_Reef_Reference_Card_V2.pdf`
by reading the PDF drawing operators directly (1 pt = 1/72 in = 0.352778 mm). The PDF is a
17 × 11 in page drawn at **true scale**: the card outline is exactly 410.000 mm wide.

| Quantity | PDF (pt) | mm | Used for |
|---|---|---|---|
| Card (trim) | 1162.203 × 387.398 | **410.000 × 136.667** (3:1) | print-scale check |
| AprilTag edge (outer edge of the black border, tag36h11) | 90.652 | **31.980** | PnP / tag size |
| Tag-centre spacing, horizontal (tag 0 → 1) | 1034.363 | **364.900** | distance `z` |
| Tag-centre spacing, vertical (tag 0 → 2) | 259.558 | **91.566** | distance `z` |
| Printed "0–300 mm" scale bar | 847.442 | **298.96** (3 × 99.77) | **do not use** |

- Tag centres (PDF, y down): tag 0 (94.818, 266.221), tag 1 (1129.181, 266.221), tag 2
  (94.818, 525.779), tag 3 (1129.181, 525.779) — corner map `tl:0, tr:1, bl:2, br:3`.
- **The scale bar is not an exact ruler.** It is 1.04 mm (0.35 %) short of 300 mm and its
  tick spacing is uneven (99.46 / 99.46 / 99.99 mm). Use the tag geometry for scale.
- **Assumption:** the physical card was printed at 100 % scale (no fit-to-page). The
  laminated V2 card has not been measured. A tape measure across the card width (410 mm)
  would confirm it; the tag spacing then follows.
- Cross-checks: the raster render's detected tags give 364.87 mm spacing and ~32.1 mm edge
  (render rounding), and the canonical-frame tag edges (211 × 176 px) match 31.98 mm at the
  canonical scale (below) within 1 %.

## Canonical rectified frame (3000 × 1000 px)

`analysis/reference_card.py` warps the tag-centre quad, expanded ×1.25 horizontally and ×2.0
vertically about its centre, to 3000 × 1000 px. That frame spans 456.13 × 183.13 mm, so its
pixels are **not square**: 6.575 px/mm horizontally, 5.455 px/mm vertically. Patch boxes in
the card YAML are in this frame. Compute distances in card millimetres, never canonical pixels.

## Colour truth

`truth.source: design_svg_2026-09-01` — the SVG design fills used by the backend GRVI
correction (`nereus-vision-dev` `profiles/template_layout_v2.json` @ `6aeb1c2`). These are
printed-design values, not measurements of the printed card (print error is typically a few
ΔE; SPEC §20, brief §5.3). The rig fixture's raster-sampled values are ~1 count off on 11 of 17
patches; `web/color_check.py` differences are listed in the YAML header.

## Known condition of the physical V2 card

The laminated card took on water during the Channel Islands dives (SPEC §20): clean up to
~P9150408, then damage to white, grey 200 and the left of grey 128. Handling rule: exclude,
never repair. Card V3 recommendations are in the design brief §7 P1.0.
