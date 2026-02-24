"""Tests for github_client module."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import httpx
import pytest
import respx

from github_tracker.github_client import (
    GITHUB_API,
    GitHubAPIError,
    GitHubAuthError,
    GitHubClient,
    _aggregate_ci_status,
    count_approvals,
    get_gh_token,
)
from github_tracker.models import CIStatus
from tests.conftest import (
    make_check_run_response,
    make_github_pr_response,
    make_review_response,
    make_workflow_run_jobs_response,
    make_workflow_run_response,
)


class TestGetGhToken:
    def test_success(self):
        with patch("github_tracker.github_client.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["gh", "auth", "token"],
                returncode=0,
                stdout="ghp_test_token_123\n",
                stderr="",
            )
            token = get_gh_token()
            assert token == "ghp_test_token_123"

    def test_empty_token(self):
        with patch("github_tracker.github_client.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["gh", "auth", "token"],
                returncode=0,
                stdout="\n",
                stderr="",
            )
            with pytest.raises(GitHubAuthError, match="empty result"):
                get_gh_token()

    def test_gh_not_found(self):
        with patch(
            "github_tracker.github_client.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            with pytest.raises(GitHubAuthError, match="gh CLI not found"):
                get_gh_token()

    def test_gh_auth_failed(self):
        with patch(
            "github_tracker.github_client.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "gh", stderr="not logged in"),
        ):
            with pytest.raises(GitHubAuthError, match="gh auth failed"):
                get_gh_token()

    def test_timeout(self):
        with patch(
            "github_tracker.github_client.subprocess.run",
            side_effect=subprocess.TimeoutExpired("gh", 10),
        ):
            with pytest.raises(GitHubAuthError, match="timed out"):
                get_gh_token()


class TestGitHubClient:
    @pytest.fixture
    def client(self):
        return GitHubClient(token="test-token")

    @pytest.mark.asyncio
    async def test_close(self, client):
        await client.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_open_prs(self, client):
        prs = [make_github_pr_response(number=1), make_github_pr_response(number=2)]
        respx.get(f"{GITHUB_API}/repos/owner/repo/pulls?state=open&per_page=100").mock(
            return_value=httpx.Response(200, json=prs)
        )
        result = await client.fetch_open_prs("owner/repo")
        assert len(result) == 2
        assert result[0]["number"] == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_open_prs_not_a_list(self, client):
        respx.get(f"{GITHUB_API}/repos/owner/repo/pulls?state=open&per_page=100").mock(
            return_value=httpx.Response(200, json={"message": "not a list"})
        )
        with pytest.raises(GitHubAPIError, match="Expected list"):
            await client.fetch_open_prs("owner/repo")

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_reviews(self, client):
        reviews = [make_review_response("APPROVED"), make_review_response("CHANGES_REQUESTED")]
        respx.get(f"{GITHUB_API}/repos/owner/repo/pulls/1/reviews").mock(
            return_value=httpx.Response(200, json=reviews)
        )
        result = await client.fetch_reviews("owner/repo", 1)
        assert len(result) == 2

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_reviews_not_a_list(self, client):
        respx.get(f"{GITHUB_API}/repos/owner/repo/pulls/1/reviews").mock(
            return_value=httpx.Response(200, json={"message": "error"})
        )
        result = await client.fetch_reviews("owner/repo", 1)
        assert result == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_check_runs(self, client):
        check_runs = {
            "check_runs": [make_check_run_response("completed", "success")]
        }
        respx.get(f"{GITHUB_API}/repos/owner/repo/commits/abc123/check-runs").mock(
            return_value=httpx.Response(200, json=check_runs)
        )
        result = await client.fetch_check_runs("owner/repo", "abc123")
        assert len(result) == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_check_runs_not_a_dict(self, client):
        respx.get(f"{GITHUB_API}/repos/owner/repo/commits/abc123/check-runs").mock(
            return_value=httpx.Response(200, json=[])
        )
        result = await client.fetch_check_runs("owner/repo", "abc123")
        assert result == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_pr_detail(self, client):
        pr_detail = {"number": 1, "comments": 5, "review_comments": 3}
        respx.get(f"{GITHUB_API}/repos/owner/repo/pulls/1").mock(
            return_value=httpx.Response(200, json=pr_detail)
        )
        result = await client.fetch_pr_detail("owner/repo", 1)
        assert result["comments"] == 5
        assert result["review_comments"] == 3

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_pr_detail_not_a_dict(self, client):
        respx.get(f"{GITHUB_API}/repos/owner/repo/pulls/1").mock(
            return_value=httpx.Response(200, json=[])
        )
        result = await client.fetch_pr_detail("owner/repo", 1)
        assert result == {}

    @respx.mock
    @pytest.mark.asyncio
    async def test_api_rate_limit(self, client):
        respx.get(f"{GITHUB_API}/repos/owner/repo/pulls?state=open&per_page=100").mock(
            return_value=httpx.Response(
                403, json={"message": "API rate limit exceeded"}
            )
        )
        with pytest.raises(GitHubAPIError, match="rate limit"):
            await client.fetch_open_prs("owner/repo")

    @respx.mock
    @pytest.mark.asyncio
    async def test_api_error(self, client):
        respx.get(f"{GITHUB_API}/repos/owner/repo/pulls?state=open&per_page=100").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )
        with pytest.raises(GitHubAPIError, match="404"):
            await client.fetch_open_prs("owner/repo")

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_pull_requests_full(self, client):
        pr_data = make_github_pr_response(
            number=42,
            title="[PROJ-123] Add feature",
            head={"sha": "sha123", "ref": "PROJ-123-add-feature"},
        )
        respx.get(f"{GITHUB_API}/repos/owner/repo/pulls?state=open&per_page=100").mock(
            return_value=httpx.Response(200, json=[pr_data])
        )
        respx.get(f"{GITHUB_API}/repos/owner/repo/pulls/42/reviews").mock(
            return_value=httpx.Response(
                200, json=[make_review_response("APPROVED", user="alice"), make_review_response("APPROVED", user="bob")]
            )
        )
        respx.get(f"{GITHUB_API}/repos/owner/repo/commits/sha123/check-runs").mock(
            return_value=httpx.Response(
                200, json={"check_runs": [make_check_run_response("completed", "success")]}
            )
        )
        respx.get(f"{GITHUB_API}/repos/owner/repo/pulls/42").mock(
            return_value=httpx.Response(200, json={"comments": 3, "review_comments": 2})
        )
        prs = await client.fetch_pull_requests(
            "owner/repo", jira_base_url="https://jira.example.com/browse"
        )
        assert len(prs) == 1
        pr = prs[0]
        assert pr.number == 42
        assert pr.title == "[PROJ-123] Add feature"
        assert pr.approval_count == 2
        assert pr.ci_status == CIStatus.SUCCESS
        assert pr.jira_ticket == "PROJ-123"
        assert pr.jira_url == "https://jira.example.com/browse/PROJ-123"
        assert pr.comment_count == 5
        assert pr.author == "testuser"
        assert pr.repo == "owner/repo"

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_pull_requests_no_jira(self, client):
        pr_data = make_github_pr_response(
            number=1,
            title="Fix bug",
            head={"sha": "sha1", "ref": "fix-bug"},
        )
        respx.get(f"{GITHUB_API}/repos/owner/repo/pulls?state=open&per_page=100").mock(
            return_value=httpx.Response(200, json=[pr_data])
        )
        respx.get(f"{GITHUB_API}/repos/owner/repo/pulls/1/reviews").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.get(f"{GITHUB_API}/repos/owner/repo/commits/sha1/check-runs").mock(
            return_value=httpx.Response(200, json={"check_runs": []})
        )
        respx.get(f"{GITHUB_API}/repos/owner/repo/pulls/1").mock(
            return_value=httpx.Response(200, json={"comments": 0, "review_comments": 0})
        )
        prs = await client.fetch_pull_requests("owner/repo")
        assert len(prs) == 1
        assert prs[0].jira_ticket is None
        assert prs[0].jira_url is None
        assert prs[0].ci_status == CIStatus.UNKNOWN

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_pull_requests_invalid_date(self, client):
        pr_data = make_github_pr_response(
            number=1,
            head={"sha": "sha1", "ref": "branch"},
            updated_at="not-a-date",
        )
        respx.get(f"{GITHUB_API}/repos/owner/repo/pulls?state=open&per_page=100").mock(
            return_value=httpx.Response(200, json=[pr_data])
        )
        respx.get(f"{GITHUB_API}/repos/owner/repo/pulls/1/reviews").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.get(f"{GITHUB_API}/repos/owner/repo/commits/sha1/check-runs").mock(
            return_value=httpx.Response(200, json={"check_runs": []})
        )
        respx.get(f"{GITHUB_API}/repos/owner/repo/pulls/1").mock(
            return_value=httpx.Response(200, json={"comments": 0, "review_comments": 0})
        )
        prs = await client.fetch_pull_requests("owner/repo")
        assert len(prs) == 1
        assert prs[0].updated_at is not None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_pull_requests_jira_ticket_no_base_url(self, client):
        pr_data = make_github_pr_response(
            number=1,
            title="[PROJ-99] Something",
            head={"sha": "sha1", "ref": "PROJ-99-something"},
        )
        respx.get(f"{GITHUB_API}/repos/owner/repo/pulls?state=open&per_page=100").mock(
            return_value=httpx.Response(200, json=[pr_data])
        )
        respx.get(f"{GITHUB_API}/repos/owner/repo/pulls/1/reviews").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.get(f"{GITHUB_API}/repos/owner/repo/commits/sha1/check-runs").mock(
            return_value=httpx.Response(200, json={"check_runs": []})
        )
        respx.get(f"{GITHUB_API}/repos/owner/repo/pulls/1").mock(
            return_value=httpx.Response(200, json={"comments": 0, "review_comments": 0})
        )
        prs = await client.fetch_pull_requests("owner/repo", jira_base_url="")
        assert prs[0].jira_ticket == "PROJ-99"
        assert prs[0].jira_url is None


class TestFetchWorkflowRuns:
    @pytest.fixture
    def client(self):
        return GitHubClient(token="test-token")

    @respx.mock
    @pytest.mark.asyncio
    async def test_success(self, client):
        workflows = {
            "workflows": [
                {"id": 123, "name": "Reisbalans deploy to cloud acceptance"},
                {"id": 456, "name": "Other workflow"},
            ]
        }
        runs = {
            "workflow_runs": [
                make_workflow_run_response(status="completed", conclusion="success"),
            ]
        }
        respx.get(f"{GITHUB_API}/repos/owner/repo/actions/workflows").mock(
            return_value=httpx.Response(200, json=workflows)
        )
        respx.get(
            f"{GITHUB_API}/repos/owner/repo/actions/workflows/123/runs?branch=master&per_page=5"
        ).mock(return_value=httpx.Response(200, json=runs))
        result = await client.fetch_workflow_runs(
            "owner/repo", "Reisbalans deploy to cloud acceptance"
        )
        assert len(result) == 1
        assert result[0]["status"] == "completed"

    @respx.mock
    @pytest.mark.asyncio
    async def test_workflow_not_found(self, client):
        workflows = {"workflows": [{"id": 456, "name": "Other workflow"}]}
        respx.get(f"{GITHUB_API}/repos/owner/repo/actions/workflows").mock(
            return_value=httpx.Response(200, json=workflows)
        )
        result = await client.fetch_workflow_runs("owner/repo", "nonexistent")
        assert result == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_workflows_not_a_dict(self, client):
        respx.get(f"{GITHUB_API}/repos/owner/repo/actions/workflows").mock(
            return_value=httpx.Response(200, json=[])
        )
        result = await client.fetch_workflow_runs("owner/repo", "test")
        assert result == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_runs_not_a_dict(self, client):
        workflows = {
            "workflows": [{"id": 123, "name": "test"}]
        }
        respx.get(f"{GITHUB_API}/repos/owner/repo/actions/workflows").mock(
            return_value=httpx.Response(200, json=workflows)
        )
        respx.get(
            f"{GITHUB_API}/repos/owner/repo/actions/workflows/123/runs?branch=master&per_page=5"
        ).mock(return_value=httpx.Response(200, json=[]))
        result = await client.fetch_workflow_runs("owner/repo", "test")
        assert result == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_custom_branch_and_per_page(self, client):
        workflows = {"workflows": [{"id": 123, "name": "test"}]}
        runs = {"workflow_runs": []}
        respx.get(f"{GITHUB_API}/repos/owner/repo/actions/workflows").mock(
            return_value=httpx.Response(200, json=workflows)
        )
        respx.get(
            f"{GITHUB_API}/repos/owner/repo/actions/workflows/123/runs?branch=main&per_page=10"
        ).mock(return_value=httpx.Response(200, json=runs))
        result = await client.fetch_workflow_runs(
            "owner/repo", "test", branch="main", per_page=10
        )
        assert result == []


class TestFetchWorkflowRunJobs:
    @pytest.fixture
    def client(self):
        return GitHubClient(token="test-token")

    @respx.mock
    @pytest.mark.asyncio
    async def test_success(self, client):
        jobs_data = make_workflow_run_jobs_response()
        respx.get(f"{GITHUB_API}/repos/owner/repo/actions/runs/42/jobs").mock(
            return_value=httpx.Response(200, json=jobs_data)
        )
        result = await client.fetch_workflow_run_jobs("owner/repo", 42)
        assert len(result) == 2
        assert result[0]["name"] == "build"

    @respx.mock
    @pytest.mark.asyncio
    async def test_not_a_dict_returns_empty(self, client):
        respx.get(f"{GITHUB_API}/repos/owner/repo/actions/runs/42/jobs").mock(
            return_value=httpx.Response(200, json=[])
        )
        result = await client.fetch_workflow_run_jobs("owner/repo", 42)
        assert result == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_empty_jobs_list(self, client):
        respx.get(f"{GITHUB_API}/repos/owner/repo/actions/runs/99/jobs").mock(
            return_value=httpx.Response(200, json={"jobs": []})
        )
        result = await client.fetch_workflow_run_jobs("owner/repo", 99)
        assert result == []


class TestParsePrBasic:
    @pytest.fixture
    def client(self):
        return GitHubClient(token="test-token")

    def test_basic_fields(self, client):
        raw = make_github_pr_response(
            number=42,
            title="Add feature",
            html_url="https://github.com/o/r/pull/42",
            head={"sha": "abc", "ref": "feature-branch"},
            user={"login": "alice"},
            comments=2,
            review_comments=3,
            updated_at="2024-06-15T12:00:00Z",
        )
        pr = client.parse_pr_basic(raw, "owner/repo", "")
        assert pr.number == 42
        assert pr.title == "Add feature"
        assert pr.url == "https://github.com/o/r/pull/42"
        assert pr.branch_name == "feature-branch"
        assert pr.author == "alice"
        assert pr.comment_count == 0
        assert pr.approval_count == 0
        assert pr.ci_status == CIStatus.PENDING
        assert pr.repo == "owner/repo"

    def test_jira_extraction(self, client):
        raw = make_github_pr_response(
            head={"sha": "abc", "ref": "PROJ-123-fix-bug"},
        )
        pr = client.parse_pr_basic(raw, "owner/repo", "https://jira.example.com/browse")
        assert pr.jira_ticket == "PROJ-123"
        assert pr.jira_url == "https://jira.example.com/browse/PROJ-123"

    def test_no_jira_base_url(self, client):
        raw = make_github_pr_response(
            head={"sha": "abc", "ref": "PROJ-99-something"},
        )
        pr = client.parse_pr_basic(raw, "owner/repo", "")
        assert pr.jira_ticket == "PROJ-99"
        assert pr.jira_url is None

    def test_invalid_date_fallback(self, client):
        raw = make_github_pr_response(updated_at="not-a-date")
        pr = client.parse_pr_basic(raw, "owner/repo", "")
        assert pr.updated_at is not None


class TestCountApprovals:
    def test_empty(self):
        assert count_approvals([]) == 0

    def test_single_approval(self):
        reviews = [{"state": "APPROVED", "user": {"login": "alice"}}]
        assert count_approvals(reviews) == 1

    def test_two_unique_approvers(self):
        reviews = [
            {"state": "APPROVED", "user": {"login": "alice"}},
            {"state": "APPROVED", "user": {"login": "bob"}},
        ]
        assert count_approvals(reviews) == 2

    def test_same_user_approved_twice_counts_once(self):
        reviews = [
            {"state": "APPROVED", "user": {"login": "alice"}},
            {"state": "APPROVED", "user": {"login": "alice"}},
        ]
        assert count_approvals(reviews) == 1

    def test_changes_requested_revokes_approval(self):
        reviews = [
            {"state": "APPROVED", "user": {"login": "alice"}},
            {"state": "CHANGES_REQUESTED", "user": {"login": "alice"}},
        ]
        assert count_approvals(reviews) == 0

    def test_commented_does_not_revoke_approval(self):
        reviews = [
            {"state": "APPROVED", "user": {"login": "alice"}},
            {"state": "COMMENTED", "user": {"login": "alice"}},
        ]
        assert count_approvals(reviews) == 1

    def test_re_approval_after_changes_requested(self):
        reviews = [
            {"state": "APPROVED", "user": {"login": "alice"}},
            {"state": "CHANGES_REQUESTED", "user": {"login": "alice"}},
            {"state": "APPROVED", "user": {"login": "alice"}},
        ]
        assert count_approvals(reviews) == 1

    def test_mixed_reviewers(self):
        reviews = [
            {"state": "APPROVED", "user": {"login": "alice"}},
            {"state": "CHANGES_REQUESTED", "user": {"login": "bob"}},
            {"state": "APPROVED", "user": {"login": "carol"}},
        ]
        assert count_approvals(reviews) == 2

    def test_missing_user_field(self):
        reviews = [{"state": "APPROVED"}]
        assert count_approvals(reviews) == 1

    def test_null_user_field(self):
        reviews = [
            {"state": "APPROVED", "user": None},
            {"state": "APPROVED", "user": {"login": "alice"}},
        ]
        assert count_approvals(reviews) == 2


class TestAggregateCIStatus:
    def test_empty(self):
        assert _aggregate_ci_status([]) == CIStatus.UNKNOWN

    def test_all_success(self):
        runs = [
            make_check_run_response("completed", "success"),
            make_check_run_response("completed", "success"),
        ]
        assert _aggregate_ci_status(runs) == CIStatus.SUCCESS

    def test_any_failure(self):
        runs = [
            make_check_run_response("completed", "success"),
            make_check_run_response("completed", "failure"),
        ]
        assert _aggregate_ci_status(runs) == CIStatus.FAILURE

    def test_any_running(self):
        runs = [
            make_check_run_response("completed", "success"),
            make_check_run_response("in_progress", None),
        ]
        assert _aggregate_ci_status(runs) == CIStatus.RUNNING

    def test_any_pending(self):
        runs = [
            make_check_run_response("completed", "success"),
            make_check_run_response("queued", None),
        ]
        assert _aggregate_ci_status(runs) == CIStatus.PENDING

    def test_failure_takes_priority_over_running(self):
        runs = [
            make_check_run_response("completed", "failure"),
            make_check_run_response("in_progress", None),
        ]
        assert _aggregate_ci_status(runs) == CIStatus.FAILURE

    def test_neutral_treated_as_success(self):
        runs = [
            make_check_run_response("completed", "success"),
            make_check_run_response("completed", "neutral"),
        ]
        assert _aggregate_ci_status(runs) == CIStatus.SUCCESS

    def test_mixed_unknown(self):
        runs = [
            make_check_run_response("completed", "success"),
            make_check_run_response("completed", "stale"),
        ]
        assert _aggregate_ci_status(runs) == CIStatus.UNKNOWN
