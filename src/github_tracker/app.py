"""Main Textual application for GitHub PR Tracker."""

from __future__ import annotations

import importlib.metadata
import logging
import webbrowser
from dataclasses import replace
from datetime import datetime, timezone

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import DataTable, Static

from github_tracker.config import Config
from github_tracker.deploy_tracker import (
    backfill_merge_commit_shas,
    detect_newly_merged_prs,
    filter_feature_branch_merges,
    update_deploy_statuses,
)
from github_tracker.github_client import GitHubClient
from github_tracker.models import DeployStatus, PRLabel, PrdDeployStatus, PullRequest
from github_tracker.pr_service import compute_prd_deploy_status, filter_expired_merged_prs, find_tree_members, group_prs, order_with_nesting
from github_tracker.refresh import (
    backfill_pr_details,
    fetch_pr_lists,
    refresh_open_pr_details,
)
from github_tracker.state import load_state, save_state
from github_tracker.widgets.header import TrackerHeader
from github_tracker.widgets.pr_table import PRTable
from github_tracker.widgets.status_bar import StatusBar

logger = logging.getLogger("github_tracker.app")

MY_PRS_REFRESH_INTERVAL = 60  # seconds
UPDATE_CHECK_REPO = "Kintull/github-pr-poller"

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


def _parse_version_tuple(v: str) -> tuple[int, ...]:
    """Parse a version string like '0.2.3' into a tuple of ints for comparison."""
    try:
        return tuple(int(x) for x in v.split("."))
    except (ValueError, AttributeError):
        return (0,)


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
            self.run_worker(self._check_for_updates())
        else:
            logger.warning("No GitHub client — skipping initial load")

    async def _check_for_updates(self) -> None:
        """Check GitHub for a newer release and update the header hint."""
        if not self.github_client:
            return
        try:
            current = importlib.metadata.version("github-tracker")
        except importlib.metadata.PackageNotFoundError:
            return
        latest = await self.github_client.fetch_latest_version(UPDATE_CHECK_REPO)
        if latest and _parse_version_tuple(latest) > _parse_version_tuple(current):
            header = self.query_one(TrackerHeader)
            header.set_update_hint(f"(v{latest} update available)")
            logger.info("Update available: v%s -> v%s", current, latest)

    async def _load_prs_progressive(self) -> None:
        """Load PRs progressively: list first, then details in background."""
        logger.info("Loading PRs from %d repos (progressive)", len(self.config.github_repos))
        header = self.query_one(TrackerHeader)
        header.status_text = "Loading PR list..."

        _my = self.query_one("#my-pr-table", PRTable)
        _other = self.query_one("#other-pr-table", PRTable)

        # favourite_keys: currently-displayed My PRs (handles migration from old label scheme)
        # + any PRs in Others that were explicitly favourite'd
        my_pr_keys = {(p.number, p.repo) for p in _my.pull_requests}
        favourite_keys = my_pr_keys | {
            (p.number, p.repo) for p in _other.pull_requests
            if PRLabel.FAVOURITE in p.labels
        }
        known_keys = {(p.number, p.repo) for p in self._previous_open_prs}

        # Phase 1: Fetch PR lists from all repos
        all_prs, raw_data, new_pr_keys = await fetch_pr_lists(
            repos=self.config.github_repos,
            github_client=self.github_client,
            jira_base_url=self.config.jira_base_url,
            github_username=self.config.github_username,
            favourite_keys=favourite_keys,
            known_keys=known_keys,
            notify_error=lambda repo, e: self.notify(f"Error fetching {repo}: {e}", severity="error"),
        )

        # Detect newly merged PRs
        current_open_numbers = {pr.number for pr in all_prs}
        new_merged = await detect_newly_merged_prs(
            self._previous_open_prs, current_open_numbers, self._merged_prs, self.github_client
        )
        # Set PRD_DEPLOYING on newly merged PRs (skip feature-branch merges)
        new_merged = [
            replace(m, prd_deploy=PrdDeployStatus.PRD_DEPLOYING) if m.acc_deploy != DeployStatus.NONE else m
            for m in new_merged
        ]
        self._merged_prs.extend(new_merged)

        # Re-check base branches and backfill merge_commit_sha
        self._merged_prs = await filter_feature_branch_merges(self._merged_prs, self.github_client)
        self._merged_prs = await backfill_merge_commit_shas(self._merged_prs, self.github_client)
        self._merged_prs = await update_deploy_statuses(
            self._merged_prs, self.github_client,
            self.config.acc_deploy_environment, self.config.argo_cooldown_minutes,
        )

        # Check PRD deployment status for repos with merged PRs
        prd_prs_to_check = [
            (i, mpr) for i, mpr in enumerate(self._merged_prs)
            if mpr.prd_deploy in (PrdDeployStatus.PRD_DEPLOYING, PrdDeployStatus.PRD_ARGO)
            and mpr.merge_commit_sha is not None
        ]
        prd_repos = {mpr.repo for _, mpr in prd_prs_to_check}
        prd_deploy_sha_by_repo: dict[str, tuple[str | None, datetime | None]] = {}
        for repo in prd_repos:
            try:
                sha, created_at = await self.github_client.fetch_latest_deployment_sha(
                    repo, self.config.prd_deploy_environment
                )
                prd_deploy_sha_by_repo[repo] = (sha, created_at)
            except Exception as e:
                logger.error("Error fetching PRD deployment for %s: %s", repo, e)
                prd_deploy_sha_by_repo[repo] = (None, None)

        for i, mpr in prd_prs_to_check:
            deploy_sha, deploy_created_at = prd_deploy_sha_by_repo.get(mpr.repo, (None, None))
            compare_status: str | None = None
            if deploy_sha and mpr.merge_commit_sha:
                try:
                    compare_status = await self.github_client.compare_commits(
                        mpr.repo, mpr.merge_commit_sha, deploy_sha
                    )
                except Exception as e:
                    logger.error("Error comparing commits for PR #%d (PRD): %s", mpr.number, e)
            new_status = compute_prd_deploy_status(
                mpr, compare_status, deploy_created_at, self.config.argo_cooldown_minutes
            )
            self._merged_prs[i] = replace(mpr, prd_deploy=new_status)

        # Filter expired merged PRs
        self._merged_prs = filter_expired_merged_prs(
            self._merged_prs, self.config.acc_retention_days
        )

        # Display combined open + merged (deduplicate)
        merged_keys = {(m.number, m.repo) for m in self._merged_prs}
        deduped_open = [p for p in all_prs if (p.number, p.repo) not in merged_keys]
        self._display_grouped_prs(deduped_open + self._merged_prs)

        header.status_text = f"{len(all_prs)} PRs — loading details..."
        logger.info("Displayed %d PRs, now loading details", len(all_prs))

        # Save state for next session's instant load
        save_state(all_prs, self._merged_prs)

        # Phase 2: Backfill reviews + CI status for each open PR
        all_prs = await backfill_pr_details(
            raw_data=raw_data,
            all_prs=all_prs,
            github_client=self.github_client,
            github_username=self.config.github_username,
            new_pr_keys=new_pr_keys,
            find_pr=self._find_pr_in_tables,
            update_pr=self._update_pr_in_tables,
        )

        # Re-group after Phase 2 (PRs may move from Other to My when COMMENTED is discovered)
        my_table = self.query_one("#my-pr-table", PRTable)
        other_table = self.query_one("#other-pr-table", PRTable)
        final_prs = list(my_table.pull_requests) + list(other_table.pull_requests)
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

        my_ordered, my_display = order_with_nesting(my_prs)
        other_ordered, other_display = order_with_nesting(other_prs)
        my_table.load_prs(my_ordered, my_display)
        other_table.load_prs(other_ordered, other_display)

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

    async def _do_refresh_open_prs(self, open_prs: list[PullRequest]) -> list[PullRequest]:
        """Fetch fresh detail/reviews/CI/threads for each PR and update tables.

        Returns list of PRs discovered to be merged during refresh.
        """
        discovered_merged = await refresh_open_pr_details(
            open_prs, self.github_client, self.config.github_username,
            self._update_pr_in_tables,
        )
        if discovered_merged:
            existing_keys = {(m.number, m.repo) for m in self._merged_prs}
            for mpr in discovered_merged:
                if (mpr.number, mpr.repo) not in existing_keys:
                    self._merged_prs.append(mpr)
                    self._update_pr_in_tables(mpr)
        return discovered_merged

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

        discovered_merged = await self._do_refresh_open_prs(open_prs)
        merged_prs_in_table = merged_prs_in_table + discovered_merged

        if merged_prs_in_table:
            # Backfill merge_commit_sha for merged PRs missing it
            updated_merged = await backfill_merge_commit_shas(merged_prs_in_table, self.github_client)
            # Sync backfilled PRs to tables and _merged_prs
            for updated in updated_merged:
                self._update_pr_in_tables(updated)
                self._sync_merged_pr(updated)

            # Check ACC deployment status (use updated list that has backfilled SHAs)
            updated_merged = await update_deploy_statuses(
                updated_merged, self.github_client,
                self.config.acc_deploy_environment, self.config.argo_cooldown_minutes,
            )
            # Sync ACC deploy status updates
            for updated in updated_merged:
                self._update_pr_in_tables(updated)
                self._sync_merged_pr(updated)

            # Check PRD deployment status
            # Re-read merged PRs from table after ACC updates
            merged_prs_in_table = [pr for pr in table.pull_requests if pr.merged_at is not None]
            prd_prs_to_check = [
                mpr for mpr in merged_prs_in_table
                if mpr.prd_deploy in (PrdDeployStatus.PRD_DEPLOYING, PrdDeployStatus.PRD_ARGO)
                and mpr.merge_commit_sha is not None
            ]
            prd_repos = {mpr.repo for mpr in prd_prs_to_check}
            prd_deploy_sha_by_repo: dict[str, tuple[str | None, datetime | None]] = {}
            for repo in prd_repos:
                try:
                    sha, created_at = await self.github_client.fetch_latest_deployment_sha(
                        repo, self.config.prd_deploy_environment
                    )
                    prd_deploy_sha_by_repo[repo] = (sha, created_at)
                except Exception as e:
                    logger.error("Error fetching PRD deployment for %s: %s", repo, e)
                    prd_deploy_sha_by_repo[repo] = (None, None)

            for mpr in prd_prs_to_check:
                deploy_sha, deploy_created_at = prd_deploy_sha_by_repo.get(mpr.repo, (None, None))
                compare_status: str | None = None
                if deploy_sha and mpr.merge_commit_sha:
                    try:
                        compare_status = await self.github_client.compare_commits(
                            mpr.repo, mpr.merge_commit_sha, deploy_sha
                        )
                    except Exception as e:
                        logger.error("Error comparing commits for PR #%d (PRD): %s", mpr.number, e)
                new_status = compute_prd_deploy_status(
                    mpr, compare_status, deploy_created_at, self.config.argo_cooldown_minutes
                )
                updated_mpr = replace(mpr, prd_deploy=new_status)
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

    def _sync_merged_pr(self, updated: PullRequest) -> None:
        """Update a merged PR in the _merged_prs list."""
        for j, m in enumerate(self._merged_prs):
            if m.number == updated.number and m.repo == updated.repo:
                self._merged_prs[j] = updated
                break

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
        """Toggle favourite status on the selected PR and its entire tree."""
        table = self._get_focused_table()
        if table is None:
            return
        pr = table.get_selected_pr()
        if pr is None:
            self.notify("No PR selected", severity="warning")
            return
        my_table = self.query_one("#my-pr-table", PRTable)
        other_table = self.query_one("#other-pr-table", PRTable)
        all_prs = list(my_table.pull_requests) + list(other_table.pull_requests)

        tree_members = find_tree_members(pr, all_prs)
        removing_favourite = PRLabel.FAVOURITE in pr.labels

        for member in tree_members:
            if removing_favourite:
                new_labels = member.labels - {PRLabel.FAVOURITE}
            else:
                new_labels = member.labels | {PRLabel.FAVOURITE}
            updated = replace(member, labels=new_labels)
            self._update_pr_in_tables(updated)
            self._merged_prs = [
                updated if m.number == member.number and m.repo == member.repo else m
                for m in self._merged_prs
            ]

        if len(tree_members) > 1:
            numbers = ", ".join(f"#{m.number}" for m in tree_members)
            verb = "Unfavourited" if removing_favourite else "Favourited"
            self.notify(f"{verb} tree: {numbers}")
        elif removing_favourite:
            self.notify(f"Unfavourited #{pr.number}")
        else:
            self.notify(f"Favourited #{pr.number}")

        merged_keys = {(m.number, m.repo) for m in self._merged_prs}
        final_open = [
            p for p in list(my_table.pull_requests) + list(other_table.pull_requests)
            if p.merged_at is None and (p.number, p.repo) not in merged_keys
        ]
        final_open.sort(key=lambda p: p.updated_at, reverse=True)
        self._display_grouped_prs(final_open + self._merged_prs, preserve_focus=True)
        save_state(final_open, self._merged_prs)
        if removing_favourite:
            self.run_worker(other_table.flash_title(pr.number))
        else:
            self.run_worker(my_table.flash_title(pr.number))
