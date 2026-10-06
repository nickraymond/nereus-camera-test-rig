# V3 c1 truth shoot, 2026-10-06 (nereus002, no flat-field)

Sheet: https://claude.ai/artifact/5HMfLWZLqAQr5gPTiNE2hD

**Set-up**
- Two LEDs at 5300 K, full brightness; room lights off.
- ColorChecker Classic in the card plane, below the V3 c1 card.
- Nothing moved, per Nick.
- No physical flat-field: no board large enough.

**Captures**
- On nereus002 under `~/nereus-rig-v3truth/results/truth_20261006/{imx708,n6,ae3}/cards`, stops 0 and −0.5, 3 repeats.
- Normal-settings stills in `exp_20261006T223939Z_truth_normal_stills`.
- IMX708: 40.9 ms, gain 1.12, focus 1.094 dpt.
- N6: 9416 µs, 3.15 dB. AE3: 7592 µs, 3.15 dB.

**Analysis** (Mac; `<root>` = the pulled `truth_20261006`, `<stills>` = the experiment folder):

```bash
python -m host_tools.v3_truth_shoot --root <root> --stills <stills> --out truth_noshade.json
python -m host_tools.v3_truth_shoot --root <root> --stills <stills> --out truth_alsc.json \
    --alsc docs/card_truth/truth_20261006/imx708_wide_vc4_tuning.json
python -m host_tools.v3_truth_shoot --root <root> --stills <stills> --out truth_alsc_plane.json \
    --alsc docs/card_truth/truth_20261006/imx708_wide_vc4_tuning.json --plane
python -m host_tools.v3_truth_sheet truth_alsc.json --noshade truth_noshade.json \
    --plane truth_alsc_plane.json --out sheet/index.html
```

**Tuning file**
- `imx708_wide_vc4_tuning.json` is the libcamera tuning file on nereus002.
- Path: `/usr/share/libcamera/ipa/rpi/vc4/imx708_wide.json`, libcamera 0.7.2+rpt20260817 (BSD-2-Clause).
- Only its `rpi.alsc` tables are used, as lens-shading correction for the IMX708 RAW.

## Results

| camera | ColorChecker patches seen | held-out ΔE00 (3×3 / root-poly): none | + tuning-file shading | + chart light gradient | V3 vs IMX708 (median ΔE00) |
|---|---|---|---|---|---|
| IMX708 | 24 | 2.81 / 3.10 | 2.27 / **1.97** | 1.67 / 1.33 | — |
| N6 | 12 (top two rows; no greys or primaries) | **1.18** / 1.71 | on-chip | 1.46 / 2.06 | **1.47** |
| AE3 | 23 (black cut) | 2.32 / **2.16** | on-chip | 1.32 / 1.35 | **0.82** |

- **V3 vs design:** the printed card is a median ΔE00 5.7 from its design file. The black is at L\* 38 and the greys are compressed (mid grey 118 → ~157 sRGB).
- **Lamps:** they light the chart about 21 % brighter at its top than at its bottom, the same on the IMX708 and the AE3.
- **Why the gradient term stays off the card:** the chart is below the card. Extending the chart's gradient up onto the card moves the V3 values by 4–11 ΔE00, mostly in lightness, and makes the N6 and AE3 cross-checks worse. So it is not applied, and the card's absolute lightness stays uncertain.
- **The `measured:` block is not yet written** into `configs/cards/nereus_v3_c1.yaml`. `card.py` would make it the truth everywhere. It waits for a paper flat-field or Nick's go. `--write-block` writes it.

**Part B (own JPEG, normal settings, vs ColorChecker)**
- ΔE00: IMX708 8.77, N6 7.64, AE3 9.47.
- Grey cast: a\* −3.6 to −6.2.
- Clipping at normal exposure: the N6 and AE3 RAWs clip the light greys, yellow and cyan (11 and 8 patches). The IMX708 clips none.
