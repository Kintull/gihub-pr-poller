"""Shared test fixtures and factories."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from github_tracker.models import CIStatus, DeployStatus, PRLabel, PullRequest


def make_pr(**overrides) -> PullRequest:
    """Factory for creating PullRequest instances with sensible defaults."""
    defaults = {
        "number": 1,
        "title": "Test PR",
        "url": "https://github.com/owner/repo/pull/1",
        "branch_name": "feature-branch",
        "comment_count": 0,
        "approval_count": 0,
        "ci_status": CIStatus.SUCCESS,
        "jira_ticket": None,
        "jira_url": None,
        "author": "testuser",
        "updated_at": datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
        "repo": "owner/repo",
        "labels": frozenset(),
        "acc_deploy": DeployStatus.NONE,
        "merged_at": None,
        "ci_completed_steps": 0,
        "ci_total_steps": 0,
        "acc_completed_steps": 0,
        "acc_total_steps": 0,
        "user_approved": False,
        "total_threads": 0,
        "unresolved_threads": 0,
        "my_commented_threads": 0,
        "my_unresolved_threads": 0,
    }
    defaults.update(overrides)
    return PullRequest(**defaults)


def make_github_pr_response(**overrides) -> dict:
    """Factory for creating GitHub API PR response dicts."""
    defaults = {
        "number": 1,
        "title": "Test PR",
        "html_url": "https://github.com/owner/repo/pull/1",
        "head": {"sha": "abc123", "ref": "feature-branch"},
        "user": {"login": "testuser"},
        "comments": 2,
        "review_comments": 3,
        "updated_at": "2024-06-15T12:00:00Z",
        "requested_reviewers": [],
        "body": "",
    }
    defaults.update(overrides)
    return defaults


def make_review_response(state: str = "APPROVED", user: str = "reviewer") -> dict:
    """Factory for creating GitHub review response dicts."""
    return {"state": state, "user": {"login": user}}


def make_check_run_response(
    status: str = "completed", conclusion: str | None = "success"
) -> dict:
    """Factory for creating GitHub check run response dicts."""
    return {"status": status, "conclusion": conclusion, "name": "CI"}


def make_workflow_run_response(
    status: str = "completed",
    conclusion: str | None = "success",
    created_at: str = "2024-06-15T13:00:00Z",
    updated_at: str = "2024-06-15T13:10:00Z",
    id: int = 12345,
) -> dict:
    """Factory for creating GitHub workflow run response dicts."""
    return {
        "id": id,
        "status": status,
        "conclusion": conclusion,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def make_review_thread(is_resolved: bool = False, authors: list[str] | None = None) -> dict:
    """Factory for creating a GraphQL review thread node."""
    return {
        "isResolved": is_resolved,
        "comments": {
            "nodes": [{"author": {"login": a}} for a in (authors or [])]
        },
    }


def make_review_threads_response(threads: list[dict]) -> dict:
    """Factory for a GraphQL reviewThreads response."""
    return {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": threads}}}}}


def make_workflow_run_jobs_response(jobs: list[dict] | None = None) -> dict:
    """Factory for creating GitHub workflow run jobs response dicts."""
    if jobs is None:
        jobs = [
            {"status": "completed", "name": "build"},
            {"status": "in_progress", "name": "test"},
        ]
    return {"jobs": jobs}


@pytest.fixture
def sample_pr():
    return make_pr()


@pytest.fixture
def sample_prs():
    return [
        make_pr(number=1, title="Add login", approval_count=2, ci_status=CIStatus.SUCCESS),
        make_pr(
            number=2,
            title="Fix navbar",
            comment_count=3,
            ci_status=CIStatus.RUNNING,
            jira_ticket="PROJ-456",
            jira_url="https://jira.example.com/browse/PROJ-456",
        ),
        make_pr(number=3, title="Update deps", ci_status=CIStatus.FAILURE),
    ]
