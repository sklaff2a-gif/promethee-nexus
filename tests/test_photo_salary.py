"""Tests pour core/photo_salary.py — Systeme de salaire visuel."""
import json
import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import date, timedelta


@pytest.fixture(autouse=True)
def reset_salary(tmp_path, monkeypatch):
    """Reset le singleton et utilise un fichier temporaire."""
    from core.photo_salary import PhotoSalary
    PhotoSalary.reset_singleton()
    monkeypatch.setattr("core.photo_salary.SALARY_STATE_FILE",
                        str(tmp_path / "photo_salary.json"))
    monkeypatch.setattr("core.photo_salary.bus", MagicMock())
    yield
    PhotoSalary.reset_singleton()


class TestPhotoSalaryInit:
    """Tests d'initialisation et singleton."""

    def test_singleton(self):
        from core.photo_salary import PhotoSalary
        s1 = PhotoSalary()
        s2 = PhotoSalary()
        assert s1 is s2

    def test_initial_credits(self):
        from core.photo_salary import PhotoSalary, WEEKLY_BASE_SALARY
        s = PhotoSalary()
        assert s.get_credits() == WEEKLY_BASE_SALARY

    def test_initial_can_observe(self):
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        assert s.can_observe() is True

    def test_initial_empty_wishlist(self):
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        assert s.get_wishlist() == []


class TestPhotoSalaryCredits:
    """Tests du systeme de credits."""

    def test_can_observe_with_credits(self):
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        assert s.can_observe() is True

    def test_cannot_observe_zero_credits(self):
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        s._credits = 0
        assert s.can_observe() is False

    @pytest.mark.asyncio
    async def test_photo_consumed_decrements(self):
        from core.photo_salary import PhotoSalary, WEEKLY_BASE_SALARY
        s = PhotoSalary()
        initial = s.get_credits()
        await s._on_photo_consumed({})
        assert s.get_credits() == initial - 1

    @pytest.mark.asyncio
    async def test_photo_consumed_at_zero_no_negative(self):
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        s._credits = 0
        await s._on_photo_consumed({})
        assert s.get_credits() == 0

    @pytest.mark.asyncio
    async def test_multiple_photos_consumed(self):
        from core.photo_salary import PhotoSalary, WEEKLY_BASE_SALARY
        s = PhotoSalary()
        for _ in range(5):
            await s._on_photo_consumed({})
        assert s.get_credits() == WEEKLY_BASE_SALARY - 5


class TestPhotoSalaryTasks:
    """Tests de comptabilisation des taches."""

    @pytest.mark.asyncio
    async def test_success_increments(self):
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        await s._on_routine_complete({"status": "success", "intent": "SCHOOL_RESEARCH"})
        assert s._tasks_completed == 1
        assert s._tasks_failed == 0

    @pytest.mark.asyncio
    async def test_failure_increments(self):
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        await s._on_routine_complete({"status": "error", "intent": "EXPANSION_CODE"})
        assert s._tasks_failed == 1
        assert s._tasks_completed == 0

    @pytest.mark.asyncio
    async def test_excellent_tracked(self):
        from core.photo_salary import PhotoSalary, BONUS_QUALITY_THRESHOLD
        s = PhotoSalary()
        await s._on_routine_complete({
            "status": "success",
            "intent": "SCHOOL_CODE_REVIEW",
            "quality_score": BONUS_QUALITY_THRESHOLD + 0.5,
        })
        assert s._tasks_excellent == 1

    @pytest.mark.asyncio
    async def test_low_quality_not_excellent(self):
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        await s._on_routine_complete({
            "status": "success",
            "intent": "SCHOOL_RESEARCH",
            "quality_score": 5.0,
        })
        assert s._tasks_excellent == 0

    @pytest.mark.asyncio
    async def test_low_quality_status(self):
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        await s._on_routine_complete({"status": "low_quality", "intent": "TEST"})
        assert s._tasks_failed == 1


