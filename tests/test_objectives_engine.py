# tests/test_objectives_engine.py — Tests du moteur d'objectifs autonomes
import os
import json
import pytest
from unittest.mock import patch, MagicMock

from core.objectives_engine import (
    ObjectivesEngine, STATE_FILE,
    MAX_ACTIVE_OBJECTIVES, PRIORITY_WEIGHTS, PATTERN_OBJECTIVE_MAP,
    COMPLETION_REWARDS,
)


@pytest.fixture
def engine():
    """Retourne le singleton ObjectivesEngine courant (frais après reset conftest)."""
    return ObjectivesEngine()


class TestObjectiveCreation:
    """Tests de création d'objectifs."""

    def test_create_objective_basic(self, engine):
        obj = engine.create_objective(
            title="Test objectif",
            obj_type="performance",
            priority="high",
            source="user",
            criteria={"metric": "success_rate", "operator": ">=", "target": 0.8},
            routine_affinities={"AUDIT_STRUCTURE": 1.0},
            deadline_routines=20,
        )
        assert obj is not None
        assert obj["title"] == "Test objectif"
        assert obj["type"] == "performance"
        assert obj["priority"] == "high"
        assert obj["source"] == "user"
        assert obj["status"] == "active"
        assert obj["progress"] == 0.0
        assert obj["routines_since_creation"] == 0
        assert obj["criteria"]["current"] is None
        assert len(obj["history"]) == 1
        assert obj["history"][0]["event"] == "created"

    def test_create_objective_returns_none_when_max_active(self, engine):
        for i in range(MAX_ACTIVE_OBJECTIVES):
            obj = engine.create_objective(
                title=f"Objectif {i}",
                obj_type="performance",
                priority="medium",
                source="user",
                criteria={"metric": "success_rate", "operator": ">=", "target": 0.8},
                routine_affinities={},
                deadline_routines=30,
            )
            assert obj is not None

        # Le 6e doit retourner None
        obj = engine.create_objective(
            title="Objectif de trop",
            obj_type="performance",
            priority="high",
            source="user",
            criteria={"metric": "success_rate", "operator": ">=", "target": 0.9},
            routine_affinities={},
            deadline_routines=10,
        )
        assert obj is None

    def test_create_objective_id_format(self, engine):
        obj = engine.create_objective(
            title="ID test",
            obj_type="exploration",
            priority="low",
            source="council",
            criteria={"metric": "success_rate", "operator": ">=", "target": 0.5},
            routine_affinities={},
        )
        assert obj["id"].startswith("obj_")

    def test_get_active_objectives(self, engine):
        engine.create_objective(
            title="Actif", obj_type="performance", priority="high", source="user",
            criteria={"metric": "success_rate", "operator": ">=", "target": 0.8},
            routine_affinities={},
        )
        active = engine.get_active_objectives()
        assert len(active) == 1
        assert active[0]["title"] == "Actif"

    def test_get_all_objectives(self, engine):
        engine.create_objective(
            title="Obj1", obj_type="performance", priority="high", source="user",
            criteria={"metric": "success_rate", "operator": ">=", "target": 0.8},
            routine_affinities={},
        )
        all_objs = engine.get_all_objectives()
        assert len(all_objs) == 1


class TestProgressCalculation:
    """Tests du calcul de progression."""

    def test_progress_gte_operator(self):
        assert ObjectivesEngine._calc_progress(0.6, 0.8, ">=") == pytest.approx(0.75)

    def test_progress_gte_complete(self):
        assert ObjectivesEngine._calc_progress(0.9, 0.8, ">=") == 1.0

    def test_progress_gte_zero_target(self):
        assert ObjectivesEngine._calc_progress(5, 0, ">=") == 1.0

    def test_progress_lte_operator(self):
        assert ObjectivesEngine._calc_progress(4, 2, "<=") == 0.5

    def test_progress_lte_zero_current(self):
        assert ObjectivesEngine._calc_progress(0, 1, "<=") == 1.0

    def test_progress_lte_zero_target(self):
        assert ObjectivesEngine._calc_progress(5, 0, "<=") == 0.0

    def test_progress_eq_match(self):
        assert ObjectivesEngine._calc_progress("GO", "GO", "==") == 1.0

    def test_progress_eq_no_match(self):
        assert ObjectivesEngine._calc_progress("DEGRADED", "GO", "==") == 0.0

    def test_progress_clamped_to_1(self):
        assert ObjectivesEngine._calc_progress(1.0, 0.5, ">=") == 1.0

    def test_progress_clamped_to_0(self):
        assert ObjectivesEngine._calc_progress(-0.5, 0.8, ">=") == 0.0


