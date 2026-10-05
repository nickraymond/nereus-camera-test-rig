"""Sheet for the TG-7 RAW crop x budget study (tg7_budget.py), with the reef JPEGs as a labelled
scene-complexity check and bmcam004's daylight frame as the real-IMX708 point.

    NRJXL_BM_DIR=... python -m compression_study.preview_loss.tg7_sheet --results tg7_budget.json \
        --reef-results reef_budget.json --tg7 <primary>/data/tg7_channel_islands --out <dir>/index.html
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

import numpy as np
from PIL import Image

from compression_study.presets.keepable import LIGHTBOX, Sink
from compression_study.preview_loss import reef_budget as R
from compression_study.preview_loss import tg7_budget as T
from compression_study.preview_loss.reef_sheet import r4_d, r4_msgs

GROUPS = {"all": None, "kelp / reef scenes (3_, 4_)": ("3_scene_card_offcenter", "4_no_card"),
          "card sweeps + preset + surface (0_, 1_, 2_)": ("0_surface_card", "1_reference_A_iso100",
                                                          "2_underwater_preset")}


def P(v, q):
    v = [x for x in v if x is not None]
    return None if not v else float(np.percentile(v, q))


def f(v, nd=1):
    return "—" if v is None else f"{v:.{nd}f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--reef-results", type=Path)
    ap.add_argument("--tg7", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--embed", action="store_true")
    a = ap.parse_args()
    res = json.loads(a.results.read_text())
    rows = res["summary"]
    e = html.escape
    sink = Sink(a.out.parent, a.embed)

    def sel(group):
        cats = GROUPS[group]
        return [r for r in rows if cats is None or r["category"] in cats]

    def dval(r, crop, b):
        c = r[crop]["budgets"][str(b)]
        return c["d"] if c else 15.1

    # ---- distribution table
    dist = []
    for g in GROUPS:
        rs = sel(g)
        for crop in R.CROPS:
            cells = "".join(
                f'<td class=num>{f(P([dval(r, crop, b) for r in rs], 50))} / '
                f'{f(P([dval(r, crop, b) for r in rs], 90))}'
                + (f' <span class=warn>({sum(dval(r, crop, b) > 10.4 for r in rs)} &gt; 10.4)</span>'
                   if any(dval(r, crop, b) > 10.4 for r in rs) else "") + "</td>"
                for b in (180, 250, 500))
            m45 = [r[crop]["msgs_for_d"]["4.5"] for r in rs]
            extra = ""
            if crop == "1600x900":
                for k in ("msgs_match_pjpg_ssim_lo", "msgs_match_pjpg_ssim", "msgs_match_pjpg_detail"):
                    v = [r[crop][k] for r in rs]
                    nn = sum(x is None for x in v)
                    extra += (f'<td class=num>{f(P(v, 50), 0)} / {f(P(v, 90), 0)}'
                              f'{f" ({nn} never)" if nn else ""}</td>')
            else:
                extra = "<td></td><td></td><td></td>"
            dist.append(f'<tr><td>{e(g) if crop == "800x450" else ""}</td><td>{crop}</td>{cells}'
                        f'<td class=num>{f(P(m45, 50), 0)} / {f(P(m45, 90), 0)}</td>{extra}</tr>')
    dist.append(f'<tr><td>bmcam004 R4 daylight foliage (real IMX708, unit log)</td><td>1600x900</td>'
                f'<td class=num>{r4_d(180):.1f} <span class=warn>(&gt; 10.4)</span></td>'
                f'<td class=num>{r4_d(250):.1f}</td><td class=num>{r4_d(500):.1f}</td>'
                f'<td class=num>{r4_msgs(4.5)}</td><td></td><td></td><td></td></tr>')
    if a.reef_results:
        rr = [r for r in json.loads(a.reef_results.read_text())["summary"]["rows"]
              if r["kind"] == "reef_jpeg"]
        rd = {b: [r["1600x900"]["budgets"][str(b)]["d"] if r["1600x900"]["budgets"][str(b)] else 15.1
                  for r in rr] for b in (180, 250, 500)}
        rm = [r["1600x900"]["msgs_for_d"]["4.5"] for r in rr]
        dist.append('<tr><td>Reference reef JPEGs (9, JPEG-derived — sanity check, not RAW)</td>'
                    '<td>1600x900</td>' + "".join(f'<td class=num>{f(P(rd[b], 50))} / {f(P(rd[b], 90))}</td>'
                                                   for b in (180, 250, 500))
                    + f'<td class=num>{f(P(rm, 50), 0)} / {f(P(rm, 90), 0)}</td><td></td><td></td><td></td></tr>')

    # ---- colour (card patch dE, 1600x900)
    card = [r for r in rows if r["1600x900"]["budgets"]["180"] and
            r["1600x900"]["budgets"]["180"].get("patch_de") is not None]
    de = {b: [r["1600x900"]["budgets"][str(b)]["patch_de"] for r in card if r["1600x900"]["budgets"][str(b)]]
          for b in (180, 250, 500)}
    de_pj = [r["pjpg"]["patch_de"] for r in card]

    # ---- per scene
    per = []
    for r in rows:
        c = r["1600x900"]
        b = c["budgets"]
        per.append(
            f'<tr><td>{e(r["category"])}</td><td>{e(r["name"])}</td><td class=num>{e(str(r["depth_m"]))}</td>'
            + "".join(f'<td class=num>{f(b[str(x)]["d"] if b[str(x)] else None)}</td>' for x in (180, 250, 500))
            + f'<td class=num>{r["pjpg"]["quality"]}</td>'
              f'<td class=num>{r["pjpg"]["ssim_lo"]:.3f} / {r["pjpg"]["ssim"]:.3f}</td>'
              f'<td class=num>{f(b["180"]["ssim_lo"] if b["180"] else None, 3)} / {f(b["180"]["ssim"] if b["180"] else None, 3)}</td>'
              f'<td class=num>{f(r["pjpg"]["patch_de"], 2)} / {f(b["180"]["patch_de"] if b["180"] else None, 2)}</td>'
              f'<td class=num>{c["msgs_for_d"]["4.5"]}</td><td class=num>{f(c["msgs_match_pjpg_ssim_lo"], 0)}</td>'
              f'<td class=num>{f(c["msgs_match_pjpg_ssim"], 0)}</td><td class=num>{f(c["msgs_match_pjpg_detail"], 0)}</td></tr>')

    # ---- 1:1 strip on the median-complexity kelp frame (4_no_card)
    kelp = sorted([r for r in rows if r["category"] == "4_no_card"],
                  key=lambda r: r["1600x900"]["msgs_for_d"]["4.5"])
    pick = kelp[len(kelp) // 2]
    from host_tools.tg7.orf_io import read_orf
    fr = read_orf(a.tg7 / "raw" / pick["category"] / f"{pick['name']}.orf")
    scene, _, _ = T.map_to_imx(fr, (1600, 900))
    ref_lin = R.render_lin(scene["mosaic"], scene)
    scale = 0.9 / float(np.percentile(ref_lin[..., 1], 99.5))
    ref8 = R.to8(ref_lin, scale)
    cut = (600, 300, 400, 300)

    def patch(img):
        return img[cut[1]:cut[1] + cut[3], cut[0]:cut[0] + cut[2]]
    strip = [("TG-7 RAW resampled to IMX708 geometry (reference render)", patch(ref8))]
    pj, pjm = R.pjpg_today(ref8, (0, 0, 1600, 900))
    strip.append((f"today's pjpg ({pjm['messages']} msgs, q{pjm['quality']}, 1000×562 shown at 1:1)",
                  patch(np.asarray(Image.fromarray(pj).resize((1600, 900), Image.Resampling.LANCZOS)))))
    for b in (180, 250, 500):
        d = pick["1600x900"]["budgets"][str(b)]["d"]
        _, mos = R.nrjxl_bytes_and_decode(scene, (0, 0, 1600, 900), min(d, 15.0))
        strip.append((f"nrjxl {b} msgs (d {d:.1f})", patch(R.to8(R.render_lin(mos, scene), scale))))
    strip_html = "".join(f'<figure>{sink.img(im, t, 800, lossless=True, pixelated_inline=True)}'
                         f'<figcaption>{e(t)}</figcaption></figure>' for t, im in strip)

    # ---- answer numbers
    allr = sel("all")
    kel = sel("kelp / reef scenes (3_, 4_)")
    m45 = [r["1600x900"]["msgs_for_d"]["4.5"] for r in allr]
    m45k = [r["1600x900"]["msgs_for_d"]["4.5"] for r in kel]
    mlo = [r["1600x900"]["msgs_match_pjpg_ssim_lo"] for r in allr]
    mdt = [r["1600x900"]["msgs_match_pjpg_detail"] for r in allr]
    d180 = [dval(r, "1600x900", 180) for r in allr]
    n_fb = sum(d > 10.4 for d in d180)
    sel_counts = {}
    for s in res["selection"]:
        sel_counts[s["category"]] = sel_counts.get(s["category"], 0) + 1
    page = '''<title>TG-7 nrjxl Budget</title>
<style>
:root{--bg:#f2f5f6;--panel:#fff;--ink:#10202a;--ink2:#465a66;--rule:#d3dde2;--acc:#1b6f8f;--accbg:#e1eff5;--warn:#b5532a}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#0c1419;--panel:#131f26;--ink:#e2edf2;--ink2:#a8bcc6;--rule:#26363f;--acc:#5ab8dc;--accbg:#10303d;--warn:#e98f63}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#0c1419;--panel:#131f26;--ink:#e2edf2;--ink2:#a8bcc6;--rule:#26363f;--acc:#5ab8dc;--accbg:#10303d;--warn:#e98f63}
body{background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;padding-inline:16px;padding-block:18px 40px}
main{max-width:1500px;margin:0 auto;display:grid;gap:14px} h1{font-size:1.45rem;margin:0} h2{font-size:1.08rem;margin:0}
p,ul{margin:0;max-width:120ch} .muted{color:var(--ink2)} .warn{color:var(--warn)} section{background:var(--panel);border:1px solid var(--rule);border-radius:6px;padding:12px;display:grid;gap:8px}
.rec{border-left:4px solid var(--acc);background:var(--accbg)}
.wrap{overflow-x:auto} table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:.84rem} th,td{padding:4px 8px;border-bottom:1px solid var(--rule);text-align:left;vertical-align:top} .num{text-align:right;white-space:nowrap}
.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px} figure{margin:0;display:grid;gap:4px} figcaption{font-size:.8rem;color:var(--ink2)}
img{width:100%;height:auto;border-radius:3px}
</style>''' + LIGHTBOX + f'''<main>
<h1>nrjxl message budget on Nick's TG-7 RAW set</h1>
<p class="muted">Desk study, 2026-10-05. <b>TG-7 raw, resampled to IMX708 geometry — an approximation.</b> {len(rows)} ORFs from <code>data/tg7_channel_islands</code> (Channel Islands kelp + V2 card, 2026-09-15/16). Production encoder (bm_cam_legacy #120 <code>rc_raw_jxl</code>, modular, effort 5); a budget's distance is the byte-target point at 97 % fill (log–log interpolation on a 16-point grid, ±1.5 % vs the exact search). Today's pjpg made the production way from the same data (render → 1000×562 Lanczos → progressive JPEG on the quality ladder under 195 msgs). Quality: luma SSIM at today's delivered size (1000×562) and at native size, a fine-detail ratio, and ΔE00 on the V2 card patches inside the crop — all vs the RAW render (no noise-free reference exists for real RAW).</p>
<section class="rec"><h2>Answer (1600×900, all {len(allr)} frames, P50 / P90)</h2><ul>
<li><b>Distance at 180 msgs:</b> {f(P(d180, 50))} / {f(P(d180, 90))}; {n_fb} of {len(allr)} need d &gt; 10.4. At 250: {f(P([dval(r, "1600x900", 250) for r in allr], 50))} / {f(P([dval(r, "1600x900", 250) for r in allr], 90))}; at 500: {f(P([dval(r, "1600x900", 500) for r in allr], 50))} / {f(P([dval(r, "1600x900", 500) for r in allr], 90))}.</li>
<li><b>Messages for d 4.5:</b> {f(P(m45, 50), 0)} / {f(P(m45, 90), 0)} (kelp/reef scenes {f(P(m45k, 50), 0)} / {f(P(m45k, 90), 0)}). bmcam004 daylight (real IMX708): {r4_msgs(4.5)}; reference reef JPEGs (JPEG-derived): 541 / 681.</li>
<li><b>Messages to match today's pjpg at today's size (SSIM at 1000×562):</b> {f(P(mlo, 50), 0)} / {f(P(mlo, 90), 0)}; to match its fine detail at native size: {f(P(mdt, 50), 0)} / {f(P(mdt, 90), 0)}.</li>
<li><b>Colour:</b> on {len(card)} frames with ≥ 4 card patches in the crop, patch ΔE00 vs RAW is {f(P(de[180], 50), 2)} for nrjxl at 180 msgs vs {f(P(de_pj, 50), 2)} for today's pjpg (P50; P90 {f(P(de[180], 90), 2)} vs {f(P(de_pj, 90), 2)}).</li>
<li><b>Range:</b> the TG-7 underwater set is the easy end (blue water, soft kelp at depth); bmcam004 daylight foliage and the reef JPEGs are the hard end. A budget that holds d 4–5 on real daylight / reef texture needs ~350–550 msgs at 1600×900; on these underwater scenes {f(P(m45, 90), 0)} (P90) is enough.</li>
</ul></section>
<section><h2>Distance and messages across scenes (P50 / P90)</h2><div class="wrap"><table>
<tr><th>set</th><th>crop</th><th class=num>d at 180 msgs</th><th class=num>d at 250</th><th class=num>d at 500</th><th class=num>msgs for d 4.5</th><th class=num>msgs = pjpg SSIM @1000×562</th><th class=num>msgs = pjpg SSIM native</th><th class=num>msgs = pjpg detail</th></tr>
{"".join(dist)}</table></div>
<p class="muted">"&gt; 10.4" = scenes above the d_max quality floor (production would send pjpg). Native-size SSIM vs the noisy RAW render partly rewards reproducing sensor noise, so it is the strict bar; the 1000×562 SSIM compares at the size today's users see.</p></section>
<section><h2>1:1 kelp texture — {e(pick["name"])} ({e(pick["category"])}, {e(str(pick["depth_m"]))} m, median-complexity no-card frame)</h2>
<div class="strip">{strip_html}</div></section>
<section><h2>Selection and mapping</h2><ul>
<li><b>Selection:</b> by capture time within each folder (spreads dives and depths); flash and diver-torch frames excluded. {", ".join(f"{k} {v}" for k, v in sorted(sel_counts.items()))}. Kelp / reef scenes (3_, 4_) weighted most; card frames give colour, no-card frames texture.</li>
<li><b>Mapping:</b> for each IMX708 ROI (w×h of 4608×2592) the TG-7 crop covers the same FOV fraction (w/4608 of the TG-7 width, 16:9, centred: 1394×784 TG-7 px for 1600×900); each CFA plane Lanczos-resampled to the IMX708 pixel count (×1.148); TG-7 CFA (GRBG) kept; 10-bit levels (black 64, white 1023).</li>
<li><b>Sensor difference:</b> TG-7 noise as captured (12-bit, 1.55 µm, ISO 100, A-mode metering), no IMX708 noise added; resampling correlates it slightly. A real IMX708 frame of the same scene would carry its own noise (σ² ≈ 0.045·v + 0.3 DN² at base gain) — not modelled here.</li>
<li><b>Card positions:</b> the S1 locate (auto) and the versioned manual clicks (<code>configs/datasets/tg7_channel_islands_manual_corners.json</code>).</li>
</ul></section>
<section><h2>Per frame, 1600×900</h2><div class="wrap"><table>
<tr><th>folder</th><th>frame</th><th class=num>depth m</th><th class=num>d @180</th><th class=num>d @250</th><th class=num>d @500</th><th class=num>pjpg q</th><th class=num>pjpg SSIM 1000 / native</th><th class=num>nrjxl@180 SSIM 1000 / native</th><th class=num>patch ΔE pjpg / nrjxl@180</th><th class=num>msgs d 4.5</th><th class=num>msgs = pjpg @1000</th><th class=num>= native</th><th class=num>= detail</th></tr>
{"".join(per)}</table></div></section>
</main>'''
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(page, encoding="utf-8")
    if sink.files:
        (a.out.parent / "files.json").write_text(json.dumps(sink.files, indent=1))
    print(a.out, len(sink.files), "files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
