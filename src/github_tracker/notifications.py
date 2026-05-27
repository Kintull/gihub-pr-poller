"""Favourite PR notification logic and persistence."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from github_tracker.models import CIStatus, DeployStatus, PRLabel, PullRequest

logger = logging.getLogger("github_tracker.notifications")

NOTIFIED_FILE = Path.home() / ".github-tracker-notified.json"


@dataclass(frozen=True)
class FavEvent:
    """A pending desktop notification for a favourite PR transition."""

    event_id: str
    title: str
    message: str
    url: str


def compute_fav_events(
    prs: Iterable[PullRequest],
    already_notified: set[str],
) -> list[FavEvent]:
    """Return notification events for favourite PRs not yet notified.

    Events:
      - ci_ready: open PR with CI=SUCCESS (message differs by auto_merge_enabled)
      - merged: PR has merged_at set and tracked deploy (acc_deploy != NONE)
      - acc_deployed: PR reached DeployStatus.ACC_DEPLOYED
    """
    events: list[FavEvent] = []
    for pr in prs:
        if PRLabel.FAVOURITE not in pr.labels:
            continue
        prefix = f"{pr.repo}#{pr.number}"

        if pr.merged_at is None and pr.ci_status == CIStatus.SUCCESS:
            eid = f"{prefix}:ci_ready"
            if eid not in already_notified:
                if pr.auto_merge_enabled:
                    events.append(FavEvent(
                        event_id=eid,
                        title=f"PR #{pr.number} CI passed",
                        message=f"Auto-merge will merge: {pr.title}",
                        url=pr.url,
                    ))
                else:
                    events.append(FavEvent(
                        event_id=eid,
                        title=f"PR #{pr.number} ready to merge",
                        message=f"CI passed: {pr.title}",
                        url=pr.url,
                    ))

        if pr.merged_at is not None and pr.acc_deploy != DeployStatus.NONE:
            eid = f"{prefix}:merged"
            if eid not in already_notified:
                events.append(FavEvent(
                    event_id=eid,
                    title=f"PR #{pr.number} merged",
                    message=f"Deploying to acceptance: {pr.title}",
                    url=pr.url,
                ))

        if pr.acc_deploy == DeployStatus.ACC_DEPLOYED:
            eid = f"{prefix}:acc_deployed"
            if eid not in already_notified:
                events.append(FavEvent(
                    event_id=eid,
                    title=f"PR #{pr.number} deployed",
                    message=f"On acceptance: {pr.title}",
                    url=pr.url,
                ))

    return events


def load_notified_events(path: Path = NOTIFIED_FILE) -> set[str]:
    """Load the set of already-notified event ids. Returns empty set on missing/invalid."""
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to read notified events: %s", e)
        return set()
    if not isinstance(data, list):
        return set()
    return {e for e in data if isinstance(e, str)}


def save_notified_events(events: set[str], path: Path = NOTIFIED_FILE) -> None:
    """Persist the set of notified event ids."""
    try:
        path.write_text(json.dumps(sorted(events), indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to write notified events: %s", e)
