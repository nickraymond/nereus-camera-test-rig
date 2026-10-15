# nereus002: AE3 boot wedge + start/stop gate. Results

Pre-registration: `PREREG.md` (same folder). All times are Pacific. Raw data lives on the Pi
under `~/nereus-rig-poolcam/results/gate/<run>/`: `cycles.csv`, `cycles.jsonl`, `gate.log`
and one experiment per cycle.

## Phase 1: root cause (2026-10-14 evening)

### Finding: the kernel's USB-disk probe at cold boot

Each OpenMV board exposes its flash as a USB mass-storage disk alongside the CDC serial port
the rig uses.

**At cold boot:**

1. The Pi kernel enumerates both boards at ~3.9 s. udev is not running yet.
2. The built-in `usb-storage` driver claims each disk interface.
3. After its 1 s settle delay (`delay_use=1`), the driver SCSI-scans the disk: INQUIRY,
   READ CAPACITY, then READ(10) of sector 0 for the partition table.
4. Sometimes the AE3 does not answer the read. The SCSI command times out, `usb-storage`
   escalates to a USB device reset, and the AE3 does not come back.

**Gate baseline cycle 2** (`gate002`, 7:50 PM) is a live capture of step 4:

```text
usb 1-1.2: SerialNumber: 0829c14000000000      (enumerated, app mode 37c5:16e3)
usb-storage 1-1.2:1.2: USB Mass Storage device detected
sd 0:0:0:0: [sda] 2048 4096-byte logical blocks
usb 1-1.2: reset high-speed USB device number 3 using dwc_otg
usb 1-1.2: device descriptor read/64, error -110
usb 1-1.2: device descriptor read/64, error -71   (x many)
usb 1-1.2: device not accepting address 3, error -71
usb 1-1.2: USB disconnect, device number 3
sda: unable to read partition table
```

After this the AE3 stays off the bus for the whole boot, and only a power cycle recovers it.

**Why in-boot resets never fail.** At runtime, Nick's udev rule `99-openmv-no-msc.rules`
unbinds `usb-storage` inside that 1 s settle delay. In every board reset of every journalled
boot, `usb-storage ... detected` is followed by **no** `sd`/`sda` line: the disk is never read.
This is why the ~750 `reset_board` resets in T3 (pool002) never wedged.

**Why the existing fix is too late at cold boot.** Nick's `field-usb-msc-off.service` runs at
~10 s, and the udev rule only fires once udev processes the coldplug. By then the kernel's
scan has already happened.

It is the same mechanism Nick diagnosed on nereus000 as **S23 bite R**:
`pi/ae3_flash/99-ae3-no-msc.rules`, which measured 6 failed reads in 43 attaches. This is the
cold-boot path that the rule cannot reach.

**pool007's flavour.** In pool007 the AE3 stayed enumerated, its control endpoint answered
(no `failed to set dtr/rts` in ~80 port opens), but its CDC never accepted a byte. The likely
cause is the same probe, aborted mid-transaction when the msc-off service unbound the driver
at ~10–15 s, leaving the board's USB stack stuck. This is **inferred, not proven**. The fix
below removes the probe entirely, so it covers both flavours if this inference is right; the
gate will show it.

**Hypotheses H1/H5/H6/H7/H8:**
- No under-voltage in any boot (`throttled 0x0`).
- No kernel errors before the disk probe.
- The boards really lose power across a UPS cycle. Board uptime at first contact was
  32.6 s, against a Pi uptime of 28 s; the board starts ~4.6 s before the Pi kernel.
- The board app starts in ~1 s, and its imports take 71 ms.
- The wedge has appeared both on battery (pool007) and on the charger (gate002 c2).
- **H2 (host disk probing) is the cause.**

### The rule "never USB-reset the AE3"

- Source: Nick's DEV_LOG (S3, Aug) and rig PR #69. Each time, a board that had **already
  crashed** was USB-de-authorized or `usbreset`, then failed enumeration with `-71`.
- Tested 7:35 PM: `USBDEVFS_RESET` on a **healthy** AE3, **5/5 OK**. It answered again
  1.11–1.15 s later, with no MCU reboot.
- So the rule is true only for a board that is already dead, which needs a power cycle anyway.
  The USB reset stays in the recovery ladder (L3) for the pool007 flavour.

### Fix (needs root: Nick)

Add `usb-storage.quirks=37c5:16e3:i,37c5:1206:i` to `/boot/firmware/cmdline.txt` (backup
`cmdline.txt.bak-20261014`). With flag `i` (IGNORE_DEVICE), `usb-storage` never claims either
board, so no disk probe ever reaches them, at cold boot or at runtime. Neither board is ever
used as a disk on this rig: file transfer is serial only, and Nick's own rule already unbinds
them.

- `usb-storage` is built into this kernel (`/sys/module/usb_storage` exists; it does not
  appear in `lsmod`). That is why this has to be a kernel command-line parameter and cannot be
  a modprobe option.
- **Undo:** restore the backup and power-cycle.

### Hub per-port power (Nick's question, 10/14 ~7:50 PM)

- The FE1.1s hub (`1a40:0101`) reports power switching: the kernel exposes
  `pm_qos_no_power_off` on all 4 ports.
- Ganged vs per-port, and whether VBUS is really switched, needs root (`sudo uhubctl`).
- It is moot while the disk-probe fix holds: that fix removes the trigger instead of
  recovering from it.

## Phase 1 data

| run | cycle | power | AE3 at first contact | note |
|---|---|---|---|---|
| gate002 baseline | 1 | charger | ok 0.10 s | still black: dark bench (needs a lamp) |
| gate002 baseline | 2 | charger | **absent**: off the bus (-110/-71 after the disk scan) | the trigger caught live |

Historical clean boots: 1 wedge (pool007) in 5.

## Simultaneous stills (Nick, 10/14 ~7:40 PM)

- **Before (sequential):** the stills were 19.8–31.4 s apart across 3 sets (gate001 c1,
  gate002 c1/c2).
- **After:** `experiment --sync`, first live set 8:11 PM, IMX708 + N6 (AE3 was off the bus):
  - frame-timestamp skew **7 ms** (still) and **1 ms** (RAW);
  - IMX708 fired first, boards 200 ms later.
- `rpicam-still --signal` latency, signal to `SensorTimestamp`: 186–231 ms (n=7).
- N6 fire to snapshot: 9 ms.
- **Not yet verified against a visible clock.** The board timestamps are when the snapshot
  returned, not exposure start, and a board may hand back a buffered frame.
