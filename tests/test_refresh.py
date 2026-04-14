"""Tests for refresh module — specifically merged PR discovery."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from github_tracker.github_client import GitHubClient
from github_tracker.models import DeployStatus, PrdDeployStatus
from github_tracker.refresh import refresh_open_pr_details
from tests.conftest import make_pr


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
