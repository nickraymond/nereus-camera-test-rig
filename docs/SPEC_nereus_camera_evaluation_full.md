# Nereus Multi-Camera Evaluation Rig
## Repository and Implementation Specification

**Status:** Development MVP  
**Primary controller:** Raspberry Pi  
**Candidate cameras:** Raspberry Pi IMX708, OpenMV N6, OpenMV AE3  
**Primary purpose:** Controlled above-water and underwater camera hardware comparison

---

## 1. Project Objective

Create a new development repository for a multi-camera evaluation rig that uses a Raspberry Pi as the central controller.

The rig must capture comparable images and short video clips from:

1. Raspberry Pi Camera Module 3 / Sony IMX708
2. OpenMV N6
3. OpenMV AE3

The Raspberry Pi will:

- control all three cameras;
- capture from the local IMX708;
- send simple USB capture commands to the N6 and AE3;
- retrieve or collect OpenMV image files;
- organize captures into timestamped experiment folders;
- record configuration and test metadata;
- provide a simple browser interface for reviewing and downloading results;
- run the existing AprilTag/reference-card detection and crop pipeline against captured still images.

This project is an **evaluation platform**, not a production camera runtime.

Do not add Bristlemouth transport, Spotter time synchronization, cellular transmission, production scheduling, or remote field-device deployment in the initial implementation.

---

## 2. Evaluation Goals

The rig will support comparative testing for camera suitability in the following applications:

- fish detection and counting;
- biofouling detection on underwater sensors;
- underwater robot or vehicle detection;
- coral reef bleaching observation;
- general underwater object detection;
- small-object inference experiments, such as detecting purple balls;
- AprilTag and reference-card detection;
- color, contrast, sharpness, and low-light characterization;
- above-water versus underwater image-quality comparison.

The immediate success metric is not full biological analysis.

The immediate success metric is:

> Can each camera reliably capture an image in which the existing reference card and AprilTags can be detected, localized, and cropped?

Inference and more advanced analysis will be implemented after the hardware capture system is stable.

---

## 3. Prior Art and Source Repositories

Review these repositories before implementing new functionality.

### 3.1 Raspberry Pi Camera Reference

Repository:

```text
https://github.com/nickraymond/bm_cam_legacy
```

Treat this repository as prior art for:

- IMX708 capture commands;
- camera configuration;
- crop and resize behavior;
- JPEG and HEIC processing;
- image-quality experiments;
- device profiles;
- AprilTag/reference-card tools;
- cut-sheet generation;
- configuration-driven capture behavior;
- logging and capture locking.

The new evaluation repository should reuse useful camera and analysis logic, but should not reproduce the old production deployment structure or Bristlemouth-specific behavior.

### 3.2 Experimental Raspberry Pi Camera Daemon

Repository:

```text
https://github.com/nickraymond/bm_rpi_camera_module
```

Review for:

- modular camera handlers;
- image and video command patterns;
- configuration organization;
- plugin or adapter concepts;
- service separation;
- camera status responses.

Do not copy the BM daemon architecture into this initial test rig unless a component directly benefits local USB camera evaluation.

### 3.3 Borealis SBC Reference

Repository:

```text
https://github.com/appliedoceansciences/borealis_sbc
```

Review only for general lessons around:

- separating hardware acquisition from higher-level processing;
- clear interface boundaries;
- safe data handling;
- independently testable modules.

BM serial functionality is outside the scope of this initial repository.

---

## 4. Scope

### 4.1 Included in the MVP

The first implementation must support:

- Raspberry Pi-hosted controller application;
- local IMX708 still-image capture;
- local IMX708 short-video capture;
- USB command interface to OpenMV N6;
- USB command interface to OpenMV AE3;
- still-image capture on both OpenMV cameras;
- short-video capture where supported and practical;
- transfer or collection of OpenMV output files;
- timestamped experiment folders;
- capture metadata;
- configurable per-camera settings;
- sequential capture of all connected cameras;
- capture timing within approximately one or two seconds;
- manual capture from the command line;
- capture through a simple local web interface;
- image and video download from the Pi;
- reference-card detection;
- AprilTag detection;
- card-region cropping;
- detection result summaries;
- test logs;
- graceful operation if one camera is disconnected;
- Mac-hosted test and analysis scripts.

