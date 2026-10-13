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
| T3 soak ≥ 2 h | pool002 | running (until 5:55 PM). **Unplanned live hot-unplug:** Nick unplugged the AE3's USB (~3:35 PM) during set 12. AE3 RAW `device_disconnected`; IMX708 + N6 captured normally (43 s set). Set 13 was OK 3/3 at 47 s: the AE3 re-enumerated and recovered with no backoff needed |
| T4 download + render | pool001 / pool002 | smoke PASS: pull + sha256 verify + render end to end. Wi-Fi ~2 MB/s, so the light-first pull was added; full timing after T3 |

## Findings

- **AE3 wedge and the UPS power cycle.** A wedged AE3 (0 bytes out, USB writes time out) cannot be recovered in software without root. A full UPS power cycle recovers it (`scripts/ups_power_cycle.sh`, skill `rig-ups-power-cycle`). The pool loop now does this once per run.
- **N6 RAW clipping.** At auto exposure the N6 clips 11–12 % of RAW pixels on the bench scene: AE gain 20 dB, mean 115/255. The AE3 on the same scene picks 13 dB and clips 0.4 %. Same sensor (PAG7936), different firmware AE targets. Left at auto per the brief.
- **Hard resets under ci-record.** While Nick's ci-record recipe ran, the Pi hard-reset twice (2:49 PM and 3:02 PM): no clean shutdown in the journal, CmaFree at 16 kB. Reported to the EM; not investigated here.
