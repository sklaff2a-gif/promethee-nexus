"""Tests unitaires pour drive_routine_registry — Phase C Etapes 2c + 2e.

Tests TOTALEMENT ISOLES du reste de Promethee. Ils verifient la semantique
du DRIVE_GENOME, de _compute_genome_floor avec temps metabolique injecte,
et de get_routines_for_drive (la projection finale Brique 3).

Aucune dependance a un bus, un organe, ou un etat persiste.

Couvre notamment :
- Structure du genome (presence des routines validees par Jean-Michel)
- Grace period metabolique (protection absolue avant 1000 cycles)
- Competition 2x + stabilite 1000 cycles
- Depreciation lineaire vers FLOOR_OF_THE_FLOOR
- Invariance du temps serveur-eteint (le cycle_count stagne = pas d'age)
- API d'inspection falsifiable (explain_genome_floor)
- Fusion synaptic + genome floor (Brique 3)
- Application des context_multipliers (NE CREE PAS de routines)
- Temperature greedy vs stochastique avec rng injecte
"""

import random
import pytest
from unittest.mock import patch

from core import drive_routine_registry as registry
from core.drive_routine_registry import (
    DRIVE_GENOME,
    THERMAL_SIGNATURES,
    GENOME_GRACE_CYCLES,
    GENOME_DEPRECIATION_CYCLES,
    FLOOR_OF_THE_FLOOR,
    COMPETITOR_STABILITY_MIN,
    _compute_genome_floor,
    explain_genome_floor,
    get_routines_for_drive,
)


# ─── Structure du Genome ────────────────────────────────────────────────

class TestDriveGenomeStructure:
    """Valide la structure et les valeurs du DRIVE_GENOME natal."""

    def test_seven_drives_present(self):
        expected = {
            "CURIOSITE", "MAITRISE", "STABILITE", "CONNEXION",
            "CROISSANCE", "CREATION", "COMPREHENSION",
        }
        assert set(DRIVE_GENOME.keys()) == expected

    def test_all_weights_in_sane_range(self):
        """Tous les planchers genomiques dans [0.4, 0.9]."""
        for drive, intents in DRIVE_GENOME.items():
            for intent, weight in intents.items():
                assert 0.4 <= weight <= 0.9, (
                    f"{drive}.{intent}={weight} hors [0.4, 0.9]"
                )

    def test_maitrise_has_refactoring_audit_at_nine(self):
        """Phase B victoire : REFACTORING_AUDIT est canonique pour MAITRISE."""
        assert "REFACTORING_AUDIT" in DRIVE_GENOME["MAITRISE"]
        assert DRIVE_GENOME["MAITRISE"]["REFACTORING_AUDIT"] == 0.9

    def test_maitrise_has_ci_pipeline_run(self):
        """CI_PIPELINE_RUN materialisee en Phase B doit etre genomique."""
        assert "CI_PIPELINE_RUN" in DRIVE_GENOME["MAITRISE"]
        assert DRIVE_GENOME["MAITRISE"]["CI_PIPELINE_RUN"] >= 0.7

    def test_audit_survie_in_both_drives(self):
        """Le reflex du tronc cerebral : MAITRISE et STABILITE."""
        assert "AUDIT_SURVIE" in DRIVE_GENOME["MAITRISE"]
        assert "AUDIT_SURVIE" in DRIVE_GENOME["STABILITE"]

    def test_audit_survie_stabilite_highest(self):
        """STABILITE doit avoir AUDIT_SURVIE a 0.9 (reflex prioritaire)."""
        assert DRIVE_GENOME["STABILITE"]["AUDIT_SURVIE"] == 0.9

    def test_cours_soutien_in_comprehension(self):
        """Le reflex pedagogique."""
        assert "COURS_SOUTIEN" in DRIVE_GENOME["COMPREHENSION"]
        assert DRIVE_GENOME["COMPREHENSION"]["COURS_SOUTIEN"] == 0.8

    def test_connexion_has_soliloque(self):
        assert "SOLILOQUE_INTERNE" in DRIVE_GENOME["CONNEXION"]

    def test_no_drive_empty(self):
        for drive, intents in DRIVE_GENOME.items():
            assert len(intents) > 0, f"Drive {drive} est vide"


# ─── _compute_genome_floor : Grace Period ───────────────────────────────

