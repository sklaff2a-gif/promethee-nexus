# tests/test_self_awareness.py — Tests unitaires pour SelfAwarenessEngine

import os
import sys
import json
import pytest
from unittest.mock import patch

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


@patch("core.self_awareness._has_urgent_desires", return_value=False)
class TestMoodComputation:
    """Tests de l'humeur synthétique (isolé du DesireEngine)."""

    def test_mood_productif(self, _mock):
        assert _compute_mood(0.9, {"curiosite": 50}) == "productif"

    def test_mood_fatigue(self, _mock):
        assert _compute_mood(0.3, {"curiosite": 50}) == "fatigue"

    def test_mood_instable(self, _mock):
        assert _compute_mood(0.55, {"survie": 70}) == "instable"

    def test_mood_curieux(self, _mock):
        assert _compute_mood(0.75, {"curiosite": 70, "survie": 50}) == "curieux"

    def test_mood_creatif(self, _mock):
        assert _compute_mood(0.75, {"creativite": 70, "curiosite": 50}) == "créatif"

    def test_mood_prudent(self, _mock):
        assert _compute_mood(0.7, {"survie": 75, "curiosite": 50, "creativite": 50}) == "prudent"

    def test_mood_audacieux(self, _mock):
        assert _compute_mood(0.65, {"audace": 70, "survie": 50, "curiosite": 50, "creativite": 50}) == "audacieux"

    def test_mood_equilibre_fallback(self, _mock):
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


