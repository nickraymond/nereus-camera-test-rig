"""Turn all results into the results table (RESULTS.md rows) and the page spec numbers (Mac).

    python -m compression_study.video_100k.report --data <data> --words page_words.json \
        --out <data>/page_spec.json

Regimes (post-hoc stratification, labelled as such): "calm" = rounds 1-2 of both cameras
(4 clips), "hard" = round 3 of both cameras (sunlit caustics + diver + bubbles, 2 clips; an
x264 CRF-30 encode is 8-13x larger than for the calm clips). The PREREG ranking statistic is
still the worst clip over all six.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .analyze import load
from .common import CLIPS

CALM = [c for c in CLIPS if not c.endswith("round3")]
HARD = [c for c in CLIPS if c.endswith("round3")]


def _mean(xs: list) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(float(np.mean(xs)), 1) if xs else None


def stats(rows: list[dict], cand: str, host: str = "mac") -> dict | None:
    rs = [r for r in rows if r["cand"] == cand and r.get("host", "mac") == host]
    ok = {r["clip"]: r for r in rs if r.get("status") == "ok"}
    over = {r["clip"]: r for r in rs if r.get("status") == "over_cap"}
    if not ok and not over:
        return None
    calm = [ok[c]["vmaf"] for c in CALM if c in ok]
    hard = [ok[c]["vmaf"] for c in HARD if c in ok]
    allv = [ok[c]["vmaf"] for c in CLIPS if c in ok]
    s2 = [
        ok[c].get("ssimulacra2") for c in CLIPS if c in ok and ok[c].get("ssimulacra2") is not None
    ]
    pi = [ok[c]["pi_final"] for c in CLIPS if c in ok and ok[c].get("pi_final")]
    any_r = (list(ok.values()) or list(over.values()))[0]
    return {
        "cand": cand,
        "host": host,
        "label": any_r.get("label", cand),
        "n_ok": len(ok),
        "n_over": len(over),
        "clips_run": sorted(set(ok) | set(over)),
        "complete": len(ok) == len(CLIPS),
        "size_max_B": max((r["size_B"] for r in ok.values()), default=None),
        "over_min_B": min((r["size_B"] for r in over.values()), default=None),
        "vmaf_calm": round(float(np.mean(calm)), 1) if calm else None,
        "vmaf_calm_n": len(calm),
        "vmaf_hard": round(float(np.mean(hard)), 1) if hard else None,
        "vmaf_hard_n": len(hard),
        "vmaf_worst": round(float(min(allv)), 1) if allv else None,
        "vmaf_all": round(float(np.mean(allv)), 1) if allv else None,
        "ssimulacra2": round(float(np.mean(s2)), 1) if s2 else None,
        "s2_calm": _mean([ok[c].get("ssimulacra2") for c in CALM if c in ok]),
        "s2_hard": _mean([ok[c].get("ssimulacra2") for c in HARD if c in ok]),
        "card_ssim_y": round(float(np.mean([ok[c]["card_ssim_y"] for c in ok])), 3) if ok else None,
        "still_ssimulacra2": round(
            float(
                np.mean([ok[c]["still_ssimulacra2"] for c in ok if "still_ssimulacra2" in ok[c]])
            ),
            1,
        )
        if any("still_ssimulacra2" in r for r in ok.values())
        else None,
        "box_wall_s_max": max((p["wall_s"] for p in pi), default=None),
        "box_wall_s_med": float(np.median([p["wall_s"] for p in pi])) if pi else None,
        "box_rss_MiB_max": max((p["peak_tree_rss_MiB"] for p in pi), default=None),
        "attempts_max": max((len(r["attempts"]) for r in ok.values()), default=None),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    datas = sorted(p for p in a.data.iterdir() if p.is_dir() and (p / "results.jsonl").exists())
    rows = load(datas)
    keys = sorted({(r["cand"], r.get("host", "mac")) for r in rows})
    res = [s for s in (stats(rows, c, h) for c, h in keys) if s]
    a.out.write_text(json.dumps(res, indent=1))
    for s in sorted(res, key=lambda s: -(s["vmaf_worst"] or -1)):
        print(
            f"{s['cand']:22s} {s['host'][:4]:4s} ok{s['n_ok']}/over{s['n_over']} "
            f"calm {s['vmaf_calm']} (n{s['vmaf_calm_n']}) "
            f"hard {s['vmaf_hard']} (n{s['vmaf_hard_n']}) "
            f"worst {s['vmaf_worst']} S2 {s['ssimulacra2']} card {s['card_ssim_y']} "
            f"max {s['size_max_B']} box {s['box_wall_s_max']} s {s['box_rss_MiB_max']} MiB"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
