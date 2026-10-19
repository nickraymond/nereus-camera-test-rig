# Test card B — pool 2 bench study on nereus002 + the 25× ready gate

Pre-registered before any bench clip (TEAM_RULES §9, §13). QE checks it before the first clip and again before the gate.
**Rev 2 (Sun 10/18, the successor session):** QE 005's FAIL on rig #121 @ a938f76 (R1, R2, R4–R8 + nits) is fixed on
the fix PR (branch `study/pool2-prep-qe-fixes` off main 9ff4ab5), and this card now describes that code. #121 merged
at the FAIL sha, so **rig main 9ff4ab5 is never deployed as is**: the bench build is the fix PR's QE PASS sha. QE 006's
FAIL on the fix PR @ 9ec4bfe (R1, R2 + nits) is fixed on the same PR.
**Rev 3 (Sun 10/18 8:08 PM, on rig #126):** the spec is rev 13 (W1–W5 binding), and part B gains one pre-registered
probe: the N6 in full colour (F15, §4 step 4).
**Rev 4 (Sun 10/18 9:53 PM PT by `date`, pool-2 prep successor 3):** must do 19 (spec rev 15, QE 008 PASS-with-nits N1–N3, Sun 8:57 PM)
is pre-registered as §4 step 4b, with its own bars (§5b) and stop rules (§8 rules 9–10). Its checker is
`scripts/md19_check.py`. No must do 19 clip exists yet.

| | |
|---|---|
| **Spec** | [SPEC.md](SPEC.md) rev 13 (sha256 `2e5525246709…`, QE 007 PASS Sun 10/18 8:06 PM; rev 12 replaced the conditional revs 10–11, rev 13 fixed it). Part B's bars are rev 9's, unchanged. Must do 19 (§4 step 4b, §5b): rev 15 (sha256 `d322d4bac58b…`, QE 008 PASS-with-nits N1–N3, Sun 10/18 8:57 PM) |
| **Binding nits** | QE 005 N1–N7 (rev 5), R1, N9, N10, N11 (rev 6); QE 005 FAIL @ a938f76: R1, R2, R4–R8 + nits (rev 2 of this card); QE 006 FAIL @ 9ec4bfe: R1, R2 + nits |
| **Decisions** | d52; d57 (Sat 3:26 PM: N6 resync fix in the build, usable-clip share, no pause bar; replaces d53); d55 (Sat 3:32 PM: sync bar N6 ↔ AE3 + IMX708 start bar; spec text in the next rev); d56 (Sat 3:32 PM: the rev 6 chroma rule approved, plus Nick's 4:4:4 vs 4:2:0 look) |
| **Unit** | nereus002: Pi Zero 2 W + IMX708 + OpenMV N6 `10003500025043364d343000` + OpenMV AE3 `0829c14000000000` |
| **Ownership** | The rig session ("nereus002: fix AE3 wedge, 25× start/stop gate, Friday dive") owns nereus002. This session works on it only under a new written hand-over to it, sequenced by PjM 004 and recorded on c_pool1016_video_review, after QE 006 PASSes the fix PR. The 10/17 slot (to Sun 12:00 PM) was declined by message on Sun morning, before 9:28 AM: nothing was touched |
| **Session** | "Pool test 2 prep (Wed 10/21) successor" (from Sun 10/18 9:09 AM; the first pool-2 prep session stopped at 9:03 AM, handoff HANDOFF_1.md). Fix PR on branch `study/pool2-prep-qe-fixes`; QE = QE 006 |

## 1. Question (Nick's words)

- "now I really want to see how and if we can do higher resolution on both cameras and maintain that 10fps"
- "I want to see the best video quality out of the N6 s AE3, they do not need to have the same settings"
- The ready gate: "do a cold boot … reboot, take the 30 second videos from all three cameras, and shut down and do that
  25x times in a row".

Spec items checked: must-dos 4–10, F1–F9, the pass/fail block, the rev 3 order, rev 4 G-a/G-b, rev 6 N6 and AE3 rules; rev 15 must do 19 (F19 is a Wed row, on the Wed card).

## 2. Build: pinned before the first clip, and again before the gate

**Now on the Pi** (rig session hand-over; last verified Fri 10/16 9:38 PM):
- `~/nereus-rig-poolcam` = main@56d49b5 (REVISION file; 308 host files match `git ls-tree 56d49b5`).
- Board scripts: 14/14 match `~/NereusData/nereus-camera-test-rig/2026-10-15_selfcheck/deploy_56d49b5/board_firmware_sha256_56d49b5.txt`.
- Firmware on both boards: 1.28.0 (MicroPython v1.28.0-64, 2026-09-05) = OpenMV v5.0.1. **No firmware update this round:** no
  OpenMV build has N6 H.264 (rev 6).

**Bench build** (deployed before the first clip; d57: the N6 resync fix is in from the start):
- Host: the fix PR's **QE PASS sha** (main 9ff4ab5 + the R1–R8 fixes; #121 already carries the resync fix aa06bc7). It
  is unpacked over `~/nereus-rig-poolcam` from `git archive <sha>`: files are overwritten or added, nothing is deleted.
  Beyond git, the Pi tree holds only caches and REVISION.
- Boards: `host_tools/deploy_openmv.py` to both boards. This is a shared board-script change:
  - `capture_service.py` and `command_protocol.py` gain the optional `subsampling` and `timing` stream settings. With
    neither set, the wire request is the same as Friday's.
  - `capture_service.py` accepts a board's custom (w, h) size, and `openmv/n6/board_config.py` offers `960x600` (the
    rev 6 fallback; QE 005 nit). Source, OpenMV v5.0.1 (= our 1.28.0): `sensor` is a shim over `csi`,
    `csi.framesize((w, h))` is a custom framesize, and the PAG7936 driver lets the N6's ISP scale it from HD
    (`OMV_CSI_HW_SCALE_ENABLE`). So 960×600 keeps HD's field of view. The AE3 has no ISP scaler and doesn't offer it.
    Untested on hardware until the N9 probe.
  - All of it is in the step-1 build, so `ctl_1` is N10's control run for it; no extra deploy is needed for 960×600.
- **Recorded at deploy, into the run folder's `BUILD.md`:**
  - the Pi REVISION;
  - the Pi-tree sha256 list vs `git ls-tree <sha>`;
  - sha256 of the 7 files on each board, read back with `scripts/board_hashes.py` (one mpremote connection, then
    reset);
  - `get_device_info` firmware for both boards.
- **Any later code deploy** (e.g. a fix found in B0): same records, and **N10:** both boards' 640×400 controls re-run
  before any comparison. Rows name the control they are compared against (`ctl_1`, `ctl_2`, …). Wed's build = the last
  one deployed before the gate.

**Backup and restore (§9).** The deployed state is fully defined by main@56d49b5 (308 files match `git ls-tree
56d49b5`; the Pi holds only caches and REVISION beyond git), so no Pi-side backup is needed. To restore, overwrite with
56d49b5 and remove the files the bench build added (`git diff --name-status 56d49b5 <bench sha>`, status `A`; the list
goes in `BUILD.md`):

```bash
git -C <rig checkout> archive 56d49b5 | ssh pi@nereus002 'tar -x -C ~/nereus-rig-poolcam'
ssh pi@nereus002 'cd ~/nereus-rig-poolcam && rm -f <the A files from BUILD.md> && echo "56d49b5 main (restored after pool2 bench)" > REVISION'
ssh pi@nereus002 'cd ~/nereus-rig-poolcam && PY=/home/pi/nereus-camera-test-rig/.venv/bin/python && $PY -m host_tools.deploy_openmv --board n6 --serial 10003500025043364d343000 && $PY -m host_tools.deploy_openmv --board ae3 --serial 0829c14000000000'
```

`/home/pi/nereus-camera-test-rig/.venv/bin/python` is the venv `pool_mode.sh` uses. Verify the restore:
- `scripts/board_hashes.py` reports all 14 board files equal to `board_firmware_sha256_56d49b5.txt`;
- `scripts/board_probe.py probe <serial>` is ok on both boards.

## 3. Conditions

- **Place:** in air, on the home bench (the rig on its charger, Fri 9:38 PM). Nobody is known to be at the bench, so
  nothing in the scene is moved.
- **Fixed busy scene** (PE 002 R-scene). Whatever the rig faces now, unchanged for every setting.
  - At the start: one IMX708 still and one N6 and one AE3 frame are saved as `scene/` in the run folder and described in
    RESULTS.
  - "Busy" is measured, not judged: the AE3's 640×400 q70 4:4:4 frames must average **≥ 24 kB** (Friday underwater:
    25.5–26.3 kB).
  - If the bench frames are lighter, every AE3 row is flagged "scene lighter than the pool". RESULTS then reports B0's
    JPEG ms against frame kB, so the pool rate can be estimated rather than assumed.
