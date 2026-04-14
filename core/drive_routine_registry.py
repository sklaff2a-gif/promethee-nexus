"""
drive_routine_registry.py — Single Source of Truth pour drive -> routine (Phase C).

Ce module remplacera progressivement les 3 tables heretiques de la V1 :
- prefrontal._DRIVE_ROUTINE_MAP
- desires.DRIVE_ROUTINE_AFFINITY
- council_analytics.DRIVE_INTENT_MAP

Principe architectural (3 couches distinctes) :
1. Le graphe synaptique (appris dynamiquement, Hebbian causal)
2. Le DRIVE_GENOME (plancher fixe avec depreciation metabolique)
3. Les modulateurs (multiplicateurs de poids, jamais selecteurs)

Temps metabolique (Gemini, Phase C 2026-04-13) :
La depreciation genomique utilise current_cycle - entry_cycle depuis
experience_clock, PAS time.time(). Les cycles sont incrementes uniquement
sur evenements causalement signes (PREFRONTAL_GOAL_COMPLETE,
DOPAMINE_DIP_FRUITLESS). Un serveur eteint 2 mois ne vieillit pas.

Principe de verite causale (Gemini, Phase B) :
Aucun organe n'apprend legitimement d'une correlation temporelle. Seuls les
evenements signes avec pointeurs sont enseignants. Ce module incarne ce
principe : toute depreciation est lisible, tracable, et justifiable par
l'historique d'events, jamais par une horloge.

Phase C Etape 2 : module ISOLE, aucun consommateur branche. Les tests
unitaires ne dependent d'aucun etat de Promethee en cours d'execution.
"""

from typing import Callable, Dict, List, Optional, Tuple
import logging
import random as _random_module

from core.experience_clock import experience_clock

logger = logging.getLogger("drive_routine_registry")

# ─── Constantes genomiques ──────────────────────────────────────────────

GENOME_GRACE_CYCLES: int        = 1000     # Protection absolue avant depreciation
GENOME_DEPRECIATION_CYCLES: int = 10000    # Depreciation pleine apres grace period
DOMINANCE_RATIO: float          = 2.0      # Competiteur doit etre > 2x base_floor
COMPETITOR_STABILITY_N: int     = 1000     # Fenetre de cycles pour mesurer stabilite
COMPETITOR_STABILITY_MIN: float = 0.7      # Seuil de stabilite pour enclencher
FLOOR_OF_THE_FLOOR: float       = 0.05     # Plancher absolu (souvenir lointain)


# ─── DRIVE_GENOME : noyau immuable a court terme ────────────────────────
# Valide par Jean-Michel (Phase C, 2026-04-13) :
# - AUDIT_SURVIE dans STABILITE (0.9) et MAITRISE (0.8) — reflex tronc cerebral
# - COURS_SOUTIEN dans COMPREHENSION (0.8) — reflex pedagogique
# - REFACTORING_AUDIT dans MAITRISE (0.9) — victoire de la Phase B
# - CI_PIPELINE_RUN dans STABILITE (0.5) — overlap naturel code/stabilite

DRIVE_GENOME: Dict[str, Dict[str, float]] = {
    "CURIOSITE": {
        "VEILLE_SILENCIEUSE":   0.9,
        "DROPZONE_SCAN":        0.7,
        "ROADMAP_RESEARCH":     0.7,
        "SELF_INSPECT":         0.6,
        "GRIMOIRE_INVOKE":      0.5,
    },
    "MAITRISE": {
        "REFACTORING_AUDIT":    0.9,
        "CI_PIPELINE_RUN":      0.8,
        "AUDIT_SURVIE":         0.8,
        "SECURITY_AUDIT":       0.7,
        "SELF_ANALYSIS":        0.7,
        "PARAM_EXPERIMENT":     0.5,
    },
    "STABILITE": {
        "MEMORY_CONSOLIDATION": 0.9,
        "AUDIT_SURVIE":         0.9,
        "AUDIT_STRUCTURE":      0.8,
        "MEMORY_CLEANUP":       0.6,
        "CI_PIPELINE_RUN":      0.5,
    },
    "COMPREHENSION": {
        "VEILLE_SILENCIEUSE":   0.8,
        "COURS_SOUTIEN":        0.8,
        "COUNCIL_DEBATE":       0.7,
        "MEMORY_CONSOLIDATION": 0.6,
        "SELF_ANALYSIS":        0.6,
    },
    "CONNEXION": {
        "COUNCIL_DEBATE":       0.9,
        "SOLILOQUE_INTERNE":    0.8,
        "STEFAN_CONFRONTATION": 0.4,
    },
    "CROISSANCE": {
        "EXPANSION_CODE":       0.9,
        "EXPANSION_CATALOG":    0.8,
        "GRIMOIRE_INVOKE":      0.7,
        "ROADMAP_SPEC":         0.6,
    },
    "CREATION": {
        "EXPANSION_CODE":       0.9,
        "ARTIFACT_CREATION":    0.8,
        "FREE_EXPLORATION":     0.4,
    },
}


