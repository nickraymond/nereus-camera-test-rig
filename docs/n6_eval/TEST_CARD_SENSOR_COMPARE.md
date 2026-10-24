# Test card: PS5520 vs PAG7936 HD still comparison (Nick, chat 10/24 12:28 PM: "I want to see a comparison on this vs the 1MP HD image")

**Question:** with the same lens, card, rig position and capture command, how does a PS5520 HD still compare with the PAG7936 HD still?
A visual comparison plus a few numbers. No recommendation comes from it (n = 3 per sensor, one scene).

## Fixed (unchanged since the PAG7936 baseline, 11:47 AM)
- Spare N6 020023000450433547373200, OpenMV v5.0.1 + tag25h9, rig service files = feat/n6-eval @ 761afdf.
- Stock M12 lens, V3 card c1 (ids 0–3) at ~0.5 m, rig and card not moved (Nick swapped only the sensor module).
- Command: nereus001 `/tmp/n6_snap.py` = `OpenMvUsbCamera.capture_image`, settings `{"framesize": "HD", "jpeg_quality": 90}`, auto 3A.

## The one change
Sensor module PAG7936 (id 0x7936) → PS5520 (id 0x5520, read 12:28 PM, `modes_probe.txt`).
Known difference: "HD" on the PS5520 is **1280×720** (16:9); on the PAG7936 it was 1280×800 (16:10). Whether the PS5520's HD is binned,
scaled or cropped from its full array is unknown; the tag size in pixels shows the field-of-view ratio.

## Steps (logged to `~/NereusData/nereus-camera-test-rig/2026-10-24_sensor_compare/ps5520/`)
1. 3 stills, same command as the baseline → `ps5520/hd_q90_{1,2,3}.jpg` + the capture result (exposure, gain, size, sha256).
2. `mpremote reset` is not needed (the service runs); check `get_device_info` answers after.
3. On the Mac, both sets: OpenCV ArUco DICT_APRILTAG_25h9 (ids filtered to 0–3), mean tag edge length in px, mean luma, clipping %.
4. Before/after slider page (PAG7936 left, PS5520 right) + the numbers table.

## Report
| metric | PAG7936 (3) | PS5520 (3) |
|---|---|---|
| frame size, JPEG bytes | | |
| exposure µs, gain dB (as reported) | | |
| tags found (0–3) | | |
| tag edge px (mean) → FOV ratio | | |
| mean luma, % clipped | | |

## Stop rules
- The N6 drops off USB, or a capture fails twice: stop and tell Nick.
- No board file writes, no firmware change, no lightsleep. Nothing on nereus002.
