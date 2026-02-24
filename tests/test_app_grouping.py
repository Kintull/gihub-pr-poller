"""Tests for PR grouping, dual tables, label flow, and table navigation."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from github_tracker.app import GitHubTrackerApp
from github_tracker.config import Config
from github_tracker.github_client import GitHubClient
from github_tracker.models import CIStatus, DeployStatus, PRLabel, PullRequest
from github_tracker.widgets.pr_table import PRTable
from textual.widgets import Static
from tests.conftest import make_pr, make_github_pr_response, make_review_response


def make_mock_client(raw_prs: list[dict] | None = None) -> GitHubClient:
    client = MagicMock(spec=GitHubClient)
    client.fetch_open_prs = AsyncMock(return_value=raw_prs or [])
    client.parse_pr_basic = MagicMock(side_effect=lambda raw, repo, jira: _make_pr_from_raw(raw, repo))
    client.fetch_reviews = AsyncMock(return_value=[])
    client.fetch_check_runs = AsyncMock(return_value=[])
    client.fetch_pr_detail = AsyncMock(return_value={"comments": 0, "review_comments": 0})
    client.fetch_workflow_runs = AsyncMock(return_value=[])
    return client


def _make_pr_from_raw(raw: dict, repo: str) -> PullRequest:
    return make_pr(
        number=raw["number"],
        title=raw["title"],
        url=raw["html_url"],
        branch_name=raw["head"]["ref"],
        author=raw["user"]["login"],
        repo=repo,
        ci_status=CIStatus.PENDING,
        approval_count=0,
    )


def make_config(**overrides) -> Config:
    defaults = {
        "jira_base_url": "",
        "github_repos": ["owner/repo"],
        "refresh_interval": 300,
        "github_username": "",
    }
    defaults.update(overrides)
    return Config(**defaults)


@pytest.fixture(autouse=True)
def _patch_state():
    with patch("github_tracker.app.load_state", return_value=([], [])):
        with patch("github_tracker.app.save_state"):
            yield


class TestGroupedDisplay:
    @pytest.mark.asyncio
    async def test_no_username_all_in_other(self):
        """Without github_username, all PRs go to 'Other PRs'."""
        raw = [make_github_pr_response(number=1), make_github_pr_response(number=2)]
        client = make_mock_client(raw_prs=raw)
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            my_table = pilot.app.query_one("#my-pr-table", PRTable)
            other_table = pilot.app.query_one("#other-pr-table", PRTable)
            assert my_table.row_count == 0
            assert other_table.row_count == 2
            # My section should be hidden
            my_label = pilot.app.query_one("#my-prs-label", Static)
            assert my_label.display is False

    @pytest.mark.asyncio
    async def test_author_goes_to_my_prs(self):
        """PRs authored by the user go to 'My PRs'."""
        raw = [
            make_github_pr_response(number=1, user={"login": "alice"}),
            make_github_pr_response(number=2, user={"login": "bob"}),
        ]
        client = make_mock_client(raw_prs=raw)
        config = make_config(github_username="alice")
        app = GitHubTrackerApp(config=config, github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            my_table = pilot.app.query_one("#my-pr-table", PRTable)
            other_table = pilot.app.query_one("#other-pr-table", PRTable)
            assert my_table.row_count == 1
            assert my_table.pull_requests[0].number == 1
            assert other_table.row_count == 1
            assert other_table.pull_requests[0].number == 2

    @pytest.mark.asyncio
    async def test_review_requested_goes_to_my_prs(self):
        """PRs where user is a requested reviewer go to 'My PRs'."""
        raw = [
            make_github_pr_response(
                number=1,
                user={"login": "bob"},
                requested_reviewers=[{"login": "alice"}],
            ),
        ]
        client = make_mock_client(raw_prs=raw)
        config = make_config(github_username="alice")
        app = GitHubTrackerApp(config=config, github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            my_table = pilot.app.query_one("#my-pr-table", PRTable)
            assert my_table.row_count == 1

    @pytest.mark.asyncio
    async def test_mentioned_goes_to_my_prs(self):
        """PRs where user is mentioned in body go to 'My PRs'."""
        raw = [
            make_github_pr_response(
                number=1,
                user={"login": "bob"},
                body="cc @alice for review",
            ),
        ]
        client = make_mock_client(raw_prs=raw)
        config = make_config(github_username="alice")
        app = GitHubTrackerApp(config=config, github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            my_table = pilot.app.query_one("#my-pr-table", PRTable)
            assert my_table.row_count == 1

    @pytest.mark.asyncio
    async def test_empty_sections_hidden(self):
        """When a group has no PRs, its section label and table are hidden."""
        raw = [make_github_pr_response(number=1, user={"login": "alice"})]
        client = make_mock_client(raw_prs=raw)
        config = make_config(github_username="alice")
        app = GitHubTrackerApp(config=config, github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            # All PRs are mine, so Other section should be hidden
            other_label = pilot.app.query_one("#other-prs-label", Static)
            other_table = pilot.app.query_one("#other-pr-table", PRTable)
            assert other_label.display is False
            assert other_table.display is False
            # My section should be visible
            my_label = pilot.app.query_one("#my-prs-label", Static)
            my_table = pilot.app.query_one("#my-pr-table", PRTable)
            assert my_label.display is True
            assert my_table.display is True

    @pytest.mark.asyncio
    async def test_both_sections_visible(self):
        """When both groups have PRs, both sections are visible."""
        raw = [
            make_github_pr_response(number=1, user={"login": "alice"}),
            make_github_pr_response(number=2, user={"login": "bob"}),
        ]
        client = make_mock_client(raw_prs=raw)
        config = make_config(github_username="alice")
        app = GitHubTrackerApp(config=config, github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            my_label = pilot.app.query_one("#my-prs-label", Static)
            other_label = pilot.app.query_one("#other-prs-label", Static)
            assert my_label.display is True
            assert other_label.display is True

    @pytest.mark.asyncio
    async def test_cached_prs_with_labels_grouped(self):
        """Cached PRs with labels are grouped correctly on mount."""
        cached = [
            make_pr(number=1, labels=frozenset({PRLabel.AUTHOR})),
            make_pr(number=2),
        ]
        with patch("github_tracker.app.load_state", return_value=(cached, [])):
            with patch("github_tracker.app.save_state"):
                app = GitHubTrackerApp(config=make_config(), github_client=None)
                async with app.run_test() as pilot:
                    my_table = pilot.app.query_one("#my-pr-table", PRTable)
                    other_table = pilot.app.query_one("#other-pr-table", PRTable)
                    assert my_table.row_count == 1
                    assert my_table.pull_requests[0].number == 1
                    assert other_table.row_count == 1
                    assert other_table.pull_requests[0].number == 2

    @pytest.mark.asyncio
    async def test_phase2_commented_moves_pr_to_my(self):
        """PR moves from Other to My when COMMENTED label is discovered in Phase 2."""
        raw = [make_github_pr_response(number=1, user={"login": "bob"})]
        client = make_mock_client(raw_prs=raw)
        # Phase 2: alice has a review on this PR
        client.fetch_reviews = AsyncMock(
            return_value=[make_review_response(state="COMMENTED", user="alice")]
        )
        config = make_config(github_username="alice")
        app = GitHubTrackerApp(config=config, github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            # After Phase 2 re-grouping, PR should be in My PRs
            my_table = pilot.app.query_one("#my-pr-table", PRTable)
            assert my_table.row_count == 1
            assert PRLabel.COMMENTED in my_table.pull_requests[0].labels


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_no_prs_both_tables_hidden(self):
        """When there are no PRs, empty message is shown and both tables are hidden."""
        client = make_mock_client(raw_prs=[])
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            empty_msg = pilot.app.query_one("#empty-message", Static)
            assert empty_msg.display is True
            my_table = pilot.app.query_one("#my-pr-table", PRTable)
            other_table = pilot.app.query_one("#other-pr-table", PRTable)
            assert my_table.display is False
            assert other_table.display is False

    @pytest.mark.asyncio
    async def test_open_pr_no_tables_visible(self):
        """Open PR action when no tables are visible notifies user."""
        open_url = MagicMock()
        app = GitHubTrackerApp(config=make_config(), github_client=None, open_url=open_url)
        async with app.run_test() as pilot:
            # Clear focus so _get_focused_table falls through
            pilot.app.set_focus(None)
            await pilot.pause()
            pilot.app.action_open_pr()
            await pilot.pause()
            open_url.assert_not_called()

    @pytest.mark.asyncio
    async def test_open_jira_no_tables_visible(self):
        """Open Jira action when no tables are visible notifies user."""
        open_url = MagicMock()
        app = GitHubTrackerApp(config=make_config(), github_client=None, open_url=open_url)
        async with app.run_test() as pilot:
            pilot.app.set_focus(None)
            await pilot.pause()
            pilot.app.action_open_jira()
            await pilot.pause()
            open_url.assert_not_called()

    @pytest.mark.asyncio
    async def test_cursor_navigation_no_tables_visible(self):
        """Cursor navigation when no tables are visible does nothing."""
        app = GitHubTrackerApp(config=make_config(), github_client=None)
        async with app.run_test() as pilot:
            pilot.app.set_focus(None)
            await pilot.pause()
            pilot.app.action_cursor_up()
            pilot.app.action_cursor_down()
            await pilot.pause()

    @pytest.mark.asyncio
    async def test_get_focused_table_returns_none_when_all_hidden(self):
        """_get_focused_table returns None when both tables are hidden and unfocused."""
        app = GitHubTrackerApp(config=make_config(), github_client=None)
        async with app.run_test() as pilot:
            pilot.app.set_focus(None)
            await pilot.pause()
            assert pilot.app._get_focused_table() is None

    @pytest.mark.asyncio
    async def test_get_focused_table_fallback_my_visible(self):
        """_get_focused_table falls back to my_table when it's visible but unfocused."""
        cached = [make_pr(number=1, labels=frozenset({PRLabel.AUTHOR}))]
        with patch("github_tracker.app.load_state", return_value=(cached, [])):
            with patch("github_tracker.app.save_state"):
                app = GitHubTrackerApp(config=make_config(), github_client=None)
                async with app.run_test() as pilot:
                    my_table = pilot.app.query_one("#my-pr-table", PRTable)
                    pilot.app.set_focus(None)
                    await pilot.pause()
                    assert not my_table.has_focus
                    table = pilot.app._get_focused_table()
                    assert table is my_table

    @pytest.mark.asyncio
    async def test_get_focused_table_fallback_other_visible(self):
        """_get_focused_table falls back to other_table when only it's visible."""
        cached = [make_pr(number=1)]
        with patch("github_tracker.app.load_state", return_value=(cached, [])):
            with patch("github_tracker.app.save_state"):
                app = GitHubTrackerApp(config=make_config(), github_client=None)
                async with app.run_test() as pilot:
                    other_table = pilot.app.query_one("#other-pr-table", PRTable)
                    pilot.app.set_focus(None)
                    await pilot.pause()
                    assert not other_table.has_focus
                    table = pilot.app._get_focused_table()
                    assert table is other_table

    @pytest.mark.asyncio
    async def test_find_pr_in_tables_not_found(self):
        """_find_pr_in_tables returns None for unknown PR number."""
        raw = [make_github_pr_response(number=1)]
        client = make_mock_client(raw_prs=raw)
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            assert pilot.app._find_pr_in_tables(9999) is None

    @pytest.mark.asyncio
    async def test_phase2_skips_pr_not_in_tables(self):
        """Phase 2 continues gracefully if a PR can't be found in tables."""
        raw = [make_github_pr_response(number=1), make_github_pr_response(number=2)]
        client = make_mock_client(raw_prs=raw)

        # After Phase 1 loads PRs but before Phase 2 starts,
        # we clear tables to simulate the PR being missing.
        original_find = GitHubTrackerApp._find_pr_in_tables

        call_count = [0]

        def _find_none_then_real(self, pr_number):
            call_count[0] += 1
            if call_count[0] == 1:
                return None  # First PR not found
            return original_find(self, pr_number)

        with patch.object(GitHubTrackerApp, "_find_pr_in_tables", _find_none_then_real):
            app = GitHubTrackerApp(config=make_config(), github_client=client)
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.app.workers.wait_for_complete()
                await pilot.pause()
                # Should complete without error
                assert call_count[0] == 2


