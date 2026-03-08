# tests/test_soma_psyche.py
"""Sprint 4 Sensorium — Boucles bidirectionnelles Soma-Psyche."""

import asyncio
import time
from unittest.mock import patch, MagicMock, AsyncMock

import pytest


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def isolate_singletons(tmp_path, monkeypatch):
    """Reset tous les singletons impliques."""
    # Psyche
    from core.psyche import PsycheEngine
    PsycheEngine.reset_singleton()
    monkeypatch.setattr("core.psyche.STATE_FILE", str(tmp_path / "psyche.json"))

    # Sensorium
    from core.sensorium import Sensorium
    Sensorium.reset_singleton()
    monkeypatch.setattr("core.sensorium.SENSORIUM_STATE_FILE", str(tmp_path / "sensorium.json"))

    yield

    PsycheEngine.reset_singleton()
    Sensorium.reset_singleton()


@pytest.fixture
def psyche_instance():
    from core.psyche import PsycheEngine
    p = PsycheEngine()
    return p


@pytest.fixture
def sensorium_instance():
    from core.sensorium import Sensorium
    s = Sensorium()
    s._gpu_backend = "heuristic"
    s._gpu_name = ""
    return s


# ============================================================
# Tests Psyche modulation par hardware
# ============================================================

class TestPsycheSomaticModulation:

    @pytest.mark.asyncio
    async def test_low_comfort_modulates_psyche(self, psyche_instance):
        """Comfort 0.2 → survie monte, audace baisse."""
        p = psyche_instance
        # Initialiser un agent pour avoir des traits
        p.agents["test"] = p._default_traits("test")
        old_survie = p.agents["test"]["survie"]
        old_audace = p.agents["test"]["audace"]

        event = {"comfort": 0.2, "senses": {}}
        await p._on_sensorium_update(event)

        assert p.agents["test"]["survie"] > old_survie
        assert p.agents["test"]["audace"] < old_audace

    @pytest.mark.asyncio
    async def test_high_comfort_no_modulation(self, psyche_instance):
        """Comfort 0.8 → aucun changement PSYCHE."""
        p = psyche_instance
        p.agents["test"] = p._default_traits("test")
        old_survie = p.agents["test"]["survie"]
        old_audace = p.agents["test"]["audace"]

        event = {"comfort": 0.8, "senses": {}}
        await p._on_sensorium_update(event)

        assert p.agents["test"]["survie"] == old_survie
        assert p.agents["test"]["audace"] == old_audace

    @pytest.mark.asyncio
    async def test_soma_psyche_throttle(self, psyche_instance):
        """2 events en 5s → seul le 1er declenche modulation."""
        p = psyche_instance
        p.agents["test"] = p._default_traits("test")

        event = {"comfort": 0.1, "senses": {}}
        await p._on_sensorium_update(event)
        survie_after_1 = p.agents["test"]["survie"]

        # 2eme appel immediat → throttled
        await p._on_sensorium_update(event)
        survie_after_2 = p.agents["test"]["survie"]

        assert survie_after_2 == survie_after_1  # Pas de changement


# ============================================================
# Tests Cardiac reaction aux events thermiques
# ============================================================

class TestCardiacSomaticReaction:

    @pytest.mark.asyncio
    async def test_thermal_critical_cardiac_reaction(self):
        """Event THERMAL_CRITICAL → cardiac.react('threat')."""
        from core.cardiac_engine import CardiacEngine
        CardiacEngine.reset_singleton()
        try:
            with patch.object(CardiacEngine, '_load'):
                heart = CardiacEngine()
            with patch.object(heart, 'react') as mock_react:
                await heart._on_sensorium_thermal_critical({"value": 0.95})
                mock_react.assert_called_once_with("threat")
        finally:
            CardiacEngine.reset_singleton()

    @pytest.mark.asyncio
    async def test_suffocation_cardiac_veto(self):
        """Event SUFFOCATION → cardiac.react('veto')."""
        from core.cardiac_engine import CardiacEngine
        CardiacEngine.reset_singleton()
        try:
            with patch.object(CardiacEngine, '_load'):
                heart = CardiacEngine()
            with patch.object(heart, 'react') as mock_react:
                await heart._on_sensorium_suffocation({"value": 0.9})
                mock_react.assert_called_once_with("veto")
        finally:
            CardiacEngine.reset_singleton()


# ============================================================
# Tests Insula body_state depuis Sensorium
# ============================================================

class TestInsulaSensorium:

    @pytest.mark.asyncio
    async def test_sensorium_updates_insula_fatigue(self):
        """Effort eleve → body_state fatigue monte."""
        from core.insula import Insula
        Insula.reset_singleton()
        try:
            with patch.object(Insula, '_load'):
                insula = Insula()
            initial_fatigue = insula.body_state["fatigue"]

            event = {"comfort": 0.3, "senses": {"effort": 0.9, "vitality": 0.5}}
            await insula._on_sensorium_update(event)

            assert insula.body_state["fatigue"] > initial_fatigue
            assert insula.body_state["stress"] > 0.3  # stress_hw = 0.7
        finally:
            Insula.reset_singleton()


# ============================================================
# Tests Desire STABILITE via hardware crisis
# ============================================================

class TestDesireHardwareCrisis:

    @pytest.mark.asyncio
    async def test_hardware_crisis_desire_stabilite(self):
        """SUFFOCATION → STABILITE deprivation monte."""
        from core.desire_engine import DesireEngine
        DesireEngine.reset_singleton()
        try:
            with patch.object(DesireEngine, '_load'):
                desires = DesireEngine()
            desires._subscribe_events()
            old_stabilite = desires.drives["STABILITE"].deprivation

            await desires._on_hardware_crisis({"value": 0.9})

            assert desires.drives["STABILITE"].deprivation > old_stabilite
        finally:
            DesireEngine.reset_singleton()


# ============================================================
# Tests boucle inverse Audace → Sensorium
# ============================================================

class TestAudaceModulation:

    def test_audace_modulates_sensitivity(self, sensorium_instance):
        """Audace 80 → bonus reduit de ~15%."""
        s = sensorium_instance
        mock_psyche = MagicMock()
        mock_psyche.get_system_average.return_value = {"audace": 80.0}

        with patch.dict("sys.modules", {"core.psyche": MagicMock(psyche=mock_psyche)}):
            mod = s._get_audace_modulation()

        # audace=80 → 1.0 - (80-50)/50 * 0.15 = 1.0 - 0.09 = 0.91
        assert mod < 1.0
        assert mod == pytest.approx(0.91, abs=0.01)

    def test_low_audace_increases_sensitivity(self, sensorium_instance):
        """Audace 20 → sensibilite accrue."""
        s = sensorium_instance
        mock_psyche = MagicMock()
        mock_psyche.get_system_average.return_value = {"audace": 20.0}

        with patch.dict("sys.modules", {"core.psyche": MagicMock(psyche=mock_psyche)}):
            mod = s._get_audace_modulation()

        # audace=20 → 1.0 - (20-50)/50 * 0.15 = 1.0 + 0.09 = 1.09
        assert mod > 1.0
        assert mod == pytest.approx(1.09, abs=0.01)

    def test_audace_default_without_psyche(self, sensorium_instance):
        """Sans module psyche → modulation neutre (1.0)."""
        s = sensorium_instance
        with patch.dict("sys.modules", {"core.psyche": None}):
            mod = s._get_audace_modulation()
        assert mod == 1.0