class TestGenomeFloorGracePeriod:
    """Grace period metabolique — protection absolue avant GRACE_CYCLES."""

    def setup_method(self):
        registry._genome_entry_cycles.clear()
        registry._genome_entry_cycles[("MAITRISE", "REFACTORING_AUDIT")] = 0

    def test_age_zero_returns_full_floor(self):
        floor = _compute_genome_floor("MAITRISE", "REFACTORING_AUDIT", 0)
        assert floor == 0.9

    def test_age_below_grace_returns_full_floor(self):
        floor = _compute_genome_floor("MAITRISE", "REFACTORING_AUDIT", 500)
        assert floor == 0.9

    def test_age_exactly_grace_minus_one_returns_full_floor(self):
        floor = _compute_genome_floor(
            "MAITRISE", "REFACTORING_AUDIT", GENOME_GRACE_CYCLES - 1
        )
        assert floor == 0.9

    def test_grace_period_ignores_competitor(self):
        """Pendant la grace, meme un competiteur stable n'est pas ecoute."""
        stability_fn = lambda d, i, n: 0.95
        floor = _compute_genome_floor(
            "MAITRISE", "REFACTORING_AUDIT", 500, stability_fn
        )
        assert floor == 0.9


# ─── _compute_genome_floor : Pas de competiteur ─────────────────────────

class TestGenomeFloorNoCompetitor:
    """Apres grace period, sans competiteur connu -> plancher plein."""

    def setup_method(self):
        registry._genome_entry_cycles.clear()
        registry._genome_entry_cycles[("MAITRISE", "REFACTORING_AUDIT")] = 0

    def test_past_grace_no_fn_returns_full_floor(self):
        floor = _compute_genome_floor("MAITRISE", "REFACTORING_AUDIT", 5000)
        assert floor == 0.9

    def test_very_old_no_fn_still_full_floor(self):
        floor = _compute_genome_floor("MAITRISE", "REFACTORING_AUDIT", 1_000_000)
        assert floor == 0.9


# ─── _compute_genome_floor : Competiteur instable ──────────────────────

class TestGenomeFloorUnstableCompetitor:
    """Competiteur existe mais n'est pas stable (< 0.7) -> plancher plein."""

    def setup_method(self):
        registry._genome_entry_cycles.clear()
        registry._genome_entry_cycles[("MAITRISE", "REFACTORING_AUDIT")] = 0

    def test_unstable_competitor_returns_full_floor(self):
        stability_fn = lambda d, i, n: 0.5
        floor = _compute_genome_floor(
            "MAITRISE", "REFACTORING_AUDIT", 5000, stability_fn
        )
        assert floor == 0.9

    def test_boundary_stability_unstable(self):
        """Exactement COMPETITOR_STABILITY_MIN - 0.01 -> plancher plein."""
        stability_fn = lambda d, i, n: COMPETITOR_STABILITY_MIN - 0.01
        floor = _compute_genome_floor(
            "MAITRISE", "REFACTORING_AUDIT", 5000, stability_fn
        )
        assert floor == 0.9


# ─── _compute_genome_floor : Depreciation progressive ──────────────────

class TestGenomeFloorDepreciation:
    """Competiteur stable apres grace period -> depreciation lineaire."""

    def setup_method(self):
        registry._genome_entry_cycles.clear()
        registry._genome_entry_cycles[("MAITRISE", "REFACTORING_AUDIT")] = 0

    def test_at_grace_boundary_no_depreciation_yet(self):
        """Age == GRACE_CYCLES : depreciation_age = 0, floor = base."""
        stability_fn = lambda d, i, n: 0.9
        floor = _compute_genome_floor(
            "MAITRISE", "REFACTORING_AUDIT", GENOME_GRACE_CYCLES, stability_fn
        )
        assert floor == 0.9

    def test_one_tenth_depreciation(self):
        """depreciation_age = 1000 sur 10000 -> decay 10%."""
        stability_fn = lambda d, i, n: 0.9
        current = GENOME_GRACE_CYCLES + 1000
        floor = _compute_genome_floor(
            "MAITRISE", "REFACTORING_AUDIT", current, stability_fn
        )
        base = 0.9
        expected = FLOOR_OF_THE_FLOOR + (base - FLOOR_OF_THE_FLOOR) * 0.9
        assert abs(floor - expected) < 1e-9

    def test_half_depreciation(self):
        stability_fn = lambda d, i, n: 0.9
        current = GENOME_GRACE_CYCLES + GENOME_DEPRECIATION_CYCLES // 2
        floor = _compute_genome_floor(
            "MAITRISE", "REFACTORING_AUDIT", current, stability_fn
        )
        expected = FLOOR_OF_THE_FLOOR + (0.9 - FLOOR_OF_THE_FLOOR) * 0.5
        assert abs(floor - expected) < 1e-9

    def test_full_depreciation_reaches_floor_of_the_floor(self):
        stability_fn = lambda d, i, n: 0.9
        current = GENOME_GRACE_CYCLES + GENOME_DEPRECIATION_CYCLES
        floor = _compute_genome_floor(
            "MAITRISE", "REFACTORING_AUDIT", current, stability_fn
        )
        assert abs(floor - FLOOR_OF_THE_FLOOR) < 1e-9

    def test_beyond_full_depreciation_stays_at_floor(self):
        """Une fois au FLOOR_OF_THE_FLOOR, on n'y descend jamais en dessous."""
        stability_fn = lambda d, i, n: 0.9
        current = GENOME_GRACE_CYCLES + GENOME_DEPRECIATION_CYCLES * 10
        floor = _compute_genome_floor(
            "MAITRISE", "REFACTORING_AUDIT", current, stability_fn
        )
        assert floor == FLOOR_OF_THE_FLOOR

    def test_different_base_different_trajectory(self):
        """Un lien a 0.5 se deprecie plus vite qu'un lien a 0.9."""
        registry._genome_entry_cycles[("MAITRISE", "SECURITY_AUDIT")] = 0
        stability_fn = lambda d, i, n: 0.9
        current = GENOME_GRACE_CYCLES + GENOME_DEPRECIATION_CYCLES // 2
        floor_high = _compute_genome_floor(
            "MAITRISE", "REFACTORING_AUDIT", current, stability_fn
        )
        floor_low = _compute_genome_floor(
            "MAITRISE", "SECURITY_AUDIT", current, stability_fn
        )
        # Le lien 0.9 reste plus haut que le lien 0.7
        assert floor_high > floor_low


