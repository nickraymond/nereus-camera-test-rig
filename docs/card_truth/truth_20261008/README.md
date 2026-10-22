# V3 c1 truth with a paper flat-field, 2026-10-08 (nereus002)

This shoot replaces the provisional 2026-10-06 values (no flat-field) as the card's `measured:` block in `configs/cards/nereus_v3_c1.yaml`.

## Light and procedure

- **Light:** 2 LEDs at 5300 K, full brightness, plus daylight leaking through closed blinds and door glass. It was held constant across the session.
- **Procedure:** the flat and the card bracket were shot minutes apart, so the flat measures the same light field as the bracket.
  1. **Paper in** (12:23 PDT): Nick taped plain white printer paper over the card and the ColorChecker. Flats, 3 frames per camera:
     - IMX708: 17.1 ms, gain 1.12.
     - N6: 3,500 µs, 3.15 dB.
     - AE3: 2,700 µs, 3.15 dB.

     The first OpenMV flats (7,500 / 6,000 µs) clipped green on the paper. They are kept on the Pi under `superseded/`.
  2. **Paper off** (12:36): card + ColorChecker bracket, stops 0 and −0.5, 3 repeats each:
     - IMX708: 24.0 ms, gain 1.12, focus 1.094 dpt.
     - N6: 5.6 ms. AE3: 4.5 ms.
- **Raw data:** `nereus002:~/nereus-rig-v3truth/results/truth_20261008/`.

## Analysis

```bash
python -m host_tools.v3_truth_shoot --root <truth_20261008> --out truth_flat.json --flat --write-block
```

The ColorChecker was found on the flat-corrected IMX708 frame: the uneven light defeats the finder's threshold on the raw frame.

## Results (`truth_flat.json`)

**Light across the card** (the flat's green, 95th / 5th percentile): IMX708 69.5 %, N6 70.4 %, AE3 73.0 %. That is a strong side gradient, the same on all three cameras, which the flat removes.

| | IMX708 (truth) | N6 | AE3 |
|---|---|---|---|
| ColorChecker patches | 24/24 | 12/24 | 22/24 |
| held-out ΔE00 | **1.24** (root-poly; 3×3 1.42) | 1.34 | 1.59 |
| V3 vs IMX708, median ΔE00 | — | 0.94 | 1.15 |

- **The fit improves:** on 2026-10-06 the IMX708 had 1.97 with tuning-file shading and 2.81 with none.
- **The gradient is gone:** the ColorChecker grey residuals are now within ±1.8 L\*. On 10-06 the top row read +1–3 and the grey row −2–5.
- **Bracket:** stop 0 vs −0.5 agree to ΔE00 0.07.

## Shift from the 2026-10-06 provisional truth

Over the 12 V3 patches: median ΔE00 2.3, max 5.3; median ΔL\* −1.3.

| patch | ΔL\* | ΔE00 |
|---|---|---|
| gray_mid | −6.0 | 5.3 |
| red | +4.8 | 5.3 |
| red_orange | +4.0 | 4.5 |
| greys and the other colours | — | 0.8–2.7 |

Part of the shift may be the illuminant (daylight + LEDs vs LEDs only) acting on the printed inks, not the flat alone.

## Effect on the 2026-10-08 sunrise sweep card ΔE

Same pipeline, truth swapped, median ΔE00 per band:

| band | RAW, old → new | camera JPEG, old → new |
|---|---|---|
| dark | 7.4 → 6.5 | 37.0 → 34.1 |
| daylight | 4.9 → **2.8** | 17.3 → 13.0 |
| LEDs | 1.9 → 1.7 | 6.9 → 8.5 |
