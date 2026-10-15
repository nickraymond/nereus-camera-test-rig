"""Minimal multi-part payload: [u32 big-endian length][bytes] per part (stdlib only).

Used when one "clip" is sent as several files (N JPEG XL stills; background + residual video).
The 4-byte length per part is counted in the budget — a real transport needs some framing too.

    python3 pack.py pack OUT PART...        python3 pack.py unpack IN OUTDIR
"""

import struct
import sys
from pathlib import Path


def pack(out: Path, parts: list[Path]) -> None:
    with open(out, "wb") as f:
        for p in parts:
            b = Path(p).read_bytes()
            f.write(struct.pack(">I", len(b)) + b)


def unpack(src: Path) -> list[bytes]:
    b = Path(src).read_bytes()
    parts, i = [], 0
    while i < len(b):
        (n,) = struct.unpack(">I", b[i : i + 4])
        parts.append(b[i + 4 : i + 4 + n])
        i += 4 + n
    return parts


if __name__ == "__main__":
    if sys.argv[1] == "pack":
        pack(Path(sys.argv[2]), [Path(p) for p in sys.argv[3:]])
    else:
        out = Path(sys.argv[3])
        out.mkdir(parents=True, exist_ok=True)
        for i, b in enumerate(unpack(Path(sys.argv[2]))):
            (out / f"part{i:02d}").write_bytes(b)
