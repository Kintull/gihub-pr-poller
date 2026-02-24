"""Tests for the StatusBar widget."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from github_tracker.widgets.status_bar import StatusBar


class StatusBarTestApp(App):
    def compose(self) -> ComposeResult:
        yield StatusBar()


class TestStatusBar:
    @pytest.mark.asyncio
    async def test_hints_displayed(self):
        async with StatusBarTestApp().run_test() as pilot:
            bar = pilot.app.query_one(StatusBar)
            assert bar is not None

    def test_hints_constant_contains_navigate(self):
        assert "Navigate" in StatusBar.HINTS

    def test_hints_constant_contains_open_pr(self):
        assert "Open PR" in StatusBar.HINTS

    def test_hints_constant_contains_jira(self):
        assert "Jira" in StatusBar.HINTS

    def test_hints_constant_contains_refresh(self):
        assert "Refresh" in StatusBar.HINTS

    def test_hints_constant_contains_quit(self):
        assert "Quit" in StatusBar.HINTS

    def test_hints_constant_contains_help(self):
        assert "Help" in StatusBar.HINTS
