# Test card: N6 eval Phase 0 bench checks on nereus001 (SPEC_n6_eval must dos 1, 2, 4)

Pre-registered before anything runs. Spec: `docs/specs/n6_eval.md` (rev 1.1, sha256 e8c63850fa17, QE 010 PASS).
Owner: N6 eval builder. Unit: **nereus001 + its spare N6 only** (serial 020023000450433547373200, the board that
moved from nereus002: Nick in this chat, Thu 10/22 ~9 PM, "Yes the board moved here"; the rig SPEC §4 "Rig move to
nereus002" lists the same serial as nereus002's N6, QE N4). Never nereus002, its boards, any bmcam or Spotter.

- **Question:** what does the spare N6 really deliver on stock v5.0.1 (modes, on-board tag decode, a logged HD stream),
  so the Mon 10/26 outdoor walks use settings that exist?
- **Conditions:** indoors, on the desk, room light. The N6 stays on nereus001's USB port. The tag25h9 board is at ~1 m
  for step 5 only, placed by Nick (one placement, no walk).
- **Power monitor (working since Fri 10/23 ~8 PM PT):** an Adafruit INA3221 at 0x40 on I2C bus 1 (ID 0x5449 / die 0x3220).
  CH1 = the N6's 5 V; it read 5.10 V / ~100 mA idle. nereus001 moved to a new Pi 5 board (device-tree serial
  7f9a27eaa3a7241f, same SD card) because the old board's GPIO2 could not read back SDA. Evidence:
  `~/NereusData/nereus-camera-test-rig/2026-10-22_n6_i2c_enable/RESULTS.md`. Nothing else uses bus 1 on this Pi
  (no UPS daemon, no logger), so the poll rate is 10 Hz (must do 2).
- **Not in this card:** the walk recorder and LED (must do 7a, its own card) and firmware flashing (stock only; F3:
  no faster build found, `~/NereusData/nereus-camera-test-rig/2026-10-23_n6_eval_phase0/must_do_6_firmware_search.md`).

## Already done, Fri 10/23 PM, on Nick's direct instructions in this chat (before this card's OK line)
Evidence: `~/NereusData/nereus-camera-test-rig/2026-10-23_n6_eval_phase0/RESULTS.md` + `commands.log`.
- Steps 1–3: repo cloned (feat/n6-eval), board files backed up (`~/board_backups/20261024T030336Z/`), this branch's service
  deployed at 280b693 (`board_hashes.py`: ALL MATCH).
- **Firmware changed** (Nick: "your new job is to get the N6 to natively find these tags", then "yes you are approved to
  flash"): OpenMV v5.0.1 + `openmv/firmware/0001` (tag25h9), FIRMWARE partition only, read back identical, 10/23
  8:28 PM. Rollback = the official firmware_v5.0.1.zip, kept in the run folder and on nereus001 `~/fw/stock_v5.0.1/`.
- On-N6 tag25h9 on the V3 card: ids 0–3, 372 ms per HD frame. VGA stream with tags at 10 fps asked: 9.2 fps delivered.

## Steps (each logged, command + output, to `~/NereusData/nereus-camera-test-rig/<date>_n6_eval_phase0/`)
1. **Repo on nereus001** (a write: new folder only): `git clone` this repo to `~/nereus-camera-test-rig` at the PR's
   sha, `python3 -m venv .venv`, `pip install -e '.[serial,power]'` (pyserial, mpremote, smbus2); `pip freeze` saved to the run folder (QE N5).
   Also installed, 10/23: `dfu-util` (apt) for the flash. Nothing else.
   Nick's `~/ADIN_SPI_OpenMV` and his T1L services are not touched.
2. **Board identity + backup** (read): `scripts/board_hashes.py --board n6 --serial <N6>` and a copy of every board
   file to `~/board_backups/<ts>/` on nereus001 and to the run folder.
   **Stop** unless the board reports omv 5.0.1 with `image.TAG25H9` (the 10/23 build): ask Nick before any flash.
3. **Deploy this PR's board service** (a write to the N6's /flash): `host_tools/deploy_openmv.py --board n6
   --serial <N6>`, then `board_hashes.py` must show every file `match`. Restore = copy the backup back with mpremote.
   **End state (QE N3):** the N6 stays on this branch's service (hash-matched) and the tag25h9 firmware after the card;
   the backup and the stock zip are the way back.
4. **Mode list** (must do 1): `mpremote connect <by-id> run openmv/probes/n6_modes_probe_v5.py`, then `reset`.
5. **On-board AprilTags** (must do 4): tag board at ~1 m, `n6_apriltag_probe_v5.py` the same way, then `reset`.
6. **Logged HD stream** (must do 2): `scripts/sync_video.py --cameras openmv_n6 --n6-serial <N6> --seconds 5
   --board-framesize HD --board-quality 70 --board-fps 12 --meta --timing`, run 3 times (12, not 10: a 10 fps cap sits
   exactly on the ≥ 50-frame bar, QE N1), then once more with `--lock-3a`.
   The INA3221 CH1 is logged at 10 Hz beside each clip (read-only registers; no config or calibration write).
7. **Power known-load check** (must do 3), when Nick has ~5 min: a resistor Nick names (value + tolerance) on CH1 at
   5 V. The read current must be within ±10 % of V/R. Until it passes, every mW reads "not measured" (F1).

## n
- Steps 4–5: one run each. A mode list and an API check are facts, not a rate. Step 5's per-frame ms is a median of
  10 frames per size and format.
- Step 6: 3 clips (baseline) + 1 (lock). Nothing is recommended from these; they only show the logging works.

## Pass bars (fixed now)
- Step 4: the probe finishes, with an `identity` line holding the version string, HD RGB565 captured at 1280×800,
  and every entry `ok` true/false/absent (none missing).
- Step 5: ≥ 1 decode at 1 m in at least one size (must do 4), or F2 is recorded (a missing function or family).
- Step 6: each clip has ≥ 50 frames in 5 s (≥ 10.0 fps delivered), 0 resyncs, 0 short frames, no board reset.
  `frames.csv` has a non-empty `exposure_us` and `gain_db` on **every** row. The `--lock-3a` clip reports
  `locked_3a.exposure` = true and `gain` = true, and its exposure stays within ±1 % across the clip. `awb` is
  reported, not a bar. The power log has ≥ 45 CH1 rows per 5 s clip and no read error.
- Step 7: |I_read − V/R| ≤ 10 % of V/R, over 10 reads.

## Stop rules
- The N6 drops off USB or hangs twice: stop, and report on c_n6_eval (F6).
- A password prompt, a write outside `~/nereus-camera-test-rig`, `~/board_backups` and the N6's service files,
  or anything touching nereus002: stop.
- The firmware is not the 10/23 tag25h9 build (omv 5.0.1 + TAG25H9): stop and ask Nick.

## Cost
- About 45 min of builder time on nereus001. Nick: one OK line in the builder's chat, plus ~1 min to place the tag
  board at 1 m for step 5. No Mac compute. It displaces nothing: nereus001 is this effort's unit.

## Is it worth it?
Kill line Sun 10/25 6 PM: without must dos 1–4, Mon 10/26 runs only tests 0 and 1. This card is the shortest
route to those must dos.

**OK line (Nick types it in the builder's chat):**
`OK: N6 eval builder runs TEST_CARD_P0 @ <sha> on nereus001 + its spare N6`
