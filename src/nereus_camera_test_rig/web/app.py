"""Local result-review web app — Spec §14 (Phase 6).

Four views, all server-rendered: Rig Status, New Experiment (calls the Phase 5
coordinator), Experiment Results (side-by-side three-camera comparison), and
Downloads (per-file + full-folder ZIP + cut-sheet export).

Design notes:
- Read path goes straight to the §13 result folders via ``results_reader`` —
  no new data format, no database.
- "New Experiment" calls ``capture.coordinator.run_experiment`` synchronously;
  a capture set takes seconds and this is a one-operator bench tool (§14: no
  auth, trusted dev network).
- Binds to ``web.host``/``web.port`` from the rig config (default 0.0.0.0:8080)
  so the app is reachable over Tailscale, not just localhost.
"""

from __future__ import annotations

import io
import logging
import shutil
import zipfile
from pathlib import Path
from typing import Any, Optional

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from .. import config as config_mod
from ..controller import build_camera
from . import results_reader
from .color_check import check_card_crop

logger = logging.getLogger("nereus.web")

CAMERA_LABELS = {"imx708": "IMX708", "openmv_n6": "OpenMV N6", "openmv_ae3": "OpenMV AE3"}


def camera_label(name: str) -> str:
    return CAMERA_LABELS.get(name, name)


def _fmt_bytes(n: Optional[float]) -> str:
    if n is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n / 1:.2f} {unit}".replace(".00", "")
        n /= 1024
    return "—"


