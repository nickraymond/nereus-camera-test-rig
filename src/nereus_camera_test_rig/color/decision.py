"""Stage ``decide`` — the S2a decision numbers (SPEC §4 Phase 8 S2a, §20 decision gate).

Reads ``correct/scores.json`` and compares every Nereus method with every baseline **within its
comparison class**, never across:

- **card-anchored** (the card in the frame is used): ΔE00 on the 12 colour patches — every grey
  is used to neutralize, so ψ would be circular;
- **card-free** (no card used): ψ on the greys, and ΔE00.

For each (Nereus method, baseline) pair, on the frames both scored (paired): each median, the
median paired difference with a **sweep-level bootstrap** 95 % CI (frames of a sweep are
correlated; a frame outside a sweep is its own cluster), the paired win rate, and the
pre-registered rule of SPEC §20: Nereus ≥ ``ABS_MIN`` **and** ≥ ``REL_MIN`` better than the
baseline, the CI of the difference excluding 0, win rate ≥ ``WIN_MIN``. The rule is written for
ψ in degrees; on ΔE00 it is applied unchanged, as an adaptation, and flagged. The blind visual
review decides first (SPEC §20); these numbers support it. Everything is reported overall, per
held-out dive and per card condition, with n per method × dive × condition. GRVI is reported as
production runs it (its no-card frames are the camera JPEG) and on the frames where it found the
card. Flash frames are reported apart.

The visual sheets — column cut sheet and blind side-randomized review — are built by
``decision_sheets``. Saved blind answers — ``<dataset config>_blind_answers.json`` next to the
dataset config, or its ``blind_answers`` key; versioned human work — are scored here.

Output: ``decide/{decision.json, index.html, cutsheet.html, blind.html, stage.json}``.
"""

from __future__ import annotations

import csv
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..config import load_yaml
from .card import load_card
from .correct import COLUMNS, _truth_linear
from .decision_sheets import Images, blind_pairs, blind_sheet, cut_frames, cut_sheet, score_blind
from .report import STYLE
from .stages import verify_fresh, write_stage
from .water_model import fit_settings

ABS_MIN, REL_MIN, WIN_MIN = 3.0, 0.30, 0.70
N_BOOT, SEED, MIN_PAIRS = 2000, 20260927, 10
GRVI_FOUND = "grvi_cheeca_v3_card_found"
LABELS = {**COLUMNS, GRVI_FOUND: "GRVI cheeca_v3 (card found only)"}
CLASSES = {
    "card_anchored": {"title": "Card-anchored — the card in the frame is used",
                      "nereus": ("raw_card_wb", "raw_card_wb_haze"),
                      "baselines": ("grvi_cheeca_v3", GRVI_FOUND, "jpeg_card_wb"),
                      "metrics": ("de2000_median",)},
    "card_free": {"title": "Card-free — no card used",
                  "nereus": ("raw_depth_wb_haze", "raw_depth_wb_haze_loso"),
                  "baselines": ("camera_jpeg", "olympus_preset_jpeg"),
                  "metrics": ("psi_median", "de2000_median")},
}
METRIC_NAMES = {"psi_median": "ψ (°)", "de2000_median": "ΔE00"}


def value(frame: dict, method: str, metric: str) -> Optional[float]:
    v = frame["methods"].get(method, {}).get(metric)
    return None if v is None else float(v)


def sweep_bootstrap(diff: np.ndarray, clusters: np.ndarray, n_boot: int = N_BOOT,
                    seed: int = SEED) -> tuple[float, float]:
    """95 % CI of the median paired difference, resampling whole clusters (sweeps)."""
    groups = [diff[clusters == c] for c in np.unique(clusters)]
    rng = np.random.default_rng(seed)
    stats = np.empty(n_boot)
    for b in range(n_boot):
        pick = rng.integers(0, len(groups), len(groups))
        stats[b] = np.median(np.concatenate([groups[i] for i in pick]))
    lo, hi = np.percentile(stats, [2.5, 97.5])
    return float(lo), float(hi)


