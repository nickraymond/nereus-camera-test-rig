"""Command-line entry point — Spec §9, §15.

Phase 0 skeleton: the argument surface exists and is importable/testable, but the
capture/experiment subcommands are not yet wired to hardware. They exit with a
clear "not implemented" message (CLAUDE.md §17) so the CLI never appears to succeed
while doing nothing. Fleshed out starting in Phase 1.
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

    p_experiment = sub.add_parser("experiment", help="run an experiment profile (Phase 5+)")
    p_experiment.add_argument("--profile", required=True, help="path to an experiment profile YAML")
    p_experiment.set_defaults(func=_cmd_not_implemented, _feature="experiment (Phase 5)")

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
        print(f"[nereus-rig] captured {outcome.output_path}")
        print(f"             {r.width}x{r.height} {r.image_format} {r.size_bytes} bytes")
        print(f"             sha256={r.sha256}")
        print(f"             metadata={outcome.metadata_path}")
        if r.sensor_metadata:
            print(f"             sensor={r.sensor_metadata}")
        return 0
    err = r.error or {}
    print(f"[nereus-rig] capture FAILED: {err.get('code')}: {err.get('message')}", file=sys.stderr)
    return 1


def _cmd_not_implemented(args: argparse.Namespace) -> int:
    feature = getattr(args, "_feature", "this command")
    print(f"[nereus-rig] {feature} is not implemented yet (Phase 0 scaffold).", file=sys.stderr)
    return 2


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
