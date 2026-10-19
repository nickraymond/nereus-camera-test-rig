# Pool-2 ladder + must do 19 on nereus002, Mon 10/19 (TEST_CARD_B rev 4 §4 steps 3–4b)

Pool-2 prep successor 4, under the rig session's written hand-over (Mon ~9:05 AM PT). Build: main 920e58f (BUILD.md).
Every label pulled with its SHA256SUMS verified on the Mac (0 mismatches). Clip table: ladder_clip_table.csv
(scripts/video_clip_table.py @ main). Times are Pi `date` (PDT).

## Conditions
- In air, home bench, fixed scene, room/daylight. IMX708 scene still mean luma 111.8 (lit). IMX708 Lux per clip 151–302
  (rising through the morning; md19 clips 198–302); settings compared within a label are back to back.
- Busy scene: AE3 640×400 q70 4:4:4 scene clip 22.8 kB/frame < 24 kB → **every AE3 row is flagged "scene lighter than
  the pool"** (B0: 23.1 kB).
- Power: charger, VIN 4976 mV, throttled 0x0 in every clip.

## Ladder (09:06–09:24 AM)
| label | setting (one board changed) | result | board numbers |
|---|---|---|---|
| ctl_2 (N10 controls) | N6 640×400 q70 (4:2:0), AE3 640×400 q70 4:2:0 | **HOLDS** 3/3 | N6 10.0 fps, 0 lost, 23 kB, JPEG 14 ms; AE3 10.0 fps, 0 lost, 16.8 kB, JPEG 63 ms |
| n6_hd | N6 1280×800 q70 4:2:0, AE3 at control | **HOLDS** 3/3 | N6 10.0 / 9.97 / 10.0 fps; clip02 lost 2 (short 2, max gap 200 ms), usable 299/300; 76 kB/frame, 22.9 MB/30 s, JPEG 26 ms, write 7 ms |
| ae3_hd | AE3 1280×800 q70 4:2:0, N6 at control | **FAILS** (clip 1, fail fast) | AE3 3.17 fps, JPEG 241 ms, 50 kB/frame; misses openmv_ae3_fps, board_skew |
| n6_960_probe | — | not run | n6_hd held (card §4: only if n6_hd fails) |
| n6_444_probe | N6 1280×800 q70 4:4:4, AE3 at control | **PROBE PASS** (not a "holds") | N6 7.0 fps (bar ≥ 5), 0 lost, measured 444, 85.5 kB/frame, JPEG 134 ms |

- IMX708 in every clip: 26.8–27.6 fps, max gap 66.7 ms, start offset ≤ 20.8 ms.
- Resync check on HD: n6_hd clip01 short 0, bad 0, measured 1280×800 4:2:0 as asked.
- **Answers:** N6 best = HD 1280×800 4:2:0 (holds 10 fps). AE3 best = 640×400 4:2:0 (HD 3.17 fps; the AE3 offers only
  QVGA / VGA / HD). N6 full colour at HD is possible at 7.0 fps (n = 1 probe).
- Host: min MemAvailable 99–144 MB per clip (< 150 MB flag on every clip), CmaFree min 0–1 MB in every clip (report-only).

## Must do 19 (09:25–09:29 AM), Wed modes: N6 HD 4:2:0, AE3 640×400 4:2:0
| label | IMX708 buffers | pool2_bench | md19_check |
|---|---|---|---|
| md19_buf4 | 4 | HOLDS 3/3 (N6 0 lost in all 3) | **PASS**: CMA bar PASS (0 alloc errors, 0 failure lines in any window), capture bar PASS, no reset (boot 06ae28ee, btime 1792425524), CmaTotal 262144 kB |
| md19_buf2 | 2 | FAILS clip 1 (fail fast) | CMA bar PASS (0 failure lines); capture bar FAIL: IMX708 15.0 fps, not ok (rpicam-vid ran to its 34.5 s timeout at 15 fps); verdict printed UNKNOWN (1 clip < 3) |

- **Outcome (§5b): both bars pass at (a) → Wed uses `--imx-buffers 4`. No CMA raise is needed** (the raise applies only if the
  CMA bar fails at both).
- md19_buf4's first md19_check run printed exit 6 (clip03 "this boot's journal can't show the window"): it ran within the
  window's +10 s tail, so the monotonic clock had not reached the window end. Same boot (boot_id unchanged); the read-only
  checker was re-run 14 s later → PASS. The first output is kept as `md19_buf4/md19_first_run_exit6.json` (Pi copy).
  The clips were not re-run. md19_buf2 was checked 15 s after its last clip.
- Driver-held CMA: unknown (no CMA debugfs; rev 15).

## Per-cycle size at Wed settings (for the gate's --cycle-mb)
md19_buf4 clips: 64.8 / 59.2 / 55.3 MB allocated (`du -s -B1`). Gate `--cycle-mb 65` (the largest).

## Not done here
- The 25× gate (§6), the Wed card, PE condition 1's bench run of the exact program.

## Gate N1 boot dry runs (TEST_CARD_B §6 N1; 09:33–09:43 AM, n1_readings.txt)
Two cold boots through the UPS (`ups_power_cycle.sh --yes --tag pool2_n1`, wake 1 min, flag present, on the charger),
each back in ~2 min with both boards enumerated. Outside use (df used − results/):
59557588992 → 59557711872 → 59557765120 B = **+0.12 MB, +0.05 MB per boot (mean 0.09 MB) ≤ 6 MB → no mitigation
needed**. Journal 472.4 MB, unchanged. Idle after boot: CmaFree 189–215 MB, MemAvailable 276–282 MB at ~100 s uptime.
