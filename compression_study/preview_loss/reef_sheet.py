"""One sheet for the reef crop x budget study (reef_budget.py output).

    NRJXL_BM_DIR=... python -m compression_study.preview_loss.reef_sheet --results reef_budget.json \
        --reef <bm_cam_legacy>/reference_images --tuning imx708_wide_tuning.json --out <dir>/index.html
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

import numpy as np

from compression_study.presets.keepable import LIGHTBOX, Sink
from compression_study.preview_loss import reef_budget as R

KINDS = {"reef_jpeg": "Reference reef set (9 TG-7 JPEGs → IMX708 RAW-equivalent)",
         "tg7_raw": "TG-7 Channel Islands, real RAW (12 underwater kelp / reef scenes, other sensor)",
         "tg7_jpeg": "Same 12 TG-7 frames, JPEG → RAW-equivalent (method check)"}
# bmcam004 R4 wake 1, 2026-10-05 16:00Z (unit log, s28_ladder gate.log): two measured points
R4 = {"d": (3.499, 9.701), "bytes": (123170, 56136)}


def pct(v, q):
    v = [x for x in v if x is not None]
    return None if not v else float(np.percentile(v, q))


def r4_d(budget):
    import math
    (d1, d2), (b1, b2) = R4["d"], R4["bytes"]
    k = math.log(b1 / b2) / math.log(d2 / d1)
    return d2 * (b2 / (R.FILL * budget * 288)) ** (1 / k)


def r4_msgs(d):
    import math
    (d1, d2), (b1, b2) = R4["d"], R4["bytes"]
    k = math.log(b1 / b2) / math.log(d2 / d1)
    return math.ceil(b2 * (d2 / d) ** k / 288 / R.FILL)


def fmt(v, nd=1):
    return "—" if v is None else f"{v:.{nd}f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--reef", type=Path, required=True)
    ap.add_argument("--tuning", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--embed", action="store_true")
    a = ap.parse_args()
    res = json.loads(a.results.read_text())
    rows = res["summary"]["rows"]
    scenes = {(s["kind"], s["name"]): s for s in res["scenes"]}
    e = html.escape
    sink = Sink(a.out.parent, a.embed)

    # ---- distribution table per set x crop x budget
    dist = []
    for kind, label in KINDS.items():
        rs = [r for r in rows if r["kind"] == kind]
        for crop in R.CROPS:
            cells = []
            for b in (180, 250, 500):
                ds = [r[crop]["budgets"][str(b)]["d"] if r[crop]["budgets"][str(b)] else 15.1
                      for r in rs]
                floor = sum(d > 10.4 for d in ds)
                cells.append(f'<td class=num>{fmt(pct(ds, 50))} / {fmt(pct(ds, 90))}'
                             f'{f" <span class=warn>({floor} over d_max)</span>" if floor else ""}</td>')
            m45 = [r[crop]["msgs_for_d"]["4.5"] for r in rs]
            mq = [r[crop]["msgs_match_pjpg_ssim"] for r in rs]
            mqd = [r[crop]["msgs_match_pjpg_detail"] for r in rs]
            nm = sum(v is None for v in mq)
            cells.append(f'<td class=num>{fmt(pct(m45, 50), 0)} / {fmt(pct(m45, 90), 0)}</td>')
            cells.append(f'<td class=num>{fmt(pct(mq, 50), 0)} / {fmt(pct(mq, 90), 0)}'
                         f'{f" ({nm} never)" if nm else ""}</td>')
            cells.append(f'<td class=num>{fmt(pct(mqd, 50), 0)} / {fmt(pct(mqd, 90), 0)}</td>')
            dist.append(f'<tr><td>{e(label) if crop == "800x450" else ""}</td><td>{crop}</td>'
                        f'{"".join(cells)}</tr>')
    dist.append(f'<tr><td>bmcam004 R4 daylight foliage (real IMX708, unit log)</td><td>1600x900</td>'
                f'<td class=num>{r4_d(180):.1f} <span class=warn>(over d_max)</span></td>'
                f'<td class=num>{r4_d(250):.1f}</td><td class=num>{r4_d(500):.1f}</td>'
                f'<td class=num>{r4_msgs(4.5)}</td><td class=num>—</td><td class=num>—</td></tr>')

    # ---- per-scene (1600x900)
    per = []
    for r in rows:
        s = scenes[(r["kind"], r["name"])]
        c = r["1600x900"]
        b = c["budgets"]
        noisy = s.get("pjpg_noisy_isp")
        nss = f" / {noisy['ssim']:.3f}" if noisy else ""
        per.append(
            f'<tr><td>{e(r["kind"])}</td><td>{e(r["name"])}</td>'
            + "".join(f'<td class=num>{fmt(b[str(x)]["d"] if b[str(x)] else None)}</td>'
                      for x in (180, 195, 250, 500))
            + f'<td class=num>{s["pjpg"]["ssim"]:.3f}{nss}</td>'
            + "".join(f'<td class=num>{fmt(b[str(x)]["ssim"] if b[str(x)] else None, 3)}</td>'
                      for x in (180, 250, 500))
            + f'<td class=num>{c["msgs_for_d"]["4.5"]}</td>'
              f'<td class=num>{fmt(c["msgs_match_pjpg_ssim"], 0)}'
              f'{" / " + fmt(c.get("msgs_match_pjpg_noisy_isp_ssim"), 0) if noisy else ""}</td>'
              f'<td class=num>{fmt(c["msgs_match_pjpg_detail"], 0)}</td></tr>')

    # ---- method check: TG-7 real RAW vs JPEG-derived, same frames
    pairs = []
    for r in rows:
        if r["kind"] != "tg7_raw":
            continue
        j = next((x for x in rows if x["kind"] == "tg7_jpeg" and x["name"] == r["name"]), None)
        if j:
            pairs.append((r["name"], r["1600x900"]["msgs_for_d"]["4.5"], j["1600x900"]["msgs_for_d"]["4.5"]))
    ratio = [jv / rv for _, rv, jv in pairs if rv]

    # ---- 1:1 texture strip on the reef primary
    scene = R.synth_raw(np.asarray(__import__("PIL.Image", fromlist=["Image"]).open(
        a.reef / "prepared" / "P7071008" / "synthetic_native_4608x2592.jpg").convert("RGB")),
        R.tuning_ccm(a.tuning), 100)
    H, W = scene["mosaic"].shape
    box = ((W - 1600) // 2 // 2 * 2, (H - 900) // 2 // 2 * 2, 1600, 900)
    ref_lin = R.render_lin(scene["clean"], scene)
    scale = 0.9 / float(np.percentile(ref_lin[..., 1], 99.5))
    ref8 = R.to8(ref_lin, scale)
    x, y = box[0], box[1]
    cut = (x + 560, y + 300, 400, 300)                       # a textured 400x300 patch, 1:1

    def patch(img, ofs=(0, 0)):
        return img[cut[1] - ofs[1]:cut[1] - ofs[1] + cut[3], cut[0] - ofs[0]:cut[0] - ofs[0] + cut[2]]
    prim = next(r for r in rows if r["name"] == "reference_reef_coral_primary")["1600x900"]["budgets"]
    strip = [("RAW-equivalent scene (reference)", patch(ref8))]
    pj, pjm = R.pjpg_today(ref8, box)
    from PIL import Image
    up = np.asarray(Image.fromarray(pj).resize((1600, 900), Image.Resampling.LANCZOS))
    strip.append((f"today's pjpg ({pjm['messages']} msgs, q{pjm['quality']}, 1000×562 upscaled)",
                  patch(up, (x, y))))
    for b in (180, 250, 500):
        d = prim[str(b)]["d"]
        _, mos = R.nrjxl_bytes_and_decode(scene, box, min(d, 15.0))
        strip.append((f"nrjxl {b} msgs (d {d:.1f})", patch(R.to8(R.render_lin(mos, scene), scale), (x, y))))
    strip_html = "".join(f'<figure>{sink.img(im, t, 800, lossless=True, pixelated_inline=True)}'
                         f'<figcaption>{e(t)}</figcaption></figure>' for t, im in strip)

    reef_rows = [r for r in rows if r["kind"] == "reef_jpeg"]
    raw_rows = [r for r in rows if r["kind"] == "tg7_raw"]
    m45_raw = [r["1600x900"]["msgs_for_d"]["4.5"] for r in raw_rows]
    m45_small = [r["800x450"]["msgs_for_d"]["4.5"] for r in reef_rows]
    mqd_small = [r["800x450"]["msgs_match_pjpg_detail"] for r in reef_rows]
    mqd = [r["1600x900"]["msgs_match_pjpg_detail"] for r in reef_rows]
    qs = [scenes[("reef_jpeg", r["name"])]["pjpg"]["quality"] for r in reef_rows]
    q_range = f"{min(qs)}–{max(qs)}"
    n_fb195 = sum(1 for r in reef_rows if not r["1600x900"]["budgets"]["195"]
                  or r["1600x900"]["budgets"]["195"]["d"] > 10.4)
    n_none195 = sum(1 for r in reef_rows if not r["1600x900"]["budgets"]["195"])
    m45 = [r["1600x900"]["msgs_for_d"]["4.5"] for r in reef_rows]
    mq = [r["1600x900"]["msgs_match_pjpg_ssim"] for r in reef_rows]
    d195 = [r["1600x900"]["budgets"]["195"]["d"] if r["1600x900"]["budgets"]["195"] else 15.1
            for r in reef_rows]
    page = '''<title>Reef nrjxl Budget</title>
<style>
:root{--bg:#f2f5f5;--panel:#fff;--ink:#11201f;--ink2:#47595a;--rule:#d3dcdc;--acc:#16766f;--accbg:#e1f1ef;--warn:#b5532a}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#0d1515;--panel:#142020;--ink:#e3eeed;--ink2:#a9bcbb;--rule:#28393a;--acc:#5cc9bf;--accbg:#123331;--warn:#e98f63}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#0d1515;--panel:#142020;--ink:#e3eeed;--ink2:#a9bcbb;--rule:#28393a;--acc:#5cc9bf;--accbg:#123331;--warn:#e98f63}
body{background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;padding-inline:16px;padding-block:18px 40px}
main{max-width:1500px;margin:0 auto;display:grid;gap:14px} h1{font-size:1.45rem;margin:0} h2{font-size:1.08rem;margin:0}
p,ul{margin:0;max-width:120ch} .muted{color:var(--ink2)} .warn{color:var(--warn)} section{background:var(--panel);border:1px solid var(--rule);border-radius:6px;padding:12px;display:grid;gap:8px}
.rec{border-left:4px solid var(--acc);background:var(--accbg)}
.wrap{overflow-x:auto} table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:.84rem} th,td{padding:4px 8px;border-bottom:1px solid var(--rule);text-align:left;vertical-align:top} .num{text-align:right;white-space:nowrap}
.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px} figure{margin:0;display:grid;gap:4px} figcaption{font-size:.8rem;color:var(--ink2)}
img{width:100%;height:auto;border-radius:3px}
</style>''' + LIGHTBOX + f'''<main>
<h1>How many messages does nrjxl need on real reef scenes?</h1>
<p class="muted">Desk study, 2026-10-05, for Nick's nrjxl budget decision. Production encoder (bm_cam_legacy #120 <code>rc_raw_jxl</code>, modular, effort 5) on a distance grid; a budget's distance is the byte-target point at 97 % fill, interpolated in log–log (±1.5 % vs the exact search, measured). Messages are the production count at 288 B. Quality: luma SSIM and a fine-detail ratio on the 1600×900 region today's pjpg covers (800×450 crop: its own region). The reference is the noise-free scene for the JPEG-derived sets and the RAW render for real RAW. Today's pjpg is the production still path (1600×900 → 1000×562 Lanczos, q ladder under 195 msgs), tone-matched.</p>
<section class="rec"><h2>Answer</h2><ul>
<li><b>At today's 1600×900 crop and 195 msgs, the reference reef scenes do not fit nrjxl at a useful quality.</b> {n_fb195} of 9 need d above the 10.4 floor (production would send pjpg), {n_none195} of them do not fit even at d 15. Median d {fmt(pct(d195, 50))}, P90 {">15" if pct(d195, 90) > 15 else fmt(pct(d195, 90))}. Today's real bmcam004 daylight frame: d 9.70.</li>
<li><b>Messages needed at 1600×900 (reef set, P50 / P90):</b> to match today's pjpg fine detail {fmt(pct(mqd, 50), 0)} / {fmt(pct(mqd, 90), 0)}; to match its SSIM on full-resolution texture {fmt(pct(mq, 50), 0)} / {fmt(pct(mq, 90), 0)}; for d 4.5 {fmt(pct(m45, 50), 0)} / {fmt(pct(m45, 90), 0)}. bmcam004 daylight: d 4.5 at {r4_msgs(4.5)}.</li>
<li><b>Card scenes understate the budget about {fmt(pct(m45, 50) / 165, 1)}×:</b> the indoor card / pool frames need 142–194 msgs for d 4.5 (measured today on 4 frames), the reef median {fmt(pct(m45, 50), 0)}. Real TG-7 RAW (underwater kelp, mostly blue water) needs {fmt(pct(m45_raw, 50), 0)} / {fmt(pct(m45_raw, 90), 0)}.</li>
<li><b>Smaller crop:</b> 800×450 over the same scenes needs {fmt(pct(m45_small, 50), 0)} / {fmt(pct(m45_small, 90), 0)} msgs for d 4.5 and matches today's pjpg detail at {fmt(pct(mqd_small, 50), 0)} / {fmt(pct(mqd_small, 90), 0)}.</li>
<li><b>Why pjpg holds up on reef texture:</b> it sends a 1000×562 8-bit image that the camera ISP has already denoised (q {q_range} on these scenes); nrjxl sends 1.44 M native Bayer samples at 12 bit with their noise. Its advantages are native resolution, RAW colour and no ISP clipping, which cost ~1.5–3× the messages on dense reef texture.</li>
</ul></section>
<section><h2>Distance and messages across scenes (P50 / P90)</h2><div class="wrap"><table>
<tr><th>scene set</th><th>crop</th><th class=num>d at 180 msgs</th><th class=num>d at 250</th><th class=num>d at 500</th><th class=num>msgs for d 4.5</th><th class=num>msgs to match pjpg SSIM</th><th class=num>msgs to match pjpg detail</th></tr>
{"".join(dist)}</table></div>
<p class="muted">"over d_max" = scenes whose fit needs d &gt; 10.4 (production sends pjpg instead). TG-7 real RAW is a different sensor (12-bit, 1.55 µm, ISO 100 under water) and mostly blue-water kelp: it codes far smaller than the reef set, so it bounds the easy end, not the reef case. bmcam004: two points the unit measured itself (d 3.5 → 123,170 B; d 9.70 → 56,136 B), interpolated.</p></section>
<section><h2>1:1 reef texture (reference_reef_coral_primary, 400×300 px at native density)</h2>
<div class="strip">{strip_html}</div>
<p class="muted">Click to zoom (pixelated above 100 %). The pjpg panel is the 1000×562 image upscaled back to native density, which is what a viewer sees at 1:1.</p></section>
<section><h2>Per scene, 1600×900</h2><div class="wrap"><table>
<tr><th>set</th><th>scene</th><th class=num>d @180</th><th class=num>d @195</th><th class=num>d @250</th><th class=num>d @500</th><th class=num>pjpg SSIM (ideal / noisy ISP)</th><th class=num>nrjxl SSIM @180</th><th class=num>@250</th><th class=num>@500</th><th class=num>msgs for d 4.5</th><th class=num>msgs = pjpg SSIM (ideal / noisy ISP)</th><th class=num>msgs = pjpg detail</th></tr>
{"".join(per)}</table></div></section>
<section><h2>What the reference reef set is, and the method check</h2>
<ul>
<li><b>Set:</b> <code>bm_cam_legacy/reference_images/</code>, 9 Olympus TG-7 camera JPEGs, 4000×3000: <code>reference_reef_coral_primary</code> + <code>alt_01…07</code> (P7070996–P7071008, 2026-07-07, 1/125–1/1000 s, f/2.8, ISO 100) and <code>P9011394</code> (AOML reef with the V2 card, 2026-09-01). <b>No RAW exists for any of them.</b> Input: the Sprint06 IMX708-size stand-ins (<code>prepared/*/synthetic_native_4608x2592.jpg</code>: 16:9 centre crop, Lanczos ×1.152).</li>
<li><b>RAW-equivalent (approximation):</b> sRGB → linear → inverse IMX708 CCM and WB (imx708_wide tuning, 5715 K) → exposure so the brightest 0.5 % sits at 75 % of full scale → BGGR 10-bit, black 64 → IMX708 noise σ² = 0.045·v + 0.3 DN² (S4 repeats, base gain). The JPEG's own denoise, sharpening, compression and the 1.152× upscale remain in it.</li>
<li><b>Method check:</b> the same path on {len(pairs)} TG-7 frames that also have real RAW: messages for d 4.5, JPEG-derived ÷ real RAW = median {fmt(pct(ratio, 50), 2)} (range {fmt(min(ratio) if ratio else None, 2)}–{fmt(max(ratio) if ratio else None, 2)}). Different sensor noise is part of that difference.</li>
</ul></section>
</main>'''
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(page, encoding="utf-8")
    if sink.files:
        (a.out.parent / "files.json").write_text(json.dumps(sink.files, indent=1))
    print(a.out, len(sink.files), "files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
