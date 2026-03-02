"""Tests for the main GitHubTrackerApp."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from github_tracker.app import GitHubTrackerApp, HelpOverlay
from github_tracker.config import Config
from github_tracker.github_client import GitHubClient
from github_tracker.models import CIStatus, DeployStatus, PRLabel, PullRequest
from github_tracker.widgets.header import TrackerHeader
from github_tracker.widgets.pr_table import PRTable
from github_tracker.widgets.status_bar import StatusBar
from tests.conftest import make_pr, make_github_pr_response, make_review_response, make_check_run_response, make_workflow_run_response


def make_mock_client(prs: list[PullRequest] | None = None, raw_prs: list[dict] | None = None) -> GitHubClient:
    """Create a mock GitHubClient that supports progressive loading."""
    client = MagicMock(spec=GitHubClient)
    client.fetch_pull_requests = AsyncMock(return_value=prs or [])
    client.fetch_open_prs = AsyncMock(return_value=raw_prs or [])
    client.parse_pr_basic = MagicMock(side_effect=lambda raw, repo, jira: _make_pr_from_raw(raw, repo))
    client.fetch_reviews = AsyncMock(return_value=[])
    client.fetch_check_runs = AsyncMock(return_value=[])
    client.fetch_pr_detail = AsyncMock(return_value={"head": {"sha": "abc123"}, "comments": 0, "review_comments": 0})
    client.fetch_workflow_runs = AsyncMock(return_value=[])
    client.fetch_workflow_run_jobs = AsyncMock(return_value=[])
    client.fetch_review_threads = AsyncMock(return_value=[])
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
        "jira_base_url": "https://jira.example.com/browse",
        "github_repos": ["owner/repo"],
        "refresh_interval": 300,
        "github_username": "",
    }
    defaults.update(overrides)
    return Config(**defaults)


def _get_other_table(app) -> PRTable:
    """Get the 'other' PR table (where PRs go when no github_username is set)."""
    return app.query_one("#other-pr-table", PRTable)


def _get_total_row_count(app) -> int:
    """Get total row count across both tables."""
    my = app.query_one("#my-pr-table", PRTable).row_count
    other = app.query_one("#other-pr-table", PRTable).row_count
    return my + other


@pytest.fixture(autouse=True)
def _patch_state():
    """Prevent tests from reading/writing real state files."""
    with patch("github_tracker.app.load_state", return_value=([], [])):
        with patch("github_tracker.app.save_state"):
            yield


class TestGitHubTrackerApp:
    @pytest.mark.asyncio
    async def test_app_starts(self):
        app = GitHubTrackerApp(config=make_config(), github_client=None)
        async with app.run_test() as pilot:
            assert pilot.app.title == "GitHub PR Tracker"

    @pytest.mark.asyncio
    async def test_header_shows_repo(self):
        app = GitHubTrackerApp(config=make_config(), github_client=None)
        async with app.run_test() as pilot:
            header = pilot.app.query_one(TrackerHeader)
            assert "owner/repo" in header._repos

    @pytest.mark.asyncio
    async def test_header_no_repos(self):
        config = make_config(github_repos=[])
        app = GitHubTrackerApp(config=config, github_client=None)
        async with app.run_test() as pilot:
            header = pilot.app.query_one(TrackerHeader)
            assert header._repos == []

    @pytest.mark.asyncio
    async def test_header_multiple_repos(self):
        config = make_config(github_repos=["owner/repo1", "owner/repo2"])
        app = GitHubTrackerApp(config=config, github_client=None)
        async with app.run_test() as pilot:
            header = pilot.app.query_one(TrackerHeader)
            assert "owner/repo1" in header._repos
            assert "owner/repo2" in header._repos

    @pytest.mark.asyncio
    async def test_status_bar_present(self):
        app = GitHubTrackerApp(config=make_config(), github_client=None)
        async with app.run_test() as pilot:
            bar = pilot.app.query_one(StatusBar)
            assert bar is not None

    @pytest.mark.asyncio
    async def test_loads_prs_progressively(self):
        raw = [make_github_pr_response(number=1)]
        client = make_mock_client(raw_prs=raw)
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            table = _get_other_table(pilot.app)
            assert table.row_count == 1

    @pytest.mark.asyncio
    async def test_empty_state_message(self):
        client = make_mock_client(raw_prs=[])
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            assert _get_total_row_count(pilot.app) == 0

    @pytest.mark.asyncio
    async def test_refresh_keybinding_updates_ci_in_focused_table(self):
        """Pressing r refreshes CI status of PRs in the focused table."""
        raw = [make_github_pr_response(number=1)]
        client = make_mock_client(raw_prs=raw)
        client.fetch_check_runs = AsyncMock(return_value=[make_check_run_response("in_progress", None)])
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            # On refresh, CI becomes SUCCESS
            client.fetch_check_runs.return_value = [make_check_run_response("completed", "success")]
            await pilot.press("r")
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            table = _get_other_table(pilot.app)
            assert table.pull_requests[0].ci_status == CIStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_refresh_no_client(self):
        app = GitHubTrackerApp(config=make_config(), github_client=None)
        async with app.run_test() as pilot:
            await pilot.press("r")
            await pilot.pause()

    @pytest.mark.asyncio
    async def test_open_pr(self):
        raw = [make_github_pr_response(number=1, html_url="https://github.com/o/r/pull/1")]
        client = make_mock_client(raw_prs=raw)
        open_url = MagicMock()
        app = GitHubTrackerApp(
            config=make_config(), github_client=client, open_url=open_url
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            open_url.assert_called_once_with("https://github.com/o/r/pull/1")

    @pytest.mark.asyncio
    async def test_open_pr_with_o(self):
        raw = [make_github_pr_response(number=1, html_url="https://github.com/o/r/pull/1")]
        client = make_mock_client(raw_prs=raw)
        open_url = MagicMock()
        app = GitHubTrackerApp(
            config=make_config(), github_client=client, open_url=open_url
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("o")
            await pilot.pause()
            open_url.assert_called_once_with("https://github.com/o/r/pull/1")

    @pytest.mark.asyncio
    async def test_open_pr_no_selection_via_o(self):
        client = make_mock_client(raw_prs=[])
        open_url = MagicMock()
        app = GitHubTrackerApp(
            config=make_config(), github_client=client, open_url=open_url
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("o")
            await pilot.pause()
            open_url.assert_not_called()

    @pytest.mark.asyncio
    async def test_open_jira(self):
        raw = [make_github_pr_response(number=1)]
        client = make_mock_client(raw_prs=raw)
        # Override parse_pr_basic to return a PR with jira_url
        client.parse_pr_basic = MagicMock(
            return_value=make_pr(
                number=1,
                jira_ticket="PROJ-123",
                jira_url="https://jira.example.com/browse/PROJ-123",
            )
        )
        open_url = MagicMock()
        app = GitHubTrackerApp(
            config=make_config(), github_client=client, open_url=open_url
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("J")
            await pilot.pause()
            open_url.assert_called_once_with(
                "https://jira.example.com/browse/PROJ-123"
            )

    @pytest.mark.asyncio
    async def test_open_jira_no_ticket(self):
        raw = [make_github_pr_response(number=1)]
        client = make_mock_client(raw_prs=raw)
        open_url = MagicMock()
        app = GitHubTrackerApp(
            config=make_config(), github_client=client, open_url=open_url
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("J")
            await pilot.pause()
            open_url.assert_not_called()

    @pytest.mark.asyncio
    async def test_open_jira_no_selection(self):
        client = make_mock_client(raw_prs=[])
        open_url = MagicMock()
        app = GitHubTrackerApp(
            config=make_config(), github_client=client, open_url=open_url
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("J")
            await pilot.pause()
            open_url.assert_not_called()

    @pytest.mark.asyncio
    async def test_toggle_help(self):
        app = GitHubTrackerApp(config=make_config(), github_client=None)
        async with app.run_test() as pilot:
            overlay = pilot.app.query_one("#help-overlay", HelpOverlay)
            assert not pilot.app._help_visible

            await pilot.press("question_mark")
            await pilot.pause()
            assert pilot.app._help_visible
            assert overlay.display is True

            await pilot.press("question_mark")
            await pilot.pause()
            assert not pilot.app._help_visible

    @pytest.mark.asyncio
    async def test_cursor_navigation_k_j(self):
        raw = [
            make_github_pr_response(number=1),
            make_github_pr_response(number=2),
            make_github_pr_response(number=3),
        ]
        client = make_mock_client(raw_prs=raw)
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            table = _get_other_table(pilot.app)
            assert table.cursor_row == 0
            await pilot.press("j")
            await pilot.pause()
            assert table.cursor_row == 1
            await pilot.press("k")
            await pilot.pause()
            assert table.cursor_row == 0

    @pytest.mark.asyncio
    async def test_api_error_during_load(self):
        client = MagicMock(spec=GitHubClient)
        client.fetch_open_prs = AsyncMock(side_effect=Exception("Network error"))
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            assert _get_total_row_count(pilot.app) == 0

    @pytest.mark.asyncio
    async def test_details_error_continues(self):
        """When fetching details for one PR fails, others still load."""
        raw = [make_github_pr_response(number=1), make_github_pr_response(number=2)]
        client = make_mock_client(raw_prs=raw)
        # First call to fetch_reviews raises, second succeeds
        client.fetch_reviews = AsyncMock(
            side_effect=[Exception("timeout"), [make_review_response("APPROVED")]]
        )
        client.fetch_check_runs = AsyncMock(
            side_effect=[Exception("timeout"), [make_check_run_response("completed", "success")]]
        )
        client.fetch_pr_detail = AsyncMock(
            side_effect=[Exception("timeout"), {"comments": 3, "review_comments": 2}]
        )
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            assert _get_total_row_count(pilot.app) == 2

    @pytest.mark.asyncio
    async def test_auto_refresh(self):
        raw = [make_github_pr_response(number=1)]
        client = make_mock_client(raw_prs=raw)
        config = make_config(refresh_interval=1)
        app = GitHubTrackerApp(config=config, github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            raw2 = [make_github_pr_response(number=1), make_github_pr_response(number=2)]
            client.fetch_open_prs.return_value = raw2
            await GitHubTrackerApp._auto_refresh(pilot.app)
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            table = _get_other_table(pilot.app)
            assert table.row_count == 2

    @pytest.mark.asyncio
    async def test_tick_spinner_no_table(self):
        """Test that _tick_spinner handles missing table gracefully."""
        app = GitHubTrackerApp(config=make_config(), github_client=None)
        async with app.run_test() as pilot:
            my_table = pilot.app.query_one("#my-pr-table", PRTable)
            other_table = pilot.app.query_one("#other-pr-table", PRTable)
            my_table.remove()
            other_table.remove()
            await pilot.pause()
            pilot.app._tick_spinner()

    @pytest.mark.asyncio
    async def test_prs_sorted_by_updated_at(self):
        raw = [
            make_github_pr_response(number=1, updated_at="2024-01-01T00:00:00Z"),
            make_github_pr_response(number=2, updated_at="2024-06-01T00:00:00Z"),
            make_github_pr_response(number=3, updated_at="2024-03-01T00:00:00Z"),
        ]
        client = make_mock_client(raw_prs=raw)
        # Make parse_pr_basic use the updated_at from raw data
        def _parse(raw, repo, jira):
            from datetime import datetime, timezone
            updated = datetime.fromisoformat(raw["updated_at"].replace("Z", "+00:00"))
            return make_pr(
                number=raw["number"],
                title=raw["title"],
                url=raw["html_url"],
                author=raw["user"]["login"],
                branch_name=raw["head"]["ref"],
                updated_at=updated,
                repo=repo,
            )
        client.parse_pr_basic = MagicMock(side_effect=_parse)
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            table = _get_other_table(pilot.app)
            assert table.pull_requests[0].number == 2
            assert table.pull_requests[1].number == 3
            assert table.pull_requests[2].number == 1


class TestCachedState:
    @pytest.mark.asyncio
    async def test_cached_prs_displayed_on_mount(self):
        cached = [make_pr(number=10, title="Cached PR")]
        with patch("github_tracker.app.load_state", return_value=(cached, [])):
            with patch("github_tracker.app.save_state"):
                app = GitHubTrackerApp(config=make_config(), github_client=None)
                async with app.run_test() as pilot:
                    table = _get_other_table(pilot.app)
                    assert table.row_count == 1
                    assert table.pull_requests[0].number == 10

    @pytest.mark.asyncio
    async def test_cached_prs_replaced_by_fresh(self):
        cached = [make_pr(number=10, title="Cached")]
        raw = [make_github_pr_response(number=20)]
        client = make_mock_client(raw_prs=raw)
        with patch("github_tracker.app.load_state", return_value=(cached, [])):
            with patch("github_tracker.app.save_state"):
                app = GitHubTrackerApp(config=make_config(), github_client=client)
                async with app.run_test() as pilot:
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    table = _get_other_table(pilot.app)
                    assert table.row_count == 1
                    assert table.pull_requests[0].number == 20

    @pytest.mark.asyncio
    async def test_save_state_called_after_phase1(self):
        raw = [make_github_pr_response(number=1)]
        client = make_mock_client(raw_prs=raw)
        with patch("github_tracker.app.load_state", return_value=([], [])):
            with patch("github_tracker.app.save_state") as mock_save:
                app = GitHubTrackerApp(config=make_config(), github_client=client)
                async with app.run_test() as pilot:
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    # save_state is called twice: after Phase 1 and after Phase 2
                    assert mock_save.call_count >= 1
                    # First call should have the PR
                    saved_prs = mock_save.call_args_list[0][0][0]
                    assert len(saved_prs) == 1
                    assert saved_prs[0].number == 1

    @pytest.mark.asyncio
    async def test_empty_cache_no_crash(self):
        with patch("github_tracker.app.load_state", return_value=([], [])):
            with patch("github_tracker.app.save_state"):
                app = GitHubTrackerApp(config=make_config(), github_client=None)
                async with app.run_test() as pilot:
                    assert _get_total_row_count(pilot.app) == 0


class TestMergeDetection:
    @pytest.mark.asyncio
    async def test_merged_pr_detected_and_tracked(self):
        """When a PR disappears from open list and is merged, it appears with ACC deploying."""
        # Start with PR #1 open (cached)
        cached = [make_pr(number=1, title="My PR", repo="owner/repo")]
        # On refresh, PR #1 is gone from the open list
        client = make_mock_client(raw_prs=[])
        # fetch_pr_detail returns merged_at for PR #1
        client.fetch_pr_detail = AsyncMock(
            return_value={"merged_at": "2024-06-15T14:00:00Z", "comments": 0, "review_comments": 0}
        )
        with patch("github_tracker.app.load_state", return_value=(cached, [])):
            with patch("github_tracker.app.save_state") as mock_save:
                app = GitHubTrackerApp(config=make_config(), github_client=client)
                async with app.run_test() as pilot:
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    # The merged PR should appear in the display
                    assert len(app._merged_prs) == 1
                    assert app._merged_prs[0].number == 1
                    assert app._merged_prs[0].acc_deploy == DeployStatus.ACC_DEPLOYING

    @pytest.mark.asyncio
    async def test_merge_check_error_handled(self):
        """Error during merge check doesn't crash the app."""
        cached = [make_pr(number=1, title="My PR", repo="owner/repo")]
        client = make_mock_client(raw_prs=[])
        client.fetch_pr_detail = AsyncMock(side_effect=Exception("Network error"))
        with patch("github_tracker.app.load_state", return_value=(cached, [])):
            with patch("github_tracker.app.save_state"):
                app = GitHubTrackerApp(config=make_config(), github_client=client)
                async with app.run_test() as pilot:
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    # No merged PRs since check failed
                    assert len(app._merged_prs) == 0

    @pytest.mark.asyncio
    async def test_workflow_runs_fetched_for_merged_prs(self):
        """Workflow runs are fetched for repos with merged PRs."""
        from datetime import datetime, timezone as tz
        merged_at = datetime(2024, 6, 15, 14, 0, 0, tzinfo=tz.utc)
        merged = [make_pr(
            number=1,
            repo="owner/repo",
            merged_at=merged_at,
            acc_deploy=DeployStatus.ACC_DEPLOYING,
        )]
        client = make_mock_client(raw_prs=[])
        client.fetch_workflow_runs = AsyncMock(return_value=[])
        with patch("github_tracker.app.load_state", return_value=([], merged)):
            with patch("github_tracker.app.save_state"):
                app = GitHubTrackerApp(config=make_config(), github_client=client)
                async with app.run_test() as pilot:
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    client.fetch_workflow_runs.assert_called_once()

    @pytest.mark.asyncio
    async def test_workflow_runs_error_handled(self):
        """Error fetching workflow runs doesn't crash."""
        from datetime import datetime, timezone as tz
        merged_at = datetime(2024, 6, 15, 14, 0, 0, tzinfo=tz.utc)
        merged = [make_pr(
            number=1,
            repo="owner/repo",
            merged_at=merged_at,
            acc_deploy=DeployStatus.ACC_DEPLOYING,
        )]
        client = make_mock_client(raw_prs=[])
        client.fetch_workflow_runs = AsyncMock(side_effect=Exception("Network error"))
        with patch("github_tracker.app.load_state", return_value=([], merged)):
            with patch("github_tracker.app.save_state"):
                app = GitHubTrackerApp(config=make_config(), github_client=client)
                async with app.run_test() as pilot:
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    # Should still have merged PR, just with deploying status
                    assert len(app._merged_prs) == 1

    @pytest.mark.asyncio
    async def test_duplicate_merged_pr_not_added(self):
        """If a merged PR already exists in _merged_prs, it's not duplicated."""
        from datetime import datetime, timezone as tz
        merged_at = datetime(2024, 6, 15, 14, 0, 0, tzinfo=tz.utc)
        # PR #5 already tracked as merged, and also was in previous open list
        existing_merged = [make_pr(number=5, repo="owner/repo", merged_at=merged_at, acc_deploy=DeployStatus.ACC_DEPLOYING)]
        cached_open = [make_pr(number=5, repo="owner/repo")]
        # On refresh: no open PRs (PR #5 disappeared)
        client = make_mock_client(raw_prs=[])
        client.fetch_pr_detail = AsyncMock(
            return_value={"merged_at": "2024-06-15T14:00:00Z", "comments": 0, "review_comments": 0}
        )
        with patch("github_tracker.app.load_state", return_value=(cached_open, existing_merged)):
            with patch("github_tracker.app.save_state"):
                app = GitHubTrackerApp(config=make_config(), github_client=client)
                async with app.run_test() as pilot:
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    # Should still have just 1 merged PR, not duplicated
                    assert len(app._merged_prs) == 1
                    assert app._merged_prs[0].number == 5

    @pytest.mark.asyncio
    async def test_fetch_workflow_run_jobs_called_for_in_progress_run(self):
        """fetch_workflow_run_jobs is called for in-progress workflow runs."""
        from datetime import datetime, timezone as tz
        merged_at = datetime(2024, 6, 15, 12, 0, 0, tzinfo=tz.utc)
        merged = [make_pr(
            number=1,
            repo="owner/repo",
            merged_at=merged_at,
            acc_deploy=DeployStatus.ACC_DEPLOYING,
        )]
        run = make_workflow_run_response(
            status="in_progress",
            conclusion=None,
            created_at="2024-06-15T13:00:00Z",
            id=42,
        )
        client = make_mock_client(raw_prs=[])
        client.fetch_workflow_runs = AsyncMock(return_value=[run])
        client.fetch_workflow_run_jobs = AsyncMock(return_value=[])
        with patch("github_tracker.app.load_state", return_value=([], merged)):
            with patch("github_tracker.app.save_state"):
                app = GitHubTrackerApp(config=make_config(), github_client=client)
                async with app.run_test() as pilot:
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    client.fetch_workflow_run_jobs.assert_called_once_with("owner/repo", 42)

    @pytest.mark.asyncio
    async def test_fetch_workflow_run_jobs_not_called_for_completed_run(self):
        """fetch_workflow_run_jobs is NOT called for completed workflow runs."""
        from datetime import datetime, timezone as tz
        merged_at = datetime(2024, 6, 15, 12, 0, 0, tzinfo=tz.utc)
        merged = [make_pr(
            number=1,
            repo="owner/repo",
            merged_at=merged_at,
            acc_deploy=DeployStatus.ACC_DEPLOYING,
        )]
        run = make_workflow_run_response(
            status="completed",
            conclusion="success",
            created_at="2024-06-15T13:00:00Z",
            id=42,
        )
        client = make_mock_client(raw_prs=[])
        client.fetch_workflow_runs = AsyncMock(return_value=[run])
        client.fetch_workflow_run_jobs = AsyncMock(return_value=[])
        with patch("github_tracker.app.load_state", return_value=([], merged)):
            with patch("github_tracker.app.save_state"):
                app = GitHubTrackerApp(config=make_config(), github_client=client)
                async with app.run_test() as pilot:
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    client.fetch_workflow_run_jobs.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_workflow_run_jobs_error_handled(self):
        """Error fetching workflow run jobs doesn't crash the app."""
        from datetime import datetime, timezone as tz
        merged_at = datetime(2024, 6, 15, 12, 0, 0, tzinfo=tz.utc)
        merged = [make_pr(
            number=1,
            repo="owner/repo",
            merged_at=merged_at,
            acc_deploy=DeployStatus.ACC_DEPLOYING,
        )]
        run = make_workflow_run_response(
            status="in_progress",
            conclusion=None,
            created_at="2024-06-15T13:00:00Z",
            id=42,
        )
        client = make_mock_client(raw_prs=[])
        client.fetch_workflow_runs = AsyncMock(return_value=[run])
        client.fetch_workflow_run_jobs = AsyncMock(side_effect=Exception("Network error"))
        with patch("github_tracker.app.load_state", return_value=([], merged)):
            with patch("github_tracker.app.save_state"):
                app = GitHubTrackerApp(config=make_config(), github_client=client)
                async with app.run_test() as pilot:
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    assert len(app._merged_prs) == 1

    @pytest.mark.asyncio
    async def test_acc_step_counts_propagated(self):
        """Step counts from jobs are stored on merged PR."""
        from datetime import datetime, timezone as tz
        merged_at = datetime(2024, 6, 15, 12, 0, 0, tzinfo=tz.utc)
        merged = [make_pr(
            number=1,
            repo="owner/repo",
            merged_at=merged_at,
            acc_deploy=DeployStatus.ACC_DEPLOYING,
        )]
        run = make_workflow_run_response(
            status="in_progress",
            conclusion=None,
            created_at="2024-06-15T13:00:00Z",
            id=77,
        )
        jobs = [
            {"status": "completed", "name": "build"},
            {"status": "in_progress", "name": "test"},
            {"status": "queued", "name": "deploy"},
        ]
        client = make_mock_client(raw_prs=[])
        client.fetch_workflow_runs = AsyncMock(return_value=[run])
        client.fetch_workflow_run_jobs = AsyncMock(return_value=jobs)
        with patch("github_tracker.app.load_state", return_value=([], merged)):
            with patch("github_tracker.app.save_state"):
                app = GitHubTrackerApp(config=make_config(), github_client=client)
                async with app.run_test() as pilot:
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    assert app._merged_prs[0].acc_completed_steps == 1
                    assert app._merged_prs[0].acc_total_steps == 3

    @pytest.mark.asyncio
    async def test_closed_not_merged_pr_not_tracked(self):
        """PR that disappeared but wasn't merged (closed) is not tracked."""
        cached = [make_pr(number=1, repo="owner/repo")]
        client = make_mock_client(raw_prs=[])
        # No merged_at in detail → PR was closed, not merged
        client.fetch_pr_detail = AsyncMock(
            return_value={"merged_at": None, "comments": 0, "review_comments": 0}
        )
        with patch("github_tracker.app.load_state", return_value=(cached, [])):
            with patch("github_tracker.app.save_state"):
                app = GitHubTrackerApp(config=make_config(), github_client=client)
                async with app.run_test() as pilot:
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    assert len(app._merged_prs) == 0


