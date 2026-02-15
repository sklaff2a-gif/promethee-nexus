# tests/test_self_awareness.py — Tests unitaires pour SelfAwarenessEngine

import os
import sys
import json
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.self_awareness import SelfAwarenessEngine, STATE_FILE, _compute_mood, awareness


class TestSelfAwarenessInit:
    """Tests d'initialisation et singleton."""

    def test_singleton(self):
        a = SelfAwarenessEngine()
        b = SelfAwarenessEngine()
        assert a is b

    def test_reset_singleton(self):
        a = SelfAwarenessEngine()
        SelfAwarenessEngine.reset_singleton()
        b = SelfAwarenessEngine()
        assert a is not b

    def test_init_subscribes_events(self):
        engine = SelfAwarenessEngine()
        engine.init()
        assert engine._subscribed is True

    def test_init_idempotent(self):
        engine = SelfAwarenessEngine()
        engine.init()
        engine.init()  # Pas d'erreur, pas de double souscription
        assert engine._subscribed is True


class TestMoodComputation:
    """Tests de l'humeur synthétique."""

    def test_mood_productif(self):
        assert _compute_mood(0.9, {"curiosite": 50}) == "productif"

    def test_mood_fatigue(self):
        assert _compute_mood(0.3, {"curiosite": 50}) == "fatigue"

    def test_mood_instable(self):
        assert _compute_mood(0.55, {"survie": 70}) == "instable"

    def test_mood_curieux(self):
        assert _compute_mood(0.75, {"curiosite": 70, "survie": 50}) == "curieux"

    def test_mood_creatif(self):
        assert _compute_mood(0.75, {"creativite": 70, "curiosite": 50}) == "créatif"

    def test_mood_prudent(self):
        assert _compute_mood(0.7, {"survie": 75, "curiosite": 50, "creativite": 50}) == "prudent"

    def test_mood_audacieux(self):
        assert _compute_mood(0.65, {"audace": 70, "survie": 50, "curiosite": 50, "creativite": 50}) == "audacieux"

    def test_mood_equilibre_fallback(self):
        assert _compute_mood(0.7, {"curiosite": 50, "creativite": 50, "survie": 50, "audace": 50}) == "équilibré"


class TestSnapshot:
    """Tests de génération de snapshot."""

    def test_generate_snapshot_basic(self):
        engine = SelfAwarenessEngine()
        snap = engine.generate_snapshot()
        assert "timestamp" in snap
        assert "traits" in snap
        assert "performance" in snap
        assert "health" in snap
        assert "mood" in snap
        assert "trend" in snap
        assert "knowledge" in snap

    def test_snapshot_default_mood(self):
        engine = SelfAwarenessEngine()
        snap = engine.generate_snapshot()
        # Sans missions, success_rate=1.0 → "productif"
        assert snap["mood"] == "productif"

    def test_snapshot_persisted(self):
        engine = SelfAwarenessEngine()
        engine.generate_snapshot()
        assert os.path.exists(STATE_FILE)

    def test_snapshot_fifo_max(self):
        engine = SelfAwarenessEngine()
        for _ in range(55):
            engine.generate_snapshot()
        assert len(engine._snapshots) == 50

    def test_get_latest_snapshot(self):
        engine = SelfAwarenessEngine()
        assert engine.get_latest_snapshot() is None
        snap = engine.generate_snapshot()
        assert engine.get_latest_snapshot() == snap

    def test_get_all_snapshots(self):
        engine = SelfAwarenessEngine()
        engine.generate_snapshot()
        engine.generate_snapshot()
        assert len(engine.get_all_snapshots()) == 2


class TestSelfContext:
    """Tests du contexte injectable."""

    def test_context_empty_without_snapshot(self):
        engine = SelfAwarenessEngine()
        assert engine.get_self_context() == ""

    def test_context_after_snapshot(self):
        engine = SelfAwarenessEngine()
        engine.generate_snapshot()
        ctx = engine.get_self_context()
        assert "[CONSCIENCE]" in ctx
        assert "Humeur:" in ctx
        assert "Perf:" in ctx
        assert "Sante:" in ctx

    def test_context_max_chars(self):
        engine = SelfAwarenessEngine()
        engine.generate_snapshot()
        ctx = engine.get_self_context(max_chars=100)
        assert len(ctx) <= 100


