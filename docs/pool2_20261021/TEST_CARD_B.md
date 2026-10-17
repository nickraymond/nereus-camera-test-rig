# Test card B — pool 2 bench study on nereus002 + the 25× ready gate

Pre-registered before any bench clip (TEAM_RULES §9, §13). QE checks it before the first clip and again before the gate.

| | |
|---|---|
| **Spec** | [SPEC.md](SPEC.md) rev 8 (sha256 `e5203f3fa492…`, QE 005 PASS Sat 3:32 PM). Rev 9 (`aacf8d28116a…`: d55, d56) is in QE's re-gate; it is committed byte-identical once it passes |
| **Binding nits** | QE 005 N1–N7 (rev 5), R1, N9, N10, N11 (rev 6) |
| **Decisions** | d52; d57 (Sat 3:26 PM: N6 resync fix in the build, usable-clip share, no pause bar; replaces d53); d55 (Sat 3:32 PM: sync bar N6 ↔ AE3 + IMX708 start bar; spec text in the next rev); d56 (Sat 3:32 PM: the rev 6 chroma rule approved, plus Nick's 4:4:4 vs 4:2:0 look) |
| **Unit** | nereus002: Pi Zero 2 W + IMX708 + OpenMV N6 `10003500025043364d343000` + OpenMV AE3 `0829c14000000000` |
| **Ownership** | Held by the pool-2 prep session under the nereus002 rig session's written hand-over (Sat ~3:12 PM PT, slot ends Sun 10/18 12:00 PM). Later slots (Mon–Tue) only by PjM 003's sequencing and a new written hand-over |
| **Session** | pool-2 prep, rig PR #121, branch `study/pool2-prep` |

## 1. Question (Nick's words)

- "now I really want to see how and if we can do higher resolution on both cameras and maintain that 10fps"
- "I want to see the best video quality out of the N6 s AE3, they do not need to have the same settings"
- The ready gate: "do a cold boot … reboot, take the 30 second videos from all three cameras, and shut down and do that
  25x times in a row".

Spec items checked: must-dos 4–10, F1–F9, the pass/fail block, the rev 3 order, rev 4 G-a/G-b, rev 6 N6 and AE3 rules.

## 2. Build: pinned before the first clip, and again before the gate

**Now on the Pi** (rig session hand-over; last verified Fri 10/16 9:38 PM):
- `~/nereus-rig-poolcam` = main@56d49b5 (REVISION file; 308 host files match `git ls-tree 56d49b5`).
- Board scripts: 14/14 match `~/NereusData/nereus-camera-test-rig/2026-10-15_selfcheck/deploy_56d49b5/board_firmware_sha256_56d49b5.txt`.
- Firmware on both boards: 1.28.0 (MicroPython v1.28.0-64, 2026-09-05) = OpenMV v5.0.1. **No firmware update this round:** no
  OpenMV build has N6 H.264 (rev 6).

**Bench build** (deployed before the first clip; d57: the N6 resync fix is in from the start):
- Host: `study/pool2-prep @ <sha>`, merged with the resync fix's branch (aa06bc7, the rig session's own PR). It is unpacked
  over `~/nereus-rig-poolcam` from `git archive <sha>`: files are overwritten or added, nothing is deleted. Beyond git,
  the Pi tree holds only caches and REVISION.