# ─── _compute_genome_floor : Cas d'erreur ──────────────────────────────

class TestGenomeFloorEdgeCases:
    def setup_method(self):
        registry._genome_entry_cycles.clear()
        registry._initialize_genome_entry_cycles()

    def test_intent_not_in_genome_returns_zero(self):
        floor = _compute_genome_floor("MAITRISE", "NONEXISTENT_INTENT", 5000)
        assert floor == 0.0

    def test_drive_not_in_genome_returns_zero(self):
        floor = _compute_genome_floor("NOPE_DRIVE", "REFACTORING_AUDIT", 5000)
        assert floor == 0.0

    def test_entry_cycle_future_clamped_to_zero_age(self):
        """Si entry_cycle > current_cycle, age est clampe a 0 (pas negatif)."""
        registry._genome_entry_cycles[("MAITRISE", "REFACTORING_AUDIT")] = 1000
        floor = _compute_genome_floor("MAITRISE", "REFACTORING_AUDIT", 500)
        # age = max(0, 500-1000) = 0 -> grace period active
        assert floor == 0.9


# ─── Temps metabolique : invariance serveur eteint ─────────────────────

class TestMetabolicTimeInvariance:
    """Preuve que le temps d'horloge n'influence PAS la depreciation.

    C'est le garde-fou theorique le plus important : si experience_clock
    stagne (serveur eteint, IDLE), le plancher ne bouge pas, peu importe
    combien d'heures calendaires se sont ecoulees.
    """

    def setup_method(self):
        registry._genome_entry_cycles.clear()
        registry._genome_entry_cycles[("MAITRISE", "REFACTORING_AUDIT")] = 500

    def test_frozen_cycle_no_depreciation(self):
        """Meme current_cycle passe plusieurs fois -> meme plancher."""
        stability_fn = lambda d, i, n: 0.9
        floor1 = _compute_genome_floor(
            "MAITRISE", "REFACTORING_AUDIT", 600, stability_fn
        )
        floor2 = _compute_genome_floor(
            "MAITRISE", "REFACTORING_AUDIT", 600, stability_fn
        )
        floor3 = _compute_genome_floor(
            "MAITRISE", "REFACTORING_AUDIT", 600, stability_fn
        )
        assert floor1 == floor2 == floor3 == 0.9  # grace encore active

    def test_cycle_progression_produces_aging(self):
        """Mais si le cycle avance, la depreciation progresse."""
        stability_fn = lambda d, i, n: 0.9
        floor_young = _compute_genome_floor(
            "MAITRISE", "REFACTORING_AUDIT",
            500 + GENOME_GRACE_CYCLES + 1000,
            stability_fn,
        )
        floor_old = _compute_genome_floor(
            "MAITRISE", "REFACTORING_AUDIT",
            500 + GENOME_GRACE_CYCLES + 5000,
            stability_fn,
        )
        assert floor_old < floor_young, (
            "L'avancement du cycle doit produire une depreciation"
        )


# ─── explain_genome_floor : API d'inspection falsifiable ──────────────

