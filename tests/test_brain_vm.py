"""
Tests pour core/brain_vm.py — Machine virtuelle cerebrale unifiee.

Couvre : singleton, tick cycle, snapshot organes, integration,
signaux descendants, publication BRAIN_TICK, persistance.
"""

import json
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.brain_vm import (
    BrainVM,
    BrainState,
    BRAIN_STATE_FILE,
    MAX_STATE_HISTORY,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def isolate_brain(tmp_path, monkeypatch):
    """Reset le singleton et isole le fichier d'etat."""
    import core.brain_vm as mod
    BrainVM.reset_singleton()
    monkeypatch.setattr(mod, "BRAIN_STATE_FILE", str(tmp_path / "brain.json"))
    with patch.object(BrainVM, "_load"):
        b = BrainVM()
        b.tick_count = 0
        b.state_history = []
        b.current_state = None
        b._alive = True
        mod.brain = b
    yield b
    BrainVM.reset_singleton()


# ============================================================
# Tests Singleton
# ============================================================

class TestSingleton:

    def test_singleton_identity(self, isolate_brain):
        b2 = BrainVM()
        assert b2 is isolate_brain

    def test_reset_singleton(self):
        BrainVM.reset_singleton()
        assert BrainVM._instance is None


# ============================================================
# Tests Tick Cycle
# ============================================================

class TestTickCycle:

    @pytest.mark.asyncio
    async def test_tick_increments_counter(self, isolate_brain):
        """Chaque CARDIAC_BEAT incremente le tick counter."""
        b = isolate_brain

        with patch("core.event_bus.bus.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            await b._on_cardiac_beat({"bpm": 65, "emotion": "serenite", "coherence": 0.6})

        assert b.tick_count == 1

    @pytest.mark.asyncio
    async def test_tick_creates_brain_state(self, isolate_brain):
        """Le tick cree un BrainState avec les organes."""
        b = isolate_brain

        with patch("core.event_bus.bus.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            await b._on_cardiac_beat({"bpm": 70, "emotion": "curiosite", "coherence": 0.7})

        assert b.current_state is not None
        assert b.current_state.tick_number == 1
        assert b.current_state.organ_states["cardiac"]["bpm"] == 70
        assert b.current_state.organ_states["cardiac"]["emotion"] == "curiosite"

    @pytest.mark.asyncio
    async def test_tick_publishes_brain_tick(self, isolate_brain):
        """Le tick publie BRAIN_TICK sur le bus."""
        b = isolate_brain
        published = []

        async def capture(event_type, data):
            published.append((event_type, data))

        with patch("core.event_bus.bus.bus") as mock_bus:
            mock_bus.publish = capture
            await b._on_cardiac_beat({"bpm": 65, "emotion": "serenite", "coherence": 0.5})

        assert len(published) == 1
        event_type, data = published[0]
        assert event_type == "BRAIN_TICK"
        assert "tick_number" in data
        assert "cognitive_state" in data
        assert "dominant_mode" in data

    @pytest.mark.asyncio
    async def test_tick_not_when_not_alive(self, isolate_brain):
        """Pas de tick si la VM n'est pas alive."""
        b = isolate_brain
        b._alive = False

        await b._on_cardiac_beat({"bpm": 65})

        assert b.tick_count == 0
        assert b.current_state is None

    @pytest.mark.asyncio
    async def test_multiple_ticks_history(self, isolate_brain):
        """Plusieurs ticks accumulent l'historique."""
        b = isolate_brain

        with patch("core.event_bus.bus.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            for i in range(5):
                await b._on_cardiac_beat({"bpm": 65 + i, "emotion": "serenite", "coherence": 0.5})

        assert b.tick_count == 5
        assert len(b.state_history) == 5

    @pytest.mark.asyncio
    async def test_history_capped(self, isolate_brain):
        """L'historique est cappe a MAX_STATE_HISTORY."""
        b = isolate_brain

        with patch("core.event_bus.bus.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            for i in range(MAX_STATE_HISTORY + 10):
                await b._on_cardiac_beat({"bpm": 65, "emotion": "serenite", "coherence": 0.5})

        assert len(b.state_history) <= MAX_STATE_HISTORY


# ============================================================
# Tests Snapshot Organes
# ============================================================

class TestSnapshot:

    def test_capture_cardiac_from_event(self, isolate_brain):
        """Le cardiac est capture directement depuis l'event CARDIAC_BEAT."""
        b = isolate_brain
        organs = b._capture_all_organs({"bpm": 72, "emotion": "flow", "coherence": 0.85})

        assert organs["cardiac"]["bpm"] == 72
        assert organs["cardiac"]["emotion"] == "flow"
        assert organs["cardiac"]["coherence"] == 0.85

    def test_capture_resilient_to_missing_organs(self, isolate_brain):
        """Les organes indisponibles retournent None."""
        b = isolate_brain

        with patch("builtins.__import__", side_effect=ImportError("test")):
            organs = b._capture_all_organs({"bpm": 65})

        # Cardiac est toujours present (vient de l'event)
        assert organs["cardiac"]["bpm"] == 65
        # Les autres sont None (import echoue)
        for key in ("desire", "reptilian", "prefrontal", "dopamine"):
            assert organs.get(key) is None


# ============================================================
# Tests Integration
# ============================================================

class TestIntegration:

    def test_integrate_uses_corpus_state(self, isolate_brain):
        """L'integration utilise l'etat du corpus callosum."""
        b = isolate_brain
        state = BrainState()
        state.organ_states = {
            "corpus": {"cognitive_state": "flow", "global_coherence": 0.9},
        }

        cog_state, coherence = b._integrate(state)
        assert cog_state == "flow"
        assert coherence > 0.5

    def test_integrate_default_without_corpus(self, isolate_brain):
        """Sans corpus, retourne standard/0.5."""
        b = isolate_brain
        state = BrainState()
        state.organ_states = {}

        cog_state, coherence = b._integrate(state)
        assert cog_state == "standard"

    def test_integrate_modulated_by_connectivity(self, isolate_brain):
        """La connectivity_matrix influence la coherence."""
        b = isolate_brain
        state = BrainState()
        state.organ_states = {
            "corpus": {"cognitive_state": "standard", "global_coherence": 0.5},
        }

        mock_matrix = MagicMock()
        mock_matrix.get_matrix_summary.return_value = {"avg_weight": 0.8}

        with patch.dict(sys.modules, {"core.connectivity_matrix": MagicMock(matrix=mock_matrix)}):
            _, coherence = b._integrate(state)

        # Coherence = 0.5 * 0.7 + 0.8 * 0.3 = 0.59
        assert coherence > 0.5


# ============================================================
# Tests Dominant Mode
# ============================================================

class TestDominantMode:

    @pytest.mark.asyncio
    async def test_dominant_mode_computed(self, isolate_brain):
        """Le mode dominant est le signal descendant le plus fort."""
        b = isolate_brain

        with patch.object(b, "_compute_signals", return_value={
            "urgence": 0.9, "exploration": 0.3, "repos": 0.1,
            "creation": 0.0, "consolidation": 0.0, "vigilance": 0.0, "social": 0.0,
        }), patch("core.event_bus.bus.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            await b._on_cardiac_beat({"bpm": 90, "emotion": "alerte", "coherence": 0.3})

        assert b.current_state.dominant_mode == "urgence"


# ============================================================
# Tests API
# ============================================================

class TestAPI:

    def test_get_status_no_state(self, isolate_brain):
        """Status sans etat courant."""
        b = isolate_brain
        status = b.get_status()
        assert status["tick_count"] == 0
        assert status["current_state"] is None

    @pytest.mark.asyncio
    async def test_get_status_after_tick(self, isolate_brain):
        """Status apres un tick."""
        b = isolate_brain

        with patch("core.event_bus.bus.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            await b._on_cardiac_beat({"bpm": 65, "emotion": "serenite", "coherence": 0.6})

        status = b.get_status()
        assert status["tick_count"] == 1
        assert status["current_state"] is not None
        assert "cognitive_state" in status["current_state"]
        assert "dominant_mode" in status["current_state"]


# ============================================================
# Tests Persistance
# ============================================================

class TestPersistence:

    def test_save_and_load(self, isolate_brain, tmp_path, monkeypatch):
        """La VM survit au save/load."""
        import core.brain_vm as mod
        path = str(tmp_path / "brain2.json")
        monkeypatch.setattr(mod, "BRAIN_STATE_FILE", path)

        isolate_brain.tick_count = 42
        isolate_brain.state_history = [{"tick": 42, "time": time.time()}]
        isolate_brain.save()

        assert os.path.exists(path)

        with open(path, "r") as f:
            data = json.load(f)
        assert data["tick_count"] == 42

    def test_start_stop(self, isolate_brain):
        """Start/stop gere le flag alive."""
        b = isolate_brain
        b.stop()
        assert not b._alive
        b.start()
        assert b._alive
