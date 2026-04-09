"""Business logic for PR label computation and grouping."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from github_tracker.models import DeployStatus, PRLabel, PrdDeployStatus, PullRequest


def compute_phase1_labels(
    pr: PullRequest, raw_pr: dict, github_username: str
) -> frozenset[PRLabel]:
    """Compute labels from Phase 1 data (PR list response).

    Checks: AUTHOR, REVIEW_REQUESTED, MENTIONED.
    Case-insensitive comparisons. Empty username returns empty labels.
    """
    if not github_username:
        return frozenset()

    username_lower = github_username.lower()
    labels: set[PRLabel] = set()

    if pr.author.lower() == username_lower:
        labels.add(PRLabel.AUTHOR)

    requested_reviewers = raw_pr.get("requested_reviewers") or []
    for reviewer in requested_reviewers:
        login = (reviewer.get("login") or "").lower()
        if login == username_lower:
            labels.add(PRLabel.REVIEW_REQUESTED)
            break

    body = raw_pr.get("body") or ""
    if re.search(rf"@{re.escape(github_username)}\b", body, re.IGNORECASE):
        labels.add(PRLabel.MENTIONED)

    return frozenset(labels)


def compute_phase2_labels(
    existing_labels: frozenset[PRLabel],
    reviews: list[dict],
    github_username: str,
) -> frozenset[PRLabel]:
    """Compute labels from Phase 2 data (reviews), merged with existing.

    Checks: COMMENTED (any review by username).
    """
    if not github_username:
        return existing_labels

    username_lower = github_username.lower()

    for review in reviews:
        user = (review.get("user") or {}).get("login", "")
        if user.lower() == username_lower:
            return existing_labels | {PRLabel.COMMENTED}

    return existing_labels


def group_prs(
    prs: list[PullRequest],
) -> tuple[list[PullRequest], list[PullRequest]]:
    """Split PRs into (my_prs, other_prs). "Mine" = has FAVOURITE label.

    Within other_prs, related PRs (any non-FAVOURITE interest label) sort before
    unrelated ones. Existing ordering is preserved within each tier.
    """
    my_prs = [pr for pr in prs if PRLabel.FAVOURITE in pr.labels]
    other_prs = [pr for pr in prs if PRLabel.FAVOURITE not in pr.labels]
    other_prs.sort(key=lambda pr: 0 if pr.labels - {PRLabel.FAVOURITE} else 1)
    return my_prs, other_prs


def compute_thread_counts(
    threads: list[dict], github_username: str
) -> tuple[int, int, int, int]:
    """Return (total_threads, unresolved_threads, my_commented_threads, my_unresolved_threads).

    my_* counts only threads where github_username left at least one comment.
    """
    total = len(threads)
    unresolved = sum(1 for t in threads if not t.get("isResolved"))
    username_lower = github_username.lower() if github_username else ""
    my_commented = 0
    my_unresolved = 0
    for thread in threads:
        authors = [
            ((c.get("author") or {}).get("login") or "").lower()
            for c in ((thread.get("comments") or {}).get("nodes") or [])
        ]
        if username_lower and username_lower in authors:
            my_commented += 1
            if not thread.get("isResolved"):
                my_unresolved += 1
    return total, unresolved, my_commented, my_unresolved


def compute_user_approved(reviews: list[dict], github_username: str) -> bool:
    """Return True if the user's most recent review state is APPROVED."""
    if not github_username:
        return False

    username_lower = github_username.lower()
    last_state: str | None = None
    for review in reviews:
        user = (review.get("user") or {}).get("login", "")
        if user.lower() == username_lower:
            last_state = review.get("state", "")
    return last_state == "APPROVED"


def compute_ci_progress(check_runs: list[dict]) -> tuple[int, int]:
    """Return (completed, total) step counts from check runs."""
    total = len(check_runs)
    completed = sum(1 for r in check_runs if r.get("status") == "completed")
    return completed, total


def compute_deploy_status(
    pr: PullRequest,
    compare_status: str | None,
    deploy_created_at: datetime | None,
    argo_cooldown_minutes: int,
) -> DeployStatus:
    """Compute deploy status from Deployments API comparison result."""
    if pr.merged_at is None:
        return DeployStatus.NONE

    if pr.merge_commit_sha is None:
        return DeployStatus.ACC_DEPLOYING

    if compare_status in ("ahead", "identical"):
        if deploy_created_at is not None:
            now = datetime.now(tz=timezone.utc)
            created = deploy_created_at if deploy_created_at.tzinfo else deploy_created_at.replace(tzinfo=timezone.utc)
            if now < created + timedelta(minutes=argo_cooldown_minutes):
                return DeployStatus.ACC_ARGO
        return DeployStatus.ACC_DEPLOYED

    return DeployStatus.ACC_DEPLOYING


def compute_prd_deploy_status(
    pr: PullRequest,
    compare_status: str | None,
    deploy_created_at: datetime | None,
    argo_cooldown_minutes: int,
) -> PrdDeployStatus:
    """Compute PRD deploy status from Deployments API comparison result."""
    if pr.merged_at is None:
        return PrdDeployStatus.NONE

    if pr.merge_commit_sha is None:
        return PrdDeployStatus.PRD_DEPLOYING

    if compare_status in ("ahead", "identical"):
        if deploy_created_at is not None:
            now = datetime.now(tz=timezone.utc)
            created = deploy_created_at if deploy_created_at.tzinfo else deploy_created_at.replace(tzinfo=timezone.utc)
            if now < created + timedelta(minutes=argo_cooldown_minutes):
                return PrdDeployStatus.PRD_ARGO
        return PrdDeployStatus.PRD_DEPLOYED

    return PrdDeployStatus.PRD_DEPLOYING


def filter_expired_merged_prs(
    merged_prs: list[PullRequest], retention_days: int
) -> list[PullRequest]:
    """Remove deployed PRs past the retention period.

    A merged PR is only removed if both ACC and PRD are fully deployed
    and the merge time is past the retention cutoff.
    """
    now = datetime.now(tz=timezone.utc)
    cutoff = now - timedelta(days=retention_days)
    result: list[PullRequest] = []
    for pr in merged_prs:
        if (
            pr.acc_deploy == DeployStatus.ACC_DEPLOYED
            and pr.prd_deploy == PrdDeployStatus.PRD_DEPLOYED
            and pr.merged_at is not None
        ):
            merged_at = pr.merged_at if pr.merged_at.tzinfo else pr.merged_at.replace(tzinfo=timezone.utc)
            if merged_at < cutoff:
                continue
        result.append(pr)
    return result
