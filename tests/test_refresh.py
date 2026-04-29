"""Tests for refresh module — specifically merged PR discovery."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from github_tracker.github_client import GitHubClient
from github_tracker.models import CIStatus, DeployStatus, PRLabel, PrdDeployStatus
from github_tracker.refresh import fetch_user_merged_prs, refresh_open_pr_details
from tests.conftest import make_github_pr_response, make_pr


def _make_client(**overrides) -> GitHubClient:
    client = MagicMock(spec=GitHubClient)
    client.fetch_pr_detail = AsyncMock(return_value={
        "merged_at": None,
        "head": {"sha": "abc123"},
        "comments": 2,
        "review_comments": 1,
        "requested_reviewers": [],
        "user": {"login": "author"},
    })
    client.fetch_reviews = AsyncMock(return_value=[])
    client.fetch_check_runs = AsyncMock(return_value=[])
    client.fetch_review_threads = AsyncMock(return_value=[])
    for k, v in overrides.items():
        setattr(client, k, v)
    return client


class TestRefreshDetectsMergedPrs:
    @pytest.mark.asyncio
    async def test_detects_merged_pr(self):
        prs = [make_pr(number=1, repo="o/r")]
        client = _make_client(
            fetch_pr_detail=AsyncMock(return_value={
                "merged_at": "2024-06-15T14:00:00Z",
                "merge_commit_sha": "sha1",
                "head": {"sha": "abc123"},
                "comments": 0,
                "review_comments": 0,
            })
        )
        update_pr = MagicMock()

        result = await refresh_open_pr_details(prs, client, "testuser", update_pr)

        assert len(result) == 1
        assert result[0].number == 1
        assert result[0].merge_commit_sha == "sha1"
        assert result[0].acc_deploy == DeployStatus.ACC_DEPLOYING
        assert result[0].prd_deploy == PrdDeployStatus.PRD_DEPLOYING
        assert result[0].merged_at is not None
        update_pr.assert_not_called()

    @pytest.mark.asyncio
    async def test_open_pr_stays_open(self):
        prs = [make_pr(number=1, repo="o/r")]
        client = _make_client()
        update_pr = MagicMock()

        result = await refresh_open_pr_details(prs, client, "testuser", update_pr)

        assert len(result) == 0
        update_pr.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_handled(self):
        prs = [make_pr(number=1, repo="o/r")]
        client = _make_client(
            fetch_pr_detail=AsyncMock(side_effect=Exception("network"))
        )
        update_pr = MagicMock()

        result = await refresh_open_pr_details(prs, client, "testuser", update_pr)

        assert len(result) == 0
        update_pr.assert_not_called()


class TestFetchUserMergedPrs:
    def _client(self, raw_by_repo: dict[str, list[dict]]) -> GitHubClient:
        client = MagicMock(spec=GitHubClient)

        async def _fetch(repo, author, since):
            return list(raw_by_repo.get(repo, []))

        client.fetch_recent_merged_prs_by_author = AsyncMock(side_effect=_fetch)
        client.parse_pr_basic = GitHubClient.parse_pr_basic.__get__(client, GitHubClient)
        return client

    @pytest.mark.asyncio
    async def test_empty_username_returns_empty(self):
        client = self._client({})
        result = await fetch_user_merged_prs(["o/r"], client, "", "", days=5)
        assert result == []
        client.fetch_recent_merged_prs_by_author.assert_not_called()

    @pytest.mark.asyncio
    async def test_zero_days_returns_empty(self):
        client = self._client({})
        result = await fetch_user_merged_prs(["o/r"], client, "", "alice", days=0)
        assert result == []
        client.fetch_recent_merged_prs_by_author.assert_not_called()

    @pytest.mark.asyncio
    async def test_builds_pr_with_author_label_and_merged_fields(self):
        raw = make_github_pr_response(
            number=42,
            user={"login": "alice"},
            head={"sha": "s1", "ref": "feat"},
            updated_at="2024-06-14T12:00:00Z",
        )
        raw["merged_at"] = "2024-06-14T12:00:00Z"
        raw["merge_commit_sha"] = "merge-sha"
        client = self._client({"o/r": [raw]})

        result = await fetch_user_merged_prs(
            ["o/r"], client, "", "alice", days=5
        )

        assert len(result) == 1
        pr = result[0]
        assert pr.number == 42
        assert pr.merged_at == datetime(2024, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
        assert pr.merge_commit_sha == "merge-sha"
        assert pr.labels == frozenset({PRLabel.AUTHOR})
        assert pr.acc_deploy == DeployStatus.ACC_DEPLOYING
        assert pr.prd_deploy == PrdDeployStatus.PRD_DEPLOYING
        assert pr.ci_status == CIStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_sorts_by_merged_at_desc_across_repos(self):
        older = make_github_pr_response(number=1, user={"login": "alice"}, head={"sha": "a", "ref": "b"})
        older["merged_at"] = "2024-06-10T10:00:00Z"
        newer = make_github_pr_response(number=2, user={"login": "alice"}, head={"sha": "c", "ref": "d"})
        newer["merged_at"] = "2024-06-14T10:00:00Z"
        client = self._client({"o/r1": [older], "o/r2": [newer]})

        result = await fetch_user_merged_prs(
            ["o/r1", "o/r2"], client, "", "alice", days=10
        )
        assert [pr.number for pr in result] == [2, 1]

    @pytest.mark.asyncio
    async def test_repo_error_does_not_abort_others(self):
        good_raw = make_github_pr_response(number=2, user={"login": "alice"}, head={"sha": "x", "ref": "y"})
        good_raw["merged_at"] = "2024-06-14T10:00:00Z"

        client = MagicMock(spec=GitHubClient)
        async def _fetch(repo, author, since):
            if repo == "o/bad":
                raise RuntimeError("boom")
            return [good_raw]
        client.fetch_recent_merged_prs_by_author = AsyncMock(side_effect=_fetch)
        client.parse_pr_basic = GitHubClient.parse_pr_basic.__get__(client, GitHubClient)

        result = await fetch_user_merged_prs(
            ["o/bad", "o/good"], client, "", "alice", days=5
        )
        assert len(result) == 1
        assert result[0].number == 2

    @pytest.mark.asyncio
    async def test_skips_pr_with_invalid_merged_at(self):
        raw = make_github_pr_response(number=99, user={"login": "alice"}, head={"sha": "s", "ref": "b"})
        raw["merged_at"] = "not-a-date"
        client = self._client({"o/r": [raw]})

        result = await fetch_user_merged_prs(["o/r"], client, "", "alice", days=5)
        assert result == []
