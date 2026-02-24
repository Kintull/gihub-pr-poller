"""Tests for logging_config module."""

import logging
from pathlib import Path
from unittest.mock import patch

from github_tracker.logging_config import LOGS_DIR, setup_logging


class TestSetupLogging:
    def test_creates_log_dir(self, tmp_path):
        log_dir = tmp_path / "logs"
        with patch("github_tracker.logging_config.LOGS_DIR", log_dir):
            setup_logging()
        assert log_dir.exists()

    def test_creates_session_log_file(self, tmp_path):
        log_dir = tmp_path / "logs"
        with patch("github_tracker.logging_config.LOGS_DIR", log_dir):
            setup_logging()
        log_files = list(log_dir.glob("session_*.log"))
        assert len(log_files) >= 1

    def test_writes_session_start_message(self, tmp_path):
        log_dir = tmp_path / "logs"
        with patch("github_tracker.logging_config.LOGS_DIR", log_dir):
            setup_logging()
        log_files = list(log_dir.glob("session_*.log"))
        content = log_files[0].read_text()
        assert "Session started" in content

    def test_logger_level_is_debug(self, tmp_path):
        log_dir = tmp_path / "logs"
        with patch("github_tracker.logging_config.LOGS_DIR", log_dir):
            setup_logging(level=logging.DEBUG)
        root = logging.getLogger("github_tracker")
        assert root.level == logging.DEBUG

    def test_logs_dir_constant(self):
        assert LOGS_DIR.name == "logs"

    def teardown_method(self):
        # Clean up handlers added during tests
        root = logging.getLogger("github_tracker")
        root.handlers = [h for h in root.handlers if not isinstance(h, logging.FileHandler)]
