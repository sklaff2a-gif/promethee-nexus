"""Tests pour core/tech_watch.py — surveillance technologique."""

import json
import os
import tempfile
import time
from unittest.mock import patch, MagicMock

import pytest

# Patch les chemins avant import
_tmpdir = tempfile.mkdtemp()
_config_path = os.path.join(_tmpdir, "tech_watch.json")
_cache_path = os.path.join(_tmpdir, "tech_watch_cache.json")


@pytest.fixture(autouse=True)
def patch_paths(monkeypatch):
    import core.tech_watch as tw
    monkeypatch.setattr(tw, "_CONFIG_FILE", _config_path)
    monkeypatch.setattr(tw, "_CACHE_FILE", _cache_path)
    # Nettoyer le cache entre les tests
    if os.path.exists(_cache_path):
        os.remove(_cache_path)
    yield


@pytest.fixture
def sample_config():
    config = {
        "version": 1,
        "check_interval_minutes": 60,
        "github_watches": [
            {
                "id": "test_pr",
                "label": "Test PR",
                "repo": "owner/repo",
                "issues": [123, 456],
                "keywords": ["test"],
                "priority": "critical",
                "action_on_merge": "Faire quelque chose",
            },
            {
                "id": "test_repo",
                "label": "Test Repo",
                "repo": "owner/other",
                "issues": [],
                "keywords": [],
                "priority": "medium",
                "watch_stars": True,
            },
        ],
    }
    with open(_config_path, "w") as f:
        json.dump(config, f)
    return config


class TestLoadConfig:
    def test_load_missing_config(self):
        from core.tech_watch import _load_config
        if os.path.exists(_config_path):
            os.remove(_config_path)
        result = _load_config()
        assert result == {"github_watches": []}

    def test_load_valid_config(self, sample_config):
        from core.tech_watch import _load_config
        result = _load_config()
        assert len(result["github_watches"]) == 2
        assert result["github_watches"][0]["id"] == "test_pr"


class TestCache:
    def test_load_empty_cache(self):
        from core.tech_watch import _load_cache
        result = _load_cache()
        assert result["last_check"] == 0

    def test_save_and_load_cache(self):
        from core.tech_watch import _save_cache, _load_cache
        data = {"last_check": 12345, "results": {"foo": "bar"}}
        _save_cache(data)
        loaded = _load_cache()
        assert loaded["last_check"] == 12345
        assert loaded["results"]["foo"] == "bar"


class TestCheckAll:
    @patch("core.tech_watch._fetch_github_issue")
    @patch("core.tech_watch._fetch_github_repo")
    def test_check_all_fresh(self, mock_repo, mock_issue, sample_config):
        from core.tech_watch import check_all
        mock_issue.return_value = {
            "number": 123,
            "title": "Test PR Title",
            "state": "open",
            "updated_at": "2026-04-01",
            "comments": 5,
            "labels": [],
            "is_pr": True,
            "merged": False,
        }
        mock_repo.return_value = {
            "stars": 100,
            "forks": 10,
            "updated_at": "2026-04-01",
            "description": "Test",
        }
        result = check_all(force=True)
        assert not result["from_cache"]
        assert result["total_watches"] == 2
        assert "test_pr" in result["watches"]
        assert len(result["watches"]["test_pr"]["issues"]) == 2

    @patch("core.tech_watch._fetch_github_issue")
    def test_detect_merge(self, mock_issue, sample_config):
        from core.tech_watch import check_all, _save_cache
        # Simuler un etat precedent (PR ouverte)
        _save_cache({
            "last_check": 0,
            "results": {
                "test_pr": {
                    "issues": {
                        "123": {"state": "open", "merged": False, "comments": 3},
                    },
                },
            },
        })
        # Maintenant la PR est mergee
        mock_issue.return_value = {
            "number": 123,
            "title": "Merged PR",
            "state": "closed",
            "updated_at": "2026-04-01",
            "comments": 8,
            "labels": [],
            "is_pr": True,
            "merged": True,
        }
        result = check_all(force=True)
        alerts = result["alerts"]
        assert any("MERGE" in a for a in alerts)
        assert any("Faire quelque chose" in a for a in alerts)

    @patch("core.tech_watch._fetch_github_issue")
    def test_detect_new_comments(self, mock_issue, sample_config):
        from core.tech_watch import check_all, _save_cache
        _save_cache({
            "last_check": 0,
            "results": {
                "test_pr": {
                    "issues": {
                        "123": {"state": "open", "merged": False, "comments": 3},
                    },
                },
            },
        })
        mock_issue.return_value = {
            "number": 123,
            "title": "Active PR",
            "state": "open",
            "updated_at": "2026-04-01",
            "comments": 7,
            "labels": [],
            "is_pr": True,
            "merged": False,
        }
        result = check_all(force=True)
        alerts = result["alerts"]
        assert any("+4 commentaires" in a for a in alerts)

    @patch("core.tech_watch._fetch_github_issue")
    @patch("core.tech_watch._fetch_github_repo")
    def test_detect_star_increase(self, mock_repo, mock_issue, sample_config):
        from core.tech_watch import check_all, _save_cache
        _save_cache({
            "last_check": 0,
            "results": {
                "test_repo": {
                    "issues": {},
                    "repo_stats": {"stars": 200},
                },
            },
        })
        mock_issue.return_value = None
        mock_repo.return_value = {"stars": 350, "forks": 20, "updated_at": "2026-04-01", "description": ""}
        result = check_all(force=True)
        alerts = result["alerts"]
        assert any("STARS" in a and "+150" in a for a in alerts)

    def test_cache_respected(self, sample_config):
        from core.tech_watch import check_all, _save_cache
        _save_cache({
            "last_check": time.time(),
            "results": {"cached": True},
        })
        result = check_all(force=False)
        assert result["from_cache"] is True

    def test_force_ignores_cache(self, sample_config):
        from core.tech_watch import check_all, _save_cache
        _save_cache({
            "last_check": time.time(),
            "results": {"cached": True},
        })
        with patch("core.tech_watch._fetch_github_issue", return_value=None):
            with patch("core.tech_watch._fetch_github_repo", return_value=None):
                result = check_all(force=True)
        assert result["from_cache"] is False


class TestFormatReport:
    def test_format_with_alerts(self):
        from core.tech_watch import format_report
        report = {
            "from_cache": False,
            "total_issues_checked": 3,
            "alerts": ["MERGE: Test PR merged"],
            "watches": {
                "test": {
                    "label": "Test",
                    "priority": "critical",
                    "issues": {
                        "123": {
                            "state": "closed",
                            "merged": True,
                            "title": "Big feature",
                            "updated_at": "2026-04-01",
                            "comments": 10,
                        },
                    },
                    "repo_stats": None,
                },
            },
        }
        text = format_report(report)
        assert "ALERTE" in text
        assert "MERGE" in text
        assert "#123" in text
        assert "MERGED" in text

    def test_format_from_cache(self):
        from core.tech_watch import format_report
        report = {"from_cache": True, "watches": {}, "alerts": []}
        text = format_report(report)
        assert "cache" in text.lower()