def compare(scores: dict, nereus: str, baseline: str, metric: str) -> dict[str, Any]:
    """Paired comparison on the frames of ``scores`` where both methods have ``metric``."""
    rows = [(value(v, nereus, metric), value(v, baseline, metric), v.get("sweep_id") or s)
            for s, v in scores.items()]
    rows = [r for r in rows if r[0] is not None and r[1] is not None]
    out: dict[str, Any] = {"nereus": nereus, "baseline": baseline, "metric": metric,
                           "n_frames": len(rows), "n_sweeps": len({r[2] for r in rows})}
    if len(rows) < MIN_PAIRS:
        return {**out, "verdict": "too few pairs"}
    a, b = np.array([r[0] for r in rows]), np.array([r[1] for r in rows])
    diff = a - b  # negative: Nereus better
    lo, hi = sweep_bootstrap(diff, np.array([r[2] for r in rows]))
    med_a, med_b = float(np.median(a)), float(np.median(b))
    gain, win = med_b - med_a, float(np.mean(a < b))
    checks = {"abs": gain >= ABS_MIN, "rel": med_b > 0 and gain / med_b >= REL_MIN,
              "ci": hi < 0, "win": win >= WIN_MIN}
    return {**out, "median_nereus": round(med_a, 2), "median_baseline": round(med_b, 2),
            "median_diff": round(float(np.median(diff)), 2), "ci95": [round(lo, 2), round(hi, 2)],
            "win_rate": round(win, 3), "checks": checks,
            "verdict": "pass" if all(checks.values()) else "fail"}


def n_table(scores: dict, methods) -> dict[str, dict]:
    """{method: {dive: {condition: [frames, patches]}}} over the frames a method scored."""
    out: dict[str, dict] = {}
    for m in methods:
        for v in scores.values():
            s = v["methods"].get(m)
            if not s or (s.get("de2000_median") is None and s.get("psi_median") is None):
                continue
            cell = out.setdefault(m, {}).setdefault(v["dive_id"], {}).setdefault(
                v["card_condition"], [0, 0])
            cell[0] += 1
            cell[1] += int(s.get("n_de", 0)) + int(s.get("n_psi", 0))
    return out


def preset_pairs(scores: dict) -> dict[str, Any]:
    """Olympus preset JPEG vs the nearest A-mode frame of the same dive (SPEC S2a baseline b):
    the preset's own output next to the camera JPEG and Nereus no-card on that A-mode frame."""
    cols = (("preset", "olympus_preset_jpeg"), ("a_mode", "camera_jpeg"),
            ("a_mode", "raw_depth_wb_haze"))
    rows = []
    for s, v in sorted(scores.items()):
        pair = v.get("pair_a_mode")
        if v.get("flash") or not pair or pair["stem"] not in scores:
            continue
        src = {"preset": v, "a_mode": scores[pair["stem"]]}
        rows.append({"preset": s, "a_mode": pair["stem"], "dive_id": v["dive_id"],
                     "dt_s": pair["dt_s"], "depth_diff_m": pair["depth_diff_m"],
                     **{f"{m}@{w}:{k}": value(src[w], m, k) for w, m in cols
                        for k in METRIC_NAMES}})
    keys = [f"{m}@{w}:{k}" for w, m in cols for k in METRIC_NAMES]
    med = {k: round(float(np.median(x)), 2) for k in keys
           for x in [[r[k] for r in rows if r[k] is not None]] if x}
    return {"n": len(rows), "medians": med, "pairs": rows}


def needs_v3(scores: dict, qc: dict, correct_summary: dict, flagged: dict,
             comparisons: list) -> list[str]:
    """What this dataset cannot answer; every item is computed from the data."""
    main = [s for s, v in scores.items() if not v.get("flash")]
    items, usable = [], {}
    for s in main:
        for pid, p in qc[s]["patches"].items():
            usable.setdefault(pid, []).append(bool(p["usable"]))
    for pid, u in sorted(usable.items()):
        if 1 - np.mean(u) > 0.5:
            items.append(f"Patch {pid} is excluded on {1 - np.mean(u):.0%} of scored frames "
                         f"(card damage or clipping): not measured reliably.")
    clean = sum(scores[s]["card_condition"] == "clean" for s in main)
    items.append(f"Only {clean} of {len(main)} scored frames show the undamaged card; every "
                 f"clean-card number rests on them.")
    if correct_summary.get("card_haze_cap_bound_frames"):
        items.append(f"Card haze is capped at the image dark floor on "
                     f"{correct_summary['card_haze_cap_bound_frames']} frames: haze cannot be "
                     f"separated from the print's non-linearity without measured card values "
                     f"(OQ-40).")
    for d, why in sorted(flagged.items()):
        items.append(f"Dive {d} is flagged ({why}): not a like-for-like light test.")
    if correct_summary.get("grvi_no_card_frames"):
        items.append(f"GRVI found no card on {len(correct_summary['grvi_no_card_frames'])} "
                     f"scored frames (native-scale detection); there its output is the camera "
                     f"JPEG.")
    for n, b in sorted({(c["nereus"], c["baseline"]) for c in comparisons
                        if c["verdict"] == "too few pairs"}):
        items.append(f"{LABELS[n]} vs {LABELS[b]}: fewer than {MIN_PAIRS} paired frames overall.")
    return items


