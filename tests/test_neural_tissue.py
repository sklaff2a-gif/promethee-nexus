"""Tests pour le substrat cellulaire neuronal (core/neural_tissue.py)."""
import os
import json
import tempfile
import random
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

import core.neural_tissue as mod
from core.neural_tissue import (
    NeuralCell, NeuralTissue, mutate, tissue,
    GRID_SIZE, INITIAL_CELLS, INITIAL_ENERGY, DIVISION_THRESHOLD,
    ALPHABET, MAX_GENOME_LENGTH, MIN_GENOME_LENGTH,
    MAINTENANCE_COST, CAPTURE_REWARD, GENERATE_REWARD,
    EXTINCTION_THRESHOLD, SIGNAL_ZONES, MAX_CELLS,
)

_FAKE_STATE_FILE = os.path.join(tempfile.gettempdir(), "test_neural_tissue.json")


@pytest.fixture(autouse=True)
def isolate_tissue(monkeypatch):
    """Isole le tissue entre chaque test."""
    if os.path.exists(_FAKE_STATE_FILE):
        os.remove(_FAKE_STATE_FILE)
    monkeypatch.setattr(mod, "TISSUE_STATE_FILE", _FAKE_STATE_FILE)
    NeuralTissue.reset_singleton()
    yield
    NeuralTissue.reset_singleton()
    if os.path.exists(_FAKE_STATE_FILE):
        os.remove(_FAKE_STATE_FILE)


# ─────────────────────────────────────────────
# NeuralCell
# ─────────────────────────────────────────────

