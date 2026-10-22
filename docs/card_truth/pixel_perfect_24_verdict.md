# Pixel Perfect 24 chart (shipped in the SpyderCheckr box): can we use it as truth?

2026-10-05, for Nick (colour epic, Phase 0 card truth). Desk study only: existing IMX708 RAWs,
web research, no new hardware state.
Tool: `python -m host_tools.chart_vs_colorchecker`. Data: `docs/card_truth/pixel_perfect_vs_colorchecker_20261005.json`.

## Verdict: usable as a relative / white-balance reference only, not as truth

Measured through the IMX708's factory colour (its libcamera tuning CCM), the chart is
**ΔE00 ≈ 5–7 (median) from the ColorChecker Classic reference**, with the colours at ≈ 6–7
and the worst patches at 10–12. That is well outside "usable as truth" (median ≤ 2–3), even
allowing a few ΔE for the factory CCM itself.

- Profiling it with tools that assume ColorChecker values would **bake that 5–7 ΔE print error
  into the camera profile**.
- Its greys are close to neutral (a\*/b\* within ±4 after white balance on its own mid greys).
  Use them for white balance and for stability checks (same chart, same light, session to
  session) only.

## What was measured

- **Frames:** the chart in 8 existing IMX708 RAWs:
  - S4 bench under cool LEDs (3) and warm 3200 K LEDs (3), 2026-09-30;
  - V3 placement snapshots 3 and 4 under room light, 2026-10-05.
- **Patches:** found with `color/chart.py` in the card plane; 24/24 patches, none clipped.
- **Rendering:** each patch through the camera's factory colour path, the libcamera
  `imx708_wide` tuning CCM (rpicam `ColourCorrectionMatrix`).
  - The DNG `ColorMatrix1` path gives identical numbers: it is built from the same tuning.
  - One exposure scale per frame, fitted on the four mid greys.
- **Comparison:** CIEDE2000 vs the ColorChecker Classic reference (X-Rite post-Nov-2014,
  Lab D50).
- **This estimate rests on the factory CCM.** Part of every number is the camera's own matrix
  error. The Raspberry Pi tuning CCMs are fitted on a Macbeth-type chart, so on a real
  ColorChecker they should land at a few ΔE, not 5–7.

**Median ΔE00 vs the ColorChecker by lighting:**

| lighting (frames) | scene AWB as shot: all / colours / greys | WB on the chart's own greys: all / colours / greys | worst patch |
|---|---|---|---|
| S4 cool LEDs (3) | 13.1 / 12.8 / 14.7 | **6.2 / 6.8 / 3.4** | 12.5 |
| S4 warm 3200 K (3) | 13.4 / 13.3 / 13.9 | **6.8 / 7.4 / 5.1** | 12.0 |
| room light, snapshots 3–4 (2) | 9.4 / 9.0 / 10.2 | **5.4 / 6.4 / 2.8** | 10.9 |

**Reading the table:**
- "As shot" is dominated by the frame's auto white balance: AWB was set on warm cardboard, so
  every grey reads blue (b\* −6 to −28).
- White-balanced on the chart's own neutral 6.5 / 5 / 3.5, the same patches are worst in every
  light:

  | patch | mean ΔE00 |
  |---|---|
  | cyan | 11.7 |
  | orange | 11.0 |
  | foliage | 9.5 |
  | blue sky | 9.1 |
  | dark skin | 8.2 |
  | green | 7.9 |

  So these are print differences, not lighting.
- The chart's colours read lighter and less saturated than a ColorChecker. For example, orange
  is b\* 43 vs 56.5, and foliage L\* 51 vs 43.
- Its black is lifted (L\* ≈ 27–28 vs 20.6): print, gloss or flare.
- **Grey ramp:** a\* runs from −3 (light greys) to +4 (black) in all three lights. Because it is
  stable across illuminants, it is likely the chart's greys, not the camera.

**Second opinion:** none independent available. No other camera with a factory profile has
photographed this chart: the TG-7 set predates it, and the OpenMV boards have no factory CCM.
The cheapest independent check is one RAW (DNG) of the chart from a phone with a factory colour
profile, e.g. an iPhone ProRAW, under daylight.

## Research: what the profiling tools do with a 24-patch chart

Desk research, 2026-10-05; sources at the end.

- **Adobe DNG Profile Editor (free; Chart Wizard):**
  - It is built for the 24-patch ColorChecker, with the reference values built in. No Adobe
    document offers a way to load your own values.
  - **Forum only:** a third-party clone printed in the ColorChecker layout is read as a
    ColorChecker. A real SpyderCheckr fails, because its patch order differs.
  - You place four corner dots on the chart. It then adjusts the hue/saturation lookup table on
    top of a base profile, rather than refitting the matrix.
  - Output: a DCP. That holds Color/ForwardMatrix1–2, ProfileHueSatMap and an optional look
    table and tone curve, for one or two illuminants (2850 K / 6500 K). It is used by Adobe
    Camera Raw, Lightroom and other DNG converters.
