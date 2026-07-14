# CLAUDE.md
# Nereus Camera Test Rig — Development Guide

## Purpose

You are contributing to a hardware-evaluation repository for Nereus Vision.

This repository compares multiple camera compute platforms:

- Raspberry Pi with IMX708
- OpenMV N6
- OpenMV AE3

The goal is not to ship a production system immediately.

The goal is to build a simple, repeatable test rig that produces reliable evidence for a future hardware down-select.

The evaluation includes:

- still images;
- short video clips;
- AprilTag detection;
- reference-card detection and crop;
- above-water and underwater testing;
- future inference experiments;
- image-quality and hardware suitability comparisons.

**The full specification is the source of truth. Read it first:** @docs/SPEC_nereus_camera_test_rig.md

---

# Engineering Philosophy

## 1. Favor Simple, Effective Solutions

Prefer:

- small modules;
- explicit control flow;
- readable Python;
- plain JSON/YAML;
- direct command-line tools;
- visible logs;
- reproducible output folders;
- straightforward tests.

Avoid:

- large frameworks;
- unnecessary services;
- premature plugin systems;
- hidden state;
- complex dependency injection;
- elaborate event systems;
- speculative abstractions.

Do not build infrastructure for a future requirement unless the current sprint needs it.

This repository should remain easy to debug by one engineer.

---

## 2. Move Quickly Without Creating Fragile Code

Fast development does not mean skipping structure.

Each feature should:

- have a narrow purpose;
- fit into an obvious module;
- include a smoke test;
- produce visible output;
- fail clearly;
- avoid unrelated edits.

Prefer incremental changes that can be validated independently.

Do not combine capture, analysis, web UI, USB transport, and deployment changes into one unreviewable patch.

---

## 3. Reuse Before Rewriting

The linked Raspberry Pi repositories contain proven work.

Before writing replacement code:

1. inspect the relevant implementation;
2. identify what is already working;
3. identify hardware-specific assumptions;
4. reuse or adapt the smallest appropriate component;
5. document what was reused and what changed.

Do not rewrite working image capture, crop, reference-card, or AprilTag logic merely to make it look cleaner.

A rewrite requires a measurable benefit.

---

## 4. Treat This Repository as an Evaluation Laboratory

This repository is for learning.

Its job is to answer:

- Which camera produces the best useful images?
- Which platform detects the reference card most reliably?
- Which platform is easiest to develop?
- Which platform is efficient enough for the target application?
- Which platform supports future inference?
- Which platform is practical for underwater deployment?

Do not prematurely force production assumptions into the evaluation code.

At the end of the hardware down-select, the production repository may be created separately.

---

## 5. Design for Deletion

Experimental code may be discarded after the down-select.

Do not over-invest in generalization merely to preserve every experimental branch forever.

Build enough structure to:

- run the experiment;
- understand the results;
- reproduce the outcome;
- reuse genuinely valuable components later.

The final production repository should inherit validated ideas, not accumulated evaluation complexity.

---

# Architecture Rules

## 6. Keep Shared Behavior Separate from Hardware Details

Shared behavior includes:

- experiment definitions;
- naming;
- metadata;
- result structure;
- analysis contracts;
- reference-card evaluation;
- host-side comparison tools.

Hardware-specific behavior includes:

- Pi camera commands;
- OpenMV camera APIs;
- USB serial behavior;
- device-specific storage;
- board-specific video support.

Use small hardware adapters.

Do not scatter logic such as:

```python
if platform == "pi":
    ...
elif platform == "n6":
    ...
elif platform == "ae3":
    ...
```

through the repository.

A platform adapter should expose a common conceptual interface while remaining free to implement it differently.

---

## 7. Keep Interfaces Small

Only abstract behavior that is genuinely shared.

A camera adapter may need:

```python
get_device_info()
health_check()
configure()
capture_image()
capture_video()
```

Do not create large inheritance trees.

Do not require every device to support identical settings.

Represent unsupported capabilities clearly.

---

## 8. Keep the Raspberry Pi as the Experiment Coordinator

For this project:

- the Pi owns experiment execution;
- the Pi controls the IMX708 directly;
- the Pi sends USB commands to OpenMV boards;
- the Pi gathers outputs;
- the Pi stores experiment artifacts;
- the Pi runs the initial reference-card pipeline;
- the Pi serves results for download.

Do not add BM, Spotter, or field-runtime behavior unless a later sprint explicitly adds it.

---

## 9. Use USB Simply

The OpenMV MVP should use a small, inspectable command protocol.

Prefer:

- newline-delimited JSON;
- explicit command IDs;
- explicit completion/failure responses;
- checksums for transferred files;
- device handshakes.

Do not implement arbitrary remote Python execution.

Do not assume `/dev/ttyACM0` is always one specific board.

Use board identity or a handshake.

---

# Experiment Discipline

## 10. One Major Variable at a Time

Keep these separate:

- camera hardware;
- lens;
- distance;
- lighting;
- above-water versus underwater;
- image resolution;
- exposure;
- white balance;
- compression;
- model inference;
- image processing.

A clean experiment changes one major variable.

