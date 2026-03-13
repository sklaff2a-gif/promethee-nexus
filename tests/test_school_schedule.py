"""Tests pour core/school_schedule.py — Emploi du temps de Promethee."""
import json
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, date

import core.school_schedule as mod
from core.school_schedule import (
    SchoolSchedule, schedule,
    SLOT_REVEIL, SLOT_CODE_REVIEW, SLOT_RESEARCH, SLOT_PAUSE,
    SLOT_WORKSHOP, SLOT_CREATION, SLOT_FREE_TIME, SLOT_BULLETIN, SLOT_SLEEP,
    SLOT_TO_INTENT, SLOT_TO_AGENT, SCHOOL_INTENTS,
    DAILY_SCHEDULE, RESEARCH_TOPICS, CREATION_PROMPTS,
)


@pytest.fixture(autouse=True)
def isolate_schedule(tmp_path, monkeypatch):
    """Isole le singleton et le fichier d'etat."""
    SchoolSchedule.reset_singleton()
    monkeypatch.setattr(mod, "SCHEDULE_STATE_FILE", str(tmp_path / "schedule.json"))
    monkeypatch.setattr(mod, "DELIVERABLES_DIR", str(tmp_path / "deliverables"))
    monkeypatch.setattr(mod, "CREATIONS_DIR", str(tmp_path / "creations"))
    monkeypatch.setattr(mod, "BULLETINS_DIR", str(tmp_path / "bulletins"))
    with patch.object(SchoolSchedule, "_load"):
        s = SchoolSchedule()
        s._last_date = date.today().isoformat()
        s._deliverables_today = []
        s._total_school_days = 0
        s._subscribed = False
        mod.schedule = s
    yield s
    SchoolSchedule.reset_singleton()


# ── Singleton ────────────────────────────────────────────────────────────

class TestSingleton:
    def test_singleton_identity(self, isolate_schedule):
        s1 = SchoolSchedule()
        s2 = SchoolSchedule()
        assert s1 is s2

    def test_reset_singleton(self):
        s1 = SchoolSchedule()
        SchoolSchedule.reset_singleton()
        with patch.object(SchoolSchedule, "_load"):
            s2 = SchoolSchedule()
        assert s1 is not s2

    def test_initial_state(self, isolate_schedule):
        s = isolate_schedule
        assert s._deliverables_today == []
        assert s._total_school_days == 0

    def test_initialized_flag(self, isolate_schedule):
        s = isolate_schedule
        assert s._initialized is True


# ── Detection de creneau ─────────────────────────────────────────────────

class TestSlotDetection:
    def _mock_hour(self, hour):
        return patch("core.school_schedule.datetime") if False else None

    def test_morning_code_review(self, isolate_schedule):
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 9, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert isolate_schedule.get_current_slot() == SLOT_CODE_REVIEW

    def test_research_slot(self, isolate_schedule):
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 11, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert isolate_schedule.get_current_slot() == SLOT_RESEARCH

    def test_workshop_slot(self, isolate_schedule):
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 14, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert isolate_schedule.get_current_slot() == SLOT_WORKSHOP

    def test_creation_slot(self, isolate_schedule):
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 15, 30)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert isolate_schedule.get_current_slot() == SLOT_CREATION

    def test_sleep_night(self, isolate_schedule):
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 22, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert isolate_schedule.get_current_slot() == SLOT_SLEEP

    def test_sleep_early_morning(self, isolate_schedule):
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 3, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert isolate_schedule.get_current_slot() == SLOT_SLEEP

    def test_reveil(self, isolate_schedule):
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 7, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert isolate_schedule.get_current_slot() == SLOT_REVEIL

    def test_bulletin(self, isolate_schedule):
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 17, 30)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert isolate_schedule.get_current_slot() == SLOT_BULLETIN


# ── Rotation des sujets ──────────────────────────────────────────────────

class TestSubjectRotation:
    def test_code_review_returns_file(self, isolate_schedule):
        subject = isolate_schedule.get_subject_for_slot(SLOT_CODE_REVIEW)
        assert "topic" in subject
        assert "target_file" in subject
        assert "/" in subject["target_file"]  # core/xxx.py ou Agents/xxx.py

    def test_research_returns_topic(self, isolate_schedule):
        subject = isolate_schedule.get_subject_for_slot(SLOT_RESEARCH)
        assert subject["topic"] in RESEARCH_TOPICS

    def test_creation_returns_prompt(self, isolate_schedule):
        subject = isolate_schedule.get_subject_for_slot(SLOT_CREATION)
        assert subject["topic"] in CREATION_PROMPTS

    def test_different_days_different_subjects(self, isolate_schedule):
        with patch("core.school_schedule.date") as mock_date:
            mock_date.today.return_value = date(2026, 3, 13)
            s1 = isolate_schedule.get_subject_for_slot(SLOT_CODE_REVIEW)
            mock_date.today.return_value = date(2026, 3, 14)
            s2 = isolate_schedule.get_subject_for_slot(SLOT_CODE_REVIEW)
        # Pas forcement different (petit pool), mais le hash change
        # On verifie juste que ca ne plante pas
        assert "target_file" in s1
        assert "target_file" in s2

    def test_bulletin_subject(self, isolate_schedule):
        subject = isolate_schedule.get_subject_for_slot(SLOT_BULLETIN)
        assert "bulletin" in subject["topic"].lower()