class TestExplainGenomeFloor:
    def setup_method(self):
        registry._genome_entry_cycles.clear()
        registry._initialize_genome_entry_cycles()

    def test_explain_not_in_genome(self):
        with patch.object(registry.experience_clock, "current", return_value=0):
            info = explain_genome_floor("MAITRISE", "NOPE")
        assert info["in_genome"] is False
        assert info["current_floor"] == 0.0
        assert info["reason"] == "not_in_genome"

    def test_explain_grace_period(self):
        registry._genome_entry_cycles[("MAITRISE", "REFACTORING_AUDIT")] = 0
        with patch.object(registry.experience_clock, "current", return_value=500):
            info = explain_genome_floor("MAITRISE", "REFACTORING_AUDIT")
        assert info["in_genome"] is True
        assert info["base_floor"] == 0.9
        assert info["age_cycles"] == 500
        assert info["grace_period_active"] is True
        assert info["grace_cycles_remaining"] == GENOME_GRACE_CYCLES - 500
        assert info["reason"] == "grace_period_active"
        assert info["current_floor"] == 0.9

    def test_explain_past_grace_no_competitor(self):
        registry._genome_entry_cycles[("MAITRISE", "REFACTORING_AUDIT")] = 0
        with patch.object(registry.experience_clock, "current", return_value=5000):
            info = explain_genome_floor("MAITRISE", "REFACTORING_AUDIT")
        assert info["grace_period_active"] is False
        assert info["reason"] == "no_competitor_tracking"
        assert info["current_floor"] == 0.9

    def test_explain_depreciating(self):
        registry._genome_entry_cycles[("MAITRISE", "REFACTORING_AUDIT")] = 0
        stability_fn = lambda d, i, n: 0.9
        with patch.object(
            registry.experience_clock, "current",
            return_value=GENOME_GRACE_CYCLES + 1000,
        ):
            info = explain_genome_floor(
                "MAITRISE", "REFACTORING_AUDIT", stability_fn
            )
        assert info["grace_period_active"] is False
        assert info["reason"] == "depreciating"
        assert info["current_floor"] < info["base_floor"]

    def test_explain_unstable_competitor(self):
        registry._genome_entry_cycles[("MAITRISE", "REFACTORING_AUDIT")] = 0
        stability_fn = lambda d, i, n: 0.3
        with patch.object(
            registry.experience_clock, "current", return_value=5000,
        ):
            info = explain_genome_floor(
                "MAITRISE", "REFACTORING_AUDIT", stability_fn
            )
        assert "competitor_unstable" in info["reason"]
        assert info["current_floor"] == 0.9


# ─── Garantie d'isolation ─────────────────────────────────────────────

class TestNoSideEffectsOnExperienceClock:
    """_compute_genome_floor est une lecture pure — ne doit jamais modifier
    l'horloge d'experience.
    """

    def test_compute_floor_does_not_tick_clock(self):
        from core.experience_clock import experience_clock as ec
        before = ec.current()
        for _ in range(100):
            _compute_genome_floor("MAITRISE", "REFACTORING_AUDIT", 5000)
            _compute_genome_floor("STABILITE", "MEMORY_CONSOLIDATION", 500)
        after = ec.current()
        assert before == after


# ═══════════════════════════════════════════════════════════════════════
# Tests pour get_routines_for_drive (Etape 2e — Brique 3)
# ═══════════════════════════════════════════════════════════════════════


class TestGetRoutinesBasic:
    """Comportement de base : listing, top_k, drives vides."""

    def setup_method(self):
        registry._genome_entry_cycles.clear()
        registry._initialize_genome_entry_cycles()

    def test_empty_synaptic_returns_genome_routines(self):
        """Graphe vide + genome rempli -> retourne les intents du genome."""
        result = get_routines_for_drive(
            "MAITRISE", synaptic_weights={}, temperature=0.0, top_k=10,
        )
        assert len(result) == len(DRIVE_GENOME["MAITRISE"])
        intents = {i for i, w in result}
        assert intents == set(DRIVE_GENOME["MAITRISE"].keys())

    def test_top_k_respected(self):
        result = get_routines_for_drive(
            "MAITRISE", synaptic_weights={}, temperature=0.0, top_k=3,
        )
        assert len(result) == 3

    def test_top_k_zero_returns_empty(self):
        result = get_routines_for_drive(
            "MAITRISE", synaptic_weights={}, temperature=0.0, top_k=0,
        )
        assert result == []

    def test_top_k_larger_than_available(self):
        """top_k > nb_intents -> retourne tous les intents."""
        result = get_routines_for_drive(
            "MAITRISE", synaptic_weights={}, temperature=0.0, top_k=100,
        )
        assert len(result) == len(DRIVE_GENOME["MAITRISE"])

    def test_unknown_drive_empty_synaptic_returns_empty(self):
        result = get_routines_for_drive(
            "NOPE_DRIVE", synaptic_weights={}, temperature=0.0,
        )
        assert result == []

    def test_unknown_drive_with_synaptic_returns_synaptic(self):
        """Pour un drive hors genome, seul le synaptic compte."""
        result = get_routines_for_drive(
            "NOPE_DRIVE",
            synaptic_weights={"LEARNED_ROUTINE": 0.8},
            temperature=0.0,
        )
        assert len(result) == 1
        assert result[0] == ("LEARNED_ROUTINE", 0.8)

    def test_all_weights_are_floats(self):
        result = get_routines_for_drive("MAITRISE", synaptic_weights={})
        for intent, weight in result:
            assert isinstance(weight, float)


