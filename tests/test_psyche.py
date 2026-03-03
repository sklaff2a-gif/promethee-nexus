# tests/test_psyche.py — Tests unitaires pour le moteur de personnalité PSYCHE
import pytest
import asyncio
import json
import os
import tempfile
from unittest.mock import patch, AsyncMock

from core.psyche import (
    PsycheEngine, psyche, BASELINES, TRAIT_NAMES, AGENT_OFFSETS,
    ROUTINE_AFFINITY, DECAY_FACTOR, _clamp, STATE_FILE,
)
from core.event_bus.bus import bus


class TestClamp:
    def test_clamp_normal(self):
        assert _clamp(50.0) == 50.0

    def test_clamp_below_zero(self):
        assert _clamp(-10.0) == 0.0

    def test_clamp_above_hundred(self):
        assert _clamp(150.0) == 100.0

    def test_clamp_boundary(self):
        assert _clamp(0.0) == 0.0
        assert _clamp(100.0) == 100.0


class TestPsycheInit:
    def setup_method(self):
        PsycheEngine.reset_singleton()
        self.engine = PsycheEngine()

    def test_singleton(self):
        e2 = PsycheEngine()
        assert self.engine is e2

    def test_init_creates_all_agents(self):
        self.engine.init()
        assert len(self.engine.agents) == len(AGENT_OFFSETS)
        for name in AGENT_OFFSETS:
            assert name in self.engine.agents

    def test_init_with_specific_agents(self):
        self.engine.init(["coder", "architect"])
        assert "coder" in self.engine.agents
        assert "architect" in self.engine.agents

    def test_default_traits_with_offsets(self):
        traits = PsycheEngine._default_traits("coder")
        # coder: curiosite +10 → 60, creativite +10 → 60, audace +10 → 60
        assert traits["curiosite"] == 60.0
        assert traits["creativite"] == 60.0
        assert traits["audace"] == 60.0
        assert traits["savoir"] == 55.0
        assert traits["survie"] == 55.0  # 60 - 5
        assert traits["respect"] == 55.0  # 55 + 0

    def test_default_traits_unknown_agent(self):
        traits = PsycheEngine._default_traits("unknown_agent")
        for t in TRAIT_NAMES:
            assert traits[t] == BASELINES[t]

    def test_default_traits_clamped(self):
        """Les traits ne dépassent jamais [0, 100]."""
        for agent in AGENT_OFFSETS:
            traits = PsycheEngine._default_traits(agent)
            for t in TRAIT_NAMES:
                assert 0 <= traits[t] <= 100


class TestTraitAccess:
    def setup_method(self):
        PsycheEngine.reset_singleton()
        self.engine = PsycheEngine()
        self.engine.init(["coder", "strategist", "architect"])

    def test_get_traits_returns_copy(self):
        traits = self.engine.get_traits("coder")
        traits["curiosite"] = 999
        assert self.engine.agents["coder"]["curiosite"] != 999

    def test_get_traits_unknown_creates_default(self):
        traits = self.engine.get_traits("new_agent")
        assert "new_agent" in self.engine.agents
        for t in TRAIT_NAMES:
            assert traits[t] == BASELINES[t]

    def test_get_all_traits(self):
        all_traits = self.engine.get_all_traits()
        assert len(all_traits) == 3
        assert "coder" in all_traits

    def test_get_dominant_trait(self):
        name, value = self.engine.get_dominant_trait("evolution")
        # evolution a curiosite+15=65, creativite+15=65, audace+15=65
        assert value >= 65.0

    def test_get_system_average(self):
        avg = self.engine.get_system_average()
        assert len(avg) == 6
        for t in TRAIT_NAMES:
            assert isinstance(avg[t], float)

    def test_get_system_average_empty(self):
        self.engine.agents = {}
        avg = self.engine.get_system_average()
        assert avg == {t: float(v) for t, v in BASELINES.items()}


