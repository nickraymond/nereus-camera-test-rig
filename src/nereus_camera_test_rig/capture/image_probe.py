"""Lightweight image inspection — Spec §19 (output validation).

Reads JPEG dimensions straight from the file's SOF marker with no third-party
dependency, so capture validation ("does it open, are the dimensions right?") works
on the Pi and in host unit tests without pulling in Pillow/OpenCV. This is a
validation aid, not an image-processing library.
"""

from __future__ import annotations

from pathlib import Path

# JPEG Start-Of-Frame markers carry the image dimensions. All SOF variants
# (baseline C0, progressive C2, etc.) share the same header layout. These four
# are not SOF frames and must be skipped.
_SOF_MARKERS = {
    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
}
_SKIP_STANDALONE = {0xD8, 0xD9}  # SOI, EOI: no length field


class ImageProbeError(ValueError):
    """Raised when a file is not a parseable JPEG."""


def jpeg_dimensions(path: str | Path) -> tuple[int, int]:
    """Return ``(width, height)`` of a JPEG by scanning its markers.

    Raises ``ImageProbeError`` if the file is missing, not a JPEG, or has no SOF
    segment (which would mean a truncated/corrupt capture).
    """
    p = Path(path)
    if not p.is_file():
        raise ImageProbeError(f"not a file: {p}")
    data = p.read_bytes()
    if len(data) < 4 or data[0] != 0xFF or data[1] != 0xD8:
        raise ImageProbeError(f"not a JPEG (bad SOI): {p}")

    i = 2
    n = len(data)
    while i + 1 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        # Skip fill bytes (runs of 0xFF).
        marker = data[i + 1]
        i += 2
        if marker in _SKIP_STANDALONE or 0xD0 <= marker <= 0xD7:
            continue  # standalone markers, no length/payload
        if i + 1 >= n:
            break
        seg_len = (data[i] << 8) | data[i + 1]
        if marker in _SOF_MARKERS:
            # Segment: len(2) precision(1) height(2) width(2) ...
            if i + 6 >= n:
                raise ImageProbeError(f"truncated SOF in {p}")
            height = (data[i + 3] << 8) | data[i + 4]
            width = (data[i + 5] << 8) | data[i + 6]
            return width, height
        i += seg_len  # jump past this segment's payload

    raise ImageProbeError(f"no SOF marker found (corrupt/truncated?): {p}")