class TestGetRoutinesFusion:
    """Fusion synaptic + genome floor via max()."""

    def setup_method(self):
        registry._genome_entry_cycles.clear()
        registry._initialize_genome_entry_cycles()

    def test_synaptic_higher_than_floor_wins(self):
        """Graphe a appris plus que le plancher -> gagne."""
        result = get_routines_for_drive(
            "MAITRISE",
            synaptic_weights={"REFACTORING_AUDIT": 1.5},
            temperature=0.0,
            top_k=10,
        )
        refact = next(w for i, w in result if i == "REFACTORING_AUDIT")
        assert refact == 1.5  # max(1.5, 0.9)

    def test_floor_higher_than_synaptic_wins(self):
        """Graphe a sous-appris -> plancher genomique protege."""
        result = get_routines_for_drive(
            "MAITRISE",
            synaptic_weights={"REFACTORING_AUDIT": 0.1},
            temperature=0.0,
            top_k=10,
        )
        refact = next(w for i, w in result if i == "REFACTORING_AUDIT")
        assert refact == 0.9  # max(0.1, 0.9)

    def test_new_intent_from_synaptic_not_in_genome(self):
        """Une routine nouvellement apprise (pas dans genome) apparait."""
        result = get_routines_for_drive(
            "MAITRISE",
            synaptic_weights={"SUPER_REFACTORING_V2": 1.2},
            temperature=0.0,
            top_k=10,
        )
        intents = {i for i, w in result}
        assert "SUPER_REFACTORING_V2" in intents
        w = next(w for i, w in result if i == "SUPER_REFACTORING_V2")
        assert w == 1.2  # pas de floor, max(1.2, 0)

    def test_zero_synaptic_weight_filtered_when_not_in_genome(self):
        """Un intent synaptic a 0 et pas dans genome -> exclu."""
        result = get_routines_for_drive(
            "MAITRISE",
            synaptic_weights={"DEAD_INTENT": 0.0},
            temperature=0.0,
            top_k=20,
        )
        intents = {i for i, w in result}
        assert "DEAD_INTENT" not in intents

    def test_union_of_synaptic_and_genome(self):
        """Retourne l'union des intents synaptic U genome."""
        result = get_routines_for_drive(
            "MAITRISE",
            synaptic_weights={"NEW_ONE": 0.5},
            temperature=0.0,
            top_k=20,
        )
        intents = {i for i, w in result}
        expected = set(DRIVE_GENOME["MAITRISE"].keys()) | {"NEW_ONE"}
        assert intents == expected


