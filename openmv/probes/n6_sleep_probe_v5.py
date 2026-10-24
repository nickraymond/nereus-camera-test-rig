"""N6 low-power states, one after another, for the INA3221 (Nick, 10/23: "what is the low idle state?").

Run by ``scripts/n6_sleep_power.py`` (``mpremote run``), which time-stamps every ``#P`` line on the
Pi clock and logs CH1 meanwhile. Each phase prints ``#P <name> start`` and holds HOLD_MS. A
control the firmware lacks prints ``#P <name> error <repr>`` and the probe moves on. The legacy
``sensor`` calls (``sleep``, ``shutdown``) are tried by name, never assumed.

Phases: awake_sensor_on (sensor running, processor idle) -> sensor_sleep -> sensor_shutdown ->
lightsleep (machine.lightsleep(HOLD_MS)) -> deepsleep (machine.deepsleep(HOLD_MS): the board drops
off USB and reboots into the rig service; the runner keeps logging).
"""
import time

import machine
import sensor

HOLD_MS = 20000


def mark(name, what="start"):
    print("#P", name, what)


def hold():
    time.sleep_ms(HOLD_MS)


sensor.reset()
sensor.set_pixformat(sensor.RGB565)
sensor.set_framesize(sensor.VGA)
sensor.skip_frames(time=1000)
mark("awake_sensor_on")
hold()

for name, call in (("sensor_sleep", lambda: sensor.sleep(True)),
                   ("sensor_shutdown", lambda: sensor.shutdown(True))):
    try:
        call()
        mark(name)
        hold()
    except Exception as e:
        mark(name, "error " + repr(e))

mark("lightsleep")
try:
    machine.lightsleep(HOLD_MS)
    mark("lightsleep", "woke")
except Exception as e:
    mark("lightsleep", "error " + repr(e))

mark("deepsleep")
time.sleep_ms(200)  # let the line reach the host before USB goes away
machine.deepsleep(HOLD_MS)