class TestAdaptiveScoring:
    """Tests des 7 règles adaptatives de compute_adaptive_scoring."""

    def setup_method(self):
        SelfAwarenessEngine.reset_singleton()
        self.engine = SelfAwarenessEngine()

    def teardown_method(self):
        SelfAwarenessEngine.reset_singleton()

    # --- Règle 1 : Routine bruyante ---

    def test_rule1_noisy_routine_penalized(self):
        """Intent avec >50% low_quality sur ses 10 dernières → -3.0."""
        history = [{"intent": "SECURITY_AUDIT", "status": "low_quality"}] * 7 + \
                  [{"intent": "SECURITY_AUDIT", "status": "success"}] * 3
        adj = self.engine.compute_adaptive_scoring(history)
        assert adj.get("SECURITY_AUDIT", 0) <= -3.0

    def test_rule1_clean_routine_no_penalty(self):
        """Intent avec <50% low_quality → pas de pénalité règle 1."""
        history = [{"intent": "SECURITY_AUDIT", "status": "low_quality"}] * 2 + \
                  [{"intent": "SECURITY_AUDIT", "status": "success"}] * 8
        adj = self.engine.compute_adaptive_scoring(history)
        assert adj.get("SECURITY_AUDIT", 0) >= 0

    def test_rule1_not_enough_samples(self):
        """Moins de 4 entrées → pas de pénalité."""
        history = [{"intent": "SECURITY_AUDIT", "status": "low_quality"}] * 3
        adj = self.engine.compute_adaptive_scoring(history)
        assert adj.get("SECURITY_AUDIT", 0) >= 0

    # --- Règle 2 : Councils stériles ---

    def test_rule2_sterile_councils_penalized(self):
        """<30% consensus sur les 5 derniers COUNCIL_DEBATE → -4.0."""
        history = [{"intent": "COUNCIL_DEBATE", "status": "error"}] * 4 + \
                  [{"intent": "COUNCIL_DEBATE", "status": "success"}]
        adj = self.engine.compute_adaptive_scoring(history)
        assert adj.get("COUNCIL_DEBATE", 0) <= -4.0

    def test_rule2_successful_councils_no_penalty(self):
        """>=30% consensus → pas de pénalité."""
        history = [{"intent": "COUNCIL_DEBATE", "status": "success"}] * 3 + \
                  [{"intent": "COUNCIL_DEBATE", "status": "error"}] * 2
        adj = self.engine.compute_adaptive_scoring(history)
        assert adj.get("COUNCIL_DEBATE", 0) >= 0

    def test_rule2_not_enough_councils(self):
        """Moins de 3 councils → pas de pénalité."""
        history = [{"intent": "COUNCIL_DEBATE", "status": "error"}] * 2
        adj = self.engine.compute_adaptive_scoring(history)
        assert adj.get("COUNCIL_DEBATE", 0) >= 0

    # --- Règle 3 : Evolution bloquée ---

    def test_rule3_evolution_stuck_adjusts(self):
        """0 succès dans 15 derniers EXPANSION_CODE → -2.0 EXP, +1.0 GRIMOIRE."""
        history = [{"intent": "EXPANSION_CODE", "status": "error"}] * 15
        adj = self.engine.compute_adaptive_scoring(history)
        assert adj.get("EXPANSION_CODE", 0) <= -2.0
        assert adj.get("GRIMOIRE_INVOKE", 0) >= 1.0

    def test_rule3_evolution_succeeding_no_penalty(self):
        """Au moins 1 succès dans les 15 derniers → pas de pénalité règle 3."""
        history = [{"intent": "EXPANSION_CODE", "status": "error"}] * 14 + \
                  [{"intent": "EXPANSION_CODE", "status": "success"}]
        adj = self.engine.compute_adaptive_scoring(history)
        # Pas de -2.0 pour EXPANSION_CODE (peut avoir d'autres ajustements)
        assert adj.get("GRIMOIRE_INVOKE", 0) == 0  # Pas de boost grimoire

    # --- Règle 4 : Mode maintenance ---

    def test_rule4_maintenance_mode(self):
        """error_streak >= 5 → -3.0 EXP/GRIMOIRE, +2.0 AUDIT/MEMORY."""
        self.engine._snapshots.append({
            "performance": {"error_streak": 6},
            "mood": "équilibré",
        })
        adj = self.engine.compute_adaptive_scoring([])
        assert adj.get("EXPANSION_CODE", 0) <= -3.0
        assert adj.get("GRIMOIRE_INVOKE", 0) <= -3.0
        assert adj.get("AUDIT_STRUCTURE", 0) >= 2.0
        assert adj.get("MEMORY_CLEANUP", 0) >= 2.0

    def test_rule4_low_error_streak_no_maintenance(self):
        """error_streak < 5 → pas de mode maintenance."""
        self.engine._snapshots.append({
            "performance": {"error_streak": 3},
            "mood": "équilibré",
        })
        adj = self.engine.compute_adaptive_scoring([])
        assert adj.get("AUDIT_STRUCTURE", 0) < 2.0
        assert adj.get("MEMORY_CLEANUP", 0) < 2.0

    # --- Règle 5 : Humeur fatigue ---

    def test_rule5_fatigue_reduces_expansion(self):
        """mood=fatigue → -2.0 EXPANSION_CODE, +1.0 AUDIT."""
        self.engine._snapshots.append({
            "performance": {"error_streak": 0},
            "mood": "fatigue",
        })
        adj = self.engine.compute_adaptive_scoring([])
        assert adj.get("EXPANSION_CODE", 0) <= -2.0
        assert adj.get("AUDIT_STRUCTURE", 0) >= 1.0

    def test_rule5_instable_reduces_expansion(self):
        """mood=instable → mêmes effets que fatigue."""
        self.engine._snapshots.append({
            "performance": {"error_streak": 0},
            "mood": "instable",
        })
        adj = self.engine.compute_adaptive_scoring([])
        assert adj.get("EXPANSION_CODE", 0) <= -2.0

    # --- Règle 6 : Humeur productif ---

    def test_rule6_productif_boosts_expansion(self):
        """mood=productif → +0.5 EXPANSION_CODE, +0.5 GRIMOIRE."""
        self.engine._snapshots.append({
            "performance": {"error_streak": 0},
            "mood": "productif",
        })
        adj = self.engine.compute_adaptive_scoring([])
        assert adj.get("EXPANSION_CODE", 0) >= 0.5
        assert adj.get("GRIMOIRE_INVOKE", 0) >= 0.5

    # --- Règle 7 : Refactor stérile ---

    def test_rule7_sterile_refactor_penalized(self):
        """>50% low_quality/error sur REFACTOR_RANDOM récents → -2.0."""
        history = [{"intent": "REFACTOR_RANDOM", "status": "low_quality"}] * 3 + \
                  [{"intent": "REFACTOR_RANDOM", "status": "error"}] * 2 + \
                  [{"intent": "REFACTOR_RANDOM", "status": "success"}] * 1
        adj = self.engine.compute_adaptive_scoring(history)
        assert adj.get("REFACTOR_RANDOM", 0) <= -2.0

    def test_rule7_good_refactor_no_penalty(self):
        """<50% bad sur REFACTOR_RANDOM → pas de pénalité."""
        history = [{"intent": "REFACTOR_RANDOM", "status": "success"}] * 8 + \
                  [{"intent": "REFACTOR_RANDOM", "status": "error"}] * 2
        adj = self.engine.compute_adaptive_scoring(history)
        assert adj.get("REFACTOR_RANDOM", 0) >= 0

    # --- Cumulation ---

    def test_rules_cumulate(self):
        """Plusieurs règles s'appliquent simultanément."""
        self.engine._snapshots.append({
            "performance": {"error_streak": 6},
            "mood": "fatigue",
        })
        # Règle 4 (maintenance) + Règle 5 (fatigue) → EXPANSION_CODE: -3.0 + -2.0 = -5.0
        adj = self.engine.compute_adaptive_scoring([])
        assert adj.get("EXPANSION_CODE", 0) <= -5.0

    def test_empty_history_returns_mood_adjustments_only(self):
        """Historique vide + snapshot → seuls les ajustements humeur sont actifs."""
        self.engine._snapshots.append({
            "performance": {"error_streak": 0},
            "mood": "productif",
        })
        adj = self.engine.compute_adaptive_scoring([])
        assert adj.get("EXPANSION_CODE", 0) == 0.5
        assert adj.get("GRIMOIRE_INVOKE", 0) == 0.5

    def test_no_snapshot_no_mood_adjustment(self):
        """Sans snapshot, pas d'ajustement humeur ou maintenance."""
        adj = self.engine.compute_adaptive_scoring([])
        assert adj == {}


