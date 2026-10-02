"""Summarise the hydrium-vs-wl53 board runs (``openmv/probes/hyd_probe_v5.py``): per board and
frame source, each codec's time per HD frame at the final 0.4 / 0.8 bpp knobs, rate-search time,
hydrium's peak heap, and whether every plane was byte-identical to the Mac encoder.

    python -m compression_study.phase2.hyd_summary compression_study/work/phase2 \
        n6_hydlive ae3_hydlive n6_hydS4 ae3_hydS4

Writes ``<dir>/hyd_summary.json`` (read by ``report.py``). Each ``<stem>_probe.jsonl`` must have
been checked with ``verify_hyd`` first (``<stem>_probe.verify.json``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "src"), str(REPO)]

from compression_study.phase2.verify import parse_probe  # noqa: E402
from compression_study.phase2.verify_hyd import results  # noqa: E402

TARGETS = {"T1": 0.4, "T2": 0.8}


def summarise(d: Path, stem: str) -> dict:
    p = parse_probe(d / f"{stem}_probe.jsonl")
    ver = json.loads((d / f"{stem}_probe.verify.json").read_text())
    fr = results(p, "frame")[0]
    out = {"stem": stem, "board": stem.split("_")[0], "frame": fr, "codecs": {},
           "errors": sorted(set(p["errors"]))}
    peaks = {r["target_name"]: r for r in results(p, "peak_H")}
    for codec in ("H", "W"):
        c = {}
        rates = {r["target_name"]: r for r in results(p, "rate_" + codec)}
        for r in results(p, "final_" + codec):
            t = r["target_name"]
            run = ver["runs"].get(f"{codec}_{t}", {})
            planes = run.get("planes", {})
            us = list(r["us"].values())
            c[t] = {"knob": r["knob"], "bytes": sum(r["bytes"].values()),
                    "bpp": run.get("bpp"), "frame_s": sum(us) / 1e6,
                    "plane_s": [min(us) / 1e6, max(us) / 1e6],
                    "search_s": rates.get(t, {}).get("search_ms", 0) / 1000,
                    "search_steps": len(rates.get(t, {}).get("steps", [])),
                    "byte_identical": sum(bool(v.get("byte_identical_to_mac"))
                                          for v in planes.values()),
                    "planes": len(planes),
                    "rmse_dn": [v.get("rmse_dn") for v in planes.values()]}
            if codec == "H" and t in peaks:
                c[t]["peak_heap"] = peaks[t]["peak_heap"]
        out["codecs"][codec] = c
    heap = results(p, "heap_H")
    if heap:
        out["heap_free_with_buffers"] = heap[0]["heap_free_with_buffers"]
    return out


if __name__ == "__main__":
    d = Path(sys.argv[1])
    summary = {stem: summarise(d, stem) for stem in sys.argv[2:]}
    (d / "hyd_summary.json").write_text(json.dumps(summary, indent=1))
    for stem, s in summary.items():
        fr = s["frame"]
        print(f"{stem}: source {fr.get('source', 'camera')} mean {fr.get('mean_dn', 0):.1f} DN"
              f" errors {s['errors']}")
        for codec, c in s["codecs"].items():
            for t, v in c.items():
                print(f"  {codec} {t} {v['bpp']:.3f} bpp  frame {v['frame_s']:.2f} s "
                      f"(plane {v['plane_s'][0]:.2f}-{v['plane_s'][1]:.2f})  search "
                      f"{v['search_s']:.1f} s/{v['search_steps']}  identical "
                      f"{v['byte_identical']}/{v['planes']}  peak {v.get('peak_heap', '-')}")
