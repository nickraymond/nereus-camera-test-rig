# nereus002 boot self-check + LED verdict: fault-injection bench test (pre-registered)

Written 2026-10-15 ~3:50 PM PT, before any bench run. One driver smoke run (`all_ok`, bench id
`smoke`, 3:52 PM, not counted) came before the commit (ba35c15, 3:52 PM); bench1 started 3:52 PM.
Scope: Nick's plan A (10/15 3:40 PM,
"freeze exception approved, I approve plan A"). A boot camera self-check and an LED verdict
only: no automatic power cycle, and nothing else in startup changes. Hard rollback 8 PM: if the
gate is not passed and QE-PASSED by then, the rig is re-armed with 38af78b exactly as at 3:30 PM.

## What is tested

`pool_capture.py run --self-check` (scripts/pool_selfcheck.py + the pool_led.py verdict modes):
before set 1 it checks the IMX708 (1), N6 (2) and AE3 (3) read-only, then shows:

| verdict | LED |
|---|---|
| all OK | slow blink 2 s on / 2 s off (10 min, then steady) |
| camera N failed | N quick flashes (0.2 s on / 0.3 s off) + 4 s dark, repeating; two faults alternate |
| low battery | Nick's code B (firmware fast flashing), overrides both |

## Fault injection (the housing is sealed: no cable is pulled)

| injected | how (scripts/selfcheck_inject.py) | host-side symptom |
|---|---|---|
| board unplugged | `detach n6/ae3`: usbfs releases its CDC interface | by-id port gone, as on an unplug |
| board wedged | `repl n6/ae3`: mpremote leaves it at the REPL | port present, no get_device_info reply |
| IMX708 in use | `hold-imx`: a background `rpicam-hello -t 0` | libcamera cannot acquire the camera |

An IMX708 unplug cannot be emulated without root or opening the housing; "in use" exercises
the same open path. Injected faults do not survive a cold boot, so the fault cases start the
loop with the boot path's own launcher (`pool_mode.sh start … --self-check --max-sets 1`);
the all-OK cases include 3 real cold boots through the UPS with the rig armed `--self-check`.

## Cases (n = 17)

| # | case | n | expected verdict |
|---|---|---|---|
| 1 | AE3 unplugged | 2 | `fault:3` |
| 2 | AE3 wedged | 2 | `fault:3` |
| 3 | N6 unplugged | 2 | `fault:2` |
| 4 | N6 wedged | 2 | `fault:2` |
| 5 | IMX708 in use | 2 | `fault:1` |
| 6 | N6 + AE3 unplugged | 1 | `fault:2,3` |
| 7 | all OK, manual start | 3 | `verdict_ok` |
| 8 | all OK, real cold boot (UPS power cycle, armed `--self-check`) | 3 | `verdict_ok` |

## Pass bars (all must hold; any miss = FAIL, fix, and re-run the whole table)

1. **17/17 correct verdicts** (`run_manifest.json` `self_check`, and the pool.log `SELF-CHECK` line).
2. **0 false faults** in cases 7–8.
3. **The right N**: the `led_trace.csv` writes match the pattern. A fault shows exactly N ON/OFF
   pairs, then a dark gap of ≥ 3.5 s. OK shows ON/OFF about 2 s apart.
4. **Set 1 starts after the check** in every run (pool.log order: `SELF-CHECK` before `[set 001`).
5. After each case the injected fault is cleared and `board_probe probe` answers ok on both
   boards (no lasting damage).

Plus Nick's eyeball check, ~2 min through the housing: one OK blink (`pool_led.py demo OK`) and
one fault code (`demo F3`), batched by the PjM.

Evidence: per-case rows in `results/selfcheck_bench/<run>/cases.csv` on the Pi, copied to
`~/NereusData/nereus-camera-test-rig/2026-10-15_selfcheck/`. The LED-write traces are summarised
in `RESULTS.md`.
