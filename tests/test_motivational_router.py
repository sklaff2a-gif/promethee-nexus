"""V34 (2026-04-27) — Tests Motivational Router.

Couvre :
  - Mapping pulsion -> routine (CONNEXION = COFFEE_BREAK Alfred)
  - Seuils différenciés (STABILITE plus haut, CONNEXION plus bas)
  - Refractory period (cooldown post-assouvissement)
  - Variety penalty (anti-addiction)
  - Filter par available_intents (intent réellement disponible)
  - Compatibilité format Drive (objet vs dict)
  - Pas de crash si drives vides
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.motivational_router import (
    check_drive_override,
    mark_drive_satisfied,
    get_router_state,
    reset_router_state,
    DRIVE_THRESHOLDS,
    PULSION_TO_ROUTINES,
    REFRACTORY_PERIOD_S,
    VARIETY_THRESHOLD_CONSECUTIVE,
    VARIETY_PENALTY_FACTOR,
    RoutineOverride,
    _state,
)


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset l'état du singleton _state avant CHAQUE test."""
    reset_router_state()
    yield
    reset_router_state()


# ═══════════════════════════════════════════════════════════════════════
# 1. Pas d'override quand toutes les pulsions sont sous seuil
# ═══════════════════════════════════════════════════════════════════════

def test_no_override_when_below_threshold():
    drives = {
        "CREATION": {"deprivation": 5.0},
        "CURIOSITE": {"deprivation": 10.0},
        "MAITRISE": {"deprivation": 3.0},
    }
    result = check_drive_override(drives)
    assert result is None


def test_no_override_when_drives_empty():
    result = check_drive_override({})
    assert result is None


def test_no_override_when_drives_none_values():
    drives = {"CREATION": None, "CURIOSITE": "garbage"}
    result = check_drive_override(drives)
    assert result is None


# ═══════════════════════════════════════════════════════════════════════
# 2. Override déclenché quand pulsion > seuil
# ═══════════════════════════════════════════════════════════════════════

def test_override_creation_above_threshold():
    drives = {"CREATION": {"deprivation": 28.5}}
    result = check_drive_override(drives)
    assert result is not None
    assert isinstance(result, RoutineOverride)
    assert result.triggering_drive == "CREATION"
    assert result.intent in PULSION_TO_ROUTINES["CREATION"]
    assert result.deprivation == 28.5
    assert result.threshold == DRIVE_THRESHOLDS["CREATION"]


def test_connexion_threshold_lower():
    """CONNEXION a un seuil plus bas (20) — déclenche plus vite."""
    drives = {"CONNEXION": {"deprivation": 21.0}}
    result = check_drive_override(drives)
    assert result is not None
    assert result.triggering_drive == "CONNEXION"
    # Mapping CONNEXION = Alfred (COFFEE_BREAK) ou Stefan
    assert result.intent in ("COFFEE_BREAK", "STEFAN_CONFRONTATION")


def test_stabilite_threshold_higher():
    """STABILITE a un seuil plus haut (80) — pas d'override à 75."""
    drives = {"STABILITE": {"deprivation": 75.0}}
    result = check_drive_override(drives)
    assert result is None  # 75 < 80, pas d'override


def test_stabilite_above_80_triggers():
    drives = {"STABILITE": {"deprivation": 85.0}}
    result = check_drive_override(drives)
    assert result is not None
    assert result.triggering_drive == "STABILITE"


# ═══════════════════════════════════════════════════════════════════════
# 3. Compatibilité formats de Drive (objet vs dict)
# ═══════════════════════════════════════════════════════════════════════

class _FakeDrive:
    """Mime un dataclass Drive avec attribut .deprivation."""
    def __init__(self, deprivation: float):
        self.deprivation = deprivation


def test_drive_object_with_attribute():
    drives = {"CREATION": _FakeDrive(deprivation=30.0)}
    result = check_drive_override(drives)
    assert result is not None
    assert result.deprivation == 30.0


def test_drive_dict_format():
    drives = {"CREATION": {"deprivation": 30.0, "other_field": "ignored"}}
    result = check_drive_override(drives)
    assert result is not None
    assert result.deprivation == 30.0


def test_drive_lowercase_normalized():
    """Les noms de pulsion sont normalisés en uppercase."""
    drives = {"creation": _FakeDrive(deprivation=30.0)}
    result = check_drive_override(drives)
    assert result is not None
    assert result.triggering_drive == "CREATION"


# ═══════════════════════════════════════════════════════════════════════
# 4. Filter par available_intents
# ═══════════════════════════════════════════════════════════════════════

def test_filter_by_available_intents():
    """Si la routine candidate n'est PAS dans available_intents, skip."""
    drives = {"CONNEXION": {"deprivation": 25.0}}
    # COFFEE_BREAK et STEFAN_CONFRONTATION non dispo
    result = check_drive_override(drives, available_intents=["AUDIT_STRUCTURE"])
    assert result is None


def test_filter_keeps_first_available():
    """Si seul STEFAN_CONFRONTATION dispo, c'est lui qui est choisi."""
    drives = {"CONNEXION": {"deprivation": 25.0}}
    result = check_drive_override(
        drives, available_intents=["STEFAN_CONFRONTATION"],
    )
    assert result is not None
    assert result.intent == "STEFAN_CONFRONTATION"