# ─── Registre des entry_cycles pour chaque lien genomique ──────────────
# Mappe (drive, intent) -> cycle d'entree dans le genome.
# Pose a current_cycle au chargement du module pour les liens natals.
# Sera mis a jour lors des promotions (nouvelle routine acceptee au genome).

_genome_entry_cycles: Dict[Tuple[str, str], int] = {}


def _initialize_genome_entry_cycles() -> None:
    """Pose l'entry_cycle de chaque lien genomique natal au cycle courant.

    Appele au chargement du module. Tous les liens du genome natal recoivent
    le meme entry_cycle (la valeur d'experience_clock au boot). Une future
    promotion de routine recevra le cycle courant au moment de la promotion.
    """
    current = experience_clock.current()
    for drive, intents in DRIVE_GENOME.items():
        for intent in intents:
            if (drive, intent) not in _genome_entry_cycles:
                _genome_entry_cycles[(drive, intent)] = current


# Type alias pour la fonction de stabilite injectee
CompetitorStabilityFn = Callable[[str, str, int], float]


def _compute_genome_floor(
    drive: str,
    intent: str,
    current_cycle: int,
    competitor_stability_fn: Optional[CompetitorStabilityFn] = None,
) -> float:
    """Retourne le plancher dynamique d'un lien genomique.

    Args:
        drive: nom du drive (MAITRISE, STABILITE, etc.)
        intent: nom de l'intent dans DRIVE_GENOME[drive]
        current_cycle: valeur actuelle de experience_clock.current()
        competitor_stability_fn: fonction optionnelle (drive, intent, n_cycles)
            retournant la stabilite du competiteur dominant (∈ [0, 1]).
            Si None ou si aucun competiteur n'est connu, pas de depreciation.

    Returns:
        Plancher ∈ [FLOOR_OF_THE_FLOOR, base_floor] ou 0.0 si non-genomique.

    Logique d'escalade (dans l'ordre, premier matched returns) :
    1. Intent absent du genome -> 0.0
    2. Age < GENOME_GRACE_CYCLES -> base_floor (protection absolue)
    3. Pas de competitor_stability_fn -> base_floor (pas de concurrence connue)
    4. Competiteur instable (< COMPETITOR_STABILITY_MIN) -> base_floor
    5. Depreciation lineaire : FLOOR_OF_THE_FLOOR + (base - FLOOR) * (1 - decay)
    """
    base = DRIVE_GENOME.get(drive, {}).get(intent, 0.0)
    if base == 0.0:
        return 0.0

    entry_cycle = _genome_entry_cycles.get((drive, intent), current_cycle)
    age_cycles = max(0, current_cycle - entry_cycle)

    # Garde-fou 1 : Grace period metabolique absolue
    if age_cycles < GENOME_GRACE_CYCLES:
        return base

    # Garde-fou 2 : Pas de competiteur connu -> protection maintenue
    if competitor_stability_fn is None:
        return base

    # Garde-fou 3 : Competiteur connu mais instable -> protection maintenue
    stability = competitor_stability_fn(drive, intent, COMPETITOR_STABILITY_N)
    if stability < COMPETITOR_STABILITY_MIN:
        return base

    # Depreciation graduelle en cycles metaboliques
    depreciation_age = age_cycles - GENOME_GRACE_CYCLES
    decay = min(1.0, depreciation_age / GENOME_DEPRECIATION_CYCLES)
    floor = FLOOR_OF_THE_FLOOR + (base - FLOOR_OF_THE_FLOOR) * (1.0 - decay)
    return max(FLOOR_OF_THE_FLOOR, floor)


