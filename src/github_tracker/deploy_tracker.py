"""Merge detection and deployment status tracking."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime

from github_tracker.github_client import GitHubClient
from github_tracker.models import DeployStatus, PrdDeployStatus, PullRequest
from github_tracker.pr_service import compute_deploy_status

logger = logging.getLogger("github_tracker.deploy_tracker")

DEPLOY_BRANCHES = {"main", "master", "edge", "acceptance", "staging", "test"}


async def detect_newly_merged_prs(
    previous_open_prs: list[PullRequest],
    current_open_numbers: set[int],
    existing_merged: list[PullRequest],
    github_client: GitHubClient,
) -> list[PullRequest]:
    """Check previously open PRs that disappeared and return newly detected merged ones."""
    existing_keys = {(m.number, m.repo) for m in existing_merged}
    new_merged: list[PullRequest] = []
    for prev_pr in previous_open_prs:
        if prev_pr.number not in current_open_numbers:
            try:
                detail = await github_client.fetch_pr_detail(prev_pr.repo, prev_pr.number)
                merged_at_str = detail.get("merged_at")
                if merged_at_str:
                    base_ref = detail.get("base", {}).get("ref", "")
                    is_deploy_branch = base_ref in DEPLOY_BRANCHES
                    if not is_deploy_branch:
                        logger.info(
                            "PR #%d merged into feature branch %s, skipping deploy tracking",
                            prev_pr.number, base_ref,
                        )
                    merged_at = datetime.fromisoformat(merged_at_str.replace("Z", "+00:00"))
                    merge_commit_sha = detail.get("merge_commit_sha")
                    merged_pr = replace(
                        prev_pr,
                        merged_at=merged_at,
                        merge_commit_sha=merge_commit_sha,
                        acc_deploy=DeployStatus.ACC_DEPLOYING if is_deploy_branch else DeployStatus.NONE,
                    )
                    if (merged_pr.number, merged_pr.repo) not in existing_keys:
                        new_merged.append(merged_pr)
                        logger.info("Detected merged PR #%d in %s", prev_pr.number, prev_pr.repo)
            except Exception as e:
                logger.error("Error checking merge status for PR #%d: %s", prev_pr.number, e)
    return new_merged


async def filter_feature_branch_merges(
    merged_prs: list[PullRequest],
    github_client: GitHubClient,
) -> list[PullRequest]:
    """Set deploy status to NONE for PRs merged into feature branches."""
    result: list[PullRequest] = []
    for mpr in merged_prs:
        if mpr.acc_deploy == DeployStatus.ACC_DEPLOYED:
            result.append(mpr)
            continue
        try:
            detail = await github_client.fetch_pr_detail(mpr.repo, mpr.number)
            base_ref = detail.get("base", {}).get("ref", "")
            if base_ref in DEPLOY_BRANCHES:
                result.append(mpr)
            else:
                logger.info(
                    "PR #%d merged into feature branch %s, skipping deploy tracking",
                    mpr.number, base_ref,
                )
                result.append(replace(
                    mpr,
                    acc_deploy=DeployStatus.NONE,
                    prd_deploy=PrdDeployStatus.NONE,
                ))
        except Exception as e:
            logger.error("Error checking base branch for PR #%d: %s", mpr.number, e)
            result.append(mpr)
    return result


async def backfill_merge_commit_shas(
    merged_prs: list[PullRequest],
    github_client: GitHubClient,
) -> list[PullRequest]:
    """Backfill merge_commit_sha for merged PRs missing it. Returns updated list."""
    result = list(merged_prs)
    for i, mpr in enumerate(result):
        if mpr.merge_commit_sha is None and mpr.acc_deploy in (DeployStatus.ACC_DEPLOYING, DeployStatus.ACC_ARGO):
            try:
                detail = await github_client.fetch_pr_detail(mpr.repo, mpr.number)
                sha = detail.get("merge_commit_sha")
                if sha:
                    result[i] = replace(mpr, merge_commit_sha=sha)
                    logger.info("Backfilled merge_commit_sha for PR #%d", mpr.number)
            except Exception as e:
                logger.error("Error backfilling merge_commit_sha for PR #%d: %s", mpr.number, e)
    return result


async def update_deploy_statuses(
    merged_prs: list[PullRequest],
    github_client: GitHubClient,
    acc_deploy_environment: str,
    argo_cooldown_minutes: int,
) -> list[PullRequest]:
    """Check deployment status for merged PRs and return updated list."""
    result = list(merged_prs)
    prs_to_check = [
        (i, mpr) for i, mpr in enumerate(result)
        if mpr.acc_deploy in (DeployStatus.ACC_DEPLOYING, DeployStatus.ACC_ARGO) and mpr.merge_commit_sha is not None
    ]
    repos_with_merged = {mpr.repo for _, mpr in prs_to_check}
    deploy_sha_by_repo: dict[str, tuple[str | None, datetime | None]] = {}
    for repo in repos_with_merged:
        try:
            sha, created_at = await github_client.fetch_latest_deployment_sha(
                repo, acc_deploy_environment
            )
            deploy_sha_by_repo[repo] = (sha, created_at)
        except Exception as e:
            logger.error("Error fetching deployment for %s: %s", repo, e)
            deploy_sha_by_repo[repo] = (None, None)

    for i, mpr in prs_to_check:
        deploy_sha, deploy_created_at = deploy_sha_by_repo.get(mpr.repo, (None, None))
        compare_status: str | None = None
        if deploy_sha and mpr.merge_commit_sha:
            try:
                compare_status = await github_client.compare_commits(
                    mpr.repo, mpr.merge_commit_sha, deploy_sha
                )
            except Exception as e:
                logger.error("Error comparing commits for PR #%d: %s", mpr.number, e)
        new_status = compute_deploy_status(
            mpr, compare_status, deploy_created_at, argo_cooldown_minutes
        )
        result[i] = replace(mpr, acc_deploy=new_status)
    return result
