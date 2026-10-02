"""hydrium (methods/hyd.c) as compiled C on the Pi: time, memory and bytes for a set of planes.

Mac:   python -m compression_study.phase2.pi_hyd_bench prepare --data <s4> --out DIR
Pi:    python -m compression_study.phase2.pi_hyd_bench run DIR
Mac:   python -m compression_study.phase2.pi_hyd_bench check DIR

``prepare`` writes the planes (uint16 LE) and ``jobs.json``: the four planes of the S4 N6 and AE3
cool frames (the frame the boards were benched on) at the boards' 0.4 / 0.8 bpp knobs, and the
IMX708 1600×900 field crop at its 50 kB knob from the study. ``run`` encodes each plane with the
Pi's own build of ``bin/hyd`` (gcc, -ffp-contract=off), keeps the best of 3 wall times and the
peak RSS, and writes the streams. ``check`` re-encodes on the Mac and compares the bytes.
"""

from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "src"), str(REPO)]

import numpy as np  # noqa: E402

from compression_study.methods import plane_codecs as pc  # noqa: E402

PLANES = ("R", "G1", "G2", "B")


def prepare(data: Path, out: Path) -> None:
    from compression_study import run_study as rs
    from compression_study.common import split

    out.mkdir(parents=True, exist_ok=True)
    summ = json.loads((rs.STUDY / "work" / "phase2" / "hyd_summary.json").read_text())
    jobs = []
    for cam in ("n6", "ae3"):
        raw = rs.load(data, cam, "cool", -1, 0)
        knobs = {t: v["knob"] for t, v in summ[f"{cam}_hydS4"]["codecs"]["H"].items()}
        for ch, p in split(raw.mosaic, raw.cfa).items():
            f = out / f"{cam}_{ch}.u16"
            f.write_bytes(p.astype("<u2").tobytes())
            for t, lab in knobs.items():
                hf, gs = (int(x) for x in lab.split("_"))
                jobs.append({"name": f"{cam}_{t}_{ch}", "file": f.name, "w": p.shape[1],
                             "h": p.shape[0], "nlut": raw.white + 1, "black": raw.black,
                             "white": raw.white, "hf": hf, "gs": gs})
    # IMX708 field crop at the study's 50 kB knob (rows/<fsid>.H.json, field row)
    raw = rs.load(data, "imx708", "cool", -1, 0)
    rows = json.loads((rs.STUDY / "work" / "rows" / "imx708_cool_s-1_air.H.json").read_text())
    row = next(r for r in rows["rows"] if r["fsid"].endswith("_field") and r["target"] == "50kB"
               and r.get("hit", True) and r["bpp"] > 0.2)
    hf, gs = pc.hyd_params(float(row["knob"]))
    from compression_study import rois
    roi = rois.load(rs.STUDY / "config" / "card_rois.yaml")["imx708_cool"]
    w, h = rs.FIELD_CROP
    cxy = np.mean([np.mean(q["quad"], axis=0) for q in roi["patches"].values()], axis=0) * 2
    x0 = int(np.clip(cxy[0] - w / 2, 0, raw.shape[1] - w)) // 2 * 2
    y0 = int(np.clip(cxy[1] - h / 2, 0, raw.shape[0] - h)) // 2 * 2
    crop = raw.mosaic[y0:y0 + h, x0:x0 + w]
    for ch, p in split(crop, raw.cfa).items():
        f = out / f"imx708field_{ch}.u16"
        f.write_bytes(p.astype("<u2").tobytes())
        jobs.append({"name": f"imx708field_50kB_{ch}", "file": f.name, "w": p.shape[1],
                     "h": p.shape[0], "nlut": raw.white + 1, "black": raw.black,
                     "white": raw.white, "hf": hf, "gs": gs})
    (out / "jobs.json").write_text(json.dumps(jobs, indent=1))
    print(f"{len(jobs)} jobs in {out}")


def args_for(j: dict) -> list[str]:
    return ["enc", str(j["w"]), str(j["h"]), str(j["nlut"]), str(j["black"]), str(j["white"]),
            str(j["hf"]), str(j["gs"]), str(pc.HYD_LF)]


def run_jobs(d: Path) -> None:
    exe = pc.hyd_build()
    res = []
    for j in json.loads((d / "jobs.json").read_text()):
        src = (d / j["file"]).read_bytes()
        best = None
        for _ in range(3):
            t = time.perf_counter()
            p = subprocess.run([exe, *args_for(j)], input=src, capture_output=True, check=True)
            dt = time.perf_counter() - t
            best = dt if best is None else min(best, dt)
        (d / f"{j['name']}.jxl").write_bytes(p.stdout)
        peak = int(p.stderr.split()[-1]) if p.stderr.startswith(b"peak_heap_bytes") else None
        res.append({**j, "bytes": len(p.stdout), "wall_s": best, "peak_heap": peak})
        print(j["name"], len(p.stdout), f"{best:.3f} s", peak, flush=True)
    ru = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    (d / "pi_results.json").write_text(json.dumps({"rows": res, "max_rss_kb": ru}, indent=1))


def check(d: Path) -> None:
    exe = pc.hyd_build()
    pi = json.loads((d / "pi_results.json").read_text())
    same = 0
    for r in pi["rows"]:
        mac = subprocess.run([exe, *args_for(r)], input=(d / r["file"]).read_bytes(),
                             capture_output=True, check=True).stdout
        ok = mac == (d / f"{r['name']}.jxl").read_bytes()
        same += ok
        r["byte_identical_to_mac"] = ok
    pi["byte_identical"] = f"{same}/{len(pi['rows'])}"
    (d / "pi_results.json").write_text(json.dumps(pi, indent=1))
    print("byte-identical", pi["byte_identical"], "max RSS", pi["max_rss_kb"], "KB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("prepare", "run", "check"))
    ap.add_argument("dir", type=Path, nargs="?")
    ap.add_argument("--data", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    if a.cmd == "prepare":
        prepare(a.data, a.out)
    elif a.cmd == "run":
        run_jobs(a.dir)
    else:
        check(a.dir)