def explain_genome_floor(
    drive: str,
    intent: str,
    competitor_stability_fn: Optional[CompetitorStabilityFn] = None,
) -> dict:
    """Retourne un dict explicatif pour debugging / falsifiabilite.

    Permet de comprendre exactement pourquoi un lien a son plancher actuel.
    Essentiel pour le debugging ET pour valider que l'apprentissage n'a pas
    derive vers une superstition silencieuse.
    """
    current = experience_clock.current()
    base = DRIVE_GENOME.get(drive, {}).get(intent, 0.0)
    if base == 0.0:
        return {
            "drive": drive,
            "intent": intent,
            "in_genome": False,
            "current_floor": 0.0,
            "reason": "not_in_genome",
        }

    entry_cycle = _genome_entry_cycles.get((drive, intent), current)
    age_cycles = max(0, current - entry_cycle)
    floor = _compute_genome_floor(drive, intent, current, competitor_stability_fn)

    reason = "unknown"
    if age_cycles < GENOME_GRACE_CYCLES:
        reason = "grace_period_active"
    elif competitor_stability_fn is None:
        reason = "no_competitor_tracking"
    else:
        stability = competitor_stability_fn(drive, intent, COMPETITOR_STABILITY_N)
        if stability < COMPETITOR_STABILITY_MIN:
            reason = f"competitor_unstable({stability:.2f})"
        else:
            reason = "depreciating"

    return {
        "drive": drive,
        "intent": intent,
        "in_genome": True,
        "base_floor": base,
        "entry_cycle": entry_cycle,
        "current_cycle": current,
        "age_cycles": age_cycles,
        "grace_period_active": age_cycles < GENOME_GRACE_CYCLES,
        "grace_cycles_remaining": max(0, GENOME_GRACE_CYCLES - age_cycles),
        "current_floor": floor,
        "floor_of_the_floor": FLOOR_OF_THE_FLOOR,
        "reason": reason,
    }


GREEDY_TEMPERATURE_THRESHOLD: float = 0.01  # En dessous, mode déterministe


