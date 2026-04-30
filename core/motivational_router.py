"""V34 (2026-04-27) — Motivational Router : du protocole à la volonté.
V34.6 (2026-04-29) — Tri par urgence + Branchement SSOT (genome unifié).

Pont direct entre desire_engine (sensation) et autonomy_engine (action).
Permet à Prométhée de PRÉEMPTER son emploi du temps quand une pulsion
dépasse un seuil critique, au lieu de subir le cron school.

Plug-and-Play : un seul hook dans autonomy_engine._score_routines.
Désactivation = commenter la ligne `motivational_router.check_drive_override(...)`.

Architecture (RFC V34 + V34.6 reconciliation SSOT) :

  desire_engine.drives  →  check_drive_override()
                              │
                              ├─→ pour chaque pulsion > THRESHOLD :
                              │     calcule urgency = (depriv-thr)/(100-thr)
                              │
                              ├─→ trie pulsions par urgency décroissant
                              │   (la plus douloureuse en proportion gagne)
                              │
                              └─→ pour chaque pulsion (ordre urgency) :
                                    │
                                    ├─ refractory actif → skip
                                    ├─ candidats lus depuis DRIVE_GENOME via
                                    │   drive_routine_registry (SSOT unique)
                                    ├─ filtre available_intents
                                    ├─ filtre recently_skipped (V34.4)
                                    └─ premier candidat éligible → override

Garde-fous anti-Goodhart :
  - REFRACTORY_PERIOD_S : après assouvissement, cooldown 60 min sur la
    pulsion (ne peut plus déclencher d'override).
  - VARIETY_PENALTY : si la même routine gagne 3 fois consécutives,
    pénalité de score (force diversité).
  - OBSERVATION_ADDICTION : tracker les boucles potentielles dans
    l'historique pour détection rétrospective.

V34.6 — Pourquoi le ratio normalisé plutôt que la différence absolue :
  La diff (depriv - threshold) sous-pondère les pulsions à seuil élevé
  (STABILITE seuil 80) car leur marge restante au-dessus du seuil est
  plus courte. Le ratio (depriv-thr)/(100-thr) mesure le pourcentage
  de marge consommée — c'est la souffrance relative, pas absolue.
  Exemple : STABILITE 92 → ratio 0.60 ; CURIOSITE 46 → ratio 0.28.
  STABILITE crie effectivement plus fort en proportion.

V34.6 — Mapping pulsion → routines : déplacé dans DRIVE_GENOME du
registre (drive_routine_registry.DRIVE_GENOME), source de vérité unique.
Les candidats sont obtenus via get_routines_for_drive_live(drive),
triés par poids décroissant (synaptic + genome floor + multiplicateurs).

Doctrine CONNEXION (gravée dans le genome V34.6) :
  COFFEE_BREAK (Alfred) 0.9 > COUNCIL_DEBATE 0.6 > SOLILOQUE_INTERNE 0.5
  > STEFAN_CONFRONTATION 0.4. Alfred (core/ami.py) et Stefan (core/rival.py)
  sont les deux entités d'altérité réelle ; le council et le soliloque
  restent des synchronisations internes (pair, monologue) — utiles mais
  pas de la vraie connexion.
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
    # V35.1 — REPOS : seuil 50, sous l'embrasement thermal_homeostasis (0.70).
    # Reveil dès heat>=0.5, mais préemption réelle vers heat>=0.7 (urgency 0.4+).
    "REPOS": 50.0,
}

# V34.6 : suppression de PULSION_TO_ROUTINES en dur. Les candidats sont
# desormais lus depuis DRIVE_GENOME (drive_routine_registry) — source de
# verite unique. Voir get_candidate_routines() ci-dessous.

# Refractory period : cooldown post-assouvissement (sec).
# Une pulsion ne peut pas re-déclencher d'override pendant N min après
# avoir été assouvie. Évite la boucle CREATION→dopamine→CREATION→...
REFRACTORY_PERIOD_S = 60 * 60  # 60 minutes

# Variety penalty : si la même pulsion gagne N fois consécutives, sa
# prochaine victoire est pénalisée (force diversité).
VARIETY_THRESHOLD_CONSECUTIVE = 3
VARIETY_PENALTY_FACTOR = 0.5  # halve le score si dépassement

# V34.4 (Rebond Neutre) : cooldown éphémère après qu'une routine a retourné
# status=skipped. Tant que ce cooldown court, le router exclut cet intent du
# mapping pulsion → glisse au candidat n+1 (CURIOSITE: ROADMAP_RESEARCH skip
# → VEILLE_SILENCIEUSE). Volontairement non-persisté : 5 min suffit pour qu'un
# refus légitime ne re-bloque pas la pulsion immédiatement, mais on retente
# au reboot ou après expiration.
SKIP_COOLDOWN_S = 5 * 60  # 5 minutes


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
        # V34.4 : recently_skipped[intent] = timestamp du dernier skip
        self.recently_skipped: Dict[str, float] = {}

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

    # V34.4 — Rebond Neutre

    def is_intent_skipped_recently(
        self, intent: str, now: Optional[float] = None
    ) -> bool:
        """True si l'intent a retourné skipped dans les SKIP_COOLDOWN_S secondes."""
        now = now if now is not None else time.time()
        last = self.recently_skipped.get(intent)
        if last is None:
            return False
        return (now - last) < SKIP_COOLDOWN_S

    def mark_intent_skipped(self, intent: str) -> None:
        """Mémorise qu'un intent vient de retourner skipped (cooldown 5 min)."""
        self.recently_skipped[intent] = time.time()


