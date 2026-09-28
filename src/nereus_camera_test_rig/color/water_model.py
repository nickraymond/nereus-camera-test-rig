"""Stage ``fit`` — the L2 water model, per dive (SPEC §4 Phase 8 S2a; brief §5.4, §7 P1.2).

Per channel c, for a grey patch of reflectance ρ (relative to the white patch) at camera-card
distance z, in exposure-normalized linear camera RGB:

    I = ρ · L_s · exp(−βD · z)  +  B∞_s · (1 − exp(−βB · z))

``L_s`` (light reaching the card: E(0)·exp(−K·d)) and ``B∞_s`` (haze at infinity) are per
sweep, ``βD`` (view-path attenuation) and ``βB`` (haze build-up) are shared per dive. The
black patch's print reflectance is a nuisance: its reflected term is a free ``C_s`` per sweep.
Then, per dive, ``ln L_s = ln E(0) − K·d_s`` over the sweeps gives the downwelling ``K``.

Only neutral patches enter the fit — their ρ is the same in every channel and needs no colour
matrix; the 12 colour patches stay held out for validation. Only patches qc kept, within
``MAX_RADIUS`` of the image half-diagonal (no flat-field yet), black only when wide enough.

Solved by **variable projection**: on a (βD, βB) grid the rest is a weighted linear least
squares; the grid minimum is the fit and the profile gives the 68 % intervals (Δχ² ≤ 1 after
scaling χ² to its reduced minimum). numpy only.

**Depth white-balance table (v0.1, 2026-09-27).** Run A showed the grey's colour barely
changes with distance (0.5–3 m) but strongly with depth, and absolute light levels differ ~10×
between dives. So table mode uses only a colour-of-light table: per card frame, the slope of a
straight line through the greys (``ramp_fit``, I = A·ρ + H) gives the light colour A and the
haze H; ``ln(R/G)`` and ``ln(B/G)`` of A are regressed on
depth per dive (and on depth + distance, as a check that distance adds little). Ratios cancel
exposure and between-frame light flicker.

Output: ``fit/{params.json, wb_table.json, wb_points.csv, observations.csv, summary.json,
stage.json}``.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from ..config import ConfigError, load_yaml
from .card import Card, load_card
from .metrics import srgb8_to_linear
from .patches import homography
from .stages import verify_fresh, write_stage

CHANNELS = "RGB"
BETA_D = np.round(np.arange(0.0, 1.5001, 0.015), 4)
BETA_B = np.round(np.geomspace(0.03, 10.0, 56), 4)
MAX_RADIUS = 0.6          # of the image half-diagonal (brief/SPEC S2a: no flat-field yet)
MIN_BLACK_PX = 6          # binned px, shorter side of the black sample
REL_SIGMA = 0.03          # per-observation error: 3 % of the value ...
FLOOR_SIGMA = 0.003       # ... plus 0.3 % of the dive-channel maximum
IDENTIFIABLE_Z_RATIO = 1.5


def grey_reflectance(card: Card) -> dict[str, float]:
    """Linear reflectance of every neutral region (paper white = 1, from the design sRGB)."""
    return {g: float(srgb8_to_linear(card.patch(card.parent_of(g)).truth[0]))
            for g in card.grey_ids}


def first_usable(alternatives, usable) -> Optional[str]:
    """The first of ``alternatives`` that ``usable(id)`` accepts."""
    return next((a for a in alternatives if usable(a)), None)


def _grey_ids(qpatches: dict, card: Card) -> list[str]:
    """The ramp greys (one of each set of alternatives) and the haze patch, if usable."""
    ok = lambda pid: qpatches.get(pid, {}).get("usable", False)  # noqa: E731
    ids = [first_usable(alts, ok) for alts in card.roles.ramp]
    return [i for i in ids if i] + ([card.roles.haze] if card.roles.haze and ok(card.roles.haze)
                                    else [])


def observations(root: Path, dist: dict, card: Card, principal,
                 reference: str) -> list[dict]:
    rows = {r["stem"]: r for r in csv.DictReader((root / "ingest" / "manifest.csv").open())}
    corners = json.loads((root / "locate" / "corners.json").read_text())
    patches = json.loads((root / "patches" / "patches.json").read_text())
    qc = json.loads((root / "qc" / "qc.json").read_text())
    rho = grey_reflectance(card)
    pp = np.asarray(principal, dtype=np.float64)
    half_diag = float(np.hypot(*pp))
    boxes = {p.id: p.box for p in (*card.patches, *card.sub_patches)}
    obs = []
    for stem, q in qc.items():
        r, d = rows[stem], dist.get(stem, {})
        if (r["category"] != reference or not r["sweep_id"] or not q["usable"]
                or d.get("medium") != "water" or d.get("z_m") is None):
            continue
        H = homography(card, np.asarray(corners[stem]["quad_raw"]))
        raw = patches[stem]["raw"]["patches"]
        for pid in _grey_ids(q["patches"], card):
            b = boxes[pid]
            centre = cv2.perspectiveTransform(
                np.array([[[b.x + b.w / 2, b.y + b.h / 2]]], dtype=np.float64), H)[0, 0]
            radius = float(np.linalg.norm(centre - pp)) / half_diag
            if radius > MAX_RADIUS:
                continue
            if pid == card.roles.haze and min(raw[pid]["size_px"]) < MIN_BLACK_PX:
                continue
            obs.append({"stem": stem, "dive": r["dive_id"], "sweep": r["sweep_id"],
                        "depth_m": float(r["depth_m"]), "z_m": d["z_m"], "patch": pid,
                        "rho": None if pid == card.roles.haze else rho[pid],
                        "radius": round(radius, 3), "I": raw[pid]["mean_norm"]})
    return obs


def _design(obs: list[dict], sweeps: list[str], beta_d: float, beta_b: float) -> np.ndarray:
    n, k = len(obs), len(sweeps)
    A = np.zeros((n, 3 * k))
    for i, o in enumerate(obs):
        s = sweeps.index(o["sweep"])
        att = np.exp(-beta_d * o["z_m"])
        if o["rho"] is None:
            A[i, k + s] = att                       # C_s: black patch reflected term
        else:
            A[i, s] = o["rho"] * att                # L_s
        A[i, 2 * k + s] = 1 - np.exp(-beta_b * o["z_m"])   # B∞_s
    return A


def fit_channel(obs: list[dict], c: int) -> dict[str, Any]:
    sweeps = sorted({o["sweep"] for o in obs}, key=int)
    y = np.array([o["I"][c] for o in obs])
    sigma = REL_SIGMA * np.abs(y) + FLOOR_SIGMA * np.abs(y).max()
    chi2 = np.full((BETA_D.size, BETA_B.size), np.inf)
    for i, bd in enumerate(BETA_D):
        for j, bb in enumerate(BETA_B):
            A = _design(obs, sweeps, bd, bb) / sigma[:, None]
            coef, *_ = np.linalg.lstsq(A, y / sigma, rcond=None)
            chi2[i, j] = float(np.sum((A @ coef - y / sigma) ** 2))
    i, j = np.unravel_index(np.argmin(chi2), chi2.shape)
    bd, bb = float(BETA_D[i]), float(BETA_B[j])
    A = _design(obs, sweeps, bd, bb)
    coef, *_ = np.linalg.lstsq(A / sigma[:, None], y / sigma, rcond=None)
    dof = max(1, len(y) - A.shape[1] - 2)
    scaled = chi2 / (chi2[i, j] / dof)
    within = scaled <= scaled[i, j] + 1.0
    k = len(sweeps)
    return {
        "beta_d": bd, "beta_d_ci": [float(BETA_D[within.any(axis=1)].min()),
                                    float(BETA_D[within.any(axis=1)].max())],
        "beta_b": bb, "beta_b_ci": [float(BETA_B[within.any(axis=0)].min()),
                                    float(BETA_B[within.any(axis=0)].max())],
        "beta_on_grid_edge": bool(i in (0, BETA_D.size - 1) or j in (0, BETA_B.size - 1)),
        "chi2_reduced": round(float(chi2[i, j] / dof), 3), "n_obs": len(y),
        "sweeps": {s: {"L": float(coef[n]), "C_black": float(coef[k + n]),
                       "B_inf": float(coef[2 * k + n])} for n, s in enumerate(sweeps)},
        "fitted": (A @ coef).tolist(),
    }


def fit_dive(obs: list[dict]) -> dict[str, Any]:
    sweeps = sorted({o["sweep"] for o in obs}, key=int)
    per = [fit_channel(obs, c) for c in range(3)]
    out: dict[str, Any] = {"channels": {}, "sweeps": {}, "n_obs": len(obs),
                           "frames": len({o["stem"] for o in obs})}
    for s in sweeps:
        so = [o for o in obs if o["sweep"] == s]
        z = [o["z_m"] for o in so]
        out["sweeps"][s] = {
            "depth_m": round(float(np.median([o["depth_m"] for o in so])), 2),
            "frames": len({o["stem"] for o in so}), "z_range_m": [min(z), max(z)],
            "identifiable": max(z) / min(z) >= IDENTIFIABLE_Z_RATIO,
            "L": [p["sweeps"][s]["L"] for p in per],
            "B_inf": [p["sweeps"][s]["B_inf"] for p in per],
            "C_black": [p["sweeps"][s]["C_black"] for p in per]}
    for c, p in zip(CHANNELS, per):
        ch = {k: v for k, v in p.items() if k not in ("sweeps", "fitted")}
        good = [s for s in sweeps if out["sweeps"][s]["L"][CHANNELS.index(c)] > 0]
        depths = np.array([out["sweeps"][s]["depth_m"] for s in good])
        if len(good) >= 2 and np.ptp(depths) >= 2.0:
            lnL = np.log([out["sweeps"][s]["L"][CHANNELS.index(c)] for s in good])
            X = np.c_[np.ones_like(depths), -depths]
            coef, res, *_ = np.linalg.lstsq(X, lnL, rcond=None)
            resid = lnL - X @ coef
            se = (np.sqrt(np.sum(resid ** 2) / max(1, len(good) - 2)
                          * np.linalg.inv(X.T @ X)[1, 1]) if len(good) > 2 else None)
            ch.update(K=round(float(coef[1]), 4), K_se=None if se is None else round(float(se), 4),
                      ln_E0=round(float(coef[0]), 4), K_sweeps=len(good))
        else:
            ch.update(K=None, K_se=None, ln_E0=None, K_sweeps=len(good))
        out["channels"][c] = ch
    for o, *f in zip(obs, *(p["fitted"] for p in per)):
        o["fitted"] = f
    return out


def ramp_fit(raw: dict, keep: set, rho: dict, ramp) -> Optional[dict[str, Any]]:
    """Per-channel straight line through the usable greys: I = A·ρ + H.

    ``A`` is the light reaching the card (its colour = the white balance), ``H`` the additive
    haze. Uses the card's ``roles.ramp`` greys (one of each set of alternatives; V2: white,
    grey 200, grey 74 and grey 128 or its right half) — never black, whose print reflectance is
    not known (colour review 2026-09-27: the single-frame black estimate over-predicted haze
    ~3×). Needs ≥ 2 greys spanning ≥ 0.1 in reflectance.
    """
    ids = [first_usable(alts, keep.__contains__) for alts in ramp]
    ids = [p for p in ids if p and raw.get(p, {}).get("mean_norm")]
    x = np.array([rho[p] for p in ids])
    if len(ids) < 2 or np.ptp(x) < 0.1:
        return None
    Y = np.array([raw[p]["mean_norm"] for p in ids])
    X = np.c_[x, np.ones_like(x)]
    coef, *_ = np.linalg.lstsq(X, Y, rcond=None)
    return {"A": coef[0].tolist(), "H": coef[1].tolist(), "greys": ids}


def wb_points(root: Path, dist: dict, card: Card, categories) -> list[dict]:
    """Light colour per card frame, from the grey-ramp slope ``A`` (haze-free)."""
    rows = {r["stem"]: r for r in csv.DictReader((root / "ingest" / "manifest.csv").open())}
    patches = json.loads((root / "patches" / "patches.json").read_text())
    qc = json.loads((root / "qc" / "qc.json").read_text())
    rho = grey_reflectance(card)
    out = []
    for stem, q in qc.items():
        r, d = rows[stem], dist.get(stem, {})
        if (r["category"] not in categories or not q["usable"] or d.get("medium") != "water"
                or d.get("z_m") is None or "raw" not in patches.get(stem, {})
                or r["flash_fired"] == "True"):
            continue
        keep = {pid for pid, p in q["patches"].items() if p["usable"]}
        ramp = ramp_fit(patches[stem]["raw"]["patches"], keep, rho, card.roles.ramp)
        if ramp is None or min(ramp["A"]) <= 0:
            continue
        A, H = np.asarray(ramp["A"]), np.asarray(ramp["H"])
        out.append({"stem": stem, "dive": r["dive_id"], "category": r["category"],
                    "depth_m": float(r["depth_m"]), "z_m": d["z_m"],
                    "greys": len(ramp["greys"]),
                    "ln_rg": float(np.log(A[0] / A[1])), "ln_bg": float(np.log(A[2] / A[1])),
                    "haze_frac_g": float(H[1] / (A[1] + H[1]))})
    return out


def _regress(points: list[dict], key: str, with_z: bool = False) -> dict[str, Any]:
    y = np.array([p[key] for p in points])
    cols = [np.ones_like(y), np.array([p["depth_m"] for p in points])]
    if with_z:
        cols.append(np.array([p["z_m"] for p in points]))
    X = np.stack(cols, axis=1)
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    dof = max(1, len(y) - X.shape[1])
    cov = np.sum(resid ** 2) / dof * np.linalg.pinv(X.T @ X)
    names = ["intercept", "per_m_depth"] + (["per_m_distance"] if with_z else [])
    return {"n": len(y), "rms": round(float(np.sqrt(np.mean(resid ** 2))), 4),
            **{n: round(float(c), 5) for n, c in zip(names, coef)},
            **{f"{n}_se": round(float(np.sqrt(cov[i, i])), 5) for i, n in enumerate(names)}}


def wb_table(points: list[dict], flagged: dict) -> dict[str, Any]:
    table: dict[str, Any] = {}
    for dive in sorted({p["dive"] for p in points}, key=int):
        pts = [p for p in points if p["dive"] == dive]
        if len(pts) < 3 or np.ptp([p["depth_m"] for p in pts]) < 2.0:
            table[dive] = {"n": len(pts), "usable": False}
            continue
        table[dive] = {"n": len(pts), "usable": True,
                       "depth_range_m": [min(p["depth_m"] for p in pts),
                                         max(p["depth_m"] for p in pts)],
                       "ln_rg": _regress(pts, "ln_rg"), "ln_bg": _regress(pts, "ln_bg"),
                       "ln_rg_with_distance": _regress(pts, "ln_rg", with_z=True),
                       "ln_bg_with_distance": _regress(pts, "ln_bg", with_z=True)}
        if dive in flagged:
            table[dive]["flag"] = flagged[dive]
    return table


def fit_settings(dataset_config: Path) -> dict[str, Any]:
    """The dataset's ``fit:`` block (SPEC §20 config, not code)."""
    fit_cfg = load_yaml(dataset_config).get("fit") or {}
    for key in ("reference_category", "wb_categories", "table_source_dives"):
        if key not in fit_cfg:
            raise ConfigError(f"{dataset_config}: fit.{key} missing")
    return {**fit_cfg, "flagged_dives": {str(k): v for k, v in
                                         (fit_cfg.get("flagged_dives") or {}).items()},
            "table_source_dives": [str(d) for d in fit_cfg["table_source_dives"]]}


