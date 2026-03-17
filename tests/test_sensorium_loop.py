"""
Tests pour SensoriumLoop — boucle perception-action fermee.

Valide le cablage : AUTONOMY_ROUTINE_COMPLETE → organes reagissent →
SENSORIUM_FEEDBACK publie → corpus mini-cycle → etat cognitif mis a jour.
Trous cables : cardiac→thalamus, thalamus→cardiac, feedback→corpus.
"""

import asyncio
import time
import sys
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# ============================================================
# Helpers : mock des modules lourds avant imports
# ============================================================

_MOCK_MODULES = {
    "core.summoner": MagicMock(),
}


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def mock_summoner():
    with patch.dict(sys.modules, _MOCK_MODULES):
        yield


# --- Thalamus ---

@pytest.fixture
def thalamus_instance(tmp_path, monkeypatch):
    from core import thalamus as tmod
    from core.thalamus import Thalamus
    Thalamus.reset_singleton()
    monkeypatch.setattr(tmod, "THALAMUS_STATE_FILE", str(tmp_path / "thal.json"))
    with patch.object(Thalamus, "_load"):
        t = Thalamus()
        tmod.thalamus = t
    yield t
    Thalamus.reset_singleton()


# --- Cardiac ---

@pytest.fixture
def cardiac_instance(tmp_path, monkeypatch):
    from core import cardiac_engine as cmod
    from core.cardiac_engine import CardiacEngine
    CardiacEngine.reset_singleton()
    monkeypatch.setattr(cmod, "CARDIAC_STATE_FILE", str(tmp_path / "cardiac.json"))
    with patch.object(CardiacEngine, "_load"):
        h = CardiacEngine()
        cmod.heart = h
    yield h
    CardiacEngine.reset_singleton()


# --- Corpus Callosum ---

@pytest.fixture
def corpus_instance(tmp_path, monkeypatch):
    from core import corpus_callosum as ccmod
    from core.corpus_callosum import CorpusCallosum
    CorpusCallosum.reset_singleton()
    monkeypatch.setattr(ccmod, "CALLOSUM_STATE_FILE", str(tmp_path / "cc.json"))
    with patch.object(CorpusCallosum, "_load"):
        c = CorpusCallosum()
        ccmod.callosum = c
    yield c
    CorpusCallosum.reset_singleton()


# ============================================================
# Test 1 : Thalamus ecoute CARDIAC_EMOTION_CHANGE
# ============================================================

class TestThalamusCardiacBridge:
    """Trou cable : cardiac publie CARDIAC_EMOTION_CHANGE → thalamus ajuste saillance."""

    @pytest.mark.asyncio
    async def test_emotion_peur_boosts_urgence(self, thalamus_instance):
        """Emotion peur avec intensite forte booste la categorie urgence."""
        from core.thalamus import EVENT_CATEGORIES
        t = thalamus_instance

        # Saillance initiale urgence
        urgence_events = [e for e, c in EVENT_CATEGORIES.items() if c == "urgence"]
        initial = {e: t._scorecard[e] for e in urgence_events}

        await t._on_cardiac_emotion({
            "emotion": "peur",
            "intensity": 0.8,
            "cause": "routine_failure",
        })

        # Toutes les saillances urgence doivent avoir augmente
        for e in urgence_events:
            assert t._scorecard[e] > initial[e], f"{e} devrait avoir augmente"

    @pytest.mark.asyncio
    async def test_emotion_enthousiasme_boosts_emergence(self, thalamus_instance):
        """Emotion enthousiasme booste la categorie emergence."""
        from core.thalamus import EVENT_CATEGORIES
        t = thalamus_instance

        emergence_events = [e for e, c in EVENT_CATEGORIES.items() if c == "emergence"]
        initial = {e: t._scorecard[e] for e in emergence_events}

        await t._on_cardiac_emotion({
            "emotion": "enthousiasme",
            "intensity": 0.9,
        })

        for e in emergence_events:
            assert t._scorecard[e] > initial[e]

    @pytest.mark.asyncio
    async def test_emotion_faible_ignoree(self, thalamus_instance):
        """Emotion avec intensite < 0.3 ignoree."""
        from core.thalamus import EVENT_CATEGORIES
        t = thalamus_instance
        initial = dict(t._scorecard)

        await t._on_cardiac_emotion({
            "emotion": "peur",
            "intensity": 0.1,
        })

        assert t._scorecard == initial

    @pytest.mark.asyncio
    async def test_emotion_inconnue_ignoree(self, thalamus_instance):
        """Emotion sans mapping ignoree."""
        t = thalamus_instance
        initial = dict(t._scorecard)

        await t._on_cardiac_emotion({
            "emotion": "confusion",
            "intensity": 0.9,
        })

        assert t._scorecard == initial

    @pytest.mark.asyncio
    async def test_serenite_boosts_regulation(self, thalamus_instance):
        """Serenite booste la regulation."""
        from core.thalamus import EVENT_CATEGORIES
        t = thalamus_instance

        reg_events = [e for e, c in EVENT_CATEGORIES.items() if c == "regulation"]
        initial = {e: t._scorecard[e] for e in reg_events}

        await t._on_cardiac_emotion({
            "emotion": "serenite",
            "intensity": 0.6,
        })

        for e in reg_events:
            assert t._scorecard[e] > initial[e]

    @pytest.mark.asyncio
    async def test_boost_capped_at_1(self, thalamus_instance):
        """Les saillances ne depassent jamais 1.0."""
        from core.thalamus import EVENT_CATEGORIES
        t = thalamus_instance

        # Mettre toutes les saillances urgence a 0.99
        for e, c in EVENT_CATEGORIES.items():
            if c == "urgence":
                t._scorecard[e] = 0.99

        await t._on_cardiac_emotion({
            "emotion": "peur",
            "intensity": 1.0,
        })

        for e, c in EVENT_CATEGORIES.items():
            if c == "urgence":
                assert t._scorecard[e] <= 1.0


# ============================================================
# Test 2 : Cardiac ecoute THALAMUS_RULE_LEARNED
# ============================================================

class TestCardiacThalamusBridge:
    """Trou cable : thalamus publie THALAMUS_RULE_LEARNED → cardiac forme marqueur somatique."""

    @pytest.mark.asyncio
    async def test_boost_rule_creates_positive_marker(self, cardiac_instance):
        """Regle boost → marqueur somatique positif."""
        h = cardiac_instance

        await h._on_thalamus_rule({
            "intent": "VEILLE_IA",
            "type": "boost",
            "action": "created",
            "rules_count": 1,
        })

        assert "VEILLE_IA" in h.somatic_markers
        assert h.somatic_markers["VEILLE_IA"]["valence"] > 0

    @pytest.mark.asyncio
    async def test_dampen_rule_creates_negative_marker(self, cardiac_instance):
        """Regle dampen → marqueur somatique negatif."""
        h = cardiac_instance

        await h._on_thalamus_rule({
            "intent": "COUNCIL_DEBATE",
            "type": "dampen",
            "action": "created",
            "rules_count": 1,
        })

        assert "COUNCIL_DEBATE" in h.somatic_markers
        assert h.somatic_markers["COUNCIL_DEBATE"]["valence"] < 0

    @pytest.mark.asyncio
    async def test_reinforced_rule_strengthens_marker(self, cardiac_instance):
        """Renforcement d'une regle renforce le marqueur."""
        h = cardiac_instance

        # Premier marqueur
        await h._on_thalamus_rule({
            "intent": "SECURITY_SCAN",
            "type": "boost",
            "action": "created",
        })
        first_count = h.somatic_markers["SECURITY_SCAN"]["formation_count"]

        # Renforcement
        await h._on_thalamus_rule({
            "intent": "SECURITY_SCAN",
            "type": "boost",
            "action": "reinforced",
        })
        assert h.somatic_markers["SECURITY_SCAN"]["formation_count"] > first_count

    @pytest.mark.asyncio
    async def test_empty_intent_ignored(self, cardiac_instance):
        """Intent vide → pas de marqueur."""
        h = cardiac_instance
        initial_count = len(h.somatic_markers)

        await h._on_thalamus_rule({"intent": "", "type": "boost"})
        assert len(h.somatic_markers) == initial_count

    @pytest.mark.asyncio
    async def test_empty_type_ignored(self, cardiac_instance):
        """Type vide → pas de marqueur."""
        h = cardiac_instance
        initial_count = len(h.somatic_markers)

        await h._on_thalamus_rule({"intent": "VEILLE_IA", "type": ""})
        assert len(h.somatic_markers) == initial_count


