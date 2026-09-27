"""Visual sheets for the S2a decision (SPEC §4 Phase 8 S2a, §20): the column cut sheet and the
blind, side-randomized review that decides the gate.

- **Cut sheet** (``decide/cutsheet.html``): one row per frame, one whole-frame thumbnail per
  method — camera JPEG (or Olympus preset JPEG), JPEG + card WB (rendered here from the camera
  JPEG: linear gain on the anchor grey), RAW + card WB, RAW + card WB − haze, RAW + card WB +
  depth matrix (v0.3), RAW + depth WB − haze (no card), the same + depth matrix (v0.3), GRVI. Frames: the middle frame of every sweep, every off-centre and no-card
  frame (torch frames have no Nereus output), every preset frame.
- **Blind review** (``decide/blind.html``): pairs of images with the method names hidden and the
  sides randomized — card-free: camera JPEG vs Nereus no card (off-centre, no-card, one frame per
  sweep); card-anchored: GRVI vs RAW + card WB (one frame per sweep where GRVI found the card).
  Choices are kept in the browser and downloaded as JSON. The side of each pair comes from a hash
  of (seed, class, frame), never from the page, so the key is stable across re-runs and does
  not depend on which pairs are listed. Saved answers (``<dataset config>_blind_answers.json``
  next to the dataset config — versioned human work, like the manual corners) are scored by
  ``score_blind``.
"""

from __future__ import annotations

import base64
import hashlib
import html
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from .correct import COLUMNS, PRESET_CATEGORY
from .jpeg_geometry import read_jpeg
from .metrics import srgb8_to_linear
from .report import STYLE

SEED = "s2a-blind-2026-09-27"
THUMB_W, BLIND_W = 300, 640
CUT_COLUMNS = ("camera", "jpeg_card_wb", "raw_card_wb", "raw_card_wb_haze", "raw_card_wb_ccm",
               "raw_depth_wb_haze", "raw_depth_wb_haze_ccm", "grvi_cheeca_v3")
BLIND = {"card_free": ("camera", "raw_depth_wb_haze"),
         "card_anchored": ("grvi_cheeca_v3", "raw_card_wb")}  # (baseline, Nereus)


def nereus_side(cls: str, stem: str) -> str:
    """'A' or 'B': where the Nereus image of this pair is shown."""
    return "AB"[hashlib.sha256(f"{SEED}:{cls}:{stem}".encode()).digest()[0] & 1]


def _encode(img: Optional[np.ndarray], width: int) -> Optional[str]:
    if img is None:
        return None
    img = cv2.resize(img, (width, int(width * img.shape[0] / img.shape[1])),
                     interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 72])
    return base64.b64encode(buf).decode() if ok else None


class Images:
    """Finds (or renders) each method's output for a frame, as BGR arrays."""

    def __init__(self, root: Path, dataset_dir: Path, rows: dict, scores: dict, patches: dict,
                 card_truth: dict):
        self.root, self.dataset_dir, self.rows = root, dataset_dir, rows
        self.scores, self.patches, self.truth = scores, patches, card_truth

    def camera(self, stem: str) -> Optional[np.ndarray]:
        jpeg = self.rows[stem].get("jpeg")
        return read_jpeg(self.dataset_dir / jpeg) if jpeg else None

    def get(self, stem: str, method: str) -> Optional[np.ndarray]:
        if method == "camera":
            return self.camera(stem)
        if method == "grvi_cheeca_v3":
            path = self.root / "grvi" / "images" / f"{stem}.jpg"
            return read_jpeg(path) if path.is_file() else None
        if method == "jpeg_card_wb":
            return self.jpeg_card_wb(stem)
        path = self.root / "correct" / "images" / method / f"{stem}.jpg"
        return cv2.imread(str(path)) if path.is_file() else None

    def jpeg_card_wb(self, stem: str) -> Optional[np.ndarray]:
        """The camera JPEG with the ``jpeg_card_wb`` scoring map: linear gain on the anchor."""
        anchor = (self.scores.get(stem) or {}).get("anchor")
        stats = ((self.patches.get(stem) or {}).get("jpeg") or {}).get("patches", {})
        img = self.camera(stem)
        if img is None or not anchor or not stats.get(anchor, {}).get("mean"):
            return None
        gain = self.truth[anchor] / np.maximum(srgb8_to_linear(stats[anchor]["mean"]), 1e-9)
        small = cv2.resize(img, (BLIND_W, int(BLIND_W * img.shape[0] / img.shape[1])),
                           interpolation=cv2.INTER_AREA)
        lin = srgb8_to_linear(small[..., ::-1].astype(np.float64)) * gain
        x = np.clip(lin, 0, 1)
        x = np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)
        return np.round(x * 255).astype(np.uint8)[..., ::-1].copy()


