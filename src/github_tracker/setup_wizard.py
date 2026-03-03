"""First-run configuration wizard."""

from __future__ import annotations

from pathlib import Path

import yaml
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Input, Label, Static

from github_tracker.config import DEFAULT_CONFIG_PATH


class SetupWizard(App[bool]):
    """Interactive first-run wizard to create .github-tracker.yaml."""

    CSS = """
    SetupWizard {
        align: center middle;
    }

    #wizard {
        width: 72;
        height: auto;
        border: double $primary;
        padding: 1 2;
        background: $surface;
    }

    #title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
        color: $primary;
    }

    .label {
        margin-top: 1;
    }

    #error {
        color: $error;
        height: 1;
    }

    #save-btn {
        margin-top: 1;
        width: 100%;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, config_path: Path = DEFAULT_CONFIG_PATH, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config_path = config_path

    def compose(self) -> ComposeResult:
        with Vertical(id="wizard"):
            yield Label("GitHub Tracker — First Run Setup", id="title")
            yield Static("", id="error")
            yield Label("GitHub username", classes="label")
            yield Input(placeholder="your-github-username", id="username")
            yield Label("Repositories (comma-separated, format: owner/repo)", classes="label")
            yield Input(placeholder="myorg/repo1, myorg/repo2", id="repos")
            yield Label("Jira base URL (optional)", classes="label")
            yield Input(placeholder="https://company.atlassian.net", id="jira-url")
            yield Label("Refresh interval in seconds (default: 300)", classes="label")
            yield Input(value="300", id="refresh")
            yield Button("Save & Launch", variant="primary", id="save-btn")

    def action_cancel(self) -> None:
        """Exit wizard without saving."""
        self.exit(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self._save()

    def _save(self) -> None:
        username = self.query_one("#username", Input).value.strip()
        repos_raw = self.query_one("#repos", Input).value.strip()
        jira_url = self.query_one("#jira-url", Input).value.strip()
        refresh_raw = self.query_one("#refresh", Input).value.strip()

        error = self.query_one("#error", Static)

        if not username:
            error.update("GitHub username is required.")
            return

        repos = [r.strip() for r in repos_raw.split(",") if r.strip()]
        if not repos:
            error.update("At least one repository is required (format: owner/repo).")
            return

        for repo in repos:
            if "/" not in repo:
                error.update(f"Invalid repo format: {repo!r} — expected owner/repo.")
                return

        try:
            refresh_interval = int(refresh_raw) if refresh_raw else 300
            if refresh_interval < 1:
                raise ValueError("non-positive")
        except ValueError:
            error.update("Refresh interval must be a positive integer.")
            return

        config = {
            "github_username": username,
            "github_repos": repos,
            "jira_base_url": jira_url,
            "refresh_interval": refresh_interval,
            "acc_workflow_name": "Reisbalans deploy to cloud acceptance",
            "acc_retention_days": 2,
            "acc_cooldown_minutes": 20,
        }

        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(yaml.dump(config, default_flow_style=False))
        self.exit(True)
