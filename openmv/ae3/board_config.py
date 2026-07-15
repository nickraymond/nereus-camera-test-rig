"""OpenMV AE3 board configuration — Spec §8, §12.

All AE3-specific facts live here so the shared ``capture_service`` stays board-agnostic
(CLAUDE.md §6/§36: no ``if board == "ae3"`` branching in shared code). The N6 has its own
``n6/board_config.py`` with the same shape; ``capture_service`` consumes whichever module
``main.py`` wires in as ``board_config.py``.

Every value here was **verified on real hardware** on 2026-07-15 (AE3 bring-up recon,
serial ``0829c14000000000``): MicroPython 1.25.0-preview, sysname ``alif``, machine
``OpenMV-AE3 with AE302F80F55D5AE``, sensor **PAG7936** (``sensor.get_id() == 0x7936``).
The AE3 carries the *same* PAG7936 sensor as the N6 but on a different SoC (Alif) and
firmware line, so the framesize/pixformat allowlists were re-probed on this firmware
rather than inherited from the N6 (CLAUDE.md §32).
"""

# Board / device identity (Spec §8 device_info). The authoritative hardware identity is
# the USB serial number (host-side discovery, §12); DEVICE_ID is a stable logical label
# carried in metadata.
BOARD = "ae3"
DEVICE_ID = "openmv-ae3-001"
SENSOR_NAME = "PAG7936"          # sensor.get_id() == 0x7936, verified 2026-07-15

# Framesize allowlist: wire name -> sensor module attribute. Only sizes verified to
# produce a frame on this AE3/PAG7936 firmware are offered (QQVGA, SVGA, XGA, B320X320
# and larger all raised "Sensor control failed"). Native aspect is 16:10 — QVGA yields
# 320x200, VGA 640x400, HD 1280x800 (max). Same working set as the N6, re-verified here.
FRAMESIZES = {
    "QVGA": "QVGA",   # 320x200
    "VGA": "VGA",     # 640x400
    "HD": "HD",       # 1280x800 (max)
}
DEFAULT_FRAMESIZE = "HD"

# Pixel-format allowlist: wire name -> sensor module attribute. RGB565 and GRAYSCALE
# verified; YUV422 raised "Sensor control failed" (BAYER works but is left out — the
# capture path saves JPEG and RGB565/GRAYSCALE cover the eval needs).
PIXEL_FORMATS = {
    "RGB565": "RGB565",
    "GRAYSCALE": "GRAYSCALE",
}
DEFAULT_PIXEL_FORMAT = "RGB565"

DEFAULT_JPEG_QUALITY = 90
DEFAULT_WARMUP_MS = 2000          # let auto-exposure settle before the snapshot

# Live focus stream (manual M12 lens adjustment). VGA balances detail vs. framerate over
# USB; lower quality keeps frames small so the stream stays responsive.
STREAM_DEFAULT_FRAMESIZE = "VGA"
STREAM_DEFAULT_QUALITY = 70
STREAM_MAX_SECONDS = 300          # safety cap so a dead host can't stream forever

# Physical sensor mount rotation relative to "upright", recorded as metadata so raw
# frames stay un-rotated (raw evidence, §11) and the host/analysis layer applies the
# correction. Convention: degrees to rotate the raw frame COUNTER-CLOCKWISE for an
# upright image (matches the N6 convention).
#
# UNVERIFIED for the AE3 (OQ-18): unlike the N6, the AE3's mount rotation has not been
# confirmed against a known-orientation reference capture — the bring-up recon shot was a
# ceiling scene with no reliable gravity cue. This value does NOT affect capture,
# checksums, or the Phase 4 exit criteria (it is metadata only). Left at 0 until a
# known-orientation shot is taken; do not assume it matches the N6's 90°.
MOUNT_ROTATION_DEG = 0
