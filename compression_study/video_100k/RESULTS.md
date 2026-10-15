# Results — best 5 s underwater clip in ≤ 100 KB, encodable on a Pi Zero 2 W

Run 2026-10-15 (Thu) 9:59–11:29 AM PT on the Mac, per [PREREG.md](PREREG.md) v2 (committed
`2c3750c` before any 5 s result). Run data: `~/NereusData/nereus-camera-test-rig/2026-10-15_video100k_5s/`
(MANIFEST.md, results.jsonl per stage, bitstreams). Full table: [results_table.md](results_table.md).
Pi numbers are **emulated, calibrated** (Pi-matched arm64 container, scaled to PR #88's real
nereus002 runs); a real-Pi check of the top 3 is still to come (after the nereus002 hand-over and
Friday's pool pull).

## Recommendation: the pre-registered winner

**`pi_svt_p4_lowmem`: AV1 with SVT-AV1 2.3 (the Pi's own Debian package), preset 4, low-memory
settings, 1280×720, 10 fps, one key frame, 2-pass, bitrate searched per clip into 95–100 % of
100,000 B** (final requests 150–176 kbps, 1–3 encodes per clip).

> **Read this with the rule's other outcomes.** p4 low-mem wins only because the cached-background
> candidate (79.1) sets the top of the tie window, which leaves the p6 recipes outside, and is then
> removed by the "standard bitstream first" step. The same rule on other candidate sets:
> - **on stage 2 alone:** p6 low-mem, 74.8, 2.2–2.6 min on the Pi;
> - **on every candidate except the cached background:** p6 low-mem at 960×540, 73.8, 1.4–1.7 min,
>   92 MiB. It sits exactly on the 3.0-point tie boundary (76.79 − 73.79 at the 2-decimal precision
>   the scores are stored at) and then wins on encode time.
>
> All three follow the PREREG text; p4 stays the recommendation because the PREREG ranks the
> candidates that ran. The real choice, **quality (p4: +2.0 VMAF over p6, +3.0 over p6 at 540p) vs
> ~3–4× the encode time (and energy)**, is Nick's call.

```
SvtAv1EncApp --preset 4 --rc 1 --tbr <kbps> --passes 2 --keyint 300 --scd 0 \
  --lp 1 --lookahead 0 --enable-tf 0 --stats pass.stats -i clip.y4m -b clip.ivf
# send the AV1 OBU stream (ffmpeg -i clip.ivf -c copy -f obu clip.obu)
```

| pre-registered numbers (main window, 6 clips) | recommended (p4 low-mem) | faster fallback (p6 low-mem) |
|---|---|---|
| VMAF, worst clip (≥ 70 good) | **76.8** | 74.8 |
| VMAF, calm clips (4) | 88–92 | 87–92 |
| bytes sent, max | 99,509 | 99,539 |
| card SSIM-Y, worst clip | 0.978 | 0.976 |
| Pi encode, final settings, worst clip | **6.4–7.5 min** (381–451 s) | 2.2–2.6 min |
| Pi time incl. the rate search, worst clip (1–3 encodes) | up to 17 min (1,026 s) | up to 5 min |
| Pi peak memory (bar 250 MiB) | **161 MiB** | 141 MiB |

- Within 0.7 VMAF of the best any encoder reached on the Mac (libaom cpu-used 3: 77.5), at full
  720p and 10 fps. Lower resolution or frame rate made it **worse**: on p6 low-mem (74.8 at
  720p, 10 fps), 960×540 → 73.8 and 5 fps → 68.8 (worst clip).
- The p6 fallback is 2 VMAF points lower for a third of the encode time (energy not measured).
- H.264 (x264 veryslow on the Pi), the most widely playable format, scores 66.9 on the worst clip
  (under the 70 bar) and needs zram (377 MiB). The Pi's hardware H.264 was not re-run; in PR #88
  (real Zero, dry 720p clips) it needed ~4.5× the bytes of the Zero's software AV1 for VMAF 70.

## Why not the background-sent-earlier pipeline (79.1, ~40 % of the encode time)?

`pi_svt_p6_lowmem+bgcache` sends only the difference from a background image the server already
holds. It was run on p6 low-mem (the stage 2 pick), so its like-for-like gain is over p6: worst
clip **79.1 vs 74.8 (+4.3)**, stress window **64.4 vs 54.1 (+10.3)**. Against the recommended p4
low-mem it is +2.3 and +5.5 (64.4 vs 58.9), so the stress window is no argument against it. Pi
2.4–2.9 min final encode (up to 9.4 min with its rate search, 7 encodes on one clip), 135 MiB.
p4 + cached background was not run (stage 3 changes only the stage 2 pick).

It is not the recommendation because of the pre-registered rule, which was fixed before any result:
79.1 − 76.8 = 2.3 < 3, so the two tie, and inside a tie **a standard bitstream beats a custom
pipeline**. The rule reflects real costs that the VMAF score does not include:
- **Extra bytes outside the 100 KB:** each background is 34–67 KB here and must be sent before the
  clips and re-sent whenever the scene changes.
- **The camera must not move, and the scene must still match the background.** Here the background
  came from another round in the same pool minutes apart, with the camera fixed on the bottom. A
  camera that swings on a mooring, or a scene whose light or water clarity changes, breaks the
  match; that was not tested.
- **Server rebuild:** the clip is a residual, not a playable video. The server adds the background
  back frame by frame before anyone can watch it, and a stock player cannot open the file.

If the field camera is rigidly mounted and the server can do the rebuild, this pipeline gives
2.3–5.5 VMAF more than p4 low-mem in about 40 % of its encode time. That is a product decision, not something this study
can settle; it would need a test with a moving camera and changing light first.

## Exploratory (post-hoc, not pre-registered): one encode at a fixed 150 kbps

The PREREG only says the rate-search attempts and their time are reported ("a deployed unit may need
a retry"). It does not allow swapping the rate control, so this is **exploratory and not part of
the recommendation**. The bench search took up to 3 encodes per clip on the main window (up to 6 on
one stress clip), which a unit can ill afford. 2-pass sizes ran 0.86–1.04× the request over the 24
searched clips, so both recipes were re-run with one encode at a fixed 150 kbps on all 12 windows:
- every file landed under 100,000 B in one encode (p4: 78,583–96,100 B; p6: up to 99,115 B, too
  close, so p6 would need a lower request such as 145 kbps);
- p4 worst clip 76.5 (−0.3; at most −0.94 on any clip); Pi 6.5–7.7 min, 8.2–9.7 min on the stress
  window; 151–153 MiB.
If the real-Pi check confirms it, a unit can skip the search. It should still check the size and
re-encode at 0.9× in the rare case it is over. The 2-pass encoder reads its input twice, so it
needs the 5 s as a file (`clip.y4m`, 69 MB): put it on the SD card, not in a RAM disk (it is not
in the memory figures).

## How good is it, honestly

- **Calm scenes look close to the original** (VMAF 88–92 on 4 of 6 clips, both windows).
- **Motion is the limit.** The worst clips are the two round 3 clips with the diver crossing the
  card (76.8 / 77.5). On the stress window (15–20 s into the same recordings, the diver close with
  bubbles: 3× more frame-to-frame change), the recommended encode drops to **58.9 / 59.5, below
  the 60 "watchable" bar**; none of the standard Pi recipes run there (p4, p6, p6 + ROI) reach 60.
  The ranking of the top candidates did not change between the two windows.

## What it looks like (check sheets, worst clip)

`decision/sheet_c3r3_main.png`, `sheet_c4r3_main.png` and `sheet_c3r3_stress.png` in the run folder
(source vs candidates, full frame + card at 2×, at the recommended encode's worst frame and the
middle frame).
- Worst main clip (diver crossing): the recommended encode keeps the diver recognisable and the
  card patches clean and flat; H.264 smears the diver and makes the card blocky; the cached
  background is the sharpest on the static floor.
- Stress window: every candidate ghosts the fast-moving diver in the first frames (VMAF 42 on
  frame 1) and softens the card's colour patches; the AprilTags stay crisp.
- Before/after slider page (private until shared): https://claude.ai/artifact/PsgPmZMuYEBhoSbCqxxu2z

## Not tested / limits

- Pi time and memory are emulated (container on the Mac's M1 Max, 4 vCPUs). Time = container time ×
  13–14 (calibrated on 60 single-thread SVT-AV1 jobs that PR #88 ran on the real Zero, before and
  after this batch). The low-memory recipes are single-threaded like the calibration jobs, so their
  range is narrow. A real nereus002 run of the top 3 (+ hardware H.264) is the next step.
- Six 5 s windows from one pool session at 2 m, static camera, 720p source (itself a 2 Mbit/s
  H.264, so the reference is not pristine). No open water, turbidity or low light.
- The clips were cut from recordings where the camera had already settled. A unit that starts the
  camera for each clip should drop the first ~1 s before the 5 s.
- The 100,000 B is the raw AV1 OBU stream. An MP4/WebM wrapper adds bytes: wrap it on the server.

## Stage summary (worst of 6 clips, VMAF)

| stage | best | runner-up | note |
|---|---|---|---|
| 1 Mac ceiling | libaom c3 77.5 | vvenc slower 76.3 | x264 66.3 |
| 2 Pi recipes | SVT p4 low-mem 76.8 | SVT p6 low-mem 74.8 | only the low-memory SVT recipes fit 250 MiB; libaom c4 77.2 needs 384 MiB |
| 3 one change on p6 low-mem | + cached background 79.1 | + card ROI 75.0 (tie) | denoise, film grain, tune VQ, 540p, 5 fps all lower |
| decision (PREREG rule) | **p4 low-mem 76.8** | p6 low-mem 74.8 | p4: tie with the cached background (2.3 < 3), standard first. Without that candidate: p6 at 540p (73.8, on the 3.0 boundary); stage 2 alone: p6 (74.8) |
| 4 stress window | + cached background 64.4 | p4 low-mem 58.9 | ranking unchanged |