- **Calibrite (X-Rite) ColorChecker Camera Calibration (free):**
  - It accepts Calibrite's own 24-patch charts (Nano, Mini, Passport, Classic, XL, Mega) and
    the SG, with auto-detection and manual corner adjustment.
  - It outputs DNG/DCP profiles (single or dual illuminant) and ICC.
  - The reference values are built in for its own charts; no third-party or custom values are
    documented. That a clone would be detected and scored against ColorChecker values is my
    inference. The reviewer's "matches the Macbeth standard" is only a review claim, and the
    measurement above contradicts it at the 5–7 ΔE level.
- **Either tool on this chart bakes in the print error.** The fit makes each patch land on the
  stored ColorChecker value. A patch printed lighter or less saturated (cyan, orange, foliage
  here) is treated as camera error, and the opposite correction is applied to every pixel of
  that hue in every later photo. We saw the same mechanism on the V2 card, when a matrix
  fitted to design values over-saturated real scenes. Only neutral balance survives.
- **A genuine SpyderCheckr 24** has Datacolor's own pigments in a serpentine layout, not the
  ColorChecker's.
  - Datacolor publishes Lab / sRGB reference data, and its software holds the values.
  - Its software outputs HSL presets for Lightroom and ACR, not a DCP.
  - Our card-truth tool takes its Lab values as a CSV.
- **ColorChecker Classic reference used here:** X-Rite post-Nov-2014 formulation, Lab D50
  (BabelColor `ColorChecker24_After_Nov2014.txt`). The pre-2014 values differ by 0.85 ΔE00 on
  average.
- **Matte spray:** manufacturers warn it can darken or saturate colours or wash out blacks
  (Golden, Moab). Any coating changes the patches, so values measured before spraying no
  longer apply. Use 45/0 lighting or cross-polarisation instead. Cross-polarisation can shift
  colours, so profile under the same set-up.

## Options for Nick

1. **Keep it as a relative reference (now, free).**
   - Use it for white balance and for session-to-session stability checks.
   - The card-truth tool (`host_tools/card_truth`) can still run with it, but only to check
     the pipeline. Its "measured" values would be relative to an unknown chart and must not be
     written as V3 truth.
2. **Get a real reference (recommended for Phase 0).** Either of these, ~$50–100:
   - a Calibrite/X-Rite ColorChecker Classic (published per-formulation values);
   - a genuine Datacolor SpyderCheckr 24 (Datacolor's reference values).

   This turns the card-truth tool into an absolute measurement, and its held-out ΔE becomes
   meaningful. The mis-shipped chart should go back to the seller as the wrong item.
3. **Or measure this chart once with a spectrophotometer** (borrowed X-Rite i1, a print shop,
   or a Nix Spectro 2-class device). Its 24 measured Lab values then make it a usable reference.
   The values must come from whatever matches **this physical chart**, never from ColorChecker
   or Spyder tables.
4. **DNG profile route** (Adobe DNG Profile Editor / Calibrite software):
   - This only fits our pipeline as a source of a camera matrix, the DCP's colour/forward
     matrix, which our linear-RAW cloud correction could read.
   - Built on this chart against ColorChecker values, that matrix carries the 5–7 ΔE print
     error.
   - DNG Profile Editor mostly writes a hue/saturation table on top of Adobe's base matrices,
     which our pipeline does not use.
   - With a real ColorChecker, the Calibrite DCP's forward matrix is a reasonable cross-check
     of our own `calibrate` fit, not a replacement.
5. **Glare:** don't spray it. Use geometry instead:
   - lights at ~45° to the chart, camera on-axis (the 45/0 set-up already in
     `docs/v3_card_truth_capture.md`), and check for glare from the camera's position;
   - or cross-polarise the lights and the lens.

## Sources

**Adobe:**
- jnack.com/blog/2008/08/04/the_dng_profile_editor_whats_it_all_about
- helpx.adobe.com DNG spec 1.6
- northlight-images.co.uk (DNG PE tutorial)
- community.adobe.com (forum; the "cannot read color patches" thread)

**Calibrite:**
- calibrite.com/us/photo-target
- calibrite.com/learning-centre/calibrite-profiler-camera-dng

**Datacolor:**
- Spyder-Checkr-24-UserGuide.pdf
- datacolor.ru spydercheckr_reference_data.pdf

**ColorChecker reference:**
- babelcolor.com/colorchecker-2.htm
- ColorChecker24_After_Nov2014.txt

**Matte spray:**
- justpaint.org (Golden)
- moabpaperblog.com (fixative sprays)
- Heritage Science 2021 on cross-polarisation (search summary only)