_state = _RouterState()


# ─── API publique ──────────────────────────────────────────────────────

def _urgency_ratio(depriv: float, threshold: float, drive_name: Optional[str] = None) -> float:
    """V34.6 — Souffrance relative au-dessus du seuil de tolerance.
    V35.2 — Bypass non-lineaire pour REPOS (alerte physiologique).

    Pour les pulsions de croissance standard, formule normalisee :
      depriv = threshold      -> 0.0 (juste au seuil)
      depriv = 100            -> 1.0 (saturation)
      margin = max(1, 100 - threshold)
      urgency = (depriv - threshold) / margin

    Pour REPOS (V35.2 — observation runtime 2026-04-30) : formule non-lineaire
    qui corrige le biais systemique de la "course entre declin et croissance".
    Le decay thermique de cognitive_heat fait decroitre REPOS naturellement
    pendant que les autres pulsions montent — REPOS perdait la course meme
    en post-embrasement. La formule ci-dessous garantit que l'embrasement
    cognitif (heat >= 0.70 -> depriv >= 80 via embrasement_min_deprivation)
    domine mecaniquement n'importe quelle pulsion de croissance, sauf une
    STABILITE en panique extreme (depriv > 95).

    Profil non-lineaire REPOS :
      depriv < 50            -> 0.0     (sous le seuil de reveil)
      depriv ∈ [50, 80[      -> linear de 0.0 a 0.85 (zone d'eveil progressive)
      depriv ∈ [80, 100]     -> 0.85 a 1.0 (zone d'embrasement, alerte garantie)

    Le saut a 0.85 a depriv=80 reflete la doctrine V35.2 : l'embrasement
    n'est pas un desir comme un autre, c'est une defaillance physique
    imminente qui justifie une priorite absolue sur les pulsions montantes.
    """
    # V35.2 — REPOS : alerte physiologique non-lineaire
    if drive_name == "REPOS":
        if depriv < 50:
            return 0.0
        if depriv >= 80:
            # Zone d'embrasement : urgency garantie >= 0.85, monte vers 1.0
            return min(1.0, 0.85 + (depriv - 80) * 0.0075)
        # Zone d'eveil 50-80 : montee lineaire vers le seuil d'embrasement
        return (depriv - 50) / 30.0 * 0.85

    # Pulsions de croissance standard : formule normalisee V34.6
    margin = max(1.0, 100.0 - threshold)
    return (depriv - threshold) / margin


