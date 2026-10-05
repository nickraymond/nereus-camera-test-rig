"""Mac side of Phase 2: check what the devices sent, with the Phase 1 decoders.

    python -m compression_study.phase2.verify --dir compression_study/work/phase2

OpenMV probe (``<board>_probe.jsonl``): reassemble the CRC-checked console chunks, decode the
viper packer streams with ``methods/packer`` (bit-exact = the decoded plane's SHA-256 equals
the one the board computed from its own plane) and re-encode with the C packer (byte-identical
= the MicroPython port writes the same bitstream). JPEG planes are decoded with djpeg. Energy:
UPS power during each ``#M`` window minus the idle window.

Pi bench (``pi/bench.json`` + ``pi/*.bin``): decode every blob with the Phase 1 decoder;
C / N must equal the original / Phase 1 reconstruction exactly, D / D2 are scored with the
Phase 1 metrics against the same frame.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "src"), str(REPO)]

import numpy as np  # noqa: E402

from compression_study.methods import packer  # noqa: E402


def parse_probe(path: Path) -> dict:
    recs = [json.loads(line) for line in path.read_text().splitlines()]
    out: dict = {"results": {}, "errors": [], "marks": [], "payloads": {}, "chunk_errors": 0}
    chunks: dict = {}
    ends: dict = {}
    for rec in recs:
        t, line = rec["t"], rec["line"]
        if line.startswith("#R "):
            _, tag, js = line.split(" ", 2)
            try:
                out["results"].setdefault(tag, []).append(json.loads(js))
            except json.JSONDecodeError:
                out["errors"].append(f"bad #R line {tag}")
        elif line.startswith("#E"):
            out["errors"].append(line)
        elif line.startswith("#M"):
            out["marks"].append((t, line.split()[1:]))
        elif line.startswith("#B "):
            parts = line.split(" ")
            if len(parts) < 4:  # a line cut short by a console drop
                out["chunk_errors"] += 1
                continue
            name = parts[1]
            if parts[2] == "end":
                if len(parts) == 5 and len(parts[4]) == 64:
                    ends[name] = (int(parts[3]), parts[4])
                else:
                    out["chunk_errors"] += 1
                continue
            try:
                off, crc, data = int(parts[2]), parts[3], base64.b64decode(parts[4])
                if f"{binascii.crc32(data) & 0xFFFFFFFF:08x}" != crc:
                    raise ValueError("crc")
                chunks.setdefault(name, {}).setdefault(off, data)  # first good copy
            except (ValueError, IndexError, binascii.Error):
                out["chunk_errors"] += 1  # a damaged copy; another copy usually survives
    for name, (n, sha) in ends.items():
        parts = chunks.get(name, {})
        buf = b"".join(parts[o] for o in sorted(parts))
        ok = len(buf) == n and hashlib.sha256(buf).hexdigest() == sha
        out["payloads"][name] = {"bytes": n, "complete": ok, "data": buf if ok else None}
    return out


def energy(power: list, marks: list) -> dict:
    """Mean UPS power per #M window; encoder energy = (window − idle) × time per iteration."""
    win = {}
    starts = {m[1]: t for t, m in marks if m[0] == "start"}
    for t, m in marks:
        if m[0] == "stop":
            tag, n = m[1], int(m[2])
            s = [v * i / 1e6 for (tt, v, i) in power
                 if v and i and starts[tag] + 0.5 <= tt <= t - 0.2]
            win[tag] = {"iterations": n, "seconds": t - starts[tag],
                        "load_w": float(np.mean(s)) if s else None, "n": len(s)}
    if "idle" in win and win["idle"]["load_w"]:
        for tag, w in win.items():
            if tag != "idle" and w["load_w"]:
                w["delta_w"] = w["load_w"] - win["idle"]["load_w"]
                w["joules_per_frame"] = w["delta_w"] * w["seconds"] / max(w["iterations"], 1)
    return win


