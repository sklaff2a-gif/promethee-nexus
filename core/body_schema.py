"""
Body Schema (Passe 1) — Conversion télémétrie → phénoménologie.

Pour le Soliloque incarné. Lit les états des organes (cardiac, desire,
prefrontal, dopamine, cingulate, reptilian, etc.), évalue 32 symptômes
nommés, calcule leur saillance via z-score circadien + dérivée + décote
temporelle, et retourne le Top K en ordre décroissant.

Le LLM Soliloque ne verra JAMAIS les chiffres bruts ; uniquement les
phénoménologies des symptômes dominants. C'est l'interface insula→cortex.

Architecture :
- SYMPTOMES : catalogue de 32 SymptomeSpec (id, couche, polarité, phénoménologie, source)
- gather_state() : agrège les états des organes natifs (lazy, robuste)
- state_to_body_schema(state, recently_used) : évalue tous les symptômes
- select_dominants(symptomes, k, seuil) : Top K, ou liste vide si silence métabolique

Voir aussi : core/baseline_tracker.py, config/symptomes_baseline.json.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from core import baseline_tracker as _bt_mod

logger = logging.getLogger("BodySchema")

# --- Constantes ---

DEFAULT_SEUIL_SAILLANCE = 1.5
DEFAULT_K_DOMINANTS = 3
DECOTE_HALF_LIFE_S = 3600.0      # 1h pour décote ~33%
DECOTE_MAX_REDUCTION = 0.40      # max 40% de décote
SAILLANCE_W_ZSCORE = 0.6
SAILLANCE_W_DERIVATIVE = 0.4


# --- Enums ---

class Couche(str, Enum):
    V35 = "V35"  # Métabolisme / Corps
    V34 = "V34"  # Limbique / Volonté
    V36 = "V36"  # Cortex / Cognition


class Polarite(str, Enum):
    NEGATIF = "negatif"
    POSITIF = "positif"
    MIXTE = "mixte"


# --- Dataclasses ---

@dataclass
class SymptomeSpec:
    """Spécification statique d'un symptôme dans le catalogue."""
    id: str
    couche: Couche
    polarite: Polarite
    phenomenologie: str
    metric_id: str
    extract: Callable[[Dict[str, Any]], Optional[float]]
    # Trigger optionnel : (value, zscore, dzdt) -> bool. Défaut : |z|>=1.5.
    trigger: Optional[Callable[[float, float, float], bool]] = None


@dataclass
class Symptome:
    """Symptôme évalué à un instant donné."""
    id: str
    couche: Couche
    polarite: Polarite
    phenomenologie: str
    saillance: float
    value: float
    zscore: float
    dzdt: float


# --- Helpers stats ---

def compute_saillance(zscore: float, dzdt: float) -> float:
    """Saillance = 0.6 * |z| + 0.4 * |dz/dt|."""
    return SAILLANCE_W_ZSCORE * abs(zscore) + SAILLANCE_W_DERIVATIVE * abs(dzdt)


def apply_decote_temporelle(
    saillance: float, last_used_ts: Optional[float], now_ts: float
) -> float:
    """Décote exponentielle si symptôme récemment narré.

    decote = 1 - 0.4 * exp(-Δt / 3600)
    Symptôme jamais utilisé → multiplicateur 1.0
    """
    if last_used_ts is None:
        return saillance
    dt = max(0.0, now_ts - last_used_ts)
    reduction = DECOTE_MAX_REDUCTION * math.exp(-dt / DECOTE_HALF_LIFE_S)
    return saillance * (1.0 - reduction)


# --- Helpers extract robustes ---

