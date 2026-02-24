"""Main Textual application for GitHub PR Tracker."""

from __future__ import annotations

import asyncio
import logging
import webbrowser
from dataclasses import replace

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import DataTable, Static

from github_tracker.config import Config
from github_tracker.github_client import GitHubClient, _aggregate_ci_status, count_approvals
from github_tracker.models import CIStatus, DeployStatus, PullRequest
from github_tracker.pr_service import (
    compute_acc_deploy,
    compute_phase1_labels,
    compute_phase2_labels,
    filter_expired_merged_prs,
    group_prs,
)
from github_tracker.state import load_state, save_state
from github_tracker.widgets.header import TrackerHeader
from github_tracker.widgets.pr_table import PRTable
from github_tracker.widgets.status_bar import StatusBar

logger = logging.getLogger("github_tracker.app")

HELP_TEXT = """
Keyboard Shortcuts:
  ↑/↓ or k/j  Navigate PR list
  Tab          Switch table
  Enter or o   Open PR in browser
  shift+j      Open Jira link in browser
  r            Refresh PR list
  ?            Toggle this help
  q            Quit
"""


class HelpOverlay(Static):
    """Overlay showing help text."""

    DEFAULT_CSS = """
    HelpOverlay {
        display: none;
        layer: overlay;
        width: 50;
        height: auto;
        margin: 2 4;
        padding: 1 2;
        background: $surface;
        border: round $primary;
    }
    """


