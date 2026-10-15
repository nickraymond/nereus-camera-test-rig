# nereus002: AE3 boot wedge + 25× start/stop gate. Pre-registration (2026-10-14, 7:40 PM PT)

Written before the bench cycles. Results go in `RESULTS.md` next to this file. Changes to the
bars below after the first cycle are recorded as dated amendments, never silent edits.

## 1. The failure (pool007, Wed 10/14 12:41–3:05 PM)

- AE3 `0829c14000000000` failed every set, 0/110. First contact at uptime 41.7 s; the
  first host write (`reset_board`) timed out: `write failed: Write timeout` (10 s).
- Journal (boot `9b09a1d3`): the AE3 enumerated normally at 3.9 s as `37c5:16e3` (app mode).
  It **never reset or re-enumerated** in the whole 2.5 h boot: 0 disconnects, so it never
  received a single command. The N6 (same hub) reset 100+ times normally.
- **No `failed to set dtr/rts`** in ~80 port opens: the AE3's control endpoint (EP0) answered
  all run. Only the CDC OUT data path never accepted bytes. Its USB stack was alive and the
  serial path was stuck.
- No kernel USB errors (-71/-110), no under-voltage (`throttled 0x0`), VOUT 4.96 V.
- dive-autostart stood down at that boot (`~/.no_dive_autostart`), so ci-record never ran.
  The wedge happened with **no host traffic to the AE3 at all** before the first write.

Boot history (journal, all clean pool-loop boots since the 10/13 hub swap):

| boot | when | power | AE3 at first contact |
|---|---|---|---|
| 1bbf3ce4 | Tue 3:24 PM (UPS cycle) | charger | OK (then T3 187/188 over 754 resets) |
| 9f613850 | Wed 9:10 AM | charger | OK |
| e0ff111a | Wed 9:57 AM | **battery** (charger plugged at ~40 s) | OK |
| **9b09a1d3** | **Wed 12:40 PM (pool)** | **battery** | **WEDGED** |
| 641fc07f | Wed 4:57 PM | charger | OK |

So: 1 wedge in 5 clean cold boots; 0 wedges after ~750 in-boot `reset_board` resets (T3).
**The wedge is cold-boot specific.**

## 2. "The AE3 must never be USB-reset": source and status

- Source: Nick's DEV_LOG (S3, 8/10–8/20) and rig PR #69 (9/28). In each case an AE3 that had
  **already crashed** (it refused the REPL, or `meas framebuffers`/`capture 50 vga` killed it)
  was USB-de-authorized or `usbreset`, and it then failed enumeration with `error -71` until
  a physical replug or power cycle.
- Measured tonight (7:35 PM): `USBDEVFS_RESET` on a **healthy** AE3, **5/5 OK**. It was back
  answering `get_device_info` in 1.11–1.15 s, and the MCU did not reboot (same service).
