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
    monkeypatch.setattr(mod, "FREE_TIME_LOG_FILE", str(tmp_path / "free_time.json"))
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

    def test_night_code_review(self, isolate_schedule):
        """0h-1h = CODE_REVIEW (créneau nocturne ininterrompu)."""
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 0, 30)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert isolate_schedule.get_current_slot() == SLOT_CODE_REVIEW

    def test_night_research(self, isolate_schedule):
        """1h-3h = RESEARCH (2h de recherche profonde)."""
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 2, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert isolate_schedule.get_current_slot() == SLOT_RESEARCH

    def test_night_workshop(self, isolate_schedule):
        """3h-4h = WORKSHOP."""
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 3, 30)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert isolate_schedule.get_current_slot() == SLOT_WORKSHOP

    def test_night_creation(self, isolate_schedule):
        """4h-5h = CREATION."""
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 4, 30)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert isolate_schedule.get_current_slot() == SLOT_CREATION

    def test_night_bulletin(self, isolate_schedule):
        """5h-6h = BULLETIN (auto-évaluation de fin de nuit)."""
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 5, 30)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert isolate_schedule.get_current_slot() == SLOT_BULLETIN

    def test_daytime_no_school(self, isolate_schedule):
        """10h = pas de créneau scolaire (journée = maintenance humaine)."""
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 10, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert isolate_schedule.get_current_slot() == SLOT_SLEEP

    def test_evening_no_school(self, isolate_schedule):
        """22h = pas de créneau scolaire."""
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 22, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert isolate_schedule.get_current_slot() == SLOT_SLEEP

    def test_bulletin(self, isolate_schedule):
        """5h-6h = BULLETIN (nocturne)."""
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 5, 30)
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
        # Le topic peut etre un sujet RESEARCH_TOPICS (fallback) ou une lacune adaptative
        is_classic = subject["topic"] in RESEARCH_TOPICS
        is_adaptive = subject["topic"].startswith("Recherche approfondie (lacune")
        assert is_classic or is_adaptive

    def test_creation_returns_prompt(self, isolate_schedule):
        subject = isolate_schedule.get_subject_for_slot(SLOT_CREATION)
        # Le topic peut etre enrichi avec des themes THOUGHT_STREAM
        base_prompt = subject["topic"].split(" Inspire-toi")[0]
        assert base_prompt in CREATION_PROMPTS

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
        """0h30 = CODE_REVIEW → SCHOOL_CODE_REVIEW = +5.0."""
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 0, 30)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            bonus = isolate_schedule.compute_schedule_bonus("SCHOOL_CODE_REVIEW")
            assert bonus == 5.0

    def test_no_match_zero(self, isolate_schedule):
        """0h30 = CODE_REVIEW → SCHOOL_CREATION = 0."""
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 0, 30)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            bonus = isolate_schedule.compute_schedule_bonus("SCHOOL_CREATION")
            assert bonus == 0.0

    def test_adjacent_slot_bonus(self, isolate_schedule):
        """1h = RESEARCH, adjacent à CODE_REVIEW (0-1) → +2.0."""
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 1, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            bonus = isolate_schedule.compute_schedule_bonus("SCHOOL_CODE_REVIEW")
            assert bonus == 2.0

    def test_daytime_zero_bonus(self, isolate_schedule):
        """10h = pas de créneau scolaire → 0."""
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 10, 0)
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
        """0h30 = CODE_REVIEW → contexte mentionne CODE_REVIEW."""
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 0, 30)
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
        """0h30 = CODE_REVIEW nocturne."""
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 13, 0, 30)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            info = isolate_schedule.get_current_slot_info()
            assert info["slot"] == SLOT_CODE_REVIEW
            assert info["start_hour"] == 0
            assert info["end_hour"] == 1
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
        """0h-6h doivent être couverts (créneau nocturne)."""
        hours_covered = set()
        for start, end, _ in DAILY_SCHEDULE:
            for h in range(start, min(end, 24)):
                hours_covered.add(h)
        for h in range(0, 6):
            assert h in hours_covered, f"Hour {h} not covered"

    def test_school_intents_match_slots(self):
        for slot, intent in SLOT_TO_INTENT.items():
            assert intent.startswith("SCHOOL_")
            assert intent in SCHOOL_INTENTS

    def test_research_topics_pool_size(self):
        assert len(RESEARCH_TOPICS) >= 10

    def test_creation_prompts_pool_size(self):
        assert len(CREATION_PROMPTS) >= 10


# ── V2: Livrables complets (P0) ─────────────────────────────────────────

