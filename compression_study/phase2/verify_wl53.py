"""Check the boards' wl53 streams: rebuild each plane exactly from the board's lossless
(nrpack) streams, re-encode it on the Mac with methods/wl53.c at the same Q16 and compare the
bytes; decode the board's stream and report its error against the exact plane.

    python -m compression_study.phase2.verify_wl53 compression_study/work/phase2/n6_wl53_probe.jsonl
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "src"), str(REPO)]

import numpy as np  # noqa: E402

from compression_study.common import sqrt_inverse, sqrt_lut  # noqa: E402
from compression_study.methods import packer  # noqa: E402
from compression_study.methods import plane_codecs as pc  # noqa: E402
from compression_study.phase2.verify import parse_probe  # noqa: E402

PLANES = ("R", "G1", "G2", "B")


def verify(path: Path) -> dict:
    p = parse_probe(path)
    fr = p["results"]["frame"][0]
    pw, ph = fr["w"] // 2, fr["h"] // 2
    rs = p["results"]["rate_search"][0]
    q16 = rs["q16"]
    lut = sqrt_lut(0, 255, 12)
    out = {"q16": q16, "chunk_errors": p["chunk_errors"], "errors": p["errors"], "planes": {}}
    for ch in PLANES:
        ref = p["payloads"].get(f"C_{ch}")
        got = p["payloads"].get(f"W_{q16}_{ch}")
        if not (ref and ref["complete"] and got and got["complete"]):
            out["planes"][ch] = {"received": False}
            continue
        plane = packer.decode_plane(ref["data"], pw, ph, 8)
        codes = lut[plane]
        mac, _ = pc.wl53_enc(codes, 4095, q16 / 16)
        rec = pc.wl53_dec_factory(pw, ph)(got["data"])
        lin = sqrt_inverse(rec, 0, 255, 12)
        err = lin - plane.astype(np.float64)
        out["planes"][ch] = {"received": True, "bytes": got["bytes"],
                             "byte_identical_to_mac": mac == got["data"],
                             "rmse_dn": float(np.sqrt(np.mean(err ** 2))),
                             "max_abs_dn": float(np.abs(err).max())}
    total = sum(v.get("bytes", 0) for v in out["planes"].values())
    out["total_bytes"], out["bpp"] = total, total * 8 / (fr["w"] * fr["h"])
    return out


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        r = verify(Path(arg))
        print(Path(arg).name, json.dumps(r, indent=1))
        Path(arg).with_suffix(".verify.json").write_text(json.dumps(r, indent=1))