def get_routines_for_drive(
    drive: str,
    synaptic_weights: Dict[str, float],
    context_multipliers: Optional[Dict[str, float]] = None,
    temperature: float = 0.0,
    top_k: int = 5,
    competitor_stability_fn: Optional[CompetitorStabilityFn] = None,
    rng: Optional[_random_module.Random] = None,
) -> List[Tuple[str, float]]:
    """Projette graphe synaptique + genome + modulateurs en liste ordonnee.

    C'est LE point d'entree unique du registre — il remplacera progressivement
    les 3 tables heretiques et les 7 modulateurs eparpilles dans la V1.

    Injection de dependances pure : la fonction n'importe rien d'autre que
    DRIVE_GENOME et experience_clock. Elle recoit son etat du monde via
    ses parametres. Cela garantit son isolabilite complete pour les tests.

    Args:
        drive: nom du drive (MAITRISE, STABILITE, CURIOSITE, etc.)
        synaptic_weights: Dict[intent, weight] appris par le graphe synaptique
            pour ce drive. Peut etre vide si le graphe n'a rien appris encore.
            Fourni par l'appelant (abstraction de synaptic_network).
        context_multipliers: Dict[intent, multiplier] applique APRES la fusion
            genome+synaptic. Agregation des 7 tables modulateurs (emotion,
            mode, trait, etc.) pour ce cycle. Par defaut None -> pas de biais.
            PRINCIPE : un multiplicateur module, il ne selectionne pas. Un
            intent absent du genome ET du synaptic n'apparaitra pas meme
            s'il a un multiplicateur de 100.
        temperature: degre d'exploration dans [0.0, +inf[.
            T < 0.01 -> greedy strict (top K par poids decroissant)
            T >= 0.01 -> echantillonnage probabiliste pondere sans
                         remplacement, avec les poids finaux comme probabilites
                         relatives
        top_k: nombre max de routines retournees (default 5)
        competitor_stability_fn: fonction injectee pour la depreciation
            genomique (voir _compute_genome_floor). Si None, pas de
            depreciation effective (plancher genomique plein).
        rng: generateur aleatoire optionnel pour tests deterministes.
            Si None, utilise le module random global.

    Returns:
        List[(intent, final_weight)] de longueur <= top_k.
        - En mode greedy : tri strict decroissant, break-tie alphabetique.
        - En mode stochastique : ordre de l'echantillonnage (premier tire = 0).

        Le weight retourne est le poids FINAL apres fusion + multipliers.
    """
    if context_multipliers is None:
        context_multipliers = {}
    if rng is None:
        rng = _random_module

    current_cycle = experience_clock.current()

    # Union des intents connus : graphe appris + genome natal
    genome_intents = DRIVE_GENOME.get(drive, {})
    all_intents = set(synaptic_weights.keys()) | set(genome_intents.keys())

    if not all_intents:
        return []

    # Fusion en 2 etapes : (1) max(synaptic, floor), (2) * multiplier
    weighted: Dict[str, float] = {}
    for intent in all_intents:
        synaptic_w = float(synaptic_weights.get(intent, 0.0))
        floor = _compute_genome_floor(drive, intent, current_cycle, competitor_stability_fn)
        base = max(synaptic_w, floor)
        multiplier = float(context_multipliers.get(intent, 1.0))
        weighted[intent] = base * multiplier

    # Filtre les poids nuls ou negatifs (non-probabilistes)
    weighted = {k: v for k, v in weighted.items() if v > 0}
    if not weighted:
        return []

    # Mode greedy : tri strict decroissant, tie-break alphabetique
    if temperature < GREEDY_TEMPERATURE_THRESHOLD:
        ranked = sorted(weighted.items(), key=lambda x: (-x[1], x[0]))
        return ranked[:top_k]

    # Mode stochastique : echantillonnage pondere sans remplacement
    # Les poids servent de probabilites relatives brutes (pas de softmax)
    items = list(weighted.items())
    k = min(top_k, len(items))
    selected: List[Tuple[str, float]] = []
    pool = list(items)
    for _ in range(k):
        total = sum(w for _, w in pool)
        if total <= 0:
            break
        probs = [w / total for _, w in pool]
        idx = rng.choices(range(len(pool)), weights=probs, k=1)[0]
        selected.append(pool[idx])
        pool.pop(idx)
    return selected


# ═══════════════════════════════════════════════════════════════════════
# Phase C Etape 4 — Provider Pattern (Inversion de Controle)
# ═══════════════════════════════════════════════════════════════════════
#
# Probleme : get_routines_for_drive() est pure et exige un synaptic_weights
# injecte. Forcer chaque consommateur (autonomy_engine, prefrontal, council,
# etc.) a importer synaptic_network cree du boilerplate, des risques d'imports
# circulaires, et contamine les consommateurs avec la connaissance du graphe.
#
# Solution : le registre expose une fonction set_synaptic_provider() que
# synaptic_network appelle AU BOOT pour enregistrer son lecteur de poids.
# Les consommateurs appellent alors get_routines_for_drive_live() qui fait
# automatiquement la lecture via le provider enregistre.
#
# Principe : le registre reste PUR (aucun import de synaptic_network). C'est
# synaptic_network qui vient brancher sa prise. Cela preserve l'isolation
# testable de la Brique 3 et evite les imports circulaires.
#
# Design doc : docs/phase_c_etape_3_hebbian_causal.md (annexe Etape 4)
# Valide par Jean-Michel 2026-04-14 apres challenge adversarial Gemini.

# Type alias pour le provider synaptic : drive_name -> {intent: weight}
SynapticWeightProvider = Callable[[str], Dict[str, float]]

