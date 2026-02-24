"""Tests for models module."""

from datetime import datetime, timezone

from github_tracker.models import (
    ACC_DEPLOY_SYMBOLS,
    CI_SYMBOLS,
    SPINNER_FRAMES,
    CIStatus,
    DeployStatus,
    PRLabel,
    PullRequest,
    acc_deploy_display,
    ci_display,
)


class TestCIStatus:
    def test_enum_values(self):
        assert CIStatus.PENDING.value == "pending"
        assert CIStatus.RUNNING.value == "running"
        assert CIStatus.SUCCESS.value == "success"
        assert CIStatus.FAILURE.value == "failure"
        assert CIStatus.UNKNOWN.value == "unknown"

    def test_from_github_queued(self):
        assert CIStatus.from_github("queued", None) == CIStatus.PENDING

    def test_from_github_in_progress(self):
        assert CIStatus.from_github("in_progress", None) == CIStatus.RUNNING

    def test_from_github_completed_success(self):
        assert CIStatus.from_github("completed", "success") == CIStatus.SUCCESS

    def test_from_github_completed_failure(self):
        assert CIStatus.from_github("completed", "failure") == CIStatus.FAILURE

    def test_from_github_completed_timed_out(self):
        assert CIStatus.from_github("completed", "timed_out") == CIStatus.FAILURE

    def test_from_github_completed_cancelled(self):
        assert CIStatus.from_github("completed", "cancelled") == CIStatus.FAILURE

    def test_from_github_completed_action_required(self):
        assert CIStatus.from_github("completed", "action_required") == CIStatus.FAILURE

    def test_from_github_completed_neutral(self):
        assert CIStatus.from_github("completed", "neutral") == CIStatus.SUCCESS

    def test_from_github_completed_skipped(self):
        assert CIStatus.from_github("completed", "skipped") == CIStatus.SUCCESS

    def test_from_github_completed_unknown_conclusion(self):
        assert CIStatus.from_github("completed", "stale") == CIStatus.UNKNOWN

    def test_from_github_unknown_status(self):
        assert CIStatus.from_github("something_else", None) == CIStatus.UNKNOWN


class TestCIDisplay:
    def test_pending(self):
        assert ci_display(CIStatus.PENDING) == "⏳"

    def test_success(self):
        assert ci_display(CIStatus.SUCCESS) == "🟢"

    def test_failure(self):
        assert ci_display(CIStatus.FAILURE) == "❌"

    def test_unknown(self):
        assert ci_display(CIStatus.UNKNOWN) == "❓"

    def test_running_spinner_index_0(self):
        assert ci_display(CIStatus.RUNNING, 0) == SPINNER_FRAMES[0]

    def test_running_spinner_index_wraps(self):
        idx = len(SPINNER_FRAMES) + 3
        assert ci_display(CIStatus.RUNNING, idx) == SPINNER_FRAMES[3]

    def test_running_default_index(self):
        assert ci_display(CIStatus.RUNNING) == SPINNER_FRAMES[0]


class TestCISymbols:
    def test_all_non_running_statuses_have_symbols(self):
        for status in CIStatus:
            if status != CIStatus.RUNNING:
                assert status in CI_SYMBOLS


class TestDeployStatus:
    def test_enum_values(self):
        assert DeployStatus.ACC_DEPLOYING.value == "acc_deploying"
        assert DeployStatus.ACC_DEPLOYED.value == "acc_deployed"
        assert DeployStatus.NONE.value == "none"

    def test_from_value(self):
        assert DeployStatus("acc_deploying") == DeployStatus.ACC_DEPLOYING
        assert DeployStatus("none") == DeployStatus.NONE


class TestAccDeployDisplay:
    def test_deployed(self):
        assert acc_deploy_display(DeployStatus.ACC_DEPLOYED) == "🟢"

    def test_none(self):
        assert acc_deploy_display(DeployStatus.NONE) == "\u2014"

    def test_deploying_spinner_index_0(self):
        assert acc_deploy_display(DeployStatus.ACC_DEPLOYING, 0) == SPINNER_FRAMES[0]

    def test_deploying_spinner_index_wraps(self):
        idx = len(SPINNER_FRAMES) + 3
        assert acc_deploy_display(DeployStatus.ACC_DEPLOYING, idx) == SPINNER_FRAMES[3]

    def test_deploying_default_index(self):
        assert acc_deploy_display(DeployStatus.ACC_DEPLOYING) == SPINNER_FRAMES[0]


class TestAccDeploySymbols:
    def test_all_non_deploying_statuses_have_symbols(self):
        for status in DeployStatus:
            if status != DeployStatus.ACC_DEPLOYING:
                assert status in ACC_DEPLOY_SYMBOLS