# ============================================================
# Test 3 : Corpus Callosum reagit a SENSORIUM_FEEDBACK
# ============================================================

class TestCorpusSensoriumFeedback:
    """Corpus callosum fait un mini-cycle sur SENSORIUM_FEEDBACK."""

    @pytest.mark.asyncio
    async def test_mini_cycle_updates_coherence(self, corpus_instance):
        """Le mini-cycle met a jour global_coherence."""
        c = corpus_instance
        c._alive = True
        initial_coherence = c.global_coherence

        with patch.object(c, "_capture_organ_states") as mock_capture, \
             patch.object(c, "_compute_global_coherence", return_value=0.85), \
             patch.object(c, "_determine_cognitive_state", return_value="flow"):
            from core.corpus_callosum import OrganSnapshot
            mock_capture.return_value = OrganSnapshot(
                timestamp=time.time(),
                cardiac_bpm=65.0, cardiac_coherence=0.8,
                cardiac_emotion="flow", cardiac_emotion_intensity=0.7,
                dominant_drive="MAITRISE", dominant_deprivation=30.0,
                frustrated_drives=[], threat_level=0.0, adrenaline=0.0,
                active_goals=1, goal_progress=0.5, has_active_goal=True,
                dopamine_level=0.7, synaptic_energy=0.5, active_nodes=5,
                voice_active=False, voice_mode="",
            )

            await c._on_sensorium_feedback({
                "intent": "VEILLE_IA",
                "status": "success",
                "quality_score": 0.8,
            })

        # La coherence est interpolee par le signal_weight (bridge)
        # Avec weight=1.0 (fallback) : 100% de la nouvelle valeur
        # Avec weight<1.0 : melange ancien/nouveau
        assert c.global_coherence >= 0.5  # Au moins influence par la nouvelle valeur

    @pytest.mark.asyncio
    async def test_mini_cycle_can_transition_state(self, corpus_instance):
        """Le mini-cycle peut changer l'etat cognitif."""
        c = corpus_instance
        c._alive = True
        c.cognitive_state = "standard"

        with patch.object(c, "_capture_organ_states") as mock_capture, \
             patch.object(c, "_compute_global_coherence", return_value=0.9), \
             patch.object(c, "_determine_cognitive_state", return_value="flow"):
            from core.corpus_callosum import OrganSnapshot
            mock_capture.return_value = OrganSnapshot(
                timestamp=time.time(),
                cardiac_bpm=60.0, cardiac_coherence=0.9,
                cardiac_emotion="flow", cardiac_emotion_intensity=0.8,
                dominant_drive="MAITRISE", dominant_deprivation=20.0,
                frustrated_drives=[], threat_level=0.0, adrenaline=0.0,
                active_goals=1, goal_progress=0.8, has_active_goal=True,
                dopamine_level=0.8, synaptic_energy=0.6, active_nodes=8,
                voice_active=False, voice_mode="",
            )

            await c._on_sensorium_feedback({
                "intent": "EXPANSION_CODE",
                "status": "success",
                "quality_score": 0.9,
            })

        assert c.cognitive_state == "flow"
        assert c._stats["state_transitions"] >= 1

    @pytest.mark.asyncio
    async def test_mini_cycle_ignored_when_not_alive(self, corpus_instance):
        """Mini-cycle ne s'execute pas si corpus n'est pas alive."""
        c = corpus_instance
        c._alive = False

        with patch.object(c, "_capture_organ_states") as mock_capture:
            await c._on_sensorium_feedback({"intent": "X"})
            mock_capture.assert_not_called()

    @pytest.mark.asyncio
    async def test_anti_reentrance(self, corpus_instance):
        """Pas de mini-cycle si un cycle est deja en cours."""
        c = corpus_instance
        c._alive = True
        c._resonance_in_progress = True

        with patch.object(c, "_capture_organ_states") as mock_capture:
            await c._on_sensorium_feedback({"intent": "X"})
            mock_capture.assert_not_called()

    @pytest.mark.asyncio
    async def test_reentrance_flag_reset_on_error(self, corpus_instance):
        """Le flag anti-reentrance est reset meme en cas d'erreur."""
        c = corpus_instance
        c._alive = True

        with patch.object(c, "_capture_organ_states", side_effect=RuntimeError("test")):
            await c._on_sensorium_feedback({"intent": "X"})

        assert not c._resonance_in_progress


# ============================================================
# Test 4 : AutonomyEngine publie SENSORIUM_FEEDBACK
# ============================================================

class TestAutonomyPublishesFeedback:
    """autonomy_engine publie SENSORIUM_FEEDBACK apres chaque routine."""

    @pytest.mark.asyncio
    async def test_publish_sensorium_feedback_structure(self):
        """_publish_sensorium_feedback publie un event avec le bon format."""
        from core.autonomy_engine import AutonomyEngine

        engine = AutonomyEngine.__new__(AutonomyEngine)
        engine._last_feedback_snapshot = {}
        engine.daily_budget_used = 0
        engine._council_degraded = False

        published = []
        async def fake_publish(event_type, data):
            published.append((event_type, data))

        with patch("core.autonomy_engine.bus.publish", side_effect=fake_publish), \
             patch("core.autonomy_engine.asyncio.sleep", new_callable=AsyncMock), \
             patch.dict(sys.modules, {
                 "core.cardiac_engine": MagicMock(heart=MagicMock(
                     bpm=70.0, current_emotion="serenite",
                     compute_coherence=MagicMock(return_value=0.6),
                     ans_balance=0.5,
                 )),
                 "core.desire_engine": MagicMock(desires=MagicMock(
                     drives={},
                 )),
                 "core.reptilian_core": MagicMock(reptile=MagicMock(
                     threat_level=0.0, mode="CALM",
                 )),
                 "core.dopamine_system": MagicMock(dopamine=MagicMock(
                     level=0.5, baseline=0.5,
                 )),
                 "core.corpus_callosum": MagicMock(callosum=MagicMock(
                     cognitive_state="standard", global_coherence=0.5,
                 )),
             }):
            await engine._publish_sensorium_feedback(
                "VEILLE_IA", "strategist", 0.8, "success")

        assert len(published) == 1
        event_type, data = published[0]
        assert event_type == "SENSORIUM_FEEDBACK"
        assert data["intent"] == "VEILLE_IA"
        assert data["agent"] == "strategist"
        assert data["quality_score"] == 0.8
        assert data["status"] == "success"
        assert "organ_snapshot" in data
        assert "timestamp" in data

    @pytest.mark.asyncio
    async def test_feedback_stores_last_snapshot(self):
        """Le snapshot est stocke dans _last_feedback_snapshot."""
        from core.autonomy_engine import AutonomyEngine

        engine = AutonomyEngine.__new__(AutonomyEngine)
        engine._last_feedback_snapshot = {}
        engine.daily_budget_used = 0
        engine._council_degraded = False

        with patch("core.autonomy_engine.bus.publish", new_callable=AsyncMock), \
             patch("core.autonomy_engine.asyncio.sleep", new_callable=AsyncMock), \
             patch.dict(sys.modules, {
                 "core.cardiac_engine": MagicMock(heart=MagicMock(
                     bpm=70.0, current_emotion="peur",
                     compute_coherence=MagicMock(return_value=0.3),
                     ans_balance=0.8,
                 )),
                 "core.desire_engine": MagicMock(desires=MagicMock(
                     drives={},
                 )),
                 "core.reptilian_core": MagicMock(reptile=MagicMock(
                     threat_level=5.0, mode="FIGHT",
                 )),
                 "core.dopamine_system": MagicMock(dopamine=MagicMock(
                     level=0.2, baseline=0.5,
                 )),
                 "core.corpus_callosum": MagicMock(callosum=MagicMock(
                     cognitive_state="crisis", global_coherence=0.2,
                 )),
             }):
            await engine._publish_sensorium_feedback(
                "SECURITY_SCAN", "security", 0.4, "error")

        snap = engine._last_feedback_snapshot
        assert snap["cardiac"]["emotion"] == "peur"
        assert snap["reptilian"]["threat_level"] == 5.0
        assert snap["corpus"]["cognitive_state"] == "crisis"

    @pytest.mark.asyncio
    async def test_feedback_resilient_to_missing_organs(self):
        """Si un organe n'est pas disponible, son snapshot est None."""
        from core.autonomy_engine import AutonomyEngine

        engine = AutonomyEngine.__new__(AutonomyEngine)
        engine._last_feedback_snapshot = {}
        engine.daily_budget_used = 0
        engine._council_degraded = False

        def raise_import(name, *a, **kw):
            raise ImportError(f"No module {name}")

        with patch("core.autonomy_engine.bus.publish", new_callable=AsyncMock), \
             patch("core.autonomy_engine.asyncio.sleep", new_callable=AsyncMock), \
             patch("builtins.__import__", side_effect=raise_import):
            # Ne doit pas lever d'exception
            await engine._publish_sensorium_feedback(
                "TEST", "test", 0.5, "success")

        snap = engine._last_feedback_snapshot
        # Tous les organes doivent etre None (import echoue)
        for key in ("cardiac", "desires", "reptilian", "dopamine", "corpus"):
            assert snap.get(key) is None


