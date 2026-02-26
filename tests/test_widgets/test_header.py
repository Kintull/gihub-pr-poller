"""Tests for the TrackerHeader widget."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from github_tracker.widgets.header import (
    LOGO_HEIGHT,
    LOGO_LINES,
    LOGO_WIDTH,
    VERSION,
    TrackerHeader,
    build_banner,
)


class HeaderTestApp(App):
    def __init__(self, repos=None, jira_base_url="", **kwargs):
        super().__init__(**kwargs)
        self._repos = repos or []
        self._jira_base_url = jira_base_url

    def compose(self) -> ComposeResult:
        yield TrackerHeader(repos=self._repos, jira_base_url=self._jira_base_url)


class TestBuildBanner:
    def test_with_repos_and_jira(self):
        banner = build_banner(
            repos=["owner/repo1", "owner/repo2"],
            jira_base_url="https://jira.example.com/browse",
            status="5 PRs",
        )
        assert "GitHub PR Tracker" in banner
        assert VERSION in banner
        assert "owner/repo1" in banner
        assert "owner/repo2" in banner
        assert "jira.example.com" in banner
        assert "5 PRs" in banner

    def test_no_repos(self):
        banner = build_banner(repos=[], jira_base_url="", status="")
        assert "no repos configured" in banner

    def test_no_jira(self):
        banner = build_banner(
            repos=["owner/repo"], jira_base_url="", status=""
        )
        assert "not configured" in banner

    def test_no_status(self):
        banner = build_banner(repos=["o/r"], jira_base_url="", status="")
        assert "GitHub PR Tracker" in banner

    def test_refresh_info_shown(self):
        banner = build_banner(repos=["o/r"], jira_base_url="", status="", refresh_info="My PRs: 1 min | All: 5 min")
        assert "Refresh" in banner
        assert "1 min" in banner

    def test_no_refresh_info_omitted(self):
        banner = build_banner(repos=["o/r"], jira_base_url="", status="", refresh_info="")
        assert "Refresh" not in banner

    def test_contains_logo(self):
        banner = build_banner(repos=[], jira_base_url="", status="")
        for line in LOGO_LINES:
            assert line.strip() in banner

    def test_has_separator(self):
        banner = build_banner(repos=["o/r"], jira_base_url="", status="")
        assert " │ " in banner


class TestLogoConstants:
    def test_logo_height(self):
        assert LOGO_HEIGHT == 3

    def test_logo_width(self):
        assert LOGO_WIDTH > 0

    def test_logo_lines_nonempty(self):
        assert all(line.strip() for line in LOGO_LINES)


class TestTrackerHeader:
    @pytest.mark.asyncio
    async def test_default_no_repos(self):
        async with HeaderTestApp().run_test() as pilot:
            header = pilot.app.query_one(TrackerHeader)
            assert header._repos == []

    @pytest.mark.asyncio
    async def test_with_repos(self):
        async with HeaderTestApp(repos=["owner/repo"]).run_test() as pilot:
            header = pilot.app.query_one(TrackerHeader)
            assert header._repos == ["owner/repo"]

    @pytest.mark.asyncio
    async def test_with_jira(self):
        async with HeaderTestApp(
            jira_base_url="https://jira.test.com/browse"
        ).run_test() as pilot:
            header = pilot.app.query_one(TrackerHeader)
            assert header._jira_base_url == "https://jira.test.com/browse"

    @pytest.mark.asyncio
    async def test_set_config_updates(self):
        async with HeaderTestApp().run_test() as pilot:
            header = pilot.app.query_one(TrackerHeader)
            header.set_config(
                repos=["new/repo"],
                jira_base_url="https://jira.new.com",
            )
            await pilot.pause()
            assert header._repos == ["new/repo"]
            assert header._jira_base_url == "https://jira.new.com"
            assert header.repo_name == "new/repo"
            assert header.jira_url == "https://jira.new.com"

    @pytest.mark.asyncio
    async def test_set_config_no_repos(self):
        async with HeaderTestApp().run_test() as pilot:
            header = pilot.app.query_one(TrackerHeader)
            header.set_config(repos=[], jira_base_url="")
            await pilot.pause()
            assert header.repo_name == ""

    @pytest.mark.asyncio
    async def test_status_text_updates_banner(self):
        async with HeaderTestApp(repos=["o/r"]).run_test() as pilot:
            header = pilot.app.query_one(TrackerHeader)
            header.status_text = "Loading..."
            await pilot.pause()
            assert header.status_text == "Loading..."

    @pytest.mark.asyncio
    async def test_rebuild_banner_no_content_widget(self):
        """Test that _rebuild_banner handles missing content widget."""
        async with HeaderTestApp().run_test() as pilot:
            header = pilot.app.query_one(TrackerHeader)
            content = header.query_one("#banner-content", Static)
            content.remove()
            await pilot.pause()
            # Should not raise
            header._rebuild_banner()