- Boards: `host_tools/deploy_openmv.py` to both boards. This is a shared board-script change: `capture_service.py` and
  `command_protocol.py` gain the optional `subsampling` and `timing` stream settings. With neither set, the wire request
  is the same as Friday's.
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
- **Network:** clips are pulled only between settings, never while a board is recording (the N6 is full-speed USB and
  drops bytes when the Pi stalls).

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
1. **Deploy the bench build** (§2: study branch + the resync fix merged, QE PASS on that push first) and record it.
2. **B0, AE3 chroma** (by Sun night; rev 6), with `--timing` on both boards:
   - `ctl_1`: both boards at 640×400 q70 in the firmware's own chroma (N6 4:2:0, AE3 4:4:4), n = 3. These are N10's
     controls on the bench build, and also the AE3 444 arm.
   - `ae3_420`: AE3 640×400 q70 `--ae3-subsampling 420`, N6 at control, n = 3.
   - **Output:**
     - fps, frames lost and median snapshot / JPEG / USB-write ms per arm;
     - a matched-frame crop pair of the AE3 in 4:4:4 vs 4:2:0, the same card region at 3× (`scripts/matched_frames.py`
       on one clip of each);
     - the format the rev 6 rule picks.
   - **R1 / d56:** the B0 result goes into d56's `evidence` field only (pinned update, `if_version`). Nick approved
     the rule on Sat 3:32 PM: "AE3 can run with reduced color like the N6". The Wed card names the format that ran.
   - **d56's added test** (Nick: "record same scenery with full and half color for the AE3 so I can see for myself
     the impact … if this can be done in air thats also ok"):
     - B0's two arms already record the same fixed bench scene in 4:4:4 and 4:2:0, back to back. The scene is static
       and the light constant; each clip's IMX708 Lux is checked to confirm it.
     - Rev 9 deliverable, in air:
       - B0's pair as QuickTime-playable viewing copies, plus 100 % crops of the same region;
       - under `~/NereusData/nereus-camera-test-rig/<date>_ae3_chroma/` with a MANIFEST;
       - its path goes in d56's `evidence`;
       - the re-encode runs only under PjM 003's compute all-clear.
     - A slider page of the crops is optional. An underwater pair is optional on the Wed card (part C).
   - **E1, exploratory, not scored:** one N6 HD clip, to see the link limit early for the Mon plan. Labelled `explore`
     and never counted toward any n.
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
     - `n6_960_probe`: one clip at 960×600 q70. It passes if its frames decode at 960×600 and the stream runs 30 s.
       **N9:** the probe isn't one of the n = 3.
     - If the probe passes: `n6_960`, n = 3. If it fails, the N6's answer is 640×400 and the limit is stated.
   - **AE3:**
     - `ae3_hd`: AE3 1280×800 q70 in the B0 format, run as fail-fast clip 1. The software JPEG did ~2.3 fps at HD on the
       S3 bench, so this is expected to fail. It is still run, because must-do 8 asks for every supported step and
       rev 3 rules out "HD at 7 fps".
     - QVGA is below the control and isn't run.
     - **Rev 9 (d56):** if the ladder runs 4:4:4 and its first rung above 640×400 fails, that rung re-runs in 4:2:0
       (n = 3). The ladder continues in 4:2:0 if it holds.
