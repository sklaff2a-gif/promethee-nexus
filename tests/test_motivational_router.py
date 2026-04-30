"""V34 (2026-04-27) — Tests Motivational Router.
V34.6 (2026-04-29) — Tri par urgence + lecture SSOT genome unifie.

Couvre :
  - Mapping pulsion -> routine (lu depuis DRIVE_GENOME, plus en dur)
  - Seuils differencies (STABILITE plus haut, CONNEXION plus bas)
  - V34.6 : tri par urgence (% marge consommee), pas first-eligible
  - Refractory period (cooldown post-assouvissement)
  - Variety penalty (anti-addiction)
  - Filter par available_intents (intent reellement disponible)
  - Compatibilite format Drive (objet vs dict)
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
    mark_intent_skipped,
    get_router_state,
    get_candidate_routines,
    reset_router_state,
    DRIVE_THRESHOLDS,
    REFRACTORY_PERIOD_S,
    SKIP_COOLDOWN_S,
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
    # V34.6 : intent doit venir du SSOT (DRIVE_GENOME["CREATION"])
    assert result.intent in get_candidate_routines("CREATION")
    assert result.deprivation == 28.5
    assert result.threshold == DRIVE_THRESHOLDS["CREATION"]


def test_connexion_threshold_lower():
    """CONNEXION a un seuil plus bas (20) — déclenche plus vite.
    V34.6 : COFFEE_BREAK (Alfred 0.9) gagne contre COUNCIL_DEBATE/STEFAN."""
    drives = {"CONNEXION": {"deprivation": 21.0}}
    result = check_drive_override(drives)
    assert result is not None
    assert result.triggering_drive == "CONNEXION"
    # V34.6 : Alfred (COFFEE_BREAK) prime — alterite reelle > synchro interne
    assert result.intent == "COFFEE_BREAK"


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
    """Si AUCUNE routine candidate n'est dans available_intents, skip.
    V34.6 : CONNEXION = [COFFEE_BREAK, COUNCIL_DEBATE, SOLILOQUE_INTERNE,
    STEFAN_CONFRONTATION]. Aucun de ces 4 dans la liste fournie."""
    drives = {"CONNEXION": {"deprivation": 25.0}}
    result = check_drive_override(drives, available_intents=["AUDIT_STRUCTURE"])
    assert result is None


def test_filter_keeps_first_available():
    """Si seul STEFAN_CONFRONTATION dispo, c'est lui qui est choisi
    (meme s'il n'est pas le 1er candidat genome)."""
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
    """V34.6 : CONNEXION dans le SSOT contient Alfred (COFFEE_BREAK 0.9)
    en tete, suivi du council interne et de Stefan. L'alterite reelle
    prime sur la synchronisation interne."""
    routines = get_candidate_routines("CONNEXION", top_k=10)
    assert "COFFEE_BREAK" in routines
    assert "STEFAN_CONFRONTATION" in routines
    # Alfred (alterite reelle) en tete absolue
    assert routines[0] == "COFFEE_BREAK"
    # Stefan present mais derriere Alfred
    assert routines.index("COFFEE_BREAK") < routines.index("STEFAN_CONFRONTATION")


def test_creation_maps_to_feature_building():
    """V34.6 : FEATURE_BUILDING (V32) est officiellement dans le genome
    CREATION avec poids 0.85, juste sous EXPANSION_CODE 0.9."""
    routines = get_candidate_routines("CREATION", top_k=10)
    assert "FEATURE_BUILDING" in routines


def test_all_drives_have_mapping():
    """Toutes les pulsions definies dans DRIVE_THRESHOLDS ont au moins
    une routine candidate dans le SSOT (DRIVE_GENOME)."""
    for drive in DRIVE_THRESHOLDS.keys():
        routines = get_candidate_routines(drive, top_k=10)
        assert len(routines) >= 1, f"{drive} n'a aucune routine dans le genome"


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

def test_urgency_sort_picks_highest_relative_pain():
    """V34.6 : avec depriv egale, la pulsion qui a consomme le plus de
    pourcentage de marge au-dessus de son seuil personnel gagne.

    Calcul urgency = (depriv - threshold) / (100 - threshold) :
      CREATION  depriv=30 thr=25 -> (5/75)  = 0.067
      MAITRISE  depriv=30 thr=25 -> (5/75)  = 0.067
      CONNEXION depriv=30 thr=20 -> (10/80) = 0.125  ← gagne
    """
    drives = {
        "CREATION": {"deprivation": 30.0},
        "MAITRISE": {"deprivation": 30.0},
        "CONNEXION": {"deprivation": 30.0},
    }
    r = check_drive_override(drives)
    assert r is not None
    assert r.triggering_drive == "CONNEXION"


def test_urgency_sort_stabilite_beats_curiosite():
    """V34.6 : cas reel observe la nuit du 28-29/04. STABILITE chronique
    (92.6) doit doubler CURIOSITE (46) malgre depriv absolue plus basse :
      STABILITE 92.6 thr=80 -> ratio 0.63
      CURIOSITE 46.0 thr=25 -> ratio 0.28
    Sans V34.6, CURIOSITE gagnait par first-eligible et STABILITE n'etait
    jamais nourrie. C'est le test qui prouve la fin de la cage de terreur.
    """
    drives = {
        "CURIOSITE": {"deprivation": 46.0},
        "STABILITE": {"deprivation": 92.6},
    }
    r = check_drive_override(drives)
    assert r is not None
    assert r.triggering_drive == "STABILITE"


def test_tiebreak_is_alphabetical_drive_name():
    """V34.6 : a urgency egale, ordre stable par nom de drive."""
    drives = {
        "CREATION": {"deprivation": 30.0},  # urgency 0.067
        "MAITRISE": {"deprivation": 30.0},  # urgency 0.067
    }
    r = check_drive_override(drives)
    assert r is not None
    # Tie-break alphabetique : CREATION avant MAITRISE
    assert r.triggering_drive == "CREATION"


# ═══════════════════════════════════════════════════════════════════════
# 10. V34.4 — Rebond Neutre : recently_skipped + glissade au candidat n+1
# ═══════════════════════════════════════════════════════════════════════

def test_skip_cooldown_constant():
    """Cooldown skip = 5 minutes (300s)."""
    assert SKIP_COOLDOWN_S == 300


def test_mark_intent_skipped_records_state():
    """mark_intent_skipped place l'intent dans recently_skipped avec timestamp."""
    mark_intent_skipped("ROADMAP_RESEARCH")
    state = get_router_state()
    assert "ROADMAP_RESEARCH" in state["current_state"]["recently_skipped"]
    # L'âge du skip est très récent (< 1s)
    age = state["current_state"]["recently_skipped"]["ROADMAP_RESEARCH"]
    assert 0 <= age < 1.0