### 4.2 Explicitly Excluded from the MVP

Do not implement:

- Bristlemouth UART transport;
- Spotter integration;
- BM command subscriptions;
- cellular image transmission;
- production system scheduling;
- HEIC transmission protocols;
- cloud upload;
- user authentication;
- remote firmware update;
- production watchdog design;
- full fish-counting pipeline;
- coral-bleaching classification;
- production YOLO model training;
- cross-camera geometric calibration;
- precise hardware-trigger synchronization;
- automatic underwater color correction.

Create extension points for future analysis, but do not implement speculative complexity.

---

## 5. Hardware Architecture

```text
Mac development computer
        |
        | SSH / HTTP / file download
        v
Raspberry Pi controller
        |
        |-- CSI --> Raspberry Pi IMX708
        |
        |-- USB --> OpenMV N6
        |
        |-- USB --> OpenMV AE3
        |
        `-- Local storage for all experiment results
```

The OpenMV devices will be controlled over USB.

Precise simultaneous triggering is unnecessary. Sequential commands within one or two seconds are acceptable.

Each OpenMV board should run a small MicroPython application that:

1. starts at boot;
2. identifies the board and firmware version;
3. listens for newline-delimited commands over USB;
4. validates commands;
5. captures an image or video;
6. saves the output locally or returns it through a supported transfer method;
7. responds with a structured result;
8. remains ready for the next command.

---

## 6. Repository Name

Use:

```text
nereus-camera-evaluation
```

The name should communicate that this is a hardware evaluation project, not the final production camera platform.

---

## 7. Repository Structure

```text
nereus-camera-evaluation/
├── README.md
├── CLAUDE.md
├── LICENSE
├── .gitignore
├── pyproject.toml
├── requirements-dev.txt
├── Makefile
│
├── configs/
│   ├── rig.example.yaml
│   ├── experiments/
│   │   ├── reference_card_above_water.yaml
│   │   ├── reference_card_below_water.yaml
│   │   ├── low_light.yaml
│   │   └── object_detection.yaml
│   └── cameras/
│       ├── imx708.yaml
│       ├── openmv_n6.yaml
│       └── openmv_ae3.yaml
│
├── src/
│   └── nereus_camera_eval/
│       ├── __init__.py
│       ├── cli.py
│       ├── controller.py
│       ├── models.py
│       ├── config.py
│       ├── logging_config.py
│       ├── cameras/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── imx708.py
│       │   ├── openmv_usb.py
│       │   └── registry.py
│       ├── capture/
│       │   ├── coordinator.py
│       │   ├── image_capture.py
│       │   ├── video_capture.py
│       │   └── naming.py
│       ├── storage/
│       │   ├── experiment_store.py
│       │   ├── metadata.py
│       │   └── checksums.py
│       ├── analysis/
│       │   ├── apriltag_detector.py
│       │   ├── reference_card.py
│       │   ├── crop.py
│       │   ├── image_metrics.py
│       │   └── result_writer.py
│       └── web/
│           ├── app.py
│           ├── templates/
│           └── static/
│
├── openmv/
│   ├── common/
│   │   ├── command_protocol.py
│   │   ├── capture_service.py
│   │   └── device_info.py
│   ├── n6/
│   │   ├── boot.py
│   │   ├── main.py
│   │   └── board_config.py
│   └── ae3/
│       ├── boot.py
│       ├── main.py
│       └── board_config.py
│
├── host_tools/
│   ├── discover_openmv.py
│   ├── deploy_openmv.py
│   ├── verify_rig.py
│   ├── run_experiment.py
│   ├── collect_results.py
│   ├── compare_cameras.py
│   └── generate_report.py
│
├── scripts/
│   ├── install_pi.sh
│   ├── configure_pi_camera.sh
│   ├── start_web.sh
│   ├── test_imx708.sh
│   ├── test_openmv_n6.sh
│   ├── test_openmv_ae3.sh
│   └── collect_diagnostics.sh
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── hardware/
│   ├── fixtures/
│   │   ├── reference_card_images/
│   │   └── expected_detections/
│   └── conftest.py
│
├── docs/
│   ├── architecture.md
│   ├── hardware_setup.md
│   ├── openmv_usb_protocol.md
│   ├── camera_configuration.md
│   ├── reference_card_pipeline.md
│   ├── experiment_workflow.md
│   ├── test_matrix.md
│   ├── downselect_criteria.md
│   └── open_questions.md
│
├── experiments/
│   └── .gitkeep
│
└── results/
    └── .gitkeep