def cut_frames(rows: dict, scores: dict, rendered: set) -> list[str]:
    """Middle frame of every sweep, every off-centre / no-card / preset frame with output."""
    by_sweep: dict[str, list] = {}
    for s, r in rows.items():
        if r["sweep_id"] and s in scores:
            by_sweep.setdefault(r["sweep_id"], []).append(s)
    picked = {sorted(v)[len(v) // 2] for v in by_sweep.values()}
    picked |= {s for s in rendered if not rows[s]["sweep_id"]
               and not scores.get(s, {}).get("flash")}
    picked |= {s for s, v in scores.items() if v["category"] == PRESET_CATEGORY}
    return sorted(picked, key=lambda s: rows[s]["time_utc"])


def blind_pairs(rows: dict, scores: dict, rendered: set) -> list[tuple[str, str]]:
    """[(class, stem)] for the blind review."""
    pairs = []
    mids = {}
    for s in sorted(scores, key=lambda s: rows[s]["time_utc"]):
        if rows[s]["sweep_id"] and not scores[s].get("flash"):
            mids.setdefault(rows[s]["sweep_id"], []).append(s)
    mid = [sorted(v)[len(v) // 2] for v in mids.values()]
    free = sorted({s for s in rendered if not rows[s]["sweep_id"]
                   and rows[s]["category"] != PRESET_CATEGORY
                   and not scores.get(s, {}).get("flash")} | set(mid),
                  key=lambda s: rows[s]["time_utc"])
    pairs += [("card_free", s) for s in free if s in rendered]
    found = [s for s in mid if not scores[s]["methods"].get("grvi_cheeca_v3", {})
             .get("grvi_no_card", True)]
    pairs += [("card_anchored", s) for s in found]
    return pairs


def _page(title: str, body: str, script: str = "") -> str:
    extra = (".sheet td{vertical-align:top;padding:4px} .sheet img{width:%dpx;display:block;"
             "border-radius:4px} .pair{display:grid;grid-template-columns:1fr 1fr;gap:12px;"
             "margin:8px 0 28px} .pair img{width:100%%;border-radius:6px;display:block} "
             ".choice{display:flex;gap:18px;margin:6px 0} fieldset{border:1px solid "
             "var(--gridline);border-radius:8px;padding:10px 14px;margin:0 0 18px}" % THUMB_W)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>'
            f"<style>{STYLE}{extra}</style></head><body><main>{body}</main>{script}</body>"
            f"</html>")


def cut_sheet(images: Images, frames: list[str], rows: dict, scores: dict) -> str:
    head = "".join(f"<th>{html.escape(COLUMNS.get(m, 'Camera JPEG / Olympus preset'))}</th>"
                   for m in CUT_COLUMNS)
    body = []
    for s in frames:
        cells = []
        for m in CUT_COLUMNS:
            data = _encode(images.get(s, m), THUMB_W)
            sc = scores.get(s, {}).get("methods", {})
            key = ("olympus_preset_jpeg" if rows[s]["category"] == PRESET_CATEGORY
                   else "camera_jpeg") if m == "camera" else m
            de = sc.get(key, {}).get("de2000_median")
            tag = " · GRVI: no card" if m == "grvi_cheeca_v3" and sc.get(m, {}).get(
                "grvi_no_card") else ""
            cap = f"ΔE00 {de:.1f}{tag}" if de is not None else tag.strip(" ·")
            cells.append(f'<td><img alt="" src="data:image/jpeg;base64,{data}">'
                         f'<span class="muted">{html.escape(cap)}</span></td>' if data
                         else '<td class="muted">—</td>')
        r = rows[s]
        body.append(f"<tr><td><b>{html.escape(s)}</b><br><span class=\"muted\">dive "
                    f"{r['dive_id']} · {html.escape(r['category'])} · {r['depth_m']} m"
                    f"</span></td>{''.join(cells)}</tr>")
    return _page("S2a Cut Sheet", ("<style>main{max-width:none}</style>"
        "<h1>Phase 8 · S2a cut sheet</h1><p class=\"note\">Whole frames, one column per method. "
        "RAW outputs are 2 × 2 binned and in sensor orientation; the camera JPEG is shown in its "
        "stored orientation too. ΔE00 = the frame's median over the scored colour patches (card "
        "frames). JPEG + card WB is rendered here from the camera JPEG with its scoring gain."
        f"</p><div class=\"scroll\" style=\"max-height:none\"><table class=\"sheet\"><thead><tr>"
        f"<th>frame</th>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"))


BLIND_JS = """<script>
const KEY = 's2a-blind-answers';
let saved = {};
try { saved = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) {}
const count = () => { document.getElementById('count').textContent = Object.keys(saved).length; };
document.querySelectorAll('input[type=radio]').forEach(r => {
  if (saved[r.name] === r.value) r.checked = true;
  r.addEventListener('change', () => {
    saved[r.name] = r.value;
    try { localStorage.setItem(KEY, JSON.stringify(saved)); } catch (e) {}
    count();
  });
});
count();
document.getElementById('dl').addEventListener('click', () => {
  const data = {seed: document.body.dataset.seed, answers: saved};
  const blob = new Blob([JSON.stringify(data, null, 1)], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = document.body.dataset.file;
  a.click();
});
</script>"""


def blind_sheet(images: Images, pairs: list[tuple[str, str]], answers_file: str) -> str:
    blocks = []
    for i, (cls, stem) in enumerate(pairs, 1):
        base, ours = BLIND[cls]
        side = nereus_side(cls, stem)
        left, right = (ours, base) if side == "A" else (base, ours)
        a, b = _encode(images.get(stem, left), BLIND_W), _encode(images.get(stem, right), BLIND_W)
        if not a or not b:
            continue
        name = f"{cls}:{stem}"
        opts = "".join(f'<label><input type="radio" name="{name}" value="{v}"> {t}</label>'
                       for v, t in (("A", "A looks more natural"), ("B", "B looks more natural"),
                                    ("=", "no preference")))
        blocks.append(f'<fieldset><legend>{i} · {html.escape(stem)}</legend><div class="pair">'
                      f'<figure><figcaption>A</figcaption><img alt="A" '
                      f'src="data:image/jpeg;base64,{a}"></figure><figure><figcaption>B'
                      f'</figcaption><img alt="B" src="data:image/jpeg;base64,{b}"></figure>'
                      f'</div><div class="choice">{opts}</div></fieldset>')
    body = ("<h1>Phase 8 · S2a blind review</h1><p class=\"note\">For each pair, pick the image "
            "whose colours look more like the real scene. Method names are hidden and the sides "
            "are randomized. Choices stay in this browser; when done, download them, save the file "
            f"as <code>{html.escape(answers_file)}</code>, commit it, and re-run "
            "<code>decide</code>.</p><p><b id=\"count\">0</b> of " + str(len(blocks))
            + " answered · <button id=\"dl\" type=\"button\">Download answers</button></p>"
            + "".join(blocks))
    return _page("S2a Blind Review", body, BLIND_JS).replace(
        "<body>", f'<body data-seed="{SEED}" '
                  f'data-file="{html.escape(Path(answers_file).name)}">', 1)


def score_blind(answers: dict) -> dict[str, Any]:
    """Share of answered pairs (ties excluded) where Nereus was preferred, per class."""
    if answers.get("seed") != SEED:
        raise ValueError(f"blind answers were made with seed {answers.get('seed')!r}, "
                         f"not {SEED!r}: the key does not match")
    out: dict[str, Any] = {}
    for name, choice in answers["answers"].items():
        cls, stem = name.split(":", 1)
        c = out.setdefault(cls, {"nereus": 0, "baseline": 0, "tie": 0})
        if choice == "=":
            c["tie"] += 1
        else:
            c["nereus" if choice == nereus_side(cls, stem) else "baseline"] += 1
    for c in out.values():
        n = c["nereus"] + c["baseline"]
        c["prefer_nereus"] = round(c["nereus"] / n, 3) if n else None
        c["rule_pass"] = n > 0 and c["nereus"] / n >= 0.70
    return out
