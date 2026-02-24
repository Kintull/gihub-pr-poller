"""Tests for jira module."""

from github_tracker.jira import build_jira_url, extract_jira_ticket


class TestExtractJiraTicket:
    def test_from_branch_name(self):
        assert extract_jira_ticket("PROJ-123-add-feature", "Some title") == "PROJ-123"

    def test_from_title_when_branch_has_none(self):
        assert extract_jira_ticket("fix-bug", "[PROJ-456] Fix the bug") == "PROJ-456"

    def test_branch_takes_priority_over_title(self):
        assert (
            extract_jira_ticket("PROJ-111-feature", "[PROJ-222] Title")
            == "PROJ-111"
        )

    def test_no_match_returns_none(self):
        assert extract_jira_ticket("fix-bug", "Fix the bug") is None

    def test_empty_strings(self):
        assert extract_jira_ticket("", "") is None

    def test_ticket_in_middle_of_branch(self):
        assert extract_jira_ticket("feature/PROJ-789/impl", "title") == "PROJ-789"

    def test_multi_digit_project_key(self):
        assert extract_jira_ticket("AB2-999-test", "") == "AB2-999"

    def test_single_char_after_first_is_valid(self):
        assert extract_jira_ticket("AB-1", "") == "AB-1"

    def test_lowercase_does_not_match(self):
        assert extract_jira_ticket("proj-123", "proj-456") is None

    def test_multiple_tickets_returns_first(self):
        assert extract_jira_ticket("PROJ-1-and-PROJ-2", "") == "PROJ-1"

    def test_title_with_brackets(self):
        assert extract_jira_ticket("main", "[TEAM-42] Add logging") == "TEAM-42"


class TestBuildJiraUrl:
    def test_basic(self):
        url = build_jira_url("PROJ-123", "https://jira.example.com/browse")
        assert url == "https://jira.example.com/browse/PROJ-123"

    def test_strips_trailing_slash(self):
        url = build_jira_url("PROJ-123", "https://jira.example.com/browse/")
        assert url == "https://jira.example.com/browse/PROJ-123"

    def test_multiple_trailing_slashes(self):
        url = build_jira_url("PROJ-1", "https://jira.example.com///")
        assert url == "https://jira.example.com/PROJ-1"
