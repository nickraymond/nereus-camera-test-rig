# Test card: PAG7936 + 4.5 mm M12 lens, bench reruns (Nick, chat 10/24 ~7:35 PM: "the 1MP sensor is back with a better lens, please run the sweeps again for power, april tag detection and other. This is a 4.5mm lens 1m away in air")

Pre-registered before each item runs. Unit: nereus001 + spare N6 020023000450433547373200, firmware v5.0.1 + tag25h9 (v5.0.1-2.gbe78cb31ce),
board service = test/141-on-n6-eval 1d6f5c7 board files (= feat/n6-eval + the frame-0 `sensor_id`, hashes ALL MATCH 7:37 PM). INA3221 CH1 at 10 Hz.

## Conditions, and what changed against the morning PAG7936 runs (`2026-10-23_n6_eval_phase0/RESULTS.md`)
| | morning PAG7936 runs | this card |
|---|---|---|
| sensor | PAG7936 (0x7936) | PAG7936 (0x7936, read 7:30 PM) |
| lens | stock M12 | **4.5 mm M12** (Nick) |
| card distance | V3 c1 at ~0.5 m | **V3 card at ~1 m** in air (Nick) |
| light | daylight / morning | **evening indoor** (7:37 PM still: 16.6 ms, 9.5 dB) |
| HD tag edge | 71 px | 87–91 px (7:37 PM still) |
- **Three things changed at once** (lens, distance, light), so differences are not attributable to the lens alone (CLAUDE.md §10). Speed and power
  depend mostly on frame size and the scene's tag count, so they stay comparable; tag decode counts are this setup's own.
- mW stays **not validated** (raw INA3221). n = 2 per cell (3 for RAM vs USB): a map, not a recommendation.

## Items (unchanged scripts and plans, same order as TEST_CARD_PS5520_BENCHES)
1. **Bench 1, speed map:** `openmv/probes/n6_apriltag_probe_v5.py`, 2 reps.
2. **Power DOE:** `scripts/n6_power_sweep.py --plan doe` ({HD 1280×800, VGA 640×400} × tags {off, on} × {10, 15, 30} fps).
3. **Bench 2:** `--plan bench2` (HD, 30 fps asked; every 1/3/6/10; ROI at 1 and 3; tags-off control).
4. **Bench 3:** `--plan bench3 --reps 3` (RAM buffer vs live USB, HD 10 fps).
5. **Bench 4, deep sleep:** `scripts/n6_deepsleep_retry.py` `official`, `csi_sleep`, `csi_shutdown`, one each. **No lightsleep.**
6. **Stills:** 3 × HD q90 (`~/n6_eval/n6_snap.py`, the same capture call as the 11:47 AM baseline), for a visual comparison with the stock lens.

## Validity + stop rules (as before)
- A cell is void unless rc 0, ≥ 150 power rows, 0 read errors; lost frames are reported.
- The N6 drops off USB twice, or once without coming back in 60 s → stop, tell Nick. A deep-sleep run not back in 60 s → stop.
- No board file writes, no firmware change, nothing on nereus002.
- Not on this card: the lens sharpness measurement (edge 10–90 % in mm, n ≥ 10), which needs its own card and target.
- Cost: ~45 min unattended on nereus001.
