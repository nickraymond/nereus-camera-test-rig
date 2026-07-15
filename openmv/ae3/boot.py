"""OpenMV AE3 boot hook — Spec §7.

Runs once before ``main.py``. Intentionally minimal: USB mode is left at the firmware
default (CDC serial + mass-storage composite) so the board stays recoverable via the USB
drive if ``main.py`` ever fails to start. We never *mount* the mass-storage device from
the host during operation — file transfer is serial-only (§10) — so leaving it enabled
is safe and costs nothing.

Board-agnostic and identical to the N6's ``n6/boot.py``; kept as a per-board copy so the
AE3 deploys independently (CLAUDE.md §6/§36). Keep board bring-up logic out of here; the
capture service lives in ``main.py``.
"""

# No-op boot: nothing to configure before the service starts.
