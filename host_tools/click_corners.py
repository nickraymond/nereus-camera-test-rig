"""Manual card corners — ``locate`` method (c) (SPEC §4 Phase 8 S1, brief §7 P1.1 step 2c).

Shows each still-unlocated frame from the RAW (as-shot WB, exposure stretch, 2×2 binned —
RAW first, SPEC §20) and records four clicked **tag centres** in RAW-mosaic coordinates to the
dataset's versioned manual-corners file (``manual_corners`` in the dataset config — commit it:
it is an hour of human work). This tool only adds or replaces the entry for the frame just
accepted (atomic write), and ``locate`` only reads it.
Re-run ``locate`` afterwards to fold the clicks in.

Controls — click the tag centres in order **TL, TR, BR, BL** (tag IDs 0, 1, 3, 2):
    Enter  accept the four clicks (rejected if the quad is implausible — check the order)
    u      undo the last click
    n      card not usable in this frame (records a skip with a reason)
    s      skip for now (nothing recorded)
    q      save and quit
Zoom/pan with the matplotlib toolbar; clicks are ignored while a toolbar tool is active.

Mac-only: matplotlib is in the ``[tg7]`` extra and is never imported from ``src/``.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from nereus_camera_test_rig.color.locate import RATIO_RANGE, plausible
from nereus_camera_test_rig.color.raw_io import RawFrame, bin2x2, normalize

ORDER = ("TL", "TR", "BR", "BL")


def display_image(frame: RawFrame) -> tuple[np.ndarray, tuple[int, int]]:
    """Binned RAW for display (RGB 0–1) + the active area's origin in mosaic pixels."""
    linear, _, cfa = normalize(frame)
    rgb, _ = bin2x2(linear, cfa)
    rgb = rgb * np.asarray(frame.as_shot_wb or (1.0, 1.0, 1.0), dtype=np.float32)
    rgb = np.clip(rgb / max(float(np.percentile(rgb, 99.5)), 1e-6), 0, 1) ** (1 / 2.2)
    return rgb, tuple((frame.valid_crop or (0, 0))[:2])


def to_raw(x: float, y: float, origin: tuple[int, int]) -> list[float]:
    """Binned display pixel → RAW-mosaic pixel (binned (j, i) is centred at (2j+0.5, 2i+0.5))."""
    return [round(2 * x + 0.5 + origin[0], 2), round(2 * y + 0.5 + origin[1], 2)]


def to_display(raw_xy, origin) -> tuple[float, float]:
    return ((raw_xy[0] - 0.5 - origin[0]) / 2, (raw_xy[1] - 0.5 - origin[1]) / 2)


def save_entry(manual_path: Path, stem: str, entry: dict[str, Any]) -> None:
    """Add/replace one frame's entry; every other entry is preserved (atomic replace)."""
    data = json.loads(manual_path.read_text()) if manual_path.is_file() else {}
    data[stem] = entry
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=manual_path.parent, prefix=".manual_", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1)
        f.write("\n")
    os.replace(tmp, manual_path)


