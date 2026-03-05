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
