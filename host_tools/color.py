"""Host tool: Phase 8 color-correction stages on the Mac — SPEC §4 Phase 8, §20.

A thin shell over ``nereus_camera_test_rig.color.stages``: it registers the Mac-only TG-7
ORF reader (rawpy) and dispatches to the stage functions, which hold all the logic.

Usage::

    python -m host_tools.color inspect <file.orf|file.dng> [--out results/color]
    python -m host_tools.color ingest <dataset_dir> --config configs/datasets/<dataset>.yaml
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
from nereus_camera_test_rig.color.ingest import ingest  # noqa: E402

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
    args = parser.parse_args(argv)

    try:
        if args.stage == "inspect":
            summary = stages.inspect(args.file, args.out)
        else:
            summary = ingest(args.dataset_dir, args.config, args.out, read_exif)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"{args.stage} failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