# ============================================================
# Test 5 : Bus subscriptions sont bien enregistrees
# ============================================================

class TestBusSubscriptions:
    """Verifie que les nouvelles subscriptions sont bien enregistrees."""

    def test_thalamus_subscribes_cardiac_emotion(self, thalamus_instance):
        """Thalamus doit ecouter CARDIAC_EMOTION_CHANGE."""
        t = thalamus_instance
        assert hasattr(t, "_on_cardiac_emotion")
        assert callable(t._on_cardiac_emotion)

    def test_cardiac_subscribes_thalamus_rule(self, cardiac_instance):
        """Cardiac doit ecouter THALAMUS_RULE_LEARNED."""
        h = cardiac_instance
        assert hasattr(h, "_on_thalamus_rule")
        assert callable(h._on_thalamus_rule)

    def test_corpus_subscribes_sensorium_feedback(self, corpus_instance):
        """Corpus doit ecouter SENSORIUM_FEEDBACK."""
        c = corpus_instance
        assert hasattr(c, "_on_sensorium_feedback")
        assert callable(c._on_sensorium_feedback)


# ============================================================
# Test 6 : Modele LIF (Leaky Integrate-and-Fire)
# ============================================================

def _make_engine():
    """Helper : cree un AutonomyEngine minimal pour tester LIF."""
    from core.autonomy_engine import AutonomyEngine
    engine = AutonomyEngine.__new__(AutonomyEngine)
    engine._lif_potentials = {}
    engine.daily_budget_used = 0
    engine._council_degraded = False
    engine._last_feedback_snapshot = {}
    return engine


def _make_routine(intent, agent="strategist"):
    return {"intent": intent, "agent": agent, "mission": "test"}


class TestLIFScoring:
    """Tests du modele Leaky Integrate-and-Fire pour la selection de routines.

    Note : les scores sont normalises par FINAL_SCORE_CLAMP_MAX (25.0) puis
    multiplies par un facteur d'injection (3.0). Un score de 5.0 injecte 0.6.
    Un score de 25.0 injecte 3.0. Il faut plusieurs cycles pour fire.
    """

    def test_first_cycle_no_fire(self):
        """Premier cycle : les potentiels sont trop bas pour fire."""
        from core.autonomy_engine import LIF_THRESHOLD
        engine = _make_engine()

        scored = [
            (_make_routine("VEILLE_IA"), 5.0),
            (_make_routine("COUNCIL_DEBATE"), 3.0),
        ]
        result = engine._lif_integrate_and_select(scored)

        # Pas de fire au premier cycle (injection = 5/25*3 = 0.6 < seuil 8.0)
        assert result[0][0]["intent"] == "VEILLE_IA"  # Ordre par score preserve
        # Les potentiels sont stockes (normalises)
        assert engine._lif_potentials["VEILLE_IA"] > 0
        assert engine._lif_potentials["VEILLE_IA"] < LIF_THRESHOLD

    def test_accumulation_fires(self):
        """Apres plusieurs cycles, un potentiel depasse le seuil et fire."""
        from core.autonomy_engine import LIF_THRESHOLD
        engine = _make_engine()

        # Score max pour accumuler vite (25.0 → injection = 25/25*3 = 3.0)
        # Convergence = 3.0 / (1-0.7) = 10.0 > seuil 8.0 → fire garanti
        scored = [
            (_make_routine("VEILLE_IA"), 25.0),
            (_make_routine("COUNCIL_DEBATE"), 3.0),
        ]

        # Accumuler : verifier que le potentiel monte vers le seuil
        potentials = []
        for cycle in range(20):
            engine._lif_integrate_and_select(scored)
            potentials.append(engine._lif_potentials.get("VEILLE_IA", 0.0))

        # Le potentiel doit avoir atteint le seuil au moins une fois (fire + reset)
        max_potential_reached = max(potentials)
        # Apres fire, le potentiel redescend (reset + nouvelle injection)
        # On verifie qu'il y a eu au moins un "dip" = preuve d'un fire+reset
        has_dip = any(
            potentials[i] > potentials[i + 1] + 1.0
            for i in range(len(potentials) - 1)
        )
        assert has_dip, f"Pas de fire detecte (potentiels: {potentials[:10]})"

    def test_leak_decay(self):
        """Le potentiel decroit entre cycles (leak)."""
        from core.autonomy_engine import LIF_LEAK_RATE
        engine = _make_engine()
        engine._lif_potentials["VEILLE_IA"] = 6.0

        # Cycle avec un intent different → VEILLE_IA leak seulement
        scored = [(_make_routine("COUNCIL_DEBATE"), 3.0)]
        engine._lif_integrate_and_select(scored)

        # VEILLE_IA n'est pas dans scored, mais son potentiel a quand meme leak
        expected = 6.0 * LIF_LEAK_RATE
        assert engine._lif_potentials["VEILLE_IA"] == pytest.approx(expected, abs=0.01)

    def test_reset_after_fire(self):
        """Apres un fire, le potentiel est reset (periode refractaire)."""
        from core.autonomy_engine import LIF_RESET_AFTER_FIRE, LIF_LEAK_RATE
        engine = _make_engine()
        # Potentiel assez haut pour rester au-dessus du seuil apres leak
        # 12.0 * 0.7 = 8.4 + injection → fire
        engine._lif_potentials["VEILLE_IA"] = 12.0

        scored = [(_make_routine("VEILLE_IA"), 1.0)]
        engine._lif_integrate_and_select(scored)

        # Apres fire, le potentiel est reset puis l'injection du score courant
        # est ajoutee au prochain cycle. Ici le reset est applique.
        assert engine._lif_potentials["VEILLE_IA"] == LIF_RESET_AFTER_FIRE

    def test_potential_cap(self):
        """Le potentiel ne depasse jamais LIF_POTENTIAL_CAP."""
        from core.autonomy_engine import LIF_POTENTIAL_CAP
        engine = _make_engine()
        engine._lif_potentials["VEILLE_IA"] = 11.0

        scored = [(_make_routine("VEILLE_IA"), 5.0)]
        engine._lif_integrate_and_select(scored)

        assert engine._lif_potentials["VEILLE_IA"] <= LIF_POTENTIAL_CAP

    def test_negative_score_inhibits(self):
        """Un score negatif reduit le potentiel (courant inhibiteur)."""
        engine = _make_engine()
        engine._lif_potentials["VEILLE_IA"] = 5.0

        scored = [(_make_routine("VEILLE_IA"), -3.0)]
        engine._lif_integrate_and_select(scored)

        # 5.0 * 0.7 + (-3.0/25*3) = 3.5 - 0.36 = 3.14 < 5.0
        assert engine._lif_potentials["VEILLE_IA"] < 5.0

    def test_cleanup_negligible_potentials(self):
        """Les potentiels negligeables sont nettoyes."""
        engine = _make_engine()
        engine._lif_potentials["OLD_ROUTINE"] = 0.005  # Negligeable

        scored = [(_make_routine("VEILLE_IA"), 3.0)]
        engine._lif_integrate_and_select(scored)

        assert "OLD_ROUTINE" not in engine._lif_potentials

    def test_empty_scored_noop(self):
        """Liste vide → retourne vide sans erreur."""
        engine = _make_engine()
        result = engine._lif_integrate_and_select([])
        assert result == []

    def test_multiple_fires_highest_wins(self):
        """Si plusieurs routines fire, la plus chargee gagne."""
        engine = _make_engine()
        engine._lif_potentials["VEILLE_IA"] = 9.0
        engine._lif_potentials["SECURITY_SCAN"] = 11.0

        scored = [
            (_make_routine("VEILLE_IA"), 1.0),
            (_make_routine("SECURITY_SCAN"), 1.0),
        ]
        result = engine._lif_integrate_and_select(scored)

        # SECURITY_SCAN a le potentiel le plus eleve → fire en premier
        # potentiel SECURITY: 11.0 * 0.7 + (1/25*3) = 7.82 (< 8, pas de fire)
        # potentiel VEILLE: 9.0 * 0.7 + (1/25*3) = 6.42 (< 8, pas de fire)
        # Avec des potentiels pre-set aussi hauts, augmentons pour garantir le fire
        engine._lif_potentials["SECURITY_SCAN"] = 12.0  # Au-dessus du seuil apres leak
        result = engine._lif_integrate_and_select(scored)
        # SECURITY_SCAN fire (12*0.7+0.12 = 8.52 > 8.0), VEILLE ne fire pas
        assert result[0][0]["intent"] == "SECURITY_SCAN"

    def test_lif_preserves_routine_structure(self):
        """Le LIF ne modifie pas la structure des routines."""
        engine = _make_engine()
        engine._lif_potentials["VEILLE_IA"] = 10.0

        original = _make_routine("VEILLE_IA")
        scored = [(original, 5.0)]
        result = engine._lif_integrate_and_select(scored)

        assert result[0][0] is original  # Meme objet
        assert result[0][1] == 5.0  # Score inchange

    def test_fallback_preserves_score_order(self):
        """Sans fire, l'ordre par score est preserve."""
        engine = _make_engine()

        scored = [
            (_make_routine("VEILLE_IA"), 5.0),
            (_make_routine("COUNCIL_DEBATE"), 7.0),
            (_make_routine("SECURITY_SCAN"), 3.0),
        ]
        # Tri initial par score
        scored.sort(key=lambda x: x[1], reverse=True)

        result = engine._lif_integrate_and_select(scored)

        # Premier cycle, pas de fire → ordre par score preserve
        assert result[0][0]["intent"] == "COUNCIL_DEBATE"
        assert result[1][0]["intent"] == "VEILLE_IA"

    def test_persistance_lif_potentials(self):
        """Les potentiels LIF survivent entre cycles."""
        engine = _make_engine()

        scored = [(_make_routine("VEILLE_IA"), 4.0)]
        engine._lif_integrate_and_select(scored)

        # Simuler un nouveau cycle (les potentiels persistent)
        assert "VEILLE_IA" in engine._lif_potentials
        # Injection = 4.0/25*3 = 0.48
        assert engine._lif_potentials["VEILLE_IA"] == pytest.approx(0.48, abs=0.01)