class TestRoutineBonus:
    """Tests du bonus scoring pour les routines."""

    def test_bonus_single_objective(self, engine):
        engine.create_objective(
            title="Test bonus",
            obj_type="performance",
            priority="high",
            source="user",
            criteria={"metric": "success_rate", "operator": ">=", "target": 0.8},
            routine_affinities={"AUDIT_STRUCTURE": 1.0, "EXPANSION_CODE": 0.5},
            deadline_routines=20,
        )
        assert engine.get_routine_bonus("AUDIT_STRUCTURE") == 1.0
        assert engine.get_routine_bonus("EXPANSION_CODE") == 0.5
        assert engine.get_routine_bonus("DROPZONE_SCAN") == 0.0

    def test_bonus_multiple_objectives(self, engine):
        engine.create_objective(
            title="Obj1", obj_type="performance", priority="high", source="user",
            criteria={"metric": "success_rate", "operator": ">=", "target": 0.8},
            routine_affinities={"AUDIT_STRUCTURE": 1.0},
            deadline_routines=20,
        )
        engine.create_objective(
            title="Obj2", obj_type="maintenance", priority="medium", source="pattern",
            criteria={"metric": "error_streak", "operator": "<=", "target": 1},
            routine_affinities={"AUDIT_STRUCTURE": 0.5},
            deadline_routines=15,
        )
        # high: 1.0*1.0 + medium: 0.5*0.7 = 1.35
        assert engine.get_routine_bonus("AUDIT_STRUCTURE") == pytest.approx(1.35)

    def test_bonus_ignores_completed(self, engine):
        obj = engine.create_objective(
            title="Complété", obj_type="performance", priority="high", source="user",
            criteria={"metric": "success_rate", "operator": ">=", "target": 0.8},
            routine_affinities={"AUDIT_STRUCTURE": 1.0},
        )
        engine.complete_objective(obj["id"], "test")
        assert engine.get_routine_bonus("AUDIT_STRUCTURE") == 0.0


class TestCompletion:
    """Tests de complétion d'objectifs."""

    def test_complete_objective(self, engine):
        obj = engine.create_objective(
            title="À compléter", obj_type="performance", priority="high", source="user",
            criteria={"metric": "success_rate", "operator": ">=", "target": 0.8},
            routine_affinities={},
        )
        engine.complete_objective(obj["id"], "Test réussi")
        completed = engine._find_objective(obj["id"])
        assert completed["status"] == "completed"
        assert completed["progress"] == 1.0
        assert any(h["event"] == "completed" for h in completed["history"])

    @patch("core.psyche.psyche")
    def test_complete_applies_psyche_rewards(self, mock_psyche, engine):
        obj = engine.create_objective(
            title="Reward test", obj_type="exploration", priority="high", source="user",
            criteria={"metric": "success_rate", "operator": ">=", "target": 0.8},
            routine_affinities={},
        )
        engine.complete_objective(obj["id"], "Test rewards")
        mock_psyche.apply_deltas.assert_called_once_with(
            "_system", COMPLETION_REWARDS["exploration"]
        )


class TestExpiration:
    """Tests d'expiration d'objectifs."""

    def test_expire_objective(self, engine):
        obj = engine.create_objective(
            title="Va expirer", obj_type="performance", priority="high", source="user",
            criteria={"metric": "success_rate", "operator": ">=", "target": 0.8},
            routine_affinities={},
            deadline_routines=5,
        )
        engine._find_objective(obj["id"])["routines_since_creation"] = 5
        engine.expire_objectives()
        expired = engine._find_objective(obj["id"])
        assert expired["status"] == "failed"
        assert any(h["event"] == "expired" for h in expired["history"])

    def test_no_expire_before_deadline(self, engine):
        obj = engine.create_objective(
            title="Pas encore", obj_type="performance", priority="high", source="user",
            criteria={"metric": "success_rate", "operator": ">=", "target": 0.8},
            routine_affinities={},
            deadline_routines=10,
        )
        engine._find_objective(obj["id"])["routines_since_creation"] = 9
        engine.expire_objectives()
        assert engine._find_objective(obj["id"])["status"] == "active"


class TestAutoGeneration:
    """Tests de l'auto-génération depuis patterns."""

    def test_auto_generate_error_streak(self, engine):
        patterns = [{"type": "error_streak", "severity": "high", "message": "3 erreurs"}]
        engine.auto_generate_from_patterns(patterns)
        active = engine.get_active_objectives()
        assert len(active) == 1
        assert active[0]["title"] == "Stabiliser le système"
        assert active[0]["criteria"]["metric"] == "error_streak"

    def test_auto_generate_no_duplicate_metric(self, engine):
        patterns = [{"type": "error_streak", "severity": "high", "message": "3 erreurs"}]
        engine.auto_generate_from_patterns(patterns)
        engine.auto_generate_from_patterns(patterns)
        active = engine.get_active_objectives()
        assert len(active) == 1

    def test_auto_generate_trait_falling(self, engine):
        patterns = [{"type": "trait_falling", "severity": "info", "message": "Trait 'curiosite' en baisse constante (60 → 45)"}]
        engine.auto_generate_from_patterns(patterns)
        active = engine.get_active_objectives()
        assert len(active) == 1
        assert "curiosite" in active[0]["title"]
        assert active[0]["criteria"]["metric"] == "trait_value:curiosite"

    def test_auto_generate_unknown_pattern_ignored(self, engine):
        patterns = [{"type": "unknown_pattern", "severity": "low", "message": "?"}]
        engine.auto_generate_from_patterns(patterns)
        assert len(engine.get_active_objectives()) == 0


