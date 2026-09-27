"""Manual-corner tool (SPEC §4 Phase 8 S1, locate method (c)): the GUI-free parts — coordinate
mapping, the click session, saving without losing other entries, and the to-do list."""

from __future__ import annotations

import json

import numpy as np
import pytest
from host_tools.click_corners import ClickSession, save_entry, to_display, to_raw, todo

from nereus_camera_test_rig.color.locate import MANUAL_FILE
from nereus_camera_test_rig.color.raw_io import RawFrame, bin2x2, normalize


def test_display_to_raw_mapping_matches_bin2x2_geometry():
    """A bright photosite block at mosaic (x, y) lands on binned pixel ((x-0.5)/2, (y-0.5)/2)."""
    mosaic = np.zeros((20, 30), np.uint16)
    mosaic[10:12, 16:18] = 4000  # one 2x2 cell: binned pixel (row 5, col 8)
    frame = RawFrame(mosaic, "RGGB", (0, 0, 0, 0), 4095.0, valid_crop=(2, 0, 26, 20))
    linear, _, cfa = normalize(frame)
    binned, _ = bin2x2(linear, cfa)
    i, j = np.unravel_index(np.argmax(binned[..., 1]), binned.shape[:2])
    assert to_raw(j, i, origin=(2, 0)) == [16.5, 10.5]  # centre of that 2x2 cell
    assert to_display([16.5, 10.5], (2, 0)) == (j, i)


def test_session_accepts_four_plausible_clicks_in_order():
    s = ClickSession(origin=(0, 0))
    for x, y in [(10, 10), (410, 10), (410, 110), (10, 110)]:
        assert s.click(x, y)
    assert not s.click(1, 1)  # a fifth click is ignored
    entry = s.accept()
    assert entry["quad_raw"][0] == [20.5, 20.5] and entry["quad_type"] == "tag_centers"


def test_session_rejects_wrong_order_and_supports_undo():
    s = ClickSession(origin=(0, 0))
    for x, y in [(10, 10), (410, 110), (410, 10), (10, 110)]:  # TL, BR, TR, BL: bow-tie
        s.click(x, y)
    assert s.accept() is None
    s.undo(), s.undo(), s.undo()
    for x, y in [(410, 10), (410, 110), (10, 110)]:
        s.click(x, y)
    assert s.accept() is not None
    assert ClickSession(origin=(0, 0)).accept() is None  # no clicks


def test_save_entry_keeps_every_other_entry(tmp_path):
    path = tmp_path / "locate" / MANUAL_FILE
    save_entry(path, "A", {"quad_raw": [[0, 0]] * 4})
    save_entry(path, "B", ClickSession.skip("card not usable in this frame"))
    save_entry(path, "A", {"quad_raw": [[1, 1]] * 4})  # replace A only
    data = json.loads(path.read_text())
    assert data["A"]["quad_raw"][0] == [1, 1] and data["B"]["skip"] is True
    assert list(path.parent.glob(".manual_*")) == []  # no temp files left behind


def test_todo_lists_unlocated_frames_without_an_entry(tmp_path):
    (tmp_path / "corners.json").write_text(json.dumps(
        {"A": {"located": True}, "B": {"located": False}, "C": {"located": False}}))
    manual = tmp_path / "configs" / "ds_manual_corners.json"
    assert todo(tmp_path, manual) == ["B", "C"]
    save_entry(manual, "B", {"quad_raw": [[0, 0]] * 4})
    assert todo(tmp_path, manual) == ["C"]
    assert todo(tmp_path, manual, redo=True) == ["B", "C"]


@pytest.mark.parametrize("key", ["keymap.save", "keymap.quit"])
def test_tool_keys_collide_with_matplotlib_defaults_so_run_unbinds_them(key):
    """Guards the rcParams reset in run(): 's' and 'q' are matplotlib defaults."""
    matplotlib = pytest.importorskip("matplotlib")
    assert any(k in matplotlib.rcParamsDefault[key] for k in ("s", "q", "ctrl+s", "ctrl+w"))
