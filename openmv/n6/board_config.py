"""OpenMV N6 board configuration — Spec §8, §12.

All N6-specific facts live here so the shared ``capture_service`` stays board-agnostic
(CLAUDE.md §6/§36: no ``if board == "n6"`` branching in shared code). The AE3 (Phase 4)
gets its own ``ae3/board_config.py`` with the same shape; ``capture_service`` consumes
whichever module ``main.py`` wires in.

Every value here was **verified on real hardware** on 2026-07-14 (see PR / recon):
MicroPython 1.26.0, build OPENMV_N6, sensor PAG7936. The legacy ``sensor`` API is used
(both ``sensor`` and ``csi`` are present; ``sensor`` is stable and sufficient).
"""

# Board / device identity (Spec §8 device_info). The authoritative hardware identity
# is the USB serial number (host-side discovery, §12); DEVICE_ID is a stable logical
# label carried in metadata.
BOARD = "n6"
DEVICE_ID = "openmv-n6-001"
SENSOR_NAME = "PAG7936"          # sensor.get_id() == 0x7936, verified 2026-07-14

# Framesize allowlist: wire name -> sensor module attribute. Only sizes verified to
# produce a frame on the PAG7936 are offered (B320X320 raised "Sensor control failed").
# Native aspect is 16:10 — QVGA yields 320x200, VGA 640x400, HD 1280x800 (max).
FRAMESIZES = {
    "QVGA": "QVGA",   # 320x200
    "VGA": "VGA",     # 640x400
    "HD": "HD",       # 1280x800 (max)
}
DEFAULT_FRAMESIZE = "HD"

# Pixel-format allowlist: wire name -> sensor module attribute.
PIXEL_FORMATS = {
    "RGB565": "RGB565",
    "GRAYSCALE": "GRAYSCALE",
}
DEFAULT_PIXEL_FORMAT = "RGB565"

# Bayer RAW (``capture_raw``, csi module), measured on OpenMV v5.0.1 on ``nereus002``
# 2026-09-28 (OQ-21, ``openmv/probes/raw_probe2_v5.py``): ``csi.BAYER`` is 8 bit, 1 byte/px;
# CFA of the delivered bytes is BGGR (decided on the V1 card: patch hues within 9 deg of
# truth, RGGB 180 deg off); black level 0 (subtracted on chip, noise clipped at 0). Only HD
# was probed — whether smaller sizes bin or crop the mosaic is unknown, so they are not offered.
RAW_FRAMESIZES = {"HD": "HD"}
RAW_DEFAULT_FRAMESIZE = "HD"
RAW_CFA = "BGGR"
RAW_BITS = 8
RAW_BLACK_LEVEL = 0

DEFAULT_JPEG_QUALITY = 90
DEFAULT_WARMUP_MS = 2000          # let auto-exposure settle before the snapshot

# Live focus stream (manual M12 lens adjustment). VGA is a good balance of detail vs.
# framerate over USB; lower quality keeps frames small so the stream stays responsive.
STREAM_DEFAULT_FRAMESIZE = "VGA"
STREAM_DEFAULT_QUALITY = 70
STREAM_MAX_SECONDS = 300          # safety cap so a dead host can't stream forever

# How the board is mounted on the rig relative to "upright". Recorded as metadata so raw
# frames stay un-rotated (raw evidence, §11) and the host/analysis layer applies the
# correction. Convention: degrees to rotate the raw frame COUNTER-CLOCKWISE for an upright
# image. A property of the mount, not the board: 90 on nereus000 (verified 2026-07-14);
# **0 on nereus002** (verified 2026-09-28: the V1 card and room read upright in the raw frame).
MOUNT_ROTATION_DEG = 0
