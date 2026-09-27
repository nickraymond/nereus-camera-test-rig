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

Output: ``fit/{params.json, observations.csv, summary.json, stage.json}``.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import load_yaml
from .card import Card, load_card
from .metrics import srgb8_to_linear
from .patches import homography
from .stages import verify_fresh, write_stage

CHANNELS = "RGB"
REFERENCE = "1_reference_A_iso100"
BETA_D = np.round(np.arange(0.0, 1.5001, 0.015), 4)
BETA_B = np.round(np.geomspace(0.03, 10.0, 56), 4)
MAX_RADIUS = 0.6          # of the image half-diagonal (brief/SPEC S2a: no flat-field yet)
MIN_BLACK_PX = 6          # binned px, shorter side of the black sample
REL_SIGMA = 0.03          # per-observation error: 3 % of the value ...
FLOOR_SIGMA = 0.003       # ... plus 0.3 % of the dive-channel maximum
IDENTIFIABLE_Z_RATIO = 1.5
FLAGGED_DIVES = {"1": "sunset dive: light falls through the dive",
                 "2": "early-morning dive: light rises through the dive"}


def grey_reflectance(card: Card) -> dict[str, float]:
    """Linear reflectance of the neutral patches relative to white (from the design sRGB)."""
    white = float(srgb8_to_linear(card.patch("gray_white").truth[0]))
    out = {p.id: float(srgb8_to_linear(p.truth[0])) / white for p in card.group("grey")}
    out["gray_mid_right"] = out["gray_mid"]
    return out


def _grey_ids(qpatches: dict) -> list[str]:
    ids = ["gray_white", "gray_light", "gray_dark", "gray_black"]
    if qpatches["gray_mid"]["usable"]:
        ids.append("gray_mid")
    elif qpatches["gray_mid_right"]["usable"]:
        ids.append("gray_mid_right")
    return [i for i in ids if qpatches[i]["usable"]]


def observations(root: Path, dist: dict, card: Card, principal) -> list[dict]:
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
        if (r["category"] != REFERENCE or not r["sweep_id"] or not q["usable"]
                or d.get("medium") != "water" or d.get("z_m") is None):
            continue
        H = homography(card, np.asarray(corners[stem]["quad_raw"]))
        raw = patches[stem]["raw"]["patches"]
        for pid in _grey_ids(q["patches"]):
            b = boxes[pid]
            centre = cv2.perspectiveTransform(
                np.array([[[b.x + b.w / 2, b.y + b.h / 2]]], dtype=np.float64), H)[0, 0]
            radius = float(np.linalg.norm(centre - pp)) / half_diag
            if radius > MAX_RADIUS:
                continue
            if pid == "gray_black" and min(raw[pid]["size_px"]) < MIN_BLACK_PX:
                continue
            obs.append({"stem": stem, "dive": r["dive_id"], "sweep": r["sweep_id"],
                        "depth_m": float(r["depth_m"]), "z_m": d["z_m"], "patch": pid,
                        "rho": None if pid == "gray_black" else rho[pid],
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


def fit(qc_dir: Path, distance_dir: Path, card_path: Path, calibration: Path) -> dict[str, Any]:
    verify_fresh(qc_dir)
    verify_fresh(distance_dir)
    root = qc_dir.parent
    card = load_card(card_path)
    calib = load_yaml(calibration)
    dist = json.loads((distance_dir / "distances.json").read_text())
    obs = observations(root, dist, card, calib["intrinsics"]["principal_point_raw"])
    params: dict[str, Any] = {}
    for dive in sorted({o["dive"] for o in obs}, key=int):
        params[dive] = fit_dive([o for o in obs if o["dive"] == dive])
        if dive in FLAGGED_DIVES:
            params[dive]["flag"] = FLAGGED_DIVES[dive]

    out_dir = root / "fit"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "params.json").write_text(json.dumps(params, indent=1) + "\n")
    with (out_dir / "observations.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stem", "dive", "sweep", "depth_m", "z_m", "patch", "rho", "radius",
                    "I_R", "I_G", "I_B", "fit_R", "fit_G", "fit_B"])
        for o in obs:
            w.writerow([o["stem"], o["dive"], o["sweep"], o["depth_m"], o["z_m"], o["patch"],
                        o["rho"], o["radius"], *(f"{v:.6g}" for v in o["I"]),
                        *(f"{v:.6g}" for v in o["fitted"])])
    summary = {"observations": len(obs), "dives": {
        d: {"frames": p["frames"], "sweeps": len(p["sweeps"]),
            "identifiable_sweeps": sum(s["identifiable"] for s in p["sweeps"].values()),
            **{c: {k: p["channels"][c][k] for k in ("beta_d", "beta_d_ci", "beta_b",
                                                     "chi2_reduced", "K", "K_se")}
               for c in CHANNELS}} for d, p in params.items()}}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_stage(out_dir, "fit", configs=[card_path, calibration], upstream=[qc_dir, distance_dir],
                params={"beta_d_grid": [float(BETA_D[0]), float(BETA_D[-1]), BETA_D.size],
                        "beta_b_grid": [float(BETA_B[0]), float(BETA_B[-1]), BETA_B.size],
                        "max_radius": MAX_RADIUS, "min_black_px": MIN_BLACK_PX,
                        "rel_sigma": REL_SIGMA, "floor_sigma": FLOOR_SIGMA,
                        "fit_category": REFERENCE})
    summary["out_dir"] = str(out_dir)
    return summary