_synaptic_provider: Optional[SynapticWeightProvider] = None
_stability_provider: Optional[CompetitorStabilityFn] = None


def set_synaptic_provider(fn: Optional[SynapticWeightProvider]) -> None:
    """Enregistre le lecteur de poids synaptiques.

    Appele au boot par synaptic_network pour brancher le graphe sur le
    registre via inversion de controle. Le registre ne connait jamais
    synaptic_network directement.

    Passer None deconnecte le provider (utile pour les tests ou un reset).
    """
    global _synaptic_provider
    _synaptic_provider = fn
    if fn is None:
        logger.info("[drive_routine_registry] synaptic_provider cleared")
    else:
        logger.info("[drive_routine_registry] synaptic_provider registered")


def set_stability_provider(fn: Optional[CompetitorStabilityFn]) -> None:
    """Enregistre la fonction de stabilite competiteur pour la depreciation
    genomique (Brique 1). Appele au boot par synaptic_network.
    """
    global _stability_provider
    _stability_provider = fn
    if fn is None:
        logger.info("[drive_routine_registry] stability_provider cleared")
    else:
        logger.info("[drive_routine_registry] stability_provider registered")


def get_synaptic_provider() -> Optional[SynapticWeightProvider]:
    """Accesseur lecture pour les tests. Ne pas utiliser en production."""
    return _synaptic_provider


def get_affinity_for_drive_intent(drive: str, intent: str) -> float:
    """Retourne le poids final d'un lien drive -> intent specifique.

    PATHOLOGIE HOT PATH : cette fonction est appelee par compute_desire_bonus
    dans la boucle de scoring 23 couches, potentiellement des centaines de
    fois par cycle de dispatch. Elle doit etre O(1) et ne JAMAIS construire
    la liste triee complete via get_routines_for_drive_live (qui ferait un
    tri + echantillonnage inutiles pour un single lookup).

    Logique :
      1. Lecture ponctuelle du poids synaptique pour ce drive via provider
      2. Calcul du floor genomique pour CE lien specifique
      3. Retour de max(synaptic, floor) sans tri ni fusion contextuelle

    Contrairement a get_routines_for_drive_live, cette fonction ne consulte
    PAS context_multipliers. Raison : le scoring 23 couches a deja ses propres
    modulateurs (emotion, mode, etc.) appliques ailleurs dans le pipeline.
    Appliquer les multiplicateurs ici serait du double comptage.

    Args:
        drive: nom du drive (MAITRISE, STABILITE, ...)
        intent: nom de l'intent candidat

    Returns:
        Poids final dans [0.0, 1.0]. 0.0 si ni le graphe ni le genome
        ne connaissent cette paire drive<->intent.

    Performance:
        O(k) ou k est le nombre de synapses du drive (typiquement 5-20).
        Le cout dominant est la lecture du provider (qui parcourt les
        synapses du graphe). Si cela devient un goulot d'etranglement,
        on pourra ajouter un cache LRU par drive avec TTL court.
    """
    # 1. Poids synaptique via provider (lecture ponctuelle)
    synaptic_weight = 0.0
    if _synaptic_provider is not None:
        try:
            weights = _synaptic_provider(drive)
            if weights:
                synaptic_weight = float(weights.get(intent, 0.0))
        except Exception as e:
            logger.warning(
                f"[drive_routine_registry] synaptic_provider failed "
                f"for (drive={drive}, intent={intent}): {e}"
            )

    # 2. Floor genomique pour ce lien specifique (O(1))
    current_cycle = experience_clock.current()
    floor = _compute_genome_floor(drive, intent, current_cycle, _stability_provider)

    # 3. Fusion simple : max(synaptic, floor)
    return max(synaptic_weight, floor)


