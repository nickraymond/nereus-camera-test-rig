# Test card: PAG7936 vs PS5520, same light, RAW Bayer (Nick, chat 10/25 ~9:00 AM: "Start with the same-light RAW (Bayer) comparison of the PAG7936 and PS5520. Write the test card first."; "I should fine tune the lens on each sensor before we run the test")

Written Sun 10/25 9:04 AM PT by N6 eval builder 3, before anything runs. Feeds SPEC_n6_eval must do 13 / 14, and leg B also does must do 1 for the PAG7936 (`docs/specs/n6_eval.md`).

**Question:** under the same light, with each sensor freshly focused, the same lens and card position and the ISP out of the loop,
is the PAG7936 still sharper on the card than the PS5520, and how do the two compare on noise and sensitivity?
Background: on the camera JPEGs the PAG7936 at HD gave a 1.79 mm edge on the card vs 2.08 mm for the PS5520 at 5 MP (10/24,
`2026-10-24_ps5520_lens45/RESULTS.md`), but the light (16.6 vs 33.4 ms), JPEG quality (q90 vs q80) and the ISP all differed.
One scene, one card position (frame centre only; corners are the separate corner-sharpness item). **No recommendation from it**: it is evidence for must do 13.

## Fixed
- nereus001 + spare N6 020023000450433547373200, OpenMV v5.0.1 + tag25h9 (v5.0.1-2.gbe78cb31ce), board service files as deployed
  (1d6f5c7 board files, ALL MATCH 10/24 9:57 PM; re-checked with `board_hashes` before leg 1). **No board writes, no deploy, no register writes.**
- The same 4.5 mm M12 lens on both sensors, V3 card c1 (tags 0–3) at ~1 m in air, rig and card not moved between legs.
- **Light:** Nick's LED lamp(s) at one fixed setting (brightness, colour temperature, position) for every leg; daylight kept out
  (blinds closed, or room lights as Nick sets them, recorded). Nick records the lamp setting in chat before leg 1.
- Capture: rig `capture_raw` (8-bit Bayer, `csi.BAYER`, streamed from RAM, SHA-256 verified; `cameras/openmv_usb.py`), framesize `HD`,
  AWB frozen, exposure and gain locked by request (below). Each call is its own camera session (reset + warm-up), so the 10 frames are 10 sessions.
- Sensor defaults as delivered, including the PAG7936's on-chip denoise + lens shading (Nick's decision 2026-10-01, OQ-54). Whether the
  PS5520's Bayer is processed on chip is unknown; the neighbour-noise correlation below shows it.

