"""
Intermediate file cleanup utilities.

Consolidates cleanup logic previously duplicated across 5 scripts.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def cleanup_intermediate_files(
    files: list[Path],
    *,
    preserve_suffixes: tuple[str, ...] = (".transcript.json",),
) -> None:
    """Delete intermediate files, optionally preserving those matching
    *preserve_suffixes*.

    This is useful for keeping transcript caches while removing chunk
    video files after processing.
    """
    for file_path in files:
        if any(str(file_path).endswith(s) for s in preserve_suffixes):
            logger.debug("Preserving: %s", file_path.name)
            continue
        try:
            file_path.unlink()
            logger.debug("Deleted: %s", file_path.name)
        except Exception as exc:
            logger.error("Error deleting %s: %s", file_path, exc)
