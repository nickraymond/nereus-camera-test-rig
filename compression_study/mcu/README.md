# MCU codec desk study (2026-10-01)

Which lossy encoder could run on the OpenMV N6 / AE3 (Cortex-M55) as a MicroPython native
module, and how close does it get to the study's D2 (sqrt planes → libjxl modular)? Scored with
the study's own metrics on the S4 frames (`compression_study/mcu_codec_bench.py`, results in
`mcu_codec_study.json`, 290 rows). Summary and recommendation: `../REPORT.md`, "MCU encoders".

- `wl53/` — our own prototype: integer LeGall 5/3 wavelet + per-band dead-zone quantizer +
  JPEG-LS-style Golomb-Rice coder with run mode (~420 lines of C, libc + libm). Desk-study code,
  not yet a native module. Tuned on the N6 cool in-air frame (`tune_wl53.py`).
- `hydrium/` — patch against Traneptora/hydrium (BSD-2-Clause, `LICENSE`, commit in
  `UPSTREAM_COMMIT`): float-input path, rounded LF, HF multiplier / global scale / LF step as
  parameters (a rate knob). Output stays standard JPEG XL (`djxl` decodes it). `drv.c` is the
  test driver.
- **Follow-up (2026-10-01):** hydrium now runs on both boards as `natmod/nrhyd.mpy`, from the
  vendored, patched source in `../methods/hydrium/` (`../methods/hyd.c` = the CLI); results in
  `../REPORT.md`, "hydrium vs wl53 on the OpenMV boards". The patch here is the desk-study
  version, kept for the record.
- `timing.py`, `timing.json`, `heap.json` — Mac timings (no SIMD) and heap measurements; board
  times are estimates (Mac × the packer's measured Mac → board ratio), not board runs.
