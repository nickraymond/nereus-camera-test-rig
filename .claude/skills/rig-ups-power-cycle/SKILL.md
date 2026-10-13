---
name: Rig UPS Power Cycle
description: Fully power-cycle a rig Pi AND its USB boards (N6/AE3) through the LiFePO4wered/Pi+ UPS — clean shutdown, power cut, automatic power-on by wake timer — with no root and no one at the bench. Use when an OpenMV board is wedged (AE3 "board is not reading its USB input" / write timeouts, a board missing from /dev/serial/by-id that a serial reset cannot bring back), when the Pi needs a cold boot, or to test boot-start behaviour (pool mode). Not for bmcam field units (they have no LiFePO4wered; use the Spotter power tools).
---

# Power-cycle the rig through the LiFePO4wered/Pi+ UPS

Nick (2026-10-13): "tell the LiFePO4+ to reboot itself by setting a timer for it to shut down
then turn back on — this way you can force the rig to reboot and try to repair the USB
connections."

The script is `scripts/ups_power_cycle.sh` (deployed with the repo, e.g.
`~/nereus-rig-poolcam/scripts/` or `~/nereus-camera-test-rig/scripts/` on the Pi).

Prior art: Nick's `~/ADIN_SPI_OpenMV/pi/field/power_cycle.py`. It produces the same cycle
with `RTC_WAKE_TIME` + `sudo -n systemctl poweroff` and went 5/5 on 2026-09-07. On battery a
cycle took **~7 min**, against ~2.5 min with the charger. Prefer his tool where passwordless
sudo exists. This script needs no sudo and is the one the pool loop calls.

## How it works (verified on nereus002, 2026-10-13 3:24 PM)

1. `lifepo4wered-cli set WAKE_TIME <min>`: the UPS powers the Pi back on that many minutes
   after it is off (README: `set wake_time 60` = an hour). The register reads back 0 after
   the wake.
2. `lifepo4wered-cli set PI_RUNNING 0`: the root `lifepo4wered-daemon` polls this flag every
   second. On 0 it runs `systemctl poweroff` (`lifepo4wered-daemon.c`), a clean shutdown.
   After that the UPS cuts its output, which also feeds the USB boards.
3. Both registers are volatile. Nothing is written to UPS flash (no `CFG_WRITE`).

The user `pi` can do this without sudo because it is in the `i2c` group.

Measured on the 3:24 PM cycle (`--wake-min 1`, charger connected):
- Pi off at +4 s.
- Ping back at +106 s.
- The pool loop's `@reboot` start at +~20 s of uptime.

## Steps

1. **Check that you own the rig.** Nobody else may be running captures. Check the workbench
   (`curl -s localhost:8088/api/runner` should report `state` idle) and any pool loop
   (`bash scripts/pool_mode.sh status`). A power cycle kills whatever is running. A recipe or
   loop writing a file at that moment loses that file.
2. **Dry run.** This prints the UPS state and runs every guard without changing anything:
   ```bash
   ssh -o ConnectTimeout=10 -o BatchMode=yes pi@<rig> 'bash ~/<repo>/scripts/ups_power_cycle.sh'
   ```
   Expect `UPS: PI_RUNNING=1 VIN=~4960mV VBAT=~3400mV ... dry run OK`.
3. **Cycle:**
   ```bash
   ssh pi@<rig> 'bash ~/<repo>/scripts/ups_power_cycle.sh --yes --wake-min 1 --reason "<why>"'
   ```
   The ssh session returns at once, and the Pi stops answering ping within ~5 s.
4. **Wait** for the Pi to answer again (~2 min with `--wake-min 1`). Use a ping loop with a
   10 min cap, not a fixed sleep.
5. **Verify.**
   - `uptime` shows 0–1 min.
   - `journalctl -b -1 | grep -i poweroff` shows `systemd-poweroff`, so the shutdown was clean.
   - `lifepo4wered-cli get WAKE_TIME` reads 0.
   - The boards are back: `ls /dev/serial/by-id/` lists both serials. The N6 is
     `020023000450433547373200` and the AE3 is `0829c14000000000`.
   - Each board answers `get_device_info`. A board that boots into the REPL instead of the
     rig service needs `mpremote connect <by-id path> reset`.
6. **Log.** `~/ups_power_cycle.log` gets one `CYCLE` line per request and a `REFUSED` line for
   each refusal.

## Guards (built into the script)

- **UPS state:** the UPS must answer and report `PI_RUNNING=1` and `AUTO_BOOT=2`. With any
  other `AUTO_BOOT` the rig does not come back (Nick's guard).
- **Battery:** on battery (VIN below `VIN_THRESHOLD`), VBAT must be ≥ 3300 mV. Below
  `VBAT_BOOT` (3150 mV here) the UPS will not boot the Pi again. A 3.23 V cell reset under
  boot load (Nick, `dive_autostart.sh`).
- **No boot loop:** uptime must be ≥ 300 s, so a caller at boot can never loop. `--force`
  overrides this.
- **Daily cap:** at most 3 cycles per 24 h.

## Failure modes

- **The Pi does not come back in 10 min.** The wake timer did not fire, or the battery is too
  low. Ask for a human (one line through the EM): press the UPS touch button, or unplug and
  replug the UPS input.
- **A board is still wedged after a cycle.** Its power may not come from the UPS output (a
  separately powered hub). It needs a physical USB replug.
- **The SD card.** The power cut happens only after a clean `systemctl poweroff`, so it is not
  a hard cut. Still, do not cycle in a loop. The daily cap is there for that.
- **Clock.** After the cycle the Pi clock restarts from its last saved value. On nereus002 it
  read ~75 s behind until NTP synced. The LiFePO4wered RTC and NTP correct it. In the pool
  (no NTP) use monotonic time plus the RTC.
