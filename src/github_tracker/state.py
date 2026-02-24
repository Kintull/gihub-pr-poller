"""State file management for caching PR data between sessions."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from github_tracker.models import CIStatus, DeployStatus, PRLabel, PullRequest

logger = logging.getLogger("github_tracker.state")

STATE_FILE = Path.cwd() / ".github-tracker-state.json"
CURRENT_VERSION = 3

_REQUIRED_PR_KEYS = {"number", "title", "author", "url", "repo"}


def load_state(path: Path = STATE_FILE) -> tuple[list[PullRequest], list[PullRequest]]:
    """Load cached PRs from state file. Returns (open_prs, merged_prs)."""
    if not path.exists():
        logger.info("No state file found at %s", path)
        return ([], [])

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to read state file: %s", e)
        return ([], [])

    return _validate_state(data)


def save_state(
    prs: list[PullRequest],
    merged_prs: list[PullRequest] | None = None,
    path: Path = STATE_FILE,
) -> None:
    """Save basic PR data to state file."""
    state = {
        "version": CURRENT_VERSION,
        "cached_at": datetime.now(tz=timezone.utc).isoformat(),
        "pull_requests": [_pr_to_dict(pr) for pr in prs],
        "merged_prs": [_merged_pr_to_dict(pr) for pr in (merged_prs or [])],
    }
    try:
        path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        logger.info("Saved state: %d open + %d merged PRs to %s", len(prs), len(merged_prs or []), path)
    except OSError as e:
        logger.error("Failed to write state file: %s", e)


def _validate_state(data: object) -> tuple[list[PullRequest], list[PullRequest]]:
    """Validate state structure and return (open_prs, merged_prs). Logs warnings for issues."""
    if not isinstance(data, dict):
        logger.warning("State file is not a JSON object")
        return ([], [])

    version = data.get("version")
    if version not in (1, 2, CURRENT_VERSION):
        logger.warning("Unknown state version: %r (expected %d)", version, CURRENT_VERSION)
        return ([], [])

    pr_list = data.get("pull_requests")
    if not isinstance(pr_list, list):
        logger.warning("State 'pull_requests' is not a list")
        return ([], [])

    results: list[PullRequest] = []
    for i, entry in enumerate(pr_list):
        pr = _dict_to_pr(entry)
        if pr is None:
            logger.warning("Skipping invalid PR entry at index %d", i)
        else:
            results.append(pr)

    merged_results: list[PullRequest] = []
    merged_list = data.get("merged_prs")
    if isinstance(merged_list, list):
        for i, entry in enumerate(merged_list):
            pr = _dict_to_merged_pr(entry)
            if pr is None:
                logger.warning("Skipping invalid merged PR entry at index %d", i)
            else:
                merged_results.append(pr)

    logger.info("Loaded %d open + %d merged cached PRs from state file", len(results), len(merged_results))
    return (results, merged_results)


def _pr_to_dict(pr: PullRequest) -> dict:
    """Serialize a PullRequest to a state dict (basic fields only)."""
    return {
        "number": pr.number,
        "title": pr.title,
        "author": pr.author,
        "url": pr.url,
        "branch_name": pr.branch_name,
        "jira_ticket": pr.jira_ticket,
        "jira_url": pr.jira_url,
        "repo": pr.repo,
        "updated_at": pr.updated_at.isoformat(),
        "labels": [label.value for label in pr.labels],
    }


def _dict_to_pr(d: object) -> PullRequest | None:
    """Deserialize a state dict to a PullRequest, or None if invalid."""
    if not isinstance(d, dict):
        return None

    if not _REQUIRED_PR_KEYS.issubset(d.keys()):
        return None

    if not isinstance(d["number"], int) or d["number"] < 1:
        return None

    for key in ("title", "author", "url", "repo"):
        if not isinstance(d[key], str):
            return None

    try:
        updated_str = d.get("updated_at", "")
        updated_at = datetime.fromisoformat(updated_str)
    except (ValueError, TypeError):
        updated_at = datetime.now(tz=timezone.utc)

    label_values = d.get("labels") or []
    labels: set[PRLabel] = set()
    for val in label_values:
        try:
            labels.add(PRLabel(val))
        except ValueError:
            logger.warning("Unknown label value: %r — skipping", val)

    return PullRequest(
        number=d["number"],
        title=d["title"],
        author=d["author"],
        url=d["url"],
        branch_name=d.get("branch_name", ""),
        comment_count=0,
        approval_count=0,
        ci_status=CIStatus.PENDING,
        jira_ticket=d.get("jira_ticket"),
        jira_url=d.get("jira_url"),
        repo=d["repo"],
        updated_at=updated_at,
        labels=frozenset(labels),
    )


def _merged_pr_to_dict(pr: PullRequest) -> dict:
    """Serialize a merged PullRequest to a state dict."""
    d = _pr_to_dict(pr)
    d["merged_at"] = pr.merged_at.isoformat() if pr.merged_at else None
    d["acc_deploy"] = pr.acc_deploy.value
    return d


def _dict_to_merged_pr(d: object) -> PullRequest | None:
    """Deserialize a state dict to a merged PullRequest, or None if invalid."""
    if not isinstance(d, dict):
        return None

    pr = _dict_to_pr(d)
    if pr is None:
        return None

    merged_at = None
    merged_at_str = d.get("merged_at")
    if merged_at_str:
        try:
            merged_at = datetime.fromisoformat(merged_at_str)
        except (ValueError, TypeError):
            merged_at = None

    acc_deploy = DeployStatus.NONE
    acc_deploy_str = d.get("acc_deploy")
    if acc_deploy_str:
        try:
            acc_deploy = DeployStatus(acc_deploy_str)
        except ValueError:
            logger.warning("Unknown acc_deploy value: %r — using NONE", acc_deploy_str)

    return PullRequest(
        number=pr.number,
        title=pr.title,
        author=pr.author,
        url=pr.url,
        branch_name=pr.branch_name,
        comment_count=0,
        approval_count=0,
        ci_status=pr.ci_status,
        jira_ticket=pr.jira_ticket,
        jira_url=pr.jira_url,
        repo=pr.repo,
        updated_at=pr.updated_at,
        labels=pr.labels,
        merged_at=merged_at,
        acc_deploy=acc_deploy,
    )
