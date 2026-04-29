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
    find_tree_members,
    group_prs,
    order_with_nesting,
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

    def test_non_author_drafts_pushed_to_bottom_of_others(self):
        prs = [
            make_pr(number=1, labels=frozenset({PRLabel.DRAFT})),
            make_pr(number=2, labels=frozenset({PRLabel.MENTIONED})),
            make_pr(number=3),
            make_pr(number=4, labels=frozenset({PRLabel.REVIEW_REQUESTED, PRLabel.DRAFT})),
        ]
        _, other = group_prs(prs)
        # Non-drafts come first; drafts last (regardless of their interest labels)
        assert [p.number for p in other[:2]] == [2, 3]
        assert sorted(p.number for p in other[2:]) == [1, 4]

    def test_author_drafts_not_pushed_to_bottom(self):
        """AUTHOR + DRAFT PRs are still treated as user-related, not drafts-of-others."""
        prs = [
            make_pr(number=1, labels=frozenset({PRLabel.AUTHOR, PRLabel.DRAFT})),
            make_pr(number=2),
        ]
        _, other = group_prs(prs)
        assert [p.number for p in other] == [1, 2]

    def test_non_author_drafts_pushed_to_bottom_of_my_prs(self):
        """Non-author drafts in My PRs (e.g. REVIEW_REQUESTED) sort to bottom."""
        prs = [
            make_pr(number=1, labels=frozenset({PRLabel.FAVOURITE, PRLabel.REVIEW_REQUESTED, PRLabel.DRAFT})),
            make_pr(number=2, labels=frozenset({PRLabel.FAVOURITE})),
        ]
        my, _ = group_prs(prs)
        assert [p.number for p in my] == [2, 1]


class TestFindTreeMembers:
    def test_single_pr_no_tree(self):
        """PR with no tree relationships returns just itself."""
        pr = make_pr(number=1, branch_name="feat", base_branch="main")
        result = find_tree_members(pr, [pr])
        assert [p.number for p in result] == [1]

    def test_root_returns_entire_tree(self):
        """Selecting root returns root + all children."""
        root = make_pr(number=1, branch_name="feat", base_branch="main")
        child1 = make_pr(number=2, branch_name="feat-a", base_branch="feat")
        child2 = make_pr(number=3, branch_name="feat-b", base_branch="feat")
        all_prs = [root, child1, child2]
        result = find_tree_members(root, all_prs)
        assert sorted(p.number for p in result) == [1, 2, 3]

    def test_child_returns_entire_tree(self):
        """Selecting a child returns the same full tree."""
        root = make_pr(number=1, branch_name="feat", base_branch="main")
        child = make_pr(number=2, branch_name="feat-a", base_branch="feat")
        all_prs = [root, child]
        result = find_tree_members(child, all_prs)
        assert sorted(p.number for p in result) == [1, 2]

    def test_deep_chain(self):
        """C -> B -> A -> main: selecting any returns all three."""
        a = make_pr(number=1, branch_name="feat-a", base_branch="main")
        b = make_pr(number=2, branch_name="feat-b", base_branch="feat-a")
        c = make_pr(number=3, branch_name="feat-c", base_branch="feat-b")
        all_prs = [a, b, c]
        for target in [a, b, c]:
            result = find_tree_members(target, all_prs)
            assert sorted(p.number for p in result) == [1, 2, 3]

    def test_cross_repo_separate_trees(self):
        """Same branch names in different repos are separate trees."""
        r1_root = make_pr(number=1, branch_name="feat", base_branch="main", repo="org/repo1")
        r1_child = make_pr(number=2, branch_name="feat-a", base_branch="feat", repo="org/repo1")
        r2_root = make_pr(number=3, branch_name="feat", base_branch="main", repo="org/repo2")
        all_prs = [r1_root, r1_child, r2_root]
        result = find_tree_members(r1_child, all_prs)
        assert sorted(p.number for p in result) == [1, 2]

    def test_deploy_branch_not_parent(self):
        """PRs targeting deploy branches don't form parent-child."""
        pr1 = make_pr(number=1, branch_name="feat-a", base_branch="main")
        pr2 = make_pr(number=2, branch_name="feat-b", base_branch="main")
        result = find_tree_members(pr1, [pr1, pr2])
        assert [p.number for p in result] == [1]

    def test_orphan_child_returns_just_itself(self):
        """Sub-PR whose parent is not in the list returns just itself."""
        orphan = make_pr(number=2, branch_name="feat-a", base_branch="feat-missing")
        result = find_tree_members(orphan, [orphan])
        assert [p.number for p in result] == [2]

    def test_mixed_favourite_states(self):
        """Tree members with different FAVOURITE states are all returned."""
        root = make_pr(number=1, branch_name="feat", base_branch="main",
                       labels=frozenset({PRLabel.FAVOURITE}))
        child = make_pr(number=2, branch_name="feat-a", base_branch="feat",
                        labels=frozenset())
        result = find_tree_members(root, [root, child])
        assert sorted(p.number for p in result) == [1, 2]


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

    def test_removes_feature_branch_merge(self):
        """PRs with both deploy statuses NONE (feature-branch merges) are removed."""
        pr = make_pr(number=1, merged_at=None, acc_deploy=DeployStatus.NONE)
        result = filter_expired_merged_prs([pr], retention_days=2)
        assert len(result) == 0

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


