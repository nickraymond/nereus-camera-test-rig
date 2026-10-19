# nereus002 Wed dive card: pool test 2 (Wed 2026-10-21)

**DRAFT** (part C of `SPEC_pool2_prep` rev 19.2, sha256 `0e73002de78d…`; rev 19 QE 009 PASS-with-nits, 19.1 = its nits,
19.2 = "most clips" on Wed; committed byte-identical on this PR as [SPEC.md](SPEC.md)). Pool-2 prep successor 4, Mon 10/19. Nick approves this card at its commit sha
before Wed. Open items are marked **OPEN**, with who closes them.

Rig: Pi Zero 2 W + IMX708 + OpenMV N6 + OpenMV AE3 in one housing, LiFePO4wered/Pi+ UPS, the physical switch (= the UPS
button). Code: `~/nereus-rig-poolcam` = rig main **920e58f** (bench build, `2026-10-19_pool2_ladder/BUILD.md`; the gate
ran on it). Boards: firmware 1.28.0 (OpenMV v5.0.1), board scripts 7/7 hashes as in BUILD.md.

## 1. Question (Nick)

- "now I really want to see how and if we can do higher resolution on both cameras and maintain that 10fps ... will also
  start to test the difference of the N6 vs AE3" (Fri 10/16, spec "Why").
- "Yes add W1-W5 please" (debrief doc, Sun 10/18 10:44 AM, spec rev 12): can each camera see an urchin, and at what range,
  in real pool water.

## 2. What's new since Friday (bench, Mon 10/19; `~/NereusData/nereus-camera-test-rig/2026-10-19_pool2_ladder/RESULTS.md`)