- So the rule is true only for a deep-crashed board, which needs a power cycle anyway. It
  does not forbid a USB reset as a recovery step on a board whose EP0 still answers (the
  pool007 flavour). `pi` can do this without root (`plugdev` rw on OpenMV nodes, Nick's udev rule).

## 3. Hypotheses (pre-registered) and the discriminating observation for each

Every gate boot runs a **first-contact probe** on each board before any capture:
1. read pending IN bytes for 1 s;
2. write `get_device_info` (3 s write timeout);
3. read the reply (5 s).

If the AE3 does not answer, a fixed **diagnostic ladder** runs, logging each step:

- L1: DTR off/on. Does EP0 answer?
- L2: close, reopen and retry.
- L3: `USBDEVFS_RESET`, then retry.
- L4: after L3, if a write succeeds but there's no reply, send Ctrl-C and read 2 s. A REPL
  traceback means the VM was alive but stuck in Python; silence means stuck in C. Then
  `machine.reset()` via the REPL.

| # | Hypothesis | Predicts | Test |
|---|---|---|---|
| H1 | Power-up inrush/brownout on the UPS boot path (battery) | wedges cluster on battery boots; throttled bits / VOUT dip | A/B: N cold boots on charger vs N on battery (Nick unplugs the charger) |
| H2 | Host probing of the board's USB disk (kernel partition read ~4.9 s, udev blkid, mtp-probe) races the board app start | wedge regardless of power; also after resets | already weak: ~750 resets with the same probing, 0 wedges. Data only. |
| H3 | Board app not running or stuck: safe-mode REPL, import-time hang (`import sensor` / CSI init at a cold sensor), VM stuck in C | L1 OK, L3 makes writes succeed but no reply; L4 silent (C) or traceback (Python) | ladder L3/L4; board `boot_ms`/import timings on healthy boots |
| H4 | USB CDC buffer/state wedge (OUT endpoint never armed / FIFO state) | L1 OK, **L3 alone cures it** (reply after the USB reset) | ladder L3 |
| H5 | Hub port power / pogo-pin contact | kernel -71/-110/disconnects on 1-1.2, over-current | journal per boot (none in pool007) |
| H6 | Cable | same as H5 | last resort only: the hardware is in the enclosure (Nick, 10/14) |
| H7 | The board kept power across the "off" (state carried over) | board uptime at first contact ≫ Pi uptime | `ticks_ms` added to `get_device_info` |
| H8 | Cold-boot ordering: the board app polls the CDC before the host configures it (~3.9 s), unlike a `reset_board` where the host is already up | wedge only on cold boots (fits §1); cured by L3 (re-enumeration with the app already running) | ladder L3 |

## 4. The gate (Nick's bar, non-negotiable)

**One cycle** = UPS power-on → Pi boot → all three cameras captured and verified → clean
shutdown → power cut. Automated by the UPS wake timer (`WAKE_TIME 1`, `PI_RUNNING 0`, the
`rig-ups-power-cycle` mechanism), plus **at least 3 real switch cycles by Nick**.

**PASS (all must hold, in that boot, with no human touch and no extra UPS cycle):**
- IMX708, N6, AE3 each produce a still that verifies:
  - the sha256 recomputed on the Pi equals the recorded one;
  - the size is plausible: IMX708 JPEG ≥ 300 kB, board JPEG ≥ 30 kB;
  - it decodes (PIL) at the expected size: IMX708 4608×2592, boards 1280×800.
- The last verified still is within **180 s** of kernel start.
- In-boot automatic recovery (the ladder) is allowed and is logged. A UPS re-cycle is not.
- The **previous** boot ended cleanly:
  - its journal shows `systemd-shutdown`/poweroff;
  - the boards really lost power: board `ticks_ms` at first contact < Pi uptime.

**FAIL** = anything else. A failure resets the streak to 0. **Bar: 25 PASS in a row, then a
second 25 in a row** (repeatability). 25/25 bounds the per-start failure rate below 12 %
(95 %, rule of three); 50/50 bounds it below 6 %.

Logged per cycle (CSV + JSON, `results/gate/<run>/`):
- boot id; uptime at each step;
- per camera: enumerate/ready/capture times, size, sha;
- VIN/VBAT/VOUT and throttled;
- ladder steps; errors; the previous shutdown's kind.

## 5. Simultaneity (Nick, 10/14 evening)

The three stills must show the same moment: the point is comparing sensors on one scene. Today
the coordinator captures **sequentially** (IMX708 → N6 → AE3, each with ~2 s warm-up plus
transfer), so the stills are several seconds apart.

- **Measure first:** the inter-camera skew of the current path, from the capture timestamps.
- **Then fix:** warm up all three in parallel and fire on one trigger.
- **Bar (proposed):** skew ≤ 100 ms between any two cameras' exposures. Verified with a visible
  millisecond clock in view of all three. Skew is reported per gate cycle.

## Amendments (dated; made before any gate cycle on the fixed configuration)

**A1 (2026-10-14 9:15 PM, QE #115): "the boards really lost power".**
- The bar is board `ticks_ms` at first contact ≤ the Pi uptime at that same moment **+ 30 s**,
  not "< Pi uptime".
- Why: the board powers up with the UPS output, ~4–5 s before the Pi kernel starts counting
  uptime. Measured: board 32.6 s vs Pi 28 s.
- A board that kept power would read the previous boot's uptime plus the off time (≥ 2 min),
  so 30 s still separates the two cases cleanly.

**A2 (same time): video is part of every gate cycle.** Nick's video requirement, "in the gate's
spirit": the 25× gate runs synchronized stills and then a 10 s synchronized clip on all three
cameras. A cycle also FAILS if the clip:
- did not record on every camera;
- has a start skew > 100 ms (timestamp-based);
- lost any board frame.

This is stricter than the "5 clean video starts/stops" in the brief.

**A3 (same time): the 180 s start bar uses the monotonic clock.**
- "The last verified still within 180 s of kernel start" is now the Pi uptime when the capture
  set returned, an upper bound, since every still is written before the set returns.
- The first version mixed file mtimes (wall clock) with uptime. The wall clock can step when NTP
  syncs mid-boot.

**A4 (same time): still skew is reported, not gated, until the lamp run.**
- The proposed ≤ 100 ms still bar (§5) is measured per cycle (`sync.json`).
- It is not a FAIL condition until it has been checked against a visible clock in a lit scene.
  In the dark, the AE3's 66 ms frame period alone makes 58–102 ms.

**A5 (2026-10-14 9:50 PM, Nick: "kick this off in the early morning when the sun enters the
room"): darkness makes a cycle VOID, never a PASS.** A cycle is **VOID** only if **every**
failure is a board still that is:

- intact: its sha256 matches and it decodes at full size;
- under the size bar only because the scene is dark: the board frame's mean grey is
  < 10/255 **and** the IMX708 still's mean grey is < 40/255 (the IMX708 auto-exposes; a dim
  scene).

A VOID neither adds to nor resets the streak, and does not count toward "3 FAILs in a row".
Everything else stays a FAIL that resets to 0: a board that does not answer, a sha or decode
error, failed video, an unclean shutdown, a board that kept power, a slow start, or a
black board frame while the IMX708 sees a lit scene.

- Why: the size bar exists to catch truncated or empty files. A black AE3 JPEG of a dark room
  is 28.8 kB, just under the 30 kB bar, and is indistinguishable from a dead sensor. So the
  dark cycles test everything except that one check, and cannot count toward 25.

**A6 (same time): one armed run does both 25×.**
- After 25 PASS in a row (A5 VOIDs excluded), the harness arms a fresh run (`…-repeat`, streak
  from 0) and continues until a second 25 in a row. The cycle cap is 200 per run.
- After a VOID the next wake is 10 min (waiting for light). After a PASS or FAIL it is 1 min.
- The rig is armed tonight, so the overnight dark cycles (VOID) are extra post-fix cold-boot
  evidence. The counted gate starts by itself when sunlight reaches the scene.
- The ≥ 3 real switch cycles (Nick) are done in the morning by switching the run to
  `mode switch` for 3 cycles.

**A7 (2026-10-14 10:05 PM, after gate006 cycle 1): video is judged by its files, and
`rpicam-vid` exits by its own timer.**
- gate006 c1 FAILED its video: `rpicam-vid` segfaulted at teardown (rc −11) after the SIGINT
  stop. It left the H.264 written (12.8 MB) but its per-frame metadata cut off mid-record, so 0
  frames were parsed. In ~15 earlier clips with the same stop it exited 0.
- Changes:
  - `rpicam-vid` now runs with `-t` = warm-up + clip + 1.5 s and exits by its own timer, its
    normal path. It is started after the boards are armed.
  - The truncated metadata is repaired: every complete record is kept.
  - The IMX708 clip passes on its artifacts: a non-empty H.264 and ≥ 2/3 of the nominal frames
    (30 fps) inside the synchronized window.
  - A non-zero exit is logged as a warning in `video_sync.json`.
- The other video FAIL conditions (A2) are unchanged.
- gate006 is stopped. Its c1 FAIL stays in the record. The gate restarts on the fixed build
  (new run, streak 0).