class GitHubTrackerApp(App):
    """TUI application for tracking GitHub PRs."""

    TITLE = "GitHub PR Tracker"

    CSS = """
    Screen {
        layers: base overlay;
    }

    #main-container {
        height: 1fr;
    }

    .section-label {
        padding: 0 1;
        color: $text;
        text-style: bold;
    }

    #empty-message {
        text-align: center;
        margin: 4;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("o", "open_pr", "Open PR", show=False),
        Binding("J", "open_jira", "Open Jira", show=False),
        Binding("question_mark", "toggle_help", "Help", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("j", "cursor_down", "Down", show=False),
    ]

    def __init__(
        self,
        config: Config,
        github_client: GitHubClient | None = None,
        open_url: callable = webbrowser.open,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.config = config
        self.github_client = github_client
        self._open_url = open_url
        self._spinner_timer = None
        self._refresh_timer = None
        self._help_visible = False
        self._merged_prs: list[PullRequest] = []
        self._previous_open_prs: list[PullRequest] = []

    def compose(self) -> ComposeResult:
        yield TrackerHeader(
            repos=self.config.github_repos,
            jira_base_url=self.config.jira_base_url,
        )
        yield Container(
            Static("My PRs", id="my-prs-label", classes="section-label"),
            PRTable(id="my-pr-table"),
            Static("Other PRs", id="other-prs-label", classes="section-label"),
            PRTable(id="other-pr-table"),
            Static("No pull requests found. Press 'r' to refresh.", id="empty-message"),
            id="main-container",
        )
        yield HelpOverlay(HELP_TEXT, id="help-overlay")
        yield StatusBar()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle Enter key on a DataTable row."""
        table = event.data_table
        if not isinstance(table, PRTable):
            return  # pragma: no cover
        pr = table.get_selected_pr()
        if pr:
            self._open_url(pr.url)

    async def on_mount(self) -> None:
        logger.info("App mounted, repos=%s", self.config.github_repos)

        self._spinner_timer = self.set_interval(0.1, self._tick_spinner)
        self._refresh_timer = self.set_interval(
            self.config.refresh_interval, self._auto_refresh
        )

        # Hide both sections initially
        self._set_section_visible("my", False)
        self._set_section_visible("other", False)

        # Load cached PRs for instant display
        cached_prs, cached_merged = load_state()
        self._previous_open_prs = list(cached_prs)
        self._merged_prs = list(cached_merged)
        # Filter out any open PRs that are also in merged (shouldn't happen normally,
        # but guard against state file inconsistencies)
        merged_numbers = {(m.number, m.repo) for m in cached_merged}
        display_open = [p for p in cached_prs if (p.number, p.repo) not in merged_numbers]
        if display_open or cached_merged:
            logger.info("Displaying %d cached + %d merged PRs", len(display_open), len(cached_merged))
            self._display_grouped_prs(display_open + cached_merged, is_cached=True)

        if self.github_client:
            self.run_worker(self._load_prs_progressive(), exclusive=True)
        else:
            logger.warning("No GitHub client — skipping initial load")

    async def _load_prs_progressive(self) -> None:
        """Load PRs progressively: list first, then details in background."""
        logger.info("Loading PRs from %d repos (progressive)", len(self.config.github_repos))
        header = self.query_one(TrackerHeader)
        header.status_text = "Loading PR list..."

        # Phase 1: Fetch PR lists from all repos (fast — single API call per repo)
        all_prs: list[PullRequest] = []
        raw_data: list[tuple[str, dict]] = []

        for repo in self.config.github_repos:
            try:
                logger.info("Fetching PR list for repo: %s", repo)
                raw_prs = await self.github_client.fetch_open_prs(repo)
                for raw_pr in raw_prs:
                    pr = self.github_client.parse_pr_basic(
                        raw_pr, repo, self.config.jira_base_url
                    )
                    labels = compute_phase1_labels(
                        pr, raw_pr, self.config.github_username
                    )
                    pr = replace(pr, labels=labels)
                    all_prs.append(pr)
                    raw_data.append((repo, raw_pr))
                logger.info("Got %d PRs from %s", len(raw_prs), repo)
            except Exception as e:
                logger.error("Error fetching PR list for %s: %s", repo, e, exc_info=True)
                self.notify(f"Error fetching {repo}: {e}", severity="error")

        all_prs.sort(key=lambda p: p.updated_at, reverse=True)
        raw_data.sort(
            key=lambda item: item[1].get("updated_at", ""), reverse=True
        )

        # Detect newly merged PRs
        current_open_numbers = {pr.number for pr in all_prs}
        for prev_pr in self._previous_open_prs:
            if prev_pr.number not in current_open_numbers:
                # PR disappeared — check if it was merged
                try:
                    detail = await self.github_client.fetch_pr_detail(prev_pr.repo, prev_pr.number)
                    merged_at_str = detail.get("merged_at")
                    if merged_at_str:
                        from datetime import datetime
                        merged_at = datetime.fromisoformat(merged_at_str.replace("Z", "+00:00"))
                        merged_pr = replace(
                            prev_pr,
                            merged_at=merged_at,
                            acc_deploy=DeployStatus.ACC_DEPLOYING,
                        )
                        # Avoid duplicates
                        if not any(m.number == merged_pr.number and m.repo == merged_pr.repo for m in self._merged_prs):
                            self._merged_prs.append(merged_pr)
                            logger.info("Detected merged PR #%d in %s", prev_pr.number, prev_pr.repo)
                except Exception as e:
                    logger.error("Error checking merge status for PR #%d: %s", prev_pr.number, e)

        # Check deploy workflow for repos with merged PRs
        repos_with_merged = {pr.repo for pr in self._merged_prs}
        workflow_runs_by_repo: dict[str, list[dict]] = {}
        for repo in repos_with_merged:
            try:
                runs = await self.github_client.fetch_workflow_runs(
                    repo, self.config.acc_workflow_name
                )
                workflow_runs_by_repo[repo] = runs
            except Exception as e:
                logger.error("Error fetching workflow runs for %s: %s", repo, e)
                workflow_runs_by_repo[repo] = []

        # Compute ACC deploy status for each merged PR
        for i, mpr in enumerate(self._merged_prs):
            runs = workflow_runs_by_repo.get(mpr.repo, [])
            new_status = compute_acc_deploy(mpr, runs, self.config.acc_cooldown_minutes)
            self._merged_prs[i] = replace(mpr, acc_deploy=new_status)

        # Filter expired merged PRs
        self._merged_prs = filter_expired_merged_prs(
            self._merged_prs, self.config.acc_retention_days
        )

        # Display combined open + merged
        self._display_grouped_prs(all_prs + self._merged_prs)

        header.status_text = f"{len(all_prs)} PRs — loading details..."
        logger.info("Displayed %d PRs, now loading details", len(all_prs))

        # Save state for next session's instant load
        save_state(all_prs, self._merged_prs)

        # Phase 2: Backfill reviews + CI status for each open PR
        my_table = self.query_one("#my-pr-table", PRTable)
        other_table = self.query_one("#other-pr-table", PRTable)

        for i, (repo, raw_pr) in enumerate(raw_data):
            pr_number = raw_pr["number"]
            head_sha = raw_pr["head"]["sha"]

            try:
                reviews, check_runs, pr_detail = await asyncio.gather(
                    self.github_client.fetch_reviews(repo, pr_number),
                    self.github_client.fetch_check_runs(repo, head_sha),
                    self.github_client.fetch_pr_detail(repo, pr_number),
                )
            except Exception as e:
                logger.error("Error loading details for PR #%d: %s", pr_number, e)
                continue

            approval_count = count_approvals(reviews)
            ci_status = _aggregate_ci_status(check_runs)
            comment_count = pr_detail.get("comments", 0) + pr_detail.get("review_comments", 0)

            # Find the PR in whichever table contains it
            pr = self._find_pr_in_tables(pr_number)
            if pr is None:
                continue

            new_labels = compute_phase2_labels(
                pr.labels, reviews, self.config.github_username
            )
            updated_pr = replace(
                pr,
                approval_count=approval_count,
                ci_status=ci_status,
                comment_count=comment_count,
                labels=new_labels,
            )
            self._update_pr_in_tables(updated_pr)
            # Update in all_prs for final save
            all_prs[i] = updated_pr

            loaded = i + 1
            header.status_text = f"{len(all_prs)} PRs — details {loaded}/{len(all_prs)}"

        # Re-group after Phase 2 (PRs may move from Other to My when COMMENTED is discovered)
        # Collect all updated PRs from both tables (includes merged PRs)
        final_prs = list(my_table.pull_requests) + list(other_table.pull_requests)
        # Separate open from merged for saving
        final_open = [p for p in final_prs if p.merged_at is None]
        final_open.sort(key=lambda p: p.updated_at, reverse=True)
        self._display_grouped_prs(final_open + self._merged_prs)

        total = len(final_open) + len(self._merged_prs)
        header.status_text = f"{total} PRs"
        logger.info("Finished loading all PR details")

        save_state(final_open, self._merged_prs)
        self._previous_open_prs = final_open

    def _display_grouped_prs(
        self, all_prs: list[PullRequest], is_cached: bool = False
    ) -> None:
        """Split PRs into my/other groups and display in respective tables."""
        my_prs, other_prs = group_prs(all_prs)

        my_table = self.query_one("#my-pr-table", PRTable)
        other_table = self.query_one("#other-pr-table", PRTable)
        empty_msg = self.query_one("#empty-message", Static)

        self._set_section_visible("my", bool(my_prs))
        self._set_section_visible("other", bool(other_prs))

        my_table.load_prs(my_prs)
        other_table.load_prs(other_prs)

        empty_msg.display = not all_prs

        header = self.query_one(TrackerHeader)
        if is_cached:
            header.status_text = f"{len(all_prs)} PRs (cached) — refreshing..."
        # Focus the first non-empty table
        if my_prs:
            my_table.focus()
        elif other_prs:
            other_table.focus()

    def _set_section_visible(self, section: str, visible: bool) -> None:
        """Show or hide a section (label + table)."""
        label = self.query_one(f"#{section}-prs-label", Static)
        table = self.query_one(f"#{section}-pr-table", PRTable)
        label.display = visible
        table.display = visible

    def _get_focused_table(self) -> PRTable | None:
        """Return the focused PRTable, or the first visible one as fallback."""
        my_table = self.query_one("#my-pr-table", PRTable)
        other_table = self.query_one("#other-pr-table", PRTable)
        if my_table.has_focus:
            return my_table
        if other_table.has_focus:
            return other_table
        # Fallback: first visible table
        if my_table.display:
            return my_table
        if other_table.display:
            return other_table
        return None

    def _find_pr_in_tables(self, pr_number: int) -> PullRequest | None:
        """Find a PR by number across both tables."""
        for table_id in ("#my-pr-table", "#other-pr-table"):
            table = self.query_one(table_id, PRTable)
            idx = table._pr_index.get(pr_number)
            if idx is not None:
                return table.pull_requests[idx]
        return None

    def _update_pr_in_tables(self, pr: PullRequest) -> None:
        """Update a PR in whichever table contains it."""
        for table_id in ("#my-pr-table", "#other-pr-table"):
            table = self.query_one(table_id, PRTable)
            if pr.number in table._pr_index:
                table.update_pr(pr)
                return

    def _tick_spinner(self) -> None:
        """Advance the spinner animation."""
        for table_id in ("#my-pr-table", "#other-pr-table"):
            try:
                table = self.query_one(table_id, PRTable)
                table.advance_spinner()
            except Exception:
                pass

    async def _auto_refresh(self) -> None:
        """Auto-refresh triggered by timer."""
        if self.github_client:
            self.run_worker(self._load_prs_progressive(), exclusive=True)

    async def action_refresh(self) -> None:
        """Handle refresh key binding."""
        if self.github_client:
            self.run_worker(self._load_prs_progressive(), exclusive=True)
        else:
            self.notify("No GitHub client configured", severity="warning")

    def action_open_pr(self) -> None:
        """Open the selected PR in the browser."""
        table = self._get_focused_table()
        if table is None:
            self.notify("No PR selected", severity="warning")
            return
        pr = table.get_selected_pr()
        if pr:
            self._open_url(pr.url)
        else:
            self.notify("No PR selected", severity="warning")

    def action_open_jira(self) -> None:
        """Open the selected PR's Jira link in the browser."""
        table = self._get_focused_table()
        if table is None:
            self.notify("No PR selected", severity="warning")
            return
        pr = table.get_selected_pr()
        if pr and pr.jira_url:
            self._open_url(pr.jira_url)
        elif pr and not pr.jira_url:
            self.notify("No Jira ticket for this PR", severity="warning")
        else:
            self.notify("No PR selected", severity="warning")

    def action_toggle_help(self) -> None:
        """Toggle the help overlay."""
        overlay = self.query_one("#help-overlay", HelpOverlay)
        self._help_visible = not self._help_visible
        overlay.display = self._help_visible

    def action_cursor_up(self) -> None:
        """Move cursor up in the focused PR table."""
        table = self._get_focused_table()
        if table:
            table.action_cursor_up()

    def action_cursor_down(self) -> None:
        """Move cursor down in the focused PR table."""
        table = self._get_focused_table()
        if table:
            table.action_cursor_down()

