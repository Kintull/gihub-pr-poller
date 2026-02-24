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
from tests.conftest import make_pr, make_github_pr_response, make_review_response, make_check_run_response


def make_mock_client(prs: list[PullRequest] | None = None, raw_prs: list[dict] | None = None) -> GitHubClient:
    """Create a mock GitHubClient that supports progressive loading."""
    client = MagicMock(spec=GitHubClient)
    client.fetch_pull_requests = AsyncMock(return_value=prs or [])
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
    async def test_refresh_keybinding(self):
        raw = [make_github_pr_response(number=1)]
        client = make_mock_client(raw_prs=raw)
        app = GitHubTrackerApp(config=make_config(), github_client=client)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            # Add a second PR on refresh
            raw2 = [make_github_pr_response(number=1), make_github_pr_response(number=2)]
            client.fetch_open_prs.return_value = raw2
            await pilot.press("r")
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            table = _get_other_table(pilot.app)
            assert table.row_count == 2

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