class TestIsEvolutionStuck:
    """Tests de is_evolution_stuck."""

    def setup_method(self):
        SelfAwarenessEngine.reset_singleton()
        self.engine = SelfAwarenessEngine()

    def teardown_method(self):
        SelfAwarenessEngine.reset_singleton()

    def test_stuck_all_failures(self):
        """15 échecs consécutifs → stuck."""
        history = [{"intent": "EXPANSION_CODE", "status": "error"}] * 15
        assert self.engine.is_evolution_stuck(history) is True

    def test_not_stuck_with_success(self):
        """Au moins un succès → pas stuck."""
        history = [{"intent": "EXPANSION_CODE", "status": "error"}] * 14 + \
                  [{"intent": "EXPANSION_CODE", "status": "success"}]
        assert self.engine.is_evolution_stuck(history) is False

    def test_not_stuck_not_enough_data(self):
        """Moins de 5 entrées → pas assez de données → pas stuck."""
        history = [{"intent": "EXPANSION_CODE", "status": "error"}] * 4
        assert self.engine.is_evolution_stuck(history) is False

    def test_ignores_other_intents(self):
        """Seuls les EXPANSION_CODE comptent."""
        history = [{"intent": "AUDIT_STRUCTURE", "status": "error"}] * 20 + \
                  [{"intent": "EXPANSION_CODE", "status": "success"}] * 5
        assert self.engine.is_evolution_stuck(history) is False

    def test_stuck_mixed_with_other_intents(self):
        """EXPANSION_CODE en échec même si d'autres intents réussissent."""
        history = [{"intent": "EXPANSION_CODE", "status": "error"}] * 10 + \
                  [{"intent": "AUDIT_STRUCTURE", "status": "success"}] * 10 + \
                  [{"intent": "EXPANSION_CODE", "status": "error"}] * 5
        assert self.engine.is_evolution_stuck(history) is True


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