def get_routines_for_drive_live(
    drive: str,
    context_multipliers: Optional[Dict[str, float]] = None,
    temperature: float = 0.0,
    top_k: int = 5,
    rng: Optional[_random_module.Random] = None,
    use_context_multipliers: bool = True,
) -> List[Tuple[str, float]]:
    """Facade live pour les consommateurs.

    Version "branchee" de get_routines_for_drive() qui lit automatiquement
    les poids synaptiques via le provider enregistre par synaptic_network
    au boot. C'est la fonction que les consommateurs (autonomy_engine,
    prefrontal, council_analytics, etc.) doivent utiliser apres la
    migration Phase C Etape 4.

    Usage typique :
        from core.drive_routine_registry import get_routines_for_drive_live
        routines = get_routines_for_drive_live("MAITRISE", top_k=5)
        # routines = [(intent, final_weight), ...]

    Comportement en cas de provider absent :
    Si set_synaptic_provider() n'a jamais ete appele (ex: tests isoles,
    boot partiel), la facade utilise synaptic_weights={} — seul le genome
    contribue. C'est un fallback SAFE : Promethee continue de fonctionner
    avec les routines canoniques du genome, sans adaptation dynamique.

    Comportement en cas d'erreur du provider :
    Si le provider leve une exception (ex: synaptic_network crashe au
    boot), la facade logge un WARNING et utilise synaptic_weights={}.
    Aucune propagation d'exception vers les consommateurs — ils restent
    fonctionnels meme si le graphe est KO.

    Args : meme signature que get_routines_for_drive() mais sans
    synaptic_weights ni competitor_stability_fn (injectes automatiquement).
    """
    synaptic_weights: Dict[str, float] = {}
    if _synaptic_provider is not None:
        try:
            result = _synaptic_provider(drive)
            if result:
                synaptic_weights = result
        except Exception as e:
            logger.warning(
                f"[drive_routine_registry] synaptic_provider failed "
                f"for drive={drive}: {e}"
            )

    # Phase C Etape 5 : auto-injection des modulateurs contextuels.
    # Si l'appelant n'a pas fourni de context_multipliers et que use_context_multipliers
    # est True (defaut), on agrege les 5 tables d'affinite via compute_context_multipliers.
    # Les tests qui veulent un comportement deterministe peuvent passer
    # use_context_multipliers=False pour desactiver l'auto-injection.
    if context_multipliers is None and use_context_multipliers:
        genome_intents = set(DRIVE_GENOME.get(drive, {}).keys())
        all_intents = list(genome_intents | set(synaptic_weights.keys()))
        if all_intents:
            context_multipliers = compute_context_multipliers(drive, all_intents)

    return get_routines_for_drive(
        drive=drive,
        synaptic_weights=synaptic_weights,
        context_multipliers=context_multipliers,
        temperature=temperature,
        top_k=top_k,
        competitor_stability_fn=_stability_provider,
        rng=rng,
    )


# ═══════════════════════════════════════════════════════════════════════
# Phase C Etape 5 — Migration des Modulateurs (2026-04-14)
# ═══════════════════════════════════════════════════════════════════════
#
# Jusqu'ici les 5 tables d'affinite contextuelle etaient eparpillees dans
# les organes (inner_voice, psyche, hypothalamus) et appliquees de maniere
# ad hoc par chaque consommateur du scoring. Etape 5 les migre en un agregateur
# unique qui produit un Dict[intent, multiplier] consomme par
# get_routines_for_drive_live().
#
# Regle d'or (Gemini-valide) : un modulateur est un adjectif, pas un nom.
# Il MODULE un intent deja candidat (present dans genome ∪ graphe synaptique)
# mais ne peut jamais en creer un nouveau. L'isolation structurelle est
# garantie par get_routines_for_drive qui filtre weighted > 0 : un intent
# absent a base = 0, donc 0 * multiplicateur = 0 → reste exclu.
#
# Design :
#  - Multiplier par table bornee dans [0.5, 1.5] via _bonus_to_multiplier
#  - Compound multiplicatif des tables
#  - Clip final dans [_MULTIPLIER_MIN, _MULTIPLIER_MAX] = [0.5, 2.0]
#  - Lazy imports : toute exception → modulateur neutre (1.0), pas de crash
# ═══════════════════════════════════════════════════════════════════════

_MULTIPLIER_MIN: float = 0.5
_MULTIPLIER_MAX: float = 2.0


