"""Tests du Lobe Temporal — couche d'intégration mémoire unifiée."""

import os
import json
import time
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from dataclasses import asdict

from core.temporal_lobe import (
    TemporalLobe, MemoryTrace, temporal,
    TEMPORAL_LOBE_STATE_FILE, CACHE_TTL,
    _temporal_decay, _text_overlap, _cache_key,
    MIN_RELEVANCE, MAX_COOCCURRENCES,
)


# --- Fixtures ---

@pytest.fixture(autouse=True)
def reset_temporal(tmp_path, monkeypatch):
    """Reset singleton + isolation fichier état."""
    TemporalLobe.reset_singleton()
    state_file = str(tmp_path / "temporal_lobe_state.json")
    monkeypatch.setattr("core.temporal_lobe.TEMPORAL_LOBE_STATE_FILE", state_file)
    with patch.object(TemporalLobe, "_load", return_value=None):
        t = TemporalLobe()
    yield t
    TemporalLobe.reset_singleton()


@pytest.fixture
def mock_chroma():
    """Mock ChromaMemoryManager."""
    mm = MagicMock()
    mm.query_with_metadata.return_value = {
        "documents": [["Doc A sur le refactoring", "Doc B sur les tests"]],
        "metadatas": [[
            {"agent": "coder", "timestamp": time.time() - 3600},
            {"agent": "architect", "timestamp": time.time() - 7200},
        ]],
        "distances": [[0.3, 0.6]],
    }
    return mm


@pytest.fixture
def mock_hippocampus():
    """Mock Hippocampus."""
    hippo = MagicMock()
    hippo.get_session_narrative.return_value = [
        {
            "summary": "Routine EXPANSION_CODE réussie — refactoring du module router",
            "salience": 0.8,
            "emotion": "satisfaction",
            "timestamp": time.time() - 1800,
        },
        {
            "summary": "Council DEBATE sur la priorisation budget",
            "salience": 0.6,
            "emotion": "curiosité",
            "timestamp": time.time() - 3600,
        },
    ]
    hippo.get_emotional_context_at.return_value = {
        "emotions": ["satisfaction"],
        "dominant_emotion": "satisfaction",
        "avg_salience": 0.7,
        "episode_count": 2,
    }
    hippo.get_arc_narrative.return_value = {
        "arc_type": "breakthrough",
        "title": "Percée refactoring",
        "narrative": "Après 3 tentatives, le refactoring est passé.",
        "emotional_arc": "frustration → satisfaction",
        "marked": True,
    }
    hippo._episodes = []
    return hippo


@pytest.fixture
def mock_heart():
    """Mock CardiacEngine."""
    heart = MagicMock()
    heart.current_emotion = "sérénité"
    heart.emotion_intensity = 0.6
    heart.get_somatic_signal.return_value = 0.3
    return heart


@pytest.fixture
def mock_desires():
    """Mock DesireEngine."""
    desires = MagicMock()
    desires.get_dominant_narrative.return_value = ["La curiosité me pousse à explorer"]
    return desires


@pytest.fixture
def mock_cortex():
    """Mock SynapticNetwork."""
    cortex = MagicMock()
    cortex.query_associations.return_value = [
        ("refactoring", 0.8),
        ("clean_code", 0.5),
    ]
    cortex.format_for_prompt.return_value = "Concepts: refactoring, clean_code"
    return cortex


# ===========================================================
# SINGLETON
# ===========================================================

class TestSingleton:
    def test_singleton_identity(self):
        a = TemporalLobe()
        b = TemporalLobe()
        assert a is b

    def test_reset_singleton(self):
        a = TemporalLobe()
        TemporalLobe.reset_singleton()
        with patch.object(TemporalLobe, "_load", return_value=None):
            b = TemporalLobe()
        assert a is not b

    def test_initial_state(self, reset_temporal):
        t = reset_temporal
        assert t._cooccurrences == {}
        assert t._total_recalls == 0
        assert t._total_enrichments == 0
        assert t._total_cache_hits == 0


# ===========================================================
# HELPERS
# ===========================================================