def with_grvi_found(scores: dict) -> dict:
    """Copy of ``scores`` with GRVI restricted to the frames where it found the card."""
    out = {}
    for s, v in scores.items():
        g = v["methods"].get("grvi_cheeca_v3")
        methods = dict(v["methods"])
        if g and not g.get("grvi_no_card"):
            methods[GRVI_FOUND] = g
        out[s] = {**v, "methods": methods}
    return out


def decide_numbers(scores: dict) -> dict[str, Any]:
    main = with_grvi_found({s: v for s, v in scores.items() if not v.get("flash")})
    splits = {"dive": sorted({v["dive_id"] for v in main.values()}),
              "card": sorted({v["card_condition"] for v in main.values()})}
    key = {"dive": "dive_id", "card": "card_condition"}
    classes = {}
    for cls, spec in CLASSES.items():
        comps = []
        for metric in spec["metrics"]:
            for n in spec["nereus"]:
                for b in spec["baselines"]:
                    c = compare(main, n, b, metric)
                    for split, values in splits.items():
                        c[f"by_{split}"] = {x: compare({s: v for s, v in main.items()
                                                        if v[key[split]] == x}, n, b, metric)
                                            for x in values}
                    comps.append(c)
        classes[cls] = {"comparisons": comps,
                        "n": n_table(main, spec["nereus"] + spec["baselines"])}
    flash = {s: {m: {k: v["methods"][m].get(k) for k in METRIC_NAMES} for m in v["methods"]}
             for s, v in scores.items() if v.get("flash")}
    return {"classes": classes, "flash": flash, "preset_pairs": preset_pairs(scores),
            "rule": {"abs_min": ABS_MIN, "rel_min": REL_MIN, "win_min": WIN_MIN,
                     "n_boot": N_BOOT, "seed": SEED, "min_pairs": MIN_PAIRS,
                     "note": "SPEC §20 states the rule for ψ (°); applied unchanged to ΔE00 "
                             "as an adaptation (to confirm)."}}


# --- HTML ---------------------------------------------------------------------------------

def _e(v: Any) -> str:
    return html.escape("—" if v is None else str(v))


def _pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.0%}"


def _comparison_rows(comps: list, split: Optional[tuple] = None) -> str:
    out = []
    for c0 in comps:
        c = c0[f"by_{split[0]}"][split[1]] if split else c0
        ch = c.get("checks") or {}
        marks = " ".join("✓" if ch[k] else "✗" for k in ("abs", "rel", "ci", "win")) if ch else "—"
        ci = f"[{c['ci95'][0]}, {c['ci95'][1]}]" if "ci95" in c else ""
        out.append(f"<tr><td>{_e(METRIC_NAMES[c['metric']])}</td><td>{_e(LABELS[c['nereus']])}"
                   f"</td><td>{_e(LABELS[c['baseline']])}</td><td>{c['n_frames']} · "
                   f"{c['n_sweeps']}</td><td>{_e(c.get('median_nereus'))}</td>"
                   f"<td>{_e(c.get('median_baseline'))}</td><td>{_e(c.get('median_diff'))} {ci}"
                   f"</td><td>{_pct(c.get('win_rate'))}</td><td>{marks}</td>"
                   f"<td class=\"v {c['verdict'].split()[0]}\">{_e(c['verdict'])}</td></tr>")
    return ("<div class=\"scroll\"><table><thead><tr><th>metric</th><th>Nereus</th>"
            "<th>baseline</th><th>frames · sweeps</th><th>Nereus median</th>"
            "<th>baseline median</th><th>median diff [95 % CI]</th><th>win rate</th>"
            "<th>abs rel CI win</th><th>rule</th></tr></thead><tbody>" + "".join(out)
            + "</tbody></table></div>")


