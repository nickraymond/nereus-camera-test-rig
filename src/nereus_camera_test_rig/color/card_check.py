"""Per-set card brightness check on an experiment folder's RAWs — pool spec §5.4.

    python -m nereus_camera_test_rig.color.card_check <experiment folder> [--card V1.yaml]

For each camera's RAW in ``captures/<camera>/`` (IMX708 ``.dng``, OpenMV ``.bayer`` + sidecar)
locate the card and report its brightest white-patch channel as a fraction of full scale and
whether it clipped (``color.raw_meter``: the same rule the exposure lock aims at). Prints one
JSON object; read-only on the folder (raw evidence is never edited). Large frames are decimated
first, so the IMX708 DNG stays inside the Pi Zero's memory (~150 MB peak).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

DEFAULT_CARD = Path(__file__).resolve().parents[3] / "configs" / "cards" / "nereus_v1.yaml"


def check_folder(exp_dir: Path, card_yaml: Path = DEFAULT_CARD) -> dict[str, Any]:
    from .card import load_card
    from .raw_io import read_dng, read_openmv_bayer
    from .raw_meter import card_levels, card_reference

    card = load_card(card_yaml)
    out: dict[str, Any] = {}
    for cam_dir in sorted(p for p in (exp_dir / "captures").iterdir() if p.is_dir()):
        raws = sorted(cam_dir.glob("*.dng")) + sorted(cam_dir.glob("*.bayer"))
        if not raws:
            continue
        raw = raws[-1]
        t0 = time.monotonic()
        try:
            frame = read_dng(raw) if raw.suffix == ".dng" else read_openmv_bayer(raw)
            levels = card_levels(frame, card)
            ref = card_reference(levels)
            out[cam_dir.name] = {"ok": True, "file": raw.name, "tags": levels["tags_found"],
                                 "patch": ref["patch"], "channel": ref["channel"],
                                 "level": round(float(ref["level"]), 4),
                                 "clipped": ref["clipped"]}
        except (ValueError, OSError, KeyError) as exc:
            out[cam_dir.name] = {"ok": False, "file": raw.name,
                                 "error": f"{type(exc).__name__}: {exc}"}
        out[cam_dir.name]["seconds"] = round(time.monotonic() - t0, 1)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("experiment")
    ap.add_argument("--card", type=Path, default=DEFAULT_CARD)
    a = ap.parse_args(argv)
    print(json.dumps(check_folder(Path(a.experiment), a.card)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
