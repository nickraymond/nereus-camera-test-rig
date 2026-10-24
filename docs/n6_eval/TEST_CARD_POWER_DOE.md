# Test card: N6 power DOE on nereus001 (Nick, chat, Fri 10/23 ~8 PM)

Pre-registered before it runs. Unit: nereus001 (new Pi 5 board) + its spare N6 (serial 020023000450433547373200),
owned by the N6 eval builder. Firmware: OpenMV v5.0.1 + `openmv/firmware/0001` (tag25h9 on), flashed 10/23 8:28 PM.
Board service: this branch at 280b693, all 7 files hash-matched.

- **Question (Nick):** "a sweep showing power consumption for HD, HD with april tag detection, 640x, 640x with april tag
  and then for 10, 15, 30 fps so there is a full DOE matrix".
- **Conditions:** indoors, desk light, the V3 card ~0.5 m in front of the N6 (the same framing as tags_onboard_25h9.jpg),
  nothing moves. Factors: framesize {HD 1280×800, VGA 640×400} × on-N6 tag25h9 detection {off, on} × fps asked
  {10, 15, 30} = 12 cells. Fixed: JPEG q70, auto exposure, the stream goes over USB to the Pi (sync_video), and the
  INA3221 CH1 (the N6's 5 V) is logged at 10 Hz. Idle (service waiting) is measured at the start and the end.
- **n:** 2 replicates per cell, the second in reverse order (drift check), 20 s per cell, so about 200 power rows per
  cell. No recommendation comes from this sweep alone (n = 2); it maps the space.
- **Metrics (reported, no pass/fail):** delivered fps (an asked rate the board can't reach is a result, not a failure),
  mean V / mA / mW over the frame window, mA sd, detect ms median, tags per frame, bytes per frame.
- **Validity bars:** every cell has ≥ 150 power rows, 0 INA read errors, sync_video rc 0 and 0 lost frames. The two
  idle cells agree within 10 %. Otherwise that cell is marked void.
- **mW status:** "not validated" until the known-load check (SPEC_n6_eval must do 3) passes. The numbers are raw
  INA3221 readings.
- **Stop rules:** the N6 drops off USB twice → stop. A read error streak > 5 s → stop. nereus002 is never touched.
- **Cost:** ~15 min unattended on nereus001; nothing from Nick.

Command: `python scripts/n6_power_sweep.py --out ~/n6_eval/power_doe_<ts>`

## Addendum: low-power states (Nick, chat 10/23 ~8:50 PM: "what is the low idle state? OpenMV says it goes to miliwatts" → "run it please")
- Question: what does the N6 draw in each low-power state the firmware offers?
- Phases, 20 s each (`openmv/probes/n6_sleep_probe_v5.py`, run by `scripts/n6_sleep_power.py`): sensor on with the
  processor idle → `sensor.sleep(True)` → `sensor.shutdown(True)` → `machine.lightsleep(20000)` → `machine.deepsleep(20000)`
  (the board leaves USB and reboots) → the rig service idle after the reboot. A control the firmware lacks is reported, not
  a failure.
- Metric: mean mA / mW per phase from 2 s after its start to 1 s before it ends (n = 1 run: a map, not a claim).
  Raw INA3221; mW not validated.
- Stop: if the N6 doesn't come back on USB within 90 s of deep sleep, stop and report. A power cycle would be a hands-on
  step for Nick, not something the builder does.
