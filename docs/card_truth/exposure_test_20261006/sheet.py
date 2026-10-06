import json, html, sys, numpy as np, cv2
from pathlib import Path
from keepable import LIGHTBOX, Sink
from nereus_camera_test_rig.color.raw_io import read_dng, normalize, demosaic_bilinear
D=Path(sys.argv[1]); out=Path(sys.argv[2]); out.parent.mkdir(parents=True,exist_ok=True)
R=json.load(open(D/"score.json")); A=R["arms"]; sink=Sink(out.parent,False); e=html.escape
ARMS=["auto","pin14500","pin29000","pin58000","pin66667","pin250000","pin500000"]
lab=lambda a: "auto" if a=="auto" else f"{int(a[3:])/1000:g} ms"
auto_eq=A["auto"]["exposure_us"]*A["auto"]["gain"]/1.123
# 1:1 crop around the V3 red patch + CC top rows: same display brightness for every arm (scaled by exposure x gain)
ref=read_dng(D/"auto_r0.dng"); l0,_,c0=normalize(ref); rgb0=demosaic_bilinear(l0,c0)
crops=[]
for a in ARMS:
    f=read_dng(D/f"{a}_r0.dng"); l,_,c=normalize(f); rgb=demosaic_bilinear(l,c)
    k=(A["auto"]["exposure_us"]*A["auto"]["gain"])/(A[a]["exposure_us"]*A[a]["gain"])
    img=rgb*k; m=np.median(rgb0.reshape(-1,3),0); img=img*(m[1]/m)/np.percentile(rgb0*(m[1]/m),99.5)
    u=(np.clip(img,0,1)**(1/2.2)*255).astype(np.uint8)
    crops.append((a,u))
# crop window: around the image region of the V3 bottom-left / CC top (fixed, chosen from the frame centre-bottom)
H,W=crops[0][1].shape[:2]
cx,cy=int(W*0.52),int(H*0.62); win=(slice(cy-180,cy+180),slice(cx-320,cx+320))
cells="".join(f'<figure>{sink.img(np.ascontiguousarray(u[win]),f"{lab(a)} crop 1:1",800,lossless=True,pixelated_inline=True)}<figcaption>{lab(a)} · gain {A[a]["gain"]} · red SNR {A[a]["red_snr_db"]["cc_red"][0]} dB</figcaption></figure>' for a,u in crops)
rows="".join(f'<tr><td>{lab(a)}</td><td class=num>{A[a]["exposure_us"]/1000:.1f}</td><td class=num>{A[a]["gain"]}</td><td class=num>{A[a]["exposure_us"]*A[a]["gain"]/1.123/auto_eq:.2f}×</td><td class=num>{A[a]["lux"]}</td>'
             f'<td class=num>{A[a]["red_snr_db"]["v3_red"][0]}</td><td class=num>{A[a]["red_snr_db"]["cc_red"][0]}</td><td class=num>{A[a]["red_snr_db"]["cc_neutral_5"][0]}</td><td class=num>{A[a]["red_snr_db"]["cc_black"][0]}</td>'
             f'<td class=num>{A[a]["loo"]["linear3x3"]["median"]}</td><td class=num>{A[a]["loo"]["rootpoly2"]["median"]}</td><td class=num>{A[a]["v3_de_vs_auto_median"]}</td><td>{", ".join(A[a]["clipped"]) or "none"}</td></tr>' for a in ARMS)
# SVG bar chart of CC red SNR
mx=40; bw=60; svg=[f'<svg viewBox="0 0 {len(ARMS)*bw+60} 230" role="img" aria-label="red SNR per arm">']
for i,a in enumerate(ARMS):
    v=A[a]["red_snr_db"]["cc_red"][0]; h=v/mx*170; x=50+i*bw
    col="var(--acc)" if a=="pin250000" else "var(--ink2)" if a!="auto" else "var(--warn)"
    svg.append(f'<rect x="{x}" y="{190-h:.1f}" width="{bw-14}" height="{h:.1f}" fill="{col}" rx="2"><title>{lab(a)}: {v} dB</title></rect><text x="{x+(bw-14)/2}" y="{185-h:.1f}" font-size="11" text-anchor="middle" fill="var(--ink)">{v}</text><text x="{x+(bw-14)/2}" y="206" font-size="10.5" text-anchor="middle" fill="var(--ink2)">{lab(a)}</text>')
for t in (0,10,20,30,40):
    y=190-t/mx*170; svg.append(f'<line x1="44" x2="{len(ARMS)*bw+50}" y1="{y:.1f}" y2="{y:.1f}" stroke="var(--rule)"/><text x="40" y="{y+4:.1f}" font-size="10" text-anchor="end" fill="var(--ink2)">{t}</text>')
