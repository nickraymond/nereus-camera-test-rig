"""OpenMV N6 boot hook — Spec §7.

Runs once before ``main.py``. Intentionally minimal: USB mode is left at the firmware
default (CDC serial + mass-storage composite) so the board stays recoverable via the
USB drive if ``main.py`` ever fails to start. We never *mount* the mass-storage device
from the host during operation — file transfer is serial-only (§10) — so leaving it
enabled is safe and costs nothing.

Keep board bring-up logic out of here; the capture service lives in ``main.py``.
"""

# No-op boot: nothing to configure before the service starts.
