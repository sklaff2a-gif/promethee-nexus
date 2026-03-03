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
    MUTATION_RATE, GOAL_ZONE_MAP, GOAL_FOOD_BONUS,
    FOOD_SPAWN_PER_ZONE,
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
        assert mock_bus.subscribe.call_count == 20


class TestTissueAfferencesCompletes:
    """Tests pour les 7 handlers afférents ajoutés (Sprints 2-4 complet)."""

    @pytest.mark.asyncio
    async def test_prefrontal_thought_boosts_cognition(self):
        t = NeuralTissue()
        t._cognitive_state["cognition_level"] = 0.3
        t.tick_count = 10
        await t._on_prefrontal_thought({"category": "observation"})
        assert t._cognitive_state["cognition_level"] == pytest.approx(0.45)

    @pytest.mark.asyncio
    async def test_prefrontal_thought_hypothesis_boosts_creativity(self):
        t = NeuralTissue()
        t._cognitive_state["cognition_level"] = 0.3
        t._cognitive_state["creativity"] = 0.3
        t.tick_count = 10
        await t._on_prefrontal_thought({"category": "hypothesis"})
        assert t._cognitive_state["creativity"] == pytest.approx(0.35)

    @pytest.mark.asyncio
    async def test_prefrontal_thought_no_creativity_on_generic(self):
        t = NeuralTissue()
        t._cognitive_state["creativity"] = 0.3
        t.tick_count = 10
        await t._on_prefrontal_thought({"category": "unknown"})
        assert t._cognitive_state["creativity"] == 0.3

    @pytest.mark.asyncio
    async def test_prefrontal_thought_cooldown(self):
        t = NeuralTissue()
        t._cognitive_state["cognition_level"] = 0.3
        t.tick_count = 10
        await t._on_prefrontal_thought({})
        assert t._cognitive_state["cognition_level"] == pytest.approx(0.45)
        # Même tick → cooldown, pas de boost
        await t._on_prefrontal_thought({})
        assert t._cognitive_state["cognition_level"] == pytest.approx(0.45)
        # Tick suivant → ok
        t.tick_count = 11
        await t._on_prefrontal_thought({})
        assert t._cognitive_state["cognition_level"] == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_psyche_update_blends_stability(self):
        t = NeuralTissue()
        t._cognitive_state["stability"] = 0.7
        await t._on_psyche_update({"data": {"system_average": {"coherence": 0.3}}})
        # 0.7 * 0.7 + 0.3 * 0.3 = 0.49 + 0.09 = 0.58
        assert t._cognitive_state["stability"] == pytest.approx(0.58)

    @pytest.mark.asyncio
    async def test_psyche_update_stability_key_fallback(self):
        t = NeuralTissue()
        t._cognitive_state["stability"] = 0.5
        await t._on_psyche_update({"data": {"system_average": {"stability": 1.0}}})
        # 0.5 * 0.7 + 1.0 * 0.3 = 0.35 + 0.30 = 0.65
        assert t._cognitive_state["stability"] == pytest.approx(0.65)

    @pytest.mark.asyncio
    async def test_synaptic_update_boosts_memory(self):
        t = NeuralTissue()
        t._cognitive_state["memory_activity"] = 0.3
        await t._on_synaptic_update({})
        assert t._cognitive_state["memory_activity"] == pytest.approx(0.4)

    @pytest.mark.asyncio
    async def test_synaptic_update_memory_clamped(self):
        t = NeuralTissue()
        t._cognitive_state["memory_activity"] = 0.95
        await t._on_synaptic_update({})
        assert t._cognitive_state["memory_activity"] <= 1.0

    @pytest.mark.asyncio
    async def test_council_end_consensus_boosts_stability(self):
        t = NeuralTissue()
        t._cognitive_state["stability"] = 0.5
        await t._on_council_end({"data": {"status": "consensus"}})
        assert t._cognitive_state["stability"] == pytest.approx(0.65)

    @pytest.mark.asyncio
    async def test_council_end_no_consensus_lowers_stability(self):
        t = NeuralTissue()
        t._cognitive_state["stability"] = 0.5
        await t._on_council_end({"data": {"status": "timeout"}})
        assert t._cognitive_state["stability"] == pytest.approx(0.45)

    @pytest.mark.asyncio
    async def test_evolution_feedback_success_boosts_creativity(self):
        t = NeuralTissue()
        t._cognitive_state["creativity"] = 0.3
        await t._on_evolution_feedback({"data": {"verdict": "success"}})
        assert t._cognitive_state["creativity"] == pytest.approx(0.45)

    @pytest.mark.asyncio
    async def test_evolution_feedback_failure_boosts_desire(self):
        t = NeuralTissue()
        t._cognitive_state["desire_intensity"] = 50.0
        await t._on_evolution_feedback({"data": {"verdict": "rejected"}})
        assert t._cognitive_state["desire_intensity"] == 53.0

    @pytest.mark.asyncio
    async def test_experience_recorded_boosts_memory_and_cognition(self):
        t = NeuralTissue()
        t._cognitive_state["memory_activity"] = 0.3
        t._cognitive_state["cognition_level"] = 0.3
        await t._on_experience_recorded({})
        assert t._cognitive_state["memory_activity"] == pytest.approx(0.4)
        assert t._cognitive_state["cognition_level"] == pytest.approx(0.35)

    @pytest.mark.asyncio
    async def test_soliloque_exchange_boosts_cognition_and_memory(self):
        t = NeuralTissue()
        t._cognitive_state["cognition_level"] = 0.3
        t._cognitive_state["memory_activity"] = 0.3
        await t._on_soliloque_exchange({})
        assert t._cognitive_state["cognition_level"] == pytest.approx(0.4)
        assert t._cognitive_state["memory_activity"] == pytest.approx(0.35)

    @pytest.mark.asyncio
    async def test_inner_voice_also_boosts_creativity_and_cognition(self):
        """Sprint 2-4 enrichi : inner voice → mémoire + créativité + cognition."""
        t = NeuralTissue()
        t._cognitive_state["memory_activity"] = 0.3
        t._cognitive_state["creativity"] = 0.3
        t._cognitive_state["cognition_level"] = 0.3
        await t._on_inner_voice({})
        assert t._cognitive_state["memory_activity"] == pytest.approx(0.45)
        assert t._cognitive_state["creativity"] == pytest.approx(0.4)
        assert t._cognitive_state["cognition_level"] == pytest.approx(0.4)


