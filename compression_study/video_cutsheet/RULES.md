# E7 video screen: window rule, encoders and scoring (fixed before any encode)

Written Sat 10/17/2026 ~3:10 PM PT, **before any encode and before any source frame was viewed**. It implements
[SPEC.md](SPEC.md) rev 3 (sha256 `d1154ff10642…`, QE 004 spec PASS 2:50 PM, sha confirmed on this PR 2:57 PM).
Where this file and the spec differ, the spec wins.

## 1. Sources and the 5 s windows

Read straight from Nick_R_SSD; nothing in a source folder is moved, renamed or edited.

**Moving camera (primary, Reef Check, diver-held):** Nick's two named folders, Oct 2022 and Oct 2025 (spec must do 1).
Mendocino Headlands is not used. Five videos exist (`…/2026-10-15_reefcheck/video/October 2022|2025/` and the
prescreen copies in `…/2026-10-17_urchin_prescreen/`), all 3840×2160 HEVC at 30000/1001 fps (ffprobe, before this rule).
- Video pick: sort the five file names (C locale) and take the 1st, 3rd and 5th:
  `Caspar-South-Restoration-25-10-16-1130-IanNorton-c1`, `…-1215-IanNorton-c3` (Oct 2025),
  `FullersResto-2022-10-15-xxx-JanFreiwald-leadline2` (Oct 2022).
- Window: 150 source frames (5.005 s) starting at frame `floor(0.50 × nb_frames)` (mid-video: away from descent and
  ascent).
- Camera-motion check (source only, no encode): global shift between consecutive frames by phase correlation on luma at
  320×180 (OpenCV `phaseCorrelate`, Hanning window); statistic = median |shift| over the 149 frame pairs, in px at
  320×180. A window passes if the median is ≥ 0.5 px (≈ 2 px per frame at 720p). If it fails, try start = 25 %, then
  75 % of `nb_frames`; if all three fail, keep 50 % and label the window "low camera motion".
- **Primary window** (the one sample clip per board for the first look): the window with the highest median shift
  (spec F1: the moving background is the worst case).
- Turbidity (F5) is not targeted by this rule: each window's luma contrast (std of Y at 320×180) is reported as a
  descriptor only; turbid water is "not covered" unless that number shows it.

**Diver (static background, moving subject):** Nick's 30 s diver clip is the pool round-3 recording (his board comment
10/16 05:27 UTC: a 30 s scene with a diver in frame and the same background). Group = the two round-3 pool clips.

**Pool (our own camera, the F4 control):** the 6 clips of PREREG v2, same cut (frames 20–69, 2.0–6.9 s). The FFV1
references made for rig #116 (`2026-10-15_video100k_5s/ref/*_f020.mkv`) are reused read-only.

## 2. References (spec rev 3: one FFV1 reference per frame rate)

- Scale 3840×2160 → 1280×720, bicubic, full-range BT.709 source → limited-range yuv420p; lossless FFV1. Each reference
  is both the encoder input and the VMAF reference.
- Frame rates from the same 150-frame window, by decimation only (no interpolation):
  **30 fps** = all 150 frames; **15 fps** = every 2nd frame (75); **10 fps** = every 3rd frame (50). The source is
  29.97 fps, so the time base is relabelled to exactly 30 / 15 / 10 fps (a 0.1 % slow-down; stated on the sheet).
- Pool and diver clips are 10 fps at the source: their 15 and 30 fps rows are **"not covered"** (rev 3).
- The Reef Check source is already compressed (HEVC ~17–45 Mbit/s at 4K): VMAF is relative to it (F4).

## 3. Encoders: desk emulation of each board's own codec (rev 3), on the Mac

Each board at its rated rate at 720p: **AE3 10 fps, N6 15 fps, IMX708 30 fps**. Sizes per 5 s clip: **500 KB first**
(the first-look sample), then 100 KB and 1 MB (1 KB = 1000 B; bytes = the raw stream that would be sent: concatenated
JPEGs for MJPEG, Annex-B for H.264). Target band 95–100 % of the size where the knob allows; otherwise the closest
size under the target, and the point is labelled with its real size.

