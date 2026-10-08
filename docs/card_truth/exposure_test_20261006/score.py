"""Exposure test scoring: red SNR (temporal, 3 repeats) and dE00 after a per-arm correction."""
import json, sys, math
from pathlib import Path
import numpy as np, cv2
from nereus_camera_test_rig.color import card_truth as T
from nereus_camera_test_rig.color.card import load_card
from nereus_camera_test_rig.color.chart import load_chart, find_chart
from nereus_camera_test_rig.color.jpeg_geometry import JpegMap
from nereus_camera_test_rig.color.locate import locate_frame, tag_geometry, tag_spec
from nereus_camera_test_rig.color.patches import homography, mosaic_to_binned, sample, INNER
from nereus_camera_test_rig.color.raw_io import read_dng, normalize, bin2x2
from nereus_camera_test_rig.color.metrics import delta_e2000
from host_tools.chart_vs_colorchecker import CC_2014
D = Path(sys.argv[1]); card = load_card("configs/cards/nereus_v3_c1.yaml"); chart = load_chart("configs/charts/colorchecker_classic_24.yaml")
ref_xyz = np.array([T.lab_to_xyz(np.array(r[1:])) for r in CC_2014])
ARMS = ["auto"] + [f"pin{u}" for u in (14500, 29000, 58000, 66667, 250000, 500000)]
# geometry once, from the brightest unclipped-ish frame (pinned 250 ms r0)
g = read_dng(D / "pin250000_r0.dng")
rec = locate_frame(Path("x"), None, card.corner_map, JpegMap.offset(0, 0), raw_reader=lambda _p: g, geometry=tag_geometry(card), spec=tag_spec(card))
assert rec["located"], rec
lin, sat, cfa = normalize(g); b, clip = bin2x2(lin, cfa, sat)
Hm = homography(card, np.asarray(rec["quad_raw"], float)); Hb = mosaic_to_binned(g.valid_crop) @ Hm
white = sample(b, Hb, card.patch("gray_light").box)["mean"]
cc = find_chart(b, Hb, white, chart, (-1500, 2700, 5700, 7000))
boxes = {**{p.id: (Hb, p.box) for p in card.patches}, **{p.id: (Hb, cc["boxes"][p.id]) for p in chart.patches}}
def mask(shape, H, box):
    x0 = box.x + box.w * (1 - INNER) / 2; y0 = box.y + box.h * (1 - INNER) / 2; w1, h1 = box.w * INNER, box.h * INNER
    c = np.array([[[x0, y0]], [[x0 + w1, y0]], [[x0 + w1, y0 + h1]], [[x0, y0 + h1]]], float)
    m = np.zeros(shape, np.uint8); cv2.fillPoly(m, [np.round(cv2.perspectiveTransform(c, H).reshape(-1, 2)).astype(np.int32)], 1); return m.astype(bool)
masks = {pid: mask(b.shape[:2], H, bx) for pid, (H, bx) in boxes.items()}
res = {"geometry": {"tags": rec["tags_found"], "cc_found": cc["n_found"]}, "arms": {}}
for arm in ARMS:
    frames = []
    for r in range(3):
        f = read_dng(D / f"{arm}_r{r}.dng"); l, s, c = normalize(f); bb, cl = bin2x2(l, c, s); frames.append((bb, cl))
        meta = json.loads((D / f"{arm}_r{r}.json").read_text())
    stack = np.stack([fb for fb, _ in frames]); clips = np.stack([fc for _, fc in frames])
    means = {pid: stack[:, m, :].mean(axis=(0, 1)) for pid, m in masks.items()}
    clipped = [pid for pid, m in masks.items() if clips[:, m, :].mean() > 0.001]
    def snr(pid, ch=0):
        v = stack[:, masks[pid], ch]                     # (3, n) red values
        # mean signal / RMS temporal noise (sqrt of the mean per-pixel variance over the 3
        # repeats): robust to 10-bit quantisation, where a median std collapses to 0
        mu = float(v.mean()); rms = float(np.sqrt(v.var(axis=0, ddof=1).mean()))
        return round(20 * math.log10(max(mu, 1e-9) / max(rms, 1e-9)), 1), round(mu, 4)
    rgb = np.array([means[p.id] for p in chart.patches])
    good = [i for i, p in enumerate(chart.patches) if p.id not in clipped]
    fits = {m: T.stats(T.loo(rgb[good], ref_xyz[good], m)) for m in ("linear3x3", "rootpoly2")}
    M = T.fit(rgb[good], ref_xyz[good], "linear3x3")
    v3lab = {p.id: T.xyz_to_lab(T.apply(M, means[p.id][None], "linear3x3")[0]).round(2).tolist() for p in card.patches}
    res["arms"][arm] = {"exposure_us": meta["ExposureTime"], "gain": round(meta["AnalogueGain"], 3), "lux": round(meta.get("Lux", 0), 1),
        "red_snr_db": {"v3_red": snr("red"), "cc_red": snr("chart_r3c3"), "cc_neutral_5": snr("chart_r4c4"), "cc_black": snr("chart_r4c6")},
        "clipped": clipped, "cc_patches_used": len(good), "loo": fits, "v3_lab": v3lab, "cc_white_level": round(float(means["chart_r4c1"].max()), 3)}
    a = res["arms"][arm]; print(arm, a["exposure_us"], a["gain"], "SNR red dB", {k: v[0] for k, v in a["red_snr_db"].items()}, "clip", len(clipped), "LOO3x3", a["loo"]["linear3x3"]["median"], "rp", a["loo"]["rootpoly2"]["median"], "white", a["cc_white_level"])
# V3 colour stability vs the auto arm
ref = res["arms"]["auto"]["v3_lab"]
for arm in ARMS:
    de = [float(delta_e2000(np.array(res["arms"][arm]["v3_lab"][k]), np.array(ref[k]))) for k in ref]
    res["arms"][arm]["v3_de_vs_auto_median"] = round(float(np.median(de)), 2)
print({a: res["arms"][a]["v3_de_vs_auto_median"] for a in ARMS})
(D / "score.json").write_text(json.dumps(res, indent=1))