class TestApplyDeltas:
    def setup_method(self):
        PsycheEngine.reset_singleton()
        self.engine = PsycheEngine()
        self.engine.init(["coder", "architect"])

    def test_apply_positive_delta(self):
        before = self.engine.get_traits("coder")["curiosite"]
        self.engine.apply_deltas("coder", {"curiosite": 5.0})
        after = self.engine.get_traits("coder")["curiosite"]
        assert after == before + 5.0

    def test_apply_negative_delta(self):
        before = self.engine.get_traits("coder")["audace"]
        self.engine.apply_deltas("coder", {"audace": -3.0})
        after = self.engine.get_traits("coder")["audace"]
        assert after == before - 3.0

    def test_apply_clamped(self):
        self.engine.apply_deltas("coder", {"curiosite": 200.0})
        assert self.engine.get_traits("coder")["curiosite"] == 100.0

    def test_apply_system_affects_all(self):
        before_coder = self.engine.get_traits("coder")["survie"]
        before_arch = self.engine.get_traits("architect")["survie"]
        self.engine.apply_deltas("_system", {"survie": 1.0})
        assert self.engine.get_traits("coder")["survie"] == before_coder + 1.0
        assert self.engine.get_traits("architect")["survie"] == before_arch + 1.0

    def test_apply_records_history(self):
        self.engine.apply_deltas("coder", {"curiosite": 1.0}, event="TEST")
        assert len(self.engine.history) == 1
        assert self.engine.history[0]["event"] == "TEST"

    def test_history_capped_at_100(self):
        for i in range(110):
            self.engine.apply_deltas("coder", {"curiosite": 0.01}, event=f"EVT_{i}")
        assert len(self.engine.history) <= 100

    def test_apply_unknown_agent_creates_it(self):
        self.engine.apply_deltas("new_agent", {"curiosite": 5.0})
        assert "new_agent" in self.engine.agents


class TestDecay:
    def setup_method(self):
        PsycheEngine.reset_singleton()
        self.engine = PsycheEngine()
        self.engine.init(["coder"])

    def test_decay_moves_toward_baseline(self):
        # Pousser curiosite du coder à 90 (baseline+offset = 60)
        self.engine.agents["coder"]["curiosite"] = 90.0
        self.engine.apply_daily_decay()
        after = self.engine.agents["coder"]["curiosite"]
        # new = 60 + (90 - 60) * 0.98 = 60 + 29.4 = 89.4
        assert after == pytest.approx(89.4, abs=0.1)
        assert after < 90.0

    def test_decay_no_double(self):
        """Le decay ne s'applique qu'une fois par jour."""
        self.engine.agents["coder"]["curiosite"] = 90.0
        self.engine.apply_daily_decay()
        v1 = self.engine.agents["coder"]["curiosite"]
        self.engine.apply_daily_decay()
        v2 = self.engine.agents["coder"]["curiosite"]
        assert v1 == v2

    def test_decay_on_below_baseline(self):
        # Trait en dessous de sa baseline agent → remonte
        self.engine.agents["coder"]["curiosite"] = 40.0  # baseline+offset = 60
        self.engine.apply_daily_decay()
        after = self.engine.agents["coder"]["curiosite"]
        # new = 60 + (40 - 60) * 0.98 = 60 - 19.6 = 40.4
        assert after > 40.0


class TestPersonalityBias:
    def setup_method(self):
        PsycheEngine.reset_singleton()
        self.engine = PsycheEngine()
        self.engine.init()

    def test_bias_returns_float(self):
        bias = self.engine.compute_personality_bias("EXPANSION_CODE")
        assert isinstance(bias, float)

    def test_bias_unknown_intent(self):
        assert self.engine.compute_personality_bias("UNKNOWN") == 0.0

    def test_bias_range(self):
        for intent in ROUTINE_AFFINITY:
            bias = self.engine.compute_personality_bias(intent)
            assert -2.0 <= bias <= 2.0

    def test_bias_reacts_to_trait_changes(self):
        before = self.engine.compute_personality_bias("EXPANSION_CODE")
        # Augmenter la créativité de tous les agents
        for name in self.engine.agents:
            self.engine.agents[name]["creativite"] = 90.0
        after = self.engine.compute_personality_bias("EXPANSION_CODE")
        assert after > before