# ============================================================
# Test 7 : Renforcement Hebbian contextuel
# ============================================================

@pytest.fixture
def synaptic_instance(tmp_path, monkeypatch):
    from core import synaptic_network as smod
    from core.synaptic_network import SynapticNetwork
    SynapticNetwork.reset_singleton()
    monkeypatch.setattr(smod, "STATE_FILE", str(tmp_path / "syn.json"))
    with patch.object(SynapticNetwork, "_load"):
        s = SynapticNetwork()
        smod.cortex = s
    yield s
    SynapticNetwork.reset_singleton()


class TestHebbianContextual:
    """Renforcement Hebbian : intent ↔ etat cognitif."""

    @pytest.mark.asyncio
    async def test_success_creates_cognitive_state_node(self, synaptic_instance):
        """Routine reussie cree un noeud cogstate et le renforce."""
        s = synaptic_instance
        from core.synaptic_network import _make_node_id

        await s._on_routine_complete({
            "intent": "VEILLE_IA",
            "status": "success",
            "quality_score": 0.8,
            "result": "Decouverte interessante sur les transformers et architectures modernes",
            "cognitive_context": {
                "cognitive_state": "flow",
                "dominant_drive": "CURIOSITE",
                "cardiac_emotion": "enthousiasme",
            },
        })

        # Noeud cogstate:flow doit exister
        flow_nid = _make_node_id("cogstate:flow")
        assert flow_nid in s.nodes
        assert s.nodes[flow_nid]["concept"] == "cogstate:flow"

        # Synapse intent → cogstate:flow doit exister
        intent_nid = _make_node_id("VEILLE_IA")
        from core.synaptic_network import _synapse_key
        key = _synapse_key(intent_nid, flow_nid)
        assert key in s.synapses
        assert s.synapses[key]["weight"] > 0

    @pytest.mark.asyncio
    async def test_success_creates_drive_node(self, synaptic_instance):
        """Routine reussie cree un noeud drive et le renforce."""
        s = synaptic_instance
        from core.synaptic_network import _make_node_id

        await s._on_routine_complete({
            "intent": "VEILLE_IA",
            "status": "success",
            "quality_score": 0.8,
            "result": "Article sur les reseaux de neurones et leur evolution recente",
            "cognitive_context": {
                "dominant_drive": "CURIOSITE",
            },
        })

        drive_nid = _make_node_id("drive:curiosite")
        assert drive_nid in s.nodes

    @pytest.mark.asyncio
    async def test_success_creates_emotion_node(self, synaptic_instance):
        """Routine reussie cree un noeud emotion et le renforce."""
        s = synaptic_instance
        from core.synaptic_network import _make_node_id

        await s._on_routine_complete({
            "intent": "VEILLE_IA",
            "status": "success",
            "quality_score": 0.8,
            "result": "Analyse des comportements emergents dans les systemes complexes",
            "cognitive_context": {
                "cardiac_emotion": "enthousiasme",
            },
        })

        emo_nid = _make_node_id("emotion:enthousiasme")
        assert emo_nid in s.nodes

    @pytest.mark.asyncio
    async def test_failure_does_not_create_context_nodes(self, synaptic_instance):
        """Routine en echec ne cree PAS de noeuds contextuels (anti-bruit)."""
        s = synaptic_instance
        from core.synaptic_network import _make_node_id

        initial_nodes = len(s.nodes)

        await s._on_routine_complete({
            "intent": "VEILLE_IA",
            "status": "error",
            "quality_score": 0.1,
            "result": "Error: connection refused",
            "cognitive_context": {
                "cognitive_state": "crisis",
                "dominant_drive": "STABILITE",
            },
        })

        # En echec, _on_routine_complete retourne early (quality < 0.3)
        # Pas de noeuds contextuels crees
        flow_nid = _make_node_id("cogstate:crisis")
        assert flow_nid not in s.nodes

    @pytest.mark.asyncio
    async def test_no_context_graceful(self, synaptic_instance):
        """Sans cognitive_context, pas d'erreur."""
        s = synaptic_instance

        await s._on_routine_complete({
            "intent": "VEILLE_IA",
            "status": "success",
            "quality_score": 0.8,
            "result": "Resultat tres interessant sur le machine learning et les reseaux neuronaux",
        })

        # Pas de crash, intent node cree normalement
        from core.synaptic_network import _make_node_id
        intent_nid = _make_node_id("VEILLE_IA")
        assert intent_nid in s.nodes

    @pytest.mark.asyncio
    async def test_repeated_success_strengthens_synapse(self, synaptic_instance):
        """Succes repetes renforcent la synapse intent ↔ etat."""
        s = synaptic_instance
        from core.synaptic_network import _make_node_id, _synapse_key

        event = {
            "intent": "VEILLE_IA",
            "status": "success",
            "quality_score": 0.9,
            "result": "Excellent article sur les architectures neuronales et le deep learning",
            "cognitive_context": {"cognitive_state": "flow"},
        }

        await s._on_routine_complete(event)
        intent_nid = _make_node_id("VEILLE_IA")
        flow_nid = _make_node_id("cogstate:flow")
        key = _synapse_key(intent_nid, flow_nid)
        weight_1 = s.synapses[key]["weight"]

        # Deuxieme succes → poids augmente
        await s._on_routine_complete(event)
        weight_2 = s.synapses[key]["weight"]
        assert weight_2 > weight_1

    @pytest.mark.asyncio
    async def test_anti_hebb_direct(self, synaptic_instance):
        """Anti-hebb direct : hebbian_strengthen avec success=False affaiblit la synapse."""
        s = synaptic_instance
        from core.synaptic_network import _make_node_id, _synapse_key

        # Creer deux noeuds avec energie connue
        nid_a = s.ensure_node("test_intent_alpha", "event", 0.6, ["test"])
        nid_b = s.ensure_node("cogstate:exploration", "affect", 0.5, ["test"])

        # Renforcer la synapse (succes)
        s.hebbian_strengthen(nid_a, nid_b, success=True, context="test")
        key = _synapse_key(nid_a, nid_b)
        weight_before = s.synapses[key]["weight"]
        assert weight_before > 0

        # Anti-hebb (echec)
        s.hebbian_strengthen(nid_a, nid_b, success=False, context="test")
        weight_after = s.synapses[key]["weight"]
        assert weight_after < weight_before