class TestPhotoSalaryComputation:
    """Tests du calcul de salaire."""

    def test_base_salary(self):
        from core.photo_salary import PhotoSalary, WEEKLY_BASE_SALARY
        s = PhotoSalary()
        result = s.compute_weekly_salary()
        assert result["base"] == WEEKLY_BASE_SALARY
        assert result["net"] == WEEKLY_BASE_SALARY

    @pytest.mark.asyncio
    async def test_bonus_excellent(self):
        from core.photo_salary import PhotoSalary, WEEKLY_BASE_SALARY, BONUS_PER_EXCELLENT
        s = PhotoSalary()
        # 3 taches excellentes
        for _ in range(3):
            await s._on_routine_complete({
                "status": "success",
                "quality_score": 9.0,
            })
        result = s.compute_weekly_salary()
        assert result["bonus_quality"] == 3 * BONUS_PER_EXCELLENT
        assert result["net"] == WEEKLY_BASE_SALARY + 3 * BONUS_PER_EXCELLENT

    @pytest.mark.asyncio
    async def test_malus_failures(self):
        from core.photo_salary import PhotoSalary, WEEKLY_BASE_SALARY, MALUS_PER_FAILURE
        s = PhotoSalary()
        for _ in range(4):
            await s._on_routine_complete({"status": "error"})
        result = s.compute_weekly_salary()
        assert result["malus_echecs"] == 4 * MALUS_PER_FAILURE
        assert result["net"] == WEEKLY_BASE_SALARY - 4 * MALUS_PER_FAILURE

    @pytest.mark.asyncio
    async def test_salary_floor(self):
        from core.photo_salary import PhotoSalary, SALARY_FLOOR
        s = PhotoSalary()
        # Beaucoup d'echecs pour forcer le plancher
        for _ in range(50):
            await s._on_routine_complete({"status": "error"})
        result = s.compute_weekly_salary()
        assert result["net"] == SALARY_FLOOR

    @pytest.mark.asyncio
    async def test_salary_ceiling(self):
        from core.photo_salary import PhotoSalary, SALARY_CEILING
        s = PhotoSalary()
        # Beaucoup de taches excellentes pour forcer le plafond
        for _ in range(50):
            await s._on_routine_complete({
                "status": "success",
                "quality_score": 10.0,
            })
        result = s.compute_weekly_salary()
        assert result["net"] == SALARY_CEILING


class TestPhotoSalaryWishlist:
    """Tests de la liste de souhaits."""

    def test_add_wish(self):
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        assert s.add_wish("nature") is True
        assert "nature" in s.get_wishlist()

    def test_no_duplicate_wish(self):
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        s.add_wish("nature")
        assert s.add_wish("nature") is False
        assert len(s.get_wishlist()) == 1

    def test_custom_category(self):
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        assert s.add_wish("coucher de soleil") is True
        assert "coucher de soleil" in s.get_wishlist()

    def test_max_wishes(self):
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        for i in range(10):
            s.add_wish(f"cat_{i}")
        assert len(s.get_wishlist()) == 5

    def test_wishlist_detailed_status_pending(self):
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        s.add_wish("nature")
        detailed = s.get_wishlist_detailed()
        assert len(detailed) == 1
        assert detailed[0]["category"] == "nature"
        assert detailed[0]["status"] == "en_attente"
        assert detailed[0]["fulfilled"] is False

    def test_wishlist_detailed_status_fulfilled(self):
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        s.add_wish("espace")
        s.fulfill_wish("espace")
        detailed = s.get_wishlist_detailed()
        assert detailed[0]["status"] == "visualiser"
        assert detailed[0]["fulfilled"] is True

    def test_wishlist_detailed_status_relance(self):
        import time as _time
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        s.add_wish("art")
        # Simuler un souhait vieux de 4 jours
        for w in s._wishlist:
            if isinstance(w, dict) and w.get("category") == "art":
                w["added_at"] = _time.time() - 4 * 24 * 3600
        detailed = s.get_wishlist_detailed()
        assert detailed[0]["status"] == "relance"

    def test_fulfill_wish_returns_false_if_not_found(self):
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        assert s.fulfill_wish("inexistant") is False

    def test_fulfill_wish_only_once(self):
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        s.add_wish("musique")
        assert s.fulfill_wish("musique") is True
        assert s.fulfill_wish("musique") is False  # Deja honore


