"""Tests for favourite PR notification logic and persistence."""

from __future__ import annotations

from datetime import datetime, timezone

from github_tracker.models import CIStatus, DeployStatus, PRLabel
from github_tracker.notifications import (
    compute_fav_events,
    load_notified_events,
    save_notified_events,
)

from .conftest import make_pr


MERGED_AT = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def _fav(**overrides):
    overrides.setdefault("labels", frozenset({PRLabel.FAVOURITE}))
    return make_pr(**overrides)


class TestComputeFavEvents:
    def test_non_favourite_ignored(self):
        pr = make_pr(ci_status=CIStatus.SUCCESS, labels=frozenset())
        assert compute_fav_events([pr], set()) == []

    def test_ci_ready_no_auto_merge(self):
        pr = _fav(number=42, title="Add login", ci_status=CIStatus.SUCCESS,
                  auto_merge_enabled=False)
        events = compute_fav_events([pr], set())
        assert len(events) == 1
        e = events[0]
        assert e.event_id == "owner/repo#42:ci_ready"
        assert "ready to merge" in e.title
        assert "CI passed" in e.message
        assert e.url == pr.url

    def test_ci_ready_with_auto_merge(self):
        pr = _fav(number=42, ci_status=CIStatus.SUCCESS, auto_merge_enabled=True)
        events = compute_fav_events([pr], set())
        assert len(events) == 1
        assert "Auto-merge" in events[0].message

    def test_ci_ready_skipped_if_pending(self):
        pr = _fav(ci_status=CIStatus.PENDING)
        assert compute_fav_events([pr], set()) == []

    def test_merged_fires_when_deploying(self):
        pr = _fav(number=7, merged_at=MERGED_AT,
                  acc_deploy=DeployStatus.ACC_DEPLOYING)
        events = compute_fav_events([pr], set())
        ids = [e.event_id for e in events]
        assert "owner/repo#7:merged" in ids

    def test_merged_skipped_for_feature_branch(self):
        pr = _fav(merged_at=MERGED_AT, acc_deploy=DeployStatus.NONE)
        events = compute_fav_events([pr], set())
        assert all("merged" not in e.event_id for e in events)

    def test_acc_deployed_fires(self):
        pr = _fav(number=9, merged_at=MERGED_AT, acc_deploy=DeployStatus.ACC_DEPLOYED)
        events = compute_fav_events([pr], set())
        ids = [e.event_id for e in events]
        assert "owner/repo#9:acc_deployed" in ids
        assert "owner/repo#9:merged" in ids

    def test_dedup_via_already_notified(self):
        pr = _fav(number=42, ci_status=CIStatus.SUCCESS)
        already = {"owner/repo#42:ci_ready"}
        assert compute_fav_events([pr], already) == []

    def test_multiple_prs(self):
        prs = [
            _fav(number=1, ci_status=CIStatus.SUCCESS),
            _fav(number=2, ci_status=CIStatus.FAILURE),
            _fav(number=3, merged_at=MERGED_AT, acc_deploy=DeployStatus.ACC_DEPLOYED),
        ]
        events = compute_fav_events(prs, set())
        ids = sorted(e.event_id for e in events)
        assert ids == [
            "owner/repo#1:ci_ready",
            "owner/repo#3:acc_deployed",
            "owner/repo#3:merged",
        ]


class TestNotifiedEventsPersistence:
    def test_load_missing_file_returns_empty(self, tmp_path):
        assert load_notified_events(tmp_path / "missing.json") == set()

    def test_save_then_load_roundtrip(self, tmp_path):
        path = tmp_path / "notified.json"
        events = {"owner/repo#1:ci_ready", "owner/repo#2:merged"}
        save_notified_events(events, path)
        assert load_notified_events(path) == events

    def test_load_invalid_json_returns_empty(self, tmp_path):
        path = tmp_path / "notified.json"
        path.write_text("not json", encoding="utf-8")
        assert load_notified_events(path) == set()

    def test_load_non_list_returns_empty(self, tmp_path):
        path = tmp_path / "notified.json"
        path.write_text('{"foo": "bar"}', encoding="utf-8")
        assert load_notified_events(path) == set()

    def test_load_filters_non_strings(self, tmp_path):
        path = tmp_path / "notified.json"
        path.write_text('["good", 42, null, "also-good"]', encoding="utf-8")
        assert load_notified_events(path) == {"good", "also-good"}