def _health_checks(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Best-effort health check per enabled camera; a probe failure never 500s."""
    checks = []
    for name, cam_cfg in config_mod.enabled_cameras(config).items():
        entry: dict[str, Any] = {"name": name, "label": camera_label(name), "config": cam_cfg}
        device = None
        try:
            device = build_camera(cam_cfg)
            entry["health"] = device.health_check()
        except Exception as exc:  # noqa: BLE001 — status page must render regardless
            entry["health"] = {"healthy": False, "error": str(exc)}
        finally:
            close = getattr(device, "close", None)
            if callable(close):
                close()
        checks.append(entry)
    return checks


# Comparison metrics: (label, getter, prefer) where prefer is "max" | "min" | None.
# Getters read the dicts loaded from capture.json / detection.json (§13).
def _metric(det_key: str):
    return lambda cam, cc: (cam.detection or {}).get(det_key)


def _capture_metric(key: str):
    return lambda cam, cc: (cam.capture or {}).get(key)


def _min_tag_px(cam, cc):
    tags = (cam.detection or {}).get("tags") or {}
    sides = [t.get("side_px_min") for t in tags.values() if t.get("side_px_min")]
    return round(min(sides), 1) if sides else None


def _image_metric(key: str):
    def get(cam, cc):
        metrics = (cam.detection or {}).get("metrics") or {}
        value = metrics.get(key)
        return round(value, 1) if isinstance(value, (int, float)) else None

    return get


def _clip_pct(cam, cc):
    metrics = (cam.detection or {}).get("metrics") or {}
    dark, bright = metrics.get("clipped_dark_frac"), metrics.get("clipped_bright_frac")
    if dark is None or bright is None:
        return None
    return f"{dark * 100:.1f} / {bright * 100:.1f}"


COMPARISON_ROWS = [
    ("Tags detected", lambda c, cc: len((c.detection or {}).get("tags_detected") or []), "max"),
    ("Min tag size (px)", _min_tag_px, "max"),
    ("Sharpness (Laplacian var)", _image_metric("sharpness_laplacian_var"), "max"),
    ("Contrast (p95 − p05)", _image_metric("contrast_p95_p05"), "max"),
    ("Clipped dark / bright (%)", _clip_pct, None),
    ("Mean luma", _image_metric("mean_luma"), None),
    ("Grey ramp mean ΔE", lambda c, cc: cc.grey_mean_delta_e if cc else None, "min"),
    ("Color patches mean ΔE", lambda c, cc: cc.color_mean_delta_e if cc else None, "min"),
    ("Grey cast (R − B)", lambda c, cc: cc.grey_cast_r_minus_b if cc else None, None),
    ("Analysis time (ms)", lambda c, cc: _round_opt(_metric("processing_time_ms")(c, cc)), "min"),
    ("Capture duration (s)",
     lambda c, cc: _round_opt(_capture_metric("duration_seconds")(c, cc), 2), "min"),
    ("File size", lambda c, cc: c.output.get("size_bytes"), "min"),
    ("Resolution", lambda c, cc: _resolution(c), None),
]


def _round_opt(value, digits: int = 0):
    if not isinstance(value, (int, float)):
        return None
    return round(value, digits) if digits else round(value)


def _resolution(cam) -> Optional[str]:
    out = cam.output
    if out.get("width") and out.get("height"):
        return f"{out['width']} × {out['height']}"
    return None


def build_comparison(cameras, color_checks) -> list[dict[str, Any]]:
    """Aligned metric rows across cameras, marking the best value per row."""
    rows = []
    for label, getter, prefer in COMPARISON_ROWS:
        values = [getter(cam, color_checks.get(cam.name)) for cam in cameras]
        best_idx: set[int] = set()
        numeric = [(i, v) for i, v in enumerate(values) if isinstance(v, (int, float))]
        # Highlight only when there's a real difference — an all-tied row (e.g.
        # every camera sees 4/4 tags) carries no decision value.
        if prefer and len(numeric) > 1 and len({v for _, v in numeric}) > 1:
            target = max(v for _, v in numeric) if prefer == "max" else min(v for _, v in numeric)
            best_idx = {i for i, v in numeric if v == target}
        display = [
            _fmt_bytes(v) if label == "File size" and isinstance(v, (int, float)) else v
            for v in values
        ]
        rows.append({"label": label, "values": display, "best": best_idx})
    return rows


def create_app(config: dict[str, Any]) -> Flask:
    app = Flask(__name__)
    app.secret_key = "nereus-local-rig"  # local tool, no auth (§14); enables flash()
    results_root = Path(config.get("rig", {}).get("results_directory", "./results"))

    @app.template_filter("fmt_bytes")
    def fmt_bytes_filter(n):
        return _fmt_bytes(n)

    @app.context_processor
    def inject_globals():
        rig_id = config.get("rig", {}).get("id", "nereus-rig")
        return {"rig_id": rig_id, "camera_label": camera_label}

    def _experiment_or_404(date: str, exp_id: str) -> results_reader.ExperimentView:
        view = results_reader.load_experiment(results_root, date, exp_id)
        if view is None:
            abort(404, f"experiment not found: {date}/{exp_id}")
        return view

    @app.get("/")
    def rig_status():
        experiments = results_reader.list_experiments(results_root)
        disk = shutil.disk_usage(results_root if results_root.is_dir() else Path.cwd())
        return render_template(
            "status.html",
            checks=_health_checks(config),
            experiments_count=len(experiments),
            last_experiment=experiments[0] if experiments else None,
            disk_free=_fmt_bytes(disk.free),
        )

    @app.get("/experiments")
    def experiments_index():
        return render_template(
            "experiments.html", experiments=results_reader.list_experiments(results_root)
        )

    @app.route("/experiments/new", methods=["GET", "POST"])
    def new_experiment():
        camera_names = list(config_mod.enabled_cameras(config))
        if request.method == "POST":
            selected = request.form.getlist("cameras") or None
            exp_type = (request.form.get("experiment_type") or "reference_card").strip()
            # Import lazily: viewing results must not require capture deps.
            from ..capture.coordinator import run_experiment

            try:
                outcome = run_experiment(
                    config,
                    exp_type,
                    environment_label=(request.form.get("environment") or "").strip(),
                    operator_notes=(request.form.get("notes") or "").strip(),
                    camera_names=selected,
                    results_root=results_root,
                    analysis=request.form.get("analysis") == "on",
                )
            except Exception as exc:  # noqa: BLE001 — surface, don't 500 (§17)
                logger.exception("experiment run failed")
                flash(f"Experiment failed to run: {exc}", "error")
                return render_template("new_experiment.html", camera_names=camera_names), 500
            flash(f"Experiment finished: {outcome.status}", "ok")
            return redirect(
                url_for(
                    "experiment_detail",
                    date=outcome.paths.root.parent.name,
                    exp_id=outcome.paths.experiment_id,
                )
            )
        return render_template("new_experiment.html", camera_names=camera_names)

    @app.get("/experiments/<date>/<exp_id>")
    def experiment_detail(date: str, exp_id: str):
        view = _experiment_or_404(date, exp_id)
        color_checks = {
            cam.name: (check_card_crop(cam.crop_abs) if cam.crop_abs else None)
            for cam in view.cameras
        }
        return render_template(
            "experiment_detail.html",
            exp=view,
            color_checks=color_checks,
            comparison=build_comparison(view.cameras, color_checks),
            files=[
                {"rel": str(p.relative_to(view.root)), "size": p.stat().st_size}
                for p in results_reader.iter_experiment_files(view.root)
            ],
        )

    @app.get("/experiments/<date>/<exp_id>/file/<path:relpath>")
    def experiment_file(date: str, exp_id: str, relpath: str):
        view = _experiment_or_404(date, exp_id)
        target = (view.root / relpath).resolve()
        if not target.is_relative_to(view.root.resolve()) or not target.is_file():
            abort(404)
        return send_file(target, as_attachment=request.args.get("dl") == "1")

    @app.get("/experiments/<date>/<exp_id>/download.zip")
    def experiment_zip(date: str, exp_id: str):
        view = _experiment_or_404(date, exp_id)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in results_reader.iter_experiment_files(view.root):
                zf.write(path, arcname=f"{exp_id}/{path.relative_to(view.root)}")
        buf.seek(0)
        return send_file(
            buf, mimetype="application/zip", as_attachment=True, download_name=f"{exp_id}.zip"
        )

    @app.get("/experiments/<date>/<exp_id>/cutsheet.<fmt>")
    def experiment_cutsheet(date: str, exp_id: str, fmt: str):
        if fmt not in ("png", "pdf"):
            abort(404)
        view = _experiment_or_404(date, exp_id)
        from .cutsheet import render_cutsheet  # lazy: needs Pillow

        color_checks = {
            cam.name: (check_card_crop(cam.crop_abs) if cam.crop_abs else None)
            for cam in view.cameras
        }
        payload = render_cutsheet(
            view, build_comparison(view.cameras, color_checks), fmt=fmt
        )
        return send_file(
            io.BytesIO(payload),
            mimetype="image/png" if fmt == "png" else "application/pdf",
            as_attachment=True,
            download_name=f"{exp_id}_cutsheet.{fmt}",
        )

    return app


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="nereus-rig-web", description=__doc__)
    parser.add_argument(
        "--config", default="configs/rig.example.yaml", help="path to rig config YAML"
    )
    parser.add_argument("--host", default=None, help="override web.host from config")
    parser.add_argument("--port", type=int, default=None, help="override web.port from config")
    args = parser.parse_args(argv)

    config = config_mod.load_rig_config(args.config)
    web_cfg = config.get("web") or {}
    host = args.host or web_cfg.get("host", "0.0.0.0")
    port = args.port or int(web_cfg.get("port", 8080))

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app = create_app(config)
    logger.info("serving on http://%s:%s (rig %s)", host, port, config.get("rig", {}).get("id"))
    app.run(host=host, port=port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