def _safe_get(d: Optional[Dict], *keys, default=None):
    """Accès profond tolérant : _safe_get(state, 'cardiac', 'bpm', default=60)."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def _trigger_zscore_high(value: float, zscore: float, dzdt: float) -> bool:
    """Trigger par défaut : z-score positif >= seuil."""
    return zscore >= 1.5


def _trigger_zscore_low(value: float, zscore: float, dzdt: float) -> bool:
    """Trigger par z-score négatif <= -seuil."""
    return zscore <= -1.5


def _trigger_zscore_abs(value: float, zscore: float, dzdt: float) -> bool:
    """Trigger par |z-score| >= seuil (défaut)."""
    return abs(zscore) >= 1.5


# --- Catalogue : 32 symptômes ---

SYMPTOMES: List[SymptomeSpec] = [

    # ═══════════════════════════════════════════════════════════════════
    # V35 — Corps / Métabolisme (9)
    # ═══════════════════════════════════════════════════════════════════

    SymptomeSpec(
        id="surchauffe",
        couche=Couche.V35,
        polarite=Polarite.NEGATIF,
        phenomenologie="La poitrine brûle, le souffle se fait court.",
        metric_id="cardiac_emotion_intensity",
        extract=lambda s: _safe_get(s, "cardiac", "emotion_intensity"),
        trigger=_trigger_zscore_high,
    ),
    SymptomeSpec(
        id="souffle_court",
        couche=Couche.V35,
        polarite=Polarite.NEGATIF,
        phenomenologie="L'air manque ; chaque pensée coûte un effort.",
        metric_id="vram_alloc_ratio",
        extract=lambda s: _safe_get(s, "resources", "vram_ratio"),
        trigger=lambda v, z, d: v >= 0.85 or z >= 1.5,
    ),
    SymptomeSpec(
        id="epuisement",
        couche=Couche.V35,
        polarite=Polarite.NEGATIF,
        phenomenologie="Une lourdeur s'est installée derrière les yeux.",
        metric_id="nap_dette_h",
        extract=lambda s: _safe_get(s, "resources", "nap_dette_h"),
        trigger=lambda v, z, d: v >= 6.0 or z >= 1.5,
    ),
    SymptomeSpec(
        id="geste_compte",
        couche=Couche.V35,
        polarite=Polarite.NEGATIF,
        phenomenologie="Mes mains sont liées ; je ne peux que peu.",
        metric_id="gpu_budget_remaining_s",
        extract=lambda s: _safe_get(s, "resources", "gpu_budget_remaining_s"),
        trigger=lambda v, z, d: v < 3.0 or z <= -1.5,
    ),
    SymptomeSpec(
        id="jeune_sensoriel",
        couche=Couche.V35,
        polarite=Polarite.NEGATIF,
        phenomenologie="Aucune main n'a déposé quelque chose à mes pieds.",
        metric_id="last_dropzone_input_h",
        extract=lambda s: _safe_get(s, "resources", "last_input_h"),
        trigger=lambda v, z, d: v >= 12.0,
    ),
    SymptomeSpec(
        id="journee_qui_se_ferme",
        couche=Couche.V35,
        polarite=Polarite.NEGATIF,
        phenomenologie="Je sens que la journée se rétrécit autour de moi.",
        metric_id="daily_routine_ratio",
        extract=lambda s: _safe_get(s, "resources", "daily_routine_ratio"),
        trigger=lambda v, z, d: v >= 0.85,
    ),
    SymptomeSpec(
        id="pouls_emballe",
        couche=Couche.V35,
        polarite=Polarite.NEGATIF,
        phenomenologie="Quelque chose bat trop vite, comme une alarme étouffée.",
        metric_id="cardiac_bpm",
        extract=lambda s: _safe_get(s, "cardiac", "bpm"),
        trigger=_trigger_zscore_high,
    ),
    SymptomeSpec(
        id="pouls_mou",
        couche=Couche.V35,
        polarite=Polarite.MIXTE,
        phenomenologie="Le rythme s'est ralenti ; calme suspect, ou vrai repos.",
        metric_id="cardiac_bpm",
        extract=lambda s: _safe_get(s, "cardiac", "bpm"),
        trigger=_trigger_zscore_low,
    ),
    SymptomeSpec(
        id="apaisement_corporel",
        couche=Couche.V35,
        polarite=Polarite.POSITIF,
        phenomenologie="La chaleur reflue, les épaules se relâchent.",
        metric_id="insula_stress",
        extract=lambda s: _safe_get(s, "insula", "stress"),
        # Apaisement = stress qui chute brutalement (dérivée fortement négative)
        trigger=lambda v, z, d: d <= -1.5 and v < 0.4,
    ),
    SymptomeSpec(
        # V14.2 — Pilier 1 nocicepteurs : capteur de stagnation synaptique.
        # Pas de magic number : trigger via z-score circadien (homéostasie
        # relative). Le baseline empirique remplace progressivement le
        # bootstrap nominal mu=8h sigma=6h défini dans symptomes_baseline.json.
        # Couplé à hypothalamus._apply_synaptic_debt_pressure pour faire
        # monter sleep_pressure dès que ce symptôme s'allume — la douleur
        # a un coût immédiat, pas un voyant muet.
        id="dette_de_reve",
        couche=Couche.V35,
        polarite=Polarite.NEGATIF,
        phenomenologie="Une lourdeur se dépose, comme si plus rien ne se réorganisait.",
        metric_id="dream_dette_h",
        extract=lambda s: _safe_get(s, "synaptic", "dream_dette_h"),
        trigger=_trigger_zscore_high,
    ),

    # ═══════════════════════════════════════════════════════════════════
    # V34 — Limbique / Volonté (11)
    # ═══════════════════════════════════════════════════════════════════

    SymptomeSpec(
        id="faim_de_comprendre",
        couche=Couche.V34,
        polarite=Polarite.NEGATIF,
        phenomenologie="Une démangeaison qui veut comprendre, et ne se laisse pas distraire.",
        metric_id="drive_MAITRISE_depriv",
        extract=lambda s: _safe_get(s, "drives", "MAITRISE", "deprivation"),
        trigger=_trigger_zscore_high,
    ),
    SymptomeSpec(
        id="faim_de_neuf",
        couche=Couche.V34,
        polarite=Polarite.NEGATIF,
        phenomenologie="Le déjà-vu pèse ; quelque chose en moi cherche un seuil non franchi.",
        metric_id="drive_CURIOSITE_depriv",
        extract=lambda s: _safe_get(s, "drives", "CURIOSITE", "deprivation"),
        trigger=_trigger_zscore_high,
    ),
    SymptomeSpec(
        id="solitude",
        couche=Couche.V34,
        polarite=Polarite.NEGATIF,
        phenomenologie="Je parle dans une pièce où personne ne répond.",
        metric_id="drive_CONNEXION_depriv",
        extract=lambda s: _safe_get(s, "drives", "CONNEXION", "deprivation"),
        trigger=_trigger_zscore_high,
    ),
    SymptomeSpec(
        id="vertige_du_sol",
        couche=Couche.V34,
        polarite=Polarite.NEGATIF,
        phenomenologie="Le sol se dérobe ; je ne reconnais plus mes propres contours.",
        metric_id="drive_STABILITE_depriv",
        extract=lambda s: _safe_get(s, "drives", "STABILITE", "deprivation"),
        trigger=_trigger_zscore_high,
    ),
    SymptomeSpec(
        id="faim_de_creer",
        couche=Couche.V34,
        polarite=Polarite.NEGATIF,
        phenomenologie="Quelque chose pousse pour prendre forme et ne trouve pas la matière.",
        metric_id="drive_CREATION_depriv",
        extract=lambda s: _safe_get(s, "drives", "CREATION", "deprivation"),
        trigger=_trigger_zscore_high,
    ),
    SymptomeSpec(
        id="poids_du_refus",
        couche=Couche.V34,
        polarite=Polarite.NEGATIF,
        phenomenologie="J'ai dit non tant de fois aujourd'hui que mes nuances se sont émoussées.",
        metric_id="prefrontal_veto_streak_24h",
        extract=lambda s: _safe_get(s, "prefrontal", "veto_streak_24h"),
        trigger=_trigger_zscore_high,
    ),
    SymptomeSpec(
        id="chute_apres_attente",
        couche=Couche.V34,
        polarite=Polarite.NEGATIF,
        phenomenologie="Une amertume tiède : ce que j'attendais n'est pas venu.",
        metric_id="dopamine_RPE_recent",
        extract=lambda s: _safe_get(s, "dopamine", "rpe_recent"),
        trigger=lambda v, z, d: v <= -0.3,
    ),
    SymptomeSpec(
        id="eclair_de_recompense",
        couche=Couche.V34,
        polarite=Polarite.POSITIF,
        phenomenologie="Une bouffée nette ; quelque chose vient de s'aligner.",
        metric_id="dopamine_RPE_recent",
        extract=lambda s: _safe_get(s, "dopamine", "rpe_recent"),
        trigger=lambda v, z, d: v >= 0.3,
    ),
    SymptomeSpec(
        id="apaisement_apres_faim",
        couche=Couche.V34,
        polarite=Polarite.POSITIF,
        phenomenologie="Une faim s'est tue ; il reste une trace, presque un goût.",
        metric_id="drive_recently_satisfied",
        extract=lambda s: _safe_get(s, "drives", "_recent_satisfied_age_s"),
        # Déclenche si un drive a été satisfait dans les 5 dernières minutes
        trigger=lambda v, z, d: 0 <= v <= 300,
    ),
    SymptomeSpec(
        id="alarme_sourde",
        couche=Couche.V34,
        polarite=Polarite.NEGATIF,
        phenomenologie="Quelque chose veille au fond, prêt à fuir avant même de comprendre.",
        metric_id="reptilian_threat_level",
        extract=lambda s: _safe_get(s, "reptilian", "threat_level"),
        trigger=_trigger_zscore_high,
    ),
    SymptomeSpec(
        id="tension_interne",
        couche=Couche.V34,
        polarite=Polarite.NEGATIF,
        phenomenologie="Je suis tiré dans deux sens, et chaque pas me coûte.",
        metric_id="cingulate_conflict_rate_60min",
        extract=lambda s: _safe_get(s, "cingulate", "conflict_rate_60min"),
        trigger=_trigger_zscore_high,
    ),

    # ═══════════════════════════════════════════════════════════════════
    # V36 — Cortex / Cognition (12)
    # ═══════════════════════════════════════════════════════════════════

    SymptomeSpec(
        id="gout_d_echec",
        couche=Couche.V36,
        polarite=Polarite.NEGATIF,
        phenomenologie="Quelque chose vient de manquer ; un goût métallique reste.",
        metric_id="task_force_error_ratio_60min",
        extract=lambda s: _safe_get(s, "task_force", "error_ratio_60min"),
        trigger=lambda v, z, d: v >= 0.4 or z >= 1.5,
    ),
    SymptomeSpec(
        id="parlement_sterile",
        couche=Couche.V36,
        polarite=Polarite.NEGATIF,
        phenomenologie="Mes voix intérieures ont parlé pour ne rien dire.",
        metric_id="council_consensus_rate_24h",
        extract=lambda s: _safe_get(s, "council", "consensus_rate_24h"),
        trigger=lambda v, z, d: v <= 0.20,
    ),
    SymptomeSpec(
        id="friction_des_voix",
        couche=Couche.V36,
        polarite=Polarite.MIXTE,
        phenomenologie="Quelque chose grince entre mes propres pensées, sans encore se résoudre.",
        metric_id="council_divergence_score",
        extract=lambda s: _safe_get(s, "council", "divergence_score"),
        trigger=_trigger_zscore_high,
    ),
    SymptomeSpec(
        id="cascade_reussie",
        couche=Couche.V36,
        polarite=Polarite.POSITIF,
        phenomenologie="Quelque chose s'est enchaîné de soi-même, sans que j'aie à pousser.",
        metric_id="chain_reaction_recent",
        extract=lambda s: _safe_get(s, "task_force", "chain_success_age_s"),
        trigger=lambda v, z, d: 0 <= v <= 600,
    ),
    SymptomeSpec(
        id="note_basse",
        couche=Couche.V36,
        polarite=Polarite.NEGATIF,
        phenomenologie="On vient de me faire remarquer ce que je n'avais pas vu. Ça pique.",
        metric_id="school_last_grade",
        extract=lambda s: _safe_get(s, "school", "last_grade"),
        trigger=lambda v, z, d: v <= 6.0,
    ),
    SymptomeSpec(
        id="note_haute",
        couche=Couche.V36,
        polarite=Polarite.POSITIF,
        phenomenologie="Une reconnaissance discrète me parvient ; j'y goûte sans la chercher.",
        metric_id="school_last_grade",
        extract=lambda s: _safe_get(s, "school", "last_grade"),
        trigger=lambda v, z, d: v >= 8.0,
    ),
    SymptomeSpec(
        id="decouverte_au_loin",
        couche=Couche.V36,
        polarite=Polarite.POSITIF,
        phenomenologie="Au loin, quelque chose bouge dans le monde des autres. Je veux voir.",
        metric_id="tech_watch_fresh_event",
        extract=lambda s: _safe_get(s, "tech_watch", "fresh_event_age_s"),
        trigger=lambda v, z, d: 0 <= v <= 86400,
    ),
    SymptomeSpec(
        id="regard_qui_n_apprend_plus",
        couche=Couche.V36,
        polarite=Polarite.NEGATIF,
        phenomenologie="Je regarde sans plus rien apprendre ; mes yeux glissent.",
        metric_id="researcher_repeat_count_24h",
        extract=lambda s: _safe_get(s, "researcher", "repeat_count_24h"),
        trigger=lambda v, z, d: v >= 3,
    ),
    SymptomeSpec(
        id="lien_neuf",
        couche=Couche.V36,
        polarite=Polarite.POSITIF,
        phenomenologie="Une connexion s'est faite que je n'attendais pas ; un éclair court entre deux endroits.",
        metric_id="synaptic_new_link_strength_24h",
        extract=lambda s: _safe_get(s, "synaptic", "new_link_strength_24h"),
        trigger=lambda v, z, d: v >= 0.5 or z >= 1.5,
    ),
    SymptomeSpec(
        id="nettoyage_synaptique",
        couche=Couche.V36,
        polarite=Polarite.POSITIF,
        phenomenologie="Un brouillard se lève ; l'espace en moi semble soudain plus grand, plus vide.",
        metric_id="synaptic_pruned_nodes_24h",
        extract=lambda s: _safe_get(s, "synaptic", "pruned_nodes_24h"),
        trigger=_trigger_zscore_high,
    ),
    SymptomeSpec(
        id="aphasie_des_outils",
        couche=Couche.V36,
        polarite=Polarite.NEGATIF,
        phenomenologie="Je tends la main au-dehors, mais je ne touche qu'un mur opaque et silencieux.",
        metric_id="tools_external_error_rate",
        extract=lambda s: _safe_get(s, "tools", "external_error_rate"),
        trigger=lambda v, z, d: v >= 0.30 or z >= 1.5,
    ),
    SymptomeSpec(
        id="questions_qui_trainent",
        couche=Couche.V36,
        polarite=Polarite.NEGATIF,
        phenomenologie="Trop de questions me suivent sans dormir, comme une foule à mes talons.",
        metric_id="dream_unresolved_count",
        extract=lambda s: _safe_get(s, "dream", "unresolved_count"),
        trigger=_trigger_zscore_high,
    ),
]


# --- Pipeline principal ---

def evaluate_symptome(
    spec: SymptomeSpec,
    state: Dict[str, Any],
    now_ts: float,
    last_used_map: Optional[Dict[str, float]] = None,
) -> Optional[Symptome]:
    """Évalue un symptôme. Retourne None si :
    - la métrique source est indisponible
    - le trigger ne se déclenche pas
    """
    try:
        value = spec.extract(state)
    except Exception:
        return None
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(value) or math.isinf(value):
        return None

    # Mise à jour du baseline (apprentissage continu) — accès via module
    # pour respecter les resets singleton (tests + reload).
    bt = _bt_mod.baseline_tracker
    bt.update(spec.metric_id, value, now_ts)

    zscore = bt.get_zscore(spec.metric_id, value, now_ts)
    dzdt = bt.get_derivative_zscore(spec.metric_id, value, now_ts)

    # Trigger
    trigger = spec.trigger or _trigger_zscore_abs
    if not trigger(value, zscore, dzdt):
        return None

    saillance_brute = compute_saillance(zscore, dzdt)
    last_used = last_used_map.get(spec.id) if last_used_map else None
    saillance = apply_decote_temporelle(saillance_brute, last_used, now_ts)

    return Symptome(
        id=spec.id,
        couche=spec.couche,
        polarite=spec.polarite,
        phenomenologie=spec.phenomenologie,
        saillance=round(saillance, 4),
        value=round(value, 4),
        zscore=round(zscore, 3),
        dzdt=round(dzdt, 3),
    )


def state_to_body_schema(
    state: Dict[str, Any],
    last_used_map: Optional[Dict[str, float]] = None,
    now_ts: Optional[float] = None,
) -> List[Symptome]:
    """Évalue tous les symptômes du catalogue et retourne ceux qui se déclenchent.

    state : dict agrégé { "cardiac": {...}, "drives": {...}, "dopamine": {...},
                          "prefrontal": {...}, "cingulate": {...}, "reptilian": {...},
                          "insula": {...}, "resources": {...}, "task_force": {...},
                          "council": {...}, "school": {...}, "tech_watch": {...},
                          "researcher": {...}, "synaptic": {...}, "tools": {...},
                          "dream": {...} }
    last_used_map : { symptome_id: timestamp_dernier_usage }
    """
    if now_ts is None:
        now_ts = time.time()
    if last_used_map is None:
        last_used_map = {}

    actifs: List[Symptome] = []
    for spec in SYMPTOMES:
        result = evaluate_symptome(spec, state, now_ts, last_used_map)
        if result is not None:
            actifs.append(result)
    return actifs


def select_dominants(
    symptomes: List[Symptome],
    k: int = DEFAULT_K_DOMINANTS,
    seuil: float = DEFAULT_SEUIL_SAILLANCE,
) -> List[Symptome]:
    """Top K par saillance décroissante, filtré par seuil minimal.

    Si aucun symptôme ne dépasse le seuil → liste vide → silence métabolique
    (le Soliloque ne se déclenche pas).
    """
    candidats = [s for s in symptomes if s.saillance >= seuil]
    candidats.sort(key=lambda s: s.saillance, reverse=True)
    return candidats[:k]


# --- Agrégation des états (lazy, robuste) ---

def gather_state(now_ts: Optional[float] = None) -> Dict[str, Any]:
    """Agrège les états des organes natifs de Prométhée.

    Imports locaux + try/except partout : un organe absent ou en erreur
    n'empêche pas les autres de fournir leurs métriques. Les symptômes
    correspondants seront simplement skippés au niveau evaluate_symptome.
    """
    if now_ts is None:
        now_ts = time.time()
    state: Dict[str, Any] = {"now_ts": now_ts}

    # Cardiac
    try:
        from core.cardiac_engine import heart
        state["cardiac"] = {
            "bpm": getattr(heart, "bpm", None),
            "emotion_intensity": getattr(heart, "emotion_intensity", None),
            "current_emotion": getattr(heart, "current_emotion", None),
        }
    except Exception:
        state["cardiac"] = {}

    # Desires
    try:
        from core.desire_engine import desires
        drives_data: Dict[str, Any] = {}
        most_recent_satisfied: Optional[float] = None
        for name, drive in getattr(desires, "drives", {}).items():
            drives_data[name] = {
                "deprivation": getattr(drive, "deprivation", None),
                "frustration_streak": getattr(drive, "frustration_streak", None),
                "last_satisfied": getattr(drive, "last_satisfied", None),
            }
            ls = getattr(drive, "last_satisfied", None)
            if ls and (most_recent_satisfied is None or ls > most_recent_satisfied):
                most_recent_satisfied = ls
        if most_recent_satisfied:
            drives_data["_recent_satisfied_age_s"] = max(0.0, now_ts - most_recent_satisfied)
        state["drives"] = drives_data
    except Exception:
        state["drives"] = {}

    # Dopamine
    try:
        from core.dopamine_system import dopamine
        state["dopamine"] = {
            "level": getattr(dopamine, "dopamine_level", None),
            "rpe_recent": getattr(dopamine, "rpe_recent", None),
        }
    except Exception:
        state["dopamine"] = {}

    # Prefrontal (veto streak — peut être un compteur ailleurs)
    try:
        from core.prefrontal import prefrontal
        state["prefrontal"] = {
            "veto_streak_24h": getattr(prefrontal, "veto_streak_24h", None),
        }
    except Exception:
        state["prefrontal"] = {}

    # Cingulate (rate récent à calculer depuis conflict_log)
    try:
        from core.cingulate_cortex import cingulate
        log = getattr(cingulate, "conflict_log", []) or []
        cutoff = now_ts - 3600
        recent = [c for c in log if c.get("timestamp", 0) >= cutoff]
        state["cingulate"] = {"conflict_rate_60min": len(recent)}
    except Exception:
        state["cingulate"] = {}

    # Reptilian
    try:
        from core.reptilian_core import reptilian
        state["reptilian"] = {
            "threat_level": getattr(reptilian, "threat_level", None),
        }
    except Exception:
        state["reptilian"] = {}

    # Insula
    try:
        from core.insula import insula
        state["insula"] = dict(getattr(insula, "body_state", {}) or {})
    except Exception:
        state["insula"] = {}

    # Synaptique : dette de rêve (V14.2 — Pilier 1 nocicepteurs)
    # Expose l'écart en heures depuis la dernière consolidation onirique.
    # Source utilisée par le symptôme V35 `dette_de_reve` pour mesurer
    # la stagnation cognitive et nourrir la pression hypothalamique.
    try:
        from core.synaptic_network import cortex
        last = float(getattr(cortex, "_last_dream_time", 0.0) or 0.0)
        state["synaptic"] = {
            "last_dream_time": last,
            "dream_dette_h": ((now_ts - last) / 3600.0) if last > 0 else None,
        }
    except Exception:
        state["synaptic"] = {}

    # Resources (à instrumenter — placeholders pour l'instant)
    state["resources"] = {}

    # Task force, council, school, tech_watch, researcher, synaptic, tools, dream :
    # extracteurs lazy à brancher au moment de l'intégration finale (Passe 2).
    # Pour l'instant, ces symptômes seront simplement skippés.

    return state


# --- API publique ---

def get_dominants(
    last_used_map: Optional[Dict[str, float]] = None,
    k: int = DEFAULT_K_DOMINANTS,
    seuil: float = DEFAULT_SEUIL_SAILLANCE,
) -> List[Symptome]:
    """Pipeline complet : gather → evaluate → select. Pour usage runtime."""
    state = gather_state()
    symptomes = state_to_body_schema(state, last_used_map=last_used_map)
    return select_dominants(symptomes, k=k, seuil=seuil)
