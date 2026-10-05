"""One visual sheet + the summary table for the preview / loss study.

    python -m compression_study.preview_loss.sheet --results results.json --traces traces.json \
        --pi pi_bench.json --out <dir>/index.html [--embed]
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

import numpy as np

from compression_study.presets.keepable import LIGHTBOX, Sink
from compression_study.preview_loss import frames as F
from compression_study.preview_loss import study as S
from compression_study.preview_loss.traces import summarise

SHOW = [("base", "Today (all-or-nothing)"), ("plane", "Today + plane-aware decode"),
        ("prog", "A · one progressive codestream"), ("prev2", "B · preview ×2 (10 msgs each)"),
        ("mdc", "C · 4 descriptions (polyphase)"), ("fec18", "D · FEC +18 parity (10 %)"),
        ("fec27", "D · FEC +27 parity (15 %)"), ("prev2+fec18", "B + D · preview ×2 + FEC +18")]
# real first sends from the HIL consoles (scaled to 180 slots)
TYPICAL, BAD = "0e63wv", "0e5kgv"


def agg(results, meth, pat, key):
    v = [fr["methods"][meth][pat][key] for fr in results["frames"].values()]
    v = [x for x in v if x is not None]
    return float(np.median(v)) if v else None


def full_quality(results, meth):
    """Median over frames of the complete image's SSIM / dE."""
    out = []
    for fr in results["frames"].values():
        dsc = fr["describe"]
        if meth in ("base", "plane"):
            q = dsc["base"]["full"]
        elif meth == "prog":
            q = dsc["prog"]["full"]
        elif meth == "mdc":
            q = dsc["mdc"]["full"]
        else:
            npv = int(meth[4]) if meth.startswith("prev") else 0
            m = int(meth.split("fec")[1]) if "fec" in meth else 0
            q = dsc[f"k{S.SLOTS - npv * S.PREV_MSGS - m}"]["full"]
        out.append((q["ssim"], q["de_med"]))
    a = np.median(np.array(out), 0)
    return round(float(a[0]), 3), round(float(a[1]), 2)


def overhead(meth):
    npv = int(meth[4]) if meth.startswith("prev") else 0
    m = int(meth.split("fec")[1]) if "fec" in meth else 0
    return npv * S.PREV_MSGS + m


PI = {"base": "0 (today)", "plane": "0", "prog": "same encode; cjxl peak RSS 68 → 123 MB",
      "prev2": "+1.4 s render + encode (2.9 kB)", "prev3": "+1.4 s",
      "mdc": "same encode time; +54–66 % bytes at equal d",
      "fec": "+0.07–0.08 s (numpy RS)", "prev2+fec": "+1.5 s"}
BACKEND = {"base": "today", "plane": "decode each complete plane; grey from G",
           "prog": "stock djxl --allow_partial_files on the prefix",
           "prev2": "decode a 2.9 kB JPEG XL; show until the full image",
           "prev3": "as prev2", "mdc": "4 decodes + interpolate missing phases",
           "fec": "RS decode (~0.1 s) when ≥ k of n chunks", "prev2+fec": "both"}
WIRE = {"base": "—", "plane": "none (container offsets already in chunk 0)",
        "prog": "new payload layout (one tiled codestream)",
        "prev2": "new chunk kind (preview copy) + its index range in START",
        "prev3": "as prev2", "mdc": "new container (4 descriptions, chunk-aligned)",
        "fec": "START carries k and m; chunk index ≥ k = parity; heal = 'any N more'",
        "prev2+fec": "both"}


def _key(meth, table):
    if meth.startswith("prev") and "fec" in meth:
        return table["prev2+fec"]
    if meth.startswith("fec"):
        return table["fec"]
    return table[meth]