```

Do not commit generated images, videos, experiment results, or local virtual environments.

---

## 8. Design Principles

### 8.1 Keep Camera Implementations Isolated

Define a small common camera interface:

```python
class CameraDevice:
    def get_device_info(self) -> dict:
        ...

    def configure(self, settings: dict) -> None:
        ...

    def capture_image(self, destination, request) -> dict:
        ...

    def capture_video(self, destination, request) -> dict:
        ...

    def health_check(self) -> dict:
        ...
```

Implement separately for:

- `Imx708Camera`
- `OpenMvUsbCamera`

Do not scatter platform checks throughout the code.

Avoid:

```python
if camera_type == "pi":
    ...
elif camera_type == "n6":
    ...
```

Prefer small adapters registered through a camera registry.

### 8.2 Preserve Useful Pi Logic

Port or adapt proven logic from the Raspberry Pi repository where appropriate:

- capture command construction;
- resolution definitions;
- crop handling;
- image resizing;
- JPEG output;
- optional HEIC output for Pi-only experiments;
- manual exposure, gain, white balance, and focus controls;
- configuration loading;
- AprilTag detection;
- reference-card cropping;
- image-quality test utilities;
- cut-sheet or comparison generation.

Do not import BM transport, Spotter time, reboot capture, production cron, or field deployment behavior.

### 8.3 Use Simple Contracts

The controller should not know OpenMV camera API details.

The OpenMV USB adapter should accept a structured request and return a structured result.

The analysis pipeline should receive an image path and return a result object.

### 8.4 Favor Repeatability Over Convenience

Every experiment must preserve:

- experiment ID;
- timestamp;
- environment label;
- camera identity;
- board firmware;
- sensor configuration;
- image dimensions;
- image format;
- exposure settings where available;
- capture duration;
- output size;
- SHA-256 checksum;
- AprilTag detections;
- reference-card crop result;
- errors and warnings.

---

## 9. OpenMV USB Command Protocol

Use a simple newline-delimited JSON protocol over USB serial.

### 9.1 Device Identification

Request:

```json
{"version":1,"command_id":"abc-001","action":"get_device_info"}
```

Response:

```json
{
  "version":1,
  "command_id":"abc-001",
  "status":"completed",
  "device":{
    "platform":"openmv",
    "board":"n6",
    "device_id":"openmv-n6-001",
    "firmware":"0.1.0"
  }
}
```

### 9.2 Image Capture

```json
{
  "version":1,
  "command_id":"abc-002",
  "action":"capture_image",
  "settings":{
    "framesize":"native",
    "pixel_format":"rgb565",
    "jpeg_quality":90,
    "warmup_frames":10
  }
}
```

### 9.3 Video Capture

```json
{
  "version":1,
  "command_id":"abc-003",
  "action":"capture_video",
  "settings":{
    "duration_seconds":5
  }
}
```

### 9.4 Result Response

```json
{
  "version":1,
  "command_id":"abc-002",
  "status":"completed",
  "output":{
    "filename":"capture_20260714T180000Z.jpg",
    "width":1280,
    "height":720,
    "size_bytes":123456
  }
}
```

### 9.5 Error Response

```json
{
  "version":1,
  "command_id":"abc-002",
  "status":"failed",
  "error":{
    "code":"capture_failed",
    "message":"Sensor snapshot failed"
  }
}
```

Do not implement arbitrary remote Python execution.

Allow only an explicit command allowlist.

---

## 10. OpenMV File Collection

Before implementation, verify the most reliable supported transfer method for each board.

Evaluate in this order:

1. capture to OpenMV removable storage and copy using supported file access;
2. capture to storage and request the file through a framed USB transfer;
3. collect files using official OpenMV tooling;
4. use direct USB serial binary transfer only if required.

Do not assume that text JSON and raw binary can be mixed safely without framing.

If binary transfer is implemented, use:

```text
JSON header line
exact binary length
binary bytes
JSON completion line
```

Validate:

- expected byte count;
- checksum;
- timeout behavior;
- interrupted transfer recovery.

---

## 11. Raspberry Pi Capture Behavior

Use the modern Pi camera utility available on the selected Raspberry Pi OS image:

- prefer `rpicam-still` and `rpicam-vid`;
- provide a compatibility fallback for `libcamera-still` only where needed;
- detect available commands during setup;
- fail with a clear message if the camera stack is unavailable.

The Pi adapter should support:

- still capture;
- short video;
- configurable resolution;
- crop;
- resize;
- JPEG quality;
- exposure time;
- analog gain;
- white balance mode;
- color gains where supported;
- autofocus mode;
- manual lens position where supported;
- warm-up delay;
- capture timeout.

Default IMX708 configurations should be derived from the existing tested camera profiles rather than invented.

---

## 12. Capture Coordination

The Raspberry Pi is the experiment coordinator.

For a three-camera still capture:

1. create experiment record;
2. create timestamped capture-set directory;
3. capture IMX708 image;
4. request N6 image;
5. request AE3 image;
6. collect outputs;
7. calculate checksums;
8. write raw metadata;
9. run reference-card analysis;
10. write analysis results;
11. update the web interface.

One or two seconds of timing difference is acceptable.

Do not add GPIO synchronization.

### Failure Behavior

If one camera fails:

- continue capturing from the remaining cameras;
- mark the failed device in metadata;
- retain successful files;
- return a partial-success result;
- do not delete the experiment folder.

---

## 13. Experiment Data Structure

```text
results/
└── 2026-07-14/
    └── exp_20260714T180000Z_reference_card_above_water/
        ├── experiment.json
        ├── captures/
        │   ├── imx708/
        │   │   ├── image.jpg
        │   │   └── capture.json
        │   ├── openmv_n6/
        │   │   ├── image.jpg
        │   │   └── capture.json
        │   └── openmv_ae3/
        │       ├── image.jpg
        │       └── capture.json
        ├── analysis/
        │   ├── imx708/
        │   │   ├── detection.json
        │   │   ├── annotated.jpg
        │   │   └── card_crop.jpg
        │   ├── openmv_n6/
        │   └── openmv_ae3/
        └── logs/
            └── experiment.log