def _n_table(n: dict, dives: list) -> str:
    head = "".join(f"<th>dive {d} clean</th><th>dive {d} damaged</th>" for d in dives)
    rows = "".join(
        f"<tr><td>{_e(LABELS[m])}</td>"
        + "".join(f"<td>{' · '.join(map(str, n[m].get(d, {}).get(c, [0, 0])))}</td>"
                  for d in dives for c in ("clean", "damaged")) + "</tr>" for m in n)
    return (f"<div class=\"scroll\"><table><thead><tr><th>method (frames · patches)</th>{head}"
            f"</tr></thead><tbody>{rows}</tbody></table></div>")


def render(result: dict, provenance: dict) -> str:
    sections = []
    dives = sorted({d for cls in result["classes"].values() for m in cls["n"].values()
                    for d in m})
    for cls, spec in CLASSES.items():
        comps = result["classes"][cls]["comparisons"]
        per = "".join(f"<h3>{kind} {_e(x)}</h3>{_comparison_rows(comps, (kind, x))}"
                      for kind in ("dive", "card")
                      for x in sorted(comps[0][f"by_{kind}"]))
        sections.append(f"<h2>{_e(spec['title'])}</h2>{_comparison_rows(comps)}"
                        f"<details><summary>Per held-out dive and per card condition</summary>"
                        f"{per}</details><h3>n per method × dive × card condition</h3>"
                        f"{_n_table(result['classes'][cls]['n'], dives)}")
    pp = result["preset_pairs"]
    med = pp["medians"]
    preset = ("<table><thead><tr><th></th><th>ψ (°)</th><th>ΔE00</th></tr></thead><tbody>"
              + "".join(f"<tr><td>{_e(label)}</td><td>{_e(med.get(k + ':psi_median'))}</td>"
                        f"<td>{_e(med.get(k + ':de2000_median'))}</td></tr>"
                        for k, label in (("olympus_preset_jpeg@preset", "Olympus preset JPEG"),
                                         ("camera_jpeg@a_mode", "nearest A-mode: camera JPEG"),
                                         ("raw_depth_wb_haze@a_mode",
                                          "nearest A-mode: Nereus no card")))
              + "</tbody></table>")
    flash = "".join(f"<tr><td>{_e(s)}</td><td>{_e(LABELS.get(m, m))}</td>"
                    f"<td>{_e(v['psi_median'])}</td><td>{_e(v['de2000_median'])}</td></tr>"
                    for s, ms in sorted(result["flash"].items()) for m, v in ms.items())
    needs = "".join(f"<li>{_e(x)}</li>" for x in result["needs_v3"])
    blind = result.get("blind")
    if blind:
        review = "".join(f"<tr><td>{_e(cls)}</td><td>{v['nereus']}</td><td>{v['baseline']}</td>"
                         f"<td>{v['tie']}</td><td>{_pct(v['prefer_nereus'])}</td>"
                         f"<td class=\"v {'pass' if v['rule_pass'] else 'fail'}\">"
                         f"{'pass' if v['rule_pass'] else 'fail'}</td></tr>"
                         for cls, v in blind.items())
        review = ("<table><thead><tr><th>class</th><th>prefer Nereus</th><th>prefer baseline"
                  "</th><th>no preference</th><th>Nereus share</th><th>≥ 70 %</th></tr>"
                  f"</thead><tbody>{review}</tbody></table>")
    else:
        review = "<p class=\"note\"><b>Blind review not done yet.</b></p>"
    rule = result["rule"]
    prov = "".join(f"<tr><td>{_e(k)}</td><td><code>{_e(v)}</code></td></tr>"
                   for k, v in provenance.items())
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>S2a Decision Report</title>
<style>{STYLE} .v.pass{{color:var(--s3);font-weight:600}} .v.fail{{color:var(--critical)}}
.v.too{{color:var(--muted)}} li{{margin:4px 0;max-width:95ch}}</style></head><body><main>
<h1>Phase 8 · S2a decision report</h1>
<p class="note">Generated {datetime.now():%Y-%m-%d %H:%M} by <code>python -m host_tools.color
decide</code>. <b>Nick's blind visual review decides first</b> (SPEC §20); these numbers support
it. Each class is compared only within itself. Paired: a comparison uses only the frames both
methods scored. Differences are Nereus − baseline (negative = Nereus better); the 95 % CI comes
from a sweep-level bootstrap ({rule['n_boot']} resamples). Rule (SPEC §20): Nereus
≥ {rule['abs_min']} and ≥ {rule['rel_min']:.0%} better on the median, CI excluding 0, win rate
≥ {rule['win_min']:.0%}. {_e(rule['note'])} Card-anchored columns are scored on the 12 colour
patches only (their greys are used); card-free columns on the greys (ψ) and colours (ΔE00).
GRVI solves on every card patch, so its card-anchored scores are in-sample.</p>
<h2>Visual review</h2><p class="note"><a href="blind.html">Blind review</a>
({result.get('blind_pairs', 0)} pairs, sides randomized, method names hidden) — decides the gate.
<a href="cutsheet.html">Cut sheet</a> — every method side by side on {result.get('cut_frames', 0)}
frames.</p>{review}
{"".join(sections)}
<h2>Olympus underwater preset vs the nearest A-mode frame</h2>
<p class="note">Each preset frame against the nearest A-mode reference frame of the same dive
(different exposure, same site, {pp['n']} pairs). Medians.</p>{preset}
<h2>Flash frames (scored apart)</h2><p class="note">The preset fired its flash; only the
camera's outputs are scored.</p><table><thead><tr><th>frame</th><th>method</th><th>ψ (°)</th>
<th>ΔE00</th></tr></thead><tbody>{flash}</tbody></table>
<h2>Needs the V3 dataset</h2><ul>{needs}</ul>
<h2>Provenance</h2><table><tbody>{prov}</tbody></table>
</main></body></html>"""


def decide(correct_dir: Path, dataset_config: Path, card_path: Path) -> dict[str, Any]:
    record = verify_fresh(correct_dir)
    root = correct_dir.parent
    scores = json.loads((correct_dir / "scores.json").read_text())
    correct_summary = json.loads((correct_dir / "summary.json").read_text())
    qc = json.loads((root / "qc" / "qc.json").read_text())
    result = decide_numbers(scores)
    comps = [c for cls in result["classes"].values() for c in cls["comparisons"]]
    result["needs_v3"] = needs_v3(scores, qc, correct_summary,
                                  fit_settings(dataset_config)["flagged_dives"], comps)
    out_dir = root / "decide"
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = load_yaml(dataset_config)
    configs = [dataset_config, card_path]
    answers = dataset_config.parent / cfg.get("blind_answers",
                                              f"{dataset_config.stem}_blind_answers.json")
    if answers.is_file():
        result["blind"] = score_blind(json.loads(answers.read_text()))
        configs.append(answers)
    ingest_dir = root / "ingest"
    rows = {r["stem"]: r for r in csv.DictReader((ingest_dir / "manifest.csv").open())}
    card = load_card(card_path)
    truth = {pid: _truth_linear(card, pid) for pid in
             [p.id for p in card.patches] + [sp.id for sp in card.sub_patches]}
    patches = json.loads((root / "patches" / "patches.json").read_text())
    images = Images(root, Path(verify_fresh(ingest_dir)["params"]["dataset_dir"]), rows, scores,
                    patches, truth)
    rendered = {p.stem for p in (correct_dir / "images" / "raw_depth_wb_haze").glob("*.jpg")}
    frames = cut_frames(rows, scores, rendered)
    pairs = blind_pairs(rows, scores, rendered)
    result.update(cut_frames=len(frames), blind_pairs=len(pairs))
    (out_dir / "cutsheet.html").write_text(cut_sheet(images, frames, rows, scores))
    try:
        shown = answers.resolve().relative_to(Path.cwd())
    except ValueError:
        shown = answers.resolve()
    (out_dir / "blind.html").write_text(blind_sheet(images, pairs, str(shown)))
    provenance = {"dataset": root.name, "correct stage git": record["git"],
                  "GRVI backend commit": correct_summary.get("grvi_backend_sha")}
    (out_dir / "decision.json").write_text(json.dumps(result, indent=1) + "\n")
    (out_dir / "index.html").write_text(render(result, provenance))
    write_stage(out_dir, "decide", configs=configs, upstream=[correct_dir],
                params=result["rule"])
    return {"out_dir": str(out_dir), "needs_v3": result["needs_v3"],
            "cut_frames": len(frames), "blind_pairs": len(pairs), "blind": result.get("blind"),
            "verdicts": {cls: [f"{METRIC_NAMES[c['metric']]} {LABELS[c['nereus']]} vs "
                               f"{LABELS[c['baseline']]}: {c['verdict']}"
                               for c in v["comparisons"]]
                         for cls, v in result["classes"].items()}}
