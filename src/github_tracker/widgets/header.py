"""Header banner widget for the GitHub PR Tracker."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

VERSION = "0.1.0"

LOGO = """\
  ╭──○
  │  ╰──○
  ○──╯\
"""

LOGO_LINES = LOGO.split("\n")
LOGO_HEIGHT = len(LOGO_LINES)
LOGO_WIDTH = max(len(line) for line in LOGO_LINES)


def build_banner(
    repos: list[str],
    jira_base_url: str,
    status: str,
    refresh_info: str = "",
) -> str:
    """Build the full banner string with logo on the left and info on the right."""
    title = f"GitHub PR Tracker v{VERSION}"

    if repos:
        repos_label = "Watching"
        repos_lines = [f"  {r}" for r in repos]
    else:
        repos_label = "Watching"
        repos_lines = ["  (no repos configured)"]

    if jira_base_url:
        jira_line = f"Jira  {jira_base_url}"
    else:
        jira_line = "Jira  (not configured)"

    status_line = status

    # Build right-side info lines
    right_lines = [title, ""]
    right_lines.append(repos_label)
    right_lines.extend(repos_lines)
    right_lines.append("")
    right_lines.append(jira_line)
    if refresh_info:
        right_lines.append(f"Refresh  {refresh_info}")
    if status_line:
        right_lines.append("")
        right_lines.append(status_line)

    # Pad both sides to equal height
    total_height = max(LOGO_HEIGHT, len(right_lines))
    padded_logo = LOGO_LINES + [""] * (total_height - LOGO_HEIGHT)
    padded_right = right_lines + [""] * (total_height - len(right_lines))

    # Combine with separator
    separator = " │ "
    lines = []
    for logo_line, info_line in zip(padded_logo, padded_right):
        lines.append(f"  {logo_line:<{LOGO_WIDTH}}{separator}{info_line}")

    return "\n".join(lines)


class TrackerHeader(Widget):
    """Top banner showing logo, repo info, Jira URL, and status."""

    DEFAULT_CSS = """
    TrackerHeader {
        dock: top;
        height: auto;
        max-height: 14;
        background: $surface;
        border: round $primary;
        padding: 0 0;
    }

    TrackerHeader #banner-content {
        width: 1fr;
        height: auto;
    }
    """

    repo_name: reactive[str] = reactive("", layout=True)
    jira_url: reactive[str] = reactive("", layout=True)
    status_text: reactive[str] = reactive("", layout=True)

    def __init__(
        self,
        repos: list[str] | None = None,
        jira_base_url: str = "",
        refresh_info: str = "",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._repos = repos or []
        self._jira_base_url = jira_base_url
        self._refresh_info = refresh_info

    def compose(self) -> ComposeResult:
        yield Static(id="banner-content")

    def on_mount(self) -> None:
        self._rebuild_banner()

    def set_config(self, repos: list[str], jira_base_url: str) -> None:
        """Update the header with new config values."""
        self._repos = repos
        self._jira_base_url = jira_base_url
        self.repo_name = ", ".join(repos) if repos else ""
        self.jira_url = jira_base_url
        self._rebuild_banner()

    def watch_status_text(self, value: str) -> None:
        self._rebuild_banner()

    def _rebuild_banner(self) -> None:
        try:
            content = self.query_one("#banner-content", Static)
        except Exception:
            return
        banner = build_banner(
            repos=self._repos,
            jira_base_url=self._jira_base_url,
            status=self.status_text,
            refresh_info=self._refresh_info,
        )
        content.update(banner)
