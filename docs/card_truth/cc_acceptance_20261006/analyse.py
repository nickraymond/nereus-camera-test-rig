import json, sys, math
from pathlib import Path
import numpy as np, cv2
from nereus_camera_test_rig.color import card_truth as T
from nereus_camera_test_rig.color.card import load_card
from nereus_camera_test_rig.color.chart import load_chart, find_chart
from nereus_camera_test_rig.color.jpeg_geometry import JpegMap
from nereus_camera_test_rig.color.locate import locate_frame, tag_geometry, tag_spec
from nereus_camera_test_rig.color.patches import homography, mosaic_to_binned, sample
from nereus_camera_test_rig.color.raw_io import read_dng, read_openmv_bayer, normalize, bin2x2
from nereus_camera_test_rig.color.calibrate import dng_to_linear
from nereus_camera_test_rig.color.metrics import delta_e2000, SRGB_TO_XYZ
from host_tools.chart_vs_colorchecker import analyse as factory, CC_2014

D = Path(sys.argv[1]); card = load_card("configs/cards/nereus_v3_c1.yaml"); chart = load_chart("configs/charts/colorchecker_classic_24.yaml")
ref = np.array([r[1:] for r in CC_2014]); ref_xyz = np.array([T.lab_to_xyz(r) for r in ref])
ROI = (1504, 846, 1600, 900)
FR = {"imx708": D / "imx708/stop_+0.dng", "n6": next((D / "n6").glob("*/stop_+0_r0.bayer")), "ae3": next((D / "ae3").glob("*/stop_+0_r0.bayer"))}
out = {}
for cam, path in FR.items():
    fr = read_dng(path) if path.suffix == ".dng" else read_openmv_bayer(path)
    rec = locate_frame(path, None, card.corner_map, JpegMap.offset(0, 0), raw_reader=lambda _p: fr, geometry=tag_geometry(card), spec=tag_spec(card))
    raw = fr.active()[0]; H, W = raw.shape
    r = {"wh": [W, H], "exposure_us": round((fr.exposure_s or 0) * 1e6), "tags": rec.get("tags_found"), "located": rec["located"]}
    if not rec["located"]:
        out[cam] = {**r, "error": rec.get("reason")}; print(cam, out[cam]); continue
    lin, sat, cfa = normalize(fr); b, clip = bin2x2(lin, cfa, sat)
    Hm = homography(card, np.asarray(rec["quad_raw"], float)); Hb = mosaic_to_binned(fr.valid_crop) @ Hm
    cw, ch = card.canonical_w, card.canonical_h
    ol = cv2.perspectiveTransform(np.array([[[0, 0], [cw, 0], [cw, ch], [0, ch]]], float), Hm)[0]
    inframe = lambda q: bool((q.min(0) >= 0).all() and (q[:, 0] < W).all() and (q[:, 1] < H).all())
    r.update({"card_outline": np.round(ol, 1).tolist(), "card_in_frame": inframe(ol), "card_width_px": round(float(np.linalg.norm(ol[1] - ol[0])), 1)})
    cp = {p.id: sample(b, Hb, p.box, clip=clip) for p in card.patches}
    r["card_clipped"] = [k for k, v in cp.items() if max(v.get("clip_frac") or [0]) > 0.001]
    white = cp["gray_light"]["mean"]
    res = None; err = ""
    for region in ([-1500, 2700, 5700, 7000], [-3000, 2600, 7200, 9000]):
        try:
            res = find_chart(b, Hb, white, chart, tuple(region)); break
        except ValueError as e:
            err = str(e)
    if res is None:
        out[cam] = {**r, "chart_error": err}; print(cam, "chart", err); continue
    boxes = res.pop("boxes")
    st = {p.id: sample(b, Hb, boxes[p.id], clip=clip) for p in chart.patches}
    pts = {}
    for p in chart.patches:
        bx = boxes[p.id]; c = np.array([[[bx.x, bx.y]], [[bx.x + bx.w, bx.y]], [[bx.x + bx.w, bx.y + bx.h]], [[bx.x, bx.y + bx.h]]], float)
        pts[p.id] = cv2.perspectiveTransform(c, Hm).reshape(-1, 2)
    allp = np.concatenate(list(pts.values()))
    rgb = np.array([st[p.id]["mean"] for p in chart.patches])
    # orientation: the grey row (dark->light) must be the row with lowest chroma; check 0 / 180 deg
    lum = rgb @ [0.25, 0.5, 0.25]; chroma = np.ptp(rgb / np.maximum(rgb.mean(1, keepdims=True), 1e-6), axis=1)
    rows = chroma.reshape(4, 6).mean(1)
    rot = 0 if rows.argmin() == 3 and lum[18] > lum[23] else 180 if rows.argmin() == 0 and lum[5] > lum[0] else None
    if rot == 180:
        rgb = rgb[::-1]
    r.update({"cc_found": True, "cc_orientation_deg": rot, "cc_patches_in_frame": int(sum(inframe(q) for q in pts.values())),
              "cc_patch_px": round(float(np.median([np.linalg.norm(q[1] - q[0]) for q in pts.values()])), 1),
              "cc_clipped": [k for k, v in st.items() if max(v.get("clip_frac") or [0]) > 0.001],
              "cc_patch_cv_pct_max": round(float(max(100 * np.max(np.asarray(v["std"]) / np.maximum(v["mean"], 1e-6)) for v in st.values())), 1),
              "cc_outline": [np.round(allp.min(0), 1).tolist(), np.round(allp.max(0), 1).tolist()], "cc_grid_rms_px": res["grid_rms_px"]})
    if cam == "imx708":
        x, y, w, h = ROI
        inroi = lambda q: bool((q[:, 0] >= x).all() and (q[:, 0] <= x + w).all() and (q[:, 1] >= y).all() and (q[:, 1] <= y + h).all())
        r["card_in_roi"] = inroi(ol); r["cc_in_roi"] = inroi(allp)
        r["card_roi_margins"] = [round(float(v), 1) for v in (ol[:, 0].min() - x, x + w - ol[:, 0].max(), ol[:, 1].min() - y, y + h - ol[:, 1].max())]
        meta = json.loads((D / "imx708/stop_+0.json").read_text())
        f = factory(rgb, meta, fr)
        r["factory"] = {k: {"median": round(float(np.median(v["de2000"])), 2), "colours": round(float(np.median(v["de2000"][:18])), 2), "greys": round(float(np.median(v["de2000"][18:])), 2), "max": round(float(max(v["de2000"])), 2), "per_patch": v["de2000"]} for k, v in f.items()}
    good = [i for i, p in enumerate(chart.patches) if max(st[p.id].get("clip_frac") or [0]) <= 0.001]
    fits = {}
    for model in ("linear3x3", "rootpoly2"):
        de = T.loo(rgb[good], ref_xyz[good], model); fits[model] = T.stats(de)
        fits[model]["per_patch"] = {chart.patches[i].label: round(float(d), 2) for i, d in zip(good, de)}
    r["fit_loo"] = fits; r["cc_rgb"] = rgb.tolist()
    out[cam] = r
    print(cam, {k: r.get(k) for k in ("tags", "card_in_frame", "card_width_px", "card_clipped", "card_in_roi", "card_roi_margins", "cc_orientation_deg", "cc_patches_in_frame", "cc_patch_px", "cc_clipped", "cc_patch_cv_pct_max", "cc_in_roi")})
    if "factory" in r: print("   factory", {k: {kk: vv for kk, vv in v.items() if kk != "per_patch"} for k, v in r["factory"].items()})
    print("   LOO", {m: {k: v for k, v in s.items() if k != "per_patch"} for m, s in fits.items()})
(D / "acceptance.json").write_text(json.dumps(out, indent=1))
