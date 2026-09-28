# Reference Card V3 — fixtures and print record

Source of truth: `configs/cards/nereus_v3_c<N>.yaml` (SPEC §20). Design: `docs/reference_card_v3.md`.
Every file here is rendered from those YAMLs by `host_tools/render_card.py`;
`tests/unit/test_card_v3.py` fails if a committed file no longer matches its YAML.

## The printed card: c1 (tag25h9 IDs 0–3)

**First print (Nick, 2026-09-27): 10 identical copies of c1, Sticker Mule rectangle sticker,
matte, 16.54 × 10.63 in (420 × 270 mm), front only**, mounted on a rigid board
(`docs/reference_card_v3.md`, "Vendor options"). The lens calibration target is a calib.io stock
ChArUco board (600 × 400 mm), not a printed back. Colours are design values until the printed
copies are measured (OQ-40, OQ-46); geometry is design until checked with calipers.

| File | What it is | Use it for |
|---|---|---|
| `sticker/nereus_v3_c1_sticker_420x270mm.pdf` | **The exact file sent to print** (vector, page = 420 × 270 mm) | Reprints; what the physical card is |
| `sticker/nereus_v3_c1_sticker_420x270mm.png` | Same, 4961 × 3189 px at 300 ppi | Upload fallback |
| `sticker/nereus_v3_c1_sticker_420x270mm.svg` | Same, SVG | Inspection |
| `nereus_v3_c1_canonical_4200x2700.png` | The canonical rectified card: 10 px/mm, pixel i = card mm [i/10, (i+1)/10). Patch boxes and tag centres in the YAML are in this frame | Warping / sampling tests; the V3 counterpart of V2's `reference_card_template_3000x1000.png` |
| `nereus_v3_c1_example_1280x800.png` + `.json` | Synthetic OpenMV-N6-like frame (focal 933 px × 1.33, card at 1.0 m, yaw 20°, pitch 10°; geometry only: no water, noise, blur or distortion) with exact ground truth: canonical → image homography, tag corners (TL, TR, BR, BL) and centres, patch centres | End-to-end tests of `locate` → `patches` once the pipeline reads V3 (OQ-47): detection must find IDs 0–3 within ~1 px of the JSON |
| `nereus_v3_c1_front.svg/.pdf`, `nereus_v3_c1_back.svg/.pdf` | Print masters with 3 mm bleed, crop marks, cut line; back = ChArUco | A board printer (e.g. calib.io custom) |

SHA-256 of the files sent to print (2026-09-27):

```
aefee2bbb76e0e45bfa2c086210ada34728fb0734a670b8fcf5142048f104c26  nereus_v3_c1_sticker_420x270mm.pdf
56e13f9f62093fd9b107be8b68b997a6c4b7201ea07e2d78e745e08ab1675cde  nereus_v3_c1_sticker_420x270mm.png
c5793287748a781272154520b70fa6233146e339310383d79d622d0e4524adde  nereus_v3_c1_sticker_420x270mm.svg
```

## Cards c2–c4 (IDs 4–7, 8–11, 12–15)

Print masters only (`nereus_v3_c{2,3,4}_front/back.svg/.pdf`); not printed. c2 is the second card
for a two-card frame (two copies of the same card must never share a frame: tags are keyed by ID).

## Regenerate

```bash
python -m host_tools.render_card configs/cards/nereus_v3_c*.yaml --out tests/fixtures/reference_card_v3
python -m host_tools.render_card configs/cards/nereus_v3_c1.yaml --reference --out tests/fixtures/reference_card_v3
```

Do not regenerate `sticker/` — it is the record of what was printed. A new print gets a new folder.
