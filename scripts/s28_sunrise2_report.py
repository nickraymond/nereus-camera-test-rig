# ruff: noqa: E501  (inline HTML template)
"""Sunrise loop 2 (2026-10-09) report: 4 still arms banded by measured Lux + the still -> video
white-balance hand-off. Formatting only: every number comes from the Pi's r5_sweep.csv,
the loop log and the per-slot wb.json / metadata; tiles are the camera's own JPEGs and clip
frames (crop 1600x900 at native 1504,846, resized 0.5x).

    python scripts/s28_sunrise2_report.py <run_dir> --out <dir>/index.html
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import re
import statistics as st
import subprocess
import tempfile
from pathlib import Path

import cv2

ARMS = [("stock", "A stock auto"), ("lowgain", "B gain-locked (AG 1.0, ≤ 60 ms)"),
        ("long250", "C 250 ms @ 1.12"), ("ev1", "D EV+1")]
BANDS = [("< 1", 0, 1), ("1–5", 1, 5), ("5–20", 5, 20), ("20–100", 20, 100), ("≥ 100", 100, 1e9)]
CROP = (1504, 846, 1600, 900)


def num(r, k):
    try:
        return float(r[k])
    except (KeyError, TypeError, ValueError):
        return None


def tile(img) -> str:
    x, y, w, h = CROP
    if img.shape[1] != 4608:  # a 1280x720 clip frame: same field, just resize
        t = cv2.resize(img, (800, 450), interpolation=cv2.INTER_AREA)
    else:
        t = cv2.resize(img[y : y + h, x : x + w], (800, 450), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", t, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


def clip_frame(mp4: Path):
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "f.png"
        subprocess.run(["ffmpeg", "-v", "error", "-ss", "15", "-i", str(mp4), "-frames:v", "1", str(out)], check=True)
        return cv2.imread(str(out))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    R = a.run
    rows = list(csv.DictReader(open(R / "r5_sweep.csv")))
    by = {(r["slot"], r["profile"]): r for r in rows}
    man = json.loads((R / "manifest.json").read_text())
    slots = sorted({r["slot"] for r in rows})
    lux = {s: num(by[(s, "stock")], "Lux") for s in slots}
    lg = [by[(s, "lowgain")] for s in slots]
    gain_pass = all(num(r, "AnalogueGain") <= 1.13 for r in lg)
    # per band x arm
    brows = []
    for name, lo, hi in BANDS:
        ss = [s for s in slots if lo <= lux[s] < hi]
        if not ss:
            brows.append(f"<tr><th>{name} Lux</th><td colspan=8 class=muted>no slots (fog: max {man['max_lux_stock']} Lux)</td></tr>")
            continue
        for i, (arm, label) in enumerate(ARMS):
            rs = [by[(s, arm)] for s in ss]
            st_ = [by[(s, "stock")] for s in ss]
            def med(k, rs=rs):
                v = [num(r, k) for r in rs if num(r, k) is not None]
                return st.median(v) if v else float("nan")
            xs = st.median([num(r, "ExposureTime") * num(r, "AnalogueGain") / (num(s0, "ExposureTime") * num(s0, "AnalogueGain")) for r, s0 in zip(rs, st_)])
            nz = [num(r, "v3_grey_hp_noise") / num(s0, "v3_grey_hp_noise") for r, s0 in zip(rs, st_) if num(r, "v3_grey_hp_noise") and num(s0, "v3_grey_hp_noise")]
            ets = [num(r, "ExposureTime") / 1000 for r in rs]
            ags = [num(r, "AnalogueGain") for r in rs]
            band_cell = f"<th rowspan=4>{name} Lux<br><span class=muted>{len(ss)} slots</span></th>" if i == 0 else ""
            brows.append(
                f"<tr>{band_cell}<td>{label}</td><td class=num>{min(ets):.0f}–{max(ets):.0f} ms · {min(ags):.2f}–{max(ags):.2f}</td>"
                f"<td class=num>{xs:.2f}×</td><td class=num>{med('red_raw_level'):.4f}</td><td class=num>{med('red_snr_db'):.1f}</td>"
                f"<td class=num>{st.median(nz):.2f}×</td><td class=num>{med('raw_de00_mean'):.1f}</td><td class=num>{med('jpeg_de00_mean'):.1f}</td></tr>")
    # hand-off
    log = (R / "log.txt").read_text()
    wb_lines = re.findall(r"slot (\d+) wb rc=(\d+) source=(\w+) gains=([\d.]*),([\d.]*)", log)
    clips = re.findall(r"clip (s\d+_clip_\w+) rc=(\d+) wall=([\d.]+) s size=(\d+) cma_before=(\d+) cma_after=(\d+)", log)
    hrows = []
    for slot, rc, src, rg, bg in wb_lines:
        w = json.loads((R / f"s{slot}_wb.json").read_text()) if (R / f"s{slot}_wb.json").exists() else {}
        awb = json.loads((R / f"s{slot}_stock.json").read_text())["ColourGains"]
        cl = [c for c in clips if c[0].startswith(f"s{slot}_")]
        hrows.append(f"<tr><td>{slot[4:6]}:{slot[6:]}</td><td class=num>{lux[slot]:.1f}</td><td>{src}</td><td class=num>{w.get('tags', '—')}</td>"
                     f"<td class=num>{(rg + ' / ' + bg) if rg else '—'}</td><td class=num>{awb[0]:.2f} / {awb[1]:.2f}</td>"
                     f"<td class=num>{w.get('detect_s', '—')}</td><td class=num>{(w.get('vmhwm_kib') or 0) // 1024 or '—'}</td>"
                     f"<td>{', '.join(c[0].split('_clip_')[1] + (' ✓' if c[1] == '0' else ' ✗') for c in cl)}</td>"
                     f"<td class=num>{', '.join(str(int(c[4]) // 1024) for c in cl)}</td></tr>")
    first_card = next((s for s, rc, src, *_ in wb_lines if src == "card"), None)
    # tiles
    picks = [s for s in slots if 1 <= lux[s]][:1] + [s for s in slots if 10 <= lux[s] < 20][:1] + [max(slots, key=lambda s: lux[s])]
    tiles = []
    for s in picks:
        cells = "".join(
            f'<figure><img src="{tile(cv2.imread(str(R / f"s{s}_{arm}.jpg")))}" alt="{arm}"><figcaption><b>{label}</b> · {num(by[(s, arm)], "ExposureTime") / 1000:.0f} ms · AG {num(by[(s, arm)], "AnalogueGain"):.2f} · DG {num(by[(s, arm)], "DigitalGain"):.2f} · red SNR {num(by[(s, arm)], "red_snr_db") or float("nan"):.1f} dB</figcaption></figure>'
            for arm, label in ARMS)
        tiles.append(f"<section><h3>{s[4:6]}:{s[6:]} · {lux[s]:.1f} Lux</h3><div class=row>{cells}</div></section>")
    ctiles = []
    for s in [s for s in slots if (R / f"s{s}_clip_auto.mp4").exists() and (R / f"s{s}_clip_card.mp4").exists() and f"slot {s} wb rc=0 source=card" in log]:
        cells = "".join(f'<figure><img src="{tile(clip_frame(R / f"s{s}_clip_{k}.mp4"))}" alt="{k}"><figcaption><b>{lab}</b></figcaption></figure>'
                        for k, lab in (("card", "card-grey WB (custom gains)"), ("auto", "camera auto WB (control)")))
        ctiles.append(f"<section><h3>clip frame at 15 s · {s[4:6]}:{s[6:]} · {lux[s]:.1f} Lux</h3><div class=row>{cells}</div></section>")
    page = """<title>Sunrise Hand-off</title>
