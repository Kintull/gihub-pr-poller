"""PR refresh logic — standalone async functions for fetching and updating PR data."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from github_tracker.github_client import GitHubClient, _aggregate_ci_status, count_approvals
from github_tracker.models import CIStatus, DeployStatus, PRLabel, PrdDeployStatus, PullRequest
from github_tracker.pr_service import (
    compute_ci_progress,
    compute_phase1_labels,
    compute_phase2_labels,
    compute_thread_counts,
    compute_user_approved,
)

logger = logging.getLogger("github_tracker.refresh")


async def fetch_pr_lists(
    repos: list[str],
    github_client: GitHubClient,
    jira_base_url: str,
    github_username: str,
    favourite_keys: set[tuple[int, str]],
    known_keys: set[tuple[int, str]],
    notify_error: Callable[[str, Exception], None] | None = None,
) -> tuple[list[PullRequest], list[tuple[str, dict]], set[tuple[int, str]]]:
    """Phase 1: Fetch PR lists from all repos.

    Returns (all_prs, raw_data, new_pr_keys).
    """
    all_prs: list[PullRequest] = []
    raw_data: list[tuple[str, dict]] = []
    new_pr_keys: set[tuple[int, str]] = set()

    for repo in repos:
        try:
            logger.info("Fetching PR list for repo: %s", repo)
            raw_prs = await github_client.fetch_open_prs(repo)
            for raw_pr in raw_prs:
                pr = github_client.parse_pr_basic(raw_pr, repo, jira_base_url)
                labels = compute_phase1_labels(pr, raw_pr, github_username)
                key = (pr.number, repo)
                if key in favourite_keys:
                    labels = labels | {PRLabel.FAVOURITE}
                elif key not in known_keys:
                    if labels:
                        labels = labels | {PRLabel.FAVOURITE}
                    else:
                        new_pr_keys.add(key)
                if raw_pr.get("draft"):
                    labels = labels | {PRLabel.DRAFT}
                pr = replace(pr, labels=labels)
                all_prs.append(pr)
                raw_data.append((repo, raw_pr))
            logger.info("Got %d PRs from %s", len(raw_prs), repo)
        except Exception as e:
            logger.error("Error fetching PR list for %s: %s", repo, e, exc_info=True)
            if notify_error:
                notify_error(repo, e)

    all_prs.sort(key=lambda p: p.updated_at, reverse=True)
    raw_data.sort(key=lambda item: item[1].get("updated_at", ""), reverse=True)
    return all_prs, raw_data, new_pr_keys


async def backfill_pr_details(
    raw_data: list[tuple[str, dict]],
    all_prs: list[PullRequest],
    github_client: GitHubClient,
    github_username: str,
    new_pr_keys: set[tuple[int, str]],
    find_pr: Callable[[int], PullRequest | None],
    update_pr: Callable[[PullRequest], None],
) -> list[PullRequest]:
    """Phase 2: Backfill reviews + CI status for each open PR.

    Returns updated all_prs list (same order as input).
    """
    result = list(all_prs)
    # Process FAVOURITE PRs first so "My PRs" table updates before "Other PRs"
    indices = sorted(
        range(len(raw_data)),
        key=lambda i: 0 if PRLabel.FAVOURITE in all_prs[i].labels else 1,
    )
    for i in indices:
        repo, raw_pr = raw_data[i]
        pr_number = raw_pr["number"]
        head_sha = raw_pr["head"]["sha"]

        # Skip checks/CI/threads for non-author drafts — they render as em-dashes.
        existing_labels = all_prs[i].labels
        if PRLabel.DRAFT in existing_labels and PRLabel.AUTHOR not in existing_labels:
            continue

        try:
            reviews, check_runs, pr_detail, threads = await asyncio.gather(
                github_client.fetch_reviews(repo, pr_number),
                github_client.fetch_check_runs(repo, head_sha),
                github_client.fetch_pr_detail(repo, pr_number),
                github_client.fetch_review_threads(repo, pr_number),
            )
        except Exception as e:
            logger.error("Error loading details for PR #%d: %s", pr_number, e)
            continue

        approval_count = count_approvals(reviews)
        ci_status = _aggregate_ci_status(check_runs)
        ci_completed, ci_total = compute_ci_progress(check_runs)
        comment_count = pr_detail.get("comments", 0) + pr_detail.get("review_comments", 0)
        user_approved = compute_user_approved(reviews, github_username)
        total_threads, unresolved_threads, my_commented, my_unresolved = compute_thread_counts(
            threads, github_username
        )

        pr = find_pr(pr_number)
        if pr is None:
            continue

        new_labels = compute_phase2_labels(pr.labels, reviews, github_username)
        if (pr_number, repo) in new_pr_keys and PRLabel.COMMENTED in new_labels and PRLabel.FAVOURITE not in new_labels:
            new_labels = new_labels | {PRLabel.FAVOURITE}
        updated_pr = replace(
            pr,
            approval_count=approval_count,
            ci_status=ci_status,
            ci_completed_steps=ci_completed,
            ci_total_steps=ci_total,
            comment_count=comment_count,
            labels=new_labels,
            user_approved=user_approved,
            total_threads=total_threads,
            unresolved_threads=unresolved_threads,
            my_commented_threads=my_commented,
            my_unresolved_threads=my_unresolved,
        )
        update_pr(updated_pr)
        result[i] = updated_pr

    return result


async def fetch_user_merged_prs(
    repos: list[str],
    github_client: GitHubClient,
    jira_base_url: str,
    github_username: str,
    days: int,
) -> list[PullRequest]:
    """Fetch PRs authored by the user that merged within the last `days` days.

    Returns PullRequests labelled AUTHOR with merged_at + merge_commit_sha set.
    Deploy statuses are seeded as ACC_DEPLOYING / PRD_DEPLOYING so the standard
    deploy-tracking pipeline (filter_feature_branch_merges, update_deploy_statuses,
    PRD compare) can resolve them to actual states. Sorted by merged_at desc.
    """
    if not github_username or days <= 0:
        return []

    since = datetime.now(tz=timezone.utc) - timedelta(days=days)
    collected: list[PullRequest] = []

    for repo in repos:
        try:
            raw_prs = await github_client.fetch_recent_merged_prs_by_author(
                repo, github_username, since
            )
        except Exception as e:
            logger.error("Error fetching user merged PRs for %s: %s", repo, e)
            continue

        for raw in raw_prs:
            pr = github_client.parse_pr_basic(raw, repo, jira_base_url)
            merged_str = raw.get("merged_at") or ""
            try:
                merged_at = datetime.fromisoformat(merged_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
            collected.append(replace(
                pr,
                merged_at=merged_at,
                merge_commit_sha=raw.get("merge_commit_sha"),
                ci_status=CIStatus.SUCCESS,
                labels=frozenset({PRLabel.AUTHOR}),
                acc_deploy=DeployStatus.ACC_DEPLOYING,
                prd_deploy=PrdDeployStatus.PRD_DEPLOYING,
            ))

    collected.sort(key=lambda p: p.merged_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return collected


async def refresh_open_pr_details(
    open_prs: list[PullRequest],
    github_client: GitHubClient,
    github_username: str,
    update_pr: Callable[[PullRequest], None],
) -> list[PullRequest]:
    """Fetch fresh detail/reviews/CI/threads for each PR and call update_pr.

    Returns list of PRs discovered to be merged (so callers can move them).
    """
    newly_merged: list[PullRequest] = []
    for pr in open_prs:
        try:
            pr_detail = await github_client.fetch_pr_detail(pr.repo, pr.number)

            merged_at_str = pr_detail.get("merged_at")
            if merged_at_str:
                merged_at = datetime.fromisoformat(merged_at_str.replace("Z", "+00:00"))
                newly_merged.append(replace(
                    pr,
                    merged_at=merged_at,
                    merge_commit_sha=pr_detail.get("merge_commit_sha"),
                    ci_status=CIStatus.SUCCESS,
                    ci_completed_steps=0,
                    ci_total_steps=0,
                    acc_deploy=DeployStatus.ACC_DEPLOYING,
                    prd_deploy=PrdDeployStatus.PRD_DEPLOYING,
                ))
                logger.info("Discovered merged PR #%d during refresh", pr.number)
                continue

            head_sha = (pr_detail.get("head") or {}).get("sha", "")
            if not head_sha:
                continue

            phase1_labels = compute_phase1_labels(pr, pr_detail, github_username)
            if PRLabel.FAVOURITE in pr.labels:
                phase1_labels = phase1_labels | {PRLabel.FAVOURITE}
            if pr_detail.get("draft"):
                phase1_labels = phase1_labels | {PRLabel.DRAFT}

            # Skip checks/CI/threads for non-author drafts.
            if PRLabel.DRAFT in phase1_labels and PRLabel.AUTHOR not in phase1_labels:
                update_pr(replace(pr, labels=phase1_labels))
                continue

            reviews, check_runs, threads = await asyncio.gather(
                github_client.fetch_reviews(pr.repo, pr.number),
                github_client.fetch_check_runs(pr.repo, head_sha),
                github_client.fetch_review_threads(pr.repo, pr.number),
            )
        except Exception as e:
            logger.error("Error refreshing PR #%d: %s", pr.number, e)
            continue

        approval_count = count_approvals(reviews)
        ci_status = _aggregate_ci_status(check_runs)
        ci_completed, ci_total = compute_ci_progress(check_runs)
        comment_count = pr_detail.get("comments", 0) + pr_detail.get("review_comments", 0)
        user_approved = compute_user_approved(reviews, github_username)
        total_threads, unresolved_threads, my_commented, my_unresolved = compute_thread_counts(
            threads, github_username
        )
        new_labels = compute_phase2_labels(phase1_labels, reviews, github_username)
        updated_pr = replace(
            pr,
            approval_count=approval_count,
            ci_status=ci_status,
            ci_completed_steps=ci_completed,
            ci_total_steps=ci_total,
            comment_count=comment_count,
            labels=new_labels,
            user_approved=user_approved,
            total_threads=total_threads,
            unresolved_threads=unresolved_threads,
            my_commented_threads=my_commented,
            my_unresolved_threads=my_unresolved,
        )
        update_pr(updated_pr)

    return newly_merged
