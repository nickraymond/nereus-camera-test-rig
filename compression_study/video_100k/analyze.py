"""Summarise results.jsonl files into one table (Mac).

    python -m compression_study.video_100k.analyze --data <data>/stage1 <data>/stage2 [--clips all]

Per candidate over the clips it ran on: n, size (max must be ≤ cap), VMAF mean / worst clip /
best clip, worst-frame 5th percentile, SSIMULACRA2, card-ROI SSIM-Y / PSNR-Y. Ranked by the
worst-clip VMAF (PREREG). Writes ``summary.json`` + ``summary.csv`` next to the first --data.
A candidate counts as "complete" only when it is ok on every requested clip.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from .common import CLIPS, EXPLORE, read_jsonl


def load(datas: list[Path]) -> list[dict]:
    rows = {}
    for d in datas:
        for r in read_jsonl(d / "results.jsonl"):
            key = (r["cand"], r["clip"], r.get("host", "mac"))
            if r.get("status") == "error" and key in rows:
                continue
            r["_data"] = str(d)
            rows[key] = r  # later lines win (re-runs)
    return list(rows.values())


def summarize(rows: list[dict], clips: list[str]) -> list[dict]:
    out = []
    keys = sorted({(r["cand"], r.get("host", "mac")) for r in rows})
    for cand, host in keys:
        rs = [
            r
            for r in rows
            if r["cand"] == cand and r.get("host", "mac") == host and r["clip"] in clips
        ]
        ok = [r for r in rs if r.get("status") == "ok"]
        s = {
            "cand": cand,
            "host": host,
            "label": rs[0].get("label", cand) if rs else cand,
            "tags": rs[0].get("tags", []) if rs else [],
            "n_ok": len(ok),
            "n_clips": len(clips),
            "complete": len(ok) == len(clips),
            "status": ",".join(sorted({r.get("status", "?") for r in rs})),
        }
        if ok:
            v = np.array([r["vmaf"] for r in ok])

            def m(k, rnd=2):
                xs = [r[k] for r in ok if r.get(k) is not None]
                return round(float(np.mean(xs)), rnd) if xs else None

            s.update(
                size_max_B=max(r["size_B"] for r in ok),
                size_mean_B=round(float(np.mean([r["size_B"] for r in ok]))),
                vmaf_mean=round(float(v.mean()), 2),
                vmaf_worst=round(float(v.min()), 2),
                vmaf_best=round(float(v.max()), 2),
                worst_clip=ok[int(v.argmin())]["clip"],
                vmaf_p5_worst=round(float(min(r["vmaf_p5"] for r in ok)), 2),
                ssimulacra2=m("ssimulacra2"),
                ssimulacra2_worst=round(
                    float(min(r["ssimulacra2"] for r in ok if "ssimulacra2" in r)), 2
                )
                if any("ssimulacra2" in r for r in ok)
                else None,
                card_ssim_y=m("card_ssim_y", 4),
                card_psnr_y=m("card_psnr_y"),
                psnr_y=m("psnr_y"),
                still_ssimulacra2=m("still_ssimulacra2"),
                enc_wall_mac_s=m("enc_wall_mac_s", 1),
                per_clip={r["clip"]: r["vmaf"] for r in ok},
            )
            pf = [r["pi_final"] for r in ok if r.get("pi_final")]
            if pf:
                s.update(
                    box_wall_s_max=max(p["wall_s"] for p in pf),
                    box_peak_tree_rss_MiB_max=max(p["peak_tree_rss_MiB"] for p in pf),
                    box_cpu_s_max=max(p["cpu_s"] for p in pf),
                    attempts_max=max(len(r["attempts"]) for r in ok),
                )
        out.append(s)
    out.sort(key=lambda s: (not s["complete"], -(s.get("vmaf_worst") or -1e9)))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, nargs="+", required=True)
    ap.add_argument("--clips", default="explore")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    clips = EXPLORE if a.clips == "explore" else CLIPS if a.clips == "all" else a.clips.split(",")
    summ = summarize(load(a.data), clips)
    out = a.out or a.data[0]
    (out / "summary.json").write_text(json.dumps(summ, indent=1))
    cols = [
        "cand",
        "host",
        "complete",
        "n_ok",
        "size_max_B",
        "vmaf_mean",
        "vmaf_worst",
        "vmaf_best",
        "vmaf_p5_worst",
        "ssimulacra2",
        "card_ssim_y",
        "card_psnr_y",
        "still_ssimulacra2",
        "enc_wall_mac_s",
        "box_wall_s_max",
        "box_peak_tree_rss_MiB_max",
        "label",
    ]
    with (out / "summary.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(summ)
    print(
        f"{'cand':24s} {'n':>3s} {'maxKB':>6s} {'VMAF':>6s} {'worst':>6s} {'best':>6s} {'p5w':>6s} "
        f"{'S2':>6s} {'cardSSIM':>8s} {'cardPSNR':>8s}  label"
    )
    for s in summ:
        if "vmaf_mean" not in s:
            print(f"{s['cand']:24s} {s['n_ok']:>3d}   -- {s['status']}")
            continue
        print(
            f"{s['cand']:24s} {s['n_ok']:>3d} {s['size_max_B'] / 1000:6.1f} {s['vmaf_mean']:6.1f} "
            f"{s['vmaf_worst']:6.1f} {s['vmaf_best']:6.1f} {s['vmaf_p5_worst']:6.1f} "
            f"{(s['ssimulacra2'] or 0):6.1f} {(s['card_ssim_y'] or 0):8.4f} "
            f"{(s['card_psnr_y'] or 0):8.2f}  "
            f"{s['label']}{'' if s['complete'] else '  [INCOMPLETE]'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
