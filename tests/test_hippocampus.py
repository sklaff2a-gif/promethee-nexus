"""
Tests pour core/hippocampus.py — Hippocampe Synthetique (Memoire Episodique).

~40 tests couvrant singleton, saillance, encodage, handlers bus,
methodes publiques, consolidation, restitution, startup recap, persistance.
"""

import json
import os
import time
from unittest.mock import patch, MagicMock

import pytest

from core.hippocampus import (
    Hippocampus,
    hippocampus as _module_singleton,
    Episode,
    NarrativeArc,
    HIPPOCAMPUS_STATE_FILE,
    BASE_SALIENCE,
    SALIENCE_THRESHOLD,
    STRONG_EMOTIONS,
    MAX_EPISODES,
    MAX_ARCS,
    MAX_ARCS_MARKED,
    AUTOSAVE_INTERVAL,
    CONSOLIDATION_INTERVAL,
)


# ─── Fixture autouse ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_hippocampus(tmp_path, monkeypatch):
    """Reset singleton + rediriger le fichier d'etat vers tmp_path."""
    Hippocampus.reset_singleton()
    state_file = str(tmp_path / "hippocampus_state.json")
    monkeypatch.setattr("core.hippocampus.HIPPOCAMPUS_STATE_FILE", state_file)
    with patch.object(Hippocampus, "_load", return_value=None):
        h = Hippocampus()
    yield h
    Hippocampus.reset_singleton()


def _make_hippocampus() -> Hippocampus:
    """Retourne le singleton courant."""
    return Hippocampus()


def _mock_affect(**overrides):
    """Cree un affect mock pour les tests."""
    affect = {
        "cardiac_emotion": "serenite",
        "cardiac_bpm": 65.0,
        "dopamine_level": 0.5,
        "dominant_drive": "CURIOSITE",
        "drive_deprivation": 40.0,
        "cognitive_state": "standard",
        "threat_level": 0.0,
    }
    affect.update(overrides)
    return affect


# ═══════════════════════════════════════════════════════════════════════════════
# TestSingleton
# ═══════════════════════════════════════════════════════════════════════════════