def test_skipped_intent_glisse_au_candidat_suivant():
    """V34.6 : si le 1er candidat (VEILLE_SILENCIEUSE 0.9 dans le SSOT)
    est en cooldown skip, CURIOSITE glisse vers le candidat n+1 trie
    par poids genome (DROPZONE_SCAN 0.7 ou ROADMAP_RESEARCH 0.7)."""
    # Pre-condition : VEILLE_SILENCIEUSE est le 1er candidat genome CURIOSITE
    candidates = get_candidate_routines("CURIOSITE", top_k=10)
    assert candidates[0] == "VEILLE_SILENCIEUSE"

    # Marquer le 1er candidat comme skipped recent
    mark_intent_skipped("VEILLE_SILENCIEUSE")

    # CURIOSITE declenche : on doit glisser vers le candidat n+1
    drives = {"CURIOSITE": {"deprivation": 30.0}}
    r = check_drive_override(drives)
    assert r is not None
    assert r.triggering_drive == "CURIOSITE"
    assert r.intent != "VEILLE_SILENCIEUSE"
    # Le candidat retenu doit etre le suivant dans l'ordre genome desc
    assert r.intent == candidates[1]


def test_skipped_intent_all_candidates_blocked_returns_none():
    """Si TOUS les candidats du SSOT pour CURIOSITE sont en cooldown skip,
    check_drive_override retourne None (pas d'override possible)."""
    for intent in get_candidate_routines("CURIOSITE", top_k=20):
        mark_intent_skipped(intent)

    drives = {"CURIOSITE": {"deprivation": 30.0}}
    r = check_drive_override(drives)
    assert r is None