class TestOrderWithNesting:
    def test_no_sub_prs_all_target_main(self):
        prs = [
            make_pr(number=1, branch_name="feat-a", base_branch="main"),
            make_pr(number=2, branch_name="feat-b", base_branch="main"),
        ]
        ordered, display = order_with_nesting(prs)
        assert [p.number for p in ordered] == [1, 2]
        assert not display[1].is_sub_pr
        assert not display[2].is_sub_pr

    def test_one_sub_pr(self):
        prs = [
            make_pr(number=10, branch_name="feat-x", base_branch="main"),
            make_pr(number=11, branch_name="feat-x-part1", base_branch="feat-x"),
        ]
        ordered, display = order_with_nesting(prs)
        assert [p.number for p in ordered] == [10, 11]
        assert not display[10].is_sub_pr
        assert display[11].is_sub_pr
        assert display[11].is_last_sub_pr

    def test_multiple_sub_prs(self):
        prs = [
            make_pr(number=1, branch_name="feat-a", base_branch="main"),
            make_pr(number=2, branch_name="feat-a-p1", base_branch="feat-a"),
            make_pr(number=3, branch_name="feat-a-p2", base_branch="feat-a"),
        ]
        ordered, display = order_with_nesting(prs)
        assert [p.number for p in ordered] == [1, 2, 3]
        assert not display[1].is_sub_pr
        assert display[2].is_sub_pr
        assert not display[2].is_last_sub_pr
        assert display[3].is_sub_pr
        assert display[3].is_last_sub_pr

    def test_chain_flattened(self):
        """C -> B -> A -> main flattens all under A."""
        prs = [
            make_pr(number=1, branch_name="feat-a", base_branch="main"),
            make_pr(number=2, branch_name="feat-b", base_branch="feat-a"),
            make_pr(number=3, branch_name="feat-c", base_branch="feat-b"),
        ]
        ordered, display = order_with_nesting(prs)
        assert [p.number for p in ordered] == [1, 2, 3]
        assert not display[1].is_sub_pr
        assert display[2].is_sub_pr
        assert display[3].is_sub_pr
        assert display[3].is_last_sub_pr

    def test_orphan_sub_pr_treated_as_regular(self):
        """Sub-PR whose parent is not in the list stays as regular PR."""
        prs = [
            make_pr(number=1, branch_name="feat-a", base_branch="main"),
            make_pr(number=2, branch_name="feat-x-part", base_branch="feat-x"),
        ]
        ordered, display = order_with_nesting(prs)
        assert [p.number for p in ordered] == [1, 2]
        assert not display[1].is_sub_pr
        assert not display[2].is_sub_pr

    def test_cross_repo_not_grouped(self):
        """Same branch name in different repos should not group."""
        prs = [
            make_pr(number=1, branch_name="feat-a", base_branch="main", repo="org/repo-a"),
            make_pr(number=2, branch_name="feat-x", base_branch="feat-a", repo="org/repo-b"),
        ]
        ordered, display = order_with_nesting(prs)
        assert [p.number for p in ordered] == [1, 2]
        assert not display[1].is_sub_pr
        assert not display[2].is_sub_pr

    def test_deploy_branch_not_treated_as_parent(self):
        """PR targeting 'master' should not be grouped under a PR whose branch is 'master'."""
        prs = [
            make_pr(number=1, branch_name="feat-a", base_branch="master"),
            make_pr(number=2, branch_name="feat-b", base_branch="master"),
        ]
        ordered, display = order_with_nesting(prs)
        assert not display[1].is_sub_pr
        assert not display[2].is_sub_pr

    def test_empty_list(self):
        ordered, display = order_with_nesting([])
        assert ordered == []
        assert display == {}

    def test_mixed_grouped_and_ungrouped(self):
        prs = [
            make_pr(number=1, branch_name="feat-a", base_branch="main"),
            make_pr(number=2, branch_name="fix-bug", base_branch="main"),
            make_pr(number=3, branch_name="feat-a-p1", base_branch="feat-a"),
        ]
        ordered, display = order_with_nesting(prs)
        assert [p.number for p in ordered] == [1, 3, 2]
        assert not display[1].is_sub_pr
        assert display[3].is_sub_pr
        assert display[3].is_last_sub_pr
        assert not display[2].is_sub_pr
