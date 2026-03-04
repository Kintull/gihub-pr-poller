"""Configuration loading and management."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger("github_tracker.config")

DEFAULT_CONFIG_PATH = Path.home() / ".github-tracker-config.yaml"

DEFAULT_CONFIG = {
    "jira_base_url": "",
    "github_repos": [],
    "refresh_interval": 300,
    "github_username": "",
    "acc_workflow_name": "Reisbalans deploy to cloud acceptance",
    "acc_retention_days": 2,
    "acc_cooldown_minutes": 20,
}


class ConfigError(Exception):
    """Raised when configuration is invalid."""


@dataclass
class Config:
    """Application configuration."""

    jira_base_url: str = ""
    github_repos: list[str] = field(default_factory=list)
    refresh_interval: int = 300
    github_username: str = ""
    acc_workflow_name: str = "Reisbalans deploy to cloud acceptance"
    acc_retention_days: int = 2
    acc_cooldown_minutes: int = 20

    def jira_enabled(self) -> bool:
        """Check if Jira integration is configured."""
        return bool(self.jira_base_url)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """Load configuration from a YAML file.

    Creates a default config file if it doesn't exist.
    """
    logger.info("Loading config from %s", path)
    if not path.exists():
        logger.warning("Config file not found at %s — creating default", path)
        create_default_config(path)
        return Config()

    raw = path.read_text()
    if not raw.strip():
        logger.warning("Config file is empty at %s — using defaults", path)
        return Config()

    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ConfigError(f"Config file must be a YAML mapping, got {type(data).__name__}")

    config = _parse_config(data)
    logger.info(
        "Config loaded: repos=%s, jira_base_url=%r, refresh_interval=%d",
        config.github_repos, config.jira_base_url, config.refresh_interval,
    )
    return config


def _parse_config(data: dict) -> Config:
    """Parse and validate a config dict into a Config object."""
    jira_base_url = data.get("jira_base_url", "")
    if not isinstance(jira_base_url, str):
        raise ConfigError("jira_base_url must be a string")

    github_repos = data.get("github_repos", [])
    if not isinstance(github_repos, list):
        raise ConfigError("github_repos must be a list")
    for repo in github_repos:
        if not isinstance(repo, str) or "/" not in repo:
            raise ConfigError(f"Invalid repo format: {repo!r} (expected 'owner/repo')")

    refresh_interval = data.get("refresh_interval", 300)
    if not isinstance(refresh_interval, int) or refresh_interval < 1:
        raise ConfigError("refresh_interval must be a positive integer")

    github_username = data.get("github_username", "")
    if not isinstance(github_username, str):
        raise ConfigError("github_username must be a string")

    acc_workflow_name = data.get("acc_workflow_name", "Reisbalans deploy to cloud acceptance")
    if not isinstance(acc_workflow_name, str):
        raise ConfigError("acc_workflow_name must be a string")

    acc_retention_days = data.get("acc_retention_days", 2)
    if not isinstance(acc_retention_days, int) or acc_retention_days < 0:
        raise ConfigError("acc_retention_days must be a non-negative integer")

    acc_cooldown_minutes = data.get("acc_cooldown_minutes", 20)
    if not isinstance(acc_cooldown_minutes, int) or acc_cooldown_minutes < 0:
        raise ConfigError("acc_cooldown_minutes must be a non-negative integer")

    return Config(
        jira_base_url=jira_base_url.rstrip("/"),
        github_repos=github_repos,
        refresh_interval=refresh_interval,
        github_username=github_username,
        acc_workflow_name=acc_workflow_name,
        acc_retention_days=acc_retention_days,
        acc_cooldown_minutes=acc_cooldown_minutes,
    )


def create_default_config(path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Create a default configuration file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(DEFAULT_CONFIG, default_flow_style=False))