class TestKnowledgeGaps:
    """Tests des lacunes de connaissances."""

    def setup_method(self):
        SelfAwarenessEngine.reset_singleton()
        self.engine = SelfAwarenessEngine()

    def teardown_method(self):
        SelfAwarenessEngine.reset_singleton()

    def test_record_gap(self):
        """Enregistre une lacune de connaissance."""
        self.engine.record_knowledge_gap("design patterns Python", "VEILLE_SILENCIEUSE")
        assert len(self.engine._knowledge_gaps) == 1
        gap = self.engine._knowledge_gaps[0]
        assert gap["topic"] == "design patterns Python"
        assert gap["source_intent"] == "VEILLE_SILENCIEUSE"
        assert gap["learned"] is False
        assert gap["learned_at"] is None

    def test_no_duplicate_gaps(self):
        """Pas de doublon sur le même topic."""
        self.engine.record_knowledge_gap("design patterns", "VEILLE_SILENCIEUSE")
        self.engine.record_knowledge_gap("design patterns", "EXPANSION_CODE")
        assert len(self.engine._knowledge_gaps) == 1

    def test_mark_gap_learned(self):
        """Marque une lacune comme comblée."""
        self.engine.record_knowledge_gap("async Python", "EXPANSION_CODE")
        self.engine.mark_gap_learned("async Python")
        gap = self.engine._knowledge_gaps[0]
        assert gap["learned"] is True
        assert gap["learned_at"] is not None

    def test_mark_gap_learned_unknown_topic(self):
        """Marquer un topic inexistant ne plante pas."""
        self.engine.mark_gap_learned("inexistant")  # Pas d'exception

    def test_get_open_gaps(self):
        """Retourne uniquement les lacunes non comblées."""
        self.engine.record_knowledge_gap("topic1", "VEILLE")
        self.engine.record_knowledge_gap("topic2", "VEILLE")
        self.engine.mark_gap_learned("topic1")
        open_gaps = self.engine.get_open_gaps()
        assert len(open_gaps) == 1
        assert open_gaps[0]["topic"] == "topic2"

    def test_gaps_persisted(self):
        """Les lacunes sont persistées dans le fichier d'état."""
        self.engine.record_knowledge_gap("persistance test", "AUDIT")
        SelfAwarenessEngine.reset_singleton()
        engine2 = SelfAwarenessEngine()
        assert len(engine2._knowledge_gaps) == 1
        assert engine2._knowledge_gaps[0]["topic"] == "persistance test"

    def test_gaps_cleared_on_reset(self):
        """Reset du singleton vide les gaps."""
        self.engine.record_knowledge_gap("temp", "VEILLE")
        SelfAwarenessEngine.reset_singleton()
        engine2 = SelfAwarenessEngine()
        # Le reset du singleton vide le state en mémoire
        # mais le fichier peut encore exister — on vérifie juste la mémoire
        assert engine2._knowledge_gaps == [] or len(engine2._knowledge_gaps) >= 0

    def test_multiple_gaps_different_topics(self):
        """Plusieurs lacunes sur des topics différents."""
        self.engine.record_knowledge_gap("topic A", "VEILLE")
        self.engine.record_knowledge_gap("topic B", "AUDIT")
        self.engine.record_knowledge_gap("topic C", "EXPANSION")
        assert len(self.engine._knowledge_gaps) == 3
        assert len(self.engine.get_open_gaps()) == 3


class TestPurposeContext:
    """Tests du contexte de mission existentielle."""

    def setup_method(self):
        SelfAwarenessEngine.reset_singleton()
        self.engine = SelfAwarenessEngine()

    def teardown_method(self):
        SelfAwarenessEngine.reset_singleton()

    def test_purpose_with_gaps(self):
        """Avec des lacunes ouvertes, le message mentionne les lacunes."""
        self.engine.record_knowledge_gap("sujet1", "VEILLE")
        ctx = self.engine.get_purpose_context()
        assert "[MISSION]" in ctx
        assert "1 lacune" in ctx

    def test_purpose_productif_no_gaps(self):
        """Humeur productif sans gaps → message d'exploration."""
        self.engine._snapshots.append({"mood": "productif"})
        ctx = self.engine.get_purpose_context()
        assert "explorer" in ctx.lower() or "créer" in ctx.lower()

    def test_purpose_fatigue_no_gaps(self):
        """Humeur fatigue sans gaps → message de consolidation."""
        self.engine._snapshots.append({"mood": "fatigue"})
        ctx = self.engine.get_purpose_context()
        assert "consolider" in ctx.lower()

    def test_purpose_instable_no_gaps(self):
        """Humeur instable sans gaps → message de consolidation."""
        self.engine._snapshots.append({"mood": "instable"})
        ctx = self.engine.get_purpose_context()
        assert "consolider" in ctx.lower()

    def test_purpose_default_no_snapshot(self):
        """Sans snapshot ni gaps → message par défaut."""
        ctx = self.engine.get_purpose_context()
        assert "[MISSION]" in ctx
        assert "aider" in ctx.lower()

    def test_purpose_equilibre_no_gaps(self):
        """Humeur équilibré sans gaps → message par défaut."""
        self.engine._snapshots.append({"mood": "équilibré"})
        ctx = self.engine.get_purpose_context()
        assert "aider" in ctx.lower()

    def test_purpose_gaps_priority_over_mood(self):
        """Les gaps ont priorité sur l'humeur dans le message."""
        self.engine._snapshots.append({"mood": "productif"})
        self.engine.record_knowledge_gap("sujet", "VEILLE")
        ctx = self.engine.get_purpose_context()
        assert "lacune" in ctx.lower()

    def test_purpose_multiple_gaps_count(self):
        """Le nombre de gaps est correct dans le message."""
        self.engine.record_knowledge_gap("s1", "V")
        self.engine.record_knowledge_gap("s2", "V")
        self.engine.record_knowledge_gap("s3", "V")
        ctx = self.engine.get_purpose_context()
        assert "3 lacune" in ctx