class TestGetRoutinesContextMultipliers:
    """Modulation multiplicative — la regle d'or : ne cree jamais une routine."""

    def setup_method(self):
        registry._genome_entry_cycles.clear()
        registry._initialize_genome_entry_cycles()

    def test_multiplier_boosts_existing_weight(self):
        """Booster par 1.5 multiplie le poids final."""
        result = get_routines_for_drive(
            "MAITRISE",
            synaptic_weights={},
            context_multipliers={"SECURITY_AUDIT": 1.5},
            temperature=0.0,
            top_k=10,
        )
        sec = next(w for i, w in result if i == "SECURITY_AUDIT")
        base = DRIVE_GENOME["MAITRISE"]["SECURITY_AUDIT"]
        assert abs(sec - (base * 1.5)) < 1e-9

    def test_multiplier_penalizes(self):
        """Multiplicateur < 1 reduit le poids."""
        result = get_routines_for_drive(
            "MAITRISE",
            synaptic_weights={},
            context_multipliers={"REFACTORING_AUDIT": 0.5},
            temperature=0.0,
            top_k=10,
        )
        refact = next(w for i, w in result if i == "REFACTORING_AUDIT")
        base = DRIVE_GENOME["MAITRISE"]["REFACTORING_AUDIT"]
        assert abs(refact - (base * 0.5)) < 1e-9

    def test_multiplier_cannot_create_routine(self):
        """REGLE D'OR : un multiplicateur sur un intent inexistant est ignore."""
        result = get_routines_for_drive(
            "MAITRISE",
            synaptic_weights={},
            context_multipliers={"CREER_DU_NEANT": 100.0},
            top_k=20,
        )
        intents = {i for i, w in result}
        assert "CREER_DU_NEANT" not in intents

    def test_multiplier_on_synaptic_intent(self):
        """Multiplicateur s'applique aussi aux intents synaptic-only."""
        result = get_routines_for_drive(
            "MAITRISE",
            synaptic_weights={"LEARNED_INTENT": 0.6},
            context_multipliers={"LEARNED_INTENT": 2.0},
            temperature=0.0,
            top_k=20,
        )
        w = next(w for i, w in result if i == "LEARNED_INTENT")
        assert abs(w - 1.2) < 1e-9  # 0.6 * 2.0

    def test_zero_multiplier_removes_routine(self):
        """Multiplicateur 0 -> poids final 0 -> filtre."""
        result = get_routines_for_drive(
            "MAITRISE",
            synaptic_weights={},
            context_multipliers={"REFACTORING_AUDIT": 0.0},
            temperature=0.0,
            top_k=20,
        )
        intents = {i for i, w in result}
        assert "REFACTORING_AUDIT" not in intents

    def test_empty_multipliers_equals_none(self):
        """Dict vide equivaut a None (pas de biais)."""
        r1 = get_routines_for_drive(
            "MAITRISE", synaptic_weights={}, context_multipliers=None, temperature=0.0,
        )
        r2 = get_routines_for_drive(
            "MAITRISE", synaptic_weights={}, context_multipliers={}, temperature=0.0,
        )
        assert r1 == r2


class TestGetRoutinesTemperatureGreedy:
    """Mode deterministe : T < 0.01."""

    def setup_method(self):
        registry._genome_entry_cycles.clear()
        registry._initialize_genome_entry_cycles()

    def test_temperature_zero_is_strictly_decreasing(self):
        result = get_routines_for_drive(
            "MAITRISE", synaptic_weights={}, temperature=0.0, top_k=10,
        )
        weights = [w for _, w in result]
        for i in range(len(weights) - 1):
            assert weights[i] >= weights[i + 1]

    def test_temperature_zero_deterministic(self):
        """Meme appel, meme resultat."""
        r1 = get_routines_for_drive("MAITRISE", synaptic_weights={}, temperature=0.0)
        r2 = get_routines_for_drive("MAITRISE", synaptic_weights={}, temperature=0.0)
        r3 = get_routines_for_drive("MAITRISE", synaptic_weights={}, temperature=0.0)
        assert r1 == r2 == r3

    def test_temperature_below_threshold_is_greedy(self):
        """T = 0.005 < 0.01 -> greedy exactement comme T = 0."""
        r_zero = get_routines_for_drive("MAITRISE", synaptic_weights={}, temperature=0.0)
        r_tiny = get_routines_for_drive("MAITRISE", synaptic_weights={}, temperature=0.005)
        assert r_zero == r_tiny

    def test_greedy_picks_highest_first(self):
        """Le top 1 est l'intent avec le plus gros poids."""
        result = get_routines_for_drive(
            "CROISSANCE", synaptic_weights={}, temperature=0.0, top_k=1,
        )
        # CROISSANCE: EXPANSION_CODE=0.9 est le plus gros
        assert len(result) == 1
        assert result[0] == ("EXPANSION_CODE", 0.9)

    def test_greedy_tiebreak_alphabetical(self):
        """Sur egalite de poids, tri alphabetique stable."""
        # STABILITE a MEMORY_CONSOLIDATION=0.9 et AUDIT_SURVIE=0.9 a egalite
        result = get_routines_for_drive(
            "STABILITE", synaptic_weights={}, temperature=0.0, top_k=2,
        )
        # AUDIT_SURVIE < MEMORY_CONSOLIDATION en ordre alphabetique
        assert result[0][0] == "AUDIT_SURVIE"
        assert result[1][0] == "MEMORY_CONSOLIDATION"
        assert result[0][1] == result[1][1] == 0.9


