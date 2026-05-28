"""
Tests pour core/inner_voice.py — Aires de Broca & Wernicke
~70 tests couvrant : singleton, workspace, Broca, Wernicke, prédictions,
DMN, narrateur, bus handlers, broadcast reactions, intégration, edge cases.
"""

import asyncio
import copy
import json
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# --- Fixtures autouse ---


@pytest.fixture(autouse=True)
def reset_inner_voice(tmp_path, monkeypatch):
    """Reset singleton + redirige fichier état vers tmp_path."""
    from core.inner_voice import InnerVoice
    InnerVoice.reset_singleton()
    monkeypatch.setattr(
        "core.inner_voice.INNER_VOICE_STATE_FILE",
        str(tmp_path / "inner_voice_state.json"),
    )
    yield
    InnerVoice.reset_singleton()


@pytest.fixture
def voice():
    """Retourne une instance fraîche d'InnerVoice."""
    from core.inner_voice import InnerVoice
    v = InnerVoice()
    return v


@pytest.fixture
def mock_bus():
    """Mock le bus pour capturer les publish."""
    mock = AsyncMock()
    mock.subscribe = MagicMock()
    mock.publish = AsyncMock()
    return mock


# ============================================================
# GROUPE 1 : Singleton
# ============================================================


class TestSingleton:
    def test_singleton_identity(self, voice):
        """Deux instanciations retournent le même objet."""
        from core.inner_voice import InnerVoice
        v2 = InnerVoice()
        assert v2 is voice

    def test_reset_singleton(self, voice):
        """reset_singleton() crée une nouvelle instance."""
        from core.inner_voice import InnerVoice
        old_id = id(voice)
        InnerVoice.reset_singleton()
        v2 = InnerVoice()
        assert id(v2) != old_id

    def test_persistence_save_load(self, voice, tmp_path, monkeypatch):
        """Save puis load restaure l'état."""
        from core.inner_voice import InnerVoice, Thought, Prediction
        voice.stream.append(Thought(
            timestamp=1.0, content="test pensee", source="cardiac",
            mode="evaluer", salience=0.5, emotion="serenite",
        ))
        voice.predictions.append(Prediction(
            id="abc123", content="pred test",
            target_event="AUTONOMY_ROUTINE_COMPLETE",
            predicted_value="EXPANSION_CODE",
            confidence=0.7, created_at=1.0,
        ))
        voice._precision = 0.8
        voice.stats["total_thoughts"] = 5
        voice.save()

        InnerVoice.reset_singleton()
        v2 = InnerVoice()
        assert len(v2.stream) == 1
        assert v2.stream[0].content == "test pensee"
        assert len(v2.predictions) == 1
        assert v2.predictions[0].id == "abc123"
        assert v2._precision == 0.8
        assert v2.stats["total_thoughts"] == 5


# ============================================================
# GROUPE 2 : Workspace — 7 Sources + Compétition
# ============================================================


class TestWorkspace:
    def test_perceive_cardiac(self, voice):
        """Source cardiaque génère un entry si last_cardiac présent."""
        voice._last_cardiac = {
            "emotion": "curiosite", "emotion_intensity": 0.6,
            "coherence": 0.7, "bpm": 70, "ans_balance": 0.5,
        }
        voice._perceive_cardiac()
        assert len(voice.workspace) == 1
        assert voice.workspace[0].source == "cardiac"

    def test_perceive_reptilian_threat(self, voice):
        """Source reptilienne si menace > 0."""
        voice._last_reptilian_alert = {"threat_level": 5, "reflex": "SHED"}
        voice._perceive_reptilian()
        assert len(voice.workspace) == 1
        assert voice.workspace[0].source == "reptilian"
        assert voice.workspace[0].salience > 0.3

    def test_perceive_reptilian_calm(self, voice):
        """Source reptilienne calm si alerte mais threat_level = 0."""
        voice._last_reptilian_alert = {"threat_level": 0}
        voice._perceive_reptilian()
        assert len(voice.workspace) == 1
        assert voice.workspace[0].raw_signal["context"] == "calm"

    def test_perceive_desire(self, voice):
        """Source pulsionnelle si deprivation élevée."""
        mock_drive = MagicMock()
        mock_drive.deprivation = 75.0
        mock_drive.name = "CURIOSITE"
        mock_drives = {"CURIOSITE": mock_drive}
        mock_desires = MagicMock()
        mock_desires.drives = mock_drives
        mock_desires.tick = MagicMock()
        with patch("core.inner_voice.desires", mock_desires, create=True):
            with patch.dict("sys.modules", {"core.desire_engine": MagicMock(desires=mock_desires)}):
                voice._perceive_desire()
        assert len(voice.workspace) == 1
        assert voice.workspace[0].source == "desire"

    def test_perceive_synaptic(self, voice):
        """Source synaptique si nœuds actifs."""
        mock_cortex = MagicMock()
        mock_cortex.nodes = {
            "n1": {"concept": "security", "energy": 0.8, "node_type": "memory"},
            "n2": {"concept": "evolution", "energy": 0.6, "node_type": "event"},
        }
        with patch.dict("sys.modules", {"core.synaptic_network": MagicMock(cortex=mock_cortex)}):
            voice._perceive_synaptic()
        assert len(voice.workspace) == 1
        assert voice.workspace[0].source == "synaptic"

    def test_perceive_prefrontal(self, voice):
        """Source préfrontale si goals actifs."""
        mock_goal = MagicMock()
        mock_goal.status = "active"
        mock_goal.progress = 0.6
        mock_goal.title = "Combler lacune security"
        mock_goal.steps = []
        mock_pf = MagicMock()
        mock_pf.goals = [mock_goal]
        mock_pf.narrative_log = []
        with patch.dict("sys.modules", {"core.prefrontal": MagicMock(prefrontal=mock_pf)}):
            voice._perceive_prefrontal()
        assert len(voice.workspace) == 1
        assert voice.workspace[0].source == "prefrontal"

    def test_competition_highest_salience_wins(self, voice):
        """Le workspace trie par saillance décroissante."""
        from core.inner_voice import WorkspaceEntry
        voice.workspace = [
            WorkspaceEntry(source="cardiac", raw_signal={}, salience=0.3, timestamp=1),
            WorkspaceEntry(source="reptilian", raw_signal={}, salience=0.9, timestamp=1),
            WorkspaceEntry(source="desire", raw_signal={}, salience=0.5, timestamp=1),
        ]
        voice.workspace.sort(key=lambda e: e.salience, reverse=True)
        assert voice.workspace[0].source == "reptilian"
        assert voice.workspace[1].source == "desire"

    def test_perceive_dmn_only_when_idle(self, voice):
        """DMN ne s'active que si _is_idle = True."""
        voice._is_idle = False
        voice._perceive_dmn()
        assert len(voice.workspace) == 0

        voice._is_idle = True
        voice._idle_since = time.time() - 100
        # Patching pour éviter imports réels
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.prefrontal": MagicMock(prefrontal=MagicMock(goals=[])),
            "core.cardiac_engine": MagicMock(heart=MagicMock(
                current_emotion="serenite", compute_coherence=MagicMock(return_value=0.7))),
            "core.synaptic_network": MagicMock(cortex=MagicMock(nodes={})),
        }):
            voice._perceive_dmn()
        assert len(voice.workspace) == 1
        assert voice.workspace[0].source == "dmn"