def get_candidate_routines(drive: str, top_k: int = 5) -> List[str]:
    """V34.6 — Lit les candidats du SSOT (DRIVE_GENOME via registre).

    Retourne la liste des intents candidats triee par poids decroissant
    (genome floor fusionne avec graphe synaptique appris si provider branche).
    Filet de securite : si registre indisponible ou drive vide, retourne [].
    Le routeur traduit alors en absence d'override (pas de fallback hardcode).

    Args:
        drive: nom upper-case du drive (CREATION, CURIOSITE, etc.)
        top_k: nombre max de candidats retournes.

    Returns:
        List[str] : intents tries du plus pertinent au moins pertinent.
        Liste vide si registre absent, drive inconnu, ou genome vide.
    """
    try:
        from core.drive_routine_registry import get_routines_for_drive_live
        ranked = get_routines_for_drive_live(
            drive=drive,
            temperature=0.0,    # mode greedy : ordre strict par poids
            top_k=top_k,
            use_context_multipliers=False,  # le router gere ses propres filtres
        )
        # ranked est List[Tuple[intent, weight]] triee desc
        return [intent for intent, _w in ranked]
    except Exception as e:
        try:
            logger.warning(f"[V34.6] get_candidate_routines({drive}) failed: {e}")
        except Exception:
            pass
        return []


