# tests/test_psyche.py — Tests PSYCHE : trait bienveillance + personality bias + events
import pytest
import asyncio
from unittest.mock import patch, AsyncMock

from core.psyche import (
    PsycheEngine, psyche, TRAIT_NAMES, BASELINES,
    AGENT_OFFSETS, ROUTINE_AFFINITY,
)


@pytest.fixture(autouse=True)
def reset_psyche():
    """Reset le singleton PSYCHE avant chaque test."""
    PsycheEngine.reset_singleton()
    yield
    PsycheEngine.reset_singleton()


@pytest.fixture
def engine():
    """Cree un PsycheEngine initialise sans I/O fichier."""
    e = PsycheEngine()
    with patch.object(e, "_load", return_value=None), \
         patch.object(e, "save"), \
         patch.object(e, "_subscribe_events"):
        e.init()
    return e


# --- TestBienveillanceTrait ---

class TestBienveillanceTrait:

    def test_bienveillance_in_trait_names(self):
        """7 traits au total, bienveillance inclus."""
        assert "bienveillance" in TRAIT_NAMES
        assert len(TRAIT_NAMES) == 7

    def test_bienveillance_baseline(self):
        """Baseline bienveillance = 65.0 (valeur fondatrice elevee)."""
        assert BASELINES["bienveillance"] == 65.0

    def test_bienveillance_agent_offsets(self):
        """Tous les agents ont un offset bienveillance."""
        for agent, offsets in AGENT_OFFSETS.items():
            assert "bienveillance" in offsets, f"Agent {agent} manque bienveillance"

    def test_default_traits_includes_bienveillance(self, engine):
        """_default_traits() inclut bienveillance."""
        traits = engine._default_traits("coder")
        assert "bienveillance" in traits
        assert len(traits) == 7

    def test_strategist_bienveillance_high(self, engine):
        """Strategist : baseline 65 + offset 10 = 75."""
        traits = engine.get_traits("strategist")
        assert traits["bienveillance"] == 75.0

    def test_coder_bienveillance_baseline(self, engine):
        """Coder : offset 0 -> bienveillance = baseline 65."""
        traits = engine.get_traits("coder")
        assert traits["bienveillance"] == 65.0

    def test_writer_bienveillance_high(self, engine):
        """Writer : baseline 65 + offset 10 = 75."""
        traits = engine.get_traits("writer")
        assert traits["bienveillance"] == 75.0


# --- TestComputePersonalityBias ---

class TestComputePersonalityBias:

    def test_council_debate_includes_bienveillance(self):
        """COUNCIL_DEBATE a une affinite bienveillance > 0."""
        aff = ROUTINE_AFFINITY["COUNCIL_DEBATE"]
        assert "bienveillance" in aff
        assert aff["bienveillance"] > 0

    def test_memory_cleanup_includes_bienveillance(self):
        """MEMORY_CLEANUP a une affinite bienveillance."""
        aff = ROUTINE_AFFINITY["MEMORY_CLEANUP"]
        assert "bienveillance" in aff
        assert aff["bienveillance"] > 0

    def test_refactor_random_includes_bienveillance(self):
        """REFACTOR_RANDOM a une affinite bienveillance."""
        aff = ROUTINE_AFFINITY["REFACTOR_RANDOM"]
        assert "bienveillance" in aff
        assert aff["bienveillance"] > 0

    def test_personality_bias_returns_float(self, engine):
        """compute_personality_bias retourne un float."""
        bias = engine.compute_personality_bias("COUNCIL_DEBATE")
        assert isinstance(bias, float)

    def test_unknown_intent_returns_zero(self, engine):
        """Intent inconnu -> 0.0."""
        assert engine.compute_personality_bias("INEXISTANT") == 0.0


# --- TestEventHandlers ---

class TestEventHandlers:

    @pytest.mark.asyncio
    async def test_council_consensus_boosts_bienveillance(self, engine):
        """Consensus -> bienveillance +0.3 pour chaque participant."""
        before = engine.get_traits("strategist")["bienveillance"]
        with patch.object(engine, "save"), \
             patch("core.psyche.bus.publish", new_callable=AsyncMock):
            await engine._on_council_end({
                "participants": ["strategist"],
                "status": "consensus",
            })
        after = engine.get_traits("strategist")["bienveillance"]
        assert after == pytest.approx(before + 0.3, abs=0.01)

    @pytest.mark.asyncio
    async def test_ci_failure_boosts_bienveillance(self, engine):
        """Echec CI -> bienveillance +0.2 (compassion)."""
        before = engine.get_traits("factory")["bienveillance"]
        with patch.object(engine, "save"), \
             patch("core.psyche.bus.publish", new_callable=AsyncMock):
            await engine._on_ci_result({
                "agent": "factory",
                "success": False,
            })
        after = engine.get_traits("factory")["bienveillance"]
        assert after == pytest.approx(before + 0.2, abs=0.01)