def _bonus_to_multiplier(bonus: float) -> float:
    """Convertit un bonus additif V1 (range ~[-1, +1]) en multiplicateur V3.

    Mapping lineaire borne :
      bonus = 0.0  -> 1.0   (neutre)
      bonus = +0.5 -> 1.25
      bonus = +1.0 -> 1.5
      bonus = -0.5 -> 0.75
      bonus = -1.0 -> 0.5
    Clip dans [0.5, 1.5] pour qu'un modulateur individuel reste "adjectif".
    """
    clamped = max(-1.0, min(1.0, float(bonus)))
    return max(0.5, min(1.5, 1.0 + clamped * 0.5))


def compute_context_multipliers(
    drive: str,
    intents: List[str],
) -> Dict[str, float]:
    """Agrege les 5 tables d'affinite contextuelle en multiplicateurs par intent.

    Phase C Etape 5. Remplace la dispersion de calculs d'affinite dans chaque
    organe par une fonction pure d'agregation qui lit :
      - inner_voice._EMOTION_ROUTINE_AFFINITY (emotion dominante des 10 dernieres pensees)
      - inner_voice._MODE_ROUTINE_AFFINITY (mode Vygotsky dominant)
      - inner_voice._SOURCE_ROUTINE_AFFINITY (source pensee dominante, proportionnel)
      - psyche.ROUTINE_AFFINITY (affinite traits de personnalite du systeme)
      - hypothalamus._INTENT_ENERGY_MAP + _INTENT_STRESS_MAP + _INTENT_DOPAMINE_MAP

    Chaque table contribue un multiplicateur individuel dans [0.5, 1.5]. Les
    multiplicateurs se composent multiplicativement, puis le produit final est
    clippe dans [_MULTIPLIER_MIN, _MULTIPLIER_MAX] = [0.5, 2.0].

    Garde-fou structurel : cette fonction n'a aucune responsabilite de
    selection d'intents. Elle recoit la liste des intents candidats (issue
    du genome ∪ graphe pour le drive) et retourne uniquement des coefficients
    multiplicatifs. Un intent absent de `intents` ne recevra aucun multiplier.
    Un multiplier de 0.5 sur un intent n'empeche pas sa selection si son base
    synaptique est eleve — il le penalise seulement.

    Lazy imports : chaque bloc d'organe est protege par un try/except. Si un
    organe n'est pas disponible (boot partiel, test isole, crash), son
    modulateur est skippe avec un WARNING, les autres continuent.

    Args:
        drive: nom du drive (reserve pour contextualisation future — actuellement
            non utilise, mais l'API est stable pour l'etape 6 qui introduira
            des modulateurs drive-specifiques).
        intents: liste des intents candidats. Seuls ces intents recevront un
            multiplicateur eventuel.

    Returns:
        Dict[intent, multiplier]. Seuls les intents avec un multiplier != 1.0
        apparaissent (les intents neutres sont omis pour economie). L'appelant
        doit defaulter a 1.0 pour les intents absents.
    """
    if not intents:
        return {}

    per_intent_mult: Dict[str, float] = {intent: 1.0 for intent in intents}

    # 1. Inner voice (emotion dominante, mode dominant, source proportionnelle)
    try:
        from core.inner_voice import voice as _inner_voice
        stream = list(getattr(_inner_voice, "stream", []) or [])[-10:]
        if stream:
            src_counts: Dict[str, int] = {}
            emo_counts: Dict[str, int] = {}
            mode_counts: Dict[str, int] = {}
            for t in stream:
                s = getattr(t, "source", None)
                if s:
                    src_counts[s] = src_counts.get(s, 0) + 1
                e = getattr(t, "emotion", None)
                if e:
                    emo_counts[e] = emo_counts.get(e, 0) + 1
                m = getattr(t, "mode", None)
                if m:
                    mode_counts[m] = mode_counts.get(m, 0) + 1

            IV = type(_inner_voice)
            EMO_TABLE = getattr(IV, "_EMOTION_ROUTINE_AFFINITY", {}) or {}
            MODE_TABLE = getattr(IV, "_MODE_ROUTINE_AFFINITY", {}) or {}
            SRC_TABLE = getattr(IV, "_SOURCE_ROUTINE_AFFINITY", {}) or {}

            if emo_counts:
                dom_emo = max(emo_counts, key=emo_counts.get)
                emo_aff = EMO_TABLE.get(dom_emo, {})
                for intent in intents:
                    b = emo_aff.get(intent, 0.0)
                    if b:
                        per_intent_mult[intent] *= _bonus_to_multiplier(b)

            if mode_counts:
                dom_mode = max(mode_counts, key=mode_counts.get)
                mode_aff = MODE_TABLE.get(dom_mode, {})
                for intent in intents:
                    b = mode_aff.get(intent, 0.0)
                    if b:
                        per_intent_mult[intent] *= _bonus_to_multiplier(b)

            total = len(stream)
            for src, count in src_counts.items():
                src_aff = SRC_TABLE.get(src, {})
                if not src_aff:
                    continue
                proportion = count / total
                for intent in intents:
                    b = src_aff.get(intent, 0.0) * proportion
                    if b:
                        per_intent_mult[intent] *= _bonus_to_multiplier(b)
    except Exception as e:
        logger.warning(f"[context_mult] inner_voice modulation skipped: {e}")

    # 2. Hypothalamus (energy, stress, dopamine — homoeostasis)
    try:
        from core.hypothalamus import (
            Hypothalamus,
            _INTENT_ENERGY_MAP,
            _INTENT_STRESS_MAP,
            _INTENT_DOPAMINE_MAP,
        )
        hypo = Hypothalamus()
        vals = getattr(hypo, "current_values", {}) or {}
        # Setpoint neutre 0.5. L'ecart au setpoint amplifie/attenue l'effet.
        # _INTENT_ENERGY_MAP : + = favorise quand energy bas ; - = penalise quand bas
        energy_delta = 0.5 - float(vals.get("energy", 0.5))
        stress_delta = float(vals.get("stress", 0.5)) - 0.5
        dopamine_delta = 0.5 - float(vals.get("dopamine", 0.5))
        for intent in intents:
            bonus = (
                _INTENT_ENERGY_MAP.get(intent, 0.0) * energy_delta
                + _INTENT_STRESS_MAP.get(intent, 0.0) * stress_delta
                + _INTENT_DOPAMINE_MAP.get(intent, 0.0) * dopamine_delta
            )
            if bonus:
                per_intent_mult[intent] *= _bonus_to_multiplier(bonus)
    except Exception as e:
        logger.warning(f"[context_mult] hypothalamus modulation skipped: {e}")

    # 3. Psyche (traits de personnalite systeme)
    try:
        from core.psyche import psyche as _psyche, ROUTINE_AFFINITY
        traits_avg: Dict[str, float] = {}
        if hasattr(_psyche, "get_system_average"):
            traits_avg = _psyche.get_system_average() or {}
        if traits_avg and ROUTINE_AFFINITY:
            for intent in intents:
                trait_weights = ROUTINE_AFFINITY.get(intent, {})
                if not trait_weights:
                    continue
                # Dot product centre : chaque trait en [0, 100] recentre a 50.
                # Resultat brut ~[-50 * sum(weights), +50 * sum(weights)] / 100.
                # Les weights sont typiquement ~0.2-0.5, donc bonus ~[-0.75, +0.75].
                bonus = 0.0
                for trait, weight in trait_weights.items():
                    trait_val = float(traits_avg.get(trait, 50.0))
                    bonus += weight * (trait_val - 50.0) / 50.0
                if bonus:
                    per_intent_mult[intent] *= _bonus_to_multiplier(bonus)
    except Exception as e:
        logger.warning(f"[context_mult] psyche modulation skipped: {e}")

    # Clip final puis filtre les neutres (economie memoire pour l'appelant)
    return {
        intent: max(_MULTIPLIER_MIN, min(_MULTIPLIER_MAX, mult))
        for intent, mult in per_intent_mult.items()
        if abs(mult - 1.0) > 1e-9
    }


# Initialisation automatique des entry_cycles au chargement du module
_initialize_genome_entry_cycles()
