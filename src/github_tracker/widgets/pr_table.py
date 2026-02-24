"""PR table widget for displaying pull requests."""

from __future__ import annotations

from dataclasses import replace

from textual.widgets import DataTable

from github_tracker.models import CIStatus, DeployStatus, PullRequest, acc_deploy_display, ci_display

COLUMNS = ("#", "Title", "Author", "\U0001f4ac", "\u2705", "CI", "ACC", "Jira")


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

    def on_mount(self) -> None:
        for col in COLUMNS:
            self.add_column(col, key=col)
        self.cursor_type = "row"

    @property
    def pull_requests(self) -> list[PullRequest]:
        return self._pull_requests

    def load_prs(self, prs: list[PullRequest]) -> None:
        """Load pull requests into the table."""
        self._pull_requests = prs
        self._pr_index = {pr.number: i for i, pr in enumerate(prs)}
        self._refresh_rows()

    def update_pr(self, pr: PullRequest) -> None:
        """Update a single PR in the table in-place."""
        idx = self._pr_index.get(pr.number)
        if idx is None:
            return
        self._pull_requests[idx] = pr
        self._update_row(pr)

    def advance_spinner(self) -> None:
        """Advance spinner animation frame and update running CI / deploying ACC cells."""
        self._spinner_index += 1
        for pr in self._pull_requests:
            if pr.ci_status == CIStatus.RUNNING:
                self.update_cell(str(pr.number), "CI", ci_display(pr.ci_status, self._spinner_index, pr.ci_completed_steps, pr.ci_total_steps))
            if pr.acc_deploy in (DeployStatus.ACC_DEPLOYING, DeployStatus.ACC_ARGO):
                self.update_cell(str(pr.number), "ACC", acc_deploy_display(pr.acc_deploy, self._spinner_index, pr.acc_completed_steps, pr.acc_total_steps))

    def _row_values(self, pr: PullRequest) -> tuple[str, ...]:
        """Build the cell values for a PR row."""
        is_merged = pr.merged_at is not None
        if is_merged:
            comment_text = "\u2014"
            approval_text = "\u2014"
            ci_text = "\u2014"
        else:
            ci_text = ci_display(pr.ci_status, self._spinner_index, pr.ci_completed_steps, pr.ci_total_steps)
            if pr.approval_count >= 2:
                approval_text = "\u2705"
            else:
                approval_text = str(pr.approval_count)
            comment_text = str(pr.comment_count)
        acc_text = acc_deploy_display(pr.acc_deploy, self._spinner_index, pr.acc_completed_steps, pr.acc_total_steps)
        jira_text = pr.jira_ticket or "\u2014"
        return (
            str(pr.number),
            pr.title,
            pr.author,
            comment_text,
            approval_text,
            ci_text,
            acc_text,
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

    def get_selected_pr(self) -> PullRequest | None:
        """Get the currently selected pull request."""
        if not self._pull_requests:
            return None
        row = self.cursor_row
        if row < 0 or row >= len(self._pull_requests):
            return None
        return self._pull_requests[row]
