"""
Standardised logger construction used across all CLI entry points.
"""

from __future__ import annotations

import logging
import sys


def build_logger(name: str, verbose: bool = False) -> logging.Logger:
    """Create (or retrieve) a logger with a consistent format.

    Avoids adding duplicate handlers on repeated calls.
    """
    logger = logging.getLogger(name)
    level = logging.DEBUG if verbose else logging.INFO
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        handler.setLevel(level)
        logger.addHandler(handler)

    return logger