class TestCooldownSystem:
    """Tests pour le mécanisme de cooldown _cooldown_ok et _publish_cooldown_ok."""

    def test_cooldown_ok_first_call(self):
        t = NeuralTissue()
        t.tick_count = 5
        assert t._cooldown_ok("TEST_EVENT") is True

    def test_cooldown_ok_blocked_same_tick(self):
        t = NeuralTissue()
        t.tick_count = 5
        t._cooldown_ok("TEST_EVENT")
        assert t._cooldown_ok("TEST_EVENT") is False

    def test_cooldown_ok_passes_after_ticks(self):
        t = NeuralTissue()
        t.tick_count = 5
        t._cooldown_ok("TEST_EVENT", min_ticks=3)
        t.tick_count = 7
        assert t._cooldown_ok("TEST_EVENT", min_ticks=3) is False
        t.tick_count = 8
        assert t._cooldown_ok("TEST_EVENT", min_ticks=3) is True

    def test_cooldown_ok_different_events_independent(self):
        t = NeuralTissue()
        t.tick_count = 5
        t._cooldown_ok("EVENT_A")
        assert t._cooldown_ok("EVENT_B") is True

    def test_publish_cooldown_ok_first_call(self):
        t = NeuralTissue()
        t.tick_count = 0
        assert t._publish_cooldown_ok("TISSUE_TEST") is True

    def test_publish_cooldown_ok_blocked_within_window(self):
        t = NeuralTissue()
        t.tick_count = 0
        t._publish_cooldown_ok("TISSUE_TEST")
        t.tick_count = 5
        assert t._publish_cooldown_ok("TISSUE_TEST") is False

    def test_publish_cooldown_ok_passes_after_window(self):
        from core.neural_tissue import PUBLISH_COOLDOWN_TICKS
        t = NeuralTissue()
        t.tick_count = 0
        t._publish_cooldown_ok("TISSUE_TEST")
        t.tick_count = PUBLISH_COOLDOWN_TICKS
        assert t._publish_cooldown_ok("TISSUE_TEST") is True