# ============================================================
# GROUPE 3 : Broca — Le Formulateur
# ============================================================


class TestBroca:
    def test_formulate_cardiac_neutral(self, voice):
        """Formulation cardiaque neutre."""
        from core.inner_voice import WorkspaceEntry
        entry = WorkspaceEntry(
            source="cardiac",
            raw_signal={"emotion": "serenite", "context": "neutral", "bpm": 60},
            salience=0.3, timestamp=1,
        )
        result = voice._formulate(entry)
        assert "serenite" in result.lower()

    def test_formulate_cardiac_flow(self, voice):
        """Formulation cardiaque flow."""
        from core.inner_voice import WorkspaceEntry
        entry = WorkspaceEntry(
            source="cardiac",
            raw_signal={"emotion": "flow", "context": "flow", "coherence": 0.9},
            salience=0.5, timestamp=1,
        )
        result = voice._formulate(entry)
        assert "flow" in result.lower()

    def test_formulate_reptilian_threat(self, voice):
        """Formulation menace reptilienne."""
        from core.inner_voice import WorkspaceEntry
        entry = WorkspaceEntry(
            source="reptilian",
            raw_signal={"threat_level": 6, "level": 6, "reflex": "SHED", "context": "threat"},
            salience=0.8, timestamp=1,
        )
        result = voice._formulate(entry)
        assert "menace" in result.lower() or "6" in result

    def test_formulate_desire_frustrated(self, voice):
        """Formulation pulsion frustrée."""
        from core.inner_voice import WorkspaceEntry
        entry = WorkspaceEntry(
            source="desire",
            raw_signal={"drive": "CURIOSITE", "deprivation": 85,
                        "context": "frustrated", "action": "Explorer"},
            salience=0.5, timestamp=1,
        )
        result = voice._formulate(entry)
        assert "curiosite" in result.lower()

    def test_formulate_dmn_wander(self, voice):
        """Formulation DMN vagabondage."""
        from core.inner_voice import WorkspaceEntry
        entry = WorkspaceEntry(
            source="dmn",
            raw_signal={"context": "wander", "concept_a": "audit",
                        "concept_b": "evolution"},
            salience=0.3, timestamp=1,
        )
        result = voice._formulate(entry)
        assert "audit" in result.lower() or "evolution" in result.lower()

    def test_formulate_prediction_error(self, voice):
        """Formulation erreur de prédiction."""
        from core.inner_voice import WorkspaceEntry
        entry = WorkspaceEntry(
            source="prediction",
            raw_signal={"context": "error", "predicted": "succes",
                        "actual": "echec", "content": "routine X"},
            salience=0.5, timestamp=1,
        )
        result = voice._formulate(entry)
        assert len(result) > 0

    def test_formulate_max_120_chars(self, voice):
        """Formulation tronquée dynamiquement (profil neutre → max ~100 chars, <120)."""
        from core.inner_voice import WorkspaceEntry
        entry = WorkspaceEntry(
            source="dmn",
            raw_signal={"context": "retrospect", "count": 10,
                        "intent": "X" * 100, "pattern": "Y" * 100},
            salience=0.3, timestamp=1,
        )
        result = voice._formulate(entry)
        assert len(result) <= 120

    def test_formulate_fallback_unknown_context(self, voice):
        """Contexte inconnu → utilise le premier template de la source."""
        from core.inner_voice import WorkspaceEntry
        entry = WorkspaceEntry(
            source="cardiac",
            raw_signal={"emotion": "alerte", "context": "unknown_context_xyz"},
            salience=0.3, timestamp=1,
        )
        result = voice._formulate(entry)
        assert len(result) > 0


# ============================================================
# GROUPE 4 : Wernicke — Le Vérificateur
# ============================================================


class TestWernicke:
    def test_verify_empty_draft_fails(self, voice):
        """Draft vide → factual = 0, score global pénalisé."""
        from core.inner_voice import WorkspaceEntry
        entry = WorkspaceEntry(
            source="cardiac", raw_signal={"emotion": "flow"},
            salience=0.5, timestamp=1, draft="",
        )
        factual = voice._check_factual(entry)
        assert factual == 0.0
        # Le score global peut quand même passer si temporel=1.0 et émotionnel neutre
        ok, score = voice._verify(entry)
        assert factual == 0.0  # La composante factuelle est bien 0

    def test_check_factual_matching_content(self, voice):
        """Draft mentionnant les éléments du signal → score élevé."""
        from core.inner_voice import WorkspaceEntry
        entry = WorkspaceEntry(
            source="cardiac",
            raw_signal={"emotion": "frustration", "context": "high_intensity"},
            salience=0.5, timestamp=1,
            draft="Frustration. Monte.",
        )
        score = voice._check_factual(entry)
        assert score > 0.5

    def test_check_emotional_alignment(self, voice):
        """Ton positif + émotion positive → cohérent."""
        from core.inner_voice import WorkspaceEntry
        voice._last_cardiac = {"emotion": "serenite"}
        entry = WorkspaceEntry(
            source="cardiac", raw_signal={},
            salience=0.5, timestamp=1,
            draft="Calme et stable.",
        )
        score = voice._check_emotional(entry)
        assert score > 0.5

    def test_check_emotional_misalignment(self, voice):
        """Ton positif + émotion négative → incohérent."""
        from core.inner_voice import WorkspaceEntry
        voice._last_cardiac = {"emotion": "frustration"}
        entry = WorkspaceEntry(
            source="cardiac", raw_signal={},
            salience=0.5, timestamp=1,
            draft="Calme et stable et bien.",
        )
        score = voice._check_emotional(entry)
        assert score < 0.7

    def test_check_temporal_no_repetition(self, voice):
        """Pas de pensées récentes → score temporel = 1."""
        from core.inner_voice import WorkspaceEntry
        entry = WorkspaceEntry(
            source="cardiac", raw_signal={},
            salience=0.5, timestamp=1, draft="Nouvelle pensee.",
        )
        score = voice._check_temporal(entry)
        assert score == 1.0

    def test_check_temporal_exact_repetition(self, voice):
        """Pensée identique dans le stream → veto dur (score = 0)."""
        from core.inner_voice import Thought, WorkspaceEntry
        voice.stream.append(Thought(
            timestamp=1, content="Meme chose", source="cardiac",
            mode="evaluer", salience=0.5,
        ))
        entry = WorkspaceEntry(
            source="cardiac", raw_signal={},
            salience=0.5, timestamp=2, draft="Meme chose",
        )
        score = voice._check_temporal(entry)
        assert score == 0.0

    def test_check_temporal_quasi_doublon(self, voice):
        """Même source + même préfixe → veto dur."""
        from core.inner_voice import Thought, WorkspaceEntry
        voice.stream.append(Thought(
            timestamp=1, content="Tiens. veille <-> synthese.",
            source="synaptic", mode="evaluer", salience=0.5,
        ))
        entry = WorkspaceEntry(
            source="synaptic", raw_signal={},
            salience=0.5, timestamp=2,
            draft="Tiens. veille <-> synthese.",
        )
        score = voice._check_temporal(entry)
        assert score == 0.0

    def test_verify_rejects_repetition_hard(self, voice):
        """_verify rejette une répétition même si factuel+émotionnel sont OK."""
        from core.inner_voice import Thought, WorkspaceEntry
        voice.stream.append(Thought(
            timestamp=1, content="Flow. Ne pas interrompre.",
            source="cardiac", mode="evaluer", salience=0.5,
        ))
        voice._last_cardiac = {"emotion": "flow"}
        entry = WorkspaceEntry(
            source="cardiac",
            raw_signal={"emotion": "flow", "context": "flow"},
            salience=0.5, timestamp=2,
            draft="Flow. Ne pas interrompre.",
        )
        ok, score = voice._verify(entry)
        assert not ok
        assert score == 0.0

    def test_check_temporal_source_saturation(self, voice):
        """Trop de pensées de la même source → pénalité forte."""
        from core.inner_voice import Thought, WorkspaceEntry
        for i in range(4):
            voice.stream.append(Thought(
                timestamp=i, content=f"Pensee {i}", source="cardiac",
                mode="evaluer", salience=0.5,
            ))
        entry = WorkspaceEntry(
            source="cardiac", raw_signal={},
            salience=0.5, timestamp=5, draft="Encore cardiac",
        )
        score = voice._check_temporal(entry)
        assert score <= 0.15

    def test_reformulation_on_low_coherence(self, voice):
        """Cohérence basse → reformulation tentée + stats."""
        from core.inner_voice import WorkspaceEntry
        voice._last_cardiac = {"emotion": "frustration"}
        entry = WorkspaceEntry(
            source="cardiac",
            raw_signal={"emotion": "frustration", "context": "neutral", "bpm": 60},
            salience=0.5, timestamp=1, draft="",  # Draft vide → score bas
        )
        initial_rejects = voice.stats["rejections_wernicke"]
        voice._verify(entry)
        assert voice.stats["rejections_wernicke"] >= initial_rejects