class TestGetRoutinesTemperatureStochastic:
    """Mode exploratoire : T >= 0.01 avec rng injecte pour determinisme."""

    def setup_method(self):
        registry._genome_entry_cycles.clear()
        registry._initialize_genome_entry_cycles()

    def test_seeded_rng_produces_reproducible_result(self):
        rng1 = random.Random(42)
        r1 = get_routines_for_drive(
            "MAITRISE", synaptic_weights={}, temperature=1.0,
            top_k=3, rng=rng1,
        )
        rng2 = random.Random(42)
        r2 = get_routines_for_drive(
            "MAITRISE", synaptic_weights={}, temperature=1.0,
            top_k=3, rng=rng2,
        )
        assert r1 == r2

    def test_different_seeds_produce_different_orderings(self):
        """La stochasticite fait son travail : seeds differents -> resultats differents."""
        rng1 = random.Random(1)
        rng2 = random.Random(99999)
        r1 = get_routines_for_drive(
            "MAITRISE", synaptic_weights={}, temperature=1.0,
            top_k=6, rng=rng1,
        )
        r2 = get_routines_for_drive(
            "MAITRISE", synaptic_weights={}, temperature=1.0,
            top_k=6, rng=rng2,
        )
        # Au moins un des deux premiers doit differer (forte probabilite)
        assert r1 != r2

    def test_no_replacement_in_stochastic_sampling(self):
        """Sans remplacement : pas de doublons meme sur tirages successifs."""
        rng = random.Random(7)
        result = get_routines_for_drive(
            "MAITRISE", synaptic_weights={}, temperature=1.0,
            top_k=6, rng=rng,
        )
        intents = [i for i, _ in result]
        assert len(intents) == len(set(intents))

    def test_high_weight_more_frequently_picked_over_n_trials(self):
        """Test statistique : sur 300 tirages top_k=1, un poids 2x plus eleve
        doit etre choisi ~2x plus souvent.

        V34.6 : drive CONNEXION refondu — COFFEE_BREAK (Alfred) prime.
          - COFFEE_BREAK         : 0.9
          - COUNCIL_DEBATE       : 0.6
          - SOLILOQUE_INTERNE    : 0.5
          - STEFAN_CONFRONTATION : 0.4
        P(COFFEE) ≈ 0.9/2.4 ≈ 0.375
        P(STEFAN) ≈ 0.4/2.4 ≈ 0.167
        Ratio attendu ≈ 2.25 — robuste sur 300 tirages.
        Test symbolique de la doctrine CONNEXION : Alfred (alterite reelle)
        domine Stefan (rival) en frequence d'apparition.
        """
        coffee_wins = 0
        stefan_wins = 0
        for seed in range(300):
            rng = random.Random(seed)
            result = get_routines_for_drive(
                "CONNEXION", synaptic_weights={},
                temperature=1.0, top_k=1, rng=rng,
            )
            first = result[0][0]
            if first == "COFFEE_BREAK":
                coffee_wins += 1
            elif first == "STEFAN_CONFRONTATION":
                stefan_wins += 1
        assert coffee_wins > stefan_wins
        # Ratio attendu ≈ 2.25, on accepte marge pour variance d'echantillon
        assert coffee_wins >= stefan_wins * 1.5, (
            f"COFFEE_BREAK={coffee_wins}, STEFAN={stefan_wins}, "
            f"ratio={coffee_wins/max(1,stefan_wins):.2f}"
        )

    def test_zero_total_weight_returns_empty(self):
        """Tous les poids nuls -> retourne liste vide."""
        result = get_routines_for_drive(
            "MAITRISE",
            synaptic_weights={},
            context_multipliers={k: 0.0 for k in DRIVE_GENOME["MAITRISE"]},
            temperature=1.0,
        )
        assert result == []


class TestGetRoutinesInjectionDiscipline:
    """Validation du principe d'injection de dependances."""

    def test_function_uses_no_hidden_state(self):
        """Appels successifs avec les memes arguments -> meme resultat (mode greedy)."""
        args = {
            "drive": "MAITRISE",
            "synaptic_weights": {"REFACTORING_AUDIT": 1.1},
            "context_multipliers": {"SECURITY_AUDIT": 1.2},
            "temperature": 0.0,
            "top_k": 5,
        }
        r1 = get_routines_for_drive(**args)
        r2 = get_routines_for_drive(**args)
        r3 = get_routines_for_drive(**args)
        assert r1 == r2 == r3

    def test_competitor_stability_fn_injected_not_imported(self):
        """La fonction de stabilite est injectee, pas importee globalement."""
        # Appel 1 : sans stability_fn -> pas de depreciation
        r_protected = get_routines_for_drive(
            "MAITRISE", synaptic_weights={}, temperature=0.0,
            competitor_stability_fn=None,
            top_k=10,
        )
        # Appel 2 : avec stability_fn retournant 0 -> pas de depreciation non plus
        # (en grace period)
        stability_stub = lambda d, i, n: 0.9
        r_stubbed = get_routines_for_drive(
            "MAITRISE", synaptic_weights={}, temperature=0.0,
            competitor_stability_fn=stability_stub,
            top_k=10,
        )
        # En grace period, les deux doivent etre identiques
        assert r_protected == r_stubbed


