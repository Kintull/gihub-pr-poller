"""Tests for config module."""

from pathlib import Path

import pytest
import yaml

from github_tracker.config import Config, ConfigError, create_default_config, load_config


class TestConfig:
    def test_defaults(self):
        c = Config()
        assert c.jira_base_url == ""
        assert c.github_repos == []
        assert c.refresh_interval == 300
        assert c.github_username == ""
        assert c.acc_deploy_environment == "acceptance"
        assert c.prd_deploy_environment == "production"
        assert c.acc_retention_days == 2
        assert c.argo_cooldown_minutes == 20

    def test_jira_enabled_false(self):
        assert Config().jira_enabled() is False

    def test_jira_enabled_true(self):
        c = Config(jira_base_url="https://jira.example.com/browse")
        assert c.jira_enabled() is True


class TestLoadConfig:
    def test_missing_file_creates_default(self, tmp_path):
        path = tmp_path / "config.yaml"
        config = load_config(path)
        assert config.jira_base_url == ""
        assert config.github_repos == []
        assert config.refresh_interval == 300
        assert path.exists()

    def test_empty_file(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text("")
        config = load_config(path)
        assert config == Config()

    def test_whitespace_only_file(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text("   \n  \n  ")
        config = load_config(path)
        assert config == Config()

    def test_valid_config(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(
            yaml.dump(
                {
                    "jira_base_url": "https://jira.example.com/browse/",
                    "github_repos": ["owner/repo"],
                    "refresh_interval": 60,
                }
            )
        )
        config = load_config(path)
        assert config.jira_base_url == "https://jira.example.com/browse"
        assert config.github_repos == ["owner/repo"]
        assert config.refresh_interval == 60

    def test_strips_trailing_slash_from_jira_url(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"jira_base_url": "https://jira.example.com/browse///"}))
        config = load_config(path)
        assert config.jira_base_url == "https://jira.example.com/browse"

    def test_invalid_yaml_type(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text("- item1\n- item2\n")
        with pytest.raises(ConfigError, match="YAML mapping"):
            load_config(path)

    def test_invalid_jira_base_url_type(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"jira_base_url": 123}))
        with pytest.raises(ConfigError, match="jira_base_url must be a string"):
            load_config(path)

    def test_invalid_github_repos_type(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"github_repos": "not-a-list"}))
        with pytest.raises(ConfigError, match="github_repos must be a list"):
            load_config(path)

    def test_invalid_repo_format_no_slash(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"github_repos": ["noslash"]}))
        with pytest.raises(ConfigError, match="Invalid repo format"):
            load_config(path)

    def test_invalid_repo_format_not_string(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"github_repos": [123]}))
        with pytest.raises(ConfigError, match="Invalid repo format"):
            load_config(path)

    def test_invalid_refresh_interval_type(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"refresh_interval": "fast"}))
        with pytest.raises(ConfigError, match="refresh_interval must be a positive integer"):
            load_config(path)

    def test_invalid_refresh_interval_zero(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"refresh_interval": 0}))
        with pytest.raises(ConfigError, match="refresh_interval must be a positive integer"):
            load_config(path)

    def test_invalid_refresh_interval_negative(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"refresh_interval": -10}))
        with pytest.raises(ConfigError, match="refresh_interval must be a positive integer"):
            load_config(path)

    def test_partial_config_uses_defaults(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"jira_base_url": "https://jira.test.com"}))
        config = load_config(path)
        assert config.jira_base_url == "https://jira.test.com"
        assert config.github_repos == []
        assert config.refresh_interval == 300
        assert config.github_username == ""

    def test_github_username(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"github_username": "alice"}))
        config = load_config(path)
        assert config.github_username == "alice"

    def test_invalid_github_username_type(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"github_username": 123}))
        with pytest.raises(ConfigError, match="github_username must be a string"):
            load_config(path)

    def test_acc_deploy_environment(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"acc_deploy_environment": "staging"}))
        config = load_config(path)
        assert config.acc_deploy_environment == "staging"

    def test_invalid_acc_deploy_environment_type(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"acc_deploy_environment": 123}))
        with pytest.raises(ConfigError, match="acc_deploy_environment must be a string"):
            load_config(path)

    def test_prd_deploy_environment(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"prd_deploy_environment": "prod"}))
        config = load_config(path)
        assert config.prd_deploy_environment == "prod"

    def test_invalid_prd_deploy_environment_type(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"prd_deploy_environment": 123}))
        with pytest.raises(ConfigError, match="prd_deploy_environment must be a string"):
            load_config(path)

    def test_acc_retention_days(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"acc_retention_days": 5}))
        config = load_config(path)
        assert config.acc_retention_days == 5

    def test_acc_retention_days_zero(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"acc_retention_days": 0}))
        config = load_config(path)
        assert config.acc_retention_days == 0

    def test_invalid_acc_retention_days_negative(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"acc_retention_days": -1}))
        with pytest.raises(ConfigError, match="acc_retention_days must be a non-negative integer"):
            load_config(path)

    def test_invalid_acc_retention_days_type(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"acc_retention_days": "two"}))
        with pytest.raises(ConfigError, match="acc_retention_days must be a non-negative integer"):
            load_config(path)

    def test_argo_cooldown_minutes(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"argo_cooldown_minutes": 30}))
        config = load_config(path)
        assert config.argo_cooldown_minutes == 30

    def test_argo_cooldown_minutes_zero(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"argo_cooldown_minutes": 0}))
        config = load_config(path)
        assert config.argo_cooldown_minutes == 0

    def test_invalid_argo_cooldown_minutes_negative(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"argo_cooldown_minutes": -5}))
        with pytest.raises(ConfigError, match="argo_cooldown_minutes must be a non-negative integer"):
            load_config(path)

    def test_invalid_argo_cooldown_minutes_type(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump({"argo_cooldown_minutes": "fast"}))
        with pytest.raises(ConfigError, match="argo_cooldown_minutes must be a non-negative integer"):
            load_config(path)


class TestCreateDefaultConfig:
    def test_creates_file(self, tmp_path):
        path = tmp_path / "subdir" / "config.yaml"
        create_default_config(path)
        assert path.exists()
        data = yaml.safe_load(path.read_text())
        assert data["jira_base_url"] == ""
        assert data["github_repos"] == []
        assert data["refresh_interval"] == 300
        assert data["github_username"] == ""
        assert data["acc_deploy_environment"] == "acceptance"
        assert data["prd_deploy_environment"] == "production"
        assert data["acc_retention_days"] == 2
        assert data["argo_cooldown_minutes"] == 20

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "a" / "b" / "config.yaml"
        create_default_config(path)
        assert path.exists()