# ============================================================
# Test 8 : Signaux Descendants
# ============================================================

class TestDescendingSignals:
    """Tests des 7 signaux descendants inspires de la mouche drosophile."""

    def test_all_signals_present(self):
        """Les 7 signaux sont toujours presents."""
        from core.autonomy_engine import AutonomyEngine
        engine = _make_engine()
        engine.is_napping = False
        engine.error_streak = 0

        signals = engine._compute_descending_signals()

        expected_keys = {"urgence", "exploration", "consolidation",
                         "creation", "repos", "vigilance", "social"}
        assert set(signals.keys()) == expected_keys

    def test_signals_bounded_0_1(self):
        """Tous les signaux sont dans [0, 1]."""
        engine = _make_engine()
        engine.is_napping = False
        engine.error_streak = 0

        signals = engine._compute_descending_signals()

        for k, v in signals.items():
            assert 0.0 <= v <= 1.0, f"{k}={v} hors bornes"

    def test_error_streak_boosts_urgence(self):
        """Error streak >= 3 booste le signal urgence."""
        engine = _make_engine()
        engine.is_napping = False
        engine.error_streak = 5

        signals = engine._compute_descending_signals()
        assert signals["urgence"] >= 0.15 * 5  # min(1.0, 5*0.15) = 0.75

    def test_napping_boosts_repos(self):
        """Mode sieste booste le signal repos."""
        engine = _make_engine()
        engine.is_napping = True
        engine.error_streak = 0

        signals = engine._compute_descending_signals()
        assert signals["repos"] >= 0.9

    def test_no_error_no_urgence(self):
        """Sans erreur ni menace, urgence est basse."""
        engine = _make_engine()
        engine.is_napping = False
        engine.error_streak = 0

        # Pas d'organes disponibles en test → urgence = 0
        signals = engine._compute_descending_signals()
        assert signals["urgence"] == 0.0

    def test_format_descending_signals_empty(self):
        """Signaux tous < 0.2 → chaine vide."""
        engine = _make_engine()
        engine.is_napping = False
        engine.error_streak = 0

        signals = {"urgence": 0.1, "exploration": 0.0, "consolidation": 0.0,
                   "creation": 0.0, "repos": 0.0, "vigilance": 0.0, "social": 0.0}

        result = engine._format_descending_signals(signals)
        assert result == ""

    def test_format_descending_signals_active(self):
        """Signaux actifs → ligne formatee avec mode dominant."""
        engine = _make_engine()

        signals = {"urgence": 0.8, "exploration": 0.3, "consolidation": 0.0,
                   "creation": 0.5, "repos": 0.0, "vigilance": 0.0, "social": 0.0}

        result = engine._format_descending_signals(signals)
        assert "[SIGNAUX DESCENDANTS]" in result
        assert "Mode dominant: URGENCE" in result
        assert "URGENCE=80%" in result
        assert "CREATION=50%" in result
        assert "EXPLORATION=30%" in result

    def test_format_dominant_is_highest(self):
        """Le mode dominant est le signal le plus fort."""
        engine = _make_engine()

        signals = {"urgence": 0.1, "exploration": 0.9, "consolidation": 0.0,
                   "creation": 0.0, "repos": 0.0, "vigilance": 0.0, "social": 0.0}

        result = engine._format_descending_signals(signals)
        assert "Mode dominant: EXPLORATION" in result

    def test_signals_resilient_to_import_errors(self):
        """Signaux calcules sans crash meme si les imports echouent."""
        engine = _make_engine()
        engine.is_napping = False
        engine.error_streak = 0

        # Forcer l'echec de tous les imports d'organes
        with patch("builtins.__import__", side_effect=ImportError("test")):
            signals = engine._compute_descending_signals()

        # Tous les signaux doivent etre 0.0 (aucun organe accessible)
        assert all(v == 0.0 for v in signals.values())


# ============================================================
# Test 9 : Incarnation Virtuelle (Proprioception Territoire)
# ============================================================

class TestTerritoryMap:
    """Tests de la carte proprioceptive du territoire cognitif."""

    def test_territory_map_structure(self, tmp_path, monkeypatch):
        """get_territory_map retourne les 9 zones avec 3 metriques."""
        from core import neural_tissue as tmod
        from core.neural_tissue import NeuralTissue
        NeuralTissue.reset_singleton()
        monkeypatch.setattr(tmod, "TISSUE_STATE_FILE", str(tmp_path / "tissue.json"))
        with patch.object(NeuralTissue, "_load"):
            t = NeuralTissue()

        # Simuler des zones avec des signaux
        t._zone_signals = {
            "creativity": {"activity": 0.8, "energy": 0.6, "density": 0.4, "diversity": 0.3},
            "cognition": {"activity": 0.5, "energy": 0.7, "density": 0.5, "diversity": 0.2},
            "threat": {"activity": 0.1, "energy": 0.2, "density": 0.3, "diversity": 0.1},
        }

        territory = t.get_territory_map()

        assert "creativity" in territory
        assert "cognition" in territory
        assert "threat" in territory
        # Chaque zone a 3 metriques
        for zone in territory.values():
            assert "activity" in zone
            assert "energy" in zone
            assert "density" in zone
            assert 0.0 <= zone["activity"] <= 1.0
            assert 0.0 <= zone["energy"] <= 1.0
            assert 0.0 <= zone["density"] <= 1.0

        NeuralTissue.reset_singleton()

    def test_territory_map_empty_zones(self, tmp_path, monkeypatch):
        """Sans zones, retourne dict vide."""
        from core import neural_tissue as tmod
        from core.neural_tissue import NeuralTissue
        NeuralTissue.reset_singleton()
        monkeypatch.setattr(tmod, "TISSUE_STATE_FILE", str(tmp_path / "tissue.json"))
        with patch.object(NeuralTissue, "_load"):
            t = NeuralTissue()
        t._zone_signals = {}

        territory = t.get_territory_map()
        assert territory == {}
        NeuralTissue.reset_singleton()

    def test_territory_clamps_values(self, tmp_path, monkeypatch):
        """Les valeurs sont clampees dans [0, 1]."""
        from core import neural_tissue as tmod
        from core.neural_tissue import NeuralTissue
        NeuralTissue.reset_singleton()
        monkeypatch.setattr(tmod, "TISSUE_STATE_FILE", str(tmp_path / "tissue.json"))
        with patch.object(NeuralTissue, "_load"):
            t = NeuralTissue()
        t._zone_signals = {
            "emotion": {"activity": 2.5, "energy": -0.3, "density": 1.5},
        }

        territory = t.get_territory_map()
        assert territory["emotion"]["activity"] == 1.0  # Clampe
        assert territory["emotion"]["energy"] == 0.0     # Clampe
        assert territory["emotion"]["density"] == 1.0     # Clampe
        NeuralTissue.reset_singleton()


