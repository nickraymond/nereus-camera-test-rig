"""V3 c1 truth shoot (2026-10-06): measure the card through each camera calibrated on the
ColorChecker Classic, and characterise each camera's colour handling.

    python -m host_tools.v3_truth_shoot --root <truth_20261006> --stills <exp normal stills> \
        --out results/card_truth/truth_20261006.json [--flat]

Geometry: the V3 card and the ColorChecker are in one plane. The ColorChecker patch boxes are
found once on the IMX708 (all 24) in the V3 card's canonical frame, then mapped through each
camera's own card homography. A camera samples the patches that fall wholly inside its frame
(coverage is reported; the N6 loses the grey row, the AE3 two greys).

Per camera, per stop: median over the 3 repeats of each patch mean (binned linear RAW, central
60 %, optional flat-field). Fit camera RGB -> XYZ D50 on the visible, unclipped ColorChecker
patches: 3x3 and root-polynomial, leave-one-patch-out CIEDE2000. The V3 patches through the
better fit give Lab D50. Part B adds the camera's own processed JPEG (normal settings),
grey neutrality, temporal red SNR, and normal-exposure clipping.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from nereus_camera_test_rig.color import card_truth as T
from nereus_camera_test_rig.color.card import load_card
from nereus_camera_test_rig.color.chart import find_chart, load_chart
from nereus_camera_test_rig.color.jpeg_geometry import JpegMap
from nereus_camera_test_rig.color.locate import locate_frame, tag_geometry, tag_spec
from nereus_camera_test_rig.color.metrics import SRGB_TO_XYZ, delta_e2000
from nereus_camera_test_rig.color.patches import INNER, homography, mosaic_to_binned, sample
from nereus_camera_test_rig.color.raw_io import bin2x2, normalize, read_dng, read_openmv_bayer

REPO = Path(__file__).resolve().parents[1]
CARD = REPO / "configs/cards/nereus_v3_c1.yaml"
CHART = REPO / "configs/charts/colorchecker_classic_24.yaml"
REF = REPO / "configs/charts/colorchecker_classic_24_post2014_lab_d50.csv"
CAMS = {
    "imx708": ("imx708/cards", "*.dng", "imx708"),
    "n6": ("n6/cards", "*/*.bayer", "openmv_n6"),
    "ae3": ("ae3/cards", "*/*.bayer", "openmv_ae3"),
}
D65 = np.array([0.95047, 1.0, 1.08883])


def read(p: Path):
    return read_dng(p) if p.suffix == ".dng" else read_openmv_bayer(p)


def locate(frame, card):
    rec = locate_frame(
        Path("x"),
        None,
        card.corner_map,
        JpegMap.offset(0, 0),
        raw_reader=lambda _p: frame,
        geometry=tag_geometry(card),
        spec=tag_spec(card),
    )
    if not rec["located"]:
        raise ValueError(rec.get("reason"))
    return homography(card, np.asarray(rec["quad_raw"], float)), rec["tags_found"]


def inside(H, box, w, h) -> bool:
    c = np.array(
        [
            [[box.x, box.y]],
            [[box.x + box.w, box.y]],
            [[box.x + box.w, box.y + box.h]],
            [[box.x, box.y + box.h]],
        ],
        float,
    )
    q = cv2.perspectiveTransform(c, H).reshape(-1, 2)
    return bool((q >= 0).all() and (q[:, 0] < w).all() and (q[:, 1] < h).all())


def srgb8_to_lab50(v) -> np.ndarray:
    lin = np.where(
        np.asarray(v) / 255 <= 0.04045,
        np.asarray(v) / 255 / 12.92,
        ((np.asarray(v) / 255 + 0.055) / 1.055) ** 2.4,
    )
    return T.xyz_to_lab((lin @ SRGB_TO_XYZ.T) @ T.bradford(D65, T.D50).T)


def alsc_flat(tuning: Path, shape, ct: float = 5300.0) -> np.ndarray:
    """IMX708 lens shading from its libcamera tuning file (``rpi.alsc``) as a flat (1 / gain).

    Gains per channel on the 16x12 grid: G = luminance_lut, R = Cr(ct) * lum, B = Cb(ct) * lum,
    with the luminance table at full strength (the ISP uses ``luminance_strength`` 0.5 for looks;
    colorimetry wants the whole vignetting removed). Cr / Cb are interpolated in colour
    temperature (clamped to the calibrated range). Grid values sit at cell centres over the full
    sensor; bilinear in between. Assumes the frame covers the full sensor (4608x2592 mode).
    """
    alsc = next(
        x["rpi.alsc"] for x in json.loads(tuning.read_text())["algorithms"] if "rpi.alsc" in x
    )

    def at_ct(cals):
        cts = [c["ct"] for c in cals]
        tabs = [np.array(c["table"], float) for c in cals]
        x = min(max(ct, cts[0]), cts[-1])
        i = max(j for j in range(len(cts)) if cts[j] <= x)
        if i == len(cts) - 1:
            return tabs[i]
        w = (x - cts[i]) / (cts[i + 1] - cts[i])
        return (1 - w) * tabs[i] + w * tabs[i + 1]

    lum = np.array(alsc["luminance_lut"], float)
    gain = np.stack(
        [at_ct(alsc["calibrations_Cr"]) * lum, lum, at_ct(alsc["calibrations_Cb"]) * lum], -1
    )
    gain = gain.reshape(12, 16, 3).astype(np.float32)
    g = cv2.resize(gain, (shape[1], shape[0]), interpolation=cv2.INTER_LINEAR)
    return 1.0 / g


def centre(box) -> np.ndarray:
    """Box centre in canonical px / 1000 (the plane's units)."""
    return np.array([box.x + box.w / 2, box.y + box.h / 2]) / 1000


def plane_fit(rgb, xyz, pos, model: str, plane: bool, it: int = 8):
    """Camera RGB -> XYZ fit, optionally with a planar illumination term exp(a x + b y).

    The plane is estimated from the chart itself: the log ratio of fitted to reference Y,
    regressed on patch position, repeated until it settles. It stands in for the flat-field
    frames this shoot does not have (no board large enough, 2026-10-06).
    """
    ab = np.zeros(2)
    for _ in range(it if plane else 1):
        r = rgb / np.exp(pos @ ab)[:, None]
        M = T.fit(r, xyz, model)
        if not plane:
            break
        e = np.log(np.maximum(T.apply(M, r, model)[:, 1], 1e-6) / xyz[:, 1])
        c, *_ = np.linalg.lstsq(np.c_[np.ones(len(pos)), pos], e, rcond=None)
        ab = ab + c[1:]
    return M, ab


def loo_plane(rgb, xyz, pos, model: str, plane: bool) -> np.ndarray:
    """Leave-one-patch-out CIEDE2000; the plane is refitted without the held-out patch too."""
    if not plane:
        return T.loo(rgb, xyz, model)
    de = []
    for i in range(len(rgb)):
        k = np.arange(len(rgb)) != i
        M, ab = plane_fit(rgb[k], xyz[k], pos[k], model, True)
        pr = T.apply(M, rgb[i : i + 1] / math.exp(pos[i] @ ab), model)
        de.append(float(delta_e2000(T.xyz_to_lab(pr), T.xyz_to_lab(xyz[i : i + 1]))[0]))
    return np.array(de)


def flat_of(paths) -> np.ndarray | None:
    if not paths:
        return None
    st = []
    for p in paths:
        lin, sat, cfa = normalize(read(p))
        st.append(bin2x2(lin, cfa, sat)[0])
    f = cv2.GaussianBlur(np.median(st, axis=0).astype(np.float32), (0, 0), 8)
    return f / np.median(f.reshape(-1, 3), axis=0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument(
        "--stills",
        type=Path,
        default=None,
        help="normal-settings stills experiment (Part B); omit for the truth only",
    )
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--flat", action="store_true", help="divide by <root>/<cam>/flat frames")
    ap.add_argument(
        "--alsc",
        type=Path,
        default=None,
        help="IMX708 only: lens-shading correction from this libcamera tuning file",
    )
    ap.add_argument(
        "--plane",
        action="store_true",
        help="fit a planar illumination term on the chart (stand-in for flat-field frames)",
    )
    ap.add_argument(
        "--write-block",
        action="store_true",
        help="write the IMX708 stop-0 result as the card YAML's measured: block",
    )
    ap.add_argument(
        "--light",
        default="2 LEDs, 5300 K, full brightness; room lights off",
        help="light description recorded in the measured block",
    )
    a = ap.parse_args()
    card, chart = load_card(CARD), load_chart(CHART)
    ref = T.load_reference(REF)
    ref_xyz = np.array([T.lab_to_xyz(np.array(ref[p.label]["lab"])) for p in chart.patches])
    # chart boxes in the card's canonical frame, from the IMX708 (sees all 24)
    g = read(sorted((a.root / CAMS["imx708"][0]).glob("stop_+0_r0.dng"))[0])
    Hm, _ = locate(g, card)
    lin, sat, cfa = normalize(g)
    b, _ = bin2x2(lin, cfa, sat)
    if a.flat:
        # uneven light (daylight + LEDs, 2026-10-08) defeats the finder's threshold: find the
        # chart on the flat-corrected frame, the same light field the flat measured
        fl = flat_of(sorted((a.root / "imx708" / "flat").glob("**/stop_*.dng")))
        if fl is not None:
            b = b / np.maximum(fl, 1e-3)
    Hb = mosaic_to_binned(g.valid_crop) @ Hm
    cc_boxes = find_chart(
        b, Hb, sample(b, Hb, card.patch("gray_light").box)["mean"], chart, (-1500, 2700, 5700, 7000)
    )["boxes"]
    out = {"flat": a.flat, "cams": {}}
    for cam, (sub, pat, still_dir) in CAMS.items():
        files = sorted(p for p in (a.root / sub).glob(pat) if p.name.startswith("stop_"))
        flat_files = (
            (
                sorted((a.root / cam / "flat").glob("**/*.bayer"))
                + sorted((a.root / cam / "flat").glob("**/stop_*.dng"))
            )
            if a.flat
            else []
        )
        if a.flat and not flat_files:
            raise SystemExit(f"{cam}: --flat but no flat frames under {a.root / cam / 'flat'}")
        flat = flat_of(flat_files)
        f0 = read(files[0])
        shading = "flat-field frames" if flat is not None else "none"
        if cam == "imx708" and a.alsc is not None and flat is None:
            lin0, sat0, cfa0 = normalize(f0)
            flat = alsc_flat(a.alsc, bin2x2(lin0, cfa0, sat0)[0].shape)
            shading = f"rpi.alsc from {a.alsc.name}, luminance at full strength, 5300 K"
        elif cam != "imx708" and flat is None:
            shading = "none (OpenMV RAW: on-chip lens shading correction is on, OQ-54)"
        Hm, tags = locate(f0, card)
        W, Hh = f0.active()[0].shape[::-1]
        Hb = mosaic_to_binned(f0.valid_crop) @ Hm
        vis = [p for p in chart.patches if inside(Hm, cc_boxes[p.id], W, Hh)]
        r = {
            "tags": tags,
            "cc_visible": [p.label for p in vis],
            "cc_coverage": f"{len(vis)}/24",
            "flat_frames": [p.name for p in flat_files],
            "shading": shading,
            "stops": {},
        }
        if flat is not None:
            # how much the correction differs between the chart and the card (green, max / min)
            gv = [float(sample(1 / flat, Hb, cc_boxes[p.id])["mean"][1]) for p in vis]
            gc = [float(sample(1 / flat, Hb, p.box)["mean"][1]) for p in card.patches]
            r["shading_gain_green"] = {
                "chart": [round(min(gv), 3), round(max(gv), 3)],
                "card": [round(min(gc), 3), round(max(gc), 3)],
            }
            # evenness of the light on the CARD (the flat's green, inside the card outline)
            cq = cv2.perspectiveTransform(
                np.array([[[0, 0]], [[4200, 0]], [[4200, 2700]], [[0, 2700]]], float), Hb
            ).reshape(-1, 2)
            cm = np.zeros(flat.shape[:2], np.uint8)
            cv2.fillPoly(cm, [np.round(cq).astype(np.int32)], 1)
            fg = flat[..., 1][cm.astype(bool)]
            r["flat_spread_pct_p95_p5"] = round(
                float(100 * (np.percentile(fg, 95) / np.percentile(fg, 5) - 1)), 1
            )
        by_stop: dict[str, list] = {}
        for p in files:
            by_stop.setdefault(p.name.split("_r")[0], []).append(p)
        for stop, ps in sorted(by_stop.items()):
            reps, clips = [], []
            for p in ps:
                fr = read(p)
                lin, sat, cfa = normalize(fr)
                bb, cl = bin2x2(lin, cfa, sat)
                if flat is not None:
                    bb = bb / np.maximum(flat, 1e-3)
                reps.append(bb)
                clips.append(cl)
            stack = np.stack(reps)
            cc = {
                p.id: [sample(x, Hb, cc_boxes[p.id], clip=c) for x, c in zip(stack, clips)]
                for p in vis
            }
            v3 = {
                p.id: [sample(x, Hb, p.box, clip=c) for x, c in zip(stack, clips)]
                for p in card.patches
            }
            med = lambda sts: np.median([s["mean"] for s in sts], axis=0)  # noqa: E731
            clipped = sorted(
                {
                    pid
                    for d in (cc, v3)
                    for pid, sts in d.items()
                    if max(max(s.get("clip_frac") or [0]) for s in sts) > 0.001
                }
            )
            use = [p for p in vis if p.id not in clipped]
            rgb = np.array([med(cc[p.id]) for p in use])
            xyz = np.array([ref_xyz[chart.patches.index(p)] for p in use])
            pos = np.array([centre(cc_boxes[p.id]) for p in use])
            if not a.plane:
                pos = pos * 0  # no illumination term: the plane factor is 1 everywhere
            loos = {m: loo_plane(rgb, xyz, pos, m, a.plane) for m in ("linear3x3", "rootpoly2")}
            fits = {m: T.stats(v) for m, v in loos.items()}
            best = min(fits, key=lambda m: fits[m]["median"])
            M, ab = plane_fit(rgb, xyz, pos, best, a.plane)
            M3, ab3 = plane_fit(rgb, xyz, pos, "linear3x3", a.plane)
            light = (lambda bx: 1.0) if not a.plane else (lambda bx: math.exp(centre(bx) @ ab))
            x50 = {
                p.id: T.apply(M, med(v3[p.id])[None] / light(p.box), best)[0] for p in card.patches
            }
            lab = {k: T.xyz_to_lab(v).round(2).tolist() for k, v in x50.items()}
            srgb = {k: T.xyz50_to_srgb8(v) for k, v in x50.items()}
            grey_ab = {
                p.label: T.xyz_to_lab(
                    T.apply(M3, med(cc[p.id])[None] / np.exp(pos[use.index(p)] @ ab3), "linear3x3")[
                        0
                    ]
                )[1:]
                .round(2)
                .tolist()
                for p in use
                if p.group == "grey"
            }
            # temporal red SNR (3 repeats) on the ColorChecker red and neutral 5, if visible
            snr = {}
            spots = [
                (f"cc_{q.label}", cc_boxes[q.id])
                for q in use
                if q.label in ("red", "neutral_5", "neutral_35")
            ]
            spots.append(("v3_red", card.patch("red").box))  # visible on all three cameras
            for lbl, bx in spots:
                m = np.zeros(stack.shape[1:3], np.uint8)
                x0, y0 = bx.x + bx.w * (1 - INNER) / 2, bx.y + bx.h * (1 - INNER) / 2
                c = np.array(
                    [
                        [[x0, y0]],
                        [[x0 + bx.w * INNER, y0]],
                        [[x0 + bx.w * INNER, y0 + bx.h * INNER]],
                        [[x0, y0 + bx.h * INNER]],
                    ],
                    float,
                )
                cv2.fillPoly(
                    m,
                    [np.round(cv2.perspectiveTransform(c, Hb).reshape(-1, 2)).astype(np.int32)],
                    1,
                )
                v = stack[:, m.astype(bool), 0]
                snr[lbl] = round(
                    20 * math.log10(v.mean() / max(math.sqrt(v.var(axis=0, ddof=1).mean()), 1e-9)),
                    1,
                )
            # per ColorChecker patch: reference L* vs fitted L* (in-sample) and held-out ΔE00
            loo_de = loos[best]
            rgb_l = rgb / np.exp(pos @ ab)[:, None]
            cc_pp = {
                p.label: {
                    "ref_L": round(ref[p.label]["lab"][0], 1),
                    "fit_L": round(float(T.xyz_to_lab(T.apply(M, rgb_l[i][None], best)[0])[0]), 1),
                    "loo_de": round(float(loo_de[i]), 2),
                }
                for i, p in enumerate(use)
            }
            r["stops"][stop] = {
                "cc_used": len(use),
                "cc_per_patch": cc_pp,
                "frames": [p.name for p in ps],
                "matrix": np.round(M, 6).tolist(),
                "light_plane": None
                if not a.plane
                else {
                    "log_slope_per_1000px": np.round(ab, 5).tolist(),
                    "pct_across_chart_y": round(
                        100 * (math.exp(abs(ab[1]) * np.ptp(pos[:, 1])) - 1), 1
                    ),
                    "pct_across_chart_x": round(
                        100 * (math.exp(abs(ab[0]) * np.ptp(pos[:, 0])) - 1), 1
                    ),
                },
                "v3_srgb8": {k: [round(float(c), 1) for c in v[0]] for k, v in srgb.items()},
                "out_of_srgb": [k for k, v in srgb.items() if v[1]],
                "over_range": [k for k, v in srgb.items() if max(v[0]) >= 299.5],
                "clipped": clipped,
                "fit": fits,
                "model": best,
                "v3_lab": lab,
                "grey_ab_3x3": grey_ab,
                "red_snr_db": snr,
                "level_gray_light": round(float(med(v3["gray_light"]).max()), 3),
            }
        if a.stills is not None:
            # Part B: the camera's own processed still (normal settings), via its RAW geometry
            sd = a.stills / "captures" / still_dir
            jpg = next(p for p in sd.iterdir() if p.name.endswith(".jpg") and "image" in p.name)
            raw_n = next(p for p in sd.iterdir() if p.suffix in (".dng", ".bayer"))
            img = cv2.imread(str(jpg))[..., ::-1].astype(np.float64)
            fr_n = read(raw_n)
            Hn, _ = locate(fr_n, card)
            sj = img.shape[1] / fr_n.active()[0].shape[1]
            Sx = np.diag([sj, sj, 1.0])
            jv = [
                p
                for p in chart.patches
                if inside(Hn, cc_boxes[p.id], fr_n.active()[0].shape[1], fr_n.active()[0].shape[0])
            ]
            jm = {p.label: np.asarray(sample(img, Sx @ Hn, cc_boxes[p.id])["mean"]) for p in jv}
            lab_j = {k: srgb8_to_lab50(v) for k, v in jm.items()}
            greys = [
                k for k in ("neutral_8", "neutral_65", "neutral_5", "neutral_35") if k in lab_j
            ]
            # one lightness offset on the mid greys (exposure), no colour change
            dl = np.mean([ref[k]["lab"][0] - lab_j[k][0] for k in greys]) if greys else 0.0
            de_j = {
                k: round(float(delta_e2000(lab_j[k] + [dl, 0, 0], np.array(ref[k]["lab"]))), 2)
                for k in lab_j
            }
            lin_n, sat_n, cfa_n = normalize(fr_n)
            bn, cn = bin2x2(lin_n, cfa_n, sat_n)
            Hbn = mosaic_to_binned(fr_n.valid_crop) @ Hn
            clip_raw_normal = sorted(
                {
                    p.id
                    for p in list(card.patches) + jv
                    if max(
                        sample(bn, Hbn, cc_boxes[p.id] if p.id in cc_boxes else p.box, clip=cn).get(
                            "clip_frac"
                        )
                        or [0]
                    )
                    > 0.001
                }
            )
            clip_jpg = sorted(k for k, v in jm.items() if v.max() >= 254)
            r["processed"] = {
                "jpeg": jpg.name,
                "patches": len(jm),
                "de2000_median": round(float(np.median(list(de_j.values()))), 2),
                "de2000_colours": round(
                    float(
                        np.median(
                            [
                                v
                                for k, v in de_j.items()
                                if "neutral" not in k and k not in ("white", "black")
                            ]
                            or [math.nan]
                        )
                    ),
                    2,
                ),
                "grey_ab": {k: lab_j[k][1:].round(1).tolist() for k in greys},
                "per_patch": de_j,
                "clipped_jpeg": clip_jpg,
                "clipped_raw_normal": clip_raw_normal,
                "exposure_normal": json.loads((sd / "capture.json").read_text()).get(
                    "sensor_metadata", {}
                ),
            }
        out["cams"][cam] = r
        s0 = r["stops"]["stop_+0"]
        pr = r.get("processed", {})
        print(
            cam,
            r["cc_coverage"],
            "fit",
            {m: s0["fit"][m]["median"] for m in s0["fit"]},
            "model",
            s0["model"],
            "SNR",
            s0["red_snr_db"],
            "| processed dE",
            pr.get("de2000_median"),
            "clip raw normal",
            pr.get("clipped_raw_normal"),
        )
    # cross-check: N6 / AE3 V3 Lab vs the IMX708's
    ref_lab = out["cams"]["imx708"]["stops"]["stop_+0"]["v3_lab"]
    for cam in ("n6", "ae3"):
        lab = out["cams"][cam]["stops"]["stop_+0"]["v3_lab"]
        de = {
            k: round(float(delta_e2000(np.array(lab[k]), np.array(ref_lab[k]))), 2) for k in ref_lab
        }
        out["cams"][cam]["v3_de_vs_imx708"] = {
            "median": round(float(np.median(list(de.values()))), 2),
            "max": round(float(max(de.values())), 2),
            "per_patch": de,
        }
        print(cam, "V3 vs IMX708 dE00 median", out["cams"][cam]["v3_de_vs_imx708"]["median"])
    lab_m = ref_lab
    lab_mm = out["cams"]["imx708"]["stops"]["stop_-0.5"]["v3_lab"]
    out["imx708_stop_consistency"] = round(
        float(np.median([delta_e2000(np.array(lab_m[k]), np.array(lab_mm[k])) for k in lab_m])), 2
    )
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=1))
    print("IMX708 stop 0 vs -0.5 V3 dE00 median", out["imx708_stop_consistency"])
    if a.write_block:
        write_measured(out, a.light)
    return 0


def write_measured(out: dict, light: str) -> None:
    """The IMX708 stop-0 result -> the card YAML's ``measured:`` block (card_truth's format)."""
    im = out["cams"]["imx708"]
    s0 = im["stops"]["stop_+0"]
    res = {
        "fit": {"model": s0["model"], "loo_de2000": s0["fit"]},
        "chart_patches_used": s0["cc_used"],
        "values": s0["v3_srgb8"],
        "lab_d50": s0["v3_lab"],
        "out_of_srgb": s0["out_of_srgb"],
        "over_range": s0["over_range"],
        "frames": s0["frames"],
        "flat_frames": im["flat_frames"],
        "flat_spread_pct_p95_p5": im.get("flat_spread_pct_p95_p5"),
        "reference_sha256": hashlib.sha256(REF.read_bytes()).hexdigest(),
    }
    session = {
        "condition": "dry",
        "light": {"description": light},
        "chart": {"config": str(CHART.relative_to(REPO)), "reference": str(REF.relative_to(REPO))},
    }
    date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    T.write_block(CARD, T.measured_block(res, session, date))
    print("wrote measured: block to", CARD)


if __name__ == "__main__":
    raise SystemExit(main())
