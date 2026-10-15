# Pre-registration v2 — best 5 s underwater clip in ≤ 100 KB, encodable on a Pi Zero 2 W

Written 2026-10-15 ~10 AM PDT, **before any 5 s encode**. Replaces v1 (2026-10-14, 29 s clips; see
History at the end). Goal `mbari` (epic E7): the Syncro/MBARI 2027 field test wants video, and 5 s clips
are acceptable.

Question: what is the best visual quality for a **5 s** underwater clip encoded to **≤ 100,000 bytes**,
encoded on a Raspberry Pi Zero 2 W? "Just lower the resolution" is not an acceptable answer on its own;
resolution and frame rate are reported as two knobs among many.

## Inputs (n = 6 clips) and the 5 s cut (fixed now)

`~/NereusPool/20261014_POOL_REVIEW/1_VIDEOS/bmcam00{3,4}_round{1,2,3}_card2m_29s.mp4`: IMX708 Wide,
`rpicam-vid` preset `wide_720p` (sensor mode 2304×1296, full field, downscaled to 1280×720), 10 fps,
293 frames, H.264 ~2 Mbit/s with a key frame every 60 frames. Pool at ~2 m, v3 card, sun caustics,
a diver in some rounds. The camera does not move between rounds.

**Cut rule: frames 20–69 of every clip (t = 2.0–6.9 s, 50 frames at 10 fps).** Same window for all
six clips; no content-based choice. Why 2.0 s: a pre-check of the source only (mean Y/U/V per frame,
no encode) shows luma flat from frame 0 (first frame within ±0.8 of the frame 20–69 mean) and the
colour balance within ±0.3 by frame 9 on the four calm clips (first-frame offset ≤ 1.0 count); on
round 3 the means keep moving with the caustics and the diver, which is content, not settling. 2.0 s
is twice the longest settle seen. Frame 60 is a source key frame (affects every candidate equally).

**Sensitivity window (secondary, not used for ranking):** frames 150–199 (t = 15.0–19.9 s), run for
the top 3 candidates only, to check the ranking does not depend on the window.

Reference = the decoded source frames of the window, stored once as lossless FFV1 (1280×720, yuv420p,
10 fps). The same file is the encoder input and the scoring reference.

## Budget (hard)

**≤ 100,000 bytes per clip on every clip**, counted as the bytes that would be sent: the raw elementary
stream or file (H.264/H.265/VVC Annex-B, AV1 low-overhead OBU, `.jxl`) or, for multi-part payloads,
the packed file with its 4-byte length per part. A candidate that misses the cap on any clip is out.
The rate knob is searched so every file lands in 95–100 % of the cap where the knob allows.

## Metrics

