"""Tests for the pr_service module."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from github_tracker.models import DeployStatus, PRLabel, PrdDeployStatus
from github_tracker.pr_service import (
    compute_ci_progress,
    compute_deploy_status,
    compute_phase1_labels,
    compute_phase2_labels,
    compute_prd_deploy_status,
    compute_thread_counts,
    compute_user_approved,
    filter_expired_merged_prs,
    group_prs,
)
from tests.conftest import make_pr, make_review_thread


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

    def test_favourite_goes_to_my_prs(self):
        prs = [
            make_pr(number=1, labels=frozenset({PRLabel.FAVOURITE})),
            make_pr(number=2, labels=frozenset({PRLabel.FAVOURITE, PRLabel.AUTHOR})),
        ]
        my, other = group_prs(prs)
        assert len(my) == 2
        assert len(other) == 0

    def test_author_alone_goes_to_others(self):
        prs = [
            make_pr(number=1, labels=frozenset({PRLabel.AUTHOR})),
            make_pr(number=2, labels=frozenset({PRLabel.REVIEW_REQUESTED})),
        ]
        my, other = group_prs(prs)
        assert len(my) == 0
        assert len(other) == 2

    def test_no_labels_goes_to_others(self):
        prs = [make_pr(number=1), make_pr(number=2)]
        my, other = group_prs(prs)
        assert len(my) == 0
        assert len(other) == 2

    def test_mixed(self):
        prs = [
            make_pr(number=1, labels=frozenset({PRLabel.FAVOURITE})),
            make_pr(number=2),
            make_pr(number=3, labels=frozenset({PRLabel.AUTHOR})),
        ]
        my, other = group_prs(prs)
        assert [p.number for p in my] == [1]
        # related (AUTHOR) sorts before unrelated
        assert [p.number for p in other] == [3, 2]

    def test_preserves_ordering(self):
        prs = [
            make_pr(number=5, labels=frozenset({PRLabel.FAVOURITE})),
            make_pr(number=3),
            make_pr(number=1, labels=frozenset({PRLabel.FAVOURITE, PRLabel.MENTIONED})),
            make_pr(number=4),
        ]
        my, other = group_prs(prs)
        assert [p.number for p in my] == [5, 1]
        assert [p.number for p in other] == [3, 4]

    def test_others_related_sorted_before_unrelated(self):
        """Related PRs (any interest label, no FAVOURITE) sort before unrelated in Others."""
        prs = [
            make_pr(number=1),
            make_pr(number=2, labels=frozenset({PRLabel.COMMENTED})),
            make_pr(number=3),
            make_pr(number=4, labels=frozenset({PRLabel.REVIEW_REQUESTED})),
        ]
        _, other = group_prs(prs)
        related = [p for p in other if p.labels]
        unrelated = [p for p in other if not p.labels]
        assert other == related + unrelated

    def test_others_within_tier_order_preserved(self):
        """Within the related and unrelated tiers, input order is preserved."""
        prs = [
            make_pr(number=10),
            make_pr(number=20, labels=frozenset({PRLabel.AUTHOR})),
            make_pr(number=30),
            make_pr(number=40, labels=frozenset({PRLabel.MENTIONED})),
        ]
        _, other = group_prs(prs)
        assert [p.number for p in other] == [20, 40, 10, 30]


class TestComputeCiProgress:
    def test_empty_returns_zero(self):
        assert compute_ci_progress([]) == (0, 0)

    def test_all_completed(self):
        runs = [
            {"status": "completed", "conclusion": "success"},
            {"status": "completed", "conclusion": "failure"},
        ]
        assert compute_ci_progress(runs) == (2, 2)

    def test_mixed_statuses(self):
        runs = [
            {"status": "completed", "conclusion": "success"},
            {"status": "in_progress", "conclusion": None},
            {"status": "queued", "conclusion": None},
        ]
        assert compute_ci_progress(runs) == (1, 3)

    def test_none_completed(self):
        runs = [
            {"status": "in_progress", "conclusion": None},
            {"status": "queued", "conclusion": None},
        ]
        assert compute_ci_progress(runs) == (0, 2)


class TestComputeDeployStatus:
    def test_no_merged_at_returns_none(self):
        pr = make_pr(merged_at=None, merge_commit_sha="abc123")
        assert compute_deploy_status(pr, "behind", None, 20) == DeployStatus.NONE

    def test_no_merge_commit_sha_returns_deploying(self):
        pr = make_pr(
            merged_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
            merge_commit_sha=None,
        )
        assert compute_deploy_status(pr, "behind", None, 20) == DeployStatus.ACC_DEPLOYING

    def test_ahead_past_cooldown_returns_deployed(self):
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(
            merged_at=now - timedelta(hours=2),
            merge_commit_sha="abc123",
        )
        deploy_created = now - timedelta(hours=1)
        assert compute_deploy_status(pr, "ahead", deploy_created, 20) == DeployStatus.ACC_DEPLOYED

    def test_identical_past_cooldown_returns_deployed(self):
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(
            merged_at=now - timedelta(hours=2),
            merge_commit_sha="abc123",
        )
        deploy_created = now - timedelta(hours=1)
        assert compute_deploy_status(pr, "identical", deploy_created, 20) == DeployStatus.ACC_DEPLOYED

    def test_ahead_within_cooldown_returns_acc_argo(self):
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(
            merged_at=now - timedelta(hours=1),
            merge_commit_sha="abc123",
        )
        deploy_created = now - timedelta(minutes=5)  # within 20min cooldown
        assert compute_deploy_status(pr, "ahead", deploy_created, 20) == DeployStatus.ACC_ARGO

    def test_identical_within_cooldown_returns_acc_argo(self):
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(
            merged_at=now - timedelta(hours=1),
            merge_commit_sha="abc123",
        )
        deploy_created = now - timedelta(minutes=5)
        assert compute_deploy_status(pr, "identical", deploy_created, 20) == DeployStatus.ACC_ARGO

    def test_behind_returns_deploying(self):
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(
            merged_at=now - timedelta(hours=1),
            merge_commit_sha="abc123",
        )
        assert compute_deploy_status(pr, "behind", now - timedelta(hours=1), 20) == DeployStatus.ACC_DEPLOYING

    def test_diverged_returns_deploying(self):
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(
            merged_at=now - timedelta(hours=1),
            merge_commit_sha="abc123",
        )
        assert compute_deploy_status(pr, "diverged", now - timedelta(hours=1), 20) == DeployStatus.ACC_DEPLOYING

    def test_none_compare_status_returns_deploying(self):
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(
            merged_at=now - timedelta(hours=1),
            merge_commit_sha="abc123",
        )
        assert compute_deploy_status(pr, None, None, 20) == DeployStatus.ACC_DEPLOYING

    def test_zero_cooldown_deployed_immediately(self):
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(
            merged_at=now - timedelta(hours=1),
            merge_commit_sha="abc123",
        )
        deploy_created = now - timedelta(seconds=5)
        assert compute_deploy_status(pr, "ahead", deploy_created, 0) == DeployStatus.ACC_DEPLOYED

    def test_ahead_no_deploy_created_at_returns_deployed(self):
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(
            merged_at=now - timedelta(hours=1),
            merge_commit_sha="abc123",
        )
        assert compute_deploy_status(pr, "ahead", None, 20) == DeployStatus.ACC_DEPLOYED


class TestComputePrdDeployStatus:
    def test_no_merged_at_returns_none(self):
        pr = make_pr(merged_at=None, merge_commit_sha="abc123")
        assert compute_prd_deploy_status(pr, "behind", None, 20) == PrdDeployStatus.NONE

    def test_no_merge_commit_sha_returns_deploying(self):
        pr = make_pr(
            merged_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
            merge_commit_sha=None,
        )
        assert compute_prd_deploy_status(pr, "behind", None, 20) == PrdDeployStatus.PRD_DEPLOYING

    def test_ahead_past_cooldown_returns_deployed(self):
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(
            merged_at=now - timedelta(hours=2),
            merge_commit_sha="abc123",
        )
        deploy_created = now - timedelta(hours=1)
        assert compute_prd_deploy_status(pr, "ahead", deploy_created, 20) == PrdDeployStatus.PRD_DEPLOYED

    def test_identical_past_cooldown_returns_deployed(self):
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(
            merged_at=now - timedelta(hours=2),
            merge_commit_sha="abc123",
        )
        deploy_created = now - timedelta(hours=1)
        assert compute_prd_deploy_status(pr, "identical", deploy_created, 20) == PrdDeployStatus.PRD_DEPLOYED

    def test_ahead_within_cooldown_returns_prd_argo(self):
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(
            merged_at=now - timedelta(hours=1),
            merge_commit_sha="abc123",
        )
        deploy_created = now - timedelta(minutes=5)
        assert compute_prd_deploy_status(pr, "ahead", deploy_created, 20) == PrdDeployStatus.PRD_ARGO

    def test_behind_returns_deploying(self):
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(
            merged_at=now - timedelta(hours=1),
            merge_commit_sha="abc123",
        )
        assert compute_prd_deploy_status(pr, "behind", now - timedelta(hours=1), 20) == PrdDeployStatus.PRD_DEPLOYING

    def test_none_compare_status_returns_deploying(self):
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(
            merged_at=now - timedelta(hours=1),
            merge_commit_sha="abc123",
        )
        assert compute_prd_deploy_status(pr, None, None, 20) == PrdDeployStatus.PRD_DEPLOYING

    def test_ahead_no_deploy_created_at_returns_deployed(self):
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(
            merged_at=now - timedelta(hours=1),
            merge_commit_sha="abc123",
        )
        assert compute_prd_deploy_status(pr, "ahead", None, 20) == PrdDeployStatus.PRD_DEPLOYED


class TestComputeUserApproved:
    def test_empty_username_returns_false(self):
        reviews = [{"state": "APPROVED", "user": {"login": "alice"}}]
        assert compute_user_approved(reviews, "") is False

    def test_empty_reviews_returns_false(self):
        assert compute_user_approved([], "alice") is False

    def test_no_matching_user_returns_false(self):
        reviews = [{"state": "APPROVED", "user": {"login": "bob"}}]
        assert compute_user_approved(reviews, "alice") is False

    def test_approved_returns_true(self):
        reviews = [{"state": "APPROVED", "user": {"login": "alice"}}]
        assert compute_user_approved(reviews, "alice") is True

    def test_changes_requested_returns_false(self):
        reviews = [{"state": "CHANGES_REQUESTED", "user": {"login": "alice"}}]
        assert compute_user_approved(reviews, "alice") is False

    def test_latest_review_counts_approved_then_changes(self):
        """If user approved then submitted CHANGES_REQUESTED, result is False."""
        reviews = [
            {"state": "APPROVED", "user": {"login": "alice"}},
            {"state": "CHANGES_REQUESTED", "user": {"login": "alice"}},
        ]
        assert compute_user_approved(reviews, "alice") is False

    def test_latest_review_counts_changes_then_approved(self):
        """If user submitted CHANGES_REQUESTED then approved, result is True."""
        reviews = [
            {"state": "CHANGES_REQUESTED", "user": {"login": "alice"}},
            {"state": "APPROVED", "user": {"login": "alice"}},
        ]
        assert compute_user_approved(reviews, "alice") is True

    def test_case_insensitive(self):
        reviews = [{"state": "APPROVED", "user": {"login": "Alice"}}]
        assert compute_user_approved(reviews, "alice") is True

    def test_none_user_in_review_skipped(self):
        reviews = [
            {"state": "APPROVED", "user": None},
            {"state": "APPROVED", "user": {"login": "alice"}},
        ]
        assert compute_user_approved(reviews, "alice") is True


class TestFilterExpiredMergedPrs:
    def test_keeps_deploying_prs(self):
        old = datetime(2020, 1, 1, tzinfo=timezone.utc)
        pr = make_pr(number=1, merged_at=old, acc_deploy=DeployStatus.ACC_DEPLOYING)
        result = filter_expired_merged_prs([pr], retention_days=2)
        assert len(result) == 1

    def test_keeps_deployed_within_retention(self):
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(number=1, merged_at=now - timedelta(hours=12),
                     acc_deploy=DeployStatus.ACC_DEPLOYED, prd_deploy=PrdDeployStatus.PRD_DEPLOYED)
        result = filter_expired_merged_prs([pr], retention_days=2)
        assert len(result) == 1

    def test_removes_deployed_past_retention(self):
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(number=1, merged_at=now - timedelta(days=5),
                     acc_deploy=DeployStatus.ACC_DEPLOYED, prd_deploy=PrdDeployStatus.PRD_DEPLOYED)
        result = filter_expired_merged_prs([pr], retention_days=2)
        assert len(result) == 0

    def test_keeps_none_deploy_status(self):
        pr = make_pr(number=1, merged_at=None, acc_deploy=DeployStatus.NONE)
        result = filter_expired_merged_prs([pr], retention_days=2)
        assert len(result) == 1

    def test_keeps_acc_deployed_prd_not_deployed(self):
        """Keep PR if ACC is deployed but PRD is still deploying."""
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(number=1, merged_at=now - timedelta(days=5),
                     acc_deploy=DeployStatus.ACC_DEPLOYED, prd_deploy=PrdDeployStatus.PRD_DEPLOYING)
        result = filter_expired_merged_prs([pr], retention_days=2)
        assert len(result) == 1

    def test_keeps_prd_deployed_acc_not_deployed(self):
        """Keep PR if PRD is deployed but ACC is still deploying."""
        now = datetime.now(tz=timezone.utc)
        pr = make_pr(number=1, merged_at=now - timedelta(days=5),
                     acc_deploy=DeployStatus.ACC_DEPLOYING, prd_deploy=PrdDeployStatus.PRD_DEPLOYED)
        result = filter_expired_merged_prs([pr], retention_days=2)
        assert len(result) == 1

    def test_mixed(self):
        now = datetime.now(tz=timezone.utc)
        prs = [
            make_pr(number=1, merged_at=now - timedelta(days=5),
                    acc_deploy=DeployStatus.ACC_DEPLOYED, prd_deploy=PrdDeployStatus.PRD_DEPLOYED),
            make_pr(number=2, merged_at=now - timedelta(hours=6), acc_deploy=DeployStatus.ACC_DEPLOYING),
            make_pr(number=3, merged_at=now - timedelta(hours=12),
                    acc_deploy=DeployStatus.ACC_DEPLOYED, prd_deploy=PrdDeployStatus.PRD_DEPLOYED),
        ]
        result = filter_expired_merged_prs(prs, retention_days=2)
        assert [p.number for p in result] == [2, 3]


class TestComputeThreadCounts:
    def test_empty_threads(self):
        assert compute_thread_counts([], "alice") == (0, 0, 0, 0)

    def test_all_resolved_no_username_match(self):
        threads = [
            make_review_thread(is_resolved=True, authors=["bob"]),
            make_review_thread(is_resolved=True, authors=["carol"]),
        ]
        total, unresolved, my_commented, my_unresolved = compute_thread_counts(threads, "alice")
        assert total == 2
        assert unresolved == 0
        assert my_commented == 0
        assert my_unresolved == 0

    def test_unresolved_counted(self):
        threads = [
            make_review_thread(is_resolved=False, authors=["bob"]),
            make_review_thread(is_resolved=True, authors=["carol"]),
        ]
        _, unresolved, _, _ = compute_thread_counts(threads, "alice")
        assert unresolved == 1

    def test_my_commented_and_unresolved(self):
        threads = [
            make_review_thread(is_resolved=False, authors=["alice", "bob"]),
            make_review_thread(is_resolved=True, authors=["alice"]),
            make_review_thread(is_resolved=False, authors=["bob"]),
        ]
        total, unresolved, my_commented, my_unresolved = compute_thread_counts(threads, "alice")
        assert total == 3
        assert unresolved == 2
        assert my_commented == 2
        assert my_unresolved == 1

    def test_username_case_insensitive(self):
        threads = [make_review_thread(is_resolved=False, authors=["Alice"])]
        _, _, my_commented, my_unresolved = compute_thread_counts(threads, "alice")
        assert my_commented == 1
        assert my_unresolved == 1

    def test_empty_username_no_my_counts(self):
        threads = [make_review_thread(is_resolved=False, authors=["alice"])]
        _, _, my_commented, my_unresolved = compute_thread_counts(threads, "")
        assert my_commented == 0
        assert my_unresolved == 0

    def test_thread_with_no_comments(self):
        threads = [make_review_thread(is_resolved=False, authors=[])]
        total, unresolved, my_commented, my_unresolved = compute_thread_counts(threads, "alice")
        assert total == 1
        assert unresolved == 1
        assert my_commented == 0
        assert my_unresolved == 0