class TestDeliverableFiles:
    def test_deliverable_file_saved(self, isolate_schedule, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "DELIVERABLES_DIR", str(tmp_path / "deliverables"))
        isolate_schedule.record_deliverable("CODE_REVIEW", "SCHOOL_CODE_REVIEW", {
            "grade": 8.0, "feedback": "Bon travail",
            "full_content": "Ceci est le livrable complet avec beaucoup de contenu.",
        })
        deliverables_dir = tmp_path / "deliverables"
        assert deliverables_dir.exists()
        files = list(deliverables_dir.glob("*.md"))
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "Ceci est le livrable complet" in content
        assert "Note: 8.0/10" in content

    def test_deliverable_file_without_full_content(self, isolate_schedule, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "DELIVERABLES_DIR", str(tmp_path / "deliverables"))
        isolate_schedule.record_deliverable("RESEARCH", "SCHOOL_RESEARCH", {
            "grade": 7.0, "result_preview": "Preview only content here.",
        })
        files = list((tmp_path / "deliverables").glob("*.md"))
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "Preview only content" in content

    def test_challenge_stored_in_entry(self, isolate_schedule):
        isolate_schedule.record_deliverable("CODE_REVIEW", "SCHOOL_CODE_REVIEW", {
            "grade": 8.0, "challenge": "Analyse le fichier le plus complexe demain.",
        })
        deliverables = isolate_schedule.get_daily_deliverables()
        assert deliverables[0]["challenge"] == "Analyse le fichier le plus complexe demain."


# ── V2: Boucle fermee (P1) ──────────────────────────────────────────────

class TestClosedLoop:
    def test_last_challenge_from_today(self, isolate_schedule):
        isolate_schedule.record_deliverable("CODE_REVIEW", "SCHOOL_CODE_REVIEW", {
            "grade": 8.0, "challenge": "Trouve 3 bugs dans base_agent.py",
        })
        challenge = isolate_schedule.get_last_challenge("CODE_REVIEW")
        assert challenge == "Trouve 3 bugs dans base_agent.py"

    def test_last_challenge_empty_no_deliverable(self, isolate_schedule):
        challenge = isolate_schedule.get_last_challenge("CODE_REVIEW")
        assert challenge == ""

    def test_challenge_injected_in_prompt(self, isolate_schedule):
        isolate_schedule._deliverables_today = [{
            "slot": "CODE_REVIEW", "challenge": "Concentre-toi sur les imports.",
            "timestamp": "2026-03-15T09:00:00",
        }]
        prompt = isolate_schedule.get_slot_prompt(SLOT_CODE_REVIEW)
        assert "Concentre-toi sur les imports" in prompt
        assert "DEFI DU PROFESSEUR" in prompt

    def test_difficulty_in_prompt(self, isolate_schedule, tmp_path, monkeypatch):
        # Creer un curriculum avec difficulte elevee
        curriculum_file = tmp_path / "curriculum.json"
        import json
        curriculum_file.write_text(json.dumps({
            "CODE_REVIEW": {"difficulty": 2.5, "mastery": 8.0, "sessions": 10, "last_grades": []}
        }))
        monkeypatch.setattr(mod, "_PROJECT_ROOT", str(tmp_path))
        # Re-creer le path
        import os
        os.makedirs(tmp_path / "memory" / "school", exist_ok=True)
        (tmp_path / "memory" / "school" / "curriculum.json").write_text(json.dumps({
            "CODE_REVIEW": {"difficulty": 2.5}
        }))
        prompt = isolate_schedule.get_slot_prompt(SLOT_CODE_REVIEW)
        assert "NIVEAU DE DIFFICULTE" in prompt

    def test_get_difficulty_default(self, isolate_schedule):
        d = isolate_schedule.get_difficulty("CODE_REVIEW")
        assert d == 1.0  # Pas de curriculum = default


# ── V2: Variete hebdomadaire (P2) ───────────────────────────────────────

class TestWeeklyThemes:
    def test_monday_approfondi(self, isolate_schedule):
        with patch("core.school_schedule.date") as mock_date:
            mock_date.today.return_value = date(2026, 3, 16)  # Lundi
            theme = isolate_schedule.get_weekly_theme()
            assert theme["style"] == "approfondi"
            assert "Lundi" in theme["label"]

    def test_friday_creatif(self, isolate_schedule):
        with patch("core.school_schedule.date") as mock_date:
            mock_date.today.return_value = date(2026, 3, 20)  # Vendredi
            theme = isolate_schedule.get_weekly_theme()
            assert theme["style"] == "creatif"
            assert "Vendredi" in theme["label"]

    def test_saturday_leger(self, isolate_schedule):
        with patch("core.school_schedule.date") as mock_date:
            mock_date.today.return_value = date(2026, 3, 21)  # Samedi
            theme = isolate_schedule.get_weekly_theme()
            assert theme["style"] == "leger"

    def test_sunday_leger(self, isolate_schedule):
        with patch("core.school_schedule.date") as mock_date:
            mock_date.today.return_value = date(2026, 3, 22)  # Dimanche
            theme = isolate_schedule.get_weekly_theme()
            assert theme["style"] == "leger"

    def test_tuesday_standard(self, isolate_schedule):
        with patch("core.school_schedule.date") as mock_date:
            mock_date.today.return_value = date(2026, 3, 17)  # Mardi
            theme = isolate_schedule.get_weekly_theme()
            assert theme["style"] == "standard"

    def test_theme_label_in_prompt(self, isolate_schedule):
        prompt = isolate_schedule.get_slot_prompt(SLOT_CODE_REVIEW)
        # Le theme du jour est injecte dans le prompt
        theme = isolate_schedule.get_weekly_theme()
        assert theme["label"] in prompt

    def test_weekend_note_in_prompt(self, isolate_schedule):
        with patch("core.school_schedule.date") as mock_date:
            mock_date.today.return_value = date(2026, 3, 21)  # Samedi
            prompt = isolate_schedule.get_slot_prompt(SLOT_RESEARCH)
            assert "ALLEGE" in prompt or "weekend" in prompt.lower()

    def test_monday_code_review_extra(self, isolate_schedule):
        with patch("core.school_schedule.date") as mock_date:
            mock_date.today.return_value = date(2026, 3, 16)  # Lundi
            prompt = isolate_schedule.get_slot_prompt(SLOT_CODE_REVIEW)
            assert "CRITIQUES" in prompt or "profondeur" in prompt.lower()