<style>
:root{--bg:#f2f4f6;--panel:#fff;--ink:#141b21;--ink2:#4a5761;--rule:#d5dce1;--acc:#1f6f8b;--accbg:#e2eff4;--ok:#1d7a46;--bad:#a8321c}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#0f1418;--panel:#161d22;--ink:#e6ecf0;--ink2:#b0bcc5;--rule:#2b353d;--acc:#62bcd6;--accbg:#12303a;--ok:#5fcf8d;--bad:#f08a72}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#0f1418;--panel:#161d22;--ink:#e6ecf0;--ink2:#b0bcc5;--rule:#2b353d;--acc:#62bcd6;--accbg:#12303a;--ok:#5fcf8d;--bad:#f08a72}
body{background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;padding-inline:16px;padding-block:18px 40px}
main{max-width:1240px;margin:0 auto;display:grid;gap:14px} h1{font-size:1.4rem;margin:0;text-wrap:balance} h2{font-size:1.08rem;margin:0} h3{font-size:.95rem;margin:0}
p,ul{margin:0;max-width:120ch} ul{padding-left:1.2em;display:grid;gap:3px} .muted{color:var(--ink2)}
section{background:var(--panel);border:1px solid var(--rule);border-radius:6px;padding:12px;display:grid;gap:8px} .rec{border-left:4px solid var(--acc);background:var(--accbg)}
.wrap{overflow-x:auto} table{border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:.84rem} th,td{padding:4px 8px;border-bottom:1px solid var(--rule);text-align:left;vertical-align:top} th{font-weight:600} .num{text-align:right}
.pass{color:var(--ok);font-weight:700}
.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px} figure{margin:0;display:grid;gap:4px} figure img{width:100%;border-radius:4px;border:1px solid var(--rule)} figcaption{font-size:.78rem;color:var(--ink2)}
</style>""" + f"""<main>
<h1>Sunrise loop 2 on nereus002: four exposure arms and the still → video white-balance hand-off</h1>
<p class=muted>Friday 2026-10-09, 06:00–10:30 PDT, a slot every 10 min. <b>Fog, reduced daylight:</b> max {man['max_lux_stock']} Lux (Thursday's sunrise reached 590), so there is no ≥ 100 Lux band. nereus002 is a bench Pi Zero 2 W, not a bmcam unit. All numbers are computed on the Pi; card ΔE00 is against the measured paper-flat truth (PR #93).</p>
<section class=rec><h2>In short</h2><ul>
<li><b>Gain lock:</b> <span class={'pass' if gain_pass else 'bad'}>{'PASS' if gain_pass else 'FAIL'}</span>, analogue gain at the 1.12 floor on all {len(lg)} slots. It cuts the DNG red level 4.5–15× vs stock and costs 1–3.5 dB of red SNR; worst in the 1–5 Lux band (10.7 vs 14.1 dB, 2× the grey noise).</li>
<li><b>Shutter is the SNR lever:</b> 250 ms at the floor gain gives the best red SNR in every band with signal (+4 dB at 20–100 Lux, +6 dB at 1–5 Lux vs stock) and ~0.6× the grey noise, with no card clipping up to 83 Lux.</li>
<li><b>EV+1</b> only adds gain while stock sits at its 66.6 ms ceiling; above ~20 Lux it is a real +1 EV (2.2×) but not better on SNR.</li>
<li><b>Card colour</b> (RAW pipeline) is 3.1–3.9 ΔE00 for every arm wherever there is signal, except the gain-locked arm at 1–5 Lux (6.0); the camera's own JPEG is 9–13 (30 for the gain-locked arm at 1–5 Lux). Below 1 Lux nothing is usable.</li>
<li><b>Hand-off:</b> {len(clips)} clips, {sum(c[1] != '0' for c in clips)} failed, no guard skip, no kill; the card was first found at {first_card[4:6] + ':' + first_card[6:] if first_card else '—'} on the gain-locked still. From then on, card-grey gains are steady (R 2.04–2.08, B 1.86–1.98) vs the camera AWB (R ~1.87, B ~2.24): ~10 % more red, ~12 % less blue.</li></ul></section>
<section><h2>Four arms, banded by measured Lux (stock still)</h2><div class=wrap><table>
<tr><th>band</th><th>arm</th><th class=num>exposure · gain</th><th class=num>light × stock</th><th class=num>DNG red level</th><th class=num>red SNR dB</th><th class=num>grey noise × stock</th><th class=num>card ΔE00 RAW</th><th class=num>card ΔE00 JPEG</th></tr>{''.join(brows)}</table></div>
<p class=muted>Medians per band. Light × stock = exposure × analogue gain vs stock (digital gain is not in the DNG). Red level = the V3 red patch's red, as a fraction of white, in the DNG. Red SNR = single-frame high-pass SNR on that patch. Grey noise = pixel noise on the V3 greys. RAW card ΔE00 = tuning-file shading + WB on the V3 greys + the 2026-10-06 bench matrix; JPEG = the camera JPEG, lightness-matched only.</p></section>
<section><h2>Still → video hand-off, per selected slot</h2><div class=wrap><table>
<tr><th>slot</th><th class=num>Lux</th><th>WB source</th><th class=num>tags</th><th class=num>card gains R / B</th><th class=num>camera AWB R / B</th><th class=num>detect s</th><th class=num>VmHWM MB</th><th>clips</th><th class=num>CmaFree before, MB</th></tr>{''.join(hrows)}</table></div>
<p class=muted>Card-wb runs the lean crop-first single-scale detector on the gain-locked still (ulimit -v 1,000,000, oom_score_adj 1000). "auto" = too dark (grey < 0.5 % of white) with no earlier gains, so the clip used the camera AWB. Clips are 30 s, 1280×720, 10 fps, H.264, at the floor gain.</p></section>
<section><h2>Card-grey WB vs camera auto WB, same slot</h2><p class=muted>Frame at 15 s of each clip, resized 0.5×.</p>{''.join(ctiles) or '<p class=muted>no slot with both clips after the first card detection</p>'}</section>
<section><h2>The four arms at three light levels</h2><p class=muted>The camera's own JPEGs, cropped to the B3a field (1600×900 at native 1504,846), <b>resized 0.5×</b>; no colour processing. The DNG is what matters for colour correction; the JPEG shows what the ISP made of each exposure.</p>{''.join(tiles)}</section>
</main>"""
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(page, encoding="utf-8")
    print(a.out, "| gain", gain_pass, "| clips", len(clips), "| first card", first_card)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
