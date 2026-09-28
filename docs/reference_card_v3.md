# Nereus Reef Reference Card V3 — Design (draft)

**Status:** design agreed with Nick 2026-09-27 (round 4, variant B). **Not printed.** Printing waits
on the S2a Go (SPEC §4 Phase 8) and on a bench check with real N6/AE3 frames (OQ-43).
Design session write-up, with every round, test and the independent review:
[Claude Doc "Reference Card V3 — Design Options and Test Results"](https://claude.ai/code/artifact/d30b0008-bfd4-4c62-aee0-8258d9336dde).

The machine-readable source of truth is one YAML per physical card:
[`configs/cards/nereus_v3_c1.yaml`](../configs/cards/nereus_v3_c1.yaml) … `_c4.yaml` (SPEC §20).
They are identical except `card_id` and the tag-ID block. Print files are rendered from them:

```bash
python -m host_tools.render_card configs/cards/nereus_v3_c*.yaml --out tests/fixtures/reference_card_v3
```

`tests/unit/test_card_v3.py` checks the YAMLs are consistent, that the rendered tags decode as
their own block (and never as V2's family), that the ChArUco back finds all 45 corners, and that
the committed print SVGs match the YAMLs.

## Layout

Rigid matte board, **420 × 270 mm** (fits 11 × 17 in with ~5 mm to spare each way), 6 mm corner
radius, 3 mm bleed. Coordinates: mm from the top-left of the trim.

| Element | Size (mm) | Position (x, y) | Why |
|---|---|---|---|
| Surround | whole card | — | Mid grey (sRGB 118, ρ 0.18) = `gray_mid` ink: extra anchor pixels everywhere, less flare into black, less A-mode overexposure than a white card |
| 4 corner tags, tag25h9 | 63 edge (9 mm cells), on 81 mm light-grey squares | centres 44.5 / 375.5, 44.5 / 225.5 | Range. Tag-centre quad 331.0 × 181.0 mm, ratio 1.829 |
| `gray_black` | 83.6 × 92 | 8, 89 | Haze / print-black reference |
| `gray_light` (ρ 0.58, sRGB 200) | 114 × 92 | 99.6, 89 | **White-balance anchor at depth**: 4× grey 128's red signal at 14–17 m |
| `gray_light2` (ρ 0.35, sRGB 160) | 91.2 × 92 | 221.6, 89 | Spare anchor when `gray_light` clips (10 % of TG-7 frames); third ramp point |
| `gray_mid` (ρ 0.18, sRGB 118) | 91.2 × 92 | 320.8, 89 | Shallow anchor; sample box inside the surround |
| 8 colours | 54.5 × 81 each | rows y 4 and 185, x 89 + k·62.5 | red, red-orange, orange, yellow / green, cyan, blue, magenta: saturated, reds weighted |
| Back: ChArUco | 10 × 6 squares of 40 mm, 30 mm `DICT_4X4_50` markers | centred | Lens intrinsics + distortion (N6 stock lens < −24 % TV) from ~12 views, in air and in water |

Tags are printed **black on light grey** (sRGB 200), not white: white clips first when the camera
overexposes the card and blooms into the black cells. Tag IDs: c1 0–3, c2 4–7, c3 8–11, c4 12–15,
corner map `tl, tr, bl, br` = block + 0, 1, 2, 3 (same convention as V2). The 35 tag25h9 codes allow
8 cards of 4 tags.

Simulated patch range (card turned up to 40°; relative only, OQ-43): on the N6, `gray_light`
≥ 40 px to 2.6 m, `gray_light2` to 2.2 m, colours ≥ 30 px to 1.6 m; the TG-7 roughly 3× farther.

## Decisions and what was rejected

| Choice | Decision | Evidence (design session, 2026-09-27) |
|---|---|---|
| Tag family | tag25h9 (Nick) | 7 × 7 cells vs 8 × 8: N6 60 mm tags at 3 m 98 % vs 83 % (36h11); 0 false detections on all 309 TG-7 frames (16h5: 3) |
| Greys | light, light-2, mid (= surround), black | White clipped G/B on 26 % of usable TG-7 frames (9 of 63 on the clean card); grey 74 is ≈ black in red at depth; ≥ 3 non-black greys give the ramp a spare degree of freedom and a held-out grey for ψ |
| Colours | 8, larger (Nick) | A V3-like 7-colour set costs ≤ ~1.5 ΔE vs all 12 in a per-frame fit on V2 data |
| Mid-edge tags | Dropped (Nick chose R4-B) | They never help locate the card, detect only to ~1.5 m on the N6, and the ChArUco back calibrates distortion better; dropping them makes each grey ~35 % wider |
| Light trap (hole to a black cavity) | Not on the card | The per-frame affine's offset (S2a PR #44) already absorbs haze; measuring the printed black fixes the unknown-black problem; trapped air, wet flock and a bolt-on part are real underwater risks. A pool experiment can test it separately (OQ-48) |
| Size | 420 × 270 mm | Hard limit 11 × 17 in. Too big for the pool's near position: fills ~81 % of the N6 frame at 0.5 m (OQ-44) |

## Print spec (agree with the vendor before ordering, OQ-45)

- UV-cured direct print on 3 mm white aluminium composite (ACM) or rigid PVC; no paper, no pouch.
  Matte finish (matte clear coat or matte ink set); **no optical brighteners** in the white base.
- Greys printed **K-only** (not CMY builds): spectrally flatter, so they stay neutral under narrow
  blue-green underwater light. Colours as the vendor's closest in-gamut build of the design sRGB;
  whatever they print is then **measured** — the design values are placeholders.
- Vector PDF/SVG at 100 % scale; the magenta hairline is the cut path (6 mm radius), not art.
- Double-sided: front = `nereus_v3_cN_front`, back = `nereus_v3_cN_back` (the back label names the
  card). Order a test coupon first.

## Vendor options (checked 2026-09-27)

| Route | What you get | Cost / lead | For | Against |
|---|---|---|---|---|
| **Sticker Mule** rectangle sticker, mounted on 3–6 mm ACM or acrylic | Vinyl + protective laminate (215 g/m², solvent inks), matte on die-cut; rectangles up to 36 × 24 in; 10 per design minimum ([max size](https://www.stickermule.com/support/largest-sticker-you-can-make), [FAQ](https://www.stickermule.com/support/faq/custom-stickers)) | Low; fast | Cheap prototypes; all plastic, so no paper to wick water (the V2 failure) | No colour management or K-only greys; laminate / adhesive can lift at the edges; mounting can bubble or stretch (measure tag spacing after mounting); continuous immersion not explicitly rated |
| **calib.io custom** | UV print on 6 mm aluminium composite, matte, optional anti-reflection coating | Nick's quote ~$350 / card, ~3 weeks | Calibration-grade maker; same process as their stock boards | Cost; the anti-reflection coating mostly helps in air (first-surface reflection ≈ 4 % in air, ≈ 0.4 % against water), so under water its benefit is small |
| **calib.io stock ChArUco** 400 × 300 mm, coarse, aluminium composite (CCT400300C) | 6 mm ACM, matte UV print, "up to 3 years" outdoors, 6.6 kg/m² ([product](https://calib.io/products/charuco-targets)) | €134, in stock | Lens intrinsics + distortion for the N6 / AE3 / TG-7 **now**, in air and in water; a direct check of the vendor's matte finish and flatness | Calibration only (no colour patches); default dictionary is 5×5 |

Matte surfaces look glossier and darker when wet (water fills the surface texture), whichever vendor
prints the card; the wet measurement (checklist step 6) captures this.

## Print and measurement checklist

1. [ ] Test coupon from the chosen vendor: the 4 greys + 8 colours + one tag, same substrate and inks.
2. [ ] Coupon: soak 5 days in salt water, remeasure (ΔE drift, edge delamination, colour bleed).
3. [ ] Order the cards (c1–c4). On arrival, check each with calipers: card 420 × 270 mm; tag-centre
   spacing 331.0 × 181.0 mm and tag edge 63.0 mm (± 0.2 mm). Record in `physical_mm` with a
   `source:` line. Never use a printed ruler.
4. [ ] Check flatness (< 1 mm bow across the long edge on a flat table) and surface finish (no gloss
   highlight at 30–60° in sun).
5. [ ] Measure every patch of every card **dry**: spectrophotometer (specular excluded) if
   available, otherwise the card beside an X-Rite ColorChecker (OQ-27) in daylight + open shade.
   Include 3–4 spots on the surround (uniformity).
6. [ ] Measure again **wet** (card under a few cm of water, or immediately after immersion): the
   ~4 % first-surface reflection that dry readings include mostly disappears under water, which
   matters most for black and the dark patches.
7. [ ] Store the measured values per card in its YAML (needs the measured-truth schema, OQ-46);
   keep the design values as a separate `source`.
8. [ ] Photograph each card above water in daylight before and after every dive (damage check and
   a fresh daylight reference).
9. [ ] Shoot the ChArUco back with each camera, 12–20 views, in air and in water (P1.4 rows 9–10, P4).
10. [ ] On the V3 dive, include shallow frames (0.5–4 m depth) as well as deep ones: the no-card depth
    table is fitted on 5–16 m, and 8 of its 9 losses to the camera JPEG are shallower (S2a, PR #44).

## Pipeline changes needed before V3 data can be processed (OQ-47)

`locate` calls the detector without the card's tag family and checks the tag quad against V2's
ratio range (2.5–6.0; V3 is 1.83); `water_model` / `correct` / `metrics` / `report` hard-code V2
patch ids (`gray_white`, `gray_dark`, `gray_mid_right`, `ANCHORS`, `RAMP`, `ALL_GREYS`). The V3
YAMLs add `apriltag.quad_ratio`, `roles` (`wb_anchors`, `ramp`, `haze`), `tags[].edge_mm`,
`canonical.px_per_mm`, `layout` and `back`, which the pipeline should read instead. The existing
`color/card.py` loader already reads the V3 YAMLs unchanged (extra fields are ignored).
