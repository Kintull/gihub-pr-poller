"""Data models for GitHub PR Tracker."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class CIStatus(Enum):
    """CI pipeline status."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    UNKNOWN = "unknown"

    @classmethod
    def from_github(cls, status: str, conclusion: str | None) -> CIStatus:
        """Convert GitHub check run status/conclusion to CIStatus."""
        if status == "queued":
            return cls.PENDING
        if status == "in_progress":
            return cls.RUNNING
        if status == "completed":
            if conclusion in ("success", "neutral", "skipped"):
                return cls.SUCCESS
            if conclusion in ("failure", "timed_out", "cancelled", "action_required"):
                return cls.FAILURE
            return cls.UNKNOWN
        return cls.UNKNOWN


class DeployStatus(Enum):
    """Deployment pipeline status."""

    ACC_DEPLOYING = "acc_deploying"
    ACC_DEPLOYED = "acc_deployed"
    NONE = "none"


class PRLabel(Enum):
    """Label describing user's relationship to a PR."""

    AUTHOR = "author"
    REVIEW_REQUESTED = "review_requested"
    MENTIONED = "mentioned"
    COMMENTED = "commented"


SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

CI_SYMBOLS = {
    CIStatus.PENDING: "⏳",
    CIStatus.SUCCESS: "🟢",
    CIStatus.FAILURE: "❌",
    CIStatus.UNKNOWN: "❓",
}


def ci_display(status: CIStatus, spinner_index: int = 0) -> str:
    """Return display string for a CI status."""
    if status == CIStatus.RUNNING:
        return SPINNER_FRAMES[spinner_index % len(SPINNER_FRAMES)]
    return CI_SYMBOLS[status]


ACC_DEPLOY_SYMBOLS = {
    DeployStatus.ACC_DEPLOYED: "🟢",
    DeployStatus.NONE: "\u2014",
}


def acc_deploy_display(status: DeployStatus, spinner_index: int = 0) -> str:
    """Return display string for a deploy status."""
    if status == DeployStatus.ACC_DEPLOYING:
        return SPINNER_FRAMES[spinner_index % len(SPINNER_FRAMES)]
    return ACC_DEPLOY_SYMBOLS[status]


@dataclass
class PullRequest:
    """Represents a GitHub Pull Request with associated metadata."""

    number: int
    title: str
    url: str
    branch_name: str
    comment_count: int
    approval_count: int
    ci_status: CIStatus
    jira_ticket: str | None
    jira_url: str | None
    author: str
    updated_at: datetime
    repo: str
    labels: frozenset[PRLabel] = field(default_factory=frozenset)
    acc_deploy: DeployStatus = field(default=DeployStatus.NONE)
    merged_at: datetime | None = field(default=None)
