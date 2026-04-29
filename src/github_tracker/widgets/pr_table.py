"""PR table widget for displaying pull requests."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from rich.style import Style
from rich.text import Text
from textual.widgets import DataTable

from github_tracker.models import CIStatus, DeployStatus, PRLabel, PrdDeployStatus, PullRequest, acc_deploy_display, ci_display, prd_deploy_display
from github_tracker.pr_service import PRDisplayItem
from github_tracker.theme import Color

COLUMNS = ("#", "Title", "Author", "\U0001f4ac", "✓", "CI", "ACC", "PRD", "Jira")

# Fixed widths for columns that should not auto-size.
# Columns not listed here remain auto-width (CI, ACC, PRD size to content).
_FIXED_WIDTHS: dict[str, int] = {
    "#": 6,
    "Author": 12,
    "\U0001f4ac": 2,
    "✓": 2,
    "Jira": 10,
}
_MIN_TITLE_WIDTH = 15


class PRTable(DataTable):
    """DataTable displaying pull requests."""

    DEFAULT_CSS = """
    PRTable {
        height: 1fr;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._pull_requests: list[PullRequest] = []
        self._pr_index: dict[int, int] = {}
        self._spinner_index: int = 0
        self._display_items: dict[int, PRDisplayItem] = {}

    def on_mount(self) -> None:
        for col in COLUMNS:
            width = _FIXED_WIDTHS.get(col)
            self.add_column(col, key=col, width=width)
        self.cursor_type = "row"
        self._resize_title_column()

    def on_resize(self) -> None:
        self._resize_title_column()

    def _update_column_widths(self, updated_cells) -> None:
        """After DataTable recalculates content_width, re-fit Title."""
        super()._update_column_widths(updated_cells)
        self._resize_title_column()

    def _resize_title_column(self) -> None:
        """Set Title column width to fill remaining space."""
        title_key = None
        other_width = 0
        for key, col in self.columns.items():
            if col.label.plain == "Title":
                title_key = key
            else:
                other_width += col.get_render_width(self)
        if title_key is None:
            return
        padding = 2 * self.cell_padding
        available = self.size.width - other_width - padding
        new_width = max(_MIN_TITLE_WIDTH, available)
        if self.columns[title_key].width != new_width:
            self.columns[title_key].width = new_width
            self.columns[title_key].auto_width = False
            self._require_update_dimensions = True

    def get_component_rich_style(self, name: str, *, partial: bool = False) -> Style:
        style = super().get_component_rich_style(name, partial=partial)
        if name == "datatable--cursor":
            return Style(bgcolor=style.bgcolor)
        return style

    @property
    def pull_requests(self) -> list[PullRequest]:
        return self._pull_requests

    def load_prs(
        self,
        prs: list[PullRequest],
        display_items: dict[int, PRDisplayItem] | None = None,
    ) -> None:
        """Load pull requests into the table."""
        self._pull_requests = prs
        self._pr_index = {pr.number: i for i, pr in enumerate(prs)}
        self._display_items = display_items or {}
        self._refresh_rows()

    def update_pr(self, pr: PullRequest) -> None:
        """Update a single PR in the table in-place."""
        idx = self._pr_index.get(pr.number)
        if idx is None:
            return
        self._pull_requests[idx] = pr
        self._update_row(pr)

    def advance_spinner(self) -> None:
        """Advance spinner animation frame and update running CI / deploying ACC / deploying PRD cells."""
        self._spinner_index += 1
        for pr in self._pull_requests:
            if pr.ci_status == CIStatus.RUNNING:
                self.update_cell(str(pr.number), "CI", ci_display(pr.ci_status, self._spinner_index, pr.ci_completed_steps, pr.ci_total_steps))
            if pr.acc_deploy in (DeployStatus.ACC_DEPLOYING, DeployStatus.ACC_ARGO):
                self.update_cell(str(pr.number), "ACC", acc_deploy_display(pr.acc_deploy, self._spinner_index, pr.acc_completed_steps, pr.acc_total_steps))
            if pr.prd_deploy in (PrdDeployStatus.PRD_DEPLOYING, PrdDeployStatus.PRD_ARGO):
                self.update_cell(str(pr.number), "PRD", prd_deploy_display(pr.prd_deploy, self._spinner_index, pr.prd_completed_steps, pr.prd_total_steps))

    def _row_values(self, pr: PullRequest) -> tuple:
        """Build the cell values for a PR row."""
        is_author = PRLabel.AUTHOR in pr.labels
        is_non_author_draft = PRLabel.DRAFT in pr.labels and not is_author
        author_text: str | Text = Text(pr.author, style=Color.BLUE) if is_author else pr.author
        title: str | Text = pr.title
        display = self._display_items.get(pr.number)
        if display and display.is_sub_pr:
            prefix = "  \u2514\u2500 " if display.is_last_sub_pr else "  \u251c\u2500 "
            title = prefix + title
        has_interest = bool(pr.labels - {PRLabel.FAVOURITE, PRLabel.DRAFT})
        number_text: str | Text = Text(str(pr.number), style=Color.YELLOW) if has_interest else str(pr.number)
        if is_non_author_draft:
            jira_text: str | Text = Text(pr.jira_ticket, style=Color.DIM) if pr.jira_ticket else "\u2014"
            title_text = title if isinstance(title, Text) else Text(title, style=Color.DIM)
            return (
                Text(str(pr.number), style=Color.DIM),
                title_text,
                Text(pr.author, style=Color.DIM),
                "\u2014",
                "\u2014",
                "\u2014",
                "\u2014",
                "\u2014",
                jira_text,
            )
        is_merged = pr.merged_at is not None
        if is_merged:
            comment_text = "\u2014"
            approval_text: str | Text = "\u2014"
            ci_text = "\u2014"
        else:
            ci_text = ci_display(pr.ci_status, self._spinner_index, pr.ci_completed_steps, pr.ci_total_steps)
            if pr.approval_count >= 2:
                approval_text = Text("✓", style=Color.GREEN)
            elif is_author:
                approval_text = Text(str(pr.approval_count), style=Color.BLUE)
            elif pr.user_approved:
                approval_text = Text(str(pr.approval_count), style=Color.GREEN)
            else:
                approval_text = Text(str(pr.approval_count), style=Color.YELLOW)
            if is_author:
                unresolved = pr.unresolved_threads
                has_threads = pr.total_threads > 0
            else:
                unresolved = pr.my_unresolved_threads
                has_threads = pr.my_commented_threads > 0
            if not has_threads:
                comment_text: str | Text = "\u2014"
            elif unresolved == 0:
                comment_text = Text("\u2713", style=Color.GREEN)
            else:
                comment_text = Text(str(unresolved), style=Color.YELLOW)
        acc_text = acc_deploy_display(pr.acc_deploy, self._spinner_index, pr.acc_completed_steps, pr.acc_total_steps)
        prd_text = prd_deploy_display(pr.prd_deploy, self._spinner_index, pr.prd_completed_steps, pr.prd_total_steps)
        jira_text = pr.jira_ticket or "\u2014"
        return (
            number_text,
            title,
            author_text,
            comment_text,
            approval_text,
            ci_text,
            acc_text,
            prd_text,
            jira_text,
        )

    def _update_row(self, pr: PullRequest) -> None:
        """Update a single row's cells by PR number key."""
        row_key = str(pr.number)
        values = self._row_values(pr)
        col_keys = list(COLUMNS)
        for col_key, value in zip(col_keys, values):
            self.update_cell(row_key, col_key, value)

    def _refresh_rows(self) -> None:
        """Clear and repopulate table rows, preserving cursor position."""
        saved_row = self.cursor_row
        self.clear()
        for pr in self._pull_requests:
            values = self._row_values(pr)
            self.add_row(*values, key=str(pr.number))
        if self._pull_requests:
            self.move_cursor(row=min(saved_row, len(self._pull_requests) - 1))

    async def flash_title(self, pr_number: int) -> None:
        """Flash the title cell of a PR 3× (grey ↔ white) over ~1 second.

        Aborts silently if the PR is removed from this table during the animation
        (e.g. because the user pressed f again before the flash finished).
        """
        idx = self._pr_index.get(pr_number)
        if idx is None:
            return
        pr = self._pull_requests[idx]
        if PRLabel.DRAFT in pr.labels and PRLabel.AUTHOR not in pr.labels:
            return
        base_title = pr.title
        display = self._display_items.get(pr_number)
        if display and display.is_sub_pr:
            prefix = "  \u2514\u2500 " if display.is_last_sub_pr else "  \u251c\u2500 "
            base_title = prefix + base_title
        row_key = str(pr_number)
        for i in range(6):
            if pr_number not in self._pr_index:
                return
            color = Color.DIM if i % 2 == 0 else "default"
            self.update_cell(row_key, "Title", Text(base_title, style=color))
            await asyncio.sleep(1 / 6)
        if pr_number not in self._pr_index:
            return
        self.update_cell(row_key, "Title", base_title)

    def get_selected_pr(self) -> PullRequest | None:
        """Get the currently selected pull request."""
        if not self._pull_requests:
            return None
        row = self.cursor_row
        if row < 0 or row >= len(self._pull_requests):
            return None
        return self._pull_requests[row]