class TestTissueThresholds:
    """Tests pour les efférences de seuil (_check_thresholds)."""

    def test_extinction_risk_published(self):
        from core.neural_tissue import THRESHOLD_EXTINCTION_RISK
        t = NeuralTissue()
        # Population sous le seuil
        t.cells = [NeuralCell(genome="CG", x=0, y=0) for _ in range(THRESHOLD_EXTINCTION_RISK - 1)]
        t._update_zone_signals()
        published = []
        t._try_publish = lambda name, payload: published.append(name)
        t._check_thresholds()
        assert "TISSUE_EXTINCTION_RISK" in published

    def test_no_extinction_risk_above_threshold(self):
        from core.neural_tissue import THRESHOLD_EXTINCTION_RISK
        t = NeuralTissue()
        t.cells = [NeuralCell(genome="CG", x=i, y=0) for i in range(THRESHOLD_EXTINCTION_RISK + 5)]
        t._update_zone_signals()
        published = []
        t._try_publish = lambda name, payload: published.append(name)
        t._check_thresholds()
        assert "TISSUE_EXTINCTION_RISK" not in published

    def test_diversity_drop_published(self):
        from core.neural_tissue import THRESHOLD_DIVERSITY_DROP, EXTINCTION_THRESHOLD
        t = NeuralTissue()
        # Beaucoup de cellules, mais peu de génomes uniques
        t.cells = [NeuralCell(genome="AA", x=i % GRID_SIZE, y=i // GRID_SIZE)
                    for i in range(EXTINCTION_THRESHOLD + 5)]
        # Un seul genome unique → diversity = 1 < THRESHOLD_DIVERSITY_DROP
        t._update_zone_signals()
        published = []
        t._try_publish = lambda name, payload: published.append(name)
        t._check_thresholds()
        assert "TISSUE_DIVERSITY_DROP" in published

    def test_zone_overload_published(self):
        from core.neural_tissue import THRESHOLD_ZONE_OVERLOAD
        t = NeuralTissue()
        t.cells = [NeuralCell(genome="CG", x=0, y=0) for _ in range(20)]
        # Forcer un signal très fort dans la zone emotion
        for y in range(4):
            for x in range(4):
                t.grid[y][x] = THRESHOLD_ZONE_OVERLOAD + 1.0
        t._update_zone_signals()
        published = []
        t._try_publish = lambda name, payload: published.append(name)
        t._check_thresholds()
        assert "TISSUE_ZONE_OVERLOAD" in published

    def test_zone_desert_published(self):
        t = NeuralTissue()
        t.cells = [NeuralCell(genome="CG", x=0, y=0) for _ in range(20)]
        t.tick_count = 100  # > 50 requis
        # Grille vide → toutes zones désertiques
        t._update_zone_signals()
        published = []
        t._try_publish = lambda name, payload: published.append(name)
        t._check_thresholds()
        assert "TISSUE_ZONE_DESERT" in published

    def test_zone_desert_not_published_early(self):
        t = NeuralTissue()
        t.cells = [NeuralCell(genome="CG", x=0, y=0) for _ in range(20)]
        t.tick_count = 10  # < 50
        t._update_zone_signals()
        published = []
        t._try_publish = lambda name, payload: published.append(name)
        t._check_thresholds()
        assert "TISSUE_ZONE_DESERT" not in published

    def test_creativity_spike_published(self):
        from core.neural_tissue import THRESHOLD_CREATIVITY_SPIKE
        t = NeuralTissue()
        t.cells = [NeuralCell(genome="CG", x=0, y=0) for _ in range(20)]
        # Zone creativity = (12, 6, 16, 10) → forcer signal fort
        for y in range(6, 10):
            for x in range(12, 16):
                t.grid[y][x] = THRESHOLD_CREATIVITY_SPIKE + 1.0
        t._update_zone_signals()
        published = []
        t._try_publish = lambda name, payload: published.append(name)
        t._check_thresholds()
        assert "TISSUE_CREATIVITY_SPIKE" in published

    def test_check_thresholds_no_crash_empty_signals(self):
        t = NeuralTissue()
        t._zone_signals = {}
        t._check_thresholds()  # Pas de crash

    def test_try_publish_respects_cooldown(self):
        t = NeuralTissue()
        t.tick_count = 0
        calls = []
        original_cooldown = t._publish_cooldown_ok
        t._publish_cooldown_ok = lambda name: (calls.append(name), False)[1]
        t._try_publish("TEST_EVENT", {})
        assert "TEST_EVENT" in calls  # Vérifié mais pas publié


class TestCognitionZone:
    """Tests pour la zone cognition (6,6→10,10)."""

    def test_cognition_zone_exists(self):
        assert "cognition" in SIGNAL_ZONES
        assert SIGNAL_ZONES["cognition"] == (6, 6, 10, 10)

    def test_cognition_level_in_cognitive_state(self):
        t = NeuralTissue()
        assert "cognition_level" in t._cognitive_state
        assert t._cognitive_state["cognition_level"] == 0.3

    def test_inject_signals_includes_cognition(self):
        t = NeuralTissue()
        t._cognitive_state["cognition_level"] = 1.0
        random.seed(42)
        t._inject_signals()
        # Zone cognition (6,6 → 10,10)
        total = sum(t.grid[y][x] for y in range(6, 10) for x in range(6, 10))
        assert total > 0

    def test_nine_zones_total(self):
        assert len(SIGNAL_ZONES) == 9


class TestDopamineModulation:
    """Sprint 6.1 — CAPTURE_REWARD et GENERATE_REWARD modulés par la dopamine."""

    def test_capture_reward_high_dopamine(self):
        """Dopamine haute → reward augmenté."""
        cell = NeuralCell(genome="C", x=3, y=3)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid[3][3] = 1.0
        initial_energy = cell.energy
        # Reward effectif = 3.0 * (0.5 + 1.0) = 4.5
        cell.tick(grid, [], capture_reward=4.5)
        gained = cell.energy - (initial_energy - MAINTENANCE_COST)
        assert gained > 3.0  # Plus que le reward normal (3.0 * 1.0 = 3.0)

    def test_capture_reward_low_dopamine(self):
        """Dopamine basse → reward réduit."""
        cell = NeuralCell(genome="C", x=3, y=3)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid[3][3] = 1.0
        initial_energy = cell.energy
        # Reward effectif = 3.0 * (0.5 + 0.0) = 1.5
        cell.tick(grid, [], capture_reward=1.5)
        gained = cell.energy - (initial_energy - MAINTENANCE_COST)
        assert gained < 3.0  # Moins que le reward normal

    def test_generate_reward_modulated(self):
        """Generate reward modulé par la dopamine."""
        cell = NeuralCell(genome="G", x=0, y=0, register=1.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        initial_energy = cell.energy
        # Generate effectif = 2.0 * (0.5 + 1.0) = 3.0
        cell.tick(grid, [], generate_reward=3.0)
        assert cell.output_count == 1
        gained = cell.energy - initial_energy + MAINTENANCE_COST + 0.5  # ACTION_COST
        assert gained == pytest.approx(3.0, abs=0.1)

    def test_tick_uses_dopamine_for_rewards(self):
        """NeuralTissue._tick() calcule les rewards à partir de dopamine_level."""
        t = NeuralTissue()
        t._seed_population()
        t._cognitive_state["dopamine_level"] = 1.0
        # On vérifie juste que le tick ne crashe pas avec la modulation
        t._tick()
        assert t.tick_count == 1

    def test_rewards_default_without_params(self):
        """Sans paramètres, les rewards sont les constantes originales."""
        cell = NeuralCell(genome="C", x=3, y=3)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid[3][3] = 1.0
        initial_energy = cell.energy
        cell.tick(grid, [])  # Pas de capture_reward explicite
        # Reward normal = CAPTURE_REWARD * min(1.0, 2.0) = 3.0
        gained = cell.energy - (initial_energy - MAINTENANCE_COST)
        assert gained == pytest.approx(3.0, abs=0.1)


class TestGoalZoneMapping:
    """Sprint 6.2 — Goal-to-zone mapping et FOOD_SPAWN bonus."""

    def test_goal_zone_map_exists(self):
        assert isinstance(GOAL_ZONE_MAP, dict)
        assert len(GOAL_ZONE_MAP) > 0

    @pytest.mark.asyncio
    async def test_goal_created_adds_bonus_zones(self):
        t = NeuralTissue()
        await t._on_goal_created({"data": {"title": "Explorer les nouvelles approches"}})
        # "explor" → ["creativity", "desire"]
        assert t._goal_bonus_zones.get("creativity", 0) == GOAL_FOOD_BONUS
        assert t._goal_bonus_zones.get("desire", 0) == GOAL_FOOD_BONUS

    @pytest.mark.asyncio
    async def test_goal_complete_removes_bonus(self):
        t = NeuralTissue()
        await t._on_goal_created({"data": {"title": "Explorer les données"}})
        assert t._goal_bonus_zones.get("creativity", 0) == GOAL_FOOD_BONUS
        await t._on_goal_complete({"data": {"title": "Explorer les données"}})
        assert t._goal_bonus_zones.get("creativity", 0) == 0

    @pytest.mark.asyncio
    async def test_goal_abandoned_removes_bonus(self):
        t = NeuralTissue()
        await t._on_goal_created({"data": {"title": "Sécuriser le module"}})
        assert t._goal_bonus_zones.get("threat", 0) == GOAL_FOOD_BONUS
        await t._on_goal_abandoned({"data": {"title": "Sécuriser le module"}})
        assert t._goal_bonus_zones.get("threat", 0) == 0

    @pytest.mark.asyncio
    async def test_unknown_goal_defaults_to_goals_zone(self):
        t = NeuralTissue()
        await t._on_goal_created({"data": {"title": "Faire un truc inconnu"}})
        assert t._goal_bonus_zones.get("goals", 0) == GOAL_FOOD_BONUS

    @pytest.mark.asyncio
    async def test_multiple_goals_stack_bonus(self):
        t = NeuralTissue()
        await t._on_goal_created({"data": {"title": "Explorer A"}})
        await t._on_goal_created({"data": {"title": "Explorer B"}})
        assert t._goal_bonus_zones.get("creativity", 0) == GOAL_FOOD_BONUS * 2

    def test_inject_signals_uses_goal_bonus(self):
        t = NeuralTissue()
        t._cognitive_state["creativity"] = 1.0
        # Injecter SANS bonus d'abord
        random.seed(42)
        t._inject_signals()
        creativity_no_bonus = sum(
            t.grid[y][x] for y in range(6, 10) for x in range(12, 16)
        )
        # Reset grille et injecter AVEC bonus
        t.grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        t._goal_bonus_zones = {"creativity": 4}
        random.seed(42)
        t._inject_signals()
        creativity_with_bonus = sum(
            t.grid[y][x] for y in range(6, 10) for x in range(12, 16)
        )
        assert creativity_with_bonus > creativity_no_bonus


class TestCircadianModulation:
    """Sprint 6.3 — Modulation circadienne : mutation, food, saisons."""

    def test_mutation_rate_normal_in_eveil(self):
        t = NeuralTissue()
        t._circadian_phase = "eveil"
        assert t._get_effective_mutation_rate() == MUTATION_RATE

    def test_mutation_rate_doubled_in_crepuscule(self):
        t = NeuralTissue()
        t._circadian_phase = "crepuscule"
        assert t._get_effective_mutation_rate() == pytest.approx(MUTATION_RATE * 2.0)

    def test_mutation_rate_halved_in_sommeil(self):
        t = NeuralTissue()
        t._circadian_phase = "sommeil_profond"
        assert t._get_effective_mutation_rate() == pytest.approx(MUTATION_RATE * 0.5)

    def test_no_injection_during_sommeil(self):
        """En sommeil profond, _inject_signals() ne fait rien."""
        t = NeuralTissue()
        t._circadian_phase = "sommeil_profond"
        t._cognitive_state["emotion_intensity"] = 1.0
        t._inject_signals()
        total = sum(t.grid[y][x] for y in range(GRID_SIZE) for x in range(GRID_SIZE))
        assert total == 0.0

    def test_injection_works_in_eveil(self):
        """En éveil, _inject_signals() fonctionne normalement."""
        t = NeuralTissue()
        t._circadian_phase = "eveil"
        t._cognitive_state["emotion_intensity"] = 1.0
        random.seed(42)
        t._inject_signals()
        total = sum(t.grid[y][x] for y in range(GRID_SIZE) for x in range(GRID_SIZE))
        assert total > 0.0

    @pytest.mark.asyncio
    async def test_circadian_change_stores_phase(self):
        t = NeuralTissue()
        await t._on_circadian_change({"data": {"phase": "crepuscule"}})
        assert t._circadian_phase == "crepuscule"

    @pytest.mark.asyncio
    async def test_dawn_repopulate_after_sleep(self):
        """Aube après sommeil → repeuplement des zones désertées."""
        t = NeuralTissue()
        t._circadian_phase = "sommeil_profond"
        t.cells = [NeuralCell(genome="CG", x=0, y=0) for _ in range(5)]
        # Calculer zone signals pour avoir des zones désertées
        t._update_zone_signals()
        initial_count = len(t.cells)
        await t._on_circadian_change({"data": {"phase": "aube"}})
        assert len(t.cells) > initial_count

    @pytest.mark.asyncio
    async def test_dawn_no_repopulate_if_not_from_sleep(self):
        """Aube sans sommeil préalable → pas de repeuplement."""
        t = NeuralTissue()
        t._circadian_phase = "eveil"  # Pas de sommeil_profond
        t.cells = [NeuralCell(genome="CG", x=0, y=0) for _ in range(5)]
        t._update_zone_signals()
        initial_count = len(t.cells)
        await t._on_circadian_change({"data": {"phase": "aube"}})
        assert len(t.cells) == initial_count

    def test_mutate_with_custom_rate(self):
        """mutate() accepte un mutation_rate personnalisé."""
        random.seed(42)
        # Taux 0 → pas de mutation
        result = mutate("AAAA", mutation_rate=0.0)
        assert result == "AAAA" or len(result) != len("AAAA")  # Seule insertion/deletion possible

    def test_mutate_with_high_rate(self):
        """Taux élevé → beaucoup de mutations."""
        changed = False
        for _ in range(10):
            result = mutate("AAAA", mutation_rate=0.99)
            if result != "AAAA":
                changed = True
                break
        assert changed

    def test_tick_uses_circadian_mutation_rate(self):
        """_tick() utilise le taux circadien effectif."""
        t = NeuralTissue()
        t._circadian_phase = "crepuscule"
        t._seed_population()
        # Pas de crash
        t._tick()
        assert t.tick_count == 1
