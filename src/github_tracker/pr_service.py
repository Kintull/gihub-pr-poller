"""Business logic for PR label computation and grouping."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from github_tracker.models import DeployStatus, PRLabel, PullRequest


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
    """Split PRs into (my_prs, other_prs). "Mine" = has any label. Preserves ordering."""
    my_prs: list[PullRequest] = []
    other_prs: list[PullRequest] = []
    for pr in prs:
        if pr.labels:
            my_prs.append(pr)
        else:
            other_prs.append(pr)
    return my_prs, other_prs


def compute_acc_deploy(
    pr: PullRequest, workflow_runs: list[dict], cooldown_minutes: int
) -> DeployStatus:
    """Compute ACC deploy status for a merged PR based on workflow runs."""
    if pr.merged_at is None:
        return DeployStatus.NONE

    now = datetime.now(tz=timezone.utc)
    merged_at = pr.merged_at if pr.merged_at.tzinfo else pr.merged_at.replace(tzinfo=timezone.utc)

    for run in workflow_runs:
        run_created_str = run.get("created_at", "")
        try:
            run_created = datetime.fromisoformat(run_created_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue

        if run_created < merged_at:
            continue

        status = run.get("status", "")
        conclusion = run.get("conclusion")

        if status in ("queued", "in_progress"):
            return DeployStatus.ACC_DEPLOYING

        if status == "completed" and conclusion == "success":
            run_completed_str = run.get("updated_at", "")
            try:
                run_completed = datetime.fromisoformat(run_completed_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                return DeployStatus.ACC_DEPLOYING
            if now >= run_completed + timedelta(minutes=cooldown_minutes):
                return DeployStatus.ACC_DEPLOYED
            return DeployStatus.ACC_DEPLOYING

        # completed + non-success (failure/skipped): waiting for retry
        if status == "completed":
            return DeployStatus.ACC_DEPLOYING

    # No relevant runs found — pipeline hasn't started yet
    return DeployStatus.ACC_DEPLOYING


def filter_expired_merged_prs(
    merged_prs: list[PullRequest], retention_days: int
) -> list[PullRequest]:
    """Remove deployed PRs past the retention period."""
    now = datetime.now(tz=timezone.utc)
    cutoff = now - timedelta(days=retention_days)
    result: list[PullRequest] = []
    for pr in merged_prs:
        if pr.acc_deploy == DeployStatus.ACC_DEPLOYED and pr.merged_at is not None:
            merged_at = pr.merged_at if pr.merged_at.tzinfo else pr.merged_at.replace(tzinfo=timezone.utc)
            if merged_at < cutoff:
                continue
        result.append(pr)
    return result