```

Never overwrite a prior experiment.

---

## 14. Reference-Card Analysis Pipeline

The initial analysis pipeline must:

1. load the captured image;
2. detect AprilTags;
3. report tag IDs and corner coordinates;
4. determine whether the expected card is present;
5. compute the card boundary from configured tags;
6. rectify perspective if the existing implementation supports it;
7. crop the reference-card region;
8. save an annotated image;
9. save the cropped card image;
10. write machine-readable results.

Example result:

```json
{
  "status":"pass",
  "tags_detected":[0,1,2,3],
  "expected_tags":[0,1,2,3],
  "all_expected_tags_found":true,
  "card_crop_created":true,
  "crop_width":1600,
  "crop_height":900,
  "processing_time_ms":184
}
```

### Initial Pass/Fail Rule

A still image passes the MVP reference-card test when:

- all required AprilTags are detected;
- the card boundary can be calculated;
- a nonempty crop is produced;
- the crop is saved without error.

Optional secondary metrics:

- tag pixel size;
- detection margin;
- blur score;
- edge sharpness;
- mean brightness;
- clipping percentage;
- color patch statistics.

Do not make secondary metrics blockers for initial hardware bring-up.

---

## 15. Web Interface

Implement a small local web application hosted by the Pi.

Required pages:

### Rig Status

Display:

- connected cameras;
- camera identity;
- health-check result;
- available storage;
- last capture status.

### New Experiment

Allow selection of:

- experiment type;
- still image or video;
- cameras to include;
- environment label;
- notes;
- camera profile.

### Experiment Results

Display:

- camera outputs side by side;
- capture metadata;
- AprilTag pass/fail;
- annotated image;
- cropped reference card;
- download links;
- error messages.

### Downloads

Allow downloading:

- individual files;
- complete experiment folder as ZIP;
- metadata JSON;
- analysis JSON.

Keep the interface local and simple. Authentication is unnecessary for the MVP on a trusted development network.

---

## 16. Mac-Hosted Tools

The Mac tools should support:

```bash
python -m host_tools.verify_rig
```

```bash
python -m host_tools.run_experiment \
  --config configs/experiments/reference_card_above_water.yaml