If multiple variables must change, document them explicitly.

---

## 11. Preserve Raw Data

Never overwrite or delete raw captures automatically.

Derived outputs must be separate:

- annotated image;
- cropped card;
- rectified card;
- thumbnail;
- metrics JSON;
- comparison sheet.

Raw captures are the evidence.

---

## 12. Every Experiment Produces a Self-Contained Result

Each run should include:

- experiment ID;
- timestamp;
- environment;
- operator notes;
- camera identity;
- firmware/software version;
- configuration used;
- raw captures;
- derived outputs;
- metadata;
- logs;
- checksums;
- analysis results;
- errors and warnings.

A future reviewer should understand the run without reading chat history.

---

## 13. Measure Before Optimizing

Do not optimize based on intuition.

Measure:

- capture time;
- transfer time;
- output size;
- memory use;
- power use;
- AprilTag success;
- crop success;
- sharpness;
- low-light behavior;
- inference latency;
- model accuracy.

Establish a baseline first.

---

## 14. Human Inspection Remains Important

Automated metrics guide decisions but do not replace review.

Use:

- metrics;
- annotated outputs;
- crops;
- cut sheets;
- side-by-side comparisons;
- engineering judgment.

For image work, visual artifacts are engineering deliverables.

---

# Coding Standards

## 15. Keep Modules Focused

Each module should have one clear responsibility.

Examples:

- camera adapter;
- USB protocol;
- experiment coordinator;
- metadata writer;
- AprilTag detector;
- crop generator;
- web result view.

Avoid giant scripts that capture, analyze, store, and serve results in one file.

---

## 16. Make Hardware Assumptions Explicit

Document:

- expected camera;
- expected board;
- expected USB behavior;
- expected storage;
- expected utility command;
- expected OS;
- expected file format.

If an assumption is not verified, mark it as an assumption.

Do not invent OpenMV APIs.

Verify against official documentation or working examples.

---

## 17. Fail Loudly and Usefully

Do not hide errors.

Failures should include:

- camera ID;
- action;
- command ID;
- relevant path;
- timeout;
- exception or return code;
- recovery suggestion where known.

A partial camera failure should not delete successful results from other cameras.

---

## 18. Keep Logging Useful

Logs should show:

- what is happening;
- which camera is involved;
- experiment ID;
- input settings;
- output path;
- elapsed time;
- success/failure.

Avoid noisy logs with no decision value.

Do not log raw binary image data.

---

## 19. Validate Outputs, Not Just Exit Codes

A script that exits successfully but creates an empty or corrupt output is a failure.

After capture, verify:

- file exists;
- file size is plausible;
- image can be opened;
- dimensions match expectations;
- checksum can be calculated.

After analysis, verify:

- result JSON exists;
- annotation exists when expected;
- crop is nonempty;
- detected tags are reported.

---

## 20. Check for Regressions

After meaningful changes, compare:

- before and after file size;
- before and after output dimensions;
- tag count;
- crop behavior;
- CLI behavior;
- metadata;
- run-folder structure;
- test results.

Do not assume a refactor preserves behavior.

Use fixtures and known reference images.

---

## 21. Avoid Hidden Dependencies

Dependencies must be documented in:

- `pyproject.toml`;
- `requirements-dev.txt`;
- setup scripts;
- hardware setup documentation.

Do not rely on an unrecorded local tool or environment variable.

---

## 22. Use Type Hints Where Helpful

Use type hints in host and Pi CPython code where they improve readability.

Do not force CPython-only typing patterns into MicroPython modules.

OpenMV code should remain lightweight and compatible with the actual runtime.

---

## 23. Keep MicroPython Constraints Visible

OpenMV code may have:

- smaller memory;
- different standard libraries;
- different filesystem behavior;
- different concurrency options;
- different camera APIs.

Do not assume CPython code can be copied unchanged.

Favor:

- small allocations;
- streaming;
- compact messages;
- simple loops;
- bounded buffers.

---

# Testing Rules

## 24. Use Three Test Levels

### Unit Tests

Run on Mac for:

- config;
- metadata;
- naming;
- serializers;
- parsers;
- result validation;
- experiment-folder logic.

### Integration Tests

Run against:

- Pi camera;
- USB OpenMV device;
- known reference-card images;
- local web interface.

### Hardware Tests

Run on real devices for:

- capture;
- repeated capture;
- reconnect;
- file transfer;
- partial failure;
- video support;
- recovery.

---

## 25. Every New Feature Needs a Smoke Test

A feature is not complete until there is a simple command that proves it works.

Examples:

```bash
./scripts/test_imx708.sh
./scripts/test_openmv_n6.sh
./scripts/test_openmv_ae3.sh
python -m host_tools.verify_rig
```

Provide expected outputs and failure signs.

---

## 26. Do Not Overbuild Test Infrastructure

Use straightforward tests.

Prefer:

- `pytest`;
- fixtures;
- sample images;
- expected JSON;
- explicit hardware smoke scripts.

Do not build a complex distributed test platform.

---

# Development Workflow

## 27. Use the Spec as the Source of Truth

Each sprint or phase should begin from a written spec or checklist.

