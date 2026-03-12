"""Main Textual application for GitHub PR Tracker."""

from __future__ import annotations

import asyncio
import logging
import webbrowser
from dataclasses import replace
from datetime import datetime, timezone

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import DataTable, Static

from github_tracker.config import Config
from github_tracker.github_client import GitHubClient, _aggregate_ci_status, count_approvals
from github_tracker.models import CIStatus, DeployStatus, PRLabel, PullRequest
from github_tracker.pr_service import (
    compute_acc_deploy,
    compute_ci_progress,
    compute_phase1_labels,
    compute_phase2_labels,
    compute_thread_counts,
    compute_user_approved,
    filter_expired_merged_prs,
    group_prs,
)
from github_tracker.state import load_state, save_state
from github_tracker.widgets.header import TrackerHeader
from github_tracker.widgets.pr_table import PRTable
from github_tracker.widgets.status_bar import StatusBar

logger = logging.getLogger("github_tracker.app")

MY_PRS_REFRESH_INTERVAL = 60  # seconds

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
        Binding("f", "favourite", "Favourite", show=False),
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
        self._my_prs_refresh_timer = None
        self._label_update_timer = None
        self._help_visible = False
        self._merged_prs: list[PullRequest] = []
        self._previous_open_prs: list[PullRequest] = []
        self._my_prs_refreshed_at: datetime | None = None

    def compose(self) -> ComposeResult:
        all_mins = self.config.refresh_interval // 60
        refresh_info = f"My PRs: {MY_PRS_REFRESH_INTERVAL // 60} min | All: {all_mins} min"
        yield TrackerHeader(
            repos=self.config.github_repos,
            jira_base_url=self.config.jira_base_url,
            refresh_info=refresh_info,
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
        self._my_prs_refresh_timer = self.set_interval(
            MY_PRS_REFRESH_INTERVAL, self._auto_refresh_my_prs
        )
        self._label_update_timer = self.set_interval(10, self._update_my_prs_label)

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

        _my = self.query_one("#my-pr-table", PRTable)
        _other = self.query_one("#other-pr-table", PRTable)

        # favourite_keys: currently-displayed My PRs (handles migration from old label scheme)
        # + any PRs in Others that were explicitly favourite'd
        my_pr_keys = {(p.number, p.repo) for p in _my.pull_requests}
        favourite_keys = my_pr_keys | {
            (p.number, p.repo) for p in _other.pull_requests
            if PRLabel.FAVOURITE in p.labels
        }

        # known_keys: all PRs seen in the previous load (both My and Others)
        known_keys = {(p.number, p.repo) for p in self._previous_open_prs}

        # Track new PRs with no Phase 1 interest (candidates for Phase 2 auto-FAVOURITE)
        new_pr_keys: set[tuple[int, str]] = set()

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
                    key = (pr.number, repo)
                    if key in favourite_keys:
                        # Preserve existing FAVOURITE (known favourite or migration)
                        labels = labels | {PRLabel.FAVOURITE}
                    elif key not in known_keys:
                        # New PR: auto-follow if user has natural interest
                        if labels:  # any Phase 1 interest label
                            labels = labels | {PRLabel.FAVOURITE}
                        else:
                            new_pr_keys.add(key)  # no interest yet; check again in Phase 2
                    # else: known PR not in favourite_keys → user unfollowed → no FAVOURITE
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
            # Fetch jobs for in-progress/queued runs
            jobs_by_run_id: dict[int, list[dict]] = {}
            for run in runs:
                run_id = run.get("id")
                if run_id and run.get("status") in ("queued", "in_progress"):
                    try:
                        jobs = await self.github_client.fetch_workflow_run_jobs(mpr.repo, run_id)
                        jobs_by_run_id[run_id] = jobs
                    except Exception as e:
                        logger.error("Error fetching jobs for run %d: %s", run_id, e)
            new_status, acc_completed, acc_total = compute_acc_deploy(
                mpr, runs, self.config.acc_cooldown_minutes, jobs_by_run_id
            )
            self._merged_prs[i] = replace(
                mpr,
                acc_deploy=new_status,
                acc_completed_steps=acc_completed,
                acc_total_steps=acc_total,
            )

        # Filter expired merged PRs
        self._merged_prs = filter_expired_merged_prs(
            self._merged_prs, self.config.acc_retention_days
        )

        # Display combined open + merged (deduplicate in case a merged PR reappears as open)
        merged_keys = {(m.number, m.repo) for m in self._merged_prs}
        deduped_open = [p for p in all_prs if (p.number, p.repo) not in merged_keys]
        self._display_grouped_prs(deduped_open + self._merged_prs)

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
                reviews, check_runs, pr_detail, threads = await asyncio.gather(
                    self.github_client.fetch_reviews(repo, pr_number),
                    self.github_client.fetch_check_runs(repo, head_sha),
                    self.github_client.fetch_pr_detail(repo, pr_number),
                    self.github_client.fetch_review_threads(repo, pr_number),
                )
            except Exception as e:
                logger.error("Error loading details for PR #%d: %s", pr_number, e)
                continue

            approval_count = count_approvals(reviews)
            ci_status = _aggregate_ci_status(check_runs)
            ci_completed, ci_total = compute_ci_progress(check_runs)
            comment_count = pr_detail.get("comments", 0) + pr_detail.get("review_comments", 0)
            user_approved = compute_user_approved(reviews, self.config.github_username)
            total_threads, unresolved_threads, my_commented, my_unresolved = compute_thread_counts(
                threads, self.config.github_username
            )

            # Find the PR in whichever table contains it
            pr = self._find_pr_in_tables(pr_number)
            if pr is None:
                continue

            new_labels = compute_phase2_labels(
                pr.labels, reviews, self.config.github_username
            )
            # Auto-follow new PRs where COMMENTED was just discovered
            if (pr_number, repo) in new_pr_keys and PRLabel.COMMENTED in new_labels and PRLabel.FAVOURITE not in new_labels:
                new_labels = new_labels | {PRLabel.FAVOURITE}
            updated_pr = replace(
                pr,
                approval_count=approval_count,
                ci_status=ci_status,
                ci_completed_steps=ci_completed,
                ci_total_steps=ci_total,
                comment_count=comment_count,
                labels=new_labels,
                user_approved=user_approved,
                total_threads=total_threads,
                unresolved_threads=unresolved_threads,
                my_commented_threads=my_commented,
                my_unresolved_threads=my_unresolved,
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
        merged_keys = {(m.number, m.repo) for m in self._merged_prs}
        final_open = [p for p in final_prs if p.merged_at is None and (p.number, p.repo) not in merged_keys]
        final_open.sort(key=lambda p: p.updated_at, reverse=True)
        self._display_grouped_prs(final_open + self._merged_prs)

        total = len(final_open) + len(self._merged_prs)
        header.status_text = f"{total} PRs"
        logger.info("Finished loading all PR details")

        self._my_prs_refreshed_at = datetime.now(timezone.utc)
        self._update_my_prs_label()
        save_state(final_open, self._merged_prs)
        self._previous_open_prs = final_open

    def _display_grouped_prs(
        self, all_prs: list[PullRequest], is_cached: bool = False, preserve_focus: bool = False
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
        if not preserve_focus:
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

    async def _do_refresh_open_prs(self, open_prs: list[PullRequest]) -> None:
        """Fetch fresh detail/reviews/CI/threads for each PR and update tables."""
        for pr in open_prs:
            try:
                pr_detail = await self.github_client.fetch_pr_detail(pr.repo, pr.number)
                head_sha = (pr_detail.get("head") or {}).get("sha", "")
                if not head_sha:
                    continue
                reviews, check_runs, threads = await asyncio.gather(
                    self.github_client.fetch_reviews(pr.repo, pr.number),
                    self.github_client.fetch_check_runs(pr.repo, head_sha),
                    self.github_client.fetch_review_threads(pr.repo, pr.number),
                )
            except Exception as e:
                logger.error("Error refreshing PR #%d: %s", pr.number, e)
                continue

            approval_count = count_approvals(reviews)
            ci_status = _aggregate_ci_status(check_runs)
            ci_completed, ci_total = compute_ci_progress(check_runs)
            comment_count = pr_detail.get("comments", 0) + pr_detail.get("review_comments", 0)
            user_approved = compute_user_approved(reviews, self.config.github_username)
            total_threads, unresolved_threads, my_commented, my_unresolved = compute_thread_counts(
                threads, self.config.github_username
            )
            phase1_labels = compute_phase1_labels(pr, pr_detail, self.config.github_username)
            if PRLabel.FAVOURITE in pr.labels:
                phase1_labels = phase1_labels | {PRLabel.FAVOURITE}
            new_labels = compute_phase2_labels(phase1_labels, reviews, self.config.github_username)
            updated_pr = replace(
                pr,
                approval_count=approval_count,
                ci_status=ci_status,
                ci_completed_steps=ci_completed,
                ci_total_steps=ci_total,
                comment_count=comment_count,
                labels=new_labels,
                user_approved=user_approved,
                total_threads=total_threads,
                unresolved_threads=unresolved_threads,
                my_commented_threads=my_commented,
                my_unresolved_threads=my_unresolved,
            )
            self._update_pr_in_tables(updated_pr)

    def _regroup_and_save(self) -> None:
        """Re-group all PRs into my/other tables and persist state."""
        my_table = self.query_one("#my-pr-table", PRTable)
        other_table = self.query_one("#other-pr-table", PRTable)
        merged_keys = {(m.number, m.repo) for m in self._merged_prs}
        final_open = [
            p for p in list(my_table.pull_requests) + list(other_table.pull_requests)
            if p.merged_at is None and (p.number, p.repo) not in merged_keys
        ]
        final_open.sort(key=lambda p: p.updated_at, reverse=True)
        self._display_grouped_prs(final_open + self._merged_prs)
        save_state(final_open, self._merged_prs)

    async def _refresh_focused_prs(self) -> None:
        """Refresh only the PRs in the currently focused table."""
        table = self._get_focused_table()
        if table is None:
            return

        header = self.query_one(TrackerHeader)
        prs = list(table.pull_requests)
        open_prs = [pr for pr in prs if pr.merged_at is None]
        merged_prs_in_table = [pr for pr in prs if pr.merged_at is not None]

        header.status_text = f"Refreshing {len(prs)} PRs..."
        logger.info(
            "Refreshing focused table: %d open, %d merged",
            len(open_prs), len(merged_prs_in_table),
        )

        await self._do_refresh_open_prs(open_prs)

        if merged_prs_in_table:
            repos_with_merged = {pr.repo for pr in merged_prs_in_table}
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

            for mpr in merged_prs_in_table:
                runs = workflow_runs_by_repo.get(mpr.repo, [])
                jobs_by_run_id: dict[int, list[dict]] = {}
                for run in runs:
                    run_id = run.get("id")
                    if run_id and run.get("status") in ("queued", "in_progress"):
                        try:
                            jobs = await self.github_client.fetch_workflow_run_jobs(
                                mpr.repo, run_id
                            )
                            jobs_by_run_id[run_id] = jobs
                        except Exception as e:
                            logger.error("Error fetching jobs for run %d: %s", run_id, e)
                new_status, acc_completed, acc_total = compute_acc_deploy(
                    mpr, runs, self.config.acc_cooldown_minutes, jobs_by_run_id
                )
                updated_mpr = replace(
                    mpr,
                    acc_deploy=new_status,
                    acc_completed_steps=acc_completed,
                    acc_total_steps=acc_total,
                )
                self._update_pr_in_tables(updated_mpr)
                for j, m in enumerate(self._merged_prs):
                    if m.number == mpr.number and m.repo == mpr.repo:
                        self._merged_prs[j] = updated_mpr
                        break

        my_table = self.query_one("#my-pr-table", PRTable)
        self._regroup_and_save()

        if table is my_table:
            self._my_prs_refreshed_at = datetime.now(timezone.utc)
            self._update_my_prs_label()

        header.status_text = f"{len(prs)} PRs"
        logger.info("Finished refreshing focused table")

    async def _auto_refresh_my_prs(self) -> None:
        """Auto-refresh My PRs table every MY_PRS_REFRESH_INTERVAL seconds."""
        if not self.github_client:
            return
        my_table = self.query_one("#my-pr-table", PRTable)
        open_prs = [pr for pr in my_table.pull_requests if pr.merged_at is None]
        await self._do_refresh_open_prs(open_prs)
        self._regroup_and_save()
        self._my_prs_refreshed_at = datetime.now(timezone.utc)
        self._update_my_prs_label()
        logger.debug("My PRs auto-refresh complete (%d open PRs)", len(open_prs))

    @staticmethod
    def _format_staleness(refreshed_at: datetime | None) -> str:
        """Return a human-readable 'updated X ago' string."""
        if refreshed_at is None:
            return ""
        seconds = int((datetime.now(timezone.utc) - refreshed_at).total_seconds())
        if seconds < 50:
            bucket = ((seconds // 10) + 1) * 10
            return f"<{bucket}s ago"
        minutes = (seconds // 60) + 1
        unit = "min" if minutes == 1 else "mins"
        return f"<{minutes} {unit} ago"

    def _update_my_prs_label(self) -> None:
        """Update the My PRs section label with the staleness indicator."""
        staleness = self._format_staleness(self._my_prs_refreshed_at)
        label_text = f"My PRs — updated {staleness}" if staleness else "My PRs"
        try:
            self.query_one("#my-prs-label", Static).update(label_text)
        except Exception:
            pass

    async def _auto_refresh(self) -> None:
        """Auto-refresh triggered by timer."""
        if self.github_client:
            self.run_worker(self._load_prs_progressive(), exclusive=True)

    async def action_refresh(self) -> None:
        """Handle refresh key binding."""
        if self.github_client:
            self.run_worker(self._refresh_focused_prs(), exclusive=True)
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

    def action_favourite(self) -> None:
        """Toggle favourite status on the selected PR."""
        table = self._get_focused_table()
        if table is None:
            return
        pr = table.get_selected_pr()
        if pr is None:
            self.notify("No PR selected", severity="warning")
            return
        removing_favourite = PRLabel.FAVOURITE in pr.labels
        if removing_favourite:
            new_labels = pr.labels - {PRLabel.FAVOURITE}
            self.notify(f"Unfavourited #{pr.number}")
        else:
            new_labels = pr.labels | {PRLabel.FAVOURITE}
            self.notify(f"Favourited #{pr.number}")
        updated_pr = replace(pr, labels=new_labels)
        self._update_pr_in_tables(updated_pr)
        # Also update _merged_prs so merged PRs (e.g. deployed to ACC) move correctly
        self._merged_prs = [
            updated_pr if p.number == pr.number and p.repo == pr.repo else p
            for p in self._merged_prs
        ]
        my_table = self.query_one("#my-pr-table", PRTable)
        other_table = self.query_one("#other-pr-table", PRTable)
        final_open = [
            p for p in list(my_table.pull_requests) + list(other_table.pull_requests)
            if p.merged_at is None
        ]
        final_open.sort(key=lambda p: p.updated_at, reverse=True)
        self._display_grouped_prs(final_open + self._merged_prs, preserve_focus=True)
        save_state(final_open, self._merged_prs)
        if removing_favourite:
            self.run_worker(other_table.flash_title(pr.number))
        else:
            self.run_worker(my_table.flash_title(pr.number))