class TestPhotoSalaryWeekReset:
    """Tests du reset hebdomadaire."""

    def test_same_week_no_reset(self):
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        s._credits = 5
        s._week_start = (date.today() - timedelta(days=date.today().weekday())).isoformat()
        s._check_week_reset()
        assert s._credits == 5  # Pas de reset

    def test_new_week_resets_credits(self):
        from core.photo_salary import PhotoSalary, WEEKLY_BASE_SALARY
        s = PhotoSalary()
        s._credits = 2
        s._week_start = (date.today() - timedelta(days=7 + date.today().weekday())).isoformat()
        s._tasks_completed = 15
        s._tasks_failed = 3
        s._check_week_reset()
        assert s._credits == WEEKLY_BASE_SALARY
        assert s._tasks_completed == 0
        assert s._tasks_failed == 0

    def test_payday_history_recorded(self):
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        s._week_start = (date.today() - timedelta(days=7 + date.today().weekday())).isoformat()
        s._tasks_completed = 10
        s._tasks_excellent = 2
        s._check_week_reset()
        assert len(s._salary_history) == 1
        assert s._salary_history[0]["tasks_completed"] == 10


class TestPhotoSalaryPersistence:
    """Tests de persistance."""

    def test_save_and_load(self, tmp_path, monkeypatch):
        from core.photo_salary import PhotoSalary
        # Premiere instance
        s1 = PhotoSalary()
        s1._credits = 7
        s1._wishlist = ["nature", "espace"]
        s1._tasks_completed = 5
        s1._save()

        # Reset et reload
        PhotoSalary.reset_singleton()
        s2 = PhotoSalary()
        assert s2._credits == 7
        assert s2._wishlist == ["nature", "espace"]
        assert s2._tasks_completed == 5

    def test_corrupted_file_fallback(self, tmp_path, monkeypatch):
        from core.photo_salary import PhotoSalary, SALARY_STATE_FILE, WEEKLY_BASE_SALARY
        state_file = str(tmp_path / "photo_salary.json")
        monkeypatch.setattr("core.photo_salary.SALARY_STATE_FILE", state_file)
        with open(state_file, "w") as f:
            f.write("NOT JSON")
        PhotoSalary.reset_singleton()
        s = PhotoSalary()
        assert s._credits == WEEKLY_BASE_SALARY


class TestPhotoSalaryNarrative:
    """Tests des narratifs."""

    def test_narrative_no_credits(self):
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        s._credits = 0
        assert "credits" in s.get_narrative().lower() or "travailler" in s.get_narrative().lower()

    def test_narrative_with_credits(self):
        from core.photo_salary import PhotoSalary, WEEKLY_BASE_SALARY
        s = PhotoSalary()
        s._credits = WEEKLY_BASE_SALARY
        assert "credits" in s.get_narrative().lower() or "decouvrir" in s.get_narrative().lower()

    def test_narrative_with_wishlist(self):
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        s._credits = 5
        s._tasks_failed = 0
        s._tasks_completed = 5
        s.add_wish("nature")
        narrative = s.get_narrative()
        assert "nature" in narrative

    def test_salary_context(self):
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        ctx = s.get_salary_context()
        assert "credits" in ctx.lower()

    def test_status_api(self):
        from core.photo_salary import PhotoSalary
        s = PhotoSalary()
        status = s.get_status()
        assert "credits" in status
        assert "wishlist" in status
        assert "salary_projection" in status