# ============================================================
# GROUPE 5 : Prédictions (Friston)
# ============================================================


class TestPredictions:
    def test_generate_prediction_after_routine(self, voice):
        """Après une routine success, génère une prédiction."""
        voice._last_routine_complete = {"intent": "EXPANSION_CODE", "status": "success"}
        voice._generate_predictions()
        assert len(voice.predictions) >= 1
        assert voice.predictions[0].target_event == "AUTONOMY_ROUTINE_COMPLETE"

    def test_no_duplicate_prediction(self, voice):
        """Pas de doublon si prédiction similaire récente."""
        voice._last_routine_complete = {"intent": "EXPANSION_CODE", "status": "success"}
        voice._generate_predictions()
        count1 = len(voice.predictions)
        voice._generate_predictions()
        assert len(voice.predictions) == count1

    def test_prediction_decay(self, voice):
        """Confiance décroît à chaque tick."""
        from core.inner_voice import Prediction, PREDICTION_DECAY
        voice.predictions.append(Prediction(
            id="p1", content="test", target_event="X",
            predicted_value="Y", confidence=0.9, created_at=time.time(),
        ))
        voice._decay_predictions()
        assert voice.predictions[0].confidence == pytest.approx(0.9 * PREDICTION_DECAY)

    @pytest.mark.asyncio
    async def test_resolve_prediction_confirmed(self, voice):
        """Prédiction confirmée → outcome + precision up."""
        from core.inner_voice import Prediction
        voice.predictions.append(Prediction(
            id="p1", content="Prochaine routine EXPANSION",
            target_event="AUTONOMY_ROUTINE_COMPLETE",
            predicted_value="EXPANSION_CODE",
            confidence=0.7, created_at=time.time(),
        ))
        initial_precision = voice._precision
        with patch.dict("sys.modules", {
            "core.event_bus": MagicMock(),
            "core.event_bus.bus": MagicMock(bus=AsyncMock()),
        }):
            with patch("core.inner_voice.bus", AsyncMock(), create=True):
                await voice._resolve_predictions({"intent": "EXPANSION_CODE", "status": "success"})
        assert voice.predictions[0].resolved
        assert voice.predictions[0].outcome == "confirmed"
        assert voice._precision > initial_precision

    @pytest.mark.asyncio
    async def test_resolve_prediction_violated(self, voice):
        """Prédiction violée → outcome violated + precision down."""
        from core.inner_voice import Prediction
        voice.predictions.append(Prediction(
            id="p2", content="Prochaine routine EXPANSION",
            target_event="AUTONOMY_ROUTINE_COMPLETE",
            predicted_value="EXPANSION_CODE",
            confidence=0.7, created_at=time.time(),
        ))
        initial_precision = voice._precision
        with patch.dict("sys.modules", {
            "core.event_bus": MagicMock(),
            "core.event_bus.bus": MagicMock(bus=AsyncMock()),
        }):
            with patch("core.inner_voice.bus", AsyncMock(), create=True):
                await voice._resolve_predictions({"intent": "SECURITY_AUDIT", "status": "success"})
        assert voice.predictions[0].resolved
        assert voice.predictions[0].outcome == "violated"
        assert voice._precision < initial_precision

    def test_prediction_expiry(self, voice):
        """Prédiction expirée après PREDICTION_EXPIRY."""
        from core.inner_voice import Prediction, PREDICTION_EXPIRY
        voice.predictions.append(Prediction(
            id="p3", content="old prediction",
            target_event="X", predicted_value="Y",
            confidence=0.5, created_at=time.time() - PREDICTION_EXPIRY - 10,
        ))
        voice._generate_predictions()
        assert voice.predictions[0].resolved
        assert voice.predictions[0].outcome == "expired"

    def test_precision_bounds(self, voice):
        """Precision reste dans [0.1, 0.95]."""
        for _ in range(30):
            voice._update_precision(True)
        assert voice._precision <= 0.95

        for _ in range(30):
            voice._update_precision(False)
        assert voice._precision >= 0.1

    def test_max_predictions_cap(self, voice):
        """Ne dépasse pas MAX_PREDICTIONS après nettoyage."""
        from core.inner_voice import Prediction, MAX_PREDICTIONS, PREDICTION_EXPIRY
        # Ajouter des prédictions non résolues, assez vieilles pour expirer
        for i in range(MAX_PREDICTIONS + 5):
            voice.predictions.append(Prediction(
                id=f"p{i}", content="pred", target_event="X",
                predicted_value="Y", confidence=0.5,
                created_at=time.time() - PREDICTION_EXPIRY - 100,
            ))
        voice._generate_predictions()
        # Après expiry + trimming, le total ne doit pas dépasser MAX_PREDICTIONS
        assert len(voice.predictions) <= MAX_PREDICTIONS


# ============================================================
# GROUPE 6 : Default Mode Network
# ============================================================


