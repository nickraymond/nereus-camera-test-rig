# Exposure-sweep dry runs — nereus002, 2026-10-05 (bench, in air)

`nereus-rig experiment --raw --exposure-sweep` (5 RAWs per camera at 1/250 … 1/15 s, gain at the
floor). Records only; the 15 RAWs per run (~130 MB) stay on nereus002 under
`~/nereus-rig-pool/results/pool/passthrough/2026-10-05/exp_20261005T021027Z_pool_sweep` (run 1) and
`exp_20261005T021630Z_pool_sweep` (run 2). 178 s / 184 s per set, peak RSS 206 / 202 MB.

- **Run 1** (scored with the first scorer, kept as evidence): the room lamp went off between
  frames 2 and 3 and an arm crossed frame 3. The first scorer let dark, noisy frames look sharp
  and located the card only from frame 3 on. Fixed: noise-corrected sharpness, too-dark guard,
  card located on the brightest frame first. Re-scored offline with the fix: N6 / AE3 pick 1/15 s
  with flat card sharpness (still card); the IMX708 is confounded by the lamp change.
- **Run 2** (fixed scorer): someone at the rig moved a light panel with their hands through the
  IMX708's view. The IMX708 picked 1/125 s and flagged 1/60 to 1/15 s as blurred. The panel lit
  the N6 / AE3 head-on, so all their frames clipped: no pick. Sheet:
  https://claude.ai/artifact/TkZqowjH2gEey42QmdmxiR
- **Run 3** (19:25 PDT, Nick at the rig): light = **2× LED panel, 5300 K**, both in frame; card V1 +
  checker at ~1 m (Nick; 1.21 m estimated from the tag spacing with nominal intrinsics). RAWs on
  nereus002 `…/exp_20261005T022526Z_pool_sweep`. Mac colour analysis
  (`compression_study/presets/sweep_colour.py` → `run3_2led_colour.json`):
  - pick 1/60 s (card area; 1/30 and 1/15 s clip the white patch);
  - camera colour ΔE00 median 2.94 (max 14.9 on white), card fit 3.82;
  - scene CCT from the card greys 5650 K vs nominal 5300 K; red SNR 26.8 on grey 128;
  - today's production JPEG (auto 49 ms at gain 2.0) clips 12 of 17 patches.
  - The Pi's own pick was none: its centre fallback region contains an LED panel.
  - Sheet: https://claude.ai/artifact/Cf6WfxtP3s3JfnoVf2bWyy
- **Run 5** (Nick: LEDs stay, crop them out): notes carry "2× 5300 K LED panels in frame,
  excluded from the analysis; possible veiling flare"; IMX708 scored on the MEDIUM ROI
  (`--sweep-roi imx708=1504,846,1600,900`).
  - The Pi's own pick is now 1/60 s, the same as the Mac's card-area pick.
  - Flare check: black/white = 0.028 in every unclipped frame (truth 0.074), so the blacks are
    not lifted.
  - MEDIUM / SMALL clipping 0 % up to 1/60 s.
  - Camera-colour ΔE median 2.74, card fit 3.62.
  - The production JPEG again clips 12/17 patches.
  - Same sheet URL, version 2.
- **OpenMV RAW pipeline diagnosis** (Nick asked: is the raw pipeline broken?) —
  `compression_study/presets/openmv_diag.py` → `run5_openmv_diag.json`, sheet
  https://claude.ai/artifact/NT49DezQc3jucXscpQDYMN. All checks pass on both boards: CFA BGGR,
  8-bit / unpacked / black 0, no row shift, no flip, exact read-back.
  - The AE3 finds the card on its RAW. It made no pick because 1/250 s already clips 0.67 % of
    the card box: the ladder is too long for the OpenMV sensor.
  - The N6's left tags fail on optics: the left side of its lens is soft (edge sharpness 0.11–0.13
    vs 0.19), on the RAW and its own JPEG alike.
- **Runs 6–7** (three cameras, 3 repeats per step; OpenMV ladder 1/4000 → 1/250 s at 3.15 dB;
  ROIs exclude the LED panels).
  - Run 6 exposed two flaws, both fixed before run 7:
    1. The brightness-normalised Laplacian fell by half on static frames as exposure rose (8-bit
       quantisation in dark frames). It is now direction-wise edge acutance.
    2. The IMX708 autofocus moved 0.95 → 2.6 dioptres across the sweep. Focus is now locked to
       the still's lens position.
  - Run 6 also had one N6 USB short read (1 of 15).
  - Run 7 (`run7_3cam_colour.json`): 45 / 45 frames.
    - Picks: IMX708 1/60 s, N6 1/250 s, AE3 1/500 s.
    - ΔE00 after the card fit, median: IMX708 3.64 (17 patches), N6 4.95 (13 right-side patches),
      AE3 6.36 (17 patches).
    - No LED flicker: the brightest patch varies ≤ 0.5 % over 3 repeats, down to 1/4000 s.
  - Sheet v5 at the same URL (https://claude.ai/artifact/Cf6WfxtP3s3JfnoVf2bWyy). Keepable files
    in ~/Downloads: `nereus002_exposure_sweep_20261004_v4.html`,
    `nereus002_exposure_sweep_20261004.html` (v3) and `nereus002_raw_diagnostic_20261004.html`.