class TestPRLabel:
    def test_enum_values(self):
        assert PRLabel.AUTHOR.value == "author"
        assert PRLabel.REVIEW_REQUESTED.value == "review_requested"
        assert PRLabel.MENTIONED.value == "mentioned"
        assert PRLabel.COMMENTED.value == "commented"

    def test_from_value(self):
        assert PRLabel("author") == PRLabel.AUTHOR
        assert PRLabel("commented") == PRLabel.COMMENTED


class TestPullRequest:
    def test_creation(self):
        now = datetime.now(tz=timezone.utc)
        pr = PullRequest(
            number=42,
            title="Add feature",
            url="https://github.com/owner/repo/pull/42",
            branch_name="PROJ-123-add-feature",
            comment_count=5,
            approval_count=2,
            ci_status=CIStatus.SUCCESS,
            jira_ticket="PROJ-123",
            jira_url="https://jira.example.com/browse/PROJ-123",
            author="alice",
            updated_at=now,
            repo="owner/repo",
        )
        assert pr.number == 42
        assert pr.title == "Add feature"
        assert pr.url == "https://github.com/owner/repo/pull/42"
        assert pr.branch_name == "PROJ-123-add-feature"
        assert pr.comment_count == 5
        assert pr.approval_count == 2
        assert pr.ci_status == CIStatus.SUCCESS
        assert pr.jira_ticket == "PROJ-123"
        assert pr.jira_url == "https://jira.example.com/browse/PROJ-123"
        assert pr.author == "alice"
        assert pr.updated_at == now
        assert pr.repo == "owner/repo"

    def test_default_labels_empty(self):
        pr = PullRequest(
            number=1,
            title="Fix bug",
            url="https://github.com/o/r/pull/1",
            branch_name="fix-bug",
            comment_count=0,
            approval_count=0,
            ci_status=CIStatus.UNKNOWN,
            jira_ticket=None,
            jira_url=None,
            author="bob",
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            repo="o/r",
        )
        assert pr.labels == frozenset()

    def test_explicit_labels(self):
        pr = PullRequest(
            number=1,
            title="Fix bug",
            url="https://github.com/o/r/pull/1",
            branch_name="fix-bug",
            comment_count=0,
            approval_count=0,
            ci_status=CIStatus.UNKNOWN,
            jira_ticket=None,
            jira_url=None,
            author="bob",
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            repo="o/r",
            labels=frozenset({PRLabel.AUTHOR, PRLabel.MENTIONED}),
        )
        assert PRLabel.AUTHOR in pr.labels
        assert PRLabel.MENTIONED in pr.labels
        assert len(pr.labels) == 2

    def test_default_acc_deploy(self):
        pr = PullRequest(
            number=1,
            title="Fix bug",
            url="https://github.com/o/r/pull/1",
            branch_name="fix-bug",
            comment_count=0,
            approval_count=0,
            ci_status=CIStatus.UNKNOWN,
            jira_ticket=None,
            jira_url=None,
            author="bob",
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            repo="o/r",
        )
        assert pr.acc_deploy == DeployStatus.NONE

    def test_default_merged_at(self):
        pr = PullRequest(
            number=1,
            title="Fix bug",
            url="https://github.com/o/r/pull/1",
            branch_name="fix-bug",
            comment_count=0,
            approval_count=0,
            ci_status=CIStatus.UNKNOWN,
            jira_ticket=None,
            jira_url=None,
            author="bob",
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            repo="o/r",
        )
        assert pr.merged_at is None

    def test_explicit_acc_deploy_and_merged_at(self):
        now = datetime.now(tz=timezone.utc)
        pr = PullRequest(
            number=1,
            title="Fix bug",
            url="https://github.com/o/r/pull/1",
            branch_name="fix-bug",
            comment_count=0,
            approval_count=0,
            ci_status=CIStatus.UNKNOWN,
            jira_ticket=None,
            jira_url=None,
            author="bob",
            updated_at=now,
            repo="o/r",
            acc_deploy=DeployStatus.ACC_DEPLOYING,
            merged_at=now,
        )
        assert pr.acc_deploy == DeployStatus.ACC_DEPLOYING
        assert pr.merged_at == now

    def test_creation_no_jira(self):
        pr = PullRequest(
            number=1,
            title="Fix bug",
            url="https://github.com/o/r/pull/1",
            branch_name="fix-bug",
            comment_count=0,
            approval_count=0,
            ci_status=CIStatus.UNKNOWN,
            jira_ticket=None,
            jira_url=None,
            author="bob",
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            repo="o/r",
        )
        assert pr.jira_ticket is None
        assert pr.jira_url is None