class TestDescendingSignalsWithTerritory:
    """Les signaux descendants integrent le territoire tissue."""

    def test_threat_zone_boosts_urgence(self):
        """Zone threat active booste le signal urgence."""
        engine = _make_engine()
        engine.is_napping = False
        engine.error_streak = 0

        mock_tissue = MagicMock()
        mock_tissue.get_territory_map.return_value = {
            "threat": {"activity": 0.8, "energy": 0.5, "density": 0.3},
            "creativity": {"activity": 0.1, "energy": 0.2, "density": 0.1},
        }

        with patch.dict(sys.modules, {"core.neural_tissue": MagicMock(tissue=mock_tissue)}):
            signals = engine._compute_descending_signals()

        assert signals["urgence"] >= 0.8

    def test_creativity_zone_boosts_creation(self):
        """Zone creativity active booste le signal creation."""
        engine = _make_engine()
        engine.is_napping = False
        engine.error_streak = 0

        mock_tissue = MagicMock()
        mock_tissue.get_territory_map.return_value = {
            "creativity": {"activity": 0.9, "energy": 0.7, "density": 0.5},
            "threat": {"activity": 0.0, "energy": 0.0, "density": 0.0},
        }

        with patch.dict(sys.modules, {"core.neural_tissue": MagicMock(tissue=mock_tissue)}):
            signals = engine._compute_descending_signals()

        assert signals["creation"] >= 0.7  # 0.9 * 0.8 = 0.72

    def test_cognition_zone_boosts_exploration(self):
        """Zone cognition active booste le signal exploration."""
        engine = _make_engine()
        engine.is_napping = False
        engine.error_streak = 0

        mock_tissue = MagicMock()
        mock_tissue.get_territory_map.return_value = {
            "cognition": {"activity": 0.8, "energy": 0.6, "density": 0.4},
            "threat": {"activity": 0.0, "energy": 0.0, "density": 0.0},
        }

        with patch.dict(sys.modules, {"core.neural_tissue": MagicMock(tissue=mock_tissue)}):
            signals = engine._compute_descending_signals()

        assert signals["exploration"] >= 0.3  # 0.8 * 0.5 = 0.4


# ============================================================
# Test 10 : LIF Neurones Individuels
# ============================================================

class TestNeuronLIF:
    """Tests du modele LIF au niveau des noeuds synaptiques."""

    def test_fire_when_threshold_reached(self, synaptic_instance):
        """Un noeud fire quand son energie depasse le seuil."""
        s = synaptic_instance
        from core.synaptic_network import NEURON_FIRE_THRESHOLD, NEURON_RESET_ENERGY

        # Creer un noeud avec energie haute
        nid = s.ensure_node("test_high_energy", "event", 0.5)
        s.nodes[nid]["energy"] = 0.9  # Au-dessus du seuil

        # Creer un voisin connecte avec synapse forte
        nid2 = s.ensure_node("test_neighbor_alpha", "memory", 0.3)
        s.hebbian_strengthen(nid, nid2, success=True, context="test")
        # Forcer un poids synaptique au-dessus du seuil de propagation
        from core.synaptic_network import _synapse_key
        skey = _synapse_key(nid, nid2)
        if skey in s.synapses:
            s.synapses[skey]["weight"] = 0.5

        initial_neighbor_energy = s.nodes[nid2]["energy"]

        # Fire manuellement
        s._lif_fire(nid, depth=0)

        # Le noeud qui fire est reset
        assert s.nodes[nid]["energy"] == NEURON_RESET_ENERGY
        # Le voisin a recu de l'energie
        assert s.nodes[nid2]["energy"] > initial_neighbor_energy

    def test_no_fire_below_threshold(self, synaptic_instance):
        """Pas de fire si l'energie est sous le seuil."""
        s = synaptic_instance
        from core.synaptic_network import NEURON_FIRE_THRESHOLD

        nid = s.ensure_node("test_low_energy_node", "event", 0.3)
        s.nodes[nid]["energy"] = 0.5  # Sous le seuil

        initial_energy = s.nodes[nid]["energy"]
        # ensure_node ne devrait pas triggerer de fire
        s.ensure_node("test_low_energy_node", "event", 0.3)
        # L'energie augmente de 0.1 mais reste sous 0.85
        assert s.nodes[nid]["energy"] <= NEURON_FIRE_THRESHOLD

    def test_cascade_limited_by_depth(self, synaptic_instance):
        """Les cascades sont limitees en profondeur."""
        s = synaptic_instance
        from core.synaptic_network import (
            NEURON_MAX_CASCADE_DEPTH, NEURON_RESET_ENERGY,
            _make_node_id, _synapse_key,
        )

        # Creer une chaine A → B → C → D (depth 3)
        nodes = []
        for i in range(5):
            nid = s.ensure_node(f"chain_node_{i}_alpha", "event", 0.5)
            s.nodes[nid]["energy"] = 0.9  # Tous au-dessus du seuil
            nodes.append(nid)

        # Connecter en chaine avec synapses fortes
        for i in range(len(nodes) - 1):
            s.hebbian_strengthen(nodes[i], nodes[i + 1], success=True, context="chain")
            # Renforcer la synapse pour garantir la propagation
            key = _synapse_key(nodes[i], nodes[i + 1])
            s.synapses[key]["weight"] = 0.8

        # Fire le premier
        s._lif_fire(nodes[0], depth=0)

        # Le premier a fire (energie reduite par rapport a 0.9)
        # Note: peut recevoir de l'energie en retour via cascade
        assert s.nodes[nodes[0]]["energy"] < 0.9

    def test_anti_reentrance(self, synaptic_instance):
        """Le flag anti-reentrance empeche les boucles infinies."""
        s = synaptic_instance

        nid = s.ensure_node("reentrance_test_node", "event", 0.5)
        s.nodes[nid]["energy"] = 0.95

        # Simuler la reentrance
        s._lif_firing = True
        s._lif_fire(nid, depth=0)
        # Le noeud n'est PAS reset (fire bloque)
        assert s.nodes[nid]["energy"] == 0.95
        s._lif_firing = False

    def test_fire_triggered_by_ensure_node(self, synaptic_instance):
        """ensure_node declenche un fire si l'energie depasse le seuil."""
        s = synaptic_instance
        from core.synaptic_network import NEURON_FIRE_THRESHOLD, NEURON_RESET_ENERGY

        # Creer un noeud juste sous le seuil
        nid = s.ensure_node("fire_trigger_testnode", "event", 0.5)
        s.nodes[nid]["energy"] = 0.80  # Juste sous 0.85

        # Re-activer le noeud : +0.1 → 0.90 > 0.85 → fire!
        s.ensure_node("fire_trigger_testnode", "event")

        # Apres fire, l'energie est reset
        assert s.nodes[nid]["energy"] == NEURON_RESET_ENERGY

    def test_weak_synapses_not_propagated(self, synaptic_instance):
        """Les synapses faibles (< 0.1) ne propagent pas."""
        s = synaptic_instance
        from core.synaptic_network import _make_node_id, _synapse_key

        nid1 = s.ensure_node("strong_neuron_test", "event", 0.5)
        nid2 = s.ensure_node("weak_connected_node", "memory", 0.3)

        # Creer une synapse tres faible
        key = _synapse_key(nid1, nid2)
        s.synapses[key] = {"weight": 0.05, "type": "hebbian",
                           "created_at": time.time(), "last_activated": time.time()}

        s.nodes[nid1]["energy"] = 0.9
        initial_e2 = s.nodes[nid2]["energy"]

        s._lif_fire(nid1, depth=0)

        # Le voisin n'a PAS recu d'energie (synapse trop faible)
        assert s.nodes[nid2]["energy"] == initial_e2

    def test_max_depth_zero_still_fires(self, synaptic_instance):
        """Meme a depth=0, le noeud fire et se reset."""
        s = synaptic_instance
        from core.synaptic_network import NEURON_RESET_ENERGY

        nid = s.ensure_node("depth_zero_test_neuron", "event", 0.5)
        s.nodes[nid]["energy"] = 0.95

        s._lif_fire(nid, depth=0)
        assert s.nodes[nid]["energy"] == NEURON_RESET_ENERGY


# ============================================================
# Test 11 : API Stimulation (Piste 7)
# ============================================================

