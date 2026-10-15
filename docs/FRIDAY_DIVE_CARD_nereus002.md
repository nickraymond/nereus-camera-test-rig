# nereus002 Friday dive card (Fri 2026-10-16)

Rig: Pi Zero 2 W + IMX708 + OpenMV N6 + OpenMV AE3 in one housing, LiFePO4wered/Pi+ UPS, and a
physical switch (= the UPS button: clean boot, clean shutdown). Code: `~/nereus-rig-poolcam`
(rig PR #115).

**What's new since the pool (10/14):**

- **All three cameras shoot the same moment.** One trigger fires all three, and every frame is
  on one clock. Bench skew: stills 60–110 ms (set by the AE3's frame time), RAW 10–50 ms,
  video start 10–50 ms.
- **The AE3 cold-boot wedge is fixed.** Cause: the kernel read the board's USB disk at
  power-on. Fix: `usb-storage.quirks=…` on the kernel line (Nick, 10/14). Start/stop gate on
  10/15:
  - run 1: 25/25 on every check;
  - run 2: 87/87 cold boots with all three cameras up, every still verified and a clean
    shutdown. Its 4 FAILs were all the N6 video stream; best strict streak 24.

  Details in `docs/rig_gate_20261014/RESULTS.md`.
- **One dive program runs by itself from switch-on:** calibration at two distances, stills,
  then video. It needs no network and no LED codes.

## The program (minutes from switch-on: start your dive watch when you switch on)

| T+ min | Block | You | The rig |
|---|---|---|---|
| 0–12 | **CAL A** (for the IMX708) | Once down (~T+2): ChArUco board at **0.7–0.9 m**. 9 places: 4 corners, 4 edge midpoints, centre, a board corner touching the frame edge each time. At each place: flat, ±30° left/right, ±30° up/down. **Hold each pose ~15 s.** | One synchronized RAW shot every ~13 s, 1 stop dark: IMX708 DNG + JPEG, N6 + AE3 Bayer. Writes `poses.csv` |
| 12–22 | **CAL B** (for the N6 / AE3) | The same board at **1.5–2.0 m**, posed for the boards' narrower view (~46°; on 10/14 their aim was top-left) | Same shots |
| 22–32 | **STILLS** | Scene / reference card at your test distance | Synchronized sets (still + RAW, all three), every ~25 s |
| 32–38 | **VIDEO** | One clip per minute (T+32 … 37). **At about :20 past each minute, flash a torch once in view of all three cameras**, then film the scene | 6 synchronized 30 s clips (each starts ~10 s after the minute): IMX708 H.264 1080p30, N6 + AE3 MJPEG VGA 10 fps |
| 38 → | stills | Anything | Synchronized sets until switch-off or low battery (stops itself at 3.10 V) |

Shots taken before you are in position just have no board in them; the pipeline skips them.

**Coverage:** there is no preview underwater (no Wi-Fi through water).
- **Before:** rehearse the 9 places in air at the pool edge, on the preview pages (loop stopped
  first, see below). Underwater the view is ~1.33× narrower (flat port), so place the board a
  little closer to the centre than it looked in air.
- **After:** pull a subset and run the calibration pipeline's `coverage.png` before leaving.
  A redo is cheap: switch off and on to restart the program.

## Known limits (decided 10/15; no code change before Friday)

- **Sync bar (d42 pending Nick):** the Friday plan's bar is ≤ 100 ms for synced stills. Until
  Nick decides d42, the **JPEG** still shots over 100 ms are **scored FAIL**. On the bench that
  was 30/112, 27 % (median 86 ms, max 187 ms), because the AE3 waits one frame and the IMX708 still is quantised to
  70 ms frames. The **RAW** synced shots meet the bar: gate 0/112 over, max 64 ms. That includes
  every calibration pose. The dive itself doesn't change: no code change, and every shot's skew
  is logged in its `sync.json`.
- **N6 video (PgM d40):** about 1 clip in 28 loses the N6 stream partway (gate: 4 of 112). Its USB drops bytes
  (gate008 cycle 22: 47 of ~100 frames). The IMX708 and AE3 clips are unaffected. Flash the
  torch early in each clip.
- **N6 RAW (Block B calibration uses the N6 Bayer):**
  - The N6 loses about 1 RAW frame in 55 with nothing else loading the Pi (gate008 cycles 36
    and 52). It lost 2 more while a Mac pull loaded the Pi.
  - CAL B gives ~46 shots in 10 min, so expect ~1 N6 frame lost and ~45 good. The calibration
    pre-reg needs ≥ 15 distinct usable poses (B1).
  - A lost frame only removes the N6 from that one pose. The real risk is coverage (corners and
    edges), not the count.

## Bench, before leaving (Thursday)

```bash
ssh pi@192.168.1.35 'grep -o "usb-storage[^ ]*" /proc/cmdline'
```
Expect `usb-storage.quirks=37c5:16e3:i,37c5:1206:i`.

```bash
ssh pi@192.168.1.35 'cd ~/nereus-rig-poolcam && ~/nereus-camera-test-rig/.venv/bin/python scripts/gate_cycle.py disarm'
```
The gate and the pool loop must never both be armed.

```bash
ssh pi@192.168.1.35 'bash ~/nereus-rig-poolcam/scripts/pool_mode.sh arm --program cal_a:12,cal_b:10,stills:10,video:6x30 --lens-position 0.8 --ups-recover-after 0 --min-vbat-mv 3100 --hours 6'
```
Add `--depth-bus N` if the Bar30 has moved to its own I²C bus.

```bash
ssh pi@192.168.1.35 'bash ~/nereus-rig-poolcam/scripts/pool_mode.sh status'
```
Expect `armed: YES (… --program …)`, `crontab: 1 pool line(s)`, `dive flag: present`.

Then: charge to VBAT ≥ 3.4 V, and **switch OFF**.

## At the pool

1. **Switch ON**, and start the dive watch.
2. Wait ~1 min (boot + first shot).
3. Seal and dive. Follow the program table.
4. **Stop:** switch OFF. The set in flight may be lost; everything before it is on disk.
5. **Pull everything before powering it for anything else** (Nick's rule, 10/14). The Mac joins
   the rig's Wi-Fi `nereus002`, which comes up ~60 s after boot when no home Wi-Fi is around:
   ```bash
   python -m host_tools.pool_pull --host pi@10.42.0.1 --no-dng
   ```
   That is the fast pass. Run it again without `--no-dng` for the archive.

**Previews** (in-air rehearsal only):

```bash
ssh pi@10.42.0.1 'bash ~/nereus-rig-poolcam/scripts/pool_mode.sh stop'
```
Then run `python -m host_tools.focus_stream --imx708` on the Pi; it serves :8083 (the boards
are on :8081 AE3 and :8082 N6). **Never run a preview with the loop running:** a preview holds
the camera and every set fails while it runs. Switch off/on afterwards to restart the program.

## If something looks wrong

- **The program didn't start** (no new `results/pool/pool…` folder 2 min after switch-on): check
  `pool_mode.sh status`, then switch off/on.
- **A camera missing in `pool.log`:** the IMX708 keeps going. A board is retried every 5 sets. A
  switch off/on resets the boards; it is a clean cold boot now that the AE3 fix is in.