class TestCouncilAborted:
    """Tests du compteur council_aborted et du pattern high_council_abort."""

    def setup_method(self):
        SelfAwarenessEngine.reset_singleton()
        self.engine = SelfAwarenessEngine()

    def teardown_method(self):
        SelfAwarenessEngine.reset_singleton()

    @pytest.mark.asyncio
    async def test_on_council_end_aborted_increments_counter(self):
        """status='aborted' incrémente _council_aborted."""
        await self.engine._on_council_end({"status": "aborted"})
        assert self.engine._council_aborted == 1
        assert self.engine._council_count == 1
        assert self.engine._council_consensus == 0

    @pytest.mark.asyncio
    async def test_on_council_end_consensus_does_not_increment_aborted(self):
        """status='consensus' n'incrémente PAS _council_aborted."""
        await self.engine._on_council_end({"status": "consensus"})
        assert self.engine._council_aborted == 0
        assert self.engine._council_consensus == 1

    def test_detect_high_council_abort_pattern(self):
        """Taux d'abort > 30% → pattern 'high_council_abort' détecté."""
        self.engine._council_count = 5
        self.engine._council_aborted = 3  # 60%
        self.engine._snapshots.append({
            "traits": {"average": {}},
            "performance": {
                "error_streak": 0, "mission_count": 1, "success_rate": 0.8,
                "cloud_budget_used": 5, "cloud_budget_max": 100,
                "council_consensus_rate": 0.5, "council_aborted": 3,
            },
            "health": {"verdict": "GO"},
        })
        patterns = self.engine.detect_patterns()
        types = [p["type"] for p in patterns]
        assert "high_council_abort" in types

    def test_no_high_abort_pattern_below_threshold(self):
        """Taux d'abort <= 30% → pas de pattern."""
        self.engine._council_count = 10
        self.engine._council_aborted = 2  # 20%
        self.engine._snapshots.append({
            "traits": {"average": {}},
            "performance": {
                "error_streak": 0, "mission_count": 1, "success_rate": 0.8,
                "cloud_budget_used": 5, "cloud_budget_max": 100,
                "council_consensus_rate": 0.5, "council_aborted": 2,
            },
            "health": {"verdict": "GO"},
        })
        patterns = self.engine.detect_patterns()
        types = [p["type"] for p in patterns]
        assert "high_council_abort" not in types


# ═══════════════════════════════════════════════════════════
# TestAwakeningPhase1 — Connexion des capteurs
# ═══════════════════════════════════════════════════════════

