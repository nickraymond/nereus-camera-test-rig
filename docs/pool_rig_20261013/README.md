# nereus002 pool-rig bring-up, 2026-10-13: test record

The owner was the session "nereus002 pool imaging" (EM: "EM kickoff: Bristlemouth pool fast-follows").
Pre-registrations are T1–T3 (`T*_prereg.md`). They were written before each test ran.
Raw run folders stay on the Pi at `~/nereus-rig-poolcam/results/pool/`. Mac copies are under
`~/NereusPool/20261013_rig/nereus002/` (not in git).

| Test | Run | Result |
|---|---|---|
| T1 bench smoke, attempt 1 | pool001 | STOPPED after 1 set. N6 and AE3 were left in Nick's recorder state: the N6 at the REPL, the AE3 wedged. Set time 269 s. → N6 hard reset, backoff added, UPS power cycle |
| T2 unattended start | pool002 | PASS. UPS cycle at 3:23:56 PM: Pi off at +4 s, ping back at +106 s, loop started from @reboot at uptime ~20 s (`started_by=boot`), first set OK 3/3. AE3 recovered (the boards are fed by the UPS). Wi-Fi was present; the boot path has no network dependency |
| T1 bench smoke, attempt 2 (first 10 sets of pool002) | pool002 | **PASS.** 10/10 sets OK 3/3, exit 0, SHA256SUMS 10/10. Set time median 44.3 s (43.1–45.2). DNG 24,029,068 B every set; Bayer 1,024,000 B; JPEG 231–388 kB (N6/AE3), 1.86 MB (IMX708). Throttled 0x0, CPU ≤ 49.4 °C, MemAvailable ≥ 224 MB, CmaFree ≥ 62 MB |
| T3 soak ≥ 2 h | pool002 | **PASS, one sub-bar missed.** 188 sets in 2.5 h (stop `duration`, 9029 s). Fully OK 186/188 = **98.9 %** (bar ≥ 98 %): IMX708 188/188; N6 187/188 (set 80, RAW transfer 337 B short → 30 s read timeout, 73 s set; OK again at set 81); AE3 187/188 (set 12, **Nick's live hot-unplug** ~3:35 PM → `device_disconnected`; OK again at set 13). Set time median 44.4 s, p90 45.5 s (1.02×), min 42.7, max 73.4; first-10 44.3 → last-10 44.6 s (+0.8 %). CPU 42.9–49.4 °C (in air, housing not sealed), throttled 0x0 throughout, MemAvailable ≥ 213 MB. **MISS: CmaFree ≥ 60 MB.** The post-set snapshot dipped to 41 MB in 3 sets (12, 16, 63; median 118 MB); no camera failure coincided. Disk 31.1 MB/set (plan 32). SHA256SUMS 188/188 verify, 0 tracebacks, never benched. Power at the UPS output during sets: median 2.84 W (p90 2.93, max 4.11), 35.1 mWh/set. Battery runtime is not measured; projected ~4.5 h from Nick's ~3 h at ~4.3 W (DEV_LOG) |
| T4 download + render | pool002 | **PASS.** Mac over Wi-Fi to the Pi, rig idle, 188-set run. Step 1 light pull (`--no-dng`): 1.30 GB, 3577 files, 672 s = 1.94 MB/s. Step 2 render: 86 s. **First sheet at 12.7 min** (bar ≤ 20). Step 3 the 3 picked DNGs: 72 MB in 38 s. Step 4 re-render with full-res DNG renders: 89 s, **total 14.8 min**. Verify: 3383 files verified, 0 BAD, 0 MISSING, 185 DNGs deferred by design. Renders at full resolution (IMX708 4608×2592 16-bit TIFF + PNG; N6/AE3 1280×800); 376 production crops (1600×900 at native 1504,846); sheet next to the Spotter TFOCUS crops. **Projection for a 3 h pool session** (~240 sets at 44 s): light pull ~14 min, then the full archive (~7.4 GB) ~64 min at ~1.9 MB/s |

## Findings

- **AE3 wedge and the UPS power cycle.** A wedged AE3 (0 bytes out, USB writes time out) cannot be recovered in software without root. A full UPS power cycle recovers it (`scripts/ups_power_cycle.sh`, skill `rig-ups-power-cycle`). The pool loop now does this once per run.
- **N6 RAW clipping.** At auto exposure the N6 clips 11–12 % of RAW pixels on the bench scene: AE gain 20 dB, mean 115/255. The AE3 on the same scene picks 13 dB and clips 0.4 %. Same sensor (PAG7936), different firmware AE targets. Left at auto per the brief.
- **Hard resets under ci-record.** While Nick's ci-record recipe ran, the Pi hard-reset twice (2:49 PM and 3:02 PM): no clean shutdown in the journal, CmaFree at 16 kB. Reported to the EM; not investigated here.

## Decisions recorded

- **Nick, 3:05 PM (EM):** stop ci-record; this session owns nereus002 until the pool test is done.
- **Nick, 3:20 PM:** build a UPS power-cycle skill → `scripts/ups_power_cycle.sh` + `.claude/skills/rig-ups-power-cycle`.
- **Nick, 4:35 PM (EM):** no automatic UPS cycle in the pool (`--ups-recover-after 0`). The rig is switched on and off with a physical switch, so every switch-off is a hard cut (→ #98 hardening, T2c).
- **Nick:** QE-gated merges allowed in the rig repo (#97, #98, #99 merged; main@60f9033 deployed and pinned in `~/nereus-rig-poolcam/REVISION`).
