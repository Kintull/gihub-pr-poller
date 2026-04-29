"""Tests for the PRTable widget."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, call, patch

import pytest
from textual.app import App, ComposeResult

from rich.text import Text

from github_tracker.models import CIStatus, DeployStatus, PRLabel, PrdDeployStatus
from github_tracker.theme import Color
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
            assert values[3] == "\u2014"  # no threads yet → em dash
            assert values[4] == Text("✓", style=Color.GREEN)
            assert values[5] == Text("✓", style=Color.GREEN)
            assert values[6] == "\u2014"  # ACC = NONE → em dash
            assert values[7] == "\u2014"  # PRD = NONE → em dash
            assert values[8] == "PROJ-1"

    @pytest.mark.asyncio
    async def test_row_values_approval_levels(self):
        """Approvals: 0 → yellow Text '0', 1 → yellow Text '1', >=2 → green '✓'."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            # Non-author, non-approved PRs with count < 2 get yellow Text
            for count in [0, 1]:
                pr = make_pr(number=count + 100, approval_count=count)
                values = table._row_values(pr)
                assert isinstance(values[4], Text), f"approval_count={count}"
                assert values[4].plain == str(count), f"approval_count={count}"
            # count >= 2 always shows checkmark regardless of labels/approval
            for count in [2, 5]:
                pr = make_pr(number=count + 100, approval_count=count)
                values = table._row_values(pr)
                assert values[4] == Text("✓", style=Color.GREEN), f"approval_count={count}"

    @pytest.mark.asyncio
    async def test_row_values_threads_no_threads_dash(self):
        """No threads → em dash for both author and non-author PRs."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            pr = make_pr(number=1, total_threads=0, my_commented_threads=0)
            assert table._row_values(pr)[3] == "\u2014"
            pr_author = make_pr(number=2, labels=frozenset({PRLabel.AUTHOR}), total_threads=0)
            assert table._row_values(pr_author)[3] == "\u2014"

    @pytest.mark.asyncio
    async def test_row_values_threads_all_resolved_checkmark(self):
        """All threads resolved → green ✓."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            pr_author = make_pr(number=1, labels=frozenset({PRLabel.AUTHOR}),
                                total_threads=3, unresolved_threads=0)
            assert table._row_values(pr_author)[3] == Text("\u2713", style=Color.GREEN)
            pr_other = make_pr(number=2, my_commented_threads=2, my_unresolved_threads=0)
            assert table._row_values(pr_other)[3] == Text("\u2713", style=Color.GREEN)

    @pytest.mark.asyncio
    async def test_row_values_threads_unresolved_yellow(self):
        """Unresolved threads → yellow count."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            pr_author = make_pr(number=1, labels=frozenset({PRLabel.AUTHOR}),
                                total_threads=5, unresolved_threads=3)
            assert table._row_values(pr_author)[3] == Text("3", style=Color.YELLOW)
            pr_other = make_pr(number=2, my_commented_threads=4, my_unresolved_threads=2)
            assert table._row_values(pr_other)[3] == Text("2", style=Color.YELLOW)

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
            assert values[6] == Text("✓", style=Color.GREEN)  # ACC → deployed
            assert values[7] == "\u2014"  # PRD = NONE → em dash
            assert values[0] == "10"

    @pytest.mark.asyncio
    async def test_acc_column_in_columns(self):
        assert "ACC" in COLUMNS

    @pytest.mark.asyncio
    async def test_prd_column_in_columns(self):
        assert "PRD" in COLUMNS

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

    @pytest.mark.asyncio
    async def test_ci_cell_shows_progress(self):
        """CI cell shows spinner with progress when ci_total_steps > 0."""
        from github_tracker.models import SPINNER_FRAMES
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            pr = make_pr(
                number=1,
                ci_status=CIStatus.RUNNING,
                ci_completed_steps=2,
                ci_total_steps=3,
            )
            table._spinner_index = 0
            values = table._row_values(pr)
            assert values[5] == f"{SPINNER_FRAMES[0]}(2/3)"

    @pytest.mark.asyncio
    async def test_acc_cell_shows_argo(self):
        """ACC cell shows spinner+ARGO for ACC_ARGO status."""
        from datetime import datetime, timezone
        from github_tracker.models import SPINNER_FRAMES
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            pr = make_pr(
                number=1,
                merged_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
                acc_deploy=DeployStatus.ACC_ARGO,
            )
            table._spinner_index = 0
            values = table._row_values(pr)
            assert values[6] == f"{SPINNER_FRAMES[0]}ARGO"

    @pytest.mark.asyncio
    async def test_advance_spinner_with_prd_deploying(self):
        from datetime import datetime, timezone
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            prs = [make_pr(
                number=1,
                merged_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
                prd_deploy=PrdDeployStatus.PRD_DEPLOYING,
            )]
            table.load_prs(prs)
            initial_index = table._spinner_index
            table.advance_spinner()
            assert table._spinner_index == initial_index + 1

    @pytest.mark.asyncio
    async def test_prd_cell_shows_argo(self):
        """PRD cell shows spinner+ARGO for PRD_ARGO status."""
        from datetime import datetime, timezone
        from github_tracker.models import SPINNER_FRAMES
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            pr = make_pr(
                number=1,
                merged_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
                prd_deploy=PrdDeployStatus.PRD_ARGO,
            )
            table._spinner_index = 0
            values = table._row_values(pr)
            assert values[7] == f"{SPINNER_FRAMES[0]}ARGO"

    @pytest.mark.asyncio
    async def test_advance_spinner_animates_prd_argo(self):
        """advance_spinner also updates PRD cell for PRD_ARGO status."""
        from datetime import datetime, timezone
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            prs = [make_pr(
                number=1,
                merged_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
                prd_deploy=PrdDeployStatus.PRD_ARGO,
            )]
            table.load_prs(prs)
            initial_index = table._spinner_index
            table.advance_spinner()
            assert table._spinner_index == initial_index + 1

    @pytest.mark.asyncio
    async def test_row_values_prd_deployed(self):
        """PRD column shows green checkmark for PRD_DEPLOYED status."""
        from datetime import datetime, timezone
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            pr = make_pr(
                number=10,
                merged_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
                prd_deploy=PrdDeployStatus.PRD_DEPLOYED,
            )
            values = table._row_values(pr)
            assert values[7] == Text("✓", style=Color.GREEN)

    @pytest.mark.asyncio
    async def test_advance_spinner_animates_acc_argo(self):
        """advance_spinner also updates ACC cell for ACC_ARGO status."""
        from datetime import datetime, timezone
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            prs = [make_pr(
                number=1,
                merged_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
                acc_deploy=DeployStatus.ACC_ARGO,
            )]
            table.load_prs(prs)
            initial_index = table._spinner_index
            table.advance_spinner()
            assert table._spinner_index == initial_index + 1

    @pytest.mark.asyncio
    async def test_row_values_approval_author_pr_blue(self):
        """AUTHOR PRs show approval count in #336699 blue."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            pr = make_pr(number=1, approval_count=1, labels=frozenset({PRLabel.AUTHOR}))
            values = table._row_values(pr)
            assert values[4] == Text("1", style=Color.BLUE)

    @pytest.mark.asyncio
    async def test_row_values_author_column_blue_for_author(self):
        """Author name is shown in #336699 blue when PR belongs to current user."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            pr = make_pr(number=1, author="myuser", labels=frozenset({PRLabel.AUTHOR}))
            values = table._row_values(pr)
            assert values[2] == Text("myuser", style=Color.BLUE)

    @pytest.mark.asyncio
    async def test_row_values_author_column_plain_for_non_author(self):
        """Author name is shown as a plain string for other users' PRs."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            pr = make_pr(number=1, author="otheruser", labels=frozenset())
            values = table._row_values(pr)
            assert values[2] == "otheruser"

    @pytest.mark.asyncio
    async def test_row_values_approval_user_approved_green(self):
        """user_approved PRs (not author) show green colored count."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            pr = make_pr(number=1, approval_count=1, user_approved=True)
            values = table._row_values(pr)
            assert values[4] == Text("1", style=Color.GREEN)

    @pytest.mark.asyncio
    async def test_row_values_approval_needs_review_yellow(self):
        """Non-author, not-yet-approved PRs with < 2 approvals show yellow count."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            pr = make_pr(number=1, approval_count=0, user_approved=False)
            values = table._row_values(pr)
            assert values[4] == Text("0", style=Color.YELLOW)

    @pytest.mark.asyncio
    async def test_cursor_style_has_no_foreground_color(self):
        """Cursor component style has no foreground color so cell text colors are preserved."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            style = table.get_component_rich_style("datatable--cursor")
            assert style.color is None

    @pytest.mark.asyncio
    async def test_non_cursor_component_style_passthrough(self):
        """Non-cursor component styles are returned unchanged."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            style = table.get_component_rich_style("datatable--hover")
            # Just verify we get a Style object back (passthrough path)
            from rich.style import Style
            assert isinstance(style, Style)

    @pytest.mark.asyncio
    async def test_row_values_favourite_shows_star_prefix(self):
        """Favourite PRs display ★ prefix in the title column."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            pr = make_pr(number=1, title="My PR", labels=frozenset({PRLabel.FAVOURITE}))
            values = table._row_values(pr)
            assert values[1] == "\u2605 My PR"

    @pytest.mark.asyncio
    async def test_row_values_non_favourite_plain_title(self):
        """Non-favourite PRs display their title without any prefix."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            pr = make_pr(number=1, title="My PR")
            values = table._row_values(pr)
            assert values[1] == "My PR"

    @pytest.mark.asyncio
    async def test_row_values_related_pr_number_highlighted_yellow(self):
        """PRs with interest labels (AUTHOR, REVIEW_REQUESTED, MENTIONED, COMMENTED) show
        the # column in yellow to indicate a relationship without FAVOURITE."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            for label in (PRLabel.AUTHOR, PRLabel.REVIEW_REQUESTED, PRLabel.MENTIONED, PRLabel.COMMENTED):
                pr = make_pr(number=42, labels=frozenset({label}))
                values = table._row_values(pr)
                assert values[0] == Text("42", style=Color.YELLOW), f"expected yellow # for {label}"

    @pytest.mark.asyncio
    async def test_row_values_unrelated_pr_number_plain(self):
        """PRs with no interest labels show the # column as a plain string."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            pr = make_pr(number=7, labels=frozenset())
            values = table._row_values(pr)
            assert values[0] == "7"

    @pytest.mark.asyncio
    async def test_row_values_non_author_draft_grays_columns(self):
        """Non-author DRAFT PRs render #, Title, Author, Jira in DIM gray and em-dash other cells."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            pr = make_pr(
                number=77,
                title="Other's draft",
                author="someone",
                approval_count=2,
                ci_status=CIStatus.SUCCESS,
                jira_ticket="PROJ-9",
                total_threads=3,
                my_commented_threads=2,
                labels=frozenset({PRLabel.DRAFT}),
            )
            values = table._row_values(pr)
            assert values[0] == Text("77", style=Color.DIM)
            assert values[1] == Text("Other's draft", style=Color.DIM)
            assert values[2] == Text("someone", style=Color.DIM)
            assert values[3] == "—"
            assert values[4] == "—"
            assert values[5] == "—"
            assert values[6] == "—"
            assert values[7] == "—"
            assert values[8] == Text("PROJ-9", style=Color.DIM)

    @pytest.mark.asyncio
    async def test_row_values_non_author_draft_no_jira_dash(self):
        """Non-author DRAFT PR without a Jira ticket shows em-dash for Jira."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            pr = make_pr(number=1, jira_ticket=None, labels=frozenset({PRLabel.DRAFT}))
            values = table._row_values(pr)
            assert values[8] == "—"

    @pytest.mark.asyncio
    async def test_row_values_author_draft_renders_normally(self):
        """AUTHOR + DRAFT PRs render normally (gray styling only for non-author drafts)."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            pr = make_pr(
                number=5,
                author="me",
                ci_status=CIStatus.SUCCESS,
                labels=frozenset({PRLabel.AUTHOR, PRLabel.DRAFT}),
            )
            values = table._row_values(pr)
            assert values[2] == Text("me", style=Color.BLUE)
            assert values[5] == Text("✓", style=Color.GREEN)

    @pytest.mark.asyncio
    async def test_flash_title_cycles_and_restores(self):
        """flash_title calls update_cell 7 times: 6 flash steps then plain restore."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            pr = make_pr(number=1, title="Flash Me", labels=frozenset())
            table.load_prs([pr])
            with patch("github_tracker.widgets.pr_table.asyncio.sleep", new_callable=AsyncMock):
                with patch.object(table, "update_cell") as mock_update:
                    await table.flash_title(1)
            assert mock_update.call_count == 7
            # Odd steps are grey, even steps are default
            assert mock_update.call_args_list[0] == call("1", "Title", Text("Flash Me", style=Color.DIM))
            assert mock_update.call_args_list[1] == call("1", "Title", Text("Flash Me", style="default"))
            # Final restore is plain string
            assert mock_update.call_args_list[6] == call("1", "Title", "Flash Me")

    @pytest.mark.asyncio
    async def test_flash_title_skips_non_author_draft(self):
        """Non-author drafts already render in gray — flashing white would flicker."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            pr = make_pr(number=1, title="Drafty", labels=frozenset({PRLabel.DRAFT}))
            table.load_prs([pr])
            with patch("github_tracker.widgets.pr_table.asyncio.sleep", new_callable=AsyncMock):
                with patch.object(table, "update_cell") as mock_update:
                    await table.flash_title(1)
            mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_flash_title_unknown_pr_is_noop(self):
        """flash_title does nothing when the PR is not in the table."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            table.load_prs([make_pr(number=1)])
            with patch.object(table, "update_cell") as mock_update:
                await table.flash_title(9999)
            mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_flash_title_aborts_mid_loop_if_pr_removed(self):
        """flash_title stops mid-animation if the PR leaves this table."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            table.load_prs([make_pr(number=1, title="Gone")])

            sleep_count = 0

            async def _sleep_and_remove(_):
                nonlocal sleep_count
                sleep_count += 1
                if sleep_count == 1:
                    table._pr_index.clear()

            with patch("github_tracker.widgets.pr_table.asyncio.sleep", side_effect=_sleep_and_remove):
                with patch.object(table, "update_cell") as mock_update:
                    await table.flash_title(1)
            # Only the first flash step fired before the abort
            assert mock_update.call_count == 1

    @pytest.mark.asyncio
    async def test_flash_title_skips_restore_if_pr_removed_after_last_flash(self):
        """flash_title skips the restore call if the PR is removed after the last flash step."""
        async with PRTableTestApp().run_test() as pilot:
            table = pilot.app.query_one("#pr-table", PRTable)
            table.load_prs([make_pr(number=1, title="Gone")])

            sleep_count = 0

            async def _sleep_and_remove(_):
                nonlocal sleep_count
                sleep_count += 1
                if sleep_count == 6:
                    table._pr_index.clear()

            with patch("github_tracker.widgets.pr_table.asyncio.sleep", side_effect=_sleep_and_remove):
                with patch.object(table, "update_cell") as mock_update:
                    await table.flash_title(1)
            # All 6 flash steps fired, but the final restore was skipped
            assert mock_update.call_count == 6