```

```bash
python -m host_tools.collect_results \
  --experiment-id exp_20260714T180000Z_reference_card_above_water
```

```bash
python -m host_tools.compare_cameras \
  --experiment-id exp_20260714T180000Z_reference_card_above_water
```

The Mac tools may call the Pi through HTTP or SSH, but the Pi should remain the authoritative capture coordinator.

---

## 17. Future Inference Support

Create interfaces and folders for later inference experiments, but do not make inference part of initial rig acceptance.

Future candidate workflows include:

- YOLO-family detection on the Pi or Mac;
- OpenMV-compatible models on N6 or AE3;
- purple-ball detection;
- fish detection;
- sensor biofouling detection;
- robot detection;
- coral-color or bleaching analysis.

Do not assume the same model binary will run on the Pi, N6, and AE3.

Model evaluation criteria:

- model size;
- inference latency;
- memory usage;
- power use;
- precision and recall;
- minimum object size;
- supported operators;
- deployment complexity.

---

## 18. Configuration

Use YAML on the Pi and Mac.

Example:

```yaml
rig:
  id: "nereus-camera-rig-001"
  results_directory: "./results"

cameras:
  imx708:
    enabled: true
    driver: "imx708"
    profile: "configs/cameras/imx708.yaml"

  openmv_n6:
    enabled: true
    driver: "openmv_usb"
    board: "n6"
    serial_number: null
    profile: "configs/cameras/openmv_n6.yaml"

  openmv_ae3:
    enabled: true
    driver: "openmv_usb"
    board: "ae3"
    serial_number: null
    profile: "configs/cameras/openmv_ae3.yaml"

analysis:
  apriltag:
    enabled: true
    expected_tag_ids: [0, 1, 2, 3]

web:
  host: "0.0.0.0"
  port: 8080
