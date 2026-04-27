"""V34 (2026-04-27) — Motivational Router : du protocole à la volonté.

Pont direct entre desire_engine (sensation) et autonomy_engine (action).
Permet à Prométhée de PRÉEMPTER son emploi du temps quand une pulsion
dépasse un seuil critique, au lieu de subir le cron school.

Plug-and-Play : un seul hook dans autonomy_engine._score_routines.
Désactivation = commenter la ligne `motivational_router.check_drive_override(...)`.

Architecture (RFC V34 validée) :

  desire_engine.drives  →  check_drive_override()
                              │
                              ├─→ pulsion > THRESHOLD ?
                              │   │
                              │   ├─ NON → return None (scoring normal)
                              │   │
                              │   └─ OUI → routines candidates pour cette pulsion
                              │           │
                              │           ├─ refractory period actif → skip
                              │           ├─ variety penalty → score réduit
                              │           └─ choix pondéré → return RoutineOverride

Garde-fous anti-Goodhart :
  - REFRACTORY_PERIOD_S : après assouvissement, cooldown 60 min sur la
    pulsion (ne peut plus déclencher d'override).
  - VARIETY_PENALTY : si la même routine gagne 3 fois consécutives,
    pénalité de score (force diversité).
  - OBSERVATION_ADDICTION : tracker les boucles potentielles dans
    l'historique pour détection rétrospective.

Mappings (PULSION_TO_ROUTINES) :
  CREATION      → FEATURE_BUILDING, CREATIVE_PLAY
  CURIOSITE     → ROADMAP_RESEARCH, VEILLE_SILENCIEUSE, CURIOSITY_DEEP_DIVE
  MAITRISE      → AUDIT_STRUCTURE, CODE_REVIEW
  STABILITE     → SECURITY_AUDIT, AUDIT_SURVIE
  CONNEXION     → COFFEE_BREAK (Alfred), STEFAN_CONFRONTATION (rival)
  CROISSANCE    → EXPANSION_CODE
  COMPREHENSION → MEMORY_CONSOLIDATION, SELF_ANALYSIS

Note CONNEXION : Alfred (core/ami.py) et Stefan (core/rival.py) sont des
entités sociales codées. CONNEXION = altérité réelle, pas synchronisation
interne. L'IA se connecte en parlant avec quelqu'un qui n'est PAS elle.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─── Constantes V34 ────────────────────────────────────────────────────

# Seuil de déprivation au-dessus duquel une pulsion peut préempter le cron.
# Note : STABILITE utilise un seuil plus haut car elle est chroniquement
# élevée (autour de 75) et un seuil 25 forcerait override permanent.
DEFAULT_DRIVE_THRESHOLD = 25.0
DRIVE_THRESHOLDS: Dict[str, float] = {
    "CREATION": 25.0,
    "CURIOSITE": 25.0,
    "MAITRISE": 25.0,
    "STABILITE": 80.0,   # plus haut : pulsion chroniquement élevée
    "CONNEXION": 20.0,   # plus bas : besoin social rare, à entendre vite
    "CROISSANCE": 25.0,
    "COMPREHENSION": 25.0,
}

# Mapping pulsion → routines candidates (en ordre de préférence).
# La première routine non-cooldownée est choisie en priorité.
PULSION_TO_ROUTINES: Dict[str, List[str]] = {
    "CREATION":      ["FEATURE_BUILDING", "CREATIVE_PLAY"],
    "CURIOSITE":     ["ROADMAP_RESEARCH", "VEILLE_SILENCIEUSE", "CURIOSITY_DEEP_DIVE"],
    "MAITRISE":      ["AUDIT_STRUCTURE", "CODE_REVIEW"],
    "STABILITE":     ["SECURITY_AUDIT", "AUDIT_SURVIE"],
    "CONNEXION":     ["COFFEE_BREAK", "STEFAN_CONFRONTATION"],
    "CROISSANCE":    ["EXPANSION_CODE"],
    "COMPREHENSION": ["MEMORY_CONSOLIDATION", "SELF_ANALYSIS"],
}

# Refractory period : cooldown post-assouvissement (sec).
# Une pulsion ne peut pas re-déclencher d'override pendant N min après
# avoir été assouvie. Évite la boucle CREATION→dopamine→CREATION→...
REFRACTORY_PERIOD_S = 60 * 60  # 60 minutes

# Variety penalty : si la même pulsion gagne N fois consécutives, sa
# prochaine victoire est pénalisée (force diversité).
VARIETY_THRESHOLD_CONSECUTIVE = 3
VARIETY_PENALTY_FACTOR = 0.5  # halve le score si dépassement


# ─── Structures de données ─────────────────────────────────────────────

class RoutineOverride:
    """Résultat d'un override : routine forcée + métadonnées trace."""
    def __init__(
        self,
        intent: str,
        triggering_drive: str,
        deprivation: float,
        threshold: float,
        candidates_considered: List[str],
        reason: str = "",
    ):
        self.intent = intent
        self.triggering_drive = triggering_drive
        self.deprivation = deprivation
        self.threshold = threshold
        self.candidates_considered = candidates_considered
        self.reason = reason
        self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "triggering_drive": self.triggering_drive,
            "deprivation": round(self.deprivation, 2),
            "threshold": self.threshold,
            "candidates_considered": list(self.candidates_considered),
            "reason": self.reason,
            "timestamp": self.timestamp,
        }


