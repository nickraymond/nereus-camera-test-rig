# Pre-registration — best video quality in ≤ 100 KB (written 2026-10-14 ~8 PM PDT, before any encode)

Question (Nick): what is the best possible visual quality for a short underwater clip encoded to
≤ 100 KB total, and can that encode run on a Raspberry Pi Zero 2 W? "Just lower the resolution"
is not an acceptable answer on its own; resolution is reported as one knob among many.

## Inputs (n = 6 clips)

`~/NereusPool/20261014_POOL_REVIEW/1_VIDEOS/bmcam00{3,4}_round{1,2,3}_card2m_29s.mp4`:
IMX708 Wide, `rpicam-vid` preset `wide_720p` (sensor mode 2304×1296, full-field ROI, downscaled to
1280×720 — no upscale, see `pool.log`), 10 fps, 293 frames / 29.3 s, H.264 ~2 Mbit/s (~7.3 MB).
Pool at ~2 m, v3 card, sun caustics moving over the whole floor, a diver in some rounds.

Reference = the decoded source at 1280×720, all 293 frames (the source is itself a 2 Mbit/s H.264,
i.e. ~73× the budget; it is the best copy that exists).

## Budget (hard)

**≤ 100,000 bytes per clip on every clip**, counted as the bytes that would be sent: the raw
elementary stream / file (H.264/H.265/VVC Annex-B, AV1 low-overhead OBU, `.jxl`, `.avif`).
A candidate that misses the cap on any clip is out. Target band 90–100 KB (bisection on the rate knob).

## Metrics

- **Primary: VMAF** (libvmaf `vmaf_v0.6.1`), mean over all 293 frames at 1280×720, frame N vs
  frame N on one time base (`settb,setpts=N`, the PR #88 fix). Lower-resolution candidates are
  upscaled (bicubic) for scoring; lower-fps candidates are scored the way they would be shown
  (frame hold; server interpolation scored separately and labelled).
- Secondary: VMAF 5th-percentile frame (temporal stability); **SSIMULACRA2** on 10 frames per clip
  (frames 14 + 29k, k = 0..9) at 1280×720; **card-ROI** PSNR-Y / SSIM-Y inside a fixed per-clip
  card box (manual box in native 1280×720 video coordinates, drawn on a check sheet).
- Film-grain-synthesis candidates are scored with grain applied (what a viewer sees) and without
  (dav1d `filmgrain=0`); VMAF is known to penalise synthetic grain, so the human sheet decides those.

## Statistic and decision rule

- Per candidate: mean / min / max over the 6 clips. **Ranking statistic = worst-clip VMAF mean**
  (the pass bar is the worst performer).
- Bars: worst-clip VMAF ≥ 60 = watchable, ≥ 70 = good (PR #88 scale: 70 ≈ good, 90 ≈ near original).
- Differences < 3 VMAF points = tie; ties go to the cheaper Pi encode / simpler pipeline.
- Exploration runs on 2 clips (calm `bmcam003_round1`, diver `bmcam004_round3`) are labelled
  exploratory; only candidates re-run on all 6 clips can be recommended.

## Pi Zero 2 W feasibility (top 3)

- No rig hardware (nereus002 belongs to another session until Friday). Measured in Docker
  `linux/arm64` on the Mac M1 Max with Debian trixie — the Pi's package versions (ffmpeg 7.1.5,
  SVT-AV1 2.3.0, libaom 3.12.1, x264 0.164, x265 4.1, rav1e 0.7.1, libjxl 0.11.2) — limited to 4 CPUs.
- Time on the Zero = Docker wall × a scale factor calibrated by re-running PR #88's real
  nereus002 SVT-AV1 jobs (same sources, same settings) in the same container; reported as a range.
  Peak RSS is measured directly (same arm64 binaries; RSS does not depend on CPU speed).
- Bars: encode ≤ 600 s for the 29.3 s clip; peak RSS ≤ 250 MiB (the RC guard) = feasible as is,
  250–400 MiB = feasible only with zram/swap (flagged), > 400 MiB = not feasible.
- Labelled "emulated, calibrated" everywhere; a real nereus002 run is a follow-up after hand-over.

## Candidates (one variable at a time where possible)

1. Codecs at 1280×720, 10 fps, one key frame (static camera): x264, x265, SVT-AV1, libaom, rav1e,
   vvenc; Pi hardware H.264 from PR #88's measured floor (not re-run).
2. Resolution / frame-rate ladder on the best codec (720p → 540p/360p, 10 → 5/2/1 fps).
3. Smart: denoise pre-filter; AV1 film-grain synthesis; ROI bit allocation (card vs empty water);
   background + deltas (high-quality key image, cheap updates); AV1 superres; long GOP tuning.
4. Image sequences: AVIF sequence, JPEG XL animation, JPEG XL key frames.
5. Video summary: N best frames as JPEG XL stills within 100 KB.
