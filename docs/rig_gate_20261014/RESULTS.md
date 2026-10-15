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

## Phase 2: the 25× start/stop gate (2026-10-15)

**Build:** rig #115 at `dcac9e0`.
- Host files and board firmware were sha256-checked against the repo; the run manifest records
  `dcac9e0`.
- The boot fix was active on every boot (`msc_quirk = True`).

**Cycle:** the UPS cuts power to the Pi and the boards, then wakes after 1 min. Each cycle:
- cold boot;
- first-contact probe of both boards;
- one synchronized set (still + RAW, all three cameras);
- still verification (sha256, size, full decode at the expected size);
- a 10 s synchronized video clip on all three cameras;
- clean poweroff.

Bars: PREREG §4 + A1–A8.

**Light.** Daylight in the room, so no cycle was VOID (A5):

| camera | still mean grey (0–255) | still bytes |
|---|---|---|
| IMX708 | 101–116 | 1.43–1.55 MB |
| N6 | 84–96 | 490–530 kB |
| AE3 | 73–91 | 232–256 kB |

**Power.** On the charger throughout. UPS log: VIN 4.94–4.96 V, VBAT 3.606–3.611 V. The gate's
own UPS read came back empty at every boot (I²C read under the lock), a logging gap. The UPS
cycle log has the values.

### Run 1: gate007, 8:49–9:59 AM. **PASSED: 25 PASS in a row (25/25, 0 FAIL, 0 VOID)**

| measure (n = 25) | result |
|---|---|
| previous shutdown | clean 25/25 (gate timer) |
| boards power-cycled | 25/25. AE3 uptime at first contact 34–39 s vs Pi ~28 s (A1) |
| AE3 / N6 first contact | ok 25/25 / ok 25/25. The recovery ladder was never needed |
| AE3 enumeration (kernel) | 3.3–3.9 s; tty bound 27–32 s uptime |
| last verified still (Pi uptime) | median 59.7 s, p90 63.4 s, max 84.2 s (bar 180 s) |
| synchronized set | median 27.7 s (max 52.6 s, cycles 5–6, see RAW below) |
| still skew (timestamps) | median 91 ms, p90 110 ms, max 131 ms. The AE3 is the late camera (e.g. offsets IMX 0 / N6 29 / AE3 131 ms) |
| RAW skew | median 50 ms, max 62 ms |
| RAWs | 3/3 in 23 cycles. In cycles 5–6 the N6 RAW lost 239 / 1262 bytes (`timed out reading 1024000 bytes`), see below |
| video | ok 25/25. Start skew median 14 ms, p90 38 ms, max 55 ms. Board frames lost: 0. fps IMX708 ~27.5, N6 10.0, AE3 ~9.6 |
| **A8 audit** (`host_tools/gate_audit.py`, Mac) | **25/25 clips decode fully**, 301–404 frames, decoded = metadata records. Audited streak 25 |

**The N6 RAW losses (cycles 5–6) were caused by the test harness, not the rig.**
- My incremental rsync of finished clips to the Mac (1.5 MB/s cap) loaded the Pi during those
  two cycles. The N6, a full-speed USB device, dropped bytes on its 1 MB RAW stream when the Pi
  read slowly. This is the same mechanism as the uncapped video soak.
- Pulls stopped at 9:10. 0 losses in the 19 cycles after.
- RAWs are not gated; stills are, and the N6 still verified in both cycles.
- **Friday rule:** never pull or preview while the loop runs (the dive card says so).

### Still skew across both runs (Friday step-3 bar: synced stills ≤ 100 ms)

From the per-shot `sync.json` frame timestamps, gate007 + gate008 cycles 1–28, n = 53 sets:

