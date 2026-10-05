# nrjxl message budget on the reference reef scenes

2026-10-05, for Nick's nrjxl budget decision (via the EM). Sheet:
https://claude.ai/artifact/1d7KZA9gJ5qojwiWUBognJ. Data: `results/reef_budget.json`. Code:
`reef_budget.py` (run) and `reef_sheet.py` (sheet).

## The reference reef set

`bm_cam_legacy/reference_images/` has 9 Olympus TG-7 camera JPEGs, each 4000×3000:
- `reference_reef_coral_primary` plus `alt_01`–`alt_07` (P7070996–P7071008, shot 2026-07-07
  at 1/125–1/1000 s, f/2.8, ISO 100);
- `P9011394`, the AOML reef with the V2 card in frame (2026-09-01).

**No RAW exists for any of them.** I searched this Mac (Spotlight plus the repos). The input I
used is the Sprint06 IMX708-size stand-ins `prepared/*/synthetic_native_4608x2592.jpg` (16:9
centre crop, Lanczos upscale ×1.152).

## Method

**RAW-equivalent of the reef scenes.** This is an approximation, labelled as such everywhere.
1. sRGB → linear.
2. Undo the IMX708 colour matrix and white balance (imx708_wide tuning at 5715 K).
3. Set exposure so the brightest 0.5 % sits at 75 % of full scale.
4. Re-mosaic to BGGR, 10 bit, black 64.
5. Add IMX708 noise σ² = 0.045·v + 0.3 DN². Fitted on the S4 repeats at base gain, low
   levels only (the higher levels are inflated by lamp drift).

The JPEG's own denoise, sharpening and compression, and the 1.152× upscale, stay in the
result.

**Method check.** The same path applied to 12 TG-7 Channel Islands frames that also have real
RAW (ORF):
- Messages needed for d 4.5, JPEG-derived ÷ real RAW: median ~1.05, range 0.83–1.35, over the
  12 pairs.
- Different sensor noise is part of that difference.

**Encoder and budgets.**
- Encoder: production `rc_raw_jxl` (code_planes → cjxl `-m 1 -e 5` → seal_container) on a
  16-point distance grid.
- Crops: 800×450, 1600×900 and 2304×1296, centred.
- A budget's distance is the byte-target point at 97 % fill, interpolated in log-log between
  grid points. The error vs an exact search is ±1.5 %, measured on 4 frames.

**Quality.**
- Metrics: luma SSIM and a fine-detail ratio (Laplacian-of-Gaussian σ 1, std vs reference).
- Region: the 1600×900 region today's pjpg covers; the 800×450 crop is scored on its own region.
- Reference: the noise-free scene for the JPEG-derived sets; the RAW render for real RAW.

**Today's pjpg.** The production `rc_jpeg_encoder` path: 1600×900 → 1000×562 Lanczos, then a
progressive JPEG on the quality ladder under 195 messages, tone-matched. It is made from an ISP
stand-in: the noise-free scene (an ideal denoising ISP) and, for the synthetic scenes, also the
noisy RAW render. On reef scenes the two bars differ by ≤ 6 messages.

**bmcam004 daylight frame.** Two points the unit measured itself (R4 wake 1, 16:00Z):
d 3.499 → 123,170 B and d 9.701 → 56,136 B, interpolated. Its RAW isn't available (staging
needs the admin token), so it adds no image or SSIM.

## Results, 1600×900, reef set (P50 / P90)

**At 195 messages (today's cap):**
- 7 of 9 scenes need d > 10.4, the quality floor, so production would send pjpg instead.
- 4 of 9 don't fit even at d 15.
- Median d 13.2. The bmcam004 daylight frame: d 9.70.

**Messages needed:**
- To match today's pjpg fine detail: **285 / 338**.
- To match today's pjpg SSIM on full-resolution texture: **470 / 551**.
- For d 4.5: **541 / 681**. The bmcam004 daylight frame: 364.

**How the other scenes compare:**
- Indoor card and pool frames need 142–194 messages for d 4.5, so card scenes understate the
  budget about 3×.
- Real TG-7 RAW (underwater kelp, mostly blue water) needs 138 / 204.

**Smaller crop.** 800×450 on the same scenes needs 157 / 192 messages for d 4.5 and matches
today's pjpg detail at 81 / 92.

**Why pjpg holds up on reef texture.** It sends a 1000×562 8-bit image the ISP has already
denoised, at quality 20–60 on these scenes. nrjxl sends 1.44 M native Bayer samples at 12 bit,
noise included. Its advantages (native resolution, RAW colour, no ISP clipping) cost about
1.5–3× today's messages on dense reef texture.

## Caveats

- **JPEG-derived approximation.** The reef numbers are not RAW measurements. The method check
  on real RAW shows they're within about ±30 %.
- **Lighting.** The reef scenes are sunlit and shallow. Deeper or turbid water has less texture
  and codes smaller; the TG-7 underwater set shows that end.
- **Pjpg bars.** The SSIM bar against an ideal-ISP pjpg is a strict one. The fine-detail bar is
  the looser of the two.
