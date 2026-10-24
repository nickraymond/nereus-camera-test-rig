# Test card: PS5520 large sizes streamed over USB, delivered fps + power (Nick, chat 10/24: "Yes: add 1280x960, 1920x1080, 2560x1440, 2592x1944 to the N6 stream allowlist (deploy to the board) and measure delivered fps + power over USB.")

Pre-registered before it runs. Follows TEST_CARD_PS5520_FPS (on-board: sensor 29.4 fps at every size; with JPEG q70 28.0 / 24.5 / 14.7 / 10.3 fps).
Same unit and conditions: nereus001 + spare N6, PS5520, v5.0.1 + tag25h9, stock lens unfocused, V3 card c1 at ~0.5 m, indoor light, auto 3A.

## The board change (Nick approved the deploy)
- `openmv/n6/board_config.py` FRAMESIZES gains `1280x960` (SXGAM), `1920x1080` (FHD), `2560x1440` (QHD), `2592x1944` (WQXGA2). Nothing else
  changes on the board: every other service file is identical to the board's current 761afdf copy.
- Before: back up the board's 7 files to nereus001 `~/board_backups/<ts>/`. Deploy with `host_tools/deploy_openmv.py --board n6 --serial …`, then
  `board_hashes` ALL MATCH and an HD still `completed`.
- Rollback: copy the backup's `board_config.py` back (or redeploy 761afdf), then `board_hashes`.

## Run
`scripts/n6_power_sweep.py --plan bigsizes --reps 2` (20 s per cell, idle start/end). Cells: HD (reference) + the 4 sizes, tags off, JPEG q70,
30 fps asked (the sensor's cap). Each cell = one `sync_video.py` clip of the N6 alone over USB; INA3221 CH1 at 10 Hz.

## Reported
Per size: delivered fps on the Pi (frames.csv `host_rx_mono`), JPEG bytes per frame, host throughput (bytes × fps), mean mA / mW (raw INA3221,
**not validated**), board snap / JPEG / USB-write ms (`--timing`). Compared with the on-board fps of TEST_CARD_PS5520_FPS: the gap is the USB cost.
n = 2: a map, not a recommendation.

## Validity + stop rules
- A cell is void unless sync_video rc 0, ≥ 150 power rows, 0 read errors. Corrupt or dropped frames are reported, not hidden.
- The N6 drops off USB twice, or once without coming back in 60 s → stop, tell Nick (replug).
- The board's flash is not written by streaming; nothing on nereus002; no firmware change; no lightsleep.
- Cost: ~6 min on nereus001; nothing from Nick unless a stop rule fires.
