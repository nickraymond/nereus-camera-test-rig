# nereus002 boot self-check + LED verdict: bench results

**Headline: bench2 on fb3105c passed 17/17 against the unchanged pre-registered bars.**
- Every injected fault gave the right LED code: AE3 3, N6 2, N6+AE3 2,3; IMX708 1 when the
  camera is **in use**. An IMX708 *unplug* could not be emulated and was not tested.
- The 3 all-OK manual starts and 3 real UPS cold boots all gave the OK blink. 0 false faults.
- Set 1 started after the check every time.
- bench1 (5a226d7/549d5a6) was 16/17, so FAIL: the cold-boot LED-timing miss, fixed in
  fb3105c, below.

Pre-registration: `PREREG.md` (same folder, ba35c15, written before bench1). All times are Pacific,
10/15. Raw data on the Pi: `~/nereus-rig-poolcam/results/selfcheck_bench/<bench>/` (`cases.csv`,
`bench.log`, `driver.out`) and each case's run folder `results/pool/poolNNN_*` (`pool.log`,
`run_manifest.json` `self_check`, `led_trace.csv`). Copy on the Mac:
`~/NereusData/nereus-camera-test-rig/2026-10-15_selfcheck/`.

## bench2 (fb3105c): the whole table re-run after the fix

4:12–4:31 PM. Same procedure as bench1. Cold boots used UPS cycles tagged `selfcheck_bench2`.
Every run's `run_manifest.json` `repo_sha` is the REVISION line `fb3105c fix/boot-selfcheck-led …`
(the Pi has no .git, so there is no "-dirty" state). After the bench, the Pi's 307 tracked files
were sha256-identical to `git archive fb3105c` (`pi_tree_sha256_fb3105c.txt` in the data folder).

| # | case | rep | run | expect | got | check s | why (non-ok) | order | LED trace | result |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | ae3_unplugged | 1 | pool030 | `fault:3` | `fault:3` | 22.5 | openmv_ae3=absent | True | 6 bursts, codes [3] (42 writes) | **PASS** |
| 2 | ae3_wedged | 1 | pool031 | `fault:3` | `fault:3` | 3.8 | openmv_ae3=bad_reply | True | 2 bursts, codes [3] (18 writes) | **PASS** |
| 3 | n6_unplugged | 1 | pool032 | `fault:2` | `fault:2` | 22.5 | openmv_n6=absent | True | 7 bursts, codes [2] (32 writes) | **PASS** |
| 4 | n6_wedged | 1 | pool033 | `fault:2` | `fault:2` | 3.6 | openmv_n6=bad_reply | True | 3 bursts, codes [2] (16 writes) | **PASS** |
| 5 | imx_in_use | 1 | pool034 | `fault:1` | `fault:1` | 2.8 | imx708=in_use | True | 2 bursts, codes [1] (6 writes) | **PASS** |
| 6 | n6_ae3_unplugged | 1 | pool035 | `fault:2,3` | `fault:2,3` | 41.1 | openmv_n6=absent, openmv_ae3=absent | True | 6 bursts, codes [2, 3] (31 writes) | **PASS** |
| 7 | all_ok | 1 | pool036 | `verdict_ok` | `verdict_ok` | 3.5 | all ok | True | OK blink, gaps 2.00-2.01 s (10 writes) | **PASS** |
| 8 | ae3_unplugged | 2 | pool037 | `fault:3` | `fault:3` | 22.6 | openmv_ae3=absent | True | 6 bursts, codes [3] (42 writes) | **PASS** |
| 9 | ae3_wedged | 2 | pool038 | `fault:3` | `fault:3` | 3.6 | openmv_ae3=bad_reply | True | 2 bursts, codes [3] (18 writes) | **PASS** |
| 10 | n6_unplugged | 2 | pool039 | `fault:2` | `fault:2` | 22.6 | openmv_n6=absent | True | 7 bursts, codes [2] (32 writes) | **PASS** |
| 11 | n6_wedged | 2 | pool040 | `fault:2` | `fault:2` | 3.8 | openmv_n6=bad_reply | True | 3 bursts, codes [2] (16 writes) | **PASS** |
| 12 | imx_in_use | 2 | pool041 | `fault:1` | `fault:1` | 2.8 | imx708=in_use | True | 2 bursts, codes [1] (6 writes) | **PASS** |
| 13 | all_ok | 2 | pool042 | `verdict_ok` | `verdict_ok` | 3.3 | all ok | True | OK blink, gaps 2.00-2.00 s (10 writes) | **PASS** |
| 14 | all_ok | 3 | pool043 | `verdict_ok` | `verdict_ok` | 3.6 | all ok | True | OK blink, gaps 2.00-2.01 s (10 writes) | **PASS** |
| 15 | all_ok_coldboot | 1 | pool044 | `verdict_ok` | `verdict_ok` | 5.0 | all ok | True | OK blink, gaps 2.00-2.00 s (17 writes) | **PASS** |
| 16 | all_ok_coldboot | 2 | pool045 | `verdict_ok` | `verdict_ok` | 4.2 | all ok | True | OK blink, gaps 2.00-2.00 s (17 writes) | **PASS** |
| 17 | all_ok_coldboot | 3 | pool046 | `verdict_ok` | `verdict_ok` | 4.3 | all ok | True | OK blink, gaps 2.00-2.00 s (17 writes) | **PASS** |

