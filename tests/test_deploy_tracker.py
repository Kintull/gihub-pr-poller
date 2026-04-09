"""Tests for deploy_tracker module."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from github_tracker.deploy_tracker import (
    backfill_merge_commit_shas,
    detect_newly_merged_prs,
    update_deploy_statuses,
)
from github_tracker.github_client import GitHubClient
from github_tracker.models import DeployStatus
from tests.conftest import make_pr


def _make_client(**overrides) -> GitHubClient:
    client = MagicMock(spec=GitHubClient)
    client.fetch_pr_detail = AsyncMock(return_value={"comments": 0, "review_comments": 0})
    client.fetch_latest_deployment_sha = AsyncMock(return_value=(None, None))
    client.compare_commits = AsyncMock(return_value=None)
    for k, v in overrides.items():
        setattr(client, k, v)
    return client


class TestDetectNewlyMergedPrs:
    @pytest.mark.asyncio
    async def test_detects_merged_pr(self):
        prev = [make_pr(number=1, repo="o/r")]
        client = _make_client(
            fetch_pr_detail=AsyncMock(return_value={
                "merged_at": "2024-06-15T14:00:00Z",
                "merge_commit_sha": "sha1",
            })
        )
        result = await detect_newly_merged_prs(prev, set(), [], client)
        assert len(result) == 1
        assert result[0].number == 1
        assert result[0].merge_commit_sha == "sha1"
        assert result[0].acc_deploy == DeployStatus.ACC_DEPLOYING

    @pytest.mark.asyncio
    async def test_skips_closed_not_merged(self):
        prev = [make_pr(number=1, repo="o/r")]
        client = _make_client(
            fetch_pr_detail=AsyncMock(return_value={"merged_at": None})
        )
        result = await detect_newly_merged_prs(prev, set(), [], client)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_skips_still_open(self):
        prev = [make_pr(number=1, repo="o/r")]
        client = _make_client()
        result = await detect_newly_merged_prs(prev, {1}, [], client)
        assert len(result) == 0
        client.fetch_pr_detail.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_already_tracked(self):
        prev = [make_pr(number=1, repo="o/r")]
        existing = [make_pr(number=1, repo="o/r", merged_at=datetime(2024, 1, 1, tzinfo=timezone.utc))]
        client = _make_client(
            fetch_pr_detail=AsyncMock(return_value={
                "merged_at": "2024-06-15T14:00:00Z",
                "merge_commit_sha": "sha1",
            })
        )
        result = await detect_newly_merged_prs(prev, set(), existing, client)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_error_handled(self):
        prev = [make_pr(number=1, repo="o/r")]
        client = _make_client(
            fetch_pr_detail=AsyncMock(side_effect=Exception("network"))
        )
        result = await detect_newly_merged_prs(prev, set(), [], client)
        assert len(result) == 0


class TestBackfillMergeCommitShas:
    @pytest.mark.asyncio
    async def test_backfills_missing_sha(self):
        merged = [make_pr(
            number=1, repo="o/r",
            merged_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            merge_commit_sha=None,
            acc_deploy=DeployStatus.ACC_DEPLOYING,
        )]
        client = _make_client(
            fetch_pr_detail=AsyncMock(return_value={"merge_commit_sha": "new_sha"})
        )
        result = await backfill_merge_commit_shas(merged, client)
        assert result[0].merge_commit_sha == "new_sha"

    @pytest.mark.asyncio
    async def test_skips_when_sha_present(self):
        merged = [make_pr(
            number=1, repo="o/r",
            merged_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            merge_commit_sha="existing",
            acc_deploy=DeployStatus.ACC_DEPLOYING,
        )]
        client = _make_client()
        result = await backfill_merge_commit_shas(merged, client)
        assert result[0].merge_commit_sha == "existing"
        client.fetch_pr_detail.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_deployed(self):
        merged = [make_pr(
            number=1, repo="o/r",
            merged_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            merge_commit_sha=None,
            acc_deploy=DeployStatus.ACC_DEPLOYED,
        )]
        client = _make_client()
        result = await backfill_merge_commit_shas(merged, client)
        assert result[0].merge_commit_sha is None
        client.fetch_pr_detail.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_handled(self):
        merged = [make_pr(
            number=1, repo="o/r",
            merged_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            merge_commit_sha=None,
            acc_deploy=DeployStatus.ACC_DEPLOYING,
        )]
        client = _make_client(
            fetch_pr_detail=AsyncMock(side_effect=Exception("timeout"))
        )
        result = await backfill_merge_commit_shas(merged, client)
        assert result[0].merge_commit_sha is None

    @pytest.mark.asyncio
    async def test_no_sha_in_response(self):
        merged = [make_pr(
            number=1, repo="o/r",
            merged_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            merge_commit_sha=None,
            acc_deploy=DeployStatus.ACC_DEPLOYING,
        )]
        client = _make_client(
            fetch_pr_detail=AsyncMock(return_value={"merge_commit_sha": None})
        )
        result = await backfill_merge_commit_shas(merged, client)
        assert result[0].merge_commit_sha is None


class TestUpdateDeployStatuses:
    @pytest.mark.asyncio
    async def test_skips_deployed(self):
        merged = [make_pr(
            number=1, repo="o/r",
            merged_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            merge_commit_sha="sha1",
            acc_deploy=DeployStatus.ACC_DEPLOYED,
        )]
        client = _make_client()
        result = await update_deploy_statuses(merged, client, "acceptance", 20)
        assert result[0].acc_deploy == DeployStatus.ACC_DEPLOYED
        client.fetch_latest_deployment_sha.assert_not_called()

    @pytest.mark.asyncio
    async def test_marks_deployed_when_ahead(self):
        merged = [make_pr(
            number=1, repo="o/r",
            merged_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            merge_commit_sha="sha1",
            acc_deploy=DeployStatus.ACC_DEPLOYING,
        )]
        deploy_created = datetime(2024, 1, 1, tzinfo=timezone.utc)
        client = _make_client(
            fetch_latest_deployment_sha=AsyncMock(return_value=("deploy_sha", deploy_created)),
            compare_commits=AsyncMock(return_value="ahead"),
        )
        result = await update_deploy_statuses(merged, client, "acceptance", 20)
        assert result[0].acc_deploy == DeployStatus.ACC_DEPLOYED

    @pytest.mark.asyncio
    async def test_deployment_fetch_error(self):
        merged = [make_pr(
            number=1, repo="o/r",
            merged_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            merge_commit_sha="sha1",
            acc_deploy=DeployStatus.ACC_DEPLOYING,
        )]
        client = _make_client(
            fetch_latest_deployment_sha=AsyncMock(side_effect=Exception("network")),
        )
        result = await update_deploy_statuses(merged, client, "acceptance", 20)
        assert result[0].acc_deploy == DeployStatus.ACC_DEPLOYING

    @pytest.mark.asyncio
    async def test_compare_error(self):
        merged = [make_pr(
            number=1, repo="o/r",
            merged_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            merge_commit_sha="sha1",
            acc_deploy=DeployStatus.ACC_DEPLOYING,
        )]
        deploy_created = datetime(2024, 1, 1, tzinfo=timezone.utc)
        client = _make_client(
            fetch_latest_deployment_sha=AsyncMock(return_value=("deploy_sha", deploy_created)),
            compare_commits=AsyncMock(side_effect=Exception("timeout")),
        )
        result = await update_deploy_statuses(merged, client, "acceptance", 20)
        assert result[0].acc_deploy == DeployStatus.ACC_DEPLOYING

    @pytest.mark.asyncio
    async def test_skips_no_merge_commit_sha(self):
        merged = [make_pr(
            number=1, repo="o/r",
            merged_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            merge_commit_sha=None,
            acc_deploy=DeployStatus.ACC_DEPLOYING,
        )]
        client = _make_client()
        result = await update_deploy_statuses(merged, client, "acceptance", 20)
        client.fetch_latest_deployment_sha.assert_not_called()
