"""OpenMV device-info builder — Spec §8.

Shared across boards: assembles the ``get_device_info`` response from the running
firmware (``os.uname()``) plus the board's ``board_config``. Kept free of any sensor
initialization so a device-info query has no side effects on the camera.

Runs on the board under MicroPython. Imported flat there (``import device_info``).
"""

import os


def build(board_config):
    """Return the device-info payload for a ``get_device_info`` response (§8).

    Fields mirror the Spec §8 example (``platform``/``board``/``device_id``/``firmware``)
    and add the MicroPython/machine strings that recon found useful for the down-select.
    """
    u = os.uname()
    return {
        "platform": "openmv",
        "board": board_config.BOARD,
        "device_id": board_config.DEVICE_ID,
        "sensor": board_config.SENSOR_NAME,
        # release is the MicroPython/OpenMV version string, e.g. "1.26.0".
        "firmware": u.release,
        "micropython": u.version,   # e.g. "v1.26.0-77 on 2025-12-22"
        "machine": u.machine,       # e.g. "OpenMV N6 with STM32N657X0"
        "mount_rotation_deg": board_config.MOUNT_ROTATION_DEG,
    }
