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


def compute_acc_deploy(
    pr: PullRequest,
    workflow_runs: list[dict],
    cooldown_minutes: int,
    jobs_by_run_id: dict[int, list[dict]] | None = None,
) -> tuple[DeployStatus, int, int]:
    """Compute ACC deploy status for a merged PR based on workflow runs.

    Returns (status, completed_steps, total_steps).
    """
    if pr.merged_at is None:
        return DeployStatus.NONE, 0, 0

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
            completed, total = 0, 0
            if jobs_by_run_id is not None:
                run_id = run.get("id")
                if run_id is not None:
                    jobs = jobs_by_run_id.get(run_id, [])
                    completed = sum(1 for j in jobs if j.get("status") == "completed")
                    total = len(jobs)
            return DeployStatus.ACC_DEPLOYING, completed, total

        if status == "completed" and conclusion == "success":
            run_completed_str = run.get("updated_at", "")
            try:
                run_completed = datetime.fromisoformat(run_completed_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                return DeployStatus.ACC_DEPLOYING, 0, 0
            if now >= run_completed + timedelta(minutes=cooldown_minutes):
                return DeployStatus.ACC_DEPLOYED, 0, 0
            return DeployStatus.ACC_ARGO, 0, 0

        # completed + skipped: workflow triggered but all jobs were conditionally skipped
        # (e.g. waiting for image build to finish). Not a real deploy attempt — keep looking.
        if status == "completed" and conclusion == "skipped":
            continue

        # completed + other non-success (failure, cancelled, etc.): waiting for retry
        if status == "completed":
            return DeployStatus.ACC_DEPLOYING, 0, 0

    # No relevant runs found — pipeline hasn't started yet
    return DeployStatus.ACC_DEPLOYING, 0, 0


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