def table_rows(results):
    rows = []
    for meth in S.METHODS:
        ss, de = full_quality(results, meth)
        rows.append({
            "method": meth, "overhead_msgs": overhead(meth),
            "complete_ssim": ss, "complete_de": de,
            "tr_visible": agg(results, meth, "traces", "p_visible"),
            "tr_colour": agg(results, meth, "traces", "p_colour"),
            "tr_complete": agg(results, meth, "traces", "p_complete"),
            "tr_shown_ssim": agg(results, meth, "traces", "ssim_shown_med"),
            "b8_complete": agg(results, meth, "burst8_any", "p_complete"),
            "b8_colour": agg(results, meth, "burst8_any", "p_colour"),
            "b16_complete": agg(results, meth, "burst16_any", "p_complete"),
            "tail40_colour": agg(results, meth, "tail40", "p_colour"),
            "iid5_complete": agg(results, meth, "iid5", "p_complete"),
            "pi": _key(meth, PI), "backend": _key(meth, BACKEND), "wire": _key(meth, WIRE)})
    return rows


def pct(v):
    return "—" if v is None else f"{100 * v:.0f} %"


def markdown_table(rows) -> str:
    h = ("| method | extra msgs (of 180) | complete: SSIM / ΔE00 | traces: visible / colour / "
         "complete | 8-run anywhere: complete | 16-run anywhere: complete | tail 40: colour "
         "shown | i.i.d. 5 %: complete | Pi Zero cost | backend | wire contract |\n"
         "|---|---|---|---|---|---|---|---|---|---|---|\n")
    for r in rows:
        h += (f"| {r['method']} | {r['overhead_msgs']} | {r['complete_ssim']:.3f} / "
              f"{r['complete_de']:.2f} | {pct(r['tr_visible'])} / {pct(r['tr_colour'])} / "
              f"{pct(r['tr_complete'])} | {pct(r['b8_complete'])} | {pct(r['b16_complete'])} | "
              f"{pct(r['tail40_colour'])} | {pct(r['iid5_complete'])} | {r['pi']} | "
              f"{r['backend']} | {r['wire']} |\n")
    return h


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--traces", type=Path, required=True)
    ap.add_argument("--pi", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--frame", default="s4_cool")
    ap.add_argument("--embed", action="store_true")
    a = ap.parse_args()
    res = json.loads(a.results.read_text())
    traces = json.loads(a.traces.read_text())
    tsum = summarise(traces)
    pi = json.loads(a.pi.read_text())
    rows = table_rows(res)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    (a.out.parent / "table.md").write_text(markdown_table(rows))
    sink = Sink(a.out.parent, a.embed)
    e = html.escape

    name, dng, xywh, note = next(f for f in F.frames() if f[0] == a.frame)
    fr = S.Frame(name, dng, xywh, note)
    by_key = {t["key"]: t for t in traces}
    cases = [("typical", by_key[TYPICAL]), ("bad", by_key[BAD])]
    cols = []
    for label, t in cases:
        lost = S.scale(t)
        rr = S.runs((~lost).astype(int).tolist())
        cols.append((label, t, lost, ", ".join(f"{s}–{s + ln - 1}" for s, ln in rr)))
    grid = ['<div class="row head"><div></div>'
            + "".join(f'<div><b>{"Typical" if lb == "typical" else "Bad"} wake</b>: {int(lost.sum())} '
                      f'of 180 chunks lost (slots {e(rng)}; real {e(t["key"])}, '
                      f'{e(t["run"].split("_")[0])}, {t["lost"]}/{t["n"]})</div>'
                      for lb, t, lost, rng in cols) + "</div>"]
    for meth, title in SHOW:
        cells = []
        for lb, t, lost, rng in cols:
            img, ok, kind = S.outcome(fr, meth, lost)
            if img is None:
                cells.append('<div class="none">nothing to show<br><span class="muted">waits '
                             'for a heal (2–3 wakes, up to ~3 h)</span></div>')
            else:
                from compression_study.preview_loss.codec import score
                sc = score(img, fr.ref)
                tag = {"full": "complete", "grey": "grey (planes that arrived)",
                       "preview": "preview 320×180", "prefix": "progressive prefix"}.get(kind, kind)
                cells.append(f'<div>{sink.img(img, f"{title}, {lb}", 800, lossless=False, quality=88)}'
                             f'<div class="cap"><b>{e(tag)}</b> · SSIM {sc["ssim"]:.3f} · '
                             f'ΔE00 {sc["de_med"]:.2f}</div></div>')
        grid.append(f'<div class="row"><div class="mname">{e(title)}</div>{"".join(cells)}</div>')
    ref = sink.img(fr.ref, "RAW reference", 800, lossless=False, quality=90)

    def tr(r):
        return (f'<tr><td>{e(r["method"])}</td><td class=num>{r["overhead_msgs"]}</td>'
                f'<td class=num>{r["complete_ssim"]:.3f} / {r["complete_de"]:.2f}</td>'
                f'<td class=num>{pct(r["tr_visible"])}</td><td class=num>{pct(r["tr_colour"])}</td>'
                f'<td class=num>{pct(r["tr_complete"])}</td><td class=num>{pct(r["b8_complete"])}</td>'
                f'<td class=num>{pct(r["b16_complete"])}</td><td class=num>{pct(r["tail40_colour"])}</td>'
                f'<td>{e(r["pi"])}</td><td>{e(r["backend"])}</td><td>{e(r["wire"])}</td></tr>')
    rl = ", ".join(f"{k}×{v}" for k, v in tsum["run_lengths"].items())
    page = '''<title>nrjxl Loss Preview</title>
<style>
:root{--bg:#f3f5f6;--panel:#fff;--ink:#121a20;--ink2:#46535d;--rule:#d6dde2;--acc:#1f7a8c;--accbg:#e3f1f4;--warn:#c0632a}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#0f1418;--panel:#161d22;--ink:#e6ecf0;--ink2:#b2bec7;--rule:#2a343c;--acc:#5fbfd1;--accbg:#12303a;--warn:#e8915a}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#0f1418;--panel:#161d22;--ink:#e6ecf0;--ink2:#b2bec7;--rule:#2a343c;--acc:#5fbfd1;--accbg:#12303a;--warn:#e8915a}
body{background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;padding-inline:16px;padding-block:18px 40px}
main{max-width:1500px;margin:0 auto;display:grid;gap:14px} h1{font-size:1.45rem;margin:0} h2{font-size:1.08rem;margin:0}
p,ul{margin:0;max-width:120ch} .muted{color:var(--ink2)} section{background:var(--panel);border:1px solid var(--rule);border-radius:6px;padding:12px;display:grid;gap:8px}
.rec{border-left:4px solid var(--acc);background:var(--accbg)}
.wrap{overflow-x:auto} table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:.84rem} th,td{padding:4px 8px;border-bottom:1px solid var(--rule);text-align:left;vertical-align:top} .num{text-align:right;white-space:nowrap}
.grid{display:grid;gap:8px} .row{display:grid;grid-template-columns:180px 1fr 1fr;gap:10px;align-items:start} .row.head{font-size:.85rem}
.mname{font-weight:600;padding-top:4px} .cap{font-size:.8rem;color:var(--ink2);font-variant-numeric:tabular-nums}
.none{aspect-ratio:16/9;max-width:100%;display:grid;place-content:center;text-align:center;border:1px dashed var(--rule);border-radius:3px;color:var(--warn);font-weight:600}
img{width:100%;height:auto;border-radius:3px}
@media (max-width:700px){.row{grid-template-columns:1fr}}
</style>''' + LIGHTBOX + f'''<main>
<h1>nrjxl stills under real chunk loss: what the user sees after the first send</h1>
<p class="muted">Desk study, 2026-10-05. Production encoder (bm_cam_legacy #120 <code>rc_raw_jxl</code>, modular, effort 5), 1600×900 IMX708 crops, 180 messages of 288 B, every method fitted to the same budget. Loss = the first sends recorded on the HIL Spotter consoles (G4 outdoor 12 h, R1-fix, C1 comms), scaled to 180 slots, plus synthetic stress patterns. Quality vs the RAW crop rendered at 800×450 (one WB/exposure per frame): luma SSIM and median CIEDE2000 of 8×8 blocks. Click any image to zoom.</p>
<section class="rec"><h2>Recommendation</h2>
<ul>
<li><b>Ship erasure coding (D) first.</b> Reed–Solomon parity over the chunks: any k of n rebuild the whole file, no heal. +18 parity chunks (10 %) completes <b>{pct(agg(res, "fec18", "traces", "p_complete"))}</b> of real first sends (today {pct(agg(res, "base", "traces", "p_complete"))}) and every single 8- or 16-chunk run anywhere; +27 (15 %) completes {pct(agg(res, "fec27", "traces", "p_complete"))}. Cost: the image is coded at a higher distance (SSIM {full_quality(res, "fec18")[0]:.3f} vs {full_quality(res, "base")[0]:.3f}), ~0.08 s on the Pi Zero, ~0.1 s to decode, and heals become "send any N more parity chunks".</li>
<li><b>Make the backend plane-aware now (free).</b> Decode each JPEG XL plane whose chunks all arrived: with no change on the camera, {pct(agg(res, "plane", "traces", "p_visible"))} of first sends show something (grey when R or B is missing).</li>
<li><b>Add the small redundant preview (B) if a colour image on every wake matters more than ~0.005 SSIM:</b> a 320×180 colour JPEG XL rendered on the camera, 10 messages (2.9 kB), sent first and again last. With FEC +18 it shows colour on {pct(agg(res, "prev2+fec18", "traces", "p_colour"))} of real first sends and on a 40-chunk tail loss.</li>
<li><b>Not recommended:</b> A (progressive) — stock libjxl decodes nothing until ~{int(np.median([f["describe"]["prog"]["first_decodable_chunk"] for f in res["frames"].values()]))} of 180 chunks arrive in order, and a hole in the middle cuts everything after it; C (polyphase descriptions) — +54–66 % bytes at the same distance, so the complete image drops to SSIM {full_quality(res, "mdc")[0]:.3f}.</li>
</ul></section>
<section><h2>Real loss (HIL first sends)</h2>
<p>{tsum["first_sends"]} first sends with START and END on the console; {tsum["with_loss"]} lost chunks (median {tsum["lost_pct_median_lossy"]} % of the clip when lossy, max {max(tsum["lost_pct_all"])} %). Loss runs: {tsum["runs_middle"]} in the middle, {tsum["runs_tail"]} at the tail; run lengths {e(rl)} (chunks × count). Runs of 8 are the Spotter's cellular queue filling (<code>MS_Q_CELLULAR_ONLY is full</code>); the console-derived losses match the backend's heal ranges exactly where both exist. So loss is mostly <b>one or two 8-chunk holes anywhere</b>, not just the tail.</p></section>
<section><h2>Table (median over 4 frames)</h2><div class="wrap"><table>
<tr><th>method</th><th class=num>extra msgs</th><th class=num>complete SSIM / ΔE</th><th class=num>traces: visible</th><th class=num>colour</th><th class=num>complete</th><th class=num>8-run anywhere: complete</th><th class=num>16-run: complete</th><th class=num>tail 40: colour</th><th>Pi Zero 2 W</th><th>backend</th><th>wire contract</th></tr>
{"".join(tr(r) for r in rows)}</table></div>
<p class="muted">"visible" = the backend can show any image after the first send; "colour" = a colour image (complete, preview or progressive prefix), not a grey plane render. Pi Zero measured on nereus002 (run-7 frame, d 5.66): production 4-plane encode 7.5 s, preview {pi["preview_render_and_encode_s"]} s, RS encode {pi["rs_encode_k116_m18_s"]}–{pi["rs_encode_k116_m27_s"]} s, RS decode {pi["rs_decode_k116_m27_s"]} s.</p></section>
<section><h2>What the user sees — {e(note)}</h2>
<div class="grid">{"".join(grid)}</div>
<details><summary>RAW reference (800×450)</summary>{ref}</details></section>
</main>'''
    a.out.write_text(page, encoding="utf-8")
    if sink.files:
        (a.out.parent / "files.json").write_text(json.dumps(sink.files, indent=1))
    print(a.out, len(page) // 1000, "kB html,", len(sink.files), "files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