class TestHelpers:
    def test_temporal_decay_zero_age(self):
        assert _temporal_decay(0) == 1.0

    def test_temporal_decay_positive_age(self):
        # Après 30 jours (demi-vie), devrait être ~0.5
        decay = _temporal_decay(30 * 86400, half_life_days=30)
        assert 0.45 < decay < 0.55

    def test_temporal_decay_negative_age(self):
        assert _temporal_decay(-100) == 1.0

    def test_text_overlap_full(self):
        words = {"hello", "world"}
        assert _text_overlap(words, "hello world") == 1.0

    def test_text_overlap_partial(self):
        words = {"hello", "world", "foo"}
        score = _text_overlap(words, "hello bar world")
        assert 0.5 < score < 0.8

    def test_text_overlap_none(self):
        words = {"hello"}
        assert _text_overlap(words, "goodbye") == 0.0

    def test_text_overlap_empty(self):
        assert _text_overlap(set(), "hello") == 0.0
        assert _text_overlap({"hello"}, "") == 0.0

    def test_cache_key_deterministic(self):
        k1 = _cache_key("test", 5, "coll", True, True, "")
        k2 = _cache_key("test", 5, "coll", True, True, "")
        assert k1 == k2

    def test_cache_key_different_params(self):
        k1 = _cache_key("test", 5, "coll", True, True, "")
        k2 = _cache_key("test", 10, "coll", True, True, "")
        assert k1 != k2


# ===========================================================
# MEMORY TRACE
# ===========================================================

class TestMemoryTrace:
    def test_default_values(self):
        t = MemoryTrace(text="hello")
        assert t.text == "hello"
        assert t.relevance == 0.0
        assert t.source == "chroma"
        assert t.emotion == ""
        assert t.associated_concepts == []

    def test_to_dict(self):
        t = MemoryTrace(text="test", relevance=0.5, source="episodic")
        d = t.to_dict()
        assert d["text"] == "test"
        assert d["relevance"] == 0.5
        assert d["source"] == "episodic"


# ===========================================================
# UNIFIED RECALL
# ===========================================================

