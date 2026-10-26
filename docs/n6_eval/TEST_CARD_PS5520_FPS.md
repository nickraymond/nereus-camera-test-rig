# Test card: PS5520 max frame rate at 1280×960, 1920×1080, 2560×1440, 2592×1944 (Nick, chat 10/24: "Please run these videos speed tests, let's see the max fps can do on these shots.")

Pre-registered before it runs. Same unit and conditions as TEST_CARD_PS5520_BENCHES (nereus001 + spare N6, PS5520, v5.0.1 + tag25h9,
stock lens unfocused, V3 card c1 at ~0.5 m, indoor light, auto exposure).

- **Question:** at each of Nick's four sizes, how many frames per second can the N6 + PS5520 produce?
- **Why a probe and not the rig stream:** the rig service only allows QVGA / VGA / 960×600 / HD (`openmv/n6/board_config.py`). Adding sizes
  is a board-file change. So this card measures **on the board**, with `openmv/probes/n6_bigsize_fps_probe_v5.py` run by mpremote. Nothing is written
  to the board. USB-delivered fps at these sizes is **not** measured here.
- **Rows** (1280×720 added as the reference to today's streams), each size:
  - RGB565 (full array, ISP crop to aspect + scale): capture-only fps over 30 frames; capture + JPEG q70 fps + mean bytes over 15 frames.
  - BAYER (the sensor's own mode for that size, no ISP scaling): capture-only fps over 30 frames.
- **Reported:** fps per row, JPEG bytes, exposure and framerate setting (a long exposure caps fps), any error (MemoryError, "Sensor control
  failed"). n = 1 run per row: a map, not a recommendation. The driver caps at 30 fps (`PS5520_FPS_MAX`).
- **Estimate (labelled as one):** the USB rate a size would need = JPEG bytes × fps, against bench 3's measured burst (200 HD frames,
  ~13.6 MB in ~1.7 s ≈ 8 MB/s host-side).
- **Stop rules:** the N6 drops off USB → stop, tell Nick (replug). No board file writes, no firmware change, no lightsleep, nothing on nereus002.
- **After:** `mpremote reset`, `board_hashes` ALL MATCH, rig service still captures.
- **Cost:** ~3 min on nereus001; nothing from Nick.