def verify_probe(dirp: Path, board: str) -> dict:
    p = parse_probe(dirp / f"{board}_probe.jsonl")
    res = p["results"]
    planes = res.get("planes", [{}])[0]
    pw, ph = planes.get("w"), planes.get("h")
    checks = {}
    for ch in ("R", "G1", "G2", "B"):
        pl = p["payloads"].get(f"C_{ch}")
        if not pl or not pl["complete"]:
            checks[ch] = {"received": False}
            continue
        plane = packer.decode_plane(pl["data"], pw, ph, 8)
        same_sha = (hashlib.sha256(plane.astype(np.uint8).tobytes()).hexdigest()
                    == planes["sha256"][ch])
        c_bytes = packer.encode_plane(plane, 8, impl="c")
        checks[ch] = {"received": True, "bytes": pl["bytes"], "bit_exact": same_sha,
                      "byte_identical_to_c": c_bytes == pl["data"],
                      "bits_per_sample": pl["bytes"] * 8 / (pw * ph)}
    jpeg = {}
    import cv2
    for name, pl in p["payloads"].items():
        if name.startswith(("D", "M1")) and pl["complete"]:
            img = cv2.imdecode(np.frombuffer(pl["data"], np.uint8), cv2.IMREAD_UNCHANGED)
            jpeg[name] = {"bytes": pl["bytes"], "decodes": img is not None,
                          "shape": None if img is None else list(img.shape),
                          "mean": None if img is None else float(img.mean())}
    pw_path = dirp / f"{board}_power.json"
    en = energy(json.loads(pw_path.read_text()), p["marks"]) if pw_path.exists() else {}
    status = json.loads((dirp / f"{board}_status.json").read_text())
    return {"board": board, "results": res, "errors": p["errors"],
            "chunk_errors": p["chunk_errors"], "packer_check": checks, "jpeg_check": jpeg,
            "energy": en, "rig_status": status["steps"][-1]["step"],
            "payloads_complete": {k: v["complete"] for k, v in p["payloads"].items()}}


def verify_pi(dirp: Path, data: Path, crop: str | None = None) -> dict:
    """Decode every Pi blob with the Phase 1 decoders and score it against the same frame
    (or the same mosaic crop, for the field-link run)."""
    from compression_study import metrics
    from compression_study import run_study as R
    from compression_study.methods import raw_planes as rp
    from compression_study.rois import load

    bench = json.loads((dirp / "bench.json").read_text())
    roi = load(R.STUDY / "config" / "card_rois.yaml")["imx708_cool"]
    reps = [R.load(data, "imx708", "cool", -1, i) for i in range(3)]
    if crop:
        from dataclasses import replace
        x, y, w, h = (int(v) for v in crop.split(","))
        reps = [replace(r, mosaic=np.ascontiguousarray(r.mosaic[y:y + h, x:x + w]))
                for r in reps]
        roi = R.shift_rois(roi, x // 2, y // 2)
    ctx, _ = R.context_for(reps, roi, R.CAMERAS["imx708"][3])
    raw = reps[0]
    out = {}
    for f in sorted(dirp.glob("*.bin")):
        blob = f.read_bytes()
        rec = rp.decode(blob)
        m = metrics.evaluate(ctx, rec, with_ssim=False)
        out[f.stem] = {"bytes": len(blob), "bpp": len(blob) * 8 / raw.n_px,
                       "bit_exact": bool(np.array_equal(rec, raw.mosaic.astype(np.float64))),
                       **{k: m[k] for k in ("red_err_noise", "stress_de_mean", "block_de_med",
                                            "bm_err_R_med")}}
        if f.stem.startswith("N_") and not crop:  # must equal the Mac's N reconstruction
            mac = rp.decode(rp.encode(raw, rp.RawSpec("N", "sqrt", 8)))
            out[f.stem]["equals_mac_N"] = bool(np.array_equal(rec, mac))
        print(f.stem, out[f.stem], flush=True)
    return {"steps": bench["steps"], "meta": bench["meta"], "decoded": out}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, required=True)
    ap.add_argument("--boards", default="")
    ap.add_argument("--data", type=Path, help="S4 copy; enables the Pi bench check")
    args = ap.parse_args(argv)
    packer.build_c()
    out = {}
    for b in args.boards.split(","):
        if (args.dir / f"{b}_probe.jsonl").exists():
            out[b] = verify_probe(args.dir, b)
    (args.dir / "verify_openmv.json").write_text(json.dumps(out, indent=1, default=str))
    if args.data and (args.dir / "pi" / "bench.json").exists():
        pi = verify_pi(args.dir / "pi", args.data)
        (args.dir / "verify_pi.json").write_text(json.dumps(pi, indent=1, default=str))
    field = args.dir / "pi_field" / "bench.json"
    if args.data and field.exists():
        crop = json.loads(field.read_text())["meta"]["crop"]
        pf = verify_pi(args.dir / "pi_field", args.data, crop)
        (args.dir / "verify_pi_field.json").write_text(json.dumps(pf, indent=1, default=str))
    for b, v in out.items():
        print(b, "chunk errors", v["chunk_errors"], "errors", v["errors"])
        print("  packer", v["packer_check"])
        print("  jpeg", {k: (x["bytes"], x["decodes"]) for k, x in v["jpeg_check"].items()})
        print("  energy", v["energy"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
