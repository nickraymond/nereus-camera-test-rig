"""Shared test helpers for capture-adapter unit tests (not a test module)."""

from __future__ import annotations

import struct


def make_jpeg(width: int, height: int) -> bytes:
    """Build a minimal valid-enough JPEG (SOI + SOF0 + EOI) for probe/validation tests."""
    sof = b"\xff\xc0" + struct.pack(">H", 17) + b"\x08"
    sof += struct.pack(">H", height) + struct.pack(">H", width)
    sof += b"\x03" + b"\x01\x11\x00\x02\x11\x00\x03\x11\x00"  # 3 component specs
    return b"\xff\xd8" + b"\xff\xe0\x00\x04AB" + sof + b"\xff\xd9"


class Completed:
    """Stand-in for subprocess.CompletedProcess."""

    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def arg_value(cmd, flag):
    """Return the value following ``flag`` in a command list, or None."""
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


class FakeRunner:
    """Mimics subprocess.run for rpicam-still: writes a JPEG + metadata on capture."""

    def __init__(self, *, returncode=0, width=4608, height=2592, stderr=b"", raises=None):
        self.returncode = returncode
        self.width = width
        self.height = height
        self.stderr = stderr
        self.raises = raises
        self.calls = []

    def __call__(self, cmd, capture_output=False, timeout=None, check=False):
        self.calls.append(cmd)
        if self.raises is not None:
            raise self.raises
        if "--list-cameras" in cmd:
            return Completed(0, stdout=b"0 : imx708_wide [4608x2592]\n")
        if self.returncode == 0:
            out = arg_value(cmd, "-o")
            if out:
                with open(out, "wb") as fh:
                    fh.write(make_jpeg(self.width, self.height))
            meta = arg_value(cmd, "--metadata")
            if meta:
                with open(meta, "w") as fh:
                    fh.write('{"ExposureTime": 13539, "AnalogueGain": 1.5, "AfState": 2}')
        return Completed(self.returncode, stderr=self.stderr)