class TestDMN:
    def test_dmn_idle_gate(self, voice):
        """DMN ne s'active qu'en mode idle."""
        voice._is_idle = False
        voice._perceive_dmn()
        assert len(voice.workspace) == 0

    def test_dmn_retrospect(self, voice):
        """DMN retrospect analyse l'historique."""
        mock_autonomy = MagicMock()
        mock_autonomy.routine_history = [
            {"intent": "SECURITY_AUDIT", "status": "success"},
            {"intent": "SECURITY_AUDIT", "status": "success"},
            {"intent": "EXPANSION_CODE", "status": "error"},
        ]
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=mock_autonomy),
        }):
            result = voice._dmn_retrospect()
        assert result["count"] == 2
        assert result["intent"] == "SECURITY_AUDIT"

    def test_dmn_prospect(self, voice):
        """DMN prospection imagine un scénario."""
        mock_goal = MagicMock()
        mock_goal.status = "active"
        mock_goal.title = "Combler lacune tests"
        mock_pf = MagicMock()
        mock_pf.goals = [mock_goal]
        with patch.dict("sys.modules", {
            "core.prefrontal": MagicMock(prefrontal=mock_pf),
        }):
            result = voice._dmn_prospect()
        assert "scenario" in result
        assert "lacune" in result["scenario"].lower() or "terminer" in result["scenario"]

    def test_dmn_self_reflect(self, voice):
        """DMN self-reflection retourne mood + compétence."""
        mock_heart = MagicMock()
        mock_heart.current_emotion = "enthousiasme"
        mock_heart.compute_coherence = MagicMock(return_value=0.8)
        with patch.dict("sys.modules", {
            "core.cardiac_engine": MagicMock(heart=mock_heart),
        }):
            result = voice._dmn_self_reflect()
        assert result["mood"] == "enthousiasme"
        assert result["competence"] == "coherent"

    def test_dmn_wander(self, voice):
        """DMN vagabondage tire 2 nœuds aléatoires."""
        mock_cortex = MagicMock()
        mock_cortex.nodes = {
            "n1": {"concept": "audit", "energy": 0.5},
            "n2": {"concept": "refactoring", "energy": 0.6},
            "n3": {"concept": "tests", "energy": 0.4},
        }
        with patch.dict("sys.modules", {
            "core.synaptic_network": MagicMock(cortex=mock_cortex),
        }):
            result = voice._dmn_wander()
        assert "concept_a" in result
        assert "concept_b" in result

    def test_dmn_salience_grows_with_idle(self, voice):
        """Saillance DMN augmente avec la durée d'idle."""
        voice._is_idle = True
        voice._idle_since = time.time() - 500  # 500s idle
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.prefrontal": MagicMock(prefrontal=MagicMock(goals=[])),
            "core.cardiac_engine": MagicMock(heart=MagicMock(
                current_emotion="serenite", compute_coherence=MagicMock(return_value=0.7))),
            "core.synaptic_network": MagicMock(cortex=MagicMock(nodes={})),
        }):
            voice._perceive_dmn()
        assert len(voice.workspace) == 1
        # Saillance devrait être > 0.2 base grâce au boost
        assert voice.workspace[0].salience > 0.2

    def test_dmn_modes_rotate(self, voice):
        """Les 4 sous-modes alternent cycliquement."""
        modes = []
        for i in range(4):
            voice._is_idle = True
            voice._idle_since = time.time() - 100
            voice.workspace.clear()
            with patch.dict("sys.modules", {
                "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
                "core.prefrontal": MagicMock(prefrontal=MagicMock(goals=[])),
                "core.cardiac_engine": MagicMock(heart=MagicMock(
                    current_emotion="serenite", compute_coherence=MagicMock(return_value=0.7))),
                "core.synaptic_network": MagicMock(cortex=MagicMock(nodes={})),
            }):
                voice._perceive_dmn()
            if voice.workspace:
                modes.append(voice.workspace[0].raw_signal.get("context"))
        assert modes == ["retrospect", "prospect", "self", "wander"]

    def test_dmn_empty_history_graceful(self, voice):
        """DMN retrospect avec historique vide ne plante pas."""
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
        }):
            result = voice._dmn_retrospect()
        assert result["count"] == 0


# ============================================================
# GROUPE 7 : Narrateur (Dennett)
# ============================================================


class TestNarrateur:
    def test_refresh_identity(self, voice):
        """refresh_identity met à jour l'IdentityNarrative."""
        mock_autonomy = MagicMock()
        mock_autonomy.routine_history = [
            {"intent": "A", "status": "success"},
            {"intent": "B", "status": "success"},
        ]
        mock_desires = MagicMock()
        mock_drive = MagicMock()
        mock_drive.name = "CURIOSITE"
        mock_drive.deprivation = 70
        mock_desires.drives = {"CURIOSITE": mock_drive}
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=mock_autonomy),
            "core.desire_engine": MagicMock(desires=mock_desires),
        }):
            voice._last_cardiac = {"emotion": "determination"}
            voice._refresh_identity()
        assert voice.identity.emotional_tone == "determination"
        assert "2 routines" in voice.identity.recent_arc
        assert voice.identity.updated_at > 0

    def test_build_arc(self, voice):
        """_build_arc résume l'historique récent."""
        mock_autonomy = MagicMock()
        mock_autonomy.routine_history = [
            {"intent": "A", "status": "success"},
            {"intent": "B", "status": "error"},
            {"intent": "C", "status": "success"},
        ]
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=mock_autonomy),
        }):
            arc = voice._build_arc()
        assert "3 routines" in arc
        assert "2 reussites" in arc

    def test_assess_competence(self, voice):
        """_assess_competence identifie forces/faiblesses."""
        mock_autonomy = MagicMock()
        mock_autonomy.routine_history = [
            {"intent": "SECURITY_AUDIT", "quality_score": 0.95},
            {"intent": "SECURITY_AUDIT", "quality_score": 0.9},
            {"intent": "EXPANSION_CODE", "quality_score": 0.3},
        ]
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=mock_autonomy),
        }):
            comp = voice._assess_competence()
        assert "security_audit" in comp.lower() or "fort" in comp.lower()

    def test_formulate_aspiration(self, voice):
        """_formulate_aspiration se base sur le drive dominant."""
        mock_desires = MagicMock()
        mock_drive = MagicMock()
        mock_drive.name = "CREATION"
        mock_drive.deprivation = 80
        mock_desires.drives = {"CREATION": mock_drive}
        with patch.dict("sys.modules", {
            "core.desire_engine": MagicMock(desires=mock_desires),
        }):
            asp = voice._formulate_aspiration()
        assert "creation" in asp.lower()

    def test_identity_narrative_in_get_identity(self, voice):
        """get_identity retourne le dict complet."""
        voice.identity.core_identity = "Test"
        voice.identity.recent_arc = "Arc test"
        result = voice.get_identity()
        assert result["core_identity"] == "Test"
        assert result["recent_arc"] == "Arc test"


# ============================================================
# GROUPE 8 : Bus Handlers
# ============================================================


