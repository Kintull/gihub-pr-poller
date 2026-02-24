"""Logging configuration with per-session file rotation."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

LOGS_DIR = Path(__file__).resolve().parent.parent.parent / "logs"


def setup_logging(level: int = logging.DEBUG) -> None:
    """Configure logging to write to a session-specific file in /logs.

    Each invocation creates a new log file with a timestamp, achieving
    per-session rotation.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR / f"session_{timestamp}.log"

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger("github_tracker")
    root_logger.setLevel(level)
    root_logger.addHandler(file_handler)

    root_logger.info("Session started — log file: %s", log_file)
