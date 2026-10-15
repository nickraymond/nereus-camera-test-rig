# nereus002 Friday dive card (Fri 2026-10-16). DRAFT 10/14 9 PM

Rig: Pi Zero 2 W + IMX708 + OpenMV N6 + OpenMV AE3 in one housing, LiFePO4wered/Pi+ UPS,
physical switch (= the UPS button: clean boot / clean shutdown). Code `~/nereus-rig-poolcam`.

**What's new since the pool (10/14):**

- **All three cameras shoot the same moment.** One trigger fires all three. Measured skew on
  the bench: stills ~60–100 ms in the dark, RAW ~15 ms, video starts 11–23 ms. Every frame is on
  one clock.
- **The AE3 cold-boot wedge is fixed** (pending Nick's one-line boot change) and gated with
  25 + 25 clean starts. See `docs/rig_gate_20261014/`.
- **One dive program** runs by itself from switch-on: calibration (two distances), stills,
  then video. It needs no network and no LED codes.

## The program (minutes from switch-on; set your dive watch at switch-on)

| T+ (min) | Block | What you do | What the rig does |
|---|---|---|---|
| 0 – 1 | boot | Seal, enter the water | Boots (~40 s) |
| 0 – 12 | **CAL A** (IMX708) | Start posing once you are down (~T+2). | ChArUco board at **0.7–0.9 m**. 9 places: 4 corners, 4 edge midpoints, centre; a board corner touching the frame edge each time. At each place: flat, ±30° left/right, ±30° up/down. **Hold each pose ~10 s** | One synchronized RAW shot every ~10 s, 1 stop dark: IMX708 DNG + JPEG, N6 + AE3 Bayer. `poses.csv` |
| 12 – 22 | **CAL B** (N6 / AE3) | The same board at **1.5–2.0 m**, posed for the boards' narrower view (they see ~46°; aim was top-left on 10/14) | Same shots |
| 22 – 32 | **STILLS** | Scene / reference card at your test distance | Synchronized sets (still + RAW, all three) every ~30 s |
| 32 – 38 | **VIDEO** | One clip per minute (T+32, 33, … 37). **At about :15 past each minute, flash a torch once in view of all three**, then the scene | 6 synchronized 30 s clips (each starts ~5 s after the minute): IMX708 H.264 1080p30, N6 + AE3 MJPEG VGA 10 fps, one clock |
| 38 → | stills | Anything | Synchronized sets until switch-off or low battery |

Shots taken before you are in position simply have no board in them; the pipeline skips them.

- **Full-frame coverage:** there is no live preview underwater (no Wi-Fi through water).
  - **Before the dive**, in air at the pool edge: rehearse the 9 places on the full-frame preview
    pages (`focus_stream` :8081 AE3 / :8082 N6, `focus_stream --imx708` :8083). **Only with the
    loop stopped** (`pool_mode.sh stop`): a preview holds the camera and every set fails while it runs. Underwater the view is
    ~1.33× narrower (flat port), so place the board a little closer to centre than it looked in
    air.
  - **After the dive:** pull a subset and run the calibration pipeline's `coverage.png` before
    leaving the pool. A redo is cheap: switch off/on to restart the program.

## Before leaving for the pool (bench)

- [ ] Boot fix present: `cat /boot/firmware/cmdline.txt` contains
      `usb-storage.quirks=37c5:16e3:i,37c5:1206:i`.
- [ ] Gate passed (25 + 25), with results in `docs/rig_gate_20261014/RESULTS.md`.
- [ ] Charged: VBAT ≥ 3.4 V on the charger.
- [ ] Gate disarmed first (`python scripts/gate_cycle.py disarm`; the gate and pool mode must never both be armed).
- [ ] Armed with the program: `pool_mode.sh arm --program cal_a:12,cal_b:10,stills:10,video:6x30`
      (exact args in the final card).
- [ ] Switched **OFF**.

## At the pool

1. **Switch ON**, start the dive watch.
2. Wait ~1 min.
3. Seal and dive.
4. **Stop:** switch OFF. The set in flight may be lost; everything before it is on disk.
5. **Pull everything before you power it for anything else** (Nick's rule, 10/14):
   `python -m host_tools.pool_pull …` (final command in the final card).

## Open items (to close before Friday)

- Nick: the boot fix (one `sudo` line, in chat), a lamp + a ms clock for the skew check, and a
  light switched on/off during one video run (ground truth).
- The calibration session's `.bayer` reader on development (Bayer for the boards).
- Block lengths and video clip count/length: these are defaults. Tell me your dive time.
