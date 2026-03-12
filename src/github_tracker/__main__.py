"""Entry point for github-tracker CLI."""

from __future__ import annotations

import argparse
import importlib.metadata
import logging
import sys

from github_tracker.app import GitHubTrackerApp
from github_tracker.config import DEFAULT_CONFIG_PATH, ConfigError, load_config
from github_tracker.github_client import GitHubAuthError, GitHubClient, get_gh_token
from github_tracker.logging_config import setup_logging

logger = logging.getLogger("github_tracker.main")


def main() -> None:
    """Run the GitHub PR Tracker application."""
    version = importlib.metadata.version("github-tracker")
    parser = argparse.ArgumentParser(description="GitHub PR tracker TUI")
    parser.add_argument("--version", action="version", version=f"github-tracker {version}")
    parser.parse_args()

    setup_logging()

    if not DEFAULT_CONFIG_PATH.exists():
        from github_tracker.setup_wizard import SetupWizard
        completed = SetupWizard().run()
        if not completed:
            sys.exit(0)

    logger.info("Starting GitHub PR Tracker")

    try:
        config = load_config()
    except ConfigError as e:
        logger.error("Configuration error: %s", e)
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    logger.info(
        "Config loaded: repos=%s, jira_base_url=%r, refresh_interval=%d",
        config.github_repos,
        config.jira_base_url,
        config.refresh_interval,
    )

    try:
        token = get_gh_token()
    except GitHubAuthError as e:
        logger.error("Authentication error: %s", e)
        print(f"Authentication error: {e}", file=sys.stderr)
        sys.exit(1)

    logger.info("GitHub authentication successful (token length: %d)", len(token))

    client = GitHubClient(token=token)
    app = GitHubTrackerApp(config=config, github_client=client)

    logger.info("Launching TUI app")
    try:
        app.run()
    except Exception:
        logger.exception("App crashed with unhandled exception")
        raise
    logger.info("App exited")


if __name__ == "__main__":
    main()