5. **The 25× ready gate** (Tue afternoon, last, on Wed's exact build and settings; §6).

**Fail fast (§9):** a setting stops at its first clip that misses a bar, because one miss fails the setting. A
throttled clip is void: cool down ≥ 5 min and re-run it; 2 throttled in a row → stop (§8).

**One command per setting:** `python scripts/pool2_bench.py <label> --under <openmv_n6|openmv_ae3> [--ae3-floor X] --
<sync_video args>`. It runs every per-clip pre-check (§4 step 0 and §8: flag present, no recorder, SD ≥ 2 GB, VBAT),
the clip, the §5 bars, fail fast and the throttled-void rule. It writes `results/pool2_bench/<label>/` and
`setting.json`. Probes use `--n 1 --no-fail-fast`. Examples:
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
| fps (the board under test) | delivered ≥ 9.5 fps over 30 s. AE3, if B0 fails: ≥ its own control 640×400 rate − 0.2 fps (relative bar) | spec pass/fail; rev 6 |
| frames | AE3: 0 lost; an errored stream or a missing board frame count counts as lost. N6: the usable share above (rev 7, d57) | spec; rev 7–8 |
| wedges | 0 wedges and 0 ladder USB resets on either board during the setting's clips. The normal per-clip `reset_board` (3.4–5.5 s) doesn't count | F2; rev 8 |
| sync (d55, rev 9) | N6 ↔ AE3 start skew ≤ 25 ms per clip (\|N6 − AE3\| `start_offset_ms`). The IMX708's first frame ≤ 34 ms after the trigger is its **own verdict line**, reported per setting and not part of "holds". `start_skew_ms` over all three is still recorded. Applies once rev 9 passes QE (before any data); until then rev 8's 25 ms over all three applies | spec; d55 |
| IMX708 | ≥ 26.0 fps and max gap ≤ 100 ms | spec (F8) |
| host | `throttled` = 0x0 over the clip (host_stats.csv); a throttled clip is void (§4) | spec |

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

## 6. The 25× ready gate (spec rev 3 + rev 4 G-a/G-b + rev 5 QE N1–N7)

**Harness:** `scripts/gate_cycle.py` (study build). No hand-rolled reboot loops. The arm line, with Wed's settings from
§5's answer:

```bash
python scripts/gate_cycle.py arm --video-only --video-s 30 --until-streak 25 --target 60 --label pool2 --cycle-mb <MB per cycle measured at Wed's settings> --video-args "<Wed's per-board sync_video args>"
```

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
  - 0 recorder processes 30 s after `sync_video.py` returns. That covers ci-record, rpicam-*, `sync_video.py`,
    `pool_capture.py`, `capture_raw_*` and `nereus-rig`, matched by argv basename, excluding the gate's own PID and its
    ancestors (**N5**);
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
    - before arming, SD free ≥ 2 GB + 25 × the per-cycle MB (the arm refuses otherwise).
    - MB = 10⁶ B.
  - **N1 before arming:** the part-B cold boots (the gate's dry runs) measure outside-use growth per boot. If it averages
    more than 6 MB per boot, it is mitigated first (e.g. a journald size cap) as a reversible rig change under the
    owner's hand-over, before order step 3. **The 100/250 MB bars are never loosened;** that would go to Nick.
- **RAM:** report only.
  - Each cycle reads mem_avail and CMA free after boot (the idle baseline) and 30 s after its clip.
  - Flagged: CMA free more than 20 MB below that boot's idle reading (rev 7: idle CMA is ~42 MB because the page cache
    borrows CMA), or mem_avail < 150 MB.
  - RESULTS shows the idle baseline next to each reading.
- **After the gate (N4, N6), in order:**
  1. `gate_cycle.py disarm`.
  2. `gate_cycle.py seal <run>`: one SHA256SUMS over the whole run folder, including `video/cycleNNN`,
     `cycles.csv/jsonl` and `gate.log`.
  3. Pull it to `~/NereusData/nereus-camera-test-rig/2026-10-20_gate25/`.
  4. On the Mac: `shasum -a 256 -c SHA256SUMS`, all OK.
  5. Only then clear the run folder from the Pi.
  6. `gate_cycle.py sd <run>`. Rev 4 item 5: SD free within 250 MB of the gate start, ≥ 2 GB, and ≥ 2× Wed's planned SD
     use.

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

## 8. Stop rules (from the proposal and spec; any one stops the bench, then card + PE 002 + PjM 003 are told)

1. 3 AE3 cold-boot wedges in one day → stop the AE3 and report.
2. VBAT < 3.4 V off the charger.
3. 2 throttled clips in a row.
4. Pi SD < 2 GB free, or the Mac data root < 8 GiB free (`df -g ~/NereusData`).
5. Anything that would need hands at the bench while Nick travels (a board off the bus after the ladder, a UPS that
   doesn't wake): stop and report. No improvising. There is no firmware change this round, so there is no firmware
   recovery.
6. Any rig change after Tue 10/20 6 PM (freeze, §10).
7. The hand-over slot ends (Sun 12:00 PM): hand back by message with the flag present; resume only under a new written
   hand-over.
8. ci-record or another recorder is found running before a setting: stop and ask the owner.

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
- RESULTS goes to `docs/pool2_20261021/RESULTS_B.md` on this PR; QE checks it; the PjM merges.
