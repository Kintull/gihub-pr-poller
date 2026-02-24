"""Tests for the state module."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from github_tracker.models import CIStatus, DeployStatus, PRLabel, PullRequest
from github_tracker.state import (
    CURRENT_VERSION,
    _dict_to_merged_pr,
    _dict_to_pr,
    _merged_pr_to_dict,
    _pr_to_dict,
    _validate_state,
    load_state,
    save_state,
)
from tests.conftest import make_pr


def _make_state(prs: list[dict] | None = None, version: int = CURRENT_VERSION) -> dict:
    return {
        "version": version,
        "cached_at": "2024-06-15T12:00:00+00:00",
        "pull_requests": prs if prs is not None else [],
    }


def _make_pr_dict(**overrides) -> dict:
    defaults = {
        "number": 1,
        "title": "Test PR",
        "author": "alice",
        "url": "https://github.com/owner/repo/pull/1",
        "branch_name": "feature-branch",
        "jira_ticket": None,
        "jira_url": None,
        "repo": "owner/repo",
        "updated_at": "2024-06-15T12:00:00+00:00",
        "labels": [],
    }
    defaults.update(overrides)
    return defaults


class TestLoadState:
    def test_no_file(self, tmp_path):
        open_prs, merged_prs = load_state(tmp_path / "missing.json")
        assert open_prs == []
        assert merged_prs == []

    def test_valid_file(self, tmp_path):
        path = tmp_path / "state.json"
        state = _make_state([_make_pr_dict()])
        path.write_text(json.dumps(state))
        open_prs, merged_prs = load_state(path)
        assert len(open_prs) == 1
        assert open_prs[0].number == 1
        assert merged_prs == []

    def test_invalid_json(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("{broken json")
        open_prs, merged_prs = load_state(path)
        assert open_prs == []
        assert merged_prs == []

    def test_empty_file(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("")
        open_prs, merged_prs = load_state(path)
        assert open_prs == []
        assert merged_prs == []

    def test_unreadable_file(self, tmp_path):
        path = tmp_path / "state.json"
        path.mkdir()  # directory, not file — will fail to read
        open_prs, merged_prs = load_state(path)
        assert open_prs == []
        assert merged_prs == []


class TestSaveState:
    def test_saves_json(self, tmp_path):
        path = tmp_path / "state.json"
        prs = [make_pr(number=42, title="My PR", author="bob")]
        save_state(prs, path=path)
        data = json.loads(path.read_text())
        assert data["version"] == CURRENT_VERSION
        assert "cached_at" in data
        assert len(data["pull_requests"]) == 1
        assert data["pull_requests"][0]["number"] == 42
        assert data["merged_prs"] == []

    def test_saves_empty_list(self, tmp_path):
        path = tmp_path / "state.json"
        save_state([], path=path)
        data = json.loads(path.read_text())
        assert data["pull_requests"] == []
        assert data["merged_prs"] == []

    def test_saves_merged_prs(self, tmp_path):
        path = tmp_path / "state.json"
        merged_at = datetime(2024, 6, 15, 14, 0, 0, tzinfo=timezone.utc)
        merged = [make_pr(number=5, merged_at=merged_at, acc_deploy=DeployStatus.ACC_DEPLOYED)]
        save_state([], merged, path=path)
        data = json.loads(path.read_text())
        assert len(data["merged_prs"]) == 1
        assert data["merged_prs"][0]["number"] == 5
        assert data["merged_prs"][0]["acc_deploy"] == "acc_deployed"

    def test_write_error(self, tmp_path):
        path = tmp_path / "nonexistent_dir" / "state.json"
        # Should not raise, just log error
        save_state([make_pr()], path=path)
        assert not path.exists()


class TestValidateState:
    def test_not_a_dict(self):
        assert _validate_state([]) == ([], [])

    def test_wrong_version(self):
        assert _validate_state(_make_state(version=99)) == ([], [])

    def test_missing_version(self):
        assert _validate_state({"pull_requests": []}) == ([], [])

    def test_pr_list_not_a_list(self):
        assert _validate_state({"version": CURRENT_VERSION, "pull_requests": "bad"}) == ([], [])

    def test_skips_invalid_entries(self):
        state = _make_state([_make_pr_dict(), "not a dict", _make_pr_dict(number=2)])
        open_prs, _ = _validate_state(state)
        assert len(open_prs) == 2

    def test_valid_entries(self):
        state = _make_state([_make_pr_dict(number=1), _make_pr_dict(number=2)])
        open_prs, _ = _validate_state(state)
        assert len(open_prs) == 2

    def test_v1_accepted(self):
        state = _make_state([_make_pr_dict()], version=1)
        open_prs, merged_prs = _validate_state(state)
        assert len(open_prs) == 1
        assert open_prs[0].labels == frozenset()
        assert merged_prs == []

    def test_v1_without_labels_field(self):
        pr_dict = _make_pr_dict()
        del pr_dict["labels"]
        state = _make_state([pr_dict], version=1)
        open_prs, merged_prs = _validate_state(state)
        assert len(open_prs) == 1
        assert open_prs[0].labels == frozenset()
        assert merged_prs == []

    def test_v2_backward_compat(self):
        state = _make_state([_make_pr_dict()], version=2)
        open_prs, merged_prs = _validate_state(state)
        assert len(open_prs) == 1
        assert merged_prs == []

    def test_skips_invalid_merged_entries(self):
        state = _make_state([_make_pr_dict()])
        state["merged_prs"] = [_make_pr_dict(number=10, merged_at="2024-06-15T14:00:00+00:00", acc_deploy="acc_deploying"), "not a dict"]
        open_prs, merged_prs = _validate_state(state)
        assert len(open_prs) == 1
        assert len(merged_prs) == 1
        assert merged_prs[0].number == 10


class TestPrToDict:
    def test_serializes_basic_fields(self):
        pr = make_pr(
            number=42,
            title="My PR",
            author="alice",
            url="https://github.com/o/r/pull/42",
            branch_name="feat-x",
            jira_ticket="PROJ-1",
            jira_url="https://jira.example.com/browse/PROJ-1",
            repo="o/r",
        )
        d = _pr_to_dict(pr)
        assert d["number"] == 42
        assert d["title"] == "My PR"
        assert d["author"] == "alice"
        assert d["url"] == "https://github.com/o/r/pull/42"
        assert d["branch_name"] == "feat-x"
        assert d["jira_ticket"] == "PROJ-1"
        assert d["jira_url"] == "https://jira.example.com/browse/PROJ-1"
        assert d["repo"] == "o/r"
        assert "updated_at" in d

    def test_excludes_volatile_fields(self):
        pr = make_pr(approval_count=5, ci_status=CIStatus.SUCCESS, comment_count=10)
        d = _pr_to_dict(pr)
        assert "approval_count" not in d
        assert "ci_status" not in d
        assert "comment_count" not in d

    def test_none_jira(self):
        pr = make_pr(jira_ticket=None, jira_url=None)
        d = _pr_to_dict(pr)
        assert d["jira_ticket"] is None
        assert d["jira_url"] is None

    def test_serializes_labels(self):
        pr = make_pr(labels=frozenset({PRLabel.AUTHOR, PRLabel.COMMENTED}))
        d = _pr_to_dict(pr)
        assert set(d["labels"]) == {"author", "commented"}

    def test_serializes_empty_labels(self):
        pr = make_pr()
        d = _pr_to_dict(pr)
        assert d["labels"] == []


class TestDictToPr:
    def test_valid_dict(self):
        d = _make_pr_dict(number=42, title="Hello", author="bob")
        pr = _dict_to_pr(d)
        assert pr is not None
        assert pr.number == 42
        assert pr.title == "Hello"
        assert pr.author == "bob"
        assert pr.ci_status == CIStatus.PENDING
        assert pr.approval_count == 0
        assert pr.comment_count == 0

    def test_not_a_dict(self):
        assert _dict_to_pr("string") is None

    def test_missing_required_key(self):
        d = _make_pr_dict()
        del d["title"]
        assert _dict_to_pr(d) is None

    def test_number_not_int(self):
        assert _dict_to_pr(_make_pr_dict(number="foo")) is None

    def test_number_zero(self):
        assert _dict_to_pr(_make_pr_dict(number=0)) is None

    def test_number_negative(self):
        assert _dict_to_pr(_make_pr_dict(number=-1)) is None

    def test_title_not_string(self):
        assert _dict_to_pr(_make_pr_dict(title=123)) is None

    def test_author_not_string(self):
        assert _dict_to_pr(_make_pr_dict(author=123)) is None

    def test_url_not_string(self):
        assert _dict_to_pr(_make_pr_dict(url=123)) is None

    def test_repo_not_string(self):
        assert _dict_to_pr(_make_pr_dict(repo=123)) is None

    def test_invalid_date_uses_fallback(self):
        pr = _dict_to_pr(_make_pr_dict(updated_at="bad-date"))
        assert pr is not None
        assert pr.updated_at is not None

    def test_missing_date_uses_fallback(self):
        d = _make_pr_dict()
        del d["updated_at"]
        pr = _dict_to_pr(d)
        assert pr is not None
        assert pr.updated_at is not None

    def test_missing_optional_fields_use_defaults(self):
        d = {
            "number": 1,
            "title": "PR",
            "author": "a",
            "url": "https://github.com/o/r/pull/1",
            "repo": "o/r",
        }
        pr = _dict_to_pr(d)
        assert pr is not None
        assert pr.branch_name == ""
        assert pr.jira_ticket is None
        assert pr.jira_url is None

    def test_jira_fields_preserved(self):
        d = _make_pr_dict(jira_ticket="PROJ-99", jira_url="https://jira.example.com/browse/PROJ-99")
        pr = _dict_to_pr(d)
        assert pr.jira_ticket == "PROJ-99"
        assert pr.jira_url == "https://jira.example.com/browse/PROJ-99"

    def test_labels_deserialized(self):
        d = _make_pr_dict(labels=["author", "commented"])
        pr = _dict_to_pr(d)
        assert pr.labels == frozenset({PRLabel.AUTHOR, PRLabel.COMMENTED})

    def test_empty_labels(self):
        d = _make_pr_dict(labels=[])
        pr = _dict_to_pr(d)
        assert pr.labels == frozenset()

    def test_missing_labels_field(self):
        d = _make_pr_dict()
        del d["labels"]
        pr = _dict_to_pr(d)
        assert pr.labels == frozenset()

    def test_unknown_labels_skipped(self):
        d = _make_pr_dict(labels=["author", "unknown_future_label"])
        pr = _dict_to_pr(d)
        assert pr.labels == frozenset({PRLabel.AUTHOR})


class TestMergedPrToDict:
    def test_includes_merged_at_and_acc_deploy(self):
        merged_at = datetime(2024, 6, 15, 14, 0, 0, tzinfo=timezone.utc)
        pr = make_pr(
            number=42,
            merged_at=merged_at,
            acc_deploy=DeployStatus.ACC_DEPLOYING,
        )
        d = _merged_pr_to_dict(pr)
        assert d["number"] == 42
        assert d["merged_at"] == merged_at.isoformat()
        assert d["acc_deploy"] == "acc_deploying"

    def test_none_merged_at(self):
        pr = make_pr(merged_at=None)
        d = _merged_pr_to_dict(pr)
        assert d["merged_at"] is None

    def test_includes_base_fields(self):
        pr = make_pr(number=1, title="Test", author="alice")
        d = _merged_pr_to_dict(pr)
        assert d["title"] == "Test"
        assert d["author"] == "alice"


class TestDictToMergedPr:
    def test_valid_merged_pr(self):
        d = _make_pr_dict(
            merged_at="2024-06-15T14:00:00+00:00",
            acc_deploy="acc_deployed",
        )
        pr = _dict_to_merged_pr(d)
        assert pr is not None
        assert pr.merged_at == datetime(2024, 6, 15, 14, 0, 0, tzinfo=timezone.utc)
        assert pr.acc_deploy == DeployStatus.ACC_DEPLOYED

    def test_missing_merged_at(self):
        d = _make_pr_dict()
        pr = _dict_to_merged_pr(d)
        assert pr is not None
        assert pr.merged_at is None

    def test_invalid_merged_at(self):
        d = _make_pr_dict(merged_at="not-a-date")
        pr = _dict_to_merged_pr(d)
        assert pr is not None
        assert pr.merged_at is None

    def test_missing_acc_deploy(self):
        d = _make_pr_dict()
        pr = _dict_to_merged_pr(d)
        assert pr is not None
        assert pr.acc_deploy == DeployStatus.NONE

    def test_unknown_acc_deploy_value(self):
        d = _make_pr_dict(acc_deploy="future_status")
        pr = _dict_to_merged_pr(d)
        assert pr is not None
        assert pr.acc_deploy == DeployStatus.NONE

    def test_invalid_base_dict(self):
        assert _dict_to_merged_pr("not a dict") is None

    def test_missing_required_keys(self):
        d = {"number": 1}  # Missing other required keys
        assert _dict_to_merged_pr(d) is None


class TestRoundTrip:
    def test_save_then_load(self, tmp_path):
        path = tmp_path / "state.json"
        prs = [
            make_pr(number=1, title="First", author="alice", jira_ticket="PROJ-1"),
            make_pr(number=2, title="Second", author="bob"),
        ]
        save_state(prs, path=path)
        open_prs, merged_prs = load_state(path)
        assert len(open_prs) == 2
        assert open_prs[0].number == 1
        assert open_prs[0].title == "First"
        assert open_prs[0].jira_ticket == "PROJ-1"
        assert open_prs[1].number == 2
        assert open_prs[1].author == "bob"
        # Volatile fields reset to defaults
        assert open_prs[0].ci_status == CIStatus.PENDING
        assert open_prs[0].approval_count == 0
        assert open_prs[0].comment_count == 0
        assert merged_prs == []

    def test_labels_round_trip(self, tmp_path):
        path = tmp_path / "state.json"
        prs = [
            make_pr(number=1, labels=frozenset({PRLabel.AUTHOR, PRLabel.MENTIONED})),
            make_pr(number=2, labels=frozenset()),
        ]
        save_state(prs, path=path)
        open_prs, _ = load_state(path)
        assert open_prs[0].labels == frozenset({PRLabel.AUTHOR, PRLabel.MENTIONED})
        assert open_prs[1].labels == frozenset()

    def test_merged_prs_round_trip(self, tmp_path):
        path = tmp_path / "state.json"
        merged_at = datetime(2024, 6, 15, 14, 0, 0, tzinfo=timezone.utc)
        open_prs = [make_pr(number=1)]
        merged_prs = [
            make_pr(
                number=2,
                merged_at=merged_at,
                acc_deploy=DeployStatus.ACC_DEPLOYING,
            ),
        ]
        save_state(open_prs, merged_prs, path=path)
        loaded_open, loaded_merged = load_state(path)
        assert len(loaded_open) == 1
        assert len(loaded_merged) == 1
        assert loaded_merged[0].number == 2
        assert loaded_merged[0].merged_at == merged_at
        assert loaded_merged[0].acc_deploy == DeployStatus.ACC_DEPLOYING