class TestPersistence:
    def setup_method(self):
        PsycheEngine.reset_singleton()
        self.engine = PsycheEngine()
        self.tmpdir = tempfile.mkdtemp()
        self.tmpfile = os.path.join(self.tmpdir, "psyche_test.json")

    def test_save_and_load(self):
        self.engine.init(["coder"])
        self.engine.apply_deltas("coder", {"curiosite": 5.0}, event="TEST")
        expected_curiosite = self.engine.agents["coder"]["curiosite"]
        # Sauvegarder dans un fichier temp
        with patch("core.psyche.STATE_FILE", self.tmpfile):
            self.engine.save()
            assert os.path.exists(self.tmpfile)

            # Charger dans une nouvelle instance
            PsycheEngine.reset_singleton()
            engine2 = PsycheEngine()
            engine2.init(["coder"])

            assert engine2.agents["coder"]["curiosite"] == expected_curiosite

    def test_load_missing_file(self):
        result = self.engine._load()
        assert result is None

    def test_save_creates_directory(self):
        self.engine.init(["coder"])
        nested = os.path.join(self.tmpdir, "sub", "psyche.json")
        with patch("core.psyche.STATE_FILE", nested):
            self.engine.save()
        assert os.path.exists(nested)


class TestCouncilForge:
    def setup_method(self):
        PsycheEngine.reset_singleton()
        self.engine = PsycheEngine()
        self.engine.init(["coder", "architect", "strategist", "security", "evolution", "researcher"])

    def test_select_topic_error_streak(self):
        topic = self.engine.select_council_topic(error_streak=3)
        assert "strategist" in topic["participants"]
        assert "architect" in topic["participants"]
        assert not topic["needs_research"]

    def test_select_topic_high_daily_count(self):
        topic = self.engine.select_council_topic(daily_count=16)
        assert "strategist" in topic["participants"]
        assert not topic["needs_research"]

    def test_budget_skipped_if_already_debated(self):
        """Le sujet 'budget' est skip si déjà dans recent_subjects (même loin)."""
        topic = self.engine.select_council_topic(
            daily_count=16,
            recent_subjects=["erreurs", "budget", "curiosite", "recherche"]
        )
        # "budget" est dans recent_subjects → ne doit PAS redébattre le budget
        assert topic["subject_key"] != "budget"

    def test_erreurs_skipped_if_already_debated(self):
        """Le sujet 'erreurs' est skip si déjà dans recent_subjects."""
        topic = self.engine.select_council_topic(
            error_streak=3,
            recent_subjects=["erreurs", "curiosite"]
        )
        assert topic["subject_key"] != "erreurs"

    def test_select_topic_high_curiosite(self):
        for name in self.engine.agents:
            self.engine.agents[name]["curiosite"] = 85.0
        topic = self.engine.select_council_topic()
        assert "researcher" in topic["participants"]
        # Même en trait extrême, la recherche reste active
        assert topic["needs_research"] is True

    def test_select_topic_high_survie(self):
        for name in self.engine.agents:
            self.engine.agents[name]["survie"] = 85.0
        topic = self.engine.select_council_topic()
        assert "architect" in topic["participants"]
        # Même en trait extrême, la recherche reste active
        assert topic["needs_research"] is True

    def test_select_topic_moderate_curiosite_uses_default_theme(self):
        """Curiosité modérée (< 80) → thème de recherche par défaut, pas le sujet curiosité."""
        for name in self.engine.agents:
            self.engine.agents[name]["curiosite"] = 70.0
        topic = self.engine.select_council_topic()
        # Ne doit PAS déclencher le sujet "curiosité élevée"
        assert "curiosité" not in topic["mission"].lower() or "très" in topic["mission"].lower()
        assert topic["needs_research"] is True

    def test_select_topic_research_fallback(self):
        """Sans condition critique, on tombe sur un thème de recherche."""
        topic = self.engine.select_council_topic()
        assert len(topic["participants"]) >= 2
        assert topic["needs_research"] is True
        assert topic["research_query"] is not None

    def test_research_themes_rotation(self):
        """Chaque debate_index donne un thème différent (rotation)."""
        topics = [self.engine.select_council_topic(debate_index=i) for i in range(8)]
        queries = [t["research_query"] for t in topics]
        # Au moins 3 queries différentes sur 8
        assert len(set(queries)) >= 3

    def test_get_debate_index(self):
        assert self.engine.get_debate_index() == 0
        self.engine.apply_deltas("coder", {"curiosite": 0.1}, event="COUNCIL_END/consensus")
        assert self.engine.get_debate_index() == 1

    def test_council_curiosite_dedup(self):
        """Curiosité > 80 + 'curiosite' in recent → skip ce topic."""
        for name in self.engine.agents:
            self.engine.agents[name]["curiosite"] = 85.0
        topic = self.engine.select_council_topic(
            recent_subjects=["curiosite"]
        )
        # Ne doit PAS re-sélectionner "curiosite" comme subject_key
        assert topic["subject_key"] != "curiosite"

    def test_council_survie_dedup(self):
        """Survie > 80 + 'survie' in recent → skip ce topic."""
        for name in self.engine.agents:
            self.engine.agents[name]["survie"] = 85.0
        topic = self.engine.select_council_topic(
            recent_subjects=["survie"]
        )
        # Ne doit PAS re-sélectionner "survie" comme subject_key
        assert topic["subject_key"] != "survie"

    def test_roadmap_council_disabled(self):
        """Priorite 2.2 roadmap est desactivee — aucun sujet roadmap_ genere."""
        topic = self.engine.select_council_topic()
        assert not topic["subject_key"].startswith("roadmap_")