class TestPatternDetection:
    """Tests de la détection de patterns."""

    def test_no_patterns_empty(self):
        engine = SelfAwarenessEngine()
        assert engine.detect_patterns() == []

    def test_error_streak_pattern(self):
        engine = SelfAwarenessEngine()
        # Simuler un snapshot avec error_streak >= 3
        engine._snapshots.append({
            "traits": {"average": {}},
            "performance": {"error_streak": 4, "mission_count": 10, "success_rate": 0.7,
                            "cloud_budget_used": 5, "cloud_budget_max": 100,
                            "council_consensus_rate": 0.5},
            "health": {"verdict": "GO"},
        })
        patterns = engine.detect_patterns()
        types = [p["type"] for p in patterns]
        assert "error_streak" in types

    def test_low_success_rate_pattern(self):
        engine = SelfAwarenessEngine()
        engine._snapshots.append({
            "traits": {"average": {}},
            "performance": {"error_streak": 0, "mission_count": 10, "success_rate": 0.4,
                            "cloud_budget_used": 5, "cloud_budget_max": 100,
                            "council_consensus_rate": 0.5},
            "health": {"verdict": "GO"},
        })
        patterns = engine.detect_patterns()
        types = [p["type"] for p in patterns]
        assert "low_success_rate" in types

    def test_cloud_budget_critical_pattern(self):
        engine = SelfAwarenessEngine()
        engine._snapshots.append({
            "traits": {"average": {}},
            "performance": {"error_streak": 0, "mission_count": 10, "success_rate": 0.8,
                            "cloud_budget_used": 95, "cloud_budget_max": 100,
                            "council_consensus_rate": 0.5},
            "health": {"verdict": "GO"},
        })
        patterns = engine.detect_patterns()
        types = [p["type"] for p in patterns]
        assert "cloud_budget_critical" in types

    def test_health_degraded_pattern(self):
        engine = SelfAwarenessEngine()
        engine._snapshots.append({
            "traits": {"average": {}},
            "performance": {"error_streak": 0, "mission_count": 1, "success_rate": 0.8,
                            "cloud_budget_used": 5, "cloud_budget_max": 100,
                            "council_consensus_rate": 0.5},
            "health": {"verdict": "NO_GO"},
        })
        patterns = engine.detect_patterns()
        types = [p["type"] for p in patterns]
        assert "health_degraded" in types

    def test_low_consensus_pattern(self):
        engine = SelfAwarenessEngine()
        engine._council_count = 5
        engine._council_consensus = 1
        engine._snapshots.append({
            "traits": {"average": {}},
            "performance": {"error_streak": 0, "mission_count": 1, "success_rate": 0.8,
                            "cloud_budget_used": 5, "cloud_budget_max": 100,
                            "council_consensus_rate": 0.2},
            "health": {"verdict": "GO"},
        })
        patterns = engine.detect_patterns()
        types = [p["type"] for p in patterns]
        assert "low_consensus" in types

    def test_trait_rising_pattern(self):
        engine = SelfAwarenessEngine()
        for val in [50.0, 52.0, 55.0]:
            engine._snapshots.append({
                "traits": {"average": {"curiosite": val, "creativite": 50, "audace": 50,
                                       "savoir": 50, "survie": 50, "respect": 50}},
                "performance": {"error_streak": 0, "mission_count": 1, "success_rate": 0.8,
                                "cloud_budget_used": 5, "cloud_budget_max": 100,
                                "council_consensus_rate": 0.5},
                "health": {"verdict": "GO"},
            })
        patterns = engine.detect_patterns()
        types = [p["type"] for p in patterns]
        assert "trait_rising" in types

    def test_high_success_rate_pattern(self):
        engine = SelfAwarenessEngine()
        engine._snapshots.append({
            "traits": {"average": {}},
            "performance": {"error_streak": 0, "mission_count": 15, "success_rate": 0.9,
                            "cloud_budget_used": 5, "cloud_budget_max": 100,
                            "council_consensus_rate": 0.5},
            "health": {"verdict": "GO"},
        })
        patterns = engine.detect_patterns()
        types = [p["type"] for p in patterns]
        assert "high_success_rate" in types

    def test_high_success_rate_not_triggered_below_threshold(self):
        engine = SelfAwarenessEngine()
        engine._snapshots.append({
            "traits": {"average": {}},
            "performance": {"error_streak": 0, "mission_count": 15, "success_rate": 0.8,
                            "cloud_budget_used": 5, "cloud_budget_max": 100,
                            "council_consensus_rate": 0.5},
            "health": {"verdict": "GO"},
        })
        patterns = engine.detect_patterns()
        types = [p["type"] for p in patterns]
        assert "high_success_rate" not in types

    def test_high_success_rate_requires_min_missions(self):
        engine = SelfAwarenessEngine()
        engine._snapshots.append({
            "traits": {"average": {}},
            "performance": {"error_streak": 0, "mission_count": 5, "success_rate": 0.95,
                            "cloud_budget_used": 5, "cloud_budget_max": 100,
                            "council_consensus_rate": 0.5},
            "health": {"verdict": "GO"},
        })
        patterns = engine.detect_patterns()
        types = [p["type"] for p in patterns]
        assert "high_success_rate" not in types