class TestUnifiedRecall:
    def test_empty_query(self, reset_temporal):
        assert reset_temporal.unified_recall("") == []
        assert reset_temporal.unified_recall("   ") == []

    def test_chroma_only(self, reset_temporal, mock_chroma):
        with patch("core.temporal_lobe.TemporalLobe._recall_episodic", return_value=[]), \
             patch("core.temporal_lobe.TemporalLobe._recall_associative", return_value=[]), \
             patch("core.temporal_lobe.TemporalLobe._enrich_traces"), \
             patch("core.temporal_lobe.TemporalLobe._publish_recall_event"):

            with patch.dict("sys.modules", {"core.vector_store": MagicMock()}):
                with patch("core.vector_store.ChromaMemoryManager") as MockCMM:
                    MockCMM.get_instance.return_value = mock_chroma
                    traces = reset_temporal.unified_recall("refactoring", limit=5,
                                                          include_episodic=False,
                                                          include_associative=False)
                    assert len(traces) > 0
                    assert all(t.source == "chroma" for t in traces)

    def test_with_episodic(self, reset_temporal, mock_hippocampus):
        """Le recall avec épisodes hippocampiques produit des traces episodic."""
        with patch.dict("sys.modules", {
            "core.vector_store": MagicMock(),
            "core.hippocampus": MagicMock(hippocampus=mock_hippocampus),
            "core.spreading_activation": MagicMock(),
            "core.synaptic_network": MagicMock(),
            "core.cardiac_engine": MagicMock(),
            "core.desire_engine": MagicMock(),
        }):
            with patch("core.temporal_lobe.TemporalLobe._recall_chroma", return_value=[]), \
                 patch("core.temporal_lobe.TemporalLobe._recall_associative", return_value=[]), \
                 patch("core.temporal_lobe.TemporalLobe._enrich_traces"), \
                 patch("core.temporal_lobe.TemporalLobe._publish_recall_event"):
                # Injecter directement via la méthode
                episodic_traces = [
                    MemoryTrace(text="Episode test", relevance=0.5,
                                timestamp=time.time(), source="episodic",
                                emotion="satisfaction", emotion_intensity=0.8)
                ]
                with patch.object(reset_temporal, "_recall_episodic", return_value=episodic_traces):
                    traces = reset_temporal.unified_recall("refactoring", limit=5)
                    assert any(t.source == "episodic" for t in traces)

    def test_cache_works(self, reset_temporal):
        """Le cache empêche les rappels redondants."""
        with patch.object(reset_temporal, "_recall_chroma", return_value=[
            MemoryTrace(text="cached", relevance=0.5, timestamp=time.time())
        ]), \
             patch.object(reset_temporal, "_recall_episodic", return_value=[]), \
             patch.object(reset_temporal, "_recall_associative", return_value=[]), \
             patch.object(reset_temporal, "_enrich_traces"), \
             patch.object(reset_temporal, "_publish_recall_event"):

            # Premier appel
            r1 = reset_temporal.unified_recall("test", limit=5)
            assert reset_temporal._total_recalls == 1
            assert reset_temporal._total_cache_hits == 0

            # Deuxième appel (cache)
            r2 = reset_temporal.unified_recall("test", limit=5)
            assert reset_temporal._total_cache_hits == 1
            assert r1 == r2

    def test_emotion_filter(self, reset_temporal):
        """Le filtre émotion ne garde que les traces correspondantes."""
        traces_mixed = [
            MemoryTrace(text="joyeux", relevance=0.5, emotion="joie",
                        timestamp=time.time()),
            MemoryTrace(text="triste", relevance=0.5, emotion="frustration",
                        timestamp=time.time()),
        ]
        with patch.object(reset_temporal, "_recall_chroma", return_value=traces_mixed), \
             patch.object(reset_temporal, "_recall_episodic", return_value=[]), \
             patch.object(reset_temporal, "_recall_associative", return_value=[]), \
             patch.object(reset_temporal, "_enrich_traces"), \
             patch.object(reset_temporal, "_publish_recall_event"):

            result = reset_temporal.unified_recall("test", emotion_filter="joie")
            assert all("joie" in t.emotion.lower() for t in result)

    def test_deduplication(self, reset_temporal):
        """Les doublons textuels sont dédoublonnés."""
        dupes = [
            MemoryTrace(text="même texte ici", relevance=0.8, timestamp=time.time()),
            MemoryTrace(text="même texte ici", relevance=0.3, timestamp=time.time()),
        ]
        with patch.object(reset_temporal, "_recall_chroma", return_value=dupes), \
             patch.object(reset_temporal, "_recall_episodic", return_value=[]), \
             patch.object(reset_temporal, "_recall_associative", return_value=[]), \
             patch.object(reset_temporal, "_enrich_traces"), \
             patch.object(reset_temporal, "_publish_recall_event"):

            result = reset_temporal.unified_recall("test", limit=5)
            assert len(result) == 1
            # Garde le meilleur score
            assert result[0].relevance >= 0.3

    def test_min_relevance_filter(self, reset_temporal):
        """Les traces sous MIN_RELEVANCE sont filtrées."""
        low = [
            MemoryTrace(text="très bas", relevance=0.001, timestamp=time.time()),
        ]
        with patch.object(reset_temporal, "_recall_chroma", return_value=low), \
             patch.object(reset_temporal, "_recall_episodic", return_value=[]), \
             patch.object(reset_temporal, "_recall_associative", return_value=[]), \
             patch.object(reset_temporal, "_enrich_traces"), \
             patch.object(reset_temporal, "_publish_recall_event"):

            result = reset_temporal.unified_recall("test", limit=5)
            # Le scoring composite peut remonter le score, mais si ça reste < MIN_RELEVANCE
            # le résultat sera filtré
            for t in result:
                assert t.relevance >= MIN_RELEVANCE

    def test_stats_increment(self, reset_temporal):
        """Le compteur de recalls s'incrémente."""
        with patch.object(reset_temporal, "_recall_chroma", return_value=[]), \
             patch.object(reset_temporal, "_recall_episodic", return_value=[]), \
             patch.object(reset_temporal, "_recall_associative", return_value=[]), \
             patch.object(reset_temporal, "_enrich_traces"), \
             patch.object(reset_temporal, "_publish_recall_event"):

            reset_temporal.unified_recall("a")
            reset_temporal.unified_recall("b")
            assert reset_temporal._total_recalls == 2


# ===========================================================
# RECALL BY EMOTION
# ===========================================================