class ClickSession:
    """GUI-free state for one frame: clicks in, a manual_corners entry out."""

    def __init__(self, origin: tuple[int, int], ratio_range=RATIO_RANGE):
        self.origin, self.ratio_range = origin, tuple(ratio_range)
        self.points: list[tuple[float, float]] = []  # display coords

    def click(self, x: float, y: float) -> bool:
        if len(self.points) >= 4:
            return False
        self.points.append((x, y))
        return True

    def undo(self) -> None:
        if self.points:
            self.points.pop()

    def accept(self) -> Optional[dict[str, Any]]:
        """The entry for four plausible clicks, else None (wrong count or order)."""
        if len(self.points) != 4:
            return None
        quad = [to_raw(x, y, self.origin) for x, y in self.points]
        if not plausible(np.asarray(quad, dtype=np.float64), self.ratio_range):
            return None
        return {"quad_raw": quad, "quad_type": "tag_centers", "display": "raw_binned_wb",
                "clicked_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    @staticmethod
    def skip(reason: str) -> dict[str, Any]:
        return {"skip": True, "reason": reason,
                "clicked_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def todo(locate_dir: Path, manual_path: Path, redo: bool = False) -> list[str]:
    """Unlocated stems (in locate's order) not yet in the manual-corners file."""
    corners = json.loads((locate_dir / "corners.json").read_text())
    done = set(json.loads(manual_path.read_text())) if manual_path.is_file() else set()
    return [s for s, r in corners.items()
            if not r["located"] and (redo or s not in done)]


def run(locate_dir: Path, manual_path: Path, dataset_dir: Path,
        raw_reader: Callable[[Path], RawFrame], rows: dict[str, dict],
        redo: bool = False) -> int:  # pragma: no cover - interactive
    import matplotlib.pyplot as plt

    # Free the keys this tool uses from matplotlib's defaults (s = save dialog, q = close).
    for key in ("keymap.save", "keymap.quit"):
        plt.rcParams[key] = []
    corners = json.loads((locate_dir / "corners.json").read_text())
    params = json.loads((locate_dir / "stage.json").read_text()).get("params", {})
    ratio_range = params.get("ratio_range", RATIO_RANGE)  # the card's, as locate used it
    stems = todo(locate_dir, manual_path, redo)
    print(f"{len(stems)} frames to click ({manual_path})")
    fig, ax = plt.subplots(figsize=(14, 10))
    state: dict[str, Any] = {"i": 0, "marks": []}

    def show():
        ax.clear()
        stem = stems[state["i"]]
        row = rows[stem]
        img, origin = display_image(raw_reader(dataset_dir / row["file"]))
        state["session"] = ClickSession(origin, ratio_range)
        state["marks"] = []
        ax.imshow(img)
        rec = corners[stem]
        for tid, xy in rec.get("tag_centers_raw", {}).items():  # partial detections
            dx, dy = to_display(xy, origin)
            ax.plot(dx, dy, "c+", ms=18, mew=2)
            ax.annotate(f"tag {tid}", (dx, dy), color="c", xytext=(6, 6),
                        textcoords="offset points")
        n = rec.get("window_tried", {}).get("neighbour")
        if n and corners.get(n, {}).get("quad_raw"):
            q = np.array([to_display(p, origin) for p in corners[n]["quad_raw"]])
            ax.plot(*np.vstack([q, q[:1]]).T, "y--", lw=1)  # where the neighbour's card was
        ax.set_title(f"[{state['i'] + 1}/{len(stems)}] {stem} · {row['category']} · "
                     f"{row['depth_m']} m · {rec.get('reason', '')}\n"
                     "click tag centres TL, TR, BR, BL · Enter accept · u undo · "
                     "n not usable · s skip · q quit", fontsize=10)
        ax.set_axis_off()
        fig.canvas.draw_idle()

    def advance():
        state["i"] += 1
        if state["i"] >= len(stems):
            plt.close(fig)
        else:
            show()

    def redraw_points():
        for artist in state["marks"]:
            artist.remove()
        pts = state["session"].points
        state["marks"] = ax.plot([p[0] for p in pts], [p[1] for p in pts], "o",
                                 color="#eb6834", ms=6)
        state["marks"] += [ax.annotate(label, (x, y), color="#eb6834", xytext=(6, -12),
                                       textcoords="offset points")
                           for label, (x, y) in zip(ORDER, pts)]
        fig.canvas.draw_idle()

    def on_click(event):
        toolbar = getattr(fig.canvas, "toolbar", None)
        if event.inaxes != ax or event.button != 1 or (toolbar and toolbar.mode):
            return
        if state["session"].click(event.xdata, event.ydata):
            redraw_points()

    def on_key(event):
        stem = stems[state["i"]]
        if event.key == "enter":
            entry = state["session"].accept()
            if entry is None:
                print(f"{stem}: need 4 clicks in TL, TR, BR, BL order (u to undo)")
                return
            save_entry(manual_path, stem, entry)
            print(f"{stem}: saved")
            advance()
        elif event.key == "u":
            state["session"].undo()
            redraw_points()
        elif event.key == "n":
            save_entry(manual_path, stem, ClickSession.skip("card not usable in this frame"))
            print(f"{stem}: marked not usable")
            advance()
        elif event.key == "s":
            advance()
        elif event.key == "q":
            plt.close(fig)

    fig.canvas.mpl_connect("button_press_event", on_click)
    fig.canvas.mpl_connect("key_press_event", on_key)
    if stems:
        show()
        plt.show()
    remaining = len(todo(locate_dir, manual_path))
    print(f"done for now — {remaining} frames still without a manual entry. "
          "Re-run `locate` to fold the clicks in.")
    return 0
