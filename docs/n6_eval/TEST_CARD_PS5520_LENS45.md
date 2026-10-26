# Test card: PS5520 + new lens, the full suite (Nick, chat 10/24 ~9:15 PM: "I have swapped the 5MP sensor and power cycled the Pi. Please bring it up and run all the outstanding tests for this sensor")

Pre-registered before each item runs. The same suite as TEST_CARD_PAG7936_LENS45 (+ addendum) @ 1269cc8, so the two sensors on the new lens line up.
Unit: nereus001 + spare N6 020023000450433547373200, PS5520 (0x5520, read 9:18 PM), v5.0.1 + tag25h9, board service 1d6f5c7 board files
(ALL MATCH 9:18 PM). INA3221 CH1 at 10 Hz. Lens: the same 4.5 mm M12 as the PAG7936 run (Nick, chat ~9:21 PM: "Yes, it's the same 4.5 mm M12 lens."). V3 card at ~1 m in air (tape in frame, same spot as 8 PM), night indoor light (9:18 PM still: 33.4 ms; gain reads 0.0 dB,
the PS5520 readout is suspect). HD tag edge 60 px (HD = the full 2592-wide view scaled to 1280×720).

## Compared with
- the PAG7936 + 4.5 mm run (`2026-10-24_pag7936_lens45/RESULTS.md`, same card position, evening): the sensor is the main change, plus the light (later, darker);
- the PS5520 + stock unfocused lens runs (`2026-10-24_ps5520_benches/`, `_fps/`, `_usb_fps/`): lens, distance (0.5 → 1 m) and light changed.
mW raw INA3221, **not validated**. n = 2 per cell (3 for RAM vs USB): a map, not a recommendation.

## Items (unchanged scripts)
1. Bench 1 speed map, 2 reps (`openmv/probes/n6_apriltag_probe_v5.py`).
2. Power DOE (`--plan doe`), 3. bench 2 (`--plan bench2`), 4. bench 3 (`--plan bench3 --reps 3`).
5. **Large sizes over USB** (`--plan bigsizes --reps 2`: HD + 1280×960, 1920×1080, 2560×1440, 2592×1944), the PS5520-only item.
6. Deep sleep `official`, `csi_sleep`, `csi_shutdown`, one each. **No lightsleep.**
7. Clips: {HD, 960x600, VGA} × {30, 15 fps} × 5 s, q70 (TEST_CARD_PAG7936_LENS45_CLIPS commands), MP4s made on the Mac.
8. Stills at every PS5520 size (QVGA … 2560x1440 at q90; 2592x1944 at **q80**, as q90 fails on the board, 6:16 PM).
9. Sharpness: 10 HD q90 stills **and 10 full 2592×1944 q80 stills**, `scripts/n6_edge_sharpness.py` (px and mm on the card; frame centre only; JPEG in the loop).
10. On-board fps probe (`n6_bigsize_fps_probe_v5.py` @ 1269cc8; the PS5520 sizes).

## Validity + stop rules (as before)
- A cell is void unless rc 0, ≥ 150 power rows, 0 read errors; lost frames reported.
- The N6 drops off USB twice, or once without coming back in 60 s → stop the chain. A deep-sleep run not back in 60 s → stop. No board writes, nothing on nereus002.
- Cost: ~55 min unattended.