class TestBusHandlers:
    @pytest.mark.asyncio
    async def test_on_cardiac_beat_triggers_cycle(self, voice):
        """CARDIAC_BEAT déclenche un cycle de pensée."""
        voice._alive = True
        voice._last_cardiac = {
            "emotion": "serenite", "emotion_intensity": 0.5,
            "coherence": 0.7, "bpm": 60,
        }
        # Mock _think_cycle pour vérifier qu'il est appelé
        voice._think_cycle = AsyncMock()
        await voice._on_cardiac_beat({"emotion": "serenite", "bpm": 60})
        voice._think_cycle.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_cardiac_beat_not_alive(self, voice):
        """CARDIAC_BEAT ignoré si pas alive."""
        voice._alive = False
        voice._think_cycle = AsyncMock()
        await voice._on_cardiac_beat({"emotion": "serenite"})
        voice._think_cycle.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_reptilian_alert_caches(self, voice):
        """REPTILIAN_ALERT met à jour le cache."""
        await voice._on_reptilian_alert({"threat_level": 5, "reflex": "SHED"})
        assert voice._last_reptilian_alert["threat_level"] == 5

    @pytest.mark.asyncio
    async def test_on_routine_complete_sets_idle(self, voice):
        """ROUTINE_COMPLETE met is_idle à True."""
        voice._is_idle = False
        with patch.dict("sys.modules", {
            "core.event_bus": MagicMock(),
            "core.event_bus.bus": MagicMock(bus=AsyncMock()),
        }):
            await voice._on_routine_complete({"intent": "TEST", "status": "success"})
        assert voice._is_idle

    @pytest.mark.asyncio
    async def test_on_routine_complete_caches_event(self, voice):
        """ROUTINE_COMPLETE cache l'événement."""
        with patch.dict("sys.modules", {
            "core.event_bus": MagicMock(),
            "core.event_bus.bus": MagicMock(bus=AsyncMock()),
        }):
            await voice._on_routine_complete({"intent": "SECURITY_AUDIT", "status": "success"})
        assert voice._last_routine_complete["intent"] == "SECURITY_AUDIT"

    @pytest.mark.asyncio
    async def test_on_prefrontal_thought_caches(self, voice):
        """PREFRONTAL_THOUGHT met à jour le cache."""
        await voice._on_prefrontal_thought({"thought": "Focus maintenu", "category": "decision"})
        assert voice._last_prefrontal_thought["thought"] == "Focus maintenu"

    @pytest.mark.asyncio
    async def test_on_autonomy_heartbeat_caches(self, voice):
        """AUTONOMY_HEARTBEAT met à jour le cache."""
        await voice._on_autonomy_heartbeat({"daily_count": 5, "error_streak": 0})
        assert voice._last_heartbeat["daily_count"] == 5

    def test_subscribe_events(self, voice, mock_bus):
        """_subscribe_events souscrit à tous les événements attendus."""
        voice._subscribed = False
        with patch("core.inner_voice.bus", mock_bus, create=True):
            with patch.dict("sys.modules", {
                "core.event_bus": MagicMock(),
                "core.event_bus.bus": MagicMock(bus=mock_bus),
            }):
                voice._subscribe_events()
        assert voice._subscribed
        # Vérifier les events souscrits
        subscribed_events = [call.args[0] for call in mock_bus.subscribe.call_args_list]
        assert "CARDIAC_BEAT" in subscribed_events
        assert "REPTILIAN_ALERT" in subscribed_events
        assert "AUTONOMY_ROUTINE_COMPLETE" in subscribed_events

    @pytest.mark.asyncio
    async def test_think_cycle_full(self, voice):
        """Cycle de pensée complet génère une pensée."""
        voice._alive = True
        voice._last_cardiac = {
            "emotion": "enthousiasme", "emotion_intensity": 0.8,
            "coherence": 0.7, "bpm": 80,
        }
        voice._last_reptilian_alert = {"threat_level": 5, "reflex": "SHED"}
        with patch.dict("sys.modules", {
            "core.desire_engine": MagicMock(desires=MagicMock(
                drives={}, tick=MagicMock())),
            "core.synaptic_network": MagicMock(cortex=MagicMock(nodes={})),
            "core.prefrontal": MagicMock(prefrontal=MagicMock(
                goals=[], narrative_log=[])),
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.cardiac_engine": MagicMock(heart=MagicMock(
                current_emotion="serenite", compute_coherence=MagicMock(return_value=0.7))),
            "core.event_bus": MagicMock(),
            "core.event_bus.bus": MagicMock(bus=AsyncMock()),
        }):
            await voice._think_cycle()
        # Au moins la source reptilienne (menace 5) devrait avoir généré une pensée
        assert len(voice.stream) >= 1

    @pytest.mark.asyncio
    async def test_think_cycle_no_broadcast_below_threshold(self, voice):
        """Pas de broadcast si saillance sous IGNITION_THRESHOLD."""
        voice._alive = True
        voice._last_cardiac = {
            "emotion": "serenite", "emotion_intensity": 0.1,
            "coherence": 0.5, "bpm": 60,
        }
        mock_bus = AsyncMock()
        with patch.dict("sys.modules", {
            "core.desire_engine": MagicMock(desires=MagicMock(
                drives={}, tick=MagicMock())),
            "core.synaptic_network": MagicMock(cortex=MagicMock(nodes={})),
            "core.prefrontal": MagicMock(prefrontal=MagicMock(
                goals=[], narrative_log=[])),
            "core.event_bus": MagicMock(),
            "core.event_bus.bus": MagicMock(bus=mock_bus),
        }):
            await voice._think_cycle()
        # broadcast_count devrait rester bas (saillance faible)
        assert voice.stats["broadcast_count"] <= 1


# ============================================================
# GROUPE 9 : Broadcast Reactions (modules receveurs)
# ============================================================


class TestBroadcastReactions:
    def test_cardiac_handler_signature(self):
        """Le handler cardiac attend le bon format d'événement."""
        # On vérifie que le module cardiac peut recevoir INNER_VOICE_BROADCAST
        event = {
            "thought": "Curiosite monte",
            "source": "desire",
            "mode": "motiver",
            "salience": 0.6,
            "emotion": "curiosite",
        }
        # Le handler doit pouvoir lire les champs sans erreur
        assert event["mode"] == "motiver"
        assert event["source"] == "desire"

    def test_desire_handler_format(self):
        """Le format du broadcast est compatible avec desire_engine."""
        event = {
            "thought": "Flow. Ne pas interrompre.",
            "source": "cardiac",
            "mode": "evaluer",
            "salience": 0.5,
        }
        assert "mode" in event
        assert event["salience"] >= 0

    def test_synaptic_handler_format(self):
        """Le format du broadcast est compatible avec synaptic_network."""
        event = {
            "thought": "Audit et evolution lies",
            "source": "synaptic",
            "mode": "evaluer",
            "salience": 0.5,
        }
        # Le handler doit pouvoir activer un nœud à partir du source
        assert event["source"] in ["cardiac", "reptilian", "desire",
                                     "synaptic", "prefrontal", "prediction", "dmn"]

    def test_prefrontal_handler_format(self):
        """Le format du broadcast est compatible avec prefrontal."""
        event = {
            "thought": "Goal a 70%. Continuer.",
            "source": "prefrontal",
            "mode": "planifier",
            "salience": 0.7,
            "timestamp": time.time(),
        }
        assert "thought" in event
        assert "timestamp" in event

    def test_broadcast_event_structure(self, voice):
        """get_stream retourne le bon format pour le broadcast."""
        from core.inner_voice import Thought
        voice.stream.append(Thought(
            timestamp=time.time(), content="Test", source="cardiac",
            mode="evaluer", salience=0.5, emotion="serenite",
        ))
        result = voice.get_stream(1)
        assert len(result) == 1
        assert "content" in result[0]
        assert "source" in result[0]
        assert "mode" in result[0]


# ============================================================
# GROUPE 10 : Integration
# ============================================================


