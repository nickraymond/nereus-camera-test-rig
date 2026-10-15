"""Apply the PREREG v2 decision rule to the results and write the results table (Mac).

    python -m compression_study.video_100k.decide --run <RUN_ROOT> --out <RUN_ROOT>/decision

Reads ``<run>/stage1/results.jsonl`` (Mac encoders: the quality ceiling) and
``<run>/pi/results.jsonl`` (Pi container: stages 2-4), plus every
``<run>/pi/pi_calibration*.jsonl``.

Pi time (emulated, calibrated), per clip, from the final encode's container measurement:
  low  = container wall × median wall ratio   (the Zero's 4 cores scale like the container's)
  high = container CPU  × max CPU ratio        (no parallel speed-up at all)
Ratios = real nereus002 / container, single-thread SVT-AV1 p12 jobs of PR #88, pooled over all
calibration runs; rows where the container stalled (wall > 3 × CPU) are dropped and listed.

Candidates tagged "posthoc" are tabled but never enter the decision.

Rule (PREREG v2): eligible = ok and ≤ 100,000 B on all 6 main-window clips, Pi high ≤ 600 s and
peak RSS ≤ 250 MiB on the worst clip. Rank by worst-clip VMAF; tie = within 3.0 of the top;
inside the tie: standard bitstream first, then lowest Pi high time (within 20 % = equal), then
highest worst-clip SSIMULACRA2. Writes ``results_table.md`` and ``decision.json``.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from .candidates import registry
from .common import CAP_B, CLIPS, SENS_CLIPS, read_jsonl

TIE_VMAF = 3.0  # "within 3.0 points" (PREREG): inclusive, with a float epsilon
PI_S, PI_RSS, ZRAM_RSS = 600.0, 250.0, 400.0
TIME_EQUAL = 1.20


def calibration(pi_dir: Path) -> dict:
    rows, dropped = [], []
    for f in sorted(pi_dir.glob("pi_calibration*.jsonl")):
        for r in read_jsonl(f):
            if r["workload"] != "svt_p12_lp1":
                continue
            if r["box"]["wall_s"] > 3 * r["box"]["cpu_s"]:
                dropped.append({"file": f.name, "source": r["source"], **r["box"]})
                continue
            rows.append(r)
    if not rows:
        raise SystemExit(f"no calibration rows in {pi_dir}: run pi_emulate calibrate first")
    wall = [r["pi"]["wall_s"] / r["box"]["wall_s"] for r in rows]
    cpu = [r["pi"]["cpu_s"] / r["box"]["cpu_s"] for r in rows]
    return {
        "n": len(rows),
        "wall_ratio_median": round(statistics.median(wall), 2),
        "cpu_ratio_median": round(statistics.median(cpu), 2),
        "cpu_ratio_max": round(max(cpu), 2),
        "dropped_stalls": dropped,
    }


def latest(rows: list[dict]) -> dict:
    """(cand, clip) -> the last non-error record (later runs win)."""
    out = {}
    for r in rows:
        key = (r["cand"], r["clip"])
        if r.get("status") == "error" and key in out:
            continue
        out[key] = r
    return out


def summarize(
    recs: dict, cand: str, clips: list[str], cal: dict | None, std: bool, posthoc: bool = False
) -> dict:
    rs = [recs.get((cand, c)) for c in clips]
    ok = [r for r in rs if r and r.get("status") == "ok" and r["size_B"] <= CAP_B]
    s = {
        "cand": cand,
        "label": next((r["label"] for r in rs if r and "label" in r), cand),
        "standard": std,
        "posthoc": posthoc,
        "n_ok": len(ok),
        "n": len(clips),
        "complete": len(ok) == len(clips),
        "status": {c: (recs.get((cand, c)) or {}).get("status", "not run") for c in clips},
    }
    if not ok:
        return s
    worst = min(ok, key=lambda r: r["vmaf"])
    s.update(
        size_max_B=max(r["size_B"] for r in ok),
        vmaf_mean=round(statistics.mean(r["vmaf"] for r in ok), 2),
        vmaf_worst=worst["vmaf"],
        worst_clip=worst["clip"],
        vmaf_best=max(r["vmaf"] for r in ok),
        vmaf_p5_worst=min(r["vmaf_p5"] for r in ok),
        s2_worst=min((r["ssimulacra2"] for r in ok if "ssimulacra2" in r), default=None),
        s2_mean=round(statistics.mean(r["ssimulacra2"] for r in ok), 2)
        if all("ssimulacra2" in r for r in ok)
        else None,
        card_ssim_worst=min(r["card_ssim_y"] for r in ok),
        card_psnr_worst=min(r["card_psnr_y"] for r in ok),
        per_clip={r["clip"]: r["vmaf"] for r in ok},
        attempts_max=max(len(r["attempts"]) for r in ok),
    )
    pf = [(r, r.get("pi_final")) for r in ok]
    if cal and all(p for _, p in pf):
        low = max(p["wall_s"] * cal["wall_ratio_median"] for _, p in pf)
        high = max(p["cpu_s"] * cal["cpu_ratio_max"] for _, p in pf)
        rss = max(p["peak_tree_rss_MiB"] for _, p in pf)
        search_high = max(
            sum(a["pi"]["cpu_s"] for a in r["attempts"] if "pi" in a) * cal["cpu_ratio_max"]
            for r, _ in pf
        )
        if rss > ZRAM_RSS or low > PI_S:
            feas = "not feasible"
        elif high <= PI_S and rss <= PI_RSS:
            feas = "feasible"
        else:
            flags = []
            if high > PI_S:
                flags.append("only if threads scale")
            if rss > PI_RSS:
                flags.append("only with zram/swap")
            feas = "flagged: " + ", ".join(flags)
        s.update(
            pi_low_s=round(low),
            pi_high_s=round(high),
            pi_rss_MiB=rss,
            pi_search_high_s=round(search_high),
            pi_box_wall_s_max=max(p["wall_s"] for _, p in pf),
            pi=feas,
        )
    return s


def decide(rows: list[dict]) -> dict:
    elig = [s for s in rows if s["complete"] and s.get("pi") == "feasible" and not s["posthoc"]]
    if not elig:
        return {"winner": None, "why": "no eligible candidate"}
    elig.sort(key=lambda s: -s["vmaf_worst"])
    top = elig[0]["vmaf_worst"]
    tie = [s for s in elig if top - s["vmaf_worst"] <= TIE_VMAF + 1e-9]
    steps = [
        f"top eligible worst-clip VMAF {top}; tie set (within {TIE_VMAF}): "
        + ", ".join(f"{s['cand']} ({s['vmaf_worst']})" for s in tie)
    ]
    pool = tie
    if any(s["standard"] for s in pool) and not all(s["standard"] for s in pool):
        pool = [s for s in pool if s["standard"]]
        steps.append("1. standard bitstream first -> " + ", ".join(s["cand"] for s in pool))
    fastest = min(s["pi_high_s"] for s in pool)
    pool = [s for s in pool if s["pi_high_s"] <= fastest * TIME_EQUAL]
    steps.append(
        f"2. lowest Pi high time ({fastest} s, within 20 % = equal) -> "
        + ", ".join(f"{s['cand']} ({s['pi_high_s']} s)" for s in pool)
    )
    pool.sort(key=lambda s: -(s["s2_worst"] if s["s2_worst"] is not None else -1e9))
    if len(pool) > 1:
        steps.append(
            "3. highest worst-clip SSIMULACRA2 -> "
            + ", ".join(f"{s['cand']} ({s['s2_worst']})" for s in pool)
        )
    return {"winner": pool[0]["cand"], "steps": steps, "tie_set": [s["cand"] for s in tie]}


def fmt(v, nd=1):
    return "–" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def table(rows: list[dict], with_pi: bool) -> list[str]:
    head = (
        "| candidate | ok | max B | VMAF worst (clip) | mean | p5 worst | S2 worst "
        "| card SSIM-Y worst |"
    )
    sep = "|---|---|---|---|---|---|---|---|"
    if with_pi:
        head += " Pi time low–high s | RSS MiB | Pi |"
        sep += "---|---|---|"
    out = [head, sep]
    for s in rows:
        if "vmaf_worst" not in s:
            out.append(
                f"| `{s['cand']}` | {s['n_ok']}/{s['n']} | "
                + " | ".join(["–"] * (6 + (3 if with_pi else 0)))
                + " |"
            )
            continue
        line = (
            f"| `{s['cand']}`{' (post-hoc)' if s.get('posthoc') else ''} | "
            f"{s['n_ok']}/{s['n']} | {s['size_max_B']:,} | "
            f"{fmt(s['vmaf_worst'])} ({s['worst_clip'].replace('bmcam00', 'c')}) | "
            f"{fmt(s['vmaf_mean'])} | {fmt(s['vmaf_p5_worst'])} | {fmt(s['s2_worst'])} | "
            f"{fmt(s['card_ssim_worst'], 4)} |"
        )
        if with_pi:
            line += (
                f" {s.get('pi_low_s', '–')}–{s.get('pi_high_s', '–')} | "
                f"{fmt(s.get('pi_rss_MiB'))} | {s.get('pi', '–')} |"
            )
        out.append(line)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    reg = registry()

    def std(c):
        cd = reg.get(c)
        return bool(cd) and cd.display_file is None and cd.display == "hold"

    def ph(c):
        return bool(reg.get(c)) and "posthoc" in reg[c].tags

    mac = latest(read_jsonl(a.run / "stage1" / "results.jsonl"))
    pi = latest(read_jsonl(a.run / "pi" / "results.jsonl"))
    cal = calibration(a.run / "pi") if (a.run / "pi").exists() else None

    def rank(rs):
        return sorted(rs, key=lambda s: (not s["complete"], -(s.get("vmaf_worst") or -1e9)))

    mac_rows = rank([summarize(mac, c, CLIPS, None, std(c)) for c in sorted({k[0] for k in mac})])
    pi_rows = rank(
        [summarize(pi, c, CLIPS, cal, std(c), ph(c)) for c in sorted({k[0] for k in pi})]
    )
    sens = [k[0] for k in pi if k[1] in SENS_CLIPS]
    sens_rows = rank([summarize(pi, c, SENS_CLIPS, cal, std(c), ph(c)) for c in sorted(set(sens))])
    dec = decide(pi_rows) if cal else {"winner": None, "why": "no Pi runs yet"}
    ceiling = next((s for s in mac_rows if s["complete"]), None)

    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "decision.json").write_text(
        json.dumps(
            {
                "calibration": cal,
                "decision": dec,
                "ceiling": ceiling,
                "mac": mac_rows,
                "pi": pi_rows,
                "sensitivity": sens_rows,
            },
            indent=1,
        )
    )
    md = ["## Stage 1 — Mac encoders (quality ceiling; no Pi measurement)", ""]
    md += table(mac_rows, False)
    if pi_rows:
        md += ["", "## Stages 2–3 — Pi recipes (container, emulated + calibrated)", ""]
        md += table(pi_rows, True)
    if sens_rows:
        md += ["", "## Stage 4 — sensitivity window (frames 150–199; not used for ranking)", ""]
        md += table(sens_rows, True)
    if cal:
        md += [
            "",
            f"Calibration: n = {cal['n']} PR #88 jobs; wall ratio median "
            f"{cal['wall_ratio_median']}, CPU ratio median {cal['cpu_ratio_median']} / max "
            f"{cal['cpu_ratio_max']}; stalls dropped: {len(cal['dropped_stalls'])}.",
        ]
    md += ["", "## Decision (PREREG v2 rule)", ""]
    md += [f"- {x}" for x in dec.get("steps", [dec.get("why", "")])]
    md.append(f"- **winner: {dec['winner']}**" if dec.get("winner") else "")
    (a.out / "results_table.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
