# Test card: N6 eval Phase 0 bench checks on nereus001 (SPEC_n6_eval must dos 1, 2, 4)

Pre-registered before anything runs. Spec: `docs/specs/n6_eval.md` (rev 1.1, sha256 e8c63850fa17, QE 010 PASS).
Owner: N6 eval builder. Unit: **nereus001 + its spare N6 only** (serial 020023000450433547373200, the board that
moved from nereus002, Nick 10/23). Never nereus002, its boards, any bmcam or Spotter.

- **Question:** what does the spare N6 really deliver on stock v5.0.1 (modes, on-board tag decode, a logged HD stream),
  so the Mon 10/26 outdoor walks use settings that exist?
- **Conditions:** indoors, on the desk, room light. The N6 stays on nereus001's USB port. The tag25h9 board is at ~1 m
  for step 5 only, placed by Nick (one placement, no walk).
- **Not in this card:** power (Nick 10/23: "ignore the power monitor for now", so must do 3 = F1, mW "not measured"),
  the walk recorder and LED (must do 7a, its own card), and firmware flashing (stock only; F3: no faster build found,
  `~/NereusData/nereus-camera-test-rig/2026-10-23_n6_eval_phase0/must_do_6_firmware_search.md`).

## Steps (each logged, command + output, to `~/NereusData/nereus-camera-test-rig/<date>_n6_eval_phase0/`)
1. **Repo on nereus001** (a write: new folder only): `git clone` this repo to `~/nereus-camera-test-rig` at the PR's
   sha, `python3 -m venv .venv`, `pip install -e '.[serial]'` (pyserial, mpremote). Nothing else is installed.
   Nick's `~/ADIN_SPI_OpenMV` and his T1L services are not touched.
2. **Board identity + backup** (read): `scripts/board_hashes.py --board n6 --serial <N6>` and a copy of every board
   file to `~/board_backups/<ts>/` on nereus001 and to the run folder.
   **Stop** if the firmware is not v5.0.1: ask Nick, never flash.
3. **Deploy this PR's board service** (a write to the N6's /flash): `host_tools/deploy_openmv.py --board n6
   --serial <N6>`, then `board_hashes.py` must show every file `match`. Restore = copy the backup back with mpremote.
4. **Mode list** (must do 1): `mpremote connect <by-id> run openmv/probes/n6_modes_probe_v5.py`, then `reset`.
5. **On-board AprilTags** (must do 4): tag board at ~1 m, `n6_apriltag_probe_v5.py` the same way, then `reset`.
6. **Logged HD stream** (must do 2): `scripts/sync_video.py --cameras openmv_n6 --n6-serial <N6> --seconds 5
   --board-framesize HD --board-quality 70 --board-fps 10 --meta --timing`, run 3 times, then once more with `--lock-3a`.

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
  reported, not a bar.

## Stop rules
- The N6 drops off USB or hangs twice: stop, and report on c_n6_eval (F6).
- A password prompt, a write outside `~/nereus-camera-test-rig`, `~/board_backups` and the N6's service files,
  or anything touching nereus002: stop.
- The firmware is not stock v5.0.1: stop before step 3 and ask Nick.

## Cost
- About 45 min of builder time on nereus001. Nick: one OK line in the builder's chat, plus ~1 min to place the tag
  board at 1 m for step 5. No Mac compute. It displaces nothing: nereus001 is this effort's unit.

## Is it worth it?
Kill line Sun 10/25 6 PM: without must dos 1–4, Mon 10/26 runs only tests 0 and 1. This card is the shortest
route to those must dos.

**OK line (Nick types it in the builder's chat):**
`OK: N6 eval builder runs TEST_CARD_P0 @ <sha> on nereus001 + its spare N6`