If implementation reality differs:

1. document the mismatch;
2. propose the smallest correction;
3. update the spec;
4. continue with the revised scope.

Do not silently expand scope.

Start by referencing @docs/SPEC_nereus_camera_test_rig.md

---

## 28. Use a Branch-and-PR Workflow with Small Commits

Never commit directly to `main`.

For each change:

1. create a branch (e.g. `feat/imx708-adapter`, `fix/openmv-handshake`);
2. make small, focused commits — each commit is one clear change;
3. open a pull request describing what changed and how it was tested;
4. leave merging to `main` to the human reviewer.

Do not merge your own PRs unless explicitly told to.

Good examples:

- scaffold repository;
- add IMX708 adapter;
- add OpenMV handshake;
- add experiment metadata;
- port AprilTag detector;
- add result gallery.

Avoid giant commits mixing unrelated architecture and hardware changes.

---

## 29. Preserve a Working Baseline

Before changing a working path:

- record the known-good command;
- retain a fixture or sample output;
- record relevant settings;
- keep a rollback commit.

Do not casually remove a working path during experimentation.

---

## 30. Keep a Clear Decision Trail

When choosing an implementation, document:

- options considered;
- decision;
- reason;
- tradeoffs;
- follow-up work.

Use lightweight architecture decision records only for meaningful decisions.

Do not create paperwork for trivial choices.

---

# Agent Behavior

## 31. Inspect Before Coding

Before writing code:

- inspect the repository;
- inspect referenced prior art;
- find existing functions;
- identify constraints;
- identify tests;
- identify hardware assumptions.

Do not start by generating a new architecture without understanding what exists.

---

## 32. Do Not Make Things Up

If something is unknown:

- say it is unknown;
- inspect the source;
- consult official documentation;
- add it to `docs/open_questions.md`.

Never invent:

- OpenMV API behavior;
- camera capabilities;
- image formats;
- storage limits;
- USB behavior;
- sensor controls;
- command-line flags.

---

## 33. Ask for Context Only When It Truly Blocks Work

Prefer making a reasonable, documented assumption for minor details.

Ask for clarification when the answer materially changes:

- hardware wiring;
- supported camera;
- output format;
- test acceptance;
- destructive action;
- production behavior.

Do not stall simple work with excessive questions.

---

## 34. Provide Practical Handoffs

After a change, provide:

- what changed;
- files changed;
- exact command to run;
- expected output;
- success criteria;
- what was not tested;
- likely failure modes.

The next step should be obvious.

---

# Repository-Specific Boundaries

## 35. Keep BM and Spotter Out of MVP

This evaluation repository should not initially include:

- BM UART;
- Spotter time;
- cellular transmission;
- field scheduling;
- production daemon logic.

Those belong to a later production architecture decision.

---

## 36. Do Not Force Pi and OpenMV to Share All Code

They should share:

- behavior contracts;
- metadata;
- experiment definitions;
- result format;
- test expectations.

They do not need identical implementations.

Portability means consistent behavior, not identical source code.

---

## 37. Treat Host Tools as First-Class Code

Mac-side tools are strategic.

They should:

- run experiments;
- gather results;
- compare cameras;
- validate outputs;
- generate reports.

Keep host tools independent of one specific camera where practical.

---

## 38. Prepare for Down-Select

The repository should make hardware comparison easy.

Track:

- image quality;
- tag detection;
- crop success;
- power;
- speed;
- storage;
- inference;
- complexity;
- maintenance;
- hardware cost.

Do not optimize the evaluation repository into a production repository before the hardware choice is made.

---

# Development Checklist

## Before Coding

- [ ] Read the current spec.
- [ ] Inspect relevant prior-art code.
- [ ] Identify reusable modules.
- [ ] Confirm hardware assumptions.
- [ ] Confirm expected inputs and outputs.
- [ ] Confirm experiment result structure.

## While Coding

- [ ] Keep modules small.
- [ ] Avoid speculative abstraction.
- [ ] Add useful logs.
- [ ] Add or update tests.
- [ ] Preserve raw captures.
- [ ] Document hardware-specific behavior.
- [ ] Keep platform code isolated.

## After Coding

- [ ] Run smoke test.
- [ ] Confirm expected files exist.
- [ ] Confirm file sizes are plausible.
- [ ] Confirm images open.
- [ ] Confirm metadata is complete.
- [ ] Confirm prior behavior still works.
- [ ] Check for empty output folders.
- [ ] Summarize untested areas.

## Before Handoff

- [ ] Provide exact commands.
- [ ] Provide expected paths.
- [ ] State assumptions.
- [ ] State limitations.
- [ ] State success criteria.
- [ ] State likely failure modes.
- [ ] Identify the next smallest useful step.

---

# Golden Rules

```text
Reuse before rewriting.
Measure before optimizing.
Keep hardware differences isolated.
Preserve raw evidence.
Change one variable at a time.
Prefer boring, debuggable engineering.
```

Additional rule:

> Never trust a script just because it exits successfully. Trust the artifacts.

For this project, success means the right files exist, have plausible sizes, contain useful metadata, and clearly answer the hardware-evaluation question.