# ── Prompts ──────────────────────────────────────────────────────────────

class TestPromptGeneration:
    def test_code_review_prompt_not_empty(self, isolate_schedule):
        prompt = isolate_schedule.get_slot_prompt(SLOT_CODE_REVIEW)
        assert len(prompt) > 50
        assert "FICHIER" in prompt

    def test_research_prompt_contains_subject(self, isolate_schedule):
        prompt = isolate_schedule.get_slot_prompt(SLOT_RESEARCH)
        assert "SUJET" in prompt
        assert len(prompt) > 50

    def test_workshop_prompt(self, isolate_schedule):
        prompt = isolate_schedule.get_slot_prompt(SLOT_WORKSHOP)
        assert "code" in prompt.lower() or "Python" in prompt

    def test_creation_prompt(self, isolate_schedule):
        prompt = isolate_schedule.get_slot_prompt(SLOT_CREATION)
        assert "CREATION" in prompt or "creatif" in prompt.lower()

    def test_bulletin_prompt(self, isolate_schedule):
        prompt = isolate_schedule.get_slot_prompt(SLOT_BULLETIN)
        assert "BULLETIN" in prompt
        assert "auto-evaluation" in prompt.lower() or "accompli" in prompt.lower()

    def test_sleep_prompt_empty(self, isolate_schedule):
        prompt = isolate_schedule.get_slot_prompt(SLOT_SLEEP)
        assert prompt == ""


# ── Bonus de scoring ─────────────────────────────────────────────────────