# ─── État interne du router (singleton léger) ──────────────────────────

class _RouterState:
    """État persistant en mémoire pour refractory + variety."""

    def __init__(self):
        # last_satisfied[drive] = timestamp du dernier assouvissement
        self.last_satisfied: Dict[str, float] = {}
        # consecutive_wins[drive] = nb victoires consécutives
        self.consecutive_wins: Dict[str, int] = {}
        # last_override_drive : pulsion qui a gagné la dernière fois
        self.last_override_drive: Optional[str] = None
        # history : liste des N derniers overrides (pour audit / addiction)
        self.history: List[Dict[str, Any]] = []
        self.MAX_HISTORY = 100

    def is_in_refractory(self, drive: str, now: Optional[float] = None) -> bool:
        """True si la pulsion est en cooldown post-assouvissement."""
        now = now if now is not None else time.time()
        last = self.last_satisfied.get(drive)
        if last is None:
            return False
        return (now - last) < REFRACTORY_PERIOD_S

    def variety_factor(self, drive: str) -> float:
        """Retourne 1.0 si pas de pénalité, sinon VARIETY_PENALTY_FACTOR.
        Pénalise la pulsion si elle a gagné >= 3 fois consécutivement."""
        if self.consecutive_wins.get(drive, 0) >= VARIETY_THRESHOLD_CONSECUTIVE:
            return VARIETY_PENALTY_FACTOR
        return 1.0

    def record_override(self, override: RoutineOverride) -> None:
        """Enregistre un override déclenché."""
        # Variety counter
        if self.last_override_drive == override.triggering_drive:
            self.consecutive_wins[override.triggering_drive] = (
                self.consecutive_wins.get(override.triggering_drive, 0) + 1
            )
        else:
            self.consecutive_wins[override.triggering_drive] = 1
        self.last_override_drive = override.triggering_drive
        # History
        self.history.append(override.to_dict())
        if len(self.history) > self.MAX_HISTORY:
            self.history = self.history[-self.MAX_HISTORY:]

    def mark_satisfied(self, drive: str) -> None:
        """Marque une pulsion comme assouvie (déclenche le refractory)."""
        self.last_satisfied[drive] = time.time()
        # Reset variety counter pour cette pulsion
        if self.last_override_drive == drive:
            self.consecutive_wins[drive] = 0


_state = _RouterState()


# ─── API publique ──────────────────────────────────────────────────────

