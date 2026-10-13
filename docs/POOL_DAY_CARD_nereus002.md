# nereus002 pool-day card (pool test Wed 2026-10-14)

Rig: Pi Zero 2 W + IMX708 + OpenMV N6 + OpenMV AE3 in one housing, powered by the
LiFePO4wered/Pi+ UPS. Code: `~/nereus-rig-poolcam` on the Pi (branch
`feat/pool-rig-unattended`). Results: `~/nereus-rig-poolcam/results/pool/<run>/`.

## What it does

Every ~45 s it captures one **set**:

- **IMX708:** a full-resolution 4608×2592 DNG and a JPEG from the same exposure, plus an
  auto still. Lens 0.8 manual (the Spotter units' pool setting).
- **N6 and AE3:** each board's own JPEG plus an 8-bit RAW Bayer frame, 1280×800.

Exposure and white balance are on auto everywhere.

It keeps going until power-off, 6 h, a full disk or a low battery (3.05 V). No network is
needed.

If a board fails, the IMX708 keeps going. The board is retried every 5 sets, and one
automatic UPS power cycle (~2.5 min on the charger, up to ~7 min on battery) clears a
wedged board.

## Night before / morning (bench)

The rig is left **armed and powered OFF**, on the charger. Armed means it starts the
capture loop by itself when it powers on.

To check it, power it on and run:

```bash
ssh pi@192.168.1.35 'bash ~/nereus-rig-poolcam/scripts/pool_mode.sh status'
```

You should see:

- `armed: YES (--hours 6 --lens-position 0.8)`
- `crontab: 1 pool line(s)`
- `dive flag: present`

If you power it on to check, a run starts. That is harmless; it is a new folder.

## Start (pool)

1. **Power on the UPS** with its touch button, the same as for a dive. Unverified: tap = on.
2. About 20 s after boot, the loop starts and creates `pool<NNN>_<time>`. The first set
   takes ~45 s.
3. **Optional, if a Mac is connected** (rig AP or LAN), so the run records the Mac clock:
   ```bash
   ssh pi@<rig> "bash ~/nereus-rig-poolcam/scripts/pool_mode.sh stop; bash ~/nereus-rig-poolcam/scripts/pool_mode.sh start --mac-time $(date -Iseconds)"
   ```
4. Seal the housing and put it in the water.

Battery is the limit:

- Nick measured ~3 h on a full charge with the dive recorder (4.2–4.6 W).
- The pool loop draws ~3.1–3.2 W at the UPS output (T3 below).
- **Projection: ≥ 3 h.** This is not measured on battery.

## What it shows

**UPS LED.** The LED may not be visible through the housing.

| LED | Meaning |
|---|---|
| Pulsing | Loop running, last set OK 3/3 |
| Flashing | Loop running, last set had a camera failure |
| Steady on | Pi on, loop not running |

**Health line.** One line per set in `pool.log`:

```
[set 012 | run+  9m | boot+10m] OK 3/3 · imx raw 24.0MB jpg 1.87MB · n6 raw 1.0MB jpg 0.38MB · ae3 raw 1.0MB jpg 0.23MB · 45s · cpu 47C thr 0x0 · mem 230MB cma 90MB · disk 59.5GB · ups 4.96Vin 3.44Vbat 3.1W
```

- `OK 3/3`: still and RAW from every camera. `PARTIAL 2/3` names the failing camera.
- `benched`: a board was skipped after 2 failed sets in a row; it is retried every 5 sets.
- `BATT`: running on the battery (no charger).

Watch it live (Wi-Fi): `ssh pi@<rig> 'tail -f ~/nereus-rig-poolcam/results/pool/pool*/pool.log'`.

## Stop

- **Power off** with the UPS button. Unverified: hold. The set in flight may be cut short;
  it is recorded as such, and every completed set is fsynced and checksummed.
- Or, on Wi-Fi: `bash ~/nereus-rig-poolcam/scripts/pool_mode.sh stop`. This finishes the
  set in flight and gives a clean stop.
- Never use `sudo reboot`: on this Pi it can become a power-off. To reboot, use
  `scripts/ups_power_cycle.sh --yes` (skill `rig-ups-power-cycle`).

## Download + cut sheet (Mac, rig repo root)

Network:

- **Bench:** `pi@192.168.1.35`.
- **At the pool:** join the open Wi-Fi `nereus002`, which comes up ~60 s after a boot with
  no home Wi-Fi, and use `--host pi@10.42.0.1`.

Then:

1. Stop the new run that started at boot, and disarm:
   ```bash
   ssh pi@<rig> 'bash ~/nereus-rig-poolcam/scripts/pool_mode.sh stop'
   ```
2. **Fast first pass**, everything except the DNGs (~6 MB/set; ~2 MB/s measured):
   ```bash
   .venv/bin/python -m host_tools.pool_pull --no-dng --host pi@<rig>
   ```
3. Cut sheet:
   ```bash
   .venv/bin/python -m host_tools.pool_render ~/NereusPool/<YYYYMMDD>/nereus002/pool<NNN>_* --spotter-root ~/NereusPool/<YYYYMMDD>
   ```
4. The DNGs the sheet picked:
   ```bash
   .venv/bin/python -m host_tools.pool_pull --host pi@<rig> --dng-for-render ~/NereusPool/<YYYYMMDD>/nereus002/pool<NNN>_*_render
   ```
   Then re-run step 3 to add the full-resolution DNG renders.
5. Everything, as the archive (resumable; ~24 MB/set): `.venv/bin/python -m host_tools.pool_pull --host pi@<rig>`.

`pool_pull` results:

- `OK` (exit 0): every file verified.
- `UNVERIFIED` (exit 3): no mismatch, but a set has no SHA256SUMS. Normally that is the last
  set, cut short at power-off; the pull prints its name.
- `BAD` (exit 1): a checksum mismatch or a missing file. Re-run the pull (it resumes), then
  look at the named file.

Open `~/NereusPool/<date>/nereus002/pool<NNN>_*_render/best_images.html`. On the sheet:

- "1:1" crops are native pixels.
- Full frames are labelled with their scale.
- The IMX708 1600×900 crop (native x=1504, y=846) sits next to bmcam003/004's when their
  pulls are under `~/NereusPool/<date>/bmcam00*`.

## After the pool

```bash
ssh pi@<rig> 'bash ~/nereus-rig-poolcam/scripts/pool_mode.sh disarm'
```

Disarm restores the crontab and removes `~/.no_dive_autostart`, so ci-record autostarts
again at the next boot.

## If something is off

| Symptom | Do |
|---|---|
| No new `pool<NNN>` after power-on | `cat ~/nereus-rig-poolcam/results/pool/boot.log`. `not armed` → `pool_mode.sh arm --hours 6 --lens-position 0.8`. `workbench runner is 'live'` → a recipe holds the cameras; stop it on :8088 |
| `REFUSED: on battery at … mV` | Charge it. It refuses to start below 3.25 V on battery (Nick's dive-autostart lesson) |
| A board `benched` every set | Expected for a dead board. One automatic UPS power cycle per run, at most 2 a day. After that, replug or power-cycle by hand |
| Pi gone > 10 min after a UPS cycle | Press the UPS touch button |
| Disk | Stops by itself at a 2 GB reserve (≈ 59 GB free = 30+ h of sets) |

Source of truth: `scripts/pool_capture.py`, `scripts/pool_mode.sh`, `scripts/ups_power_cycle.sh`,
`host_tools/pool_pull.py`, `host_tools/pool_render.py`. Test record: `docs/pool_rig_20261013/`.