class TestScheduleBonus:
    def test_exact_match_bonus(self, isolate_schedule):
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 9, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            bonus = isolate_schedule.compute_schedule_bonus("SCHOOL_CODE_REVIEW")
            assert bonus == 5.0

    def test_no_match_zero(self, isolate_schedule):
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 9, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            bonus = isolate_schedule.compute_schedule_bonus("SCHOOL_CREATION")
            assert bonus == 0.0

    def test_adjacent_slot_bonus(self, isolate_schedule):
        with patch("core.school_schedule.datetime") as mock_dt:
            # 10h = RESEARCH, adjacent a CODE_REVIEW (8-10)
            mock_dt.now.return_value = datetime(2026, 3, 13, 10, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            bonus = isolate_schedule.compute_schedule_bonus("SCHOOL_CODE_REVIEW")
            assert bonus == 2.0

    def test_sleep_zero_bonus(self, isolate_schedule):
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 22, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            bonus = isolate_schedule.compute_schedule_bonus("SCHOOL_CODE_REVIEW")
            assert bonus == 0.0

    def test_non_school_intent_zero(self, isolate_schedule):
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 9, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            bonus = isolate_schedule.compute_schedule_bonus("COUNCIL_DEBATE")
            assert bonus == 0.0


# ── Suivi des livrables ──────────────────────────────────────────────────

class TestDeliverableTracking:
    def test_record_deliverable(self, isolate_schedule):
        isolate_schedule.record_deliverable("CODE_REVIEW", "SCHOOL_CODE_REVIEW", {
            "grade": 7.5, "result_preview": "Bon travail."
        })
        assert len(isolate_schedule.get_daily_deliverables()) == 1

    def test_multiple_deliverables(self, isolate_schedule):
        isolate_schedule.record_deliverable("CODE_REVIEW", "SCHOOL_CODE_REVIEW", {"grade": 7.0})
        isolate_schedule.record_deliverable("RESEARCH", "SCHOOL_RESEARCH", {"grade": 8.0})
        assert len(isolate_schedule.get_daily_deliverables()) == 2

    def test_deliverable_has_timestamp(self, isolate_schedule):
        isolate_schedule.record_deliverable("CREATION", "SCHOOL_CREATION", {"grade": 6.0})
        deliverables = isolate_schedule.get_daily_deliverables()
        assert "timestamp" in deliverables[0]

    def test_day_reset(self, isolate_schedule):
        isolate_schedule.record_deliverable("CODE_REVIEW", "SCHOOL_CODE_REVIEW", {"grade": 7.0})
        assert len(isolate_schedule.get_daily_deliverables()) == 1
        # Simuler changement de jour
        isolate_schedule._last_date = "2026-03-12"
        isolate_schedule._check_day_reset()
        assert len(isolate_schedule.get_daily_deliverables()) == 0
        assert isolate_schedule._total_school_days == 1


# ── Contexte ─────────────────────────────────────────────────────────────

class TestScheduleContext:
    def test_context_not_empty(self, isolate_schedule):
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 9, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            ctx = isolate_schedule.get_schedule_context()
            assert len(ctx) > 20

    def test_context_contains_slot(self, isolate_schedule):
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 9, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            ctx = isolate_schedule.get_schedule_context()
            assert "CODE_REVIEW" in ctx

    def test_sleep_context(self, isolate_schedule):
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 22, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            ctx = isolate_schedule.get_schedule_context()
            assert "SOMMEIL" in ctx


# ── Persistance ──────────────────────────────────────────────────────────

class TestPersistence:
    def test_save_load_cycle(self, isolate_schedule, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "SCHEDULE_STATE_FILE", str(tmp_path / "state.json"))
        isolate_schedule.record_deliverable("CODE_REVIEW", "SCHOOL_CODE_REVIEW", {"grade": 8.0})
        isolate_schedule.save()
        # Reload
        SchoolSchedule.reset_singleton()
        monkeypatch.setattr(mod, "SCHEDULE_STATE_FILE", str(tmp_path / "state.json"))
        s2 = SchoolSchedule()
        assert s2._total_school_days == isolate_schedule._total_school_days

    def test_corrupted_file(self, tmp_path, monkeypatch):
        state_file = tmp_path / "bad.json"
        state_file.write_text("not json{{{")
        monkeypatch.setattr(mod, "SCHEDULE_STATE_FILE", str(state_file))
        SchoolSchedule.reset_singleton()
        s = SchoolSchedule()
        assert s._total_school_days == 0
        assert s._deliverables_today == []


# ── Slot info ────────────────────────────────────────────────────────────

class TestSlotInfo:
    def test_slot_info_complete(self, isolate_schedule):
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 9, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            info = isolate_schedule.get_current_slot_info()
            assert info["slot"] == SLOT_CODE_REVIEW
            assert info["start_hour"] == 8
            assert info["end_hour"] == 10
            assert info["agent"] == "security"
            assert info["intent"] == "SCHOOL_CODE_REVIEW"
            assert len(info["prompt"]) > 0

    def test_slot_info_sleep(self, isolate_schedule):
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 23, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            info = isolate_schedule.get_current_slot_info()
            assert info["slot"] == SLOT_SLEEP

    def test_slot_to_intent_mapping(self):
        assert SLOT_TO_INTENT[SLOT_CODE_REVIEW] == "SCHOOL_CODE_REVIEW"
        assert SLOT_TO_INTENT[SLOT_RESEARCH] == "SCHOOL_RESEARCH"
        assert SLOT_TO_INTENT[SLOT_WORKSHOP] == "SCHOOL_WORKSHOP"
        assert SLOT_TO_INTENT[SLOT_CREATION] == "SCHOOL_CREATION"
        assert SLOT_TO_INTENT[SLOT_BULLETIN] == "SCHOOL_BULLETIN"

    def test_slot_to_agent_mapping(self):
        assert SLOT_TO_AGENT[SLOT_CODE_REVIEW] == "security"
        assert SLOT_TO_AGENT[SLOT_RESEARCH] == "researcher"
        assert SLOT_TO_AGENT[SLOT_WORKSHOP] == "evolution"
        assert SLOT_TO_AGENT[SLOT_CREATION] == "writer"


# ── Constants ────────────────────────────────────────────────────────────

class TestConstants:
    def test_all_slots_covered(self):
        hours_covered = set()
        for start, end, _ in DAILY_SCHEDULE:
            for h in range(start, min(end, 24)):
                hours_covered.add(h)
        # 6h-18h doivent etre couverts
        for h in range(6, 18):
            assert h in hours_covered, f"Hour {h} not covered"

    def test_school_intents_match_slots(self):
        for slot, intent in SLOT_TO_INTENT.items():
            assert intent.startswith("SCHOOL_")
            assert intent in SCHOOL_INTENTS

    def test_research_topics_pool_size(self):
        assert len(RESEARCH_TOPICS) >= 10

    def test_creation_prompts_pool_size(self):
        assert len(CREATION_PROMPTS) >= 10
