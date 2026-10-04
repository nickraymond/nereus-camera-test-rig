# nereus002 vs bmcam003 / bmcam004 — what an R0 proxy result transfers

Recorded 2026-10-03 for the Sprint28 R0 proxy run on nereus002 (EM: the bmcam rigs are busy).
nereus002 values were read live (`ssh pi@192.168.1.35`, read-only). bmcam values come **only from
the bm_cam_legacy repo** (branch `feature/sprint28-camera`, head bd6f38c), never from the units.

| | nereus002 (proxy) | bmcam003 / bmcam004 | source (bmcam) | transfers? |
|---|---|---|---|---|
| Board | Raspberry Pi Zero 2 W Rev 1.0 | Pi Zero 2 W | Sprint28 `SPEC.md` (“measured on the Pi Zero 2 W”) | yes |
| OS | Debian 13 trixie | Debian 13 trixie | `runs/sprint15_hil_20260818_bmcam003_004/run_manifest.json` | yes |
| Kernel | 6.18.34+rpt-rpi-v8, aarch64 | 6.18.34+rpt-rpi-v8, aarch64 | same manifest; `runs/s27_ladder_20261001/p0_rpicam_limits/00_env.txt` (bmcam004) | yes |
| rpicam-apps | v1.12.0 (12-05-2026) | v1.12.0 (12-05-2026) | `00_env.txt` (bmcam004) | yes |
| libcamera | (same rpicam build) | v0.7.1+rpt20260609 | `00_env.txt` (bmcam004) | yes (assumed same package set) |
| Camera | imx708_wide, 4608×2592 | imx708_wide | sprint15 manifest; `00_env.txt` | yes |
| MemTotal | 425172 kB | 425172 kB | `00_env.txt` | yes |
| CmaTotal | 262144 kB (256 MB; no `cma=` on the cmdline) | 262144 kB on both units | `runs/g3_hardmode_20261001/snapshots/*` and `00_env.txt` | yes. **Note:** Sprint28 SPEC §0.2/§7 still quotes Sprint07's `cma=128M` (bmcam000, Bullseye); the 003/004 snapshots show 256 MB |
| CmaFree at idle | 96016 kB | 74–104 MB (snapshots, runtime idle or between cycles) | g3 / r1 snapshots | roughly |
| MemAvailable at idle | ~205 MB (recorder_web, workbench, field_power_log running) | 227–239 MB | snapshots | nereus002 has ~25–35 MB **less** headroom: a pass here is conservative |
| Swap | 415 MB | not recorded in the repo | — | unknown |
| config.txt overlays | `camera_auto_detect=1`, `vc4-kms-v3d`, `dwc2,dr_mode=host`, `nospi10`, `i2c_arm=on`, `audio=on` | not recorded in the repo | — | unknown; `dwc2` host mode is for nereus002's USB hub (N6 + AE3) |
| USB devices | powered hub with OpenMV N6 + AE3 (idle) | Bristlemouth UART / bus hardware | — | different peripherals; not CMA users |
| cjxl | v0.11.2 (libjxl-tools) | **unknown** (Sprint28 SPEC §0.2: nothing in the deploy scripts installs it) | SPEC §0.2 | R0.4 must be re-checked on the units |
| system numpy | 2.2.4 | **unknown** (same SPEC line) | SPEC §0.2 | R0.4 must be re-checked |
| SD free | 69 GB of 116 | 24–25 GB of 115 | snapshots | both ample for a 24 MB DNG per wake |
| Runtime around the capture | none (rig software idle) | production runtime stopped + cron disarmed for R0 (LADDER R0 steps 1–3) | LADDER.md | the probe refuses to run with camera processes either way |

**What transfers:** CMA headroom with and without `--raw`, capture time delta, encode time and peak
RSS, and the guard behaviour should carry over: same board, OS, kernel, camera stack, RAM and
CMA size. **What does not:** cjxl/numpy presence (R0.4) and anything that depends on the bmcam
runtime's own memory use or peripherals. Those still need the real R0 on 003/004.
