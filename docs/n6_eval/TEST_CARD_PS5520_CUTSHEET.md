# Test card: cut sheet, PS5520 at every allowed size vs the PAG7936 frames on hand (Nick, chat 10/24 ~6:10 PM: "show me a cut sheet comparing both sensors and their different resolution settings for the same frame")

Pre-registered before it runs. A visual comparison, n = 1 still per size: no recommendation comes from it.

- **PS5520 (mounted now):** one rig-service still per size in the N6 allowlist (fda23ad): QVGA, VGA, 960x600, HD, 1280x960, 1920x1080, 2560x1440,
  2592x1944. JPEG q90, auto 3A, warm-up 2 s, same card and rig position as the 12:30 PM PS5520 stills. Lens still unfocused.
  Command: `OpenMvUsbCamera.capture_image` (as `/tmp/n6_snap.py`) with `framesize` per size. Every file checked: opens, size matches, sha256.
- **PAG7936 (not mounted):** only frames already captured. HD 1280×800 q90 still (`2026-10-24_sensor_compare/pag7936/hd_q90_1.jpg`, 11:47 AM),
  plus q70 stream frames: HD from bench 2 (`power_bench2_20261024T151756Z`, 10/24 8:17 AM) and VGA 640×400 from the DOE (`power_doe_20261024T033219Z`,
  10/23 ~8:3x PM). Their light and quality differ and the sheet says so. Other PAG7936 sizes need the module swapped back (Nick's hands), so they are not on this sheet.
- **Sheet:** per sensor and size, the whole frame, plus the card at that frame's own pixels (nearest-neighbour, same display size). Card position from
  25h9 tag detection on the Mac (ids 0–3). Where tags are not found, the box is mapped from the full 2592×1944 frame with the ISP crop rule
  (`ports/stm32/stm_isp.c:242-260`), and the sheet marks it as mapped. The mapping is checked against the sizes where tags were found.
- **Also on the page:** stream speed + power reference tables for both sensors, copied from the run RESULTS files (sources named per table).
- Stop rules: a capture fails twice, or the N6 drops off USB → stop, tell Nick. No board writes beyond the service's own /flash capture + delete.
- Cost: ~2 min on nereus001.