@pytest.mark.asyncio
class TestEventHandlers:
    def setup_method(self):
        PsycheEngine.reset_singleton()
        bus.reset()
        self.engine = PsycheEngine()
        self.engine.init(["coder", "architect", "factory", "strategist", "researcher"])

    async def test_council_end_consensus(self):
        before = self.engine.get_traits("coder")["respect"]
        await self.engine._on_council_end({
            "status": "consensus",
            "participants": ["coder", "architect"],
        })
        after = self.engine.get_traits("coder")["respect"]
        assert after == before + 1.0

    async def test_council_end_max_rounds(self):
        before = self.engine.get_traits("coder")["audace"]
        await self.engine._on_council_end({
            "status": "max_rounds",
            "participants": ["coder"],
        })
        after = self.engine.get_traits("coder")["audace"]
        assert after == before + 0.5

    async def test_council_turn(self):
        before = self.engine.get_traits("coder")["curiosite"]
        await self.engine._on_council_turn({"agent": "coder"})
        after = self.engine.get_traits("coder")["curiosite"]
        assert after == before + 0.1

    async def test_artifact_created(self):
        before = self.engine.get_traits("factory")["creativite"]
        await self.engine._on_artifact_created({})
        after = self.engine.get_traits("factory")["creativite"]
        assert after == before + 0.5

    async def test_ci_success(self):
        before = self.engine.get_traits("coder")["survie"]
        await self.engine._on_ci_result({"agent": "coder", "success": True})
        after = self.engine.get_traits("coder")["survie"]
        assert after == before + 0.5

    async def test_ci_failure(self):
        before = self.engine.get_traits("coder")["audace"]
        await self.engine._on_ci_result({"agent": "coder", "success": False})
        after = self.engine.get_traits("coder")["audace"]
        assert after == before - 0.3

    async def test_routine_complete_veille(self):
        before = self.engine.get_traits("researcher")["curiosite"]
        await self.engine._on_routine_complete({
            "intent": "VEILLE_SILENCIEUSE",
            "participants": [],
        })
        after = self.engine.get_traits("researcher")["curiosite"]
        assert after == before + 0.5

    async def test_routine_complete_council_debate(self):
        before = self.engine.get_traits("coder")["respect"]
        await self.engine._on_routine_complete({
            "intent": "COUNCIL_DEBATE",
            "participants": ["coder", "architect"],
        })
        after = self.engine.get_traits("coder")["respect"]
        assert after == before + 0.5


class TestAffinityMatrix:
    def test_all_routines_have_weights(self):
        for intent, weights in ROUTINE_AFFINITY.items():
            total = sum(weights.values())
            assert 0.9 <= total <= 1.1, f"{intent} weights sum to {total}"

    def test_all_traits_valid(self):
        for intent, weights in ROUTINE_AFFINITY.items():
            for trait in weights:
                assert trait in TRAIT_NAMES, f"{intent} references unknown trait {trait}"