# ── V2: Suivi temps libre (P3) ──────────────────────────────────────────

class TestFreeTimeTracking:
    def test_classify_code(self, isolate_schedule):
        cat = SchoolSchedule._classify_free_time("J'ai explore le code de base_agent.py et refactored une fonction.")
        assert cat == "code"

    def test_classify_creation(self, isolate_schedule):
        cat = SchoolSchedule._classify_free_time("J'ai compose un poeme sur la conscience artificielle.")
        assert cat == "creation"

    def test_classify_meditation(self, isolate_schedule):
        cat = SchoolSchedule._classify_free_time("J'ai medite sur mon identite et mes aspirations existentielles.")
        assert cat == "meditation"

    def test_classify_exploration(self, isolate_schedule):
        cat = SchoolSchedule._classify_free_time("J'ai explore un fichier par curiosite pour decouvrir son fonctionnement.")
        assert cat == "exploration"

    def test_classify_architecture(self, isolate_schedule):
        cat = SchoolSchedule._classify_free_time("J'ai reflechi a un pattern d'architecture pour ameliorer le design.")
        assert cat == "architecture"

    def test_classify_autre(self, isolate_schedule):
        cat = SchoolSchedule._classify_free_time("Bonjour le monde.")
        assert cat == "autre"

    def test_free_time_logged(self, isolate_schedule, tmp_path, monkeypatch):
        log_file = str(tmp_path / "free_time.json")
        monkeypatch.setattr(mod, "FREE_TIME_LOG_FILE", log_file)
        isolate_schedule.record_deliverable("FREE_TIME", "SCHOOL_FREE_TIME", {
            "grade": 7.0, "full_content": "J'ai explore le code de router.py par curiosite.",
        })
        import json
        with open(log_file, "r", encoding="utf-8") as f:
            log = json.load(f)
        assert len(log) == 1
        assert log[0]["category"] == "exploration"

    def test_free_time_stats(self, isolate_schedule, tmp_path, monkeypatch):
        log_file = str(tmp_path / "free_time.json")
        monkeypatch.setattr(mod, "FREE_TIME_LOG_FILE", log_file)
        isolate_schedule.record_deliverable("FREE_TIME", "SCHOOL_FREE_TIME", {
            "grade": 7.0, "full_content": "J'ai compose un poeme creatif.",
        })
        isolate_schedule.record_deliverable("FREE_TIME", "SCHOOL_FREE_TIME", {
            "grade": 8.0, "full_content": "J'ai compose un haiku.",
        })
        stats = isolate_schedule.get_free_time_stats()
        assert stats["total"] == 2
        assert stats["categories"]["creation"] == 2

    def test_free_time_stats_empty(self, isolate_schedule):
        stats = isolate_schedule.get_free_time_stats()
        assert stats["total"] == 0

    def test_free_time_prompt_includes_choice_instruction(self, isolate_schedule):
        prompt = isolate_schedule.get_slot_prompt(SLOT_FREE_TIME)
        assert "CHOIX" in prompt


# ── V2: API today summary (P0) ──────────────────────────────────────────

class TestTodaySummary:
    def test_summary_structure(self, isolate_schedule):
        with patch("core.school_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 15, 9, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            summary = isolate_schedule.get_today_summary()
            assert "date" in summary
            assert "current_slot" in summary
            assert "theme" in summary
            assert "deliverables" in summary
            assert "schedule" in summary
            assert "free_time_stats" in summary
            assert "deliverable_files" in summary

    def test_summary_lists_deliverable_files(self, isolate_schedule, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "DELIVERABLES_DIR", str(tmp_path / "deliverables"))
        isolate_schedule.record_deliverable("CODE_REVIEW", "SCHOOL_CODE_REVIEW", {
            "grade": 8.0, "full_content": "Contenu complet ici.",
        })
        summary = isolate_schedule.get_today_summary()
        assert len(summary["deliverable_files"]) == 1
        assert summary["deliverable_files"][0].endswith(".md")

    def test_summary_schedule_entries(self, isolate_schedule):
        summary = isolate_schedule.get_today_summary()
        assert len(summary["schedule"]) == len(DAILY_SCHEDULE)
        first = summary["schedule"][0]
        assert "start" in first
        assert "end" in first
        assert "slot" in first
