"""Tests du mentor Claude."""

import pytest
from core.mentor import Mentor


@pytest.fixture(autouse=True)
def reset_mentor(tmp_path):
    Mentor.reset_singleton()
    import core.mentor as mm
    mm.MENTOR_STATE_FILE = str(tmp_path / "mentor_state.json")
    m = Mentor()
    m._api_key = ""  # Pas de cle API dans les tests
    yield m
    Mentor.reset_singleton()


class TestMentorBase:

    def test_singleton(self):
        a = Mentor()
        b = Mentor()
        assert a is b

    def test_not_available_without_key(self, reset_mentor):
        m = reset_mentor
        assert not m.is_available()

    def test_available_with_key_and_budget(self, reset_mentor):
        m = reset_mentor
        m._api_key = "test-key"
        m._today = ""  # force daily reset
        assert m.is_available()

    def test_budget_limit(self, reset_mentor):
        m = reset_mentor
        m._api_key = "test-key"
        from datetime import date
        m._today = date.today().isoformat()
        m._calls_today = 5
        assert not m.is_available()

    def test_daily_reset(self, reset_mentor):
        m = reset_mentor
        m._api_key = "test-key"
        m._today = "2020-01-01"
        m._calls_today = 5
        assert m.is_available()  # nouveau jour → reset

    def test_get_status(self, reset_mentor):
        m = reset_mentor
        status = m.get_status()
        assert "available" in status
        assert "calls_today" in status
        assert "budget" in status
        assert status["budget"] == 5

    def test_build_context_empty(self, reset_mentor):
        m = reset_mentor
        ctx = m._build_context()
        assert "premiere session" in ctx.lower()

    def test_build_context_with_history(self, reset_mentor):
        m = reset_mentor
        m._history = [{
            "date": "2026-04-07T03:00:00",
            "slot": "RESEARCH",
            "subject": "Test subject",
            "local_grade": 8.0,
            "claude_feedback": "Bon travail mais...",
        }]
        ctx = m._build_context()
        assert "RESEARCH" in ctx
        assert "Test subject" in ctx


class TestMentorPersistence:

    def test_save_load(self, reset_mentor, tmp_path):
        m = reset_mentor
        m._calls_today = 3
        m._history = [{"slot": "TEST"}]
        m._save()

        Mentor.reset_singleton()
        import core.mentor as mm
        mm.MENTOR_STATE_FILE = str(tmp_path / "mentor_state.json")
        m2 = Mentor()
        assert m2._calls_today == 3
        assert len(m2._history) == 1