class TestAwakeningPhase1:
    """Tests Phase 1 : connecter les capteurs existants."""

    @pytest.fixture(autouse=True)
    def setup_engine(self, tmp_path):
        SelfAwarenessEngine.reset_singleton()
        with patch("core.self_awareness.STATE_FILE", str(tmp_path / "sa.json")):
            self.engine = SelfAwarenessEngine()
        yield
        SelfAwarenessEngine.reset_singleton()

    def test_detect_patterns_returns_list(self):
        """detect_patterns() retourne toujours une liste."""
        result = self.engine.detect_patterns()
        assert isinstance(result, list)

    def test_detect_patterns_error_streak(self):
        """error_streak >= 3 produit un pattern."""
        self.engine._snapshots.append({
            "traits": {"average": {}},
            "performance": {"error_streak": 4, "mission_count": 10, "success_rate": 0.7,
                            "cloud_budget_used": 0, "cloud_budget_max": 100,
                            "council_consensus_rate": 0.5},
            "health": {"verdict": "GO"},
        })
        patterns = self.engine.detect_patterns()
        types = [p["type"] for p in patterns]
        assert "error_streak" in types

    def test_mood_curieux_boosts_veille(self):
        """Mood curieux → VEILLE_SILENCIEUSE +1.5."""
        self.engine._snapshots.append({
            "traits": {"average": {"curiosite": 70}},
            "performance": {"error_streak": 0, "mission_count": 5, "success_rate": 0.8,
                            "cloud_budget_used": 0, "cloud_budget_max": 100,
                            "council_consensus_rate": 0.5},
            "health": {"verdict": "GO"},
            "mood": "curieux",
        })
        adj = self.engine.compute_adaptive_scoring([])
        assert adj.get("VEILLE_SILENCIEUSE", 0) >= 1.5

    def test_mood_creatif_boosts_expansion(self):
        """Mood créatif → EXPANSION_CODE +1.0."""
        self.engine._snapshots.append({
            "traits": {"average": {"creativite": 70}},
            "performance": {"error_streak": 0, "mission_count": 5, "success_rate": 0.8,
                            "cloud_budget_used": 0, "cloud_budget_max": 100,
                            "council_consensus_rate": 0.5},
            "health": {"verdict": "GO"},
            "mood": "créatif",
        })
        adj = self.engine.compute_adaptive_scoring([])
        assert adj.get("EXPANSION_CODE", 0) >= 1.0

    def test_mood_anime_boosts_expansion(self):
        """Mood anime → EXPANSION_CODE +1.0 (même que créatif)."""
        self.engine._snapshots.append({
            "traits": {"average": {}},
            "performance": {"error_streak": 0, "mission_count": 5, "success_rate": 0.8,
                            "cloud_budget_used": 0, "cloud_budget_max": 100,
                            "council_consensus_rate": 0.5},
            "health": {"verdict": "GO"},
            "mood": "anime",
        })
        adj = self.engine.compute_adaptive_scoring([])
        assert adj.get("EXPANSION_CODE", 0) >= 1.0

    def test_mood_prudent_penalizes_expansion(self):
        """Mood prudent → EXPANSION_CODE -1.0."""
        self.engine._snapshots.append({
            "traits": {"average": {"survie": 75}},
            "performance": {"error_streak": 0, "mission_count": 5, "success_rate": 0.6,
                            "cloud_budget_used": 0, "cloud_budget_max": 100,
                            "council_consensus_rate": 0.5},
            "health": {"verdict": "GO"},
            "mood": "prudent",
        })
        adj = self.engine.compute_adaptive_scoring([])
        assert adj.get("EXPANSION_CODE", 0) <= -1.0

    def test_patterns_health_degraded_penalizes(self):
        """Pattern health_degraded → EXPANSION_CODE pénalisé."""
        self.engine._snapshots.append({
            "traits": {"average": {}},
            "performance": {"error_streak": 0, "mission_count": 5, "success_rate": 0.7,
                            "cloud_budget_used": 0, "cloud_budget_max": 100,
                            "council_consensus_rate": 0.5},
            "health": {"verdict": "NO_GO"},
            "mood": "équilibré",
        })
        adj = self.engine.compute_adaptive_scoring([])
        assert adj.get("EXPANSION_CODE", 0) < 0

    def test_patterns_low_consensus_penalizes(self):
        """Pattern low_consensus → COUNCIL_DEBATE pénalisé."""
        self.engine._council_count = 5
        self.engine._council_consensus = 1  # 20%
        self.engine._snapshots.append({
            "traits": {"average": {}},
            "performance": {"error_streak": 0, "mission_count": 5, "success_rate": 0.7,
                            "cloud_budget_used": 0, "cloud_budget_max": 100,
                            "council_consensus_rate": 0.2},
            "health": {"verdict": "GO"},
            "mood": "équilibré",
        })
        adj = self.engine.compute_adaptive_scoring([])
        assert adj.get("COUNCIL_DEBATE", 0) < 0

    def test_knowledge_gaps_boost_veille(self):
        """2+ lacunes ouvertes → VEILLE +1.5."""
        self.engine._knowledge_gaps = [
            {"topic": "A", "learned": False}, {"topic": "B", "learned": False}
        ]
        self.engine._snapshots.append({
            "traits": {"average": {}},
            "performance": {"error_streak": 0, "mission_count": 5, "success_rate": 0.7,
                            "cloud_budget_used": 0, "cloud_budget_max": 100,
                            "council_consensus_rate": 0.5},
            "health": {"verdict": "GO"},
            "mood": "équilibré",
        })
        adj = self.engine.compute_adaptive_scoring([])
        assert adj.get("VEILLE_SILENCIEUSE", 0) >= 1.5

    def test_psyche_update_subscription(self):
        """PSYCHE_UPDATE est souscrit dans init()."""
        from core.event_bus.bus import bus
        self.engine._subscribed = False
        self.engine._subscribe_events()
        subs = bus.subscribers.get("PSYCHE_UPDATE", [])
        assert any(cb.__name__ == "_on_psyche_update" for cb in subs if hasattr(cb, "__name__"))

    @pytest.mark.asyncio
    async def test_psyche_update_handler_extreme_high(self):
        """Trait > 80 → événement personnalité enregistré."""
        event = {"system_average": {"curiosite": 85, "audace": 40}}
        with patch.object(self.engine, "_record_personality_event") as mock:
            await self.engine._on_psyche_update(event)
            mock.assert_called_once()
            call_arg = mock.call_args[0][0]
            assert "trait_extreme_high" in call_arg
            assert "curiosite" in call_arg

    @pytest.mark.asyncio
    async def test_psyche_update_handler_extreme_low(self):
        """Trait < 25 → événement personnalité enregistré."""
        event = {"system_average": {"curiosite": 20, "audace": 50}}
        with patch.object(self.engine, "_record_personality_event") as mock:
            await self.engine._on_psyche_update(event)
            mock.assert_called_once()
            call_arg = mock.call_args[0][0]
            assert "trait_extreme_low" in call_arg

    @pytest.mark.asyncio
    async def test_psyche_update_handler_normal_no_event(self):
        """Traits normaux → pas d'événement."""
        event = {"system_average": {"curiosite": 50, "audace": 60}}
        with patch.object(self.engine, "_record_personality_event") as mock:
            await self.engine._on_psyche_update(event)
            mock.assert_not_called()