# ═══════════════════════════════════════════════════════════════════════
# 5. Refractory period (cooldown post-assouvissement)
# ═══════════════════════════════════════════════════════════════════════

def test_refractory_blocks_same_drive():
    """Après mark_drive_satisfied, la pulsion est en cooldown."""
    drives = {"CREATION": {"deprivation": 30.0}}
    # Premier override : OK
    r1 = check_drive_override(drives)
    assert r1 is not None
    # Marquer comme satisfaite
    mark_drive_satisfied("CREATION")
    # Deuxième tentative : refusée (refractory)
    r2 = check_drive_override(drives)
    assert r2 is None


def test_refractory_does_not_block_other_drive():
    """CREATION en refractory n'empêche pas CURIOSITE de gagner."""
    drives_creation = {"CREATION": {"deprivation": 30.0}}
    check_drive_override(drives_creation)
    mark_drive_satisfied("CREATION")
    # CURIOSITE doit pouvoir déclencher
    drives_curiosite = {
        "CREATION": {"deprivation": 30.0},  # toujours haute mais cooldown
        "CURIOSITE": {"deprivation": 30.0},
    }
    r = check_drive_override(drives_curiosite)
    assert r is not None
    assert r.triggering_drive == "CURIOSITE"


def test_refractory_has_correct_duration():
    """Refractory = 60 minutes par défaut."""
    assert REFRACTORY_PERIOD_S == 3600


# ═══════════════════════════════════════════════════════════════════════
# 6. Variety penalty (anti-addiction)
# ═══════════════════════════════════════════════════════════════════════

def test_variety_counter_increments():
    """Si même pulsion gagne 2 fois consécutifs, counter = 2."""
    drives = {"CREATION": {"deprivation": 30.0}}
    check_drive_override(drives)
    check_drive_override(drives)
    state = get_router_state()
    assert state["current_state"]["consecutive_wins"]["CREATION"] == 2


def test_variety_resets_on_drive_change():
    """Si une autre pulsion gagne, le counter de la précédente reset."""
    check_drive_override({"CREATION": {"deprivation": 30.0}})
    check_drive_override({"CURIOSITE": {"deprivation": 30.0}})
    state = get_router_state()
    # CREATION devrait être à 1 (reset implicite par changement)
    # ou pas dans le dict si reset complet
    assert state["current_state"]["last_override_drive"] == "CURIOSITE"


def test_variety_threshold_constants():
    assert VARIETY_THRESHOLD_CONSECUTIVE == 3
    assert VARIETY_PENALTY_FACTOR == 0.5


# ═══════════════════════════════════════════════════════════════════════
# 7. Mapping CONNEXION = Alfred + Stefan (validation philosophique)
# ═══════════════════════════════════════════════════════════════════════

def test_connexion_maps_to_alfred_and_stefan():
    """CONNEXION doit mapper sur les 2 entités sociales : Alfred (COFFEE_BREAK)
    et Stefan (STEFAN_CONFRONTATION). Ordre de préférence : Alfred d'abord."""
    routines = PULSION_TO_ROUTINES["CONNEXION"]
    assert "COFFEE_BREAK" in routines
    assert "STEFAN_CONFRONTATION" in routines
    # Alfred (pair) avant Stefan (rival) dans l'ordre de préférence
    assert routines.index("COFFEE_BREAK") < routines.index("STEFAN_CONFRONTATION")


def test_creation_maps_to_feature_building():
    routines = PULSION_TO_ROUTINES["CREATION"]
    assert "FEATURE_BUILDING" in routines


def test_all_drives_have_mapping():
    """Toutes les pulsions définies dans DRIVE_THRESHOLDS ont un mapping."""
    for drive in DRIVE_THRESHOLDS.keys():
        assert drive in PULSION_TO_ROUTINES
        assert len(PULSION_TO_ROUTINES[drive]) >= 1


# ═══════════════════════════════════════════════════════════════════════
# 8. RoutineOverride structure
# ═══════════════════════════════════════════════════════════════════════

def test_override_to_dict():
    drives = {"CREATION": {"deprivation": 30.0}}
    r = check_drive_override(drives)
    assert r is not None
    d = r.to_dict()
    assert d["intent"] == r.intent
    assert d["triggering_drive"] == "CREATION"
    assert d["deprivation"] == 30.0
    assert "candidates_considered" in d
    assert "timestamp" in d


def test_history_recorded():
    drives = {"CREATION": {"deprivation": 30.0}}
    check_drive_override(drives)
    state = get_router_state()
    assert state["current_state"]["history_size"] == 1
    assert len(state["history_tail"]) == 1


# ═══════════════════════════════════════════════════════════════════════
# 9. Ordre stable des pulsions (déterminisme)
# ═══════════════════════════════════════════════════════════════════════

def test_priority_order_is_stable():
    """Si plusieurs pulsions au-dessus du seuil, ordre stable."""
    drives = {
        "CREATION": {"deprivation": 30.0},
        "MAITRISE": {"deprivation": 30.0},
        "CONNEXION": {"deprivation": 30.0},
    }
    r = check_drive_override(drives)
    assert r is not None
    # CREATION vient avant MAITRISE et CONNEXION dans DRIVE_THRESHOLDS keys
    assert r.triggering_drive == "CREATION"
