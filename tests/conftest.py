"""Shared test fixtures and factories."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from github_tracker.models import CIStatus, DeployStatus, PRLabel, PrdDeployStatus, PullRequest


def make_pr(**overrides) -> PullRequest:
    """Factory for creating PullRequest instances with sensible defaults."""
    defaults = {
        "number": 1,
        "title": "Test PR",
        "url": "https://github.com/owner/repo/pull/1",
        "branch_name": "feature-branch",
        "base_branch": "main",
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
        "prd_deploy": PrdDeployStatus.NONE,
        "merged_at": None,
        "ci_completed_steps": 0,
        "ci_total_steps": 0,
        "acc_completed_steps": 0,
        "acc_total_steps": 0,
        "prd_completed_steps": 0,
        "prd_total_steps": 0,
        "merge_commit_sha": None,
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
        "base": {"ref": "main"},
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