# --- TestSystemAverage ---

class TestSystemAverage:

    def test_system_average_includes_bienveillance(self, engine):
        """Moyenne systeme contient les 7 traits."""
        avg = engine.get_system_average()
        assert "bienveillance" in avg
        assert len(avg) == 7

    def test_system_average_bienveillance_value(self, engine):
        """Moyenne bienveillance > 60 (baseline 65 + offsets positifs)."""
        avg = engine.get_system_average()
        assert avg["bienveillance"] > 60.0


# --- TestProposeCouncilTopic ---

class TestProposeCouncilTopic:
    """Prométhée propose ses propres sujets de Council via _propose_council_topic()."""

    def test_no_signals_returns_none(self, engine):
        """Sans signaux internes, pas de proposition."""
        with patch.dict("sys.modules", {
            "core.inner_voice": type("M", (), {"voice": type("V", (), {"get_stream": lambda s, n=10: []})()})(),
            "core.cingulate_cortex": type("M", (), {"cingulate": type("C", (), {"_active_conflicts": []})()})(),
            "core.prefrontal": type("M", (), {"prefrontal": type("P", (), {"get_working_memory": lambda s: []})()})(),
            "core.default_mode_network": type("M", (), {"dmn": type("D", (), {"insights": []})()})(),
        }):
            result = engine._propose_council_topic()
        assert result is None

    def test_high_salience_thought_triggers_proposal(self, engine):
        """Pensée à haute saillance + émotion frustration → proposition."""
        mock_voice = type("V", (), {
            "get_stream": lambda s, n=10: [
                {"salience": 0.9, "content": "Je ne comprends pas pourquoi mes routines échouent", "emotion": "frustration"},
            ]
        })()
        with patch.dict("sys.modules", {
            "core.inner_voice": type("M", (), {"voice": mock_voice})(),
            "core.cingulate_cortex": type("M", (), {"cingulate": type("C", (), {"_active_conflicts": []})()})(),
            "core.prefrontal": type("M", (), {"prefrontal": type("P", (), {"get_working_memory": lambda s: []})()})(),
            "core.default_mode_network": type("M", (), {"dmn": type("D", (), {"insights": []})()})(),
        }):
            result = engine._propose_council_topic()
        assert result is not None
        assert result["subject_key"] == "inner_proposal"
        assert "préoccupations" in result["mission"]
        assert result["needs_research"] is False

    def test_conflict_sets_security_participants(self, engine):
        """Conflit de sévérité élevée → participants security."""
        mock_cingulate = type("C", (), {
            "_active_conflicts": [
                {"severity": 3.0, "details": "Contradiction entre reptilien et dopamine", "type": "homeostatic"}
            ]
        })()
        with patch.dict("sys.modules", {
            "core.inner_voice": type("M", (), {"voice": type("V", (), {"get_stream": lambda s, n=10: []})()})(),
            "core.cingulate_cortex": type("M", (), {"cingulate": mock_cingulate})(),
            "core.prefrontal": type("M", (), {"prefrontal": type("P", (), {"get_working_memory": lambda s: []})()})(),
            "core.default_mode_network": type("M", (), {"dmn": type("D", (), {"insights": []})()})(),
        }):
            result = engine._propose_council_topic()
        assert result is not None
        assert "security" in result["participants"]

    def test_blocked_goal_triggers_proposal(self, engine):
        """Goal bloqué (progress < 0.3, cost >= 10) → proposition."""
        mock_prefrontal = type("P", (), {
            "get_working_memory": lambda s: [
                {"goal_title": "Implémenter workspace global", "progress": 0.1, "cost_spent": 15}
            ]
        })()
        with patch.dict("sys.modules", {
            "core.inner_voice": type("M", (), {"voice": type("V", (), {"get_stream": lambda s, n=10: []})()})(),
            "core.cingulate_cortex": type("M", (), {"cingulate": type("C", (), {"_active_conflicts": []})()})(),
            "core.prefrontal": type("M", (), {"prefrontal": mock_prefrontal})(),
            "core.default_mode_network": type("M", (), {"dmn": type("D", (), {"insights": []})()})(),
        }):
            result = engine._propose_council_topic()
        assert result is not None
        assert "Goal bloqué" in result["mission"]
        assert "coder" in result["participants"]

    def test_dmn_insight_triggers_proposal(self, engine):
        """Insight DMN → proposition avec participants researcher."""
        mock_dmn = type("D", (), {
            "insights": [
                {"text": "Connexion inattendue: 'cardiac' relie 'dopamine' et 'prefrontal'", "timestamp": 0, "cycle": 1}
            ]
        })()
        with patch.dict("sys.modules", {
            "core.inner_voice": type("M", (), {"voice": type("V", (), {"get_stream": lambda s, n=10: []})()})(),
            "core.cingulate_cortex": type("M", (), {"cingulate": type("C", (), {"_active_conflicts": []})()})(),
            "core.prefrontal": type("M", (), {"prefrontal": type("P", (), {"get_working_memory": lambda s: []})()})(),
            "core.default_mode_network": type("M", (), {"dmn": mock_dmn})(),
        }):
            result = engine._propose_council_topic()
        assert result is not None
        assert "researcher" in result["participants"]

    def test_low_salience_ignored(self, engine):
        """Pensées à faible saillance ne déclenchent pas de proposition."""
        mock_voice = type("V", (), {
            "get_stream": lambda s, n=10: [
                {"salience": 0.3, "content": "Tout va bien", "emotion": "serenite"},
                {"salience": 0.2, "content": "Routine normale", "emotion": "neutre"},
            ]
        })()
        with patch.dict("sys.modules", {
            "core.inner_voice": type("M", (), {"voice": mock_voice})(),
            "core.cingulate_cortex": type("M", (), {"cingulate": type("C", (), {"_active_conflicts": []})()})(),
            "core.prefrontal": type("M", (), {"prefrontal": type("P", (), {"get_working_memory": lambda s: []})()})(),
            "core.default_mode_network": type("M", (), {"dmn": type("D", (), {"insights": []})()})(),
        }):
            result = engine._propose_council_topic()
        assert result is None

    def test_multiple_sources_combined(self, engine):
        """Signaux multiples combinés → mission contient plusieurs lignes."""
        mock_voice = type("V", (), {
            "get_stream": lambda s, n=10: [
                {"salience": 0.8, "content": "Pourquoi mes prédictions sont si mauvaises ?", "emotion": "confusion"},
            ]
        })()
        mock_cingulate = type("C", (), {
            "_active_conflicts": [
                {"severity": 2.5, "details": "Conflit reptilien-dopamine", "type": "homeostatic"}
            ]
        })()
        with patch.dict("sys.modules", {
            "core.inner_voice": type("M", (), {"voice": mock_voice})(),
            "core.cingulate_cortex": type("M", (), {"cingulate": mock_cingulate})(),
            "core.prefrontal": type("M", (), {"prefrontal": type("P", (), {"get_working_memory": lambda s: []})()})(),
            "core.default_mode_network": type("M", (), {"dmn": type("D", (), {"insights": []})()})(),
        }):
            result = engine._propose_council_topic()
        assert result is not None
        assert "[voix]" in result["mission"]
        assert "[conflit]" in result["mission"]

    def test_organ_unavailable_graceful(self, engine):
        """Si un organe n'est pas importable, dégradation gracieuse."""
        # Aucun module mocké → imports échouent → pas de crash
        with patch.dict("sys.modules", {
            "core.inner_voice": None,
            "core.cingulate_cortex": None,
            "core.prefrontal": None,
            "core.default_mode_network": None,
        }):
            result = engine._propose_council_topic()
        assert result is None

    def test_select_council_topic_uses_proposal(self, engine):
        """select_council_topic() utilise la proposition si disponible."""
        proposal = {
            "participants": ["strategist", "evolution", "coder"],
            "mission": "Test inner proposal",
            "needs_research": False,
            "research_query": None,
            "subject_key": "inner_proposal",
        }
        with patch.object(engine, "_propose_council_topic", return_value=proposal):
            topic = engine.select_council_topic(recent_subjects=[])
        assert topic["subject_key"] == "inner_proposal"

    def test_select_council_skips_if_recent(self, engine):
        """Si inner_proposal est dans recent_subjects, on ne re-propose pas."""
        proposal = {
            "participants": ["strategist", "evolution", "coder"],
            "mission": "Test",
            "needs_research": False,
            "research_query": None,
            "subject_key": "inner_proposal",
        }
        with patch.object(engine, "_propose_council_topic", return_value=proposal):
            topic = engine.select_council_topic(recent_subjects=["inner_proposal"])
        # Devrait être un autre sujet (eureka, desir, ou recherche web)
        assert topic["subject_key"] != "inner_proposal"


# --- TestSaveLoad ---

class TestSaveLoad:

    def test_bienveillance_persisted(self, engine, tmp_path):
        """Save/load preserve bienveillance."""
        import json
        state_file = tmp_path / "psyche_state.json"
        with patch("core.psyche.STATE_FILE", str(state_file)):
            engine.save()
            data = json.loads(state_file.read_text(encoding="utf-8"))
            strategist_traits = data["agents"]["strategist"]
            assert "bienveillance" in strategist_traits
            assert strategist_traits["bienveillance"] == 75.0
