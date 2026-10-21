# nereus002 blink codes (the LiFePO4wered LED)

**At switch-on the rig checks all three cameras (~1 min after you switch on), then shows the result.**
The check never fixes anything itself. The program always runs, with whichever cameras work.

| You see | It means | You do |
|---|---|---|
| Breathing, or steady ON, in the first ~1 min (before the 3 slow blinks) | Booting or checking: no result yet | Wait |
| **3 SLOW BLINKS** (2 s on, 2 s off), then **steady ON** | **All 3 cameras OK** | Seal and dive |
| After that: **breathing** (slow pulse) | **Recording a video clip** (all OK) | Nothing: it goes back to steady ON between clips |
| **1 quick flash** + 4 s dark, repeating | **IMX708** (main camera) failed | Switch OFF, wait 10 s, switch ON. Watch again |
| **2 quick flashes** + 4 s dark, repeating | **N6** failed | Switch OFF, wait 10 s, switch ON. Watch again |
| **3 quick flashes** + 4 s dark, repeating | **AE3** failed | Switch OFF, wait 10 s, switch ON. Watch again |
| Two counts taking turns (e.g. 2 … 3 …) | Both of those cameras failed | Switch OFF, wait 10 s, switch ON. Watch again |
| The **same code again** after the off/on | It did not clear | Your call: dive anyway (the other cameras record) or stop |
| **Fast flashing, no pauses** | **Low battery** | Charge it |
| No result after 3 min (still breathing or steady) | The check did not run | Switch OFF, wait 10 s, switch ON |
| LED off | Rig off | Switch ON |

**Breathing means two things; the order tells them apart.** Breathing *before* the 3 slow blinks =
booting. Breathing *after* the blinks and at least 5 s of steady ON = recording. The first clip (~1 min
after switch-on) can start a few seconds before its breathing does. A fault code keeps showing while the
other cameras record (no breathing then).

**Watch three or four groups; the highest count is the camera.** The 4 s dark gap separates the
groups. While the rig is capturing, a flash can occasionally merge with the next one, which only ever
makes a group look *lower*. Two counts that keep taking turns (2 … 3 … 2 … 3) = both cameras failed.

Camera numbers: **1 = IMX708**, **2 = N6**, **3 = AE3**.

---
For engineers: `pool_capture.py run --self-check` (rig PR, 10/15, Nick's plan A). The verdict is
in `results/pool/<run>/pool.log` (`SELF-CHECK …`) and `run_manifest.json` (`self_check`).
Every LED write is logged in `led_trace.csv` (mode `recording` = the clip pulse). No automatic power cycle.
Recording pulse (Nick 10/20): firmware `LED_STATE 2` (pulsing), one write per clip start and one back to
steady at its end; `pool_led.OK_BLINKS` = 3, `READY_SOLID_S` = 5 s. Demo: `pool_led.py demo SEQ`.