| shot | median | p95 | max | over 100 ms |
|---|---|---|---|---|
| **still** (JPEG; boards' ISP) | 92 ms | 124 ms | 164 ms | **18/53 (34 %)** |
| **RAW** (IMX708 DNG + JPEG, board Bayer) | 46 ms | 60 ms | 64 ms | **0/53** |

**Cause, named from the data.** The AE3 is the last camera in 49/53 still shots. Two fixed
mechanisms add up:

1. **The AE3 takes one frame after its fire.** Its fire-to-snapshot is 66–67 ms on every shot
   (its sensor runs ~15 fps at HD), while the N6's is 8 ms. That alone puts the AE3 ~58 ms behind
   the N6.
2. **The IMX708's still is quantised to its still-mode frames.** `rpicam-still --signal`
   switches from the binned preview to the full-resolution still mode (14.35 fps, 70 ms frames),
   and its frame lands 190 ms to ~370 ms after SIGUSR1 (1–3 frames). The 164 ms outlier is the
   IMX708 two frames late.

The boards are fired at a fixed 200 ms after the IMX708, so the skew is the spread of these
three.

**Friday.**
- **Calibration poses use RAW-only shots:** 0/53 over 100 ms, so the bar is met.
- **The stills block (still + RAW per set):** the RAW shot meets it; the JPEG still shot misses it
  in ~1 in 3.
- Proposal: **report-only for Friday**. No code change (freeze). `sync.json` records every shot's
  skew, so analysis can keep the shots ≤ 100 ms (~2/3 of still shots and every RAW shot).
- For static scenes (the card and board held still) 0.16 s does not change the image. That is
  far inside Nick's original "±1 s" concern.
- **Fix after Friday:**
  - fire the AE3 ~60 ms before the N6 (per-board lead, a config value);
  - re-calibrate the IMX708 lead;
  - check against a visible ms clock. That is still open: the skews here are timestamp-based.

### Run 2 (repeat): gate008, from 9:59 AM

Armed automatically when run 1 passed (A6). The STOP at cycle 1 was the planned pause for the
audit and switch cycles (A9). Results: _filled when it completes_.

### Nick's real switch cycles (A9)

_Filled after the run (planned ~11:25 AM, on battery)._

## Simultaneous stills (Nick, 10/14 ~7:40 PM)

- **Before (sequential):** the stills were 19.8–31.4 s apart across 3 sets (gate001 c1,
  gate002 c1/c2).
- **After (`experiment --sync`):**
  - The boards arm (warm-up, autos live) and fire on one byte.
  - The IMX708 runs `rpicam-still --signal` and is fired first. The boards fire 200 ms later,
    because the IMX708 takes 186–231 ms from SIGUSR1 to its frame (mode switch, n=7).
  - Per-shot skew comes from frame timestamps (IMX `SensorTimestamp`, board snapshot ticks) and
    is written to `sync.json`.
  - A synchronized set takes **26–30 s**, against 45–50 s sequential.

Measured (dark bench):

| run | still skew ms | RAW skew ms |
|---|---|---|
| 8:11 PM (IMX + N6) | 7 | 1 |
| 8:15 PM (all three) | 59 | 15.5 |
| gate003 c1–c4 (cold boots, all three) | 66 / 82.5 / 62 / 58 | 17 / 12 / 13 / 23 |

- The still skew is dominated by the AE3's frame period: 66 ms at HD in the dark, against the
  N6's 8 ms.
- **Not yet verified against a visible clock**: the board timestamps mark when the snapshot
  returned. **Needs Nick:** a lamp and a running ms clock in view.

## Synchronized video (Phase 3c path, built now so the gate tests the Friday build)

`scripts/sync_video.py`: one command, all three cameras, every frame on the Pi monotonic clock.

- **IMX708:** `rpicam-vid` hardware H.264, 1080p30, records from the arm with per-frame
  `--metadata`. Its synchronized start is the first frame at or after the fire.
  - Pause/resume was rejected: `rpicam-vid` resumes only at the next keyframe. In the test that
    gave 251 of ~300 frames, so the IMX708 could start up to ~2 s late.
- **Boards:** `start_stream` with the trigger, MJPEG over USB, 10 fps each. Each frame header
  carries board ticks.
- **Host:** the reader thread never touches the SD card; a writer thread does the writes. The
  reader resyncs on lost bytes and counts them.

**CMA** (ci-record hard-reset this Pi twice on 10/13; one of those boots, `ccc3c914`, ends with
no kernel message at all):

| rpicam-vid 1080p H.264, 6 s, n=1 each | min CmaFree | frames |
|---|---|---|
| default buffers | 0 MB | 167 |
| `--buffer-count 4` (chosen) | 16 MB | 158 |
| `--buffer-count 3` | 21 MB | 130 (drops) |
| 1536×864 | 23 MB | 172 (likely a cropped FOV, not used) |

Under the full 3-camera load, CmaFree also reads 0 MB at times. That number counts reclaimable
page cache from the video writes, so the stability runs below are the real evidence.

**Runs:**

| run | IMX708 | N6 | AE3 | start skew | Pi |
|---|---|---|---|---|---|
| 10 s (pause mode, rejected) | 251 fr | 653 fr, 63 fps | 104 fr, 10 fps | 17.8 ms* | ok |
| 10 s ×2 (30 fps boards) | 266/272 fr, ~27 fps | 303 fr, 30 fps, 0 lost | 101 fr, 10 fps, 0 lost | 11.5 / 18.5 ms | ok |
| **5 min, boards uncapped then 30 fps** | 8223 fr, 27.4 fps | **FAIL**: byte loss (57 resyncs), silent at ~85 s | 3033 fr, 0 lost | — | ok, 58.5 °C |
| **5 min, boards 10 fps + decoupled writer** | 8087 fr, 27.0 fps | 3001 fr, 10.0 fps, **0 lost** | 3000 fr, 10.0 fps, **0 lost** | **11.3 ms** | ok, 59.6 °C, no throttle |

\* pause mode: the IMX708 time was the fire, not its real first frame.

**N6 USB limit.**
- The N6 is a full-speed (12 Mbit/s) device on the hub.
- At 30–63 fps VGA (~0.4–0.8 MB/s) it drops bytes whenever the Pi's reader stalls. It is
  MicroPython's CDC send timeout.
- The same mechanism explains a still transfer that came up 3.4 KB short in an early trigger
  test. sha256 catches it for stills.
- Friday video: both boards at 10 fps (the AE3's VGA ceiling anyway).

**Ground truth.** `host_tools/sync_flash.py` finds a light step (room light, torch) in every
camera's frame series and reports each camera's step time on the Pi clock. Its resolution is
about one frame: 33 ms on the IMX708, 100 ms on the boards at 10 fps. **Needs Nick:** switch a
light on/off in view during one bench run.