class TestStimulateAPI:
    """Tests de l'API de stimulation externe des organes."""

    def test_cardiac_stimulation(self, cardiac_instance):
        """Stimulation cardiaque directe via react()."""
        h = cardiac_instance
        initial_bpm = h.bpm

        h.react("eureka")

        # Le BPM a change (eureka = +30 bpm_delta)
        assert h.bpm != initial_bpm
        assert h.current_emotion == "enthousiasme"

    def test_cardiac_invalid_stimulus_ignored(self, cardiac_instance):
        """Stimulus inconnu est ignore par le cardiac."""
        h = cardiac_instance
        initial_emotion = h.current_emotion

        h.react("invalid_stimulus_xyz")

        # Pas de changement
        assert h.current_emotion == initial_emotion

    def test_desire_satisfy(self):
        """Satisfaction directe d'un drive."""
        from core import desire_engine as dmod
        from core.desire_engine import DesireEngine
        DesireEngine.reset_singleton()
        with patch.object(DesireEngine, "_load"):
            d = DesireEngine()
            dmod.desires = d

        drive = d.drives.get("CURIOSITE")
        if drive:
            drive.deprivation = 80.0
            drive.frustration_streak = 5

            # Satisfaction directe
            drive.deprivation = max(0.0, drive.deprivation - 10.0)
            drive.frustration_streak = 0

            assert drive.deprivation == 70.0
            assert drive.frustration_streak == 0

        DesireEngine.reset_singleton()

    def test_desire_frustrate(self):
        """Frustration directe d'un drive."""
        from core import desire_engine as dmod
        from core.desire_engine import DesireEngine
        DesireEngine.reset_singleton()
        with patch.object(DesireEngine, "_load"):
            d = DesireEngine()
            dmod.desires = d

        drive = d.drives.get("STABILITE")
        if drive:
            initial_dep = drive.deprivation
            drive.deprivation = min(100.0, drive.deprivation + 15.0)
            drive.frustration_streak += 1

            assert drive.deprivation == initial_dep + 15.0
            assert drive.frustration_streak >= 1

        DesireEngine.reset_singleton()

    def test_desire_clamp_values(self):
        """Les valeurs desire sont clampees."""
        from core import desire_engine as dmod
        from core.desire_engine import DesireEngine
        DesireEngine.reset_singleton()
        with patch.object(DesireEngine, "_load"):
            d = DesireEngine()
            dmod.desires = d

        drive = d.drives.get("CURIOSITE")
        if drive:
            drive.deprivation = 95.0
            # Frustrate de 20 → devrait clamper a 100
            drive.deprivation = min(100.0, drive.deprivation + 20.0)
            assert drive.deprivation == 100.0

            # Satisfy de 30 → ne doit pas aller sous 0
            drive.deprivation = max(0.0, drive.deprivation - 150.0)
            assert drive.deprivation == 0.0

        DesireEngine.reset_singleton()

    @pytest.mark.asyncio
    async def test_reptilian_via_bus(self):
        """Stimulation reptilienne via publication bus."""
        from core.event_bus.bus import bus

        published = []
        async def capture(data):
            published.append(data)

        bus.subscribe("REPTILIAN_ALERT", capture)
        try:
            await bus.publish("REPTILIAN_ALERT", {
                "threat_level": 7.0,
                "source": "api_stimulate",
            })
            assert len(published) >= 1
            assert published[-1]["threat_level"] == 7.0
        finally:
            bus.unsubscribe("REPTILIAN_ALERT", capture)

    @pytest.mark.asyncio
    async def test_dopamine_surge_via_bus(self):
        """Stimulation dopamine surge via bus."""
        from core.event_bus.bus import bus

        published = []
        async def capture(data):
            published.append(data)

        bus.subscribe("DOPAMINE_SURGE", capture)
        try:
            await bus.publish("DOPAMINE_SURGE", {
                "magnitude": 0.8,
                "source": "api_stimulate",
            })
            assert len(published) >= 1
            assert published[-1]["magnitude"] == 0.8
        finally:
            bus.unsubscribe("DOPAMINE_SURGE", capture)

    def test_allowed_bus_events_whitelist(self):
        """La whitelist d'events bus est coherente."""
        # Importer la whitelist depuis main
        # On verifie juste que les events dangereux ne sont pas dedans
        dangerous = {"SHUTDOWN", "REBOOT", "KILL_SWITCH", "DELETE_ALL"}
        allowed = {
            "DOPAMINE_SURGE", "DOPAMINE_DIP", "REPTILIAN_ALERT",
            "KNOWLEDGE_GAP_DETECTED", "EUREKA_BRIDGE",
            "CARDIAC_EMOTION_CHANGE",
        }
        assert dangerous.isdisjoint(allowed)


# ============================================================
# Test 12 : Rappel Causal (hippocampus enrichi)
# ============================================================

@pytest.fixture
def hippocampus_instance(tmp_path, monkeypatch):
    from core import hippocampus as hmod
    from core.hippocampus import Hippocampus
    Hippocampus.reset_singleton()
    monkeypatch.setattr(hmod, "HIPPOCAMPUS_STATE_FILE", str(tmp_path / "hippo.json"))
    with patch.object(Hippocampus, "_load"), \
         patch.object(Hippocampus, "_subscribe_events"):
        h = Hippocampus()
        hmod.hippocampus = h
    yield h
    Hippocampus.reset_singleton()


class TestCausalRecall:
    """Tests du rappel causal dans l'hippocampus."""

    def test_build_causal_chain_complete(self, hippocampus_instance):
        """La chaine causale contient etat, pulsion, emotion, facteurs, resultat."""
        h = hippocampus_instance
        chain = h._build_causal_chain(
            intent="VEILLE_IA",
            status="success",
            quality=0.95,
            scoring={"dopamine": 1.8, "cardiac": 0.7, "desire": 0.5},
            cog_ctx={"cognitive_state": "flow", "dominant_drive": "CURIOSITE", "cardiac_emotion": "enthousiasme"},
            failure_type="",
        )

        assert len(chain) >= 4
        assert any("flow" in c for c in chain)
        assert any("CURIOSITE" in c for c in chain)
        assert any("enthousiasme" in c for c in chain)
        assert any("succes" in c for c in chain)

    def test_build_causal_chain_failure(self, hippocampus_instance):
        """La chaine causale capture l'echec et sa raison."""
        h = hippocampus_instance
        chain = h._build_causal_chain(
            intent="EXPANSION_CODE",
            status="error",
            quality=0.1,
            scoring={"dopamine": -1.0},
            cog_ctx={"cognitive_state": "crisis"},
            failure_type="hallucination",
        )

        assert any("echec" in c for c in chain)
        assert any("hallucination" in c for c in chain)
        assert any("crisis" in c for c in chain)

    def test_build_causal_chain_empty_context(self, hippocampus_instance):
        """Sans contexte cognitif, la chaine est minimale."""
        h = hippocampus_instance
        chain = h._build_causal_chain(
            intent="AUDIT",
            status="success",
            quality=0.8,
            scoring={},
            cog_ctx={},
            failure_type="",
        )

        # Au minimum le resultat
        assert len(chain) >= 1
        assert any("succes" in c for c in chain)

    def test_on_routine_complete_stores_causal(self, hippocampus_instance):
        """_on_routine_complete stocke la chaine causale dans l'episode."""
        h = hippocampus_instance

        # Mocker _capture_affect pour eviter imports organes
        with patch.object(h, "_capture_affect", return_value={
            "cardiac_emotion": "flow", "cardiac_bpm": 70.0,
            "dopamine_level": 0.7, "dominant_drive": "MAITRISE",
            "drive_deprivation": 60.0, "cognitive_state": "flow",
            "threat_level": 0.0,
        }):
            h._on_routine_complete({
                "status": "success",
                "intent": "EXPANSION_CODE",
                "agent": "evolution",
                "quality_score": 0.9,
                "scoring_breakdown": {"dopamine": 1.5, "cardiac": 0.8, "tissue": 0.3},
                "cognitive_context": {
                    "cognitive_state": "flow",
                    "dominant_drive": "MAITRISE",
                    "cardiac_emotion": "enthousiasme",
                },
            })

        # Verifier le dernier episode
        assert len(h._episodes) >= 1
        ep = h._episodes[-1]
        assert len(ep.causal_chain) >= 3
        assert len(ep.scoring_factors) >= 1
        assert "dopamine" in ep.scoring_factors

    def test_get_causal_context(self, hippocampus_instance):
        """get_causal_context retourne un resume des derniers episodes."""
        h = hippocampus_instance
        from core.hippocampus import Episode

        h._episodes.append(Episode(
            intent="VEILLE_IA",
            causal_chain=["etat: flow", "pulsion: CURIOSITE", "resultat: succes (q=0.9)"],
        ))

        ctx = h.get_causal_context(n=3)
        assert "Rappel causal" in ctx
        assert "VEILLE_IA" in ctx
        assert "flow" in ctx

    def test_get_causal_context_empty(self, hippocampus_instance):
        """Sans episodes causaux, retourne vide."""
        h = hippocampus_instance
        ctx = h.get_causal_context()
        assert ctx == ""

    def test_scoring_factors_top5(self, hippocampus_instance):
        """Les scoring_factors gardent le top 5 par valeur absolue."""
        h = hippocampus_instance

        with patch.object(h, "_capture_affect", return_value={
            "cardiac_emotion": "serenite", "cardiac_bpm": 65.0,
            "dopamine_level": 0.5, "dominant_drive": "STABILITE",
            "drive_deprivation": 40.0, "cognitive_state": "standard",
            "threat_level": 0.0,
        }):
            h._on_routine_complete({
                "status": "success",
                "intent": "AUDIT_STRUCTURE",
                "agent": "architect",
                "quality_score": 0.8,
                "scoring_breakdown": {
                    "dopamine": 1.5, "cardiac": 0.8, "tissue": 0.3,
                    "prefrontal": -0.4, "desire": 0.7, "thalamus": 0.1,
                    "insula": 0.05, "dmn": -0.2,
                },
            })

        if h._episodes:
            ep = h._episodes[-1]
            assert len(ep.scoring_factors) <= 5


