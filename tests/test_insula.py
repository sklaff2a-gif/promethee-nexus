# tests/test_insula.py — Tests Insula (conscience interoceptive)
import pytest
import json
import os
import time
from unittest.mock import patch, AsyncMock
from collections import deque


@pytest.fixture(autouse=True)
def isolate_insula(tmp_path, monkeypatch):
    """Isole le singleton Insula pour chaque test."""
    from core import insula as mod
    mod.Insula.reset_singleton()
    monkeypatch.setattr(mod, "INSULA_STATE_FILE", str(tmp_path / "insula_state.json"))
    with patch.object(mod.Insula, "_load"):
        ins = mod.Insula()
    ins.body_state = {
        "energy": 0.6, "stress": 0.3, "arousal": 0.4,
        "valence": 0.5, "confidence": 0.6, "fatigue": 0.3,
    }
    ins.somatic_markers = {}
    ins.awareness_history = deque(maxlen=40)
    ins.interoception_accuracy = 0.5
    ins.total_readings = 0
    ins._cycle_count = 0
    ins._subscribed = False
    mod.insula = ins
    yield ins
    mod.Insula.reset_singleton()


# ===== TestSingleton =====

class TestSingleton:
    def test_singleton_identity(self, isolate_insula):
        from core.insula import Insula
        i2 = Insula()
        assert i2 is isolate_insula


# ===== TestBodyState =====

class TestBodyState:
    def test_initial_dimensions(self, isolate_insula):
        ins = isolate_insula
        from core.insula import BODY_STATE_DIMENSIONS
        for dim in BODY_STATE_DIMENSIONS:
            assert dim in ins.body_state

    def test_values_in_range(self, isolate_insula):
        ins = isolate_insula
        for v in ins.body_state.values():
            assert 0.0 <= v <= 1.0

    def test_coherence_perfect(self, isolate_insula):
        ins = isolate_insula
        # Toutes les dimensions identiques → coherence maximale
        for d in ins.body_state:
            ins.body_state[d] = 0.5
        coh = ins._compute_coherence()
        assert coh == 1.0

    def test_coherence_low_variance(self, isolate_insula):
        ins = isolate_insula
        ins.body_state = {
            "energy": 0.0, "stress": 1.0, "arousal": 0.0,
            "valence": 1.0, "confidence": 0.0, "fatigue": 1.0,
        }
        coh = ins._compute_coherence()
        assert coh < 0.5

    def test_coherence_bounded(self, isolate_insula):
        ins = isolate_insula
        coh = ins._compute_coherence()
        assert 0.0 <= coh <= 1.0

    def test_stress_clamp(self, isolate_insula):
        ins = isolate_insula
        ins.body_state["stress"] = 5.0  # Out of range
        # Compute coherence ne devrait pas crasher
        coh = ins._compute_coherence()
        assert isinstance(coh, float)

    def test_gut_feeling_positive(self, isolate_insula):
        ins = isolate_insula
        ins.somatic_markers["GOOD"] = 0.8
        ins.body_state["valence"] = 0.8
        ins.body_state["energy"] = 0.7
        ins.body_state["stress"] = 0.1
        gut = ins._compute_gut_feeling("GOOD")
        assert gut > 0

    def test_gut_feeling_negative(self, isolate_insula):
        ins = isolate_insula
        ins.somatic_markers["BAD"] = -0.7
        ins.body_state["valence"] = 0.2
        ins.body_state["stress"] = 0.9
        gut = ins._compute_gut_feeling("BAD")
        assert gut < 0


# ===== TestSomaticMarkers =====

