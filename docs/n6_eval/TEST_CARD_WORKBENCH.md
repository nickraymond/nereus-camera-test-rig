# Test card: Nick's workbench + recipes on nereus001 (Nick, chat 10/24 ~9 AM)

Nick: "get Nereus back up and working with the 'recipes' so we can do a live stream, or compare the boards, or run the urchin
detection with another LCD screen ... after the report and before any other hardware changes."

Source surveyed read-only (ADIN_SPI_OpenMV main, `pi/workbench/`). The server is stdlib-only, port 8088, no auth, and runs by hand
(`pi/workbench/run_workbench.sh`). Firmware pins are a substring match on the board's `sys.version` (`workbench.py:747-748`). No run
path writes the boards' main.py/boot.py/ROMFS (survey, unverified on hardware).

## What nereus001 can do today (only the N6 is attached; no AE3, no IMX708 (`rpicam`: "No cameras available"), no LCD)
| Goal | Recipe | Status on nereus001 |
|---|---|---|
| Live stream | `field-streams` (:8090), csi=off | Should run: N6 panel live, AE3 panel "not found" |
| Record / watch / download | `video-record` (:8093), N6 only | Should run; needs `ffmpeg` (not installed) |
| Video DOE | `video-doe` (:8094), cameras=N6 | Should run |
| N6 detection stream | `n6-detect-stream` | **Refused:** pin "v5.0.0" doesn't match v5.0.1; also needs `/rom/yolov8n_192.tflite` (unknown) |
| Compare boards | s28 / two-colour / composites | Need the AE3 (and the IMX708 for composites) |
| Urchin on an LCD | `s8-hil-urchin` + `s8-hil-review` | Need the AE3, an LCD + `hil-lcd.service` (unit not in the repo), `~/hil_monterey` media, and urchin models in the N6 ROMFS |

## Steps (each logged to `~/NereusData/nereus-camera-test-rig/<date>_workbench/`)
1. **A separate clone** of ADIN_SPI_OpenMV origin/main at `~/workbench_src/ADIN_SPI_OpenMV` (a new folder). Nick's `~/ADIN_SPI_OpenMV`
   (his untracked files, the T1L services' code) is not touched.
2. **User-space venv** `~/mpv` (`--system-site-packages`) with mpremote, pyserial, numpy, pillow: what `run_workbench.sh` looks for.
3. **`sudo apt-get install -y ffmpeg`** (system package, for video-record). This is the one system change.
4. **Start the workbench by hand** (`nohup bash pi/workbench/run_workbench.sh`, :8088). No systemd unit, no udev rules, nothing enabled
   at boot; a reboot stops it.
5. **Check from the Mac:** the :8088 page lists the recipes; `field-streams` shows a live N6 picture on :8090; `video-record` records
   30 s of N6 and plays it back.
6. **After each run:** `mpremote reset` the N6 so the rig service (main.py) runs again, then check `board_hashes.py` ALL MATCH.

## Pass bars
- :8088 answers 200 from the Mac and lists the recipes.
- field-streams: an N6 frame updates ≥ 1 per second on :8090 for 60 s.
- video-record: a 30 s N6 clip plays in the browser and downloads.
- The rig service answers `get_device_info` after the reset.

## Stop rules
- Anything wants DFU, a udev rule, a systemd unit or a board file write: stop and ask.
- The N6 drops off USB twice: stop.
- Nothing on nereus002 or its boards, ever.

## Needs Nick (not in this card)
- The board comparison and urchin-on-LCD recipes need an **AE3 (and an IMX708 for the composites) on nereus001**, an LCD +
  the `hil-lcd` unit, and the Monterey media.
- `n6-detect-stream`'s firmware pin is a change in **Nick's ADIN_SPI_OpenMV repo**, not this one.

**OK line:** `OK: N6 eval builder runs TEST_CARD_WORKBENCH @ <sha> on nereus001 (incl. apt ffmpeg)`