**17/17 PASS**; false faults in all-OK cases: 0

**Numbers (QE's asks), from `led_trace.csv`.** Each row is logged at the *start* of a write, so these
are write-start → write-start gaps.

| gap | bench2 (fb3105c) | bench1 |
|---|---|---|
| fault ON → OFF (hold 0.2 s) | 0.200–0.948 s (n 129) | 0.210–1.425 s (n 125) |
| fault OFF → ON within a burst (hold 0.3 s) | 0.300–2.334 s (n 73) | 0.310–1.882 s (n 71) |
| fault dark, burst → burst (0.3 + 4.0 s) | 4.300–6.359 s (n 46) | 4.311–4.318 s (n 44) |
| OK ON → OFF (2 s) | 1.999–2.005 s (n 39) | 2.011–**3.728** s (n 33) |
| OK OFF → ON (2 s) | 2.000–2.009 s (n 36) | 2.011–2.446 s (n 29) |

- `check_s`: 2.78–41.10 s. A clean check is 2.8–5.0 s; ~22 s with one board absent (the 20 s USB
  wait); 41 s with both boards absent.
- **Switch-on → first verdict write, on the 3 cold boots:** 45.3, 37.5 and 37.7 s of kernel uptime
  (`CLOCK_MONOTONIC` at the first trace row). Add the Pi firmware's pre-kernel boot, not measured,
  typically a few seconds. "Watch the LED for ~1 min" on the card holds. bench1 cold boots: 44.4,
  37.7 and 36.9 s.
- **Fault-mode write rate (N2):** fault patterns ran 9–37 s per case (until set 1 ended), at
  0.54 writes/s (code 1) to 1.39 writes/s (code 3 with short sets), all to the UPS (0x43). That is
  up to ~2.8× the 0.5 writes/s that Nick's 10/13 pattern A used (that was a design choice, not a
  measured limit). In the pool, a fault code runs until switch-off.

**Known limit for Friday (QE N1): a slow LED write can merge or hide one flash.**
- Some writes stall while a set is capturing: up to 0.95 s on an ON write and 2.33 s on an OFF
  write in bench2. The cause is not the lock any more (≤ 0.2 s wait); it is the
  `lifepo4wered-cli` process start and/or the LED thread waiting on the capture's Python thread.
- With phases timed from the write start, a stalled write leaves the *next* visible phase only
  as long as that next write takes, so two flashes can merge, or a flash can be too short to see.
  The trace logs write starts, so it cannot show the visible phases directly.
- 4 of 46 fault bursts (9 %) in bench2 had a write slower than its hold by > 0.1 s (bench1: 4/44).
- A stall can only *lower* the count, never add a flash. **So the card says: watch three groups;
  the highest count is the camera.** If the 9 % per group were independent, all three groups
  would be miscounted ~0.1 % of the time. The stalls come during set captures, so they may
  cluster; that is why the card asks for three groups, not one.
- The OK blink is unaffected unless a write stalls > 2 s: OK gaps were 2.000–2.009 s in bench2.
- After Friday: run the LED driver in its own process, log write completion (`t_done`), hold
  phases from completion, and score the visible phases.

**Non-blocking, after Friday:** N3, `selfcheck_inject.py` copies the two board serials instead
of importing `hil_soak.SERIALS`. N4, no fake-run unit test of `pool_capture.py run --self-check`.


## bench1 (5a226d7 manual cases; 549d5a6 cold boots): 16/17, so FAIL

3:52–4:11 PM. Manual cases ran `pool_mode.sh start --self-check --max-sets 1`. The cold boots
ran with the rig armed `--self-check`, via a UPS power cycle (`ups_power_cycle.sh`, tag
`selfcheck_bench`), then `pool_mode.sh stop` after set 1. 549d5a6 differs from 5a226d7 only by
the `bad_reply` label and docs.