## The one change
Sensor module: PS5520 (0x5520, mounted now) ↔ PAG7936 (0x7936). Nick refocuses the lens after every swap (his call, 10/25).
Known differences that come with the sensor and are not changed: native pixel pitch and field of view; HD Bayer size (PS5520 1280×720
from its 30 fps native mode; PAG7936 1280×800 native); minimum analogue gain (PAG7936 3.15 dB measured, S3; PS5520 1.94 dB =
gain index 4 in the driver `ps5520.c`; dB are each sensor's own scale and not comparable).

## Exposure plan (same on both sensors)
- Exposure **2, 4 and 8 ms** at each sensor's lowest gain (request `gain_db` 1.0; each driver clamps to its floor; read-back recorded).
  10 frames per exposure, plus **5 dark frames at 8 ms** with the lens covered (Nick's hand).
- Why ≤ 8 ms: `capture_raw` lengthens the frame through **PAG7936 frame-time registers** (`_set_frame_time`, 0x4C–0x4E + 0xEB) whenever
  the exposure read-back is clamped. On the PS5520 those writes would hit unknown registers. The PS5520 HD Bayer mode is a 30 fps mode
  (~33 ms frame, `ps5520.c` table `PS5520_1280x720x30fps`) and the PAG7936's HD Bayer frame is 8.25 ms (OQ-51), so 8 ms never
  triggers it. Checked per frame: `frame_time_us` must be null (stop rule).

## Steps (data to `~/NereusData/nereus-camera-test-rig/2026-10-25_raw_compare/`, one subfolder per leg)
0. **Feasibility, PS5520 (mounted now):** 1 frame at 2 ms. Check: 1280×720, SHA match, exposure read-back within 2 %, `frame_time_us` null.
   On the Mac: tag edge in px on the green plane (card fully inside the frame) tells whether the PS5520 HD Bayer mode is a **crop** (~123 px, full pixel pitch, as the
   5 MP JPEG) or **binned / skipped** (~60 px). CFA phase checked on the card's colour patches (sidecar says BGGR). If binned: stop and
   ask Nick (a full-pitch PS5520 Bayer needs a board-config change + deploy, outside this card).
1. **Leg A, PS5520:** Nick sets the lamps; I start the focus page (`host_tools.focus_stream` from the deployed
   `test/141-on-n6-eval` checkout, as `~/n6_eval/focus141_big.sh`, switched to 2592×1944); it shows the sensor id. Nick focuses on the card
   at 1:1; I record the page's sharpness readout and stop the page (`kill <pid>`). Then the exposure plan + darks.
2. **Leg B, PAG7936:** Nick swaps the sensor (and power-cycles if needed, as on 10/24), same lens. Focus page at HD, sensor id shown
   by the page (0x7936), Nick focuses, readout recorded. Exposure plan + darks.
   **Also SPEC_n6_eval must do 1 for the PAG7936** (the kill line is Sun 10/25 6 PM; only the PS5520 list exists, `2026-10-24_sensor_compare/ps5520/modes_probe.txt`):
   `openmv/probes/n6_modes_probe_v5.py` via `mpremote run` (RAM only, no board writes), exactly as for the PS5520 on 10/24 12:28 PM →
   `legB/modes_probe.txt`. Firmware is v5.0.1 + the one tag25h9 #define, as for the PS5520 list (stated in the result).
3. **Leg A′, PS5520 again (recommended; Nick's call, ~15 min of his time):** swap back, refocus, 10 frames at 4 ms only.
   This is the only check that the light and setup stayed the same across the swaps.
4. **Mac analysis** (`scripts/n6_raw_compare.py`, new, unit-tested on synthetic Bayer before use): black from the darks; one bilinear
   demosaic (`color/raw_io.demosaic_bilinear`, the same code for both sensors); card located from the tags.

## Metrics (per sensor, at the longest exposure where neither sensor clips the card: no grey patch > 250 DN)
| metric | how |
|---|---|
| edge 10–90 % rise, px and **mm on the card** | `scripts/n6_edge_sharpness.py` method on the green plane, 4 tags × 4 sides × 10 frames, median (IQR) |
| temporal noise and SNR | per-pixel std over the 10 frames on gray_light / gray_light2 / gray_mid, per CFA channel, black-subtracted |
| sensitivity | green DN per ms on gray_light at the same exposure, with each sensor's read-back gain (not comparable across sensors; stated) |
| black level, dark noise | mean and std of the 5 darks per channel |
| on-chip processing | neighbour noise correlation on gray_mid (OQ-54: PAG7936 denoise on ≈ 0.27–0.4; truly raw ≈ 0.04) |
| linearity | grey means at 2 / 4 / 8 ms vs exposure |
| visual | before/after slider: the card from each sensor at the same mm scale, same demosaic, WB on gray_mid |

## Validity (a cell is void otherwise; reported, never silently dropped)
- Every frame: SHA match, expected size, exposure read-back within 2 % of the request, `frame_time_us` null, tags 0–3 found (sensor id from the focus page per leg).
- Within a leg: gray_light mean of the first and last 4 ms frame within ±3 % (light steady).
- Across legs (only if leg A′ runs): A′ vs A gray_light mean within ±5 % and edge rise within its IQR. If not, the result is labelled
  **"light or setup changed: not a same-light comparison"** and no sensor conclusion is drawn.

## Stop rules
- Any frame with `frame_time_us` not null on the PS5520 → stop, `reset_board`, tell Nick.
- The N6 drops off USB twice, or once without coming back in 60 s → stop. Two failed captures in a row → stop.
- No board file writes, no firmware change, no sensor register writes, no lightsleep. Nothing on nereus002.

## Cost
Builder ~10 min per leg + ~1 h analysis. Nick: lamps ~5 min, focus ~5 min per leg, 1 swap (2 with leg A′), lens cover per leg: ~30–45 min hands-on.
