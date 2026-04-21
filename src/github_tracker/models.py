"""Data models for GitHub PR Tracker."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from rich.text import Text

from github_tracker.theme import Color


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
    ACC_ARGO = "acc_argo"
    NONE = "none"


class PrdDeployStatus(Enum):
    """PRD deployment pipeline status."""

    PRD_DEPLOYING = "prd_deploying"
    PRD_DEPLOYED = "prd_deployed"
    PRD_ARGO = "prd_argo"
    NONE = "none"


class PRLabel(Enum):
    """Label describing user's relationship to a PR."""

    AUTHOR = "author"
    REVIEW_REQUESTED = "review_requested"
    MENTIONED = "mentioned"
    COMMENTED = "commented"
    FAVOURITE = "favourite"


SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

CI_SYMBOLS: dict[CIStatus, Text] = {
    CIStatus.PENDING: Text("⏳", style=Color.YELLOW),
    CIStatus.SUCCESS: Text("✓", style=Color.GREEN),
    CIStatus.FAILURE: Text("✗", style=Color.RED),
    CIStatus.UNKNOWN: Text("?", style=Color.RED),
}


def ci_display(status: CIStatus, spinner_index: int = 0, completed: int = 0, total: int = 0) -> str | Text:
    """Return display string for a CI status."""
    if status == CIStatus.RUNNING:
        frame = SPINNER_FRAMES[spinner_index % len(SPINNER_FRAMES)]
        if total > 0:
            return f"{frame}({completed}/{total})"
        return f"{frame}({frame}/{frame})"
    return CI_SYMBOLS[status]


ACC_DEPLOY_SYMBOLS: dict[DeployStatus, str | Text] = {
    DeployStatus.ACC_DEPLOYED: Text("✓", style=Color.GREEN),
    DeployStatus.NONE: "\u2014",
}


def acc_deploy_display(status: DeployStatus, spinner_index: int = 0, completed: int = 0, total: int = 0) -> str | Text:
    """Return display string for a deploy status."""
    if status == DeployStatus.ACC_DEPLOYING:
        frame = SPINNER_FRAMES[spinner_index % len(SPINNER_FRAMES)]
        if total > 0:
            pct = completed * 100 // total
            return f"{frame}{pct}%"
        return frame
    if status == DeployStatus.ACC_ARGO:
        return f"{SPINNER_FRAMES[spinner_index % len(SPINNER_FRAMES)]}ARGO"
    return ACC_DEPLOY_SYMBOLS[status]


PRD_DEPLOY_SYMBOLS: dict[PrdDeployStatus, str | Text] = {
    PrdDeployStatus.PRD_DEPLOYED: Text("✓", style=Color.GREEN),
    PrdDeployStatus.NONE: "\u2014",
}


def prd_deploy_display(status: PrdDeployStatus, spinner_index: int = 0, completed: int = 0, total: int = 0) -> str | Text:
    """Return display string for a PRD deploy status."""
    if status == PrdDeployStatus.PRD_DEPLOYING:
        return SPINNER_FRAMES[spinner_index % len(SPINNER_FRAMES)]
    if status == PrdDeployStatus.PRD_ARGO:
        return f"{SPINNER_FRAMES[spinner_index % len(SPINNER_FRAMES)]}ARGO"
    return PRD_DEPLOY_SYMBOLS[status]


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
    base_branch: str = field(default="")
    labels: frozenset[PRLabel] = field(default_factory=frozenset)
    acc_deploy: DeployStatus = field(default=DeployStatus.NONE)
    prd_deploy: PrdDeployStatus = field(default=PrdDeployStatus.NONE)
    merged_at: datetime | None = field(default=None)
    ci_completed_steps: int = field(default=0)
    ci_total_steps: int = field(default=0)
    acc_completed_steps: int = field(default=0)
    acc_total_steps: int = field(default=0)
    prd_completed_steps: int = field(default=0)
    prd_total_steps: int = field(default=0)
    merge_commit_sha: str | None = field(default=None)
    user_approved: bool = field(default=False)
    total_threads: int = field(default=0)
    unresolved_threads: int = field(default=0)
    my_commented_threads: int = field(default=0)
    my_unresolved_threads: int = field(default=0)
