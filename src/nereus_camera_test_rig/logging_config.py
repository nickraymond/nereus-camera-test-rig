"""Logging setup — Spec §16.

Structured, readable logs (CLAUDE.md §18): timestamp, logger name, level, message.
Adapted from bm_rpi_camera_module's ``common/logging_config.py`` but simplified — no
BM-specific defaults. Console by default; optional rotating file handler.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

LOG_FORMAT = "%(asctime)s [%(name)s] [%(levelname)s] %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

_DEFAULT_MAX_BYTES = 1024 * 1024  # 1 MiB
_DEFAULT_BACKUPS = 5


def setup_logging(
    level: str = "INFO",
    *,
    console: bool = True,
    log_file: Optional[str | Path] = None,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    backups: int = _DEFAULT_BACKUPS,
    logger_name: str = "nereus",
) -> logging.Logger:
    """Configure and return the rig logger.

    Replaces any existing handlers on the named logger so repeated calls (e.g. in
    tests) don't stack duplicate output. Does not touch the root logger.
    """
    logger = logging.getLogger(logger_name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        logger.addHandler(stream)

    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
