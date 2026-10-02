# Video codec bench — 15 s clips, H.264 vs AV1 (2026-10-02)

Question (Nick): record a 15 s 720p or 1080p clip on the IMX708 (Pi Zero 2 W) and the OpenMV
N6, save it, compress it with H.264 or AV1 for a limited transmission budget, and show it on the
dashboard. How do the two codecs compare, and what do the devices allow? The backend side
(unpacking, dashboard playback) is out of scope here.

Study code: not imported from `src/`, nothing added to `pyproject.toml`. GPL encoders
(x264, x265) run only on the Mac as benchmarks (spec §20).

## Pipeline

| Step | Where | Command |
|---|---|---|
| IMX708 master, 15 s 1080p30 MJPEG q95 | Pi | `rpicam-vid -n -t 15000 --codec mjpeg -q 95 --width 1920 --height 1080 --framerate 30 --save-pts pts.txt -o master.mjpeg` |
| N6 master, 15 s 1280×800 JPEG q90 over USB | Pi | `python -m compression_study.video.n6_record --serial <N6 serial> --out DIR --quality 90` |
| N6 on-board limits (sensor / JPEG fps, RAM) | Pi → N6 | `python -m compression_study.phase2.pi_run_probe --board n6 --serial … --probe openmv/probes/video_rate_probe_v5.py --out DIR` |
| Lossless sources (static, simulated buoy motion, 720p, 10 fps, 360p) | Mac | `python -m compression_study.video.sources --data <data>` then `--variants` |
| On-device encodes: H.264 hardware, SVT-AV1 | Pi | `python -m compression_study.video.pi_encode --sources … --out … --encoders h264_hw` / `--encoders av1_svt_p12 --svt-params lp=1:lookahead=0:enable-tf=0 --raw-input DIR` |
| Live H.264 while recording (no master) | Pi | `python -m compression_study.video.pi_live_h264 --out DIR` |
| Best-case encodes (x264, x265, SVT-AV1 preset 4) | Mac | `python -m compression_study.video.mac_encode --sources … --out <data>/enc_mac` |
| Scores (VMAF / PSNR / SSIM, per frame) | Mac | `python -m compression_study.video.score --data <data> --shard i/4 --threads 3` (4 workers) |
| Summary | Mac | `python -m compression_study.video.analyze --data <data>` |
| Page (player, cut sheet, charts, tables) | Mac | `python -m compression_study.video.build_page --data <data> --out compression_study/work/video_page` |

`<data>` = the primary checkout's `data/video_20261002/` (git-ignored): masters, sources,
encodes, `scores_*of4.jsonl`, `summary.json`.

## Lessons for the next run

- **Frame pairing in scoring.** VMAF must compare frame N with frame N. Pairing by timestamp
  drifted by a frame wherever container time bases differ (FFV1/mkv 1/1000 vs mp4 1/10240 or
  1/15360), even after rescaling frame indices: motion clips lost 15–20 VMAF points and the
  N6 clips (18.4 / 9.2 fps) had 20–77 misaligned frames each. `score.py` now puts both inputs
  on one time base (`settb=1/30,setpts=N`) and checks frame counts.
- **SVT-AV1 on the Zero.** Default settings run out of memory (415 MB) even at 720p; one thread,
  no look-ahead and no temporal filter fit (≤ 250 MB at 1080p, free memory down to ~23 MB).
  Feed it raw YUV from disk: decoding the FFV1 source in the same process let frames pile up
  in front of the slow encoder and pushed the Zero into swap.
- **Memory safety.** Each bench ffmpeg runs with `oom_score_adj = 1000` (the kernel kills it
  first) plus a MemAvailable watchdog, because Nick's field services share the Zero.
- **Rate control.** The Zero's hardware H.264 has a size floor (it ignores targets below it);
  SVT-AV1 with no look-ahead undershoots small targets on still scenes. Compare on real sizes.