```

Do not identify OpenMV devices solely by `/dev/ttyACM0` and `/dev/ttyACM1`.

Use USB identity or a device handshake because Linux device numbering may change after reboot or reconnect.

---

## 19. Logging and Diagnostics

Use structured, readable logs.

Every log record should include where relevant:

- timestamp;
- experiment ID;
- camera ID;
- action;
- status;
- duration;
- error.

Do not log raw image contents.

Provide:

```bash
./scripts/collect_diagnostics.sh
```

The diagnostic bundle should include:

- OS version;
- Python version;
- installed camera commands;
- connected USB devices;
- Pi camera status;
- application version;
- configuration with secrets removed;
- recent logs;
- disk usage.

---

## 20. Testing

### Unit Tests

Cover:

- configuration parsing;
- output naming;
- metadata generation;
- command serialization;
- OpenMV response parsing;
- experiment folder creation;
- partial camera failure;
- reference-card result formatting.

### Pi Integration Tests

Cover:

- camera detection;
- still capture;
- video capture;
- crop and resize;
- metadata collection;
- reference-card detection on a known image.

### OpenMV Hardware Tests

For each board:

- USB discovery;
- device identity;
- still capture;
- repeated still captures;
- file transfer;
- invalid command rejection;
- disconnect recovery;
- output checksum;
- short-video behavior where supported.

### End-to-End Test

Run one three-camera experiment and verify:

- one experiment directory;
- one output per available camera;
- metadata for each output;
- analysis result for each still image;
- browser display;
- downloadable ZIP;
- no uncaught exception if one camera is unavailable.

---

## 21. Implementation Phases

### Phase 1: Repository Foundation

Deliver:

- repository structure;
- configuration loader;
- common data models;
- CLI skeleton;
- logging;
- test framework;
- documentation;
- development installation instructions.

### Phase 2: IMX708 Baseline

Deliver:

- Pi camera discovery;
- still capture;
- video capture;
- adaptation of proven Pi settings;
- timestamped output;
- metadata;
- CLI capture command.

### Phase 3: Reference-Card Pipeline

Deliver:

- AprilTag detection;
- card localization;
- crop;
- annotation;
- JSON result;
- known-image tests.

### Phase 4: OpenMV N6

Deliver:

- N6 MicroPython service;
- USB discovery and handshake;
- still capture;
- file retrieval;
- metadata;
- repeated-capture test.

### Phase 5: OpenMV AE3

Deliver equivalent AE3 behavior while keeping board-specific code isolated.

### Phase 6: Three-Camera Coordination

Deliver:

- sequential capture;
- partial-failure handling;
- common experiment folder;
- automatic analysis.

### Phase 7: Web Interface

Deliver:

- capture controls;
- status;
- result gallery;
- downloads;
- experiment ZIP export.

### Phase 8: Evaluation Experiments

Add repeatable profiles for:

- above-water reference card;
- below-water reference card;
- controlled artificial lighting;
- ambient lighting;
- low light;
- turbidity;
- fixed-distance resolution;
- purple-ball detection dataset collection;
- static video clips.

---

## 22. Down-Select Criteria

The repository should support evidence-based comparison across:

- image quality;
- AprilTag detectability;
- reference-card crop success;
- low-light performance;
- underwater color performance;
- sharpness and contrast;
- video quality;
- startup time;
- capture latency;
- power consumption;
- storage behavior;
- inference capability;
- development complexity;
- maintainability;
- deployment complexity;
- hardware cost;
- physical size;
- future production fit.

Define the scoring method before final platform selection.

---

## 23. MVP Acceptance Criteria

The MVP is complete when:

1. The Pi detects the IMX708, N6, and AE3.
2. A single command starts one capture set.
3. All available cameras produce a still image.
4. Images are stored in one timestamped experiment folder.
5. Each image has metadata.
6. The reference card can be evaluated.
7. AprilTags are detected when visibly resolvable.
8. A card crop is saved when required tags are found.
9. Results are visible in a browser.
10. The experiment can be downloaded as a ZIP.
11. A disconnected camera produces a clear partial-failure result.
12. Unit tests pass on the Mac.
13. Hardware smoke tests are documented and repeatable.
14. No BM, Spotter, or production field-runtime dependencies are required.

---

## 24. First Task for Claude Code

Start with repository scaffolding and a written implementation assessment.

Before implementing hardware control:

1. inspect all three referenced repositories;
2. identify reusable Raspberry Pi capture files;
3. identify existing AprilTag/reference-card analysis files;
4. identify code that must not be copied because it is BM- or Spotter-specific;
5. propose the exact modules to port, adapt, or rewrite;
6. create `docs/prior_art_review.md`;
7. create `docs/implementation_plan.md`;
8. scaffold the repository;
9. add placeholder interfaces and tests;
10. do not implement OpenMV APIs until USB control and file-transfer mechanisms are verified.

The first milestone should end with a clean repository that runs Mac-side unit tests and clearly documents the next hardware bring-up steps.