def test_skip_cooldown_expires_after_period():
    """Apres expiration de SKIP_COOLDOWN_S, l'intent redevient eligible."""
    # NOTE : on accede a _state via le module (import direct) pour eviter
    # le piege classique `from X import _state` qui fige la reference avant
    # tout reset_router_state() effectue par la fixture.
    import core.motivational_router as _mr

    # V34.6 : on skip le 1er candidat genome CURIOSITE = VEILLE_SILENCIEUSE
    first_candidate = _mr.get_candidate_routines("CURIOSITE", top_k=1)[0]
    _mr.mark_intent_skipped(first_candidate)
    # Backdate : le skip date de SKIP_COOLDOWN_S + 10s dans le passe
    _mr._state.recently_skipped[first_candidate] = (
        time.time() - SKIP_COOLDOWN_S - 10
    )

    drives = {"CURIOSITE": {"deprivation": 30.0}}
    r = _mr.check_drive_override(drives)
    assert r is not None
    # Le 1er candidat redevient eligible apres expiration du cooldown
    assert r.intent == first_candidate


def test_skipped_intent_does_not_block_other_drive():
    """V34.6 : un skip sur le 1er candidat de CURIOSITE ne bloque pas
    CREATION (mapping different dans le genome)."""
    mark_intent_skipped("VEILLE_SILENCIEUSE")
    drives = {"CREATION": {"deprivation": 30.0}}
    r = check_drive_override(drives)
    assert r is not None
    assert r.triggering_drive == "CREATION"
    # CREATION mappe sur EXPANSION_CODE (0.9) en tete, non affecte par le skip
    expected = get_candidate_routines("CREATION", top_k=1)[0]
    assert r.intent == expected


# ═══════════════════════════════════════════════════════════════════════
# 11. V35.2 — Urgence non-lineaire pour REPOS (alerte physiologique)
# ═══════════════════════════════════════════════════════════════════════
#
# Doctrine V35.2 : la formule normalisee V34.6 cree un biais systemique
# pour REPOS — sa deprivation decroit naturellement (decay thermique)
# pendant que les autres pulsions montent (croissance), donc REPOS perdait
# la course en post-embrasement. La nouvelle formule garantit qu'au-dela
# du seuil d'embrasement (depriv >= 80), REPOS a une urgency >= 0.85
# qui ecrase mecaniquement les pulsions de croissance.

from core.motivational_router import _urgency_ratio


