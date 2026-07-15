"""Command-line entry point — Spec §9, §15.

Subcommands: ``info`` (config summary), ``capture`` (single still/video from one
camera, Phase 1), and ``experiment`` (sequential multi-camera capture set into one
Spec §13 experiment folder, Phase 5). Each returns a nonzero exit only on real
failure (CLAUDE.md §17) — never a false success.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nereus-rig",
        description="Nereus multi-camera test rig controller (evaluation platform).",
    )
    parser.add_argument(
        "--config", default="configs/rig.example.yaml", help="path to rig config YAML"
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_info = sub.add_parser("info", help="show rig version and loaded config summary")
    p_info.set_defaults(func=_cmd_info)

    p_capture = sub.add_parser("capture", help="capture a still/video from a camera")
    p_capture.add_argument("--camera", default="imx708", help="camera name from config")
    p_capture.add_argument("--kind", choices=("image", "video"), default="image")
    p_capture.add_argument("--out", default="results/adhoc", help="output directory")
    p_capture.set_defaults(func=_cmd_capture)

    p_experiment = sub.add_parser(
        "experiment", help="run a multi-camera capture set into one experiment folder"
    )
    p_experiment.add_argument(
        "--type", default="reference_card", help="experiment type/slug (folder name)"
    )
    p_experiment.add_argument("--env", default="", help="environment label, e.g. bench")
    p_experiment.add_argument("--notes", default="", help="operator notes for experiment.json")
    p_experiment.add_argument(
        "--cameras", default=None, help="comma-separated camera subset (default: all enabled)"
    )
    p_experiment.add_argument(
        "--no-analysis", action="store_true", help="skip the reference-card analysis pass"
    )
    p_experiment.set_defaults(func=_cmd_experiment)

    return parser


def _cmd_info(args: argparse.Namespace) -> int:
    from . import __version__

    print(f"nereus-camera-test-rig {__version__}")
    print(f"config: {args.config}")
    return 0


def _cmd_capture(args: argparse.Namespace) -> int:
    from . import config as config_mod
    from .controller import capture_once

    try:
        cfg = config_mod.load_rig_config(args.config)
    except config_mod.ConfigError as exc:
        print(f"[nereus-rig] config error: {exc}", file=sys.stderr)
        return 2

    try:
        outcome = capture_once(cfg, args.camera, args.kind, args.out)
    except KeyError as exc:
        print(f"[nereus-rig] {exc}", file=sys.stderr)
        return 2

    r = outcome.result
    if r.ok:
        dims = f"{r.width}x{r.height} " if r.width and r.height else ""
        print(f"[nereus-rig] captured {outcome.output_path}")
        print(f"             {dims}{r.image_format} {r.size_bytes} bytes")
        print(f"             sha256={r.sha256}")
        print(f"             metadata={outcome.metadata_path}")
        if r.sensor_metadata:
            print(f"             sensor={r.sensor_metadata}")
        return 0
    err = r.error or {}
    print(f"[nereus-rig] capture FAILED: {err.get('code')}: {err.get('message')}", file=sys.stderr)
    return 1


def _cmd_experiment(args: argparse.Namespace) -> int:
    from . import config as config_mod
    from .capture.coordinator import run_experiment

    try:
        cfg = config_mod.load_rig_config(args.config)
    except config_mod.ConfigError as exc:
        print(f"[nereus-rig] config error: {exc}", file=sys.stderr)
        return 2

    subset = [c.strip() for c in args.cameras.split(",") if c.strip()] if args.cameras else None
    outcome = run_experiment(
        cfg,
        args.type,
        environment_label=args.env,
        operator_notes=args.notes,
        camera_names=subset,
        analysis=not args.no_analysis,
    )

    print(f"[nereus-rig] experiment {outcome.record.experiment_id}")
    print(f"             folder: {outcome.paths.root}")
    for c in outcome.camera_outcomes:
        if c.ok:
            r = c.result
            dims = f"{r.width}x{r.height} " if r.width and r.height else ""
            note = ""
            if c.analysis is not None:
                note = f" · analysis={c.analysis.status} tags={c.analysis.tags_detected}"
            print(f"  [ok]   {c.camera_name}: {dims}{r.size_bytes} bytes{note}")
        else:
            err = c.result.error or {}
            print(f"  [FAIL] {c.camera_name}: {err.get('code')}: {err.get('message')}")
    print(f"[nereus-rig] status: {outcome.status}")

    # Exit 0 only when every included camera captured. Analysis finding no card does
    # NOT fail the run (expected during mechanical bring-up).
    return 0 if outcome.status == "completed" else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