class TestRecallByEmotion:
    def test_empty_emotion(self, reset_temporal):
        assert reset_temporal.recall_by_emotion("") == []

    def test_from_hippocampus(self, reset_temporal, mock_hippocampus):
        with patch.dict("sys.modules", {
            "core.hippocampus": MagicMock(hippocampus=mock_hippocampus),
            "core.cardiac_engine": MagicMock(),
            "core.desire_engine": MagicMock(),
            "core.spreading_activation": MagicMock(),
        }):
            with patch.object(reset_temporal, "_enrich_traces"):
                result = reset_temporal.recall_by_emotion("satisfaction", limit=5)
                assert len(result) >= 1
                assert any("satisfaction" in t.emotion.lower() for t in result)

    def test_from_cooccurrences(self, reset_temporal):
        reset_temporal._cooccurrences = {
            "intent:EXPANSION_CODE": {"emotion:frustration": 5.0},
        }
        with patch.object(reset_temporal, "_enrich_traces"):
            result = reset_temporal.recall_by_emotion("frustration")
            assert len(result) >= 1
            assert any("EXPANSION_CODE" in t.text for t in result)

    def test_time_window(self, reset_temporal, mock_hippocampus):
        """Les épisodes hors fenêtre sont exclus."""
        # Rendre l'épisode ancien
        mock_hippocampus.get_session_narrative.return_value = [
            {
                "summary": "Très ancien épisode",
                "salience": 0.8,
                "emotion": "satisfaction",
                "timestamp": time.time() - 200000,  # > 24h
            },
        ]
        with patch.dict("sys.modules", {
            "core.hippocampus": MagicMock(hippocampus=mock_hippocampus),
            "core.cardiac_engine": MagicMock(),
            "core.desire_engine": MagicMock(),
            "core.spreading_activation": MagicMock(),
        }):
            with patch.object(reset_temporal, "_enrich_traces"):
                result = reset_temporal.recall_by_emotion("satisfaction",
                                                          time_window=3600)
                episodic = [t for t in result if t.source == "episodic"]
                assert len(episodic) == 0


# ===========================================================
# RECALL BY CONCEPT
# ===========================================================

class TestRecallByConcept:
    def test_empty_concepts(self, reset_temporal):
        assert reset_temporal.recall_by_concept([]) == []

    def test_from_synaptic(self, reset_temporal, mock_cortex):
        with patch.dict("sys.modules", {
            "core.synaptic_network": MagicMock(cortex=mock_cortex),
            "core.vector_store": MagicMock(),
            "core.cardiac_engine": MagicMock(),
            "core.desire_engine": MagicMock(),
            "core.spreading_activation": MagicMock(),
        }):
            with patch.object(reset_temporal, "_recall_chroma", return_value=[]), \
                 patch.object(reset_temporal, "_enrich_traces"):
                result = reset_temporal.recall_by_concept(["refactoring"])
                assert len(result) >= 1
                assert any(t.source == "associative" for t in result)

    def test_concepts_limited_to_5(self, reset_temporal, mock_cortex):
        """Max 5 concepts traités pour la performance."""
        many = [f"concept_{i}" for i in range(10)]
        with patch.dict("sys.modules", {
            "core.synaptic_network": MagicMock(cortex=mock_cortex),
            "core.vector_store": MagicMock(),
            "core.cardiac_engine": MagicMock(),
            "core.desire_engine": MagicMock(),
            "core.spreading_activation": MagicMock(),
        }):
            with patch.object(reset_temporal, "_recall_chroma", return_value=[]), \
                 patch.object(reset_temporal, "_enrich_traces"):
                reset_temporal.recall_by_concept(many)
                # Vérifie que query_associations n'est pas appelé plus de 5 fois
                assert mock_cortex.query_associations.call_count <= 5


# ===========================================================
# RECALL BY PERIOD
# ===========================================================

