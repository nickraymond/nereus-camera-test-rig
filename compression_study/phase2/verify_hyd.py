"""Check the boards' hydrium and wl53 streams from ``openmv/probes/hyd_probe_v5.py``: rebuild each
plane exactly from the board's lossless (nrpack) streams, re-encode it on the Mac with the same
knobs (``bin/hyd`` from methods/hyd.c, ``bin/wl53``) and compare the bytes; decode the board's
stream (djxl / wl53) and report its error against the exact plane.

    python -m compression_study.phase2.verify_hyd compression_study/work/phase2/n6_hyd_probe.jsonl
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
LF = 4  # as in the probe


def results(p: dict, tag: str) -> list[dict]:
    """``#R`` records, de-duplicated (the probe prints each 3× against console drops)."""
    seen, out = set(), []
    for r in p["results"].get(tag, []):
        key = json.dumps(r, sort_keys=True)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def streams(p: dict) -> dict:
    """{(codec, target): (knob label, {plane: bytes})} from the payload names."""
    out: dict = {}
    for name, v in p["payloads"].items():
        parts = name.split("_")
        if parts[0] not in ("H", "W") or not v["complete"]:
            continue
        codec, target, plane = parts[0], parts[1], parts[-1]
        label = "_".join(parts[2:-1])
        out.setdefault((codec, target), (label, {}))[1][plane] = v["data"]
    return out


def verify(path: Path) -> dict:
    p = parse_probe(path)
    fr = results(p, "frame")[0]
    pw, ph = fr["w"] // 2, fr["h"] // 2
    planes = {}
    for ch in PLANES:
        ref = p["payloads"].get(f"C_{ch}")
        if ref and ref["complete"]:
            planes[ch] = packer.decode_plane(ref["data"], pw, ph, 8)
    lut = sqrt_lut(0, 255, 12)
    out = {"frame": fr, "chunk_errors": p["chunk_errors"], "errors": p["errors"], "runs": {}}
    for (codec, target), (label, got) in sorted(streams(p).items()):
        run = {"knob": label, "planes": {}}
        for ch in PLANES:
            if ch not in planes or ch not in got:
                run["planes"][ch] = {"received": False}
                continue
            plane, data = planes[ch], got[ch]
            if codec == "H":
                hf, gs = (int(x) for x in label.split("_"))
                mac, _ = pc.hyd_enc(plane, 256, 0, 255, hf, gs, LF)
                lin = pc.hyd_dec(data) * 255.0
            else:
                mac, _ = pc.wl53_enc(lut[plane], 4095, int(label) / 16)
                lin = sqrt_inverse(pc.wl53_dec_factory(pw, ph)(data), 0, 255, 12)
            err = lin - plane.astype(np.float64)
            run["planes"][ch] = {"received": True, "bytes": len(data),
                                 "byte_identical_to_mac": mac == data,
                                 "rmse_dn": float(np.sqrt(np.mean(err ** 2))),
                                 "mean_err_dn": float(err.mean())}
        total = sum(v.get("bytes", 0) for v in run["planes"].values())
        run["total_bytes"], run["bpp"] = total, total * 8 / (fr["w"] * fr["h"])
        out["runs"][f"{codec}_{target}"] = run
    return out


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        r = verify(Path(arg))
        print(Path(arg).name, json.dumps(r, indent=1))
        Path(arg).with_suffix(".verify.json").write_text(json.dumps(r, indent=1))
