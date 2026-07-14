# Implementation Plan

**Phase 0 deliverable (Spec §4).** Names the exact modules to **port**, **adapt**, or
**rewrite/new**, mapped onto the target repo structure (Spec §6) and the Build Plan phases
(Spec §4). Derived from [`prior_art_review.md`](prior_art_review.md). Open unknowns live in
[`open_questions.md`](open_questions.md).

Legend:
- **Port** — lift with minimal change (strip paths/telemetry only).
- **Adapt** — reuse the logic/structure, refactor into our interface, strip coupling.
- **New** — no prior art; write from scratch (verify APIs first).

---

## Module map (target `src/nereus_camera_test_rig/` unless noted)

| Target module | Source (prior art) | Action | Phase |
|---|---|---|---|
| `cameras/base.py` — `CameraDevice` interface (§5) | handler contract concept from `bm_rpi_camera_module/pluginspec/handler.py` | **New** (contract), informed by prior art | 0 |
| `cameras/registry.py` — driver registry, no scattered `if platform==` | (pattern from `plugin_loader.py`) | **New** | 0 |
| `config.py` — YAML load + validation | `bm_daemon/common/config.py`, `main_pi_camera.py:_build_image_pipeline_settings` | **Adapt** | 0 |
| `models.py` — shared dataclasses (experiment record, capture request/result, detection result) | result field set from `bm_rpi_camera_module` status | **New**, field names reused | 0 |
| `logging_config.py` | `bm_daemon/common/logging_config.py` | **Port/Adapt** | 0 |
| `cli.py` — capture/experiment commands | argparse in `main_pi_camera.py` (L578+) | **Adapt** (template only) | 0→1 |
| `cameras/imx708.py` — `Imx708Camera` | `process_image_v2.py` (`_select_camera_command`, `_run_native_full_capture`, `_camera_controls_from_settings`, `_focus_...`, `_load_libcamera_metadata_json`) | **Adapt** (strip BM telemetry + `/home/pi` paths) | 1 |
| `capture/image_capture.py` (crop/downsample) | `crop_downsample_helper.py` | **Port** | 1 |
| `capture/video_capture.py` | `process_image_v2.py` video + `bm_rpi_camera_module/video_capture.py` (structure) | **Adapt** | 1 |
| `analysis/apriltag_detector.py` | `bm_reference_card_quality_v2.py:detect_tags` | **Adapt** | 2 |
| `analysis/reference_card.py` | `infer_card_corners_from_tags`, `expand_quad`, `rectify_quad`, `parse_corner_map` | **Adapt** (re-parameterize card geometry) | 2 |
| `analysis/crop.py` | `rectify_quad` + bbox crop path | **Adapt** | 2 |
| `analysis/image_metrics.py` | `variance_laplacian`, `tenengrad`, `contrast_p95_p05`, `compute_card_metrics`, `compare_to_reference` | **Port/Adapt** | 2 |
| `analysis/result_writer.py` — detection.json (§13 schema) | (pass-rule logic in `quality_status`/`sprint_status`) | **Adapt** | 2 |
| `storage/experiment_store.py` — run-folder layout (§13) | folder conventions from `bm_cam_legacy` (concept) | **New** | 0→5 |
| `storage/metadata.py` — metadata writer (§5) | `save/load/update_capture_metadata` | **Adapt** | 1 |
| `storage/checksums.py` — SHA-256 | (none) | **New** | 0→3 |
| `capture/naming.py` — experiment/file naming | timestamp helpers (standardize the two formats) | **New** | 0 |
| `capture/coordinator.py` — sequential 3-camera capture (§11) | (none; orchestration concept only) | **New** | 5 |
| `cameras/openmv_usb.py` — `OpenMvUsbCamera` host adapter | **none** | **New** — verify OpenMV/serial APIs first | 3 |
| `openmv/common/command_protocol.py` — newline-JSON allowlist (§8) | **none** | **New** — MicroPython | 3 |
| `openmv/common/capture_service.py`, `device_info.py` | **none** | **New** — MicroPython | 3 |
| `openmv/n6/*`, `openmv/ae3/*` — board apps | **none** | **New** — board-isolated, no shared `if board==` | 3,4 |
| `host_tools/{discover_openmv,deploy_openmv}.py` | **none** | **New** | 3 |
| `host_tools/{verify_rig,run_experiment,collect_results,compare_cameras,generate_report}.py` | **none** | **New** | 0→7 |
| `web/app.py` + templates (§14) | **none** | **New** | 6 |
| `utils/camera_lock` | `bm_camera/utils/camera_lock.py` (+ `bm_daemon/io/camera_lock.py`) | **Port** | 1 |

---

## Phase 0 concrete deliverables (this phase only)

Ordered; each is a small commit. Steps A–E; A (this review) is done when these two docs +
`open_questions.md` are committed.

1. **Tooling** — `pyproject.toml` (src-layout, deps from the review's dependency list, dev
   extras), `requirements-dev.txt`, `Makefile` (`install`, `test`, `lint`), `.gitignore`
   additions (`results/`, `experiments/`, venvs, generated media).
2. **Foundation skeletons (interfaces only, no hardware):**
   - `cameras/base.py` — the §5 `CameraDevice` interface (methods raise `NotImplementedError`).
   - `cameras/registry.py` — register/lookup by driver name.
   - `config.py` — load `configs/rig.example.yaml`; validate top-level keys (§12).
   - `models.py` — dataclasses: `ExperimentRecord`, `CaptureRequest`, `CaptureResult`,
     `DetectionResult` (fields per §5/§13).
   - `logging_config.py`, `capture/naming.py`.
   - Importable placeholders (docstring + `NotImplementedError`) for `capture/`, `storage/`,
     `analysis/`, `web/`, `controller.py`, `cli.py`.
3. **Config stubs** — `configs/rig.example.yaml`, `configs/cameras/{imx708,openmv_n6,openmv_ae3}.yaml`,
   `configs/experiments/{reference_card_above_water,reference_card_below_water,low_light,object_detection}.yaml`.
4. **Tests (Mac-only, no hardware):** `tests/conftest.py` + unit tests for config loading,
   model construction/round-trip, naming, and registry. Empty `tests/{integration,hardware,fixtures}/` dirs.
5. **Verify** — `make test` green on Mac; tree matches §6; zero hardware APIs implemented;
   check the Phase 0 boxes in the spec in the same PR.

**Deliberately deferred (not Phase 0):** any real camera/OpenMV/analysis/web behavior; test
fixtures with real reference cards; the `install_pi.sh` / hardware smoke scripts' bodies.

---

## Sequencing rationale

- Phase 0 builds only the contracts and Mac-testable pure logic so later phases slot in
  without churn — consistent with "abstract only genuinely shared behavior" (CLAUDE.md §7).
- The IMX708 adapter (Phase 1) is first hardware because it has the most proven prior art
  and the lowest risk — it establishes the capture→metadata→validation loop the OpenMV
  adapters will mirror.
- OpenMV work (Phases 3–4) is scheduled after the reference-card pipeline (Phase 2) so that,
  once OpenMV stills exist, they immediately flow through a *working, tested* analysis path.
- All OpenMV modules are marked **New** and gated behind `open_questions.md` verification —
  no OpenMV API will be written until confirmed against official docs or a working example.