svg.append('<text x="12" y="105" font-size="10.5" fill="var(--ink2)" transform="rotate(-90 12 105)" text-anchor="middle">red SNR, dB</text></svg>')
page='''<title>Exposure Test</title>
<style>
:root{--bg:#f3f5f6;--panel:#fff;--ink:#121a20;--ink2:#46535d;--rule:#d6dde2;--acc:#1f7a8c;--accbg:#e3f1f4;--warn:#c0632a}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#0f1418;--panel:#161d22;--ink:#e6ecf0;--ink2:#b2bec7;--rule:#2a343c;--acc:#5fbfd1;--accbg:#12303a;--warn:#e8915a}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#0f1418;--panel:#161d22;--ink:#e6ecf0;--ink2:#b2bec7;--rule:#2a343c;--acc:#5fbfd1;--accbg:#12303a;--warn:#e8915a}
body{background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;padding-inline:16px;padding-block:18px 40px}
main{max-width:1300px;margin:0 auto;display:grid;gap:14px} h1{font-size:1.4rem;margin:0} h2{font-size:1.08rem;margin:0}
p,ul{margin:0;max-width:120ch} .muted{color:var(--ink2)} section{background:var(--panel);border:1px solid var(--rule);border-radius:6px;padding:12px;display:grid;gap:8px}
.rec{border-left:4px solid var(--acc);background:var(--accbg)}
.wrap{overflow-x:auto} table{border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:.85rem} th,td{padding:3px 8px;border-bottom:1px solid var(--rule);text-align:left;vertical-align:top} .num{text-align:right}
.crops{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px} figure{margin:0;display:grid;gap:4px} figcaption{font-size:.8rem;color:var(--ink2)}
svg{max-width:560px;width:100%;height:auto} img{width:100%;height:auto;border-radius:3px}
</style>'''+LIGHTBOX+f'''<main>
<h1>Exposure test: auto vs gain pinned at 1.12 (IMX708, nereus002, 2026-10-06 ~21:50Z)</h1>
<section class=rec><h2>Verdict</h2><ul>
<li><b>Red SNR follows shutter time, not gain.</b> Auto ran at its 60 ms limit and raised gain to 3.35 (CC red {A["auto"]["red_snr_db"]["cc_red"][0]} dB); gain pinned at 1.12 with 66.7 ms gives the same ({A["pin66667"]["red_snr_db"]["cc_red"][0]} dB). Pinned 1/4 s: <b>+{A["pin250000"]["red_snr_db"]["cc_red"][0]-A["auto"]["red_snr_db"]["cc_red"][0]:.1f} dB</b> (4.2× the light, nothing clips); 1/2 s: +{A["pin500000"]["red_snr_db"]["cc_red"][0]-A["auto"]["red_snr_db"]["cc_red"][0]:.1f} dB but clips {len(A["pin500000"]["clipped"])} patches (incl. the white).</li>
<li><b>ΔE after correction does not change with exposure here</b>: held-out 3×3 ΔE00 3.9–4.4 in every arm (root-poly 4.5–5.0), and the corrected V3 colours stay within ≤ 1.1 ΔE00 of the auto arm. At this light level noise is not what limits colour; the correction (window light) is.</li>
<li><b>For dim scenes:</b> auto stops lengthening the shutter at ~60 ms and buys gain instead, which adds no SNR. Pinning gain low and taking a longer shutter (1/4 s here) is +6 dB of red SNR for free on a static scene — motion blur is the cost under water (not tested).</li>
</ul></section>
<section><h2>Conditions</h2><p>LEDs off (Nick): ambient room/window light only, <b>Lux 85–88 on every frame</b> (rpicam estimate) — stable. Not murky-water dim: auto needed 1/16.7 s at gain 3.35 (target was &gt; 1/15 s and gain &gt; 4), so photon-starved arms were added at gain 1.12 and 14.5 / 29 / 58 ms (0.08 / 0.16 / 0.33 of auto's exposure; Nick's 1/8, 1/4, 1/2 shifted because auto's gain rose from 2.2 to 3.3 when the LEDs went off). <b>Interleaved</b>: 3 rounds of auto → 14.5 → 29 → 58 → 66.7 → 250 → 500 ms. Focus locked at 1.094 dpt for the pinned arms (auto's AF ranged 1.3–1.6 dpt); AWB locked (RAW unaffected). V3 4/4 tags, ColorChecker 24/24 found. Direct <code>rpicam-still</code> calls (rig config untouched) instead of the sweep tool, to control focus — a deviation from the plan.</p></section>
<section><h2>Red SNR on the ColorChecker red patch</h2>{"".join(svg)}
<p class=muted>SNR = mean signal / RMS temporal noise over 3 repeats per pixel, red Bayer sites only (robust to 10-bit quantisation). Orange = auto, blue = best pinned arm.</p></section>
<section><h2>All numbers</h2><div class=wrap><table><tr><th>arm</th><th class=num>shutter ms</th><th class=num>gain</th><th class=num>light vs auto</th><th class=num>lux</th><th class=num>V3 red SNR dB</th><th class=num>CC red</th><th class=num>CC neutral 5</th><th class=num>CC black</th><th class=num>3×3 held-out ΔE00</th><th class=num>root-poly</th><th class=num>V3 ΔE00 vs auto</th><th>clipped</th></tr>{rows}</table></div></section>
<section><h2>1:1 crops, displayed at the same brightness (noise is what differs)</h2><div class=crops>{cells}</div></section>
</main>'''
out.write_text(page); (out.parent/"files.json").write_text(json.dumps({k:k for k in sink.files})); print(out,len(sink.files))
