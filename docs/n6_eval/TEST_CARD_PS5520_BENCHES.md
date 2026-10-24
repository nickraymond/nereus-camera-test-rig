# Test card: rerun the N6 benches with the PS5520 (Nick, chat 10/24 ~12:35 PM: "move forward with all your other tests and power monitor")

Pre-registered before each item runs. Same unit, firmware, service and scripts as TEST_CARD_BENCH_1024 and TEST_CARD_POWER_DOE
(nereus001 + spare N6 020023000450433547373200, v5.0.1 + tag25h9, service = feat/n6-eval @ 761afdf hash-matched, INA3221 CH1 at 10 Hz,
V3 card c1 at ~0.5 m). **The one change: sensor PAG7936 → PS5520 (id 0x5520).** Results are compared with the PAG7936 rows of
`2026-10-23_n6_eval_phase0/RESULTS.md`.

## Conditions to read the results by
- **Lens not refocused** after the swap (Nick can't now). The PS5520 image is soft (TEST_CARD_SENSOR_COMPARE). So detection counts
  and decodes are for "PS5520, stock lens, unfocused": they are not a verdict on the sensor. Timing and power are less affected.
- Same size names, different pixels: PS5520 HD = 1280×720 (PAG7936 1280×800), VGA = 640×480 (640×400). Reported as measured.
- mW stays **not validated** (known-load check pending). n is small: a map, not a recommendation.

## Items (run in this order, unchanged scripts)
1. **Bench 1, speed map:** `openmv/probes/n6_apriltag_probe_v5.py` via mpremote, then `mpremote reset`.
2. **Power DOE:** `scripts/n6_power_sweep.py --plan doe` ({HD, VGA} × tags {off, on} × {10, 15, 30} fps, 2 reps, 20 s, idle start/end).
3. **Bench 2:** `--plan bench2` (HD 30 fps asked; tags every 1/3/6/10; ROI tracking at 1 and 3; tags-off control).
4. **Bench 3:** `--plan bench3 --reps 3` (RAM buffer vs live USB, HD q70 10 fps).
5. **Bench 4, deep sleep:** `scripts/n6_deepsleep_retry.py` variants `official`, `csi_sleep`, `csi_shutdown`, one each, in that order,
   each only if the one before came back by itself. **No `lightsleep` run** (it hangs the N6; never on this board).

## Reported
Same tables as the PAG7936 runs, side by side: detect ms + decodes per size; delivered fps, mA / mW per cell; ROI fps; RAM vs USB mW;
asleep mA + wake time per variant; idle with the sensor never started vs initialised.

## Validity + stop rules (from the earlier cards)
- A DOE/bench cell is void unless rc 0, ≥ 150 power rows, 0 read errors, 0 lost frames.
- The N6 drops off USB twice, or once without coming back in 60 s → stop the sequence, tell Nick (replug).
- A deep-sleep run not back on USB within 60 s → stop, ask Nick. MemoryError → report, no retry loop.
- No board file writes, no firmware change, nothing on nereus002.
- Cost: ~45 min unattended on nereus001; nothing from Nick unless a stop rule fires.
