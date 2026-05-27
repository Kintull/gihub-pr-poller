"""Desktop notifications for favourite PR events."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys

logger = logging.getLogger("github_tracker.notifier")


def notify(title: str, message: str, url: str | None = None) -> None:
    """Send a desktop notification. Silent fail on unsupported platforms."""
    body = f"{message}\n{url}" if url else message
    try:
        if sys.platform == "darwin":
            _notify_macos(title, body)
        elif sys.platform.startswith("linux"):
            _notify_linux(title, body)
        else:
            logger.debug("Notify (no-op on %s): %s — %s", sys.platform, title, body)
    except Exception as e:
        logger.warning("Failed to send notification: %s", e)


def _escape_applescript(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _notify_macos(title: str, message: str) -> None:
    t = _escape_applescript(title)
    m = _escape_applescript(message)
    script = f'display notification "{m}" with title "{t}"'
    subprocess.run(
        ["osascript", "-e", script],
        check=False,
        timeout=5,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _notify_linux(title: str, message: str) -> None:
    if shutil.which("notify-send") is None:
        logger.debug("notify-send not installed; skipping notification")
        return
    subprocess.run(
        ["notify-send", title, message],
        check=False,
        timeout=5,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