- **Light:** room light, not controlled. Each clip records the IMX708's `Lux` and exposure (`imx708.meta.json`).
  Clips are not compared across a big light change (e.g. day vs lamp); the controls re-run instead.
- **Power:** on the charger (UPS VIN > 4.5 V), or VBAT ≥ 3.4 V. Read before each setting.
- **Held fixed (Friday's settings):**
  - IMX708: 1920×1080 H.264 at 30 fps, lens position 0.8, 4 buffers. Out of scope to change (spec).
  - Boards: MJPEG q70 at 10 fps.
  - Clips: 30 s, synced by `scripts/sync_video.py` (one fire, Pi monotonic clock).
- **One variable at a time:** each setting changes one board's framesize or chroma format. The other board stays at its
  control.
- **Network:** clips are pulled only between settings, never while a board is recording (the N6 drops USB bytes when the
  Pi's reader stalls; both boards link at 480M, `lsusb -t` Sat 3:25 PM).

## 4. Steps, in order

0. **Access and guards** (before anything else):
   - `touch ~/.no_dive_autostart` (plain, no marker; it stays present at hand-back, as the owner asked).
   - Check for recorders: `pgrep -af '[c]i-record|[r]picam|[s]ync_video|[p]ool_capture|[g]ate_cycle'` must be empty.
     If ci-record is running, stop and ask the rig session; I don't stop Nick's services.
   - Record: `lsusb -t` and each board's link speed (rev 6: before any HD clip), `df -B1`, `vcgencmd get_throttled`,
     UPS VIN/VBAT, uptime, boot id, the quirk in `/proc/cmdline`.
   - Done Sat 3:25 PM: flag created; no recorder running; both boards on cdc_acm at 480M; SD 33.1 GB free; throttled
     0x0; quirk present; REVISION main@56d49b5. CmaFree was 42 MB at idle: page cache borrows CMA, so the report-only
     RAM flag needs this baseline.
   - Save the scene stills.
1. **Deploy the bench build** (§2: the fix PR's QE 006 PASS sha, never main 9ff4ab5 as is) and record it.
2. **B0, AE3 chroma** (by Sun night; rev 6), with `--timing` on both boards:
   - `ctl_1`: both boards at 640×400 q70 in the firmware's own chroma (N6 4:2:0, AE3 4:4:4), n = 3. These are N10's
     controls on the bench build, and also the AE3 444 arm.
   - `ae3_420`: AE3 640×400 q70 `--ae3-subsampling 420`, N6 at control, n = 3.
   - **Output:**
     - fps, frames lost and median snapshot / JPEG / USB-write ms per arm;
     - a crop pair of the AE3 in 4:4:4 vs 4:2:0: the same box at 3×, nearest neighbour, from the frame nearest 15 s after
       the fire in one clip of each arm (`scripts/chroma_pair.py <ctl_1 clipNN> <ae3_420 clipNN> --camera openmv_ae3
       --t 15 --crop X,Y,W,H --zoom 3 --out …`). The box is chosen on the scene stills before the crops are made, on the
       busiest detail. Both frames have the same size, so the box has the same footprint; the tool refuses different
       sizes;
     - each arm's chroma format as measured from the JPEG SOF headers (`subsampling_measured`, video_clip_table), not
       only the asked value;
     - the format the rev 6 rule picks.
   - **R1 / d56:** the B0 result goes into d56's `evidence` field only (pinned update, `if_version`). Nick approved
     the rule on Sat 3:32 PM: "AE3 can run with reduced color like the N6". The Wed card names the format that ran.
   - **d56's added test** (Nick: "record same scenery with full and half color for the AE3 so I can see for myself
     the impact … if this can be done in air thats also ok"):
     - B0's two arms already record the same fixed bench scene in 4:4:4 and 4:2:0, back to back. The scene is static
       and the light constant; each clip's IMX708 Lux is checked to confirm it.
     - Rev 9 deliverable, in air:
       - B0's pair as QuickTime-playable viewing copies, plus 100 % crops of the same region (`chroma_pair.py … --zoom
         1`);
       - under `~/NereusData/nereus-camera-test-rig/<date>_ae3_chroma/` with a MANIFEST;
       - the viewing copies are labelled "re-encoded for viewing", and the original clips stay untouched (rev 9);
       - its path goes in d56's `evidence`;
       - the re-encode runs only under the PjM's heavy-compute all-clear (PjM 004 from Sun 10/18).
     - A slider page of the crops is optional. An underwater pair is optional on the Wed card (part C).
   - **E1, exploratory, not scored:** one N6 HD clip, to see the link limit early for the Mon plan. Label
     `explore_n6_hd`, never counted toward any n: `pool2_bench.py explore_n6_hd --under openmv_n6 --probe 1280x800 --
     --n6-framesize HD --timing`. A probe prints PROBE PASS/FAIL, never HOLDS.
   - **Resync on real streams (QE 005 nit):** the resync fix is untested at HD and at 4:2:0 on real streams. On the first
     healthy 4:2:0 clip (`ae3_420`) and the first healthy HD clip (E1 or `n6_hd`): `short_frames` = 0, `bad_frames` = 0,
     and the measured size and chroma format equal the asked ones. Otherwise that setting stops and the PE + PjM are told.
3. **Controls at the chosen AE3 format** (Mon AM, after d56 or the rule):
   - If the format is 4:2:0, `ae3_420` is its control.
   - The AE3's own 640×400 rate for the relative bar comes from its format's control.
   - Any code deploy after step 1 re-runs both controls (`ctl_2`, …; N10).
4. **Resolution steps** (Mon → Tue noon, final build). Each setting changes one board; the other stays at its control.
   - **N6, prediction pre-registered here (rev 6), corrected by step 0 before any HD clip:**
     - HD 1280×800 MJPEG q70 needs ~0.97 MB/s (97 kB/frame, 10/2 bench).
     - Rev 6 assumed a full-speed (12 Mbit/s) N6 link, from the board's USB product name "Virtual Comm Port in FS
       Mode" (RESULTS 10/14). `lsusb -t` on Sat 10/17 3:25 PM shows **both boards, the N6 included, at 480M
       (high-speed)** on the hub. The "FS Mode" text is a descriptor string, not the link speed.
     - Prediction as registered now: ~0.97 MB/s fits the link, so **HD holds 10 fps.** The risk is byte loss when the
       Pi's reader stalls (the 10/14 mechanism: MicroPython's CDC send timeout, seen at 0.4–0.8 MB/s), more likely at
       ~3.4× Friday's data rate. With the resync fix a loss costs one frame; the usable-share bar decides it (rev 7).
   - `n6_hd`: N6 1280×800 q70, n = 3.
   - **Only if `n6_hd` fails:**
     - `n6_960_probe`: one clip at 960×600 q70 (`--under openmv_n6 --probe 960x600 -- --n6-framesize 960x600`, plus
       the AE3 format chosen in B0). Spec rev 6's "frames decode at 960×600 and the stream runs 30 s" is read as:
       **decode** = every N6 frame fully decoded by Pillow at 960×600 (plus SOF size and 0 bad frames); **runs 30 s**
       = its last frame ≥ 29.5 s after the fire. **N9:** the probe isn't one of the n = 3, and never prints HOLDS. A
       probe whose clips were all void resumes under its label.
     - If the probe passes: `n6_960`, n = 3. If it fails, the N6's answer is 640×400 and the limit is stated.
   - **AE3:**
     - `ae3_hd`: AE3 1280×800 q70 in the B0 format, run as fail-fast clip 1. The software JPEG did ~2.3 fps at HD on the
       S3 bench, so this is expected to fail. It is still run, because must-do 8 asks for every supported step and
       rev 3 rules out "HD at 7 fps".
     - QVGA is below the control and isn't run.
     - **Rev 9 (d56):** if the ladder runs 4:4:4 and its first rung above 640×400 fails, that rung re-runs in 4:2:0
       (n = 3). The ladder continues in 4:2:0 if it holds.
   - **N6 full colour, `n6_444_probe` (spec rev 12–13 F15; before Tue noon, after the N6's answer is known):** one 30 s
     clip at the N6's Wed size with `--n6-subsampling 444`, the AE3 at its control:
     `pool2_bench.py n6_444_probe --under openmv_n6 --probe <W>x<H> --probe-min-fps 5 -- --n6-framesize <its name>
     --n6-subsampling 444 --timing` (`--probe-min-fps`: rig #126).
     - PROBE PASS = every N6 frame decodes at that size, its measured chroma is 4:4:4, and it delivers ≥ 5 fps over the
       30 s (`fps_clip`), with the stream running the whole clip. Its frame kB and JPEG ms are reported.
     - It is a probe: never one of any n, never a "holds". If it fails (the hardware encoder may be 4:2:0 only), W4 runs the
       AE3 only, the N6 records half colour on both W passes, and the Wed card says so.
4b. **Must do 19, CMA headroom (spec rev 15; after the ladder decides Wed's modes, before the Tue 3 PM card, before
   the gate).** Build: rig main as deployed (the Pi's REVISION). Settings: Wed's exact program, with the N6 and AE3 at
   their part-B "holds" settings (the N6-vs-AE3 clips: the largest board frames of the program). The W passes' chroma
   changes only board-side encoding, not the Pi's buffers.
   - (a) `python scripts/pool2_bench.py md19_buf4 --under openmv_n6 -- <Wed board args> --imx-buffers 4 --timing`, then
     at once `python scripts/md19_check.py md19_buf4`;
   - (b) the same with label `md19_buf2` and `--imx-buffers 2`.
   - Order: (a) first, then (b) (rev 15). n = 3 clips of 30 s each.
   - Driver-held CMA is **unknown** on nereus002. Kernel 6.18.34+rpt-rpi-v8 has `CONFIG_CMA_DEBUGFS` and
     `CONFIG_CMA_SYSFS` not set (rig session, read-only, Sun 10/18). The card records "unknown", and the CMA bar is
     judged on kernel-log errors only (rev 15).
   - QE 008 N1: the 10/13 reset reading is **not in the logs** (rig session: the journal rotated out). The only datum
     is CmaFree 16 kB ~3 min after the 3:02 PM reset, under ci-record. So no 10/13-based bar applies; the bar stays
     ≤ 90 % of the pool, which can't be read here.
   - pool2_bench's own fail-fast still applies. A label that stops short of 3 clips for a reason outside §5b (e.g. a
     skew miss) is re-run once under a new label (`md19_buf4_r2`); a second short label → stop (§8 rule 10).
5. **The 25× ready gate** (Tue afternoon, last, on Wed's exact build and settings; §6).

**Fail fast (§9):** a setting stops at its first clip that misses a bar, because one miss fails the setting. A
throttled clip is void: cool down ≥ 5 min and re-run it; 2 throttled in a row → stop (§8).

**One command per setting:** `python scripts/pool2_bench.py <label> --under <openmv_n6|openmv_ae3> [--ae3-floor X] --
<sync_video args>`. Its own options come before `--`; everything after goes to `sync_video.py` unchanged (QE 005 R1).
- It runs every per-clip pre-check (§4 step 0 and §8): the flag is present; no gate (`~/.gate_armed.json`) or pool mode
  (`~/.pool_armed`) is armed; the process check ran (`ps` gave output) and found no recorder; SD ≥ 2 GB; VBAT;
  `vcgencmd get_throttled` = 0x0 now (sticky bits from earlier in the boot would void every clip until a reboot, so
  the bench refuses and the Pi is cold-booted through the UPS first, flag present).
- Then the clip, the §5 bars, fail fast and the throttled-void rule. It writes `results/pool2_bench/<label>/clipNN/`
  and `setting.json`, rewritten after every clip.
- **The verdict is over every clip the label ever had (QE 005 R4, QE 006 R1).** A label whose clips already decide it (a
  miss, or n passing clips) gets no new clip. A clip that `setting.json` lists stays in the verdict even if its folder
  is deleted (`folder_missing`), and its number is never reused. A clip folder with no record (a crash mid-clip) is
  scored from its files, and a missing `video_sync.json` is a miss. A label interrupted by a pre-check resumes on the
  next run. The same label with other arguments is refused, and so is a label started on another build (the Pi's
  REVISION): a new build gets new labels (`ctl_2`, …), reported next to the old ones.
- `--seconds`, `--out` and `--cameras` after `--` are refused: the bench sets them (QE 006 nit).
- Exit: 0 holds (probe: PROBE PASS), 1 fails (probe: PROBE FAIL), 2 usage error, 3 a pre-check refused or the label
  can't be resumed, 4 stopped after 2 throttled clips in a row (§8 rule 3: the bench stops).
- Probes use `--probe WxH` (one clip, PROBE PASS/FAIL). Examples, verbatim (each has a unit test):
  - `ctl_1`: `--under openmv_ae3 -- --timing`
  - `ae3_420`: `--under openmv_ae3 -- --ae3-subsampling 420 --timing`
  - `n6_hd`: `--under openmv_n6 -- --n6-framesize HD` (plus the AE3 format chosen in B0)

## 5. Bars per setting (set now; the spec's, unchanged)

**AE3 settings** hold when all 3 clips meet every bar below. **N6 settings** (spec rev 7 d57 + rev 8 R3) hold when:
- ≥ 2 of 3 clips are **usable**: ≥ 285 frames, i.e. 95 % of the expected 300. Expected = asked fps × clip seconds =
  10 × 30, fixed now. It is not the seq span, which a dying stream would shorten.
- the usable clips meet the fps, skew, IMX708 and host bars;
- an unusable clip counts only in the usable share. Each N6 setting reports the share and the lost frames per clip.
- A wedge or ladder reset on either board fails any setting. A throttled clip is void.

| Bar | Value | Source |
|---|---|---|
| fps (the board under test) | delivered ≥ 9.5 fps over 30 s = `fps_clip`: frames captured in [fire, fire + 30 s] ÷ 30 (QE 005 R2), so a stream that stops early fails. `fps` (first → last frame) is reported only. AE3, if B0 fails: ≥ its own control 640×400 `fps_clip` − 0.2 fps (relative bar) | spec pass/fail; rev 6 |
| frames | AE3: 0 lost; an errored stream or a missing board frame count counts as lost. N6: the usable share above (rev 7, d57) | spec; rev 7–8 |
| format | the board's frames are what was asked, from their JPEG headers: measured size = the asked framesize (QVGA 320×200, VGA 640×400, HD 1280×800, `960x600` literal), measured chroma = the asked `subsampling` when one was asked (`auto` = the firmware's choice, recorded only), and 0 bad frames. A miss for the board under test; flagged (`cross_effect`) for the other board | QE 006 nit |
| wedges | 0 wedges and 0 ladder USB resets on either board during the setting's clips. The normal per-clip `reset_board` (3.4–5.5 s) doesn't count | F2; rev 8 |
| sync (d55, rev 9) | N6 ↔ AE3 start skew ≤ 25 ms per clip (\|N6 − AE3\| `start_offset_ms`). The IMX708's first frame ≤ 34 ms after the trigger is its **own verdict line**, reported per setting next to its real frame period (1000 / delivered fps, ~38 ms at 26.4 fps) and not part of "holds"; some misses are expected and decide nothing. `start_skew_ms` over all three is still recorded. Applies once rev 9 passes QE (before any data); until then rev 8's 25 ms over all three applies | spec; d55 |
| IMX708 | `fps_clip` ≥ 26.0 and max gap ≤ 100 ms, and rpicam-vid `ok` with rc 0 (QE 005 R2) | spec (F8) |
| host | `throttled` = 0x0 in every host_stats.csv sample over the clip (spec rev 9), sticky "has occurred" bits included; they also catch a transient between the 1 Hz samples. A throttled clip is void (§4); the next pre-check then refuses until a reboot. No reading is a miss | spec; QE 006 R2 |

Also recorded per clip, not bars:
- MB per 30 s, frame kB, median timing ms (B0), first-frame delays, `lsusb` speeds;
- min mem_avail, min CMA free, max CPU °C, VBAT/VIN;
- the IMX708's Lux, and each board's chroma format.

**No pause bar and no USB root-cause work** (d57 replaces d53). The N6 resync fix (aa06bc7, rig #124) is in the build
from step 1: a byte loss costs one frame, not the rest of the clip. Its `short_frames` count is recorded per clip.

**Answer per camera (must-do 10):** the highest setting that holds, where every lower step also holds. Wed runs the
N6's highest setting that holds (d57).
- N6 "best": HD MJPEG if it holds, else 960×600 if it holds, else 640×400 (rev 6).
- AE3 "best": the highest resolution that holds in the B0 format (rev 3, rev 6).

## 5b. Must do 19 bars (spec rev 15, set now; checker `scripts/md19_check.py`)

| Bar (every one of the label's 3 non-void clips) | Value |
|---|---|
| CMA | 0 CMA / dma-buf allocation errors: no kernel line (`journalctl -k -b -o short-monotonic`) inside [first fire − 5 s, last fire + 30 s + 10 s] that names CMA / DMA / dma-buf / memory **and** a failure. Every CMA / DMA line in the window is listed verbatim in `md19.json`. Driver-held CMA peak ≤ 90 % of the pool: **unknown** (no debugfs) |
| capture | every board 0 `frames_lost` and `fps_clip` ≥ 9.5; IMX708 `fps_clip` ≥ 26.0 and max gap ≤ 100 ms (its `frames_lost` counts frames under the 30 fps nominal, which rev 9's 26 fps allows); no host reset (this boot's clock is past every clip and its journal starts before the first) |
| report only | min MemAvailable per clip (rev 4 flag < 150 MB), min CmaFree, the kernel CMA / DMA lines |

**Open reading (asked PE 005 by message, Sun 10/18, before 9:53 PM PT):** B0's `ae3_420` clip03 lost one N6 frame (`short_frames` 1, the d57 resync
fix). "0 lost frames" read literally fails that, and the card's STOP follows. The other reading is each camera's own
part-B frame bar (N6 ≥ 285 / 300 usable). The checker implements the literal reading until the PE pins one.

**What happens** (rev 15, by the bar that failed; `md19_check.py` exit 0 pass, 1 capture fails, 5 only CMA fails,
6 can't judge):
- Both bars pass at (a) → Wed uses `--imx-buffers 4`; else both pass at (b) → `--imx-buffers 2`.
- Only the CMA bar fails at both → the CMA pool raise, **only with Nick's own words** (root, Nick's sudo; NEEDED on
  c_pool1016_video_review). `/boot/firmware/config.txt` line 26 `dtoverlay=vc4-kms-v3d` → `dtoverlay=vc4-kms-v3d,cma-320`.
  - Backup first: `sudo cp -p /boot/firmware/config.txt /boot/firmware/config.txt.bak-20261019`. Restore:
    `sudo cp -p /boot/firmware/config.txt.bak-20261019 /boot/firmware/config.txt`.
  - One cold boot (flag present). Then read back CmaTotal = 327680 kB (QE 008 N3) and re-run (a), then (b), as
    `md19_buf4_cma320` / `md19_buf2_cma320`, recording MemAvailable and swap too (rig session: mem_avail is already
    124–137 MB in clips).
  - The raise must land by the Tue 6 PM freeze and be announced on the board (§10). The overlay's own help lists cma-320
    as "needs 1GB"; this Pi has 512 MB.
- The capture bar fails, or the CMA bar still fails after the raise → **stop** (§8 rule 9).

## 6. The 25× ready gate (spec rev 3 + rev 4 G-a/G-b + rev 5 QE N1–N7)

**Harness:** `scripts/gate_cycle.py` (study build). No hand-rolled reboot loops. The arm line, with Wed's settings from
§5's answer:

```bash
python scripts/gate_cycle.py arm --video-only --video-s 30 --until-streak 25 --target 60 --label pool2 --cycle-mb <MB per cycle measured at Wed's settings> --video-args="<Wed's per-board sync_video args>"
```

`--video-args` needs the `=`: argparse reads a value that starts with `--` as an option (QE 005 nit; unit-tested).

- **The cycle** (cold boot through the UPS, `ups_power_cycle.sh` via the gate):
  1. boot;
  2. board first contact (with the recovery ladder);
  3. one synced 30 s clip on all 3 cameras at Wed's settings;
  4. a 30 s wait, then the checks;
  5. clean shutdown.

  There are no stills, so a dark scene doesn't void a cycle and the gate can run in any light.
- **G-a, 25 in a row** (a failed cycle resets the count). A cycle passes when all of these hold:
  - it booted and ran;
  - every camera recorded: no arm/fire error, and ≥ 1 frame in its file;
  - 0 recorder processes 30 s after `sync_video.py` returns. That covers ci-record, rpicam-*, libcamera-*,
    `sync_video.py`, `pool_capture.py`, `capture_raw_*`, `capture_dark.py`, `hil_soak.py`, `pool_selfcheck.py`,
    `selfcheck_*`, `pool2_bench.py` and `nereus-rig` / `nereus_camera_test_rig.cli`. They're matched by the program a
    process runs (argv[0], or the script after python / bash / env / timeout), never by another argv token, so `vim
    sync_video.py` doesn't count. The gate's own PID and its ancestors are excluded (**N5**). If `ps` gives no output,
    nothing was checked and the cycle fails (`procs:not_checked`, QE 005 R7);
  - the previous shutdown was clean;
  - the boards really lost power.
- **Video completeness, scored per cycle and never resetting the count (d52):**
  - per camera: frames, fraction of nominal, frames lost, fps, first-frame delay, and "complete" (recorded, no error,
    0 lost);
  - per clip: start skew.

  RESULTS reports how many of the cycles' clips were complete, per camera.
- **G-b, SD clean** (its own verdict line; both lines must pass):
  - **Readings** with `df -B1` on the results filesystem: at gate start (`arm`), after each boot before recording, and
    30 s after each clip.
    - Run folder size: `du -s -B1` (allocated, **N3**).
    - Outside use = used − run folder.
    - **N1:** each reading also records `journalctl --disk-usage` and `du -s -B1` of `/var/log`, `/var/lib/apt`,
      `/var/cache/apt` and `~` (excluding `results/`).
  - **Growth (N2):** post-clip(k) − post-clip(k−1); cycle 1 is measured from gate start.
  - **Bars:**
    - ≤ 100 MB growth per cycle;
    - the last reading ≤ 250 MB above the gate start;
    - SD free ≥ 2 GB at every reading;
    - before arming, SD free ≥ 2 GB + `--target` (60) × the per-cycle MB (the arm refuses otherwise). That is stricter
      than rev 4's 25×, on purpose: the gate may run up to 60 cycles, and every clip stays on the Pi until the pull
      (N6);
    - **stop (F9, §8 rule 4; QE 005 R6):** auto mode stops power-cycling after any cycle with a reading of SD free
      < 2 GB, or no reading, and writes the run's STOP file. A boot whose own reading is < 2 GB (or unread) records no
      clip (that cycle fails G-a), so a later hard reset or switch boot can't add clips. The rig stays up for the pull
      and clear.
    - MB = 10⁶ B.
  - **N1 before arming:** the part-B cold boots (the gate's dry runs) measure outside-use growth per boot. If it averages
    more than 6 MB per boot, it is mitigated first (e.g. a journald size cap) as a reversible rig change under the
    owner's hand-over, before order step 3. **The 100/250 MB bars are never loosened;** that would go to Nick.
- **RAM:** report only.
  - Each cycle reads mem_avail and CMA free after boot (the idle baseline) and 30 s after its clip, each before that
    reading's slow `du` scans.
  - Flagged: CMA free more than 20 MB below that boot's idle reading (rev 7: idle CMA is ~42 MB because the page cache
    borrows CMA), or mem_avail < 150 MB.
  - RESULTS shows the idle baseline next to each reading.
- **Clip folders (QE 005 R8):** each cycle attempt records into `video/cycleNNN_<boot id[:8]>/` (plus `_2` if it
  exists). A hard reset mid-cycle re-runs cycle N on the next boot, and the failed attempt's clips stay as evidence.
- **After the gate (N4, N6), in order:**
  1. `gate_cycle.py disarm`.
  2. `gate_cycle.py seal <run>`: first `gb_verdict.json` (G-b bars 2–4 over every cycle), then one SHA256SUMS over the
     whole run folder: `video/cycleNNN_*`, `cycles.csv/jsonl`, `gate.log`, `gb_verdict.json`, nested files included.
  3. Pull it to `~/NereusData/nereus-camera-test-rig/2026-10-20_gate25/`.
  4. On the Mac: `shasum -a 256 -c SHA256SUMS`, all OK.
  5. Only then clear the run folder from the Pi.
  6. `gate_cycle.py sd <run> --wed-mb <the Wed card's planned SD MB>` (QE 005 R5). Rev 4 item 5: SD free within 250 MB
     of the gate-start reading, ≥ 2 GB, and ≥ 2× Wed's planned SD use. Exit 0 only when all three hold and the run
     folder is gone from the Pi. The gate-start reading comes from `~/.gate_sd_start_<run>.json`, written at arm, so it
     survives the clear. If Nick says yes to PE 003's rev 10–11 (W1–W3, ~0.6 GB), the Wed MB includes those clips.
     `seal` skips and counts a torn last line of `cycles.jsonl` (a hard reset mid-write).

  Gate clips stay on the Pi until that pull; there is no per-cycle clearing (**N6**). Nothing is cleared that isn't
  verified on the Mac first.
- **Timing:** ~200 s per cycle → a clean 25 ≈ 1.4 h; `--target 60` caps a long tail at ~3.3 h. The gate runs on the
  final build after the resolution steps, before the Tue 10/20 6 PM freeze. Any change after the gate means the gate
  re-runs (rev 3 order 4).
- **"Not ready" rule** (carried from rev 3, QE nit): a gate that has failed or isn't finished at Tue 10/20 6 PM is
  reported as **"not ready"** in the PjM's envelope (c_envelope_1021) for Nick's go/no-go. It is never silently
  re-tried under the freeze.

## 7. n, and why

- n = 3 clips of 30 s per setting (spec). One miss fails a setting, so the run stops at the first miss.
- Never recommend from n = 1: the probes (`explore`, `n6_960_probe`) inform the plan only.
- The gate's n is 25 cycles in a row (Nick).

## 8. Stop rules (from the proposal and spec; any one stops the bench, then the card, the PE (PE 003; PE 002 stood down Sun) and PjM 004 are told)

1. 3 AE3 cold-boot wedges in one day → stop the AE3 and report.
2. VBAT < 3.4 V off the charger.
3. 2 throttled clips in a row.
4. Pi SD < 2 GB free, or the Mac data root < 8 GiB free (`df -g ~/NereusData`).
5. Anything that would need hands at the bench while Nick travels (a board off the bus after the ladder, a UPS that
   doesn't wake): stop and report. No improvising. There is no firmware change this round, so there is no firmware
   recovery.
6. Any rig change after Tue 10/20 6 PM (freeze, §10).
7. The hand-over slot ends: hand back by message with the flag present; resume only under a new written hand-over
   (sequenced by PjM 004).
8. ci-record or another recorder is found running before a setting: stop and ask the owner.
9. Must do 19: the capture bar fails, or the CMA bar still fails after the raise. The data goes to the PE and Nick on the
   card; any change to E1 (N6 at HD) is Nick's call (spec rev 15).
10. Must do 19: a second label stops short of 3 clips for a reason outside §5b.

## 9. Cost

| Item | Estimate |
|---|---|
| B0 | ~1.5 h on nereus002 |
| Final build + controls | ~1 h |
| Resolution steps | ~2 h |
| Gate | ~1.5–3.5 h |
| Disk | ≤ 5 GB on the Mac data root under `~/NereusData/nereus-camera-test-rig/2026-10-17_pool2_prep/bench/` (each setting pulled with SHA256SUMS); Pi SD stop at < 2 GB |
| Displaces | nothing else on nereus002 in the slot (d41 is after Sun noon) |
| Nick | ~15 min Tue (Wed card + envelope); d55, d56 on the board |

## 10. Worth it?

It decides Wed's per-camera settings for the E6 shoot-out (sensor choice in Jan) on measured numbers rather than
guesses. The gate proves the rig boots and records reliably before a field day.

## 11. Exit

- Per camera: the winner and the table, by Tue noon.
- If either board doesn't hold above 640×400, Wed runs it at 640×400 with the limit named (USB link, encoder, pacing)
  and an option list for Nick.
- **N11:** N6 H.264 is a future option once openmv#3247 lands in an OpenMV release.
- RESULTS goes to `docs/pool2_20261021/RESULTS_B.md` on the bench-results PR; QE checks it; the PjM merges.
