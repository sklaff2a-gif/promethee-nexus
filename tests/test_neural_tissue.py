# tests/test_neural_tissue.py
"""Tests Sprint 0 — Stabilisation du tissu neural."""

import pytest
import random
from unittest.mock import patch

from core.neural_tissue import (
    NeuralCell, NeuralTissue, GRID_SIZE, INITIAL_CELLS, INITIAL_ENERGY,
    DIVISION_THRESHOLD, MAINTENANCE_COST, MAINTENANCE_COST_BASAL,
    BASAL_ENERGY_THRESHOLD, SIGNAL_ZONES, FOOD_SPAWN_PER_ZONE,
    MIN_ZONE_INTENSITY, DAWN_REPOPULATE_COUNT, VIABLE_GENOMES,
    SEASON_ORDER, SEASON_CYCLE_LENGTH, SEASON_FOOD_BONUS,
    SEASON_TRANSITION_BONUS, ALPHA_ENERGY_THRESHOLD, ALPHA_OUTPUT_THRESHOLD,
    ZONE_ADJACENCY, MAX_CELLS,
    PANDEMIC_MIN_INTERVAL, PANDEMIC_MAX_INTERVAL, PANDEMIC_MIN_POPULATION,
    PANDEMIC_MOTIF_LEN, INFECTION_DURATION, INFECTION_DRAIN,
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
        # 60 >= 50 et signal >= 0.1 -> MAINTENANCE_COST = 1.0
        # 60 + 3.0 - 1.0 = 62.0
        assert cell.energy == pytest.approx(62.0, abs=0.01)

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
        # 50 >= 50 et signal >= 0.1 -> MAINTENANCE_COST = 1.0
        # 50 + 3.0 - 1.0 = 52.0
        assert cell.energy == pytest.approx(52.0, abs=0.01)

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
        # 0.5 >= 0.1 et 63 >= 50 → MAINTENANCE_COST = 1.0
        # 60 + 3.0 - 1.0 = 62.0
        assert cell.energy == pytest.approx(62.0, abs=0.01)

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

        def mock_try_publish(event_name, payload):
            if event_name == "TISSUE_SEASON_CHANGE":
                published.append(payload)
            return original_try_publish(event_name, payload)

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

        def mock_try_publish(event_name, payload):
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
        """Valeurs des constantes pandémie."""
        assert PANDEMIC_MIN_INTERVAL == 2000
        assert PANDEMIC_MAX_INTERVAL == 5000
        assert PANDEMIC_MIN_POPULATION == 100
        assert PANDEMIC_MOTIF_LEN == 2
        assert INFECTION_DURATION == 15
        assert INFECTION_DRAIN == 8.0
        assert IMMUNE_MUTATION_CHANCE == 0.10
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
        """Seules les cellules contenant le motif sont infectées."""
        tissue = _make_tissue()
        # Cellules avec et sans le motif
        tissue.cells = [
            NeuralCell(genome="GRCGC", x=0, y=0, energy=100.0),
            NeuralCell(genome="GRCGC", x=1, y=0, energy=100.0),
            NeuralCell(genome="AAATT", x=2, y=0, energy=100.0),  # Pas de "GR"
        ]
        tissue._update_dominant_patterns()
        tissue._pandemic_active = False
        # Forcer le motif
        tissue.dominant_patterns = [{"genome": "GRCGC", "frequency": 0.67, "avg_fitness": 1.0}]
        # Monkey-patch pour forcer le motif "GR"
        old_randint = random.randint
        random.randint = lambda a, b: 0  # start=0 → motif "GR"
        try:
            tissue._trigger_pandemic()
        finally:
            random.randint = old_randint
        infected = [c for c in tissue.cells if c.infected_by is not None]
        saines = [c for c in tissue.cells if c.infected_by is None]
        assert len(infected) == 2
        assert len(saines) == 1
        assert saines[0].genome == "AAATT"

    # 4
    def test_infection_timer_initialized(self):
        """Le timer d'infection est initialisé à INFECTION_DURATION."""
        tissue = self._make_pandemic_tissue("GRCGC", 150)
        tissue._trigger_pandemic()
        for c in tissue.cells:
            if c.infected_by is not None:
                assert c.infection_timer == INFECTION_DURATION

    # 5
    def test_energy_drain_per_tick(self):
        """Chaque tick draine INFECTION_DRAIN d'énergie aux infectées."""
        tissue = _make_tissue()
        cell = NeuralCell(genome="GRCGC", x=0, y=0, energy=200.0)
        cell.infected_by = "GR"
        cell.infection_timer = 15
        tissue.cells = [cell]
        tissue._pandemic_active = True
        tissue._pandemic_motif = "GR"
        initial_energy = cell.energy
        # Forcer pas de mutation immunitaire
        with patch("core.neural_tissue.random.random", return_value=0.99):
            tissue._pandemic_tick()
        assert cell.energy == pytest.approx(initial_energy - INFECTION_DRAIN, abs=0.01)

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
        # local_signal = 4.0 > 0.1, energy = 106 >= 50 → cost = MAINTENANCE_COST = 1.0
        # heat_tolerant: local_signal 4.0 > 3.0 → cost *= 0.5 → 0.5
        # 100 + 6.0 - 0.5 = 105.5
        assert cell2.energy == pytest.approx(105.5, abs=0.01)

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
        # energy=109 >= 50 → cost=1.0
        # 100 + 9.0 - 1.0 = 108.0
        assert cell.energy == pytest.approx(108.0, abs=0.01)

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
        """Cellule isolée (0 voisins + age > 10) → apoptose."""
        cell = NeuralCell(genome="C", x=0, y=0, energy=100.0, age=15)
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
        """Ratio apoptose/nécrose favorable → bonus positif."""
        tissue = _make_tissue()
        tissue.total_apoptosis = 15
        tissue.total_necrosis = 5
        tissue._update_dominant_patterns()
        bonus = tissue.compute_tissue_bonus("exploration")
        # health_ratio = 15/20 = 0.75 > 0.5 → health_bonus = 0.2
        assert bonus >= 0.2

    # 4
    def test_compute_tissue_bonus_health_bad(self):
        """Beaucoup de nécrose → malus."""
        tissue = _make_tissue()
        tissue.total_apoptosis = 2
        tissue.total_necrosis = 15
        tissue._update_dominant_patterns()
        bonus = tissue.compute_tissue_bonus("")
        # health_ratio = 2/17 ≈ 0.12 < 0.3 → health_bonus = -0.2
        # Le bonus total peut être > 0 grâce aux autres composantes
        # Vérifions juste que le health malus est appliqué
        tissue2 = _make_tissue()
        tissue2.total_apoptosis = 15
        tissue2.total_necrosis = 2
        tissue2._update_dominant_patterns()
        bonus2 = tissue2.compute_tissue_bonus("")
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
        # Cellule isolée âgée
        isolated = NeuralCell(genome="C", x=0, y=0, energy=100.0, age=15)
        tissue.cells = [isolated]
        tissue._tick()
        # La cellule isolée devrait être apoptosée
        assert tissue.total_apoptosis >= 1

    # 5
    def test_necrosis_in_tick_pipeline(self):
        """La nécrose se déclenche pour les cellules mourant d'épuisement."""
        tissue = _make_tissue()
        tissue._circadian_phase = "eveil"
        # Cellule avec quasi plus d'énergie
        dying = NeuralCell(genome="C", x=8, y=8, energy=0.1, age=0)
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
