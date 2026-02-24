"""Jira ticket extraction from branch names and PR titles."""

from __future__ import annotations

import re

JIRA_TICKET_PATTERN = re.compile(r"[A-Z][A-Z0-9]+-\d+")


def extract_jira_ticket(branch_name: str, title: str) -> str | None:
    """Extract a Jira ticket ID from the branch name, falling back to the PR title.

    Looks for patterns like PROJ-123, ABC-1, MYTEAM-9999.
    Tries branch name first, then PR title.
    """
    match = JIRA_TICKET_PATTERN.search(branch_name)
    if match:
        return match.group(0)
    match = JIRA_TICKET_PATTERN.search(title)
    if match:
        return match.group(0)
    return None


def build_jira_url(ticket: str, base_url: str) -> str:
    """Build a full Jira URL for a ticket.

    Args:
        ticket: Jira ticket ID, e.g. "PROJ-123"
        base_url: Jira base URL, e.g. "https://mycompany.atlassian.net/browse"
    """
    return f"{base_url.rstrip('/')}/{ticket}"
