# tests/test_neural_tissue.py
"""Tests Sprint 0 — Stabilisation du tissu neural."""

import pytest
import random
from unittest.mock import patch

from core.neural_tissue import (
    NeuralCell, NeuralTissue, GRID_SIZE, INITIAL_CELLS, INITIAL_ENERGY,
    DIVISION_THRESHOLD, MAINTENANCE_COST, MAINTENANCE_COST_BASAL,
    BASAL_ENERGY_THRESHOLD, SIGNAL_ZONES, FOOD_SPAWN_PER_ZONE,
)


@pytest.fixture(autouse=True)
def reset_tissue():
    """Reset le singleton NeuralTissue entre chaque test."""
    NeuralTissue.reset_singleton()
    yield
    NeuralTissue.reset_singleton()


def _make_tissue():
    """Cree un NeuralTissue sans bus ni fichier."""
    with patch.object(NeuralTissue, '_load'):
        tissue = NeuralTissue()
    tissue._seed_population()
    return tissue


class TestBasalMetabolism:
    """Metabolisme basal — cellule <50 energie paie 0.3, >=50 paie 1.0."""

    def test_high_energy_pays_full_cost(self):
        cell = NeuralCell(genome="C", x=0, y=0, energy=60.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        cell.tick(grid, [])
        # C sans signal : register=0, pas d'energie gagnee, pas d'ACTION_COST
        # 60 >= 50 -> MAINTENANCE_COST = 1.0 -> 60 - 1.0 = 59.0
        assert cell.energy == pytest.approx(59.0, abs=0.01)

    def test_low_energy_pays_basal_cost(self):
        cell = NeuralCell(genome="C", x=0, y=0, energy=40.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        cell.tick(grid, [])
        # 40 < 50 -> MAINTENANCE_COST_BASAL = 0.3 -> 40 - 0.3 = 39.7
        assert cell.energy == pytest.approx(39.7, abs=0.01)

    def test_threshold_boundary(self):
        """Exactement 50.0 d'energie = plein cout (>=50)."""
        cell = NeuralCell(genome="C", x=0, y=0, energy=50.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        cell.tick(grid, [])
        # 50 >= 50 -> MAINTENANCE_COST = 1.0 -> 50 - 1.0 = 49.0
        assert cell.energy == pytest.approx(49.0, abs=0.01)

    def test_basal_extends_survival(self):
        """Une cellule en mode basal survit bien plus de 100 ticks."""
        cell = NeuralCell(genome="C", x=0, y=0, energy=49.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        ticks = 0
        while cell.alive:
            cell.tick(grid, [])
            ticks += 1
        # 49 / 0.3 = ~163 ticks
        assert ticks > 100, f"Cellule morte apres seulement {ticks} ticks"


class TestInitialPopulation:
    """Population initiale = 50 cellules."""

    def test_initial_population_count(self):
        tissue = _make_tissue()
        assert len(tissue.cells) == 50

    def test_initial_cells_constant(self):
        assert INITIAL_CELLS == 50


class TestSommeilSignals:
    """Sommeil profond injecte des signaux reduits au lieu de zero."""

    def test_sommeil_still_injects_signals(self):
        tissue = _make_tissue()
        tissue.grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        tissue._circadian_phase = "sommeil_profond"
        random.seed(42)
        tissue._inject_signals()

        total_signal = sum(
            tissue.grid[y][x]
            for y in range(GRID_SIZE)
            for x in range(GRID_SIZE)
        )
        assert total_signal > 0, "La grille devrait avoir des signaux en sommeil"

    def test_sommeil_less_signals_than_eveil(self):
        """Le sommeil injecte moins de signaux que l'eveil."""
        random.seed(42)
        tissue_eveil = _make_tissue()
        tissue_eveil.grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        tissue_eveil._circadian_phase = "eveil"
        tissue_eveil._inject_signals()
        signal_eveil = sum(
            tissue_eveil.grid[y][x]
            for y in range(GRID_SIZE)
            for x in range(GRID_SIZE)
        )

        random.seed(42)
        NeuralTissue.reset_singleton()
        tissue_sommeil = _make_tissue()
        tissue_sommeil.grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        tissue_sommeil._circadian_phase = "sommeil_profond"
        tissue_sommeil._inject_signals()
        signal_sommeil = sum(
            tissue_sommeil.grid[y][x]
            for y in range(GRID_SIZE)
            for x in range(GRID_SIZE)
        )

        assert signal_sommeil < signal_eveil


class TestDivisionThreshold:
    """Division accessible a DIVISION_THRESHOLD=130."""

    def test_division_constant(self):
        assert DIVISION_THRESHOLD == 130.0

    def test_division_at_131(self):
        """Cellule avec 131 d'energie et instruction R peut diviser."""
        cell = NeuralCell(genome="R", x=8, y=8, energy=131.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        child = cell.tick(grid, [])
        assert child is not None, "Devrait diviser a 131"
        assert child.alive

    def test_no_division_at_129(self):
        """Cellule avec 129 d'energie ne peut pas diviser."""
        cell = NeuralCell(genome="R", x=8, y=8, energy=129.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        child = cell.tick(grid, [])
        assert child is None, "Ne devrait pas diviser a 129"


class TestPopulationStability:
    """Stabilite de la population sur la duree."""

    def test_population_stability_100_ticks(self):
        """La population reste >30 apres 100 ticks avec des signaux."""
        random.seed(42)
        tissue = _make_tissue()
        tissue._circadian_phase = "eveil"

        for _ in range(100):
            tissue._tick()

        alive_count = sum(1 for c in tissue.cells if c.alive)
        assert alive_count > 30, f"Population tombee a {alive_count}"

    def test_no_extinction_during_sommeil(self):
        """La population reste >5 apres 50 ticks en sommeil profond."""
        random.seed(42)
        tissue = _make_tissue()
        tissue._circadian_phase = "sommeil_profond"

        for _ in range(50):
            tissue._tick()

        alive_count = sum(1 for c in tissue.cells if c.alive)
        assert alive_count > 5, f"Population tombee a {alive_count} en sommeil"
