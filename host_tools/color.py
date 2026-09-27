"""Host tool: Phase 8 color-correction stages on the Mac — SPEC §4 Phase 8, §20.

A thin shell over ``nereus_camera_test_rig.color.stages``: it registers the Mac-only TG-7
ORF reader (rawpy) and dispatches to the stage functions, which hold all the logic.

Usage::

    python -m host_tools.color inspect <file.orf|file.dng> [--out results/color]
    python -m host_tools.color ingest <dataset_dir> --config configs/datasets/<dataset>.yaml
    python -m host_tools.color locate results/color/<dataset_id>/ingest --config <dataset.yaml>
    python -m host_tools.color click results/color/<dataset_id>/locate --config <dataset.yaml>
    python -m host_tools.color distance results/color/<dataset_id>/locate --config <dataset.yaml>
    python -m host_tools.color patches results/color/<dataset_id>/locate
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# Run this checkout's own src/, not whatever the venv's editable install points at (in a git
# worktree that is the primary checkout) — same reason as pytest's `pythonpath`.
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from nereus_camera_test_rig.color import stages  # noqa: E402
from nereus_camera_test_rig.color.distance import distance  # noqa: E402
from nereus_camera_test_rig.color.ingest import ingest  # noqa: E402
from nereus_camera_test_rig.color.locate import locate  # noqa: E402
from nereus_camera_test_rig.color.patches import patches  # noqa: E402
from nereus_camera_test_rig.config import load_yaml  # noqa: E402

from .tg7.exif import read_exif  # noqa: E402
from .tg7.orf_io import read_orf  # noqa: E402


def main(argv=None) -> int:
    stages.register_reader(".orf", read_orf)
    parser = argparse.ArgumentParser(description="Phase 8 color-correction stages.")
    sub = parser.add_subparsers(dest="stage", required=True)
    p = sub.add_parser("inspect", help="metadata + linear stats + preview of one RAW file")
    p.add_argument("file", type=Path)
    p.add_argument("--out", type=Path, default=REPO / "results" / "color")
    p = sub.add_parser("ingest", help="build the tool manifest for a dataset folder")
    p.add_argument("dataset_dir", type=Path)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--out", type=Path, default=REPO / "results" / "color")
    p = sub.add_parser("locate", help="find the card (tag-centre quad) in every card frame")
    p.add_argument("ingest_dir", type=Path)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--card", type=Path, default=REPO / "configs" / "cards" / "nereus_v2.yaml")
    p = sub.add_parser("distance", help="camera-to-card distance z per located frame (PnP)")
    p.add_argument("locate_dir", type=Path)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--calibration", type=Path,
                   help="default: configs/calibration/<dataset camera>.yaml")
    p.add_argument("--card", type=Path, default=REPO / "configs" / "cards" / "nereus_v2.yaml")
    p = sub.add_parser("patches", help="sample every card patch (binned RAW + camera JPEG)")
    p.add_argument("locate_dir", type=Path)
    p.add_argument("--card", type=Path, default=REPO / "configs" / "cards" / "nereus_v2.yaml")
    p = sub.add_parser("click", help="click tag centres on frames locate could not find")
    p.add_argument("locate_dir", type=Path)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--redo", action="store_true", help="also revisit frames already clicked")
    p.add_argument("--list", action="store_true", help="only list the frames still to do")
    args = parser.parse_args(argv)

    if args.stage == "click":
        return _click(args)

    try:
        if args.stage == "inspect":
            summary = stages.inspect(args.file, args.out)
        elif args.stage == "ingest":
            summary = ingest(args.dataset_dir, args.config, args.out, read_exif)
        elif args.stage == "distance":
            calibration = args.calibration or (REPO / "configs" / "calibration" /
                                               f"{load_yaml(args.config).get('camera', '')}.yaml")
            summary = distance(args.locate_dir, calibration, args.config, args.card)
        elif args.stage == "patches":
            summary = patches(args.locate_dir, args.card, raw_reader=read_orf)
        else:
            summary = locate(args.ingest_dir, args.card, args.config, raw_reader=read_orf)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"{args.stage} failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, default=str))
    return 0


def _click(args) -> int:
    import csv

    from nereus_camera_test_rig.color.locate import manual_corners_path

    from . import click_corners

    manual = manual_corners_path(args.config, load_yaml(args.config), args.locate_dir)
    stems = click_corners.todo(args.locate_dir, manual, args.redo)
    if args.list:
        print("\n".join(stems) or "nothing to click")
        print(f"{len(stems)} frames · manual corners file: {manual}")
        return 0
    ingest_dir = args.locate_dir.parent / "ingest"
    record = stages.verify_fresh(ingest_dir)
    rows = {r["stem"]: r for r in csv.DictReader((ingest_dir / "manifest.csv").open())}
    return click_corners.run(args.locate_dir, manual, Path(record["params"]["dataset_dir"]),
                             read_orf, rows, args.redo)


if __name__ == "__main__":
    sys.exit(main())