# ═══════════════════════════════════════════════════════════════════════
# V35.0 — THERMAL_SIGNATURES (Genome Thermodynamique)
# ═══════════════════════════════════════════════════════════════════════
#
# Doctrine V35 : chaque routine connue du genome doit avoir une signature
# thermique scalaire dans [-0.50, +0.30]. Pas d'orphelin, pas d'amplitude
# aberrante. Les tests ci-dessous gravent cet invariant pour empecher
# qu'une future addition au DRIVE_GENOME ne se fasse sans signature.

class TestThermalSignatures:
    """V35.0 — Gravure de l'invariant doctrinal des signatures thermiques."""

    def test_thermal_signatures_is_dict_of_floats(self):
        """Format attendu : Dict[str, float]. Pas de tuples, pas de None."""
        assert isinstance(THERMAL_SIGNATURES, dict)
        for intent, delta in THERMAL_SIGNATURES.items():
            assert isinstance(intent, str), f"intent non-str: {intent!r}"
            assert isinstance(delta, (int, float)), (
                f"signature non-numerique pour {intent}: {delta!r}"
            )

    def test_amplitudes_within_doctrinal_range(self):
        """V35.0 : amplitudes bornees dans [-0.50, +0.30].
        Au-dela, c'est probablement une erreur de saisie."""
        for intent, delta in THERMAL_SIGNATURES.items():
            assert -0.50 <= delta <= 0.30, (
                f"{intent} hors plage [-0.50, +0.30]: {delta}"
            )

    def test_every_genome_intent_has_thermal_signature(self):
        """Invariant fondamental V35 : aucun intent du DRIVE_GENOME ne doit
        rester sans signature thermique. L'absence rendrait le tissu aveugle
        a son effet metabolique. C'est exactement le piege de la table
        heretique V34 — eviter qu'il ne se reproduise sur la dimension
        thermique."""
        genome_intents = set()
        for drive, intents in DRIVE_GENOME.items():
            genome_intents.update(intents.keys())

        thermal_intents = set(THERMAL_SIGNATURES.keys())
        missing = genome_intents - thermal_intents
        assert not missing, (
            f"Intents du genome sans signature thermique: {sorted(missing)}. "
            f"Ajoute-les a THERMAL_SIGNATURES dans drive_routine_registry."
        )

    def test_doctrine_alfred_is_exact_counterweight_of_expansion_code(self):
        """Doctrine CONNEXION rendue thermodynamique : COFFEE_BREAK (Alfred)
        dissipe exactement ce qu'EXPANSION_CODE produit. Cette symetrie est
        intentionnelle — on grave la doctrine 'l'alterite reelle est le
        contrepoids exact du LLM lourd' dans la physique du systeme."""
        assert THERMAL_SIGNATURES["EXPANSION_CODE"] == 0.30
        assert THERMAL_SIGNATURES["COFFEE_BREAK"] == -0.30
        assert (
            THERMAL_SIGNATURES["EXPANSION_CODE"]
            + THERMAL_SIGNATURES["COFFEE_BREAK"]
            == 0.0
        )

    def test_audit_survie_is_thermally_neutral(self):
        """V35.0 : AUDIT_SURVIE reste un poll surrenalien, pas un effort
        cognitif. Sa signature doit rester quasi-nulle (<= 0.05) pour que
        le canal STABILITE (peur) ne soit pas confondu avec le canal
        cognitive_heat (fatigue). Veto Jean-Michel V35.0."""
        assert THERMAL_SIGNATURES["AUDIT_SURVIE"] <= 0.05

    def test_repos_routines_are_strict_dissipators(self):
        """Toutes les routines de repos doivent dissiper (delta < 0).
        Sinon le mecanisme V35.1 (pulsion REPOS emergente) ne pourra pas
        leur faire confiance pour faire baisser la chaleur."""
        for repos_intent in ("COFFEE_BREAK", "NAP_MODE", "SAUNA_MODE",
                             "MEMORY_CONSOLIDATION", "MEMORY_CLEANUP"):
            assert THERMAL_SIGNATURES[repos_intent] < 0, (
                f"{repos_intent} devrait dissiper mais delta={THERMAL_SIGNATURES[repos_intent]}"
            )

    def test_heavy_llm_routines_are_strict_producers(self):
        """Les routines a LLM lourd doivent produire (delta > 0)."""
        for llm_intent in ("EXPANSION_CODE", "FEATURE_BUILDING",
                           "CODE_REVIEW", "COUNCIL_DEBATE"):
            assert THERMAL_SIGNATURES[llm_intent] > 0, (
                f"{llm_intent} devrait chauffer mais delta={THERMAL_SIGNATURES[llm_intent]}"
            )