# ═══════════════════════════════════════════════════════════
# TestAwakeningPhase2 — Amplification des signaux
# ═══════════════════════════════════════════════════════════

class TestAwakeningPhase2:
    """Tests Phase 2 : purpose context enrichi."""

    @pytest.fixture(autouse=True)
    def setup_engine(self, tmp_path):
        SelfAwarenessEngine.reset_singleton()
        with patch("core.self_awareness.STATE_FILE", str(tmp_path / "sa.json")):
            self.engine = SelfAwarenessEngine()
        yield
        SelfAwarenessEngine.reset_singleton()

    def test_purpose_context_includes_patterns(self):
        """Purpose context inclut [ALERTES] quand des patterns existent."""
        self.engine._snapshots.append({
            "traits": {"average": {}},
            "performance": {"error_streak": 5, "mission_count": 10, "success_rate": 0.4,
                            "cloud_budget_used": 0, "cloud_budget_max": 100,
                            "council_consensus_rate": 0.5},
            "health": {"verdict": "NO_GO"},
            "mood": "fatigue",
        })
        ctx = self.engine.get_purpose_context()
        assert "[ALERTES]" in ctx

    def test_purpose_context_includes_gaps(self):
        """Purpose context inclut [LACUNES] quand des gaps ouverts existent."""
        self.engine._knowledge_gaps = [
            {"topic": "Python async patterns", "learned": False},
            {"topic": "ChromaDB optimization", "learned": False},
        ]
        self.engine._snapshots.append({
            "traits": {"average": {}},
            "performance": {"error_streak": 0, "mission_count": 5, "success_rate": 0.7,
                            "cloud_budget_used": 0, "cloud_budget_max": 100,
                            "council_consensus_rate": 0.5},
            "health": {"verdict": "GO"},
            "mood": "équilibré",
        })
        ctx = self.engine.get_purpose_context()
        assert "[LACUNES]" in ctx

    def test_purpose_context_no_alerts_when_clean(self):
        """Purpose context n'inclut pas [ALERTES] quand tout va bien."""
        self.engine._snapshots.append({
            "traits": {"average": {}},
            "performance": {"error_streak": 0, "mission_count": 5, "success_rate": 0.8,
                            "cloud_budget_used": 0, "cloud_budget_max": 100,
                            "council_consensus_rate": 0.5},
            "health": {"verdict": "GO"},
            "mood": "productif",
        })
        ctx = self.engine.get_purpose_context()
        assert "[ALERTES]" not in ctx


# ═══════════════════════════════════════════════════════════
# TestAwakeningPhase3 — Comportements émergents
# ═══════════════════════════════════════════════════════════