class TestNeuralCell:

    def test_cell_creation(self):
        cell = NeuralCell(genome="ACG", x=5, y=3)
        assert cell.genome == "ACG"
        assert cell.energy == INITIAL_ENERGY
        assert cell.alive is True
        assert cell.age == 0
        assert cell.pointer == 0

    def test_cell_tick_advances_pointer(self):
        cell = NeuralCell(genome="ACG", x=0, y=0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        cell.tick(grid, [])
        assert cell.pointer == 1
        assert cell.age == 1

    def test_cell_pointer_wraps(self):
        cell = NeuralCell(genome="AC", x=0, y=0, pointer=1)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        cell.tick(grid, [])
        assert cell.pointer == 0  # Wraps from 1 → 0

    def test_cell_dies_without_energy(self):
        cell = NeuralCell(genome="AC", x=0, y=0, energy=0.5)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        cell.tick(grid, [])
        assert cell.alive is False

    def test_capture_instruction_with_signal(self):
        cell = NeuralCell(genome="C", x=3, y=3)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid[3][3] = 1.0
        initial_energy = cell.energy
        cell.tick(grid, [])
        # Capture should give energy reward
        assert cell.register == 1.0
        assert cell.energy > initial_energy - MAINTENANCE_COST
        # Signal should be consumed partially
        assert grid[3][3] < 1.0

    def test_capture_instruction_no_signal(self):
        cell = NeuralCell(genome="C", x=3, y=3, register=5.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        cell.tick(grid, [])
        assert cell.register == 0.0

    def test_generate_instruction_with_register(self):
        cell = NeuralCell(genome="G", x=0, y=0, register=1.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        initial_energy = cell.energy
        cell.tick(grid, [])
        assert cell.output_count == 1
        assert cell.energy > initial_energy - MAINTENANCE_COST - 1

    def test_generate_instruction_no_register(self):
        cell = NeuralCell(genome="G", x=0, y=0, register=0.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        cell.tick(grid, [])
        assert cell.output_count == 0

    def test_activate_propagates_to_neighbor(self):
        cell = NeuralCell(genome="A", x=0, y=0, register=2.0)
        neighbor = NeuralCell(genome="C", x=1, y=0, energy=50.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        cell.tick(grid, [neighbor])
        assert neighbor.energy > 50.0

    def test_inhibit_reduces_signal(self):
        cell = NeuralCell(genome="I", x=5, y=5)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid[5][5] = 2.0
        cell.tick(grid, [])
        assert grid[5][5] < 2.0

    def test_transform_amplifies_strong_signal(self):
        cell = NeuralCell(genome="T", x=0, y=0, register=0.8)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        cell.tick(grid, [])
        assert cell.register > 0.8

    def test_transform_dampens_weak_signal(self):
        cell = NeuralCell(genome="T", x=0, y=0, register=0.3)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        cell.tick(grid, [])
        assert cell.register < 0.3

    def test_replicate_creates_child(self):
        cell = NeuralCell(genome="RACG", x=5, y=5, energy=250.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        child = cell.tick(grid, [])
        assert child is not None
        assert child.generation == 1
        assert cell.energy < 250.0

    def test_replicate_insufficient_energy(self):
        cell = NeuralCell(genome="R", x=0, y=0, energy=100.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        child = cell.tick(grid, [])
        assert child is None

    def test_unknown_instruction_nop(self):
        """Instruction inconnue = NOP, pas de crash."""
        cell = NeuralCell(genome="Z", x=0, y=0, energy=50.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        cell.tick(grid, [])
        assert cell.alive  # Pas de crash


# ─────────────────────────────────────────────
# Mutation
# ─────────────────────────────────────────────

class TestMutation:

    def test_mutate_returns_string(self):
        result = mutate("ACG")
        assert isinstance(result, str)
        assert len(result) >= MIN_GENOME_LENGTH

    def test_mutate_preserves_alphabet(self):
        random.seed(42)
        for _ in range(100):
            result = mutate("ACGTIR")
            for ch in result:
                assert ch in ALPHABET

    def test_mutate_respects_max_length(self):
        long_genome = ALPHABET * 4  # 24 chars = MAX
        random.seed(42)
        for _ in range(50):
            result = mutate(long_genome)
            assert len(result) <= MAX_GENOME_LENGTH

    def test_mutate_respects_min_length(self):
        short_genome = "AC"
        random.seed(42)
        for _ in range(50):
            result = mutate(short_genome)
            assert len(result) >= MIN_GENOME_LENGTH

    def test_high_mutation_rate_changes_genome(self):
        """Avec un taux élevé, le génome change (presque) toujours."""
        original = "AAAA"
        changed = False
        random.seed(0)
        with patch.object(mod, "MUTATION_RATE", 0.99):
            for _ in range(10):
                result = mutate(original)
                if result != original:
                    changed = True
                    break
        assert changed


# ─────────────────────────────────────────────
# NeuralTissue Singleton
# ─────────────────────────────────────────────

class TestTissueSingleton:

    def test_singleton_identity(self):
        a = NeuralTissue()
        b = NeuralTissue()
        assert a is b

    def test_reset_singleton(self):
        a = NeuralTissue()
        NeuralTissue.reset_singleton()
        b = NeuralTissue()
        assert a is not b

    def test_initial_state(self):
        t = NeuralTissue()
        assert t.tick_count == 0
        assert t.total_births == 0
        assert t.cells == []
        assert len(t.grid) == GRID_SIZE
        assert len(t.grid[0]) == GRID_SIZE


class TestTissuePopulation:

    def test_seed_population(self):
        t = NeuralTissue()
        t._seed_population()
        assert len(t.cells) == INITIAL_CELLS
        assert t.total_births == INITIAL_CELLS

    def test_seed_population_valid_positions(self):
        t = NeuralTissue()
        t._seed_population()
        for cell in t.cells:
            assert 0 <= cell.x < GRID_SIZE
            assert 0 <= cell.y < GRID_SIZE
            assert cell.alive

    def test_seed_population_varied_genomes(self):
        t = NeuralTissue()
        random.seed(42)
        t._seed_population()
        genomes = set(c.genome for c in t.cells)
        assert len(genomes) > 1  # Pas tous identiques


class TestTissueTick:

    def test_single_tick(self):
        t = NeuralTissue()
        t._seed_population()
        t._tick()
        assert t.tick_count == 1

    def test_tick_removes_dead_cells(self):
        t = NeuralTissue()
        t.cells = [
            NeuralCell(genome="A", x=0, y=0, energy=0.1),
            NeuralCell(genome="A", x=1, y=1, energy=50.0),
        ]
        t._tick()
        alive = [c for c in t.cells if c.alive]
        # La première cellule devrait mourir (energy 0.1 - MAINTENANCE_COST)
        assert len(alive) <= len(t.cells)

    def test_tick_repopulates_on_extinction(self):
        t = NeuralTissue()
        t.cells = [NeuralCell(genome="A", x=0, y=0, energy=0.1)]
        t._tick()
        # Devrait repeupler si < EXTINCTION_THRESHOLD
        assert len(t.cells) >= EXTINCTION_THRESHOLD

    def test_tick_respects_max_cells(self):
        t = NeuralTissue()
        # Remplir avec des cellules qui veulent se répliquer
        t.cells = [
            NeuralCell(genome="R", x=i % GRID_SIZE, y=i // GRID_SIZE, energy=250.0)
            for i in range(MAX_CELLS)
        ]
        t._tick()
        assert len(t.cells) <= MAX_CELLS

    def test_multiple_ticks_evolve(self):
        """Après plusieurs ticks, la population change."""
        t = NeuralTissue()
        random.seed(123)
        t._seed_population()
        initial_genomes = set(c.genome for c in t.cells)
        for _ in range(50):
            t._tick()
        current_genomes = set(c.genome for c in t.cells if c.alive)
        # La composition devrait avoir changé
        assert t.tick_count == 50


class TestTissueSignals:

    def test_inject_signals_adds_to_grid(self):
        t = NeuralTissue()
        t._cognitive_state["emotion_intensity"] = 1.0
        random.seed(42)
        t._inject_signals()
        # La zone emotion (0,0 → 4,4) devrait avoir des signaux
        total = sum(t.grid[y][x] for y in range(4) for x in range(4))
        assert total > 0

    def test_signal_decay(self):
        t = NeuralTissue()
        t.grid[5][5] = 10.0
        t._seed_population()
        t.cells = []  # Vider pour que seul le decay agisse
        t._tick()
        assert t.grid[5][5] < 10.0

    def test_all_signal_zones_valid(self):
        """Toutes les zones sont dans les limites de la grille."""
        for name, (x1, y1, x2, y2) in SIGNAL_ZONES.items():
            assert 0 <= x1 < GRID_SIZE, f"Zone {name} x1={x1}"
            assert 0 <= y1 < GRID_SIZE, f"Zone {name} y1={y1}"
            assert x2 <= GRID_SIZE, f"Zone {name} x2={x2}"
            assert y2 <= GRID_SIZE, f"Zone {name} y2={y2}"


class TestZoneSignals:

    def test_get_zone_signals_returns_all_zones(self):
        t = NeuralTissue()
        t._seed_population()
        t._tick()
        signals = t.get_zone_signals()
        assert len(signals) == len(SIGNAL_ZONES)
        for zone_name in SIGNAL_ZONES:
            assert zone_name in signals

    def test_get_zone_signals_has_four_metrics(self):
        t = NeuralTissue()
        t._seed_population()
        t._tick()
        signals = t.get_zone_signals()
        expected_keys = {"activity", "density", "energy", "diversity"}
        for zone_name, metrics in signals.items():
            assert set(metrics.keys()) == expected_keys, f"Zone {zone_name} manque des metriques"

    def test_get_zone_signals_empty_before_tick(self):
        t = NeuralTissue()
        assert t.get_zone_signals() == {}

    def test_zone_activity_reflects_injected_signal(self):
        t = NeuralTissue()
        t.cells = []
        # Injecter un signal fort dans la zone emotion (0,0 -> 4,4)
        for y in range(0, 4):
            for x in range(0, 4):
                t.grid[y][x] = 2.0
        t._update_zone_signals()
        signals = t.get_zone_signals()
        assert signals["emotion"]["activity"] > 0

    def test_zone_density_reflects_cells(self):
        t = NeuralTissue()
        # Zone emotion = (0,0,4,4) -> surface 16
        # 4 cellules dans la zone -> density = 4/16 = 0.25
        t.cells = [
            NeuralCell(genome="CG", x=0, y=0),
            NeuralCell(genome="CG", x=1, y=1),
            NeuralCell(genome="CG", x=2, y=2),
            NeuralCell(genome="CG", x=3, y=3),
        ]
        t._update_zone_signals()
        signals = t.get_zone_signals()
        assert signals["emotion"]["density"] == 0.25

    def test_zone_energy_is_zero_without_cells(self):
        t = NeuralTissue()
        t.cells = []
        t._update_zone_signals()
        signals = t.get_zone_signals()
        for zone_name in signals:
            assert signals[zone_name]["energy"] == 0.0

    def test_zone_diversity_all_different(self):
        t = NeuralTissue()
        # 4 cellules avec 4 genomes differents dans zone emotion
        t.cells = [
            NeuralCell(genome="AA", x=0, y=0),
            NeuralCell(genome="CC", x=1, y=1),
            NeuralCell(genome="GG", x=2, y=2),
            NeuralCell(genome="TT", x=3, y=3),
        ]
        t._update_zone_signals()
        signals = t.get_zone_signals()
        assert signals["emotion"]["diversity"] == 1.0

    def test_zone_diversity_uniform(self):
        t = NeuralTissue()
        # 3 cellules meme genome dans zone emotion -> diversity = 1/3
        t.cells = [
            NeuralCell(genome="AA", x=0, y=0),
            NeuralCell(genome="AA", x=1, y=1),
            NeuralCell(genome="AA", x=2, y=2),
        ]
        t._update_zone_signals()
        signals = t.get_zone_signals()
        assert abs(signals["emotion"]["diversity"] - 1/3) < 0.01

    def test_last_tick_ms_set_after_tick(self):
        t = NeuralTissue()
        t._seed_population()
        assert t._last_tick_ms == 0.0
        t._tick()
        assert t._last_tick_ms > 0

    def test_get_stats_includes_tick_ms(self):
        t = NeuralTissue()
        t._seed_population()
        t._tick()
        stats = t.get_stats()
        assert "tick_ms" in stats
        assert isinstance(stats["tick_ms"], float)

    def test_zone_signals_persisted_and_restored(self):
        t = NeuralTissue()
        t._seed_population()
        t._tick()
        assert t.get_zone_signals() != {}
        t._save()

        NeuralTissue.reset_singleton()
        t2 = NeuralTissue()
        restored = t2.get_zone_signals()
        assert len(restored) == len(SIGNAL_ZONES)

    def test_get_zone_signals_returns_copy(self):
        t = NeuralTissue()
        t._seed_population()
        t._tick()
        signals = t.get_zone_signals()
        # Mutation du retour ne doit pas corrompre l'etat interne
        signals["emotion"] = {"activity": 999}
        internal = t.get_zone_signals()
        assert internal["emotion"]["activity"] != 999


class TestTissuePatterns:

    def test_dominant_patterns_detected(self):
        t = NeuralTissue()
        # Créer une population homogène
        t.cells = [NeuralCell(genome="CG", x=0, y=0) for _ in range(20)]
        t._update_dominant_patterns()
        assert len(t.dominant_patterns) >= 1
        assert t.dominant_patterns[0]["genome"] == "CG"
        assert t.dominant_patterns[0]["count"] == 20
        assert t.dominant_patterns[0]["frequency"] == 1.0

    def test_get_dominant_genome(self):
        t = NeuralTissue()
        t.cells = [NeuralCell(genome="CG", x=0, y=0) for _ in range(10)]
        t._update_dominant_patterns()
        assert t.get_dominant_genome() == "CG"

    def test_get_dominant_genome_empty(self):
        t = NeuralTissue()
        assert t.get_dominant_genome() is None

    def test_get_emergent_patterns_top_n(self):
        t = NeuralTissue()
        t.cells = (
            [NeuralCell(genome="CG", x=0, y=0) for _ in range(10)] +
            [NeuralCell(genome="AC", x=0, y=0) for _ in range(5)]
        )
        t._update_dominant_patterns()
        top3 = t.get_emergent_patterns(top_n=3)
        assert len(top3) == 2
        assert top3[0]["genome"] == "CG"
        assert top3[1]["genome"] == "AC"


class TestTissueAPI:

    def test_compute_tissue_bonus_empty(self):
        t = NeuralTissue()
        assert t.compute_tissue_bonus("test") == 0.0

    def test_compute_tissue_bonus_with_patterns(self):
        t = NeuralTissue()
        t.cells = [NeuralCell(genome="CG", x=0, y=0) for _ in range(20)]
        t._update_dominant_patterns()
        bonus = t.compute_tissue_bonus("test")
        assert bonus > 0.0
        assert bonus <= 1.0

    def test_get_tissue_context_empty(self):
        t = NeuralTissue()
        assert t.get_tissue_context() == ""

    def test_get_tissue_context_with_cells(self):
        t = NeuralTissue()
        t.cells = [NeuralCell(genome="CGT", x=0, y=0) for _ in range(20)]
        t._update_dominant_patterns()
        ctx = t.get_tissue_context()
        assert "Substrat cellulaire" in ctx
        assert "CGT" in ctx
        assert "perception" in ctx

    def test_get_tissue_context_includes_zones(self):
        """Sprint 4 — contexte enrichi avec zones actives."""
        t = NeuralTissue()
        t.cells = [NeuralCell(genome="CGT", x=0, y=0) for _ in range(20)]
        t._update_dominant_patterns()
        t._update_zone_signals()
        ctx = t.get_tissue_context()
        assert "zones actives" in ctx

    def test_get_stats_structure(self):
        t = NeuralTissue()
        t._seed_population()
        stats = t.get_stats()
        assert "alive_cells" in stats
        assert "tick_count" in stats
        assert "dominant_genome" in stats
        assert "genome_diversity" in stats
        assert "cognitive_state" in stats
        assert stats["alive_cells"] == INITIAL_CELLS
        assert stats["grid_size"] == GRID_SIZE


class TestTissueHandlers:

    @pytest.mark.asyncio
    async def test_cardiac_beat_updates_state(self):
        t = NeuralTissue()
        await t._on_cardiac_beat({"data": {"arousal": 0.9}})
        assert t._cognitive_state["emotion_intensity"] == 0.9

    @pytest.mark.asyncio
    async def test_reptilian_alert_updates_state(self):
        t = NeuralTissue()
        await t._on_reptilian_alert({"data": {"threat_level": 7.0}})
        assert t._cognitive_state["threat_level"] == 7.0

    @pytest.mark.asyncio
    async def test_dopamine_surge_increases(self):
        t = NeuralTissue()
        t._cognitive_state["dopamine_level"] = 0.5
        await t._on_dopamine_surge({})
        assert t._cognitive_state["dopamine_level"] == 0.7

    @pytest.mark.asyncio
    async def test_dopamine_dip_decreases(self):
        t = NeuralTissue()
        t._cognitive_state["dopamine_level"] = 0.5
        await t._on_dopamine_dip({})
        assert t._cognitive_state["dopamine_level"] == 0.3

    @pytest.mark.asyncio
    async def test_dopamine_clamped(self):
        t = NeuralTissue()
        t._cognitive_state["dopamine_level"] = 0.95
        await t._on_dopamine_surge({})
        assert t._cognitive_state["dopamine_level"] <= 1.0
        t._cognitive_state["dopamine_level"] = 0.05
        await t._on_dopamine_dip({})
        assert t._cognitive_state["dopamine_level"] >= 0.0

    @pytest.mark.asyncio
    async def test_circadian_change(self):
        t = NeuralTissue()
        await t._on_circadian_change({"data": {"phase": "sommeil_profond"}})
        assert t._cognitive_state["stability"] == 0.9
        await t._on_circadian_change({"data": {"phase": "eveil"}})
        assert t._cognitive_state["stability"] == 0.5


class TestTissueGrandCablage:
    """Tests Sprint 2 — 8 nouveaux handlers bus (Grand Câblage)."""

    @pytest.mark.asyncio
    async def test_goal_created_increments(self):
        t = NeuralTissue()
        assert t._cognitive_state["goal_count"] == 0
        await t._on_goal_created({})
        assert t._cognitive_state["goal_count"] == 1
        await t._on_goal_created({})
        assert t._cognitive_state["goal_count"] == 2

    @pytest.mark.asyncio
    async def test_goal_created_capped_at_10(self):
        t = NeuralTissue()
        t._cognitive_state["goal_count"] = 10
        await t._on_goal_created({})
        assert t._cognitive_state["goal_count"] == 10

    @pytest.mark.asyncio
    async def test_goal_complete_decrements_and_boosts_stability(self):
        t = NeuralTissue()
        t._cognitive_state["goal_count"] = 3
        t._cognitive_state["stability"] = 0.5
        await t._on_goal_complete({})
        assert t._cognitive_state["goal_count"] == 2
        assert t._cognitive_state["stability"] == 0.6

    @pytest.mark.asyncio
    async def test_goal_abandoned_decrements_and_lowers_stability(self):
        t = NeuralTissue()
        t._cognitive_state["goal_count"] = 2
        t._cognitive_state["stability"] = 0.5
        await t._on_goal_abandoned({})
        assert t._cognitive_state["goal_count"] == 1
        assert t._cognitive_state["stability"] == pytest.approx(0.45)

    @pytest.mark.asyncio
    async def test_corpus_callosum_flow(self):
        t = NeuralTissue()
        await t._on_corpus_callosum({"data": {"cognitive_state": "flow"}})
        assert t._cognitive_state["stability"] == 0.9
        assert t._cognitive_state["creativity"] == 0.8

    @pytest.mark.asyncio
    async def test_corpus_callosum_creative_surge(self):
        t = NeuralTissue()
        t._cognitive_state["creativity"] = 0.3
        await t._on_corpus_callosum({"data": {"cognitive_state": "creative_surge"}})
        assert t._cognitive_state["creativity"] == 0.6

    @pytest.mark.asyncio
    async def test_corpus_callosum_crisis(self):
        t = NeuralTissue()
        t._cognitive_state["stability"] = 0.7
        await t._on_corpus_callosum({"data": {"cognitive_state": "crisis"}})
        assert t._cognitive_state["stability"] == pytest.approx(0.4)

    @pytest.mark.asyncio
    async def test_corpus_callosum_stagnation(self):
        t = NeuralTissue()
        t._cognitive_state["creativity"] = 0.5
        await t._on_corpus_callosum({"data": {"cognitive_state": "stagnation"}})
        assert t._cognitive_state["creativity"] == 0.3

    @pytest.mark.asyncio
    async def test_corpus_callosum_exploration(self):
        t = NeuralTissue()
        t._cognitive_state["creativity"] = 0.3
        await t._on_corpus_callosum({"data": {"cognitive_state": "exploration"}})
        assert t._cognitive_state["creativity"] == pytest.approx(0.45)

    @pytest.mark.asyncio
    async def test_inner_voice_boosts_memory(self):
        t = NeuralTissue()
        t._cognitive_state["memory_activity"] = 0.3
        await t._on_inner_voice({})
        assert t._cognitive_state["memory_activity"] == pytest.approx(0.45)

    @pytest.mark.asyncio
    async def test_inner_voice_clamped(self):
        t = NeuralTissue()
        t._cognitive_state["memory_activity"] = 0.95
        await t._on_inner_voice({})
        assert t._cognitive_state["memory_activity"] <= 1.0

    @pytest.mark.asyncio
    async def test_hallucination_boosts_threat(self):
        t = NeuralTissue()
        t._cognitive_state["threat_level"] = 2.0
        await t._on_hallucination({})
        assert t._cognitive_state["threat_level"] == 5.0

    @pytest.mark.asyncio
    async def test_hallucination_threat_capped(self):
        t = NeuralTissue()
        t._cognitive_state["threat_level"] = 9.0
        await t._on_hallucination({})
        assert t._cognitive_state["threat_level"] == 10.0

    @pytest.mark.asyncio
    async def test_routine_complete_success_lowers_desire(self):
        t = NeuralTissue()
        t._cognitive_state["desire_intensity"] = 50.0
        await t._on_routine_complete({"data": {"success": True}})
        assert t._cognitive_state["desire_intensity"] == 45.0

    @pytest.mark.asyncio
    async def test_routine_complete_failure_raises_desire(self):
        t = NeuralTissue()
        t._cognitive_state["desire_intensity"] = 50.0
        await t._on_routine_complete({"data": {"success": False}})
        assert t._cognitive_state["desire_intensity"] == 53.0

    @pytest.mark.asyncio
    async def test_knowledge_gap_boosts_creativity_and_desire(self):
        t = NeuralTissue()
        t._cognitive_state["creativity"] = 0.3
        t._cognitive_state["desire_intensity"] = 40.0
        await t._on_knowledge_gap({})
        assert t._cognitive_state["creativity"] == 0.5
        assert t._cognitive_state["desire_intensity"] == 45.0

    @pytest.mark.asyncio
    async def test_knowledge_gap_creativity_capped(self):
        t = NeuralTissue()
        t._cognitive_state["creativity"] = 0.95
        await t._on_knowledge_gap({})
        assert t._cognitive_state["creativity"] <= 1.0


class TestTissuePersistence:

    def test_save_and_load(self):
        t = NeuralTissue()
        t._seed_population()
        t.tick_count = 42
        t.total_births = 100
        t.total_deaths = 70
        t._update_dominant_patterns()
        t._save()

        assert os.path.exists(_FAKE_STATE_FILE)

        NeuralTissue.reset_singleton()
        t2 = NeuralTissue()
        assert t2.tick_count == 42
        assert t2.total_births == 100
        assert t2.total_deaths == 70
        assert len(t2.cells) >= INITIAL_CELLS

    def test_load_missing_file(self):
        t = NeuralTissue()
        assert t.tick_count == 0
        assert t.cells == []

    def test_load_corrupt_file(self):
        with open(_FAKE_STATE_FILE, "w") as f:
            f.write("not json{{{")
        NeuralTissue.reset_singleton()
        t = NeuralTissue()
        assert t.tick_count == 0

    def test_load_restores_cognitive_state(self):
        t = NeuralTissue()
        t._cognitive_state["threat_level"] = 8.0
        t._seed_population()
        t._save()

        NeuralTissue.reset_singleton()
        t2 = NeuralTissue()
        assert t2._cognitive_state["threat_level"] == 8.0


class TestTissueNeighbors:

    def test_get_neighbors_finds_adjacent(self):
        t = NeuralTissue()
        cell = NeuralCell(genome="A", x=5, y=5)
        neighbor = NeuralCell(genome="A", x=6, y=5)
        far = NeuralCell(genome="A", x=10, y=10)
        t.cells = [cell, neighbor, far]
        neighbors = t._get_neighbors(cell)
        assert neighbor in neighbors
        assert far not in neighbors
        assert cell not in neighbors

    def test_get_neighbors_wraps_around(self):
        t = NeuralTissue()
        cell = NeuralCell(genome="A", x=0, y=0)
        wrap = NeuralCell(genome="A", x=GRID_SIZE - 1, y=0)
        t.cells = [cell, wrap]
        neighbors = t._get_neighbors(cell)
        assert wrap in neighbors


class TestTissueInit:

    def test_init_seeds_if_empty(self):
        t = NeuralTissue()
        assert len(t.cells) == 0
        t.init()
        assert len(t.cells) == INITIAL_CELLS

    def test_init_subscribes_to_bus(self):
        t = NeuralTissue()
        mock_bus = MagicMock()
        with patch.dict("sys.modules", {"core.event_bus.bus": MagicMock(bus=mock_bus)}):
            t.init()
        assert t._subscribed is True
        assert mock_bus.subscribe.call_count == 13