| Board | Codec emulated | Off-the-shelf encoder and settings | Rate knob |
|---|---|---|---|
| AE3 | MJPEG, software JPEG (OpenMV `lib/imlib/jpege.c`) | libjpeg-turbo `cjpeg -quality Q -dct int`, baseline, standard Huffman tables (no `-optimize`); subsampling by OpenMV's AUTO rule: Q ≤ 35 → 4:2:0, 36–59 → 4:2:2, ≥ 60 → 4:4:4 | one Q (1–100) for all frames, the highest Q whose total ≤ target |
| N6 | H.264 if its firmware exposes the hardware encoder (VC8000NanoE); else MJPEG | H.264: x264 `--profile baseline --preset veryfast`, 1 pass, `--bitrate` with `--vbv-maxrate` = bitrate and `--vbv-bufsize` = 1 s, one key frame per clip. MJPEG: as AE3 but always 4:2:0 and Q in steps of 10 (the VC8000 path uses `qLevel = quality/10`) | bitrate search (≤ 6 encodes); MJPEG: Q steps of 10 |
| IMX708 (Pi) | H.264, Pi hardware encoder (Friday: `rpicam-vid --codec h264 --inline`, profile/level/intra left at rpicam-vid defaults) | x264 `--profile high --level 4.2 --preset veryfast --keyint 60`, 1 pass, bitrate with VBV as above | bitrate search (≤ 6 encodes) |

- x264 `veryfast` is a stand-in for fixed-function hardware encoders, which usually do worse than x264 at equal bytes;
  every cell says "desk emulation" and the PR #88 measurement (real Zero hardware H.264 needed ~4.5× the bytes of the
  Zero's software AV1 for VMAF 70) is quoted beside it.
- **Via the Pi (only if time remains after the first look):** the same references through the rig #116 Pi recipe
  (SVT-AV1 preset 6, low-memory settings, 2-pass), to show what transcoding on the Pi buys per board. Labelled.
- The AE3 720p 10 fps cell is hypothetical (spec rev 3): Friday's AE3 managed 9.33 fps at 640×400, and the rig's bench
  notes give ~2.3 fps at HD with its software JPEG.

## 4. Scoring

- VMAF `vmaf_v0.6.1`, each encode against the reference at **its own frame rate**, frame N vs frame N on one time base
  (PR #88 pairing fix): mean, 5th-percentile frame, and the worst frame (its index, for the still).
- SSIMULACRA2 on 10 frames evenly spread over the clip (`frame = round(k × (n − 1) / 9)`, k = 0..9), RGB PNG with the
  same conversion on both sides.
- VMAF across boards is not like-for-like (different frame rates change VMAF's motion feature): the sheet compares
  boards by eye and by bytes per frame, and each board's VMAF against its own reference.
- **Knee** (spec must do 5 as amended in rev 3): per encoder, the smallest size s such that every size ≥ s has a
  worst-clip VMAF within 3.0 of its 1 MB value. With three sizes it is only bracketed; the sheet says so.
- Pi cost: a labelled estimate from the calibrated 10/15 study (rig #116), scaled by frame count; no Pi runs.

## 5. Outputs and stop rules

- Run folder `~/NereusData/nereus-camera-test-rig/2026-10-17_video_screen/` with MANIFEST.md (sources + SHA-256 of each
  reference, code sha, host). Only `.md/.csv/.json` and scripts go in git.
- Heavy compute under PjM 003's all-clear (2:59 PM): `nice 10`, ≤ 2 parallel jobs, stop below 8 GiB free.
- Stop and tell the PE and PjM 003 at the ~6 h cap (session start 2:55 PM → ~9 PM Sat), if Mac free disk < 8 GiB plus
  the largest source (~1.1 GB), or if any step needs custom encoder work or the rig.