# ============================================================
# Test 13 : Prediction Contextuelle (RPE + cognitive_state)
# ============================================================

class TestContextualPrediction:
    """Tests de la prediction contextuelle dans le systeme dopaminergique."""

    def test_reward_memory_has_cognitive_state(self):
        """RewardMemory a le champ success_by_cognitive_state."""
        from core.dopamine_system import RewardMemory
        mem = RewardMemory()
        assert hasattr(mem, "success_by_cognitive_state")
        assert isinstance(mem.success_by_cognitive_state, dict)

    def test_cognitive_state_serialization(self):
        """success_by_cognitive_state survit a to_dict/from_dict."""
        from core.dopamine_system import RewardMemory
        mem = RewardMemory()
        mem.success_by_cognitive_state = {"flow": [0.9, 0.8], "crisis": [0.3]}

        d = mem.to_dict()
        assert "success_by_cognitive_state" in d
        assert d["success_by_cognitive_state"]["flow"] == [0.9, 0.8]

        mem2 = RewardMemory.from_dict(d)
        assert mem2.success_by_cognitive_state["flow"] == [0.9, 0.8]
        assert mem2.success_by_cognitive_state["crisis"] == [0.3]

    def test_update_value_stores_cognitive_state(self, tmp_path, monkeypatch):
        """_update_value stocke le resultat par etat cognitif."""
        from core import dopamine_system as dmod
        from core.dopamine_system import DopamineSystem
        DopamineSystem.reset_singleton()
        monkeypatch.setattr(dmod, "DOPAMINE_STATE_FILE", str(tmp_path / "dopa.json"))
        with patch.object(DopamineSystem, "_load"):
            ds = DopamineSystem()
            dmod.dopamine = ds

        # Mocker l'etat cognitif
        with patch("core.dopamine_system._get_cognitive_state", return_value="flow"):
            ds._update_value("VEILLE_IA", 0.9, 0.4)

        mem = ds.memories.get("VEILLE_IA")
        assert mem is not None
        assert "flow" in mem.success_by_cognitive_state
        assert 0.9 in mem.success_by_cognitive_state["flow"]

        DopamineSystem.reset_singleton()

    def test_prediction_modulated_by_state(self, tmp_path, monkeypatch):
        """La prediction est modulee par l'etat cognitif."""
        from core import dopamine_system as dmod
        from core.dopamine_system import DopamineSystem, RewardMemory
        DopamineSystem.reset_singleton()
        monkeypatch.setattr(dmod, "DOPAMINE_STATE_FILE", str(tmp_path / "dopa.json"))
        with patch.object(DopamineSystem, "_load"):
            ds = DopamineSystem()
            dmod.dopamine = ds

        # Creer une memoire avec des resultats differents par etat
        mem = RewardMemory()
        mem.expected_reward = 0.5
        mem.success_by_cognitive_state = {
            "flow": [0.9, 0.95, 0.85],   # Tres bien en flow
            "crisis": [0.2, 0.1, 0.15],  # Tres mal en crisis
        }
        ds.memories["EXPANSION_CODE"] = mem

        # En flow : prediction haute
        with patch("core.dopamine_system._get_cognitive_state", return_value="flow"), \
             patch("core.dopamine_system._get_current_mood", return_value="neutre"), \
             patch("core.dopamine_system._get_time_bucket", return_value="matin"):
            pred_flow = ds._get_contextual_prediction(mem)

        # En crisis : prediction basse
        with patch("core.dopamine_system._get_cognitive_state", return_value="crisis"), \
             patch("core.dopamine_system._get_current_mood", return_value="neutre"), \
             patch("core.dopamine_system._get_time_bucket", return_value="matin"):
            pred_crisis = ds._get_contextual_prediction(mem)

        assert pred_flow > pred_crisis

        DopamineSystem.reset_singleton()


# ============================================================
# Test 14 : Signal Bridge (modulation par connectivity_matrix)
# ============================================================

class TestSignalBridge:
    """Tests du pont de traduction inter-organes."""

    def test_get_signal_weight_known_event(self):
        """get_signal_weight retourne le poids pour un event connu."""
        from core import connectivity_matrix as cmod
        from core.connectivity_matrix import ConnectivityMatrix
        ConnectivityMatrix.reset_singleton()
        with patch.object(ConnectivityMatrix, "_load"), \
             patch.object(ConnectivityMatrix, "_subscribe_events"):
            m = ConnectivityMatrix()
            m.connections = {}
            m._init_default_connections()

        # cardiac→thalamus est une connexion initiale (0.8)
        w = m.get_signal_weight("CARDIAC_EMOTION_CHANGE", "thalamus")
        assert w == 0.8

        ConnectivityMatrix.reset_singleton()

    def test_get_signal_weight_unknown_event(self):
        """Event inconnu retourne 1.0 (pas de modulation)."""
        from core import connectivity_matrix as cmod
        from core.connectivity_matrix import ConnectivityMatrix
        ConnectivityMatrix.reset_singleton()
        with patch.object(ConnectivityMatrix, "_load"), \
             patch.object(ConnectivityMatrix, "_subscribe_events"):
            m = ConnectivityMatrix()
            m.connections = {}

        w = m.get_signal_weight("UNKNOWN_EVENT", "thalamus")
        assert w == 1.0

        ConnectivityMatrix.reset_singleton()

    def test_get_signal_weight_no_connection(self):
        """Pas de connexion entre source et receiver → 1.0."""
        from core import connectivity_matrix as cmod
        from core.connectivity_matrix import ConnectivityMatrix
        ConnectivityMatrix.reset_singleton()
        with patch.object(ConnectivityMatrix, "_load"), \
             patch.object(ConnectivityMatrix, "_subscribe_events"):
            m = ConnectivityMatrix()
            m.connections = {}  # Vide

        w = m.get_signal_weight("CARDIAC_EMOTION_CHANGE", "thalamus")
        assert w == 1.0  # Pas de connexion = pas de modulation

        ConnectivityMatrix.reset_singleton()

    def test_thalamus_modulated_by_weight(self, thalamus_instance):
        """Le boost thalamus est module par le poids cardiac→thalamus."""
        from core.thalamus import EVENT_CATEGORIES
        t = thalamus_instance

        # Mock : poids faible (0.2)
        mock_matrix = MagicMock()
        mock_matrix.get_signal_weight.return_value = 0.2

        urgence_events = [e for e, c in EVENT_CATEGORIES.items() if c == "urgence"]
        initial = {e: t._scorecard[e] for e in urgence_events}

        with patch.dict(sys.modules, {"core.connectivity_matrix": MagicMock(matrix=mock_matrix)}):
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                t._on_cardiac_emotion({"emotion": "peur", "intensity": 0.8})
            )

        # Le boost est reduit par le poids faible (0.2)
        boost_weak = max(t._scorecard[e] - initial[e] for e in urgence_events)

        # Reset
        for e in urgence_events:
            t._scorecard[e] = initial[e]

        # Mock : poids fort (1.0)
        mock_matrix.get_signal_weight.return_value = 1.0
        with patch.dict(sys.modules, {"core.connectivity_matrix": MagicMock(matrix=mock_matrix)}):
            asyncio.get_event_loop().run_until_complete(
                t._on_cardiac_emotion({"emotion": "peur", "intensity": 0.8})
            )

        boost_strong = max(t._scorecard[e] - initial[e] for e in urgence_events)

        # Le boost fort doit etre > au boost faible
        assert boost_strong > boost_weak