class TestSomaticMarkers:
    def test_marker_created(self, isolate_insula):
        ins = isolate_insula
        ins._update_somatic_marker("NEW", 0.8)
        assert "NEW" in ins.somatic_markers

    def test_marker_learns_positive(self, isolate_insula):
        ins = isolate_insula
        ins.body_state["valence"] = 0.8
        ins._update_somatic_marker("X", 0.9)
        assert ins.somatic_markers["X"] > 0

    def test_marker_learns_negative(self, isolate_insula):
        ins = isolate_insula
        ins.body_state["valence"] = 0.1
        ins._update_somatic_marker("X", -0.8)
        assert ins.somatic_markers["X"] < 0

    def test_marker_bounded(self, isolate_insula):
        ins = isolate_insula
        for _ in range(100):
            ins._update_somatic_marker("X", 1.0)
        assert ins.somatic_markers["X"] <= 1.0

    def test_marker_decay(self, isolate_insula):
        ins = isolate_insula
        ins.somatic_markers["X"] = 0.5
        ins.decay_somatic_markers()
        assert ins.somatic_markers["X"] < 0.5

    def test_marker_decay_removes_tiny(self, isolate_insula):
        ins = isolate_insula
        ins.somatic_markers["TINY"] = 0.005
        ins.decay_somatic_markers()
        assert "TINY" not in ins.somatic_markers

    def test_multiple_markers_independent(self, isolate_insula):
        ins = isolate_insula
        ins._update_somatic_marker("A", 0.9)
        ins._update_somatic_marker("B", -0.5)
        assert ins.somatic_markers["A"] > ins.somatic_markers["B"]

    def test_marker_gradual_learning(self, isolate_insula):
        ins = isolate_insula
        ins._update_somatic_marker("X", 0.5)
        m1 = ins.somatic_markers["X"]
        ins._update_somatic_marker("X", 0.5)
        m2 = ins.somatic_markers["X"]
        # Devrait converger progressivement
        assert abs(m2) >= abs(m1) or abs(m2 - m1) < 0.2

    def test_marker_reinforced_by_body(self, isolate_insula):
        ins = isolate_insula
        ins.body_state["valence"] = 0.9  # Etat corporel positif
        ins._update_somatic_marker("X", 0.5)
        marker_with_positive_body = ins.somatic_markers["X"]

        ins2 = isolate_insula
        ins2.body_state["valence"] = 0.1  # Etat corporel negatif
        ins2._update_somatic_marker("X", 0.5)
        marker_with_negative_body = ins2.somatic_markers["X"]

        assert marker_with_positive_body > marker_with_negative_body

    def test_no_marker_zero_gut(self, isolate_insula):
        ins = isolate_insula
        # Sans marqueur et etat equilibre
        for d in ins.body_state:
            ins.body_state[d] = 0.5
        gut = ins._compute_gut_feeling("UNKNOWN")
        assert abs(gut) < 0.2


# ===== TestGutFeeling =====

class TestGutFeeling:
    def test_gut_bounded(self, isolate_insula):
        ins = isolate_insula
        ins.somatic_markers["X"] = 1.0
        ins.body_state["valence"] = 1.0
        gut = ins._compute_gut_feeling("X")
        assert -1.0 <= gut <= 1.0

    def test_gut_zero_unknown(self, isolate_insula):
        ins = isolate_insula
        for d in ins.body_state:
            ins.body_state[d] = 0.5
        gut = ins._compute_gut_feeling("UNKNOWN")
        assert abs(gut) < 0.3

    def test_gut_influenced_by_stress(self, isolate_insula):
        ins = isolate_insula
        ins.somatic_markers["X"] = 0.0
        ins.body_state["stress"] = 0.1
        gut_low_stress = ins._compute_gut_feeling("X")
        ins.body_state["stress"] = 0.9
        gut_high_stress = ins._compute_gut_feeling("X")
        assert gut_high_stress <= gut_low_stress

    def test_gut_influenced_by_energy(self, isolate_insula):
        ins = isolate_insula
        ins.somatic_markers["X"] = 0.0
        ins.body_state["energy"] = 0.9
        gut_high_energy = ins._compute_gut_feeling("X")
        ins.body_state["energy"] = 0.1
        gut_low_energy = ins._compute_gut_feeling("X")
        assert gut_high_energy >= gut_low_energy

    def test_coherence_amplifies(self, isolate_insula):
        ins = isolate_insula
        ins.somatic_markers["X"] = 0.5
        # Haute coherence
        for d in ins.body_state:
            ins.body_state[d] = 0.6
        gut_coherent = ins._compute_gut_feeling("X")
        # Basse coherence
        ins.body_state["stress"] = 0.0
        ins.body_state["arousal"] = 1.0
        gut_incoherent = ins._compute_gut_feeling("X")
        # Le gut feeling devrait etre plus fort avec haute coherence
        assert abs(gut_coherent) >= abs(gut_incoherent) - 0.1


# ===== TestScoringBonus =====