class TestRefreshFocusedPrs:
    @pytest.mark.asyncio
    async def test_no_table_focused_does_nothing(self):
        """When _get_focused_table() returns None, _refresh_focused_prs returns early."""
        client = make_mock_client(raw_prs=[])
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            with patch.object(pilot.app, "_get_focused_table", return_value=None):
                await pilot.app._refresh_focused_prs()
            assert _get_total_row_count(pilot.app) == 0

    @pytest.mark.asyncio
    async def test_refresh_updates_approval_count(self):
        """Focused refresh updates approval count from new reviews."""
        raw = [make_github_pr_response(number=1)]
        client = make_mock_client(raw_prs=raw)
        client.fetch_reviews = AsyncMock(return_value=[make_review_response("APPROVED", "alice")])
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            client.fetch_reviews.return_value = [
                make_review_response("APPROVED", "alice"),
                make_review_response("APPROVED", "bob"),
            ]
            await pilot.press("r")
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            table = _get_other_table(pilot.app)
            assert table.pull_requests[0].approval_count == 2

    @pytest.mark.asyncio
    async def test_refresh_skips_pr_when_detail_raises(self):
        """Error fetching pr_detail is logged and that PR is skipped."""
        raw = [make_github_pr_response(number=1), make_github_pr_response(number=2)]
        client = make_mock_client(raw_prs=raw)
        client.fetch_pr_detail = AsyncMock(
            side_effect=[Exception("timeout"), {"head": {"sha": "abc"}, "comments": 0, "review_comments": 0}]
        )
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("r")
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            # App still shows both PRs despite one fetch failing
            assert _get_total_row_count(pilot.app) == 2

    @pytest.mark.asyncio
    async def test_refresh_skips_pr_when_no_head_sha(self):
        """PR is skipped when pr_detail returns no head sha."""
        raw = [make_github_pr_response(number=1)]
        client = make_mock_client(raw_prs=raw)
        # Phase 2 (full load) uses head sha from raw_pr, but focused refresh fetches pr_detail
        # Return a detail with no head sha → skip this PR
        client.fetch_pr_detail = AsyncMock(return_value={"comments": 0, "review_comments": 0})
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("r")
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            assert _get_total_row_count(pilot.app) == 1

    @pytest.mark.asyncio
    async def test_refresh_updates_merged_pr_acc_status(self):
        """Focused refresh transitions a merged PR from ACC_DEPLOYING to ACC_DEPLOYED."""
        from datetime import datetime, timedelta, timezone as tz
        now = datetime.now(tz=tz.utc)
        merged_at = now - timedelta(hours=1)
        merged = [make_pr(
            number=5,
            repo="owner/repo",
            merged_at=merged_at,
            acc_deploy=DeployStatus.ACC_DEPLOYING,
        )]
        run_in_progress = make_workflow_run_response(
            status="in_progress", conclusion=None,
            created_at=(now - timedelta(minutes=30)).isoformat(), id=55,
        )
        run_success = make_workflow_run_response(
            status="completed", conclusion="success",
            created_at=(now - timedelta(minutes=30)).isoformat(),
            updated_at=(now - timedelta(hours=2)).isoformat(),  # past cooldown
            id=55,
        )
        client = make_mock_client(raw_prs=[])
        client.fetch_workflow_runs = AsyncMock(return_value=[run_in_progress])
        with patch("github_tracker.app.load_state", return_value=([], merged)):
            with patch("github_tracker.app.save_state"):
                app = GitHubTrackerApp(config=make_config(), github_client=client)
                async with app.run_test() as pilot:
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    # Now workflow run has completed successfully
                    client.fetch_workflow_runs.return_value = [run_success]
                    await pilot.press("r")
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    assert app._merged_prs[0].acc_deploy == DeployStatus.ACC_DEPLOYED

    @pytest.mark.asyncio
    async def test_refresh_merged_pr_workflow_runs_error_handled(self):
        """Error fetching workflow runs for merged PR doesn't crash."""
        from datetime import datetime, timedelta, timezone as tz
        now = datetime.now(tz=tz.utc)
        merged = [make_pr(
            number=5,
            repo="owner/repo",
            merged_at=now - timedelta(hours=1),
            acc_deploy=DeployStatus.ACC_DEPLOYING,
        )]
        run = make_workflow_run_response(
            status="in_progress", conclusion=None,
            created_at=(now - timedelta(minutes=30)).isoformat(), id=55,
        )
        client = make_mock_client(raw_prs=[])
        client.fetch_workflow_runs = AsyncMock(return_value=[run])
        with patch("github_tracker.app.load_state", return_value=([], merged)):
            with patch("github_tracker.app.save_state"):
                app = GitHubTrackerApp(config=make_config(), github_client=client)
                async with app.run_test() as pilot:
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    client.fetch_workflow_runs.side_effect = Exception("Network error")
                    await pilot.press("r")
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    assert len(app._merged_prs) == 1

    @pytest.mark.asyncio
    async def test_refresh_merged_pr_jobs_error_handled(self):
        """Error fetching jobs for in-progress run doesn't crash."""
        from datetime import datetime, timedelta, timezone as tz
        now = datetime.now(tz=tz.utc)
        merged = [make_pr(
            number=5,
            repo="owner/repo",
            merged_at=now - timedelta(hours=1),
            acc_deploy=DeployStatus.ACC_DEPLOYING,
        )]
        run = make_workflow_run_response(
            status="in_progress", conclusion=None,
            created_at=(now - timedelta(minutes=30)).isoformat(), id=77,
        )
        client = make_mock_client(raw_prs=[])
        client.fetch_workflow_runs = AsyncMock(return_value=[run])
        client.fetch_workflow_run_jobs = AsyncMock(side_effect=Exception("timeout"))
        with patch("github_tracker.app.load_state", return_value=([], merged)):
            with patch("github_tracker.app.save_state"):
                app = GitHubTrackerApp(config=make_config(), github_client=client)
                async with app.run_test() as pilot:
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    await pilot.press("r")
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    assert len(app._merged_prs) == 1

    @pytest.mark.asyncio
    async def test_refresh_saves_state_after_regrouping(self):
        """Focused refresh saves state so migrated PRs persist across restarts."""
        raw = [make_github_pr_response(number=1, requested_reviewers=[])]
        client = make_mock_client(raw_prs=raw)
        config = make_config(github_username="myuser")
        with patch("github_tracker.app.save_state") as mock_save:
            app = GitHubTrackerApp(config=config, github_client=client)
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.app.workers.wait_for_complete()
                await pilot.pause()
                mock_save.reset_mock()
                client.fetch_pr_detail = AsyncMock(return_value={
                    "head": {"sha": "abc123"},
                    "comments": 0,
                    "review_comments": 0,
                    "requested_reviewers": [{"login": "myuser"}],
                    "body": "",
                })
                await pilot.press("r")
                await pilot.pause()
                await pilot.app.workers.wait_for_complete()
                await pilot.pause()
                mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_refresh_does_not_auto_follow_when_reviewer_added(self):
        """Focused refresh does NOT auto-follow an existing PR when user becomes a reviewer.

        Auto-FAVOURITE only happens on first discovery. Known PRs keep their current
        FAVOURITE state — if they have none, they stay in Others even after labels change.
        """
        raw = [make_github_pr_response(number=1, requested_reviewers=[])]
        client = make_mock_client(raw_prs=raw)
        config = make_config(github_username="myuser")
        app = GitHubTrackerApp(config=config, github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            # PR starts in Other PRs (user not a reviewer yet)
            other_table = app.query_one("#other-pr-table", PRTable)
            my_table = app.query_one("#my-pr-table", PRTable)
            assert other_table.row_count == 1
            assert my_table.row_count == 0
            # Now user is added as reviewer
            client.fetch_pr_detail = AsyncMock(return_value={
                "head": {"sha": "abc123"},
                "comments": 0,
                "review_comments": 0,
                "requested_reviewers": [{"login": "myuser"}],
                "body": "",
            })
            await pilot.press("r")
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            # PR stays in Others — refresh does not auto-follow existing PRs
            assert my_table.row_count == 0
            assert other_table.row_count == 1

    @pytest.mark.asyncio
    async def test_refresh_merged_pr_syncs_merged_prs_list(self):
        """Focused refresh keeps _merged_prs in sync with updated step counts."""
        from datetime import datetime, timedelta, timezone as tz
        now = datetime.now(tz=tz.utc)
        merged = [make_pr(
            number=5,
            repo="owner/repo",
            merged_at=now - timedelta(hours=1),
            acc_deploy=DeployStatus.ACC_DEPLOYING,
        )]
        run = make_workflow_run_response(
            status="in_progress", conclusion=None,
            created_at=(now - timedelta(minutes=30)).isoformat(), id=99,
        )
        jobs = [
            {"status": "completed", "name": "build"},
            {"status": "in_progress", "name": "test"},
        ]
        client = make_mock_client(raw_prs=[])
        client.fetch_workflow_runs = AsyncMock(return_value=[run])
        client.fetch_workflow_run_jobs = AsyncMock(return_value=jobs)
        with patch("github_tracker.app.load_state", return_value=([], merged)):
            with patch("github_tracker.app.save_state"):
                app = GitHubTrackerApp(config=make_config(), github_client=client)
                async with app.run_test() as pilot:
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    await pilot.press("r")
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    assert app._merged_prs[0].acc_completed_steps == 1
                    assert app._merged_prs[0].acc_total_steps == 2


class TestCIProgressPropagation:
    @pytest.mark.asyncio
    async def test_ci_step_counts_set_in_phase2(self):
        """CI step counts are computed and stored on PR after Phase 2."""
        raw = [make_github_pr_response(number=1)]
        client = make_mock_client(raw_prs=raw)
        client.fetch_check_runs = AsyncMock(return_value=[
            {"status": "completed", "conclusion": "success", "name": "build"},
            {"status": "in_progress", "conclusion": None, "name": "test"},
        ])
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            table = _get_other_table(pilot.app)
            pr = table.pull_requests[0]
            assert pr.ci_completed_steps == 1
            assert pr.ci_total_steps == 2


class TestUserApprovedPropagation:
    def _find_pr_in_tables(self, app, pr_number: int):
        """Find a PR by number across both tables."""
        for table_id in ("#my-pr-table", "#other-pr-table"):
            table = app.query_one(table_id, PRTable)
            idx = table._pr_index.get(pr_number)
            if idx is not None:
                return table.pull_requests[idx]
        return None

    @pytest.mark.asyncio
    async def test_user_approved_set_in_phase2_when_approved(self):
        """Phase 2 sets user_approved=True when user has approved the PR."""
        raw = [make_github_pr_response(number=1)]
        client = make_mock_client(raw_prs=raw)
        client.fetch_reviews = AsyncMock(return_value=[make_review_response("APPROVED", "myuser")])
        config = make_config(github_username="myuser")
        app = GitHubTrackerApp(config=config, github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            pr = self._find_pr_in_tables(pilot.app, 1)
            assert pr is not None
            assert pr.user_approved is True

    @pytest.mark.asyncio
    async def test_user_approved_false_when_no_review_by_user(self):
        """Phase 2 leaves user_approved=False when user has not reviewed."""
        raw = [make_github_pr_response(number=1)]
        client = make_mock_client(raw_prs=raw)
        client.fetch_reviews = AsyncMock(return_value=[make_review_response("APPROVED", "otheruser")])
        config = make_config(github_username="myuser")
        app = GitHubTrackerApp(config=config, github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            pr = self._find_pr_in_tables(pilot.app, 1)
            assert pr is not None
            assert pr.user_approved is False

    @pytest.mark.asyncio
    async def test_user_approved_set_in_focused_refresh(self):
        """Focused refresh ('r') also computes and sets user_approved."""
        raw = [make_github_pr_response(number=1)]
        client = make_mock_client(raw_prs=raw)
        client.fetch_reviews = AsyncMock(return_value=[])
        # No username → PR stays in other table, no labels
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            table = _get_other_table(pilot.app)
            assert table.pull_requests[0].user_approved is False
            # After refresh with approval (still no username → always False)
            client.fetch_reviews.return_value = [make_review_response("APPROVED", "")]
            await pilot.press("r")
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            assert table.pull_requests[0].user_approved is False


class TestFavourite:
    @pytest.mark.asyncio
    async def test_favourite_moves_pr_to_my_prs(self):
        """Pressing f on an Other PR adds FAVOURITE and moves it to My PRs."""
        raw = [make_github_pr_response(number=1)]
        client = make_mock_client(raw_prs=raw)
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            other_table = app.query_one("#other-pr-table", PRTable)
            my_table = app.query_one("#my-pr-table", PRTable)
            assert other_table.row_count == 1
            assert my_table.row_count == 0
            other_table.focus()
            await pilot.press("f")
            await pilot.pause()
            assert my_table.row_count == 1
            assert other_table.row_count == 0
            assert PRLabel.FAVOURITE in my_table.pull_requests[0].labels

    @pytest.mark.asyncio
    async def test_favourite_toggle_removes_from_my_prs(self):
        """Pressing f again on a FAVOURITE-only PR removes it from My PRs."""
        raw = [make_github_pr_response(number=1)]
        client = make_mock_client(raw_prs=raw)
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            other_table = app.query_one("#other-pr-table", PRTable)
            my_table = app.query_one("#my-pr-table", PRTable)
            other_table.focus()
            await pilot.press("f")
            await pilot.pause()
            assert my_table.row_count == 1
            my_table.focus()
            await pilot.press("f")
            await pilot.pause()
            assert my_table.row_count == 0
            assert other_table.row_count == 1
            assert PRLabel.FAVOURITE not in other_table.pull_requests[0].labels

    @pytest.mark.asyncio
    async def test_favourite_saves_state(self):
        """Pressing f saves state with the FAVOURITE label."""
        raw = [make_github_pr_response(number=1)]
        client = make_mock_client(raw_prs=raw)
        with patch("github_tracker.app.save_state") as mock_save:
            app = GitHubTrackerApp(config=make_config(), github_client=client)
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.app.workers.wait_for_complete()
                await pilot.pause()
                mock_save.reset_mock()
                app.query_one("#other-pr-table", PRTable).focus()
                await pilot.press("f")
                await pilot.pause()
                mock_save.assert_called_once()
                saved_prs = mock_save.call_args[0][0]
                assert any(PRLabel.FAVOURITE in p.labels for p in saved_prs)

    @pytest.mark.asyncio
    async def test_favourite_label_preserved_through_phase1(self):
        """FAVOURITE label survives the Phase 1 label recompute on full reload."""
        raw = [make_github_pr_response(number=1, requested_reviewers=[])]
        client = make_mock_client(raw_prs=raw)
        app = GitHubTrackerApp(config=make_config(github_username="myuser"), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            app.query_one("#other-pr-table", PRTable).focus()
            await pilot.press("f")
            await pilot.pause()
            my_table = app.query_one("#my-pr-table", PRTable)
            assert PRLabel.FAVOURITE in my_table.pull_requests[0].labels
            # Simulate a full reload — FAVOURITE must survive Phase 1
            app.run_worker(app._load_prs_progressive(), exclusive=True)
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            assert my_table.row_count == 1
            assert PRLabel.FAVOURITE in my_table.pull_requests[0].labels

    @pytest.mark.asyncio
    async def test_favourite_label_preserved_through_focused_refresh(self):
        """FAVOURITE label survives a focused refresh."""
        raw = [make_github_pr_response(number=1, requested_reviewers=[])]
        client = make_mock_client(raw_prs=raw)
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            app.query_one("#other-pr-table", PRTable).focus()
            await pilot.press("f")
            await pilot.pause()
            my_table = app.query_one("#my-pr-table", PRTable)
            assert PRLabel.FAVOURITE in my_table.pull_requests[0].labels
            my_table.focus()
            await pilot.press("r")
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            assert PRLabel.FAVOURITE in my_table.pull_requests[0].labels

    @pytest.mark.asyncio
    async def test_unfavourite_triggers_flash_in_others(self):
        """Pressing f to unfollow a PR triggers flash_title on the Other PRs table."""
        raw = [make_github_pr_response(number=1)]
        client = make_mock_client(raw_prs=raw)
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            # Move to My PRs first
            other_table = app.query_one("#other-pr-table", PRTable)
            other_table.focus()
            await pilot.press("f")
            await pilot.pause()
            # Now unfollow — flash should fire
            my_table = app.query_one("#my-pr-table", PRTable)
            my_table.focus()
            with patch.object(other_table, "flash_title", new_callable=AsyncMock) as mock_flash:
                await pilot.press("f")
                await pilot.pause()
                await pilot.app.workers.wait_for_complete()
                await pilot.pause()
            mock_flash.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_favourite_no_table_focused_does_nothing(self):
        """action_favourite returns early when no table is focused."""
        client = make_mock_client(raw_prs=[])
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            with patch.object(pilot.app, "_get_focused_table", return_value=None):
                pilot.app.action_favourite()  # should not raise

    @pytest.mark.asyncio
    async def test_favourite_no_pr_selected_warns(self):
        """Pressing f with no PR selected shows a warning."""
        client = make_mock_client(raw_prs=[])
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            # Patch notify to detect the warning
            with patch.object(pilot.app, "notify") as mock_notify:
                pilot.app.action_favourite()
                mock_notify.assert_called_once()


class TestAutoFavourite:
    @pytest.mark.asyncio
    async def test_new_pr_with_author_gets_auto_favourited(self):
        """New PR where user is the author gets auto-FAVOURITE and goes to My PRs."""
        raw = [make_github_pr_response(number=1, user={"login": "myuser"})]
        client = make_mock_client(raw_prs=raw)
        config = make_config(github_username="myuser")
        app = GitHubTrackerApp(config=config, github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            my_table = app.query_one("#my-pr-table", PRTable)
            other_table = app.query_one("#other-pr-table", PRTable)
            assert my_table.row_count == 1
            assert other_table.row_count == 0
            assert PRLabel.FAVOURITE in my_table.pull_requests[0].labels

    @pytest.mark.asyncio
    async def test_known_pr_without_favourite_stays_in_others(self):
        """Known PR (previously seen) without FAVOURITE stays in Others even if user is author."""
        raw = [make_github_pr_response(number=1, user={"login": "myuser"})]
        client = make_mock_client(raw_prs=raw)
        config = make_config(github_username="myuser")
        # Simulate a "known" PR in _previous_open_prs (no FAVOURITE label)
        known_pr = make_pr(number=1, repo="owner/repo", author="myuser", labels=frozenset({PRLabel.AUTHOR}))
        with patch("github_tracker.app.load_state", return_value=([known_pr], [])):
            with patch("github_tracker.app.save_state"):
                app = GitHubTrackerApp(config=config, github_client=client)
                async with app.run_test() as pilot:
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    my_table = app.query_one("#my-pr-table", PRTable)
                    other_table = app.query_one("#other-pr-table", PRTable)
                    assert my_table.row_count == 0
                    assert other_table.row_count == 1

    @pytest.mark.asyncio
    async def test_migration_pr_in_my_prs_table_gets_favourite(self):
        """Migration: PR in My PRs table (no FAVOURITE) gets FAVOURITE on reload."""
        raw = [make_github_pr_response(number=1)]
        client = make_mock_client(raw_prs=raw)
        config = make_config(github_username="myuser")
        # Cached PR with no FAVOURITE (old-scheme state)
        cached_pr = make_pr(number=1, repo="owner/repo", labels=frozenset())
        with patch("github_tracker.app.load_state", return_value=([cached_pr], [])):
            with patch("github_tracker.app.save_state"):
                app = GitHubTrackerApp(config=config, github_client=client)
                async with app.run_test() as pilot:
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    my_table = app.query_one("#my-pr-table", PRTable)
                    other_table = app.query_one("#other-pr-table", PRTable)
                    # Manually put PR in My PRs table (simulating old-scheme display)
                    my_table.load_prs([cached_pr])
                    other_table.load_prs([])
                    # Trigger a full reload
                    app.run_worker(app._load_prs_progressive(), exclusive=True)
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    # PR should have FAVOURITE (migration path: my_pr_keys)
                    assert my_table.row_count == 1
                    assert PRLabel.FAVOURITE in my_table.pull_requests[0].labels

    @pytest.mark.asyncio
    async def test_new_pr_with_no_phase1_interest_gets_favourite_from_commented(self):
        """New PR with no Phase 1 interest gets auto-FAVOURITE when user commented (Phase 2)."""
        raw = [make_github_pr_response(number=1)]  # author "testuser", not "myuser"
        client = make_mock_client(raw_prs=raw)
        client.fetch_reviews = AsyncMock(
            return_value=[make_review_response(state="COMMENTED", user="myuser")]
        )
        config = make_config(github_username="myuser")
        app = GitHubTrackerApp(config=config, github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            my_table = app.query_one("#my-pr-table", PRTable)
            other_table = app.query_one("#other-pr-table", PRTable)
            assert my_table.row_count == 1
            assert other_table.row_count == 0
            assert PRLabel.FAVOURITE in my_table.pull_requests[0].labels

    @pytest.mark.asyncio
    async def test_known_unfollowed_pr_with_commented_stays_in_others(self):
        """Known PR without FAVOURITE stays in Others even when COMMENTED is discovered."""
        raw = [make_github_pr_response(number=1)]  # author "testuser"
        client = make_mock_client(raw_prs=raw)
        client.fetch_reviews = AsyncMock(
            return_value=[make_review_response(state="COMMENTED", user="myuser")]
        )
        config = make_config(github_username="myuser")
        # PR is "known" (previously seen, no FAVOURITE — never followed or explicitly unfollowed)
        known_pr = make_pr(number=1, repo="owner/repo", labels=frozenset())
        with patch("github_tracker.app.load_state", return_value=([known_pr], [])):
            with patch("github_tracker.app.save_state"):
                app = GitHubTrackerApp(config=config, github_client=client)
                async with app.run_test() as pilot:
                    await pilot.pause()
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    my_table = app.query_one("#my-pr-table", PRTable)
                    other_table = app.query_one("#other-pr-table", PRTable)
                    assert my_table.row_count == 0
                    assert other_table.row_count == 1


class TestFormatStaleness:
    def test_none_returns_empty(self):
        assert GitHubTrackerApp._format_staleness(None) == ""

    def test_under_10s(self):
        t = datetime.now(timezone.utc)
        result = GitHubTrackerApp._format_staleness(t)
        assert result == "<10s ago"

    def test_10s_bucket(self):
        t = datetime.now(timezone.utc) - __import__("datetime").timedelta(seconds=10)
        result = GitHubTrackerApp._format_staleness(t)
        assert result == "<20s ago"

    def test_40s_bucket(self):
        t = datetime.now(timezone.utc) - __import__("datetime").timedelta(seconds=40)
        result = GitHubTrackerApp._format_staleness(t)
        assert result == "<50s ago"

    def test_50s_shows_1_min(self):
        t = datetime.now(timezone.utc) - __import__("datetime").timedelta(seconds=50)
        result = GitHubTrackerApp._format_staleness(t)
        assert result == "<1 min ago"

    def test_60s_shows_2_mins(self):
        t = datetime.now(timezone.utc) - __import__("datetime").timedelta(seconds=60)
        result = GitHubTrackerApp._format_staleness(t)
        assert result == "<2 mins ago"

    def test_120s_shows_3_mins(self):
        t = datetime.now(timezone.utc) - __import__("datetime").timedelta(seconds=120)
        result = GitHubTrackerApp._format_staleness(t)
        assert result == "<3 mins ago"


class TestAutoRefreshMyPrs:
    @pytest.mark.asyncio
    async def test_auto_refresh_my_prs_updates_table(self):
        """_auto_refresh_my_prs refreshes open PRs in My PRs table."""
        raw = [make_github_pr_response(number=1, requested_reviewers=[])]
        client = make_mock_client(raw_prs=raw)
        client.fetch_reviews = AsyncMock(return_value=[make_review_response("APPROVED")])
        config = make_config(github_username="testuser")
        app = GitHubTrackerApp(config=config, github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.app._auto_refresh_my_prs()
            my_table = app.query_one("#my-pr-table", PRTable)
            assert my_table.pull_requests[0].approval_count == 1

    @pytest.mark.asyncio
    async def test_auto_refresh_my_prs_sets_refreshed_at(self):
        """_auto_refresh_my_prs sets _my_prs_refreshed_at."""
        raw = [make_github_pr_response(number=1, requested_reviewers=[])]
        client = make_mock_client(raw_prs=raw)
        app = GitHubTrackerApp(config=make_config(github_username="testuser"), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            pilot.app._my_prs_refreshed_at = None
            await pilot.app._auto_refresh_my_prs()
            assert pilot.app._my_prs_refreshed_at is not None

    @pytest.mark.asyncio
    async def test_auto_refresh_my_prs_no_client_does_nothing(self):
        """_auto_refresh_my_prs returns early when no client."""
        app = GitHubTrackerApp(config=make_config(), github_client=None)
        async with app.run_test() as pilot:
            await pilot.pause()
            pilot.app._my_prs_refreshed_at = None
            await pilot.app._auto_refresh_my_prs()
            assert pilot.app._my_prs_refreshed_at is None

    @pytest.mark.asyncio
    async def test_auto_refresh_my_prs_updates_label(self):
        """_auto_refresh_my_prs updates the My PRs section label."""
        raw = [make_github_pr_response(number=1, requested_reviewers=[])]
        client = make_mock_client(raw_prs=raw)
        app = GitHubTrackerApp(config=make_config(github_username="testuser"), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.app._auto_refresh_my_prs()
            label = app.query_one("#my-prs-label")
            assert "updated" in str(label._Static__content)

    @pytest.mark.asyncio
    async def test_update_my_prs_label_no_refreshed_at_shows_plain(self):
        """_update_my_prs_label shows plain 'My PRs' when no timestamp."""
        app = GitHubTrackerApp(config=make_config(), github_client=None)
        async with app.run_test() as pilot:
            await pilot.pause()
            pilot.app._my_prs_refreshed_at = None
            pilot.app._update_my_prs_label()
            label = app.query_one("#my-prs-label")
            assert str(label._Static__content) == "My PRs"

    @pytest.mark.asyncio
    async def test_update_my_prs_label_swallows_query_error(self):
        """_update_my_prs_label silently swallows widget query errors."""
        app = GitHubTrackerApp(config=make_config(), github_client=None)
        async with app.run_test() as pilot:
            await pilot.pause()
            with patch.object(pilot.app, "query_one", side_effect=Exception("no widget")):
                pilot.app._update_my_prs_label()  # should not raise

    @pytest.mark.asyncio
    async def test_refresh_focused_my_prs_sets_refreshed_at(self):
        """Pressing r on My PRs table sets _my_prs_refreshed_at."""
        raw = [make_github_pr_response(number=1, requested_reviewers=[])]
        client = make_mock_client(raw_prs=raw)
        app = GitHubTrackerApp(config=make_config(github_username="testuser"), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            pilot.app._my_prs_refreshed_at = None
            my_table = app.query_one("#my-pr-table", PRTable)
            my_table.focus()
            await pilot.press("r")
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            assert pilot.app._my_prs_refreshed_at is not None

    @pytest.mark.asyncio
    async def test_refresh_focused_other_table_does_not_set_refreshed_at(self):
        """Pressing r on Others table does not set _my_prs_refreshed_at."""
        raw = [make_github_pr_response(number=1, requested_reviewers=[])]
        client = make_mock_client(raw_prs=raw)
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            pilot.app._my_prs_refreshed_at = None
            other_table = app.query_one("#other-pr-table", PRTable)
            other_table.focus()
            await pilot.press("r")
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            assert pilot.app._my_prs_refreshed_at is None

    @pytest.mark.asyncio
    async def test_load_prs_progressive_sets_refreshed_at(self):
        """_load_prs_progressive sets _my_prs_refreshed_at after Phase 2."""
        raw = [make_github_pr_response(number=1, requested_reviewers=[])]
        client = make_mock_client(raw_prs=raw)
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            assert pilot.app._my_prs_refreshed_at is not None


class TestHeaderRefreshInfo:
    @pytest.mark.asyncio
    async def test_header_shows_refresh_info(self):
        """Header banner includes the My PRs and All PRs refresh intervals."""
        app = GitHubTrackerApp(config=make_config(refresh_interval=300), github_client=None)
        async with app.run_test() as pilot:
            await pilot.pause()
            header = app.query_one(TrackerHeader)
            banner = str(header.query_one("#banner-content")._Static__content)
            assert "1 min" in banner
            assert "5 min" in banner


class TestMain:
    def test_config_error(self):
        with patch("github_tracker.__main__.setup_logging"):
            with patch(
                "github_tracker.__main__.load_config",
                side_effect=__import__("github_tracker.config", fromlist=["ConfigError"]).ConfigError("bad config"),
            ):
                with pytest.raises(SystemExit) as exc_info:
                    from github_tracker.__main__ import main
                    main()
                assert exc_info.value.code == 1

    def test_auth_error(self):
        with patch("github_tracker.__main__.setup_logging"):
            with patch("github_tracker.__main__.load_config", return_value=Config()):
                with patch(
                    "github_tracker.__main__.get_gh_token",
                    side_effect=__import__(
                        "github_tracker.github_client", fromlist=["GitHubAuthError"]
                    ).GitHubAuthError("no auth"),
                ):
                    with pytest.raises(SystemExit) as exc_info:
                        from github_tracker.__main__ import main
                        main()
                    assert exc_info.value.code == 1

    def test_success(self):
        with patch("github_tracker.__main__.setup_logging"):
            with patch("github_tracker.__main__.load_config", return_value=Config()):
                with patch("github_tracker.__main__.get_gh_token", return_value="token"):
                    with patch("github_tracker.__main__.GitHubClient") as mock_client_cls:
                        with patch("github_tracker.__main__.GitHubTrackerApp") as mock_app_cls:
                            mock_app_instance = MagicMock()
                            mock_app_cls.return_value = mock_app_instance
                            from github_tracker.__main__ import main
                            main()
                            mock_app_instance.run.assert_called_once()
