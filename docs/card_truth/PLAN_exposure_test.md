# PLAN, not approved: auto exposure vs gain pinned at 1.12, both cards in frame, dimmed LEDs

Draft, 2026-10-06. **Do not run until the EM relays Nick's GO.** It changes no rig config: it
uses the existing exposure-sweep tool from PR #91, deployed to a side folder on nereus002.

## Question

Under dim light, which is better: letting the IMX708 auto-expose (it raises the analogue gain), or
pinning the gain at its floor (1.12) with long shutters? "Better" means a cleaner red channel and
equal or better colour after correction.

## Set-up

- **Scene:** the V3 c1 card and the ColorChecker Classic in their current places. All 24
  ColorChecker patches are in the IMX708 frame (2026-10-06 acceptance); the N6 and AE3 are not
  needed.
- **Light:** both LEDs dimmed to one fixed setting for the whole test. Record the setting and the
  rpicam `Lux` reading of the auto arm. No daylight; room lights off.
- **Focus:** IMX708 at the card distance, 1.09 dpt. The sweep locks focus to the still's
  autofocus position, which landed on the card on 10/5 and 10/6. The plan checks it.

## Arms (IMX708, RAW + JPEG, 3 repeats each)

| arm | exposure | gain | how |
|---|---|---|---|
| A, auto | auto | auto (expected > 1.12 in dim light) | the normal still + RAW: `nereus-rig experiment --raw` |
| B1 | 1/15 s (66,667 µs) | 1.12 (pinned) | `--exposure-sweep --sweep-shutters-us imx708=66667,250000,500000 --sweep-repeats 3` |
| B2 | 1/4 s (250,000 µs) | 1.12 | same sweep |
| B3 | 1/2 s (500,000 µs) | 1.12 | same sweep |

Run A and the B sweep back to back in one experiment. The sweep pins the gain at the floor via
the IMX708 adapter's `locked_exposure_settings` and locks AWB and focus from the still.

**Clipping:** the light level is set so B3 does not clip the ColorChecker white. If it does, those
patches are excluded and reported; the light is not changed mid-test.

## Scoring (Mac, from the RAWs)

1. **Red SNR:**
   - Per arm, on the V3 red patch, the ColorChecker red patch and the ColorChecker greys.
   - Measured in the RAW's red channel: the mean over the repeats ÷ the per-pixel temporal std
     across the 3 repeats (shot + read noise, not texture).
   - Reported in dB, with the gain each arm actually used, read back from the DNG metadata.
2. **ΔE after correction:**
   - Per arm, a 3×3 camera→XYZ fit on the ColorChecker (post-2014 values) with
     leave-one-patch-out ΔE00 (the Rung 1 metric).
   - The same arm's matrix applied to the V3 patches. Their Lab spread across arms shows how
     stable the correction is (no V3 truth exists yet).
3. **Sanity:** the read-back exposure, gain, AWB and lens position per frame; ColorChecker clip
   fraction; one cut sheet with the red patch at 1:1 per arm.

## Decision rule (proposed)

- Prefer the longest pinned-gain arm that does not clip.
- It should also beat auto on red SNR by ≥ 3 dB, with a held-out ΔE00 no worse than auto's
  + 0.3.
- If auto already runs at gain ≈ 1.12, the test says nothing new. Report that and dim further.

## Cost

About 15 min on the rig (one experiment), plus about 30 min on the Mac.
