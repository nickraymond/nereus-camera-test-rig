"""One sheet for roi_budget.py's answer cell: RAW | nrjxl | pjpg, uncorrected and card-corrected.

    python -m compression_study.presets.roi_budget_sheet

Reads runs/s28_roi_budget_20261002/{summary.json, table.csv, chroma.json, img/*.webp} and
writes sheet/index.html + sheet/img/ (publish with the Artifact tool's `files`). Today's pjpg
covers only today's 1600×900 crop: it is placed at its true position and scale on a grey
canvas of the picked ROI's size, so all three columns line up.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
from PIL import Image

RUN = Path(__file__).resolve().parent / "runs" / "s28_roi_budget_20261002"


def main() -> int:
    s = json.loads((RUN / "summary.json").read_text())
    rows = list(csv.DictReader((RUN / "table.csv").open()))
    chroma = json.loads((RUN / "chroma.json").read_text())
    rname, msgs = s["pick"]
    box, today = s["rois"][rname], s["rois"]["MEDIUM"]
    out = RUN / "sheet"
    (out / "img").mkdir(parents=True, exist_ok=True)
    files = {}
    for k in ("raw_unc", "raw_cc", "nrjxl_unc", "nrjxl_cc", "pjpg_unc", "pjpg_cc"):
        im = Image.open(RUN / "img" / f"{k}.webp").convert("RGB")
        if k.startswith("pjpg") and box != today:
            canvas = Image.new("RGB", (box[2], box[3]), (46, 50, 54))
            canvas.paste(im, (today[0] - box[0], today[1] - box[1]))
            im = canvas
        im.save(out / "img" / f"{k}.webp", "WEBP", lossless=True)
        files[f"img/{k}.webp"] = str(out / "img" / f"{k}.webp")

    cell = next(r for r in rows if r["roi"] == rname and r["budget_msgs"] == str(msgs))
    raw = next(r for r in rows if r["roi"] == rname and r["budget_msgs"] == "RAW")
    pj = s["pjpg"]
    cols = [
        ("RAW reference", "raw", f"{int(raw['bytes']) / 1e6:.1f} MB", f"{int(raw['messages']):,} (not sendable)",
         raw["de_truth"], "1.000", f"{box[2]}×{box[3]} native, uncompressed"),
        (f"nrjxl, {msgs}-message budget", "nrjxl", f"{int(cell['bytes']) / 1000:.1f} kB",
         cell["messages"], cell["de_truth"], cell["ssim"],
         f"{box[2]}×{box[3]} native, cjxl e5 d {float(cell['distance']):.2f}"),
        (f"Today's pjpg (q{pj['quality']})", "pjpg", f"{pj['bytes'] / 1000:.1f} kB", pj["messages"],
         f"{s['bar_de']:.2f}", f"{pj['ssim']:.3f}",
         "1000×562 upsampled 1.6×; covers only today's 1600×900 crop (grey = not sent)"),
    ]

    def fig(title, key, state):
        alt = f"{title}, {state}"
        return (f'<button class="img" type="button" aria-label="Open {alt} at full size">'
                f'<img src="img/{key}_{"unc" if state == "uncorrected" else "cc"}.webp" alt="{alt}"'
                f' loading="lazy"></button>')

    head = "".join(f"<th scope='col'>{c[0]}</th>" for c in cols)
    rows_html = ""
    for state, label, note in (
            ("uncorrected", "Uncorrected",
             "As the MEDIUM sheet: RAW and nrjxl white-balanced on grey 128 only, no colour matrix, "
             "gamma 2.2. pjpg as the camera delivers it."),
            ("card-corrected", "Card-corrected",
             "What Nick's pipeline sees: each image's own 3×3 matrix fitted to the card truth on the "
             "13 colour patches, then sRGB.")):
        rows_html += (f"<tr><th scope='row'><span class='state'>{label}</span><span class='small'>"
                      f"{note}</span></th>"
                      + "".join(f"<td>{fig(c[0], c[1], state)}</td>" for c in cols) + "</tr>")
    stats = "".join(
        f"<td><dl><div><dt>Size</dt><dd>{c[2]}</dd></div><div><dt>Messages</dt><dd>{c[3]}</dd></div>"
        f"<div><dt>ΔE vs card</dt><dd>{float(c[4]):.2f}</dd></div><div><dt>SSIM</dt><dd>{c[5]}</dd></div>"
        f"</dl><p class='small'>{c[6]}</p></td>" for c in cols)

    # the full grid
    rois = list(s["rois"])
    budgets = s["budgets"]
    grid = "<tr><th scope='col'>ROI</th>" + "".join(
        f"<th scope='col' class='num'>{b} msgs<br><span class='small'>{b * 288 / 1000:.1f} kB</span></th>"
        for b in budgets) + "</tr>"
    for rn in rois:
        b = s["rois"][rn]
        grid += f"<tr><th scope='row'>{rn}<br><span class='small'>{b[2]}×{b[3]}</span></th>"
        for bud in budgets:
            r = next(x for x in rows if x["roi"] == rn and x["budget_msgs"] == str(bud))
            if r["verdict"] == "does not fit":
                grid += ("<td class='cell nofit'><b>does not fit</b><span class='small'>"
                         f"{int(r['bytes']) / 1000:.1f} kB at d 15</span></td>")
                continue
            hit = " pick" if (rn, bud) == (rname, msgs) else ""
            grid += (f"<td class='cell {r['verdict'].lower()}{hit}'><b>{r['verdict']}</b>"
                     f"<span>ΔE {float(r['de_truth']):.2f} · SSIM {float(r['ssim']):.3f}</span>"
                     f"<span class='small'>d {float(r['distance']):.2f} · {int(r['bytes']):,} B</span></td>")
        grid += "</tr>"

    keys = ("gray_light", "gray_mid", "gray_dark", "paper")
    ch = "<tr><th scope='col'>Image</th><th scope='col'>State</th>" + "".join(
        f"<th scope='col' class='num'>{k.replace('gray_', 'grey ')}</th>" for k in keys) + "</tr>"
    for r in chroma:
        ch += (f"<tr><td>{r['image']}</td><td>{r['state']}</td>" + "".join(
            f"<td class='num'>{r[k][0]:+.1f}, {r[k][1]:+.1f}</td>" for k in keys) + "</tr>")

    html = f'''<meta charset="utf-8">
<title>ROI Budget Check</title>
<style>
:root{{--bg:#f3f5f6;--panel:#fff;--ink:#121a20;--ink2:#46535d;--rule:#d6dde2;--accent:#0f6f7c;
--pass:#1d6b3a;--passbg:#e3f2e8;--fail:#9b2c1f;--failbg:#f8e4e0;--nofit:#5d666d;--nofitbg:#eceff1}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{color-scheme:dark;--bg:#0f1418;--panel:#161d22;
--ink:#e6ecf0;--ink2:#b2bec7;--rule:#2a343c;--accent:#4fb3bf;--pass:#8fd6a6;--passbg:#163222;--fail:#f0a597;
--failbg:#3a1c17;--nofit:#a7b1b8;--nofitbg:#1f272d}}}}
:root[data-theme="dark"]{{color-scheme:dark;--bg:#0f1418;--panel:#161d22;--ink:#e6ecf0;--ink2:#b2bec7;
--rule:#2a343c;--accent:#4fb3bf;--pass:#8fd6a6;--passbg:#163222;--fail:#f0a597;--failbg:#3a1c17;
--nofit:#a7b1b8;--nofitbg:#1f272d}}
body{{background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;
padding-inline:16px;padding-block:20px 48px}}
main{{max-width:1500px;margin:0 auto;display:grid;gap:18px}}
h1{{font-size:1.6rem;margin:0}} h2{{font-size:1.15rem;margin:0 0 6px;text-wrap:balance}}
p{{margin:0;max-width:95ch}} .lede{{color:var(--ink2)}}
.answer{{background:var(--panel);border:1px solid var(--rule);border-left:4px solid var(--accent);
border-radius:6px;padding:12px 14px;font-size:1.05rem}}
.wrap{{overflow-x:auto}} table{{border-collapse:collapse;width:100%}}
th,td{{padding:6px 8px;border-bottom:1px solid var(--rule);text-align:left;vertical-align:top}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}
.cell{{font-variant-numeric:tabular-nums;min-width:120px}} .cell span{{display:block}}
.pass{{background:var(--passbg)}} .pass b{{color:var(--pass)}} .fail{{background:var(--failbg)}}
.fail b{{color:var(--fail)}} .nofit{{background:var(--nofitbg)}} .nofit b{{color:var(--nofit)}}
.pick{{outline:2px solid var(--accent);outline-offset:-2px}}
.small{{color:var(--ink2);font-size:.82rem}} .state{{display:block;font-weight:600}}
.sheet th[scope=row]{{width:200px;min-width:150px}} .sheet td{{width:30%}}
.img{{all:unset;cursor:zoom-in;display:block}} .img:focus-visible{{outline:2px solid var(--accent)}}
.img img{{width:100%;height:auto;display:block;border-radius:4px}}
dl{{display:grid;grid-template-columns:1fr 1fr;gap:2px 12px;margin:0 0 4px}}
dl div{{display:flex;justify-content:space-between;gap:6px;border-bottom:1px solid var(--rule)}}
dt{{color:var(--ink2)}} dd{{margin:0;font-weight:600;font-variant-numeric:tabular-nums}}
section{{background:var(--panel);border:1px solid var(--rule);border-radius:6px;padding:12px}}
#zoom{{position:fixed;inset:0;background:#000;overflow:auto;cursor:zoom-out;z-index:9}}
#zoom img{{display:block;margin:auto;max-width:none;image-rendering:pixelated}}
</style>
<main>
<h1>Largest ROI per message budget</h1>
<p class="lede">IMX708 on nereus002, cool lamp, one exposure. Every ROI at native sensor density. nrjxl = the
Sprint28 container (4 Bayer planes, sqrt curve, cjxl modular, production effort 5), with the lowest distance
that fits each budget (288-byte messages). Bar: ΔE below today's pjpg on the same 13 colour patches
(<b>{s['bar_de']:.2f}</b>; q{pj['quality']}, {pj['messages']} messages).</p>
<p class="answer"><b>Answer:</b> the largest ROI that passes is <b>LARGE ({box[2]}×{box[3]}), at {msgs} messages</b>
({int(cell['bytes']) / 1000:.1f} kB, ΔE {float(cell['de_truth']):.2f} vs bar {s['bar_de']:.2f}). It does not fit 120
messages even at distance 15, the top of the production range.</p>

<section><h2>ROI × budget</h2>
<div class="wrap"><table>{grid}</table></div>
<p class="small">ΔE = mean ΔE00 vs the V1 card truth on the 13 colour patches after each image's own 3×3 card
fit (the cloud step). RAW reference: ΔE {raw['de_truth']} on every ROI (the floor). SSIM = luma vs the RAW render of
the same ROI. Every cell that fits passes on colour, and ΔE sits at the RAW floor everywhere: the card fit on patch
means is not sensitive to compression. SSIM is where the cells differ. Today's pjpg SSIM is {pj['ssim']:.3f},
measured on its own crop after a tone match. Lever not tried yet: effort 7 (slower, smaller files).</p></section>

<section><h2>LARGE at {msgs} messages: RAW | nrjxl | pjpg</h2>
<p class="small">Click any image to open it at full size (1:1); click again or press Esc to close.
Top row: before the card fit. Bottom row: after it.</p>
<div class="wrap"><table class="sheet">
<tr><th></th>{head}</tr>{rows_html}<tr><th scope="row" class="small">Per image</th>{stats}</tr>
</table></div>
<p class="small"><b>Upsampled:</b> only today's pjpg (sent at 1000×562, shown 1.6× up at its true position).
RAW and nrjxl are native resolution.</p></section>

<section><h2>Why the pjpg whites looked more correct</h2>
<p>They are not more neutral, they are blue. In the MEDIUM sheet, RAW and nrjxl were shown <b>before</b> the card
fit: white-balanced on grey 128, no colour matrix, gamma 2.2, so the greys are neutral by construction (a*, b*
≈ 0). The pjpg is the camera's own render. Its AWB left a blue cast (b* −14 to −26; grey-light displays as
RGB 139, 165, 206 vs RAW 139, 138, 139), and its tone curve puts whites near 255. The RAW render of this −1 stop
frame has no tone curve, so its paper sits darker (99th percentile 211) and reads grey rather than white. Bright
and slightly blue tends to read as "clean white"; the numbers say the RAW greys are the neutral ones.</p>
<div class="wrap"><table>{ch}</table></div>
<p class="small">a*, b* (CIELAB, D65); 0, 0 = neutral. "paper" = the brightest unclipped card pixels in today's
crop. Grey-white is outside today's crop, so grey-light is the brightest patch here. nrjxl = MEDIUM at 176
messages, the cell shown in the earlier sheet. After the card fit the greys pick up b* −4 to −6, because the fit
uses only the 13 colour patches (no greys); including the greys in the fit would pin them.</p></section>
</main>
<div id="zoom" hidden><img alt="Full-size view"></div>
<script>
const z=document.getElementById('zoom'),zi=z.querySelector('img');
document.querySelectorAll('.img').forEach(b=>b.addEventListener('click',()=>{{zi.src=b.querySelector('img').src;z.hidden=false;}}));
z.addEventListener('click',()=>{{z.hidden=true;}});
document.addEventListener('keydown',e=>{{if(e.key==='Escape')z.hidden=true;}});
</script>'''
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / "files.json").write_text(json.dumps(files, indent=1))
    print(out / "index.html", sum(Path(p).stat().st_size for p in files.values()) / 1e6, "MB images")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
