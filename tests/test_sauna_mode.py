"""Tests pour le mode Sauna — nettoyage du tissu neural."""

import pytest
import time
import sys
from unittest.mock import MagicMock, AsyncMock, patch


# Mock partage entre les tests pour preserver l API existante :
# chaque test fait `mock_tissue_module.tissue = ...` au debut.
mock_tissue_module = MagicMock()


@pytest.fixture(autouse=True)
def _isolate_neural_tissue_mock(monkeypatch):
    """Injecte le mock de core.neural_tissue scope-fonction (2026-05-07).

    Ancien pattern (avant 2026-05-07) : `sys.modules["core.neural_tissue"]
    = mock_tissue_module` au niveau MODULE polluait toute la session
    pytest. Tous les tests en aval qui importaient depuis core.neural_tissue
    recevaient le mock -> echec en cascade (TestClassifyGenomeProfile,
    TerritoryMap, etc., ~25 echecs).

    monkeypatch.setitem restaure automatiquement l etat sys.modules
    apres chaque test. Meme correctif structurel que V30.15 (26/04)
    sur core.event_bus.
    """
    monkeypatch.setitem(sys.modules, "core.neural_tissue", mock_tissue_module)


class FakeCell:
    def __init__(self, energy=80, waste=50):
        self.energy = energy
        self.waste = waste
        self.alive = True
        self.x = 0
        self.y = 0


def make_tissue(n_cells=10, avg_energy=80, avg_waste=50):
    """Crée un tissue simulé."""
    tissue = MagicMock()
    tissue.cells = [FakeCell(energy=avg_energy, waste=avg_waste) for _ in range(n_cells)]
    tissue.waste_grid = [[avg_waste * 0.1] * 5 for _ in range(5)]
    tissue.toxic_grid = [[0.0] * 5 for _ in range(5)]
    tissue._cognitive_state = {
        "memory_activity": 0.5,
        "creativity": 0.5,
        "cognition_level": 0.5,
        "thermal_stress": 0.3,
        "somatic_load": 0.3,
        "vitality_level": 0.5,
        "stability": 0.5,
    }
    tissue.tick_count = 100
    return tissue


class TestSaunaMode:

    def setup_method(self):
        # Re-import fresh pour chaque test
        from core.sauna_mode import SaunaMode
        self.sauna = SaunaMode()

    def test_initial_state(self):
        assert self.sauna.active is False
        assert self.sauna.started_at == 0.0
        assert self.sauna.stats == {}

    def test_get_status_no_tissue(self):
        mock_tissue_module.tissue = None
        status = self.sauna.get_status()
        assert status["active"] is False
        assert status["should_trigger"] is False

    def test_should_auto_trigger_high_waste(self):
        tissue = make_tissue(n_cells=10, avg_waste=25)  # 25 * 10 = 250 > 200
        mock_tissue_module.tissue = tissue
        assert self.sauna.should_auto_trigger() is True

    def test_should_auto_trigger_low_energy(self):
        tissue = make_tissue(n_cells=10, avg_energy=60, avg_waste=5)  # avg 60 < 80
        mock_tissue_module.tissue = tissue
        assert self.sauna.should_auto_trigger() is True

    def test_should_not_trigger_healthy(self):
        tissue = make_tissue(n_cells=10, avg_energy=100, avg_waste=10)  # 100 > 80, 100 < 200
        mock_tissue_module.tissue = tissue
        assert self.sauna.should_auto_trigger() is False

    @pytest.mark.asyncio
    async def test_start_cleans_waste(self):
        tissue = make_tissue(n_cells=5, avg_energy=90, avg_waste=100)
        mock_tissue_module.tissue = tissue

        result = await self.sauna.start()

        assert result["status"] == "completed"
        assert result["waste_cleaned"] >= 0
        # Les dechets doivent avoir diminué (waste * 0.3)
        for cell in tissue.cells:
            assert cell.waste <= 100 * 0.35  # ~30% restant avec marge

    @pytest.mark.asyncio
    async def test_start_rebalances_signals(self):
        tissue = make_tissue(n_cells=5)
        tissue._cognitive_state["memory_activity"] = 0.95  # Saturé
        tissue._cognitive_state["thermal_stress"] = 0.8     # Elevé
        mock_tissue_module.tissue = tissue

        await self.sauna.start()

        cs = tissue._cognitive_state
        assert cs["memory_activity"] < 0.95  # Ramené vers equilibre
        assert cs["thermal_stress"] < 0.8     # Réduit

    @pytest.mark.asyncio
    async def test_start_boosts_vitality(self):
        tissue = make_tissue(n_cells=5)
        tissue._cognitive_state["vitality_level"] = 0.5
        tissue._cognitive_state["stability"] = 0.5
        mock_tissue_module.tissue = tissue

        await self.sauna.start()

        assert tissue._cognitive_state["vitality_level"] == 0.7  # +0.2
        assert tissue._cognitive_state["stability"] == 0.6       # +0.1

    @pytest.mark.asyncio
    async def test_already_active(self):
        self.sauna.active = True
        self.sauna.started_at = time.time()

        result = await self.sauna.start()
        assert result["status"] == "already_active"
        assert "remaining_s" in result

    @pytest.mark.asyncio
    async def test_weak_cells_accelerated(self):
        tissue = make_tissue(n_cells=5, avg_energy=20)  # Toutes faibles
        mock_tissue_module.tissue = tissue

        await self.sauna.start()

        # Les cellules faibles (< 30) perdent 5 d'énergie
        for cell in tissue.cells:
            assert cell.energy <= 20  # 20 - 5 = 15

    @pytest.mark.asyncio
    async def test_capture_state(self):
        tissue = make_tissue(n_cells=3, avg_energy=75, avg_waste=30)
        mock_tissue_module.tissue = tissue

        state = self.sauna._capture_state()
        assert state["waste"] == 90  # 30 * 3
        assert state["avg_energy"] == 75
        assert state["alive_cells"] == 3
        assert "vitality" in state

    @pytest.mark.asyncio
    async def test_result_structure(self):
        tissue = make_tissue(n_cells=5, avg_energy=80, avg_waste=60)
        mock_tissue_module.tissue = tissue

        result = await self.sauna.start()

        assert "status" in result
        assert "duration_s" in result
        assert "before" in result
        assert "after" in result
        assert "waste_cleaned" in result
        assert "energy_change" in result

    @pytest.mark.asyncio
    async def test_not_active_after_completion(self):
        tissue = make_tissue(n_cells=5)
        mock_tissue_module.tissue = tissue

        await self.sauna.start()
        assert self.sauna.active is False