- **Primary: VMAF** (libvmaf `vmaf_v0.6.1`), mean over the 50 frames at 1280×720, frame N vs frame N on
  one time base (`settb,setpts=N`, the PR #88 fix). Lower resolutions are upscaled bicubic for scoring;
  lower frame rates are scored as shown (frame hold; server-side interpolation scored separately and
  labelled).
- Secondary: VMAF 5th-percentile frame (temporal stability); **SSIMULACRA2** on 10 frames per clip
  (window frames 2 + 5k, k = 0..9) at 1280×720; **card-ROI** SSIM-Y / PSNR-Y inside the fixed
  per-camera card box (native 1280×720 coordinates, `common.CARD_BOX`, unchanged from v1).
- Film-grain-synthesis candidates are scored with grain applied (what a viewer sees) and without
  (decoder grain off); VMAF penalises synthetic grain, so a human sheet decides those.

## Statistic, bars and what "best" means

- Per candidate: mean / worst / best over the 6 clips. **Ranking statistic = worst-clip VMAF mean.**
- Bars on the worst clip: VMAF ≥ 70 = good, ≥ 60 = watchable (PR #88 scale: 70 ≈ good, 90 ≈ near the
  original).
- **Eligible for "best"**: under the cap on all 6 clips **and** feasible on the Pi as is (bars below,
  on the worst clip).
- **Tie** = eligible candidates within 3.0 worst-clip VMAF points of the top eligible one. Inside the
  tie set the winner is decided in this order:
  1. a standard single bitstream that a stock decoder plays (ffmpeg / dav1d; no server-side
     reconstruction, no cached background) beats a custom pipeline;
  2. then the lowest Pi encode time (upper end of the range, worst clip); times within 20 % of each
     other count as equal;
  3. then the highest worst-clip SSIMULACRA2.
- Also reported: the quality ceiling (best Mac-encoder result regardless of the Pi) and its gap to the
  recommendation.
- Only candidates run on all 6 clips can be recommended. Runs on the 2 exploratory clips (calm
  `bmcam003_round1`, diver `bmcam004_round3`) are for debugging and are labelled exploratory.

## Pi Zero 2 W feasibility (emulated, calibrated)

- No rig hardware: nereus002 belongs to another session; a real-Pi check of the top 3 comes later, only
  after an explicit hand-over and after Friday's pool data is pulled.
- Measured in Docker `linux/arm64` on the Mac (M1 Max) with Debian trixie, i.e. the Pi's package
  versions (ffmpeg 7.1.5, SVT-AV1 2.3.0, libaom 3.12.1, x264 0.164, x265 4.1, libjxl 0.11.2), pinned
  to 4 vCPUs, 1 GiB, no swap. The bitstreams it writes are the ones scored. Nothing else heavy runs on
  the Mac while the container is timed.
- **Time calibration**: re-run PR #88's real nereus002 jobs (single-thread SVT-AV1 p12, FFV1 decode)
  in the same container, before and after the Pi batch. Pi time is reported as a range:
  low = container wall × median wall ratio (assumes the Zero's 4 cores scale like the container's);
  high = container CPU time × max CPU ratio (no parallel speed-up at all). Calibration rows where the
  container stalled (single-thread job with wall > 3× its CPU time) are dropped and listed.
- Pi time = one encode at the final settings, including making the encoder input (y4m) where the
  recipe needs it. The number of rate-search attempts and their summed time are also reported (a
  deployed unit may need a retry to stay under the cap).
- **Bars (worst clip):** encode high end ≤ 600 s and peak RSS (whole process tree) ≤ 250 MiB =
  feasible as is. Low end ≤ 600 s < high end = "feasible only if threads scale" (flagged).
  RSS 250–400 MiB = feasible only with zram/swap (flagged); > 400 MiB or low end > 600 s = not feasible.
- Labelled "emulated, calibrated" everywhere.

## Candidates (fixed now; one variable at a time where possible)

Stage 1 — codec field, Mac encoders (quality ceiling), 1280×720, 10 fps, one key frame, 2-pass VBR:
x264 veryslow, x265 veryslow, SVT-AV1 3.1 presets 2 / 4 / 8, libaom cpu-used 3 / 6, rav1e speed 4,
vvenc medium / slower (VVC: ffmpeg decodes it, browsers do not; noted).

Stage 2 — Pi recipes in the container, same format: SVT-AV1 2.3 presets 4 / 6 / 8 / 10, each with the
default threading and with the PR #88 low-memory settings (`--lp 1 --lookahead 0 --enable-tf 0`);
libaom 3.12 cpu-used 4 / 6; x264 0.164 veryslow; x265 4.1 slow. Pi hardware H.264 is not re-run: PR #88's
measured result is quoted as the reference.

Stage 3 — one change at a time on the best stage 2 recipe (picked by the rule above), in the container:
960×540; 5 fps (frame hold) and 5 fps + server interpolation; hqdn3d denoise; film-grain synthesis;
ROI (card −60 qindex); tune VQ; residual against a background cached at the server (labelled: needs
the background sent earlier, not counted in the 100 KB).

Stage 4 — top 3 eligible: sensitivity window on all 6 clips, a side-by-side human check sheet on the
worst clip, then the recommendation (codec, settings, bytes, worst-clip VMAF, Pi time range and RSS).

## Outputs

Run data under `~/NereusData/nereus-camera-test-rig/2026-10-15_video100k_5s/` with a `MANIFEST.md`
(what, source SHA-256s, code SHA, host). Only code, this file and small text results (≤ 1 MB) go in git.

## History

- **v1 (2026-10-14)**: same metrics and bars on the full 29.3 s clips (293 frames). Exploratory runs on the
  two exploratory clips were made and **were seen before v2 was written**: at 29 s every candidate run on
  both clips had its worst clip below 50 VMAF (top: background + residual, SVT-AV1 p4, 47.8), and a
  background cached at the server helped the calm clips but missed the cap on the round 3 clips. Those results informed
  the stage 3 list above; none of them are used for the 5 s ranking.
- **v2 (this file)**: 5 s window (frames 20–69), tie rule spelled out, Pi time as a calibrated range,
  stage 2 split into default vs low-memory threading.