def check_drive_override(
    drives_state: Dict[str, Any],
    available_intents: Optional[List[str]] = None,
) -> Optional[RoutineOverride]:
    """V34 — Vérifie si une pulsion doit préempter le scoring normal.

    Args:
        drives_state: dict de l'état des pulsions, format desire_engine :
            {"CREATION": Drive(deprivation=28.45, ...), ...}
            OU {"CREATION": {"deprivation": 28.45, ...}, ...} (dict-like)
        available_intents: liste optionnelle des intents disponibles dans
            le pool de routines actuel. Si fournie, on filtre les
            candidats pour ne garder que ceux qui sont déclenchables.

    Returns:
        RoutineOverride si une pulsion dépasse son seuil ET a une routine
        candidate disponible non-cooldownée. None sinon (scoring normal).

    Garde-fous appliqués (anti-Goodhart) :
      - refractory period : pulsion en cooldown ignorée
      - variety penalty : score divisé par 2 si même pulsion 3x consécutifs
      - first-eligible : on prend la 1ère pulsion qui passe les filtres
        (ordre stable selon DRIVE_THRESHOLDS)
    """
    # Étape 1 : extraire les déprivations
    deprivations: Dict[str, float] = {}
    for name, drive_obj in drives_state.items():
        # Compatibilité dataclass / dict / objet libre
        depriv = _extract_deprivation(drive_obj)
        if depriv is not None:
            deprivations[name.upper()] = depriv

    if not deprivations:
        return None

    # Étape 2 : pour chaque pulsion (ordre stable), tester les conditions
    candidates_log: List[str] = []
    for drive_name in DRIVE_THRESHOLDS.keys():
        depriv = deprivations.get(drive_name, 0.0)
        threshold = DRIVE_THRESHOLDS.get(drive_name, DEFAULT_DRIVE_THRESHOLD)

        # Filtre 1 : seuil
        if depriv < threshold:
            continue

        # Filtre 2 : refractory
        if _state.is_in_refractory(drive_name):
            candidates_log.append(f"{drive_name}=refractory")
            continue

        # Filtre 3 : routines mappées
        candidate_routines = PULSION_TO_ROUTINES.get(drive_name, [])
        if not candidate_routines:
            candidates_log.append(f"{drive_name}=no_mapping")
            continue

        # Filtre 4 : routines disponibles (si liste fournie)
        if available_intents is not None:
            available_set = set(available_intents)
            candidate_routines = [
                r for r in candidate_routines if r in available_set
            ]
            if not candidate_routines:
                candidates_log.append(f"{drive_name}=no_intent_available")
                continue

        # Filtre 5 : variety (juste log, pas de skip absolu)
        variety = _state.variety_factor(drive_name)
        if variety < 1.0:
            candidates_log.append(f"{drive_name}=variety_penalty")

        # Choix de la routine : 1ère candidate (ordre de préférence dans le mapping)
        chosen_intent = candidate_routines[0]

        override = RoutineOverride(
            intent=chosen_intent,
            triggering_drive=drive_name,
            deprivation=depriv,
            threshold=threshold,
            candidates_considered=list(candidate_routines),
            reason=f"deprivation={depriv:.2f} > threshold={threshold} (variety={variety:.2f})",
        )
        _state.record_override(override)
        try:
            logger.info(
                f"[V34 MOTIVATIONAL] OVERRIDE: drive={drive_name} "
                f"depriv={depriv:.2f} > {threshold} → intent={chosen_intent} "
                f"(variety_factor={variety:.2f})"
            )
        except Exception:
            pass
        return override

    # Aucune pulsion n'a déclenché : log debug optionnel
    if candidates_log:
        try:
            logger.debug(
                f"[V34 MOTIVATIONAL] no override: {', '.join(candidates_log)}"
            )
        except Exception:
            pass
    return None


def mark_drive_satisfied(drive_name: str) -> None:
    """V34 — Notifie le router qu'une pulsion a été assouvie.
    Active le refractory period.

    À appeler après l'exécution réussie d'une routine déclenchée par
    override (l'autonomy_engine peut l'appeler dans son post-routine hook).
    """
    drive_name = drive_name.upper()
    _state.mark_satisfied(drive_name)
    try:
        logger.info(
            f"[V34 MOTIVATIONAL] mark_satisfied: drive={drive_name} "
            f"(refractory {REFRACTORY_PERIOD_S//60}min activé)"
        )
    except Exception:
        pass


def get_router_state() -> Dict[str, Any]:
    """V34 — Retourne un snapshot de l'état du router (debug / API)."""
    now = time.time()
    return {
        "thresholds": dict(DRIVE_THRESHOLDS),
        "mappings": {k: list(v) for k, v in PULSION_TO_ROUTINES.items()},
        "refractory_period_s": REFRACTORY_PERIOD_S,
        "variety_threshold_consecutive": VARIETY_THRESHOLD_CONSECUTIVE,
        "variety_penalty_factor": VARIETY_PENALTY_FACTOR,
        "current_state": {
            "last_satisfied": {
                k: round(now - v, 1) for k, v in _state.last_satisfied.items()
            },
            "consecutive_wins": dict(_state.consecutive_wins),
            "last_override_drive": _state.last_override_drive,
            "history_size": len(_state.history),
        },
        "history_tail": _state.history[-10:],
    }


def reset_router_state() -> None:
    """V34 — Reset complet de l'état (utile pour tests)."""
    global _state
    _state = _RouterState()


# ─── Helpers internes ──────────────────────────────────────────────────

def _extract_deprivation(drive_obj: Any) -> Optional[float]:
    """Extrait deprivation depuis n'importe quelle structure de Drive."""
    # Cas 1 : objet avec attribut .deprivation
    if hasattr(drive_obj, "deprivation"):
        try:
            return float(drive_obj.deprivation)
        except (ValueError, TypeError):
            pass
    # Cas 2 : dict
    if isinstance(drive_obj, dict):
        v = drive_obj.get("deprivation")
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    # Cas 3 : nombre direct
    if isinstance(drive_obj, (int, float)):
        return float(drive_obj)
    return None