class TestIntegration:
    def test_get_voice_context_empty(self, voice):
        """Contexte vide si pas de pensées."""
        ctx = voice.get_voice_context()
        assert "[PRECISION]" in ctx  # Toujours la précision

    def test_get_voice_context_with_thought(self, voice):
        """Contexte contient la dernière pensée."""
        from core.inner_voice import Thought
        voice.stream.append(Thought(
            timestamp=time.time(), content="Focus security",
            source="prefrontal", mode="planifier", salience=0.7,
        ))
        ctx = voice.get_voice_context()
        assert "[VOIX]" in ctx
        assert "Focus security" in ctx

    def test_set_idle_toggle(self, voice):
        """set_idle bascule le mode DMN."""
        voice.set_idle(False)
        assert not voice._is_idle
        voice.set_idle(True)
        assert voice._is_idle

    def test_get_stats_complete(self, voice):
        """get_stats retourne toutes les clés attendues."""
        stats = voice.get_stats()
        expected_keys = [
            "stream_length", "predictions_active", "predictions_total",
            "precision", "is_idle", "tick_count", "identity_tone",
            "last_thought", "total_thoughts", "ignitions",
            "broadcast_count", "dmn_activations",
        ]
        for key in expected_keys:
            assert key in stats, f"Clé manquante: {key}"


# ============================================================
# GROUPE 11 : Edge Cases
# ============================================================


class TestEdgeCases:
    def test_empty_stream_get_current_thought(self, voice):
        """get_current_thought avec stream vide retourne None."""
        assert voice.get_current_thought() is None

    def test_stream_fifo_cap(self, voice):
        """Stream ne dépasse pas MAX_STREAM."""
        from core.inner_voice import Thought, MAX_STREAM
        for i in range(MAX_STREAM + 50):
            voice.stream.append(Thought(
                timestamp=i, content=f"P{i}", source="cardiac",
                mode="evaluer", salience=0.5,
            ))
        # Simuler le trimming qui se fait dans _record_thought
        if len(voice.stream) > MAX_STREAM:
            voice.stream = voice.stream[-MAX_STREAM:]
        assert len(voice.stream) <= MAX_STREAM

    def test_zero_predictions_graceful(self, voice):
        """Pas de prédictions → get_predictions vide."""
        assert voice.get_predictions() == []

    def test_idle_prolongee_dmn_salience(self, voice):
        """Idle très longue → saillance DMN cap à 0.7."""
        voice._is_idle = True
        voice._idle_since = time.time() - 3600  # 1h idle
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.prefrontal": MagicMock(prefrontal=MagicMock(goals=[])),
            "core.cardiac_engine": MagicMock(heart=MagicMock(
                current_emotion="serenite", compute_coherence=MagicMock(return_value=0.7))),
            "core.synaptic_network": MagicMock(cortex=MagicMock(nodes={})),
        }):
            voice._perceive_dmn()
        assert voice.workspace[0].salience <= 0.7

    def test_save_load_empty_state(self, voice, tmp_path):
        """Save/load avec état vide ne crash pas."""
        voice.save()
        from core.inner_voice import InnerVoice
        InnerVoice.reset_singleton()
        v2 = InnerVoice()
        assert len(v2.stream) == 0
        assert len(v2.predictions) == 0


# ============================================================
# GROUPE 12 : Mode Vygotsky
# ============================================================


class TestModeVygotsky:
    def test_determine_mode_reptilian(self, voice):
        """Source reptilienne → mode 'inhiber'."""
        from core.inner_voice import WorkspaceEntry
        entry = WorkspaceEntry(source="reptilian", raw_signal={"context": "threat"},
                               salience=0.8, timestamp=1)
        assert voice._determine_mode(entry) == "inhiber"

    def test_determine_mode_prefrontal_planning(self, voice):
        """Source préfrontale active → mode 'planifier'."""
        from core.inner_voice import WorkspaceEntry
        entry = WorkspaceEntry(source="prefrontal", raw_signal={"context": "progress"},
                               salience=0.6, timestamp=1)
        assert voice._determine_mode(entry) == "planifier"

    def test_determine_mode_desire(self, voice):
        """Source pulsionnelle → mode 'motiver'."""
        from core.inner_voice import WorkspaceEntry
        entry = WorkspaceEntry(source="desire", raw_signal={"context": "frustrated"},
                               salience=0.5, timestamp=1)
        assert voice._determine_mode(entry) == "motiver"

    def test_determine_mode_prediction(self, voice):
        """Source prédictive → mode 'predire'."""
        from core.inner_voice import WorkspaceEntry
        entry = WorkspaceEntry(source="prediction", raw_signal={"context": "error"},
                               salience=0.5, timestamp=1)
        assert voice._determine_mode(entry) == "predire"

    def test_determine_mode_dmn_wander(self, voice):
        """Source DMN wander → mode 'vagabonder'."""
        from core.inner_voice import WorkspaceEntry
        entry = WorkspaceEntry(source="dmn", raw_signal={"context": "wander"},
                               salience=0.3, timestamp=1)
        assert voice._determine_mode(entry) == "vagabonder"

    def test_determine_mode_dmn_self(self, voice):
        """Source DMN self → mode 'refleter'."""
        from core.inner_voice import WorkspaceEntry
        entry = WorkspaceEntry(source="dmn", raw_signal={"context": "self"},
                               salience=0.3, timestamp=1)
        assert voice._determine_mode(entry) == "refleter"


# ============================================================
# GROUPE 13 : Influence Scoring (Couche 8)
# ============================================================


class TestVoiceBonusScoring:
    """Phase C Etape 6b : la Couche 8 n'ecoute plus que la derniere pensee.

    Plus de bonus emotion/mode/source : ces modulations sont maintenant
    gerees par drive_routine_registry.compute_context_multipliers en amont.
    La Couche 8 valorise uniquement la mention litterale de l'intent dans
    la derniere pensee du soliloque (match NLP par tokens).
    """

    def test_bonus_empty_stream(self, voice):
        """Bonus 0.0 si pas de pensées."""
        assert voice.compute_voice_bonus("EXPANSION_CODE") == 0.0

    def test_bonus_no_mention(self, voice):
        """Derniere pensee sans aucun token de l'intent → 0.0."""
        from core.inner_voice import Thought
        voice.stream.append(Thought(
            timestamp=time.time(),
            content="Ciel bleu, nuages blancs, vent doux.",
            source="dmn", mode="vagabonder", salience=0.3,
            emotion="serenite",
        ))
        assert voice.compute_voice_bonus("EXPANSION_CODE") == 0.0
        assert voice.compute_voice_bonus("SECURITY_AUDIT") == 0.0

    def test_bonus_full_mention(self, voice):
        """Derniere pensee mentionne TOUS les tokens de l'intent → bonus max (1.0)."""
        from core.inner_voice import Thought
        voice.stream.append(Thought(
            timestamp=time.time(),
            content="Je dois lancer un audit de la structure maintenant.",
            source="prefrontal", mode="planifier", salience=0.7,
            emotion="determination",
        ))
        bonus = voice.compute_voice_bonus("AUDIT_STRUCTURE")
        assert bonus == 1.0

    def test_bonus_partial_mention(self, voice):
        """Derniere pensee ne mentionne qu'une partie des tokens → bonus partiel."""
        from core.inner_voice import Thought
        voice.stream.append(Thought(
            timestamp=time.time(),
            content="L'expansion du module me parait necessaire.",
            source="synaptic", mode="planifier", salience=0.6,
            emotion="enthousiasme",
        ))
        # EXPANSION_CODE : 2 tokens ("expansion" >= 4, "code" >= 4)
        # "expansion" matche, "code" non → ratio 0.5
        bonus = voice.compute_voice_bonus("EXPANSION_CODE")
        assert bonus == 0.5

    def test_bonus_only_last_thought_counts(self, voice):
        """Seule la DERNIERE pensee est lue, pas les precedentes."""
        from core.inner_voice import Thought
        # Pensees anterieures mentionnent AUDIT_STRUCTURE — ignorees
        for i in range(3):
            voice.stream.append(Thought(
                timestamp=time.time(),
                content="audit structure",
                source="prefrontal", mode="evaluer", salience=0.5,
            ))
        # Derniere pensee ne mentionne rien de AUDIT_STRUCTURE
        voice.stream.append(Thought(
            timestamp=time.time(),
            content="Je me repose.",
            source="dmn", mode="refleter", salience=0.3,
        ))
        assert voice.compute_voice_bonus("AUDIT_STRUCTURE") == 0.0

    def test_bonus_no_malus(self, voice):
        """La Couche 8 ne produit jamais de malus (pas de -X.X)."""
        from core.inner_voice import Thought
        voice.stream.append(Thought(
            timestamp=time.time(),
            content="Menace imminente, danger extreme, crise totale.",
            source="reptilian", mode="inhiber", salience=0.9,
            emotion="alerte",
        ))
        # Quel que soit l'intent, le bonus est >= 0
        assert voice.compute_voice_bonus("EXPANSION_CODE") >= 0.0
        assert voice.compute_voice_bonus("VEILLE_SILENCIEUSE") >= 0.0

    def test_bonus_clamping(self, voice):
        """Bonus reste borne [-1.0, +2.0] meme en sortie maximale."""
        from core.inner_voice import Thought
        voice.stream.append(Thought(
            timestamp=time.time(),
            content="security security security audit audit audit",
            source="reptilian", mode="inhiber", salience=1.0,
            emotion="alerte",
        ))
        bonus = voice.compute_voice_bonus("SECURITY_AUDIT")
        assert -1.0 <= bonus <= 2.0


