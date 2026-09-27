"""Mac-only TG-7 (Olympus ORF) tools — SPEC §20.

Uses rawpy (bundled LGPL LibRaw) and the exiftool CLI, so it stays under ``host_tools/`` and
is never imported from ``src/``. Everything downstream consumes the ``RawFrame`` contract.
"""