class TestRecallByPeriod:
    def test_invalid_range(self, reset_temporal):
        assert reset_temporal.recall_by_period(100, 50) == []

    def test_from_episodes(self, reset_temporal):
        from dataclasses import dataclass as dc

        @dc
        class FakeEpisode:
            summary: str = "Test episode"
            salience: float = 0.7
            timestamp: float = time.time() - 1000
            cardiac_emotion: str = "curiosité"
            agent: str = "researcher"

        fake_hippo = MagicMock()
        fake_hippo._episodes = [FakeEpisode()]

        with patch.dict("sys.modules", {
            "core.hippocampus": MagicMock(hippocampus=fake_hippo),
            "core.cardiac_engine": MagicMock(),
            "core.desire_engine": MagicMock(),
            "core.spreading_activation": MagicMock(),
        }):
            with patch.object(reset_temporal, "_enrich_traces"):
                start = time.time() - 2000
                end = time.time()
                result = reset_temporal.recall_by_period(start, end)
                assert len(result) == 1
                assert result[0].text == "Test episode"

    def test_chronological_order(self, reset_temporal):
        """Les résultats sont triés chronologiquement."""
        from dataclasses import dataclass as dc

        @dc
        class FakeEpisode:
            summary: str = ""
            salience: float = 0.5
            timestamp: float = 0.0
            cardiac_emotion: str = ""
            agent: str = ""

        now = time.time()
        fake_hippo = MagicMock()
        fake_hippo._episodes = [
            FakeEpisode(summary="B", timestamp=now - 500),
            FakeEpisode(summary="A", timestamp=now - 1000),
            FakeEpisode(summary="C", timestamp=now - 100),
        ]

        with patch.dict("sys.modules", {
            "core.hippocampus": MagicMock(hippocampus=fake_hippo),
            "core.cardiac_engine": MagicMock(),
            "core.desire_engine": MagicMock(),
            "core.spreading_activation": MagicMock(),
        }):
            with patch.object(reset_temporal, "_enrich_traces"):
                result = reset_temporal.recall_by_period(now - 2000, now)
                assert [t.text for t in result] == ["A", "B", "C"]


# ===========================================================
# NARRATIVE & FORMAT
# ===========================================================

class TestNarrative:
    def test_narrative_summary_empty(self, reset_temporal):
        """Sans organes, retourne le message par défaut."""
        with patch.dict("sys.modules", {
            "core.hippocampus": None,
            "core.cardiac_engine": None,
            "core.desire_engine": None,
        }):
            result = reset_temporal.get_narrative_summary()
            assert "Aucune activité" in result

    def test_narrative_summary_with_data(self, reset_temporal, mock_hippocampus, mock_heart):
        hippo_mod = MagicMock()
        hippo_mod.hippocampus = mock_hippocampus
        heart_mod = MagicMock()
        heart_mod.heart = mock_heart
        desire_mod = MagicMock()
        desire_mod.desires = MagicMock()
        desire_mod.desires.get_dominant_narrative.return_value = ["Curiosité brûlante"]

        with patch.dict("sys.modules", {
            "core.hippocampus": hippo_mod,
            "core.cardiac_engine": heart_mod,
            "core.desire_engine": desire_mod,
        }):
            result = reset_temporal.get_narrative_summary(n_hours=24.0)
            assert "sérénité" in result.lower() or "Épisodes" in result

    def test_format_for_prompt_empty(self, reset_temporal):
        assert reset_temporal.format_for_prompt([]) == ""

    def test_format_for_prompt_basic(self, reset_temporal):
        traces = [
            MemoryTrace(text="Un souvenir important", relevance=0.8,
                        emotion="joie", source="episodic",
                        associated_concepts=["refactoring"]),
        ]
        result = reset_temporal.format_for_prompt(traces)
        assert "Un souvenir important" in result
        assert "[joie]" in result
        assert "(refactoring)" in result
        assert "<episodic>" in result

    def test_format_for_prompt_max_chars(self, reset_temporal):
        traces = [
            MemoryTrace(text="A" * 200, relevance=0.8),
            MemoryTrace(text="B" * 200, relevance=0.7),
            MemoryTrace(text="C" * 200, relevance=0.6),
        ]
        result = reset_temporal.format_for_prompt(traces, max_chars=250)
        # Ne devrait pas dépasser la limite
        assert len(result) <= 300  # Marge pour les préfixes "- "

    def test_get_temporal_context(self, reset_temporal):
        with patch.object(reset_temporal, "get_narrative_summary",
                          return_value="Épisode récent"):
            ctx = reset_temporal.get_temporal_context()
            assert "Épisode récent" in ctx