class TestEventHandlers:
    """Tests des handlers d'événements bus."""

    @pytest.mark.asyncio
    async def test_on_agent_response_success(self):
        engine = SelfAwarenessEngine()
        await engine._on_agent_response({"status": "success"})
        assert engine._mission_count == 1
        assert engine._mission_success == 1

    @pytest.mark.asyncio
    async def test_on_agent_response_failure(self):
        engine = SelfAwarenessEngine()
        await engine._on_agent_response({"status": "error"})
        assert engine._mission_count == 1
        assert engine._mission_success == 0

    @pytest.mark.asyncio
    async def test_on_council_end_consensus(self):
        engine = SelfAwarenessEngine()
        await engine._on_council_end({"status": "consensus"})
        assert engine._council_count == 1
        assert engine._council_consensus == 1

    @pytest.mark.asyncio
    async def test_on_ci_result(self):
        engine = SelfAwarenessEngine()
        await engine._on_ci_result({"success": True})
        assert engine._ci_pass == 1
        await engine._on_ci_result({"success": False})
        assert engine._ci_fail == 1


class TestPersistence:
    """Tests de persistance."""

    def test_save_and_load(self):
        engine = SelfAwarenessEngine()
        engine._mission_count = 10
        engine._mission_success = 8
        engine.generate_snapshot()

        # Reset et recharger
        SelfAwarenessEngine.reset_singleton()
        engine2 = SelfAwarenessEngine()
        assert engine2._mission_count == 10
        assert engine2._mission_success == 8
        assert len(engine2._snapshots) == 1


class TestTrend:
    """Tests du calcul de tendance."""

    def test_trend_initial(self):
        engine = SelfAwarenessEngine()
        trend = engine._compute_trend({"curiosite": 55})
        assert trend["status"] == "initial"

    def test_trend_stable(self):
        engine = SelfAwarenessEngine()
        engine._snapshots.append({
            "traits": {"average": {"curiosite": 55.0}},
        })
        trend = engine._compute_trend({"curiosite": 55.0})
        assert trend["status"] == "stable"

    def test_trend_shifting(self):
        engine = SelfAwarenessEngine()
        engine._snapshots.append({
            "traits": {"average": {"curiosite": 50.0}},
        })
        trend = engine._compute_trend({"curiosite": 55.0})
        assert trend["status"] == "shifting"
        assert "curiosite" in trend["rising"]
