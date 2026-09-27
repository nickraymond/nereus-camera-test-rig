"""Colour metrics and the scoring protocol — SPEC §4 Phase 8 S1.6, §20; brief §2.

- **ψ** (primary): angle in degrees between a grey patch's *linear* RGB and neutral
  (1, 1, 1). 0° = perfectly neutral. Scored on grey patches **not** used to neutralize.
- **ΔE2000** (CIEDE2000, Sharma, Wu & Dalal 2005) on the colour patches, after an
  **L*-only match on grey 128**: one scale factor on linear RGB brings the anchor grey's
  luminance to its truth, so exposure is not scored but colour is. Plus the CIE76
  chroma / hue components ΔC*ab and |ΔH*ab|.
- **Red SNR** on white / grey 200 in linear RAW (mean ÷ std) and red as a fraction of full
  scale (brief: < ~5 % ⇒ unrecoverable).

Every method is scored on its final 8-bit sRGB output (``score_srgb8``): decode with the
sRGB transfer function, then the metrics above, on the same patches. Internal diagnostics
on RAW apply white balance + a colour matrix first (``camera_to_linear``) — ψ in raw camera
RGB is meaningless.

**SNR gate.** A grey patch is scored for ψ only if it has signal: its brightest channel has
SNR ≥ ``SNR_MIN`` (when stds are given) and a linear value ≥ ``MIN_SIGNAL``. A channel that
an output drove to zero is *not* gated — it is the method's error and counts (e.g. the TG-7
JPEG's red clipped to 0 under water). Gated patches are listed so every table can state n.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np

from .card import Card

SNR_MIN = 3.0
MIN_SIGNAL = 1e-3  # linear, ~ 8-bit sRGB value 3

# IEC 61966-2-1 linear sRGB → XYZ (D65); the white point is its row sums, so sRGB white is
# exactly L* = 100, a* = b* = 0.
SRGB_TO_XYZ = np.array([[0.4124, 0.3576, 0.1805],
                        [0.2126, 0.7152, 0.0722],
                        [0.0193, 0.1192, 0.9505]])
WHITE_XYZ = SRGB_TO_XYZ.sum(axis=1)


def srgb8_to_linear(v) -> np.ndarray:
    x = np.asarray(v, dtype=np.float64) / 255.0
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def linear_to_lab(rgb) -> np.ndarray:
    """Linear sRGB (..., 3) → CIELAB (D65)."""
    xyz = np.asarray(rgb, dtype=np.float64) @ SRGB_TO_XYZ.T / WHITE_XYZ
    d = 6 / 29
    f = np.where(xyz > d ** 3, np.cbrt(xyz), xyz / (3 * d * d) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], axis=-1)


def luminance(rgb) -> np.ndarray:
    return np.asarray(rgb, dtype=np.float64) @ SRGB_TO_XYZ[1]


def psi_deg(rgb) -> np.ndarray:
    """Angle (degrees) between linear RGB (..., 3) and the neutral axis."""
    v = np.asarray(rgb, dtype=np.float64)
    cos = v.sum(axis=-1) / (np.sqrt(3) * np.linalg.norm(v, axis=-1))
    return np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))


def delta_e2000(lab1, lab2) -> np.ndarray:
    """CIEDE2000 (kL = kC = kH = 1), vectorized over leading axes."""
    L1, a1, b1 = np.moveaxis(np.asarray(lab1, dtype=np.float64), -1, 0)
    L2, a2, b2 = np.moveaxis(np.asarray(lab2, dtype=np.float64), -1, 0)
    cbar7 = ((np.hypot(a1, b1) + np.hypot(a2, b2)) / 2) ** 7
    g = 0.5 * (1 - np.sqrt(cbar7 / (cbar7 + 25.0 ** 7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.where(c1p == 0, 0.0, np.degrees(np.arctan2(b1, a1p)) % 360)
    h2p = np.where(c2p == 0, 0.0, np.degrees(np.arctan2(b2, a2p)) % 360)
    zero = c1p * c2p == 0

    dh = h2p - h1p
    dh = np.where(dh > 180, dh - 360, np.where(dh < -180, dh + 360, dh))
    dh = np.where(zero, 0.0, dh)
    dL, dC = L2 - L1, c2p - c1p
    dH = 2 * np.sqrt(c1p * c2p) * np.sin(np.radians(dh / 2))

    lbar, cbar = (L1 + L2) / 2, (c1p + c2p) / 2
    hsum = h1p + h2p
    hbar = np.where(zero, hsum,
                    np.where(np.abs(h1p - h2p) <= 180, hsum / 2,
                             np.where(hsum < 360, (hsum + 360) / 2, (hsum - 360) / 2)))
    t = (1 - 0.17 * np.cos(np.radians(hbar - 30)) + 0.24 * np.cos(np.radians(2 * hbar))
         + 0.32 * np.cos(np.radians(3 * hbar + 6)) - 0.20 * np.cos(np.radians(4 * hbar - 63)))
    dtheta = 30 * np.exp(-(((hbar - 275) / 25) ** 2))
    rc = 2 * np.sqrt(cbar ** 7 / (cbar ** 7 + 25.0 ** 7))
    sl = 1 + 0.015 * (lbar - 50) ** 2 / np.sqrt(20 + (lbar - 50) ** 2)
    sc, sh = 1 + 0.045 * cbar, 1 + 0.015 * cbar * t
    rt = -np.sin(np.radians(2 * dtheta)) * rc
    return np.sqrt((dL / sl) ** 2 + (dC / sc) ** 2 + (dH / sh) ** 2
                   + rt * (dC / sc) * (dH / sh))


def delta_ch(lab_ref, lab) -> tuple[np.ndarray, np.ndarray]:
    """CIE76 components: ΔC*ab (signed, sample − reference) and |ΔH*ab|."""
    r, s = np.asarray(lab_ref, dtype=np.float64), np.asarray(lab, dtype=np.float64)
    dc = np.hypot(s[..., 1], s[..., 2]) - np.hypot(r[..., 1], r[..., 2])
    dab2 = (s[..., 1] - r[..., 1]) ** 2 + (s[..., 2] - r[..., 2]) ** 2
    return dc, np.sqrt(np.maximum(dab2 - dc ** 2, 0.0))


def camera_to_linear(rgb, wb: Sequence[float], matrix: Optional[np.ndarray] = None):
    """Raw camera RGB → white-balanced (and, with a 3×3 ``matrix``, colour-corrected) RGB."""
    out = np.asarray(rgb, dtype=np.float64) * np.asarray(wb, dtype=np.float64)
    return out @ np.asarray(matrix).T if matrix is not None else out


def red_signal(raw_patches: Mapping[str, Mapping[str, Any]],
               ids: Iterable[str] = ("gray_white", "gray_light")) -> dict[str, dict]:
    """Red SNR (mean ÷ std) and red fraction of full scale on linear-RAW patch stats."""
    out = {}
    for pid in ids:
        s = raw_patches.get(pid) or {}
        if not s.get("mean"):
            continue
        mean, std = s["mean"][0], s["std"][0]
        out[pid] = {"red_snr": round(mean / std, 2) if std else None,
                    "red_full_scale": round(mean, 5)}
    return out


def _truths(card: Card) -> dict[str, tuple[int, int, int]]:
    truth = {p.id: p.truth for p in card.patches}
    truth.update({s.id: truth[s.parent] for s in card.sub_patches})
    return truth


def _has_signal(lin: np.ndarray, std_lin: Optional[np.ndarray], snr_min: float) -> bool:
    k = int(np.argmax(lin))
    if lin[k] < MIN_SIGNAL:
        return False
    return std_lin is None or std_lin[k] == 0 or lin[k] / std_lin[k] >= snr_min


def score_linear(means: Mapping[str, Sequence[float]], card: Card, *,
                 neutralized: Iterable[str] = ("gray_mid",), anchor: str = "gray_mid",
                 exclude: Iterable[str] = (), stds: Optional[Mapping] = None,
                 snr_min: float = SNR_MIN) -> dict[str, Any]:
    """Score linear-sRGB patch means (0..1) of one frame by the §20 protocol.

    ``neutralized``: patches the method used to neutralize (held out of ψ). ``anchor``: the
    grey for the L*-only match (``gray_mid``, or ``gray_mid_right`` where the left half is
    damaged). ``exclude``: patches qc marked unusable.
    """
    truth, excluded = _truths(card), set(exclude)
    neutral_ids = set(neutralized)
    greys = [p.id for p in card.group("grey") if p.id not in excluded | neutral_ids]
    psi, gated = {}, []
    for pid in greys:
        if pid not in means:
            continue
        lin = np.asarray(means[pid], dtype=np.float64)
        sd = np.asarray(stds[pid], dtype=np.float64) if stds and pid in stds else None
        if _has_signal(lin, sd, snr_min):
            psi[pid] = round(float(psi_deg(lin)), 3)
        else:
            gated.append(pid)

    de: dict[str, dict] = {}
    anchor_ok = anchor in means and anchor not in excluded
    if anchor_ok and luminance(means[anchor]) > 0:
        k = luminance(srgb8_to_linear(truth[anchor])) / luminance(means[anchor])
        for p in card.group("color"):
            if p.id in excluded or p.id not in means:
                continue
            ref = linear_to_lab(srgb8_to_linear(p.truth))
            lab = linear_to_lab(np.asarray(means[p.id], dtype=np.float64) * k)
            dc, dh = delta_ch(ref, lab)
            de[p.id] = {"de2000": round(float(delta_e2000(ref, lab)), 3),
                        "dC": round(float(dc), 3), "dH": round(float(dh), 3)}
    values = np.array([v["de2000"] for v in de.values()])
    return {
        "psi": psi, "psi_gated": gated, "n_psi": len(psi),
        "psi_median": round(float(np.median(list(psi.values()))), 3) if psi else None,
        "de2000": de, "n_de": len(de), "anchor": anchor if anchor_ok else None,
        "de2000_median": round(float(np.median(values)), 3) if values.size else None,
        "de2000_p90": round(float(np.percentile(values, 90)), 3) if values.size else None,
    }


def score_srgb8(means: Mapping[str, Sequence[float]], card: Card, **kw) -> dict[str, Any]:
    """``score_linear`` on 8-bit sRGB patch means (a method's final output). Stds, if
    given, are 8-bit too; they are carried through the local slope of the sRGB decode."""
    lin = {k: srgb8_to_linear(v) for k, v in means.items()}
    stds = kw.pop("stds", None)
    if stds:
        eps = 0.5
        stds = {k: np.abs(srgb8_to_linear(np.asarray(means[k]) + eps)
                          - srgb8_to_linear(np.asarray(means[k]) - eps)) / (2 * eps)
                * np.asarray(v) for k, v in stds.items() if k in means}
    return score_linear(lin, card, stds=stds, **kw)