| Camera | Friday | Wed (part-B winner) | Bench evidence |
|---|---|---|---|
| IMX708 | H.264 1080p30, lens 0.8 | **unchanged**, `--imx-buffers 4` (sync_video's default) | 26.8–27.6 fps, max gap 66.7 ms; must do 19 md19_buf4 PASS (0 CMA alloc errors, 0 lost); 2 buffers → 15 fps |
| N6 | MJPEG 640×400 q70 4:2:0, 10 fps | **MJPEG 1280×800 (HD) q70 4:2:0, 10 fps** | n6_hd HOLDS 3/3 (10.0 / 9.97 / 10.0 fps; one clip lost 2 frames, usable 299/300), 76 kB/frame, 22.9 MB/30 s |
| AE3 | MJPEG 640×400 q70 4:4:4, 9.3 fps | **MJPEG 640×400 q70 4:2:0, 10 fps** | ctl_2 HOLDS 3/3 at 10.0 fps, 0 lost; HD 4:2:0 = 3.17 fps (FAILS); 4:4:4 = 9.47 fps (B0) |
| N6, full colour | — | HD 4:4:4 on the swim passes 1 + check only | n6_444_probe PASS, 7.0 fps (bar ≥ 5), 85.5 kB/frame |

- Bench scene was lighter than the pool (AE3 4:4:4 22.8 kB/frame vs Friday's 25.5–26.3 kB): the AE3's pool rate is the
  pool's own number (F4).
- No N6 H.264: no OpenMV build has it (spec rev 6; openmv#3247 is a draft).
- Lens corrections: none on the rig. Wed records uncorrected video on all three cameras (spec rev 9, c_charuco).
- **25× ready gate:** **OPEN** (running Mon 10/19 from 9:49 AM, run `gate009_20261019T164921Z_pool2`; result goes here).
- **PE condition 1, one bench run of the exact program** below on 920e58f, before the Tue 6 PM freeze, judging every clip
  (the 120 s and 60 s ones too) on must do 19's bars (rev 19 item 2): **OPEN**. If a long clip fails, that phase splits
  into 30 s clips, PE 006 re-pins the string, and the bench run repeats.

## 3. The program (pinned, spec rev 19 / 19.1; one line, copy it whole)

```
video@ctl:1x30,video@best:6x30[--n6-framesize HD --ae3-subsampling 420],video@w_pass1:1x120[--n6-framesize HD --n6-subsampling 444 --ae3-subsampling 444],video@w_pass2:1x120[--n6-framesize HD --ae3-subsampling 420],video@w_check:1x120[--n6-framesize HD --n6-subsampling 444 --ae3-subsampling 444],video@w5:1x60[--n6-framesize HD --ae3-subsampling 420]
```

sha256 of the string (no trailing newline): `a605ebf1a8be748a68310a5a941f13d39dd416584d1ee4ffabb1c396e9bf9cdc`.
`pool_capture.parse_program` @ 920e58f gives the minutes below (a video phase's clips sit on a whole-minute grid: period
= clip + 30 s rounded up; each clip starts ~10 s after its minute, as on Friday).

| T+ min (switch-on) | Phase | You (diver) | The rig (all three cameras, one fire per clip) |
|---|---|---|---|
| 0–1 | **ctl**, 1 × 30 s | Getting in; nothing needed | 640×400 control at sync_video's defaults (N6 4:2:0, AE3 4:4:4), F4 |
| 1–7 | **best**, 6 × 30 s | The N6-vs-AE3 scene: same scene, distance and light for all 6 clips (must do 12) | N6 HD 4:2:0, AE3 640×400 4:2:0, IMX708 1080p30 |
| 7–10 | **w_pass1**, 1 × 120 s | Swim pass 1 (full colour), below | N6 HD **4:4:4**, AE3 640×400 **4:4:4** |
| 10–13 | **w_pass2**, 1 × 120 s | Swim pass 2 (half colour), the same swim | N6 HD 4:2:0, AE3 640×400 4:2:0 (part-B; **W1 is scored on this pass**) |
| 13–16 | **w_check**, 1 × 120 s | Check pass: the same swim again (F12 drift check) | pass 1's settings |
| 16–18 | **w5**, 1 × 60 s | Two crossings at ~1 m, board face-on: slow, then swimming speed | N6 HD 4:2:0, AE3 4:2:0 |
| 18 → | stills | Anything | Synchronized stills until switch-off or low battery (3.10 V) |

**Each swim pass (spec rev 12 must do 17):** hold the board face-on to the rig, centred between the N6 and AE3 axes,
mid-water, never touching the floor or walls. Start ~4 m out (or past where the tags vanish) when the clip starts (~10 s
after the minute). Swim slowly straight in to ~0.5 m, holding still ~5 s at about 3, 2 and 1 m (by eye off the floor
tape; the tags give the true range). The clip is 120 s.

Pool time: the program is ~18 min; the block is boxed at 30 min (F14). At the box, stop and keep what's recorded; pass 1
comes first.

## 4. The board (spec rev 12 must do 16)

Nick's AprilTag reference board: the v3 colour card with its corner tags (tag25h9), with in its white margin, laminated
and taped: the four urchin samples (2 × 80 mm, 2 × 50 mm), 80 mm and 50 mm black-and-grey quartered disks, one
slanted-edge square (black on mid-grey, ≥ 50 mm, rotated ~5°).

Before the dive, **OPEN** (Nick, card blk_board_outline):
- print check: the 10 cm bar and one 80 mm sample measured with a ruler, ±2 mm, else reprint; values here;
- one tag's black-square edge in mm (sets the range): value here;
- the mounted board's outline W × H mm and the samples' positions (ruler or a straight-on photo) →
  `edge_ml/pool2/board_layout_wed.yaml` with `measured: true` (scoring refuses the template);
- the print sheet's sha256 (debrief-doc PDF or a regenerated one) in the run MANIFEST.

## 5. Values set before any Wed data

- **Card values file:** [card_values_wed.json](card_values_wed.json), committed; every scoring stage checks it (rig #133).
  part-B chroma N6 4:2:0, AE3 4:2:0 → W1 uses pass 2 (rev 13 R3; rev 19 item 5); guard cameras none (HD and 640×400 both
  map to the fit's K); detector pinned (below). **`first_bin` OPEN:** 1.0 m for every camera unless the 1.0 m fit check
  (Nick's outline + the N6→AE3 offset ≈ (−64, −69) mm at 1.08 m, `2026-10-18_pool2_wscore_dryrun/rev16/BASELINE_N6_AE3.txt`,
  scaled by Nick's tag edge / 63) says a camera's whole board doesn't fit at 1.0 m.
- **Detector (spec rev 17):** OWLv2 `google/owlv2-base-patch16-ensemble` at revision
  `cfd3195ba4ea9592eec887ded089f4c08eff231d`, prompt "purple sea urchin", score threshold **0.3**; 0.05 and 0.5 reported
  beside it.
- **Lens values (bm #184 pool049 fits, intrinsics_full; NOT VALIDATED as a lens model; rev 13 + rev 16)**, from
  `edge_ml/pool2/pose.py` @ 920e58f:

  | Camera @ Wed mode | fx | fy | cx | cy | dist_coeffs | fit file sha256 |
  |---|---|---|---|---|---|---|
  | N6 1280×800 | 1628.86 | 1630.04 | 637.41 | 490.39 | k1 −0.421407, k2 0.253584, p1 0.002649, p2 0.001211, k3 −0.109866 | `681dda546912…` (20261017_153029_pool049_openmv_raw) |
  | AE3 640×400 (binned full field: halves the 1280×800 K) | 697.11 | 696.63 | 338.68 | 187.53 | k1 0.391043, k2 −0.159819, p1 0.004304, p2 −0.002333, k3 1.267584 | `681dda546912…` (same file) |
  | IMX708 1920×1080 (ScalerCrop (0, 0)/4608×2592) | 1095.12 | 1095.00 | 964.11 | 539.26 | rational, 8 coefficients (pose.py DIST) | `178dd3556110…` (20261017_152713_pool049_imx708_dng) |

  Both file hashes were re-checked on this Mac Mon 10/19.

## 6. Bars (set now)

**N6 vs AE3, the 6 `best` clips** (spec pass/fail block + rev 7 d57 + rev 8 R3 + rev 9 d55; rev 19 item 1: scored per clip,
median and range across the 6, never one clip):
- fps delivered ≥ 9.5 over the clip, per board;
- AE3: 0 frames lost. N6: usable = ≥ 95 % of its 300 expected frames; **≥ 4 of the 6 clips usable** (spec rev 19.2), and the
  usable clips meet every other bar;
- 0 wedges or ladder USB resets (F2; the normal per-clip board reset doesn't count);
- N6 ↔ AE3 start skew ≤ 25 ms per clip (video_sync.json first frames, d55 a);
- IMX708 ≥ 26.0 fps with max gap ≤ 100 ms (F8);
- IMX708 start ≤ 34 ms after its fire: its own verdict line, never decides a board setting (d55 b);
- host `throttled` = 0x0, else the clip is void.

**W1–W5** are scored on the Mac after the dive by `python -m edge_ml.pool2.run` (rig #133) with the bars of spec rev 12–13,
16, 17 and 18, unchanged: W1 = both 80 mm samples ≥ 30 px in each of the bin's 5 frames and each hit in ≥ 4 of 5, chain from
the first bin; W2 report-only; W3 report-only (CIEDE2000 per v3 patch, 1.0 and 2.0 m bins); W4 Nick by eye (pass 1 vs pass 2
board crops at the holds); W5 report-only (edge blur vs speed). No W result is reported before QE.

## 7. Stop rules (at the pool)

1. The LED at switch-on says a camera failed (blink codes, `docs/BLINK_CODES_nereus002.md`): switch OFF, wait 10 s, ON,
   restart the watch. The same code twice: Nick's call; the program records with the other cameras.
2. An AE3 cold-boot wedge 3 times in a day: stop the AE3, report (F2).
3. Battery: the loop stops itself at 3.10 V (`--min-vbat-mv 3100`). Start only with VBAT ≥ 3.4 V or on the charger (F3).
4. The 30-min block box (F14).
5. SD: the run refuses below 2 GB free (`--reserve-gb 2`); before the dive SD free ≥ 2 GB + 2 × 1.36 GB ≈ 4.72 GB
   (rev 19.1). This program writes ≈ 1.36 GB (64.8 MB per 30 s clip at Wed's settings, md19_buf4).
6. Host throttling: a clip with `throttled` ≠ 0x0 is void; 2 throttled clips in a row → stop (spec pass/fail block).
7. Freeze (§10): no rig change after Tue 10/20 6 PM unless Nick accepts the risk.

## 8. Bench, before leaving (Tue, by 6 PM)

```bash
ssh pi@nereus002 'cd ~/nereus-rig-poolcam && ~/nereus-camera-test-rig/.venv/bin/python scripts/gate_cycle.py disarm'
```
The gate and the pool loop must never both be armed.

```bash
ssh pi@nereus002 "bash ~/nereus-rig-poolcam/scripts/pool_mode.sh arm --program 'video@ctl:1x30,video@best:6x30[--n6-framesize HD --ae3-subsampling 420],video@w_pass1:1x120[--n6-framesize HD --n6-subsampling 444 --ae3-subsampling 444],video@w_pass2:1x120[--n6-framesize HD --ae3-subsampling 420],video@w_check:1x120[--n6-framesize HD --n6-subsampling 444 --ae3-subsampling 444],video@w5:1x60[--n6-framesize HD --ae3-subsampling 420]' --lens-position 0.8 --ups-recover-after 0 --min-vbat-mv 3100 --hours 6 --self-check"
```
**OPEN:** this arm line is checked in the PE condition 1 bench run (the run manifest's `program_sha256` must equal
`a605ebf1a8be…`). Add `--depth-bus N` if the Bar30 has moved to its own I²C bus (as on Friday's card).

```bash
ssh pi@nereus002 'bash ~/nereus-rig-poolcam/scripts/pool_mode.sh status'
```
Expect `armed: YES (… --program …)`, `crontab: 1 pool line(s)`, `dive flag: present`. Then charge to VBAT ≥ 3.4 V and
**switch OFF**.

## 9. At the pool, and after

As on Friday's card (`docs/FRIDAY_DIVE_CARD_nereus002.md` "At the pool"): switch ON and start the dive watch; read the LED
for ~1 min; seal and dive; follow §3; switch OFF to stop; **pull everything before powering the rig for anything else**
(`python -m host_tools.pool_pull --host pi@10.42.0.1 --no-dng`, then again without `--no-dng`).

## 10. Approval envelope: rig section (must do 15; the Lead drafts c_envelope_1021)

- **Units:** nereus002 only (no field units, no Spotters).
- **Verbs:** `gate_cycle.py disarm`; `pool_mode.sh arm` (the line in §8) / `status`; switch ON / OFF; `host_tools.pool_pull`.
  Recording only; no new verbs (spec rev 12).
- **Scripts:** `scripts/pool_mode.sh`, `scripts/pool_capture.py` (program above, sha256 `a605ebf1a8be…`),
  `scripts/sync_video.py`, `host_tools/pool_pull.py`, all at rig main 920e58f.
- **Stop rules:** §7.
- **Freeze:** Tue 10/20 6 PM (§10).
