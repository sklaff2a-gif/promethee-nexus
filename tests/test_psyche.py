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
