"""Visual sheets for the S2a decision (SPEC §4 Phase 8 S2a, §20): the column cut sheet and the
blind, side-randomized review that decides the gate.

- **Cut sheet** (``decide/cutsheet.html``): one row per frame, one whole-frame thumbnail per
  method — camera JPEG (or Olympus preset JPEG), JPEG + card WB (rendered here from the camera
  JPEG: linear gain on the anchor grey), RAW + card WB, RAW + card WB − haze, RAW + card WB +
  depth matrix (v0.3), per-frame card affine, RAW + depth WB − haze (no card), the same +
  depth matrix (v0.3), GRVI. Frames: the middle frame of every sweep, every off-centre and
  no-card frame (torch frames have no Nereus output), every preset frame.
- **Blind reviews** (``REVIEWS``): pairs of images with the method names hidden and the sides
  randomized. ``gate`` (``decide/blind.html``) decides S2a — camera JPEG vs Nereus no card
  (off-centre, no-card, one frame per sweep) and GRVI vs RAW + card WB (one frame per sweep
  where GRVI found the card); ``v02_v03m`` (``blind_v02_v03m.html``) compares v0.2 with the v0.3
  depth matrix fitted to the measured print, with and without the card; ``custom_review`` builds any REF:CAND pair.
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
CUT_COLUMNS = ("camera", "jpeg_card_wb", "raw_card_wb", "raw_card_slope_wb", "raw_card_wb_haze",
               "raw_card_wb_ccm", "raw_card_affine",
               "raw_depth_wb_haze", "raw_depth_wb_haze_ccm", "grvi_cheeca_v3")
# Blind reviews: named sets of comparisons. Each compares a reference and a candidate method on
# a frame set ("card": one frame per sweep; "free": that plus off-centre and no-card frames).
# The comparison id seeds the sides, so a review's key never changes when others are added.
REVIEWS = {
    "gate": ({"id": "card_free", "frames": "free", "reference": "camera",
              "candidate": "raw_depth_wb_haze"},
             {"id": "card_anchored", "frames": "card", "reference": "grvi_cheeca_v3",
              "candidate": "raw_card_wb"}),
    # v0.2 vs v0.3 with v0.3 fitted to the measured print. (The first v0.2 vs v0.3 review, on
    # the design-fitted v0.3, is archived as <dataset>_blind_answers_v02_v03_designfit.json:
    # its answers belong to images that no longer exist.)
    "v02_v03m": ({"id": "v02_v03m_card", "frames": "card", "reference": "raw_card_wb",
                  "candidate": "raw_card_wb_ccm"},
                 {"id": "v02_v03m_no_card", "frames": "free", "reference": "raw_depth_wb_haze",
                  "candidate": "raw_depth_wb_haze_ccm"}),
}


def custom_review(spec: str) -> tuple[str, tuple[dict, ...]]:
    """``REF:CAND[:card|free]`` (method names as in ``correct``) → a one-comparison review."""
    parts = spec.split(":")
    if len(parts) not in (2, 3) or (len(parts) == 3 and parts[2] not in ("card", "free")):
        raise ValueError(f"blind comparison {spec!r}: expected REF:CAND[:card|free]")
    ref, cand = parts[:2]
    name = f"{ref}_vs_{cand}"
    return name, ({"id": name, "frames": parts[2] if len(parts) == 3 else "free",
                   "reference": ref, "candidate": cand},)


def nereus_side(cls: str, stem: str) -> str:
    """'A' or 'B': where the candidate image of comparison ``cls`` is shown for ``stem``."""
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


def review_frames(rows: dict, scores: dict, rendered: set) -> dict[str, list[str]]:
    """{"card": the middle frame of every sweep, "free": those plus every off-centre and
    no-card frame with a Nereus output} — no flash or preset frames, in capture order."""
    mids: dict[str, list] = {}
    for s in sorted(scores, key=lambda s: rows[s]["time_utc"]):
        if rows[s]["sweep_id"] and not scores[s].get("flash"):
            mids.setdefault(rows[s]["sweep_id"], []).append(s)
    card = [sorted(v)[len(v) // 2] for v in mids.values()]
    free = sorted({s for s in rendered if not rows[s]["sweep_id"]
                   and rows[s]["category"] != PRESET_CATEGORY
                   and not scores.get(s, {}).get("flash")} | set(card),
                  key=lambda s: rows[s]["time_utc"])
    return {"card": card, "free": [s for s in free if s in rendered]}


def blind_pairs(frames: dict[str, list[str]], review) -> list[tuple[dict, str]]:
    """[(comparison, stem)] for a review."""
    return [(c, s) for c in review for s in frames[c["frames"]]]


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


def blind_sheet(images: Images, pairs: list[tuple[dict, str]], answers_file: str,
                name: str = "gate", title: str = "S2a blind review") -> tuple[str, int]:
    """(page, number of pairs shown). A pair is shown only when both images exist."""
    blocks = []
    for comp, stem in pairs:
        side = nereus_side(comp["id"], stem)
        left, right = ((comp["candidate"], comp["reference"]) if side == "A"
                       else (comp["reference"], comp["candidate"]))
        a, b = _encode(images.get(stem, left), BLIND_W), _encode(images.get(stem, right), BLIND_W)
        if not a or not b:
            continue
        field = f"{comp['id']}:{stem}"
        opts = "".join(f'<label><input type="radio" name="{field}" value="{v}"> {t}</label>'
                       for v, t in (("A", "A looks more natural"), ("B", "B looks more natural"),
                                    ("=", "no preference")))
        blocks.append(f'<fieldset><legend>{len(blocks) + 1} · {html.escape(stem)}</legend>'
                      f'<div class="pair"><figure><figcaption>A</figcaption><img alt="A" '
                      f'src="data:image/jpeg;base64,{a}"></figure><figure><figcaption>B'
                      f'</figcaption><img alt="B" src="data:image/jpeg;base64,{b}"></figure>'
                      f'</div><div class="choice">{opts}</div></fieldset>')
    body = (f"<h1>Phase 8 · {html.escape(title)}</h1><p class=\"note\">For each pair, pick the "
            "image whose colours look more like the real scene. Method names are hidden and the "
            "sides are randomized. Choices stay in this browser; when done, download them, save "
            f"the file as <code>{html.escape(answers_file)}</code>, commit it, and re-run "
            "<code>decide</code>.</p><p><b id=\"count\">0</b> of " + str(len(blocks))
            + " answered · <button id=\"dl\" type=\"button\">Download answers</button></p>"
            + "".join(blocks))
    # the gate keeps its original browser key, so answers already clicked survive a rebuild
    key = "s2a-blind-answers" if name == "gate" else f"s2a-blind-answers-{name}"
    page = _page(title, body, BLIND_JS.replace("'s2a-blind-answers'", f"'{key}'"))
    return page.replace("<body>", f'<body data-seed="{SEED}" '
                                  f'data-file="{html.escape(Path(answers_file).name)}">',
                        1), len(blocks)


def score_blind(answers: dict, review) -> dict[str, Any]:
    """Per comparison of ``review``: answered pairs (ties apart) preferring the candidate."""
    if answers.get("seed") != SEED:
        raise ValueError(f"blind answers were made with seed {answers.get('seed')!r}, "
                         f"not {SEED!r}: the key does not match")
    comps = {c["id"]: c for c in review}
    out: dict[str, Any] = {}
    for name, choice in answers["answers"].items():
        cid, stem = name.split(":", 1)
        if cid not in comps:
            raise ValueError(f"blind answer {name!r} is not part of this review "
                             f"({sorted(comps)})")
        c = out.setdefault(cid, {"reference": comps[cid]["reference"],
                                 "candidate": comps[cid]["candidate"],
                                 "prefer_candidate_n": 0, "prefer_reference_n": 0, "tie": 0})
        if choice == "=":
            c["tie"] += 1
        elif choice == nereus_side(cid, stem):
            c["prefer_candidate_n"] += 1
        else:
            c["prefer_reference_n"] += 1
    for c in out.values():
        n = c["prefer_candidate_n"] + c["prefer_reference_n"]
        c["prefer_candidate"] = round(c["prefer_candidate_n"] / n, 3) if n else None
        c["rule_pass"] = n > 0 and c["prefer_candidate_n"] / n >= 0.70
    return out