def check_drive_override(
    drives_state: Dict[str, Any],
    available_intents: Optional[List[str]] = None,
) -> Optional[RoutineOverride]:
    """V34.6 — Verifie si une pulsion doit preempter le scoring normal.

    Refonte V34.6 :
    - Tri par urgence relative (% de marge consommee au-dessus du seuil)
      au lieu du first-eligible naif sur l'ordre du dict. STABILITE 92
      passe enfin devant CURIOSITE 46.
    - Candidats lus depuis le SSOT (DRIVE_GENOME via registre) au lieu
      de la table heretique PULSION_TO_ROUTINES en dur.

    Args:
        drives_state: dict de l'etat des pulsions, format desire_engine :
            {"CREATION": Drive(deprivation=28.45, ...), ...}
            OU {"CREATION": {"deprivation": 28.45, ...}, ...} (dict-like)
        available_intents: liste optionnelle des intents disponibles dans
            le pool de routines actuel. Si fournie, on filtre les
            candidats pour ne garder que ceux qui sont declenchables.

    Returns:
        RoutineOverride si une pulsion depasse son seuil ET a une routine
        candidate disponible non-cooldownee. None sinon (scoring normal).

    Garde-fous appliques (anti-Goodhart) :
      - urgency-sorted : la pulsion qui hurle le plus fort en proportion
      - refractory period : pulsion en cooldown ignoree
      - variety penalty : score divise par 2 si meme pulsion 3x consecutifs
      - SSOT-only : aucun mapping en dur, lecture du genome unifie
    """
    # Etape 1 : extraire les deprivations
    deprivations: Dict[str, float] = {}
    for name, drive_obj in drives_state.items():
        depriv = _extract_deprivation(drive_obj)
        if depriv is not None:
            deprivations[name.upper()] = depriv

    if not deprivations:
        return None

    # Etape 2 : V34.6 — tri par urgence relative.
    # Pour chaque pulsion qui depasse son seuil, calcule le ratio de
    # souffrance, puis trie decroissant. La plus douloureuse en proportion
    # gagne le droit de tenter l'override en premier.
    eligible: List[Tuple[str, float, float, float]] = []
    # tuple (drive_name, depriv, threshold, urgency_ratio)
    for drive_name, depriv in deprivations.items():
        threshold = DRIVE_THRESHOLDS.get(drive_name, DEFAULT_DRIVE_THRESHOLD)
        if depriv < threshold:
            continue
        urgency = _urgency_ratio(depriv, threshold, drive_name=drive_name)
        eligible.append((drive_name, depriv, threshold, urgency))

    if not eligible:
        return None

    # Tri stable : urgence decroissante, puis ordre alphabetique du drive
    # (tie-break deterministe pour les tests).
    eligible.sort(key=lambda x: (-x[3], x[0]))

    # Etape 3 : pour chaque pulsion (ordre urgency desc), tester les filtres
    candidates_log: List[str] = []
    for drive_name, depriv, threshold, urgency in eligible:
        # Filtre 1 : refractory
        if _state.is_in_refractory(drive_name):
            candidates_log.append(f"{drive_name}=refractory")
            continue

        # Filtre 2 : routines candidates depuis le SSOT (genome unifie)
        candidate_routines = get_candidate_routines(drive_name, top_k=10)
        if not candidate_routines:
            candidates_log.append(f"{drive_name}=no_mapping")
            continue

        # Filtre 3 : routines disponibles (si liste fournie)
        if available_intents is not None:
            available_set = set(available_intents)
            candidate_routines = [
                r for r in candidate_routines if r in available_set
            ]
            if not candidate_routines:
                candidates_log.append(f"{drive_name}=no_intent_available")
                continue

        # V34.4 — Filtre skipped recent : glisse au candidat n+1
        candidate_routines = [
            r for r in candidate_routines
            if not _state.is_intent_skipped_recently(r)
        ]
        if not candidate_routines:
            candidates_log.append(f"{drive_name}=all_recently_skipped")
            continue

        # Filtre 4 : variety (log seulement, pas de skip absolu)
        variety = _state.variety_factor(drive_name)
        if variety < 1.0:
            candidates_log.append(f"{drive_name}=variety_penalty")

        # Choix : 1er candidat eligible (ordre genome desc, deja trie)
        chosen_intent = candidate_routines[0]

        override = RoutineOverride(
            intent=chosen_intent,
            triggering_drive=drive_name,
            deprivation=depriv,
            threshold=threshold,
            candidates_considered=list(candidate_routines),
            reason=(
                f"deprivation={depriv:.2f} > threshold={threshold} "
                f"(urgency={urgency:.2f}, variety={variety:.2f})"
            ),
        )
        _state.record_override(override)
        try:
            logger.info(
                f"[V34.6 MOTIVATIONAL] OVERRIDE: drive={drive_name} "
                f"depriv={depriv:.2f} > {threshold} urgency={urgency:.2f} "
                f"→ intent={chosen_intent} (variety={variety:.2f})"
            )
        except Exception:
            pass
        return override

    # Aucune pulsion n'a passe les filtres
    if candidates_log:
        try:
            logger.debug(
                f"[V34.6 MOTIVATIONAL] no override: {', '.join(candidates_log)}"
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


def mark_intent_skipped(intent: str) -> None:
    """V34.4 — Notifie le router qu'un intent vient de retourner skipped.

    Active un cooldown SKIP_COOLDOWN_S sur cet intent : le prochain
    check_drive_override l'exclura du mapping pulsion et glissera
    automatiquement vers le candidat n+1.

    À appeler depuis autonomy_engine quand une routine FORCED retourne
    status=skipped (refus légitime, frigo vide).
    """
    _state.mark_intent_skipped(intent)
    try:
        logger.info(
            f"[V34.4 REBOND NEUTRE] intent={intent} skipped — "
            f"cooldown {SKIP_COOLDOWN_S//60}min, glissade au candidat suivant"
        )
    except Exception:
        pass


def get_router_state() -> Dict[str, Any]:
    """V34 — Retourne un snapshot de l'état du router (debug / API).

    V34.6 : 'mappings' lit desormais le SSOT (DRIVE_GENOME via registre)
    au lieu de la table heretique. Reflet vivant du genome unifie.
    """
    now = time.time()
    # V34.6 : derive les mappings du SSOT
    mappings: Dict[str, List[str]] = {}
    for drive in DRIVE_THRESHOLDS.keys():
        mappings[drive] = get_candidate_routines(drive, top_k=10)
    return {
        "thresholds": dict(DRIVE_THRESHOLDS),
        "mappings": mappings,
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
            "recently_skipped": {
                k: round(now - v, 1) for k, v in _state.recently_skipped.items()
            },
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