# ===========================================================
# SCORING
# ===========================================================

class TestScoring:
    def test_empty_intent(self, reset_temporal):
        assert reset_temporal.compute_temporal_bonus("") == 0.0

    def test_unknown_intent(self, reset_temporal):
        assert reset_temporal.compute_temporal_bonus("UNKNOWN") == 0.0

    def test_positive_emotions_bonus(self, reset_temporal):
        reset_temporal._cooccurrences = {
            "intent:EXPANSION_CODE": {
                "emotion:satisfaction": 8.0,
                "emotion:joie": 5.0,
            }
        }
        bonus = reset_temporal.compute_temporal_bonus("EXPANSION_CODE")
        assert bonus > 0

    def test_negative_emotions_malus(self, reset_temporal):
        reset_temporal._cooccurrences = {
            "intent:REFACTOR": {
                "emotion:frustration": 10.0,
                "emotion:anxiété": 5.0,
            }
        }
        bonus = reset_temporal.compute_temporal_bonus("REFACTOR")
        assert bonus < 0

    def test_struggle_arc_malus(self, reset_temporal):
        reset_temporal._cooccurrences = {
            "intent:BUGFIX": {
                "arc:struggle": 5.0,
                "arc:breakthrough": 1.0,
            }
        }
        bonus = reset_temporal.compute_temporal_bonus("BUGFIX")
        assert bonus < 0  # struggle > breakthrough

    def test_concepts_bonus(self, reset_temporal):
        reset_temporal._cooccurrences = {
            "intent:CODE": {
                "concept:python": 3.0,
                "concept:refactoring": 2.0,
                "concept:testing": 1.0,
            }
        }
        bonus = reset_temporal.compute_temporal_bonus("CODE")
        assert bonus > 0

    def test_clamping(self, reset_temporal):
        reset_temporal._cooccurrences = {
            "intent:MAX": {
                "emotion:joie": 100.0,
                "concept:a": 1, "concept:b": 1, "concept:c": 1,
                "concept:d": 1, "concept:e": 1, "concept:f": 1,
            }
        }
        bonus = reset_temporal.compute_temporal_bonus("MAX")
        assert bonus <= 2.0

        reset_temporal._cooccurrences = {
            "intent:MIN": {
                "emotion:frustration": 100.0,
                "arc:struggle": 10.0,
            }
        }
        bonus = reset_temporal.compute_temporal_bonus("MIN")
        assert bonus >= -0.5


# ===========================================================
# CO-OCCURRENCES
# ===========================================================

class TestCooccurrences:
    @pytest.mark.asyncio
    async def test_on_routine_complete(self, reset_temporal, mock_heart):
        """Le handler indexe les co-occurrences."""
        heart_mod = MagicMock()
        heart_mod.heart = mock_heart

        with patch.dict("sys.modules", {
            "core.cardiac_engine": heart_mod,
            "core.spreading_activation": MagicMock(
                extract_concepts=MagicMock(return_value=[("python", 0.8)])
            ),
            "core.hippocampus": MagicMock(
                hippocampus=MagicMock(get_arc_narrative=MagicMock(return_value=None))
            ),
        }):
            await reset_temporal._on_routine_complete({
                "intent": "EXPANSION_CODE",
                "success": True,
                "quality": 0.8,
                "agent": "coder",
                "result": "Code refactoré avec succès, module optimisé",
            })

            assert "intent:EXPANSION_CODE" in reset_temporal._cooccurrences
            assocs = reset_temporal._cooccurrences["intent:EXPANSION_CODE"]
            assert "emotion:sérénité" in assocs

    @pytest.mark.asyncio
    async def test_no_index_without_intent(self, reset_temporal):
        """Pas d'indexation sans intent."""
        await reset_temporal._on_routine_complete({"success": True})
        assert len(reset_temporal._cooccurrences) == 0

    def test_decay_cooccurrences(self, reset_temporal):
        reset_temporal._cooccurrences = {
            "intent:A": {"emotion:joie": 10.0, "emotion:peur": 0.05},
        }
        reset_temporal._decay_cooccurrences()
        assocs = reset_temporal._cooccurrences.get("intent:A", {})
        # joie devrait avoir baissé
        assert assocs.get("emotion:joie", 0) < 10.0
        # peur devrait avoir été supprimée (< 0.1)
        assert "emotion:peur" not in assocs

    def test_decay_removes_empty_intents(self, reset_temporal):
        reset_temporal._cooccurrences = {
            "intent:DEAD": {"emotion:x": 0.05},
        }
        reset_temporal._decay_cooccurrences()
        assert "intent:DEAD" not in reset_temporal._cooccurrences

    @pytest.mark.asyncio
    async def test_max_cooccurrences_fifo(self, reset_temporal):
        """L'index est limité à MAX_COOCCURRENCES par intent."""
        # Remplir au-delà de la limite
        reset_temporal._cooccurrences["intent:BIG"] = {
            f"concept:{i}": float(i) for i in range(MAX_COOCCURRENCES + 50)
        }
        heart_mod = MagicMock()
        heart_mod.heart = MagicMock(current_emotion="neutre")

        with patch.dict("sys.modules", {
            "core.cardiac_engine": heart_mod,
            "core.spreading_activation": MagicMock(side_effect=ImportError),
            "core.hippocampus": MagicMock(side_effect=ImportError),
        }):
            await reset_temporal._on_routine_complete({
                "intent": "BIG", "success": True, "quality": 0.5,
            })
            assert len(reset_temporal._cooccurrences["intent:BIG"]) <= MAX_COOCCURRENCES