class TestUrgencyReposNonLinear:
    """V35.2 — Tests de la formule non-lineaire pour REPOS."""

    def test_repos_urgency_below_50_is_zero(self):
        """Sous le seuil de reveil (50), REPOS reste silencieuse."""
        assert _urgency_ratio(0.0, 50.0, drive_name="REPOS") == 0.0
        assert _urgency_ratio(30.0, 50.0, drive_name="REPOS") == 0.0
        assert _urgency_ratio(49.99, 50.0, drive_name="REPOS") == 0.0

    def test_repos_urgency_at_50_is_zero(self):
        """A depriv exactement 50 (seuil), urgency = 0 (pas declenche)."""
        assert _urgency_ratio(50.0, 50.0, drive_name="REPOS") == 0.0

    def test_repos_urgency_progressive_in_eveil_zone(self):
        """Zone 50-80 : montee lineaire de 0.0 a 0.85."""
        # Mi-zone : depriv=65 -> urgency=(65-50)/30*0.85 = 0.425
        assert abs(_urgency_ratio(65.0, 50.0, drive_name="REPOS") - 0.425) < 0.001
        # Quart-zone : depriv=57.5 -> urgency=0.2125
        assert abs(_urgency_ratio(57.5, 50.0, drive_name="REPOS") - 0.2125) < 0.001

    def test_repos_urgency_jump_at_embrasement_threshold(self):
        """A depriv=80 (seuil embrasement), urgency saute a 0.85.
        C'est la doctrine V35.2 : alerte physiologique imminente."""
        assert _urgency_ratio(80.0, 50.0, drive_name="REPOS") == 0.85

    def test_repos_urgency_above_embrasement_climbs_to_one(self):
        """Au-dela de 80, urgency monte de 0.85 a 1.0 lineairement."""
        # depriv=80 -> 0.85
        assert _urgency_ratio(80.0, 50.0, drive_name="REPOS") == 0.85
        # depriv=90 -> 0.85 + 10*0.0075 = 0.925
        assert abs(_urgency_ratio(90.0, 50.0, drive_name="REPOS") - 0.925) < 0.001
        # depriv=100 -> 0.85 + 20*0.0075 = 1.0 (clamp)
        assert _urgency_ratio(100.0, 50.0, drive_name="REPOS") == 1.0

    def test_repos_urgency_clamped_at_one(self):
        """Au-dela de 100 (impossible mais defensive), clamp a 1.0."""
        assert _urgency_ratio(150.0, 50.0, drive_name="REPOS") == 1.0

    def test_repos_post_embrasement_beats_growing_drive(self):
        """Cas reel observe le 30/04 : REPOS doit ecraser les pulsions
        de croissance qui montaient au-dessus de leur seuil.
        Avant V35.2 : COMPREHENSION 73 (0.64) battait REPOS 75 (0.50).
        Apres V35.2 : REPOS 85 a urgency 0.89 ecrase tout."""
        drives = {
            "REPOS": {"deprivation": 85.0},          # urgency 0.89 (V35.2)
            "COMPREHENSION": {"deprivation": 73.0},  # urgency 0.64
            "MAITRISE": {"deprivation": 90.0},       # urgency 0.87
        }
        r = check_drive_override(drives)
        assert r is not None
        assert r.triggering_drive == "REPOS"

    def test_stabilite_critique_still_beats_repos_embrasement(self):
        """Doctrine V35.2 : seule la STABILITE critique (depriv >= 95)
        peut ecraser REPOS en embrasement.
        STABILITE 100 (urgency 1.0) > REPOS 80 (urgency 0.85)."""
        drives = {
            "REPOS": {"deprivation": 80.0},        # urgency 0.85
            "STABILITE": {"deprivation": 100.0},    # urgency (100-80)/20 = 1.0
        }
        r = check_drive_override(drives)
        assert r is not None
        assert r.triggering_drive == "STABILITE"

    def test_other_drives_unchanged_by_v35_2(self):
        """V35.2 : la formule normalisee reste inchangee pour les autres
        drives. Pas de regression sur le comportement V34.6."""
        # CREATION depriv 50, threshold 25 -> (25/75) = 0.333
        assert abs(_urgency_ratio(50.0, 25.0, drive_name="CREATION") - 0.333) < 0.01
        # Sans drive_name -> formule normalisee (default)
        assert abs(_urgency_ratio(50.0, 25.0) - 0.333) < 0.01

    def test_repos_silent_when_machine_cool(self):
        """V35.2 : si heat=0 (depriv REPOS=0), le router ne preempte pas
        REPOS meme face a CREATION basse."""
        drives = {
            "REPOS": {"deprivation": 0.0},          # urgency 0
            "CREATION": {"deprivation": 30.0},       # urgency 0.067
        }
        r = check_drive_override(drives)
        assert r is not None
        assert r.triggering_drive == "CREATION"