| # | case | rep | run | expect | got | check s | why (non-ok) | order | LED trace | result |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | ae3_unplugged | 1 | pool013 | `fault:3` | `fault:3` | 22.4 | openmv_ae3=absent | True | 6 bursts, codes [3] (39 writes) | **PASS** |
| 2 | ae3_wedged | 1 | pool014 | `fault:3` | `fault:3` | 3.6 | openmv_ae3=replied | True | 2 bursts, codes [3] (18 writes) | **PASS** |
| 3 | n6_unplugged | 1 | pool015 | `fault:2` | `fault:2` | 22.2 | openmv_n6=absent | True | 7 bursts, codes [2] (32 writes) | **PASS** |
| 4 | n6_wedged | 1 | pool016 | `fault:2` | `fault:2` | 3.5 | openmv_n6=replied | True | 3 bursts, codes [2] (16 writes) | **PASS** |
| 5 | imx_in_use | 1 | pool017 | `fault:1` | `fault:1` | 2.7 | imx708=in_use | True | 2 bursts, codes [1] (6 writes) | **PASS** |
| 6 | n6_ae3_unplugged | 1 | pool018 | `fault:2,3` | `fault:2,3` | 41.1 | openmv_n6=absent, openmv_ae3=absent | True | 5 bursts, codes [2, 3] (30 writes) | **PASS** |
| 7 | all_ok | 1 | pool019 | `verdict_ok` | `verdict_ok` | 3.4 | all ok | True | OK blink, gaps 2.01-2.18 s (9 writes) | **PASS** |
| 8 | ae3_unplugged | 2 | pool020 | `fault:3` | `fault:3` | 22.5 | openmv_ae3=absent | True | 6 bursts, codes [3] (42 writes) | **PASS** |
| 9 | ae3_wedged | 2 | pool021 | `fault:3` | `fault:3` | 3.6 | openmv_ae3=replied | True | 2 bursts, codes [3] (18 writes) | **PASS** |
| 10 | n6_unplugged | 2 | pool022 | `fault:2` | `fault:2` | 22.3 | openmv_n6=absent | True | 6 bursts, codes [2] (28 writes) | **PASS** |
| 11 | n6_wedged | 2 | pool023 | `fault:2` | `fault:2` | 3.5 | openmv_n6=replied | True | 3 bursts, codes [2] (16 writes) | **PASS** |
| 12 | imx_in_use | 2 | pool024 | `fault:1` | `fault:1` | 2.8 | imx708=in_use | True | 2 bursts, codes [1] (6 writes) | **PASS** |
| 13 | all_ok | 2 | pool025 | `verdict_ok` | `verdict_ok` | 3.3 | all ok | True | OK blink, gaps 2.01-2.45 s (8 writes) | **PASS** |
| 14 | all_ok | 3 | pool026 | `verdict_ok` | `verdict_ok` | 3.5 | all ok | True | OK blink, gaps 2.01-2.03 s (9 writes) | **PASS** |
| 15 | all_ok_coldboot | 1 | pool027 | `verdict_ok` | `verdict_ok` | 4.9 | all ok | True | OK blink, gaps 2.01-3.73 s (18 writes) | **FAIL** |
| 16 | all_ok_coldboot | 2 | pool028 | `verdict_ok` | `verdict_ok` | 5.0 | all ok | True | OK blink, gaps 2.01-2.47 s (8 writes) | **PASS** |
| 17 | all_ok_coldboot | 3 | pool029 | `verdict_ok` | `verdict_ok` | 4.3 | all ok | True | OK blink, gaps 2.01-2.48 s (16 writes) | **PASS** |

**16/17 PASS**; false faults in all-OK cases: 0

LED trace timing (all runs): fault flash ON n 125, median 0.21 s, min 0.21, max 1.42; fault dark gap n 44, median 4.31 s, min 4.31, max 4.32; OK blink half-period n 62, median 2.01 s, min 2.01, max 3.73

**The miss:** cold boot #1 (pool027). Verdict, order and burst content were all right, but one
OK blink phase lasted 3.73 s against the 1.7–2.8 s bar. The trace is logged at the *start* of
each write, so the ON write at 23:06:13.97 UTC took ~1.7 s to complete. That lines up with the
UPS power logger (`LockedPowerLog`, 4 locked `lifepo4wered-cli get` reads every 10 s), which
right after boot held the shared I²C lock ~1.7 s; `set_led` waited for it (fail-soft limit
2 s). The same stall explains the 1.42 s fault flash in the timing stats. The burst scorer
checks write states, not flash length, so it did not catch that one.

**Fix (fb3105c):**
- `set_led` waits at most 0.2 s for the lock (`LED_LOCK_WAIT_S`), then writes unlocked. It is a
  single register write, and the kernel serializes SMBus transfers.
- The verdict patterns hold each phase from the *start* of its write (`LedDriver._show`), so a
  slow write cannot stretch it.

Per the pre-registration, the whole table is re-run (bench2).

Notes from bench1:
- A board left at its REPL echoes the request back. `first_contact` reports that as stage
  `replied` with ok false. Correctly a fault, but the label read like OK, so it is now
  `bad_reply` (549d5a6).
- Check time: 3.3–3.6 s on a clean rig (~5 s right after a cold boot); ~22 s when a board is
  absent (the 20 s USB wait).
- At a cold boot the verdict lands ~45 s after power-on (run start at uptime ~39 s + the check),
  so "watch the LED for ~1 min" on the card holds.
