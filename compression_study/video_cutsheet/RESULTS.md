# E7 video screen: which board can encode what at 720p, and what 100 KB / 500 KB / 1 MB buys

Desk study, Sat 10/17/2026, 2:55 PM onward, per [SPEC.md](SPEC.md) rev 3 (sha256 `d1154ff10642…`, QE 004 spec PASS 2:50 PM,
sha confirmed on PR #122 at 1bad610) and [RULES.md](RULES.md) (committed `af28877` at 3:02 PM, before any encode; its
text says "~3:10 PM", the commit time is the authoritative one). Mac only; no rig, no Pi, no builds, no custom encoders.
Run data: `~/NereusData/nereus-camera-test-rig/2026-10-17_video_screen/` (MANIFEST.md). Full numbers:
[results_table.md](results_table.md). First-look page: https://claude.ai/artifact/BDDpQpuvqjEgqZcUQJxqGi (private; Sat 3:31 PM).

## The answer in one paragraph

Off the shelf, **only the IMX708 on the Pi encodes real video by itself today** (the Pi's hardware H.264, real time).
**The N6 encodes only JPEG with stock firmware**: its chip has an H.264 block, but using it needs a custom firmware build
(an unmerged upstream draft). **The AE3 has no encoder hardware** and manages ~2 fps at 720p with software JPEG, so
720p at 10 fps needs custom work or a lower resolution. **MJPEG cannot get under ~0.8 MB (AE3) or ~1.3 MB (N6) per 5 s
at 720p**, so 100 KB and 500 KB clips at 720p need a real video codec: today that means the Pi encodes (its hardware
H.264, or AV1 in software), fed by its own camera or by the N6's USB stream. At equal bytes, **AV1 on the Pi looks far
better than H.264** (see the table), but costs minutes of Pi time per clip instead of real time.

## Board × encoder table (main deliverable)

Capability from docs, firmware source and earlier rig measurements only (sources in [capability.json](capability.json)).

| Board | Encoder | Where it runs | 720p at rated fps? | Today | Note |
|---|---|---|---|---|---|
| IMX708 (Pi Zero 2 W) | H.264 | Pi hardware encoder (rpicam-vid) | Yes, 30 fps (docs: up to 1080p30) | **Feasible now** | Real time while recording, ~4.7 W (PR #88). Ignores size targets below ~65 KB per 5 s at 720p30 with motion (scaled from PR #88's 195 KB per 15 s). Friday's dive ran 1080p30. |
| IMX708 (Pi Zero 2 W) | AV1 | Pi CPU, SVT-AV1 2.3 (Debian package, BSD) | Yes, slowly: 6.6–7.8 min per 5 s clip (estimate) | **Feasible now** | Low-memory settings, 141 MiB (rig #116, emulated + calibrated). Estimate = 3× the measured 10 fps time. |
| IMX708 (Pi Zero 2 W) | MJPEG | Pi CPU (rpicam-vid uses libjpeg) | Yes | **Feasible now** | Not useful at these sizes: its smallest 5 s clip at 30 fps would be ~2.4 MB (estimate: 3× the 10 fps MJPEG floor; not run). |
| IMX708 (Pi Zero 2 W) | H.265 | — | No hardware encoder | **Not an option** | Software x265 is GPL, which the licence policy excludes from shipped code (SPEC §20). |
| OpenMV N6 | MJPEG | N6 hardware JPEG | Yes, 15 fps (31 fps measured at 1280×800) | **Feasible now** | Smallest 5 s clip ~1.3–1.9 MB at 720p / 15 fps (desk emulation): can't reach 1 MB. No SD card; RAM holds ~5 s. |
| OpenMV N6 | H.264 | N6 hardware block (VENC, in the chip) | Chip: up to 1080p30 (ST datasheet) | **Needs custom work** | Not in stock OpenMV firmware (v5.0.1; dev build 10/16). An unmerged draft, openmv/openmv #3247, adds it: a custom firmware build. OpenMV IDE 5.1.0's 'H.264' (10/12) records on the PC, not the board. |
| OpenMV N6 → Pi | H.264 (Pi hardware) or AV1 (Pi CPU) | N6 streams JPEG over USB, the Pi encodes | Yes: N6 streamed 1280×800 at 29.6 fps (q70) to the Pi | **Feasible now** | Pi time per clip: H.264 real time; AV1 ~3.3–3.9 min (estimate). |
| OpenMV AE3 | MJPEG | AE3 CPU (software JPEG) | No: ~2.3 fps at 1280×800 (rig bench note); 9.3 fps at 640×400 (Friday) | **Needs custom work (or lower res / fps)** | Smallest 5 s clip ~0.8 MB at 720p / 10 fps even if the rate were reached. The pool-2 prep bench is re-measuring the AE3's best resolution. |
| OpenMV AE3 | H.264 / H.265 | — | No hardware encoder in the Alif E3 | **Needs custom work** | No OpenMV video encoder; software H.264 on the MCU would be new code. |
| OpenMV AE3 → Pi | H.264 / AV1 on the Pi | AE3 streams JPEG, the Pi encodes | No at 720p / 10 fps: limited by the AE3's JPEG rate | **Feasible now only at lower res / fps** | For example 640×400 at ~9.3 fps (Friday). |

## Quality per 5 s clip (desk emulation, worst of the 3 moving-camera windows)

| encoder | 100 KB worst / mean VMAF | 500 KB | 1 MB | knee (bracketed) | MJPEG floor (smallest setting) |
|---|---|---|---|---|---|
| IMX708 · H.264 Pi hardware (desk proxy) · 30 fps | 3.7 / 11.8 | 42.2 / 52.3 | 64.8 / 70.9 | 500 KB–1 MB or above (bracket; still rising at 1 MB) | — |
| IMX708 · same, no B-frames like the Pi hardware (sensitivity, post-hoc) · 30 fps | 1.8 / 11.6 | 43.0 / 53.1 | 66.4 / 72.0 | 500 KB–1 MB or above (bracket; still rising at 1 MB) | — |
| IMX708 · AV1 on the Pi (software) · 30 fps | 34.9 / 41.8 | 70.6 / 76.2 | 81.4 / 85.0 | 500 KB–1 MB or above (bracket; still rising at 1 MB) | — |
| N6 · MJPEG on-board hardware JPEG · 15 fps | can't reach (floor 1275 KB) | can't reach (floor 1275 KB) | can't reach (floor 1275 KB) | n/a (missing sizes) | 1275–1868 KB, worst VMAF 64.9 |
| N6 · H.264 on-board (needs custom firmware; desk proxy) · 15 fps | 2.0 / 11.7 | 51.4 / 61.5 | 73.2 / 78.4 | 500 KB–1 MB or above (bracket; still rising at 1 MB) | — |
| N6 → Pi · AV1 on the Pi (software) · 15 fps | 39.0 / 47.1 | 74.6 / 81.1 | 85.2 / 89.4 | 500 KB–1 MB or above (bracket; still rising at 1 MB) | — |
| AE3 · MJPEG on-board software JPEG · 10 fps | can't reach (floor 769 KB) | can't reach (floor 769 KB) | 48.1 / 61.0 | n/a (missing sizes) | 769–801 KB, worst VMAF 20.6 |
| AE3 → Pi · AV1 on the Pi (software) · 10 fps | 41.9 / 50.9 | 76.7 / 84.0 | 86.8 / 91.6 | 500 KB–1 MB or above (bracket; still rising at 1 MB) | — |

Diver (pool round 3) and pool (6 clips, our camera, 10 fps only): see [results_table.md](results_table.md). AV1 on the Pi at 10 fps scores 74.1 / 91.3 / 95.1 (worst clip at 100 KB / 500 KB / 1 MB) there; rig #116 got 74.8 at 100 KB for the same recipe on the same clips.

- VMAF 0–100 (70 ≈ good, 60 ≈ watchable), each board against the source at **its own** frame rate; across boards it is
  not like-for-like (frame rate changes VMAF's motion term, and VMAF ignores 10 fps choppiness).
- **Knee** (spec must do 5 as amended by rev 3): only bracketed with three sizes. On moving-camera footage every encoder that reaches 500 KB still gains 10–23 VMAF points
  from 500 KB to 1 MB on the worst clip, so every knee is the bracket 500 KB–1 MB or above: the smallest size with the
  most quality is not found below 1 MB. On the static pool clips (AV1, 10 fps) 500 KB → 1 MB adds 3.8 points (91.3 → 95.1),
  just over the 3-point bar. Where the worst-clip curves cross the bars: AV1 on the Pi passes 70 between 100 and 500 KB
  at 15 and 10 fps, and just at 500 KB at 30 fps (70.6); the N6 H.264 proxy passes 60 and 70 between 500 KB and 1 MB;
  the IMX708 H.264 proxy passes 60 between 500 KB and 1 MB and doesn't reach 70 by 1 MB (64.8); AE3 MJPEG reaches
  neither (48.1 at 1 MB).
- **Spotter answer (≤ 100 KB per 5 s at 720p):** nothing is watchable on the worst moving-camera clip (AV1 on the Pi 34.9 / 39.0 / 41.9
  at 30 / 15 / 10 fps; H.264 proxies ≤ 3.7; MJPEG can't get that small). On the static pool clips AV1 at 10 fps reaches
  74.1. **Recommendation:** AV1 (SVT-AV1 p6 low-mem) on the Pi at 10 fps, 100 KB, for static scenes only; a swaying
  mooring at 100 KB needs fewer pixels or frames, which this screen did not test.
- **Own telemetry module:** **recommendation:** AV1 on the Pi at ~500 KB per 5 s (~800 kbit/s) is the smallest
  tested size that is "good" (≥ 70) on the worst moving-camera clip at every board rate (70.6–76.7), fed by the IMX708
  or by the N6's USB stream. Pi hardware H.264 (real time, ~10 J per clip) needs ≥ 1 MB to be watchable on moving
  footage (64.8 at 30 fps, likely lower on the real encoder). The encoder and board choice are Nick's.

## Failure modes (spec F1–F6)

- **F1 mooring sway:** confirmed and large. AV1 at 10 fps: worst moving-camera clip 76.7 at 500 KB vs static pool 74.1
  at 100 KB, so a moving background needs ~5× the bytes for the same quality.
- **MJPEG has a size cliff, not a smooth curve** (checked after PjM 003's question, `checks/ae3_mjpeg_q_sweep_rc_c3.json`):
  on the primary clip the AE3 emulation gives 769 / 770 / 789 / 822 / 879 / 996 KB at quality 1 / 3 / 5 / 8 / 12 / 19
  with VMAF 34 / 37 / 48 / 60 / 70 / 81. At quality 1 all 64 luma quantizers are capped at 255 (56 at quality 3, 45 at
  5), so the file barely shrinks while the picture keeps falling apart. The jump from 34 at 769 KB to 81 at 996 KB is real.
- **F2 smearing VMAF under-weights:** at 1:1 the H.264 proxies at 500 KB smear moving kelp; the AE3's 1 MB MJPEG scores
  VMAF 81 on the primary window but shows colour blotches (VMAF reads luma only; SSIMULACRA2 11.5 flags it). The page
  shows one fixed moment per board (source frame 72); worst-frame indices are in `results.csv`.
- **F3 Pi time with motion:** not measured (rev 3: estimate only). Estimates stay inside PREREG v2's bars (AV1 30 fps
  ~6.6–7.8 min < 10 min; 141 MiB < 250 MiB), but motion clips may run longer than the pool-calibrated estimate.
- **F4 compressed source flatters VMAF:** the Reef Check source is 4K HEVC (17–45 Mbit/s) scaled 3× down; scores are
  relative to it. Control: the pool clips (our camera) reproduce rig #116 (74.1 vs 74.8).
- **F5 turbid water:** partly covered. The primary window (Caspar c3) is green and hazy; no low-light window.
- **F6 Pi throttling:** not applicable (no Pi runs).

## Pi cost per 5 s clip (estimate; no Pi runs in this screen)

| Encode on the Pi | Time per 5 s clip | Energy | Basis |
|---|---|---|---|
| Pi hardware H.264 (IMX708, or re-encoding the N6 stream) | real time | ~10 J above idle per clip (4.7 W while recording vs 2.9 W idle) | measured (PR #88), scaled to 5 s |
| AV1 on the Pi, 10 fps (AE3 rate) | 2.2–2.6 min | ~80–110 J | rig #116 calibrated time; power from PR #88 (~0.6–0.7 W above idle) |
| AV1 on the Pi, 15 fps (N6 rate) | 3.3–3.9 min | ~120–160 J | estimate: 1.5× the 10 fps time |
| AV1 on the Pi, 30 fps (IMX708 rate) | 6.6–7.8 min | ~240–330 J | estimate: 3× the 10 fps time |

## What was done (and one correction during the run)

- Windows (RULES.md §1): Caspar c1 and c3 (Oct 2025) at 50 % of the video, FullersResto leadline2 (Oct 2022) at 75 %
  (its 50 % and 25 % windows fell under the 0.5 px/frame camera-motion bar). Primary window = Caspar c3 (highest motion,
  1.43 px/frame at 320×180). Diver = the two round-3 pool clips; pool = the six PREREG v2 clips (10 fps only, so their
  15 / 30 fps rows are "not covered").
- References: one FFV1 per frame rate from the same 150-frame window (30 / 15 / 10 fps by decimation).
- Encoders: libjpeg-turbo `cjpeg` (OpenMV's subsampling rule; N6 in Q steps of 10), x264 `veryfast` as the hardware
  H.264 stand-in (N6 baseline with one key frame; IMX708 high 4.2 with intra 60, Friday's rpicam-vid defaults),
  SVT-AV1 p6 low-memory 2-pass (the rig #116 Pi recipe). x264 is GPL: used only as a Mac benchmark process in study
  code (SPEC §20, approved 2026-10-01).
- **Correction:** the first scoring pass had a colour-tag mismatch (BT.709-tagged references vs untagged AV1 output and
  BT.601 JPEG), which made ffmpeg convert one input before VMAF and SSIMULACRA2. Fixed before any result left the Mac:
  cjpeg now gets RGB decoded with its own BT.601 matrix, and both sides are labelled BT.709 limited range before
  scoring. Check: PSNR-Y through the scoring chain equals PSNR-Y on the raw decoded planes (44.837 vs 44.837 dB on AV1,
  39.967 vs 39.967 on H.264); lossless H.264 scores VMAF 99.95 / SSIMULACRA2 100. Every result here is from the
  re-run; the pre-fix run is kept in `superseded/` and not used.

## Not covered / limits

- **Desk emulation, not the boards.** x264 `veryfast` stands in for hardware H.264. On the real Zero (PR #88, 10/02)
  hardware H.264 scored ~10 VMAF below x264 medium 2-pass at equal size, so the IMX708 H.264 rows are probably
  optimistic. A post-hoc sensitivity without B-frames (the Pi hardware has none) gives the same picture (43.0 vs 42.2
  worst clip at 500 KB). The N6's real JPEG tables (VC8000, `qLevel = quality/10`) differ from libjpeg's, so its MJPEG
  floor is approximate.
- **AE3 at 720p / 10 fps is hypothetical:** a rig bench note gives ~2.3 fps at 1280×800 with its software JPEG (not
  re-measured here; the pool-2 prep bench is measuring it).
- Pi time and energy are estimates from rig #116 and PR #88; no Pi or board runs.
- Three sizes only (100 KB / 500 KB / 1 MB): knees are bracketed; the full 25 KB–1 MB curve was not run (Nick's call).
- VMAF is per board frame rate and not comparable across boards; it ignores choppiness. The 29.97 fps source is
  relabelled to 30 / 15 / 10 fps (0.1 % slower).
- One window (FullersResto leadline2) needed the 75 % fallback of the motion rule; its motion is the lowest (0.62 px).

## Spec check-off (rev 3)

| Spec item (rev 3 governs) | Status | Evidence |
|---|---|---|
| Board × encoder table, on-board vs needs-the-Pi, res, fps, feasible now vs custom work | done | table above; `capability.json`; page |
| 1 desk-emulated sample per board at 500 KB | done, with gaps | IMX708: H.264 proxy 500 KB clip. N6 and AE3: their own codec (MJPEG; N6 firmware has no H.264) can't reach 500 KB, so the page shows N6 → Pi AV1 500 KB, the N6 H.264 custom-firmware proxy at 500 KB and the N6 MJPEG floor (still); AE3 MJPEG at 1 MB, its floor (still) and AE3 → Pi AV1 500 KB (hypothetical rate) |
| One FFV1 reference per frame rate; native rate stated; < 30 fps sources "not covered" | done | `windows.json`, MANIFEST.md; pool 15 / 30 fps "not covered" |
| AE3 720p / 10 fps marked hypothetical | done | table, page |
| Window rule committed before the first encode | done | `af28877` 3:02 PM; references made 3:03 PM, first codec encode 3:06 PM |
| Must do 1: ≥ 3 Reef Check windows with camera motion; diver; pool | done | 3 windows (0.62–1.43 px/frame); diver = pool round 3; 6 pool clips |
| Must do 3 (p6 / p4 / p6 540p) | replaced by rev 2 (off-the-shelf per board) | AV1 p6 low-mem used for the via-Pi rows; p4 and 540p not run |
| Must do 4 sizes | 100 KB / 500 KB / 1 MB per rev 2; full curve not run | `results_table.md` |
| Must do 5 knee, bracketed (rev 3) + 70 / 60 crossings + Spotter point | done | Knee section above |
| Must do 6 Pi cost as a labelled estimate (rev 3) | done | Pi cost table |
| Must do 7 worst-frame stills | not done: page shows a fixed moment per board; worst-frame indices listed | `results.csv` |
| Every ladder point scored or listed with a reason | done | 87 scored; 27 MJPEG cells below its floor, each listed with the floor size |
| Sheet published before Mon 10/19 | done | Sat 3:31 PM |

Session time: 2:55 PM to about 3:45 PM Sat, well inside the ~6 h cap. No stop-early line was crossed (disk ≥ 95 GiB free,
no custom encoder work, no rig).