class TestTableNavigation:
    @pytest.mark.asyncio
    async def test_tab_switches_focus(self):
        """Tab key cycles focus between the two tables."""
        raw = [
            make_github_pr_response(number=1, user={"login": "alice"}),
            make_github_pr_response(number=2, user={"login": "bob"}),
        ]
        client = make_mock_client(raw_prs=raw)
        config = make_config(github_username="alice")
        app = GitHubTrackerApp(config=config, github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            my_table = pilot.app.query_one("#my-pr-table", PRTable)
            other_table = pilot.app.query_one("#other-pr-table", PRTable)
            # My table should initially have focus
            assert my_table.has_focus
            await pilot.press("tab")
            await pilot.pause()
            assert other_table.has_focus
            await pilot.press("tab")
            await pilot.pause()
            assert my_table.has_focus

    @pytest.mark.asyncio
    async def test_tab_noop_single_visible_table(self):
        """Tab does nothing when only one table is visible."""
        raw = [make_github_pr_response(number=1)]
        client = make_mock_client(raw_prs=raw)
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            other_table = pilot.app.query_one("#other-pr-table", PRTable)
            assert other_table.has_focus
            await pilot.press("tab")
            await pilot.pause()
            # Focus should still be on other since my is hidden
            assert other_table.has_focus

    @pytest.mark.asyncio
    async def test_k_j_routes_to_focused_table(self):
        """k/j navigate in the currently focused table."""
        raw = [
            make_github_pr_response(number=1, user={"login": "alice"}),
            make_github_pr_response(number=2, user={"login": "alice"}),
            make_github_pr_response(number=3, user={"login": "bob"}),
        ]
        client = make_mock_client(raw_prs=raw)
        config = make_config(github_username="alice")
        app = GitHubTrackerApp(config=config, github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            my_table = pilot.app.query_one("#my-pr-table", PRTable)
            assert my_table.has_focus
            assert my_table.cursor_row == 0
            await pilot.press("j")
            await pilot.pause()
            assert my_table.cursor_row == 1

    @pytest.mark.asyncio
    async def test_open_pr_uses_focused_table(self):
        """Open PR action uses the currently focused table."""
        raw = [
            make_github_pr_response(number=1, user={"login": "alice"}, html_url="https://github.com/o/r/pull/1"),
            make_github_pr_response(number=2, user={"login": "bob"}, html_url="https://github.com/o/r/pull/2"),
        ]
        client = make_mock_client(raw_prs=raw)
        config = make_config(github_username="alice")
        open_url = MagicMock()
        app = GitHubTrackerApp(config=config, github_client=client, open_url=open_url)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            # Focus is on my_table (PR #1)
            await pilot.press("o")
            await pilot.pause()
            open_url.assert_called_with("https://github.com/o/r/pull/1")
            open_url.reset_mock()
            # Switch to other table
            await pilot.press("tab")
            await pilot.pause()
            await pilot.press("o")
            await pilot.pause()
            open_url.assert_called_with("https://github.com/o/r/pull/2")
