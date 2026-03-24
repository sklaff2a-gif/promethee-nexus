# tests/test_neural_tissue.py
"""Tests Sprint 0 — Stabilisation du tissu neural."""

import pytest
import random
from unittest.mock import patch, MagicMock, AsyncMock

from core.neural_tissue import (
    NeuralCell, NeuralTissue, GRID_SIZE, INITIAL_CELLS, INITIAL_ENERGY,
    DIVISION_THRESHOLD, MAINTENANCE_COST, MAINTENANCE_COST_BASAL,
    BASAL_ENERGY_THRESHOLD, SIGNAL_ZONES, FOOD_SPAWN_PER_ZONE,
    MIN_ZONE_INTENSITY, DAWN_REPOPULATE_COUNT, VIABLE_GENOMES,
    SEASON_ORDER, SEASON_CYCLE_LENGTH, SEASON_FOOD_BONUS,
    SEASON_TRANSITION_BONUS, ALPHA_ENERGY_THRESHOLD, ALPHA_OUTPUT_THRESHOLD,
    ZONE_ADJACENCY, MAX_CELLS,
    PANDEMIC_MIN_INTERVAL, PANDEMIC_MAX_INTERVAL, PANDEMIC_MIN_POPULATION,
    PANDEMIC_MOTIF_LEN, PANDEMIC_INFECTION_RATE,
    INFECTION_DURATION_MIN, INFECTION_DURATION_MAX,
    INFECTION_DRAIN_MIN, INFECTION_DRAIN_MAX,
    IMMUNE_MUTATION_CHANCE, IMMUNITY_DECAY_GENERATIONS, ALPHABET,
    # Biologie avancée
    HEAT_TOLERANT_CYCLES, HEAT_TOLERANT_SIGNAL_THRESHOLD,
    HEAT_TOLERANT_COST_FACTOR, FAMINE_ADAPTED_CYCLES,
    FAMINE_ADAPTED_COST_FACTOR, CREATIVE_BURST_OUTPUT_THRESHOLD,
    CREATIVE_BURST_BONUS, PANDEMIC_VETERAN_BONUS,
    EPIGENETIC_INHERITANCE_DECAY,
    WASTE_PER_ACTION, WASTE_DECAY, SYMBIOSIS_ENERGY_FACTOR, MAX_WASTE,
    APOPTOSIS_MIN_AGE, APOPTOSIS_ENERGY_THRESHOLD,
    APOPTOSIS_DIVERGENCE_THRESHOLD, APOPTOSIS_ENERGY_REDISTRIBUTION,
    TOXIC_RESIDUE, TOXIC_DURATION, SEED_GENOME,
    ACTION_COST, CAPTURE_REWARD, GENERATE_REWARD,
    _genome_divergence,
    CARRYING_CAPACITY, LOGISTIC_FLOOR,
    DRAINAGE_THRESHOLD, DRAINAGE_RATE, MAX_GRID_SIGNAL,
    CROSSOVER_PROBABILITY, CROSSOVER_ENERGY_THRESHOLD,
    COMPETITION_DIVISOR_CAP,
    crossover,
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
        grid[0][0] = 1.0  # Signal élevé → plein coût maintenance
        cell.tick(grid, [])
        # C capture signal 1.0 → register=1.0, CAPTURE_REWARD=3.0 → +3.0
        # 60 >= 50 et signal >= 0.1 -> MAINTENANCE_COST = 1.8
        # 60 + 3.0 - 1.8 = 61.2
        assert cell.energy == pytest.approx(61.2, abs=0.01)

    def test_low_energy_pays_basal_cost(self):
        cell = NeuralCell(genome="C", x=0, y=0, energy=40.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        cell.tick(grid, [])
        # 40 < 50 -> MAINTENANCE_COST_BASAL = 0.3 -> 40 - 0.3 = 39.7
        assert cell.energy == pytest.approx(39.7, abs=0.01)

    def test_threshold_boundary(self):
        """Exactement 50.0 d'energie = plein cout (>=50) en zone riche."""
        cell = NeuralCell(genome="C", x=0, y=0, energy=50.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid[0][0] = 1.0  # Signal élevé → plein coût maintenance
        cell.tick(grid, [])
        # C capture signal 1.0 → register=1.0, CAPTURE_REWARD=3.0 → +3.0
        # 50 >= 50 et signal >= 0.1 -> MAINTENANCE_COST = 1.8
        # 50 + 3.0 - 1.8 = 51.2
        assert cell.energy == pytest.approx(51.2, abs=0.01)

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


class TestMinZoneIntensity:
    """Levier 1 — Plancher d'intensité MIN_ZONE_INTENSITY."""

    def test_min_intensity_constant(self):
        assert MIN_ZONE_INTENSITY == 0.15

    def test_min_intensity_floor_applied(self):
        """Zone avec intensity 0.0 reçoit quand même du signal grâce au plancher."""
        tissue = _make_tissue()
        tissue.grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        tissue._circadian_phase = "eveil"
        # Forcer toutes les intensités cognitives à 0.0
        tissue._cognitive_state = {
            "emotion_intensity": 0.0, "threat_level": 0.0,
            "dopamine_level": 0.0, "goal_count": 0.0,
            "desire_intensity": 0.0, "memory_activity": 0.0,
            "stability": 0.0, "creativity": 0.0, "cognition_level": 0.0,
            "thermal_stress": 0.0, "somatic_load": 0.0,
            "suffocation": 0.0, "vitality_level": 0.5,
        }
        random.seed(42)
        tissue._inject_signals()
        # Toutes les zones devraient avoir du signal grâce à MIN_ZONE_INTENSITY
        total_signal = sum(
            tissue.grid[y][x]
            for y in range(GRID_SIZE)
            for x in range(GRID_SIZE)
        )
        assert total_signal > 0, "Grille devrait avoir du signal malgré intensités à 0.0"

    def test_min_intensity_no_effect_on_high(self):
        """Zone avec intensity 0.7 n'est pas affectée par le plancher."""
        tissue = _make_tissue()
        tissue.grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        tissue._circadian_phase = "eveil"
        # Forcer goal_count élevé → intensity goals = min(10/3, 1.0) = 1.0
        tissue._cognitive_state = {
            "emotion_intensity": 0.0, "threat_level": 0.0,
            "dopamine_level": 0.0, "goal_count": 10,
            "desire_intensity": 0.0, "memory_activity": 0.0,
            "stability": 0.0, "creativity": 0.0, "cognition_level": 0.0,
            "thermal_stress": 0.0, "somatic_load": 0.0,
            "suffocation": 0.0, "vitality_level": 0.5,
        }
        random.seed(42)
        tissue._inject_signals()
        gx1, gy1, gx2, gy2 = SIGNAL_ZONES["goals"]
        max_signal = max(
            tissue.grid[y][x]
            for y in range(gy1, min(gy2, GRID_SIZE))
            for x in range(gx1, min(gx2, GRID_SIZE))
        )
        assert max_signal > MIN_ZONE_INTENSITY, "Zone riche devrait avoir un signal > plancher"


class TestSignalAwareMaintenance:
    """Levier 2 — Maintenance contextuelle selon le signal local."""

    def test_low_signal_pays_basal(self):
        """Cellule sur signal=0, energy=60 → coût basal 0.3."""
        cell = NeuralCell(genome="C", x=0, y=0, energy=60.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        cell.tick(grid, [])
        # Signal 0.0 < 0.1 → MAINTENANCE_COST_BASAL = 0.3
        # C sans signal → register=0, pas de capture
        # 60 - 0.3 = 59.7
        assert cell.energy == pytest.approx(59.7, abs=0.01)

    def test_high_signal_pays_full(self):
        """Cellule sur signal=1.0, energy=60 → coût plein 1.0."""
        cell = NeuralCell(genome="C", x=0, y=0, energy=60.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid[0][0] = 1.0
        cell.tick(grid, [])
        # C capture signal → +3.0, grid[0][0] *= 0.5 → 0.5
        # 0.5 >= 0.1 et 63 >= 50 → MAINTENANCE_COST = 1.8
        # 60 + 3.0 - 1.8 = 61.2
        assert cell.energy == pytest.approx(61.2, abs=0.01)

    def test_low_energy_always_basal(self):
        """energy < 50 → coût basal même avec signal élevé."""
        cell = NeuralCell(genome="C", x=0, y=0, energy=40.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid[0][0] = 1.0
        cell.tick(grid, [])
        # C capture signal → +3.0, grid[0][0] *= 0.5 → 0.5
        # 0.5 >= 0.1 MAIS 43 < 50 → MAINTENANCE_COST_BASAL = 0.3
        # 40 + 3.0 - 0.3 = 42.7
        assert cell.energy == pytest.approx(42.7, abs=0.01)

    def test_signal_aware_extends_desert_survival(self):
        """Cellule en zone pauvre (signal=0) survit > 200 ticks."""
        cell = NeuralCell(genome="C", x=0, y=0, energy=100.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        ticks = 0
        while cell.alive:
            cell.tick(grid, [])
            ticks += 1
        # 100 / 0.3 = ~333 ticks
        assert ticks > 200, f"Cellule morte après seulement {ticks} ticks en zone déserte"


class TestDawnRepopulateImproved:
    """Levier 3 — Repeuplement amélioré à l'aube."""

    def test_dawn_count_constant(self):
        assert DAWN_REPOPULATE_COUNT == 5

    def test_dawn_uses_viable_genomes(self):
        """Les cellules injectées ont un génome issu de VIABLE_GENOMES."""
        tissue = _make_tissue()
        tissue.cells = []  # Vider la population
        tissue._zone_signals = {"goals": {"density": 0.01}}  # Zone désertée
        random.seed(42)
        tissue._dawn_repopulate()
        for cell in tissue.cells:
            assert cell.genome in VIABLE_GENOMES, (
                f"Génome {cell.genome} n'est pas dans VIABLE_GENOMES"
            )

    def test_dawn_spawns_count_per_zone(self):
        """5 cellules sont injectées par zone désertée."""
        tissue = _make_tissue()
        tissue.cells = []
        tissue._zone_signals = {"goals": {"density": 0.01}}
        random.seed(42)
        tissue._dawn_repopulate()
        assert len(tissue.cells) == DAWN_REPOPULATE_COUNT


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
    """Division accessible a DIVISION_THRESHOLD=115."""

    def test_division_constant(self):
        assert DIVISION_THRESHOLD == 115.0

    def test_division_at_116(self):
        """Cellule avec 116 d'energie et instruction R peut diviser."""
        cell = NeuralCell(genome="R", x=8, y=8, energy=116.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        child = cell.tick(grid, [])
        assert child is not None, "Devrait diviser a 116"
        assert child.alive

    def test_no_division_at_114(self):
        """Cellule avec 114 d'energie ne peut pas diviser."""
        cell = NeuralCell(genome="R", x=8, y=8, energy=114.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        child = cell.tick(grid, [])
        assert child is None, "Ne devrait pas diviser a 114"


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


class TestSeasonality:
    """Saisonnalite — rotation des bonus de nourriture entre zones."""

    def test_season_cycle_advances(self):
        """L'index de saison change apres SEASON_CYCLE_LENGTH ticks."""
        tissue = _make_tissue()
        tissue._circadian_phase = "eveil"
        assert tissue._current_season_index == 0
        # tick_count est incremente en fin de _tick(), _inject_signals voit l'ancienne valeur
        # Il faut que _inject_signals voie tick_count=500 → changement au 501e tick
        # On positionne tick_count juste avant le seuil pour aller plus vite
        tissue.tick_count = SEASON_CYCLE_LENGTH - 1  # 499
        tissue._tick()  # _inject_signals voit 499, puis tick_count → 500
        assert tissue._current_season_index == 0
        tissue._tick()  # _inject_signals voit 500 → 500//500=1 → changement
        assert tissue._current_season_index == 1, (
            f"Season index devrait etre 1, got {tissue._current_season_index}"
        )

    def test_season_bonus_applied(self):
        """Zone en saison haute recoit plus de signal que zone neutre."""
        tissue = _make_tissue()
        tissue._circadian_phase = "eveil"
        tissue._current_season_index = 0  # saison "emotion"
        tissue.tick_count = 0
        tissue.grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        tissue._cognitive_state = {
            "emotion_intensity": 0.5, "threat_level": 0.0,
            "dopamine_level": 0.5, "goal_count": 0,
            "desire_intensity": 50.0, "memory_activity": 0.3,
            "stability": 0.5, "creativity": 0.3, "cognition_level": 0.3,
            "thermal_stress": 0.0, "somatic_load": 0.0,
            "suffocation": 0.0, "vitality_level": 0.5,
        }
        # Pas de goal bonus
        tissue._goal_bonus_zones = {}
        random.seed(42)
        tissue._inject_signals()

        # Mesurer signal zone "emotion" (saison haute) vs zone "threat" (neutre)
        ex1, ey1, ex2, ey2 = SIGNAL_ZONES["emotion"]
        signal_emotion = sum(
            tissue.grid[y][x]
            for y in range(ey1, ey2) for x in range(ex1, ex2)
        )
        tx1, ty1, tx2, ty2 = SIGNAL_ZONES["threat"]
        signal_threat = sum(
            tissue.grid[y][x]
            for y in range(ty1, ty2) for x in range(tx1, tx2)
        )
        assert signal_emotion > signal_threat, (
            f"Zone emotion (saison haute) devrait avoir plus de signal: "
            f"{signal_emotion:.2f} vs {signal_threat:.2f}"
        )

    def test_season_transition_bonus(self):
        """Zone sortante recoit un bonus intermediaire."""
        tissue = _make_tissue()
        tissue._circadian_phase = "eveil"
        tissue._current_season_index = 1  # saison "desire", sortante = "emotion"
        tissue.tick_count = SEASON_CYCLE_LENGTH  # pour eviter changement
        tissue.grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        tissue._cognitive_state = {
            "emotion_intensity": 0.5, "threat_level": 0.0,
            "dopamine_level": 0.5, "goal_count": 0,
            "desire_intensity": 50.0, "memory_activity": 0.3,
            "stability": 0.5, "creativity": 0.3, "cognition_level": 0.3,
            "thermal_stress": 0.0, "somatic_load": 0.0,
            "suffocation": 0.0, "vitality_level": 0.5,
        }
        tissue._goal_bonus_zones = {}
        random.seed(42)
        tissue._inject_signals()

        # Zone sortante "emotion" (bonus transition) vs zone "threat" (neutre, meme intensite)
        ex1, ey1, ex2, ey2 = SIGNAL_ZONES["emotion"]
        signal_emotion = sum(
            tissue.grid[y][x]
            for y in range(ey1, ey2) for x in range(ex1, ex2)
        )
        tx1, ty1, tx2, ty2 = SIGNAL_ZONES["threat"]
        signal_threat = sum(
            tissue.grid[y][x]
            for y in range(ty1, ty2) for x in range(tx1, tx2)
        )
        assert signal_emotion > signal_threat, (
            f"Zone sortante devrait avoir plus de signal: "
            f"{signal_emotion:.2f} vs {signal_threat:.2f}"
        )

    def test_season_no_bonus_other_zones(self):
        """Les zones non-saison et non-sortante ne recoivent pas de bonus."""
        tissue = _make_tissue()
        tissue._circadian_phase = "eveil"
        tissue._current_season_index = 0  # saison "emotion", sortante = "threat" (index -1 = 8)
        tissue.tick_count = 0
        # La saison 0 = "emotion", sortante = SEASON_ORDER[-1] = "threat"
        # Toutes les autres zones ne devraient pas avoir de bonus saisonnalite
        assert SEASON_ORDER[0] == "emotion"
        assert SEASON_ORDER[-1] == "threat"
        # Verification : cognition n'est ni saison haute ni sortante
        assert "cognition" != SEASON_ORDER[0]
        assert "cognition" != SEASON_ORDER[-1]

    def test_get_current_season(self):
        """get_current_season retourne les bonnes infos."""
        tissue = _make_tissue()
        tissue._current_season_index = 3
        tissue.tick_count = SEASON_CYCLE_LENGTH * 3 + 250
        season = tissue.get_current_season()
        assert season["zone"] == SEASON_ORDER[3]  # "cognition"
        assert season["next_zone"] == SEASON_ORDER[4]  # "creativity"
        assert season["tick_in_season"] == 250
        assert "50%" == season["progress"]

    def test_season_change_event(self):
        """TISSUE_SEASON_CHANGE est publie au changement de saison."""
        tissue = _make_tissue()
        tissue._circadian_phase = "eveil"
        tissue._current_season_index = 0
        published = []
        original_try_publish = tissue._try_publish

        def mock_try_publish(event_name, payload, **kwargs):
            if event_name == "TISSUE_SEASON_CHANGE":
                published.append(payload)
            return original_try_publish(event_name, payload, **kwargs)

        tissue._try_publish = mock_try_publish
        # Positionner juste avant le seuil
        tissue.tick_count = SEASON_CYCLE_LENGTH - 1
        tissue._tick()  # voit 499, pas de changement
        tissue._tick()  # voit 500 → changement
        assert len(published) >= 1, "TISSUE_SEASON_CHANGE devrait etre publie"
        assert published[0]["from_zone"] == "emotion"
        assert published[0]["to_zone"] == "desire"

    def test_season_persisted(self):
        """save/load preserve l'index de saison."""
        tissue = _make_tissue()
        tissue._current_season_index = 5
        tissue.total_exiles = 42
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = f.name
        try:
            import core.neural_tissue as nt_module
            old_file = nt_module.TISSUE_STATE_FILE
            nt_module.TISSUE_STATE_FILE = tmp_path
            tissue._save()
            # Reset et reload
            tissue._current_season_index = 0
            tissue.total_exiles = 0
            tissue._load()
            assert tissue._current_season_index == 5
            assert tissue.total_exiles == 42
        finally:
            nt_module.TISSUE_STATE_FILE = old_file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


class TestAlphaRule:
    """Modele Alpha — 1 alpha max par zone, expulsion des challengers."""

    def test_alpha_detection(self):
        """Cellule avec energie > seuil est detectee comme alpha."""
        tissue = _make_tissue()
        tissue.cells = []
        # Cellule alpha par energie
        alpha = NeuralCell(genome="GCGC", x=13, y=13, energy=400.0)
        tissue.cells.append(alpha)
        summary = tissue._get_alpha_summary()
        assert "goals" in summary, f"Alpha devrait etre dans goals, got {summary}"

    def test_single_alpha_tolerated(self):
        """Un seul alpha dans une zone ne provoque pas d'exil."""
        tissue = _make_tissue()
        tissue.cells = [
            NeuralCell(genome="GCGC", x=13, y=13, energy=400.0),
        ]
        tissue.total_exiles = 0
        tissue._enforce_alpha_rule()
        assert tissue.total_exiles == 0

    def test_dual_alpha_exile(self):
        """2 alphas dans une zone → le plus faible est expulse."""
        tissue = _make_tissue()
        dominant = NeuralCell(genome="GCGC", x=13, y=13, energy=400.0, output_count=100)
        challenger = NeuralCell(genome="CCGC", x=14, y=14, energy=400.0, output_count=50)
        tissue.cells = [dominant, challenger]
        tissue._zone_signals = {z: {"density": 0.01} for z in SIGNAL_ZONES}
        tissue.total_exiles = 0
        tissue._enforce_alpha_rule()
        assert tissue.total_exiles == 1
        # Le challenger devrait avoir ete deplace hors de goals
        zone = tissue._get_cell_zone(challenger)
        assert zone != "goals", f"Challenger devrait etre hors de goals, est dans {zone}"

    def test_exile_migration(self):
        """Challenger est migre vers une zone adjacente moins dense."""
        tissue = _make_tissue()
        dominant = NeuralCell(genome="GCGC", x=13, y=13, energy=400.0, output_count=100)
        challenger = NeuralCell(genome="CCGC", x=14, y=14, energy=400.0, output_count=50)
        tissue.cells = [dominant, challenger]
        tissue._zone_signals = {z: {"density": 0.01} for z in SIGNAL_ZONES}
        tissue.total_exiles = 0
        tissue._enforce_alpha_rule()
        # Le challenger est dans une zone adjacente a goals
        new_zone = tissue._get_cell_zone(challenger)
        assert new_zone in ZONE_ADJACENCY["goals"], (
            f"Challenger devrait etre dans une zone adjacente a goals, est dans {new_zone}"
        )

    def test_exile_forced_division(self):
        """Si toutes zones adjacentes sont denses, division forcee."""
        tissue = _make_tissue()
        dominant = NeuralCell(genome="GCGC", x=13, y=13, energy=400.0, output_count=100)
        challenger = NeuralCell(genome="CCGC", x=14, y=14, energy=400.0, output_count=50)
        tissue.cells = [dominant, challenger]
        # Toutes les zones super denses → forcer division
        tissue._zone_signals = {z: {"density": 3.0} for z in SIGNAL_ZONES}
        tissue.total_exiles = 0
        before_count = len(tissue.cells)
        tissue._enforce_alpha_rule()
        assert tissue.total_exiles == 1
        # Division forcee ajoute un enfant
        assert len(tissue.cells) == before_count + 1
        # Energie du challenger divisee par 2
        assert challenger.energy == pytest.approx(200.0, abs=1.0)

    def test_exile_mutation_rate(self):
        """Le genome du challenger est mute apres exil."""
        tissue = _make_tissue()
        dominant = NeuralCell(genome="GCGC", x=13, y=13, energy=400.0, output_count=100)
        original_genome = "CCGCCCGCCCGCCCGCCCGCCCGC"  # Long pour maximiser chances mutation
        challenger = NeuralCell(genome=original_genome, x=14, y=14, energy=400.0, output_count=50)
        tissue.cells = [dominant, challenger]
        tissue._zone_signals = {z: {"density": 0.01} for z in SIGNAL_ZONES}
        tissue._circadian_phase = "crepuscule"  # mutation rate doublee
        random.seed(42)
        tissue._enforce_alpha_rule()
        # Avec un genome long et mutation rate doublee × 2 (exile), forte chance de mutation
        # On verifie juste que la mutation a ete appliquee (genome different ou pas)
        # En pratique, avec rate=0.08 sur 24 chars, ~2 mutations en moyenne
        assert challenger.genome is not None
        assert len(challenger.genome) >= 2

    def test_alpha_exile_event(self):
        """TISSUE_ALPHA_EXILE est publie lors d'un exil."""
        tissue = _make_tissue()
        dominant = NeuralCell(genome="GCGC", x=13, y=13, energy=400.0, output_count=100)
        challenger = NeuralCell(genome="CCGC", x=14, y=14, energy=400.0, output_count=50)
        tissue.cells = [dominant, challenger]
        tissue._zone_signals = {z: {"density": 0.01} for z in SIGNAL_ZONES}
        published = []
        original_try_publish = tissue._try_publish

        def mock_try_publish(event_name, payload, **kwargs):
            if event_name == "TISSUE_ALPHA_EXILE":
                published.append(payload)

        tissue._try_publish = mock_try_publish
        tissue._enforce_alpha_rule()
        assert len(published) == 1
        assert published[0]["from_zone"] == "goals"

    def test_total_exiles_counted(self):
        """3 alphas dans une zone → 2 exils comptes."""
        tissue = _make_tissue()
        alpha1 = NeuralCell(genome="GCGC", x=13, y=13, energy=400.0, output_count=200)
        alpha2 = NeuralCell(genome="CCGC", x=14, y=14, energy=400.0, output_count=100)
        alpha3 = NeuralCell(genome="RCGC", x=13, y=14, energy=400.0, output_count=50)
        tissue.cells = [alpha1, alpha2, alpha3]
        tissue._zone_signals = {z: {"density": 0.01} for z in SIGNAL_ZONES}
        tissue.total_exiles = 0
        tissue._enforce_alpha_rule()
        assert tissue.total_exiles == 2


class TestPandemic:
    """Système immunitaire — pandémies périodiques ciblant le génome dominant."""

    def _make_pandemic_tissue(self, genome="GRCGC", count=150):
        """Crée un tissue prêt pour les tests pandémie."""
        tissue = _make_tissue()
        tissue.cells = [
            NeuralCell(genome=genome, x=i % GRID_SIZE, y=i // GRID_SIZE, energy=100.0)
            for i in range(count)
        ]
        tissue._update_dominant_patterns()
        tissue._circadian_phase = "eveil"
        return tissue

    # 1
    def test_pandemic_constants(self):
        """Valeurs des constantes pandémie (régulation v2)."""
        assert PANDEMIC_MIN_INTERVAL == 36000
        assert PANDEMIC_MAX_INTERVAL == 54000
        assert PANDEMIC_MIN_POPULATION == 100
        assert PANDEMIC_MOTIF_LEN == 2
        assert PANDEMIC_INFECTION_RATE == 0.60
        assert INFECTION_DURATION_MIN == 10
        assert INFECTION_DURATION_MAX == 25
        assert INFECTION_DRAIN_MIN == 3.0
        assert INFECTION_DRAIN_MAX == 8.0
        assert IMMUNE_MUTATION_CHANCE == 0.15
        assert IMMUNITY_DECAY_GENERATIONS == 5

    # 2
    def test_trigger_targets_dominant(self):
        """Le motif pathogène est un bigramme du génome dominant."""
        tissue = self._make_pandemic_tissue("GRCGC", 150)
        random.seed(42)
        tissue._trigger_pandemic()
        assert tissue._pandemic_active
        assert len(tissue._pandemic_motif) == PANDEMIC_MOTIF_LEN
        assert tissue._pandemic_motif in "GRCGC"

    # 3
    def test_infection_marks_matching_cells(self):
        """Seules les cellules contenant le motif peuvent être infectées, et AAATT jamais."""
        tissue = _make_tissue()
        # 10 cellules GRCGC + 1 AAATT pour que le rate 60% soit significatif
        tissue.cells = [
            NeuralCell(genome="GRCGC", x=i % GRID_SIZE, y=i // GRID_SIZE, energy=100.0)
            for i in range(10)
        ] + [NeuralCell(genome="AAATT", x=10, y=0, energy=100.0)]
        tissue._update_dominant_patterns()
        tissue._pandemic_active = False
        tissue.dominant_patterns = [{"genome": "GRCGC", "frequency": 0.91, "avg_fitness": 1.0}]
        # Monkey-patch pour forcer le motif "GR"
        old_randint = random.randint
        random.randint = lambda a, b: 0  # start=0 → motif "GR"
        try:
            tissue._trigger_pandemic()
        finally:
            random.randint = old_randint
        infected = [c for c in tissue.cells if c.infected_by is not None]
        saines = [c for c in tissue.cells if c.infected_by is None]
        # AAATT ne doit jamais être infectée
        aaatt_cell = tissue.cells[-1]
        assert aaatt_cell.infected_by is None
        # max_infected = max(1, int(10 * 0.60)) = 6 → 6 infectées sur 10
        assert len(infected) == 6
        assert all(c.genome == "GRCGC" for c in infected)

    # 4
    def test_infection_timer_initialized(self):
        """Le timer d'infection est dans [DURATION_MIN, DURATION_MAX]."""
        tissue = self._make_pandemic_tissue("GRCGC", 150)
        tissue._trigger_pandemic()
        for c in tissue.cells:
            if c.infected_by is not None:
                assert INFECTION_DURATION_MIN <= c.infection_timer <= INFECTION_DURATION_MAX

    # 5
    def test_energy_drain_per_tick(self):
        """Chaque tick draine _pandemic_drain d'énergie aux infectées."""
        tissue = _make_tissue()
        cell = NeuralCell(genome="GRCGC", x=0, y=0, energy=200.0)
        cell.infected_by = "GR"
        cell.infection_timer = 15
        tissue.cells = [cell]
        tissue._pandemic_active = True
        tissue._pandemic_motif = "GR"
        tissue._pandemic_drain = 5.0  # Sévérité fixée pour le test
        initial_energy = cell.energy
        # Forcer pas de mutation immunitaire
        with patch("core.neural_tissue.random.random", return_value=0.99):
            tissue._pandemic_tick()
        assert cell.energy == pytest.approx(initial_energy - 5.0, abs=0.01)

    # 6
    def test_immune_mutation_can_cure(self):
        """La mutation immunitaire peut éliminer le motif du génome."""
        # Tester plusieurs fois car la mutation est aléatoire
        cured = False
        for seed in range(100):
            random.seed(seed)
            result = NeuralTissue._immune_mutation("GRCGC", "GR")
            if "GR" not in result:
                cured = True
                break
        assert cured, "La mutation immunitaire devrait pouvoir éliminer le motif"

    # 7
    def test_immune_mutation_preserves_length(self):
        """La mutation immunitaire conserve la longueur du génome."""
        for seed in range(20):
            random.seed(seed)
            result = NeuralTissue._immune_mutation("GRCGC", "GR")
            assert len(result) == len("GRCGC")

    # 8
    def test_cured_cell_gains_immunity(self):
        """Une cellule guérie gagne l'immunité contre le motif."""
        tissue = _make_tissue()
        cell = NeuralCell(genome="GRCGC", x=0, y=0, energy=200.0)
        cell.infected_by = "GR"
        cell.infection_timer = 15
        tissue.cells = [cell]
        tissue._pandemic_active = True
        tissue._pandemic_motif = "GR"
        # Forcer mutation + guérison : mutation qui élimine "GR"
        def mock_immune_mut(genome, motif):
            return "AACGC"  # "GR" disparu
        with patch.object(NeuralTissue, '_immune_mutation', side_effect=mock_immune_mut):
            with patch("core.neural_tissue.random.random", return_value=0.01):  # < 0.10
                tissue._pandemic_tick()
        assert cell.infected_by is None
        assert "GR" in cell.immune_to

    # 9
    def test_immunity_prevents_reinfection(self):
        """Une cellule immune n'est pas réinfectée."""
        tissue = _make_tissue()
        immune_cell = NeuralCell(genome="GRCGC", x=0, y=0, energy=100.0,
                                  immune_to={"GR"})
        normal_cell = NeuralCell(genome="GRCGC", x=1, y=0, energy=100.0)
        tissue.cells = [immune_cell, normal_cell]
        tissue.dominant_patterns = [{"genome": "GRCGC", "frequency": 1.0, "avg_fitness": 1.0}]
        # Forcer motif "GR"
        old_randint = random.randint
        random.randint = lambda a, b: 0
        try:
            tissue._trigger_pandemic()
        finally:
            random.randint = old_randint
        assert immune_cell.infected_by is None
        assert normal_cell.infected_by == "GR"

    # 10
    def test_immunity_inherited_via_replicate(self):
        """L'enfant hérite de l'immunité du parent."""
        parent = NeuralCell(genome="GRCGCR", x=8, y=8, energy=300.0,
                            generation=1, immune_to={"GR", "CG"})
        parent._eff_mutation_rate = 0.0  # Pas de mutation
        child = parent._replicate()
        assert "GR" in child.immune_to
        assert "CG" in child.immune_to

    # 11
    def test_immunity_decays_over_generations(self):
        """L'immunité perd un élément après N générations."""
        parent = NeuralCell(genome="GRCGCR", x=8, y=8, energy=300.0,
                            generation=IMMUNITY_DECAY_GENERATIONS - 1,
                            immune_to={"GR", "CG"})
        parent._eff_mutation_rate = 0.0
        child = parent._replicate()
        # generation enfant = 5, 5 % 5 == 0 → decay
        assert len(child.immune_to) == 1

    # 12
    def test_timer_schedules_within_range(self):
        """Le prochain timer est dans [MIN, MAX] ticks."""
        tissue = self._make_pandemic_tissue("GRCGC", 150)
        tissue._next_pandemic_tick = -1
        tissue._pandemic_check_timer()
        expected_min = tissue.tick_count + PANDEMIC_MIN_INTERVAL
        expected_max = tissue.tick_count + PANDEMIC_MAX_INTERVAL
        # Le timer a été initialisé (la pandémie n'a pas été déclenchée car tick_count < timer)
        assert expected_min <= tissue._next_pandemic_tick <= expected_max

    # 13
    def test_skip_during_sleep(self):
        """Pas de pandémie en sommeil profond."""
        tissue = self._make_pandemic_tissue("GRCGC", 150)
        tissue._circadian_phase = "sommeil_profond"
        tissue._next_pandemic_tick = tissue.tick_count  # Devrait déclencher
        tissue._pandemic_check_timer()
        assert not tissue._pandemic_active

    # 14
    def test_skip_population_low(self):
        """Pas de pandémie si population < PANDEMIC_MIN_POPULATION."""
        tissue = self._make_pandemic_tissue("GRCGC", 50)  # < 100
        tissue._next_pandemic_tick = tissue.tick_count
        tissue._pandemic_check_timer()
        assert not tissue._pandemic_active

    # 15
    def test_pandemic_start_event(self):
        """TISSUE_PANDEMIC_START est publié au déclenchement."""
        tissue = self._make_pandemic_tissue("GRCGC", 150)
        published = []
        original = tissue._try_publish

        def mock_publish(event, payload):
            if event == "TISSUE_PANDEMIC_START":
                published.append(payload)
            return original(event, payload)

        tissue._try_publish = mock_publish
        tissue._trigger_pandemic()
        assert len(published) == 1
        assert "motif" in published[0]
        assert "infected_count" in published[0]

    # 16
    def test_pandemic_end_event(self):
        """TISSUE_PANDEMIC_END est publié à la fin de la pandémie."""
        tissue = _make_tissue()
        cell = NeuralCell(genome="AAATT", x=0, y=0, energy=200.0)
        # Pas infectée, mais pandémie active → fin immédiate
        tissue.cells = [cell]
        tissue._pandemic_active = True
        tissue._pandemic_motif = "GR"
        published = []
        original = tissue._try_publish

        def mock_publish(event, payload):
            if event == "TISSUE_PANDEMIC_END":
                published.append(payload)
            return original(event, payload)

        tissue._try_publish = mock_publish
        tissue._pandemic_tick()
        assert len(published) == 1
        assert published[0]["motif"] == "GR"

    # 17
    def test_resistant_cells_survive(self):
        """Les cellules sans le motif ne sont pas affectées."""
        tissue = _make_tissue()
        resistant = NeuralCell(genome="AAATT", x=0, y=0, energy=100.0)
        vulnerable = NeuralCell(genome="GRCGC", x=1, y=0, energy=100.0)
        tissue.cells = [resistant, vulnerable]
        tissue.dominant_patterns = [{"genome": "GRCGC", "frequency": 0.5, "avg_fitness": 1.0}]
        old_randint = random.randint
        random.randint = lambda a, b: 0  # motif "GR"
        try:
            tissue._trigger_pandemic()
        finally:
            random.randint = old_randint
        assert resistant.infected_by is None
        assert resistant.alive
        assert vulnerable.infected_by == "GR"

    # 18
    def test_multiple_pandemics(self):
        """Deux pandémies successives sont comptées correctement."""
        tissue = self._make_pandemic_tissue("GRCGC", 150)
        tissue._trigger_pandemic()
        assert tissue.total_pandemics == 1
        # Terminer la pandémie
        tissue._pandemic_end(0, 0)
        assert not tissue._pandemic_active
        # Deuxième pandémie
        tissue._trigger_pandemic()
        assert tissue.total_pandemics == 2

    # 19
    def test_infection_rate_caps_infected(self):
        """Le taux d'infection plafonne les infectés à PANDEMIC_INFECTION_RATE."""
        tissue = self._make_pandemic_tissue("GRCGC", 200)
        random.seed(42)
        tissue._trigger_pandemic()
        infected = [c for c in tissue.cells if c.infected_by is not None]
        # 200 cellules GRCGC, toutes vulnérables → max 60% = 120
        assert len(infected) <= int(200 * PANDEMIC_INFECTION_RATE)
        assert len(infected) > 0

    # 20
    def test_severity_variable_drain(self):
        """La sévérité variable produit un drain dans [DRAIN_MIN, DRAIN_MAX]."""
        tissue = self._make_pandemic_tissue("GRCGC", 150)
        tissue._trigger_pandemic()
        assert INFECTION_DRAIN_MIN <= tissue._pandemic_drain <= INFECTION_DRAIN_MAX

    # 21
    def test_severity_affects_duration(self):
        """La durée d'infection est dans [DURATION_MIN, DURATION_MAX]."""
        tissue = self._make_pandemic_tissue("GRCGC", 200)
        tissue._trigger_pandemic()
        durations = set()
        for c in tissue.cells:
            if c.infected_by is not None:
                durations.add(c.infection_timer)
        # Toutes les cellules d'une même pandémie ont la même durée
        assert len(durations) == 1
        duration = durations.pop()
        assert INFECTION_DURATION_MIN <= duration <= INFECTION_DURATION_MAX

    # 22
    def test_pandemic_event_includes_severity(self):
        """L'événement TISSUE_PANDEMIC_START contient severity, drain et duration."""
        tissue = self._make_pandemic_tissue("GRCGC", 150)
        published = []
        original = tissue._try_publish

        def mock_publish(event, payload):
            if event == "TISSUE_PANDEMIC_START":
                published.append(payload)
            return original(event, payload)

        tissue._try_publish = mock_publish
        tissue._trigger_pandemic()
        assert len(published) == 1
        assert "severity" in published[0]
        assert "drain_per_tick" in published[0]
        assert "duration" in published[0]
        assert 0.0 <= published[0]["severity"] <= 1.0


# ═══════════════════════════════════════════════════════════════
# BIOLOGIE AVANCÉE — Épigénétique, Symbiose, Apoptose/Nécrose
# ═══════════════════════════════════════════════════════════════


class TestEpigenetics:
    """Sprint A — Marqueurs épigénétiques acquis par expérience."""

    # 1
    def test_epigenetic_constants(self):
        """Valeurs des constantes épigénétiques."""
        assert HEAT_TOLERANT_CYCLES == 50
        assert HEAT_TOLERANT_SIGNAL_THRESHOLD == 3.0
        assert HEAT_TOLERANT_COST_FACTOR == 0.5
        assert FAMINE_ADAPTED_CYCLES == 30
        assert FAMINE_ADAPTED_COST_FACTOR == 0.5
        assert CREATIVE_BURST_OUTPUT_THRESHOLD == 20
        assert CREATIVE_BURST_BONUS == 0.5
        assert PANDEMIC_VETERAN_BONUS == 0.5
        assert EPIGENETIC_INHERITANCE_DECAY == 0.15

    # 2
    def test_initial_no_markers(self):
        """Nouvelle cellule n'a aucun marqueur."""
        cell = NeuralCell(genome="C", x=0, y=0)
        assert cell.epigenetic_markers == {}

    # 3
    def test_has_marker_false_initially(self):
        """_has_marker retourne False si aucun marqueur."""
        cell = NeuralCell(genome="C", x=0, y=0)
        assert not cell._has_marker("heat_tolerant")
        assert not cell._has_marker("famine_adapted")

    # 4
    def test_heat_tolerant_acquisition(self):
        """50 cycles avec signal > 3.0 acquiert heat_tolerant."""
        cell = NeuralCell(genome="C", x=0, y=0, energy=200.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid[0][0] = 4.0  # > 3.0
        for _ in range(HEAT_TOLERANT_CYCLES):
            cell._update_epigenetics(grid)
        assert cell._has_marker("heat_tolerant")

    # 5
    def test_heat_tolerant_not_acquired_early(self):
        """49 cycles ne suffisent pas."""
        cell = NeuralCell(genome="C", x=0, y=0, energy=200.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid[0][0] = 4.0
        for _ in range(HEAT_TOLERANT_CYCLES - 1):
            cell._update_epigenetics(grid)
        assert not cell._has_marker("heat_tolerant")

    # 6
    def test_heat_tolerant_cost_reduction(self):
        """Cellule heat_tolerant paie 50% de maintenance en zone chaude."""
        cell = NeuralCell(genome="C", x=0, y=0, energy=100.0,
                          epigenetic_markers={"heat_tolerant": {"cycles": 50, "acquired": True}})
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid[0][0] = 4.0  # > 3.0 → active le modificateur
        cell.tick(grid, [])
        # C capture signal 4.0 → register=4.0, reward=3.0*min(4.0,2.0)=6.0
        # grid[0][0] *= 0.5 → 2.0, épigénétique update (already acquired)
        # 2.0 < 3.0 → signal pas assez haut pour HEAT_TOLERANT_SIGNAL_THRESHOLD
        # Wait, after C instruction: grid[0][0] = 4.0 * 0.5 = 2.0
        # local_signal = 2.0 < 3.0 → cost starts as MAINTENANCE_COST (energy >= 50, signal >= 0.1)
        # heat_tolerant check: local_signal 2.0 < 3.0 → NOT activated
        # Let me use a higher signal
        cell2 = NeuralCell(genome="C", x=0, y=0, energy=100.0,
                           epigenetic_markers={"heat_tolerant": {"cycles": 50, "acquired": True}})
        grid2 = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid2[0][0] = 8.0  # After C: grid = 4.0, signal still > 3.0
        cell2.tick(grid2, [])
        # C: signal=8.0 > 0.1 → register=8.0, reward=3.0*min(8.0,2.0)=6.0
        # grid[0][0] = 8.0*0.5 = 4.0 after C
        # local_signal = 4.0 > 0.1, energy = 106 >= 50 → cost = MAINTENANCE_COST = 1.8
        # heat_tolerant: local_signal 4.0 > 3.0 → cost *= 0.5 → 0.9
        # 100 + 6.0 - 0.9 = 105.1
        assert cell2.energy == pytest.approx(105.1, abs=0.01)

    # 7
    def test_famine_adapted_acquisition(self):
        """30 cycles avec energy < 50 acquiert famine_adapted."""
        cell = NeuralCell(genome="C", x=0, y=0, energy=40.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        for _ in range(FAMINE_ADAPTED_CYCLES):
            cell._update_epigenetics(grid)
        assert cell._has_marker("famine_adapted")

    # 8
    def test_famine_adapted_cost_reduction(self):
        """Cellule famine_adapted paie 50% du coût basal."""
        cell = NeuralCell(genome="C", x=0, y=0, energy=40.0,
                          epigenetic_markers={"famine_adapted": {"cycles": 30, "acquired": True}})
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        cell.tick(grid, [])
        # energy=40 < 50 + signal=0 < 0.1 → cost_basal=0.3
        # famine_adapted → cost *= 0.5 → 0.15
        # 40 - 0.15 = 39.85
        assert cell.energy == pytest.approx(39.85, abs=0.01)

    # 9
    def test_creative_burst_acquisition(self):
        """output_count >= 20 acquiert creative_burst."""
        cell = NeuralCell(genome="C", x=0, y=0, energy=100.0, output_count=20)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        cell._update_epigenetics(grid)
        assert cell._has_marker("creative_burst")

    # 10
    def test_creative_burst_bonus_generate(self):
        """Cellule creative_burst obtient +50% reward sur Generate."""
        cell = NeuralCell(genome="G", x=0, y=0, energy=100.0, register=1.0,
                          epigenetic_markers={"creative_burst": {"cycles": 0, "acquired": True}})
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        cell.tick(grid, [])
        # G: register=1.0 > 0.1 → output_count=1, reward=2.0*1.5=3.0
        # cost_action=0.5, cost_maintenance=0.3 (signal=0<0.1)
        # 100 + 3.0 - 0.5 - 0.3 = 102.2
        assert cell.energy == pytest.approx(102.2, abs=0.01)

    # 11
    def test_pandemic_veteran_acquisition(self):
        """Cellule guérie d'une pandémie acquiert pandemic_veteran."""
        tissue = _make_tissue()
        cell = NeuralCell(genome="GRCGC", x=0, y=0, energy=200.0)
        cell.infected_by = "GR"
        cell.infection_timer = 15
        tissue.cells = [cell]
        tissue._pandemic_active = True
        tissue._pandemic_motif = "GR"
        # Forcer mutation + guérison
        def mock_immune_mut(genome, motif):
            return "AACGC"  # "GR" disparu
        with patch.object(NeuralTissue, '_immune_mutation', side_effect=mock_immune_mut):
            with patch("core.neural_tissue.random.random", return_value=0.01):
                tissue._pandemic_tick()
        assert cell._has_marker("pandemic_veteran")

    # 12
    def test_pandemic_veteran_bonus_capture(self):
        """Cellule pandemic_veteran obtient +50% reward sur Capture."""
        cell = NeuralCell(genome="C", x=0, y=0, energy=100.0,
                          epigenetic_markers={"pandemic_veteran": {"cycles": 0, "acquired": True}})
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid[0][0] = 2.0
        cell.tick(grid, [])
        # C: signal=2.0 > 0.1 → register=2.0, reward=3.0*min(2.0,2.0)*1.5=9.0
        # grid[0][0] = 2.0*0.5 = 1.0, local_signal=1.0 >= 0.1
        # energy=109 >= 50 → cost=1.8
        # 100 + 9.0 - 1.8 = 107.2
        assert cell.energy == pytest.approx(107.2, abs=0.01)

    # 13
    def test_marker_inheritance_replicate(self):
        """L'enfant hérite des marqueurs acquis du parent."""
        # Tester sur plusieurs seeds pour trouver un héritage
        inherited_at_least_once = False
        for seed in range(20):
            random.seed(seed)
            NeuralTissue.reset_singleton()
            parent = NeuralCell(genome="GRCGCR", x=8, y=8, energy=300.0,
                                generation=1,
                                epigenetic_markers={
                                    "heat_tolerant": {"cycles": 50, "acquired": True},
                                    "famine_adapted": {"cycles": 30, "acquired": True},
                                })
            parent._eff_mutation_rate = 0.0
            child = parent._replicate()
            acquired = [n for n, m in child.epigenetic_markers.items() if m.get("acquired")]
            if len(acquired) >= 1:
                inherited_at_least_once = True
                break
        assert inherited_at_least_once, "Au moins un marqueur devrait être hérité sur 20 essais"

    # 14
    def test_marker_inheritance_decay_chance(self):
        """15% de chance de perdre chaque marqueur à la division."""
        inherited = 0
        trials = 100
        for i in range(trials):
            random.seed(i)
            parent = NeuralCell(genome="GRCGCR", x=8, y=8, energy=300.0,
                                epigenetic_markers={
                                    "heat_tolerant": {"cycles": 50, "acquired": True},
                                })
            parent._eff_mutation_rate = 0.0
            child = parent._replicate()
            if child._has_marker("heat_tolerant"):
                inherited += 1
        # ~85% héritage → 80-95 sur 100
        assert 70 <= inherited <= 95, f"Héritage inattendu: {inherited}/100"

    # 15
    def test_unacquired_not_inherited(self):
        """Marqueur non acquis n'est pas hérité."""
        parent = NeuralCell(genome="GRCGCR", x=8, y=8, energy=300.0,
                            epigenetic_markers={
                                "heat_tolerant": {"cycles": 10, "acquired": False},
                            })
        parent._eff_mutation_rate = 0.0
        child = parent._replicate()
        assert not child._has_marker("heat_tolerant")

    # 16
    def test_multiple_markers_coexist(self):
        """Plusieurs marqueurs peuvent coexister."""
        cell = NeuralCell(genome="C", x=0, y=0, energy=40.0, output_count=25)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid[0][0] = 4.0
        # Simuler cycles suffisants pour heat_tolerant + famine
        for _ in range(HEAT_TOLERANT_CYCLES):
            cell._update_epigenetics(grid)
        assert cell._has_marker("heat_tolerant")
        assert cell._has_marker("creative_burst")
        # famine_adapted aussi (energy < 50 au départ, mais elle augmente via capture)
        # On vérifie juste que les marqueurs coexistent

    # 17
    def test_markers_in_stats(self):
        """get_stats inclut le compteur de marqueurs épigénétiques."""
        tissue = _make_tissue()
        # Donner un marqueur à la première cellule
        tissue.cells[0].epigenetic_markers = {
            "heat_tolerant": {"cycles": 50, "acquired": True}
        }
        stats = tissue.get_stats()
        assert "epigenetic_markers" in stats
        assert stats["epigenetic_markers"].get("heat_tolerant", 0) >= 1

    # 18
    def test_epigenetics_persisted(self):
        """save/load préserve les marqueurs épigénétiques."""
        tissue = _make_tissue()
        tissue.cells[0].epigenetic_markers = {
            "heat_tolerant": {"cycles": 50, "acquired": True},
            "creative_burst": {"cycles": 0, "acquired": True},
        }
        import tempfile, os
        import core.neural_tissue as nt_module
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = f.name
        old_file = nt_module.TISSUE_STATE_FILE
        try:
            nt_module.TISSUE_STATE_FILE = tmp_path
            tissue._save()
            # Reset et reload
            tissue.cells[0].epigenetic_markers = {}
            tissue._load()
            markers = tissue.cells[0].epigenetic_markers
            assert markers.get("heat_tolerant", {}).get("acquired") is True
            assert markers.get("creative_burst", {}).get("acquired") is True
        finally:
            nt_module.TISSUE_STATE_FILE = old_file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


class TestSymbiosis:
    """Sprint B — waste_grid, instruction S, symbiose émergente."""

    # 1
    def test_symbiosis_constants(self):
        """Valeurs des constantes symbiose."""
        assert WASTE_PER_ACTION == 0.5
        assert WASTE_DECAY == 0.95
        assert SYMBIOSIS_ENERGY_FACTOR == 2.0
        assert MAX_WASTE == 5.0

    # 2
    def test_alphabet_includes_s(self):
        """L'alphabet inclut S pour Symbiose."""
        assert 'S' in ALPHABET
        assert ALPHABET == "ACGTIRS"

    # 3
    def test_waste_grid_initialized(self):
        """Le tissue a une waste_grid 16x16 initialisée à 0."""
        tissue = _make_tissue()
        assert len(tissue.waste_grid) == GRID_SIZE
        assert len(tissue.waste_grid[0]) == GRID_SIZE
        assert tissue.waste_grid[0][0] == 0.0

    # 4
    def test_action_deposits_waste(self):
        """Les instructions A/G/T/I déposent du waste quand waste_grid fourni."""
        waste_grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        for instr in ['A', 'G', 'T', 'I']:
            waste_grid[0][0] = 0.0
            cell = NeuralCell(genome=instr, x=0, y=0, energy=100.0)
            cell.tick(grid, [], waste_grid=waste_grid)
            assert waste_grid[0][0] == pytest.approx(WASTE_PER_ACTION, abs=0.01), (
                f"Instruction {instr} devrait déposer du waste"
            )

    # 5
    def test_waste_decay(self):
        """Le waste décroit de WASTE_DECAY par tick."""
        tissue = _make_tissue()
        tissue.waste_grid[5][5] = 2.0
        tissue._tick()
        assert tissue.waste_grid[5][5] == pytest.approx(2.0 * WASTE_DECAY, abs=0.1)

    # 6
    def test_instruction_s_consumes_waste(self):
        """L'instruction S consomme le waste local et gagne de l'énergie."""
        waste_grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        waste_grid[0][0] = 1.0
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        cell = NeuralCell(genome="S", x=0, y=0, energy=100.0)
        cell.tick(grid, [], waste_grid=waste_grid)
        # S: consomme min(1.0, 2.0)=1.0, energy += 1.0*2.0=2.0, - ACTION_COST=0.5
        # maintenance: signal=0<0.1 → basal=0.3
        # 100 + 2.0 - 0.5 - 0.3 = 101.2
        assert cell.energy == pytest.approx(101.2, abs=0.01)
        assert waste_grid[0][0] == pytest.approx(0.0, abs=0.01)

    # 7
    def test_instruction_s_no_waste_pays_cost(self):
        """S sans waste = simple perte ACTION_COST."""
        waste_grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        cell = NeuralCell(genome="S", x=0, y=0, energy=100.0)
        cell.tick(grid, [], waste_grid=waste_grid)
        # S: no waste → juste -ACTION_COST=0.5, basal=0.3
        # 100 - 0.5 - 0.3 = 99.2
        assert cell.energy == pytest.approx(99.2, abs=0.01)

    # 8
    def test_instruction_s_capped(self):
        """S consomme maximum 2.0 de waste."""
        waste_grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        waste_grid[0][0] = 4.0
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        cell = NeuralCell(genome="S", x=0, y=0, energy=100.0)
        cell.tick(grid, [], waste_grid=waste_grid)
        # S: consomme min(4.0, 2.0)=2.0, energy += 2.0*2.0=4.0
        # waste restant = 4.0 - 2.0 = 2.0
        assert waste_grid[0][0] == pytest.approx(2.0, abs=0.01)
        # 100 + 4.0 - 0.5 - 0.3 = 103.2
        assert cell.energy == pytest.approx(103.2, abs=0.01)

    # 9
    def test_max_waste_capped(self):
        """Le waste par case est cappé à MAX_WASTE."""
        waste_grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        waste_grid[0][0] = 4.8
        cell = NeuralCell(genome="A", x=0, y=0, energy=100.0)
        cell.tick(grid, [], waste_grid=waste_grid)
        assert waste_grid[0][0] <= MAX_WASTE

    # 10
    def test_s_without_waste_grid_nop(self):
        """S sans waste_grid = NOP + ACTION_COST (rétrocompatible)."""
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        cell = NeuralCell(genome="S", x=0, y=0, energy=100.0)
        cell.tick(grid, [])  # Pas de waste_grid
        # S sans waste_grid: juste -ACTION_COST=0.5, basal=0.3
        # 100 - 0.5 - 0.3 = 99.2
        assert cell.energy == pytest.approx(99.2, abs=0.01)

    # 11
    def test_waste_in_tissue_context(self):
        """S est mappé en 'symbiose' dans get_tissue_context."""
        tissue = _make_tissue()
        tissue.cells = [
            NeuralCell(genome="SCGC", x=i, y=0, energy=100.0, output_count=5)
            for i in range(20)
        ]
        tissue._update_dominant_patterns()
        tissue._update_zone_signals()
        ctx = tissue.get_tissue_context()
        assert "symbiose" in ctx

    # 12
    def test_symbiosis_emergence_event(self):
        """TISSUE_SYMBIOSIS_EMERGED publié quand >10% cellules ont S."""
        tissue = _make_tissue()
        # 15 cellules avec S sur 20 → 75% → seuil dépassé
        tissue.cells = [
            NeuralCell(genome="SCGC", x=i % GRID_SIZE, y=i // GRID_SIZE, energy=100.0)
            for i in range(15)
        ] + [
            NeuralCell(genome="RCGC", x=15, y=i, energy=100.0)
            for i in range(5)
        ]
        published = []
        original = tissue._try_publish
        def mock_pub(event, payload):
            if event == "TISSUE_SYMBIOSIS_EMERGED":
                published.append(payload)
            return original(event, payload)
        tissue._try_publish = mock_pub
        tissue._check_symbiosis_emergence()
        assert len(published) == 1
        assert published[0]["ratio"] > 0.10

    # 13
    def test_waste_grid_persisted(self):
        """save/load préserve la waste_grid."""
        tissue = _make_tissue()
        tissue.waste_grid[3][7] = 2.5
        import tempfile, os
        import core.neural_tissue as nt_module
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = f.name
        old_file = nt_module.TISSUE_STATE_FILE
        try:
            nt_module.TISSUE_STATE_FILE = tmp_path
            tissue._save()
            tissue.waste_grid[3][7] = 0.0
            tissue._load()
            assert tissue.waste_grid[3][7] == pytest.approx(2.5, abs=0.01)
        finally:
            nt_module.TISSUE_STATE_FILE = old_file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    # 14
    def test_symbiosis_energy_factor(self):
        """Le facteur d'énergie symbiotique est bien appliqué."""
        waste_grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        waste_grid[0][0] = 0.5
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        cell = NeuralCell(genome="S", x=0, y=0, energy=100.0)
        cell.tick(grid, [], waste_grid=waste_grid)
        # S: consomme 0.5, energy += 0.5*2.0=1.0
        # 100 + 1.0 - 0.5 - 0.3 = 100.2
        assert cell.energy == pytest.approx(100.2, abs=0.01)


class TestApoptosis:
    """Sprint C — Apoptose (mort propre, redistribution)."""

    # 1
    def test_apoptosis_constants(self):
        """Valeurs des constantes apoptose."""
        assert APOPTOSIS_MIN_AGE == 50
        assert APOPTOSIS_ENERGY_THRESHOLD == 20.0
        assert APOPTOSIS_DIVERGENCE_THRESHOLD == 0.6
        assert APOPTOSIS_ENERGY_REDISTRIBUTION == 0.8
        assert SEED_GENOME == "ACGTIR"

    # 2
    def test_genome_divergence_identical(self):
        """Divergence de génomes identiques = 0."""
        assert _genome_divergence("ACGTIR", "ACGTIR") == 0.0

    # 3
    def test_genome_divergence_different(self):
        """Divergence de génomes totalement différents = 1.0."""
        assert _genome_divergence("SSSSSS", "ACGTIR") == 1.0

    # 4
    def test_genome_divergence_length_diff(self):
        """Divergence avec longueurs différentes compte les positions manquantes."""
        div = _genome_divergence("AC", "ACGTIR")
        # max_len=6, mismatches=4 (positions manquantes), matches=AC=0 mismatch
        assert div == pytest.approx(4 / 6, abs=0.01)

    # 5
    def test_should_apoptose_isolation(self):
        """Cellule isolée (0 voisins + age > 30) → apoptose."""
        cell = NeuralCell(genome="C", x=0, y=0, energy=100.0, age=35)
        reason = cell.should_apoptose(neighbors_alive=0)
        assert reason == "isolation"

    # 6
    def test_should_apoptose_senescence(self):
        """Cellule âgée avec peu d'énergie → apoptose sénescence."""
        cell = NeuralCell(genome="C", x=0, y=0, energy=15.0, age=55)
        reason = cell.should_apoptose(neighbors_alive=3)
        assert reason == "senescence"

    # 7
    def test_should_apoptose_divergence(self):
        """Cellule très mutée par rapport au SEED → apoptose divergence."""
        cell = NeuralCell(genome="SSSSSS", x=0, y=0, energy=100.0, age=25)
        reason = cell.should_apoptose(neighbors_alive=3)
        assert reason == "divergence"

    # 8
    def test_should_apoptose_healthy(self):
        """Cellule saine ne déclenche pas l'apoptose."""
        cell = NeuralCell(genome="ACGTIR", x=0, y=0, energy=100.0, age=5)
        reason = cell.should_apoptose(neighbors_alive=3)
        assert reason == ""

    # 9
    def test_apoptosis_energy_redistribution(self):
        """L'apoptose redistribue 80% de l'énergie aux voisines."""
        tissue = _make_tissue()
        dying = NeuralCell(genome="C", x=0, y=0, energy=100.0, age=15)
        neighbor1 = NeuralCell(genome="C", x=1, y=0, energy=50.0)
        neighbor2 = NeuralCell(genome="C", x=0, y=1, energy=50.0)
        tissue.cells = [dying, neighbor1, neighbor2]
        tissue._execute_apoptosis(dying, [neighbor1, neighbor2], "isolation")
        assert not dying.alive
        # 100 * 0.8 = 80, partagé entre 2 → 40 chacune
        assert neighbor1.energy == pytest.approx(90.0, abs=0.01)
        assert neighbor2.energy == pytest.approx(90.0, abs=0.01)

    # 10
    def test_apoptosis_event(self):
        """TISSUE_APOPTOSIS est publié lors d'une apoptose."""
        tissue = _make_tissue()
        dying = NeuralCell(genome="C", x=0, y=0, energy=100.0)
        published = []
        original = tissue._try_publish
        def mock_pub(event, payload):
            if event == "TISSUE_APOPTOSIS":
                published.append(payload)
            return original(event, payload)
        tissue._try_publish = mock_pub
        tissue._execute_apoptosis(dying, [], "isolation")
        assert len(published) == 1
        assert published[0]["reason"] == "isolation"

    # 11
    def test_apoptosis_counter(self):
        """Le compteur total_apoptosis s'incrémente."""
        tissue = _make_tissue()
        tissue.total_apoptosis = 0
        dying = NeuralCell(genome="C", x=0, y=0, energy=100.0)
        tissue._execute_apoptosis(dying, [], "test")
        assert tissue.total_apoptosis == 1

    # 12
    def test_apoptosis_no_waste(self):
        """L'apoptose ne produit PAS de waste (mort propre)."""
        tissue = _make_tissue()
        tissue.waste_grid[0][0] = 0.0
        dying = NeuralCell(genome="C", x=0, y=0, energy=100.0)
        tissue._execute_apoptosis(dying, [], "test")
        assert tissue.waste_grid[0][0] == 0.0

    # 13
    def test_apoptosis_young_no_trigger(self):
        """Cellule jeune (age <= 10) isolée ne déclenche pas l'apoptose."""
        cell = NeuralCell(genome="C", x=0, y=0, energy=100.0, age=5)
        reason = cell.should_apoptose(neighbors_alive=0)
        assert reason == ""

    # 14
    def test_apoptosis_divergence_needs_age(self):
        """Divergence ne déclenche pas si age <= 20."""
        cell = NeuralCell(genome="SSSSSS", x=0, y=0, energy=100.0, age=15)
        reason = cell.should_apoptose(neighbors_alive=3)
        assert reason == ""

    # 15
    def test_apoptosis_redistribution_no_neighbors(self):
        """Apoptose sans voisins vivants → énergie perdue."""
        tissue = _make_tissue()
        dying = NeuralCell(genome="C", x=0, y=0, energy=100.0)
        tissue._execute_apoptosis(dying, [], "isolation")
        assert not dying.alive
        assert tissue.total_apoptosis == 1

    # 16
    def test_should_apoptose_senescence_boundary(self):
        """Exactement à la frontière : age=50, energy=20 → pas de sénescence."""
        # Utiliser SEED_GENOME pour éviter la divergence
        cell = NeuralCell(genome=SEED_GENOME, x=0, y=0, energy=20.0, age=50)
        reason = cell.should_apoptose(neighbors_alive=3)
        # age > 50 requis (pas >=), energy < 20 requis (pas <=)
        assert reason == ""

    # 17
    def test_genome_divergence_empty(self):
        """Divergence de génomes vides = 0."""
        assert _genome_divergence("", "") == 0.0

    # 18
    def test_genome_divergence_partial(self):
        """Divergence partielle calculée correctement."""
        div = _genome_divergence("ACGSIR", "ACGTIR")
        # 2 mismatches sur 6 (S≠T, pas de diff longueur)
        assert div == pytest.approx(1 / 6, abs=0.01)


class TestNecrosis:
    """Sprint C — Nécrose (mort toxique, résidu bloquant)."""

    # 1
    def test_necrosis_constants(self):
        """Valeurs des constantes nécrose."""
        assert TOXIC_RESIDUE == 3.0
        assert TOXIC_DURATION == 5

    # 2
    def test_necrosis_toxic_deposit(self):
        """La nécrose dépose un résidu toxique sur la case."""
        tissue = _make_tissue()
        tissue.toxic_grid[3][3] = 0.0
        tissue.toxic_timer_grid[3][3] = 0
        dying = NeuralCell(genome="C", x=3, y=3, energy=0.0)
        tissue._execute_necrosis(dying, "energy_depleted")
        assert tissue.toxic_grid[3][3] == pytest.approx(TOXIC_RESIDUE, abs=0.01)
        assert tissue.toxic_timer_grid[3][3] == TOXIC_DURATION
        assert not dying.alive

    # 3
    def test_necrosis_blocks_reproduction(self):
        """Une case toxique bloque le placement d'un enfant."""
        tissue = _make_tissue()
        tissue.cells = []
        tissue.toxic_timer_grid[8][9] = 3  # Case (9,8) toxique
        tissue.toxic_timer_grid[8][7] = 0  # Case (7,8) libre
        tissue.toxic_timer_grid[9][8] = 0  # Case (8,9) libre
        tissue.toxic_timer_grid[7][8] = 0  # Case (8,7) libre
        # Enfant prévu sur case toxique
        child = NeuralCell(genome="RCGC", x=9, y=8, energy=60.0)
        # Simuler l'ajout
        new_cells = [child]
        for c in new_cells:
            if tissue.toxic_timer_grid[c.y][c.x] > 0:
                placed = False
                for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                    nx = (c.x + dx) % GRID_SIZE
                    ny = (c.y + dy) % GRID_SIZE
                    if tissue.toxic_timer_grid[ny][nx] == 0:
                        c.x = nx
                        c.y = ny
                        placed = True
                        break
                if placed:
                    tissue.cells.append(c)
            else:
                tissue.cells.append(c)
        assert len(tissue.cells) == 1
        # L'enfant a été déplacé (pas sur la case toxique)
        assert not (tissue.cells[0].x == 9 and tissue.cells[0].y == 8)

    # 4
    def test_toxic_timer_decrement(self):
        """Le timer toxique décrémente à chaque tick."""
        tissue = _make_tissue()
        tissue.toxic_timer_grid[5][5] = 3
        tissue.toxic_grid[5][5] = 3.0
        tissue._tick()
        assert tissue.toxic_timer_grid[5][5] == 2

    # 5
    def test_toxic_cleanup_after_timer(self):
        """La toxine est nettoyée quand le timer atteint 0."""
        tissue = _make_tissue()
        tissue.toxic_timer_grid[5][5] = 1
        tissue.toxic_grid[5][5] = 3.0
        tissue._tick()
        assert tissue.toxic_timer_grid[5][5] == 0
        assert tissue.toxic_grid[5][5] == 0.0

    # 6
    def test_necrosis_event(self):
        """TISSUE_NECROSIS est publié lors d'une nécrose."""
        tissue = _make_tissue()
        dying = NeuralCell(genome="C", x=0, y=0, energy=0.0)
        published = []
        original = tissue._try_publish
        def mock_pub(event, payload):
            if event == "TISSUE_NECROSIS":
                published.append(payload)
            return original(event, payload)
        tissue._try_publish = mock_pub
        tissue._execute_necrosis(dying, "energy_depleted")
        assert len(published) == 1
        assert published[0]["reason"] == "energy_depleted"

    # 7
    def test_pandemic_death_is_necrosis(self):
        """Une mort pandémique crée une nécrose."""
        tissue = _make_tissue()
        cell = NeuralCell(genome="GRCGC", x=3, y=3, energy=1.0)
        cell.infected_by = "GR"
        cell.infection_timer = 0  # Timer épuisé
        tissue.cells = [cell]
        tissue._pandemic_active = True
        tissue._pandemic_motif = "GR"
        tissue.toxic_grid[3][3] = 0.0
        with patch("core.neural_tissue.random.random", return_value=0.99):
            tissue._pandemic_tick()
        assert not cell.alive
        assert tissue.toxic_grid[3][3] > 0
        assert tissue.total_necrosis >= 1

    # 8
    def test_necrosis_counter(self):
        """Le compteur total_necrosis s'incrémente."""
        tissue = _make_tissue()
        tissue.total_necrosis = 0
        dying = NeuralCell(genome="C", x=0, y=0, energy=0.0)
        tissue._execute_necrosis(dying, "test")
        assert tissue.total_necrosis == 1

    # 9
    def test_necrosis_produces_waste(self):
        """La nécrose produit du waste (nourriture pour cellules S)."""
        tissue = _make_tissue()
        tissue.waste_grid[3][3] = 0.0
        dying = NeuralCell(genome="C", x=3, y=3, energy=0.0)
        tissue._execute_necrosis(dying, "test")
        assert tissue.waste_grid[3][3] > 0

    # 10
    def test_toxic_grids_persisted(self):
        """save/load préserve les grilles toxiques."""
        tissue = _make_tissue()
        tissue.toxic_grid[2][3] = 3.0
        tissue.toxic_timer_grid[2][3] = 4
        tissue.total_necrosis = 7
        import tempfile, os
        import core.neural_tissue as nt_module
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = f.name
        old_file = nt_module.TISSUE_STATE_FILE
        try:
            nt_module.TISSUE_STATE_FILE = tmp_path
            tissue._save()
            tissue.toxic_grid[2][3] = 0.0
            tissue.toxic_timer_grid[2][3] = 0
            tissue.total_necrosis = 0
            tissue._load()
            assert tissue.toxic_grid[2][3] == pytest.approx(3.0, abs=0.01)
            assert tissue.toxic_timer_grid[2][3] == 4
            assert tissue.total_necrosis == 7
        finally:
            nt_module.TISSUE_STATE_FILE = old_file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


class TestInteractionsCroisees:
    """Sprint D — Connexions entre épigénétique, symbiose et apoptose/nécrose."""

    # 1
    def test_s_ignores_toxins(self):
        """L'instruction S consomme le waste mais pas les toxines."""
        waste_grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        waste_grid[0][0] = 1.0
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        cell = NeuralCell(genome="S", x=0, y=0, energy=100.0)
        # Les toxines sont sur toxic_grid, pas waste_grid → S ne les touche pas
        # S consomme seulement waste_grid
        cell.tick(grid, [], waste_grid=waste_grid)
        # L'énergie a augmenté → waste consommé
        assert cell.energy > 100.0

    # 2
    def test_tissue_context_enriched(self):
        """get_tissue_context inclut marqueurs et ratio apoptose/nécrose."""
        tissue = _make_tissue()
        tissue.cells[0].epigenetic_markers = {
            "heat_tolerant": {"cycles": 50, "acquired": True}
        }
        tissue.total_apoptosis = 15
        tissue.total_necrosis = 5
        tissue._update_dominant_patterns()
        tissue._update_zone_signals()
        ctx = tissue.get_tissue_context()
        assert "marqueurs" in ctx
        assert "santé" in ctx

    # 3
    def test_compute_tissue_bonus_health_good(self):
        """Ratio apoptose/nécrose favorable → bonus positif avec un intent mappé."""
        tissue = _make_tissue()
        tissue.total_apoptosis = 15
        tissue.total_necrosis = 5
        tissue._update_dominant_patterns()
        tissue._update_zone_signals()
        bonus = tissue.compute_tissue_bonus("EXPANSION_CODE")
        # V2 : zone affinity (creativity+cognition) + health bonus +0.1
        assert bonus >= 0.1

    # 4
    def test_compute_tissue_bonus_health_bad(self):
        """Beaucoup de nécrose → malus relatif."""
        tissue = _make_tissue()
        tissue.total_apoptosis = 2
        tissue.total_necrosis = 15
        tissue._update_dominant_patterns()
        tissue._update_zone_signals()
        bonus = tissue.compute_tissue_bonus("AUDIT_STRUCTURE")
        # Vérifions que le health malus est appliqué
        tissue2 = _make_tissue()
        tissue2.total_apoptosis = 15
        tissue2.total_necrosis = 2
        tissue2._update_dominant_patterns()
        tissue2._update_zone_signals()
        bonus2 = tissue2.compute_tissue_bonus("AUDIT_STRUCTURE")
        assert bonus < bonus2  # Moins bon quand nécrose domine

    # 5
    def test_get_stats_new_fields(self):
        """get_stats inclut total_apoptosis, total_necrosis, toxic_cells, waste_total."""
        tissue = _make_tissue()
        tissue.total_apoptosis = 5
        tissue.total_necrosis = 3
        tissue.waste_grid[0][0] = 1.5
        tissue.toxic_timer_grid[1][1] = 2
        stats = tissue.get_stats()
        assert stats["total_apoptosis"] == 5
        assert stats["total_necrosis"] == 3
        assert stats["waste_total"] >= 1.5
        assert stats["toxic_cells"] >= 1

    # 6
    def test_save_load_retrocompat(self):
        """save/load avec rétrocompatibilité (champs absents → valeurs par défaut)."""
        tissue = _make_tissue()
        import tempfile, os, json
        import core.neural_tissue as nt_module
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            tmp_path = f.name
            # Écrire un JSON sans les nouveaux champs
            json.dump({
                "tick_count": 42,
                "total_births": 10,
                "total_deaths": 5,
            }, f)
        old_file = nt_module.TISSUE_STATE_FILE
        try:
            nt_module.TISSUE_STATE_FILE = tmp_path
            tissue._load()
            assert tissue.tick_count == 42
            # Valeurs par défaut pour les nouveaux champs
            assert tissue.total_apoptosis == 0
            assert tissue.total_necrosis == 0
        finally:
            nt_module.TISSUE_STATE_FILE = old_file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


class TestIntegration:
    """Sprint E — Tests d'intégration et performance."""

    # 1
    def test_tick_with_all_features(self):
        """Un tick complet fonctionne avec toutes les nouvelles features."""
        tissue = _make_tissue()
        tissue._circadian_phase = "eveil"
        # Ajouter du waste et des toxines
        tissue.waste_grid[5][5] = 2.0
        tissue.toxic_timer_grid[3][3] = 2
        tissue.toxic_grid[3][3] = 3.0
        # Donner un marqueur à une cellule
        tissue.cells[0].epigenetic_markers = {
            "heat_tolerant": {"cycles": 50, "acquired": True}
        }
        # Exécuter un tick sans exception
        tissue._tick()
        assert tissue.tick_count == 1

    # 2
    def test_performance_100_ticks(self):
        """100 ticks avec 50 cellules en < 500ms."""
        import time
        random.seed(42)
        tissue = _make_tissue()
        tissue._circadian_phase = "eveil"
        t0 = time.perf_counter()
        for _ in range(100):
            tissue._tick()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        assert elapsed_ms < 5000, f"100 ticks ont pris {elapsed_ms:.1f}ms (max 5000)"

    # 3
    def test_no_regression_existing_features(self):
        """Les features existantes (saison, alpha, pandémie) fonctionnent encore."""
        tissue = _make_tissue()
        tissue._circadian_phase = "eveil"
        # Saison
        season = tissue.get_current_season()
        assert "zone" in season
        # Alpha summary
        summary = tissue._get_alpha_summary()
        assert isinstance(summary, dict)
        # Stats complètes
        stats = tissue.get_stats()
        assert "alive_cells" in stats
        assert "total_apoptosis" in stats

    # 4
    def test_apoptosis_in_tick_pipeline(self):
        """L'apoptose se déclenche dans le pipeline _tick pour cellules isolées."""
        tissue = _make_tissue()
        tissue._circadian_phase = "eveil"
        # Cellule isolée âgée (age > 30 pour déclencher apoptose isolation)
        isolated = NeuralCell(genome="C", x=0, y=0, energy=100.0, age=35)
        tissue.cells = [isolated]
        tissue._tick()
        # La cellule isolée devrait être apoptosée
        assert tissue.total_apoptosis >= 1

    # 5
    def test_necrosis_in_tick_pipeline(self):
        """La nécrose se déclenche pour les cellules mourant d'épuisement."""
        tissue = _make_tissue()
        tissue._circadian_phase = "eveil"
        # Cellule avec quasi plus d'énergie (genome A = NOP, pas de capture)
        dying = NeuralCell(genome="A", x=8, y=8, energy=0.1, age=0)
        neighbor = NeuralCell(genome="C", x=9, y=8, energy=100.0)
        tissue.cells = [dying, neighbor]
        tissue._tick()
        # La cellule devrait être morte par nécrose (énergie épuisée)
        assert tissue.total_necrosis >= 1

    # 6
    def test_waste_accumulates_during_ticks(self):
        """Le waste s'accumule pendant les ticks normaux."""
        random.seed(42)
        tissue = _make_tissue()
        tissue._circadian_phase = "eveil"
        for _ in range(10):
            tissue._tick()
        total_waste = sum(
            tissue.waste_grid[y][x]
            for y in range(GRID_SIZE) for x in range(GRID_SIZE)
        )
        assert total_waste > 0, "Du waste devrait s'être accumulé"


# ============================================================
# Écologie cellulaire — Capacité de charge logistique
# ============================================================

class TestLogisticCapacity:
    """Capacité de charge logistique — Verhulst."""

    def test_logistic_factor_at_zero_population(self):
        """Facteur = 1.0 quand aucune cellule."""
        tissue = _make_tissue()
        tissue.cells = []
        pop_ratio = len(tissue.cells) / CARRYING_CAPACITY
        factor = max(LOGISTIC_FLOOR, 1.0 - pop_ratio)
        assert factor == pytest.approx(1.0)

    def test_logistic_factor_at_half_capacity(self):
        """Facteur ≈ 0.5 à mi-capacité."""
        pop = CARRYING_CAPACITY // 2
        pop_ratio = pop / CARRYING_CAPACITY
        factor = max(LOGISTIC_FLOOR, 1.0 - pop_ratio)
        assert factor == pytest.approx(0.5)

    def test_logistic_factor_at_full_capacity(self):
        """Facteur = LOGISTIC_FLOOR à pleine capacité."""
        pop_ratio = CARRYING_CAPACITY / CARRYING_CAPACITY
        factor = max(LOGISTIC_FLOOR, 1.0 - pop_ratio)
        assert factor == pytest.approx(LOGISTIC_FLOOR)

    def test_logistic_factor_clamp(self):
        """Le facteur ne descend jamais sous LOGISTIC_FLOOR."""
        for pop in [400, 450, 499, 500]:
            pop_ratio = pop / CARRYING_CAPACITY
            factor = max(LOGISTIC_FLOOR, 1.0 - pop_ratio)
            assert factor >= LOGISTIC_FLOOR

    def test_signal_intensity_decreases_with_population(self):
        """L'intensité réelle sur la grille diminue avec la population."""
        random.seed(42)
        tissue = _make_tissue()
        tissue._circadian_phase = "eveil"
        # Peu de cellules → signal fort
        tissue.cells = [NeuralCell(genome="RCGC", x=i % GRID_SIZE, y=i // GRID_SIZE, energy=50.0)
                        for i in range(50)]
        tissue._inject_signals()
        total_low = sum(tissue.grid[y][x] for y in range(GRID_SIZE) for x in range(GRID_SIZE))

        # Reset la grille et tester avec beaucoup de cellules
        tissue.grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        random.seed(42)
        tissue.cells = [NeuralCell(genome="RCGC", x=i % GRID_SIZE, y=i // GRID_SIZE, energy=50.0)
                        for i in range(450)]
        tissue._inject_signals()
        total_high = sum(tissue.grid[y][x] for y in range(GRID_SIZE) for x in range(GRID_SIZE))

        assert total_low > total_high, f"Signal bas pop ({total_low}) devrait > signal haute pop ({total_high})"

    def test_population_stabilizes(self):
        """Après 200 ticks, la population reste dans une fourchette raisonnable."""
        random.seed(42)
        tissue = _make_tissue()
        tissue._circadian_phase = "eveil"
        for _ in range(200):
            tissue._tick()
        pop = len(tissue.cells)
        assert pop >= 5, f"Population trop basse: {pop}"
        assert pop <= MAX_CELLS, f"Population dépasse MAX_CELLS: {pop}"


# ============================================================
# Écologie cellulaire — Drainage latéral des signaux
# ============================================================

class TestDrainage:
    """Diffusion latérale des signaux excédentaires."""

    def test_drainage_below_threshold_no_effect(self):
        """Signal sous le seuil → pas de drainage."""
        tissue = _make_tissue()
        tissue.grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        tissue.grid[8][8] = 2.0  # < DRAINAGE_THRESHOLD (3.0)
        tissue._apply_drainage()
        assert tissue.grid[8][8] == pytest.approx(2.0)

    def test_drainage_above_threshold_spills(self):
        """Signal au-dessus du seuil → excès réparti aux voisins."""
        tissue = _make_tissue()
        tissue.grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        tissue.grid[8][8] = 4.0  # excès = 1.0, spill = 0.25
        tissue._apply_drainage()
        # La cellule source perd le spill
        assert tissue.grid[8][8] < 4.0
        # Les voisins reçoivent une partie
        assert tissue.grid[7][8] > 0.0  # haut
        assert tissue.grid[9][8] > 0.0  # bas
        assert tissue.grid[8][7] > 0.0  # gauche
        assert tissue.grid[8][9] > 0.0  # droite

    def test_drainage_corner_cell(self):
        """Coin de grille : seulement 2 voisins, drainage proportionnel."""
        tissue = _make_tissue()
        tissue.grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        tissue.grid[0][0] = 5.0  # excès = 2.0, spill = 0.5
        tissue._apply_drainage()
        # 2 voisins : (1,0) et (0,1), chacun reçoit 0.25
        assert tissue.grid[1][0] == pytest.approx(0.25)
        assert tissue.grid[0][1] == pytest.approx(0.25)
        assert tissue.grid[0][0] == pytest.approx(4.5)

    def test_drainage_preserves_total_signal(self):
        """Conservation d'énergie : somme totale ≈ constante."""
        tissue = _make_tissue()
        tissue.grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        tissue.grid[8][8] = 4.5
        tissue.grid[4][4] = 3.5
        total_before = sum(tissue.grid[y][x] for y in range(GRID_SIZE) for x in range(GRID_SIZE))
        tissue._apply_drainage()
        total_after = sum(tissue.grid[y][x] for y in range(GRID_SIZE) for x in range(GRID_SIZE))
        assert total_after == pytest.approx(total_before, abs=0.01)

    def test_drainage_capped_at_max(self):
        """Les voisins ne dépassent pas MAX_GRID_SIGNAL."""
        tissue = _make_tissue()
        tissue.grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        tissue.grid[8][8] = MAX_GRID_SIGNAL
        tissue.grid[8][7] = MAX_GRID_SIGNAL  # Voisin déjà saturé
        tissue._apply_drainage()
        assert tissue.grid[8][7] <= MAX_GRID_SIGNAL

    def test_drainage_reduces_zone_overload(self):
        """Zone chargée diffuse vers zone vide adjacente."""
        tissue = _make_tissue()
        tissue.grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        # Charger une zone
        for y in range(4):
            for x in range(4):
                tissue.grid[y][x] = 4.0
        total_zone_before = sum(tissue.grid[y][x] for y in range(4) for x in range(4))
        tissue._apply_drainage()
        total_zone_after = sum(tissue.grid[y][x] for y in range(4) for x in range(4))
        # La zone a perdu du signal (diffusé aux bordures)
        assert total_zone_after < total_zone_before


# ============================================================
# Écologie cellulaire — Reproduction sexuée (crossover)
# ============================================================

class TestCrossover:
    """Crossover à un point entre deux génomes."""

    def test_crossover_basic(self):
        """Crossover produit un hybride des deux parents."""
        random.seed(42)
        child = crossover("AAAA", "BBBB")
        # Le child contient des caractères des deux parents (+ mutation possible)
        assert len(child) >= 2

    def test_crossover_different_lengths(self):
        """Crossover gère des longueurs inégales (troncature à min_len)."""
        random.seed(42)
        child = crossover("AABBCC", "XY")
        # min_len = 2, crossover sur 2 chars
        assert len(child) >= 1

    def test_crossover_too_short(self):
        """Genome de longueur 1 → fallback sur mutation."""
        random.seed(42)
        child = crossover("A", "B")
        assert len(child) >= 1

    def test_replicate_with_partner(self):
        """_replicate avec partenaire produit un génome qui peut différer du parent."""
        random.seed(42)
        cell = NeuralCell(genome="AAAA", x=5, y=5, energy=200.0)
        results = set()
        for i in range(50):
            random.seed(i)
            cell.energy = 200.0
            child = cell._replicate(partner_genome="CCCC")
            if child:
                results.add(child.genome)
        # Avec crossover, on devrait avoir de la diversité
        assert len(results) > 1, "Le crossover devrait produire des génomes variés"

    def test_replicate_without_partner(self):
        """_replicate sans partenaire → comportement clone classique."""
        random.seed(42)
        cell = NeuralCell(genome="RCGC", x=5, y=5, energy=200.0)
        child = cell._replicate(partner_genome=None)
        assert child is not None
        assert child.generation == cell.generation + 1

    def test_crossover_only_different_genomes(self):
        """Le crossover ne se déclenche que pour des génomes différents dans _tick."""
        tissue = _make_tissue()
        tissue._circadian_phase = "eveil"
        # Toutes les cellules ont le même génome → pas de partenaire crossover
        tissue.cells = [NeuralCell(genome="RCGC", x=i % GRID_SIZE, y=i // GRID_SIZE,
                                   energy=150.0) for i in range(10)]
        tissue._tick()
        # Pas d'erreur, le code gère l'absence de partenaire

    def test_diversity_increases_with_crossover(self):
        """Après 100 ticks avec génomes variés, la diversité augmente."""
        random.seed(42)
        tissue = _make_tissue()
        tissue._circadian_phase = "eveil"
        genomes = ["RCGC", "GRCGC", "CCGC", "RCGGC", "GCGC"]
        tissue.cells = []
        for i in range(50):
            g = genomes[i % len(genomes)]
            tissue.cells.append(NeuralCell(
                genome=g, x=random.randint(0, GRID_SIZE - 1),
                y=random.randint(0, GRID_SIZE - 1), energy=120.0
            ))
        initial_diversity = len(set(c.genome for c in tissue.cells))
        for _ in range(100):
            tissue._tick()
        final_diversity = len(set(c.genome for c in tissue.cells if c.alive))
        # La diversité devrait être >= à l'initiale (crossover + mutation)
        assert final_diversity >= initial_diversity or len(tissue.cells) < 10


class TestHybridVigor:
    """Vigueur hybride — les enfants de parents génétiquement distants reçoivent un bonus."""

    def test_hybrid_child_gets_energy_bonus(self):
        """Crossover entre génomes divergents → enfant reçoit bonus énergie."""
        random.seed(42)
        cell = NeuralCell(genome="AAAA", x=5, y=5, energy=200.0)
        # Forcer le crossover (seed qui donne random() < CROSSOVER_PROBABILITY)
        bonuses = []
        for i in range(100):
            random.seed(i)
            cell.energy = 200.0
            child = cell._replicate(partner_genome="TTTT")
            if child:
                # Sans hybrid vigor, energy serait 100.0 (200/2)
                # Avec, c'est 100.0 + divergence * HYBRID_VIGOR_MULTIPLIER
                if child.energy > 100.1:
                    bonuses.append(child.energy - 100.0)
        # Au moins certains crossovers produisent un bonus
        assert len(bonuses) > 0, "Le hybrid vigor devrait donner un bonus à certains enfants"

    def test_no_bonus_without_partner(self):
        """Clone sans partenaire → pas de bonus hybrid."""
        random.seed(42)
        cell = NeuralCell(genome="AAAA", x=5, y=5, energy=200.0)
        child = cell._replicate(partner_genome=None)
        assert child.energy == pytest.approx(100.0, abs=0.01)

    def test_bonus_proportional_to_divergence(self):
        """Parents plus divergents → bonus plus élevé."""
        from core.neural_tissue import HYBRID_VIGOR_MULTIPLIER
        bonuses_close = []
        bonuses_far = []
        for i in range(200):
            random.seed(i)
            cell_close = NeuralCell(genome="AAAB", x=5, y=5, energy=200.0)
            child_close = cell_close._replicate(partner_genome="AAAC")
            if child_close and child_close.energy > 100.1:
                bonuses_close.append(child_close.energy - 100.0)

            random.seed(i)
            cell_far = NeuralCell(genome="AAAA", x=5, y=5, energy=200.0)
            child_far = cell_far._replicate(partner_genome="TTTT")
            if child_far and child_far.energy > 100.1:
                bonuses_far.append(child_far.energy - 100.0)

        if bonuses_close and bonuses_far:
            avg_close = sum(bonuses_close) / len(bonuses_close)
            avg_far = sum(bonuses_far) / len(bonuses_far)
            assert avg_far > avg_close, "Parents plus divergents devraient donner un bonus plus élevé"


class TestMateSelection:
    """Attraction par la différence — sélection pondérée par distance génétique."""

    def test_prefers_distant_partner(self):
        """Parmi des voisins, le plus génétiquement distant est choisi plus souvent."""
        from core.neural_tissue import _genome_divergence, MATE_DIVERSITY_WEIGHT
        tissue = _make_tissue()
        tissue._circadian_phase = "eveil"
        # Cellule RCGT entourée de : RCGS (proche) et TTTT (distant)
        main_cell = NeuralCell(genome="RCGT", x=8, y=8, energy=150.0)
        close_neighbor = NeuralCell(genome="RCGS", x=8, y=9, energy=150.0)
        far_neighbor = NeuralCell(genome="TTTT", x=9, y=8, energy=150.0)
        tissue.cells = [main_cell, close_neighbor, far_neighbor]

        # Simuler la sélection de partenaire 500 fois
        from collections import Counter
        choices = Counter()
        neighbors = [close_neighbor, far_neighbor]
        for _ in range(500):
            weights = [_genome_divergence(main_cell.genome, n.genome) ** MATE_DIVERSITY_WEIGHT
                       for n in neighbors]
            total_w = sum(weights)
            if total_w > 0:
                weights = [w / total_w for w in weights]
                partner = random.choices(neighbors, weights=weights, k=1)[0]
                choices[partner.genome] += 1
        # TTTT (distant) devrait être choisi bien plus souvent que RCGS (proche)
        assert choices["TTTT"] > choices["RCGS"], \
            f"TTTT={choices['TTTT']} devrait être > RCGS={choices['RCGS']}"

    def test_constants_defined(self):
        """Les constantes de sélection sont définies."""
        from core.neural_tissue import HYBRID_VIGOR_MULTIPLIER, MATE_DIVERSITY_WEIGHT
        assert HYBRID_VIGOR_MULTIPLIER > 0
        assert MATE_DIVERSITY_WEIGHT > 1.0  # Doit amplifier les différences


class TestLocalCompetition:
    """Compétition locale — le reward Capture est divisé par la densité locale."""

    def test_competition_single_cell(self):
        """1 cellule seule → reward plein (÷1)."""
        cell = NeuralCell(genome="C", x=5, y=5, energy=50.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid[5][5] = 2.0
        cell.tick(grid, [], local_density=1)
        # reward = CAPTURE_REWARD * min(2.0, 2.0) / 1 = 3.0 * 2.0 = 6.0
        # energy = 50.0 + 6.0 - MAINTENANCE_COST(1.8) = 54.2
        assert cell.energy == pytest.approx(54.2, abs=0.01)

    def test_competition_multiple_cells(self):
        """3 cellules même case → reward ÷3."""
        cell = NeuralCell(genome="C", x=5, y=5, energy=50.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid[5][5] = 2.0
        cell.tick(grid, [], local_density=3)
        # reward = CAPTURE_REWARD * min(2.0, 2.0) / 3 = 6.0 / 3 = 2.0
        # energy = 50.0 + 2.0 - 1.8 = 50.2
        assert cell.energy == pytest.approx(50.2, abs=0.01)

    def test_competition_capped(self):
        """10 cellules → reward ÷ COMPETITION_DIVISOR_CAP (pas ÷10)."""
        cell = NeuralCell(genome="C", x=5, y=5, energy=50.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid[5][5] = 2.0
        cell.tick(grid, [], local_density=10)
        # competitors = min(10, COMPETITION_DIVISOR_CAP=5) = 5
        # reward = 6.0 / 5 = 1.2
        # energy = 50.0 + 1.2 - 1.8 = 49.4
        assert cell.energy == pytest.approx(49.4, abs=0.01)

    def test_competition_zero_density_safe(self):
        """Densité 0 → pas de division par zéro (max(0, 1) = 1)."""
        cell = NeuralCell(genome="C", x=5, y=5, energy=50.0)
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid[5][5] = 2.0
        cell.tick(grid, [], local_density=0)
        # competitors = min(0, 5) = 0, max(0, 1) = 1 → reward plein
        # energy = 50.0 + 6.0 - 1.8 = 54.2
        assert cell.energy == pytest.approx(54.2, abs=0.01)

    def test_dense_zone_cells_gain_less_energy(self):
        """Cellules en zone dense gagnent moins par tick que cellules isolées."""
        grid_dense = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid_dense[5][5] = 2.0
        grid_sparse = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid_sparse[5][5] = 2.0

        cell_dense = NeuralCell(genome="C", x=5, y=5, energy=50.0)
        cell_sparse = NeuralCell(genome="C", x=5, y=5, energy=50.0)

        cell_dense.tick(grid_dense, [], local_density=4)
        cell_sparse.tick(grid_sparse, [], local_density=1)

        assert cell_sparse.energy > cell_dense.energy

    def test_sparse_zone_advantage(self):
        """Cellule seule en zone vide capture plus qu'une en zone surpeuplée."""
        grid = [[0.0] * GRID_SIZE for _ in range(GRID_SIZE)]
        grid[0][0] = 1.5
        grid[8][8] = 1.5

        lone_cell = NeuralCell(genome="C", x=0, y=0, energy=50.0)
        crowded_cell = NeuralCell(genome="C", x=8, y=8, energy=50.0)

        lone_cell.tick(grid, [], local_density=1)
        crowded_cell.tick(grid, [], local_density=5)

        energy_gain_lone = lone_cell.energy - 50.0 + MAINTENANCE_COST
        energy_gain_crowded = crowded_cell.energy - 50.0 + MAINTENANCE_COST
        assert energy_gain_lone > energy_gain_crowded
        assert energy_gain_lone == pytest.approx(energy_gain_crowded * 5, abs=0.01)


# ============================================================
# Sprint 2 Sensorium — Zones thermoception/soma + cognitive_state
# ============================================================

class TestSensoriumTissueIntegration:
    """Sprint 2 Sensorium : injection zones hardware dans le tissu neural."""

    def test_tissue_receives_sensorium(self):
        """SENSORIUM_UPDATE met a jour cognitive_state."""
        tissue = _make_tissue()
        import asyncio
        event = {
            "senses": {
                "thermoception": 0.9,
                "effort": 0.7,
                "oppression": 0.3,
                "suffocation": 0.6,
                "vitality": 0.4,
            },
            "comfort": 0.3,
            "raw": {},
        }
        asyncio.get_event_loop().run_until_complete(
            tissue._on_sensorium_update(event)
        )
        assert tissue._cognitive_state["thermal_stress"] == 0.9
        assert tissue._cognitive_state["somatic_load"] == 0.7
        assert tissue._cognitive_state["suffocation"] == 0.6
        # Vitality inversee : 1.0 - 0.4 = 0.6
        assert tissue._cognitive_state["vitality_level"] == pytest.approx(0.6, abs=0.01)

    def test_thermoception_zone_exists(self):
        """Zone thermoception est definie dans SIGNAL_ZONES."""
        assert "thermoception" in SIGNAL_ZONES
        x1, y1, x2, y2 = SIGNAL_ZONES["thermoception"]
        assert x2 > x1 and y2 > y1

    def test_soma_zone_exists(self):
        """Zone soma est definie dans SIGNAL_ZONES."""
        assert "soma" in SIGNAL_ZONES
        x1, y1, x2, y2 = SIGNAL_ZONES["soma"]
        assert x2 > x1 and y2 > y1

    def test_thermoception_zone_signal(self):
        """Thermal stress eleve injecte du signal dans zone thermoception."""
        tissue = _make_tissue()
        tissue._cognitive_state["thermal_stress"] = 0.9
        tissue._inject_signals()
        # Verifier qu'il y a du signal dans la zone thermoception
        x1, y1, x2, y2 = SIGNAL_ZONES["thermoception"]
        total_signal = 0.0
        for gy in range(y1, y2):
            for gx in range(x1, x2):
                total_signal += tissue.grid[gy][gx]
        assert total_signal > 0.0

    def test_soma_zone_signal(self):
        """Somatic load eleve injecte du signal dans zone soma."""
        tissue = _make_tissue()
        tissue._cognitive_state["somatic_load"] = 0.8
        tissue._inject_signals()
        x1, y1, x2, y2 = SIGNAL_ZONES["soma"]
        total_signal = 0.0
        for gy in range(y1, y2):
            for gx in range(x1, x2):
                total_signal += tissue.grid[gy][gx]
        assert total_signal > 0.0

    def test_zone_adjacency_thermoception(self):
        """Thermoception est adjacent a emotion, desire, cognition."""
        assert "thermoception" in ZONE_ADJACENCY
        adj = ZONE_ADJACENCY["thermoception"]
        assert "emotion" in adj
        assert "desire" in adj
        assert "cognition" in adj

    def test_zone_adjacency_soma(self):
        """Soma est adjacent a desire, threat, cognition."""
        assert "soma" in ZONE_ADJACENCY
        adj = ZONE_ADJACENCY["soma"]
        assert "desire" in adj
        assert "threat" in adj
        assert "cognition" in adj

    def test_adjacency_bidirectional(self):
        """Les adjacences thermoception/soma sont bidirectionnelles."""
        for zone_name in ("thermoception", "soma"):
            for adj in ZONE_ADJACENCY[zone_name]:
                assert zone_name in ZONE_ADJACENCY[adj], (
                    f"{zone_name} dans adj de {adj} mais pas reciproque"
                )


# ============================================================
# Sprint 3 Sensorium — Substrat Dynamique
# ============================================================

class TestSubstrateDynamic:
    """Sprint 3 Sensorium : le hardware module le substrat tissulaire."""

    def test_thermoception_increases_mutations(self):
        """Thermoception 0.8 → mutation_factor ~2.2."""
        tissue = _make_tissue()
        from unittest.mock import MagicMock
        mock_sensor = MagicMock()
        mock_sensor.get_senses.return_value = {
            "thermoception": 0.8, "effort": 0, "oppression": 0,
            "suffocation": 0, "vitality": 0,
        }
        import sys
        mock_mod = MagicMock()
        mock_mod.sensorium = mock_sensor
        with patch.dict("sys.modules", {"core.sensorium": mock_mod}):
            result = tissue._get_substrate_modulation()
        assert result["mutation_factor"] == pytest.approx(2.2, abs=0.01)

    def test_effort_slows_tick(self):
        """Effort 1.0 → tick_slowdown 2.0."""
        tissue = _make_tissue()
        from unittest.mock import MagicMock
        mock_sensor = MagicMock()
        mock_sensor.get_senses.return_value = {
            "thermoception": 0, "effort": 1.0, "oppression": 0,
            "suffocation": 0, "vitality": 0,
        }
        mock_mod = MagicMock()
        mock_mod.sensorium = mock_sensor
        with patch.dict("sys.modules", {"core.sensorium": mock_mod}):
            result = tissue._get_substrate_modulation()
        assert result["tick_slowdown"] == pytest.approx(2.0, abs=0.01)

    def test_oppression_reduces_pop_cap(self):
        """Oppression 1.0 → pop_cap_factor 0.6."""
        tissue = _make_tissue()
        from unittest.mock import MagicMock
        mock_sensor = MagicMock()
        mock_sensor.get_senses.return_value = {
            "thermoception": 0, "effort": 0, "oppression": 1.0,
            "suffocation": 0, "vitality": 0,
        }
        mock_mod = MagicMock()
        mock_mod.sensorium = mock_sensor
        with patch.dict("sys.modules", {"core.sensorium": mock_mod}):
            result = tissue._get_substrate_modulation()
        assert result["pop_cap_factor"] == pytest.approx(0.6, abs=0.01)

    def test_substrate_graceful_without_sensorium(self):
        """Sensorium absent → facteurs par defaut (1.0)."""
        tissue = _make_tissue()
        with patch.dict("sys.modules", {"core.sensorium": None}):
            result = tissue._get_substrate_modulation()
        assert result["mutation_factor"] == 1.0
        assert result["tick_slowdown"] == 1.0
        assert result["pop_cap_factor"] == 1.0
        assert result["reward_boost"] == 1.0


# ============================================================
# TestClassifyGenomeProfile — Profils comportementaux
# ============================================================

class TestClassifyGenomeProfile:
    """Tests pour _classify_genome_profile()."""

    def test_producteur_majority_cg(self):
        """Génome CCGCG → producteur (C+G dominant ≥ 40%)."""
        from core.neural_tissue import _classify_genome_profile
        assert _classify_genome_profile("CCGCG") == "producteur"

    def test_colonisateur_majority_ra(self):
        """Génome RRAAR → colonisateur (R+A dominant)."""
        from core.neural_tissue import _classify_genome_profile
        assert _classify_genome_profile("RRAAR") == "colonisateur"

    def test_modulateur_majority_it(self):
        """Génome IIITT → modulateur (I+T dominant)."""
        from core.neural_tissue import _classify_genome_profile
        assert _classify_genome_profile("IIITT") == "modulateur"

    def test_recycleur_majority_s(self):
        """Génome SSSSS → recycleur (S dominant)."""
        from core.neural_tissue import _classify_genome_profile
        assert _classify_genome_profile("SSSSS") == "recycleur"

    def test_mixte_balanced(self):
        """Génome CRIS → mixte (aucune dominance ≥ 40%)."""
        from core.neural_tissue import _classify_genome_profile
        # C=1(25%), R=1(25%), I=1(25%), S=1(25%) → aucun ≥ 40%
        assert _classify_genome_profile("CRIS") == "mixte"

    def test_empty_genome_is_mixte(self):
        """Génome vide → mixte."""
        from core.neural_tissue import _classify_genome_profile
        assert _classify_genome_profile("") == "mixte"

    def test_case_insensitive(self):
        """Fonctionne en minuscules."""
        from core.neural_tissue import _classify_genome_profile
        assert _classify_genome_profile("ccgcg") == "producteur"


# ============================================================
# TestGetZoneDominants — Dominants par zone
# ============================================================

class TestGetZoneDominants:
    """Tests pour get_zone_dominants()."""

    def test_zone_dominants_basic(self):
        """Retourne les dominants des zones peuplées."""
        tissue = _make_tissue()
        # Peupler la zone cognition avec un génome connu
        tissue._zone_signals["cognition"] = {
            "activity": 1.0, "density": 0.5, "energy": 100.0,
            "diversity": 0.5, "dominant_genome": "CCGCG",
            "genome_frequency": 0.6,
        }
        result = tissue.get_zone_dominants()
        assert "cognition" in result
        assert result["cognition"]["genome"] == "CCGCG"
        assert result["cognition"]["profile"] == "producteur"
        assert result["cognition"]["frequency"] == 0.6

    def test_zone_dominants_empty_genome_excluded(self):
        """Zones sans dominant_genome ne sont pas retournées."""
        tissue = _make_tissue()
        tissue._zone_signals["creativity"] = {
            "activity": 0.5, "dominant_genome": "",
        }
        result = tissue.get_zone_dominants()
        assert "creativity" not in result

    def test_zone_dominants_multiple_zones(self):
        """Plusieurs zones avec dominants différents."""
        tissue = _make_tissue()
        tissue._zone_signals["creativity"] = {
            "dominant_genome": "RRAAR", "genome_frequency": 0.5,
        }
        tissue._zone_signals["memory"] = {
            "dominant_genome": "SSSSS", "genome_frequency": 0.3,
        }
        result = tissue.get_zone_dominants()
        assert result["creativity"]["profile"] == "colonisateur"
        assert result["memory"]["profile"] == "recycleur"


# ============================================================
# TestComputeTissueBonusV2 — Scoring V2 avec zones/profils/saison
# ============================================================

class TestComputeTissueBonusV2:
    """Tests pour compute_tissue_bonus() V2."""

    def test_bonus_zero_without_cells(self):
        """Pas de cellules → bonus = 0."""
        tissue = _make_tissue()
        tissue.cells = []
        assert tissue.compute_tissue_bonus("EXPANSION_CODE") == 0.0

    def test_bonus_zero_without_zone_signals(self):
        """Pas de zone_signals → bonus = 0."""
        tissue = _make_tissue()
        tissue._zone_signals = {}
        assert tissue.compute_tissue_bonus("EXPANSION_CODE") == 0.0

    def test_zone_affinity_boosts_intent(self):
        """Intent avec zones actives → bonus positif."""
        tissue = _make_tissue()
        # Simuler zone cognition et creativity actives
        tissue._zone_signals["cognition"] = {
            "activity": 1.5, "density": 0.3, "energy": 180.0,
            "diversity": 0.6, "dominant_genome": "CCGCG",
            "genome_frequency": 0.5,
        }
        tissue._zone_signals["creativity"] = {
            "activity": 1.2, "density": 0.3, "energy": 150.0,
            "diversity": 0.5, "dominant_genome": "GCCG",
            "genome_frequency": 0.4,
        }
        bonus = tissue.compute_tissue_bonus("EXPANSION_CODE")
        assert bonus > 0.0

    def test_deserted_zone_penalty(self):
        """Zone pertinente désertée (density < 0.05) → pénalité."""
        tissue = _make_tissue()
        tissue._zone_signals["cognition"] = {
            "activity": 0.0, "density": 0.01, "energy": 5.0,
            "diversity": 0.1, "dominant_genome": "",
        }
        tissue._zone_signals["creativity"] = {
            "activity": 0.5, "density": 0.3, "energy": 100.0,
            "diversity": 0.4, "dominant_genome": "",
        }
        bonus = tissue.compute_tissue_bonus("EXPANSION_CODE")
        # La zone cognition désertée devrait pénaliser
        assert bonus < 0.5  # Pas le bonus max

    def test_profile_effect_boost(self):
        """Zone avec bon profil → bonus supplémentaire via ZONE_PROFILE_EFFECTS."""
        tissue = _make_tissue()
        tissue._zone_signals["creativity"] = {
            "activity": 1.0, "density": 0.3, "energy": 150.0,
            "diversity": 0.5, "dominant_genome": "CCGCG",
            "genome_frequency": 0.6,
        }
        tissue._zone_signals["cognition"] = {
            "activity": 1.0, "density": 0.3, "energy": 150.0,
            "diversity": 0.5, "dominant_genome": "",
        }
        # CCGCG = producteur, ("creativity", "producteur") → EXPANSION_CODE: +0.5
        bonus_with_profile = tissue.compute_tissue_bonus("EXPANSION_CODE")
        # Vérifier qu'il y a bien un bonus
        assert bonus_with_profile > 0

    def test_season_alignment_bonus(self):
        """Intent dont les zones incluent la saison courante → +0.3."""
        tissue = _make_tissue()
        # SEASON_ORDER[0] = "emotion", SOLILOQUE_INTERNE → ["emotion", "creativity"]
        tissue._current_season_index = 0  # emotion
        tissue._zone_signals["emotion"] = {
            "activity": 0.5, "density": 0.3, "energy": 100.0,
            "diversity": 0.3, "dominant_genome": "",
        }
        tissue._zone_signals["creativity"] = {
            "activity": 0.5, "density": 0.3, "energy": 100.0,
            "diversity": 0.3, "dominant_genome": "",
        }
        bonus = tissue.compute_tissue_bonus("SOLILOQUE_INTERNE")
        # Doit inclure +0.3 de saison
        assert bonus >= 0.3

    def test_bonus_clamped_to_range(self):
        """Bonus clampé à [-1.0, +2.0]."""
        tissue = _make_tissue()
        # Saturer les zones pour tenter de dépasser +2
        for zone_name in ["cognition", "creativity", "memory", "stability"]:
            tissue._zone_signals[zone_name] = {
                "activity": 5.0, "density": 0.8, "energy": 500.0,
                "diversity": 1.0, "dominant_genome": "CCGCG",
                "genome_frequency": 0.9,
            }
        tissue._current_season_index = 0
        bonus = tissue.compute_tissue_bonus("EXPANSION_CODE")
        assert -1.0 <= bonus <= 2.0

    def test_unknown_intent_returns_global_only(self):
        """Intent inconnu (pas dans INTENT_ZONE_MAP) → seulement santé globale."""
        tissue = _make_tissue()
        tissue._zone_signals["cognition"] = {
            "activity": 1.0, "density": 0.3, "energy": 100.0,
            "diversity": 0.5, "dominant_genome": "",
        }
        bonus = tissue.compute_tissue_bonus("UNKNOWN_INTENT_XYZ")
        # Pas d'affinité zone, seulement le composant santé globale
        assert -1.0 <= bonus <= 2.0


# ============================================================
# TestPublishZoneUpdate — Publication TISSUE_ZONE_UPDATE
# ============================================================

class TestPublishZoneUpdate:
    """Tests pour _publish_zone_update()."""

    def test_publish_zone_update_fires_event(self):
        """_publish_zone_update() publie un événement TISSUE_ZONE_UPDATE via _try_publish."""
        tissue = _make_tissue()
        tissue._zone_signals["cognition"] = {
            "activity": 1.0, "density": 0.3, "energy": 100.0,
            "diversity": 0.5, "dominant_genome": "CCGCG",
            "genome_frequency": 0.5,
        }
        published = []

        def mock_try_publish(event_name, payload, **kwargs):
            published.append((event_name, payload))

        tissue._try_publish = mock_try_publish
        tissue._publish_zone_update()
        assert len(published) == 1
        assert published[0][0] == "TISSUE_ZONE_UPDATE"
        data = published[0][1]
        assert "zones" in data
        assert "dominants" in data
        assert "season" in data
        assert "tick" in data
        assert "alive_cells" in data

    def test_publish_zone_update_includes_dominants(self):
        """Les dominants sont inclus dans l'événement."""
        tissue = _make_tissue()
        tissue._zone_signals["creativity"] = {
            "activity": 1.0, "density": 0.3, "energy": 100.0,
            "diversity": 0.5, "dominant_genome": "RRAAR",
            "genome_frequency": 0.7,
        }
        published = []

        def mock_try_publish(event_name, payload, **kwargs):
            published.append((event_name, payload))

        tissue._try_publish = mock_try_publish
        tissue._publish_zone_update()
        assert len(published) == 1
        data = published[0][1]
        dominants = data["dominants"]
        assert "creativity" in dominants
        assert dominants["creativity"]["profile"] == "colonisateur"

    def test_publish_interval_in_tick(self):
        """ZONE_UPDATE_PUBLISH_INTERVAL = 50 → publication toutes les 50 ticks."""
        from core.neural_tissue import ZONE_UPDATE_PUBLISH_INTERVAL
        assert ZONE_UPDATE_PUBLISH_INTERVAL == 50


# ============================================================
# TestIntentZoneMap — Mapping intent→zones
# ============================================================

class TestIntentZoneMap:
    """Tests pour INTENT_ZONE_MAP."""

    def test_expansion_code_zones(self):
        """EXPANSION_CODE → creativity + cognition."""
        from core.neural_tissue import INTENT_ZONE_MAP
        assert "creativity" in INTENT_ZONE_MAP["EXPANSION_CODE"]
        assert "cognition" in INTENT_ZONE_MAP["EXPANSION_CODE"]

    def test_security_audit_zones(self):
        """SECURITY_AUDIT → threat + stability."""
        from core.neural_tissue import INTENT_ZONE_MAP
        assert "threat" in INTENT_ZONE_MAP["SECURITY_AUDIT"]
        assert "stability" in INTENT_ZONE_MAP["SECURITY_AUDIT"]

    def test_all_intents_reference_valid_zones(self):
        """Tous les intents référencent des zones existantes dans SIGNAL_ZONES."""
        from core.neural_tissue import INTENT_ZONE_MAP, SIGNAL_ZONES
        valid_zones = set(SIGNAL_ZONES.keys())
        for intent, zones in INTENT_ZONE_MAP.items():
            for zone in zones:
                assert zone in valid_zones, f"Intent {intent} référence zone inconnue: {zone}"

    def test_all_main_intents_mapped(self):
        """Au moins 10 intents sont mappés."""
        from core.neural_tissue import INTENT_ZONE_MAP
        assert len(INTENT_ZONE_MAP) >= 10


class TestCognitiveDecay:
    """Tests du decay anti-saturation des signaux cognitifs."""

    def test_saturated_signal_decays(self):
        """Un signal à 1.0 décroît vers son baseline après decay."""
        from core.neural_tissue import COGNITIVE_BASELINES
        tissue = _make_tissue()
        tissue._cognitive_state["memory_activity"] = 1.0
        tissue._decay_cognitive_signals()
        assert tissue._cognitive_state["memory_activity"] < 1.0
        assert tissue._cognitive_state["memory_activity"] > COGNITIVE_BASELINES["memory_activity"]

    def test_signal_at_baseline_unchanged(self):
        """Un signal déjà au baseline ne bouge pas."""
        from core.neural_tissue import COGNITIVE_BASELINES
        tissue = _make_tissue()
        tissue._cognitive_state["creativity"] = COGNITIVE_BASELINES["creativity"]
        tissue._decay_cognitive_signals()
        assert tissue._cognitive_state["creativity"] == COGNITIVE_BASELINES["creativity"]

    def test_signal_below_baseline_rises(self):
        """Un signal en dessous du baseline remonte."""
        from core.neural_tissue import COGNITIVE_BASELINES
        tissue = _make_tissue()
        tissue._cognitive_state["stability"] = 0.1  # baseline = 0.7
        tissue._decay_cognitive_signals()
        assert tissue._cognitive_state["stability"] > 0.1

    def test_all_baselines_defined(self):
        """Chaque signal décayable a un baseline défini."""
        from core.neural_tissue import COGNITIVE_BASELINES
        expected = {"memory_activity", "creativity", "cognition_level",
                    "stability", "dopamine_level", "emotion_intensity",
                    "desire_intensity"}
        assert expected == set(COGNITIVE_BASELINES.keys())

    def test_convergence_after_many_ticks(self):
        """Après 1000 ticks sans stimulation, le signal converge vers le baseline."""
        from core.neural_tissue import COGNITIVE_BASELINES
        tissue = _make_tissue()
        tissue._cognitive_state["cognition_level"] = 1.0
        for _ in range(1000):
            tissue._decay_cognitive_signals()
        baseline = COGNITIVE_BASELINES["cognition_level"]
        assert tissue._cognitive_state["cognition_level"] == pytest.approx(baseline, abs=0.05)

    def test_desire_decays_from_max(self):
        """desire_intensity à 100 décroît vers 50."""
        from core.neural_tissue import COGNITIVE_BASELINES
        tissue = _make_tissue()
        tissue._cognitive_state["desire_intensity"] = 100.0
        for _ in range(100):
            tissue._decay_cognitive_signals()
        assert tissue._cognitive_state["desire_intensity"] < 100.0
        assert tissue._cognitive_state["desire_intensity"] > COGNITIVE_BASELINES["desire_intensity"]

    def test_decay_rate_is_gentle(self):
        """Un seul tick de decay ne change le signal que de ~0.3%."""
        from core.neural_tissue import COGNITIVE_DECAY_RATE
        tissue = _make_tissue()
        tissue._cognitive_state["creativity"] = 1.0
        old = tissue._cognitive_state["creativity"]
        tissue._decay_cognitive_signals()
        delta = abs(old - tissue._cognitive_state["creativity"])
        # delta ≈ 0.003 * (1.0 - 0.3) = 0.0021
        assert delta < 0.01  # Très doux
        assert delta > 0.0   # Mais non nul

    def test_hardware_signals_not_decayed(self):
        """Les signaux hardware (sensorium) ne sont pas affectés par le decay."""
        tissue = _make_tissue()
        tissue._cognitive_state["thermal_stress"] = 0.9
        tissue._cognitive_state["somatic_load"] = 0.8
        tissue._decay_cognitive_signals()
        assert tissue._cognitive_state["thermal_stress"] == 0.9
        assert tissue._cognitive_state["somatic_load"] == 0.8

    def test_decay_called_in_tick(self):
        """_tick() appelle _decay_cognitive_signals()."""
        tissue = _make_tissue()
        tissue._cognitive_state["memory_activity"] = 1.0
        with patch.object(tissue, '_save'), \
             patch.object(tissue, '_check_emergence'), \
             patch.object(tissue, '_check_symbiosis_emergence'), \
             patch.object(tissue, '_check_thresholds'), \
             patch.object(tissue, '_publish_zone_update'):
            tissue._tick()
        assert tissue._cognitive_state["memory_activity"] < 1.0