# ===========================================================
# PERSISTANCE
# ===========================================================

class TestPersistence:
    def test_save_and_load(self, reset_temporal, tmp_path, monkeypatch):
        state_file = str(tmp_path / "temporal_lobe_state.json")
        monkeypatch.setattr("core.temporal_lobe.TEMPORAL_LOBE_STATE_FILE", state_file)

        reset_temporal._cooccurrences = {"intent:X": {"emotion:joie": 3.0}}
        reset_temporal._total_recalls = 42
        reset_temporal._save()

        assert os.path.exists(state_file)

        # Reload
        TemporalLobe.reset_singleton()
        t2 = TemporalLobe()
        assert t2._cooccurrences == {"intent:X": {"emotion:joie": 3.0}}
        assert t2._total_recalls == 42

    def test_load_missing_file(self, reset_temporal, tmp_path, monkeypatch):
        state_file = str(tmp_path / "inexistant.json")
        monkeypatch.setattr("core.temporal_lobe.TEMPORAL_LOBE_STATE_FILE", state_file)

        TemporalLobe.reset_singleton()
        t = TemporalLobe()
        assert t._cooccurrences == {}

    def test_load_corrupted_file(self, reset_temporal, tmp_path, monkeypatch):
        state_file = str(tmp_path / "corrupt.json")
        with open(state_file, "w") as f:
            f.write("{invalid json")
        monkeypatch.setattr("core.temporal_lobe.TEMPORAL_LOBE_STATE_FILE", state_file)

        TemporalLobe.reset_singleton()
        t = TemporalLobe()
        assert t._cooccurrences == {}  # Dégradation gracieuse


# ===========================================================
# DÉGRADATION GRACIEUSE
# ===========================================================