class TestEvaluateProgress:
    """Tests de l'évaluation de progression."""

    def test_evaluate_updates_current(self, engine):
        obj = engine.create_objective(
            title="Eval test", obj_type="performance", priority="high", source="user",
            criteria={"metric": "success_rate", "operator": ">=", "target": 0.8},
            routine_affinities={},
        )
        fake_snapshot = {
            "performance": {"success_rate": 0.6, "error_streak": 0,
                           "cloud_budget_used": 10, "cloud_budget_max": 100,
                           "council_consensus_rate": 0.5},
            "health": {"verdict": "GO"},
            "traits": {"average": {}},
        }
        with patch("core.self_awareness.awareness") as mock_aw:
            mock_aw.get_latest_snapshot.return_value = fake_snapshot
            engine.evaluate_progress()
        updated = engine._find_objective(obj["id"])
        assert updated["criteria"]["current"] == 0.6
        assert updated["progress"] == pytest.approx(0.75)

    def test_evaluate_auto_completes(self, engine):
        obj = engine.create_objective(
            title="Auto complete", obj_type="performance", priority="high", source="user",
            criteria={"metric": "success_rate", "operator": ">=", "target": 0.8},
            routine_affinities={},
        )
        fake_snapshot = {
            "performance": {"success_rate": 0.9, "error_streak": 0,
                           "cloud_budget_used": 10, "cloud_budget_max": 100,
                           "council_consensus_rate": 0.5},
            "health": {"verdict": "GO"},
            "traits": {"average": {}},
        }
        with patch("core.self_awareness.awareness") as mock_aw:
            mock_aw.get_latest_snapshot.return_value = fake_snapshot
            engine.evaluate_progress()
        assert engine._find_objective(obj["id"])["status"] == "completed"


class TestReadMetric:
    """Tests de lecture de métriques."""

    def test_read_success_rate(self, engine):
        snap = {"performance": {"success_rate": 0.75}, "health": {}, "traits": {"average": {}}}
        assert engine._read_metric("success_rate", snap) == 0.75

    def test_read_cloud_budget_ratio(self, engine):
        snap = {"performance": {"cloud_budget_used": 30, "cloud_budget_max": 100}, "health": {}, "traits": {"average": {}}}
        assert engine._read_metric("cloud_budget_ratio", snap) == 0.3

    def test_read_health_verdict(self, engine):
        snap = {"performance": {}, "health": {"verdict": "DEGRADED"}, "traits": {"average": {}}}
        assert engine._read_metric("health_verdict", snap) == "DEGRADED"

    def test_read_trait_value(self, engine):
        snap = {"performance": {}, "health": {}, "traits": {"average": {"curiosite": 62.5}}}
        assert engine._read_metric("trait_value:curiosite", snap) == 62.5

    def test_read_unknown_metric(self, engine):
        snap = {"performance": {}, "health": {}, "traits": {"average": {}}}
        assert engine._read_metric("inexistant", snap) is None

    def test_read_none_snapshot(self, engine):
        assert engine._read_metric("success_rate", None) is None


class TestPersistence:
    """Tests de la persistance JSON."""

    def test_save_and_load(self, engine):
        engine.create_objective(
            title="Persiste", obj_type="maintenance", priority="low", source="user",
            criteria={"metric": "error_streak", "operator": "<=", "target": 1},
            routine_affinities={"AUDIT_STRUCTURE": 0.5},
        )
        # Recréer le singleton → doit recharger
        ObjectivesEngine.reset_singleton()
        engine2 = ObjectivesEngine()
        all_objs = engine2.get_all_objectives()
        assert len(all_objs) == 1
        assert all_objs[0]["title"] == "Persiste"


class TestExtractTraitName:
    """Tests de l'extraction du nom de trait."""

    def test_extract_from_message(self):
        msg = "Trait 'curiosite' en baisse constante (60.0 → 45.2)."
        assert ObjectivesEngine._extract_trait_name(msg) == "curiosite"

    def test_extract_no_quotes(self):
        msg = "Trait en baisse"
        assert ObjectivesEngine._extract_trait_name(msg) is None
