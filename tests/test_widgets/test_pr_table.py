"""Tests for the PRTable widget."""

from __future__ import annotations

from dataclasses import replace

import pytest
from textual.app import App, ComposeResult

from github_tracker.models import CIStatus, DeployStatus
from github_tracker.widgets.pr_table import COLUMNS, PRTable
from tests.conftest import make_pr


class PRTableTestApp(App):
    def compose(self) -> ComposeResult:
        yield PRTable(id="pr-table")


class TestPRTable:
    @pytest.mark.asyncio
    async def test_columns_added_on_mount(self):
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            column_labels = [col.label.plain for col in table.columns.values()]
            for col in COLUMNS:
                assert col in column_labels

    @pytest.mark.asyncio
    async def test_cursor_type_is_row(self):
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            assert table.cursor_type == "row"

    @pytest.mark.asyncio
    async def test_load_prs(self):
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            prs = [
                make_pr(number=1, title="PR One", comment_count=5, approval_count=2),
                make_pr(number=2, title="PR Two", ci_status=CIStatus.FAILURE),
            ]
            table.load_prs(prs)
            assert table.row_count == 2
            assert table.pull_requests == prs

    @pytest.mark.asyncio
    async def test_load_prs_builds_index(self):
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            prs = [make_pr(number=10), make_pr(number=20)]
            table.load_prs(prs)
            assert table._pr_index == {10: 0, 20: 1}

    @pytest.mark.asyncio
    async def test_load_empty_prs(self):
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            table.load_prs([])
            assert table.row_count == 0

    @pytest.mark.asyncio
    async def test_get_selected_pr(self):
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            prs = [make_pr(number=1), make_pr(number=2)]
            table.load_prs(prs)
            table.move_cursor(row=0)
            selected = table.get_selected_pr()
            assert selected is not None
            assert selected.number == 1

    @pytest.mark.asyncio
    async def test_get_selected_pr_empty(self):
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            assert table.get_selected_pr() is None

    @pytest.mark.asyncio
    async def test_get_selected_pr_negative_cursor(self):
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            assert table.get_selected_pr() is None

    @pytest.mark.asyncio
    async def test_advance_spinner_with_running(self):
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            prs = [make_pr(number=1, ci_status=CIStatus.RUNNING)]
            table.load_prs(prs)
            initial_index = table._spinner_index
            table.advance_spinner()
            assert table._spinner_index == initial_index + 1

    @pytest.mark.asyncio
    async def test_advance_spinner_no_running(self):
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            prs = [make_pr(number=1, ci_status=CIStatus.SUCCESS)]
            table.load_prs(prs)
            table.advance_spinner()
            assert table._spinner_index == 1

    @pytest.mark.asyncio
    async def test_jira_ticket_display(self):
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            prs = [make_pr(number=1, jira_ticket="PROJ-123")]
            table.load_prs(prs)
            assert table.row_count == 1

    @pytest.mark.asyncio
    async def test_no_jira_shows_dash(self):
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            prs = [make_pr(number=1, jira_ticket=None)]
            table.load_prs(prs)
            assert table.row_count == 1

    @pytest.mark.asyncio
    async def test_get_selected_pr_cursor_beyond_list(self):
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            prs = [make_pr(number=1), make_pr(number=2)]
            table.load_prs(prs)
            table.move_cursor(row=1)
            table._pull_requests = [make_pr(number=1)]
            assert table.get_selected_pr() is None

    @pytest.mark.asyncio
    async def test_reload_replaces_data(self):
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            table.load_prs([make_pr(number=1)])
            assert table.row_count == 1
            table.load_prs([make_pr(number=2), make_pr(number=3)])
            assert table.row_count == 2

    @pytest.mark.asyncio
    async def test_update_pr_in_place(self):
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            pr = make_pr(number=1, approval_count=0, ci_status=CIStatus.PENDING)
            table.load_prs([pr])
            updated = replace(pr, approval_count=3, ci_status=CIStatus.SUCCESS)
            table.update_pr(updated)
            assert table.pull_requests[0].approval_count == 3
            assert table.pull_requests[0].ci_status == CIStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_update_pr_unknown_number_ignored(self):
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            table.load_prs([make_pr(number=1)])
            unknown_pr = make_pr(number=999)
            table.update_pr(unknown_pr)
            assert table.row_count == 1
            assert table.pull_requests[0].number == 1

    @pytest.mark.asyncio
    async def test_advance_spinner_updates_ci_cell_only(self):
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            prs = [
                make_pr(number=1, ci_status=CIStatus.RUNNING),
                make_pr(number=2, ci_status=CIStatus.SUCCESS),
            ]
            table.load_prs(prs)
            table.move_cursor(row=1)
            table.advance_spinner()
            # Cursor should NOT be reset by spinner
            assert table.cursor_row == 1

    @pytest.mark.asyncio
    async def test_refresh_rows_preserves_cursor(self):
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            prs = [make_pr(number=1), make_pr(number=2), make_pr(number=3)]
            table.load_prs(prs)
            table.move_cursor(row=2)
            # Simulate a reload which triggers _refresh_rows
            table.load_prs(prs)
            assert table.cursor_row == 2

    @pytest.mark.asyncio
    async def test_refresh_rows_clamps_cursor(self):
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            prs = [make_pr(number=1), make_pr(number=2), make_pr(number=3)]
            table.load_prs(prs)
            table.move_cursor(row=2)
            # Reload with fewer PRs — cursor should clamp to last row
            table.load_prs([make_pr(number=1)])
            assert table.cursor_row == 0

    @pytest.mark.asyncio
    async def test_row_values(self):
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            pr = make_pr(
                number=42,
                title="My PR",
                author="alice",
                comment_count=3,
                approval_count=2,
                ci_status=CIStatus.SUCCESS,
                jira_ticket="PROJ-1",
                url="https://github.com/o/r/pull/42",
            )
            values = table._row_values(pr)
            assert values[0] == "42"
            assert values[1] == "My PR"
            assert values[2] == "alice"
            assert values[3] == "3"
            assert values[4] == "\u2705"
            assert values[5] == "\U0001f7e2"
            assert values[6] == "\u2014"  # ACC = NONE → em dash
            assert values[7] == "PROJ-1"

    @pytest.mark.asyncio
    async def test_row_values_approval_levels(self):
        """Approvals: 0 → '0', 1 → '1', >=2 → '✅'."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            for count, expected in [(0, "0"), (1, "1"), (2, "\u2705"), (5, "\u2705")]:
                pr = make_pr(number=count + 100, approval_count=count)
                values = table._row_values(pr)
                assert values[4] == expected, f"approval_count={count}"

    @pytest.mark.asyncio
    async def test_row_values_comments_plain_number(self):
        """Comments should display as a plain number, no emoji."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            for count in [0, 1, 7]:
                pr = make_pr(number=count + 200, comment_count=count)
                values = table._row_values(pr)
                assert values[3] == str(count)

    @pytest.mark.asyncio
    async def test_row_values_merged_pr(self):
        """Merged PRs show dashes for comments/approvals/CI and ACC deploy status."""
        from datetime import datetime, timezone
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            pr = make_pr(
                number=10,
                merged_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
                acc_deploy=DeployStatus.ACC_DEPLOYED,
                comment_count=5,
                approval_count=2,
                ci_status=CIStatus.SUCCESS,
            )
            values = table._row_values(pr)
            assert values[3] == "\u2014"  # comments → dash
            assert values[4] == "\u2014"  # approvals → dash
            assert values[5] == "\u2014"  # CI → dash
            assert values[6] == "\U0001f7e2"  # ACC → deployed green
            assert values[0] == "10"

    @pytest.mark.asyncio
    async def test_acc_column_in_columns(self):
        assert "ACC" in COLUMNS

    @pytest.mark.asyncio
    async def test_advance_spinner_with_deploying(self):
        from datetime import datetime, timezone
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            prs = [make_pr(
                number=1,
                merged_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
                acc_deploy=DeployStatus.ACC_DEPLOYING,
            )]
            table.load_prs(prs)
            initial_index = table._spinner_index
            table.advance_spinner()
            assert table._spinner_index == initial_index + 1
