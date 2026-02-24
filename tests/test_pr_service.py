"""Tests for the pr_service module."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from github_tracker.models import DeployStatus, PRLabel
from github_tracker.pr_service import (
    compute_acc_deploy,
    compute_phase1_labels,
    compute_phase2_labels,
    filter_expired_merged_prs,
    group_prs,
)
from tests.conftest import make_pr, make_workflow_run_response


class TestComputePhase1Labels:
    def test_empty_username_returns_empty(self):
        pr = make_pr(author="alice")
        raw = {"requested_reviewers": [], "body": ""}
        assert compute_phase1_labels(pr, raw, "") == frozenset()

    def test_author_match(self):
        pr = make_pr(author="alice")
        raw = {"requested_reviewers": [], "body": ""}
        labels = compute_phase1_labels(pr, raw, "alice")
        assert PRLabel.AUTHOR in labels

    def test_author_case_insensitive(self):
        pr = make_pr(author="Alice")
        raw = {"requested_reviewers": [], "body": ""}
        labels = compute_phase1_labels(pr, raw, "alice")
        assert PRLabel.AUTHOR in labels

    def test_not_author(self):
        pr = make_pr(author="bob")
        raw = {"requested_reviewers": [], "body": ""}
        labels = compute_phase1_labels(pr, raw, "alice")
        assert PRLabel.AUTHOR not in labels

    def test_review_requested(self):
        pr = make_pr(author="bob")
        raw = {"requested_reviewers": [{"login": "alice"}], "body": ""}
        labels = compute_phase1_labels(pr, raw, "alice")
        assert PRLabel.REVIEW_REQUESTED in labels

    def test_review_requested_case_insensitive(self):
        pr = make_pr(author="bob")
        raw = {"requested_reviewers": [{"login": "Alice"}], "body": ""}
        labels = compute_phase1_labels(pr, raw, "alice")
        assert PRLabel.REVIEW_REQUESTED in labels

    def test_review_not_requested(self):
        pr = make_pr(author="bob")
        raw = {"requested_reviewers": [{"login": "charlie"}], "body": ""}
        labels = compute_phase1_labels(pr, raw, "alice")
        assert PRLabel.REVIEW_REQUESTED not in labels

    def test_mentioned_in_body(self):
        pr = make_pr(author="bob")
        raw = {"requested_reviewers": [], "body": "cc @alice for review"}
        labels = compute_phase1_labels(pr, raw, "alice")
        assert PRLabel.MENTIONED in labels

    def test_mentioned_case_insensitive(self):
        pr = make_pr(author="bob")
        raw = {"requested_reviewers": [], "body": "cc @Alice for review"}
        labels = compute_phase1_labels(pr, raw, "alice")
        assert PRLabel.MENTIONED in labels

    def test_not_mentioned(self):
        pr = make_pr(author="bob")
        raw = {"requested_reviewers": [], "body": "cc @charlie for review"}
        labels = compute_phase1_labels(pr, raw, "alice")
        assert PRLabel.MENTIONED not in labels

    def test_mentioned_word_boundary(self):
        pr = make_pr(author="bob")
        raw = {"requested_reviewers": [], "body": "@alice-bot is not @alice"}
        labels = compute_phase1_labels(pr, raw, "alice")
        assert PRLabel.MENTIONED in labels

    def test_multiple_labels(self):
        pr = make_pr(author="alice")
        raw = {"requested_reviewers": [{"login": "alice"}], "body": "cc @alice"}
        labels = compute_phase1_labels(pr, raw, "alice")
        assert PRLabel.AUTHOR in labels
        assert PRLabel.REVIEW_REQUESTED in labels
        assert PRLabel.MENTIONED in labels

    def test_null_body(self):
        pr = make_pr(author="bob")
        raw = {"requested_reviewers": [], "body": None}
        labels = compute_phase1_labels(pr, raw, "alice")
        assert PRLabel.MENTIONED not in labels

    def test_missing_requested_reviewers(self):
        pr = make_pr(author="bob")
        raw = {"body": ""}
        labels = compute_phase1_labels(pr, raw, "alice")
        assert PRLabel.REVIEW_REQUESTED not in labels


class TestComputePhase2Labels:
    def test_empty_username_returns_existing(self):
        existing = frozenset({PRLabel.AUTHOR})
        result = compute_phase2_labels(existing, [{"user": {"login": "alice"}}], "")
        assert result == existing

    def test_commented(self):
        existing = frozenset()
        reviews = [{"user": {"login": "alice"}, "state": "COMMENTED"}]
        result = compute_phase2_labels(existing, reviews, "alice")
        assert PRLabel.COMMENTED in result

    def test_commented_case_insensitive(self):
        existing = frozenset()
        reviews = [{"user": {"login": "Alice"}, "state": "APPROVED"}]
        result = compute_phase2_labels(existing, reviews, "alice")
        assert PRLabel.COMMENTED in result

    def test_not_commented(self):
        existing = frozenset()
        reviews = [{"user": {"login": "charlie"}, "state": "APPROVED"}]
        result = compute_phase2_labels(existing, reviews, "alice")
        assert PRLabel.COMMENTED not in result

    def test_merges_with_existing(self):
        existing = frozenset({PRLabel.AUTHOR})
        reviews = [{"user": {"login": "alice"}, "state": "COMMENTED"}]
        result = compute_phase2_labels(existing, reviews, "alice")
        assert PRLabel.AUTHOR in result
        assert PRLabel.COMMENTED in result

    def test_no_reviews(self):
        existing = frozenset({PRLabel.AUTHOR})
        result = compute_phase2_labels(existing, [], "alice")
        assert result == existing

    def test_missing_user_in_review(self):
        existing = frozenset()
        reviews = [{"user": None, "state": "COMMENTED"}]
        result = compute_phase2_labels(existing, reviews, "alice")
        assert PRLabel.COMMENTED not in result


class TestGroupPrs:
    def test_empty_list(self):
        my, other = group_prs([])
        assert my == []
        assert other == []

    def test_all_mine(self):
        prs = [
            make_pr(number=1, labels=frozenset({PRLabel.AUTHOR})),
            make_pr(number=2, labels=frozenset({PRLabel.REVIEW_REQUESTED})),
        ]
        my, other = group_prs(prs)
        assert len(my) == 2
        assert len(other) == 0

    def test_all_other(self):
        prs = [make_pr(number=1), make_pr(number=2)]
        my, other = group_prs(prs)
        assert len(my) == 0
        assert len(other) == 2

    def test_mixed(self):
        prs = [
            make_pr(number=1, labels=frozenset({PRLabel.AUTHOR})),
            make_pr(number=2),
            make_pr(number=3, labels=frozenset({PRLabel.COMMENTED})),
        ]
        my, other = group_prs(prs)
        assert [p.number for p in my] == [1, 3]
        assert [p.number for p in other] == [2]

    def test_preserves_ordering(self):
        prs = [
            make_pr(number=5, labels=frozenset({PRLabel.AUTHOR})),
            make_pr(number=3),
            make_pr(number=1, labels=frozenset({PRLabel.MENTIONED})),
            make_pr(number=4),
        ]
        my, other = group_prs(prs)
        assert [p.number for p in my] == [5, 1]
        assert [p.number for p in other] == [3, 4]


class TestComputeAccDeploy:
    def test_no_merged_at_returns_none(self):
        pr = make_pr(merged_at=None)
        assert compute_acc_deploy(pr, [], 20) == DeployStatus.NONE

    def test_no_runs_returns_deploying(self):
        pr = make_pr(merged_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc))
        assert compute_acc_deploy(pr, [], 20) == DeployStatus.ACC_DEPLOYING

    def test_run_in_progress_returns_deploying(self):
        pr = make_pr(merged_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc))
        runs = [make_workflow_run_response(
            status="in_progress",
            conclusion=None,
            created_at="2024-06-15T12:30:00Z",
        )]
        assert compute_acc_deploy(pr, runs, 20) == DeployStatus.ACC_DEPLOYING

    def test_run_queued_returns_deploying(self):
        pr = make_pr(merged_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc))
        runs = [make_workflow_run_response(
            status="queued",
            conclusion=None,
            created_at="2024-06-15T12:30:00Z",
        )]
        assert compute_acc_deploy(pr, runs, 20) == DeployStatus.ACC_DEPLOYING

    def test_completed_success_within_cooldown_returns_deploying(self):
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(merged_at=now - timedelta(hours=1))
        runs = [make_workflow_run_response(
            status="completed",
            conclusion="success",
            created_at=(now - timedelta(minutes=30)).isoformat(),
            updated_at=(now - timedelta(minutes=5)).isoformat(),  # within 20min cooldown
        )]
        assert compute_acc_deploy(pr, runs, 20) == DeployStatus.ACC_DEPLOYING

    def test_completed_success_past_cooldown_returns_deployed(self):
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(merged_at=now - timedelta(hours=2))
        runs = [make_workflow_run_response(
            status="completed",
            conclusion="success",
            created_at=(now - timedelta(hours=1, minutes=30)).isoformat(),
            updated_at=(now - timedelta(hours=1)).isoformat(),  # well past 20min cooldown
        )]
        assert compute_acc_deploy(pr, runs, 20) == DeployStatus.ACC_DEPLOYED

    def test_completed_failure_returns_deploying(self):
        pr = make_pr(merged_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc))
        runs = [make_workflow_run_response(
            status="completed",
            conclusion="failure",
            created_at="2024-06-15T12:30:00Z",
        )]
        assert compute_acc_deploy(pr, runs, 20) == DeployStatus.ACC_DEPLOYING

    def test_run_before_merge_ignored(self):
        pr = make_pr(merged_at=datetime(2024, 6, 15, 14, 0, 0, tzinfo=timezone.utc))
        runs = [make_workflow_run_response(
            status="completed",
            conclusion="success",
            created_at="2024-06-15T10:00:00Z",  # before merge
            updated_at="2024-06-15T10:10:00Z",
        )]
        # No relevant runs → deploying
        assert compute_acc_deploy(pr, runs, 20) == DeployStatus.ACC_DEPLOYING

    def test_invalid_created_at_skipped(self):
        pr = make_pr(merged_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc))
        runs = [{"status": "completed", "conclusion": "success", "created_at": "bad-date", "updated_at": "bad"}]
        assert compute_acc_deploy(pr, runs, 20) == DeployStatus.ACC_DEPLOYING

    def test_success_with_invalid_updated_at_returns_deploying(self):
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(merged_at=now - timedelta(hours=1))
        runs = [{
            "status": "completed",
            "conclusion": "success",
            "created_at": (now - timedelta(minutes=30)).isoformat(),
            "updated_at": "bad-date",
        }]
        assert compute_acc_deploy(pr, runs, 20) == DeployStatus.ACC_DEPLOYING

    def test_zero_cooldown_deployed_immediately(self):
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(merged_at=now - timedelta(hours=1))
        runs = [make_workflow_run_response(
            status="completed",
            conclusion="success",
            created_at=(now - timedelta(minutes=30)).isoformat(),
            updated_at=(now - timedelta(minutes=5)).isoformat(),
        )]
        assert compute_acc_deploy(pr, runs, 0) == DeployStatus.ACC_DEPLOYED


class TestFilterExpiredMergedPrs:
    def test_keeps_deploying_prs(self):
        old = datetime(2020, 1, 1, tzinfo=timezone.utc)
        pr = make_pr(number=1, merged_at=old, acc_deploy=DeployStatus.ACC_DEPLOYING)
        result = filter_expired_merged_prs([pr], retention_days=2)
        assert len(result) == 1

    def test_keeps_deployed_within_retention(self):
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(number=1, merged_at=now - timedelta(hours=12), acc_deploy=DeployStatus.ACC_DEPLOYED)
        result = filter_expired_merged_prs([pr], retention_days=2)
        assert len(result) == 1

    def test_removes_deployed_past_retention(self):
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(number=1, merged_at=now - timedelta(days=5), acc_deploy=DeployStatus.ACC_DEPLOYED)
        result = filter_expired_merged_prs([pr], retention_days=2)
        assert len(result) == 0

    def test_keeps_none_deploy_status(self):
        pr = make_pr(number=1, merged_at=None, acc_deploy=DeployStatus.NONE)
        result = filter_expired_merged_prs([pr], retention_days=2)
        assert len(result) == 1

    def test_mixed(self):
        now = datetime.now(tz=timezone.utc)
        prs = [
            make_pr(number=1, merged_at=now - timedelta(days=5), acc_deploy=DeployStatus.ACC_DEPLOYED),
            make_pr(number=2, merged_at=now - timedelta(hours=6), acc_deploy=DeployStatus.ACC_DEPLOYING),
            make_pr(number=3, merged_at=now - timedelta(hours=12), acc_deploy=DeployStatus.ACC_DEPLOYED),
        ]
        result = filter_expired_merged_prs(prs, retention_days=2)
        assert [p.number for p in result] == [2, 3]
