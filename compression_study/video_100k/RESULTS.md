# Results — best 5 s underwater clip in ≤ 100 KB, encodable on a Pi Zero 2 W

Run 2026-10-15 (Thu) 9:59–11:29 AM PT on the Mac, per [PREREG.md](PREREG.md) v2 (committed
`2c3750c` before any 5 s result). Run data: `~/NereusData/nereus-camera-test-rig/2026-10-15_video100k_5s/`
(MANIFEST.md, results.jsonl per stage, bitstreams). Full table: [results_table.md](results_table.md).
Pi numbers are **emulated, calibrated** (Pi-matched arm64 container, scaled to PR #88's real
nereus002 runs); a real-Pi check of the top 3 is still to come (after the nereus002 hand-over and
Friday's pool pull).

## Recommendation

**AV1 with SVT-AV1 2.3 (the Pi's own Debian package), preset 4, low-memory settings, 1280×720,
10 fps, one key frame, 2-pass at a fixed 150 kbps request.**

```
SvtAv1EncApp --preset 4 --rc 1 --tbr 150 --passes 2 --keyint 300 --scd 0 \
  --lp 1 --lookahead 0 --enable-tf 0 --stats pass.stats -i clip.y4m -b clip.ivf
# send the AV1 OBU stream (ffmpeg -i clip.ivf -c copy -f obu clip.obu)
```

| | recommended (p4 low-mem) | faster fallback (p6 low-mem) |
|---|---|---|
| VMAF, worst of 6 clips (≥ 70 good; pre-registered ranking, bitrate searched) | **76.8** | 74.8 |
| same, one encode at a fixed 150 kbps (what a unit does; post-hoc) | 76.5 | 74.8 |
| VMAF, calm clips (4) | 88–92 | 87–92 |
| bytes sent at 150 kbps, max of 12 windows (cap 100,000) | 96,100 | 99,115 |
| card SSIM-Y, worst clip | 0.978 | 0.976 |
| Pi Zero 2 W encode, one 5 s clip (emulated) | **6.5–7.7 min**; busy scene 8.2–9.7 min | 2.1–2.5 min; busy 3.1–3.7 min |
| Pi peak memory (bar 250 MiB) | **151–162 MiB** | 136–141 MiB |

- It is within 0.7 VMAF of the best any encoder reached on the Mac (libaom cpu-used 3: 77.5),
  at the full 720p and 10 fps. Lower resolution or frame rate made it **worse**, not better:
  960×540 → 73.8, 5 fps → 68.8 (both on p6 low-mem, worst clip).
- The p6 fallback is 2 VMAF points lower for a third of the encode time (energy not measured).
  Under the pre-registered tie rule it lost only because the top eligible score (79.1, below) set
  the tie window; if the encode time matters more than 2 points, p6 low-mem is the pick.
- Today's widely playable choice, H.264 (x264 veryslow on the Pi), scores 66.9 on the worst clip
  (under the 70 bar), and needs zram (377 MiB). The Pi's hardware H.264 was not re-run; in PR #88
  (real Zero, dry 720p clips) it needed ~4.5× the bytes of the Zero's software AV1 for VMAF 70.

## How good is it, honestly

- **Calm scenes look close to the original** (VMAF 88–92 on 4 of 6 clips, both windows).
- **Motion is the limit.** The worst clips are the two round 3 clips with the diver crossing the
  card (76.8 / 77.5). On the stress window (15–20 s into the same recordings, the diver close with
  bubbles: 3× more frame-to-frame change), the recommended encode drops to **58.9 / 59.5, below
  the 60 "watchable" bar**; none of the standard Pi recipes run there (p4, p6, p6 + ROI) reach 60.
  The ranking of the top candidates did not change between the two windows.
- **Background sent earlier** (residual against a background cached at the server, e.g. sent once
  a day; 34–67 KB, not counted in the clip): best of all, 79.1 worst clip and 64.4 on the stress
  window, Pi 2.4–2.9 min, 135 MiB. It needs server-side reconstruction and a background that
  matches the scene, so the rule ranks it after a standard bitstream. Worth a follow-up if the
  camera stays put.

## Rate control on the unit (post-hoc, not pre-registered)

The bench searched the bitrate to land in 95–100 % of the cap (up to 6 encodes on one stress clip).
A unit can't afford that. 2-pass sizes ran 0.86–1.04× the request over the 24 searched clips, so we re-ran
both recipes with one encode at a fixed **150 kbps** request on all 12 windows (main + stress):
**every file landed under 100,000 B in one encode** (p4: 78,583–96,100 B; p6: up to 99,115 B, too
close, so give p6 a lower request such as 145 kbps). Quality cost vs the searched rate: ≤ 0.94 VMAF
on any clip, 0.3 on the worst clip. A unit should still check the size and re-encode at 0.9× in the
rare case it is over. The 2-pass encoder reads its input twice, so it needs the 5 s as a file
(`clip.y4m`, 69 MB): put it on the SD card, not in a RAM disk (it is not in the memory figures).

## What it looks like (check sheets, worst clip)

`decision/sheet_c3r3_main.png` and `sheet_c3r3_stress.png` in the run folder (source vs candidates,
full frame + card at 2×, at the recommended encode's worst frame and the middle frame).
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
| 4 stress window | + cached background 64.4 | p4 low-mem 58.9 | ranking unchanged |
