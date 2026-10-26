# Test card: N6 bench, Sat 10/24 (Nick: "start work on the next set of bench work")

Pre-registered before each item runs. Unit: nereus001 (Pi 5 board 7f9a27eaa3a7241f) + spare N6 020023000450433547373200,
firmware v5.0.1 + tag25h9 (10/23), service = this branch (hash-matched). The V3 card is ~0.5 m in front of the N6, indoors,
nothing moves. INA3221 CH1 at 10 Hz; mW stays "not validated" until the known-load check. The N6 has no SD card (Nick, 10/24),
so there is no SD arm. n is small (a map, not a recommendation).

## Bench 1: AprilTag speed map (why HD is slow)
- Question: where does the time go, and which framesize / region-of-interest gets on-N6 detection to ≥ 10 Hz?
- How: `openmv/probes/n6_apriltag_probe_v5.py` via mpremote. HD, 960×600, VGA, QVGA × GRAYSCALE / RGB565, 10 frames
  each: detect ms, snapshot ms, decodes. At HD GRAYSCALE, `roi=` timed with one box around all tags and with 4 per-tag boxes.
- Reported: median and max ms, and decodes (4 expected). No pass bar.

## Bench 2: decoupled detection (video at full rate, detection on every Nth frame)
- Question: can the N6 stream HD at ≥ 10 fps while still detecting tags a few times a second?
- How: start_stream `tag_every` = N (detect on every Nth frame only). HD q70, asked 30 fps, N in {1, 3, 6, 10}, 20 s each,
  2 reps: `scripts/n6_power_sweep.py --plan bench2`. **Added before the run (after bench 1 found ROI 32 ms vs 182 ms):**
  `tag_roi`, which searches only boxes (+40 px) around the last frame's tags and falls back to the full frame when one is
  missing, at N = 1 and 3. A tags-off control is included.
- Reported: delivered fps, detections per second, share of ROI detections, tags per detection, power. Bar (from SPEC_n6_eval must do 9, for reference): ≥ 10.0 fps
  delivered at HD.

## Bench 3: buffer to RAM, then offload, vs streaming over USB
- Question (Nick): what is the power while frames are kept in N6 RAM (no USB traffic) vs streamed live over USB?
- How: start_stream `buffer_frames` = 1: JPEGs are kept in RAM during the capture window and sent in one burst after it.
  HD q70 10 fps, 10 s capture. The capture window and the burst are timed separately. Compared against live USB at the
  same settings. 2 reps.
- Reported: mean mW in the capture window (RAM) vs live streaming, burst duration and energy. Ethernet is not on this
  bench, so not tested.

## Stop rules (all items)
- The N6 drops off USB twice, or once without coming back in 60 s: stop, report, ask Nick.
- A RAM allocation fails (MemoryError): report it, shorten the capture, never retry in a loop.
- No sleep or deep-sleep calls in this card. They wait for the researched procedure and their own card.

## Bench 4: deep-sleep retry (Nick, chat 10/24 ~8:35 AM: "Run the deep-sleep retry now, one change per run as planned. I'm near the rig and can press SW2 or replug if it sticks.")
- Basis: OpenMV v5.0.1 `scripts/examples/50-OpenMV-Boards/53-N6-Boards/80-Low-Power/deep_sleep.py` (set the RTC with
  `rtc.datetime(...)`, then `machine.deepsleep(ms)`), plus the research notes in the run folder's RESULTS.md.
- Runs, in this order, **one change each**, and only if the one before came back by itself. Each starts from a hard reset
  (`mpremote reset`, so the sensor has never started), then 10 s of baseline:
  1. `official`: set the RTC, `deepsleep(20000)`, nothing else.
  2. `csi_sleep`: (1) + start the sensor for 1 s, then `csi.sleep(True)` before deep sleep.
  3. `csi_shutdown`: (1) + start the sensor, then `csi.shutdown(True)` before deep sleep.
  4. `lightsleep`: set the RTC, `lightsleep(20000)`, then log "woke".
- The board writes each step to `/flash/ds_log.txt` (fsync) before it sleeps. The Pi logs CH1 at 10 Hz and polls the
  board's USB node (gone / back times). After the wake it reads `machine.reset_cause()` and the log.
- Metric: mean mA / mW from 2 s after USB goes away to 1 s before the 20 s timer ends; wake time = USB back − USB gone.
  n = 1 per variant (a map). Raw INA3221 (not validated).
- **Stop:** the board is not back on USB within 60 s of leaving → stop the sequence, ask Nick for SW2, and record whether
  SW2 wakes it.