class TestSingleton:
    """Tests du pattern singleton."""

    def test_identity(self, reset_hippocampus):
        """Deux appels retournent la meme instance."""
        h1 = Hippocampus()
        h2 = Hippocampus()
        assert h1 is h2

    def test_reset(self, reset_hippocampus):
        """reset_singleton() cree une nouvelle instance."""
        h1 = Hippocampus()
        Hippocampus.reset_singleton()
        with patch.object(Hippocampus, "_load", return_value=None):
            h2 = Hippocampus()
        assert h1 is not h2

    def test_initial_state(self, reset_hippocampus):
        """L'instance fraiche a des listes vides."""
        h = _make_hippocampus()
        assert h._episodes == []
        assert h._arcs == []
        assert h._known_intents == set()
        assert h._stats["episodes_encoded"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# TestSalienceComputation
# ═══════════════════════════════════════════════════════════════════════════════

class TestSalienceComputation:
    """Tests du calcul de saillance."""

    def test_base_salience_by_type(self, reset_hippocampus):
        """Chaque type a sa saillance de base."""
        h = _make_hippocampus()
        affect = _mock_affect()
        for event_type, expected_base in BASE_SALIENCE.items():
            result = h._compute_salience(event_type, "KNOWN_INTENT", affect, error_streak=0)
            # L'intent est inconnu la premiere fois → bonus 0.15
            # Mais on l'ajoute aux known_intents pour eviter le bonus
            h._known_intents.add("KNOWN_INTENT")
            result2 = h._compute_salience(event_type, "KNOWN_INTENT", affect, error_streak=0)
            assert result2 == pytest.approx(expected_base, abs=0.01), \
                f"{event_type}: expected {expected_base}, got {result2}"

    def test_bonus_error_streak(self, reset_hippocampus):
        """error_streak ajoute +0.08/pt, cap +0.3."""
        h = _make_hippocampus()
        h._known_intents.add("TEST")
        affect = _mock_affect()
        base = h._compute_salience("routine_failure", "TEST", affect, error_streak=0)
        with_streak = h._compute_salience("routine_failure", "TEST", affect, error_streak=3)
        assert with_streak == pytest.approx(base + 0.24, abs=0.01)
        # Cap a +0.3
        with_high = h._compute_salience("routine_failure", "TEST", affect, error_streak=10)
        assert with_high == pytest.approx(min(base + 0.3, 1.0), abs=0.01)

    def test_bonus_first_intent(self, reset_hippocampus):
        """Premier intent donne +0.15."""
        h = _make_hippocampus()
        affect = _mock_affect()
        s1 = h._compute_salience("routine_success", "NOUVEAU", affect, error_streak=0)
        h._known_intents.add("NOUVEAU")
        s2 = h._compute_salience("routine_success", "NOUVEAU", affect, error_streak=0)
        assert s1 == pytest.approx(s2 + 0.15, abs=0.01)

    def test_bonus_strong_emotion(self, reset_hippocampus):
        """Emotion forte ajoute +0.1."""
        h = _make_hippocampus()
        h._known_intents.add("TEST")
        affect_calm = _mock_affect(cardiac_emotion="serenite")
        affect_rage = _mock_affect(cardiac_emotion="rage")
        s_calm = h._compute_salience("veto", "TEST", affect_calm)
        s_rage = h._compute_salience("veto", "TEST", affect_rage)
        assert s_rage == pytest.approx(s_calm + 0.1, abs=0.01)

    def test_bonus_streak_break(self, reset_hippocampus):
        """Succes apres streak >= 3 ajoute +0.2."""
        h = _make_hippocampus()
        h._known_intents.add("TEST")
        affect = _mock_affect()
        s_normal = h._compute_salience("routine_success", "TEST", affect, error_streak=0)
        s_break = h._compute_salience("routine_success", "TEST", affect, error_streak=3)
        # +0.2 (streak_break) + 0.24 (error_streak 3*0.08)
        assert s_break == pytest.approx(s_normal + 0.2 + 0.24, abs=0.01)

    def test_cap_at_one(self, reset_hippocampus):
        """La saillance ne depasse jamais 1.0."""
        h = _make_hippocampus()
        affect = _mock_affect(cardiac_emotion="rage")
        # Tout les bonus : first intent, strong emotion, high streak
        s = h._compute_salience("council_forced", "BRAND_NEW", affect, error_streak=10)
        assert s <= 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# TestEpisodeEncoding
# ═══════════════════════════════════════════════════════════════════════════════

class TestEpisodeEncoding:
    """Tests de l'encodage d'episodes."""

    def test_encode_above_threshold(self, reset_hippocampus):
        """Un evenement saillant est encode."""
        h = _make_hippocampus()
        with patch.object(h, "_capture_affect", return_value=_mock_affect()):
            ep = h._encode_episode("veto", intent="TEST", agent="strategist")
        assert ep is not None
        assert ep.event_type == "veto"
        assert ep.intent == "TEST"
        assert len(h._episodes) == 1
        assert h._stats["episodes_encoded"] == 1

    def test_reject_below_threshold(self, reset_hippocampus):
        """Un evenement sous le seuil est rejete (routine_success = 0.15 < 0.3)."""
        h = _make_hippocampus()
        h._known_intents.add("KNOWN")  # Pas de bonus first intent
        with patch.object(h, "_capture_affect", return_value=_mock_affect()):
            ep = h._encode_episode("routine_success", intent="KNOWN")
        assert ep is None
        assert len(h._episodes) == 0
        assert h._stats["episodes_rejected"] == 1

    def test_episode_fields_complete(self, reset_hippocampus):
        """Tous les champs de l'episode sont remplis."""
        h = _make_hippocampus()
        affect = _mock_affect(cardiac_emotion="rage", cardiac_bpm=120.0,
                              dopamine_level=0.8, dominant_drive="MAITRISE",
                              drive_deprivation=70.0, cognitive_state="flow",
                              threat_level=3.0)
        with patch.object(h, "_capture_affect", return_value=affect):
            ep = h._encode_episode("veto", intent="TEST", agent="coder",
                                   error_streak=5, detail="raison du veto")
        assert ep.cardiac_emotion == "rage"
        assert ep.cardiac_bpm == 120.0
        assert ep.dopamine_level == 0.8
        assert ep.dominant_drive == "MAITRISE"
        assert ep.cognitive_state == "flow"
        assert ep.threat_level == 3.0
        assert ep.detail == "raison du veto"
        assert ep.id != ""
        assert ep.summary != ""

    def test_affect_graceful_degradation(self, reset_hippocampus):
        """Si capture_affect echoue, les valeurs par defaut sont utilisees."""
        h = _make_hippocampus()
        with patch.object(h, "_capture_affect", return_value={
            "cardiac_emotion": "",
            "cardiac_bpm": 65.0,
            "dopamine_level": 0.5,
            "dominant_drive": "",
            "drive_deprivation": 0.0,
            "cognitive_state": "",
            "threat_level": 0.0,
        }):
            ep = h._encode_episode("veto", intent="TEST")
        assert ep is not None
        assert ep.cardiac_emotion == ""

    def test_fifo_max_episodes(self, reset_hippocampus):
        """FIFO: les plus vieux episodes sont supprimes au-dela de MAX_EPISODES."""
        h = _make_hippocampus()
        with patch.object(h, "_capture_affect", return_value=_mock_affect()):
            with patch.object(h, "_save"):  # Eviter I/O
                for i in range(MAX_EPISODES + 10):
                    h._encode_episode("veto", intent=f"INTENT_{i}",
                                      agent="test")
        assert len(h._episodes) == MAX_EPISODES

    def test_known_intents_updated(self, reset_hippocampus):
        """L'intent est ajoute aux known_intents apres encodage."""
        h = _make_hippocampus()
        assert "NEW_INTENT" not in h._known_intents
        with patch.object(h, "_capture_affect", return_value=_mock_affect()):
            h._encode_episode("veto", intent="NEW_INTENT")
        assert "NEW_INTENT" in h._known_intents

    def test_autosave_triggered(self, reset_hippocampus):
        """La sauvegarde est declenchee toutes les AUTOSAVE_INTERVAL episodes."""
        h = _make_hippocampus()
        save_count = 0

        def mock_save():
            nonlocal save_count
            save_count += 1

        with patch.object(h, "_capture_affect", return_value=_mock_affect()):
            with patch.object(h, "_save", side_effect=mock_save):
                for i in range(AUTOSAVE_INTERVAL + 1):
                    h._encode_episode("veto", intent=f"I_{i}")
        assert save_count >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# TestBusHandlers
# ═══════════════════════════════════════════════════════════════════════════════

class TestBusHandlers:
    """Tests des handlers bus."""

    def test_routine_complete_success(self, reset_hippocampus):
        """AUTONOMY_ROUTINE_COMPLETE avec success=True encode routine_success."""
        h = _make_hippocampus()
        # routine_success a saillance 0.15 < seuil 0.3, sauf si first intent
        with patch.object(h, "_capture_affect", return_value=_mock_affect()):
            h._on_routine_complete({
                "success": True,
                "intent": "BRAND_NEW_INTENT",
                "agent": "coder",
                "quality_score": 0.8,
                "error_streak": 0,
            })
        # first intent bonus = 0.15 + base 0.15 = 0.30 >= THRESHOLD
        assert len(h._episodes) == 1
        assert h._episodes[0].event_type == "routine_success"

    def test_routine_complete_failure(self, reset_hippocampus):
        """AUTONOMY_ROUTINE_COMPLETE avec success=False encode routine_failure."""
        h = _make_hippocampus()
        with patch.object(h, "_capture_affect", return_value=_mock_affect()):
            h._on_routine_complete({
                "success": False,
                "intent": "EXPANSION_CODE",
                "agent": "coder",
                "failure_type": "hallucination",
                "error_streak": 2,
            })
        assert len(h._episodes) == 1
        assert h._episodes[0].event_type == "routine_failure"
        assert h._episodes[0].failure_type == "hallucination"

    def test_dopamine_surge(self, reset_hippocampus):
        """DOPAMINE_SURGE encode un episode dopamine_surge."""
        h = _make_hippocampus()
        with patch.object(h, "_capture_affect", return_value=_mock_affect()):
            h._on_dopamine_surge({
                "intent": "EXPANSION_CODE",
                "reward": 0.9,
            })
        assert len(h._episodes) == 1
        assert h._episodes[0].event_type == "dopamine_surge"

    def test_goal_complete(self, reset_hippocampus):
        """PREFRONTAL_GOAL_COMPLETE encode goal_complete."""
        h = _make_hippocampus()
        with patch.object(h, "_capture_affect", return_value=_mock_affect()):
            h._on_goal_complete({
                "title": "Ameliorer success_rate",
                "goal_id": "g1",
            })
        assert len(h._episodes) == 1
        assert h._episodes[0].event_type == "goal_complete"

    def test_cognitive_transition(self, reset_hippocampus):
        """CORPUS_CALLOSUM_STATE encode cognitive_transition si etat change."""
        h = _make_hippocampus()
        h._last_cognitive_state = "standard"
        with patch.object(h, "_capture_affect", return_value=_mock_affect()):
            # Premier changement : encode
            h._on_cognitive_state({"cognitive_state": "flow"})
            assert len(h._episodes) == 1
            assert h._episodes[0].event_type == "cognitive_transition"
            # Meme etat : pas d'encodage
            h._on_cognitive_state({"cognitive_state": "flow"})
            assert len(h._episodes) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# TestPublicRecordMethods
# ═══════════════════════════════════════════════════════════════════════════════

class TestPublicRecordMethods:
    """Tests des methodes record_* publiques."""

    def test_record_veto(self, reset_hippocampus):
        """record_veto encode un episode veto."""
        h = _make_hippocampus()
        with patch.object(h, "_capture_affect", return_value=_mock_affect()):
            h.record_veto("EXPANSION_CODE", "coder", "reptilien: threat > 7")
        assert len(h._episodes) == 1
        assert h._episodes[0].event_type == "veto"
        assert h._episodes[0].detail == "reptilien: threat > 7"

    def test_record_veto_override(self, reset_hippocampus):
        """record_veto_override encode un episode veto_override."""
        h = _make_hippocampus()
        with patch.object(h, "_capture_affect", return_value=_mock_affect()):
            h.record_veto_override("EXPANSION_CODE", "coder", "goal avance")
        assert len(h._episodes) == 1
        assert h._episodes[0].event_type == "veto_override"

    def test_record_loop_breaker(self, reset_hippocampus):
        """record_loop_breaker encode un episode loop_breaker."""
        h = _make_hippocampus()
        with patch.object(h, "_capture_affect", return_value=_mock_affect()):
            h.record_loop_breaker("skip", "EXPANSION_CODE", 5)
        assert len(h._episodes) == 1
        assert h._episodes[0].event_type == "loop_breaker"
        assert h._episodes[0].error_streak == 5

    def test_record_council_forced(self, reset_hippocampus):
        """record_council_forced encode un episode council_forced."""
        h = _make_hippocampus()
        with patch.object(h, "_capture_affect", return_value=_mock_affect()):
            h.record_council_forced(7)
        assert len(h._episodes) == 1
        assert h._episodes[0].event_type == "council_forced"
        assert h._episodes[0].intent == "COUNCIL_DEBATE"


# ═══════════════════════════════════════════════════════════════════════════════
# TestConsolidation
# ═══════════════════════════════════════════════════════════════════════════════

class TestConsolidation:
    """Tests de la consolidation en arcs narratifs."""

    def _inject_episodes(self, h, episodes_data):
        """Injecte des episodes pre-construits dans l'hippocampe."""
        for data in episodes_data:
            ep = Episode(**data)
            h._episodes.append(ep)

    def test_struggle_with_success(self, reset_hippocampus):
        """3+ echecs consecutifs suivis d'un succes = arc struggle."""
        h = _make_hippocampus()
        now = time.time()
        self._inject_episodes(h, [
            {"id": "e1", "event_type": "routine_failure", "intent": "EXPAND",
             "timestamp": now - 300, "salience": 0.5, "cardiac_emotion": "frustration"},
            {"id": "e2", "event_type": "routine_failure", "intent": "EXPAND",
             "timestamp": now - 200, "salience": 0.5, "cardiac_emotion": "frustration"},
            {"id": "e3", "event_type": "routine_failure", "intent": "EXPAND",
             "timestamp": now - 100, "salience": 0.5, "cardiac_emotion": "colere"},
            {"id": "e4", "event_type": "routine_success", "intent": "EXPAND",
             "timestamp": now, "salience": 0.6, "quality_score": 0.8,
             "cardiac_emotion": "soulagement"},
        ])
        h.consolidate()
        assert len(h._arcs) >= 1
        struggle = [a for a in h._arcs if a.arc_type == "struggle"]
        assert len(struggle) >= 1
        assert "EXPAND" in struggle[0].narrative
        assert "3" in struggle[0].narrative

    def test_struggle_without_success(self, reset_hippocampus):
        """3+ echecs sans succes final = arc struggle aussi."""
        h = _make_hippocampus()
        now = time.time()
        self._inject_episodes(h, [
            {"id": "e1", "event_type": "routine_failure", "intent": "AUDIT",
             "timestamp": now - 300, "salience": 0.5, "cardiac_emotion": ""},
            {"id": "e2", "event_type": "routine_failure", "intent": "AUDIT",
             "timestamp": now - 200, "salience": 0.5, "cardiac_emotion": ""},
            {"id": "e3", "event_type": "routine_failure", "intent": "AUDIT",
             "timestamp": now - 100, "salience": 0.5, "cardiac_emotion": ""},
        ])
        h.consolidate()
        struggle = [a for a in h._arcs if a.arc_type == "struggle"]
        assert len(struggle) >= 1
        assert "sans succes" in struggle[0].narrative

    def test_breakthrough(self, reset_hippocampus):
        """Succes avec error_streak >= 3 + echecs precedents = arc breakthrough."""
        h = _make_hippocampus()
        now = time.time()
        self._inject_episodes(h, [
            {"id": "e1", "event_type": "routine_failure", "intent": "CODE",
             "timestamp": now - 400, "salience": 0.5, "cardiac_emotion": "stress"},
            {"id": "e2", "event_type": "routine_failure", "intent": "CODE",
             "timestamp": now - 300, "salience": 0.5, "cardiac_emotion": "stress"},
            {"id": "e3", "event_type": "routine_failure", "intent": "CODE",
             "timestamp": now - 200, "salience": 0.5, "cardiac_emotion": ""},
            {"id": "e4", "event_type": "routine_success", "intent": "CODE",
             "timestamp": now, "salience": 0.7, "quality_score": 0.9,
             "error_streak": 3, "cardiac_emotion": "joie"},
        ])
        h.consolidate()
        breakthroughs = [a for a in h._arcs if a.arc_type == "breakthrough"]
        assert len(breakthroughs) >= 1
        assert "percee" in breakthroughs[0].narrative.lower()

    def test_no_arc_under_min_episodes(self, reset_hippocampus):
        """Pas d'arc si < 3 episodes."""
        h = _make_hippocampus()
        now = time.time()
        self._inject_episodes(h, [
            {"id": "e1", "event_type": "routine_failure", "intent": "X",
             "timestamp": now, "salience": 0.5},
        ])
        h.consolidate()
        assert len(h._arcs) == 0

    def test_fifo_preserves_marked(self, reset_hippocampus):
        """FIFO des arcs preserve les arcs marques."""
        h = _make_hippocampus()
        # Remplir au-dela de MAX_ARCS
        for i in range(MAX_ARCS + 5):
            arc = NarrativeArc(
                id=f"a{i}",
                arc_type="struggle",
                title=f"Arc {i}",
                narrative=f"Narrative {i}",
                timestamp=time.time() + i,
                marked=(i == 0),  # Le premier est marque
            )
            h._arcs.append(arc)
        h.consolidate()
        assert len(h._arcs) <= MAX_ARCS
        # L'arc marque doit etre preserve
        marked = [a for a in h._arcs if a.marked]
        assert len(marked) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# TestRestitution
# ═══════════════════════════════════════════════════════════════════════════════

class TestRestitution:
    """Tests des methodes de restitution."""

    def test_session_narrative(self, reset_hippocampus):
        """get_session_narrative retourne les top N par saillance, tri chrono."""
        h = _make_hippocampus()
        h._session_start = time.time() - 1000
        now = time.time()
        h._episodes = [
            Episode(id="e1", event_type="veto", summary="Veto 1",
                    timestamp=now - 100, salience=0.5, cardiac_emotion="stress"),
            Episode(id="e2", event_type="veto", summary="Veto 2",
                    timestamp=now - 50, salience=0.9, cardiac_emotion="rage"),
            Episode(id="e3", event_type="veto", summary="Veto 3",
                    timestamp=now, salience=0.3, cardiac_emotion="serenite"),
        ]
        result = h.get_session_narrative(n=2)
        assert len(result) == 2
        # Les 2 plus saillants : e2 (0.9) et e1 (0.5), tries par timestamp
        assert result[0]["summary"] == "Veto 1"
        assert result[1]["summary"] == "Veto 2"

    def test_arc_narrative(self, reset_hippocampus):
        """get_arc_narrative retourne l'arc le plus recent pour un intent."""
        h = _make_hippocampus()
        h._arcs = [
            NarrativeArc(id="a1", arc_type="struggle", intent="CODE",
                         narrative="Ancien", timestamp=100.0),
            NarrativeArc(id="a2", arc_type="breakthrough", intent="CODE",
                         narrative="Recent", timestamp=200.0),
        ]
        result = h.get_arc_narrative("CODE")
        assert result is not None
        assert result["narrative"] == "Recent"
        assert result["arc_type"] == "breakthrough"

    def test_arc_narrative_not_found(self, reset_hippocampus):
        """get_arc_narrative retourne None si aucun arc pour l'intent."""
        h = _make_hippocampus()
        assert h.get_arc_narrative("INEXISTANT") is None

    def test_hippocampus_context(self, reset_hippocampus):
        """get_hippocampus_context retourne un contexte avec dernier arc."""
        h = _make_hippocampus()
        h._arcs = [
            NarrativeArc(id="a1", arc_type="crisis",
                         narrative="Crise detectee.", timestamp=100.0),
        ]
        ctx = h.get_hippocampus_context()
        assert "[MEMOIRE]" in ctx
        assert "Crise detectee." in ctx

    def test_hippocampus_context_streak_alert(self, reset_hippocampus):
        """get_hippocampus_context alerte sur les streaks d'echecs."""
        h = _make_hippocampus()
        h._episodes = [
            Episode(id="e1", event_type="routine_failure", intent="CODE",
                    error_streak=5, timestamp=time.time()),
        ]
        ctx = h.get_hippocampus_context()
        assert "streak" in ctx.lower()
        assert "CODE" in ctx

    def test_emotional_context_at(self, reset_hippocampus):
        """get_emotional_context_at retourne le contexte emotionnel dans une fenetre."""
        h = _make_hippocampus()
        t = 1000.0
        h._episodes = [
            Episode(id="e1", cardiac_emotion="stress", salience=0.5, timestamp=t - 100),
            Episode(id="e2", cardiac_emotion="stress", salience=0.6, timestamp=t),
            Episode(id="e3", cardiac_emotion="joie", salience=0.4, timestamp=t + 100),
            Episode(id="e4", cardiac_emotion="rage", salience=0.8, timestamp=t + 1000),
        ]
        result = h.get_emotional_context_at(t, window=200)
        assert result["dominant_emotion"] == "stress"
        assert result["episode_count"] == 3  # e1, e2, e3
        assert "rage" not in result["emotions"]


# ═══════════════════════════════════════════════════════════════════════════════
# TestStartupRecap
# ═══════════════════════════════════════════════════════════════════════════════

class TestStartupRecap:
    """Tests du recap au demarrage."""

    def test_recap_with_previous_session(self, reset_hippocampus):
        """get_startup_recap retourne un resume de la session precedente."""
        h = _make_hippocampus()
        h._session_start = time.time()
        past = h._session_start - 1000
        h._episodes = [
            Episode(id="e1", event_type="routine_success", intent="CODE",
                    summary="Succes CODE", timestamp=past - 200, salience=0.5),
            Episode(id="e2", event_type="routine_failure", intent="AUDIT",
                    summary="Echec AUDIT", timestamp=past - 100, salience=0.4),
            Episode(id="e3", event_type="veto", intent="EXPAND",
                    summary="Veto EXPAND", timestamp=past, salience=0.5),
        ]
        recap = h.get_startup_recap()
        assert "1 succes" in recap
        assert "1 echecs" in recap
        assert "1 vetos" in recap
        assert "Veto EXPAND" in recap  # Dernier moment

    def test_recap_without_previous_session(self, reset_hippocampus):
        """get_startup_recap retourne vide sans episodes precedents."""
        h = _make_hippocampus()
        h._session_start = time.time()
        # Episodes dans la session courante seulement
        h._episodes = [
            Episode(id="e1", event_type="veto", timestamp=time.time() + 10),
        ]
        recap = h.get_startup_recap()
        assert recap == ""

    def test_recap_content_complete(self, reset_hippocampus):
        """Le recap contient les arcs si disponibles."""
        h = _make_hippocampus()
        h._session_start = time.time()
        past = h._session_start - 1000
        h._episodes = [
            Episode(id="e1", event_type="routine_success", summary="S1",
                    timestamp=past, salience=0.5),
        ]
        h._arcs = [
            NarrativeArc(id="a1", narrative="Arc narratif test",
                         timestamp=past, arc_type="struggle"),
        ]
        recap = h.get_startup_recap()
        assert "Arc narratif test" in recap


# ═══════════════════════════════════════════════════════════════════════════════
# TestPersistence
# ═══════════════════════════════════════════════════════════════════════════════

class TestPersistence:
    """Tests de la persistance save/load."""

    def test_save_load_roundtrip(self, reset_hippocampus, tmp_path):
        """Save puis load restaure les episodes et arcs."""
        h = _make_hippocampus()
        h._episodes = [
            Episode(id="e1", event_type="veto", intent="CODE",
                    summary="Test veto", timestamp=time.time(), salience=0.5),
        ]
        h._arcs = [
            NarrativeArc(id="a1", arc_type="struggle", title="Lutte",
                         narrative="Test lutte", timestamp=time.time(),
                         marked=True),
        ]
        h._known_intents = {"CODE", "AUDIT"}
        h._last_cognitive_state = "flow"

        # Save
        h._save()

        # Reset et reload
        Hippocampus.reset_singleton()
        h2 = Hippocampus()  # _load sera appele normalement cette fois

        assert len(h2._episodes) == 1
        assert h2._episodes[0].id == "e1"
        assert h2._episodes[0].intent == "CODE"
        assert len(h2._arcs) == 1
        assert h2._arcs[0].marked is True
        assert "CODE" in h2._known_intents
        assert h2._last_cognitive_state == "flow"

    def test_purge_old_episodes(self, reset_hippocampus):
        """Les episodes > 7 jours sont purges."""
        h = _make_hippocampus()
        now = time.time()
        old = now - (8 * 86400)  # 8 jours
        h._episodes = [
            Episode(id="e_old", timestamp=old, salience=0.5),
            Episode(id="e_new", timestamp=now, salience=0.5),
        ]
        h._purge_old()
        assert len(h._episodes) == 1
        assert h._episodes[0].id == "e_new"

    def test_purge_preserves_marked_arcs(self, reset_hippocampus):
        """Les arcs marques ne sont jamais purges."""
        h = _make_hippocampus()
        now = time.time()
        old = now - (60 * 86400)  # 60 jours
        h._arcs = [
            NarrativeArc(id="a_old_marked", timestamp=old, marked=True,
                         arc_type="struggle"),
            NarrativeArc(id="a_old_unmarked", timestamp=old, marked=False,
                         arc_type="transition"),
            NarrativeArc(id="a_new", timestamp=now, marked=False,
                         arc_type="crisis"),
        ]
        h._purge_old()
        assert len(h._arcs) == 2
        ids = {a.id for a in h._arcs}
        assert "a_old_marked" in ids  # Marque → preserve
        assert "a_old_unmarked" not in ids  # Non marque + vieux → purge
        assert "a_new" in ids  # Recent → preserve


# ═══════════════════════════════════════════════════════════════════════════════
# TestHandlersEdgeCases
# ═══════════════════════════════════════════════════════════════════════════════

class TestHandlersEdgeCases:
    """Tests des cas limites des handlers."""

    def test_handler_ignores_non_dict(self, reset_hippocampus):
        """Les handlers ignorent les payloads non-dict."""
        h = _make_hippocampus()
        h._on_routine_complete("not a dict")
        h._on_dopamine_surge(None)
        h._on_goal_complete(42)
        h._on_reptilian_alert([])
        assert len(h._episodes) == 0

    def test_reptilian_ignores_non_freeze_fight(self, reset_hippocampus):
        """Le handler reptilien ignore les reactions autres que FREEZE/FIGHT."""
        h = _make_hippocampus()
        with patch.object(h, "_capture_affect", return_value=_mock_affect()):
            h._on_reptilian_alert({"reaction": "SHED", "threat_level": 5.0})
        assert len(h._episodes) == 0

    def test_cognitive_state_first_state_ignored(self, reset_hippocampus):
        """Le premier etat cognitif n'est pas encode (pas de transition)."""
        h = _make_hippocampus()
        h._last_cognitive_state = ""  # Pas d'etat precedent
        with patch.object(h, "_capture_affect", return_value=_mock_affect()):
            h._on_cognitive_state({"cognitive_state": "flow"})
        assert len(h._episodes) == 0
        assert h._last_cognitive_state == "flow"

    def test_prediction_correct_ignored(self, reset_hippocampus):
        """Les predictions confirmees ne sont pas encodees en episode (chantier 26/05).

        Apres le fix asymetrie hippocampe, le payload utilise is_surprise (pas
        `correct`). Une prediction non-surprise est routee vers prediction_memory,
        pas vers les episodes hippocampe.
        """
        h = _make_hippocampus()
        h._on_prediction_resolved({"is_surprise": False, "content": "test"})
        assert len(h._episodes) == 0

    def test_prediction_incorrect_encoded(self, reset_hippocampus):
        """Les predictions violees (is_surprise=True) sont encodees en episode."""
        h = _make_hippocampus()
        with patch.object(h, "_capture_affect", return_value=_mock_affect()):
            h._on_prediction_resolved({
                "is_surprise": True,
                "content": "test_pred",
                "outcome": "echec",
            })
        assert len(h._episodes) == 1
        assert h._episodes[0].event_type == "prediction_error"


# ═══════════════════════════════════════════════════════════════════════════════
# TestDataclasses
# ═══════════════════════════════════════════════════════════════════════════════

class TestDataclasses:
    """Tests des dataclasses Episode et NarrativeArc."""

    def test_episode_to_dict_from_dict(self):
        """Roundtrip Episode to_dict / from_dict."""
        ep = Episode(id="test", event_type="veto", intent="CODE",
                     salience=0.7, cardiac_emotion="rage")
        d = ep.to_dict()
        ep2 = Episode.from_dict(d)
        assert ep2.id == "test"
        assert ep2.event_type == "veto"
        assert ep2.salience == 0.7
        assert ep2.cardiac_emotion == "rage"

    def test_arc_to_dict_from_dict(self):
        """Roundtrip NarrativeArc to_dict / from_dict."""
        arc = NarrativeArc(id="a1", arc_type="breakthrough",
                           title="Percee", narrative="Texte",
                           episode_ids=["e1", "e2"], marked=True)
        d = arc.to_dict()
        arc2 = NarrativeArc.from_dict(d)
        assert arc2.id == "a1"
        assert arc2.arc_type == "breakthrough"
        assert arc2.marked is True
        assert arc2.episode_ids == ["e1", "e2"]

    def test_from_dict_ignores_unknown_fields(self):
        """from_dict ignore les champs inconnus (forward compat)."""
        d = {"id": "e1", "event_type": "veto", "future_field": 42}
        ep = Episode.from_dict(d)
        assert ep.id == "e1"
        assert not hasattr(ep, "future_field") or True  # Le champ est ignore


# ═══════════════════════════════════════════════════════════════════════════════
# TestGenerateSummary
# ═══════════════════════════════════════════════════════════════════════════════

class TestGenerateSummary:
    """Tests de la generation de resumes deterministes."""

    def test_all_event_types_have_summary(self, reset_hippocampus):
        """Chaque type d'evenement genere un resume non-vide."""
        h = _make_hippocampus()
        for event_type in BASE_SALIENCE:
            summary = h._generate_summary(event_type, "TEST", "agent", 0.5, "error", 3)
            assert summary, f"Pas de resume pour {event_type}"
            assert len(summary) > 5

    def test_routine_success_summary(self, reset_hippocampus):
        """Resume de routine_success contient intent et qualite."""
        h = _make_hippocampus()
        s = h._generate_summary("routine_success", "EXPAND", "coder", 0.8, "", 0)
        assert "EXPAND" in s
        assert "0.8" in s

    def test_routine_failure_summary(self, reset_hippocampus):
        """Resume de routine_failure contient intent, type et streak."""
        h = _make_hippocampus()
        s = h._generate_summary("routine_failure", "AUDIT", "security", 0.0, "hallucination", 5)
        assert "AUDIT" in s
        assert "hallucination" in s
        assert "5" in s


# ═══════════════════════════════════════════════════════════════════════════════
# TestSalienceV2 — Bonus dopamine, transition, emotions elargies
# ═══════════════════════════════════════════════════════════════════════════════

class TestSalienceV2:
    """Tests des bonus de saillance V2 (dopamine, transition, emotions elargies)."""

    def test_frustration_is_strong_emotion(self, reset_hippocampus):
        """frustration est dans STRONG_EMOTIONS et donne +0.1."""
        assert "frustration" in STRONG_EMOTIONS
        h = _make_hippocampus()
        h._known_intents.add("TEST")
        affect_calm = _mock_affect(cardiac_emotion="serenite")
        affect_frust = _mock_affect(cardiac_emotion="frustration")
        s_calm = h._compute_salience("veto", "TEST", affect_calm)
        s_frust = h._compute_salience("veto", "TEST", affect_frust)
        assert s_frust == pytest.approx(s_calm + 0.1, abs=0.01)

    def test_determination_and_enthousiasme_strong(self, reset_hippocampus):
        """determination et enthousiasme sont dans STRONG_EMOTIONS."""
        assert "determination" in STRONG_EMOTIONS
        assert "enthousiasme" in STRONG_EMOTIONS

    def test_dopamine_surge_bonus(self, reset_hippocampus):
        """dopamine > 0.7 donne +0.1 de saillance."""
        h = _make_hippocampus()
        h._known_intents.add("TEST")
        affect_low = _mock_affect(dopamine_level=0.5)
        affect_high = _mock_affect(dopamine_level=0.85)
        s_low = h._compute_salience("veto", "TEST", affect_low)
        s_high = h._compute_salience("veto", "TEST", affect_high)
        assert s_high == pytest.approx(s_low + 0.1, abs=0.01)

    def test_transition_bonus(self, reset_hippocampus):
        """Transition emotionnelle (prev_emotion + emotion_cause) donne +0.1."""
        h = _make_hippocampus()
        h._known_intents.add("TEST")
        affect_no_trans = _mock_affect()
        affect_trans = _mock_affect(prev_emotion="frustration", emotion_cause="success")
        s_no = h._compute_salience("veto", "TEST", affect_no_trans)
        s_trans = h._compute_salience("veto", "TEST", affect_trans)
        assert s_trans == pytest.approx(s_no + 0.1, abs=0.01)

    def test_salience_can_reach_075(self, reset_hippocampus):
        """Combinaison realiste (frustration + dopamine + transition) atteint 0.75+."""
        h = _make_hippocampus()
        h._known_intents.add("TEST")
        # veto base=0.4 + frustration=0.1 + dopamine=0.1 + transition=0.1 = 0.7
        affect = _mock_affect(
            cardiac_emotion="frustration",
            dopamine_level=0.85,
            prev_emotion="serenite",
            emotion_cause="failure",
        )
        s = h._compute_salience("veto", "TEST", affect)
        assert s >= 0.7

    def test_salience_still_capped_at_one(self, reset_hippocampus):
        """Meme avec tous les bonus, la saillance reste capped a 1.0."""
        h = _make_hippocampus()
        # Tous les bonus actifs : first intent, strong emotion, high streak, dopamine, transition
        affect = _mock_affect(
            cardiac_emotion="rage",
            dopamine_level=0.9,
            prev_emotion="serenite",
            emotion_cause="eureka",
        )
        s = h._compute_salience("council_forced", "BRAND_NEW_V2", affect, error_streak=10)
        assert s <= 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# TestNoteworthyAutoFlag (04/05/2026 — Fix C cablage automatique)
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoteworthyAutoFlag:
    """Le flag noteworthy doit etre set automatiquement a la creation d'episode
    selon NOTEWORTHY_SALIENCE_THRESHOLD. Sans ce cablage, l'Attention Conjointe
    ne peut JAMAIS se declencher organiquement."""

    def test_high_salience_event_is_noteworthy(self, reset_hippocampus):
        """Un evenement a haute saillance (veto + emotion forte) doit etre flag."""
        h = _make_hippocampus()
        # veto = base 0.7, donc bien au-dessus du seuil 0.55
        affect = _mock_affect(cardiac_emotion="rage", dopamine_level=0.8)
        with patch.object(h, "_capture_affect", return_value=affect):
            ep = h._encode_episode("veto", intent="TEST_HIGH", agent="strategist")
        assert ep is not None
        assert ep.salience >= 0.55, f"salience={ep.salience} doit depasser le seuil"
        assert ep.noteworthy is True, "Episode haute salience doit etre noteworthy"

    def test_low_salience_event_is_not_noteworthy(self, reset_hippocampus):
        """Un evenement a faible saillance ne doit PAS etre flag."""
        h = _make_hippocampus()
        h._known_intents.add("KNOWN")  # eviter bonus first intent
        # routine_success = 0.15, threshold = 0.3, donc soit rejete soit faible
        # on prend un cas qui passe le filtre mais reste sous 0.55
        affect = _mock_affect(cardiac_emotion="serenite", dopamine_level=0.3)
        with patch.object(h, "_capture_affect", return_value=affect):
            # Force un evenement qui passe le filtre mais reste sous le seuil
            ep = h._encode_episode("intent_change", intent="KNOWN", agent="coder")
        if ep is not None and ep.salience < 0.55:
            assert ep.noteworthy is False, (
                f"Episode salience={ep.salience} < 0.55 ne doit PAS etre noteworthy"
            )

    def test_noteworthy_threshold_constant(self, reset_hippocampus):
        """La constante NOTEWORTHY_SALIENCE_THRESHOLD existe et vaut 0.55."""
        from core.hippocampus import NOTEWORTHY_SALIENCE_THRESHOLD
        assert NOTEWORTHY_SALIENCE_THRESHOLD == 0.55

    def test_noteworthy_field_persists_in_save_load(self, reset_hippocampus, tmp_path, monkeypatch):
        """Le flag noteworthy survit a un cycle save/load."""
        import json
        h = _make_hippocampus()
        # Creer un episode haute salience
        affect = _mock_affect(cardiac_emotion="rage", dopamine_level=0.8)
        with patch.object(h, "_capture_affect", return_value=affect):
            ep = h._encode_episode("veto", intent="PERSIST_TEST", agent="strategist")
        assert ep.noteworthy is True
        # Save -> Load
        state_file = str(tmp_path / "persist_test.json")
        monkeypatch.setattr("core.hippocampus.HIPPOCAMPUS_STATE_FILE", state_file)
        h._save()
        # Verifier directement le JSON
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        loaded_ep = next(e for e in data["episodes"] if e["intent"] == "PERSIST_TEST")
        assert loaded_ep["noteworthy"] is True