def fit(qc_dir: Path, distance_dir: Path, card_path: Path, calibration: Path,
        dataset_config: Path) -> dict[str, Any]:
    verify_fresh(qc_dir)
    verify_fresh(distance_dir)
    root = qc_dir.parent
    card = load_card(card_path)
    calib = load_yaml(calibration)
    settings = fit_settings(dataset_config)
    flagged = settings["flagged_dives"]
    dist = json.loads((distance_dir / "distances.json").read_text())
    obs = observations(root, dist, card, calib["intrinsics"]["principal_point_raw"],
                       settings["reference_category"])
    params: dict[str, Any] = {}
    for dive in sorted({o["dive"] for o in obs}, key=int):
        params[dive] = fit_dive([o for o in obs if o["dive"] == dive])
        if dive in flagged:
            params[dive]["flag"] = flagged[dive]

    points = wb_points(root, dist, card, settings["wb_categories"])
    table = wb_table(points, flagged)

    out_dir = root / "fit"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "params.json").write_text(json.dumps(params, indent=1) + "\n")
    (out_dir / "wb_table.json").write_text(json.dumps(table, indent=1) + "\n")
    with (out_dir / "wb_points.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(points[0]) if points else ["stem"])
        w.writeheader()
        w.writerows(points)
    with (out_dir / "observations.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stem", "dive", "sweep", "depth_m", "z_m", "patch", "rho", "radius",
                    "I_R", "I_G", "I_B", "fit_R", "fit_G", "fit_B"])
        for o in obs:
            w.writerow([o["stem"], o["dive"], o["sweep"], o["depth_m"], o["z_m"], o["patch"],
                        o["rho"], o["radius"], *(f"{v:.6g}" for v in o["I"]),
                        *(f"{v:.6g}" for v in o["fitted"])])
    summary = {"observations": len(obs), "wb_points": len(points),
               "wb_table": {d: ({k: t[k]["per_m_depth"] for k in ("ln_rg", "ln_bg")}
                                if t["usable"] else "unusable") for d, t in table.items()},
               "dives": {
        d: {"frames": p["frames"], "sweeps": len(p["sweeps"]),
            "identifiable_sweeps": sum(s["identifiable"] for s in p["sweeps"].values()),
            **{c: {k: p["channels"][c][k] for k in ("beta_d", "beta_d_ci", "beta_b",
                                                     "chi2_reduced", "K", "K_se")}
               for c in CHANNELS}} for d, p in params.items()}}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_stage(out_dir, "fit", configs=[card_path, calibration, dataset_config],
                upstream=[qc_dir, distance_dir],
                params={"beta_d_grid": [float(BETA_D[0]), float(BETA_D[-1]), BETA_D.size],
                        "beta_b_grid": [float(BETA_B[0]), float(BETA_B[-1]), BETA_B.size],
                        "max_radius": MAX_RADIUS, "min_black_px": MIN_BLACK_PX,
                        "rel_sigma": REL_SIGMA, "floor_sigma": FLOOR_SIGMA,
                        "fit_category": settings["reference_category"],
                        "wb_haze": "grey-ramp intercept (not the black patch)"})
    summary["out_dir"] = str(out_dir)
    return summary