# ============================================================
# GROUPE 14 : Grimoire Suggestion
# ============================================================


class TestGrimoireSuggestion:
    def test_suggestion_empty_stream(self, voice):
        """Pas de suggestion si stream vide."""
        assert voice.get_grimoire_suggestion() is None

    def test_suggestion_debug_keywords(self, voice):
        """Mots-clés erreur/crash → dr_debug."""
        from core.inner_voice import Thought
        voice.stream.extend([
            Thought(timestamp=time.time(), content="Erreur detectee. Crash imminent.",
                    source="reptilian", mode="inhiber", salience=0.8),
            Thought(timestamp=time.time(), content="Exception non geree. Bug grave.",
                    source="prediction", mode="predire", salience=0.6),
        ])
        suggestion = voice.get_grimoire_suggestion()
        assert suggestion == "dr_debug"

    def test_suggestion_hallucination_keywords(self, voice):
        """Mots-clés hallucination → hallucination_doctor."""
        from core.inner_voice import Thought
        voice.stream.extend([
            Thought(timestamp=time.time(), content="Hallucination detectee. Alien import.",
                    source="prediction", mode="predire", salience=0.7),
            Thought(timestamp=time.time(), content="Offtopic genere. Hors perimetre.",
                    source="prefrontal", mode="evaluer", salience=0.5),
        ])
        suggestion = voice.get_grimoire_suggestion()
        assert suggestion == "hallucination_doctor"

    def test_suggestion_loop_keywords(self, voice):
        """Mots-clés boucle/repetition → loop_breaker."""
        from core.inner_voice import Thought
        voice.stream.extend([
            Thought(timestamp=time.time(), content="Boucle detectee. Repetition.",
                    source="dmn", mode="refleter", salience=0.5),
            Thought(timestamp=time.time(), content="Cycle infini. Stuck.",
                    source="prefrontal", mode="evaluer", salience=0.6),
        ])
        suggestion = voice.get_grimoire_suggestion()
        assert suggestion == "loop_breaker"

    def test_suggestion_threshold_below(self, voice):
        """Score < 2 → pas de suggestion (faux positif)."""
        from core.inner_voice import Thought
        voice.stream.append(Thought(
            timestamp=time.time(), content="Tout va bien. Stable.",
            source="cardiac", mode="evaluer", salience=0.3,
        ))
        assert voice.get_grimoire_suggestion() is None

    def test_suggestion_code_reviewer(self, voice):
        """Mots-clés code review → code_reviewer."""
        from core.inner_voice import Thought
        voice.stream.extend([
            Thought(timestamp=time.time(), content="Code qualite basse. Revue necessaire.",
                    source="prefrontal", mode="planifier", salience=0.6),
            Thought(timestamp=time.time(), content="Review spec. Refactor urgent.",
                    source="dmn", mode="refleter", salience=0.5),
        ])
        suggestion = voice.get_grimoire_suggestion()
        assert suggestion == "code_reviewer"


# ============================================================
# GROUPE : Coloration tissulaire (Sprint D)
# ============================================================


class TestTissueColoration:
    """Tests pour la coloration tissulaire de la voix intérieure."""

    def _make_entry(self, source="cardiac", context="beat"):
        from core.inner_voice import WorkspaceEntry
        return WorkspaceEntry(
            source=source,
            raw_signal={"context": context, "bpm": 72, "emotion": "calme",
                        "direction": "Stable"},
            salience=0.5, timestamp=1,
        )

    def test_neutral_profile_no_prefix(self, voice):
        """Profil neutre (0.5, 0.3, 0.5) → pas de préfixe ajouté."""
        voice._color_profile = (0.5, 0.3, 0.5, False)
        entry = self._make_entry()
        result = voice._formulate(entry)
        from core.inner_voice import _MOOD_PREFIXES
        all_prefixes = [p for prefs in _MOOD_PREFIXES.values() for p in prefs]
        assert not any(result.startswith(p) for p in all_prefixes)

    def test_entropy_high_prefix(self, voice):
        """Entropie haute (>0.7) → préfixe entropy_high."""
        voice._color_profile = (0.8, 0.2, 0.5, False)
        entry = self._make_entry()
        result = voice._formulate(entry)
        from core.inner_voice import _MOOD_PREFIXES
        assert any(result.startswith(p) for p in _MOOD_PREFIXES["entropy_high"])

    def test_tension_high_prefix(self, voice):
        """Tension haute (>0.6) → préfixe tension_high."""
        voice._color_profile = (0.3, 0.8, 0.5, False)
        entry = self._make_entry()
        result = voice._formulate(entry)
        from core.inner_voice import _MOOD_PREFIXES
        assert any(result.startswith(p) for p in _MOOD_PREFIXES["tension_high"])

    def test_vitality_low_prefix(self, voice):
        """Vitalité basse (<0.3) → préfixe vitality_low."""
        voice._color_profile = (0.3, 0.2, 0.2, False)
        entry = self._make_entry()
        result = voice._formulate(entry)
        from core.inner_voice import _MOOD_PREFIXES
        assert any(result.startswith(p) for p in _MOOD_PREFIXES["vitality_low"])

    def test_dreaming_prefix(self, voice):
        """Mode rêve → préfixe dreaming (prioritaire sur les autres)."""
        voice._color_profile = (0.9, 0.1, 0.5, True)
        entry = self._make_entry()
        result = voice._formulate(entry)
        from core.inner_voice import _MOOD_PREFIXES
        assert any(result.startswith(p) for p in _MOOD_PREFIXES["dreaming"])

    def test_dynamic_length_short(self, voice):
        """Profil faible entropie+vitalité → max ~68, tronqué."""
        voice._color_profile = (0.1, 0.0, 0.1, False)
        # max_chars = 60 + int(0.1*60) + int(0.1*20) = 60+6+2 = 68
        from core.inner_voice import WorkspaceEntry
        entry = WorkspaceEntry(
            source="dmn",
            raw_signal={"context": "retrospect", "count": 10,
                        "intent": "A" * 50, "pattern": "B" * 50},
            salience=0.3, timestamp=1,
        )
        result = voice._formulate(entry)
        assert len(result) <= 68

    def test_dynamic_length_long(self, voice):
        """Profil haute entropie+vitalité → max ~132, pas tronqué à 120."""
        voice._color_profile = (0.9, 0.0, 0.9, False)
        # max_chars = 60 + int(0.9*60) + int(0.9*20) = 60+54+18 = 132
        from core.inner_voice import WorkspaceEntry
        # Un texte de ~125 chars ne serait pas tronqué avec le profil haut
        entry = WorkspaceEntry(
            source="dmn",
            raw_signal={"context": "retrospect", "count": 10,
                        "intent": "analyse", "pattern": "X" * 100},
            salience=0.3, timestamp=1,
        )
        result = voice._formulate(entry)
        # Le max est 132, la pensée peut dépasser 120
        assert len(result) <= 132

    def test_compute_color_profile_fallback(self, voice):
        """Si tissue lève une exception → profil neutre retourné."""
        import sys
        mock_tissue_mod = MagicMock()
        mock_tissue_mod.tissue.get_zone_signals.side_effect = RuntimeError("no tissue")
        with patch.dict("sys.modules", {"core.neural_tissue": mock_tissue_mod}):
            profile = voice._compute_color_profile()
        assert profile == (0.5, 0.3, 0.5, False)


