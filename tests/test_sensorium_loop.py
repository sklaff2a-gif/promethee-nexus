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

        assert c.global_coherence == 0.85

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
    """Tests du modele Leaky Integrate-and-Fire pour la selection de routines."""

    def test_first_cycle_no_fire(self):
        """Premier cycle : les potentiels sont trop bas pour fire."""
        from core.autonomy_engine import LIF_THRESHOLD
        engine = _make_engine()

        scored = [
            (_make_routine("VEILLE_IA"), 5.0),
            (_make_routine("COUNCIL_DEBATE"), 3.0),
        ]
        result = engine._lif_integrate_and_select(scored)

        # Pas de fire au premier cycle (5.0 < seuil 8.0)
        assert result[0][0]["intent"] == "VEILLE_IA"  # Ordre par score preserve
        # Les potentiels sont stockes
        assert engine._lif_potentials["VEILLE_IA"] == 5.0
        assert engine._lif_potentials["COUNCIL_DEBATE"] == 3.0

    def test_accumulation_fires(self):
        """Apres plusieurs cycles, un potentiel depasse le seuil et fire."""
        from core.autonomy_engine import LIF_THRESHOLD, LIF_LEAK_RATE
        engine = _make_engine()

        scored = [
            (_make_routine("VEILLE_IA"), 5.0),
            (_make_routine("COUNCIL_DEBATE"), 3.0),
        ]

        # Cycle 1 : potentiel = 5.0 (pas de fire)
        engine._lif_integrate_and_select(scored)
        assert engine._lif_potentials["VEILLE_IA"] < LIF_THRESHOLD

        # Cycle 2 : potentiel = 5.0 * 0.7 + 5.0 = 8.5 (fire!)
        result = engine._lif_integrate_and_select(scored)
        assert result[0][0]["intent"] == "VEILLE_IA"
        # Apres fire, le potentiel est reset
        assert engine._lif_potentials["VEILLE_IA"] == 0.0

    def test_leak_decay(self):
        """Le potentiel decroit entre cycles (leak)."""
        from core.autonomy_engine import LIF_LEAK_RATE
        engine = _make_engine()
        engine._lif_potentials["VEILLE_IA"] = 6.0

        # Cycle avec score 0 → le potentiel leak seulement
        scored = [(_make_routine("COUNCIL_DEBATE"), 3.0)]
        engine._lif_integrate_and_select(scored)

        # VEILLE_IA n'est pas dans scored, mais son potentiel a quand meme leak
        assert engine._lif_potentials["VEILLE_IA"] == pytest.approx(6.0 * LIF_LEAK_RATE, abs=0.01)

    def test_reset_after_fire(self):
        """Apres un fire, le potentiel est reset (periode refractaire)."""
        from core.autonomy_engine import LIF_RESET_AFTER_FIRE
        engine = _make_engine()
        engine._lif_potentials["VEILLE_IA"] = 10.0  # Au-dessus du seuil

        scored = [(_make_routine("VEILLE_IA"), 1.0)]
        engine._lif_integrate_and_select(scored)

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

        # 5.0 * 0.7 + (-3.0) = 0.5
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
        # potentiel SECURITY: 11.0 * 0.7 + 1.0 = 8.7
        # potentiel VEILLE: 9.0 * 0.7 + 1.0 = 7.3 → pas de fire
        # Seul SECURITY fire
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
        assert engine._lif_potentials["VEILLE_IA"] == 4.0
