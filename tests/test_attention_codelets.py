"""Tests pour le systeme de codelets d'attention (LIDA)."""

import time
import pytest
from unittest.mock import patch, MagicMock

from core.attention_codelets import (
    CodeletAlert,
    CodeletSystem,
    codelet_system,
    _CODELET_REGISTRY,
    detect_danger,
    detect_stagnation,
    detect_novelty,
    detect_opportunity,
    detect_contradiction,
    detect_resonance,
    detect_convergence,
    detect_fatigue,
    detect_flow,
    detect_creativity_burst,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def reset_codelet_system():
    """Reset le singleton entre chaque test."""
    CodeletSystem.reset_singleton()
    # Recreer sans souscription bus
    with patch("core.attention_codelets.CodeletSystem._subscribe_events"):
        system = CodeletSystem()
    yield system
    CodeletSystem.reset_singleton()


def _make_tick_data(
    threat=0.0,
    dominant_drive="",
    dominant_deprivation=0,
    dopa_level=0.5,
    emotion="neutre",
    cognitive_state="standard",
    dominant_mode="repos",
):
    """Helper pour construire un tick_data de test."""
    return {
        "cognitive_state": cognitive_state,
        "dominant_mode": dominant_mode,
        "organ_states": {
            "reptilian": {"threat_level": threat, "mode": "normal"},
            "desire": {
                "dominant_drive": dominant_drive,
                "dominant_deprivation": dominant_deprivation,
                "frustrated_count": 0,
            },
            "dopamine": {"level": dopa_level, "baseline": 0.5},
            "cardiac": {
                "bpm": 65,
                "emotion": emotion,
                "coherence": 0.5,
            },
        },
    }


# ============================================================
# Tests du registre
# ============================================================

class TestCodeletRegistry:
    """Tests pour le registre de codelets."""

    def test_ten_codelets_registered(self):
        """Les 10 codelets (5 base + 5 Promethee) sont enregistres."""
        assert len(_CODELET_REGISTRY) >= 10
        expected = {
            "danger", "stagnation", "novelty", "opportunity", "contradiction",
            "resonance", "convergence", "fatigue", "flow", "creativity_burst",
        }
        assert expected.issubset(set(_CODELET_REGISTRY.keys()))

    def test_registry_has_function_and_cooldown(self):
        """Chaque entree du registre contient (function, cooldown)."""
        for name, (func, cooldown) in _CODELET_REGISTRY.items():
            assert callable(func), f"{name} n'est pas callable"
            assert isinstance(cooldown, (int, float)), f"{name} cooldown invalide"
            assert cooldown > 0, f"{name} cooldown <= 0"


# ============================================================
# Tests des codelets individuels (fonctions pures)
# ============================================================

class TestCodeletDanger:
    """Tests pour le codelet danger."""

    def test_no_alert_below_threshold(self):
        data = _make_tick_data(threat=3.0)
        assert detect_danger(data, []) is None

    def test_alert_above_threshold(self):
        data = _make_tick_data(threat=5.0)
        alert = detect_danger(data, [])
        assert alert is not None
        assert alert.name == "danger"
        assert alert.salience == 0.9
        assert alert.priority == "high"

    def test_critical_above_six(self):
        data = _make_tick_data(threat=7.0)
        alert = detect_danger(data, [])
        assert alert is not None
        assert alert.priority == "critical"

    def test_no_reptilian_data(self):
        data = {"organ_states": {"reptilian": None}}
        assert detect_danger(data, []) is None


class TestCodeletStagnation:
    """Tests pour le codelet stagnation."""

    def test_no_alert_varied_modes(self):
        history = [
            {"dominant_mode": "repos"},
            {"dominant_mode": "exploration"},
            {"dominant_mode": "creation"},
            {"dominant_mode": "repos"},
            {"dominant_mode": "vigilance"},
        ]
        assert detect_stagnation({}, history) is None

    def test_alert_same_mode_5x(self):
        history = [{"dominant_mode": "repos"}] * 5
        alert = detect_stagnation({}, history)
        assert alert is not None
        assert alert.name == "stagnation"
        assert "repos" in alert.content

    def test_no_alert_short_history(self):
        history = [{"dominant_mode": "repos"}] * 3
        assert detect_stagnation({}, history) is None

    def test_no_alert_empty_mode(self):
        history = [{"dominant_mode": ""}] * 5
        assert detect_stagnation({}, history) is None


class TestCodeletNovelty:
    """Tests pour le codelet novelty."""

    def test_alert_on_transition(self):
        history = [
            {"cognitive_state": "standard"},
            {"cognitive_state": "standard"},
        ]
        data = _make_tick_data(cognitive_state="exploration")
        alert = detect_novelty(data, history)
        assert alert is not None
        assert alert.name == "novelty"
        assert "standard" in alert.content
        assert "exploration" in alert.content

    def test_no_alert_same_state(self):
        history = [{"cognitive_state": "standard"}, {"cognitive_state": "standard"}]
        data = _make_tick_data(cognitive_state="standard")
        assert detect_novelty(data, history) is None

    def test_no_alert_transition_to_standard(self):
        history = [{"cognitive_state": "exploration"}, {"cognitive_state": "exploration"}]
        data = _make_tick_data(cognitive_state="standard")
        assert detect_novelty(data, history) is None

    def test_no_alert_short_history(self):
        assert detect_novelty({}, []) is None


class TestCodeletOpportunity:
    """Tests pour le codelet opportunity."""

    def test_no_alert_low_deprivation(self):
        data = _make_tick_data(dominant_drive="CURIOSITE", dominant_deprivation=50)
        assert detect_opportunity(data, []) is None

    def test_alert_high_deprivation(self):
        data = _make_tick_data(dominant_drive="CURIOSITE", dominant_deprivation=85)
        alert = detect_opportunity(data, [])
        assert alert is not None
        assert alert.name == "opportunity"
        assert "CURIOSITE" in alert.content

    def test_no_desire_data(self):
        data = {"organ_states": {"desire": None}}
        assert detect_opportunity(data, []) is None


class TestCodeletContradiction:
    """Tests pour le codelet contradiction."""

    def test_no_alert_coherent_state(self):
        data = _make_tick_data(dopa_level=0.8, emotion="satisfaction")
        assert detect_contradiction(data, []) is None

    def test_alert_dopamine_high_emotion_negative(self):
        data = _make_tick_data(dopa_level=0.8, emotion="frustration")
        alert = detect_contradiction(data, [])
        assert alert is not None
        assert alert.name == "contradiction"
        assert "dopamine" in alert.content

    def test_no_alert_dopamine_low(self):
        data = _make_tick_data(dopa_level=0.3, emotion="frustration")
        assert detect_contradiction(data, []) is None


# ============================================================
# Tests du CodeletSystem
# ============================================================

class TestCodeletSystem:
    """Tests pour le systeme de gestion des codelets."""

    def test_run_all_no_alerts(self, reset_codelet_system):
        system = reset_codelet_system
        data = _make_tick_data()  # Tout normal
        with patch("core.global_workspace.workspace"):
            alerts = system.run_all(data, [])
        assert alerts == []
        assert system._stats["total_runs"] == 1

    def test_run_all_with_danger(self, reset_codelet_system):
        system = reset_codelet_system
        data = _make_tick_data(threat=6.0)
        with patch("core.global_workspace.workspace"):
            alerts = system.run_all(data, [])
        assert len(alerts) == 1
        assert alerts[0].name == "danger"
        assert system._stats["total_alerts"] == 1

    def test_cooldown_respected(self, reset_codelet_system):
        system = reset_codelet_system
        data = _make_tick_data(threat=6.0)
        with patch("core.global_workspace.workspace"):
            alerts1 = system.run_all(data, [])
            alerts2 = system.run_all(data, [])
        assert len(alerts1) == 1
        assert len(alerts2) == 0  # Cooldown actif

    def test_cooldown_expires(self, reset_codelet_system):
        system = reset_codelet_system
        data = _make_tick_data(threat=6.0)
        with patch("core.global_workspace.workspace"):
            alerts1 = system.run_all(data, [])
            # Forcer l'expiration du cooldown
            system._cooldowns["danger"] = time.time() - 400
            alerts2 = system.run_all(data, [])
        assert len(alerts1) == 1
        assert len(alerts2) == 1

    def test_multiple_alerts_same_tick(self, reset_codelet_system):
        system = reset_codelet_system
        data = _make_tick_data(threat=6.0, dopa_level=0.9, emotion="peur")
        with patch("core.global_workspace.workspace"):
            alerts = system.run_all(data, [])
        names = {a.name for a in alerts}
        assert "danger" in names
        assert "contradiction" in names

    def test_get_status(self, reset_codelet_system):
        system = reset_codelet_system
        status = system.get_status()
        assert "registered_codelets" in status
        assert status["registered_codelets"] >= 5
        assert "codelets" in status
        assert "danger" in status["codelets"]

    def test_get_recent_alerts(self, reset_codelet_system):
        system = reset_codelet_system
        data = _make_tick_data(threat=6.0)
        with patch("core.global_workspace.workspace"):
            system.run_all(data, [])
        alerts = system.get_recent_alerts()
        assert len(alerts) == 1
        assert alerts[0].name == "danger"

    def test_get_registered_names(self, reset_codelet_system):
        system = reset_codelet_system
        names = system.get_registered_names()
        assert "danger" in names
        assert "stagnation" in names
        assert len(names) >= 5

    def test_workspace_submit_called(self, reset_codelet_system):
        system = reset_codelet_system
        data = _make_tick_data(threat=6.0)
        mock_ws = MagicMock()
        with patch("core.global_workspace.workspace", mock_ws):
            system.run_all(data, [])
        mock_ws.submit.assert_called_once()
        call_kwargs = mock_ws.submit.call_args
        assert call_kwargs[1]["source"] == "codelet_danger"
        assert call_kwargs[1]["salience"] == 0.9

    def test_stats_by_codelet(self, reset_codelet_system):
        system = reset_codelet_system
        data = _make_tick_data(threat=6.0)
        with patch("core.global_workspace.workspace"):
            system.run_all(data, [])
            system._cooldowns.clear()
            system.run_all(data, [])
        assert system._stats["alerts_by_codelet"]["danger"] == 2
        assert system._stats["total_alerts"] == 2


# ============================================================
# Tests d'integration CodeletAlert
# ============================================================

class TestCodeletAlert:
    """Tests pour la dataclass CodeletAlert."""

    def test_create_alert(self):
        alert = CodeletAlert(
            name="test",
            content="Test content",
            salience=0.5,
            category="cognition",
        )
        assert alert.name == "test"
        assert alert.priority == "normal"
        assert alert.timestamp > 0

    def test_alert_with_priority(self):
        alert = CodeletAlert(
            name="test",
            content="Critical",
            salience=0.9,
            category="urgence",
            priority="critical",
        )
        assert alert.priority == "critical"


# ============================================================
# Tests des 5 codelets crees par Promethee (session 3)
# ============================================================

class TestCodeletResonance:
    """Tests pour detect_resonance — harmonie systemique."""

    def test_no_alert_low_coherence(self):
        data = _make_tick_data(dopa_level=0.8, emotion="satisfaction")
        data["global_coherence"] = 0.5  # Trop bas
        assert detect_resonance(data, []) is None

    def test_no_alert_negative_emotion(self):
        data = _make_tick_data(dopa_level=0.8, emotion="frustration")
        data["global_coherence"] = 0.9
        assert detect_resonance(data, []) is None

    def test_alert_harmony(self):
        data = _make_tick_data(dopa_level=0.8, emotion="satisfaction")
        data["global_coherence"] = 0.8
        alert = detect_resonance(data, [])
        assert alert is not None
        assert alert.name == "resonance"
        assert alert.priority == "high"

    def test_no_alert_low_dopamine(self):
        data = _make_tick_data(dopa_level=0.3, emotion="satisfaction")
        data["global_coherence"] = 0.8
        assert detect_resonance(data, []) is None


class TestCodeletConvergence:
    """Tests pour detect_convergence — tendance haussiere coherence."""

    def test_no_alert_short_history(self):
        history = [{"coherence": 0.5}] * 3
        assert detect_convergence({}, history) is None

    def test_no_alert_declining(self):
        history = [{"coherence": 0.8 - i * 0.1} for i in range(5)]
        assert detect_convergence({}, history) is None

    def test_alert_rising(self):
        history = [{"coherence": 0.3 + i * 0.1} for i in range(5)]
        alert = detect_convergence({}, history)
        assert alert is not None
        assert alert.name == "convergence"
        assert "0.30" in alert.content
        assert "0.70" in alert.content

    def test_no_alert_flat(self):
        history = [{"coherence": 0.5}] * 5
        assert detect_convergence({}, history) is None  # Amplitude < 0.1

    def test_no_alert_small_rise(self):
        history = [{"coherence": 0.5 + i * 0.01} for i in range(5)]
        assert detect_convergence({}, history) is None  # Amplitude 0.04 < 0.1


class TestCodeletFatigue:
    """Tests pour detect_fatigue — fatigue cognitive multi-organes."""

    def test_no_alert_one_criterion(self):
        data = _make_tick_data()
        data["organ_states"]["cardiac"]["bpm"] = 55  # Seul BPM bas
        assert detect_fatigue(data, []) is None

    def test_alert_two_criteria(self):
        data = _make_tick_data(dopa_level=0.3)
        data["organ_states"]["cardiac"]["bpm"] = 55
        data["organ_states"]["dopamine"]["baseline"] = 0.7
        data["organ_states"]["dopamine"]["level"] = 0.3
        # Coherence en hausse = PAS de baisse = critere 1 non rempli
        history = [{"coherence": 0.5}, {"coherence": 0.6}, {"coherence": 0.7}]
        alert = detect_fatigue(data, history)
        assert alert is not None
        assert alert.name == "fatigue"
        assert alert.salience == 0.5  # 2/3 (BPM + dopamine)

    def test_alert_three_criteria(self):
        data = _make_tick_data(dopa_level=0.3)
        data["organ_states"]["cardiac"]["bpm"] = 55
        data["organ_states"]["dopamine"]["baseline"] = 0.7
        data["organ_states"]["dopamine"]["level"] = 0.3
        history = [{"coherence": 0.8}, {"coherence": 0.6}, {"coherence": 0.4}]
        alert = detect_fatigue(data, history)
        assert alert is not None
        assert alert.salience == 0.8  # 3/3

    def test_no_alert_healthy(self):
        data = _make_tick_data(dopa_level=0.8)
        data["organ_states"]["cardiac"]["bpm"] = 70
        data["organ_states"]["dopamine"]["baseline"] = 0.5
        history = [{"coherence": 0.5 + i * 0.05} for i in range(5)]
        assert detect_fatigue(data, history) is None


class TestCodeletFlow:
    """Tests pour detect_flow — etat de flow (Csikszentmihalyi)."""

    def test_no_alert_low_coherence(self):
        data = _make_tick_data(threat=0.5)
        data["global_coherence"] = 0.3
        assert detect_flow(data, []) is None

    def test_no_alert_high_threat(self):
        data = _make_tick_data(threat=5.0)
        data["global_coherence"] = 0.8
        assert detect_flow(data, []) is None

    def test_no_alert_frustrated(self):
        data = _make_tick_data(threat=0.5)
        data["global_coherence"] = 0.8
        data["organ_states"]["desire"]["frustrated_count"] = 2
        history = [{"phi": 0.1 + i * 0.05} for i in range(3)]
        assert detect_flow(data, history) is None

    def test_alert_all_conditions(self):
        data = _make_tick_data(threat=0.5)
        data["global_coherence"] = 0.8
        data["organ_states"]["desire"]["frustrated_count"] = 0
        history = [{"phi": 0.2}, {"phi": 0.3}, {"phi": 0.4}]
        alert = detect_flow(data, history)
        assert alert is not None
        assert alert.name == "flow"
        assert alert.category == "creation"

    def test_no_alert_phi_declining(self):
        data = _make_tick_data(threat=0.5)
        data["global_coherence"] = 0.8
        data["organ_states"]["desire"]["frustrated_count"] = 0
        history = [{"phi": 0.5}, {"phi": 0.3}, {"phi": 0.2}]
        assert detect_flow(data, history) is None


class TestCodeletCreativityBurst:
    """Tests pour detect_creativity_burst — burst creatif."""

    def test_no_alert_insufficient_conditions(self):
        data = _make_tick_data()
        data["dominant_mode"] = "repos"
        data["phi"] = 0.1
        assert detect_creativity_burst(data, []) is None

    def test_alert_three_conditions(self):
        data = _make_tick_data(dopa_level=0.8)
        data["dominant_mode"] = "creation"
        data["phi"] = 0.5
        data["organ_states"]["dopamine"]["baseline"] = 0.5
        data["organ_states"]["dopamine"]["level"] = 0.8
        history = [
            {"cognitive_state": "standard"},
            {"cognitive_state": "exploration"},
            {"cognitive_state": "standard"},
        ]
        alert = detect_creativity_burst(data, history)
        assert alert is not None
        assert alert.name == "creativity_burst"
        assert alert.category == "creation"

    def test_salience_formula_3_conditions(self):
        data = _make_tick_data(dopa_level=0.8)
        data["dominant_mode"] = "creation"
        data["phi"] = 0.2  # Sous 0.3 = condition phi NON remplie
        data["organ_states"]["dopamine"]["baseline"] = 0.5
        data["organ_states"]["dopamine"]["level"] = 0.8
        history = [
            {"cognitive_state": "standard"},
            {"cognitive_state": "exploration"},
            {"cognitive_state": "standard"},
        ]
        alert = detect_creativity_burst(data, history)
        # 3/4 : creation + transition + DA haute (phi False)
        # 0.5 + 0.2*0.3 + 0 = 0.56
        assert abs(alert.salience - 0.56) < 0.01

    def test_salience_formula_4_conditions(self):
        data = _make_tick_data(dopa_level=0.8)
        data["dominant_mode"] = "exploration"
        data["phi"] = 0.4
        data["organ_states"]["dopamine"]["baseline"] = 0.5
        data["organ_states"]["dopamine"]["level"] = 0.8
        history = [
            {"cognitive_state": "standard"},
            {"cognitive_state": "exploration"},
            {"cognitive_state": "creation"},
        ]
        alert = detect_creativity_burst(data, history)
        # 0.5 + 0.4*0.3 + 0.2 (4/4) = 0.82
        assert abs(alert.salience - 0.82) < 0.01

    def test_no_alert_short_history(self):
        data = _make_tick_data()
        data["dominant_mode"] = "creation"
        data["phi"] = 0.5
        assert detect_creativity_burst(data, [{"cognitive_state": "x"}]) is None