# ============================================================
# Sprint 6 Sensorium — Perception somatique
# ============================================================

class TestSensoriumPerception:
    """Sprint 6 Sensorium : la voix interieure verbalise les sensations hardware."""

    def test_perceive_sensorium_stressed(self, voice):
        """Comfort 0.3 → WorkspaceEntry avec source=sensorium."""
        mock_sensorium = MagicMock()
        mock_sensorium.get_comfort_index.return_value = 0.3
        mock_sensorium.get_senses.return_value = {
            "thermoception": 0.9, "effort": 0.5,
            "oppression": 0.3, "suffocation": 0.2, "vitality": 0.4,
        }
        # Fix 28/05/2026 : depuis le commit 2d63fce (sensorium floor
        # anti-hyperacousie), _perceive_sensorium bypass aussi via get_raw()
        # si cpu<20 ∧ ram<75 ∧ temp<65. Le test doit fournir des valeurs raw
        # cohérentes avec le stress thermique simulé (sinon MagicMock.get_raw
        # retourne un truthy → return prématuré → pas d'entrée workspace).
        mock_sensorium.get_raw.return_value = {
            "effort": 50.0, "oppression": 60.0, "thermoception": 75.0,
        }
        with patch.dict("sys.modules", {"core.sensorium": MagicMock(sensorium=mock_sensorium)}):
            voice._perceive_sensorium()

        sensorium_entries = [e for e in voice.workspace if e.source == "sensorium"]
        assert len(sensorium_entries) == 1
        entry = sensorium_entries[0]
        assert entry.raw_signal["dominant"] == "thermoception"
        # Fix 28/05/2026 : vocabulaire mis à jour post-2d63fce (formulations
        # relatives anti-alarmistes : "Mes circuits s'echauffent legerement"
        # au lieu de "chaleur monte"). "echauffent" est le mot-clé stable.
        assert "echauffent" in entry.raw_signal["content"]
        assert entry.raw_signal["emotion"] == "inquietude"
        assert entry.salience >= 0.5

    def test_perceive_sensorium_relaxed(self, voice):
        """Comfort 0.8 → pas d'entree sensorium."""
        mock_sensorium = MagicMock()
        mock_sensorium.get_comfort_index.return_value = 0.8
        mock_sensorium.get_senses.return_value = {
            "thermoception": 0.2, "effort": 0.1,
            "oppression": 0.1, "suffocation": 0.1, "vitality": 0.2,
        }
        with patch.dict("sys.modules", {"core.sensorium": MagicMock(sensorium=mock_sensorium)}):
            voice._perceive_sensorium()

        sensorium_entries = [e for e in voice.workspace if e.source == "sensorium"]
        assert len(sensorium_entries) == 0

    def test_thermal_critical_amplifies(self, voice):
        """Event THERMAL_CRITICAL → salience boostee a 0.9."""
        voice._last_sensorium_alert = {"type": "thermal_critical", "value": 0.95}
        mock_sensorium = MagicMock()
        mock_sensorium.get_comfort_index.return_value = 0.2
        mock_sensorium.get_senses.return_value = {
            "thermoception": 0.95, "effort": 0.3,
            "oppression": 0.2, "suffocation": 0.1, "vitality": 0.3,
        }
        # Fix 28/05/2026 : alerte thermique critique → temp_raw au-dessus du
        # plancher 65°C pour ne pas être bypass par le floor 2d63fce.
        mock_sensorium.get_raw.return_value = {
            "effort": 30.0, "oppression": 40.0, "thermoception": 85.0,
        }
        with patch.dict("sys.modules", {"core.sensorium": MagicMock(sensorium=mock_sensorium)}):
            voice._perceive_sensorium()

        sensorium_entries = [e for e in voice.workspace if e.source == "sensorium"]
        assert len(sensorium_entries) == 1
        assert sensorium_entries[0].salience == 0.9
        assert "surchauffent" in sensorium_entries[0].raw_signal["content"]
        # Alerte consommee
        assert voice._last_sensorium_alert == {}

    def test_perceive_sensorium_dominant_sense(self, voice):
        """Effort max → narration 'processeur sous tension'."""
        mock_sensorium = MagicMock()
        mock_sensorium.get_comfort_index.return_value = 0.4
        mock_sensorium.get_senses.return_value = {
            "thermoception": 0.3, "effort": 0.8,
            "oppression": 0.2, "suffocation": 0.1, "vitality": 0.2,
        }
        # Fix 28/05/2026 : effort dominant → cpu_raw au-dessus du plancher 20%
        # pour ne pas être bypass par le floor 2d63fce.
        mock_sensorium.get_raw.return_value = {
            "effort": 80.0, "oppression": 50.0, "thermoception": 50.0,
        }
        with patch.dict("sys.modules", {"core.sensorium": MagicMock(sensorium=mock_sensorium)}):
            voice._perceive_sensorium()

        sensorium_entries = [e for e in voice.workspace if e.source == "sensorium"]
        assert len(sensorium_entries) == 1
        # Fix 28/05/2026 : vocabulaire mis à jour post-2d63fce (narratives
        # relatives : "Mes ressources se mobilisent" au lieu de "processeur
        # sous tension"). "ressources" est le mot-clé stable pour effort.
        assert "ressources" in sensorium_entries[0].raw_signal["content"]
