"""Tests for the first-run setup wizard."""

from __future__ import annotations

import pytest
import yaml
from textual.widgets import Button, Input, Static

from github_tracker.setup_wizard import SetupWizard

# Terminal size large enough to render all form fields + button
_SIZE = (120, 60)


@pytest.mark.asyncio
async def test_wizard_renders(tmp_path):
    config_path = tmp_path / "config.yaml"
    async with SetupWizard(config_path=config_path).run_test(size=_SIZE) as pilot:
        assert pilot.app.query_one("#username", Input) is not None
        assert pilot.app.query_one("#repos", Input) is not None
        assert pilot.app.query_one("#jira-url", Input) is not None
        assert pilot.app.query_one("#refresh", Input) is not None
        assert pilot.app.query_one("#save-btn", Button) is not None


@pytest.mark.asyncio
async def test_save_valid(tmp_path):
    config_path = tmp_path / "config.yaml"
    wizard = SetupWizard(config_path=config_path)
    async with wizard.run_test(size=_SIZE) as pilot:
        wizard.query_one("#username", Input).value = "alice"
        wizard.query_one("#repos", Input).value = "org/repo1, org/repo2"
        wizard.query_one("#jira-url", Input).value = "https://jira.example.com"
        wizard.query_one("#refresh", Input).value = "60"
        await pilot.click("#save-btn")

    assert wizard.return_value is True
    assert config_path.exists()
    cfg = yaml.safe_load(config_path.read_text())
    assert cfg["github_username"] == "alice"
    assert cfg["github_repos"] == ["org/repo1", "org/repo2"]
    assert cfg["jira_base_url"] == "https://jira.example.com"
    assert cfg["refresh_interval"] == 60
    assert cfg["acc_workflow_name"] == "Reisbalans deploy to cloud acceptance"
    assert cfg["acc_retention_days"] == 2
    assert cfg["acc_cooldown_minutes"] == 20


@pytest.mark.asyncio
async def test_save_empty_refresh_uses_default(tmp_path):
    config_path = tmp_path / "config.yaml"
    wizard = SetupWizard(config_path=config_path)
    async with wizard.run_test(size=_SIZE) as pilot:
        wizard.query_one("#username", Input).value = "alice"
        wizard.query_one("#repos", Input).value = "org/repo"
        wizard.query_one("#refresh", Input).value = ""
        await pilot.click("#save-btn")

    cfg = yaml.safe_load(config_path.read_text())
    assert cfg["refresh_interval"] == 300


@pytest.mark.asyncio
async def test_save_error_missing_username(tmp_path):
    config_path = tmp_path / "config.yaml"
    wizard = SetupWizard(config_path=config_path)
    async with wizard.run_test(size=_SIZE) as pilot:
        wizard._save()
        await pilot.pause()
        error = wizard.query_one("#error", Static)
        assert "username" in str(error.content).lower()
    assert not config_path.exists()


@pytest.mark.asyncio
async def test_save_error_missing_repos(tmp_path):
    config_path = tmp_path / "config.yaml"
    wizard = SetupWizard(config_path=config_path)
    async with wizard.run_test(size=_SIZE) as pilot:
        wizard.query_one("#username", Input).value = "alice"
        wizard._save()
        await pilot.pause()
        error = wizard.query_one("#error", Static)
        assert "repository" in str(error.content).lower()
    assert not config_path.exists()


@pytest.mark.asyncio
async def test_save_error_invalid_repo_format(tmp_path):
    config_path = tmp_path / "config.yaml"
    wizard = SetupWizard(config_path=config_path)
    async with wizard.run_test(size=_SIZE) as pilot:
        wizard.query_one("#username", Input).value = "alice"
        wizard.query_one("#repos", Input).value = "invalid-repo"
        wizard._save()
        await pilot.pause()
        error = wizard.query_one("#error", Static)
        assert "invalid" in str(error.content).lower()
    assert not config_path.exists()


@pytest.mark.asyncio
async def test_save_error_invalid_refresh(tmp_path):
    config_path = tmp_path / "config.yaml"
    wizard = SetupWizard(config_path=config_path)
    async with wizard.run_test(size=_SIZE) as pilot:
        wizard.query_one("#username", Input).value = "alice"
        wizard.query_one("#repos", Input).value = "org/repo"
        wizard.query_one("#refresh", Input).value = "abc"
        wizard._save()
        await pilot.pause()
        error = wizard.query_one("#error", Static)
        assert "integer" in str(error.content).lower()
    assert not config_path.exists()


@pytest.mark.asyncio
async def test_save_error_zero_refresh(tmp_path):
    config_path = tmp_path / "config.yaml"
    wizard = SetupWizard(config_path=config_path)
    async with wizard.run_test(size=_SIZE) as pilot:
        wizard.query_one("#username", Input).value = "alice"
        wizard.query_one("#repos", Input).value = "org/repo"
        wizard.query_one("#refresh", Input).value = "0"
        wizard._save()
        await pilot.pause()
        error = wizard.query_one("#error", Static)
        assert "integer" in str(error.content).lower()
    assert not config_path.exists()


@pytest.mark.asyncio
async def test_escape_cancels(tmp_path):
    config_path = tmp_path / "config.yaml"
    wizard = SetupWizard(config_path=config_path)
    async with wizard.run_test(size=_SIZE) as pilot:
        await pilot.press("escape")

    assert wizard.return_value is False
    assert not config_path.exists()