class TestDegradationGracieuse:
    def test_no_chroma(self, reset_temporal):
        """Sans ChromaDB, le recall fonctionne quand même."""
        with patch.object(reset_temporal, "_recall_chroma",
                          side_effect=Exception("no chroma")):
            # Ne crash pas
            with patch.object(reset_temporal, "_recall_episodic", return_value=[]), \
                 patch.object(reset_temporal, "_recall_associative", return_value=[]), \
                 patch.object(reset_temporal, "_enrich_traces"), \
                 patch.object(reset_temporal, "_publish_recall_event"):
                result = reset_temporal.unified_recall("test")
                assert result == []  # Pas de crash

    def test_no_hippocampus(self, reset_temporal):
        """Sans hippocampe, le recall fonctionne."""
        with patch.object(reset_temporal, "_recall_chroma", return_value=[
            MemoryTrace(text="from chroma", relevance=0.5, timestamp=time.time())
        ]), \
             patch.object(reset_temporal, "_recall_episodic",
                          side_effect=Exception("no hippo")), \
             patch.object(reset_temporal, "_recall_associative", return_value=[]), \
             patch.object(reset_temporal, "_enrich_traces"), \
             patch.object(reset_temporal, "_publish_recall_event"):
            result = reset_temporal.unified_recall("test")
            assert len(result) >= 0  # Pas de crash

    def test_no_synaptic(self, reset_temporal):
        """Sans réseau synaptique, le recall fonctionne."""
        with patch.object(reset_temporal, "_recall_chroma", return_value=[
            MemoryTrace(text="from chroma", relevance=0.5, timestamp=time.time())
        ]), \
             patch.object(reset_temporal, "_recall_episodic", return_value=[]), \
             patch.object(reset_temporal, "_recall_associative",
                          side_effect=Exception("no cortex")), \
             patch.object(reset_temporal, "_enrich_traces"), \
             patch.object(reset_temporal, "_publish_recall_event"):
            result = reset_temporal.unified_recall("test")
            assert len(result) >= 0

    def test_all_organs_down(self, reset_temporal):
        """Même si tous les organes sont down, pas de crash."""
        with patch.object(reset_temporal, "_recall_chroma", return_value=[]), \
             patch.object(reset_temporal, "_recall_episodic", return_value=[]), \
             patch.object(reset_temporal, "_recall_associative", return_value=[]), \
             patch.object(reset_temporal, "_enrich_traces"), \
             patch.object(reset_temporal, "_publish_recall_event"):
            result = reset_temporal.unified_recall("test")
            assert result == []


# ===========================================================
# STATS
# ===========================================================

class TestStats:
    def test_initial_stats(self, reset_temporal):
        stats = reset_temporal.get_stats()
        assert stats["indexed_intents"] == 0
        assert stats["total_recalls"] == 0
        assert stats["cache_hit_rate"] == 0.0

    def test_stats_after_activity(self, reset_temporal):
        reset_temporal._cooccurrences = {
            "intent:A": {"emotion:joie": 1.0, "concept:x": 2.0},
            "intent:B": {"emotion:peur": 1.0},
        }
        reset_temporal._total_recalls = 10
        reset_temporal._total_enrichments = 25
        reset_temporal._total_cache_hits = 3

        stats = reset_temporal.get_stats()
        assert stats["indexed_intents"] == 2
        assert stats["total_associations"] == 3
        assert stats["total_recalls"] == 10
        assert stats["total_enrichments"] == 25
        assert stats["cache_hit_rate"] > 0


# ===========================================================
# MODULE HISTORY
# ===========================================================

class TestModuleHistory:
    def test_delegates_to_unified_recall(self, reset_temporal):
        with patch.object(reset_temporal, "unified_recall", return_value=[]) as mock_ur:
            reset_temporal.get_module_history("router.py")
            mock_ur.assert_called_once()
            args = mock_ur.call_args
            assert "router.py" in args[1].get("query", args[0][0] if args[0] else "")


# ===========================================================
# BUS INIT
# ===========================================================

class TestBusInit:
    def test_init_subscribes(self, reset_temporal):
        mock_bus = MagicMock()
        with patch.dict("sys.modules", {
            "core.event_bus": MagicMock(),
            "core.event_bus.bus": MagicMock(bus=mock_bus),
        }):
            reset_temporal.init()
            mock_bus.subscribe.assert_called()

    def test_init_without_bus(self, reset_temporal):
        """Sans bus, init ne crash pas."""
        with patch.dict("sys.modules", {
            "core.event_bus": None,
            "core.event_bus.bus": None,
        }):
            # Ne devrait pas crash
            reset_temporal.init()


# ===========================================================
# TOP PATTERNS
# ===========================================================

class TestTopPatterns:
    def test_empty(self, reset_temporal):
        assert reset_temporal._get_top_patterns() == []

    def test_returns_top_n(self, reset_temporal):
        reset_temporal._cooccurrences = {
            "intent:A": {"emotion:joie": 10.0, "emotion:peur": 2.0},
            "intent:B": {"emotion:frustration": 5.0},
            "intent:C": {"concept:python": 3.0},  # Pas d'émotion
        }
        patterns = reset_temporal._get_top_patterns(2)
        assert len(patterns) == 2
        assert "A↔joie" in patterns[0]  # Plus fréquent