class TestScoringBonus:
    def test_zero_below_threshold(self, isolate_insula):
        ins = isolate_insula
        # Gut feeling tres faible → 0
        for d in ins.body_state:
            ins.body_state[d] = 0.5
        bonus = ins.compute_interoception_bonus("NEUTRAL")
        assert bonus == 0.0

    def test_positive_bonus(self, isolate_insula):
        ins = isolate_insula
        ins.somatic_markers["GOOD"] = 0.9
        ins.body_state["valence"] = 0.8
        ins.body_state["confidence"] = 0.9
        ins.body_state["energy"] = 0.8
        ins.body_state["stress"] = 0.1
        bonus = ins.compute_interoception_bonus("GOOD")
        assert bonus > 0

    def test_negative_bonus(self, isolate_insula):
        ins = isolate_insula
        ins.somatic_markers["BAD"] = -0.8
        ins.body_state["valence"] = 0.1
        ins.body_state["stress"] = 0.9
        ins.body_state["confidence"] = 0.3
        bonus = ins.compute_interoception_bonus("BAD")
        assert bonus < 0

    def test_range_upper(self, isolate_insula):
        ins = isolate_insula
        ins.somatic_markers["X"] = 1.0
        ins.body_state["valence"] = 1.0
        ins.body_state["confidence"] = 1.0
        ins.body_state["energy"] = 1.0
        ins.body_state["stress"] = 0.0
        bonus = ins.compute_interoception_bonus("X")
        assert bonus <= 1.0

    def test_range_lower(self, isolate_insula):
        ins = isolate_insula
        ins.somatic_markers["X"] = -1.0
        ins.body_state["valence"] = 0.0
        ins.body_state["stress"] = 1.0
        bonus = ins.compute_interoception_bonus("X")
        assert bonus >= -0.5

    def test_unknown_intent(self, isolate_insula):
        ins = isolate_insula
        # Etat equilibre, pas de marqueur
        for d in ins.body_state:
            ins.body_state[d] = 0.5
        bonus = ins.compute_interoception_bonus("NEVER_SEEN")
        assert bonus == 0.0


# ===== TestBusHandlers =====

class TestBusHandlers:
    @pytest.mark.asyncio
    async def test_cardiac_beat_arousal(self, isolate_insula):
        ins = isolate_insula
        await ins._on_cardiac_beat({"bpm": 100.0})
        assert ins.body_state["arousal"] > 0.5

    @pytest.mark.asyncio
    async def test_hypothalamus_updates(self, isolate_insula):
        ins = isolate_insula
        await ins._on_hypothalamus_regulation({
            "errors": {"energy": -0.5, "stress": 0.6},
            "stability_score": 0.4,
        })
        assert ins.body_state["confidence"] == 0.4

    @pytest.mark.asyncio
    async def test_reptilian_spikes_stress(self, isolate_insula):
        ins = isolate_insula
        old_stress = ins.body_state["stress"]
        await ins._on_reptilian_alert({"severity": 0.8})
        assert ins.body_state["stress"] > old_stress

    @pytest.mark.asyncio
    async def test_dopamine_updates_valence(self, isolate_insula):
        ins = isolate_insula
        await ins._on_dopamine_update({"level": 0.9})
        assert ins.body_state["valence"] == 0.9

    @pytest.mark.asyncio
    async def test_circadian_updates_fatigue(self, isolate_insula):
        ins = isolate_insula
        await ins._on_circadian_phase({"phase": "sommeil_profond"})
        assert ins.body_state["fatigue"] == 0.9

    @pytest.mark.asyncio
    async def test_routine_complete_updates_marker(self, isolate_insula):
        ins = isolate_insula
        await ins._on_routine_complete({
            "intent": "TEST",
            "status": "success",
            "quality_score": 0.9,
        })
        assert "TEST" in ins.somatic_markers


# ===== TestContext =====

class TestContext:
    def test_context_equilibre(self, isolate_insula):
        ins = isolate_insula
        for d in ins.body_state:
            ins.body_state[d] = 0.5
        ctx = ins.get_body_awareness_context()
        assert "equilibre" in ctx.lower()

    def test_context_high_stress(self, isolate_insula):
        ins = isolate_insula
        ins.body_state["stress"] = 0.9
        ctx = ins.get_body_awareness_context()
        assert "haut" in ctx.lower()

    def test_context_low_energy(self, isolate_insula):
        ins = isolate_insula
        ins.body_state["energy"] = 0.1
        ctx = ins.get_body_awareness_context()
        assert "bas" in ctx.lower()


# ===== TestStats =====

class TestStats:
    def test_stats_keys(self, isolate_insula):
        ins = isolate_insula
        stats = ins.get_stats()
        assert "body_state" in stats
        assert "coherence_score" in stats
        assert "somatic_markers_count" in stats
        assert "total_readings" in stats


# ===== TestPersistence =====

class TestPersistence:
    def test_save_creates_file(self, isolate_insula):
        ins = isolate_insula
        ins._save()
        from core.insula import INSULA_STATE_FILE
        assert os.path.exists(INSULA_STATE_FILE)

    def test_roundtrip(self, isolate_insula, tmp_path, monkeypatch):
        from core import insula as mod
        ins = isolate_insula
        ins.body_state["stress"] = 0.8
        ins.somatic_markers["X"] = 0.6
        ins.total_readings = 42
        ins._save()

        mod.Insula.reset_singleton()
        i2 = mod.Insula()
        assert i2.body_state["stress"] == 0.8
        assert i2.somatic_markers.get("X") == 0.6
        assert i2.total_readings == 42

    def test_load_missing(self, isolate_insula):
        ins = isolate_insula
        ins._load()