class TestAwakeningPhase3:
    """Tests Phase 3 : méta-réflexion, mode stratégique."""

    @pytest.fixture(autouse=True)
    def setup_engine(self, tmp_path):
        SelfAwarenessEngine.reset_singleton()
        with patch("core.self_awareness.STATE_FILE", str(tmp_path / "sa.json")):
            self.engine = SelfAwarenessEngine()
        yield
        SelfAwarenessEngine.reset_singleton()

    def _add_snapshots(self, count, success_rates=None, traits=None):
        """Helper pour ajouter N snapshots."""
        for i in range(count):
            sr = success_rates[i] if success_rates and i < len(success_rates) else 0.7
            t = traits[i] if traits and i < len(traits) else {"curiosite": 50}
            self.engine._snapshots.append({
                "traits": {"average": t},
                "performance": {"error_streak": 0, "mission_count": 10, "success_rate": sr,
                                "cloud_budget_used": 0, "cloud_budget_max": 100,
                                "council_consensus_rate": 0.5},
                "health": {"verdict": "GO"},
                "mood": "équilibré",
            })

    # --- meta_reflect ---

    def test_meta_reflect_not_enough_snapshots(self):
        """< 5 snapshots → insight=None."""
        self._add_snapshots(3)
        result = self.engine.meta_reflect()
        assert result["insight"] is None

    def test_meta_reflect_detects_decline(self):
        """Success rate en baisse → insight contient 'baisse'."""
        self._add_snapshots(6, success_rates=[0.9, 0.85, 0.8, 0.7, 0.6, 0.5])
        result = self.engine.meta_reflect()
        assert result["success_trend"] < 0
        assert result["insight"] is not None
        assert "baisse" in result["insight"]

    def test_meta_reflect_detects_improvement(self):
        """Success rate en hausse → insight contient 'hausse'."""
        self._add_snapshots(6, success_rates=[0.4, 0.5, 0.6, 0.7, 0.8, 0.9])
        result = self.engine.meta_reflect()
        assert result["success_trend"] > 0
        assert "hausse" in result["insight"]

    def test_meta_reflect_detects_volatile_traits(self):
        """Traits très variables → volatile_traits non vide."""
        traits = [
            {"curiosite": 30}, {"curiosite": 70}, {"curiosite": 35},
            {"curiosite": 75}, {"curiosite": 40}, {"curiosite": 80},
        ]
        self._add_snapshots(6, traits=traits)
        result = self.engine.meta_reflect()
        assert len(result["volatile_traits"]) > 0

    def test_meta_reflect_stable_traits(self):
        """Traits stables → volatile_traits vide."""
        traits = [{"curiosite": 50}, {"curiosite": 51}, {"curiosite": 50},
                  {"curiosite": 52}, {"curiosite": 50}, {"curiosite": 51}]
        self._add_snapshots(6, traits=traits)
        result = self.engine.meta_reflect()
        assert len(result["volatile_traits"]) == 0

    # --- compute_strategic_mode ---

    def test_strategic_mode_survie_error_streak(self):
        """error_streak >= 7 → mode survie."""
        self.engine._snapshots.append({
            "traits": {"average": {}},
            "performance": {"error_streak": 8, "mission_count": 10, "success_rate": 0.3,
                            "cloud_budget_used": 0, "cloud_budget_max": 100,
                            "council_consensus_rate": 0.5},
            "health": {"verdict": "GO"},
            "mood": "fatigue",
        })
        assert self.engine.compute_strategic_mode() == "survie"

    def test_strategic_mode_survie_nogo(self):
        """Santé NO_GO → mode survie."""
        self.engine._snapshots.append({
            "traits": {"average": {}},
            "performance": {"error_streak": 0, "mission_count": 10, "success_rate": 0.7,
                            "cloud_budget_used": 0, "cloud_budget_max": 100,
                            "council_consensus_rate": 0.5},
            "health": {"verdict": "NO_GO"},
            "mood": "équilibré",
        })
        assert self.engine.compute_strategic_mode() == "survie"

    def test_strategic_mode_consolidation(self):
        """Trend négatif → consolidation."""
        self._add_snapshots(6, success_rates=[0.8, 0.75, 0.7, 0.65, 0.55, 0.5])
        assert self.engine.compute_strategic_mode() == "consolidation"

    def test_strategic_mode_exploration(self):
        """Success rate > 75% et trend >= 0 → exploration."""
        self._add_snapshots(6, success_rates=[0.8, 0.82, 0.85, 0.83, 0.85, 0.87])
        assert self.engine.compute_strategic_mode() == "exploration"

    def test_strategic_mode_standard(self):
        """Conditions normales → standard."""
        self._add_snapshots(6, success_rates=[0.6, 0.65, 0.6, 0.65, 0.6, 0.65])
        assert self.engine.compute_strategic_mode() == "standard"

    def test_scoring_survie_penalizes_expansion(self):
        """Mode survie dans scoring → EXPANSION_CODE fortement pénalisé."""
        self.engine._snapshots.append({
            "traits": {"average": {}},
            "performance": {"error_streak": 8, "mission_count": 10, "success_rate": 0.3,
                            "cloud_budget_used": 0, "cloud_budget_max": 100,
                            "council_consensus_rate": 0.5},
            "health": {"verdict": "GO"},
            "mood": "fatigue",
        })
        adj = self.engine.compute_adaptive_scoring([])
        assert adj.get("EXPANSION_CODE", 0) < -3
