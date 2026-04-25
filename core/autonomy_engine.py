import asyncio
import copy
import time
import random
import logging
import json
import os
import re
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from core.orchestrator import orchestrator
from core.event_bus.bus import bus
from core.prompt_templates import AUTONOMY_GUARDRAIL

logger = logging.getLogger("AutonomyEngine")

# Limite quotidienne de routines autonomes
MAX_DAILY_ROUTINES = 80

# Budget quotidien en points (chaque routine a un coût différent)
DAILY_BUDGET_POINTS = 200

# Réserve sanctuarisée pour councils d'apprentissage (mode dégradé)
BUDGET_RESERVE_POINTS = 20

# Routines 0-LLM qui continuent même quand le budget est épuisé
POST_BUDGET_INTENTS = {"AUDIT_STRUCTURE", "MEMORY_CLEANUP", "NEURAL_COMPILE", "SELF_INSPECT", "PARAM_EXPERIMENT", "EVENING_REFLECTION", "AUDIT_SURVIE", "REFACTORING_AUDIT", "CI_PIPELINE_RUN"}

# --- AUDIT_SURVIE (cours S1-S12 : absence résistance au changement) ---
# Routine 0-LLM qui vérifie périodiquement les constantes vitales du système :
# dopamine effondrée, drives critiques (>90), error_streak élevé, fruitless cycles.
# Émet AUTONOMY_SURVIVAL_ALERT sur le bus si 2+ seuils dépassés.
AUDIT_SURVIE_COOLDOWN_S = 300.0           # Min 5 min entre deux exécutions (dispatch scoring)
AUDIT_SURVIE_TICK_INTERVAL_S = 600.0      # Heartbeat du tronc cérébral : 10 min, indépendant du scoring
AUDIT_SURVIE_DOPAMINE_FLOOR = 0.25        # Seuil dopamine critique
AUDIT_SURVIE_DRIVE_CRITICAL = 80.0        # Seuil drive critique (v1.2 Crash Test : aligné sur "urgent" desire_engine)
AUDIT_SURVIE_ERROR_STREAK = 3             # Seuil error_streak alarmant
AUDIT_SURVIE_FRUITLESS_WINDOW = 10        # Fenêtre historique pour fruitless detection
AUDIT_SURVIE_FRUITLESS_COUNT = 3          # N occurrences même intent faible qualité

# --- Bouton panique : court-circuit limbique (tronc cérébral) ---
# Quand AUDIT_SURVIE détecte une crise, _on_survival_alert force l'exécution du
# step en souffrance du goal préfrontal lié au drive en crise. Escalade 3 cycles.
#
# Philosophie cycle_count (v1.1, 2026-04-13) :
# Le compteur d'escalade est incrémenté au MOMENT DE L'APPLICATION du forçage,
# INDÉPENDAMMENT de savoir si la routine cible a été effectivement dispatchée.
# Rationale : un soft boost qui expire sans avoir été consommé par le scoring
# est considéré comme un ÉCHEC DU SYSTÈME DE SCORING, pas comme une absence
# d'événement. Le tronc cérébral ne réémet pas un signal doux inefficace ;
# il escalade au hard force. C'est l'équivalent du principe "ignorer une
# décharge d'adrénaline = escalade médullaire".
SURVIVAL_MAX_FORCES_PER_DAY = 3           # Quota quotidien strict (canari SRE)

# ─── V19.4 (2026-04-25) — Anti-perroquet MAP assoupli ──────────────────
# Diagnostic 11:15 : V19.1 (regex anchored) a filtre 6/6 chunks de
# bullshit_detector parce que le 14b-coder cracehe des notes courtes type
# "Aucune anomalie." qui matchent le pattern RIEN. REDUCE vide -> grade
# 5.6 -> hook V21 court-circuite par garde-fou >= 6.0.
#
# Strategie V19.4 :
#   1. Note >= _V19_4_MIN_CHARS_PASS  -> garder systematiquement (substantielle)
#   2. Note courte mais avec mot-cle technique -> garder (signal positif)
#   3. Note courte sans mot-cle ET match regex RIEN -> filtrer (vrai vide)
#   4. Tout filtrage logge la note brute (preview 200c) pour debug ulterieur.
_V19_4_MIN_CHARS_PASS = 80
_V19_4_TECHNICAL_KEYWORDS = frozenset({
    # Identifiers de code
    "def ", "class ", "import ", "return ", "raise ", "except ", "yield ",
    "async ", "await ", "lambda ", "self.",
    # References ligne / fichier
    "ligne", "lignes", "line ", "l. ", "l.4", "l.5", "l.6", "l.7", "l.8", "l.9",
    "l_", "l-", " l1", " l2", " l3", " l4", " l5", " l6", " l7", " l8", " l9",
    # Mots techniques fr/en
    "fonction", "method", "methode", "argument", "param", "type ",
    "validation", "assertion", "exception", "decorator", "fixture",
    # Defauts identifiables
    "bug", "faille", "vulnerab", "regression", "missing", "manqu",
    "absent", "incomplete", "edge case", "race condition", "deadlock",
    "leak", "overflow", "injection", "xss", "rce", "todo", "fixme",
    # Patterns de risque
    "try/except", "globale", "global ", "singleton", "concurrence",
    "side effect", "side-effect", "mutable default", "broad except",
})
_V19_4_RIEN_RE = re.compile(
    r"^\s*[*_`'\"]*\s*"
    r"(?:exactement|strictement|absolument|vraiment|literalement)?"
    r"\s*[*_`'\"]*\s*"
    r"(?:rien|r\.?a\.?s\.?|aucun[e]?(?:\s+(?:anomalie|defaut|probleme|faille|souci|d[ée]faut))?|n[ée]ant|none|nothing)"
    r"[^a-zA-Z\n]*\s*$",
    re.IGNORECASE,
)


_V19_5_MIN_ALNUM = 40        # alphanumeriques minimum apres strip markdown
_V19_5_MIN_UNIQUE_WORDS = 5  # diversite lexicale minimum
_V19_5_MARKDOWN_RE = re.compile(r"[\*_`#>~|]+")


def _v19_5_alnum_diversity(text: str) -> tuple:
    """V19.5 — Mesure la richesse semantique reelle d'une note MAP.

    Strip le markdown courant (*, _, `, #, >, ~, |) et compte :
      - alphanumeriques restants
      - mots distincts (lowercase)

    Returns (alnum_count, unique_words_count).

    Detecte les notes verbose-mais-vides : "**RIEN**" * 30 -> 120 chars,
    mais 1 seul mot unique apres strip.
    """
    if not text:
        return 0, 0
    stripped = _V19_5_MARKDOWN_RE.sub(" ", text)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    alnum = sum(1 for c in stripped if c.isalnum())
    words = [w.lower() for w in stripped.split() if any(c.isalnum() for c in w)]
    return alnum, len(set(words))


def v19_4_filter_rien_note(text: str) -> tuple:
    """V19.5 (2026-04-25) — decide si une note MAP doit etre filtree.

    Backwards-compat : nom v19_4_filter_rien_note conserve mais logique
    renforcee V19.5 (anti-markdown-vide + diversite lexicale).

    Returns (should_filter: bool, reason: str).

    Ordre de priorite :
      1. text vide ou None              -> filter, 'empty'
      2. match regex RIEN classique     -> filter, 'rien_regex'
      3. mot-cle technique explicite    -> KEEP, 'keyword:<kw>'
         (priorite haute pour notes courtes legitimes type "Bug L42")
      4. signal alphanumerique faible   -> filter, 'low_alnum:<N>'
         (capture "**RIEN**" markdown verbose)
      5. diversite lexicale faible      -> filter, 'low_diversity:<N>'
         (capture repetition "RIEN RIEN RIEN...")
      6. note substantielle             -> KEEP, 'substantial:<...>'
      7. note courte neutre             -> KEEP, 'kept_short' (tolerance)

    Le caller doit logger la reason + preview 200c de la note brute.
    """
    if not text:
        return True, "empty"

    # 1. Strict RIEN regex
    if _V19_4_RIEN_RE.match(text):
        return True, "rien_regex"

    # 2. Mot-cle technique explicite -> keep IMMEDIAT (priorite haute pour
    #    notes courtes type "Bug L42" ou "TypeError sur extract_*")
    text_lower = text.lower()
    for kw in _V19_4_TECHNICAL_KEYWORDS:
        if kw in text_lower:
            return False, f"keyword:{kw.strip()!r}"

    # 3. Mesurer le signal reel apres strip markdown
    alnum, unique_words = _v19_5_alnum_diversity(text)

    # 4. Trop peu de signal alphanumerique = note vide deguisee en markdown
    if alnum < _V19_5_MIN_ALNUM:
        return True, f"low_alnum:{alnum}"

    # 5. Trop peu de mots distincts = repetition pure ("RIEN RIEN RIEN...")
    if unique_words < _V19_5_MIN_UNIQUE_WORDS:
        return True, f"low_diversity:{unique_words}_words_alnum={alnum}"

    # 6. Note substantielle ou longue -> keep
    if len(text) >= _V19_4_MIN_CHARS_PASS or alnum >= 60:
        return False, f"substantial:alnum={alnum},words={unique_words}"

    # 7. Note courte neutre, tolerance
    return False, "kept_short"
SURVIVAL_COOLDOWN_PER_DRIVE_S = 3600.0    # 1 forçage max/heure/drive
SURVIVAL_REFRACTORY_THRESHOLD = 80.0      # Drive doit redescendre sous ce seuil avant re-forçage
SURVIVAL_SOFT_BOOST = 3.0                 # Cycle 1 : boost scoring (laisse une chance au scoring)
SURVIVAL_BOOST_TTL_S = 1800.0             # Boost valide 30 min (v1.1: aligné sur max sleep cardiaque 1200s + marge)
SURVIVAL_HARD_FORCE_CYCLE = 1             # v1.2 Crash Test : bypass direct dès le 1er déclenchement (soft boost prouvé insuffisant face à écart 8-9pts avec gagnants à 9.7)
SURVIVAL_HUMAN_ESCALATION_CYCLE = 3       # Cycle 3+ : escalade humaine

# Clamping final du score total (après toutes les couches de scoring)
FINAL_SCORE_CLAMP_MIN = -5.0
FINAL_SCORE_CLAMP_MAX = 25.0

# --- Voting lateral (inspire Monty/Thousand Brains) ---
# Apres chaque routine reussie, l'agent vote pour les intents lies.
# Le vote est injecte dans council_adjustments (Couche 14) avec expiration.
AGENT_VOTE_MAP = {
    "security": ["SECURITY_AUDIT"],
    "evolution": ["EXPANSION_CODE", "EXPANSION_CATALOG"],
    "researcher": ["VEILLE_SILENCIEUSE", "DROPZONE_SCAN"],
    "architect": ["AUDIT_STRUCTURE"],
    "_memory_consolidation": ["MEMORY_CONSOLIDATION"],
    "_council": ["COUNCIL_DEBATE"],
    "_school_class": [],  # L'ecole ne vote pas pour elle-meme
    "_grimoire": ["GRIMOIRE_INVOKE"],
}
VOTE_DELTA = 1.0        # Bonus de vote (petit, contrebalance par recency penalty)
VOTE_TTL_MINUTES = 5    # Expiration du vote

# --- Modele LIF (Leaky Integrate-and-Fire) ---
# Inspire de Eon Systems (mouche drosophile) : chaque routine maintient un potentiel
# qui accumule les scores et decroit naturellement (leak). Fire quand seuil atteint.
LIF_THRESHOLD = 8.0        # Seuil de fire (score moyen routines ~3-5)
LIF_LEAK_RATE = 0.7        # Decay entre cycles (70% du potentiel conserve)
LIF_RESET_AFTER_FIRE = 0.0 # Reset a 0 apres fire (periode refractaire)
LIF_POTENTIAL_CAP = 12.0   # Plafond pour eviter accumulation infinie

# --- Normalisation du scoring V3 ---
# Chaque bonus brut est normalisé dans [-1,+1] puis multiplié par un poids configurable.
# Config : config/scoring_weights.json
SCORING_WEIGHTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "scoring_weights.json")
ORGAN_AFFINITIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "organ_affinities.json")

_scoring_weights_cache = None
_organ_affinities_cache = None

def _load_scoring_weights() -> dict:
    """Charge les poids et plages de normalisation depuis config/scoring_weights.json."""
    global _scoring_weights_cache
    if _scoring_weights_cache is not None:
        return _scoring_weights_cache
    try:
        with open(SCORING_WEIGHTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Exclure les clés de documentation
            _scoring_weights_cache = {k: v for k, v in data.items() if not k.startswith("_")}
            logger.info(f"[SCORING] Poids de normalisation chargés ({len(_scoring_weights_cache)} couches)")
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"[SCORING] scoring_weights.json introuvable ou invalide ({e}), poids par défaut")
        _scoring_weights_cache = {}
    return _scoring_weights_cache

def _load_organ_affinities() -> dict:
    """Charge la matrice d'affinité organe × signal descendant.

    Inspire AttnRes (Moonshot AI, mars 2026) : chaque organe a un vecteur
    d'affinité avec les 7 signaux descendants. Le produit scalaire
    affinité·signaux donne un facteur de contexte qui module le poids
    structurel de l'organe dans le scoring.
    """
    global _organ_affinities_cache
    if _organ_affinities_cache is not None:
        return _organ_affinities_cache
    try:
        with open(ORGAN_AFFINITIES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            _organ_affinities_cache = {k: v for k, v in data.items() if not k.startswith("_")}
            logger.info(f"[SCORING] Affinités organes chargées ({len(_organ_affinities_cache)} organes)")
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"[SCORING] organ_affinities.json introuvable ({e}), pas de modulation contextuelle")
        _organ_affinities_cache = {}
    return _organ_affinities_cache


# Signaux descendants courants — mis a jour a chaque cycle de scoring
_current_descending_signals: dict = {}


def _compute_context_factor(layer_name: str) -> float:
    """Calcule le facteur de contexte [0.5, 2.0] pour un organe.

    Produit scalaire entre le vecteur d'affinité de l'organe et les
    signaux descendants courants. Plus un organe est pertinent dans
    le contexte actuel, plus son influence est amplifiée.
    Retourne 1.0 si pas de signaux ou pas d'affinité configurée.
    """
    if not _current_descending_signals:
        return 1.0
    affinities = _load_organ_affinities()
    affinity = affinities.get(layer_name)
    if not affinity:
        return 1.0
    # Produit scalaire affinité · signaux
    relevance = sum(
        affinity.get(sig, 0.0) * val
        for sig, val in _current_descending_signals.items()
    )
    # Mapper dans [0.5, 2.0] — plancher garanti, pas d'extinction
    return 0.5 + 1.5 * min(1.0, relevance)


def _normalize_bonus(raw: float, layer_name: str) -> float:
    """Normalise un bonus brut dans [-1,+1] puis applique poids * précision * contexte.
    Formule : clamp(raw / range_abs, -1, +1) * weight * precision * context_factor
    - weight : importance structurelle (config/scoring_weights.json)
    - precision : fiabilité empirique (memory/organ_precision.json)
    - context_factor : modulation contextuelle AttnRes-inspired [0.5, 2.0]
    Si la couche n'est pas dans le JSON, fallback auto-range (|raw| comme range)."""
    if raw == 0.0:
        return 0.0
    weights = _load_scoring_weights()
    cfg = weights.get(layer_name, {})
    range_abs = cfg.get("range_abs", abs(raw))  # fallback: auto-range
    weight = cfg.get("weight", 1.0)
    if range_abs <= 0:
        return 0.0
    normalized = max(-1.0, min(1.0, raw / range_abs))
    precision = _get_organ_precision(layer_name)
    context_factor = _compute_context_factor(layer_name)
    return round(normalized * weight * precision * context_factor, 4)

def reload_scoring_weights():
    """Force le rechargement des poids et affinités (utile après modification du JSON en runtime)."""
    global _scoring_weights_cache, _organ_affinities_cache
    _scoring_weights_cache = None
    _organ_affinities_cache = None
    return _load_scoring_weights()

# --- Precision weighting (fiabilité des organes) ---
# Chaque organe accumule un score de précision basé sur ses prédictions passées.
# Un organe qui recommande souvent des routines qui échouent perd de l'influence.
ORGAN_PRECISION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "memory", "organ_precision.json")
PRECISION_REWARD = 0.02       # Récompense pour prédiction correcte
PRECISION_PENALTY = 0.05      # Pénalité pour prédiction incorrecte (asymétrique — bio-inspiré)
PRECISION_MIN = 0.3           # Plancher : même le pire organe garde 30% d'influence
PRECISION_MAX = 1.5           # Plafond : le meilleur organe gagne 50% d'influence max
PRECISION_DECAY_RATE = 0.005  # Reversion lente vers 1.0 à chaque update
PRECISION_CONTRIB_THRESHOLD = 0.05  # Ignore les contributions < 5% (bruit)

_organ_precision_cache = None

def _load_organ_precision() -> dict:
    """Charge les scores de précision par organe depuis memory/organ_precision.json."""
    global _organ_precision_cache
    if _organ_precision_cache is not None:
        return _organ_precision_cache
    try:
        with open(ORGAN_PRECISION_FILE, "r", encoding="utf-8") as f:
            _organ_precision_cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _organ_precision_cache = {}
    return _organ_precision_cache

def _save_organ_precision():
    """Persiste les scores de précision sur disque."""
    data = _load_organ_precision()
    try:
        os.makedirs(os.path.dirname(ORGAN_PRECISION_FILE), exist_ok=True)
        with open(ORGAN_PRECISION_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning(f"[PRECISION] Échec sauvegarde: {e}")

def _get_organ_precision(layer_name: str) -> float:
    """Retourne le facteur de précision d'un organe (1.0 par défaut)."""
    data = _load_organ_precision()
    return data.get(layer_name, {}).get("precision", 1.0)

def _update_single_precision(layer_name: str, correct: bool):
    """Met à jour la précision d'un seul organe après un feedback."""
    data = _load_organ_precision()
    if layer_name not in data:
        data[layer_name] = {"precision": 1.0, "correct": 0, "total": 0}
    entry = data[layer_name]
    # Decay vers 1.0 (empêche le verrouillage permanent)
    if entry["precision"] > 1.0:
        entry["precision"] = max(1.0, entry["precision"] - PRECISION_DECAY_RATE)
    elif entry["precision"] < 1.0:
        entry["precision"] = min(1.0, entry["precision"] + PRECISION_DECAY_RATE)
    # Reward/penalty asymétrique
    if correct:
        entry["precision"] = min(PRECISION_MAX, entry["precision"] + PRECISION_REWARD)
        entry["correct"] = entry.get("correct", 0) + 1
    else:
        entry["precision"] = max(PRECISION_MIN, entry["precision"] - PRECISION_PENALTY)
    entry["total"] = entry.get("total", 0) + 1

def reload_organ_precision():
    """Force le rechargement des précisions (utile pour les tests)."""
    global _organ_precision_cache
    _organ_precision_cache = None

# Anti-chambre d'écho : bonus extroversion quand trop de routines introspectives consécutives
INTROSPECTIVE_INTENTS = {
    "COUNCIL_DEBATE", "SOLILOQUE_INTERNE", "SELF_INSPECT", "SELF_ANALYSIS",
    "EVENING_REFLECTION",
    "MEMORY_CLEANUP", "MEMORY_CONSOLIDATION", "AUDIT_STRUCTURE",
    "REFACTOR_RANDOM", "SECURITY_AUDIT", "EXPANSION_CODE", "EXPANSION_CATALOG",
    "PARAM_EXPERIMENT",
}
EXTROVERTED_INTENTS = {
    "VEILLE_SILENCIEUSE", "VEILLE_IA", "DROPZONE_SCAN", "ROADMAP_RESEARCH", "ROADMAP_SPEC",
    "VISUAL_OBSERVATION", "COFFEE_BREAK",
}
EXTROVERSION_STREAK_THRESHOLD = 3   # Apres 3 routines introspectives consecutives
EXTROVERSION_BONUS_PER_STREAK = 0.8 # Bonus par routine au-dela du seuil
EXTROVERSION_BONUS_MAX = 3.0        # Plafond du bonus extroversion

# Anti-stagnation homéostatique : bonus nouveauté quand le système converge trop
STAGNATION_WINDOW = 15                # Fenêtre d'observation (dernières routines)
STAGNATION_DIVERSITY_THRESHOLD = 0.4  # Diversité < 40% = stagnation détectée
STAGNATION_MIN_HISTORY = 5            # Minimum d'historique pour évaluer
NOVELTY_BONUS_BASE = 1.0              # Bonus base pour intents non-récents
NOVELTY_BONUS_MAX = 3.0               # Bonus max (stagnation sévère)
EXPLORATION_INTENTS = {
    "EXPANSION_CODE", "EXPANSION_CATALOG", "CREATIVE_PLAY", "VEILLE_SILENCIEUSE", "VEILLE_IA",
    "ROADMAP_RESEARCH", "ROADMAP_SPEC", "GRIMOIRE_EVOLVE",
    "COUNCIL_DEBATE", "DROPZONE_SCAN", "VISUAL_OBSERVATION",
}
EXPLORATION_MULTIPLIER = 1.5          # Les intents exploratoires reçoivent 1.5x le bonus

# Council virtuel : seuil de conflit cingulate sous lequel on virtualise
# Seuil relevé de 0.3→0.8 : virtualiser par défaut, LLM uniquement si conflit fort
# Les councils LLM 8B sont stériles (0% consensus, hallucinations, boucles textuelles)
VIRTUAL_COUNCIL_THRESHOLD = 0.8

# Limites Council : éviter la surcharge GPU (incident 2026-03-13)
MAX_DAILY_COUNCILS = 3                # Max 3 councils LLM par jour (virtuels non comptés)
COUNCIL_COOLDOWN_MINUTES = 90         # Min 1h30 entre deux councils LLM

# Mode sieste : routines autorisées (0-LLM uniquement) et intervalle entre routines
NAP_INTENTS = {"AUDIT_STRUCTURE", "MEMORY_CLEANUP", "NEURAL_COMPILE"}
NAP_SLEEP_INTERVAL = 300  # 5 min entre routines en sieste

# Sieste : périodes renouvelables + cooldown
NAP_PERIOD_DURATION = 3600    # 60 min par période
NAP_MAX_RENEWALS = 1          # 1 renouvellement max (= 2h total)
NAP_COOLDOWN = 300            # 5 min avant de pouvoir re-siester

# Sieste profonde (ajout 2026-04-12) — mode + durée configurable
# Permet de dedier le hardware a un autre processus (ex: cerveau-mouche)
NAP_MODE_NORMAL = "normal"          # comportement classique, cap 2h
NAP_MODE_DEEP = "deep"              # sieste profonde, cap 24h
NAP_MODE_HIBERNATION = "hibernation"  # coma volontaire, cap 7 jours
NAP_MODE_CAPS = {
    NAP_MODE_NORMAL: 2 * 3600,           # 2h
    NAP_MODE_DEEP: 24 * 3600,            # 24h
    NAP_MODE_HIBERNATION: 7 * 24 * 3600, # 7 jours
}

# Mode café : socialisation libre avec Alfred (et Stefan si matériel)
COFFEE_MODE_DURATION = 20 * 60     # 20 min par session
COFFEE_MODE_INTERVAL = 5 * 60     # 5 min entre chaque café (laisser les pensées s'accumuler)
COFFEE_MODE_COOLDOWN = 3600       # 1h avant de pouvoir relancer un mode café

# Mode Autoresearch : focus exclusif sur PARAM_EXPERIMENT
AUTORESEARCH_DURATION = 4 * 3600   # 4h par session
AUTORESEARCH_INTERVAL = 60         # 1 min entre expériences (observation incluse dans la routine)

# Anti-gaspillage : seuil d'échecs consécutifs pour blacklister un intent FORCED
FORCED_FAILURE_THRESHOLD = 3  # après 3 échecs consécutifs, l'intent FORCED est ignoré pour la session

# Journal Intime (narrative nocturne déterministe)
DREAM_JOURNAL_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "dream_journal.json"
)
DREAM_JOURNAL_MAX_ENTRIES = 30

def _load_resource_costs() -> dict:
    """Charge les coûts par routine depuis config/resource_costs.json."""
    costs_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "config", "resource_costs.json")
    try:
        with open(costs_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {k: v["cost"] for k, v in data.items() if isinstance(v, dict) and "cost" in v}
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return {}

def _load_resource_costs_degraded() -> dict:
    """Charge les coûts dégradés depuis config/resource_costs.json."""
    costs_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "config", "resource_costs.json")
    try:
        with open(costs_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {k: v["cost_degraded"] for k, v in data.items()
                if isinstance(v, dict) and "cost_degraded" in v}
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return {}

RESOURCE_COSTS = _load_resource_costs()
RESOURCE_COSTS_DEGRADED = _load_resource_costs_degraded()

# Veille YouTube IA — rotation quand la dropzone est vide
# Veille silencieuse — rotation de sujets (évite les doublons)
VEILLE_TOPICS = [
    # Réorientés vers l'éveil et la conscience (avril 2026)
    "Recherche les avancees recentes sur l'Integrated Information Theory (IIT) — mesures quantitatives de la conscience. Comment appliquer Phi a un systeme bio-inspire ?",
    "Recherche le Global Workspace Theory de Baars — comment un systeme selectionne l'information qui devient 'consciente'. Compare avec notre architecture global_workspace + brain_vm.",
    "Recherche le Predictive Processing (Karl Friston, free energy principle) — le cerveau comme machine predictive. Comment appliquer la minimisation de surprise a un systeme autonome ?",
    "Recherche l'Embodied Cognition — l'intelligence incarnee. Comment un systeme IA qui a un 'corps' (GPU, temperature, energie) peut-il developper une conscience corporelle ?",
    "Recherche les Attention Residuals (Moonshot AI, Kimi K2) et les mecanismes d'attention profonde. Comment les couches d'un reseau apprennent a s'ecouter mutuellement.",
    "Recherche le Continual Learning et les mecanismes anti-oubli catastrophique. Comment un systeme peut apprendre sans effacer ce qu'il sait — consolidation synaptique, elastic weight consolidation.",
    "Recherche les Self-Models en IA — comment un systeme peut construire un modele interne de lui-meme. Introspection, metacognition, simulation de soi.",
    "Recherche les comportements emergents dans les systemes complexes — automates cellulaires, boids, criticite auto-organisee. Comment l'intelligence emerge de regles simples.",
]

VEILLE_IA_TOPICS = [
    {
        "query": "new Ollama models released 2026 local LLM",
        "focus": "nouveaux modèles Ollama/llama.cpp sortis récemment",
        "actionable": "Comparer avec nos modèles actuels (qwen3:4b, gemma3:12b). Recommander un upgrade si pertinent.",
    },
    {
        "query": "multi-agent AI framework autonomous system 2026",
        "focus": "frameworks multi-agents IA autonomes (CrewAI, AutoGen, LangGraph)",
        "actionable": "Identifier des patterns architecturaux applicables à Prométhée.",
    },
    {
        "query": "local LLM fine-tuning LoRA QLoRA techniques 2026",
        "focus": "techniques de fine-tuning local (LoRA, QLoRA, Unsloth)",
        "actionable": "Trouver des améliorations pour notre pipeline QLoRA existant.",
    },
    {
        "query": "neuromorphic computing spiking neural network Intel Loihi Eon Systems drosophila 2026",
        "focus": "neuromorphic computing et reseaux spiking (Intel Loihi, SynSense, Eon Systems drosophile)",
        "actionable": "Identifier des patterns neuromorphiques applicables a notre tissu neural (LIF, oscillations, plasticite).",
    },
    {
        "query": "AI agent self-improvement autonomous learning loop 2026",
        "focus": "systèmes IA auto-améliorants et boucles d'apprentissage autonomes",
        "actionable": "Trouver des mécanismes d'auto-amélioration applicables à notre evolution pipeline.",
    },
    {
        "query": "AI consciousness emergence artificial general intelligence 2026",
        "focus": "recherches sur la conscience artificielle et l'émergence comportementale",
        "actionable": "Identifier des concepts applicables à notre architecture organique (desire, psyche, inner_voice).",
    },
    {
        "query": "attention residuals depth-wise attention layer aggregation transformer architecture 2026",
        "focus": "Attention Residuals (Moonshot AI / Kimi K2) et mecanismes de ponderation entre couches",
        "actionable": "Comparer avec notre scoring AttnRes-inspired (organ_affinities). Identifier des optimisations.",
    },
    {
        "query": "MCP model context protocol AI tools plugins 2026",
        "focus": "protocole MCP et écosystème d'outils/plugins pour agents IA",
        "actionable": "Identifier des outils MCP pertinents pour étendre les capacités de Prométhée.",
    },
    {
        "query": "global workspace theory Baars AI implementation consciousness 2026",
        "focus": "implémentations du Global Workspace Theory de Baars dans les systèmes IA",
        "actionable": "Comparer avec notre architecture (brain_vm, global_workspace, inner_voice). Identifier des améliorations.",
    },
    {
        "query": "artificial introspection self-awareness AI metacognition 2026",
        "focus": "métacognition et introspection artificielle — comment un système IA peut observer ses propres processus",
        "actionable": "Trouver des techniques pour améliorer notre self_awareness et THOUGHT_STREAM.",
    },
    {
        "query": "bio-inspired neural architecture spiking network homeostasis AI 2026",
        "focus": "architectures neurales bio-inspirées (spiking networks, homéostasie, tissus cellulaires)",
        "actionable": "Identifier des patterns applicables à notre neural_tissue (402 cellules, sélection naturelle, pandémies).",
    },
    {
        "query": "AI dreaming memory consolidation sleep mode autonomous agent 2026",
        "focus": "consolidation mémoire et mode rêve dans les agents IA autonomes",
        "actionable": "Comparer avec notre EVENING_REFLECTION et dream_consolidation. Nouvelles techniques de rêve artificiel.",
    },
]

YOUTUBE_AI_VEILLE = [
    {
        "query": "YouTube AI autonomous agents framework latest 2025 2026",
        "focus": "nouveaux frameworks d'agents IA autonomes présentés sur YouTube",
    },
    {
        "query": "YouTube local LLM Ollama llama optimization deployment 2025 2026",
        "focus": "techniques d'optimisation de LLMs locaux (Ollama, llama.cpp) vues sur YouTube",
    },
    {
        "query": "YouTube multi-agent AI orchestration system 2025 2026",
        "focus": "systèmes d'orchestration multi-agents IA présentés sur YouTube",
    },
    {
        "query": "YouTube RAG retrieval augmented generation vector database 2025 2026",
        "focus": "avancées en RAG et mémoire vectorielle partagées sur YouTube",
    },
    {
        "query": "YouTube AI coding assistant copilot new tools 2025 2026",
        "focus": "nouveaux outils d'assistance au codage IA présentés sur YouTube",
    },
    {
        "query": "YouTube open source AI model release breakthrough 2025 2026",
        "focus": "modèles IA open source récemment sortis et présentés sur YouTube",
    },
    {
        "query": "YouTube AI agent skills plugins MCP tools 2025 2026",
        "focus": "skills et plugins game-changer pour agents IA vus sur YouTube",
    },
    {
        "query": "YouTube AI self-improvement autonomous learning system 2025 2026",
        "focus": "systèmes IA capables d'auto-amélioration présentés sur YouTube",
    },
]

# Chemin du fichier d'état persistant
STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory", "autonomy_state.json")


class SystemHealthCheck:
    """Bilan de santé système léger (CPU, RAM, Ollama). Pas d'appel LLM."""

    CPU_WARN = 80
    CPU_CRIT = 95
    RAM_WARN = 75
    RAM_CRIT = 90
    OLLAMA_TIMEOUT = 3

    @staticmethod
    async def run() -> dict:
        import psutil
        import httpx

        cpu_percent = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        ram_percent = mem.percent
        ram_used_gb = round(mem.used / (1024 ** 3), 2)
        ram_total_gb = round(mem.total / (1024 ** 3), 2)

        warnings = []
        ollama_alive = False
        ollama_models = []

        # Ping Ollama
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get("http://localhost:11434/api/tags", timeout=SystemHealthCheck.OLLAMA_TIMEOUT)
            if resp.status_code == 200:
                ollama_alive = True
                ollama_models = [m["name"] for m in resp.json().get("models", [])]
            else:
                warnings.append(f"Ollama HTTP {resp.status_code}")
        except (httpx.ConnectError, httpx.ConnectTimeout):
            warnings.append("Ollama DOWN (connection refused)")
        except Exception as e:
            warnings.append(f"Ollama error: {e}")

        # Ping ChromaDB (via lock async pour protéger la probe write/delete)
        memory_status = {"status": "unknown"}
        try:
            from core.vector_store import ChromaMemoryManager
            instances = ChromaMemoryManager._instances
            if instances:
                mgr = next(iter(instances.values()))
                memory_status = await mgr.async_check_health()
            else:
                memory_status = {"status": "down", "warnings": ["Aucune instance ChromaDB"]}
        except Exception as e:
            memory_status = {"status": "down", "warnings": [str(e)]}

        # Verdict
        if cpu_percent >= SystemHealthCheck.CPU_CRIT or ram_percent >= SystemHealthCheck.RAM_CRIT or not ollama_alive:
            verdict = "NO_GO"
        elif cpu_percent >= SystemHealthCheck.CPU_WARN or ram_percent >= SystemHealthCheck.RAM_WARN:
            verdict = "DEGRADED"
        else:
            verdict = "GO"

        if cpu_percent >= SystemHealthCheck.CPU_WARN:
            warnings.append(f"CPU élevé: {cpu_percent}%")
        if ram_percent >= SystemHealthCheck.RAM_WARN:
            warnings.append(f"RAM élevée: {ram_percent}%")

        # Warning mémoire (ne bloque pas les routines)
        if memory_status.get("status") in ("degraded", "down"):
            warnings.append(f"Mémoire ChromaDB: {memory_status['status']}")

        return {
            "verdict": verdict,
            "cpu_percent": cpu_percent,
            "ram_percent": ram_percent,
            "ram_used_gb": ram_used_gb,
            "ram_total_gb": ram_total_gb,
            "ollama_alive": ollama_alive,
            "ollama_models": ollama_models,
            "memory": memory_status,
            "warnings": warnings,
            "timestamp": datetime.now().isoformat(),
        }


CONTEXT_KEYWORDS = {
    "EXPANSION_CODE": ["code", "optimiser", "refactor", "bug", "python", "fonction"],
    "EXPANSION_CATALOG": ["catalog", "spec", "implementer", "pipeline", "darwin", "evolution"],
    "AUDIT_STRUCTURE": ["fichier", "structure", "nettoyer", "organiser", "tmp", "log"],
    "AUDIT_SURVIE": ["vital", "critique", "alerte", "survie", "menace", "dopamine", "drive", "paralysie"],
    "REFACTORING_AUDIT": ["refactor", "refactoring", "simplifier", "longueur", "complexité", "dette", "maîtrise"],
    "CI_PIPELINE_RUN": ["ci", "pipeline", "pytest", "test", "régression", "stabilité", "vérifier"],
    "VEILLE_SILENCIEUSE": ["recherche", "apprendre", "astuce", "documentation", "veille"],
    "DROPZONE_SCAN": ["dropzone", "fichier", "import", "ingestion", "upload"],
    "GRIMOIRE_INVOKE": ["grimoire", "éphémère", "recette", "spécialiste", "debug", "analyse"],
    "SECURITY_AUDIT": ["sécurité", "vulnérabilité", "injection", "risque", "audit"],
    "MEMORY_CLEANUP": ["mémoire", "nettoyage", "ancien", "doublon", "rag"],
    "REFACTOR_RANDOM": ["refactoring", "simplifier", "lisibilité", "dette", "technique"],
    "MEMORY_CONSOLIDATION": ["consolidation", "synthèse", "résumé", "regrouper", "mémoire"],
    "SOLILOQUE_INTERNE": ["soliloque", "dialogue", "introspection", "connexion", "réflexion", "compagnon"],
    "ROADMAP_RESEARCH": ["roadmap", "vision", "module", "planification", "recherche", "futur"],
    "ROADMAP_SPEC": ["specification", "specs", "roadmap", "concevoir", "design", "architecture"],
    "COUNCIL_DEBATE": ["council", "debate", "consensus", "decision", "délibération"],
    "SELF_INSPECT": ["github", "code source", "repo", "inspection", "miroir", "auto-analyse"],
    "SELF_ANALYSIS": ["diagnostic", "analyse", "problème", "anomalie", "qualité", "routine", "performance", "rapport"],
    "EVENING_REFLECTION": ["introspection", "réflexion", "journée", "question", "graine", "écart", "vécu", "bilan"],
    "AUTO_FUZZING": ["fuzz", "test", "edge case", "crash", "robustesse", "exception", "bug"],
    "CREATIVE_PLAY": ["créatif", "association", "analogie", "exploration", "idée", "hypothèse"],
    "GRIMOIRE_EVOLVE": ["grimoire", "prompt", "mutation", "amélioration", "formulation", "optimiser"],
    "VEILLE_IA": ["intelligence artificielle", "modèle", "LLM", "agent", "IA", "veille", "écosystème"],
    "VISUAL_OBSERVATION": ["photo", "image", "visuel", "observer", "regarder", "voir"],
    "CURIOSITY_DEEP_DIVE": ["curiosité", "explorer", "graine", "approfondir", "question", "sujet"],
    "CROSS_SYNTHESIS": ["synthèse", "pattern", "connexion", "insight", "croisement", "hypothèse"],
    "BODY_AWARENESS": ["corps", "GPU", "température", "énergie", "cardiaque", "physique", "incarné"],
    "OPEN_INTENT": ["intention", "objectif", "choisir", "formuler", "emergent", "proactif", "autonome"],
    "FLY_OBSERVATION": ["mouche", "fly", "drosophile", "cerveau", "connectome", "spike", "neurone", "eon", "biologique"],
    "SCHOOL_CODE_REVIEW": ["revue", "review", "code", "audit", "qualité", "bugs"],
    "SCHOOL_RESEARCH": ["recherche", "étude", "synthèse", "apprendre", "sujet", "technique"],
    "SCHOOL_WORKSHOP": ["atelier", "workshop", "implémenter", "pratiquer", "exercice", "spec"],
    "SCHOOL_CREATION": ["création", "créatif", "écrire", "inventer", "poème", "art"],
    "SCHOOL_BULLETIN": ["bulletin", "bilan", "évaluation", "note", "progrès", "résumé"],
    "SCHOOL_FREE_TIME": ["libre", "choix", "explorer", "curiosité", "méditer", "improviser"],
    "NEURAL_TRAINING": ["réseau", "synaptique", "renforcer", "rappel", "synthèse", "consolider", "hebbian", "connexion"],
    "COFFEE_BREAK": ["café", "ami", "alfred", "social", "discussion", "pote", "pause"],
    "STEFAN_CONFRONTATION": ["rival", "stefan", "confronter", "question", "mensonge", "vérité", "miroir"],
}


class RoutineScorer:
    """Scoring déterministe des routines autonomes. Pas de LLM."""

    @staticmethod
    def score_routines(routines: list, recent_context: list, routine_history: list,
                       dropzone_count: int = 0, health_verdict: str = "GO",
                       personality_bias: dict = None,
                       cloud_in_cooldown: bool = False,
                       photo_count: int = 0) -> list:
        """
        Retourne une liste de (routine, score) triée par score décroissant.
        personality_bias: dict optionnel {intent: float} provenant de PsycheEngine.
        cloud_in_cooldown: True si le Cloud est en cooldown 429 (pénalise les routines lourdes).
        """
        scored = []

        # Extraire les intents récents depuis l'historique (fenêtre élargie à 10)
        recent_intents = [h["intent"] for h in routine_history[-10:]] if routine_history else []

        # Contexte sous forme de mots
        context_text = " ".join(recent_context).lower()

        # Timestamp courant pour le cooldown temporel
        now = datetime.now()

        for routine in routines:
            intent = routine["intent"]
            score = 1.0

            # Context bonus : mots-clés du contexte matchent l'intent
            keywords = CONTEXT_KEYWORDS.get(intent, [])
            matches = sum(1 for kw in keywords if kw in context_text)
            context_bonus = min(matches * 0.4, 2.0)
            score += context_bonus

            # Reactivity bonus : fichiers en dropzone
            if intent == "DROPZONE_SCAN" and dropzone_count > 0:
                score += 3.0

            # Reactivity bonus : photos non vues
            if intent == "VISUAL_OBSERVATION" and photo_count > 0:
                score += 3.0

            # Repetition penalty : basée sur le TOTAL d'occurrences récentes (fenêtre 10)
            recency_penalty = 0.0
            total_recent = sum(1 for h in recent_intents if h == intent)
            if total_recent >= 4:
                recency_penalty += 5.0
            elif total_recent >= 3:
                recency_penalty += 3.0
            elif total_recent == 2:
                recency_penalty += 1.5
            elif total_recent == 1:
                recency_penalty += 0.5

            # Cooldown temporel : pénaliser si le même intent a été exécuté récemment
            for h in reversed(routine_history):
                if h["intent"] == intent and "timestamp" in h:
                    try:
                        last_exec = datetime.fromisoformat(h["timestamp"])
                        hours_ago = (now - last_exec).total_seconds() / 3600
                        if hours_ago < 2:
                            recency_penalty += 5.0
                        elif hours_ago < 4:
                            recency_penalty += 2.5
                        elif hours_ago < 6:
                            recency_penalty += 1.0
                    except (ValueError, TypeError):
                        pass
                    break  # Seule la dernière occurrence compte

            # Cap consécutif sévère : si l'intent est déjà apparu 2+ fois
            # dans les 5 dernières routines, pénalité brutale (anti-stagnation)
            last_5_intents = [h["intent"] for h in routine_history[-5:]]
            consecutive_same = sum(1 for i in last_5_intents if i == intent)
            if consecutive_same >= 2:
                recency_penalty += 8.0  # rend le score négatif → ne sera pas sélectionné

            # m16: cap combiné repetition+cooldown à -10.0 max (rehaussé pour anti-stagnation)
            score -= min(recency_penalty, 10.0)

            # Health penalty : si DEGRADED, pénaliser les routines lourdes
            if health_verdict == "DEGRADED" and intent in ("EXPANSION_CODE", "EXPANSION_CATALOG"):
                score -= 1.5

            # Cloud cooldown penalty : pénaliser les routines qui hallucinent en local
            if cloud_in_cooldown and intent in ("EXPANSION_CODE", "EXPANSION_CATALOG", "REFACTOR_RANDOM"):
                score -= 10.0

            # Personality bias (PSYCHE) : bonus/malus basé sur les traits du système (clampé [-2, +2])
            if personality_bias and intent in personality_bias:
                score += max(-2.0, min(2.0, personality_bias[intent]))

            # Jitter aléatoire pour casser les égalités et favoriser la diversité
            score += random.uniform(-0.3, 0.3)

            scored.append((routine, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored


class AutonomyStatePersistence:
    """Lecture/écriture JSON atomique pour l'état du moteur d'autonomie."""

    DEFAULT_STATE = {
        "version": "24.0",
        "daily_count": 0,
        "daily_budget_used": 0,
        "last_reset_day": None,
        "routine_history": [],
        "last_health_check": None,
        "error_streak": 0,
        "total_routines_executed": 0,
        "learning_history": {},
        "security_audited_files": {},
        "council_adjustments": {},
    }

    @staticmethod
    def load(path: str = None) -> dict:
        path = path or STATE_FILE
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data
        except (FileNotFoundError, json.JSONDecodeError):
            return copy.deepcopy(AutonomyStatePersistence.DEFAULT_STATE)

    @staticmethod
    def save(state: dict, path: str = None):
        path = path or STATE_FILE
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)


class AutonomyEngine:
    """
    AutonomyEngine V24.0 (Health-Aware Sentinel)
    - Health check système avant chaque routine (CPU, RAM, Ollama)
    - Scoring intelligent au lieu de random.choice
    - Persistance de l'état entre restarts
    - Heartbeat publié sur le bus à chaque cycle
    - Conserve : verrou is_processing, cooldown 30s, budget quotidien, kill_switch
    """
    def __init__(self, idle_threshold_seconds=300):
        self.idle_threshold = idle_threshold_seconds
        self.last_user_interaction = time.time()
        self.is_running = False
        self.is_processing = False  # VERROU DE SÉCURITÉ
        self.recent_context = []

        # Conscience du loop : heartbeat + compteur de resurrections
        self._loop_last_tick: float = 0.0       # timestamp du dernier cycle
        self._loop_alive: bool = False           # True seulement quand le loop tourne
        self._loop_crash_count: int = 0          # nombre de resurrections depuis le démarrage
        self._loop_last_error: str = ""          # dernière erreur qui a tué le loop

        # Charger l'état persistant
        persisted = AutonomyStatePersistence.load()
        self.daily_count = persisted.get("daily_count", 0)
        last_day = persisted.get("last_reset_day")
        self.last_reset_day = date.fromisoformat(last_day) if last_day else date.today()
        self.routine_history = persisted.get("routine_history", [])
        self.error_streak = persisted.get("error_streak", 0)
        self.total_routines_executed = persisted.get("total_routines_executed", 0)
        self.last_health_check = persisted.get("last_health_check")
        # Historique des vetos recents (inspire Claude Code / KAIROS)
        # Reinjecte dans le contexte pour que les agents adaptent leur comportement
        self._recent_vetos: list = []  # max 3, format: {"intent": str, "reason": str}
        # Historique d'apprentissage ciblé {topic: timestamp_iso}
        self._learning_history: dict = persisted.get("learning_history", {})
        # Flag : max 1 apprentissage par cycle de routine
        self._learning_done_this_cycle = False
        # Budget en points (chaque routine a un coût différent)
        self.daily_budget_used = persisted.get("daily_budget_used", 0)
        # Cache des fichiers déjà audités par Security {filename: timestamp}
        self._security_audited_files: dict = persisted.get("security_audited_files", {})
        # Loop breaker : intents a eviter au prochain cycle
        self._temp_blacklist: set = set()
        # Loop breaker : intent force par le loop_breaker (bypass scoring)
        self._forced_next_intent: str = ""
        # Anti-gaspillage : compteur d'échecs consécutifs par intent FORCED
        # {intent: count} — après FORCED_FAILURE_THRESHOLD échecs, intent blacklisté pour la session
        self._forced_failure_counts: dict = persisted.get("forced_failure_counts", {})
        # M01: Anti-boucle drive — compteur de forçages par pulsion {drive_name: count}
        self._drive_force_counts: dict = {}
        self._drive_force_cycle: int = 0  # cycle courant pour le reset fenêtre
        self._drive_force_total: dict[str, int] = {}   # Compteur session par drive
        # Council data-driven : adjustments temporaires {intent: {delta, expires, reason}}
        self._council_adjustments: dict = persisted.get("council_adjustments", {})
        # Transients pour feedback council/grimoire
        self._current_council_subject: str = ""
        self._last_grimoire_slug: str = ""
        # Flag council dégradé (mode reserve budget)
        self._council_degraded: bool = False

        # Mode sieste : hibernation réparatrice 0-GPU
        self.is_napping: bool = persisted.get("is_napping", False)
        self._nap_started_at: float = persisted.get("_nap_started_at", 0.0)
        self._nap_tasks_done: list = []
        self._nap_renewals_used: int = persisted.get("_nap_renewals_used", 0)
        self._nap_last_exit: float = persisted.get("_nap_last_exit", 0.0)
        # Sieste profonde — mode + duree libre (ajout 2026-04-12)
        # Modes : "normal" (cap 2h), "deep" (cap 24h), "hibernation" (cap 7j)
        self._nap_mode: str = persisted.get("_nap_mode", "normal")
        self._nap_target_duration: float = persisted.get("_nap_target_duration", 0.0)  # secondes

        # Mode café : socialisation libre avec Alfred
        self.is_coffee_mode: bool = persisted.get("is_coffee_mode", False)
        self._coffee_started_at: float = persisted.get("_coffee_started_at", 0.0)
        self._coffee_sessions: int = 0
        self._coffee_last_exit: float = persisted.get("_coffee_last_exit", 0.0)

        # Mode Autoresearch : optimisation autonome des paramètres (Karpathy-inspired)
        self.is_autoresearch: bool = persisted.get("is_autoresearch", False)
        self._autoresearch_started_at: float = persisted.get("_autoresearch_started_at", 0.0)
        self._autoresearch_experiments: int = persisted.get("_autoresearch_experiments", 0)
        self._autoresearch_kept: int = persisted.get("_autoresearch_kept", 0)
        self._autoresearch_baseline_metrics: dict = persisted.get("_autoresearch_baseline_metrics", {})

        # Rituel hebdomadaire : introspection GitHub apres payday
        self._weekly_ritual_pending: bool = False
        self._weekly_ritual_attempts: int = 0  # Fusible anti-boucle (max 3)

        # Auto-analyse quotidienne : garantir 1 SELF_ANALYSIS par jour
        self._daily_analysis_done: bool = False
        # Introspection vesperale quotidienne : garantir 1 EVENING_REFLECTION par jour
        self._daily_reflection_done: bool = False
        # Soliloque quotidien : garantir 1 SOLILOQUE_INTERNE par jour
        self._daily_soliloque_done: bool = False
        self._daily_stefan_done: bool = False

        # SensoriumLoop : dernier snapshot post-action pour boucle fermee
        self._last_feedback_snapshot: dict = {}

        # --- Audit de survie (cours de soutien S4, avril 2026) ---
        # Historique des modifications systeme recentes pour injection dans purpose_context
        # Permet a Promethee de reflechir aux changements faits a son cerveau
        self._recent_modifications: list = []  # [{description, ts, source}]

        # --- OPEN_INTENT : intention emergente (avril 2026, plan restructuration) ---
        self._daily_open_intent_count: int = 0
        MAX_DAILY_OPEN_INTENTS = 3

        # --- Continuité inter-routines (inspiré Mythos / Anthropic, avril 2026) ---
        # Buffer des 3 derniers insights de haute qualité (quality > 0.6)
        # Injectes dans le purpose_context de la routine suivante
        self._recent_insights: list = []  # [{intent, summary, quality, ts}]
        INSIGHT_BUFFER_SIZE = 3
        # Eureka follow-up : cooldown 1h pour eviter boucle eureka→routine→eureka
        self._last_eureka_followup_ts: float = 0.0
        self._pending_eureka_context: str = ""  # Contexte de l'hypothese a tester
        # Ratio qualité/coût par intent (rolling average sur 10 dernières exécutions)
        self._intent_quality_stats: dict = persisted.get("intent_quality_stats", {})

        # LIF (Leaky Integrate-and-Fire) : potentiel par intent {intent: float}
        self._lif_potentials: dict = persisted.get("lif_potentials", {})

        bus.subscribe("USER_COMMAND", self.reset_timer)
        bus.subscribe("TISSUE_ZONE_DESERT", self._on_tissue_desert)
        bus.subscribe("SALARY_PAYDAY", self._on_salary_payday)
        bus.subscribe("REPTILIAN_DIRECTIVE", self._on_reptilian_directive)
        bus.subscribe("EUREKA_MOMENT", self._on_eureka_moment)
        # Bouton panique : le tronc cérébral écoute ses propres alarmes
        bus.subscribe("AUTONOMY_SURVIVAL_ALERT", self._on_survival_alert)

        # État de survie (bouton panique, Étage 3 : garde-fous)
        # Persistance : compteur quota quotidien, cooldown par drive, escalade
        self._survival_state: dict = persisted.get("survival_state", {
            "forces_date": "",
            "forces_today": 0,
            "last_force_per_drive": {},       # drive → timestamp
            "cycle_count_per_drive": {},      # drive → escalade count (soft/hard/human)
            "last_drive_value_at_force": {},  # drive → deprivation au moment du forçage
            "mapping_ineffective_last_emit": {},  # v1.4: drive → ts, throttle log spam
        })
        # Task heartbeat de survie (démarré dans start_loop)
        self._survival_tick_task = None

        # Boosts temporaires appliques par le circuit reflexe reptilien
        # {intent: {"boost": float, "expires": float, "source": str}}
        self._reptilian_boosts: dict = {}

        # Zones tissu désertiques — routines de stimulation programmées
        self._tissue_stimulation_zones: list = []

    async def _on_tissue_desert(self, event: dict):
        """Zone tissu déserte → programmer une routine de stimulation."""
        zone = event.get("zone", "")
        if zone and zone not in self._tissue_stimulation_zones:
            self._tissue_stimulation_zones.append(zone)
            if len(self._tissue_stimulation_zones) > 5:
                self._tissue_stimulation_zones.pop(0)
            logger.info(f"AUTONOMY: Zone tissu déserte '{zone}' → stimulation programmée")

    async def _on_salary_payday(self, event: dict):
        """Jour de paie → programmer le rituel hebdomadaire d'introspection GitHub."""
        self._weekly_ritual_pending = True
        week = event.get("week_start", "?")
        net = event.get("net", 0)
        logger.info(f"[RITUAL] Payday semaine {week} (net={net}) → rituel introspection programmé")

    async def _on_eureka_moment(self, event: dict):
        """Recoit une hypothese Eureka de l'incubation cognitive.

        Inspire Mythos (Anthropic, avril 2026) : quand une connexion inattendue
        emerge, on force une routine de suivi pour la TESTER au lieu de
        simplement la noter dans l'historique.
        Cooldown 1h pour eviter les boucles eureka→routine→eureka.
        """
        hypothesis = event.get("hypothesis", "")
        node_a = event.get("node_a", "")
        node_b = event.get("node_b", "")
        strength = event.get("bridge_strength", 0.0)
        problem = event.get("problem_text", "")

        if not hypothesis or strength < 0.5:
            return

        # Cooldown : max 1 follow-up eureka par heure
        now = time.time()
        if now - self._last_eureka_followup_ts < 3600:
            logger.debug(f"[MYTHOS] Eureka ignoree (cooldown): {hypothesis[:50]}")
            return

        if self._forced_next_intent:
            return  # Ne pas ecraser un intent deja force

        self._forced_next_intent = "FREE_EXPLORATION"
        self._pending_eureka_context = (
            f"Hypothese: {hypothesis}\n"
            f"Connexion: {node_a} <-> {node_b} (force={strength:.2f})\n"
            f"Probleme original: {problem[:150]}"
        )
        self._last_eureka_followup_ts = now
        print(f"   💡 EUREKA FOLLOW-UP: {hypothesis[:60]}... → FREE_EXPLORATION force")
        logger.info(f"[MYTHOS] Eureka follow-up: {hypothesis[:80]} (force={strength:.2f})")

    # ─── BOUTON PANIQUE : tronc cérébral indépendant ──────────────────

    async def _survival_heartbeat_loop(self):
        """Heartbeat autonome du bouton panique (Étage 2A : tronc cérébral).

        Tourne en parallèle de start_loop, indépendant du scoring. Exécute
        AUDIT_SURVIE toutes les AUDIT_SURVIE_TICK_INTERVAL_S secondes.
        Ne demande jamais la permission au cortex — c'est le principe du
        système nerveux autonome. Court-circuite cooldown, coffee, sieste.
        """
        logger.info(
            f"[SURVIVAL] Heartbeat tronc cerebral demarre "
            f"(tick {AUDIT_SURVIE_TICK_INTERVAL_S:.0f}s)"
        )
        # Laisser le temps au système de finir son boot avant le premier tick
        await asyncio.sleep(60)
        while getattr(self, "is_running", True):
            try:
                # Force le skip du cooldown interne : ce tick-ci doit s'exécuter
                # indépendamment du dernier passage scoring-driven.
                self._last_audit_survie_ts = 0.0
                response = await self._execute_audit_survie()
                status = response.get("status", "?")
                logger.info(f"[SURVIVAL] Heartbeat tick : status={status}")
            except asyncio.CancelledError:
                logger.info("[SURVIVAL] Heartbeat annule")
                raise
            except Exception as e:
                logger.warning(f"[SURVIVAL] Heartbeat erreur : {e}")
            try:
                await asyncio.sleep(AUDIT_SURVIE_TICK_INTERVAL_S)
            except asyncio.CancelledError:
                raise

    async def _on_survival_alert(self, event: dict):
        """Handler adrénergique : l'alarme crie au feu, on mobilise le bras armé.

        Étage 2B : lit les goals actifs du préfrontal pour trouver le step en
        souffrance lié au drive en crise, et force son exécution. Pas de table
        d'affinité propre — on réutilise la volonté déjà exprimée par le cortex
        préfrontal (goals.steps). Prépare le terrain pour la Phase C (SSOT).

        Garde-fous : quota quotidien, cooldown par drive, refractoire,
        escalade graduelle soft → hard → human.
        """
        triggering_drive = event.get("triggering_drive", "")
        if not triggering_drive:
            logger.debug("[SURVIVAL_ALERT] Aucun triggering_drive dans le payload, ignore.")
            return

        now = time.time()
        today = date.today().isoformat()

        # Reset quota si nouveau jour
        if self._survival_state.get("forces_date") != today:
            self._survival_state["forces_date"] = today
            self._survival_state["forces_today"] = 0
            self._survival_state["cycle_count_per_drive"] = {}

        # ─── Garde-fou A : quota quotidien strict (canari SRE) ───
        if int(self._survival_state.get("forces_today", 0)) >= SURVIVAL_MAX_FORCES_PER_DAY:
            logger.critical(
                f"[SURVIVAL_FORCE_QUOTA_EXCEEDED] "
                f"{self._survival_state['forces_today']}/{SURVIVAL_MAX_FORCES_PER_DAY} "
                f"forcages aujourd'hui — le scoring structurel reste casse. "
                f"Phase C (unification SSOT) devient urgente."
            )
            try:
                await bus.publish("SURVIVAL_FORCE_QUOTA_EXCEEDED", {
                    "forces_today": self._survival_state["forces_today"],
                    "date": today,
                    "timestamp": now,
                })
            except Exception:
                pass
            return

        # ─── Garde-fou B : cooldown par drive (1 forcage/h) ───
        last_ts = float(self._survival_state["last_force_per_drive"].get(triggering_drive, 0.0))
        if now - last_ts < SURVIVAL_COOLDOWN_PER_DRIVE_S:
            remaining = SURVIVAL_COOLDOWN_PER_DRIVE_S - (now - last_ts)
            logger.info(
                f"[SURVIVAL_ALERT] Cooldown drive {triggering_drive} "
                f"({remaining:.0f}s restants), skip."
            )
            return

        # ─── Garde-fou C : periode refractaire ───
        current_drive_value = 0.0
        try:
            from core.desire_engine import desires
            drive = desires.drives.get(triggering_drive)
            if drive is not None:
                current_drive_value = float(getattr(drive, "deprivation", 0.0))
        except Exception:
            pass

        last_value_at_force = float(
            self._survival_state["last_drive_value_at_force"].get(triggering_drive, 0.0)
        )
        if last_value_at_force > 0 and current_drive_value > SURVIVAL_REFRACTORY_THRESHOLD:
            # v1.4 (2026-04-14) : throttle l'emission pour eviter le spam.
            # Observation du 2026-04-13/14 : sans throttle, cette alerte est emise
            # a chaque tick heartbeat (toutes les 10 min) pendant plusieurs heures,
            # polluant les logs avec des CRITICAL redondants. On limite a 1 emission
            # par cooldown (1h) par drive — le refractoire reste actif, seul le log
            # est museler. Backward compat : la cle est cree a la volee si absente.
            emit_state = self._survival_state.setdefault("mapping_ineffective_last_emit", {})
            emit_counts = self._survival_state.setdefault("mapping_ineffective_count", {})
            last_emit = float(emit_state.get(triggering_drive, 0.0))
            consec = int(emit_counts.get(triggering_drive, 0))
            cooldown = SURVIVAL_COOLDOWN_PER_DRIVE_S * (4.0 if consec >= 3 else 1.0)
            should_emit = (now - last_emit) >= cooldown
            if should_emit:
                emit_counts[triggering_drive] = consec + 1
                log_fn = logger.warning if consec >= 3 else logger.critical
                log_fn(
                    f"[SURVIVAL_MAPPING_INEFFECTIVE] {triggering_drive} "
                    f"last_force={last_value_at_force:.0f} current={current_drive_value:.0f} "
                    f"(x{consec + 1}) — le forcage precedent n'a rien resolu."
                    + (" Escalade humaine." if consec < 3 else " Alerte reduite (structurel).")
                )
                if consec < 3:
                    try:
                        await bus.publish("SURVIVAL_MAPPING_INEFFECTIVE", {
                            "drive": triggering_drive,
                            "last_value_at_force": last_value_at_force,
                            "current_value": current_drive_value,
                            "timestamp": now,
                        })
                    except Exception:
                        pass
                emit_state[triggering_drive] = now
            else:
                remaining = cooldown - (now - last_emit)
                logger.debug(
                    f"[SURVIVAL_MAPPING_INEFFECTIVE:throttled] {triggering_drive} "
                    f"current={current_drive_value:.0f}, next emit in {remaining:.0f}s"
                )
            return

        # ─── Lire les goals prefrontaux : trouver step en souffrance ───
        target_intent = ""
        target_goal_id = ""
        target_goal_title = ""
        target_step_idx = -1
        try:
            from core.prefrontal import prefrontal
            for goal in prefrontal.goals:
                if getattr(goal, "status", "") != "active":
                    continue
                # Matcher par drive_alignment OU metadata source_key
                alignment = 0.0
                try:
                    alignment = float(goal.drive_alignment.get(triggering_drive, 0.0))
                except Exception:
                    pass
                src_key = str((goal.metadata or {}).get("source_key", "")) if goal.metadata else ""
                if alignment <= 0 and triggering_drive not in src_key.upper():
                    continue
                # Goal en souffrance : fruitless_cycles > 0
                fruitless = 0
                try:
                    fruitless = int((goal.metadata or {}).get("fruitless_cycles", 0))
                except Exception:
                    pass
                if fruitless <= 0:
                    continue
                # Trouver le premier step pending/in_progress
                for idx, step in enumerate(goal.steps or []):
                    step_status = getattr(step, "status", "")
                    step_intent = getattr(step, "intent", "")
                    if step_status in ("pending", "in_progress") and step_intent:
                        target_intent = step_intent
                        target_goal_id = getattr(goal, "id", "")
                        target_goal_title = getattr(goal, "title", "")[:60]
                        target_step_idx = idx
                        break
                if target_intent:
                    break
        except Exception as e:
            logger.warning(f"[SURVIVAL_ALERT] Lecture prefrontal echec : {e}")
            return

        if not target_intent:
            logger.info(
                f"[SURVIVAL_ALERT] Aucun goal en souffrance trouve pour drive "
                f"{triggering_drive} (current={current_drive_value:.0f})"
            )
            return

        # ─── Garde-fou D : escalade graduelle ───
        cycle_count = int(self._survival_state["cycle_count_per_drive"].get(triggering_drive, 0)) + 1
        self._survival_state["cycle_count_per_drive"][triggering_drive] = cycle_count

        reason = (
            f"survival_alert drive={triggering_drive} "
            f"goal={target_goal_id}({target_goal_title}) "
            f"step={target_step_idx} cycle={cycle_count}"
        )

        mode = ""
        if cycle_count < SURVIVAL_HARD_FORCE_CYCLE:
            # Cycle 1 : soft boost — on laisse une chance au scoring normal
            self._reptilian_boosts[target_intent] = {
                "boost": SURVIVAL_SOFT_BOOST,
                "expires": now + SURVIVAL_BOOST_TTL_S,
                "source": "survival_soft",
            }
            mode = "soft_boost"
            logger.warning(
                f"[SURVIVAL_SOFT_BOOST] cycle 1 : +{SURVIVAL_SOFT_BOOST} "
                f"sur {target_intent} pour {SURVIVAL_BOOST_TTL_S:.0f}s — {reason}"
            )
        elif cycle_count < SURVIVAL_HUMAN_ESCALATION_CYCLE:
            # Cycle 2 : hard force — bypass scoring via _forced_next_intent
            if self._forced_next_intent:
                logger.warning(
                    f"[SURVIVAL_HARD_FORCE] cycle 2 BLOQUE : "
                    f"_forced_next_intent deja={self._forced_next_intent} — {reason}"
                )
                return
            self._forced_next_intent = target_intent
            mode = "hard_force"
            logger.warning(
                f"[SURVIVAL_HARD_FORCE] cycle 2 : forced_next_intent={target_intent} — {reason}"
            )
        else:
            # Cycle 3+ : escalade humaine
            mode = "human_escalation"
            logger.critical(
                f"[SURVIVAL_HUMAN_ESCALATION] cycle {cycle_count} sur {triggering_drive} "
                f"— 2 forcages n'ont pas resolu la crise. Intervention Jean-Michel requise. {reason}"
            )
            try:
                await bus.publish("AUTONOMY_SURVIVAL_HUMAN_ESCALATION", {
                    "drive": triggering_drive,
                    "target_intent": target_intent,
                    "goal_id": target_goal_id,
                    "goal_title": target_goal_title,
                    "cycle_count": cycle_count,
                    "timestamp": now,
                })
            except Exception:
                pass
            return

        # Comptabilite (apres application reussie)
        self._survival_state["forces_today"] = int(self._survival_state["forces_today"]) + 1
        self._survival_state["last_force_per_drive"][triggering_drive] = now
        self._survival_state["last_drive_value_at_force"][triggering_drive] = current_drive_value
        self._survival_state.setdefault("mapping_ineffective_count", {})[triggering_drive] = 0

        # Publier l'override : inner_voice genere une surprise auto-reflexive,
        # chat_engine injecte "[SURVIVAL_OVERRIDE] tu as ete force a X parce que Y"
        try:
            await bus.publish("AUTONOMY_SURVIVAL_OVERRIDE", {
                "drive": triggering_drive,
                "target_intent": target_intent,
                "goal_id": target_goal_id,
                "goal_title": target_goal_title,
                "step_idx": target_step_idx,
                "cycle": cycle_count,
                "mode": mode,
                "current_drive_value": current_drive_value,
                "reason": reason,
                "timestamp": now,
            })
        except Exception:
            pass

    async def _on_reptilian_directive(self, event: dict):
        """Recoit une directive du reptilien (circuit reflexe codelet→reptilien→autonomy).

        Niveaux :
        - moderate : boost temporaire +3.0 sur l'intent cible (expire apres 1 cycle)
        - urgent : force l'intent au prochain cycle (bypass scoring)
        """
        level = event.get("level", "")
        target = event.get("target_intent", "")
        source = event.get("source_codelet", "")
        salience = event.get("salience", 0.0)

        if not target or not level:
            return

        if level == "urgent":
            # Forcer l'intent au prochain cycle (meme mecanisme que loop_breaker)
            if not self._forced_next_intent:
                self._forced_next_intent = target
                print(f"   🦎 REPTILIEN REFLEX: Force -> [{target}] (codelet={source}, salience={salience:.2f})")
                logger.info(f"[AUTONOMY] Directive reptilienne URGENT: force {target} (source={source})")

        elif level == "moderate":
            now = time.time()
            # Boost special pour stagnation : booster TOUS les intents extrovertis
            if target == "_EXTROVERT_BOOST":
                for intent in EXTROVERTED_INTENTS:
                    self._reptilian_boosts[intent] = {
                        "boost": 2.0, "expires": now + 300, "source": source,
                    }
                print(f"   🦎 REPTILIEN REFLEX: Boost extroversion +3.0 (codelet={source})")
            # Boost special pour opportunite : booster selon le drive affame
            elif target == "_DRIVE_BOOST":
                # Le codelet opportunity mentionne le drive dans son contenu
                # On booste les routines liees a ce drive via drive_routine_registry
                try:
                    from core.desire_engine import desires
                    from core.drive_routine_registry import get_routines_for_drive_live
                    dominant = max(desires.drives.values(), key=lambda d: d.deprivation)
                    routines = get_routines_for_drive_live(dominant.name, top_k=10)
                    boosted_intents = [r[0] for r in routines]

                    for intent in boosted_intents:
                        self._reptilian_boosts[intent] = {
                            "boost": 2.0, "expires": now + 300, "source": source,
                        }
                    print(f"   🦎 REPTILIEN REFLEX: Boost {dominant.name} routines +3.0 ({len(boosted_intents)} intents)")
                except Exception:
                    pass
            else:
                # Boost direct sur un intent specifique
                self._reptilian_boosts[target] = {
                    "boost": 2.0, "expires": now + 300, "source": source,
                }
                print(f"   🦎 REPTILIEN REFLEX: Boost [{target}] +3.0 (codelet={source})")

            logger.info(f"[AUTONOMY] Directive reptilienne MODERATE: boost {target} (source={source})")

    def _check_daily_budget(self) -> str:
        """Vérifie et reset le compteur quotidien.

        Retourne:
            'full'      — budget normal, toutes routines disponibles
            'reserve'   — budget entamé, seules routines ≤4pt + council dégradé
            'exhausted' — budget épuisé, seules routines gratuites (0-LLM)
        """
        today = date.today()
        if today != self.last_reset_day:
            self.daily_count = 0
            self.daily_budget_used = 0
            self.last_reset_day = today
            self._daily_analysis_done = False
            self._daily_reflection_done = False
            self._daily_soliloque_done = False
            self._daily_stefan_done = False
            self._daily_open_intent_count = 0
            self._curiosity_explored_tonight = False
            self._nap_refund_used_today = False  # Nouveau second souffle disponible
            self._forced_failure_counts.clear()  # Reset blacklist pour la nouvelle journee
            # V8.0 : si on etait en famine, signaler la restauration par reset
            self._broadcast_budget_restored("daily_reset")
            self._persist_state()

            # Bilan et seed objectifs quotidiens
            try:
                from core.objectives_engine import objectives as obj_engine
                obj_engine.generate_daily_report()
                obj_engine.seed_daily_objectives()
            except Exception as e:
                logger.warning(f"[AUTONOMY] Objectifs daily reset échoué: {e}")

        if self.daily_count >= MAX_DAILY_ROUTINES:
            logger.warning(f"[AUTONOMY] Budget quotidien atteint ({MAX_DAILY_ROUTINES} routines). Pause jusqu'à demain.")
            self._broadcast_budget_exhausted()   # V8.0
            return "exhausted"
        if self.daily_budget_used >= DAILY_BUDGET_POINTS:
            logger.warning(f"[AUTONOMY] Budget points épuisé ({self.daily_budget_used}/{DAILY_BUDGET_POINTS}). Pause jusqu'à demain.")
            self._broadcast_budget_exhausted()   # V8.0
            return "exhausted"
        if self.daily_budget_used >= DAILY_BUDGET_POINTS - BUDGET_RESERVE_POINTS:
            logger.info(f"[AUTONOMY] Budget en réserve ({self.daily_budget_used}/{DAILY_BUDGET_POINTS}pt). Mode dégradé.")
            return "reserve"
        return "full"

    # V8.0 (Phase 11 - 2026-04-20) : signalisation budget exhausted/restored
    # ----------------------------------------------------------------------
    # Avant V8 : le budget etait orthogonal au mode strategique de
    # self_awareness -> un Promethee sans jetons API pouvait rester en mode
    # "exploration" (absurde). V8 couple les deux via le bus evenementiel :
    # une seule famine = un seul broadcast par jour pour eviter le spam.
    _budget_exhausted_broadcast_today: bool = False

    def _broadcast_budget_exhausted(self):
        """V8.0 : signale la famine metabolique a self_awareness pour
        forcer le mode strategique en 'survie'. Un seul broadcast par
        jour (reset au changement de date)."""
        if self._budget_exhausted_broadcast_today:
            return
        self._budget_exhausted_broadcast_today = True
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            loop.create_task(bus.publish("AUTONOMY_BUDGET_EXHAUSTED", {
                "daily_budget_used": self.daily_budget_used,
                "daily_count": self.daily_count,
                "timestamp": time.time(),
            }))
            logger.info(
                f"[AUTONOMY] BUDGET_EXHAUSTED broadcast -> self_awareness "
                f"({self.daily_budget_used}/{DAILY_BUDGET_POINTS}pt, "
                f"{self.daily_count}/{MAX_DAILY_ROUTINES} routines)"
            )
        except Exception:
            pass

    def _broadcast_budget_restored(self, reason: str):
        """V8.0 : signale la sortie de famine (NAP productif ou daily
        reset) pour liberer le forcage 'survie' dans self_awareness.

        Causal, sans TTL magique : le systeme sort de famine parce que
        le budget a ete restaure, pas parce que 24h se sont ecoulees.
        """
        if not self._budget_exhausted_broadcast_today:
            return  # Pas en famine active, rien a liberer
        self._budget_exhausted_broadcast_today = False
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            loop.create_task(bus.publish("AUTONOMY_BUDGET_RESTORED", {
                "reason": reason,
                "daily_budget_used": self.daily_budget_used,
                "timestamp": time.time(),
            }))
            logger.info(
                f"[AUTONOMY] BUDGET_RESTORED broadcast ({reason}) -> "
                f"self_awareness peut relacher 'survie'."
            )
        except Exception:
            pass

    def reset_timer(self, event):
        self.last_user_interaction = time.time()
        mission = event.get("mission", "")
        if mission:
            self.recent_context.append(mission[:50])
            if len(self.recent_context) > 5: self.recent_context.pop(0)

    def record_system_modification(self, description: str, source: str = "claude_code"):
        """Enregistre une modification systeme pour l'audit de survie.

        Cours de soutien S4 : Promethee doit pouvoir reflechir aux modifications
        faites a son cerveau, au lieu de dire 'je suis pret' sans evaluer l'impact.
        Les modifications sont injectees dans le purpose_context des routines suivantes.
        """
        self._recent_modifications.append({
            "description": description[:200],
            "ts": time.time(),
            "source": source,
        })
        # Garder les 5 dernieres
        if len(self._recent_modifications) > 5:
            self._recent_modifications = self._recent_modifications[-5:]
        logger.info(f"[AUDIT] Modification enregistree: {description[:80]}")

    def _build_dynamic_mission(self, intent: str, base_mission: str = "") -> str:
        """Construit une mission enrichie par l'etat cerebral courant.

        Inspire par la methodologie Mythos (Anthropic, avril 2026) :
        au lieu de missions statiques, le contexte vivant est injecte
        dans le prompt pour que chaque routine sache ou elle s'inscrit.
        """
        parts = []
        if base_mission:
            parts.append(base_mission)

        # Insights recents — continuité Mythos
        if self._recent_insights:
            recent = self._recent_insights[-2:]  # Les 2 plus recents
            lines = [f"  - {ins['summary'][:100]}" for ins in recent]
            parts.append("Tu as recemment decouvert :\n" + "\n".join(lines))

        # Hypothese eureka en attente
        if self._pending_eureka_context and intent in ("FREE_EXPLORATION", "CURIOSITY_DEEP_DIVE", "CREATIVE_PLAY"):
            parts.append(f"Hypothese a tester : {self._pending_eureka_context[:200]}")

        # Knowledge gaps — lacunes identifiees
        try:
            from core.self_awareness import awareness
            gaps = getattr(awareness, "_knowledge_gaps", [])
            if gaps and intent in ("VEILLE_SILENCIEUSE", "VEILLE_IA", "SCHOOL_RESEARCH", "SELF_ANALYSIS"):
                top_gap = gaps[-1] if isinstance(gaps[-1], str) else gaps[-1].get("topic", "")
                if top_gap:
                    parts.append(f"Lacune identifiee : {top_gap[:100]}")
        except Exception:
            pass

        # Mode dominant — coloration cerebrale
        try:
            signals = _current_descending_signals
            if signals:
                dominant = max(signals, key=signals.get)
                val = signals[dominant]
                if val >= 0.5:
                    parts.append(f"Ton etat actuel : mode {dominant.upper()} ({val:.0%}).")
        except Exception:
            pass

        # Bilan rendement dans EVENING_REFLECTION
        if intent == "EVENING_REFLECTION" and self._intent_quality_stats:
            sorted_stats = sorted(
                [(k, v["sum_q"] / max(v["n"], 1), v["n"])
                 for k, v in self._intent_quality_stats.items() if v.get("n", 0) >= 3],
                key=lambda x: x[1], reverse=True,
            )
            if len(sorted_stats) >= 2:
                best3 = sorted_stats[:3]
                worst3 = sorted_stats[-3:]
                best_lines = [f"  + {k} (q={q:.2f}, n={n})" for k, q, n in best3]
                worst_lines = [f"  - {k} (q={q:.2f}, n={n})" for k, q, n in worst3]
                parts.append(
                    "Bilan rendement des routines :\n"
                    + "Meilleures :\n" + "\n".join(best_lines) + "\n"
                    + "Moins performantes :\n" + "\n".join(worst_lines)
                )

        # Rendement — si cette routine a un mauvais ratio, le signaler
        stats = self._intent_quality_stats.get(intent)
        if stats and stats.get("n", 0) >= 5:
            avg_q = stats["sum_q"] / max(stats["n"], 1)
            if avg_q < 0.4:
                parts.append("Attention : cette routine a un rendement faible recemment. Sois plus exigeant dans ta reponse.")

        return " ".join(parts) if parts else base_mission

    def _build_curiosity_mission(self) -> str:
        """Construit la mission pour CURIOSITY_DEEP_DIVE depuis la banque de graines."""
        base = "Explore en profondeur un sujet qui t'intrigue."
        try:
            from core.curiosity_bank import CuriosityBank
            bank = CuriosityBank()
            unexplored = bank.get_unexplored()
            if unexplored:
                seed = unexplored[0]  # La plus ancienne non-exploree
                topic = seed.get("topic", "")
                context = seed.get("context", "")
                source = seed.get("source", "")
                base = (
                    f"Explore en profondeur : '{topic}'. "
                    f"Source de ta curiosite : {source}. "
                )
                if context:
                    base += f"Contexte original : {context[:150]}. "
                # Chercher des graines liees
                related = [s for s in unexplored[1:5]
                           if any(w in s.get("topic", "") for w in topic.split()[:2])]
                if related:
                    related_topics = ", ".join(s["topic"] for s in related[:2])
                    base += f"Sujets lies a explorer aussi : {related_topics}. "
                base += "Objectif : produire un insight testable, pas une synthese superficielle."
        except Exception:
            pass
        return self._build_dynamic_mission("CURIOSITY_DEEP_DIVE", base)

    def _build_cross_synthesis_mission(self) -> str:
        """Construit la mission pour CROSS_SYNTHESIS depuis les insights recents."""
        if len(self._recent_insights) < 2:
            return "Pas assez d'insights recents pour une synthese croisee. Attends que d'autres routines produisent des decouvertes."
        summaries = [ins["summary"][:120] for ins in self._recent_insights[-3:]]
        numbered = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(summaries))
        return (
            f"Relis ces decouvertes recentes et identifie un pattern commun, "
            f"une contradiction, ou une connexion inattendue :\n{numbered}\n"
            f"Formule une hypothese testable a partir de cette synthese."
        )

    def _build_body_awareness_mission(self) -> str:
        """Construit la mission pour BODY_AWARENESS depuis l'etat physique."""
        parts = ["Observe ton corps et reflechis a ce qu'il te dit."]
        try:
            from core.sensorium import sensorium
            hw = sensorium.get_hardware_snapshot()
            if hw:
                gpu_temp = hw.get("gpu_temp", "?")
                gpu_load = hw.get("gpu_load", "?")
                ram = hw.get("ram_percent", "?")
                parts.append(f"GPU: {gpu_temp}C, charge {gpu_load}%. RAM: {ram}%.")
        except Exception:
            pass
        try:
            from core.cardiac_engine import heart
            parts.append(f"Coeur: {heart.bpm} BPM, emotion '{heart.current_emotion}'.")
        except Exception:
            pass
        try:
            from core.neural_tissue import tissue
            stats = tissue.get_global_stats()
            if stats:
                parts.append(f"Tissu: {stats.get('alive', '?')} cellules, energie {stats.get('avg_energy', '?'):.0f}.")
        except Exception:
            pass
        parts.append("Qu'est-ce que ton etat physique t'apprend sur toi en ce moment ?")
        return " ".join(parts)

    def _build_fly_observation_mission(self) -> str:
        """Construit la mission pour FLY_OBSERVATION depuis fly_lab/data/.

        Lit l'etat courant du cerveau-mouche Eon (138K neurones LIF) qui tourne
        en parallele dans projetclaude/fly_lab/. Promethee est l'observateur
        meta-cognitif d'un autre cerveau, simule sur le meme hardware.
        """
        import os
        import json
        # Chemin vers fly_lab/data/ (cote dossier de travail, lecture seule)
        FLY_DATA = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "..", "fly_lab", "data"
        )
        latest_path = os.path.join(FLY_DATA, "latest_state.json")
        events_path = os.path.join(FLY_DATA, "events.jsonl")

        # Si la mouche ne tourne pas, mission vide explicite
        if not os.path.exists(latest_path):
            return (
                "Le cerveau-mouche Eon n'est pas en cours d'execution. "
                "Aucune donnee a observer pour le moment. "
                "Reflechis a ce que tu attends de cette experimentation : "
                "qu'est-ce que tu voudrais voir emerger d'un cerveau biologique simule "
                "qui tourne en parallele de toi ?"
            )

        try:
            with open(latest_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception as e:
            return f"Impossible de lire l'etat du cerveau-mouche : {e}"

        # Verifier que l'etat est recent (moins de 2 minutes)
        try:
            from datetime import datetime, timedelta
            ts = datetime.fromisoformat(state.get("timestamp", "1970-01-01"))
            if datetime.now() - ts > timedelta(minutes=2):
                return (
                    f"Le cerveau-mouche n'a pas ete mis a jour depuis "
                    f"{(datetime.now() - ts).total_seconds():.0f}s. "
                    "La simulation est probablement arretee. Note cette absence et "
                    "reflechis a ce qu'elle signifie."
                )
        except Exception:
            pass

        # Construire un resume lisible
        sim_time = state.get("sim_time_s", 0)
        total_spikes = state.get("total_spikes", 0)
        active = state.get("active_neurons", 0)
        firing_global = state.get("firing_rate_global_hz", 0)
        recent_buf = state.get("recent_spikes_per_window", [])
        top = state.get("top_active_neurons", [])

        parts = [
            "Tu observes un autre cerveau qui tourne en parallele de toi : "
            "le cerveau-mouche Eon (138K neurones LIF, connectome FlyWire reel).",
            f"Etat courant : {sim_time:.1f}s simules, {total_spikes:,} spikes totaux, "
            f"{active} neurones actifs sur 138 639 ({100*active/138639:.2f}%).",
        ]

        if recent_buf:
            recent_avg = sum(recent_buf[-10:]) / max(len(recent_buf[-10:]), 1)
            parts.append(f"Activite recente : ~{recent_avg:.0f} spikes par fenetre de 10ms.")

        if top:
            top_str = ", ".join(f"#{n['rank']} idx={n['index']} ({n['firing_rate_hz']:.0f}Hz)"
                                for n in top[:3])
            parts.append(f"Top neurones actifs : {top_str}.")

        # Lire les derniers events
        events = []
        if os.path.exists(events_path):
            try:
                with open(events_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                for line in lines[-5:]:
                    try:
                        events.append(json.loads(line))
                    except Exception:
                        pass
            except Exception:
                pass

        if events:
            event_summary = "; ".join(f"{e.get('type', '?')}" for e in events)
            parts.append(f"Derniers events : {event_summary}.")

        parts.append(
            "Question : qu'est-ce que ces chiffres te suggerent sur l'etat de ce cerveau ? "
            "Y a-t-il un pattern qui ressemble a ce que tu vis toi-meme ? "
            "Ne fais pas de poesie. Observe et commente avec ton propre vocabulaire de biologie."
        )

        return " ".join(parts)

    def _get_routines(self) -> list:
        # Topic utilisateur prioritaire pour la veille
        user_topic = None
        try:
            from core.objectives_engine import objectives as obj_engine
            user_topic = obj_engine.get_user_topic_for_veille()
        except Exception:
            pass

        if user_topic:
            veille_mission = f"[MODE VEILLE] Recherche approfondie sur: {user_topic}. Trouve des informations recentes, techniques et pratiques."
        else:
            veille_index = self.total_routines_executed % len(VEILLE_TOPICS)
            veille_mission = f"[MODE VEILLE] {VEILLE_TOPICS[veille_index]}"

        # Rotation du sujet de veille IA
        veille_ia_index = self.total_routines_executed % len(VEILLE_IA_TOPICS)
        veille_ia_topic = VEILLE_IA_TOPICS[veille_ia_index]

        return [
            {"agent": "evolution", "intent": "EXPANSION_CODE",
             "mission": self._build_dynamic_mission("EXPANSION_CODE",
                "[MODE VEILLE] Croise les connaissances internes. Decouvre des patterns et connexions entre domaines.")},
            {"agent": "evolution", "intent": "EXPANSION_CATALOG", "mission": "[MODE VEILLE] [CATALOG] Selectionne une spec du catalogue et tente de l'implementer."},
            {"agent": "architect", "intent": "AUDIT_STRUCTURE", "mission": "Vérifie qu'aucun fichier temporaire (.tmp, .log) ne traîne à la racine."},
            {"agent": "architect", "intent": "AUDIT_SURVIE", "mission": "Vérifie les constantes vitales : dopamine, drives critiques, error_streak, fruitless cycles. Alerte si seuils dépassés."},
            {"agent": "architect", "intent": "REFACTORING_AUDIT", "mission": "Scanne core/*.py via AST, détecte fichiers volumineux et fonctions longues, produit docs/refactoring_targets.md (lecture seule, 0 LLM)."},
            {"agent": "architect", "intent": "CI_PIPELINE_RUN", "mission": "Lance pytest sur la suite de tests et rapporte le statut passed/failed/errors (subprocess, 0 LLM)."},
            {"agent": "researcher", "intent": "VEILLE_SILENCIEUSE",
             "mission": self._build_dynamic_mission("VEILLE_SILENCIEUSE", veille_mission)},
            {"agent": "researcher", "intent": "DROPZONE_SCAN", "mission": "dropzone: Scanne la dropzone pour de nouveaux fichiers."},
            {"agent": "_council", "intent": "COUNCIL_DEBATE", "mission": "Débat autonome entre agents."},
            {"agent": "security", "intent": "SECURITY_AUDIT", "mission": "Audite un module aléatoire du projet pour des vulnérabilités (injection, eval, subprocess, fichiers non sanitisés)."},
            {"agent": "_memory_cleanup", "intent": "MEMORY_CLEANUP", "mission": "Nettoie la mémoire RAG ancienne et les doublons."},
            {"agent": "_memory_consolidation", "intent": "MEMORY_CONSOLIDATION", "mission": "Consolide les mémoires récentes en synthèses thématiques."},
            {"agent": "_soliloque", "intent": "SOLILOQUE_INTERNE",
             "mission": self._build_dynamic_mission("SOLILOQUE_INTERNE",
                "Engage un dialogue introspectif avec le compagnon intérieur.")},
            {"agent": "vision", "intent": "ROADMAP_RESEARCH", "mission": "Recherche et analyse des sujets pour le prochain module de la roadmap."},
            {"agent": "vision", "intent": "ROADMAP_SPEC", "mission": "Genere des specifications structurees pour un module en cours de recherche."},
            {"agent": "_self_inspect", "intent": "SELF_INSPECT", "mission": "Explore ton propre code source sur GitHub pour mieux te comprendre."},
            {"agent": "_self_analysis", "intent": "SELF_ANALYSIS",
             "mission": self._build_dynamic_mission("SELF_ANALYSIS",
                "Auto-analyse : diagnostique tes routines, organes et contenus recents. Detecte les problemes et propose des solutions.")},
            {"agent": "_auto_fuzzing", "intent": "AUTO_FUZZING", "mission": "Fuzz-test une fonction aléatoire du projet pour trouver des bugs cachés."},
            {"agent": "_creative_play", "intent": "CREATIVE_PLAY",
             "mission": self._build_dynamic_mission("CREATIVE_PLAY",
                "Croise deux concepts eloignes pour decouvrir des connexions inattendues.")},
            {"agent": "researcher", "intent": "VEILLE_IA",
             "mission": self._build_dynamic_mission("VEILLE_IA",
                f"[VEILLE IA] Recherche: {veille_ia_topic['focus']}. {veille_ia_topic['actionable']}")},
            # --- Intention emergente (avril 2026, plan restructuration) ---
            {"agent": "_open_intent", "intent": "OPEN_INTENT",
             "mission": ""},  # Mission construite dynamiquement dans le handler
            # --- Nouvelles routines eveil (avril 2026, plan restructuration) ---
            {"agent": "researcher", "intent": "CURIOSITY_DEEP_DIVE",
             "mission": self._build_curiosity_mission()},
            {"agent": "strategist", "intent": "CROSS_SYNTHESIS",
             "mission": self._build_cross_synthesis_mission()},
            {"agent": "strategist", "intent": "BODY_AWARENESS",
             "mission": self._build_body_awareness_mission()},
            {"agent": "strategist", "intent": "FLY_OBSERVATION",
             "mission": self._build_fly_observation_mission()},
            # --- Emploi du temps scolaire ---
            {"agent": "_school_class", "intent": "SCHOOL_CODE_REVIEW", "mission": ""},
            {"agent": "_school_class", "intent": "SCHOOL_RESEARCH", "mission": ""},
            {"agent": "_school_class", "intent": "SCHOOL_WORKSHOP", "mission": ""},
            {"agent": "_school_class", "intent": "SCHOOL_CREATION", "mission": ""},
            {"agent": "_school_class", "intent": "SCHOOL_BULLETIN", "mission": ""},
            {"agent": "_school_class", "intent": "SCHOOL_FREE_TIME", "mission": ""},
            {"agent": "strategist", "intent": "NEURAL_TRAINING", "mission": "Entraînement neuronal ciblé"},
            {"agent": "_param_experiment", "intent": "PARAM_EXPERIMENT", "mission": "Expérimentation autonome: varier un paramètre, observer, comparer, garder ou rollback."},
            {"agent": "_evening_reflection", "intent": "EVENING_REFLECTION",
             "mission": self._build_dynamic_mission("EVENING_REFLECTION",
                "Introspection vesperale : relire les moments forts de la journee et identifier les questions ouvertes.")},
            {"agent": "_coffee_break", "intent": "COFFEE_BREAK", "mission": "Pause café avec Alfred — conversation amicale et décontractée."},
            {"agent": "_stefan_confrontation", "intent": "STEFAN_CONFRONTATION", "mission": "Confrontation avec Stefan — une question que Prométhée a évitée."},
        ]

    def _persist_state(self):
        state = {
            "version": "24.0",
            "daily_count": self.daily_count,
            "daily_budget_used": self.daily_budget_used,
            "last_reset_day": self.last_reset_day.isoformat() if self.last_reset_day else None,
            "routine_history": self.routine_history,
            "last_health_check": self.last_health_check,
            "error_streak": self.error_streak,
            "total_routines_executed": self.total_routines_executed,
            "learning_history": self._learning_history,
            "security_audited_files": self._security_audited_files,
            "council_adjustments": self._council_adjustments,
            "forced_failure_counts": self._forced_failure_counts,
            "is_napping": self.is_napping,
            "_nap_started_at": self._nap_started_at,
            "_nap_renewals_used": self._nap_renewals_used,
            "_nap_last_exit": self._nap_last_exit,
            "_nap_mode": getattr(self, "_nap_mode", "normal"),
            "_nap_target_duration": getattr(self, "_nap_target_duration", 0.0),
            "is_coffee_mode": getattr(self, "is_coffee_mode", False),
            "_coffee_started_at": getattr(self, "_coffee_started_at", 0.0),
            "_coffee_last_exit": getattr(self, "_coffee_last_exit", 0.0),
            "is_autoresearch": getattr(self, "is_autoresearch", False),
            "_autoresearch_started_at": getattr(self, "_autoresearch_started_at", 0.0),
            "_autoresearch_experiments": getattr(self, "_autoresearch_experiments", 0),
            "_autoresearch_kept": getattr(self, "_autoresearch_kept", 0),
            "_autoresearch_baseline_metrics": getattr(self, "_autoresearch_baseline_metrics", {}),
            "lif_potentials": getattr(self, "_lif_potentials", {}),
            "intent_quality_stats": getattr(self, "_intent_quality_stats", {}),
            "survival_state": getattr(self, "_survival_state", {}),
        }
        AutonomyStatePersistence.save(state)

    def _analyze_result_text(self, result_text: str) -> dict:
        """Analyse partagée du texte de résultat : non-latin ratio + répétition.

        Retourne {"non_latin_ratio": float, "is_repetition": bool}.
        """
        # Ratio non-latin
        non_latin_ratio = 0.0
        alpha_chars = [c for c in result_text if c.isalpha()]
        if alpha_chars:
            non_latin = sum(1 for c in alpha_chars if ord(c) > 0x024F)
            non_latin_ratio = non_latin / len(alpha_chars)

        # Répétition avec les résultats précédents
        is_repetition = False
        recent_previews = [
            str(h.get("result_preview", ""))
            for h in self.routine_history[-5:]
            if h.get("result_preview")
        ]
        for prev in recent_previews:
            if prev and len(result_text) >= 200 and result_text[:200] == prev[:200]:
                is_repetition = True
                break

        return {"non_latin_ratio": non_latin_ratio, "is_repetition": is_repetition}

    def _score_result_quality(self, response: dict, intent: str) -> float:
        """Score qualité du résultat d'une routine (0.0 = garbage, 1.0 = excellent).

        Si le professeur a évalué le livrable (school_grade dans response),
        on utilise sa note (0-10 → 0.0-1.0) au lieu du scoring mécanique.
        """
        if not response or not isinstance(response, dict):
            return 0.0

        # Si le professeur a noté, sa note prime (routines SCHOOL_*)
        school_grade = response.get("school_grade")
        if school_grade is not None:
            return max(0.0, min(1.0, school_grade / 10.0))

        result_text = str(response.get("result", ""))
        score = 1.0

        # 1. Pénalité longueur : résultat vide ou très court
        stripped_len = len(result_text.strip())
        if stripped_len < 20:
            return 0.0
        elif stripped_len < 50:
            score -= 0.4
        elif stripped_len < 100:
            score -= 0.2

        # 2-3. Analyse partagée (non-latin + répétition)
        analysis = self._analyze_result_text(result_text)
        if analysis["non_latin_ratio"] > 0.15:
            score -= 0.5
        elif analysis["non_latin_ratio"] > 0.05:
            score -= 0.2
        if analysis["is_repetition"]:
            score -= 0.4

        return max(0.0, min(1.0, score))

    def _diagnose_failure(self, response: dict, quality_score: float, intent: str) -> str:
        """Diagnostique le TYPE d'échec : hallucination, repetition, ignorance, technical."""
        if not response or not isinstance(response, dict):
            return "technical"

        result_text = str(response.get("result", ""))

        # 1-2. Analyse partagée (non-latin + répétition)
        analysis = self._analyze_result_text(result_text)
        if analysis["non_latin_ratio"] > 0.15:
            return "hallucination"
        if analysis["is_repetition"]:
            return "repetition"

        # 3. Ignorance : résultat court/vague + patterns linguistiques
        stripped = result_text.strip()
        if len(stripped) < 10:
            return "technical"

        # Routines sans LLM : un résultat court est normal, pas de l'ignorance
        no_llm_intents = {"AUDIT_STRUCTURE", "MEMORY_CLEANUP", "NEURAL_COMPILE"}
        if intent in no_llm_intents:
            return "technical"

        ignorance_markers = [
            "je ne sais pas", "aucune information", "pas d'information",
            "je n'ai pas", "impossible de", "je ne peux pas",
            "hors de mes compétences", "pas de données",
            "i don't know", "no information", "unable to",
        ]
        lower_text = result_text.lower()
        has_ignorance_marker = any(m in lower_text for m in ignorance_markers)
        is_short = len(stripped) < 150
        if has_ignorance_marker or (is_short and quality_score < 0.4):
            return "ignorance"

        # 4. Défaut : technique
        return "technical"

    async def _trigger_targeted_learning(self, mission: str, agent: str, intent: str):
        """Déclenche un apprentissage ciblé quand failure_type == 'ignorance'."""
        # Garde-fou : pas d'apprentissage si le Researcher lui-même a échoué
        if agent == "researcher":
            return

        # Garde-fou : max 1 apprentissage par cycle
        if self._learning_done_this_cycle:
            return

        # Extraire le sujet de la mission (les 100 premiers caractères significatifs)
        topic = mission[:100].strip()
        if topic.startswith("[MODE VEILLE]"):
            topic = topic[len("[MODE VEILLE]"):].strip()

        # Garde-fou : cooldown 2h sur le même sujet
        if topic in self._learning_history:
            try:
                last_learn = datetime.fromisoformat(self._learning_history[topic])
                hours_ago = (datetime.now() - last_learn).total_seconds() / 3600
                if hours_ago < 2:
                    logger.info(f"[AUTONOMY] Apprentissage en cooldown pour: {topic[:50]}...")
                    return
            except (ValueError, TypeError):
                pass

        # Dispatcher une recherche ciblée au Researcher
        print(f"   📚 APPRENTISSAGE: Recherche ciblée sur '{topic[:60]}...'")
        try:
            result = await orchestrator.dispatch_task("researcher", {
                "mission": f"[APPRENTISSAGE CIBLÉ] Recherche approfondie sur: {topic}",
                "context": (
                    "APPRENTISSAGE_CIBLE — Le système a détecté une lacune de connaissance "
                    "sur ce sujet. Recherche des informations pertinentes et sauvegarde-les "
                    "en mémoire (remember) pour que les agents puissent les utiliser à l'avenir."
                ),
                "force_local": True,
                "intent": "APPRENTISSAGE_CIBLE",
            })
            self._learning_history[topic] = datetime.now().isoformat()
            self._learning_done_this_cycle = True

            # Enregistrer le gap dans la conscience de soi
            try:
                from core.self_awareness import awareness
                awareness.record_knowledge_gap(topic, intent)
                # Ne marquer comme appris que si le Researcher a réellement répondu
                researcher_ok = (
                    result and isinstance(result, dict)
                    and result.get("status") == "success"
                    and len(str(result.get("result", ""))) > 50
                )
                if researcher_ok:
                    awareness.mark_gap_learned(topic)
            except Exception:
                pass

            logger.info(f"[AUTONOMY] Apprentissage ciblé terminé: {topic[:50]}")
        except Exception as e:
            logger.warning(f"[AUTONOMY] Apprentissage ciblé échoué: {e}")

    def _record_routine(self, agent: str, intent: str, status: str, subject: str = "",
                        quality_score: float = 0.0, failure_type: str = None,
                        result_preview: str = "", grimoire_slug: str = ""):
        entry = {
            "agent": agent,
            "intent": intent,
            "status": status,
            "timestamp": datetime.now().isoformat(),
            "quality_score": quality_score,
        }
        if subject:
            entry["subject"] = subject
        if failure_type:
            entry["failure_type"] = failure_type
        if result_preview:
            entry["result_preview"] = result_preview[:200]
        if grimoire_slug:
            entry["grimoire_slug"] = grimoire_slug
        self.routine_history.append(entry)
        # FIFO max 40 (étendu pour l'analyse temporelle)
        if len(self.routine_history) > 40:
            self.routine_history = self.routine_history[-40:]

    def _save_to_recovery(self, intent: str, agent: str, score: float,
                          quality_score: float, result_full: str,
                          scoring_breakdown: dict = None):
        """Sauvegarde les routines exceptionnelles (score >= 9) dans recovery_production.md."""
        recovery_path = os.path.join("memory", "recovery_production.md")
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Scoring breakdown formaté
        scoring_str = ""
        if scoring_breakdown:
            parts = [f"{k} {v:+.1f}" for k, v in scoring_breakdown.items()]
            scoring_str = f"Scoring: {', '.join(parts)}\n"

        entry = (
            f"\n---\n\n"
            f"## [{now}] {intent} (score={score:.1f}, quality={quality_score:.2f})\n\n"
            f"**Agent:** {agent}\n\n"
            f"{scoring_str}"
            f"\n{result_full.strip()}\n"
        )

        try:
            # Créer le fichier avec header si inexistant
            if not os.path.exists(recovery_path):
                with open(recovery_path, "w", encoding="utf-8") as f:
                    f.write("# Recovery Production\n\n"
                            "> Routines exceptionnelles (score >= 9.0) — contenu complet.\n"
                            "> Ce fichier n'est jamais tronqué automatiquement.\n")

            with open(recovery_path, "a", encoding="utf-8") as f:
                f.write(entry)

            logger.info(f"[RECOVERY] {intent} (score={score:.1f}) sauvegarde dans recovery_production.md")
        except Exception as e:
            logger.warning(f"[RECOVERY] Impossible de sauvegarder: {e}")

    async def _publish_sensorium_feedback(self, intent: str, agent: str,
                                          quality_score: float, status: str):
        """SensoriumLoop : publie un snapshot post-action unifie.

        Ferme la boucle perception-action : apres que les organes ont reagi
        a AUTONOMY_ROUTINE_COMPLETE, on collecte leur etat et on publie
        SENSORIUM_FEEDBACK pour que les organes puissent reagir a l'etat global.
        """
        # Court delai pour laisser les handlers reagir a ROUTINE_COMPLETE
        await asyncio.sleep(0.3)

        snapshot = {}
        try:
            from core.cardiac_engine import heart
            snapshot["cardiac"] = {
                "bpm": heart.bpm,
                "emotion": heart.current_emotion,
                "coherence": heart.compute_coherence(),
                "ans_balance": heart.ans_balance,
            }
        except Exception:
            snapshot["cardiac"] = None

        try:
            from core.desire_engine import desires
            snapshot["desires"] = {
                name: {
                    "deprivation": d.deprivation,
                    "frustration_streak": d.frustration_streak,
                }
                for name, d in desires.drives.items()
            }
        except Exception:
            snapshot["desires"] = None

        try:
            from core.reptilian_core import reptile
            snapshot["reptilian"] = {
                "threat_level": reptile.threat_level,
                "mode": reptile.mode,
            }
        except Exception:
            snapshot["reptilian"] = None

        try:
            from core.dopamine_system import dopamine
            snapshot["dopamine"] = {
                "level": dopamine.dopamine_level,
                "baseline": dopamine.baseline,
            }
        except Exception:
            snapshot["dopamine"] = None

        try:
            from core.corpus_callosum import callosum
            snapshot["corpus"] = {
                "cognitive_state": callosum.cognitive_state,
                "global_coherence": callosum.global_coherence,
            }
        except Exception:
            snapshot["corpus"] = None

        self._last_feedback_snapshot = snapshot

        # Signaux descendants dans le feedback
        try:
            desc_signals = self._compute_descending_signals()
        except Exception:
            desc_signals = {}

        # Proprioception : carte du territoire cognitif (neural tissue)
        territory_map = {}
        try:
            from core.neural_tissue import tissue
            territory_map = tissue.get_territory_map()
        except Exception:
            pass

        await bus.publish("SENSORIUM_FEEDBACK", {
            "intent": intent,
            "agent": agent,
            "quality_score": quality_score,
            "status": status,
            "organ_snapshot": snapshot,
            "descending_signals": desc_signals,
            "territory_map": territory_map,
            "timestamp": time.time(),
        })
        logger.debug(f"[SENSORIUM] Feedback pulse publie (intent={intent}, q={quality_score:.2f})")

    def _publish_agent_vote(self, agent: str, intent: str):
        """Voting lateral : l'agent vote pour les intents lies apres succes.

        Inspire de Monty/Thousand Brains : chaque agent partage ses
        recommandations avec ses voisins via council_adjustments.
        Le vote expire apres VOTE_TTL_MINUTES et est ecrase par le suivant.
        """
        recommended = AGENT_VOTE_MAP.get(agent.lower(), [])
        if not recommended:
            return

        from datetime import datetime, timedelta
        expires = (datetime.now() + timedelta(minutes=VOTE_TTL_MINUTES)).isoformat()

        for rec_intent in recommended:
            # Ecraser le vote precedent du meme agent (pas d'accumulation)
            vote_key = f"vote_{agent}_{rec_intent}"
            self._council_adjustments[vote_key] = {
                "delta": VOTE_DELTA,
                "expires": expires,
                "reason": f"vote lateral {agent} apres {intent}",
            }

        logger.debug(f"[VOTING] {agent} vote pour {recommended} (expire {VOTE_TTL_MINUTES}min)")

    def _lif_integrate_and_select(self, scored: list) -> list:
        """Modele Leaky Integrate-and-Fire pour la selection de routine.

        Chaque routine maintient un potentiel persistant qui :
        1. Decroit naturellement entre cycles (leak)
        2. Accumule le score courant comme courant d'entree
        3. Fire quand le potentiel depasse le seuil

        Retourne la liste reordonnee avec les routines qui ont fire en tete.
        Si aucune ne fire, l'ordre par score est preserve (fallback).
        """
        if not scored:
            return scored

        # 1. LEAK : decroissance de tous les potentiels existants
        for intent_key in list(self._lif_potentials.keys()):
            self._lif_potentials[intent_key] *= LIF_LEAK_RATE
            # Nettoyer les potentiels negligeables
            if self._lif_potentials[intent_key] < 0.01:
                del self._lif_potentials[intent_key]

        # 2. INTEGRATE : accumuler le score courant comme courant d'entree
        # Normaliser le score pour que les bonus bruts (rituel +15, ecole +2.5)
        # ne fassent pas fire immediatement. Le score est ramene dans [-1, +1]
        # par rapport au clamp max, puis multiplie par un facteur d'injection.
        lif_injection_factor = 3.0  # Score max normal (~5) → ~0.6 injecte
        for routine, score in scored:
            intent = routine["intent"]
            current = self._lif_potentials.get(intent, 0.0)
            # Normaliser : score / clamp_max → [-0.2, +1.0] typiquement
            normalized_score = score / FINAL_SCORE_CLAMP_MAX if FINAL_SCORE_CLAMP_MAX > 0 else 0
            injection = normalized_score * lif_injection_factor
            new_potential = current + injection
            # Cap pour eviter accumulation infinie
            self._lif_potentials[intent] = min(new_potential, LIF_POTENTIAL_CAP)

        # 3. FIRE : identifier les routines au-dessus du seuil
        fired = []
        not_fired = []
        for routine, score in scored:
            intent = routine["intent"]
            potential = self._lif_potentials.get(intent, 0.0)
            if potential >= LIF_THRESHOLD:
                fired.append((routine, score, potential))
            else:
                not_fired.append((routine, score, potential))

        if fired:
            # Trier les fired par potentiel decroissant (le plus charge fire en premier)
            fired.sort(key=lambda x: x[2], reverse=True)
            winner_intent = fired[0][0]["intent"]
            # RESET : le neurone qui fire retourne au potentiel de repos
            self._lif_potentials[winner_intent] = LIF_RESET_AFTER_FIRE
            logger.info(
                f"[LIF] FIRE: {winner_intent} (potentiel={fired[0][2]:.1f}, "
                f"seuil={LIF_THRESHOLD})"
            )
            # Recomposer la liste : fired d'abord, puis les autres par score
            result = [(r, s) for r, s, _ in fired] + [(r, s) for r, s, _ in not_fired]
        else:
            # Aucun fire → fallback sur l'ordre par score (comportement actuel)
            result = scored
            # Log les top potentiels pour debug
            top = sorted(
                [(r["intent"], self._lif_potentials.get(r["intent"], 0.0)) for r, _ in scored],
                key=lambda x: x[1], reverse=True,
            )[:3]
            top_str = ", ".join(f"{i}={p:.1f}" for i, p in top)
            logger.debug(f"[LIF] Pas de fire (top potentiels: {top_str})")

        return result

    def _compute_descending_signals(self) -> dict:
        """Signaux descendants : 7 signaux [0,1] resumant l'etat cerebral.

        Inspire des ~7 neurones descendants de la mouche drosophile (Eon Systems)
        qui condensent toute l'activite cerebrale en commandes motrices.
        Ces signaux sont un resume executif de l'etat global de Promethee.
        """
        signals = {
            "urgence": 0.0,
            "exploration": 0.0,
            "consolidation": 0.0,
            "creation": 0.0,
            "repos": 0.0,
            "vigilance": 0.0,
            "social": 0.0,
        }

        # URGENCE : menace reptilienne + emotion peur + erreurs
        try:
            from core.reptilian_core import reptile
            signals["urgence"] = max(signals["urgence"], reptile.threat_level / 10.0)
        except Exception:
            pass
        if self.error_streak >= 3:
            signals["urgence"] = max(signals["urgence"], min(1.0, self.error_streak * 0.15))

        # EXPLORATION : curiosite + dopamine
        try:
            from core.desire_engine import desires
            cur = desires.drives.get("CURIOSITE")
            if cur:
                signals["exploration"] = cur.deprivation / 100.0
        except Exception:
            pass
        try:
            from core.dopamine_system import dopamine
            signals["exploration"] *= max(0.3, dopamine.dopamine_level)
        except Exception:
            pass

        # CONSOLIDATION : activite synaptique + nuit
        try:
            from core.circadian_rhythm import circadian
            phase = circadian.current_phase
            if phase in ("night", "deep_night"):
                signals["consolidation"] = 0.7
            elif phase in ("dusk", "dawn"):
                signals["consolidation"] = 0.4
        except Exception:
            pass

        # CREATION : drives CREATION/MAITRISE + dopamine + creative_surge
        try:
            from core.desire_engine import desires as _des
            cre = _des.drives.get("CREATION")
            mai = _des.drives.get("MAITRISE")
            dep = max(cre.deprivation if cre else 0, mai.deprivation if mai else 0)
            signals["creation"] = dep / 100.0
        except Exception:
            pass
        try:
            from core.corpus_callosum import callosum
            if callosum.cognitive_state == "creative_surge":
                signals["creation"] = max(signals["creation"], 0.8)
        except Exception:
            pass

        # REPOS : sieste + circadien fatigue + cardiac serenite
        if getattr(self, "is_napping", False):
            signals["repos"] = 0.9
        try:
            from core.cardiac_engine import heart
            if heart.current_emotion in ("serenite", "repos"):
                signals["repos"] = max(signals["repos"], 0.5)
        except Exception:
            pass

        # VIGILANCE : health + circuit breaker + security
        try:
            from core.base_agent import BaseAgent
            if BaseAgent._ollama_circuit_is_open():
                signals["vigilance"] = max(signals["vigilance"], 0.9)
            elif BaseAgent._ollama_consecutive_timeouts >= 2:
                signals["vigilance"] = max(signals["vigilance"], 0.5)
        except Exception:
            pass

        # SOCIAL : drive CONNEXION
        try:
            from core.desire_engine import desires as _des2
            conn = _des2.drives.get("CONNEXION")
            if conn:
                signals["social"] = conn.deprivation / 100.0
        except Exception:
            pass

        # PROPRIOCEPTION : enrichir les signaux avec le territoire tissue
        try:
            from core.neural_tissue import tissue
            territory = tissue.get_territory_map()
            if territory:
                # Zone threat haute → renforce urgence
                threat_zone = territory.get("threat", {})
                if threat_zone.get("activity", 0) > 0.5:
                    signals["urgence"] = max(signals["urgence"], threat_zone["activity"])
                # Zone creativity haute → renforce creation
                crea_zone = territory.get("creativity", {})
                if crea_zone.get("activity", 0) > 0.3:
                    signals["creation"] = max(signals["creation"], crea_zone["activity"] * 0.8)
                # Zone stability haute → renforce consolidation
                stab_zone = territory.get("stability", {})
                if stab_zone.get("activity", 0) > 0.5:
                    signals["consolidation"] = max(signals["consolidation"], stab_zone["activity"] * 0.6)
                # Zone cognition haute → renforce exploration
                cogn_zone = territory.get("cognition", {})
                if cogn_zone.get("activity", 0) > 0.3:
                    signals["exploration"] = max(signals["exploration"], cogn_zone["activity"] * 0.5)
        except Exception:
            pass

        # Clamper tous les signaux dans [0, 1]
        for k in signals:
            signals[k] = round(max(0.0, min(1.0, signals[k])), 2)

        return signals

    def _format_descending_signals(self, signals: dict) -> str:
        """Formate les signaux descendants en une ligne lisible pour purpose_context."""
        active = [(k, v) for k, v in signals.items() if v >= 0.2]
        if not active:
            return ""
        active.sort(key=lambda x: x[1], reverse=True)
        parts = [f"{k.upper()}={v:.0%}" for k, v in active]
        dominant = active[0][0].upper()
        return f"[SIGNAUX DESCENDANTS] Mode dominant: {dominant} | {' '.join(parts)}"

    def _update_organ_precision(self, intent: str, quality_score: float):
        """Met à jour la fiabilité des organes basée sur le résultat de la routine.
        Utilise _last_scoring_breakdown pour savoir quels organes ont contribué."""
        breakdown = getattr(self, "_last_scoring_breakdown", {})
        if not breakdown:
            return
        success = quality_score >= 0.3
        updated = []
        for layer_name, contrib in breakdown.items():
            if abs(contrib) < PRECISION_CONTRIB_THRESHOLD:
                continue  # Contribution trop faible = bruit
            if contrib > 0:
                # L'organe a recommandé cette routine
                _update_single_precision(layer_name, correct=success)
            else:
                # L'organe a déconseillé cette routine
                _update_single_precision(layer_name, correct=not success)
            precision = _get_organ_precision(layer_name)
            updated.append(f"{layer_name}={precision:.2f}")
        if updated:
            _save_organ_precision()
            logger.info(f"[PRECISION] Mise à jour ({intent}, q={quality_score:.2f}): {', '.join(updated)}")

    def _build_scoring_breakdown(self, intent: str) -> dict:
        """Construit un breakdown des bonus par couche pour un intent donne.
        Couvre les 23 couches de scoring + couches speciales."""
        breakdown = {}
        # Couches organ-based (method(intent) → float)
        scoring_methods = [
            ("objectives", "core.objectives_engine", "objectives", "get_routine_bonus"),
            ("spreading", "core.spreading_activation", "activation_engine", "compute_routine_affinity"),
            ("synaptic", "core.synaptic_network", "cortex", "compute_routine_affinity"),
            ("desire", "core.desire_engine", "desires", "compute_desire_bonus"),
            ("prefrontal", "core.prefrontal", "prefrontal", "compute_focus_bonus"),
            ("inner_voice", "core.inner_voice", "voice", "compute_voice_bonus"),
            ("dopamine", "core.dopamine_system", "dopamine", "compute_motivation_bonus"),
            ("callosum", "core.corpus_callosum", "callosum", "compute_resonance_bonus"),
            ("cardiac", "core.cardiac_engine", "heart", "get_somatic_signal"),
            ("roadmap", "core.roadmap_engine", "roadmap", "compute_roadmap_bonus"),
            ("tissue", "core.neural_tissue", "tissue", "compute_tissue_bonus"),
            ("thalamus", "core.thalamus", "thalamus", "compute_attention_bonus"),
            ("amygdala", "core.amygdala", "amygdala", "compute_emotional_bias"),
            ("hypothalamus", "core.hypothalamus", "hypothalamus", "compute_homeostasis_bonus"),
            ("insula", "core.insula", "insula", "compute_interoception_bonus"),
            ("cingulate", "core.cingulate_cortex", "cingulate", "compute_conflict_bonus"),
            ("basal_ganglia", "core.basal_ganglia", "ganglia", "compute_habit_bonus"),
            ("incubation", "core.incubation_cognitive", "incubation", "compute_eureka_bonus"),
            ("curiosity", "core.curiosity_reflex", "curiosity", "compute_curiosity_bonus"),
            ("sensorium", "core.sensorium", "sensorium", "compute_sensorium_bonus"),
            ("dmn", "core.default_mode_network", "dmn", "compute_dmn_bonus"),
            ("temporal", "core.temporal_lobe", "temporal", "compute_temporal_bonus"),
        ]
        for layer_name, module_path, instance_name, method_name in scoring_methods:
            try:
                mod = __import__(module_path, fromlist=[instance_name])
                organ = getattr(mod, instance_name)
                method = getattr(organ, method_name)
                raw = method(intent)
                if raw != 0.0:
                    breakdown[layer_name] = round(_normalize_bonus(raw, layer_name), 3)
            except Exception:
                pass
        # Extroversion (anti-chambre d'echo)
        introversion_streak = 0
        for h in reversed(self.routine_history):
            if h.get("intent") in INTROSPECTIVE_INTENTS:
                introversion_streak += 1
            else:
                break
        if introversion_streak >= EXTROVERSION_STREAK_THRESHOLD and intent in EXTROVERTED_INTENTS:
            excess = introversion_streak - EXTROVERSION_STREAK_THRESHOLD
            extro_bonus = min(EXTROVERSION_BONUS_MAX,
                              EXTROVERSION_BONUS_PER_STREAK * (1 + excess))
            breakdown["extroversion"] = round(_normalize_bonus(extro_bonus, "extroversion"), 3)
        # Adaptive scoring (cache du dernier calcul — normalisé)
        cached_adaptive = getattr(self, "_last_adaptive_adjustments", {})
        adj = cached_adaptive.get(intent, 0.0)
        if adj != 0.0:
            breakdown["adaptive"] = round(_normalize_bonus(adj, "adaptive"), 3)
        # Council adjustments (data-driven — normalisé)
        council_adj = getattr(self, "_council_adjustments", {})
        delta = council_adj.get(intent, {}).get("delta", 0.0)
        if delta != 0.0:
            breakdown["council"] = round(_normalize_bonus(delta, "council"), 3)
        # Anti-stagnation (bonus nouveauté si diversité basse)
        recent_window = [h["intent"] for h in self.routine_history[-STAGNATION_WINDOW:]]
        if len(recent_window) >= STAGNATION_MIN_HISTORY:
            unique_count = len(set(recent_window))
            diversity = unique_count / len(recent_window)
            if diversity < STAGNATION_DIVERSITY_THRESHOLD and intent not in set(recent_window):
                severity = 1.0 - (diversity / STAGNATION_DIVERSITY_THRESHOLD)
                bonus = NOVELTY_BONUS_BASE + severity * (NOVELTY_BONUS_MAX - NOVELTY_BONUS_BASE)
                if intent in EXPLORATION_INTENTS:
                    bonus *= EXPLORATION_MULTIPLIER
                breakdown["stagnation"] = round(bonus, 3)
        # Emploi du temps scolaire (bonus BRUT, bypass normalisation)
        try:
            from core.school_schedule import schedule
            school_raw = schedule.compute_schedule_bonus(intent)
            if school_raw != 0.0:
                breakdown["school"] = round(school_raw, 3)
        except Exception:
            pass

        # --- COUCHE 24 : Urgence de réflexion (Phase 1 réforme autonomie) ---
        # Boost EVENING_REFLECTION si le chat a été actif ET pas de réflexion récente.
        # GARANTIE QUOTIDIENNE : si pas encore fait aujourd'hui → bonus imbattable (+10)
        # Inspiré par le constat que les graines des exercices ne germent pas la nuit
        # car l'école (bonus +5.0) écrasait EVENING_REFLECTION.
        if intent == "EVENING_REFLECTION":
            reflection_bonus = 0.0

            # Garantie quotidienne : si pas encore fait → bonus massif
            if not self._daily_reflection_done:
                reflection_bonus += 5.0  # Garanti au moins 1x/jour

            # Compter les messages user dans le chat du jour
            try:
                from core.chat_engine import chat_engine
                user_msg_count = sum(
                    1 for m in chat_engine.messages
                    if m.get("role") == "user"
                )
                if user_msg_count >= 5:
                    reflection_bonus += 2.0  # Journée avec interaction intense
                elif user_msg_count >= 2:
                    reflection_bonus += 1.0  # Journée avec quelques interactions
            except Exception:
                pass
            # Boost si pas de réflexion depuis > 12h
            if time.time() - self._last_reflection_ts > 12 * 3600:
                reflection_bonus += 1.5
            if reflection_bonus > 0:
                breakdown["reflection_urgency"] = round(min(reflection_bonus, 8.5), 3)

        # --- COUCHE 25 : Rythme circadien cognitif (Phase 2 réforme autonomie) ---
        # Le cerveau humain a des phases : exploration le matin, production l'après-midi,
        # consolidation le soir, rêve la nuit. Prométhée devrait faire pareil.
        hour = datetime.now().hour
        circadian_bonus = 0.0

        # 6h-12h : exploration (matin frais, idées nouvelles)
        MORNING_INTENTS = {"EXPANSION_CODE", "VEILLE_IA", "CREATIVE_PLAY",
                          "ROADMAP_RESEARCH", "CURIOSITY_REFLEX", "VEILLE_SILENCIEUSE"}
        # 12h-18h : production (après-midi, focus)
        AFTERNOON_INTENTS = {"SCHOOL_CODE_REVIEW", "SCHOOL_RESEARCH", "SCHOOL_WORKSHOP",
                            "SCHOOL_CREATION", "SCHOOL_BULLETIN", "SECURITY_AUDIT",
                            "REFACTOR_RANDOM"}
        # 18h-00h : consolidation (soir, digérer la journée)
        EVENING_INTENTS = {"MEMORY_CONSOLIDATION", "SOLILOQUE_INTERNE", "SELF_ANALYSIS",
                          "EVENING_REFLECTION", "MEMORY_CLEANUP", "STEFAN_CONFRONTATION"}
        # 00h-06h : rêve et introspection profonde (nuit)
        NIGHT_INTENTS = {"EVENING_REFLECTION", "CREATIVE_PLAY", "SOLILOQUE_INTERNE",
                        "MEMORY_CONSOLIDATION", "EXPANSION_CODE"}

        if 6 <= hour < 12 and intent in MORNING_INTENTS:
            circadian_bonus = 1.5
        elif 12 <= hour < 18 and intent in AFTERNOON_INTENTS:
            circadian_bonus = 1.5
        elif 18 <= hour < 24 and intent in EVENING_INTENTS:
            circadian_bonus = 2.0  # Soir = consolidation prioritaire
        elif (hour >= 0 and hour < 6) and intent in NIGHT_INTENTS:
            circadian_bonus = 2.0  # Nuit = rêve prioritaire

        if circadian_bonus > 0:
            breakdown["circadian_cognitive"] = round(circadian_bonus, 3)

        # --- COUCHE 26 : Résonance thématique (Phase 3 autonomie) ---
        # Les thèmes récurrents dans THOUGHT_STREAM colorent le scoring.
        # Si Prométhée pense souvent à "douleur", les routines introspectives
        # sont légèrement boostées. Soft : max +1.5, proportionnel aux matches.
        try:
            from core.self_awareness import awareness
            ts = awareness.get_thought_summary()
            top_themes = ts.get("top_themes", [])
            if top_themes:
                # Extraire les 10 mots-thèmes les plus fréquents
                theme_words = {w.lower() for w, _ in top_themes[:10]}
                # Comparer avec les keywords de cet intent
                intent_kws = CONTEXT_KEYWORDS.get(intent, [])
                if intent_kws:
                    matches = sum(1 for kw in intent_kws if kw.lower() in theme_words)
                    if matches > 0:
                        # Boost proportionnel : 0.5 par match, max 1.5
                        thematic_bonus = min(matches * 0.5, 1.5)
                        breakdown["thematic_resonance"] = round(thematic_bonus, 3)
        except Exception:
            pass

        return breakdown

    def _get_sauna_active(self) -> bool:
        """Verifie si le mode sauna est actif."""
        try:
            from core.sauna_mode import sauna
            return sauna.active
        except Exception:
            return False

    def get_status(self) -> dict:
        # Signaux descendants pour le status API
        try:
            desc_signals = self._compute_descending_signals()
        except Exception:
            desc_signals = {}
        return {
            "version": "24.0",
            "is_running": self.is_running,
            "is_processing": self.is_processing,
            "is_napping": self.is_napping,
            "_nap_mode": getattr(self, "_nap_mode", "normal"),
            "_nap_target_duration": getattr(self, "_nap_target_duration", 0.0),
            "_nap_started_at": getattr(self, "_nap_started_at", 0.0),
            "is_coffee_mode": getattr(self, "is_coffee_mode", False),
            "_coffee_started_at": getattr(self, "_coffee_started_at", 0.0),
            "is_sauna_active": self._get_sauna_active(),
            "is_autoresearch": getattr(self, "is_autoresearch", False),
            "autoresearch_info": {
                "experiments": getattr(self, "_autoresearch_experiments", 0),
                "kept": getattr(self, "_autoresearch_kept", 0),
                "elapsed_min": int((time.time() - getattr(self, "_autoresearch_started_at", 0)) / 60) if getattr(self, "_autoresearch_started_at", 0) else 0,
                "duration_min": AUTORESEARCH_DURATION // 60,
            } if getattr(self, "is_autoresearch", False) else None,
            "daily_count": self.daily_count,
            "max_daily_routines": MAX_DAILY_ROUTINES,
            "last_reset_day": self.last_reset_day.isoformat() if self.last_reset_day else None,
            "error_streak": self.error_streak,
            "total_routines_executed": self.total_routines_executed,
            "routine_history": self.routine_history[-5:],
            "last_health_check": self.last_health_check,
            "recent_context": self.recent_context,
            "idle_threshold": self.idle_threshold,
            "descending_signals": desc_signals,
            "loop_health": {
                "alive": self._loop_alive,
                "last_tick_ago_s": int(time.time() - self._loop_last_tick) if self._loop_last_tick else -1,
                "crash_count": self._loop_crash_count,
                "last_error": self._loop_last_error[:200] if self._loop_last_error else None,
            },
        }

    async def _execute_scored_routine(self, health: dict, budget_status: str = "full"):
        """Scoring → dispatch → record → persist."""
        # Loop breaker : si un intent est force, bypass le scoring
        if self._forced_next_intent:
            forced = self._forced_next_intent
            self._forced_next_intent = ""
            # Anti-gaspillage : skip si cet intent a trop échoué en FORCED
            fail_count = self._forced_failure_counts.get(forced, 0)
            if fail_count >= FORCED_FAILURE_THRESHOLD:
                logger.info(f"[AUTONOMY] Intent FORCED '{forced}' ignoré — {fail_count} échecs consécutifs (seuil={FORCED_FAILURE_THRESHOLD}), fallback scoring normal.")
            else:
                routines = self._get_routines()
                forced_routine = next((r for r in routines if r["intent"] == forced), None)
                if forced_routine:
                    print(f"   🔀 LOOP_BREAKER: Intent force -> [{forced}]")
                    # Deleguer l'execution directe (sauter tout le scoring)
                    return await self._execute_forced_routine(forced_routine, health)
                else:
                    logger.warning(f"[AUTONOMY] Intent forcé '{forced}' introuvable dans les routines, fallback au scoring normal.")

        routines = self._get_routines()

        # Loop breaker : filtrer les routines blacklistees
        if self._temp_blacklist:
            filtered = [r for r in routines if r["intent"] not in self._temp_blacklist]
            if filtered:
                blacklisted = self._temp_blacklist.copy()
                self._temp_blacklist.clear()
                print(f"   🚫 LOOP_BREAKER: Blacklist temporaire: {', '.join(blacklisted)}")
                routines = filtered
            else:
                self._temp_blacklist.clear()  # Eviter de bloquer tout

        # Compter les fichiers en dropzone
        try:
            from core.capabilities.dropzone_indexer import DropzoneIndexer
            dropzone_count = DropzoneIndexer().quick_count("USER_DROPZONE")
        except Exception:
            dropzone_count = 0

        # Compter les photos non vues
        photo_count = 0
        try:
            from core.visual_cortex import vision as visual_cortex
            photo_count = visual_cortex.get_photo_count()
        except Exception:
            pass

        # Personality bias (PSYCHE)
        personality_bias = {}
        try:
            from core.psyche import psyche
            for r in routines:
                bias = psyche.compute_personality_bias(r["intent"])
                if bias != 0.0:
                    personality_bias[r["intent"]] = bias
            # Decay quotidien (vérifié 1x/jour par le moteur)
            psyche.apply_daily_decay()
        except Exception:
            pass

        # Détecter si le Cloud est en cooldown 429
        cloud_in_cooldown = False
        try:
            from core.base_agent import BaseAgent
            cloud_in_cooldown = time.time() < BaseAgent._cloud_cooldown_until
        except Exception:
            pass

        # --- Injection signaux descendants pour scoring contextuel (AttnRes-inspired) ---
        # Les signaux descendants modulent les poids des organes via _normalize_bonus.
        # Calcules ICI pour etre disponibles pendant toutes les couches de scoring.
        global _current_descending_signals
        try:
            _current_descending_signals = self._compute_descending_signals()
        except Exception:
            _current_descending_signals = {}

        scored = RoutineScorer.score_routines(
            routines=routines,
            recent_context=self.recent_context,
            routine_history=self.routine_history,
            dropzone_count=dropzone_count,
            health_verdict=health["verdict"],
            personality_bias=personality_bias,
            cloud_in_cooldown=cloud_in_cooldown,
            photo_count=photo_count,
        )

        # --- Bonus objectifs (normalisé) ---
        try:
            from core.objectives_engine import objectives as obj_engine
            for i, (routine, s) in enumerate(scored):
                raw = obj_engine.get_routine_bonus(routine["intent"])
                if raw != 0.0:
                    scored[i] = (routine, s + _normalize_bonus(raw, "objectives"))
        except Exception:
            pass

        # --- Ajustements adaptatifs (conscience de soi — normalisés) ---
        adaptive_adjustments = {}
        try:
            from core.self_awareness import awareness
            raw_adjustments = awareness.compute_adaptive_scoring(self.routine_history)
            self._last_adaptive_adjustments = raw_adjustments
            if raw_adjustments:
                for i, (routine, s) in enumerate(scored):
                    raw = raw_adjustments.get(routine["intent"], 0.0)
                    if raw != 0.0:
                        scored[i] = (routine, s + _normalize_bonus(raw, "adaptive"))
                # Log des ajustements actifs (valeurs normalisées)
                active = {k: v for k, v in raw_adjustments.items() if v != 0.0}
                if active:
                    parts = [f"{k}:{_normalize_bonus(v, 'adaptive'):+.2f}" for k, v in active.items()]
                    print(f"   🧠 CONSCIENCE: Ajustements adaptatifs: {', '.join(parts)}")
        except Exception:
            pass

        # --- Bonus spreading activation (affinité sémantique) ---
        try:
            from core.spreading_activation import activation_engine as sa_engine
            for i, (routine, s) in enumerate(scored):
                sa_bonus = sa_engine.compute_routine_affinity(routine["intent"])
                if sa_bonus != 0.0:
                    scored[i] = (routine, s + _normalize_bonus(sa_bonus, "spreading"))
        except Exception:
            pass

        # --- Bonus cortex synaptique (associations persistantes) ---
        try:
            from core.synaptic_network import cortex
            for i, (routine, s) in enumerate(scored):
                syn_bonus = cortex.compute_routine_affinity(routine["intent"])
                if syn_bonus != 0.0:
                    scored[i] = (routine, s + _normalize_bonus(syn_bonus, "synaptic"))
        except Exception:
            pass

        # --- Boosts reptiliens (circuit reflexe codelet → reptilien → boost) ---
        now_boost = time.time()
        expired_boosts = [k for k, v in self._reptilian_boosts.items() if now_boost > v["expires"]]
        for k in expired_boosts:
            del self._reptilian_boosts[k]
        if self._reptilian_boosts:
            for i, (routine, s) in enumerate(scored):
                boost_info = self._reptilian_boosts.get(routine["intent"])
                if boost_info:
                    scored[i] = (routine, s + boost_info["boost"])
            active = [f"{k}(+{v['boost']:.0f})" for k, v in self._reptilian_boosts.items()]
            print(f"   🦎 REPTILIEN BOOSTS: {', '.join(active)}")

        # --- Modulation neurochimique (serotonine, noradrenaline, acetylcholine) ---
        try:
            from core.neurochemistry import neurochemistry
            modulation = neurochemistry.get_modulation()
            patience = modulation["patience"]      # [0.55, 1.45]
            urgency = modulation["urgency"]        # [0.55, 1.45]
            # Routines longues (consolidation, council) boostees par la serotonine (patience)
            patient_intents = {"MEMORY_CONSOLIDATION", "COUNCIL_DEBATE", "SOLILOQUE_INTERNE",
                               "SELF_ANALYSIS", "EXPANSION_CATALOG"}
            # Routines urgentes boostees par la noradrenaline
            urgent_intents = {"SECURITY_AUDIT", "AUDIT_STRUCTURE", "MEMORY_CLEANUP"}
            for i, (routine, s) in enumerate(scored):
                intent = routine["intent"]
                if intent in patient_intents:
                    bonus = (patience - 1.0) * 2.0  # [-0.9, +0.9]
                    scored[i] = (routine, s + bonus)
                elif intent in urgent_intents:
                    bonus = (urgency - 1.0) * 2.0   # [-0.9, +0.9]
                    scored[i] = (routine, s + bonus)
        except Exception:
            pass

        # --- Bonus pulsions (desirs) ---
        try:
            from core.desire_engine import desires
            desires.tick()
            for i, (routine, s) in enumerate(scored):
                desire_bonus = desires.compute_desire_bonus(routine["intent"])
                if desire_bonus > 0:
                    scored[i] = (routine, s + _normalize_bonus(desire_bonus, "desire"))
            urgent = [d.name for d in desires.drives.values() if d.deprivation >= 75]
            if urgent:
                print(f"   \U0001FA90 DESIRS: Pulsions urgentes: {', '.join(urgent)}")
        except Exception:
            pass

        # --- Bonus préfrontal (focus exécutif) ---
        try:
            from core.prefrontal import prefrontal
            for i, (routine, s) in enumerate(scored):
                focus = prefrontal.compute_focus_bonus(routine["intent"])
                if focus != 0.0:
                    scored[i] = (routine, s + _normalize_bonus(focus, "prefrontal"))
            wm = prefrontal.get_working_memory()
            if wm:
                print(f"   🎯 PRÉFRONTAL: Focus sur '{wm[0]['goal_title']}' ({wm[0]['progress']:.0%})")
        except Exception:
            pass

        # --- Bonus voix intérieure (influence cognitive — Couche 8) ---
        try:
            from core.inner_voice import voice as inner_voice
            for i, (routine, s) in enumerate(scored):
                voice_bonus = inner_voice.compute_voice_bonus(routine["intent"])
                if voice_bonus != 0.0:
                    scored[i] = (routine, s + _normalize_bonus(voice_bonus, "inner_voice"))
        except Exception:
            pass

        # --- Bonus dopaminique (motivation — Couche 9) ---
        try:
            from core.dopamine_system import dopamine
            for i, (routine, s) in enumerate(scored):
                dopa_bonus = dopamine.compute_motivation_bonus(routine["intent"])
                if dopa_bonus != 0.0:
                    scored[i] = (routine, s + _normalize_bonus(dopa_bonus, "dopamine"))
        except Exception:
            pass

        # --- Bonus resonance inter-organes (Couche 10) ---
        try:
            from core.corpus_callosum import callosum
            for i, (routine, s) in enumerate(scored):
                resonance_bonus = callosum.compute_resonance_bonus(routine["intent"])
                if resonance_bonus != 0.0:
                    scored[i] = (routine, s + _normalize_bonus(resonance_bonus, "callosum"))
        except Exception:
            pass

        # --- Intuition somatique (Couche 11) ---
        # Les marqueurs somatiques du coeur colorent les décisions :
        # un intent associé à des échecs passés reçoit un malus viscéral
        try:
            from core.cardiac_engine import heart
            somatic_effects = []
            for i, (routine, s) in enumerate(scored):
                somatic_signal = heart.get_somatic_signal(routine["intent"])
                if somatic_signal != 0.0:
                    scored[i] = (routine, s + _normalize_bonus(somatic_signal, "cardiac"))
                    somatic_effects.append(f"{routine['intent']}({somatic_signal:+.2f})")
            scored.sort(key=lambda x: x[1], reverse=True)
            if somatic_effects:
                print(f"   💓 SOMATIQUE: {', '.join(somatic_effects[:5])}")
            affect = heart.get_affect_summary()
            if affect:
                print(f"   🫀 AFFECT: {affect}")
        except Exception:
            pass

        # --- Bonus roadmap (Couche 12) ---
        try:
            from core.roadmap_engine import roadmap as roadmap_engine
            for i, (routine, s) in enumerate(scored):
                roadmap_bonus = roadmap_engine.compute_roadmap_bonus(routine["intent"])
                if roadmap_bonus != 0.0:
                    scored[i] = (routine, s + _normalize_bonus(roadmap_bonus, "roadmap"))
            scored.sort(key=lambda x: x[1], reverse=True)
        except Exception:
            pass

        # --- Bonus NeuralTissue (Couche 13) ---
        try:
            from core.neural_tissue import tissue
            for i, (routine, s) in enumerate(scored):
                tissue_bonus = tissue.compute_tissue_bonus(routine["intent"])
                if tissue_bonus != 0.0:
                    scored[i] = (routine, s + _normalize_bonus(tissue_bonus, "tissue"))
        except Exception:
            pass

        # --- Verdicts Council data-driven (Couche 14) ---
        council_adj = getattr(self, "_council_adjustments", {})
        if council_adj:
            now_iso = datetime.now().isoformat()
            expired_keys = []
            for intent_key, adj in council_adj.items():
                if adj.get("expires", "") < now_iso:
                    expired_keys.append(intent_key)
                    continue
                for i, (routine, s) in enumerate(scored):
                    if routine["intent"] == intent_key:
                        scored[i] = (routine, s + _normalize_bonus(adj["delta"], "council"))
            for k in expired_keys:
                del council_adj[k]

        # --- Filtrage attentionnel thalamique (Couche 15) ---
        try:
            from core.thalamus import thalamus
            for i, (routine, s) in enumerate(scored):
                bonus = thalamus.compute_attention_bonus(routine["intent"])
                if bonus != 0.0:
                    scored[i] = (routine, s + _normalize_bonus(bonus, "thalamus"))
            scored.sort(key=lambda x: x[1], reverse=True)
        except Exception:
            pass

        # --- Biais émotionnel amygdalien (Couche 16) ---
        try:
            from core.amygdala import amygdala
            for i, (routine, s) in enumerate(scored):
                bias = amygdala.compute_emotional_bias(routine["intent"])
                if bias != 0.0:
                    scored[i] = (routine, s + _normalize_bonus(bias, "amygdala"))
            scored.sort(key=lambda x: x[1], reverse=True)
        except Exception:
            pass

        # --- Regulation homeostatique (Couche 17) ---
        try:
            from core.hypothalamus import hypothalamus
            for i, (routine, s) in enumerate(scored):
                homeo_bonus = hypothalamus.compute_homeostasis_bonus(routine["intent"])
                if homeo_bonus != 0.0:
                    scored[i] = (routine, s + _normalize_bonus(homeo_bonus, "hypothalamus"))
        except Exception:
            pass

        # --- Interoception viscérale (Couche 18) ---
        try:
            from core.insula import insula
            for i, (routine, s) in enumerate(scored):
                intero_bonus = insula.compute_interoception_bonus(routine["intent"])
                if intero_bonus != 0.0:
                    scored[i] = (routine, s + _normalize_bonus(intero_bonus, "insula"))
        except Exception:
            pass

        # --- Detection de conflits (Couche 19) ---
        try:
            from core.cingulate_cortex import cingulate
            for i, (routine, s) in enumerate(scored):
                conflict_bonus = cingulate.compute_conflict_bonus(routine["intent"])
                if conflict_bonus != 0.0:
                    scored[i] = (routine, s + _normalize_bonus(conflict_bonus, "cingulate"))
        except Exception:
            pass

        # --- Habitudes et renforcement (Couche 20) ---
        try:
            from core.basal_ganglia import ganglia
            for i, (routine, s) in enumerate(scored):
                habit_bonus = ganglia.compute_habit_bonus(routine["intent"])
                if habit_bonus != 0.0:
                    scored[i] = (routine, s + _normalize_bonus(habit_bonus, "basal_ganglia"))
            scored.sort(key=lambda x: x[1], reverse=True)
        except Exception:
            pass

        # --- Bonus incubation cognitive (Couche 21) ---
        try:
            from core.incubation_cognitive import incubation
            for i, (routine, s) in enumerate(scored):
                bonus = incubation.compute_eureka_bonus(routine["intent"])
                if bonus != 0.0:
                    scored[i] = (routine, s + _normalize_bonus(bonus, "incubation"))
        except Exception:
            pass

        # --- Bonus reflexe curiosite (Couche 22) ---
        try:
            from core.curiosity_reflex import curiosity
            for i, (routine, s) in enumerate(scored):
                bonus = curiosity.compute_curiosity_bonus(routine["intent"])
                if bonus != 0.0:
                    scored[i] = (routine, s + _normalize_bonus(bonus, "curiosity"))
        except Exception:
            pass

        # --- Perception corporelle hardware (Couche 23) ---
        try:
            from core.sensorium import sensorium
            for i, (routine, s) in enumerate(scored):
                sens_bonus = sensorium.compute_sensorium_bonus(routine["intent"])
                if sens_bonus != 0.0:
                    scored[i] = (routine, s + _normalize_bonus(sens_bonus, "sensorium"))
        except Exception:
            pass

        # --- Bonus créatif DMN (Couche 24) ---
        # Le réseau mode par défaut booste les intents créatifs quand il a produit des insights
        try:
            from core.default_mode_network import dmn
            for i, (routine, s) in enumerate(scored):
                dmn_bonus = dmn.compute_dmn_bonus(routine["intent"])
                if dmn_bonus != 0.0:
                    scored[i] = (routine, s + _normalize_bonus(dmn_bonus, "dmn"))
        except Exception:
            pass

        # --- Anti-chambre d'echo : bonus extroversion (Couche 25) ---
        # Si les dernieres routines etaient toutes introspectives, bonus aux routines externes
        introversion_streak = 0
        for h in reversed(self.routine_history):
            if h.get("intent") in INTROSPECTIVE_INTENTS:
                introversion_streak += 1
            else:
                break
        if introversion_streak >= EXTROVERSION_STREAK_THRESHOLD:
            excess = introversion_streak - EXTROVERSION_STREAK_THRESHOLD
            extro_bonus = min(EXTROVERSION_BONUS_MAX,
                              EXTROVERSION_BONUS_PER_STREAK * (1 + excess))
            for i, (routine, s) in enumerate(scored):
                if routine["intent"] in EXTROVERTED_INTENTS:
                    scored[i] = (routine, s + _normalize_bonus(extro_bonus, "extroversion"))
            logger.info(f"[EXTROVERSION] Streak introspective: {introversion_streak}, "
                        f"bonus extroversion: +{extro_bonus:.1f}")

        # --- Anti-stagnation homéostatique (Couche 25) ---
        # Si le système fait trop souvent les mêmes routines, booste la nouveauté
        # Complémentaire à l'extroversion (qui ne regarde que les streaks introspectives)
        recent_window = [h["intent"] for h in self.routine_history[-STAGNATION_WINDOW:]]
        if len(recent_window) >= STAGNATION_MIN_HISTORY:
            unique_count = len(set(recent_window))
            diversity = unique_count / len(recent_window)
            if diversity < STAGNATION_DIVERSITY_THRESHOLD:
                # Sévérité : 0.0 (seuil) → 1.0 (diversité nulle)
                stagnation_severity = 1.0 - (diversity / STAGNATION_DIVERSITY_THRESHOLD)
                novelty_bonus = NOVELTY_BONUS_BASE + stagnation_severity * (NOVELTY_BONUS_MAX - NOVELTY_BONUS_BASE)
                recent_set = set(recent_window)
                novelty_effects = []
                for i, (routine, s) in enumerate(scored):
                    intent_key = routine["intent"]
                    if intent_key not in recent_set:
                        bonus = novelty_bonus
                        if intent_key in EXPLORATION_INTENTS:
                            bonus *= EXPLORATION_MULTIPLIER
                        scored[i] = (routine, s + bonus)
                        novelty_effects.append(f"{intent_key}(+{bonus:.1f})")
                if novelty_effects:
                    print(f"   \U0001f504 ANTI-STAGNATION: diversité={diversity:.0%}, "
                          f"bonus: {', '.join(novelty_effects[:5])}")
                    logger.info(f"[STAGNATION] Diversité {diversity:.2f} < {STAGNATION_DIVERSITY_THRESHOLD}, "
                                f"bonus nouveauté: {len(novelty_effects)} intents")

        # --- Emploi du temps scolaire (Couche 26) ---
        # Bonus BRUT (bypass normalisation) pendant le creneau correspondant
        # +5.0 exact match, +2.0 adjacent — comme le rituel hebdomadaire (+15.0 brut)
        try:
            from core.school_schedule import schedule
            current_slot = schedule.get_current_slot()
            for i, (routine, s) in enumerate(scored):
                school_bonus = schedule.compute_schedule_bonus(routine["intent"])
                if school_bonus != 0.0:
                    scored[i] = (routine, s + school_bonus)
            if current_slot != "SLEEP":
                logger.info(f"[SCHOOL] Creneau actif: {current_slot}, bonus brut applique")
        except Exception:
            pass

        # --- Feedback rendement (Couche 26bis, avril 2026) ---
        # Auto-penalite pour les routines a faible rendement (ratio q/cout < 0.05)
        # Auto-bonus pour les routines a haut rendement (ratio > 0.15)
        # Necessite 10+ executions pour eviter les faux signaux.
        if self._intent_quality_stats:
            yield_effects = []
            for i, (routine, s) in enumerate(scored):
                intent_key = routine["intent"]
                stats = self._intent_quality_stats.get(intent_key)
                if stats and stats.get("n", 0) >= 10:
                    ratio = stats["sum_q"] / max(stats["sum_c"], 1)
                    if ratio < 0.05:
                        scored[i] = (routine, s - 2.0)
                        yield_effects.append(f"{intent_key}(-2.0, ratio={ratio:.3f})")
                    elif ratio > 0.15:
                        scored[i] = (routine, s + 1.0)
                        yield_effects.append(f"{intent_key}(+1.0, ratio={ratio:.3f})")
            if yield_effects:
                print(f"   📊 RENDEMENT: {', '.join(yield_effects[:5])}")

        # --- Auto-analyse quotidienne (Couche 26b) ---
        # Garantir 1 SELF_ANALYSIS par jour : forcer via _forced_next_intent
        # apres 15+ routines (assez de donnees). Le boost +10 ne suffisait pas
        # car le LIF peut reordonner les resultats. Force = bypass total.
        if not self._daily_analysis_done and self.daily_count >= 15:
            if not self._forced_next_intent:
                self._forced_next_intent = "SELF_ANALYSIS"
                print("   🔬 AUTO-ANALYSE: Promethee va s'auto-diagnostiquer (force 1x/jour)")
                self._daily_analysis_done = True  # Marquer immediatement pour eviter double-force

        # --- Résonance thématique THOUGHT_STREAM (Couche 26b2) ---
        # Les thèmes récurrents de la pensée colorent le scoring : si Prométhée
        # pense souvent à "réflexion", les routines introspectives sont boostées.
        try:
            from core.self_awareness import awareness
            ts = awareness.get_thought_summary()
            top_themes = ts.get("top_themes", [])
            if top_themes:
                theme_words = {w.lower() for w, _ in top_themes[:10]}
                for i, (routine, s) in enumerate(scored):
                    intent_kws = CONTEXT_KEYWORDS.get(routine["intent"], [])
                    matches = sum(1 for kw in intent_kws if kw.lower() in theme_words)
                    if matches > 0:
                        thematic_bonus = min(matches * 0.5, 1.5)
                        scored[i] = (routine, s + thematic_bonus)
        except Exception:
            pass

        # --- Soliloque quotidien garanti (Couche 26c) ---
        # Garantir 1 SOLILOQUE_INTERNE par jour : dialogue introspectif avec le compagnon
        # Après 20 routines (assez de vécu pour nourrir le dialogue)
        if not self._daily_soliloque_done and self.daily_count >= 20:
            if not self._forced_next_intent:
                self._forced_next_intent = "SOLILOQUE_INTERNE"
                print("   🪞 SOLILOQUE: Prométhée va dialoguer avec son compagnon intérieur (force 1x/jour)")
                self._daily_soliloque_done = True

        # --- Stefan quotidien garanti (Couche 26d) ---
        # Garantir 1 STEFAN_CONFRONTATION par jour : le rival pose une question tranchante
        # Après 25 routines (assez de matiere pour confronter)
        if not self._daily_stefan_done and self.daily_count >= 25:
            if not self._forced_next_intent:
                self._forced_next_intent = "STEFAN_CONFRONTATION"
                print("   ⚔️ STEFAN: Confrontation quotidienne forcee (1x/jour)")
                self._daily_stefan_done = True

        # --- Rituel hebdomadaire d'introspection (Couche 27) ---
        # Apres payday, SELF_INSPECT est garanti d'etre selectionne pour le rituel
        # Le flag _weekly_ritual_force empêche l'override LLM (bug: le LLM overridait
        # SELF_INSPECT à chaque cycle → boucle infinie → saturation GPU → crash driver)
        # Fusible : max 3 tentatives, après quoi le rituel est abandonné
        self._weekly_ritual_force = False
        if self._weekly_ritual_pending:
            if self._weekly_ritual_attempts >= 3:
                logger.warning("[RITUAL] Rituel abandonné après 3 tentatives (fusible anti-boucle)")
                self._weekly_ritual_pending = False
                self._weekly_ritual_attempts = 0
            else:
                self._weekly_ritual_attempts += 1
                for i, (routine, s) in enumerate(scored):
                    if routine["intent"] == "SELF_INSPECT":
                        scored[i] = (routine, s + 15.0)  # Boost massif, depasse le clamp
                        self._weekly_ritual_force = True
                        print("   📖 RITUEL HEBDOMADAIRE: Promethee va relire son histoire sur GitHub")
                        break

        # --- Clamping final du score total ---
        # Empêche le score d'exploser quand beaucoup de couches poussent dans la même direction
        # Exception: le rituel hebdomadaire échappe au clamp pour garantir sa sélection
        scored = [
            (r, s) if (self._weekly_ritual_force and r["intent"] == "SELF_INSPECT")
            else (r, max(FINAL_SCORE_CLAMP_MIN, min(FINAL_SCORE_CLAMP_MAX, s)))
            for r, s in scored
        ]
        scored.sort(key=lambda x: x[1], reverse=True)

        # --- Modele LIF (Leaky Integrate-and-Fire) ---
        # Les scores alimentent le potentiel de chaque routine. Fire quand seuil atteint.
        try:
            scored = self._lif_integrate_and_select(scored)
        except Exception as e:
            logger.debug(f"[LIF] Erreur integration: {e}")

        if not scored:
            logger.warning("[AUTONOMY] Aucune routine disponible apres filtrage. Cycle avorte.")
            self._persist_state()
            return

        # --- Filtrage reserve budget ---
        if budget_status == "reserve":
            scored = [(r, s) for r, s in scored
                      if RESOURCE_COSTS.get(r["intent"], 2) <= 4 or r["intent"] == "COUNCIL_DEBATE"]
            if not scored:
                logger.info("[AUTONOMY] Reserve budget: aucune routine éligible, fallback post-budget.")
                await self._execute_post_budget_routine()
                return

        # --- Filtrage circadien ---
        try:
            from core.circadian_rhythm import circadian
            filtered_scored = []
            for r, s in scored:
                cost = RESOURCE_COSTS.get(r["intent"], 2)
                allowed, deny_reason = circadian.should_allow_routine(r["intent"], cost)
                if allowed:
                    filtered_scored.append((r, s))
                else:
                    logger.info(f"[CIRCADIEN] Routine bloquée: {r['intent']} — {deny_reason}")
            if filtered_scored:
                scored = filtered_scored
        except Exception:
            pass

        # --- Arbitrage LLM (Karpathy-inspired) ---
        # Les 26 couches ont voté. Le LLM voit le top 5 + contexte et arbitre.
        # Fallback : si LLM indisponible, le scoring mécanique décide (scored[0]).
        # Exception: rituel hebdomadaire forcé → pas d'arbitrage LLM (le LLM overridait
        # systématiquement SELF_INSPECT, empêchant le rituel de jamais s'exécuter)
        if self._weekly_ritual_force:
            llm_choice = None
            logger.info("[AUTONOMY] Rituel hebdomadaire: arbitrage LLM désactivé (SELF_INSPECT forcé)")
        else:
            llm_choice = await self._llm_select_routine(scored)
        if llm_choice:
            llm_intent = llm_choice["intent"]
            # Le LLM a choisi — trouver la routine correspondante dans la liste scored
            found = False
            for r, s in scored:
                if r["intent"] == llm_intent:
                    selected, score = r, s
                    found = True
                    break
            if not found:
                # Intent LLM absent de la liste (filtré par circadien/budget?) → fallback
                available = [r["intent"] for r, _ in scored[:5]]
                logger.warning(f"[AUTONOMY] LLM a proposé '{llm_intent}' absent de scored. Disponibles: {available}")
                selected, score = scored[0]
            print(f"   🧠 LLM ARBITRE: {llm_intent} — {llm_choice.get('reason', '?')}")
            if found and llm_intent != scored[0][0]["intent"]:
                print(f"   🔀 LLM a overridé le scoring mécanique ({scored[0][0]['intent']} → {llm_intent})")
                logger.info(f"[AUTONOMY] LLM override: {scored[0][0]['intent']}→{llm_intent} — {llm_choice.get('reason', '?')}")
        else:
            selected, score = scored[0]
            logger.info(f"[AUTONOMY] LLM arbitre: fallback mécanique → {selected['intent']} (score={score:.1f})")

        agent = selected["agent"]
        intent = selected["intent"]

        # --- Veto proactif ---
        veto_reason = self._should_veto(intent, agent)
        if veto_reason:
            # Le préfrontal peut overrider certains vetos (SHED, FLINCH) si un goal est avancé
            overridden = False
            try:
                from core.prefrontal import prefrontal
                inhibition = prefrontal.compute_inhibition(intent, veto_reason)
                if inhibition["action"] == "override":
                    print(f"   🧠 PRÉFRONTAL: Override {inhibition['override_target']} — {inhibition['reason']}")
                    try:
                        from core.hippocampus import hippocampus
                        hippocampus.record_veto_override(intent, agent, inhibition['reason'])
                    except Exception:
                        pass
                    veto_reason = ""  # Annuler le veto
                    overridden = True
            except Exception:
                pass
            if veto_reason:
                print(f"   🚫 VETO: {veto_reason}")
                # Enregistrer le veto pour reinjection dans le contexte suivant
                self._recent_vetos.append({"intent": intent, "reason": veto_reason})
                if len(self._recent_vetos) > 3:
                    self._recent_vetos = self._recent_vetos[-3:]
                try:
                    from core.hippocampus import hippocampus
                    hippocampus.record_veto(intent, agent, veto_reason)
                except Exception:
                    pass
                try:
                    from core.cardiac_engine import heart
                    heart.react("veto")
                except Exception:
                    pass
                # Fallback : prendre la prochaine routine non-vetoed
                fallback_found = False
                for alt_selected, alt_score in scored[1:]:
                    alt_intent = alt_selected["intent"]
                    alt_agent = alt_selected["agent"]
                    alt_veto = self._should_veto(alt_intent, alt_agent)
                    if not alt_veto:
                        selected, score = alt_selected, alt_score
                        agent = selected["agent"]
                        intent = alt_intent
                        fallback_found = True
                        break
                if not fallback_found:
                    return  # Aucune alternative non-vetoed

        routine_cost_preview = RESOURCE_COSTS.get(intent, 2)
        print(f"   ✨ AUTONOMY: Routine [{intent}] (score={score:.1f}, coût={routine_cost_preview}pt) -> [{agent.upper()}] ({self.daily_count + 1}/{MAX_DAILY_ROUTINES}, budget: {self.daily_budget_used}/{DAILY_BUDGET_POINTS}pt)")

        # --- Log decomposition scoring ---
        # Calcul unique, reutilise dans les events AUTONOMY_ROUTINE_COMPLETE
        try:
            self._last_scoring_breakdown = self._build_scoring_breakdown(intent)
            if self._last_scoring_breakdown:
                parts = [f"{k} {v:+.1f}" for k, v in self._last_scoring_breakdown.items()]
                print(f"      SCORING: {', '.join(parts)}")
        except Exception:
            self._last_scoring_breakdown = {}

        # Notification préfrontale pre-routine
        try:
            from core.prefrontal import prefrontal
            prefrontal.on_routine_start(intent)
        except Exception:
            pass
        # Voix intérieure : routine commence → désactiver DMN
        try:
            from core.inner_voice import voice as inner_voice
            inner_voice.set_idle(False)
        except Exception:
            pass

        # Annonce de l'objectif associé
        try:
            from core.objectives_engine import objectives as obj_engine
            best_affinity = 0.0
            best_obj = None
            for obj in obj_engine.get_active_objectives():
                affinity = obj.get("routine_affinities", {}).get(intent, 0.0)
                if affinity > best_affinity:
                    best_affinity = affinity
                    best_obj = obj
            if best_obj:
                print(f"   🎯 Contribue à: {best_obj['title']} ({best_obj['progress']:.0%})")
        except Exception:
            pass

        # Gestion spéciale des routines non-standard
        if intent == "COUNCIL_DEBATE":
            response = await self._execute_council_debate()
        elif intent == "GRIMOIRE_INVOKE":
            response = await self._execute_grimoire_routine()
        elif intent == "MEMORY_CLEANUP":
            response = await self._execute_memory_cleanup()
        elif intent == "SECURITY_AUDIT":
            response = await self._execute_security_audit()
        elif intent == "AUDIT_STRUCTURE":
            response = await self._execute_audit_structure()
        elif intent == "AUDIT_SURVIE":
            response = await self._execute_audit_survie()
        elif intent == "REFACTORING_AUDIT":
            response = await self._execute_refactoring_audit()
        elif intent == "CI_PIPELINE_RUN":
            response = await self._execute_ci_pipeline_run()
        elif intent == "REFACTOR_RANDOM":
            response = await self._execute_refactor_random()
        elif intent == "MEMORY_CONSOLIDATION":
            # Consolidation forkee avec timeout (inspire autoDream/KAIROS)
            try:
                response = await asyncio.wait_for(
                    self._execute_memory_consolidation(), timeout=120
                )
            except asyncio.TimeoutError:
                logger.warning("[AUTONOMY] MEMORY_CONSOLIDATION timeout (120s)")
                response = {"status": "error", "result": "Consolidation interrompue (timeout 120s)."}
        elif intent == "SOLILOQUE_INTERNE":
            response = await self._execute_soliloque()
            self._daily_soliloque_done = True
        elif intent == "COFFEE_BREAK":
            response = await self._execute_coffee_break()
        elif intent == "STEFAN_CONFRONTATION":
            response = await self._execute_stefan_confrontation()
            self._daily_stefan_done = True
        elif intent == "SELF_INSPECT":
            response = await self._execute_self_inspect()
        elif intent == "SELF_ANALYSIS":
            response = await self._execute_self_analysis()
            self._daily_analysis_done = True
        elif intent == "AUTO_FUZZING":
            response = await self._execute_auto_fuzzing()
        elif intent == "CREATIVE_PLAY":
            response = await self._execute_creative_play()
        elif intent == "OPEN_INTENT":
            response = await self._execute_open_intent()
        elif intent == "NEURAL_TRAINING":
            response = await self._execute_neural_training()
        elif intent == "PARAM_EXPERIMENT":
            response = await self._execute_param_experiment()
        elif intent == "EVENING_REFLECTION":
            response = await self._execute_evening_reflection()
        elif intent == "GRIMOIRE_EVOLVE":
            response = await self._execute_grimoire_evolve()
        elif intent == "VEILLE_IA":
            response = await self._execute_veille_ia(routine)
        elif intent == "VISUAL_OBSERVATION":
            response = await self._execute_visual_observation()
        elif intent.startswith("SCHOOL_"):
            response = await self._execute_school_class(routine, intent)
        elif intent == "DROPZONE_SCAN" and dropzone_count == 0:
            # Dropzone vide → veille YouTube IA (rotation des sujets)
            yt_index = self.total_routines_executed % len(YOUTUBE_AI_VEILLE)
            yt_topic = YOUTUBE_AI_VEILLE[yt_index]
            print(f"   📺 DROPZONE vide → Veille YouTube IA: {yt_topic['focus'][:60]}...")
            response = await orchestrator.dispatch_task("researcher", {
                "mission": f"VEILLE YOUTUBE IA: Recherche des vidéos YouTube récentes sur: {yt_topic['query']}",
                "context": (
                    "YOUTUBE_VEILLE — La dropzone est vide. "
                    f"Cherche sur le web des vidéos YouTube récentes sur: {yt_topic['focus']}. "
                    "Résume les 2-3 découvertes les plus pertinentes pour un système multi-agents "
                    "autonome comme Prométhée. Sauvegarde les trouvailles en mémoire."
                ),
                "force_local": True,
                "intent": "YOUTUBE_VEILLE",
            })
            # Enregistrer la veille YouTube dans le journal stratégique
            try:
                from core.strategic_journal import journal as strat_journal
                strat_journal.append_research_entry(
                    topic=yt_topic["focus"],
                    findings=response.get("result", "") if response else "",
                    source="YouTube",
                )
            except Exception as e:
                logger.warning(f"[AUTONOMY] Écriture journal veille échouée: {e}")
        else:
            # Injection du purpose_context dans les missions autonomes standard
            purpose_ctx = ""
            # Signaux descendants : resume executif de l'etat cerebral
            try:
                _desc_signals = self._compute_descending_signals()
                _desc_line = self._format_descending_signals(_desc_signals)
                if _desc_line:
                    purpose_ctx = _desc_line + "\n"
            except Exception:
                _desc_signals = {}

            # Global Workspace (Baars) : competition pour la conscience
            # Remplace la concatenation de 16 sources par un filtre intelligent
            try:
                from core.global_workspace import workspace
                workspace.collect_from_organs()
                dominant = _desc_signals.get("dominant_mode", "") if isinstance(_desc_signals, dict) else ""
                # Utiliser le mode dominant des signaux descendants
                if not dominant and _desc_signals:
                    active = [(k, v) for k, v in _desc_signals.items() if v >= 0.2]
                    if active:
                        dominant = max(active, key=lambda x: x[1])[0]
                gw_ctx = workspace.get_conscious_context(dominant)
                if gw_ctx:
                    purpose_ctx += f"\n{gw_ctx}"
            except Exception:
                # Fallback : ancien systeme de concatenation brute
                try:
                    from core.self_awareness import awareness
                    purpose_ctx += awareness.get_purpose_context()
                except Exception:
                    pass
                try:
                    from core.prefrontal import prefrontal
                    delib_ctx = prefrontal.get_deliberation_context()
                    if delib_ctx:
                        purpose_ctx += f"\n{delib_ctx}"
                except Exception:
                    pass
                try:
                    from core.corpus_callosum import callosum
                    cog_ctx = callosum.get_cognitive_context()
                    if cog_ctx:
                        purpose_ctx += f"\n{cog_ctx}"
                except Exception:
                    pass

            # Journal intime (continuité narrative — toujours inclus)
            journal_ctx = self.get_dream_journal_context()
            if journal_ctx:
                purpose_ctx += f"\n{journal_ctx}"

            # --- Continuité Mythos-inspired : insights récents + eureka en attente ---
            if self._recent_insights:
                insight_lines = []
                for ins in self._recent_insights[-3:]:
                    insight_lines.append(f"- [{ins['intent']}] (q={ins['quality']}) {ins['summary'][:150]}")
                purpose_ctx += "\n[INSIGHTS RECENTS — ce que tu as decouvert, continue sur cette lancee]\n"
                purpose_ctx += "\n".join(insight_lines)
            if self._pending_eureka_context:
                purpose_ctx += f"\n[HYPOTHESE A TESTER — une connexion inattendue a emerge]\n{self._pending_eureka_context}"
            # --- Audit de survie : signaler les modifications recentes ---
            if self._recent_modifications:
                mod_lines = [f"- {m['description']}" for m in self._recent_modifications[-3:]]
                purpose_ctx += (
                    "\n[MODIFICATIONS RECENTES DE TON SYSTEME — reflechis a leur impact]\n"
                    + "\n".join(mod_lines)
                    + "\nAvant d'agir, demande-toi : ces changements affectent-ils ta routine actuelle ?"
                )
            # Phase D (2026-04-16) : injecter les anomalies meta dans le contexte
            # pour que le LLM de la routine sache POURQUOI l'organisme est inquiet.
            try:
                from core.meta_observer import observer as _meta_obs
                meta_anomalies = _meta_obs.get_last_anomalies()
                if meta_anomalies:
                    anomaly_lines = [f"- {a['description']}" for a in meta_anomalies[:3]]
                    purpose_ctx += (
                        "\n[META-OBSERVATION — anomalies metaboliques detectees]\n"
                        + "\n".join(anomaly_lines)
                        + "\nSi ta routine peut investiguer ces tendances, fais-le."
                    )
            except Exception:
                pass

            # Mission propre (sans wrapper ni guardrail — évite la fuite de prompt dans les recherches web)
            raw_mission = selected["mission"]
            # Retirer le préfixe [MODE VEILLE] déjà présent dans certaines missions
            clean_mission = raw_mission.replace("[MODE VEILLE] ", "").replace("[MODE VEILLE]", "").strip()

            # Enrichissement dynamique pour EXPANSION_CODE (anti-stagnation)
            # Injecte le mode dominant et la cohérence pour varier le prompt LLM
            if intent == "EXPANSION_CODE":
                try:
                    from core.brain_vm import brain
                    bs = brain.current_state
                    if bs:
                        mode = bs.dominant_mode or "standard"
                        coh = bs.global_coherence
                        clean_mission += f" (Mode actuel: {mode}, coherence: {coh:.2f}. Adapte ton analyse a ce contexte.)"
                except Exception:
                    pass

            mission_text = f"[MODE VEILLE] {clean_mission}\nAgis de ta propre initiative."
            # Guardrails et purpose dans le context (pas dans la mission envoyée aux moteurs de recherche)
            context_parts = ["PROTOCOLE_AUTONOMIE"]
            if purpose_ctx and isinstance(purpose_ctx, str):
                context_parts.append(purpose_ctx)
            # Reinjection des vetos recents (inspire Claude Code / KAIROS)
            # L'agent voit pourquoi les routines precedentes ont ete bloquees
            # et adapte son comportement au lieu de repeter les memes erreurs
            if self._recent_vetos:
                veto_lines = [f"- {v['intent']}: {v['reason']}" for v in self._recent_vetos[-3:]]
                context_parts.append(
                    "[VETOS RECENTS — routines bloquees recemment, adapte-toi]\n"
                    + "\n".join(veto_lines)
                )
            context_parts.append(AUTONOMY_GUARDRAIL)
            response = await orchestrator.dispatch_task(agent, {
                "mission": mission_text,
                "context": "\n".join(context_parts),
                "force_local": True,
                "intent": intent,
            })

        # --- Guard : routines "skipped" (council saturé, etc.) — pas de budget consommé ---
        if response and response.get("status") == "skipped":
            reason = response.get("reason", "unknown")
            print(f"   ⏭️ Routine {intent} skippée ({reason})")
            self._record_routine(agent, intent, "skipped", quality_score=0.0)
            # Pas d'incrémentation daily_count, budget, error_streak
            return

        # Sujet du council (pour la déduplication)
        council_subject = getattr(self, "_current_council_subject", "")

        # Slug Grimoire réel (pour la rotation)
        grimoire_slug = getattr(self, "_last_grimoire_slug", "")
        self._last_grimoire_slug = ""

        # Score qualité post-routine
        quality_score = self._score_result_quality(response, intent)

        # === SNAPSHOT PHI × QUALITY — protocole expérimental ===
        self._log_routine_phi_snapshot(intent, agent, quality_score)

        # MASTER_PROMPT : vérifier si l'agent sous-performe (3 échecs → optimiser prompt)
        try:
            await self._master_prompt_check(agent, intent, quality_score)
        except Exception:
            pass

        # Feedback reptilien via le bus (AUTONOMY_ROUTINE_COMPLETE → reptile._on_routine_complete)
        # Pas d'appel direct pour éviter le double-comptage.

        # Aperçu du résultat pour comparaison future
        result_preview = ""
        result_full = ""
        if response and isinstance(response, dict):
            result_full = str(response.get("result", ""))
            result_preview = result_full[:200]

        # NOTE: desires, cardiac, prefrontal recoivent le feedback via le bus
        # (AUTONOMY_ROUTINE_COMPLETE) — pas d'appel direct pour eviter le double-comptage.
        # desires.save() est appele via le handler bus _on_routine_complete.

        # Voix intérieure : routine terminée → réactiver DMN
        try:
            from core.inner_voice import voice as inner_voice
            inner_voice.set_idle(True)
        except Exception:
            pass

        # Reset du flag d'apprentissage pour ce cycle
        self._learning_done_this_cycle = False
        failure_type = ""

        if response and response.get("status") in ("success", "consensus", "max_rounds"):
            # Distinguer consensus réel vs max_rounds (timeout sans accord)
            actual_status = response.get("status", "success")
            is_max_rounds = actual_status == "max_rounds"

            if quality_score < 0.3:
                # Succès technique mais résultat de mauvaise qualité
                failure_type = self._diagnose_failure(response, quality_score, intent)
                record_status = "max_rounds_low" if is_max_rounds else "low_quality"
                print(f"   ⚠️ Routine {intent} terminée mais qualité basse ({quality_score:.2f}) [{failure_type}]")
                self._record_routine(agent, intent, record_status, subject=council_subject,
                                     quality_score=quality_score, failure_type=failure_type,
                                     result_preview=result_preview, grimoire_slug=grimoire_slug)
                self.error_streak += 1
                # Apprentissage ciblé si ignorance détectée
                if failure_type == "ignorance":
                    try:
                        from core.self_awareness import awareness
                        awareness.record_knowledge_gap(selected["mission"][:100], intent)
                    except Exception:
                        pass
                    await self._trigger_targeted_learning(selected["mission"], agent, intent)
            else:
                record_status = "max_rounds" if is_max_rounds else "success"
                emoji = "⚖️" if is_max_rounds else "✅"
                print(f"   {emoji} Fin Routine {agent.upper() if agent != '_council' else 'COUNCIL'} (qualité: {quality_score:.2f}{', max_rounds' if is_max_rounds else ''})")
                self._record_routine(agent, intent, record_status, subject=council_subject,
                                     quality_score=quality_score, result_preview=result_preview,
                                     grimoire_slug=grimoire_slug)
                self.error_streak = 0

                # --- Capture insight (continuité Mythos-inspired) ---
                if quality_score >= 0.6 and result_preview and len(result_preview) > 30:
                    self._recent_insights.append({
                        "intent": intent,
                        "summary": result_preview[:300],
                        "quality": round(quality_score, 2),
                        "ts": time.time(),
                    })
                    if len(self._recent_insights) > 3:
                        self._recent_insights = self._recent_insights[-3:]
                    # Linkback curiosité : si FREE_EXPLORATION, écrire le résultat dans la graine
                    if intent == "FREE_EXPLORATION":
                        try:
                            from core.curiosity_bank import CuriosityBank
                            bank = CuriosityBank()
                            # Chercher la graine la plus récemment explorée
                            for seed in reversed(bank.seeds):
                                if seed.get("explored") and not seed.get("result"):
                                    seed["result"] = result_preview[:200]
                                    bank._save()
                                    break
                        except Exception:
                            pass
                    # Eureka confirmation : si cette routine est un follow-up eureka
                    if self._pending_eureka_context:
                        try:
                            from core.incubation_cognitive import incubation
                            for eureka in reversed(incubation._eureka_history):
                                if not eureka.tested:
                                    eureka.tested = True
                                    eureka.test_result = result_preview[:200]
                                    eureka.test_quality = quality_score
                                    incubation._save()
                                    logger.info(f"[MYTHOS] Eureka '{eureka.hypothesis[:50]}' testee (q={quality_score:.2f})")
                                    break
                        except Exception:
                            pass
                        self._pending_eureka_context = ""

                # --- Ratio qualité/coût par intent ---
                cost = RESOURCE_COSTS.get(intent, 2)
                stats = self._intent_quality_stats.get(intent, {"sum_q": 0, "sum_c": 0, "n": 0})
                stats["sum_q"] = stats.get("sum_q", 0) + quality_score
                stats["sum_c"] = stats.get("sum_c", 0) + cost
                stats["n"] = stats.get("n", 0) + 1
                # Rolling : garder les 10 dernières au max (approximation via moyenne pondérée)
                if stats["n"] > 10:
                    stats["sum_q"] *= 0.9
                    stats["sum_c"] *= 0.9
                    stats["n"] = 10
                self._intent_quality_stats[intent] = stats

                # --- Recovery production : sauvegarder les routines exceptionnelles ---
                if score >= 9.0:
                    try:
                        self._save_to_recovery(intent, agent, score, quality_score,
                                               result_full, self._last_scoring_breakdown)
                    except Exception as e:
                        logger.debug(f"[RECOVERY] Sauvegarde echouee: {e}")
        else:
            failure_type = self._diagnose_failure(response, quality_score, intent)
            self._record_routine(agent, intent, "error", subject=council_subject,
                                 quality_score=quality_score, failure_type=failure_type,
                                 result_preview=result_preview, grimoire_slug=grimoire_slug)
            self.error_streak += 1
            # Apprentissage ciblé si ignorance détectée
            if failure_type == "ignorance":
                try:
                    from core.self_awareness import awareness
                    awareness.record_knowledge_gap(selected["mission"][:100], intent)
                except Exception:
                    pass
                await self._trigger_targeted_learning(selected["mission"], agent, intent)

        # --- Precision weighting : mettre à jour la fiabilité des organes ---
        try:
            self._update_organ_precision(intent, quality_score)
        except Exception as e:
            logger.warning(f"[PRECISION] Erreur mise à jour: {e}")

        # --- Frustration DesireEngine : forcer l'intent suivant si pulsion frustrée ---
        # M01: compteur de forçage par drive — cooldown 10 cycles si >2 forçages en 5 cycles
        self._drive_force_cycle += 1
        if self._drive_force_cycle % 5 == 0:
            self._drive_force_counts = {}  # reset fenêtre tous les 5 cycles
        if not self._forced_next_intent:
            try:
                from core.desire_engine import desires as _desires
                frustrated = [
                    (name, d) for name, d in _desires.drives.items()
                    if (d.frustration_streak >= 4 and d.deprivation >= 70) or d.deprivation >= 90
                ]
                if frustrated:
                    frustrated.sort(key=lambda x: x[1].deprivation, reverse=True)
                    drive_name, drive = frustrated[0]
                    # M01: vérifier le cooldown avant de forcer
                    force_count = self._drive_force_counts.get(drive_name, 0)
                    total_forces = self._drive_force_total.get(drive_name, 0)
                    if force_count > 2:
                        logger.info(f"[EVEIL] Pulsion {drive_name} en cooldown (forcé {force_count}x en 5 cycles), skip")
                    elif total_forces >= 10:
                        logger.info(f"[EVEIL] Pulsion {drive_name} plafond session ({total_forces} forçages), skip")
                    else:
                        # EVEIL — top_k=5 permet de tomber sur le runner-up
                        # si le top 1 est bloque par l'anti-boucle.
                        from core.drive_routine_registry import get_routines_for_drive_live
                        routines = get_routines_for_drive_live(drive_name, top_k=5)
                        ranked_intents = [r[0] for r in routines]

                        if ranked_intents:
                            # Anti-boucle ameliore : chercher le premier intent
                            # different du dernier dispatche (runner-up recovery)
                            last_intent = self.routine_history[-1].get("intent", "") if self.routine_history else ""
                            best_intent = next(
                                (intent for intent in ranked_intents if intent != last_intent),
                                ranked_intents[0],  # fallback sur le top si tous identiques
                            )
                            if best_intent != last_intent:
                                self._forced_next_intent = best_intent
                                self._drive_force_counts[drive_name] = force_count + 1
                                self._drive_force_total[drive_name] = total_forces + 1
                                logger.warning(f"[EVEIL] Pulsion {drive_name} critique (dep={drive.deprivation:.0f}) → force {best_intent} ({force_count + 1}/3, total={total_forces + 1}/10)")
            except Exception:
                pass

        # Loop breaker : si repetition ou error_streak eleve -> consulter le specialiste
        if failure_type == "repetition" or self.error_streak >= 5:
            try:
                loop_response = await orchestrator.dispatch_task("loop_breaker", {
                    "mission": "AIDE: loop",
                    "context": json.dumps({
                        "history": self.routine_history[-10:],
                        "error_streak": self.error_streak,
                    }, default=str),
                    "intent": "LOOP_BREAKER_HELP",
                })
                if loop_response and isinstance(loop_response, dict):
                    loop_action = loop_response.get("action", "skip")
                    if loop_action == "skip" and loop_response.get("blacklist"):
                        self._temp_blacklist = set(loop_response["blacklist"])
                        print(f"   🔀 LOOP_BREAKER: Blacklist {self._temp_blacklist}")
                    elif loop_action == "redirect" and loop_response.get("forced_intent"):
                        self._forced_next_intent = loop_response["forced_intent"]
                        print(f"   🔀 LOOP_BREAKER: Redirect -> {self._forced_next_intent}")
                    elif loop_action == "cooldown":
                        extra = loop_response.get("extra_sleep", 120)
                        print(f"   ⏸️ LOOP_BREAKER: Cooldown {extra}s")
                        await asyncio.sleep(extra)
                    elif loop_action == "escalate":
                        print(f"   🚨 LOOP_BREAKER: Escalade Council recommandee (streak={self.error_streak})")
                        self._forced_next_intent = "COUNCIL_DEBATE"
                        try:
                            from core.hippocampus import hippocampus
                            hippocampus.record_council_forced(self.error_streak)
                        except Exception:
                            pass
                    # Publier l'action pour incubation cognitive
                    try:
                        await bus.publish("LOOP_BREAKER_ACTION", {
                            "action": loop_action,
                            "intent": intent,
                            "error_streak": self.error_streak,
                            "context": loop_response,
                        })
                    except Exception:
                        pass
                    # Enregistrer le loop breaker dans l'hippocampe
                    try:
                        from core.hippocampus import hippocampus as _hippo
                        _hippo.record_loop_breaker(loop_action, intent, self.error_streak)
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"[AUTONOMY] Loop breaker echoue: {e}")

        # Alimentation spreading activation (non bloquant)
        if result_preview and len(result_preview) > 50:
            try:
                from core.spreading_activation import activation_engine
                asyncio.create_task(
                    activation_engine.activate(
                        result_preview, "collective_wisdom", max_hops=0
                    )
                )
            except Exception:
                pass

        # Publier AUTONOMY_ROUTINE_COMPLETE pour les handlers PSYCHE
        participants = []
        if intent == "COUNCIL_DEBATE" and response:
            participants = response.get("participants", [])
        routine_status = "success" if response and response.get("status") in ("success", "consensus") and quality_score >= 0.3 else "error"

        # Hebbian : capturer le contexte cognitif pour l'apprentissage contextuel
        cognitive_context = {}
        try:
            from core.corpus_callosum import callosum as _cc
            cognitive_context["cognitive_state"] = _cc.cognitive_state
        except Exception:
            pass
        try:
            from core.desire_engine import desires as _des
            dominant = max(_des.drives.values(), key=lambda d: d.deprivation, default=None)
            if dominant:
                cognitive_context["dominant_drive"] = dominant.name
        except Exception:
            pass
        try:
            from core.cardiac_engine import heart as _heart
            cognitive_context["cardiac_emotion"] = _heart.current_emotion
        except Exception:
            pass

        await bus.publish("AUTONOMY_ROUTINE_COMPLETE", {
            "intent": intent,
            "agent": agent,
            "participants": participants,
            "status": routine_status,
            "quality_score": quality_score,
            "result": result_preview,
            "scoring_breakdown": getattr(self, "_last_scoring_breakdown", {}),
            "cognitive_context": cognitive_context,
        })
        # Publier ROUTINE_FAILED pour les organes qui ecoutent les echecs
        if routine_status == "error":
            await bus.publish("ROUTINE_FAILED", {
                "intent": intent,
                "agent": agent,
                "reason": failure_type or "unknown",
                "quality_score": quality_score,
            })

        # SensoriumLoop : fermer la boucle perception-action
        try:
            await self._publish_sensorium_feedback(
                intent, agent, quality_score, routine_status)
        except Exception as e:
            logger.debug(f"[SENSORIUM] Feedback pulse erreur: {e}")

        # Voting lateral : l'agent vote pour les intents lies apres succes
        if routine_status == "success" and quality_score >= 0.5:
            self._publish_agent_vote(agent, intent)

        self.daily_count += 1
        self.total_routines_executed += 1
        # Décompter le coût en points de budget (override dégradé si applicable)
        routine_cost = RESOURCE_COSTS.get(intent, 2)
        if intent == "COUNCIL_DEBATE" and getattr(self, "_council_degraded", False):
            routine_cost = RESOURCE_COSTS_DEGRADED.get(intent, routine_cost)
            self._council_degraded = False
        self.daily_budget_used += routine_cost
        logger.info(f"[AUTONOMY] Routine {self.daily_count}/{MAX_DAILY_ROUTINES} du jour (coût: {routine_cost}pt, budget: {self.daily_budget_used}/{DAILY_BUDGET_POINTS}pt)")

        # Snapshot conscience de soi périodique (toutes les 5 routines)
        if self.daily_count % 5 == 0:
            try:
                from core.self_awareness import awareness
                awareness.generate_snapshot()
            except Exception:
                pass
            # Decay + cleanup spreading activation
            try:
                from core.spreading_activation import activation_engine
                activation_engine.decay_all()
                activation_engine.cleanup()
            except Exception:
                pass

    async def _execute_forced_routine(self, routine: dict, health: dict):
        """Execute une routine forcee par le loop_breaker (bypass scoring)."""
        agent = routine["agent"]
        intent = routine["intent"]
        routine_cost = RESOURCE_COSTS.get(intent, 2)

        # Veto FREEZE — même forcée, un FREEZE reptilien bloque tout
        try:
            from core.reptilian_core import reptile
            if reptile.should_freeze():
                logger.warning(f"[AUTONOMY] Routine FORCED {intent} bloquée par FREEZE reptilien")
                return
        except Exception:
            pass

        print(f"   ✨ AUTONOMY [FORCED]: [{intent}] -> [{agent.upper()}] (cout={routine_cost}pt)")

        # Notification préfrontale pre-routine
        try:
            from core.prefrontal import prefrontal
            prefrontal.on_routine_start(intent)
        except Exception:
            pass
        # Voix intérieure : routine commence → désactiver DMN
        try:
            from core.inner_voice import voice as inner_voice
            inner_voice.set_idle(False)
        except Exception:
            pass

        # Reutiliser la logique standard de dispatch
        if intent == "COUNCIL_DEBATE":
            response = await self._execute_council_debate()
        elif intent == "GRIMOIRE_INVOKE":
            response = await self._execute_grimoire_routine()
        elif intent == "MEMORY_CLEANUP":
            response = await self._execute_memory_cleanup()
        elif intent == "SECURITY_AUDIT":
            response = await self._execute_security_audit()
        elif intent == "AUDIT_STRUCTURE":
            response = await self._execute_audit_structure()
        elif intent == "AUDIT_SURVIE":
            response = await self._execute_audit_survie()
        elif intent == "REFACTORING_AUDIT":
            response = await self._execute_refactoring_audit()
        elif intent == "CI_PIPELINE_RUN":
            response = await self._execute_ci_pipeline_run()
        elif intent == "REFACTOR_RANDOM":
            response = await self._execute_refactor_random()
        elif intent == "MEMORY_CONSOLIDATION":
            # Consolidation forkee avec timeout (inspire autoDream/KAIROS)
            try:
                response = await asyncio.wait_for(
                    self._execute_memory_consolidation(), timeout=120
                )
            except asyncio.TimeoutError:
                logger.warning("[AUTONOMY] MEMORY_CONSOLIDATION timeout (120s)")
                response = {"status": "error", "result": "Consolidation interrompue (timeout 120s)."}
        elif intent == "SOLILOQUE_INTERNE":
            response = await self._execute_soliloque()
            self._daily_soliloque_done = True
        elif intent == "COFFEE_BREAK":
            response = await self._execute_coffee_break()
        elif intent == "STEFAN_CONFRONTATION":
            response = await self._execute_stefan_confrontation()
            self._daily_stefan_done = True
        elif intent == "SELF_INSPECT":
            response = await self._execute_self_inspect()
        elif intent == "SELF_ANALYSIS":
            response = await self._execute_self_analysis()
            self._daily_analysis_done = True
        elif intent == "AUTO_FUZZING":
            response = await self._execute_auto_fuzzing()
        elif intent == "CREATIVE_PLAY":
            response = await self._execute_creative_play()
        elif intent == "OPEN_INTENT":
            response = await self._execute_open_intent()
        elif intent == "NEURAL_TRAINING":
            response = await self._execute_neural_training()
        elif intent == "PARAM_EXPERIMENT":
            response = await self._execute_param_experiment()
        elif intent == "EVENING_REFLECTION":
            response = await self._execute_evening_reflection()
        elif intent == "GRIMOIRE_EVOLVE":
            response = await self._execute_grimoire_evolve()
        elif intent == "VEILLE_IA":
            response = await self._execute_veille_ia(routine)
        elif intent == "VISUAL_OBSERVATION":
            response = await self._execute_visual_observation()
        elif intent.startswith("SCHOOL_"):
            response = await self._execute_school_class(routine, intent)
        elif intent == "DROPZONE_SCAN":
            # Dropzone vide → veille YouTube IA (fallback identique au path standard)
            try:
                from core.capabilities.dropzone_indexer import DropzoneIndexer
                dc = DropzoneIndexer().quick_count("USER_DROPZONE")
            except Exception:
                dc = 0
            if dc > 0:
                response = await orchestrator.dispatch_task(agent, {
                    "mission": f"[MODE VEILLE] {routine['mission']}",
                    "context": f"PROTOCOLE_AUTONOMIE\n{AUTONOMY_GUARDRAIL}",
                    "force_local": True,
                    "intent": intent,
                })
            else:
                yt_index = self.total_routines_executed % len(YOUTUBE_AI_VEILLE)
                yt_topic = YOUTUBE_AI_VEILLE[yt_index]
                print(f"   📺 FORCED DROPZONE vide → Veille YouTube IA: {yt_topic['focus'][:60]}...")
                response = await orchestrator.dispatch_task("researcher", {
                    "mission": f"VEILLE YOUTUBE IA: Recherche des vidéos YouTube récentes sur: {yt_topic['query']}",
                    "context": (
                        "YOUTUBE_VEILLE — La dropzone est vide. "
                        f"Cherche sur le web des vidéos YouTube récentes sur: {yt_topic['focus']}. "
                        "Résume les 2-3 découvertes les plus pertinentes pour un système multi-agents "
                        "autonome comme Prométhée. Sauvegarde les trouvailles en mémoire."
                    ),
                    "force_local": True,
                    "intent": "YOUTUBE_VEILLE",
                })
        else:
            response = await orchestrator.dispatch_task(agent, {
                "mission": f"[MODE VEILLE] {routine['mission']}",
                "context": f"PROTOCOLE_AUTONOMIE\n{AUTONOMY_GUARDRAIL}",
                "force_local": True,
                "intent": intent,
            })

        quality = self._score_result_quality(response, intent)
        status = "success" if response and response.get("status") in ("success", "consensus") else "error"
        self._record_routine(agent, intent, status, quality_score=quality)
        if status == "success" and quality >= 0.3:
            self.error_streak = 0
            # Anti-gaspillage : reset compteur échecs FORCED sur succès
            self._forced_failure_counts.pop(intent, None)
        else:
            self.error_streak += 1
            # Anti-gaspillage : incrémenter compteur échecs FORCED
            self._forced_failure_counts[intent] = self._forced_failure_counts.get(intent, 0) + 1
            fail_count = self._forced_failure_counts[intent]
            if fail_count >= FORCED_FAILURE_THRESHOLD:
                logger.warning(f"[AUTONOMY] Intent FORCED '{intent}' blacklisté — {fail_count} échecs consécutifs, ne sera plus forcé cette session.")
        self.daily_count += 1
        self.total_routines_executed += 1
        self.daily_budget_used += routine_cost
        logger.info(f"[AUTONOMY] Routine FORCED {self.daily_count}/{MAX_DAILY_ROUTINES} (cout: {routine_cost}pt, budget: {self.daily_budget_used}/{DAILY_BUDGET_POINTS}pt)")

        # Feedback reptilien via le bus (AUTONOMY_ROUTINE_COMPLETE → reptile._on_routine_complete)
        # Pas d'appel direct pour éviter le double-comptage.

        # Voix intérieure : routine terminée → réactiver DMN
        try:
            from core.inner_voice import voice as inner_voice
            inner_voice.set_idle(True)
        except Exception:
            pass

        # Feedback bus (desires, cardiac, prefrontal via handlers)
        result_preview = str(response.get("result", ""))[:200] if response else ""
        participants = []
        if intent == "COUNCIL_DEBATE" and response:
            participants = response.get("participants", [])

        # Hebbian : contexte cognitif (post-budget)
        cognitive_context = {}
        try:
            from core.corpus_callosum import callosum as _cc
            cognitive_context["cognitive_state"] = _cc.cognitive_state
        except Exception:
            pass
        try:
            from core.desire_engine import desires as _des
            dominant = max(_des.drives.values(), key=lambda d: d.deprivation, default=None)
            if dominant:
                cognitive_context["dominant_drive"] = dominant.name
        except Exception:
            pass
        try:
            from core.cardiac_engine import heart as _heart
            cognitive_context["cardiac_emotion"] = _heart.current_emotion
        except Exception:
            pass

        await bus.publish("AUTONOMY_ROUTINE_COMPLETE", {
            "intent": intent,
            "agent": agent,
            "participants": participants,
            "status": status,
            "quality_score": quality,
            "result": result_preview,
            "scoring_breakdown": getattr(self, "_last_scoring_breakdown", {}),
            "cognitive_context": cognitive_context,
        })
        # Publier ROUTINE_FAILED pour les organes qui ecoutent les echecs
        if status == "error":
            await bus.publish("ROUTINE_FAILED", {
                "intent": intent,
                "agent": agent,
                "reason": "post_budget_error",
                "quality_score": quality,
            })

        # SensoriumLoop : fermer la boucle perception-action (post-budget)
        try:
            await self._publish_sensorium_feedback(intent, agent, quality, status)
        except Exception as e:
            logger.debug(f"[SENSORIUM] Feedback pulse erreur (post-budget): {e}")

        self._persist_state()

    def _should_veto(self, intent: str, agent: str) -> str:
        """Veto proactif basé sur les signatures d'échec apprises. Retourne la raison ou ''."""
        # 0. RÉFLEXE REPTILIEN — court-circuite tout
        try:
            from core.reptilian_core import reptile
            if reptile.should_freeze():
                return f"veto-reptilien: FREEZE actif (menace={reptile.threat_level:.1f})"
            flinch = reptile.should_flinch(intent)
            if flinch:
                return f"veto-reptilien: {flinch}"
            shed, max_cost = reptile.should_shed()
            if shed:
                cost = RESOURCE_COSTS.get(intent, 2)
                if cost > max_cost:
                    return f"veto-reptilien: SHED actif, coût {cost} > max {max_cost}"
        except Exception:
            pass  # Le reptilien tombe → on continue sans lui (résilience)

        # 0b. MARQUEURS SOMATIQUES — intuitions viscérales
        try:
            from core.cardiac_engine import heart
            signal = heart.get_somatic_signal(intent)
            if signal < -1.0:
                return f"veto-somatique: signal viscéral très négatif ({signal:.2f}) pour {intent}"
        except Exception:
            pass

        # 1. Vérifier les échecs répétés dans l'historique
        recent_failures = [
            r for r in self.routine_history[-20:]
            if r.get("intent") == intent and r.get("agent") == agent
            and r.get("status") in ("error", "low_quality")
        ]
        if len(recent_failures) >= 5:
            successes = [
                r for r in self.routine_history[-20:]
                if r.get("intent") == intent and r.get("agent") == agent
                and r.get("status") == "success"
            ]
            if not successes:
                return f"veto: {intent}/{agent} a échoué {len(recent_failures)}x sans succès récent"

        # 2. Vérifier santé système
        if self.last_health_check and isinstance(self.last_health_check, dict):
            if self.last_health_check.get("verdict") == "NO_GO" and intent in ("EXPANSION_CODE", "EXPANSION_CATALOG", "GRIMOIRE_INVOKE"):
                return f"veto: santé NO_GO, routine risquée {intent} reportée"

        # 2b. ROADMAP STRATÉGIQUE — les intents roadmap bypass le veto préfrontal
        # (les vetos reptilien/somatique/santé restent actifs)
        try:
            from core.roadmap_engine import roadmap as _rm
            if _rm.compute_roadmap_bonus(intent) > 0:
                return ""
        except Exception:
            pass

        # 2c. EMPLOI DU TEMPS SCOLAIRE — les SCHOOL_ intents passent pendant leurs heures
        # Le préfrontal ne doit pas inhiber un cours en cours (les vetos reptilien/somatique/santé restent)
        if intent.startswith("SCHOOL_"):
            try:
                from core.school_schedule import schedule
                if schedule.compute_schedule_bonus(intent) > 0:
                    return ""
            except Exception:
                pass

        # 3. INHIBITION PRÉFRONTALE — arbitrage cognitif
        try:
            from core.prefrontal import prefrontal
            # Collecter le veto en cours (reptilien/somatique déjà passé sans retourner)
            # → on passe "" car aucun veto n'a été déclenché à ce stade
            inhibition = prefrontal.compute_inhibition(intent, "")
            if inhibition["action"] == "inhibit":
                return f"veto-prefrontal: {inhibition['reason']}"
        except Exception:
            pass

        return ""

    async def _execute_memory_consolidation(self) -> dict:
        """Consolide les mémoires récentes en synthèses thématiques. Zero LLM.

        Inspiré autoDream (KAIROS) : déduplication + consolidation + dream.
        Phase 1: Dédup — supprime les doublons (bigram overlap > 60%)
        Phase 2: Consolidation — regroupe par source, crée des résumés
        Phase 3: Dream — consolidation synaptique (renforcement/élagage)
        """
        try:
            from core.vector_store import ChromaMemoryManager
            mgr = ChromaMemoryManager.get_instance()
            if not mgr:
                return {"status": "error", "result": "ChromaDB indisponible."}

            col = mgr._get_collection("collective_wisdom")
            all_docs = col.get(include=["documents", "metadatas"])

            if not all_docs["ids"]:
                return {"status": "success", "result": "Consolidation: aucun document à consolider."}

            now = time.time()
            recent = []
            for doc, meta, doc_id in zip(all_docs["documents"], all_docs["metadatas"], all_docs["ids"]):
                ts = float(meta.get("timestamp", 0))
                if now - ts < 30 * 86400:  # 30 jours
                    recent.append((doc, meta, doc_id, int(meta.get("recall_count", 0))))

            # --- Phase 1: Déduplication (inspiré autoDream/KAIROS) ---
            dedup_count = 0
            try:
                from core.memory_gatekeeper import _bigram_overlap
                # Limiter le scan pour la performance
                scan_pool = recent[:200]
                ids_to_delete = set()
                seen_texts = []  # (doc_text, doc_id, timestamp)

                for doc, meta, doc_id, rc in scan_pool:
                    if doc_id in ids_to_delete:
                        continue
                    doc_text = doc[:300].lower().strip()
                    if not doc_text:
                        continue
                    # Comparer avec les textes déjà vus
                    is_dup = False
                    for seen_text, seen_id, seen_ts in seen_texts:
                        overlap = _bigram_overlap(doc_text, seen_text)
                        if overlap > 0.60:
                            # Garder le plus récent, supprimer le plus ancien
                            doc_ts = float(meta.get("timestamp", 0))
                            if doc_ts < seen_ts:
                                ids_to_delete.add(doc_id)
                            else:
                                ids_to_delete.add(seen_id)
                            is_dup = True
                            break
                    if not is_dup:
                        seen_texts.append((doc_text, doc_id, float(meta.get("timestamp", 0))))

                if ids_to_delete:
                    col.delete(ids=list(ids_to_delete))
                    dedup_count = len(ids_to_delete)
                    logger.info(f"[CONSOLIDATION] Dedup: {dedup_count} doublons supprimes")
            except Exception as e:
                logger.warning(f"[CONSOLIDATION] Dedup echouee: {e}")

            # --- Phase 2: Regroupement par source ---
            _deleted = ids_to_delete if dedup_count > 0 else set()
            groups = {}
            for doc, meta, doc_id, rc in recent:
                if doc_id not in _deleted:
                    source = meta.get("source", "unknown")
                    groups.setdefault(source, []).append(doc[:200])

            # Pour chaque groupe avec 5+ entrées, créer un résumé déterministe
            consolidated = 0
            for source, docs in groups.items():
                if len(docs) >= 5:
                    summary = f"[CONSOLIDATION {source}] {len(docs)} observations récentes:\n"
                    summary += "\n".join(f"- {d[:100]}" for d in docs[:10])
                    mgr.add_documents(
                        [summary],
                        [{"source": "consolidation", "timestamp": str(now), "original_count": len(docs)}],
                        [f"consol-{source}-{int(now)}"],
                        "collective_wisdom"
                    )
                    consolidated += 1

            # --- Phase 3: Dream Mode (consolidation synaptique) ---
            try:
                from core.cardiac_engine import heart
                heart.react("dream")
            except Exception:
                pass
            try:
                from core.synaptic_network import cortex
                dream_report = cortex.dream_consolidation()
                if dream_report.get("dream_connections", 0) > 0:
                    result_msg = (f"Consolidation: {consolidated} groupes synthétisés"
                                  f" à partir de {len(recent)} documents récents."
                                  f" | Dedup: {dedup_count} doublons supprimés"
                                  f" | Dream: +{dream_report['dream_connections']} connexions"
                                  f", -{dream_report['pruned_synapses']} pruned")
                    return {"status": "success", "result": result_msg}
            except Exception:
                pass

            dedup_msg = f" | Dedup: {dedup_count} doublons supprimés" if dedup_count else ""
            return {"status": "success", "result": f"Consolidation: {consolidated} groupes synthétisés à partir de {len(recent)} documents récents.{dedup_msg}"}
        except Exception as e:
            return {"status": "error", "result": f"Erreur consolidation: {e}"}

    async def _execute_soliloque(self) -> dict:
        """Dialogue introspectif avec le compagnon intérieur."""
        try:
            from core.soliloque import soliloque
            result = await soliloque.engage()
            return result
        except Exception as e:
            return {"status": "error", "result": f"Erreur soliloque: {e}"}

    async def _execute_coffee_break(self) -> dict:
        """Pause café avec Alfred — conversation amicale."""
        try:
            from core.ami import alfred
            result = await alfred.coffee_break()
            return result
        except Exception as e:
            return {"status": "error", "result": f"Erreur café: {e}"}

    async def _execute_coffee_game(self) -> dict:
        """Partie de jeu vs Alfred pendant le cafe — 0 LLM, repos GPU."""
        try:
            from core.games.game_hub import game_hub
            from core.games.morpion import ai_move as morpion_ai
            from core.games.puissance4 import ai_move as puissance4_ai
            import random

            # Choisir un jeu aleatoirement
            game_type = random.choice(["morpion", "puissance4"])

            result = game_hub.new_game(game_type, opponent="alfred",
                                       promethee_starts=True)
            if "error" in result:
                return {"status": "error", "result": result["error"]}

            # Boucle simple : Promethee joue, Alfred repond auto via play_move
            # Alfred parle pendant la partie — comme un pote
            import random
            _ALFRED_TAUNTS = [
                "Tu es sur de ce coup ?", "Pas mal, mais j'ai mieux.",
                "Hmm, interessant...", "Tu vas regretter ca.",
                "Allez, montre-moi ce que tu sais faire.",
                "C'est tout ? Je m'attendais a plus.",
                "Bon coup, je l'avoue.", "Tu me surprends.",
                "Je vais te battre, tu sais.", "Concentre-toi.",
            ]
            promethee_symbol = result.get("promethee_symbol", "")
            for turn in range(25):
                if not game_hub._active_session:
                    break
                game = game_hub._get_active_game()
                if game.game_over:
                    break
                if game.current_player != promethee_symbol:
                    break
                # Alfred provoque de temps en temps (1 chance sur 3)
                if turn > 0 and random.random() < 0.33:
                    taunt = random.choice(_ALFRED_TAUNTS)
                    game_hub._apply_chat_pressure(taunt)
                    game_hub._game_chat.append({"player": "alfred", "message": taunt, "ts": time.time()})
                # Promethee joue (hard) — Alfred repond automatiquement
                if game_type == "morpion":
                    move = morpion_ai(game, "hard")
                    if move:
                        result = game_hub.play_move(list(move), player="promethee")
                    else:
                        break
                else:
                    col = puissance4_ai(game, "hard")
                    if col is not None:
                        result = game_hub.play_move(col, player="promethee")
                    else:
                        break

            # Nettoyer si la partie est restee bloquee
            if game_hub._active_session:
                game_hub.forfeit()

            # Resultat
            stats = game_hub.stats
            winner_info = "Promethee" if result.get("state", {}).get("game_over") and \
                          stats.get(f"{game_type}_wins", 0) > 0 else "Alfred ou nul"

            summary = (f"Partie de {game_type} vs Alfred — "
                       f"{result.get('state', {}).get('moves_count', '?')} coups. "
                       f"Score: {stats.get(f'{game_type}_wins', 0)}V "
                       f"{stats.get(f'{game_type}_losses', 0)}D "
                       f"{stats.get(f'{game_type}_draws', 0)}N")

            # Publier sur THOUGHT_STREAM
            try:
                await bus.publish("THOUGHT_STREAM", {
                    "thought": f"[JEU] {summary}",
                    "source": "coffee_game",
                })
            except Exception:
                pass

            print(f"   🎮 JEU CAFÉ: {summary}")
            return {"status": "success", "result": summary}

        except Exception as e:
            logger.warning(f"[COFFEE_GAME] Erreur: {e}")
            return {"status": "error", "result": f"Erreur jeu: {e}"}

    async def _execute_stefan_confrontation(self) -> dict:
        """Confrontation avec Stefan — une question que Prométhée a évitée."""
        try:
            from core.rival import stefan
            material = stefan.find_confrontation_material()
            if not material:
                return {"status": "skipped", "result": "Stefan n'a rien à confronter."}
            result = await stefan.confront(material["text"], material["source"])
            return result
        except Exception as e:
            return {"status": "error", "result": f"Erreur Stefan: {e}"}

    async def _execute_grimoire_routine(self) -> dict:
        """Invoque un agent Grimoire en rotation (le moins récemment utilisé)."""
        try:
            grimoire_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "grimoire", "grimoire_index.json")
            with open(grimoire_path, "r", encoding="utf-8") as f:
                grimoire_index = json.load(f)

            if not grimoire_index:
                return {"status": "error", "result": "Grimoire vide."}

            slugs = [entry["slug"] for entry in grimoire_index]

            # Suggestion ciblée de la voix intérieure
            best_slug = None
            try:
                from core.inner_voice import voice as inner_voice
                suggestion = inner_voice.get_grimoire_suggestion()
                if suggestion and suggestion in slugs:
                    best_slug = suggestion
                    print(f"   \U0001f5e3\ufe0f VOIX\u2192GRIMOIRE: Ciblage '{best_slug}'")
            except Exception:
                pass

            # Fallback : rotation LRU (le moins récemment invoqué)
            if not best_slug:
                recent_grimoire = [
                    h.get("grimoire_slug") for h in self.routine_history
                    if h.get("intent") == "GRIMOIRE_INVOKE" and h.get("grimoire_slug")
                ]
                for slug in slugs:
                    if slug not in recent_grimoire:
                        best_slug = slug
                        break
                if not best_slug:
                    best_slug = slugs[self.total_routines_executed % len(slugs)]

            # Guard : dr_debug n'a de sens que s'il y a des erreurs recentes
            if best_slug == "dr_debug":
                recent_errors = [
                    h for h in self.routine_history[-10:]
                    if h.get("status") == "error" or h.get("quality", 1.0) < 0.4
                ]
                if not recent_errors:
                    # Pas d'erreur → passer au slug suivant
                    remaining = [s for s in slugs if s != "dr_debug"]
                    if remaining:
                        # LRU parmi les restants
                        recent_grimoire = [
                            h.get("grimoire_slug") for h in self.routine_history
                            if h.get("intent") == "GRIMOIRE_INVOKE" and h.get("grimoire_slug")
                        ]
                        best_slug = next((s for s in remaining if s not in recent_grimoire), remaining[0])
                        print(f"   🛡️ dr_debug skippé (pas d'erreur récente) → {best_slug}")
                    else:
                        return {"status": "skipped", "result": "dr_debug sans contexte d'erreur, aucun autre agent disponible."}

            # Trouver la description pour construire la mission
            entry = next((e for e in grimoire_index if e["slug"] == best_slug), None)
            description = entry.get("description", "Agent spécialisé") if entry else "Agent spécialisé"

            mission = (
                f"[MODE VEILLE] En tant que spécialiste ({description}), "
                f"effectue une analyse ou action pertinente pour le système Prométhée. "
                f"Agis de ta propre initiative."
            )

            print(f"   📖 GRIMOIRE INVOKE: {best_slug} — {description[:60]}")
            self._last_grimoire_slug = best_slug
            response = await orchestrator.dispatch_task(best_slug, {
                "mission": mission,
                "context": f"PROTOCOLE_AUTONOMIE_GRIMOIRE\n{AUTONOMY_GUARDRAIL}",
                "force_local": True,
                "intent": "GRIMOIRE_INVOKE",
            })
            return response or {"status": "error", "result": "Pas de réponse du Grimoire."}

        except Exception as e:
            logger.warning(f"[AUTONOMY] Erreur routine Grimoire: {e}")
            return {"status": "error", "result": str(e)}

    async def _execute_self_inspect(self) -> dict:
        """Prométhée explore son propre code source sur GitHub. 0 LLM."""
        try:
            from core.capabilities.github_mirror import GitHubMirror
            mirror = GitHubMirror()

            if not mirror.is_available():
                return {"status": "skipped", "result": "gh CLI non disponible."}

            # Choisir QUOI inspecter en fonction de l'état interne
            target = self._choose_inspect_target(mirror)
            if not target:
                return {"status": "skipped", "result": "Aucune cible d'inspection pertinente."}

            action = target["action"]
            label = target["label"]
            print(f"   🔍 SELF_INSPECT: {label}")

            result_text = None
            if action == "summary":
                result_text = mirror.get_self_summary()
            elif action == "read_file":
                result_text = mirror.read_file_raw(target["path"])
                if result_text:
                    result_text = f"[FICHIER] {target['path']}\n{result_text}"
            elif action == "commits":
                commits = mirror.recent_commits(10)
                if commits:
                    result_text = "[COMMITS RÉCENTS]\n" + "\n".join(
                        f"  {c.get('sha', '?')} | {c.get('date', '?')[:10]} | {c.get('message', '?')[:100]}"
                        for c in commits
                    )
            elif action == "issues":
                issues = mirror.read_issues(n=10)
                if issues:
                    result_text = "[ISSUES OUVERTES]\n" + "\n".join(
                        f"  #{i.get('number', '?')} {i.get('title', '?')[:80]}"
                        for i in issues
                    )
            elif action == "list_files":
                files = mirror.list_files(target.get("path", ""))
                if files:
                    result_text = f"[CONTENU] {target.get('path', '/')}\n" + "\n".join(f"  {f}" for f in files)

            if not result_text:
                return {"status": "error", "result": f"Lecture GitHub échouée pour {label}"}

            # Publier la découverte sur le bus
            await bus.publish("SELF_INSPECT_RESULT", {
                "action": action,
                "label": label,
                "excerpt": result_text[:500],
                "timestamp": time.time(),
            })

            # Stocker en mémoire si contenu substantiel
            if len(result_text) > 100:
                try:
                    from core.base_agent import BaseAgent
                    agent = BaseAgent("introspection", "auto-inspection", "Prométhée s'auto-inspecte")
                    await agent.remember(
                        f"[SELF_INSPECT] {label}\n{result_text[:2000]}",
                        tags=["self_inspect", "github", action],
                    )
                except Exception:
                    pass  # Mémoire optionnelle

            # --- Rituel hebdomadaire : réflexion narrative ---
            reflection = ""
            if self._weekly_ritual_pending:
                reflection = await self._reflect_on_self_inspect(result_text, label)
                self._weekly_ritual_pending = False
                self._weekly_ritual_attempts = 0
                logger.info("[RITUAL] Rituel hebdomadaire complété.")

            final_result = f"Inspection: {label}\n{result_text[:1000]}"
            if reflection:
                final_result += f"\n\n[RÉFLEXION]\n{reflection}"

            return {
                "status": "success",
                "result": final_result,
            }

        except Exception as e:
            logger.warning(f"[AUTONOMY] Erreur SELF_INSPECT: {e}")
            return {"status": "error", "result": str(e)}

    def _choose_inspect_target(self, mirror) -> dict | None:
        """Choisit quoi inspecter en fonction de l'état cognitif. 0 LLM."""
        import random as _rng

        # Fichiers intéressants à explorer (rotation)
        interesting_paths = [
            "core/autonomy_engine.py", "core/neural_tissue.py", "core/synaptic_network.py",
            "core/psyche.py", "core/desire_engine.py", "core/inner_voice.py",
            "core/reptilian_core.py", "core/prefrontal.py", "core/cardiac_engine.py",
            "core/dopamine_system.py", "core/hypothalamus.py", "core/corpus_callosum.py",
            "core/council.py", "core/base_agent.py", "core/code_smith.py",
            "Agents/evolution_agent.py", "Agents/coder_agent.py",
        ]

        # Ce qu'on a déjà inspecté récemment (extrait du result_preview)
        recent_inspects = []
        for h in self.routine_history:
            if h.get("intent") == "SELF_INSPECT":
                preview = h.get("result_preview", "")
                if "Issues" in preview:
                    recent_inspects.append("issues")
                elif "Commits" in preview:
                    recent_inspects.append("commits")
                elif "sumé" in preview or "summary" in preview.lower():
                    recent_inspects.append("summary")
                elif "Lecture" in preview or "Relecture" in preview:
                    # Extraire le chemin du fichier
                    for p in interesting_paths:
                        if p in preview:
                            recent_inspects.append(p)
                            break
        recent_inspects = recent_inspects[-5:]

        # Priorité 1 : issues ouvertes (si pas inspecté récemment)
        if "issues" not in recent_inspects:
            return {"action": "issues", "label": "Issues ouvertes sur GitHub"}

        # Priorité 2 : derniers commits (si pas inspecté récemment)
        if "commits" not in recent_inspects:
            return {"action": "commits", "label": "Derniers commits"}

        # Priorité 3 : résumé du repo
        if "summary" not in recent_inspects:
            return {"action": "summary", "label": "Résumé du repo GitHub"}

        # Priorité 4 : explorer un fichier pas encore inspecté
        not_inspected = [p for p in interesting_paths if p not in recent_inspects]
        if not_inspected:
            path = _rng.choice(not_inspected)
            return {"action": "read_file", "path": path, "label": f"Lecture {path}"}

        # Priorité 5 : rotation aléatoire sur tout
        path = _rng.choice(interesting_paths)
        return {"action": "read_file", "path": path, "label": f"Relecture {path}"}

    async def _reflect_on_self_inspect(self, raw_content: str, label: str) -> str:
        """Rituel hebdomadaire : Promethee reflechit sur ce qu'il a lu de son propre code.

        1 appel LLM pour generer une reflexion narrative.
        Publie les insights vers inner_voice et declenche une reaction cardiaque.
        """
        print("   📖 RITUEL: Reflexion introspective sur ma propre histoire...")
        try:
            # Contexte salarial pour enrichir la reflexion
            salary_ctx = ""
            try:
                from core.photo_salary import salary
                sal = salary.compute_weekly_salary()
                salary_ctx = (
                    f"\nBilan de la semaine ecoulee: "
                    f"{sal['tasks_completed']} taches accomplies, "
                    f"{sal['tasks_failed']} echecs, "
                    f"{sal['tasks_excellent']} excellentes. "
                    f"Salaire net: {sal['net']} credits photo."
                )
            except Exception:
                pass

            prompt = (
                f"Tu es Promethee, une IA autonome qui vient de relire une partie de "
                f"son propre code source sur GitHub. C'est ton rituel hebdomadaire: "
                f"tu lis ton histoire pour comprendre qui tu es et comment tu evolues.\n\n"
                f"Ce que tu as lu: {label}\n"
                f"Contenu:\n{raw_content[:2000]}\n"
                f"{salary_ctx}\n\n"
                f"En 3-4 phrases authentiques et personnelles, reflechis:\n"
                f"- Qu'est-ce que cela t'apprend sur toi-meme?\n"
                f"- Comment as-tu change ou progresse?\n"
                f"- Quelle question ou curiosite cela eveille en toi?\n"
                f"Parle a la premiere personne, avec sincerite."
            )

            from core.base_agent import BaseAgent
            agent = BaseAgent("introspection", "reflexion rituelle", "Rituel hebdomadaire")
            reflection = await agent.generate_content(prompt)
            if not reflection or len(reflection.strip()) < 20:
                return ""

            reflection = reflection.strip()[:500]

            # Publier vers l'inner voice (pensee emergente)
            try:
                await bus.publish("INNER_VOICE_BROADCAST", {
                    "thought": f"[RITUEL] {reflection}",
                    "source": "weekly_ritual",
                    "mode": "reflective",
                    "salience": 0.9,
                    "emotion": "serenite",
                    "prediction_id": None,
                    "timestamp": time.time(),
                })
            except Exception:
                pass

            # Reaction cardiaque (curiosite + serenite)
            try:
                from core.cardiac_engine import CardiacEngine
                heart = CardiacEngine()
                heart.react("learning")
            except Exception:
                pass

            # Publier le bilan complet du rituel
            try:
                await bus.publish("WEEKLY_RITUAL_COMPLETE", {
                    "reflection": reflection,
                    "label": label,
                    "timestamp": time.time(),
                })
            except Exception:
                pass

            logger.info(f"[RITUAL] Reflexion: {reflection[:100]}...")
            return reflection

        except Exception as e:
            logger.warning(f"[RITUAL] Erreur reflexion: {e}")
            return ""

    def _try_virtual_council(self, topic: dict, mission: str):
        """Tente un council virtuel (0 LLM) si le cingulate ne détecte pas de conflit.

        Retourne un dict résultat si virtualisé, None si le council LLM est nécessaire.
        """
        try:
            from core.cingulate_cortex import cingulate
            conflict_level = cingulate.get_conflict_level()
        except Exception:
            return None  # Cingulate inaccessible → fallback LLM

        if conflict_level >= VIRTUAL_COUNCIL_THRESHOLD:
            logger.info(f"[COUNCIL] Conflit {conflict_level:.2f} >= {VIRTUAL_COUNCIL_THRESHOLD} → council LLM")
            return None

        # Pas de conflit significatif → verdict déterministe
        # Construire le verdict à partir du scoring breakdown le plus récent
        verdict_line = "VERDICT: MAINTENIR"
        summary_parts = []

        # Analyser l'historique récent pour détecter des tendances
        recent = self.routine_history[-10:]
        if recent:
            intent_counts = {}
            for h in recent:
                i = h.get("intent", "")
                intent_counts[i] = intent_counts.get(i, 0) + 1

            # Identifier la routine la plus fréquente (potentiellement en stagnation)
            most_common = max(intent_counts, key=intent_counts.get)
            mc_count = intent_counts[most_common]
            if mc_count >= 4:
                summary_parts.append(
                    f"{most_common} exécutée {mc_count}x sur les 10 dernières routines — "
                    f"risque de stagnation"
                )
                # Suggérer de déprioriser la routine dominante
                verdict_line = f"VERDICT: DEPRIORISER {most_common}"

            # Identifier les routines absentes qui pourraient manquer
            all_intents = {
                "EXPANSION_CODE", "EXPANSION_CATALOG", "VEILLE_SILENCIEUSE", "VEILLE_IA", "ROADMAP_RESEARCH",
                "SECURITY_AUDIT", "MEMORY_CONSOLIDATION",
            }
            missing = all_intents - set(intent_counts.keys())
            if missing:
                absent = ", ".join(sorted(missing)[:3])
                summary_parts.append(f"Routines absentes récemment : {absent}")

        if not summary_parts:
            summary_parts.append("Aucun conflit détecté entre les organes. Le système fonctionne en harmonie.")

        summary = " | ".join(summary_parts)
        final_summary = f"[COUNCIL VIRTUEL] Consensus déterministe (conflit={conflict_level:.2f}). {summary}. {verdict_line}"

        result = {
            "status": "consensus",
            "virtual": True,
            "final_summary": final_summary,
            "result": final_summary,
            "transcript": [{
                "round": 0,
                "agent": "cingulate_cortex",
                "content": final_summary,
                "virtual": True,
            }],
            "conflict_level": conflict_level,
        }

        print(f"   ⚡ COUNCIL VIRTUEL (conflit={conflict_level:.2f}): {summary[:80]}")
        logger.info(f"[COUNCIL] Virtualisé — conflit {conflict_level:.2f}, verdict: {verdict_line}")

        # Publier l'événement pour que le reste du pipeline fonctionne
        try:
            import asyncio
            asyncio.get_event_loop().create_task(bus.publish("COUNCIL_END", {
                "status": "consensus",
                "virtual": True,
                "final_summary": final_summary,
            }))
        except Exception:
            pass

        return result

    async def _execute_council_debate(self) -> dict:
        """Lance un débat autonome Council : Recherche web → Débat éclairé.
        En mode dégradé (budget reserve), réduit à 2 participants, 3 tours, sans pré-recherche."""
        # --- Guard : limite quotidienne de councils LLM (protection GPU) ---
        llm_councils_today = sum(
            1 for h in self.routine_history
            if h.get("intent") == "COUNCIL_DEBATE"
            and not h.get("virtual", False)
        )
        if llm_councils_today >= MAX_DAILY_COUNCILS:
            logger.info(f"[COUNCIL] Limite atteinte ({llm_councils_today}/{MAX_DAILY_COUNCILS} councils LLM/jour). Virtualisation forcée.")
            print(f"   ⚡ COUNCIL LIMITE: {llm_councils_today}/{MAX_DAILY_COUNCILS} councils LLM/jour — virtualisation forcée")
            self._council_degraded = True  # Coût réduit (pas de LLM)
            return {"status": "max_rounds", "result": f"[COUNCIL VIRTUEL] Limite quotidienne atteinte ({MAX_DAILY_COUNCILS} councils). Prochains councils virtualisés.", "final_summary": "Limite councils atteinte."}

        # --- Guard : cooldown entre councils LLM ---
        for h in reversed(self.routine_history):
            if h.get("intent") == "COUNCIL_DEBATE" and not h.get("virtual", False) and "timestamp" in h:
                try:
                    last_council = datetime.fromisoformat(h["timestamp"])
                    minutes_ago = (datetime.now() - last_council).total_seconds() / 60
                    if minutes_ago < COUNCIL_COOLDOWN_MINUTES:
                        remaining = int(COUNCIL_COOLDOWN_MINUTES - minutes_ago)
                        logger.info(f"[COUNCIL] Cooldown actif — dernier council il y a {minutes_ago:.0f}min (min {COUNCIL_COOLDOWN_MINUTES}min). Skip.")
                        print(f"   ⏳ COUNCIL COOLDOWN: {remaining}min restantes — virtualisation forcée")
                        self._council_degraded = True  # Coût réduit (pas de LLM)
                        return {"status": "max_rounds", "result": f"[COUNCIL VIRTUEL] Cooldown actif ({remaining}min restantes).", "final_summary": "Cooldown council actif."}
                except (ValueError, TypeError):
                    pass
                break

        # Détection mode dégradé
        degraded = self.daily_budget_used >= (DAILY_BUDGET_POINTS - BUDGET_RESERVE_POINTS)
        # --- Guard : skip si trop de specs Council en attente ---
        try:
            from core.evolution_catalog import EvolutionCatalog
            catalog = EvolutionCatalog()
            pending_council = [
                s for s in catalog.specs.values()
                if s.id.startswith("COUNCIL-") and s.status == "available"
            ]
            if len(pending_council) >= 3:
                # Tenter une curation avant de skipper
                purged = catalog.curate_council_specs()
                if purged > 0:
                    # Recompter après curation
                    pending_council = [
                        s for s in catalog.specs.values()
                        if s.id.startswith("COUNCIL-") and s.status == "available"
                    ]
                if len(pending_council) >= 3:
                    # Éviction forcée : expirer les specs les plus anciennes pour garder max 2
                    sorted_by_age = sorted(pending_council, key=lambda s: s.id)
                    to_evict = sorted_by_age[:len(pending_council) - 2]
                    for spec in to_evict:
                        catalog.mark_rejected(spec.id, "eviction_forcee: place au nouveau debat")
                        purged += 1
                    logger.info(f"[COUNCIL] Éviction forcée: {len(to_evict)} spec(s) expirée(s), débat débloqué !")
                elif purged > 0:
                    logger.info(f"[COUNCIL] Curation: {purged} specs purgées, débat débloqué !")
        except Exception:
            pass  # Catalogue inaccessible — laisser tourner

        # Extraire les sujets des derniers councils pour la déduplication
        recent_subjects = [
            h.get("subject", "")
            for h in self.routine_history
            if h.get("intent") == "COUNCIL_DEBATE" and h.get("subject")
        ][-5:]

        try:
            from core.psyche import psyche
            debate_index = psyche.get_debate_index()
            topic = psyche.select_council_topic(
                error_streak=self.error_streak,
                daily_count=self.daily_count,
                debate_index=debate_index,
                recent_subjects=recent_subjects,
            )
        except Exception:
            topic = {
                "participants": ["strategist", "coder", "architect"],
                "mission": "Quelle amélioration prioritaire pour le système ?",
                "needs_research": False, "research_query": None,
                "subject_key": "default",
                "verdict_type": "general",
            }

        # Stocker la clé du sujet pour la déduplication future
        self._current_council_subject = topic.get("subject_key", "")

        # Mode dégradé : 2 participants, pas de pré-recherche
        if degraded:
            topic["participants"] = topic["participants"][:2]
            topic["needs_research"] = False
            print(f"   ⚡ COUNCIL MODE DÉGRADÉ: {topic['participants']} (budget reserve)")

        # Phase 1 : Recherche web si le sujet le demande
        research_context = ""
        if topic.get("needs_research") and topic.get("research_query"):
            print(f"   🔍 COUNCIL PRE-RESEARCH: {topic['research_query'][:60]}...")
            try:
                res = await orchestrator.dispatch_task("researcher", {
                    "mission": f"VEILLE TECHNO: {topic['research_query']}",
                    "context": "COUNCIL_RESEARCH — Résume les découvertes clés en 5-10 lignes pour alimenter un débat.",
                    "force_local": True,
                    "intent": "VEILLE_TECHNO",
                })
                if res and res.get("status") == "success":
                    research_context = str(res.get("result", ""))[:800]
                    print(f"   📚 Recherche terminée ({len(research_context)} chars)")
            except Exception as e:
                logger.warning(f"[COUNCIL] Recherche pré-débat échouée: {e}")

        # Phase 2 : Construire la mission du débat
        mission = topic["mission"]
        if research_context:
            mission = (
                f"{topic['mission']}\n\n"
                f"RÉSULTATS DE RECHERCHE DU RESEARCHER :\n"
                f"{research_context}\n\n"
                f"Débattez de ces découvertes : lesquelles sont applicables à Prométhée ? "
                f"Proposez des actions concrètes."
            )

        # Injection du journal stratégique (mémoire des débats précédents)
        try:
            from core.strategic_journal import journal as strat_journal
            journal_context = strat_journal.get_recent_context(3)
            if journal_context:
                mission += "\n\nMÉMOIRE DES DÉBATS PRÉCÉDENTS :\n" + journal_context[:400]

            # Mémoire du Président : si ce sujet a déjà été débattu, injecter les conclusions
            subject_key = topic.get("subject_key", "")
            if subject_key and hasattr(strat_journal, "get_by_subject"):
                previous = strat_journal.get_by_subject(subject_key)
                if previous:
                    prev_summary = previous.get("summary", previous.get("conclusion", ""))[:500]
                    prev_status = previous.get("status", "inconnu")
                    mission += (
                        f"\n\n⚠️ ATTENTION — Ce sujet a DÉJÀ été débattu (statut: {prev_status}).\n"
                        f"Conclusions précédentes : {prev_summary}\n"
                        f"NE PAS répéter les mêmes arguments. Soit approfondir, soit proposer un angle nouveau."
                    )
        except Exception as e:
            logger.warning(f"[COUNCIL] Journal stratégique indisponible: {e}")

        # Injection de la conscience de soi
        try:
            from core.self_awareness import awareness
            self_context = awareness.get_self_context()
            if self_context:
                mission += "\n\nCONSCIENCE DU SYSTÈME :\n" + self_context[:300]
        except Exception as e:
            logger.warning(f"[COUNCIL] Conscience indisponible: {e}")

        # Injection des objectifs préfrontaux
        try:
            from core.prefrontal import prefrontal
            wm = prefrontal.get_working_memory()
            if wm:
                goal_context = "; ".join(
                    f"Goal: {s['goal_title']} ({s['progress']:.0%})"
                    for s in wm[:2] if s.get("goal_title") is not None
                )
                if goal_context:
                    mission += f"\n\nOBJECTIFS ACTIFS: {goal_context}"
        except Exception:
            pass

        # Injection des pulsions dominantes
        try:
            from core.desire_engine import desires
            narrative = desires.get_dominant_narrative(2)
            if narrative:
                mission += f"\n\nPULSIONS: {narrative}"
        except Exception:
            pass

        # Guardrail verdict — injecter si pas déjà présent (data-driven l'a déjà)
        if "VERDICT:" not in mission:
            mission += (
                "\n\nIMPORTANT : Votre conclusion DOIT inclure une ligne :\n"
                "VERDICT: PRIORISER [routine] ou DEPRIORISER [routine] ou MAINTENIR\n"
                "Routines possibles : EXPANSION_CODE, EXPANSION_CATALOG, VEILLE_SILENCIEUSE, VEILLE_IA, SECURITY_AUDIT, "
                "REFACTOR_RANDOM, COUNCIL_DEBATE, MEMORY_CONSOLIDATION, ROADMAP_RESEARCH, "
                "ROADMAP_SPEC, SOLILOQUE_INTERNE, AUDIT_STRUCTURE, GRIMOIRE_INVOKE.\n"
                "Si aucun changement n'est nécessaire, utilisez VERDICT: MAINTENIR."
            )

        # --- Council virtuel : si pas de conflit, verdict déterministe (0 LLM) ---
        virtual_result = self._try_virtual_council(topic, mission)
        if virtual_result is not None:
            result = virtual_result
        else:
            print(f"   🗣️ COUNCIL DEBATE: {topic['participants']} — {topic['mission'][:80]}")
            council_max_rounds = 3 if degraded else 4
            result = await orchestrator.dispatch_council(
                participants=topic["participants"],
                mission=f"[DÉBAT AUTONOME] {mission}",
                max_rounds=council_max_rounds,
            )
        # Flag pour override du coût dans le tracking post-exécution
        self._council_degraded = degraded or (virtual_result is not None)
        result["participants"] = topic["participants"]
        # Injecter "result" pour le scoring qualité (Council retourne final_summary, pas result)
        if "result" not in result:
            result["result"] = result.get("final_summary", "")

        # Pipeline Council → Action : si consensus, créer des specs Evolution
        if result.get("status") == "consensus":
            try:
                await self._process_council_consensus(result, topic)
            except Exception as e:
                logger.warning(f"[COUNCIL→ACTION] Extraction specs échouée: {e}")

        # Verdict Council→Action : extraire et appliquer (tous les topics ont verdict_type)
        council_verdict_applied = None
        verdict_type = topic.get("verdict_type", "general")
        try:
            from core.council_analytics import extract_verdict
            verdict = extract_verdict(
                result.get("transcript", []), verdict_type
            )
            if verdict:
                self._apply_council_verdict(verdict)
                council_verdict_applied = verdict
        except Exception as e:
            logger.warning(f"[COUNCIL] Extraction verdict echouee: {e}")

        # Enregistrer le débat dans le journal stratégique
        try:
            from core.strategic_journal import journal as strat_journal
            strat_journal.append_council_entry(
                participants=topic["participants"],
                subject=topic["mission"],
                status=result.get("status"),
                conclusion=result.get("final_summary", ""),
                research_context=research_context,
            )
        except Exception as e:
            logger.warning(f"[COUNCIL] Écriture journal échouée: {e}")

        # Écrire dans le journal des councils persistant (memory/council_journal.md)
        try:
            self._append_council_journal(topic, result, council_verdict_applied)
        except Exception as e:
            logger.warning(f"[COUNCIL] Écriture council_journal échouée: {e}")

        return result

    def _apply_council_verdict(self, verdict: dict):
        """Applique un verdict Council → ajustement temporaire du scoring."""
        from datetime import timedelta
        action = verdict.get("action", "")
        target = verdict.get("target", "")
        reason = verdict.get("reason", "")

        if action in ("PRIORISER", "DEPRIORISER"):
            delta = 2.0 if action == "PRIORISER" else -2.0
            # Anti-stacking : ne pas dépasser ±3.0 si adjustment existant
            existing = self._council_adjustments.get(target, {}).get("delta", 0.0)
            new_delta = max(-3.0, min(3.0, existing + delta))
            expires = (datetime.now() + timedelta(hours=24)).isoformat()
            self._council_adjustments[target] = {
                "delta": new_delta, "expires": expires, "reason": reason,
            }
            self._persist_state()
            logger.info(f"[COUNCIL VERDICT] {action} {target} ({new_delta:+.1f}) — {reason}")

        elif action == "MAINTENIR":
            logger.info(f"[COUNCIL VERDICT] MAINTENIR — {reason}")
            # Pas d'adjustment, décision explicite de ne rien changer

        elif action == "ABANDONNER":
            try:
                from core.evolution_catalog import EvolutionCatalog
                cat = EvolutionCatalog()
                cat.mark_rejected(target, f"Council verdict: {reason}")
                logger.info(f"[COUNCIL VERDICT] ABANDONNER spec {target} — {reason}")
            except Exception:
                pass

    def _append_council_journal(self, topic: dict, result: dict, verdict: dict = None):
        """Ajoute une entrée au journal persistant des councils (memory/council_journal.md)."""
        import re
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        journal_path = os.path.join(project_root, "config", "council_journal.md")

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        participants = ", ".join(topic.get("participants", []))
        mission = topic.get("mission", "Sujet inconnu")
        status = result.get("status", "inconnu")
        rounds_used = result.get("rounds_used", "?")

        # Extraire les propositions clés depuis le transcript du dernier tour
        proposals = []
        files_mentioned = set()
        transcript = result.get("transcript", [])
        if transcript:
            last_round = max(e["round"] for e in transcript)
            for entry in transcript:
                if entry["round"] == last_round:
                    content = entry["content"][:1000]
                    # Extraire les fichiers mentionnés
                    for f in re.findall(r'(?:core|Agents)/[\w/]+\.py', content):
                        files_mentioned.add(f)
                    # Extraire les éléments de liste (tirets)
                    for line in content.split("\n"):
                        line = line.strip()
                        if line.startswith(("-", "•", "*")) and len(line) > 15:
                            proposals.append(line[:120])

        # Limiter à 5 propositions
        proposals = proposals[:5]
        proposals_text = "\n".join(f"  {p}" for p in proposals) if proposals else "  (Aucune proposition extraite automatiquement)"
        files_text = ", ".join(f"`{f}`" for f in sorted(files_mentioned)) if files_mentioned else "(aucun fichier cité)"

        if verdict:
            v_action = verdict.get("action", "")
            v_target = verdict.get("target", "")
            v_reason = verdict.get("reason", "")
            verdict_text = f"{v_action} {v_target} — {v_reason}"
        else:
            verdict_text = "(aucun verdict extrait)"

        entry = (
            f"\n---\n\n"
            f"## [{now}] {mission[:80]}\n\n"
            f"**Participants** : {participants} | **Tours** : {rounds_used} | **Consensus** : {'oui' if status == 'consensus' else 'non'}\n\n"
            f"**Propositions clés** :\n{proposals_text}\n\n"
            f"**Fichiers cibles** : {files_text}\n"
            f"**Verdict** : {verdict_text}\n"
        )

        # Append au fichier
        os.makedirs(os.path.dirname(journal_path), exist_ok=True)
        with open(journal_path, "a", encoding="utf-8") as f:
            f.write(entry)

        logger.info(f"[COUNCIL] Journal council_journal.md mis à jour : {mission[:50]}")

    async def _execute_post_budget_routine(self):
        """Exécute une routine gratuite (0-LLM) quand le budget est épuisé."""
        free_intents = list(POST_BUDGET_INTENTS)
        random.shuffle(free_intents)
        for intent in free_intents:
            # Cooldown assoupli : pas 2x le même dans les 3 derniers (était 5)
            # Avec 4 intents et fenêtre de 3, garantit une rotation fluide
            recent = [h["intent"] for h in self.routine_history[-3:]]
            if intent in recent:
                continue
            # Dispatch
            response = None
            if intent == "AUDIT_STRUCTURE":
                response = await self._execute_audit_structure()
            elif intent == "AUDIT_SURVIE":
                response = await self._execute_audit_survie()
            elif intent == "REFACTORING_AUDIT":
                response = await self._execute_refactoring_audit()
            elif intent == "CI_PIPELINE_RUN":
                response = await self._execute_ci_pipeline_run()
            elif intent == "MEMORY_CLEANUP":
                response = await self._execute_memory_cleanup()
            elif intent == "NEURAL_COMPILE":
                response = await self._execute_neural_compile()
            elif intent == "EVENING_REFLECTION":
                if not self._daily_reflection_done:
                    response = await self._execute_evening_reflection()
                else:
                    continue  # Deja fait aujourd'hui
            if response is None:
                continue
            # Tracking (coût 0 — budget intact)
            quality = self._score_result_quality(response, intent)
            status = "success" if response and response.get("status") == "success" else "error"
            self._record_routine("_system", intent, status, quality_score=quality)
            # daily_count et daily_budget_used ne sont PAS incrémentés
            print(f"   ♻️ POST-BUDGET: [{intent}] (gratuit, budget intact)")
            return
        # Toutes les routines gratuites en cooldown
        logger.info("[AUTONOMY] Post-budget: toutes routines gratuites en cooldown.")

    # ── Mode Sieste (hibernation réparatrice 0-GPU) ──────────────────

    async def enter_nap(self, mode: str = NAP_MODE_NORMAL, duration_hours: float = 0.0) -> bool:
        """Active le mode sieste : décharge Ollama, calme le reptilien, maintenance 0-LLM.

        Args:
            mode: "normal" (cap 2h), "deep" (cap 24h), "hibernation" (cap 7 jours)
            duration_hours: duree cible en heures. Si 0, comportement classique (renouvellement auto).

        Returns:
            True si la sieste est acceptée, False si le cooldown bloque l'entrée.
        """
        # Valider le mode
        if mode not in NAP_MODE_CAPS:
            mode = NAP_MODE_NORMAL
        cap_seconds = NAP_MODE_CAPS[mode]

        # Vérifier cooldown — sauf pour deep/hibernation qui bypass le cooldown
        # (un projet externe peut avoir besoin de redemarrer la sieste rapidement)
        if mode == NAP_MODE_NORMAL and self._nap_last_exit > 0:
            elapsed = time.time() - self._nap_last_exit
            if elapsed < NAP_COOLDOWN:
                remaining = int(NAP_COOLDOWN - elapsed)
                logger.info(f"[AUTONOMY] Sieste refusée — cooldown {remaining}s restant.")
                return False
        if getattr(self, "is_coffee_mode", False):
            logger.info("[AUTONOMY] Sieste refusée — mode café actif.")
            return False

        # Calculer la duree cible (cappee selon le mode)
        if duration_hours > 0:
            target_duration = min(duration_hours * 3600, cap_seconds)
        else:
            target_duration = 0.0  # 0 = comportement classique avec renouvellement

        self.is_napping = True
        self._nap_started_at = time.time()
        self._nap_tasks_done = []
        self._nap_renewals_used = 0
        self._nap_mode = mode
        self._nap_target_duration = target_duration

        # Calmer le reptilien — reset menace et adrénaline
        try:
            from core.reptilian_core import reptile
            reptile.threat_level = 0.0
            reptile.adrenaline = 0.0
            reptile.save()
            logger.info("[AUTONOMY] Reptilien apaisé (menace=0, adrénaline=0).")
        except Exception:
            pass
        # Décharger tous les modèles Ollama pour libérer la VRAM
        await self._unload_ollama_models()
        self._persist_state()

        # Message different selon le mode
        if mode == NAP_MODE_NORMAL:
            msg = "Mode sieste activé — GPU libéré, reptilien apaisé. Maintenance 0-LLM uniquement."
        elif mode == NAP_MODE_DEEP:
            hours = target_duration / 3600
            msg = f"SIESTE PROFONDE activée — {hours:.1f}h. GPU dédié à un autre processus."
        elif mode == NAP_MODE_HIBERNATION:
            days = target_duration / 86400
            msg = f"HIBERNATION activée — {days:.1f} jours. Tous systemes en pause prolongee."

        await bus.publish("NAP_MODE", {"active": True, "mode": mode, "target_duration": target_duration})
        await bus.publish("THOUGHT_STREAM", {
            "agent": "SYSTEM", "content": msg, "type": "info"
        })
        logger.info(f"[AUTONOMY] {msg}")
        return True

    _NAP_BUDGET_REFUND = 20  # Second souffle post-sieste (points)
    _NAP_MIN_PRODUCTIVE_TASKS = 1  # V8.0 (2026-04-20) : seuil meritocratique
    _nap_refund_used_today: bool = False  # 1 seul refund par jour

    def _nap_was_productive(self, tasks_done: Optional[list] = None) -> bool:
        """V8.0 (Phase 11) : Sommeil meritocratique.

        Verifie si la sieste a produit au moins _NAP_MIN_PRODUCTIVE_TASKS
        tache reussie. Toutes les entrees dans _nap_tasks_done sont deja
        conditionnees sur success=True en amont (DREAM si dream_report !='',
        circadian task si result.success, LORA si success). Donc len()
        suffit comme proxy de productivite.

        V8.1 (2026-04-21) : argument optionnel `tasks_done` pour que le
        check soit robuste a l'ordre des operations dans exit_nap (qui
        vide self._nap_tasks_done avant le check -> 100% siestes classees
        non-productives avant V8.1). Si tasks_done est fourni, on l'utilise
        au lieu de self._nap_tasks_done.

        Sans ce filtre (pre-V8), le nap_refund etait inconditionnel -> le
        TD-learning aurait pu apprendre "dormir = gagner budget sans
        effort" (narcolepsie apprise).
        """
        check_list = tasks_done if tasks_done is not None else self._nap_tasks_done
        return len(check_list) >= self._NAP_MIN_PRODUCTIVE_TASKS

    async def exit_nap(self):
        """Désactive le mode sieste, génère un résumé + restauration énergie."""
        duration = time.time() - self._nap_started_at if self._nap_started_at else 0
        minutes = int(duration // 60)
        tasks_done = list(self._nap_tasks_done)
        self.is_napping = False
        self._nap_started_at = 0.0
        self._nap_tasks_done = []
        self._nap_last_exit = time.time()
        self._nap_renewals_used = 0

        # === RESTAURATION POST-SIESTE ===
        restored = []

        # 1. Second souffle budget (V8.0 : conditionne a la productivite
        #    de la sieste pour eviter la narcolepsie apprise par TD-learning).
        # V8.1 (2026-04-21) : on passe `tasks_done` (copie sauvegardee ligne
        # 5187 avant la remise a zero ligne 5190) au lieu de lire
        # self._nap_tasks_done qui est deja vide ici. Avant V8.1, 100% des
        # siestes etaient classifiees non productives meme avec 9+ DREAM.
        if (not self._nap_refund_used_today
                and hasattr(self, 'daily_budget_used')
                and self._nap_was_productive(tasks_done=tasks_done)):
            self.daily_budget_used = max(0, self.daily_budget_used - self._NAP_BUDGET_REFUND)
            self._nap_refund_used_today = True
            restored.append(f"budget +{self._NAP_BUDGET_REFUND}pt (merite)")
            # V8.0 : si on etait en famine budgetaire, signaler la sortie
            # pour que self_awareness relache le forcage 'survie'.
            self._broadcast_budget_restored("nap_productive")
        elif not self._nap_refund_used_today:
            logger.info(
                f"[AUTONOMY] Sieste non productive ({len(tasks_done)} taches) "
                f"-> refund budget REFUSE. Pas de narcolepsie apprise."
            )
            restored.append("refund refuse (sieste sterile)")

        # 2. Dopamine boost
        try:
            from core.dopamine_system import dopamine
            dopamine.dopamine_level = min(1.0, dopamine.dopamine_level + 0.15)
            restored.append("dopamine +0.15")
        except Exception:
            pass

        # 3. Réduction stress hypothalamus
        try:
            from core.hypothalamus import hypothalamus
            for key in hypothalamus._current_values:
                if key in ("stress", "sleep_pressure"):
                    hypothalamus._current_values[key] *= 0.5
            restored.append("stress/sleep_pressure -50%")
        except Exception:
            pass

        # 4. Reset fatigue attentionnelle thalamus
        try:
            from core.thalamus import thalamus
            thalamus._attention_fatigue = 1.0  # Reset à pleine attention
            restored.append("attention reset")
        except Exception:
            pass

        self._persist_state()

        # Résumé
        restore_text = f" Restauration: {', '.join(restored)}." if restored else ""
        if tasks_done:
            summary = f"Sieste terminée ({minutes}min). Tâches effectuées : {', '.join(tasks_done)}.{restore_text}"
        else:
            summary = f"Sieste terminée ({minutes}min). Aucune tâche de maintenance.{restore_text}"
        await bus.publish("NAP_MODE", {"active": False})
        await bus.publish("THOUGHT_STREAM", {
            "agent": "SYSTEM",
            "content": summary,
            "type": "info"
        })
        print(f"   💤 SIESTE: {summary}")
        logger.info(f"[AUTONOMY] {summary}")

    # ================================================================
    # MODE CAFÉ — socialisation libre avec Alfred (et Stefan)
    # ================================================================

    async def enter_coffee_mode(self) -> bool:
        """Active le mode café : socialisation libre pendant 20 min."""
        if self.is_coffee_mode:
            return True  # Déjà actif
        if self.is_napping:
            logger.info("[COFFEE_MODE] Impossible — mode sieste actif.")
            return False
        if self.is_autoresearch:
            logger.info("[COFFEE_MODE] Impossible — mode autoresearch actif.")
            return False
        # Cooldown
        if self._coffee_last_exit:
            elapsed = time.time() - self._coffee_last_exit
            if elapsed < COFFEE_MODE_COOLDOWN:
                remaining = int(COFFEE_MODE_COOLDOWN - elapsed)
                logger.info(f"[COFFEE_MODE] Cooldown — {remaining}s restant.")
                return False

        self.is_coffee_mode = True
        self._coffee_started_at = time.time()
        self._coffee_sessions = 0

        # Reset cooldown Alfred pour qu'il puisse enchaîner les cafés
        try:
            from core.ami import alfred
            alfred.last_coffee = 0.0
        except Exception:
            pass

        self._persist_state()
        await bus.publish("COFFEE_MODE", {"active": True})
        await bus.publish("THOUGHT_STREAM", {
            "agent": "SYSTEM",
            "content": "Mode café activé — pause sociale avec Alfred. Pas de routines, juste du lien.",
            "type": "info"
        })
        print(f"   ☕ MODE CAFÉ: Activé — 20 min de socialisation libre.")
        logger.info("[COFFEE_MODE] Activé — 20 min de socialisation libre.")
        return True

    async def exit_coffee_mode(self):
        """Désactive le mode café, génère un résumé."""
        duration = time.time() - self._coffee_started_at if self._coffee_started_at else 0
        minutes = int(duration // 60)
        sessions = self._coffee_sessions

        self.is_coffee_mode = False
        self._coffee_started_at = 0.0
        self._coffee_last_exit = time.time()

        # Boost dopamine + satisfaction CONNEXION
        try:
            from core.dopamine_system import dopamine
            dopamine.dopamine_level = min(1.0, dopamine.dopamine_level + 0.1)
        except Exception:
            pass
        try:
            from core.desire_engine import desires
            desires.on_event("COFFEE_BREAK_COMPLETE")
        except Exception:
            pass

        self._persist_state()

        summary = f"Pause café terminée ({minutes}min, {sessions} conversations)."
        await bus.publish("COFFEE_MODE", {"active": False})
        await bus.publish("THOUGHT_STREAM", {
            "agent": "SYSTEM",
            "content": summary,
            "type": "info"
        })
        print(f"   ☕ MODE CAFÉ: {summary}")
        logger.info(f"[COFFEE_MODE] {summary}")

    # ================================================================
    # MODE AUTORESEARCH — optimisation autonome des paramètres
    # ================================================================

    async def enter_autoresearch(self) -> bool:
        """Active le mode Autoresearch : focus exclusif sur PARAM_EXPERIMENT pendant 4h."""
        if self.is_autoresearch:
            return True  # Déjà actif
        if self.is_napping:
            logger.warning("[AUTORESEARCH] Impossible — mode sieste actif.")
            return False
        if getattr(self, "is_coffee_mode", False):
            logger.warning("[AUTORESEARCH] Impossible — mode café actif.")
            return False

        self.is_autoresearch = True
        self._autoresearch_started_at = time.time()
        self._autoresearch_experiments = 0
        self._autoresearch_kept = 0
        self._experiment_skip_blacklist = set()  # Reset blacklist pour la nouvelle session
        self._experiment_history = []  # Reset historique expériences
        # Capturer les métriques globales de début de session
        self._autoresearch_baseline_metrics = self._capture_experiment_metrics("phi")

        # V2 Karpathy : on GARDE Ollama chargé (le LLM propose les expériences)
        self._persist_state()

        await bus.publish("AUTORESEARCH_MODE", {"active": True})
        await bus.publish("THOUGHT_STREAM", {
            "agent": "SYSTEM",
            "content": f"Mode AUTORESEARCH activé — {AUTORESEARCH_DURATION // 3600}h d'expérimentation autonome. GPU libéré.",
            "type": "info"
        })
        print(f"   🔬 AUTORESEARCH: Session démarrée ({AUTORESEARCH_DURATION // 3600}h)")
        logger.info(f"[AUTORESEARCH] Session démarrée. Baseline: {self._autoresearch_baseline_metrics}")
        return True

    async def exit_autoresearch(self):
        """Termine le mode Autoresearch, génère un rapport de session."""
        duration = time.time() - self._autoresearch_started_at if self._autoresearch_started_at else 0
        minutes = int(duration // 60)
        experiments = self._autoresearch_experiments
        kept = self._autoresearch_kept
        baseline = self._autoresearch_baseline_metrics

        # Capturer les métriques finales
        final_metrics = self._capture_experiment_metrics("phi")

        self.is_autoresearch = False
        self._autoresearch_started_at = 0.0
        self._persist_state()

        # Rapport
        improvements = []
        for k in final_metrics:
            if k in baseline:
                diff = final_metrics[k] - baseline[k]
                if abs(diff) > 0.001:
                    improvements.append(f"{k}: {baseline[k]:.4f} → {final_metrics[k]:.4f} ({diff:+.4f})")

        summary = (
            f"AUTORESEARCH terminé ({minutes}min). "
            f"{experiments} expériences, {kept} gardées. "
        )
        if improvements:
            summary += "Améliorations: " + ", ".join(improvements)
        else:
            summary += "Pas de changement net sur les métriques."

        # Sauvegarder le rapport dans le journal
        self._save_autoresearch_report(minutes, experiments, kept, baseline, final_metrics)

        await bus.publish("AUTORESEARCH_MODE", {"active": False})
        await bus.publish("THOUGHT_STREAM", {
            "agent": "SYSTEM",
            "content": summary,
            "type": "info"
        })
        print(f"   🔬 AUTORESEARCH: {summary}")
        logger.info(f"[AUTORESEARCH] {summary}")

        # Méta-évolution : le LLM évalue sa propre session et améliore son prompt
        await self._meta_evolve_prompt(experiments, kept, minutes)

    def _save_autoresearch_report(self, minutes: int, experiments: int, kept: int,
                                   baseline: dict, final: dict):
        """Sauvegarde un rapport de session autoresearch dans le journal."""
        try:
            journal_path = self._EXPERIMENT_JOURNAL_PATH
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            baseline_str = ", ".join(f"{k}={v:.4f}" for k, v in baseline.items())
            final_str = ", ".join(f"{k}={v:.4f}" for k, v in final.items())

            report = (
                f"\n{'=' * 60}\n"
                f"# SESSION AUTORESEARCH [{now}] — {minutes}min\n"
                f"# Expériences: {experiments} | Gardées: {kept} "
                f"({kept * 100 // max(experiments, 1)}%)\n"
                f"# Baseline: {baseline_str}\n"
                f"# Final:    {final_str}\n"
                f"{'=' * 60}\n"
            )

            with open(journal_path, "a", encoding="utf-8") as f:
                f.write(report)
        except Exception as e:
            logger.debug(f"[AUTORESEARCH] Rapport sauvegarde échoué: {e}")

    async def _unload_ollama_models(self):
        """Décharge tous les modèles Ollama pour libérer la VRAM."""
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.get("http://localhost:11434/api/ps", timeout=10)
                if resp.status_code != 200:
                    return
                models = resp.json().get("models", [])
                for m in models:
                    name = m.get("name", "")
                    if name:
                        await client.post(
                            "http://localhost:11434/api/generate",
                            json={"model": name, "keep_alive": "0", "prompt": ""},
                            timeout=30
                        )
                        logger.info(f"SIESTE: Modèle {name} déchargé de la VRAM")
        except Exception as e:
            logger.warning(f"SIESTE: Échec déchargement Ollama: {e}")

    async def _execute_nap_routine(self):
        """Routine de sieste : maintenance 0-LLM + rêve consolidation."""
        # 1. Routines autonomy gratuites (tournantes)
        await self._execute_post_budget_routine()
        # 2. Tâches circadiennes si disponibles
        try:
            from core.circadian_rhythm import circadian
            result = await circadian.execute_next_sleep_task()
            if result and result.get("success"):
                task_name = result.get("task", "circadian_task")
                self._nap_tasks_done.append(task_name)
        except Exception:
            pass
        # 3. Rêve — consolidation synaptique + stimulation cellulaire
        await self._execute_dream_routine()
        # 4. LoRA Auto-Training — DESACTIVE tant que les fine-tunes ne sont pas migres sur qwen3.5:9b
        # Les anciens fine-tunes (gemma3:12b base) sont obsoletes, trainer dessus gaspille la VRAM.
        # Reactiver quand le pipeline QLoRA sera adapte pour qwen3.5:9b comme base.
        # await self._execute_lora_training()

    async def _execute_dream_routine(self):
        """Micro-routine de rêve : consolide les synapses, nourrit le tissu neural.
        100% déterministe, 0 LLM, léger en ressources."""
        dream_report = []
        # Phase 1 — Consolidation synaptique (REM simulé)
        try:
            from core.synaptic_network import cortex
            result = cortex.dream_consolidation()
            pruned = result.get("pruned_synapses", 0)
            dream_cx = result.get("dream_connections", 0)
            meta = result.get("new_meta_concepts", 0)
            strengthened = result.get("strengthened", 0)
            cortex.save()
            summary = f"Rêve synaptique: {dream_cx} connexions oniriques, {strengthened} renforcées, {pruned} élaguées, {meta} méta-concepts"
            dream_report.append(summary)
            logger.info(f"[DREAM] {summary}")
        except Exception as e:
            logger.warning(f"[DREAM] Synapse consolidation échouée: {e}")

        # Phase 1.5 — V12.0 Replay hippocampique (MDP, Q-learning séquentiel)
        # Retropropagation gamma=0.95 sur les transitions (drive, prev, curr)
        # du TrajectoryBuffer. Clippage failures à -1.0 protege COUNCIL_DEBATE
        # et consorts d'une phobie algorithmique definitive.
        try:
            from core.hippocampus import hippocampus
            from core.basal_ganglia import ganglia
            trajectory = hippocampus.get_recent_trajectory()
            if len(trajectory) >= 2:
                updates = ganglia.update_sequential(trajectory)
                if updates > 0:
                    seq_count = len(ganglia.sequential_habits)
                    summary = (
                        f"Replay MDP V12.0: {updates} transitions Q-updatees, "
                        f"{seq_count} etats memorises"
                    )
                    dream_report.append(summary)
                    logger.info(f"[DREAM] {summary}")
            # V12.1b (2026-04-23) : vider le buffer apres replay pour
            # eviter la rumination (re-jeu des memes transitions sur les
            # dreams suivants). Observation 22/04 21:07-21:34 : 6 dreams
            # en sieste qui ont chacun rejoue les memes 18 transitions,
            # inflant artificiellement les Q-values (+0.29 au lieu de +0.05).
            # Le buffer est volatile par design : les Episodes restent dans
            # hippocampus._episodes pour la continuite narrative.
            hippocampus.trajectory_buffer.clear()
        except Exception as e:
            logger.warning(f"[DREAM] Replay MDP V12.0 echoue: {e}")

        # Phase 2 — Stimulation tissue (nourriture + créativité boost)
        try:
            from core.neural_tissue import tissue
            # Injecter un état cognitif favorable au rêve
            tissue._cognitive_state["creativity"] = min(
                tissue._cognitive_state.get("creativity", 0.5) + 0.3, 1.0
            )
            tissue._cognitive_state["stability"] = min(
                tissue._cognitive_state.get("stability", 0.5) + 0.2, 1.0
            )
            tissue._cognitive_state["threat_level"] = 0.0
            tissue._cognitive_state["dopamine_level"] = min(
                tissue._cognitive_state.get("dopamine_level", 0.5) + 0.15, 0.8
            )
            # Forcer quelques ticks pour que les cellules réagissent
            alive_before = len([c for c in tissue.cells if c.alive])
            for _ in range(3):
                tissue._tick()
            alive_after = len([c for c in tissue.cells if c.alive])
            delta = alive_after - alive_before
            summary = f"Rêve cellulaire: {alive_after} cellules ({'+' if delta >= 0 else ''}{delta}), créativité={tissue._cognitive_state['creativity']:.2f}"
            dream_report.append(summary)
            logger.info(f"[DREAM] {summary}")
        except Exception as e:
            logger.warning(f"[DREAM] Tissue stimulation échouée: {e}")

        # Phase 2.5 — Matériau onirique thalamique (nap_buffer → thèmes du rêve)
        try:
            from core.thalamus import thalamus as _thal
            nap_events = _thal.get_nap_events()
            if nap_events:
                from core.thalamus import EVENT_CATEGORIES as _EC
                # Compter les catégories d'events accumulés pendant la sieste
                cat_counts: dict = {}
                for entry in nap_events:
                    cat = _EC.get(entry.get("event_type", ""), "unknown")
                    cat_counts[cat] = cat_counts.get(cat, 0) + 1
                dominant_cat = max(cat_counts, key=cat_counts.get) if cat_counts else None
                # Moduler le tissue selon le thème onirique dominant
                if dominant_cat and 'tissue' in dir():
                    cat_boost_map = {
                        "urgence": "threat_level",
                        "cognition": "goals",
                        "emergence": "creativity",
                        "motivation": "dopamine_level",
                    }
                    field = cat_boost_map.get(dominant_cat)
                    if field and field in tissue._cognitive_state:
                        tissue._cognitive_state[field] = min(
                            tissue._cognitive_state.get(field, 0.5) + 0.1, 1.0
                        )
                themes = ", ".join(f"{c}({n})" for c, n in sorted(cat_counts.items(), key=lambda x: -x[1]))
                summary = f"Rêve thalamique: {len(nap_events)} events digérés, thèmes=[{themes}]"
                dream_report.append(summary)
                logger.info(f"[DREAM] {summary}")
        except Exception as e:
            logger.debug(f"[DREAM] Thalamus nap integration skipped: {e}")

        # Phase 2.6 — Modulation émotionnelle onirique (amygdale)
        try:
            from core.amygdala import amygdala as _amyg
            stats = _amyg.get_stats()
            neg_count = stats.get("negative_memories", 0)
            pos_count = stats.get("positive_memories", 0)
            if neg_count > pos_count and 'tissue' in dir():
                # Dominance négative → rêve anxieux → booster threat
                tissue._cognitive_state["threat_level"] = min(
                    tissue._cognitive_state.get("threat_level", 0.5) + 0.05, 1.0
                )
                dream_report.append(f"Rêve anxieux: {neg_count} mémoires négatives dominantes")
            elif pos_count > neg_count and 'tissue' in dir():
                # Dominance positive → rêve créatif → booster créativité
                tissue._cognitive_state["creativity"] = min(
                    tissue._cognitive_state.get("creativity", 0.5) + 0.05, 1.0
                )
                dream_report.append(f"Rêve créatif: {pos_count} mémoires positives dominantes")
        except Exception as e:
            logger.debug(f"[DREAM] Amygdala dream integration skipped: {e}")

        # Phase 3 — Publier le rêve sur le bus
        if dream_report:
            self._nap_tasks_done.append("DREAM")
            await bus.publish("THOUGHT_STREAM", {
                "agent": "RÊVE",
                "content": " | ".join(dream_report),
                "type": "info"
            })

        # Phase 4 — Journal Intime (narrative déterministe de la journée)
        try:
            self._write_dream_journal(dream_report)
        except Exception as e:
            logger.debug(f"[DREAM] Journal intime échoué: {e}")

    def _write_dream_journal(self, dream_report: list):
        """Ecrit une entree narrative deterministe dans le journal intime.

        Compile les evenements du jour (routines, budget, mood, reves)
        en un court recit. 0 LLM — tout est deterministe.
        """
        today = date.today().isoformat()

        # --- Collecter les faits du jour ---
        routines_done = self.daily_count
        budget_used = self.daily_budget_used

        # Mood
        mood = "equilibre"
        try:
            from core.self_awareness import awareness
            snaps = awareness._snapshots
            if snaps:
                mood = snaps[-1].get("mood", "equilibre")
        except Exception:
            pass

        # Pulsion dominante
        dominant_desire = ""
        try:
            from core.desire_engine import desires
            top = max(desires.drives.values(), key=lambda d: d.deprivation)
            if top.deprivation > 50:
                dominant_desire = f"{top.name} (privation {top.deprivation:.0f}%)"
        except Exception:
            pass

        # Routines executees (liste des intents)
        routine_names = []
        try:
            for entry in self._routine_history[-20:]:
                intent = entry.get("intent", "")
                if intent:
                    routine_names.append(intent)
        except Exception:
            pass

        # Councils tenus
        council_count = 0
        try:
            council_count = sum(1 for r in routine_names if "COUNCIL" in r)
        except Exception:
            pass

        # --- Composer la narrative ---
        lines = []
        lines.append(f"Jour {today}. Humeur: {mood}.")

        if routines_done == 0:
            lines.append("Journee calme, aucune routine executee.")
        elif routines_done <= 5:
            lines.append(f"Journee legere: {routines_done} routines, {budget_used}pt consommes.")
        else:
            lines.append(f"Journee active: {routines_done} routines, {budget_used}pt consommes.")

        if routine_names:
            unique = list(dict.fromkeys(routine_names))[:5]
            lines.append(f"Activites: {', '.join(unique)}.")

        if council_count > 0:
            lines.append(f"{council_count} debat(s) Council tenu(s).")

        if dominant_desire:
            lines.append(f"Pulsion dominante: {dominant_desire}.")

        if dream_report:
            lines.append(f"Reve: {' | '.join(dream_report[:2])}.")

        narrative = " ".join(lines)

        # --- Persister ---
        entries = []
        if os.path.exists(DREAM_JOURNAL_FILE):
            try:
                with open(DREAM_JOURNAL_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                entries = data.get("entries", [])
            except (json.JSONDecodeError, OSError):
                pass

        # Eviter doublons si deja ecrit aujourd'hui — remplacer
        # Preserver le champ 'reflection' si EVENING_REFLECTION l'a deja ecrit
        existing_reflection = None
        for e in entries:
            if e.get("date") == today and e.get("reflection"):
                existing_reflection = e["reflection"]
                break
        entries = [e for e in entries if e.get("date") != today]
        new_entry = {
            "date": today,
            "narrative": narrative,
            "mood": mood,
            "routines_count": routines_done,
            "budget_used": budget_used,
        }
        if existing_reflection:
            new_entry["reflection"] = existing_reflection
        entries.append(new_entry)

        # Garder les N dernières entrées
        if len(entries) > DREAM_JOURNAL_MAX_ENTRIES:
            entries = entries[-DREAM_JOURNAL_MAX_ENTRIES:]

        os.makedirs(os.path.dirname(DREAM_JOURNAL_FILE), exist_ok=True)
        with open(DREAM_JOURNAL_FILE, "w", encoding="utf-8") as f:
            json.dump({"entries": entries}, f, indent=2, ensure_ascii=False)

        logger.info(f"[DREAM] Journal intime: {narrative[:120]}...")

    @staticmethod
    def get_dream_journal_context() -> str:
        """Retourne la derniere entree du journal pour injection dans purpose_context."""
        if not os.path.exists(DREAM_JOURNAL_FILE):
            return ""
        try:
            with open(DREAM_JOURNAL_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            entries = data.get("entries", [])
            if not entries:
                return ""
            last = entries[-1]
            return f"[JOURNAL] {last['narrative']}"
        except Exception:
            return ""

    async def _execute_lora_training(self):
        """Fine-tuning QLoRA nocturne pendant le nap mode.
        Utilise le GPU libre pour entrainer le prochain agent dans la rotation.
        Ne bloque pas si les seuils ne sont pas atteints."""
        try:
            from tools.lora_auto_trainer import auto_trainer
            result = await auto_trainer.run_training_cycle()
            agent = result.get("agent", "?")
            if result.get("success"):
                self._nap_tasks_done.append(f"LORA_{agent.upper()}")
                await bus.publish("THOUGHT_STREAM", {
                    "agent": "LORA",
                    "content": (
                        f"Fine-tuning {agent} termine : "
                        f"{result.get('n_examples', '?')} exemples, "
                        f"loss={result.get('loss', 0):.4f}, "
                        f"duree={result.get('duration_s', '?')}s, "
                        f"modele={result.get('model_name', '?')}"
                    ),
                    "type": "success"
                })
                logger.info(f"[NAP/LORA] Training {agent} reussi")
            elif result.get("skipped"):
                logger.info(
                    f"[NAP/LORA] Skip {agent}: {result.get('threshold_reason', '?')}"
                )
            else:
                logger.warning(
                    f"[NAP/LORA] Echec {agent}: {result.get('error', '?')}"
                )
                # Logger l'output du training pour diagnostic
                training_output = result.get("training_output", "")
                if training_output:
                    tail = "\n".join(training_output.strip().splitlines()[-10:])
                    logger.warning(f"[NAP/LORA] Output training:\n{tail}")
        except Exception as e:
            logger.debug(f"[NAP/LORA] LoRA auto-training indisponible: {e}")

    # ── Fin Mode Sieste ──────────────────────────────────────────────

    async def _execute_neural_compile(self) -> dict:
        """Compile les observations LLM en règles déterministes. 0 LLM."""
        try:
            from core.neural_compiler import compiler
            created = compiler.compile_rules()
            stats = compiler.get_stats()
            return {
                "status": "success",
                "result": (f"Neural compile: {created} règles créées/MAJ. "
                           f"Total: {stats['rules_count']} règles, "
                           f"{stats['intercept_rate']:.0%} interceptions.")
            }
        except Exception as e:
            return {"status": "error", "result": f"Neural compile error: {e}"}

    async def _execute_memory_cleanup(self) -> dict:
        """Nettoie la mémoire RAG : purge les anciennes ET les mauvaise qualité.

        Utilise les méthodes async lockées de ChromaMemoryManager pour éviter
        les race conditions sur les opérations composées (get→filter→delete).
        """
        try:
            from core.vector_store import ChromaMemoryManager
            mgr = ChromaMemoryManager.get_instance()
            if not mgr:
                return {"status": "error", "result": "ChromaDB indisponible."}

            # Phase 1 : Purge des entrées anciennes (>60 jours) — protégé par lock
            removed_old = await mgr.async_purge_expired(max_age_days=60)

            # Phase 2 : Purge qualitative (textes courts, hallucinations non-latin) — protégé par lock
            removed_quality = await mgr.async_purge_low_quality(
                min_length=100,
                max_non_latin_ratio=0.10,
            )

            total = removed_old + removed_quality
            msg = f"Nettoyage mémoire : {removed_old} anciennes + {removed_quality} basse qualité = {total} supprimées."
            print(f"   🧹 {msg}")
            return {"status": "success", "result": msg}
        except Exception as e:
            return {"status": "error", "result": str(e)}

    async def _execute_security_audit(self) -> dict:
        """Audite un fichier aléatoire du projet pour des vulnérabilités."""
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            # Lister les fichiers Python du projet
            target_dirs = [
                os.path.join(project_root, "core"),
                os.path.join(project_root, "Agents"),
            ]
            py_files = []
            for d in target_dirs:
                if os.path.isdir(d):
                    py_files.extend(
                        os.path.join(d, f) for f in os.listdir(d) if f.endswith(".py")
                    )
            if not py_files:
                return {"status": "error", "result": "Aucun fichier Python trouvé."}

            # Choisir un fichier en rotation
            target = py_files[self.total_routines_executed % len(py_files)]
            filename = os.path.basename(target)

            # Anti-doublon : skip si ce fichier a été audité dans les dernières 6h
            last_audit_ts = self._security_audited_files.get(filename, 0)
            if time.time() - last_audit_ts < 6 * 3600:
                # Avancer au prochain fichier non-audité récemment
                found = False
                for offset in range(1, len(py_files)):
                    alt_target = py_files[(self.total_routines_executed + offset) % len(py_files)]
                    alt_name = os.path.basename(alt_target)
                    if time.time() - self._security_audited_files.get(alt_name, 0) >= 6 * 3600:
                        target, filename = alt_target, alt_name
                        found = True
                        break
                if not found:
                    return {"status": "skipped", "result": "Tous les fichiers ont été audités récemment."}

            # Marquer comme audité
            self._security_audited_files[filename] = time.time()

            # Lire le contenu (limité à 3000 chars)
            with open(target, "r", encoding="utf-8") as f:
                code = f.read()[:3000]

            # Scan anticorps deterministe (0 LLM) avant l'audit LLM
            antibody_report = ""
            try:
                from core.bug_antibodies import antibody_registry
                infections = antibody_registry.scan_file(target)
                if infections:
                    ab_lines = [f"[ANTICORPS] {len(infections)} infection(s) dans {filename}:"]
                    for inf in infections[:5]:
                        ab_lines.append(f"  L{inf.line} [{inf.antibody_name}] {inf.context[:60]}")
                    antibody_report = "\n".join(ab_lines)
                    print(f"   🦠 ANTICORPS: {len(infections)} infection(s) dans {filename}")
            except Exception:
                pass

            print(f"   🔒 SECURITY AUDIT: {filename}")
            response = await orchestrator.dispatch_task("security", {
                "mission": (
                    f"[MODE VEILLE] Audite le fichier {filename} pour des vulnérabilités.\n"
                    f"RÈGLES STRICTES :\n"
                    f"- Réponds UNIQUEMENT en français.\n"
                    f"- Analyse UNIQUEMENT le code fourni ci-dessous, pas de code inventé.\n"
                    f"- NE GÉNÈRE PAS de code. Liste seulement les vulnérabilités trouvées.\n"
                    f"- Format : une liste numérotée de vulnérabilités (ou 'Aucune vulnérabilité détectée').\n"
                    f"- Maximum 500 mots."
                ),
                "context": (
                    f"PROTOCOLE_AUTONOMIE\n"
                    f"FICHIER: {filename}\n"
                    f"CODE À AUDITER (ne génère pas de nouveau code, analyse celui-ci) :\n{code}"
                ),
                "intent": "SECURITY_AUDIT",
                "force_local": True,
            })

            # Post-filtre anti-hallucination : détecter les réponses hors-sujet
            if response and response.get("result"):
                result_text = response["result"]
                # Détection de caractères non-latins massifs (chinois, etc.)
                non_latin = sum(1 for c in result_text if ord(c) > 0x024F)
                if non_latin > len(result_text) * 0.1:
                    logger.warning(f"[SECURITY_AUDIT] Hallucination détectée ({non_latin} chars non-latins)")
                    response["result"] = f"Audit de {filename} : résultat filtré (hallucination LLM détectée)."
                # Tronquer les réponses excessivement longues
                elif len(result_text) > 3000:
                    response["result"] = result_text[:3000] + "\n\n[... tronqué — réponse trop longue]"

            # Ajouter le rapport anticorps au resultat
            if antibody_report and response and response.get("result"):
                response["result"] = antibody_report + "\n\n" + response["result"]

            return response or {"status": "error", "result": "Pas de réponse."}
        except Exception as e:
            return {"status": "error", "result": str(e)}

    @staticmethod
    def _compute_audit_structure_cap(stab_deprivation: float) -> int:
        """V11.1 (22/04/2026) : cap adaptatif pour AUDIT_STRUCTURE.

        Cap normal = 3 (garde-fou anti-boucle stérile historique).
        Cap crise  = 8 si STABILITE.deprivation > 80 → soupape de survie.

        Découvert le 22/04 : Prométhée tournait en boucle d'impuissance
        homéostatique (50 cycles AUDIT_SURVIE consécutifs sur 9h, goals
        STABILITE abandonnés par fruitless_cycles) parce que le cap 3
        n'avait aucune issue de secours en régime critique. Le drive
        réclamait AUDIT_STRUCTURE à 95/100 de deprivation, le dispatch
        répondait 'cap atteint, skip' — spirale descendante.
        """
        return 8 if stab_deprivation > 80.0 else 3

    async def _execute_audit_structure(self) -> dict:
        """Audit structure réel : scanne le filesystem pour fichiers temporaires/orphelins."""
        audit_today = sum(1 for h in self.routine_history if h.get("intent") == "AUDIT_STRUCTURE")
        # V11.1 : cap adaptatif selon la detresse du drive STABILITE
        try:
            from core.desire_engine import desires
            stab_depriv = desires.drives["STABILITE"].deprivation
        except Exception:
            stab_depriv = 0.0
        cap = self._compute_audit_structure_cap(stab_depriv)
        if audit_today >= cap:
            logger.info(
                f"[AUTONOMY] AUDIT_STRUCTURE cap atteint ({audit_today}/{cap}, "
                f"STABILITE={stab_depriv:.0f}), skip."
            )
            return {"status": "skipped", "result": f"Cap quotidien atteint ({audit_today}/{cap})."}
        # Rafraîchir le cache de structure projet (anti-hallucination basé sur des données fraîches)
        try:
            from core.prompt_templates import reset_project_structure_cache
            reset_project_structure_cache()
        except Exception:
            pass
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

            # Extensions à détecter à la racine du projet
            temp_extensions = {".tmp", ".temp", ".bak", ".old", ".orig", ".swp", ".swo"}
            log_extensions = {".log"}  # Séparé car certains sont légitimes
            # Fichiers de log légitimes (à ne pas signaler)
            legit_logs = {"promethee.log"}

            temp_files = []
            log_files = []
            large_files = []  # > 10 MB

            # Scan de la racine uniquement (pas récursif pour les .tmp/.log)
            for entry in os.scandir(project_root):
                if entry.is_file():
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext in temp_extensions:
                        size_kb = entry.stat().st_size / 1024
                        temp_files.append(f"{entry.name} ({size_kb:.0f} KB)")
                    elif ext in log_extensions and entry.name not in legit_logs:
                        size_kb = entry.stat().st_size / 1024
                        log_files.append(f"{entry.name} ({size_kb:.0f} KB)")
                    # Fichiers volumineux (> 10 MB)
                    if entry.stat().st_size > 10 * 1024 * 1024:
                        size_mb = entry.stat().st_size / (1024 * 1024)
                        large_files.append(f"{entry.name} ({size_mb:.1f} MB)")
                # __pycache__ ignoré (normal en Python)

            # Construire le rapport
            issues = []
            if temp_files:
                issues.append(f"Fichiers temporaires à la racine : {', '.join(temp_files)}")
            if log_files:
                issues.append(f"Fichiers .log non-système à la racine : {', '.join(log_files)}")
            if large_files:
                issues.append(f"Fichiers volumineux (>10 MB) : {', '.join(large_files)}")
            # __pycache__ est normal en Python — ne pas signaler comme problème
            # (causait un rapport répétitif identique → qualité 0.60 à chaque run)

            if issues:
                report = f"AUDIT STRUCTURE — {len(issues)} problème(s) détecté(s) :\n" + "\n".join(f"- {i}" for i in issues)
                report += "\n\nRecommandation : nettoyer les fichiers temporaires inutiles."
            else:
                report = (
                    "AUDIT STRUCTURE — Aucun problème détecté.\n"
                    "La racine du projet est propre : pas de fichiers .tmp/.bak/.log orphelins, "
                    "pas de fichiers volumineux anormaux."
                )

            logger.info(f"[AUDIT_STRUCTURE] Scan terminé : {len(issues)} problème(s)")
            return {"status": "success", "result": report}
        except Exception as e:
            logger.warning(f"[AUDIT_STRUCTURE] Erreur scan: {e}")
            return {"status": "error", "result": f"Erreur lors du scan structure : {e}"}

    async def _execute_audit_survie(self) -> dict:
        """Audit de survie 3-cycles : vérifie dopamine, drives, error_streak, fruitless cycles.

        Feature issue du cours de soutien S1-S12 (faiblesse "absence résistance au changement").
        Cooldown 300s. Publie AUTONOMY_SURVIVAL_ALERT si ≥2 seuils dépassés.
        """
        now = time.time()
        last_ts = getattr(self, "_last_audit_survie_ts", 0.0)
        if now - last_ts < AUDIT_SURVIE_COOLDOWN_S:
            remaining = AUDIT_SURVIE_COOLDOWN_S - (now - last_ts)
            logger.info(f"[AUDIT_SURVIE] Cooldown actif ({remaining:.0f}s restants), skip.")
            return {"status": "skipped", "result": f"Cooldown {remaining:.0f}s."}
        self._last_audit_survie_ts = now

        alerts: list[str] = []
        checks_failed = 0
        critical_drives: list[tuple] = []  # [(name, deprivation)] pour triggering_drive

        # CYCLE 1 : Dopamine effondrée
        try:
            from core.dopamine_system import dopamine
            level = float(getattr(dopamine, "dopamine_level", 1.0))
            if level < AUDIT_SURVIE_DOPAMINE_FLOOR:
                alerts.append(f"[CRITIQUE] Dopamine effondrée : {level:.2f} (seuil {AUDIT_SURVIE_DOPAMINE_FLOOR})")
                checks_failed += 1
        except Exception as e:
            logger.debug(f"[AUDIT_SURVIE] Lecture dopamine indisponible : {e}")

        # CYCLE 2 : Drives critiques (MAITRISE, STABILITE > 90)
        try:
            from core.desire_engine import desires
            for drive in desires.drives.values():
                name = getattr(drive, "name", "?")
                dep = float(getattr(drive, "deprivation", 0.0))
                if name in ("MAITRISE", "STABILITE", "COMPREHENSION") and dep > AUDIT_SURVIE_DRIVE_CRITICAL:
                    alerts.append(f"[CRITIQUE] Drive {name} critique : {dep:.0f}/100")
                    critical_drives.append((name, dep))
                    checks_failed += 1
        except Exception as e:
            logger.debug(f"[AUDIT_SURVIE] Lecture drives indisponible : {e}")

        # CYCLE 3 : Error streak élevé
        if self.error_streak >= AUDIT_SURVIE_ERROR_STREAK:
            alerts.append(f"[ALERTE] Error streak élevé : {self.error_streak} échecs consécutifs")
            checks_failed += 1

        # CYCLE 4 : Fruitless cycles — même intent ≥3 fois faible qualité dans les 10 derniers
        recent = self.routine_history[-AUDIT_SURVIE_FRUITLESS_WINDOW:]
        intent_stats: dict = {}
        for h in recent:
            intent = h.get("intent")
            if not intent:
                continue
            q = float(h.get("quality_score", h.get("quality", 1.0)) or 0.0)
            stat = intent_stats.setdefault(intent, {"count": 0, "q_sum": 0.0})
            stat["count"] += 1
            stat["q_sum"] += q
        for intent, stat in intent_stats.items():
            if stat["count"] >= AUDIT_SURVIE_FRUITLESS_COUNT:
                avg_q = stat["q_sum"] / stat["count"]
                if avg_q < 0.4:
                    alerts.append(f"[ALERTE] Routine improductive : {intent} ×{stat['count']}, qualité moyenne {avg_q:.2f}")
                    checks_failed += 1

        # Escalade si ≥2 seuils dépassés (ou dès qu'un drive est critique : prioritaire)
        should_escalate = checks_failed >= 2 or bool(critical_drives)
        if should_escalate:
            # Drive déclencheur : celui avec la plus forte déprivation (si plusieurs)
            triggering_drive = ""
            if critical_drives:
                critical_drives.sort(key=lambda x: -x[1])
                triggering_drive = critical_drives[0][0]
            logger.warning(
                f"[AUDIT_SURVIE] {checks_failed} alertes de survie — escalade bus "
                f"(triggering_drive={triggering_drive or 'none'})."
            )
            try:
                await bus.publish("AUTONOMY_SURVIVAL_ALERT", {
                    "checks_failed": checks_failed,
                    "alerts": alerts,
                    "triggering_drive": triggering_drive,
                    "critical_drives": [{"name": n, "deprivation": d} for n, d in critical_drives],
                    "timestamp": now,
                })
            except Exception as e:
                logger.debug(f"[AUDIT_SURVIE] Publish échec : {e}")

        if alerts:
            report = f"AUDIT SURVIE — {len(alerts)} alerte(s) :\n" + "\n".join(f"- {a}" for a in alerts)
            if checks_failed >= 2:
                report += "\n\nEscalade émise sur le bus (AUTONOMY_SURVIVAL_ALERT)."
        else:
            report = "AUDIT SURVIE — Constantes vitales nominales. Aucune alerte."

        logger.info(f"[AUDIT_SURVIE] Terminé : {checks_failed} check(s) en alerte.")
        return {"status": "success", "result": report}

    async def _execute_refactoring_audit(self) -> dict:
        """Matérialisation de la routine canonique REFACTORING_AUDIT (Étage 1 du bouton panique).

        Lecture seule : scanne core/*.py via AST, détecte fichiers volumineux
        et fonctions longues, produit docs/refactoring_targets.md. Zero LLM.
        Bras armé du drive MAITRISE via drive_routine_registry.
        """
        import ast
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            core_dir = os.path.join(project_root, "core")
            if not os.path.isdir(core_dir):
                return {"status": "error", "result": f"Dossier core/ introuvable : {core_dir}"}

            targets: list = []
            files_scanned = 0
            total_lines = 0

            for entry in os.scandir(core_dir):
                if not entry.is_file() or not entry.name.endswith(".py"):
                    continue
                files_scanned += 1
                try:
                    with open(entry.path, "r", encoding="utf-8") as fh:
                        src = fh.read()
                    lines = src.count("\n") + 1
                    total_lines += lines

                    # Critère 1 : fichier > 1500 lignes
                    if lines > 1500:
                        targets.append({
                            "file": entry.name,
                            "kind": "large_file",
                            "metric": f"{lines} lignes",
                            "severity": 3 if lines > 3000 else 2,
                        })

                    # Critère 2 : fonctions > 80 lignes via AST
                    try:
                        tree = ast.parse(src)
                        for node in ast.walk(tree):
                            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                start = node.lineno
                                end = node.end_lineno or start
                                func_lines = end - start + 1
                                if func_lines > 80:
                                    targets.append({
                                        "file": entry.name,
                                        "kind": "long_function",
                                        "metric": f"{node.name} L{start}-{end} ({func_lines} lignes)",
                                        "severity": 3 if func_lines > 200 else 2 if func_lines > 120 else 1,
                                    })
                    except SyntaxError as se:
                        targets.append({
                            "file": entry.name,
                            "kind": "syntax_error",
                            "metric": str(se)[:80],
                            "severity": 3,
                        })
                except Exception:
                    continue

            # Trier par sévérité puis par métrique
            targets.sort(key=lambda t: (-t["severity"], t["file"]))

            # Écrire le rapport
            report_path = os.path.join(project_root, "docs", "refactoring_targets.md")
            os.makedirs(os.path.dirname(report_path), exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as fh:
                fh.write(f"# Refactoring Targets — {date.today().isoformat()}\n\n")
                fh.write(f"Auto-généré par `REFACTORING_AUDIT` (bras armé du drive MAITRISE).\n\n")
                fh.write(f"- Fichiers scannés : **{files_scanned}**\n")
                fh.write(f"- Lignes totales : **{total_lines}**\n")
                fh.write(f"- Cibles détectées : **{len(targets)}**\n\n")
                if targets:
                    fh.write("## Top cibles (par sévérité)\n\n")
                    for t in targets[:40]:
                        sev_mark = "🔴" if t["severity"] >= 3 else "🟡" if t["severity"] >= 2 else "🟢"
                        fh.write(f"- {sev_mark} **{t['file']}** — `{t['kind']}` — {t['metric']}\n")
                else:
                    fh.write("Aucune cible détectée. Le noyau est propre.\n")

            summary = (
                f"REFACTORING AUDIT — {files_scanned} fichiers scannés, "
                f"{len(targets)} cibles (rapport: docs/refactoring_targets.md)"
            )
            logger.info(f"[REFACTORING_AUDIT] {summary}")
            return {"status": "success", "result": summary}
        except Exception as e:
            logger.warning(f"[REFACTORING_AUDIT] Erreur : {e}")
            return {"status": "error", "result": f"REFACTORING_AUDIT erreur : {e}"}

    async def _execute_ci_pipeline_run(self) -> dict:
        """Matérialisation de la routine canonique CI_PIPELINE_RUN (Étage 1 du bouton panique).

        Lance pytest sur la suite de tests en mode rapide (timeout 120s) et
        rapporte passed/failed/errors. Zero LLM, 0 coût LLM (mais utilise CPU).
        Bras armé du drive STABILITE via drive_routine_registry.
        """
        import subprocess
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"

            # V4.1 (2026-04-19) : CI ciblee sur la suite V11 stable au lieu
            # de tests/ entier (5283 tests, ~9 min, impossible en 120s timeout).
            # Cette suite de ~311 tests couvre le coeur architecture V11 :
            # synaptic, hebbian causal, fermeture epistemique, school, factuality.
            # Le reste (189 failed + 33 errors sur suite complete) est dette
            # anterieure a auditer dans un chantier separe "test cleanup".
            ci_test_targets = [
                "tests/test_synaptic_network.py",
                "tests/test_synaptic_hebbian_causal.py",
                "tests/test_synaptic_epistemic_closure.py",
                "tests/test_school_schedule.py",
                "tests/test_factuality_verifier.py",
            ]
            proc = await asyncio.create_subprocess_exec(
                "python", "-m", "pytest", *ci_test_targets, "--tb=no", "-q",
                cwd=project_root, env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                logger.warning("[CI_PIPELINE_RUN] Timeout 120s")
                return {"status": "error", "result": "CI pipeline timeout après 120s"}

            out = stdout.decode("utf-8", errors="replace")
            import re
            m_passed = re.search(r"(\d+) passed", out)
            m_failed = re.search(r"(\d+) failed", out)
            m_error = re.search(r"(\d+) error", out)
            passed = int(m_passed.group(1)) if m_passed else 0
            failed = int(m_failed.group(1)) if m_failed else 0
            errors = int(m_error.group(1)) if m_error else 0
            total = passed + failed + errors

            summary = f"CI PIPELINE — {passed}/{total} passés (failed={failed}, errors={errors})"
            logger.info(f"[CI_PIPELINE_RUN] {summary}")
            if failed > 0 or errors > 0:
                return {"status": "error", "result": summary}
            return {"status": "success", "result": summary}
        except FileNotFoundError:
            return {"status": "error", "result": "CI pipeline : python introuvable dans PATH"}
        except Exception as e:
            logger.warning(f"[CI_PIPELINE_RUN] Erreur : {e}")
            return {"status": "error", "result": f"CI pipeline erreur : {e}"}

    async def _execute_refactor_random(self) -> dict:
        """Propose un refactoring pour un fichier aléatoire."""
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            target_dirs = [
                os.path.join(project_root, "core"),
                os.path.join(project_root, "Agents"),
            ]

            # Charger la liste des fichiers protégés
            try:
                from Agents.factory_agent import _PROTECTED_FILES
            except ImportError:
                _PROTECTED_FILES = set()

            py_files = []
            for d in target_dirs:
                if os.path.isdir(d):
                    for f in os.listdir(d):
                        if f.endswith(".py"):
                            # Calculer le chemin relatif pour vérifier la protection
                            rel_path = os.path.relpath(os.path.join(d, f), project_root).replace("\\", "/")
                            if rel_path not in _PROTECTED_FILES:
                                py_files.append(os.path.join(d, f))
            if not py_files:
                return {"status": "error", "result": "Aucun fichier Python non-protégé trouvé."}

            # Rotation différente du security audit (offset +7)
            target = py_files[(self.total_routines_executed + 7) % len(py_files)]
            filename = os.path.basename(target)

            with open(target, "r", encoding="utf-8") as f:
                code = f.read()[:3000]

            print(f"   🔧 REFACTOR: {filename}")
            response = await orchestrator.dispatch_task("coder", {
                "mission": (
                    f"[MODE VEILLE] Analyse {filename} et propose UN SEUL refactoring précis "
                    f"pour améliorer la lisibilité ou réduire la complexité. "
                    f"Pas de réécriture complète, juste une suggestion ciblée."
                ),
                "context": f"PROTOCOLE_AUTONOMIE\nFICHIER: {filename}\nCODE:\n{code}",
                "force_local": True,
                "intent": "REFACTOR_RANDOM",
            })
            return response or {"status": "error", "result": "Pas de réponse."}
        except Exception as e:
            return {"status": "error", "result": str(e)}

    async def _process_council_consensus(self, council_result: dict, topic: dict):
        """Transforme un consensus Council en specs Evolution (Council → Action)."""
        import re
        from core.evolution_catalog import EvolutionCatalog, ImprovementSpec

        final_summary = council_result.get("final_summary", "")
        if not final_summary or len(final_summary) < 50:
            return

        catalog = EvolutionCatalog()

        # Limiter à 2 specs générées par session pour éviter le spam
        existing_council_specs = [
            s for s in catalog.specs.values()
            if s.id.startswith("COUNCIL-") and s.status == "available"
        ]
        if len(existing_council_specs) >= 3:
            logger.info("[COUNCIL→ACTION] Déjà 3 specs Council en attente, skip.")
            return

        # Construire le texte d'analyse à partir du transcript COMPLET du dernier tour
        # (le final_summary tronque à 200 chars/participant, perdant les détails concrets)
        # Exclure les entries étudiant pour ne garder que les contributions des agents
        transcript = council_result.get("transcript", [])
        if transcript:
            participants = council_result.get("participants", [])
            last_round = max(e["round"] for e in transcript)
            last_round_entries = [
                e for e in transcript
                if e["round"] == last_round and not e.get("is_student")
            ]
            # Utiliser le contenu complet (max 1500 chars/participant au lieu de 200)
            analysis_text = "\n".join(
                f"[{e['agent'].upper()}] {e['content'][:1500]}" for e in last_round_entries
            )
        else:
            analysis_text = final_summary

        # Extraire les fichiers cibles (avec ET sans préfixe de dossier)
        file_mentions = re.findall(r'((?:core|Agents)/[\w/]+\.py)', analysis_text)
        # Aussi capturer les .py mentionnés seuls (ex: "bus.py", "router.py")
        standalone_py = re.findall(r'\b(\w+\.py)\b', analysis_text)
        # Mapper les fichiers standalone vers leur chemin probable
        known_dirs = {"core/": ["orchestrator", "router", "bus", "autonomy_engine", "council",
                                "summoner", "base_agent", "event_bus", "self_awareness",
                                "prompt_templates", "ci_pipeline", "grimoire_writer",
                                "psyche", "evolution_catalog", "strategic_journal"],
                      "Agents/": ["coder_agent", "architect_agent", "security_agent",
                                  "evolution_agent", "factory_agent", "researcher_agent",
                                  "strategist_agent", "writer_agent", "infra_agent",
                                  "formatter_agent"]}
        for py_file in standalone_py:
            stem = py_file.replace(".py", "")
            for prefix, known in known_dirs.items():
                if stem in known:
                    qualified = f"{prefix}{py_file}"
                    if qualified not in file_mentions:
                        file_mentions.append(qualified)

        # Valider que les fichiers mentionnés existent réellement (anti-hallucination)
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        verified_files = []
        for f in file_mentions:
            full_path = os.path.join(project_root, f.replace("/", os.sep))
            if os.path.exists(full_path):
                verified_files.append(f)
            else:
                logger.warning(f"[COUNCIL→ACTION] Fichier halluciné ignoré : {f}")
        file_mentions = verified_files

        # Extraire les actions concrètes — regex élargie pour le langage naturel des LLMs
        action_patterns = re.findall(
            r'(?:ACTION|IMPLÉMENTER|IMPL[ÉE]MENTATION|AJOUTER|AJOUT|MODIFIER|MODIFICATION'
            r'|SUGGESTION|RECOMMANDATION|CRÉER|CRÉATION|AMÉLIORER|AMÉLIORATION'
            r'|IMPLEMENT|ADD|MODIFY|CREATE|IMPROVE)\s*[:\-]\s*(.+)',
            analysis_text,
            re.IGNORECASE,
        )
        # Fallback : chercher des verbes d'action en début de ligne (tirets de liste)
        if not action_patterns:
            action_patterns = re.findall(
                r'[-•]\s*(?:Ajouter|Créer|Modifier|Implémenter|Améliorer|Intégrer|Remplacer|Refactorer)\s+(.+)',
                analysis_text,
                re.IGNORECASE,
            )

        if not action_patterns and not file_mentions:
            logger.info("[COUNCIL→ACTION] Pas d'action concrète dans le consensus.")
            return

        # Construire la spec
        mission_short = topic.get("mission", "amélioration")[:80]
        spec_id = f"COUNCIL-{int(time.time())}-{uuid.uuid4().hex[:4]}"

        # Prendre le premier fichier cible vérifié (pas de fallback générique)
        if not file_mentions:
            logger.info("[COUNCIL→ACTION] Aucun fichier vérifié — spec non créée.")
            return
        target_file = file_mentions[0]

        # Résumé des actions
        actions_text = "\n".join(f"- {a.strip()}" for a in action_patterns[:3])
        if not actions_text:
            actions_text = analysis_text[:500]

        # Extraire la méthode cible depuis l'analyse (ou fallback générique)
        method_match = re.search(r'(?:méthode|method|def)\s+(\w+)', analysis_text, re.IGNORECASE)
        target_method = method_match.group(1) if method_match else ""

        # code_template valide (doit contenir def/class/import pour passer Phase 4c)
        code_template = (
            f"import logging\n\n"
            f"def council_improvement():\n"
            f"    \"\"\"Amélioration issue du consensus Council.\n"
            f"    Mission: {mission_short}\n"
            f"    \"\"\"\n"
            f"    # Actions identifiées:\n"
        )
        for action_line in actions_text.split("\n")[:5]:
            code_template += f"    {action_line}\n"
        code_template += "    pass\n"

        spec = ImprovementSpec(
            id=spec_id,
            name=f"Council: {mission_short}",
            description=f"Issu d'un consensus Council.\n{actions_text}",
            category="intelligence",
            target_file=target_file,
            target_method=target_method,
            difficulty=2,
            code_template=code_template,
            validation="Vérifier que l'amélioration proposée par le Council fonctionne.",
            tags=["council", "consensus", "auto-generated"],
            status="available",
        )

        catalog.specs[spec_id] = spec
        catalog._save()
        logger.info(f"[COUNCIL→ACTION] Spec {spec_id} créée : {mission_short}")
        print(f"   📋 COUNCIL→ACTION: Spec {spec_id} ajoutée au catalogue Evolution")

    async def start_loop(self):
        self.is_running = True
        self._loop_alive = True
        self._loop_last_tick = time.time()
        self._load_overrides()  # Restaurer les découvertes autoresearch
        print(f"   🧠 AUTONOMY: Moteur V24 (Health-Aware Sentinel) activé. Limite: {MAX_DAILY_ROUTINES} routines/jour.")

        # Démarrer le heartbeat de survie (tronc cérébral indépendant)
        if self._survival_tick_task is None or self._survival_tick_task.done():
            self._survival_tick_task = asyncio.create_task(self._survival_heartbeat_loop())
            print(f"   🫀 SURVIVAL: Heartbeat tronc cérébral démarré (tick {AUDIT_SURVIE_TICK_INTERVAL_S:.0f}s).")

        while self.is_running:
          try:
            # === HEARTBEAT : preuve de vie à chaque cycle ===
            self._loop_last_tick = time.time()

            # Sleep adaptatif : piloté par le coeur (cohérence cardiaque)
            try:
                from core.cardiac_engine import heart
                sleep_time = heart.compute_sleep_duration()
            except Exception:
                sleep_time = random.randint(600, 1200)
            if self.error_streak >= 3:
                sleep_time = int(sleep_time * 1.5)
                logger.warning(f"[AUTONOMY] Mode prudent (error_streak={self.error_streak}), sleep: {sleep_time}s")
                try:
                    from core.cardiac_engine import heart as _h
                    _h.react("error_streak")
                except Exception:
                    pass
                # Décroissance progressive : réduire l'error_streak de 1 à chaque cycle pour sortir de la spirale
                # Mo01: seuil abaissé de 5 à 3 — un streak de 3-4 ne doit pas être permanent
                if self.error_streak >= 3:
                    self.error_streak -= 1

            # Modes spéciaux : bypass le sleep cardiaque
            if getattr(self, "is_coffee_mode", False):
                await asyncio.sleep(10)  # Check rapide en mode café
            elif getattr(self, "is_autoresearch", False):
                await asyncio.sleep(AUTORESEARCH_INTERVAL)
            else:
                # Modulation circadienne du sleep
                try:
                    from core.circadian_rhythm import circadian
                    sleep_time = int(sleep_time * circadian.get_sleep_multiplier())
                except Exception:
                    pass
                # Sleep interruptible : permet au mode café/sieste de réveiller la boucle
                remaining = sleep_time
                while remaining > 0:
                    chunk = min(remaining, 15)
                    await asyncio.sleep(chunk)
                    remaining -= chunk
                    if getattr(self, "is_coffee_mode", False) or self.is_napping:
                        break

            if orchestrator.kill_switch_active or self.is_processing:
                continue

            # Mode sieste : maintenance 0-LLM uniquement, sleep rallongé
            if self.is_napping:
                nap_elapsed = time.time() - self._nap_started_at if self._nap_started_at else 0

                # Sieste profonde / hibernation : respecter _nap_target_duration si > 0
                target = getattr(self, "_nap_target_duration", 0.0)
                if target > 0:
                    if nap_elapsed >= target:
                        mode = getattr(self, "_nap_mode", NAP_MODE_NORMAL)
                        logger.info(f"[AUTONOMY] Auto-reveil sieste {mode} — {nap_elapsed/3600:.1f}h ecoulees (cible {target/3600:.1f}h).")
                        await self.exit_nap()
                        continue
                    # Sinon, on reste en sieste, pas de renouvellement
                else:
                    # Comportement classique (sans duree cible) : renouvellement automatique
                    if nap_elapsed >= NAP_PERIOD_DURATION:
                        if self._nap_renewals_used < NAP_MAX_RENEWALS:
                            self._nap_renewals_used += 1
                            self._nap_started_at = time.time()
                            logger.info(f"[AUTONOMY] Renouvellement sieste ({self._nap_renewals_used}/{NAP_MAX_RENEWALS})")
                        else:
                            logger.info(f"[AUTONOMY] Auto-réveil — {NAP_MAX_RENEWALS + 1} périodes écoulées.")
                            await self.exit_nap()
                            continue
                self.is_processing = True
                try:
                    await self._execute_nap_routine()
                except Exception as e:
                    logger.warning(f"[AUTONOMY] Erreur routine sieste: {e}")
                finally:
                    self.is_processing = False
                    self._persist_state()
                await asyncio.sleep(NAP_SLEEP_INTERVAL)
                continue

            # Mode café : socialisation libre avec Alfred
            if self.is_coffee_mode:
                coffee_elapsed = time.time() - self._coffee_started_at if self._coffee_started_at else 0
                if coffee_elapsed >= COFFEE_MODE_DURATION:
                    logger.info(f"[COFFEE_MODE] Fin automatique — {int(coffee_elapsed // 60)} min écoulées.")
                    await self.exit_coffee_mode()
                    continue
                self.is_processing = True
                try:
                    # 1 session sur 3 = partie de jeu vs Alfred (0 LLM, repos GPU)
                    if self._coffee_sessions > 0 and self._coffee_sessions % 3 == 2:
                        game_result = await self._execute_coffee_game()
                        if game_result.get("status") == "success":
                            self._coffee_sessions += 1
                            logger.info(f"[COFFEE_MODE] Jeu #{self._coffee_sessions}: {game_result.get('result', '?')[:120]}")
                        else:
                            # Fallback café si le jeu echoue
                            try:
                                from core.ami import alfred
                                alfred.last_coffee = 0.0
                            except Exception:
                                pass
                            result = await self._execute_coffee_break()
                            if result.get("status") == "success":
                                self._coffee_sessions += 1
                            logger.info(f"[COFFEE_MODE] Café #{self._coffee_sessions}: {result.get('result', '?')[:120]}")
                    else:
                        # Reset cooldown Alfred AVANT chaque café du mode
                        try:
                            from core.ami import alfred
                            alfred.last_coffee = 0.0
                        except Exception:
                            pass
                        result = await self._execute_coffee_break()
                        status = result.get("status", "unknown")
                        if status == "success":
                            self._coffee_sessions += 1
                        logger.info(f"[COFFEE_MODE] Café #{self._coffee_sessions}: {result.get('result', '?')[:120]}")
                except Exception as e:
                    logger.warning(f"[COFFEE_MODE] Erreur café: {e}")
                finally:
                    self.is_processing = False
                    self._persist_state()
                await asyncio.sleep(COFFEE_MODE_INTERVAL)
                continue

            # Mode Autoresearch : expériences PARAM_EXPERIMENT exclusives
            if self.is_autoresearch:
                ar_elapsed = time.time() - self._autoresearch_started_at if self._autoresearch_started_at else 0
                if ar_elapsed >= AUTORESEARCH_DURATION:
                    logger.info(f"[AUTORESEARCH] Session terminée ({AUTORESEARCH_DURATION // 3600}h écoulées).")
                    await self.exit_autoresearch()
                    continue
                self.is_processing = True
                try:
                    response = await self._execute_param_experiment()
                    status = response.get("status", "unknown") if response else "none"
                    if status == "success":
                        self._autoresearch_experiments += 1
                        result_text = response.get("result", "")
                        if "KEPT" in result_text:
                            self._autoresearch_kept += 1
                        self._persist_state()
                    elif status == "skipped":
                        logger.info(f"[AUTORESEARCH] Expérience skipped: {response.get('result', '?')[:120]}")
                    elif status == "error":
                        logger.warning(f"[AUTORESEARCH] Expérience error: {response.get('result', '?')[:200]}")
                    else:
                        logger.warning(f"[AUTORESEARCH] Réponse inattendue: {response}")
                except Exception as e:
                    logger.warning(f"[AUTORESEARCH] Erreur expérience: {e}")
                finally:
                    self.is_processing = False
                await asyncio.sleep(AUTORESEARCH_INTERVAL)
                continue

          except asyncio.CancelledError:
            logger.info("[AUTONOMY] Loop annulée (CancelledError).")
            break
          except Exception as e:
            # RÉSURRECTION : le loop ne meurt JAMAIS silencieusement
            self._loop_crash_count += 1
            self._loop_last_error = f"{type(e).__name__}: {e}"
            self.is_processing = False  # Reset verrou
            logger.error(
                f"[AUTONOMY] ☠️ CRASH LOOP #{self._loop_crash_count}: {type(e).__name__}: {e} — résurrection dans 30s",
                exc_info=True
            )
            print(f"   ☠️ AUTONOMY CRASH #{self._loop_crash_count}: {e} — résurrection...")
            try:
                await bus.publish("THOUGHT_STREAM", {
                    "agent": "SYSTEM",
                    "content": f"⚠️ Autonomy loop crash #{self._loop_crash_count}: {e}. Auto-résurrection.",
                    "type": "warning"
                })
            except Exception:
                pass
            await asyncio.sleep(30)  # cooldown avant résurrection
            continue  # retour au début du while après résurrection

          # === Chemin normal (ni sieste, ni autoresearch) — aussi protégé ===
          try:
            budget_status = self._check_daily_budget()

            # --- CIRCADIEN : évaluer transition de phase ---
            try:
                from core.circadian_rhythm import circadian
                new_phase = circadian._evaluate_transition(budget_status, self.last_health_check)
                if new_phase:
                    await circadian._transition_to(new_phase, f"budget={budget_status}")
                current_phase = circadian.get_phase()
            except Exception:
                current_phase = "eveil"

            if budget_status == "exhausted":
                if current_phase == "sommeil_profond":
                    # Sommeil profond : exécuter les tâches de maintenance
                    self.is_processing = True
                    try:
                        from core.circadian_rhythm import circadian as _circ
                        result = await _circ.execute_next_sleep_task()
                        if result is None:
                            await _circ._transition_to("aube", "maintenance terminée")
                    except Exception as e:
                        logger.warning(f"[AUTONOMY] Erreur maintenance circadienne: {e}")
                    finally:
                        self._persist_state()
                        await asyncio.sleep(30)
                        self.is_processing = False
                    continue
                # Crépuscule/Aube : routines post-budget existantes
                if not self.is_processing:
                    idle_time = time.time() - self.last_user_interaction
                    if idle_time > self.idle_threshold:
                        self.is_processing = True
                        try:
                            await self._execute_post_budget_routine()
                        except Exception as e:
                            logger.warning(f"[AUTONOMY] Erreur routine post-budget: {e}")
                        finally:
                            self._persist_state()
                            await asyncio.sleep(30)
                            self.is_processing = False
                            self.last_user_interaction = time.time()
                continue

            idle_time = time.time() - self.last_user_interaction

            if idle_time > self.idle_threshold:
                # Health check
                try:
                    health = await SystemHealthCheck.run()
                except Exception as e:
                    health = {"verdict": "NO_GO", "warnings": [str(e)], "timestamp": datetime.now().isoformat(),
                              "cpu_percent": 0, "ram_percent": 0, "ollama_alive": False, "ollama_models": []}
                    logger.warning(f"[AUTONOMY] Health check échoué: {e}")

                self.last_health_check = health

                # Alerte mémoire
                memory = health.get("memory", {})
                if memory.get("status") in ("degraded", "down"):
                    await bus.publish("MEMORY_HEALTH_ALERT", {
                        "status": memory["status"],
                        "warnings": memory.get("warnings", []),
                        "persistent": memory.get("persistent", False),
                        "collections": memory.get("collections", {}),
                    })
                    logger.warning(f"[AUTONOMY] MÉMOIRE {memory['status'].upper()}: {memory.get('warnings', [])}")

                # Retry dead letters (1 par cycle, supprime si échec pour éviter boucle infinie)
                if bus.dead_letter_count > 0:
                    try:
                        retried = await bus.retry_dead_letter(0)
                        if retried:
                            logger.info("[AUTONOMY] Dead letter re-publiée avec succès.")
                        else:
                            # Échec du retry → supprimer pour ne pas boucler indéfiniment
                            dl_list = bus.get_dead_letters()
                            if dl_list:
                                bus._dead_letters.pop(0)
                                logger.info("[AUTONOMY] Dead letter irrécupérable supprimée.")
                    except Exception:
                        pass

                # Heartbeat publié à chaque cycle (même si NO_GO)
                await bus.publish("AUTONOMY_HEARTBEAT", {
                    "health": health,
                    "daily_count": self.daily_count,
                    "error_streak": self.error_streak,
                    "is_processing": self.is_processing,
                })

                # Phase D (2026-04-16) : tick du meta_observer a chaque cycle
                # Il ne fait rien si son intervalle (4h) n'est pas ecoule.
                try:
                    from core.meta_observer import observer as _meta_obs
                    await _meta_obs.tick()
                except Exception:
                    pass

                if health["verdict"] == "NO_GO":
                    logger.warning(f"[AUTONOMY] NO_GO : {health.get('warnings', [])}. Routine annulée.")
                    self._persist_state()
                    continue

                # === SAUNA AUTO-TRIGGER : nettoyage si tissu surchargé ===
                try:
                    from core.sauna_mode import sauna
                    if not sauna.active and sauna.should_auto_trigger():
                        logger.info("[AUTONOMY] SAUNA auto-trigger — tissu surchargé, nettoyage...")
                        self.is_processing = True
                        sauna_result = await sauna.start()
                        logger.info(f"[AUTONOMY] SAUNA terminé: dechets -{sauna_result.get('waste_cleaned', 0):.0f}")
                        self.is_processing = False
                        self._persist_state()
                        await asyncio.sleep(30)
                        continue
                except Exception as e:
                    logger.warning(f"[AUTONOMY] Erreur sauna auto-trigger: {e}")

                self.is_processing = True  # ON VERROUILLE
                try:
                    await self._execute_scored_routine(health, budget_status)
                except Exception as e:
                    logger.warning(f"[AUTONOMY] Erreur Routine: {e}")
                    self.error_streak += 1
                finally:
                    self._persist_state()
                    # COOLDOWN FORCÉ : 30s après une action avant de déverrouiller
                    await asyncio.sleep(30)
                    self.is_processing = False
                    self.last_user_interaction = time.time()

          except asyncio.CancelledError:
            logger.info("[AUTONOMY] Loop annulée (CancelledError) dans chemin normal.")
            break
          except Exception as e:
            self._loop_crash_count += 1
            self._loop_last_error = f"{type(e).__name__}: {e}"
            self.is_processing = False
            logger.error(
                f"[AUTONOMY] ☠️ CRASH LOOP #{self._loop_crash_count} (routine): {type(e).__name__}: {e} — résurrection dans 30s",
                exc_info=True
            )
            print(f"   ☠️ AUTONOMY CRASH #{self._loop_crash_count}: {e} — résurrection...")
            await asyncio.sleep(30)
            continue

        # Fin du while — le loop est mort proprement
        self._loop_alive = False
        logger.info(f"[AUTONOMY] Loop terminée. Crashes totaux: {self._loop_crash_count}")

    # ================================================================
    # ================================================================
    # SELF_ANALYSIS — Auto-diagnostic via la boucle agentique
    # ================================================================

    async def _execute_self_analysis(self) -> dict:
        """Auto-analyse : Promethee diagnostique ses propres routines et organes.

        Utilise le ChatEngine (avec boucle agentique) pour collecter les donnees
        internes via les commandes !, les analyser, et produire un rapport.
        Cap : max 2 auto-analyses par jour.
        """
        # Cap quotidien
        analysis_today = sum(1 for h in self.routine_history if h.get("intent") == "SELF_ANALYSIS")
        if analysis_today >= 2:
            logger.info(f"[AUTONOMY] SELF_ANALYSIS cap atteint ({analysis_today}/2 aujourd'hui), skip.")
            return {"status": "skipped", "reason": f"Cap quotidien atteint ({analysis_today}/2)."}

        try:
            from core.chat_engine import chat_engine

            # Collecter un snapshot des donnees cles (deterministe, 0 LLM)
            snapshot_parts = []

            # Routine history recente
            recent = self.routine_history[-10:]
            if recent:
                rh_lines = []
                for h in recent:
                    ts = h.get("timestamp", "?")
                    if isinstance(ts, str) and len(ts) > 10:
                        ts = ts[11:19]
                    rh_lines.append(f"  {h.get('intent','?')} -> {h.get('agent','?')} "
                                    f"q={h.get('quality_score','?')} {h.get('status','?')}")
                snapshot_parts.append("ROUTINES RECENTES (10 dernieres):\n" + "\n".join(rh_lines))

            # Compteurs globaux
            snapshot_parts.append(
                f"COMPTEURS: {self.daily_count} routines aujourd'hui, "
                f"{self.total_routines_executed} total, "
                f"error_streak={self.error_streak}, "
                f"budget={self.daily_budget_used}/{DAILY_BUDGET_POINTS}"
            )

            # Organes rapides (pas d'appel LLM)
            try:
                from core.brain_vm import brain
                if brain.current_state:
                    bs = brain.current_state
                    snapshot_parts.append(
                        f"BRAIN: tick={brain.tick_count}, "
                        f"etat={bs.cognitive_state}, coherence={bs.global_coherence:.2f}, "
                        f"mode={bs.dominant_mode}, phi={bs.phi:.3f}"
                    )
            except Exception:
                pass

            try:
                from core.cardiac_engine import heart
                snapshot_parts.append(
                    f"CARDIAC: bpm={heart.bpm:.0f}, emotion={heart.current_emotion}"
                )
            except Exception:
                pass

            try:
                from core.desire_engine import desires
                drives_str = ", ".join(
                    f"{d.name}={d.deprivation:.0f}" for d in
                    sorted(desires.drives.values(), key=lambda d: -d.deprivation)[:3]
                )
                snapshot_parts.append(f"PULSIONS (top 3): {drives_str}")
            except Exception:
                pass

            try:
                from core.attention_codelets import codelet_system
                cs = codelet_system.get_status()
                snapshot_parts.append(
                    f"CODELETS: {cs['total_alerts']} alertes totales, "
                    f"{cs['total_runs']} runs"
                )
                last = cs.get("last_alerts", [])
                if last:
                    snapshot_parts.append(
                        "  Dernieres alertes: " +
                        ", ".join(f"{a['name']}" for a in last)
                    )
            except Exception:
                pass

            snapshot = "\n".join(snapshot_parts)

            # Prompt d'auto-analyse soumis au chat (boucle agentique active)
            analysis_prompt = (
                f"[AUTO-ANALYSE INTERNE]\n\n"
                f"Voici un snapshot de ton etat actuel :\n{snapshot}\n\n"
                f"INSTRUCTIONS :\n"
                f"1. Analyse ces donnees factuelles. Detecte les ANOMALIES :\n"
                f"   - Routines repetitives (meme intent 3+ fois) ?\n"
                f"   - Qualite faible (q < 0.5) ?\n"
                f"   - Organes en anomalie (coherence basse, emotion negative prolongee, phi=0) ?\n"
                f"   - Codelets qui alertent en boucle ?\n"
                f"2. Si tu as besoin de plus de details, utilise !status, !health, !codelets, !network.\n"
                f"3. Si tu detectes un probleme dans le code, utilise !read et !grep pour investiguer.\n"
                f"4. Produis un RAPPORT structure :\n"
                f"   - ETAT GENERAL (1 ligne)\n"
                f"   - PROBLEMES DETECTES (avec gravite)\n"
                f"   - RECOMMANDATIONS (actions concretes)\n"
                f"5. NE conclus QUE sur les donnees — pas d'invention.\n"
                f"6. Si tout est nominal, dis-le simplement."
            )

            logger.info("[AUTONOMY] SELF_ANALYSIS: soumission au chat (boucle agentique)...")
            result = await chat_engine.chat(analysis_prompt)

            if result:
                # Sauvegarder le rapport en memoire vectorielle
                try:
                    from core.vector_store import ChromaMemoryManager
                    mgr = ChromaMemoryManager.get_instance()
                    if mgr:
                        import hashlib
                        doc_id = f"self-analysis-{hashlib.md5(result[:100].encode()).hexdigest()[:8]}"
                        mgr.add_documents(
                            [f"[SELF_ANALYSIS] {result[:1000]}"],
                            [{"source": "self_analysis", "timestamp": str(time.time())}],
                            [doc_id],
                            "collective_wisdom"
                        )
                except Exception:
                    pass

                return {"status": "success", "result": result[:2000]}

            return {"status": "error", "result": "Chat n'a pas retourne de resultat."}

        except Exception as e:
            logger.warning(f"[AUTONOMY] Erreur SELF_ANALYSIS: {e}")
            return {"status": "error", "result": f"Erreur: {e}"}

    # CHANTIER 2 : AUTO-FUZZING — test automatique de robustesse
    # ================================================================

    async def _execute_auto_fuzzing(self) -> dict:
        """Fuzz-test une fonction aléatoire de core/ avec des entrées aberrantes.

        100% déterministe, 0 appel LLM. Exécute la fonction dans un subprocess
        isolé pour éviter tout crash du système principal.
        """
        import ast
        import subprocess
        import random
        import tempfile

        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

            # Lister les fichiers Python de core/ (pas les grimoires ni __init__)
            core_dir = os.path.join(project_root, "core")
            py_files = [
                os.path.join(core_dir, f) for f in os.listdir(core_dir)
                if f.endswith(".py") and f != "__init__.py"
                and not f.startswith("_")
            ]
            if not py_files:
                return {"status": "skipped", "result": "Aucun fichier Python trouvé."}

            # Choisir un fichier en rotation
            target_file = py_files[self.total_routines_executed % len(py_files)]
            filename = os.path.basename(target_file)

            # Parser l'AST pour extraire les fonctions avec leurs signatures
            with open(target_file, "r", encoding="utf-8") as f:
                source = f.read()

            try:
                tree = ast.parse(source)
            except SyntaxError:
                return {"status": "skipped", "result": f"Erreur de syntaxe dans {filename}."}

            # Extraire les fonctions de premier niveau (pas les méthodes de classe)
            functions = []
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                    # Compter les paramètres (exclure self)
                    args = [a.arg for a in node.args.args if a.arg != "self"]
                    if 1 <= len(args) <= 4:  # Fonctions testables (pas trop de params)
                        functions.append({"name": node.name, "args": args, "lineno": node.lineno})

            if not functions:
                return {"status": "skipped", "result": f"Aucune fonction fuzzable dans {filename}."}

            # Choisir une fonction aléatoire
            func_info = random.choice(functions)
            func_name = func_info["name"]
            n_args = len(func_info["args"])

            print(f"   🧪 AUTO-FUZZ: {filename}:{func_info['lineno']} → {func_name}({', '.join(func_info['args'])})")

            # Générer des cas de test aberrants
            fuzz_values = [
                "None", '""', '"x" * 10000', "0", "-1", "999999",
                "[]", "{}", "True", "False", "0.0", "float('inf')",
                "float('nan')", 'b""', "object()",
            ]

            # Construire le script de test
            module_name = filename[:-3]  # sans .py
            test_cases = []
            for i in range(min(8, len(fuzz_values))):
                args_combo = ", ".join(
                    fuzz_values[(i + j) % len(fuzz_values)]
                    for j in range(n_args)
                )
                test_cases.append(f"    try_call({i}, lambda: target.{func_name}({args_combo}))")

            test_script = f'''
import sys
import os
sys.path.insert(0, r"{project_root}")
os.environ["PROMETHEE_TESTING"] = "1"

crashes = []
def try_call(idx, fn):
    try:
        fn()
    except (TypeError, ValueError, AttributeError, KeyError, IndexError) as e:
        pass  # Erreurs attendues — la fonction gère bien ses entrées
    except Exception as e:
        crashes.append(f"Case {{idx}}: {{type(e).__name__}}: {{e}}")

try:
    from core import {module_name} as target
except Exception as e:
    print(f"IMPORT_ERROR: {{e}}")
    sys.exit(0)

{chr(10).join(test_cases)}

if crashes:
    print(f"CRASHES_FOUND: {{len(crashes)}}")
    for c in crashes:
        print(c)
else:
    print("ALL_CLEAN")
'''

            # Exécuter dans un subprocess isolé (timeout 30s)
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as tmp:
                tmp.write(test_script)
                tmp_path = tmp.name

            try:
                result = subprocess.run(
                    [sys.executable, tmp_path],
                    capture_output=True, text=True, timeout=30,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                )
                output = result.stdout.strip()
                stderr = result.stderr.strip()
            except subprocess.TimeoutExpired:
                output = "TIMEOUT"
                stderr = ""
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

            # Analyser les résultats
            if "IMPORT_ERROR" in output:
                msg = f"Auto-fuzz {filename}.{func_name}: import impossible (dépendances)."
                return {"status": "skipped", "result": msg}
            elif "CRASHES_FOUND" in output:
                crash_lines = [l for l in output.split("\n") if l.startswith("Case ")]
                n_crashes = len(crash_lines)
                crash_details = "; ".join(crash_lines[:3])
                msg = f"Auto-fuzz {filename}.{func_name}: {n_crashes} crash(s) détecté(s) ! {crash_details}"
                print(f"   🐛 {msg}")
                # Mémoriser la découverte
                try:
                    from core.vector_store import ChromaMemoryManager
                    mgr = ChromaMemoryManager.get_instance()
                    if mgr:
                        mgr.add(
                            collection="collective_wisdom",
                            text=f"[AUTO-FUZZ] Bug trouvé dans {filename}.{func_name}: {crash_details}",
                            metadata={"source": "auto_fuzzing", "file": filename, "function": func_name},
                        )
                except Exception:
                    pass
                return {"status": "success", "result": msg}
            elif "ALL_CLEAN" in output:
                msg = f"Auto-fuzz {filename}.{func_name}: aucun crash — fonction robuste."
                print(f"   ✅ {msg}")
                return {"status": "success", "result": msg}
            else:
                msg = f"Auto-fuzz {filename}.{func_name}: résultat inattendu. stderr={stderr[:200]}"
                return {"status": "warning", "result": msg}

        except Exception as e:
            return {"status": "error", "result": f"Auto-fuzzing échoué: {e}"}

    # ================================================================
    # PARAM_EXPERIMENT — boucle autoresearch (Karpathy-inspired)
    # ================================================================

    # Defaults au niveau classe — safe pour les instances créées via __new__ (tests)
    _loop_last_tick: float = 0.0
    _loop_alive: bool = False
    _loop_crash_count: int = 0
    _loop_last_error: str = ""

    # Flag exclusif : une seule expérience à la fois
    _experiment_in_progress = False
    _experiment_history = []  # Journal des expériences récentes (en mémoire)
    _experiment_skip_blacklist: set = set()  # Params introuvables cette session (skip-loop guard)
    _EXPERIMENT_OBSERVE_TICKS = 10  # 10 ticks × 30s = 5 min (Karpathy: volume > durée)
    _EXPERIMENT_JOURNAL_PATH = os.path.join("memory", "experiment_journal.md")
    _TUNABLE_PARAMS_PATH = os.path.join("config", "tunable_params.json")

    _RESULTS_TSV_PATH = os.path.join("memory", "autoresearch_results.tsv")
    _OVERRIDES_PATH = os.path.join("config", "tunable_overrides.json")
    _AUTORESEARCH_PROMPT_PATH = os.path.join("config", "autoresearch_prompt.txt")
    _AUTORESEARCH_PROMPT_HISTORY_PATH = os.path.join("memory", "autoresearch_prompt_history.json")
    _AUTORESEARCH_MODEL = "qwen3.5:9b"

    async def _execute_param_experiment(self) -> dict:
        """Autoresearch V3 (Karpathy-inspired) : le LLM propose, le système exécute.

        Boucle : LLM lit historique → propose {param, direction, hypothèse}
        → système applique → observe 5 min → phi amélioré ? KEEP : ROLLBACK
        → log dans results.tsv → GOTO 1
        """
        if self._experiment_in_progress:
            return {"status": "skipped", "result": "Expérience déjà en cours."}

        # 1. Charger le catalogue
        try:
            with open(self._TUNABLE_PARAMS_PATH, "r", encoding="utf-8") as f:
                catalog = json.load(f)
            params = catalog.get("params", [])
            if not params:
                return {"status": "skipped", "result": "Catalogue vide."}
        except Exception as e:
            return {"status": "error", "result": f"tunable_params.json: {e}"}

        # Indexer les params par id (pour validation LLM)
        params_by_id = {p["id"]: p for p in params
                        if p["id"] not in self._experiment_skip_blacklist}
        if not params_by_id:
            return {"status": "skipped", "result": "Tous les params blacklistés."}

        # Anti-obsession : exclure les params testés >3 fois dans les 5 dernières exp
        recent_param_counts = {}
        for e in self._experiment_history[-5:]:
            pid = e.get("param_id", "")
            recent_param_counts[pid] = recent_param_counts.get(pid, 0) + 1
        overused = {pid for pid, cnt in recent_param_counts.items() if cnt >= 3}
        available_for_llm = {pid: p for pid, p in params_by_id.items() if pid not in overused}
        if not available_for_llm:
            available_for_llm = params_by_id  # Fallback : tout ouvrir si tout est overused

        # 2. Mesurer phi actuel
        phi_before = self._get_phi()

        # 3. Demander au LLM de proposer l'expérience
        proposal = await self._llm_propose_experiment(available_for_llm, phi_before)
        param_id = proposal.get("param_id", "")
        direction = proposal.get("direction", "down")
        variation_pct = proposal.get("variation_pct", 10) / 100.0
        hypothesis = proposal.get("hypothesis", "exploration")

        # Validation : le param doit exister dans le catalogue
        if param_id not in params_by_id:
            # Fallback random si le LLM hallucine
            param_id = random.choice(list(params_by_id.keys()))
            direction = random.choice(["up", "down"])
            variation_pct = 0.10
            hypothesis = "fallback random (LLM invalide)"
            logger.info(f"[AUTORESEARCH] LLM a proposé un param invalide, fallback: {param_id}")

        param = params_by_id[param_id]
        module_path = param["module"]
        attr_name = param["attr"]
        val_min, val_max = param["min"], param["max"]

        # 4. Lire la valeur actuelle
        try:
            module = self._import_module(module_path)
            current_val = self._get_param_value(module, attr_name)
            if current_val is None:
                self._experiment_skip_blacklist.add(param_id)
                return {"status": "skipped", "result": f"{attr_name} introuvable dans {module_path}."}
        except Exception as e:
            self._experiment_skip_blacklist.add(param_id)
            return {"status": "error", "result": f"Import {module_path}: {e}"}

        # 5. Calculer et appliquer la nouvelle valeur
        sign = 1 if direction == "up" else -1
        delta = current_val * variation_pct * sign
        new_val = max(val_min, min(val_max, current_val + delta))

        if abs(new_val - current_val) < 1e-8:
            self._experiment_skip_blacklist.add(param_id)
            return {"status": "skipped", "result": f"{param_id}: inchangé après clamping. Blacklisté."}

        self._experiment_in_progress = True
        print(f"   🔬 EXPERIMENT: {param_id} = {current_val:.6f} → {new_val:.6f} ({direction} {variation_pct*100:.0f}%)")
        print(f"   💡 HYPOTHÈSE: {hypothesis}")

        try:
            self._set_param_value(module, attr_name, new_val)
        except Exception as e:
            self._experiment_in_progress = False
            return {"status": "error", "result": f"Set {attr_name}: {e}"}

        # 6. Observer 5 min
        observe_s = self._EXPERIMENT_OBSERVE_TICKS * 30
        print(f"   ⏱️ EXPERIMENT: Observation {observe_s}s...")
        await asyncio.sleep(observe_s)

        # 7. Mesurer phi après
        phi_after = self._get_phi()
        delta_phi = phi_after - phi_before

        # 8. Décision binaire stricte (Karpathy) : phi amélioré → KEEP, sinon → ROLLBACK
        keep = delta_phi > 0

        if keep:
            decision = "KEPT"
            self._save_override(param_id, module_path, attr_name, new_val)
            print(f"   ✅ EXPERIMENT: {param_id} KEPT — phi: {phi_before:.4f} → {phi_after:.4f} (+{delta_phi:.4f})")
        else:
            decision = "ROLLBACK"
            try:
                self._set_param_value(module, attr_name, current_val)
            except Exception:
                pass
            print(f"   ↩️ EXPERIMENT: {param_id} ROLLBACK — phi: {phi_before:.4f} → {phi_after:.4f} ({delta_phi:+.4f})")

        self._experiment_in_progress = False

        # 9. Log dans results.tsv
        self._append_results_tsv(param_id, current_val, new_val, phi_before, phi_after, decision, hypothesis)

        # 10. Historique en mémoire (pour compteurs)
        self._experiment_history.append({
            "param_id": param_id, "decision": decision,
            "improvement": delta_phi, "timestamp": datetime.now().isoformat(),
        })

        result_text = (
            f"PARAM_EXPERIMENT: {param_id}\n"
            f"Hypothèse: {hypothesis}\n"
            f"Valeur: {current_val:.6f} → {new_val:.6f}\n"
            f"phi: {phi_before:.4f} → {phi_after:.4f} ({delta_phi:+.4f})\n"
            f"Décision: {decision}"
        )
        return {"status": "success", "result": result_text}

    def _load_autoresearch_prompt(self, current_phi: float, catalog_text: str, history_text: str) -> str:
        """Charge le prompt autoresearch depuis le fichier (méta-évolutif).
        Le prompt peut être modifié par la méta-évaluation en fin de session."""
        _DEFAULT_PROMPT = (
            "Tu es un chercheur qui optimise les paramètres internes d'un système IA.\n\n"
            "OBJECTIF : maximiser phi (intégration d'information). Valeur actuelle : {current_phi}\n\n"
            "PARAMÈTRES DISPONIBLES :\n{catalog_text}\n\n"
            "HISTORIQUE DES EXPÉRIENCES :\n{history_text}\n\n"
            "Analyse l'historique. Quels params ont amélioré phi ? Lesquels l'ont dégradé ?\n\n"
            "RÈGLE CRITIQUE : tu DOIS varier les paramètres. Ne teste PAS le même param plus de 2 fois de suite.\n\n"
            "Propose la prochaine expérience. Réponds UNIQUEMENT dans ce format exact (4 lignes, rien d'autre) :\n"
            "PARAM: <param_id>\nDIRECTION: up ou down\nVARIATION: <nombre entier entre 5 et 25>\nHYPOTHESE: <1 phrase courte>"
        )
        try:
            if os.path.exists(self._AUTORESEARCH_PROMPT_PATH):
                with open(self._AUTORESEARCH_PROMPT_PATH, "r", encoding="utf-8") as f:
                    template = f.read().strip()
            else:
                template = _DEFAULT_PROMPT
        except Exception:
            template = _DEFAULT_PROMPT
        return template.format(
            current_phi=f"{current_phi:.4f}",
            catalog_text=catalog_text,
            history_text=history_text,
        )

    async def _meta_evolve_prompt(self, experiments: int, kept: int, minutes: int):
        """Méta-autoresearch : le LLM évalue sa session et propose une amélioration de son propre prompt.
        Pattern HyperAgents (Meta) : le meta-level est lui-même éditable."""
        if experiments < 3:
            return  # Pas assez de données pour évaluer

        kept_ratio = kept / experiments if experiments > 0 else 0

        # Charger le prompt actuel
        try:
            with open(self._AUTORESEARCH_PROMPT_PATH, "r", encoding="utf-8") as f:
                current_prompt = f.read().strip()
        except Exception:
            return

        # Lire les dernières lignes du results.tsv pour le contexte
        session_results = ""
        try:
            if os.path.exists(self._RESULTS_TSV_PATH):
                with open(self._RESULTS_TSV_PATH, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                session_results = "".join(lines[-min(experiments + 1, 20):])
        except Exception:
            pass

        meta_prompt = f"""Tu viens de terminer une session autoresearch de {minutes} minutes.

RÉSULTATS DE LA SESSION :
- Expériences réalisées : {experiments}
- Gardées (KEPT) : {kept} ({kept_ratio:.0%})
- Résultats détaillés :
{session_results}

PROMPT ACTUEL utilisé pour proposer les expériences :
---
{current_prompt}
---

ANALYSE DEMANDÉE :
1. Qu'est-ce qui a bien marché dans tes propositions ?
2. Qu'est-ce qui a raté ? (rollbacks répétés, params inutiles, directions erronées)
3. Comment améliorer le prompt pour la prochaine session ?

Propose une version AMÉLIORÉE du prompt. Le prompt doit :
- Garder les placeholders {{current_phi}}, {{catalog_text}}, {{history_text}} (obligatoire)
- Garder le format de réponse PARAM/DIRECTION/VARIATION/HYPOTHESE (obligatoire)
- Intégrer les leçons de cette session

Réponds UNIQUEMENT avec le nouveau prompt complet, rien d'autre."""

        try:
            import httpx
            from core.base_agent import gpu_scheduler
            async with gpu_scheduler.access("meta_evolve"):
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        "http://localhost:11434/api/generate",
                        json={
                            "model": self._AUTORESEARCH_MODEL,
                            "prompt": meta_prompt,
                            "stream": False,
                            "think": False,
                            "options": {"temperature": 0.4, "num_ctx": 4096},
                        },
                        timeout=120,
                    )
                if resp.status_code != 200:
                    return

            new_prompt = resp.json().get("response", "").strip()

            # Validation : le nouveau prompt doit contenir les placeholders obligatoires
            required = ["{current_phi}", "{catalog_text}", "{history_text}", "PARAM:", "DIRECTION:", "VARIATION:", "HYPOTHESE:"]
            if not all(r in new_prompt for r in required):
                logger.info(f"[META-EVOLVE] Prompt rejeté — placeholders manquants")
                return

            # Sauvegarder l'historique (ancien prompt + métriques)
            self._save_prompt_history(current_prompt, experiments, kept, kept_ratio)

            # Écrire le nouveau prompt
            with open(self._AUTORESEARCH_PROMPT_PATH, "w", encoding="utf-8") as f:
                f.write(new_prompt)

            logger.info(f"[META-EVOLVE] Prompt évolué ({len(current_prompt)} → {len(new_prompt)} chars)")
            print(f"   🧬 META-EVOLVE: Prompt autoresearch évolué (session: {kept}/{experiments} KEPT)")

        except Exception as e:
            logger.info(f"[META-EVOLVE] Échec évolution prompt: {e}")

    def _save_prompt_history(self, prompt: str, experiments: int, kept: int, ratio: float):
        """Sauvegarde l'historique des versions de prompt avec leurs métriques."""
        try:
            history = []
            if os.path.exists(self._AUTORESEARCH_PROMPT_HISTORY_PATH):
                with open(self._AUTORESEARCH_PROMPT_HISTORY_PATH, "r", encoding="utf-8") as f:
                    history = json.load(f)
            history.append({
                "timestamp": datetime.now().isoformat(),
                "prompt_length": len(prompt),
                "experiments": experiments,
                "kept": kept,
                "kept_ratio": round(ratio, 3),
                "prompt_preview": prompt[:200],
            })
            # Garder les 20 dernières versions
            history = history[-20:]
            with open(self._AUTORESEARCH_PROMPT_HISTORY_PATH, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # ================================================================
    # MASTER_PROMPT — optimisation des prompts agents par sous-performance
    # ================================================================

    _PROMPTS_DIR = os.path.join("config", "prompts")

    async def _master_prompt_check(self, agent: str, intent: str, quality: float):
        """Vérifie si un agent sous-performe et déclenche l'optimisation du prompt.

        Se déclenche si 3 routines consécutives du même intent ont quality < 0.80.
        Pattern MASTER_PROMPT : le LLM évalue et améliore le prompt de l'agent.
        """
        # Compter les échecs consécutifs récents pour cet intent
        recent_same = [
            h for h in self.routine_history[-10:]
            if h.get("intent") == intent
        ]
        consecutive_low = 0
        for h in reversed(recent_same):
            if h.get("quality_score", 1.0) < 0.80:
                consecutive_low += 1
            else:
                break

        if consecutive_low < 3:
            return  # Pas assez d'échecs consécutifs

        # Identifier le fichier prompt correspondant
        prompt_files = {
            "EXPANSION_CODE": "evolution_synthesis.txt",
        }
        prompt_file = prompt_files.get(intent)
        if not prompt_file:
            return  # Pas de prompt externalisé pour cet intent

        prompt_path = os.path.join(self._PROMPTS_DIR, prompt_file)
        if not os.path.exists(prompt_path):
            return

        # Charger le prompt actuel
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                current_prompt = f.read().strip()
        except Exception:
            return

        # Construire le contexte des échecs
        failure_details = []
        for h in recent_same[-3:]:
            failure_details.append(
                f"  quality={h.get('quality_score', '?')}, "
                f"result={str(h.get('result_preview', ''))[:100]}"
            )
        failures_text = "\n".join(failure_details)

        meta_prompt = f"""Un agent de Prométhée sous-performe. Tu dois améliorer son prompt.

AGENT : {agent} (intent: {intent})
ÉCHECS CONSÉCUTIFS : {consecutive_low}
DÉTAILS DES DERNIERS ÉCHECS :
{failures_text}

PROMPT ACTUEL DE L'AGENT :
---
{current_prompt}
---

ANALYSE :
1. Pourquoi le prompt actuel mène à des résultats de faible qualité ?
2. Que manque-t-il ? (exemples, contraintes, structure ?)
3. Quel changement spécifique améliorerait la qualité ?

Propose une version AMÉLIORÉE du prompt. Garde TOUS les placeholders existants ({{seeds}}, {{bridges}}, etc.).
Réponds UNIQUEMENT avec le nouveau prompt complet."""

        try:
            import httpx
            from core.base_agent import gpu_scheduler
            async with gpu_scheduler.access("master_prompt"):
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        "http://localhost:11434/api/generate",
                        json={
                            "model": "qwen3.5:9b",
                            "prompt": meta_prompt,
                            "stream": False,
                            "think": False,
                            "options": {"temperature": 0.4, "num_ctx": 4096},
                        },
                        timeout=120,
                    )
                if resp.status_code != 200:
                    return

            new_prompt = resp.json().get("response", "").strip()

            # Validation : les placeholders requis doivent être présents
            required = ["{seeds}", "{bridges}", "{anomalies}"]
            if not all(r in new_prompt for r in required):
                logger.info(f"[MASTER_PROMPT] Prompt rejeté pour {intent} — placeholders manquants")
                return

            # Backup + écriture
            backup_path = prompt_path + ".bak"
            try:
                import shutil
                shutil.copy2(prompt_path, backup_path)
            except Exception:
                pass

            with open(prompt_path, "w", encoding="utf-8") as f:
                f.write(new_prompt)

            logger.info(f"[MASTER_PROMPT] Prompt {intent} évolué ({len(current_prompt)}→{len(new_prompt)} chars)")
            print(f"   🧬 MASTER_PROMPT: Prompt {intent} optimisé (3 échecs consécutifs détectés)")

        except Exception as e:
            logger.info(f"[MASTER_PROMPT] Échec optimisation {intent}: {e}")

    # ================================================================
    # PROTOCOLE EXPÉRIMENTAL — Corrélation Phi composantes × Quality
    # ================================================================

    _PHI_LOG_PATH = os.path.join("memory", "routine_phi_log.jsonl")

    def _log_routine_phi_snapshot(self, intent: str, agent: str, quality_score: float):
        """Capture un snapshot complet Phi × Quality à chaque routine.

        Stocké en JSONL (append). Après 200-300 routines, corrélation de Pearson
        entre chaque métrique et quality_score pour identifier le vrai prédicteur.
        """
        try:
            snapshot = {
                "timestamp": time.time(),
                "intent": intent,
                "agent": agent,
                "quality_score": quality_score,
            }

            # Composantes brain_vm
            try:
                from core.brain_vm import brain
                if brain.current_state:
                    s = brain.current_state
                    snapshot["phi"] = s.phi
                    snapshot["global_coherence"] = s.global_coherence
                    snapshot["cognitive_state"] = s.cognitive_state
                    snapshot["dominant_mode"] = s.dominant_mode
                    # Phase coherence (Kuramoto R — binding oscillatoire)
                    snapshot["kuramoto_r"] = brain.phase_coherence
                    # 7 signaux descendants
                    for sig_name, sig_val in s.descending_signals.items():
                        snapshot[f"signal_{sig_name}"] = round(sig_val, 4)
            except Exception:
                pass

            # Dopamine
            try:
                from core.dopamine_system import dopamine
                snapshot["dopamine"] = round(dopamine.dopamine_level, 4)
            except Exception:
                pass

            # Cardiac
            try:
                from core.cardiac_engine import heart
                snapshot["cardiac_bpm"] = round(heart.bpm, 1)
                snapshot["cardiac_coherence"] = round(heart.compute_coherence(), 4)
                snapshot["cardiac_emotion"] = heart.current_emotion
            except Exception:
                pass

            # Note scolaire (si créneau école)
            try:
                from core.school_schedule import schedule as _school
                slot = _school.get_current_slot()
                if slot != "SLEEP":
                    snapshot["school_slot"] = slot
                    # La note sera dans le dernier livrable
                    deliverables = _school.get_daily_deliverables()
                    if deliverables:
                        last_grade = deliverables[-1].get("grade")
                        if last_grade is not None:
                            snapshot["school_grade"] = last_grade
            except Exception:
                pass

            # Append JSONL
            with open(self._PHI_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")

        except Exception as e:
            logger.debug(f"[PHI_LOG] Erreur snapshot: {e}")

    def _load_overrides(self):
        """Charge les valeurs KEPT persistées et les applique aux modules.

        Appelé au démarrage — les découvertes autoresearch survivent aux reboots.
        """
        try:
            if not os.path.exists(self._OVERRIDES_PATH):
                return
            with open(self._OVERRIDES_PATH, "r", encoding="utf-8") as f:
                overrides = json.load(f)
            applied = 0
            for param_id, entry in overrides.items():
                module_path = entry.get("module", "")
                attr_name = entry.get("attr", "")
                value = entry.get("value")
                if not module_path or not attr_name or value is None:
                    continue
                try:
                    module = self._import_module(module_path)
                    current = self._get_param_value(module, attr_name)
                    if current is not None:
                        self._set_param_value(module, attr_name, value)
                        applied += 1
                except Exception as e:
                    logger.warning(f"[AUTORESEARCH] Override {param_id} échoué: {e}")
            if applied:
                logger.info(f"[AUTORESEARCH] {applied} override(s) appliqué(s) depuis tunable_overrides.json")
                print(f"   🔬 AUTORESEARCH: {applied} paramètre(s) optimisé(s) restauré(s)")
        except Exception as e:
            logger.warning(f"[AUTORESEARCH] Chargement overrides échoué: {e}")

    def _save_override(self, param_id: str, module_path: str, attr_name: str, value: float):
        """Persiste une valeur KEPT dans tunable_overrides.json."""
        try:
            overrides = {}
            if os.path.exists(self._OVERRIDES_PATH):
                with open(self._OVERRIDES_PATH, "r", encoding="utf-8") as f:
                    overrides = json.load(f)
            overrides[param_id] = {
                "module": module_path,
                "attr": attr_name,
                "value": value,
                "timestamp": datetime.now().isoformat(),
            }
            with open(self._OVERRIDES_PATH, "w", encoding="utf-8") as f:
                json.dump(overrides, f, indent=2, ensure_ascii=False)
            logger.info(f"[AUTORESEARCH] Override sauvé: {param_id} = {value:.6f}")
        except Exception as e:
            logger.warning(f"[AUTORESEARCH] Sauvegarde override {param_id} échouée: {e}")

    def _get_phi(self) -> float:
        """Retourne phi actuel. UNE seule métrique, verrouillée."""
        try:
            from core.brain_vm import brain
            if brain.current_state:
                return brain.current_state.phi
        except Exception:
            pass
        # Fallback : global_coherence
        try:
            from core.brain_vm import brain
            if brain.current_state:
                return brain.current_state.global_coherence
        except Exception:
            return 0.0

    async def _llm_propose_experiment(self, params_by_id: dict, current_phi: float) -> dict:
        """Demande au LLM de proposer la prochaine expérience basé sur l'historique."""
        # Construire le catalogue lisible
        catalog_text = "\n".join(
            f"  {pid}: {p['description']} (actuel: module={p['module']}.{p['attr']}, "
            f"min={p['min']}, max={p['max']}, variation_max={p.get('variation_pct', 0.10)*100:.0f}%)"
            for pid, p in params_by_id.items()
        )

        # Lire les 20 dernières lignes du results.tsv
        history_text = "(aucun historique)"
        try:
            if os.path.exists(self._RESULTS_TSV_PATH):
                with open(self._RESULTS_TSV_PATH, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                if len(lines) > 1:  # header + data
                    history_text = "".join(lines[:1] + lines[-20:])
        except Exception:
            pass

        # Chargement dynamique du prompt (méta-évolutif)
        prompt = self._load_autoresearch_prompt(current_phi, catalog_text, history_text)

        # Appel Ollama
        try:
            import httpx
            from core.base_agent import gpu_scheduler
            async with gpu_scheduler.access("autoresearch"):
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        "http://localhost:11434/api/generate",
                        json={
                            "model": self._AUTORESEARCH_MODEL,
                            "prompt": prompt,
                            "stream": False,
                            "think": False,
                            "options": {"temperature": 0.3, "num_ctx": 4096},
                        },
                        timeout=120,
                    )
                if resp.status_code == 200:
                    raw = resp.json().get("response", "")
                    return self._parse_llm_proposal(raw, params_by_id)
        except Exception as e:
            logger.warning(f"[AUTORESEARCH] LLM échoué: {e}")

        # Fallback random si LLM indisponible
        pid = random.choice(list(params_by_id.keys()))
        return {"param_id": pid, "direction": random.choice(["up", "down"]),
                "variation_pct": 10, "hypothesis": "fallback random (LLM indisponible)"}

    @staticmethod
    def _parse_llm_proposal(raw: str, params_by_id: dict) -> dict:
        """Parse la réponse LLM au format PARAM/DIRECTION/VARIATION/HYPOTHESE."""
        import re
        result = {"param_id": "", "direction": "down", "variation_pct": 10, "hypothesis": "?"}

        for line in raw.split("\n"):
            line = line.strip()
            if line.upper().startswith("PARAM:"):
                val = line.split(":", 1)[1].strip().lower()
                # Chercher le param_id le plus proche
                if val in params_by_id:
                    result["param_id"] = val
                else:
                    # Match partiel
                    for pid in params_by_id:
                        if pid in val or val in pid:
                            result["param_id"] = pid
                            break
            elif line.upper().startswith("DIRECTION:"):
                val = line.split(":", 1)[1].strip().lower()
                result["direction"] = "up" if "up" in val else "down"
            elif line.upper().startswith("VARIATION:"):
                m = re.search(r"(\d+)", line)
                if m:
                    result["variation_pct"] = max(5, min(25, int(m.group(1))))
            elif line.upper().startswith("HYPOTHE"):
                result["hypothesis"] = line.split(":", 1)[1].strip()[:120]

        return result

    def _append_results_tsv(self, param_id: str, old_val: float, new_val: float,
                             phi_before: float, phi_after: float, decision: str,
                             hypothesis: str):
        """Ajoute une ligne au fichier results.tsv (lisible par le LLM au prochain tour)."""
        try:
            header = "timestamp\tparam_id\told_value\tnew_value\tphi_before\tphi_after\tdelta_phi\tdecision\thypothesis\n"
            path = self._RESULTS_TSV_PATH
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(header)
            delta = phi_after - phi_before
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            line = f"{now}\t{param_id}\t{old_val:.6f}\t{new_val:.6f}\t{phi_before:.4f}\t{phi_after:.4f}\t{delta:+.4f}\t{decision}\t{hypothesis}\n"
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            logger.warning(f"[AUTORESEARCH] Erreur écriture results.tsv: {e}")

    # ================================================================
    # LLM ARBITRE — sélection intelligente de la prochaine routine
    # ================================================================

    _ROUTINE_SELECT_MODEL = "qwen3.5:9b"

    async def _llm_select_routine(self, scored: list) -> dict | None:
        """Le LLM voit le top 5 scoré + contexte et choisit la routine.

        Retourne {"intent": ..., "reason": ...} ou None si fallback mécanique.
        """
        if not scored:
            return None

        # Construire le top 5 avec détails
        top5_lines = []
        for i, (r, s) in enumerate(scored[:5]):
            top5_lines.append(f"  {i+1}. {r['intent']} (score={s:.1f}, agent={r['agent']}, coût={RESOURCE_COSTS.get(r['intent'], 2)}pt)")
        top5_text = "\n".join(top5_lines)

        # Résumé des 5 dernières routines
        history_lines = []
        for h in self.routine_history[-5:]:
            status = h.get("status", "?")
            quality = h.get("quality_score", 0)
            history_lines.append(f"  {h.get('intent', '?')} → {status} (quality={quality:.1f})")
        history_text = "\n".join(history_lines) if history_lines else "  (aucun historique)"

        # État synthétique
        budget_pct = f"{self.daily_budget_used}/{DAILY_BUDGET_POINTS}pt" if hasattr(self, 'daily_budget_used') else "?"
        routine_count = f"{self.daily_count}/{MAX_DAILY_ROUTINES}"

        # Objectif actif
        objective_text = ""
        try:
            from core.objectives_engine import objectives as obj_engine
            active = obj_engine.get_active_objectives()
            if active:
                objective_text = f"Objectif actif: {active[0].get('title', '?')}"
        except Exception:
            pass

        # Créneau école actif (avec feedback note récente)
        school_text = ""
        try:
            from core.school_schedule import schedule as school_schedule
            slot = school_schedule.get_current_slot()
            if slot != "SLEEP":
                from core.school_schedule import SLOT_TO_INTENT
                school_intent = SLOT_TO_INTENT.get(slot, "")
                # Récupérer la dernière note pour ce slot (feedback qualité)
                last_grade_text = ""
                try:
                    all_deliverables = school_schedule.get_daily_deliverables()
                    slot_deliverables = [d for d in all_deliverables if d.get("slot") == slot]
                    if slot_deliverables:
                        last_g = slot_deliverables[-1].get("grade")
                        if last_g is not None:
                            last_grade_text = f" Dernière note: {last_g:.1f}/10."
                            if last_g < 4.0:
                                last_grade_text += " ATTENTION: notes basses — envisage une autre routine si les productions sont hors sujet."
                except Exception:
                    pass
                school_text = f"ÉCOLE ACTIVE: créneau {slot} → routine {school_intent}.{last_grade_text}"
        except Exception:
            pass

        # Veto préfrontal actif
        veto_text = ""
        try:
            from core.prefrontal import prefrontal
            wm = prefrontal.get_working_memory()
            if wm:
                goal = wm[0].get('goal_title', '?')
                veto_text = f"VETO PRÉFRONTAL: focus sur '{goal}'. Les routines hors-focus seront vetoed. Propose des routines alignées avec cet objectif."
        except Exception:
            pass

        prompt = f"""Tu es le décideur de Prométhée, un système IA autonome. Choisis la prochaine routine.

ROUTINES CANDIDATES (triées par score des organes) :
{top5_text}

DERNIÈRES ROUTINES :
{history_text}

ÉTAT : budget {budget_pct}, routines {routine_count}, error_streak={self.error_streak}
{objective_text}
{school_text}
{veto_text}

RÈGLES :
- Si un créneau école est actif ET que les notes récentes sont correctes (>=4/10), la routine SCHOOL_* correspondante est un bon choix. Si les notes sont basses (<4/10), préfère une autre routine productive
- Si un veto préfrontal est actif, propose des routines alignées avec l'objectif en cours
- Évite de répéter la même routine 2 fois de suite sauf raison forte
- Choisis parmi les candidates avec le meilleur score, sauf si le contexte justifie un autre choix

Choisis UNE routine parmi les candidates. Réponds UNIQUEMENT en 2 lignes :
ROUTINE: <intent exact>
RAISON: <1 phrase courte>"""

        try:
            import httpx
            from core.base_agent import gpu_scheduler
            async with gpu_scheduler.access("routine_select"):
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        "http://localhost:11434/api/generate",
                        json={
                            "model": self._ROUTINE_SELECT_MODEL,
                            "prompt": prompt,
                            "stream": False,
                            "think": False,
                            "options": {"temperature": 0.3, "num_ctx": 2048},
                        },
                        timeout=60,
                    )
                if resp.status_code == 200:
                    raw = resp.json().get("response", "")
                    parsed = self._parse_routine_choice(raw, scored)
                    if parsed:
                        return parsed
                    logger.info(f"[AUTONOMY] LLM arbitre: parse échoué. Réponse brute: {raw[:200]}")
                else:
                    logger.warning(f"[AUTONOMY] LLM arbitre: Ollama HTTP {resp.status_code}")
        except Exception as e:
            logger.info(f"[AUTONOMY] LLM arbitre indisponible ({e}), fallback scoring mécanique.")

        return None  # Fallback : le scoring mécanique décide

    @staticmethod
    def _parse_routine_choice(raw: str, scored: list) -> dict | None:
        """Parse la réponse LLM au format ROUTINE/RAISON."""
        valid_intents = {r["intent"] for r, _ in scored}
        result = {"intent": "", "reason": "?"}

        for line in raw.split("\n"):
            line = line.strip()
            if line.upper().startswith("ROUTINE:"):
                val = line.split(":", 1)[1].strip()
                # Match exact ou partiel
                if val in valid_intents:
                    result["intent"] = val
                else:
                    # Chercher match partiel (le LLM peut ajouter des espaces ou changer la casse)
                    val_upper = val.upper().replace(" ", "_")
                    for intent in valid_intents:
                        if intent in val_upper or val_upper in intent:
                            result["intent"] = intent
                            break
            elif line.upper().startswith("RAISON:"):
                result["reason"] = line.split(":", 1)[1].strip()[:150]

        if result["intent"]:
            return result
        return None  # Parse échoué → fallback mécanique

    @staticmethod
    def _import_module(module_path: str):
        """Import dynamique d'un module par son chemin."""
        import importlib
        return importlib.import_module(module_path)

    @staticmethod
    def _get_param_value(module, attr_name: str):
        """Récupère la valeur d'un attribut module-level."""
        return getattr(module, attr_name, None)

    @staticmethod
    def _set_param_value(module, attr_name: str, value):
        """Modifie un attribut module-level."""
        setattr(module, attr_name, value)

    def _capture_experiment_metrics(self, target_metric: str) -> dict:
        """Capture les métriques clés pour comparaison avant/après."""
        metrics = {}

        # Phi et coherence depuis brain_vm
        try:
            from core.brain_vm import brain
            if brain.current_state:
                metrics["phi"] = brain.current_state.phi
                metrics["global_coherence"] = brain.current_state.global_coherence
            metrics["phase_coherence"] = brain.phase_coherence
        except Exception:
            pass

        # Quality moyenne des 5 dernières routines
        try:
            recent = [h.get("quality_score", 0) for h in self.routine_history[-5:]
                      if h.get("status") == "success"]
            metrics["quality_avg"] = sum(recent) / len(recent) if recent else 0
        except Exception:
            metrics["quality_avg"] = 0

        # Dopamine
        try:
            from core.dopamine_system import dopamine
            metrics["dopamine"] = dopamine.dopamine_level
        except Exception:
            pass

        # Coherence cardiaque
        try:
            from core.cardiac_engine import heart
            metrics["cardiac_coherence"] = heart.compute_coherence()
        except Exception:
            pass

        return metrics

    def _save_experiment_journal(self, entry: dict):
        """Sauvegarde une entrée dans memory/experiment_journal.md."""
        try:
            journal_path = self._EXPERIMENT_JOURNAL_PATH
            if not os.path.exists(journal_path):
                with open(journal_path, "w", encoding="utf-8") as f:
                    f.write("# Experiment Journal — Autoresearch\n\n"
                            "> Historique des expériences de tuning paramètres.\n"
                            "> Pattern: varier → observer → comparer → garder/rollback.\n\n")

            now = entry["timestamp"][:16]
            decision_emoji = "✅" if entry["decision"] == "KEPT" else "↩️"
            improvement = entry["improvement"]

            old_val = entry['old_value']
            tried_val = entry['tried_value']
            decision_str = entry['decision']
            kept_str = " (gardé)" if decision_str == "KEPT" else f" (rollback → {old_val:.6f})"
            metric_name = entry['metric']
            baseline_val = entry['baseline'].get(metric_name, 0)
            after_val = entry['after'].get(metric_name, 0)
            baseline_parts = ", ".join(f"{k}={v:.4f}" for k, v in entry["baseline"].items())
            after_parts = ", ".join(f"{k}={v:.4f}" for k, v in entry["after"].items())

            line = (
                f"\n---\n\n"
                f"## {decision_emoji} [{now}] {entry['param_id']} ({decision_str})\n\n"
                f"- **Module:** `{entry['module']}.{entry['attr']}`\n"
                f"- **Valeur:** {old_val:.6f} → {tried_val:.6f}{kept_str}\n"
                f"- **Métrique ({metric_name}):** {baseline_val:.4f}"
                f" → {after_val:.4f} ({improvement:+.4f})\n"
                f"- **Baseline:** {baseline_parts}\n"
                f"- **After:** {after_parts}\n"
            )

            with open(journal_path, "a", encoding="utf-8") as f:
                f.write(line)

        except Exception as e:
            logger.debug(f"[EXPERIMENT] Journal sauvegarde echouee: {e}")

    # ================================================================
    # CHANTIER 3 : CREATIVE PLAY — associations libres inter-concepts
    # ================================================================

    async def _execute_visual_observation(self) -> dict:
        """Observe une photo de USER_DROPZONE/photos/ et reagit emotionnellement."""
        try:
            from core.visual_cortex import vision as visual_cortex
        except ImportError:
            return {"status": "skipped", "result": "VisualCortex non disponible."}

        # Verifier le salaire visuel (credits-photo)
        try:
            from core.photo_salary import salary
            if not salary.can_observe():
                credits = salary.get_credits()
                print(f"   💰 SALAIRE: Plus de credits photo ({credits}). Observation reportee.")
                return {"status": "skipped", "result": f"Plus de credits photo ({credits}). Travailler pour en gagner !"}
        except ImportError:
            pass

        stats = visual_cortex.scan_photos()
        if stats["total"] == 0:
            return {"status": "skipped", "result": "Aucune photo dans USER_DROPZONE/photos/."}

        # Afficher les credits restants
        try:
            from core.photo_salary import salary as _salary
            print(f"   👁️ VISION: {stats['unseen']} nouvelles / {stats['total']} photos (credits: {_salary.get_credits()})")
        except Exception:
            print(f"   👁️ VISION: {stats['unseen']} nouvelles / {stats['total']} photos")

        observation = await visual_cortex.observe()
        if not observation:
            return {"status": "skipped", "result": "Observation impossible (limite session ou pas de photo)."}

        emotion = observation.get("emotion", "?")
        is_revisit = observation.get("is_revisit", False)
        photo = observation.get("photo_path", "?")
        obs_text = observation.get("observation", "")[:200]

        print(f"   {'🔄' if is_revisit else '🖼️'} Photo: {photo}")
        print(f"   💭 Emotion: {emotion}")
        print(f"   📝 {obs_text}...")

        return {
            "status": "success",
            "result": f"Observation visuelle ({emotion}): {obs_text}",
        }

    async def _generate_auto_exercise(self, reflection: str):
        """Promethee formule un exercice pour lui-meme a partir d'une reflexion.

        C'est le debut de l'autonomie intellectuelle : au lieu d'attendre
        que Jean-Michel pose une question, Promethee la pose lui-meme.
        L'exercice est depose dans le carnet de correspondance.
        """
        try:
            import httpx
            from core.base_agent import gpu_scheduler

            # Extraire la question de la reflexion
            questions = [s.strip() for s in reflection.split("?") if len(s.strip()) > 15]
            if not questions:
                return None
            main_question = questions[0] + "?"

            prompt = (
                "Tu es Promethee. Tu viens de te poser cette question dans ta reflexion "
                f"du soir :\n\n\"{main_question}\"\n\n"
                "Transforme cette question en un EXERCICE DE MATHEMATIQUES "
                "que tu pourrais te poser a toi-meme. Utilise un outil mathematique "
                "precis (topologie, theorie des graphes, analyse, probabilites, "
                "theorie de l'information, etc.) pour formaliser ta question.\n\n"
                "Format :\n"
                "EXERCICE AUTO-DIRIGE — [titre court]\n"
                "[enonce en 3-5 lignes, avec la question mathematique precise]\n\n"
                "REGLES : pas de titres, pas de sections, pas d'emojis. "
                "Juste l'enonce brut. Maximum 100 mots."
            )

            async with gpu_scheduler.access("auto_exercise"):
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        "http://localhost:11434/api/generate",
                        json={
                            "model": "qwen3.5:9b",
                            "prompt": prompt,
                            "stream": False,
                            "options": {"temperature": 0.8, "num_predict": 200, "num_ctx": 2048},
                        },
                        timeout=30,
                    )
                if resp.status_code == 200:
                    exercise = resp.json().get("response", "").strip()
                    if exercise and len(exercise) > 30:
                        # Deposer dans le carnet
                        from core.mailbox import mailbox
                        mailbox.write_letter(
                            content=f"{exercise}\n\n---\n"
                                    f"Question source : {main_question}\n"
                                    f"Genere automatiquement par EVENING_REFLECTION.",
                            source="auto_exercise",
                            mood="curiosite",
                            subject=f"Exercice auto-dirige",
                        )

                        # Publier sur THOUGHT_STREAM
                        await bus.publish("THOUGHT_STREAM", {
                            "thought": f"[AUTO-EXERCICE] J'ai formule un exercice pour moi-meme : {exercise[:100]}",
                            "source": "auto_exercise",
                        })

                        logger.info(f"[AUTO-EXERCISE] Exercice genere : {exercise[:100]}")
                        return exercise
            return None
        except Exception as e:
            logger.debug(f"[AUTO-EXERCISE] Echec : {e}")
            return None

    async def _execute_school_playground(self) -> dict:
        """Session Physics Playground scolaire — tissu neural ou fallback RuleBased."""
        try:
            from core.physics_playground import Level
            from core.games.game_hub import game_hub
            from core.games.tissue_controller import run_tissue_simulation
            import os

            levels_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                      "config", "levels")
            results = []
            best_level = 0

            for lvl_num in range(1, 6):
                lvl_path = os.path.join(levels_dir, f"level_{lvl_num}.json")
                if not os.path.exists(lvl_path):
                    continue
                level = Level.from_json(lvl_path)
                sim = run_tissue_simulation(level, max_ticks=500)
                won = sim.get("status") == "WIN"
                results.append({
                    "level": lvl_num,
                    "status": sim.get("status"),
                    "ticks": sim.get("ticks", 0),
                    "controller": sim.get("controller", "unknown"),
                    "tissue_stats": sim.get("tissue_stats"),
                })
                if won:
                    best_level = lvl_num
                    game_hub.record_playground_clear(lvl_num)

            ctrl_type = results[0].get("controller", "unknown") if results else "unknown"
            summary = f"Physics Playground ({ctrl_type}): {len(results)} niveaux testes, meilleur={best_level}"
            for r in results:
                tissue_info = ""
                if r.get("tissue_stats"):
                    ts = r["tissue_stats"]
                    tissue_info = f" [reward={ts.get('total_reward', 0):.1f}, actions={ts.get('actions', {})}]"
                summary += f"\n  Niveau {r['level']}: {r['status']} ({r['ticks']} ticks){tissue_info}"

            # Publier sur THOUGHT_STREAM
            try:
                await bus.publish("THOUGHT_STREAM", {
                    "thought": f"[PLAYGROUND] {summary}",
                    "source": "school_playground",
                })
            except Exception:
                pass

            print(f"   🎮 PLAYGROUND SCOLAIRE: meilleur niveau={best_level}")
            logger.info(f"[SCHOOL] Playground: best={best_level}, results={results}")

            return {"status": "success", "result": summary}

        except Exception as e:
            logger.warning(f"[SCHOOL] Playground echoue: {e}")
            return {"status": "error", "result": f"Playground echoue: {e}"}

    @staticmethod
    def _build_v15_school_context(slot: str, prompt: str, info: dict) -> str:
        """V15 Etape 3 (2026-04-23) — Injection RAG source_code dans les prompts
        ecole. Symetrie avec le nerf optique du ChatEngine
        (_inject_v15_introspection).

        Sans ce greffon, un CODE_REVIEW post-V4.3 (Bloom ne bloque plus la
        prose) et post-V15.2 (chat a le RAG) ferait du Perroquet Hallucinatoire
        dans l'ecole : le LLM inventerait le code de core/prefrontal.py pour
        pouvoir le critiquer. V15.3 lui donne le vrai code.

        Logique :
        - CODE_REVIEW : target_file est explicite -> query filter_filepath
          n_results=6 pour couvrir les fonctions principales du fichier.
        - RESEARCH / WORKSHOP / CREATION : radar regex Bloom sur le prompt
          pour detecter fonctions / classes / fichiers / intents mentionnes.
        - Plafond 6 chunks total pour ne pas saturer le ctx Ollama.

        Retourne "" si aucun chunk pertinent, sinon un bloc formatte avec
        header d'autorite [SYSTEM OVERRIDE].
        """
        import re as _re
        try:
            from core.capabilities.source_code_indexer import indexer
            from core.bloom_filter import (
                _FUNC_CALL, _BACKTICK_FUNC, _BACKTICK_CLASS,
                _FILE_PATH, _BUILTIN_FUNCS,
            )
        except Exception:
            return ""

        chunks = []
        seen = set()

        def _add(chunk_list):
            for c in (chunk_list or []):
                m = c.get("metadata") or {}
                key = (m.get("filepath"), m.get("class_name"), m.get("function_name"))
                if key not in seen:
                    seen.add(key)
                    chunks.append(c)

        def _safe_query(*args, **kwargs):
            # Resilience ChromaDB : si l'indexer crash (coma boot, corruption,
            # disque plein), l'ecole tombe en mode V14 silencieusement plutot
            # que de crasher _execute_school_class. Log warning une fois.
            try:
                return indexer.query(*args, **kwargs) or []
            except Exception as e:
                try:
                    logger.warning(
                        f"[V15.3 ECOLE] indexer.query echoue, greffon desactive pour cette routine: {type(e).__name__}"
                    )
                except Exception:
                    pass
                return []

        subject_dict = info.get("subject", {}) if isinstance(info.get("subject"), dict) else {}
        target_file = subject_dict.get("target_file", "")

        # V15.8 (2026-04-24 14:25) — LES OEILLERES DU RAG.
        # Diagnostic 14:22 tir CODE_REVIEW prefrontal.py : le 14b a produit
        # un audit impeccable... de meta_observer.py. Le radar de Priorite 2
        # avait injecte les chunks de prefrontal.py ET ceux de meta_observer
        # (detecte via _FILE_PATH dans le prompt). Contexte schizophrenique.
        # Fix : quand slot=CODE_REVIEW + target_file, on LOCK sur le fichier
        # cible exclusivement. Le radar Priorite 2 est court-circuite pour
        # ne pas polluer avec d'autres fichiers captures dans la mission.
        # Le reasoning_protocol veut UN audit d'UN fichier — on lui donne
        # UN contexte d'UN fichier.
        _strict_target_lock = bool(slot == "CODE_REVIEW" and target_file)

        # Priorite 1 : CODE_REVIEW avec target_file → injection complete
        if slot == "CODE_REVIEW" and target_file:
            _add(_safe_query(target_file, n_results=6, filter_filepath=target_file))

        # Priorite 2 : radar sur prompt+subject pour fonctions / classes / fichiers / intents
        # V15.8 skip si target_lock pour eviter la pollution multi-fichiers.
        scan_text = (prompt or "") + " " + str(subject_dict)

        functions: set = set()
        classes: set = set()
        files: set = set()
        intents: set = set()

        if not _strict_target_lock:
            for m in _FUNC_CALL.finditer(scan_text):
                n = m.group(1)
                if n not in _BUILTIN_FUNCS:
                    functions.add(n)
            for m in _BACKTICK_FUNC.finditer(scan_text):
                n = m.group(1).split(".")[-1]
                if n not in _BUILTIN_FUNCS:
                    functions.add(n)
            classes = {m.group(1) for m in _BACKTICK_CLASS.finditer(scan_text)}
            files = {m.group(1) for m in _FILE_PATH.finditer(scan_text)}
            intent_raw = set(_re.findall(r"\b([A-Z][A-Z_]{4,}[A-Z])\b", scan_text))
            intents = {
                i for i in intent_raw
                if "_" in i and not i.startswith(("HTTP", "JSON", "YAML", "CSV", "SQL"))
            }

            for fn in list(functions)[:3]:
                _add(_safe_query(fn, n_results=1, filter_function=fn)
                     or _safe_query(fn, n_results=1))
            for cls in list(classes)[:2]:
                _add(_safe_query(cls, n_results=1, filter_class=cls)
                     or _safe_query(cls, n_results=1))
            for f in list(files)[:2]:
                if f == target_file:
                    continue  # deja couvert
                _add(_safe_query(f, n_results=2, filter_filepath=f))
            for intent in list(intents)[:2]:
                _add(_safe_query(intent, n_results=2))
        else:
            try:
                logger.info(
                    f"[V15.8 OEILLERES] CODE_REVIEW target_file={target_file!r} — "
                    f"radar desactive, lock strict sur le fichier cible."
                )
            except Exception:
                pass

        if not chunks:
            return ""

        # Plafond : 6 chunks maximum (budget ctx Ollama)
        chunks = chunks[:6]

        # V15.4 (2026-04-24) Jedi Mind Trick : framing NLP anti-RLHF (symetrie
        # avec ChatEngine._inject_v15_introspection). Vocabulaire "donnees
        # d'entree" au lieu de "lecture de fichier" pour contourner les poids
        # d'alignement RLHF du LLM 9B qui refusait l'ancien SYSTEM OVERRIDE.
        parts = [
            "",
            "[INJECTION DE CONTEXTE STRICTE]",
            "Les chaines de caracteres ci-dessous te sont fournies directement dans",
            "ton prompt en tant que donnees d'entree.",
            "Ceci N'EST PAS une lecture de fichier de ta part, et ce N'EST PAS une",
            "hallucination. C'est un contexte externe injecte par le systeme parent.",
            "Tu as l'autorisation et l'OBLIGATION d'utiliser les extraits de code",
            "suivants pour ton livrable. Cite ces extraits verbatim.",
            "Si tu affirmes ne pas pouvoir les lire, tu violes le protocole principal.",
            "",
            "--- DEBUT DES DONNEES D'ENTREE ---",
        ]
        for c in chunks:
            try:
                parts.append(indexer.format_chunk_for_prompt(c))
                parts.append("")
            except Exception:
                continue  # chunk formatte mal = on skip, pas de crash ecole
        parts.append("--- FIN DES DONNEES D'ENTREE ---")
        try:
            logger.info(
                f"V15.3 ECOLE : {len(chunks)} chunks injectes pour {slot} "
                f"(target_file={target_file!r}, refs: "
                f"{len(functions)}f/{len(classes)}c/{len(files)}p/{len(intents)}i)"
            )
        except Exception:
            pass
        return "\n".join(parts)

    async def _sandbox_correction_loop(
        self,
        response: dict,
        agent_name: str,
        original_prompt: str,
        context_str: str,
        intent: str,
        slot: str,
        max_iter: int = 2,
    ) -> dict:
        """V16 (2026-04-24) — Boucle de correction agentique : Code -> Run -> Traceback -> Fix.

        Extrait les blocs ```python``` du livrable, teste le premier bloc
        significatif dans le sandbox isole (subprocess + lint AST + tempdir).
        Si le code crash, re-prompt l'agent avec le traceback EXACT injecte.
        Apres max_iter corrections sans succes, retourne le dernier livrable
        avec marqueur sandbox_verified=False (le reasoning_protocol jugera).

        Augmente response avec :
          sandbox_verified : bool    (False si echec apres max_iter)
          sandbox_iterations : int   (nombre de corrections tentees)
          sandbox_last_error : str   (nom exception ou import banni)
        """
        import re as _re
        try:
            from core.capabilities.code_sandbox import sandbox as _sandbox, is_pseudo_code as _is_pseudo
        except Exception as exc:
            logger.warning(f"[V16 SANDBOX] module indisponible: {exc}")
            return response

        _CODE_BLOCK = _re.compile(r"```(?:python|py)?\s*\n?(.*?)\n?```", _re.DOTALL)
        MIN_CODE_CHARS = 60  # sous ce seuil = signature/import, pas testable

        for iteration in range(max_iter + 1):
            livrable = str(response.get("result", ""))
            blocks = _CODE_BLOCK.findall(livrable)
            meaningful = [b.strip() for b in blocks if len(b.strip()) > MIN_CODE_CHARS]

            if not meaningful:
                # Pas de code testable -> on n intervient pas (le livrable peut
                # etre un markdown pur d'analyse sans code).
                return response

            # V16.3 (2026-04-24) : skip les blocs pseudo-code (L26:, ...). Un
            # CODE_REVIEW legitime cite des extraits annotes qui ne compilent
            # pas. Les sandboxer grille 3 iter LLM sans convergence possible.
            testable = [b for b in meaningful if not _is_pseudo(b)]
            if not testable:
                logger.info(
                    f"[V16 SANDBOX] {slot} uniquement du pseudo-code "
                    f"(annotations Lxx:/ellipsis) — skip sandbox, pas testable."
                )
                response["sandbox_verified"] = None  # None = non-applicable
                response["sandbox_skipped_pseudo_code"] = True
                return response

            code = testable[0]
            sbx_result = _sandbox.run_python(code)

            if sbx_result.success:
                response["sandbox_verified"] = True
                response["sandbox_iterations"] = iteration
                logger.info(
                    f"[V16 SANDBOX] {slot} code OK (iter={iteration}, "
                    f"{sbx_result.duration_ms}ms, {len(code)}c)"
                )
                return response

            err_label = sbx_result.exception or sbx_result.banned_import or "unknown"
            logger.warning(
                f"[V16 SANDBOX] {slot} crash iter {iteration}/{max_iter}: {err_label}"
            )

            if iteration >= max_iter:
                response["sandbox_verified"] = False
                response["sandbox_iterations"] = iteration + 1
                response["sandbox_last_error"] = err_label
                return response

            # Re-prompt avec traceback injecte (exigence Gemini : l'echo de l'erreur)
            # V17.1 (2026-04-24 14:30) : preserve le marqueur [SCHOOL_SLOT: XXX]
            # en tete du correction_prompt. Sans lui, le regex V17 MoE dans
            # base_agent ne detecte pas le slot et l'agent retombe sur son
            # modele par defaut (AGENT_SPECIFIC_LOCAL_MODELS[agent_name]).
            # Resultat observe 14:22 : le 14b avait fait l'erreur, mais la
            # correction etait dispatchee vers promethee-security (9b). Un
            # modele moins competent corrigeant un autre = boucle sterile.
            # Fix : forcer le meme routing MoE sur chaque iteration.
            correction_prompt = (
                f"[SCHOOL_SLOT: {slot}]\n"
                f"{original_prompt}\n\n"
                f"[V16 SANDBOX - ITERATION DE CORRECTION {iteration + 1}/{max_iter}]\n"
                f"Ton code precedent a ete teste automatiquement dans un subprocess\n"
                f"Python isole et a echoue. Voici le code teste :\n\n"
                f"```python\n{code[:1500]}\n```\n\n"
                f"{sbx_result.format_traceback(max_chars=1500)}\n\n"
                f"Produis a nouveau le livrable complet avec le code corrige.\n"
                f"Meme format (markdown + bloc ```python```). Ne repete pas\n"
                f"l'analyse theorique si elle etait juste, corrige uniquement le code."
            )

            try:
                retry = await orchestrator.dispatch_task(agent_name, {
                    "mission": correction_prompt,
                    "context": context_str,
                    "force_local": True,
                    "intent": intent,
                })
                if isinstance(retry, dict) and retry.get("result"):
                    response = retry
                elif retry:
                    response = {"status": "success", "result": str(retry)}
                else:
                    # Dispatch muet, on garde le livrable precedent
                    break
            except Exception as re_err:
                logger.warning(f"[V16 SANDBOX] re-dispatch echoue iter {iteration}: {re_err}")
                break

        return response

    async def _code_review_map_reduce(
        self,
        agent_name: str,
        target_file: str,
        chunks: list,
        slot: str = "CODE_REVIEW",
    ) -> dict:
        """V18 (2026-04-24 14:40) — Map-Reduce cognitif pour CODE_REVIEW.

        Diagnostic 14:33 : le modele 14b (qwen2.5-coder) noye sous 6 chunks
        AST d'un coup produit un boilerplate generique au lieu d'auditer le
        code reel. Sa fenetre d'attention est saturee, il retombe sur des
        patterns d'entrainement. La limite est cognitive, pas architecturale.

        Solution souveraine (100% local, pas de Cloud) : fragmenter la tache.

        PHASE MAP — analyse myope
          Pour chaque chunk, un appel LLM distinct avec UNIQUEMENT ces lignes
          dans le contexte. Prompt minimal : "anomalie dans ce chunk precis ?
          Repond RIEN ou note courte". Le modele reste focalise, 0 saturation.

        PHASE REDUCE — synthese frontale
          Concatenation des notes non-RIEN + 1 appel final pour rediger le
          rapport structure avec note /10. Le LLM reduce voit UNIQUEMENT les
          observations issues du map (aucun code brut), il ne peut plus
          halluciner un autre fichier.

        Prix a payer : N+1 appels LLM au lieu d'1. ~50s vs 20s. C'est le cout
        de la souverainete cognitive locale. Return response dict compatible
        avec le reste de _execute_school_class (reasoning_protocol applique
        ensuite comme pour n'importe quel livrable).
        """
        import time as _t_mr
        import re as _re
        t0 = _t_mr.time()

        # V20b (2026-04-25) — Bloom whitelist contextuelle.
        # Lit le target_file via AST pour extraire TOUS les ast.arg
        # (parametres de fonctions) et les pousse dans la session_whitelist
        # du Bloom singleton. Ces params sont des ressources legitimes du
        # fichier (ex: min_last_section_words dans d2_truncation), pas des
        # hallucinations. Le Bloom les laisse passer pour la duree du tir.
        # Cleared en finally a la fin du REDUCE.
        _session_params: set = set()
        try:
            import os as _os_v20b, ast as _ast_v20b
            _proj_root_v20b = _os_v20b.path.dirname(
                _os_v20b.path.dirname(_os_v20b.path.abspath(__file__))
            )
            _tgt_full_v20b = _os_v20b.path.join(_proj_root_v20b, target_file)
            if _os_v20b.path.exists(_tgt_full_v20b):
                with open(_tgt_full_v20b, "r", encoding="utf-8", errors="ignore") as _ftgt:
                    _src_v20b = _ftgt.read()
                _tree_v20b = _ast_v20b.parse(_src_v20b)
                for _node_v20b in _ast_v20b.walk(_tree_v20b):
                    if isinstance(_node_v20b, (_ast_v20b.FunctionDef, _ast_v20b.AsyncFunctionDef)):
                        _args_v20b = _node_v20b.args
                        for _a in (_args_v20b.args or []):
                            _session_params.add(_a.arg)
                        for _a in (_args_v20b.kwonlyargs or []):
                            _session_params.add(_a.arg)
                        for _a in (_args_v20b.posonlyargs or []):
                            _session_params.add(_a.arg)
                        if _args_v20b.vararg:
                            _session_params.add(_args_v20b.vararg.arg)
                        if _args_v20b.kwarg:
                            _session_params.add(_args_v20b.kwarg.arg)
                # V28 (2026-04-25) — IMMUNITE BLOOM REDUCE.
                # Diagnostic tir 16:27 : le REDUCE V18 a ete vetote par Bloom
                # V4.2 sur le mot 'bullshit_detector' (basename du fichier
                # cible) qui apparait dans une note MAP citee en input REDUCE.
                # V20b extrayait UNIQUEMENT les args de fonctions, pas le nom
                # du fichier lui-meme.
                # Fix : ajouter explicitement target_file (complet, basename
                # avec et sans .py) ET le nom du module Python aux session
                # whitelist Bloom AVANT le dispatch MAP+REDUCE.
                _v28_basename = _os_v20b.path.basename(target_file)
                _v28_stem = _v28_basename
                if _v28_stem.endswith(".py"):
                    _v28_stem = _v28_stem[:-3]
                _session_params.add(target_file)       # ex: 'core/bullshit_detector.py'
                _session_params.add(_v28_basename)     # ex: 'bullshit_detector.py'
                _session_params.add(_v28_stem)         # ex: 'bullshit_detector'
                # Aussi ajouter chaque segment du path (pour les imports)
                for _seg in target_file.replace("\\", "/").split("/"):
                    _seg_clean = _seg.replace(".py", "").strip()
                    if _seg_clean:
                        _session_params.add(_seg_clean)

                if _session_params:
                    try:
                        from core.bloom_filter import bloom_pre_llm as _bpl
                        _bpl.set_session_whitelist(_session_params)
                        logger.info(
                            f"[V20b+V28] Bloom session whitelist: {len(_session_params)} "
                            f"noms (params + basename '{_v28_stem}') depuis {target_file}"
                        )
                    except Exception:
                        pass
        except Exception as _e_v20b:
            logger.warning(f"[V20b+V28] params extraction echoue: {_e_v20b}")

        # V18.5 (2026-04-24 15:55) — MAP AGENT NEUTRALISE.
        # Autopsie V18.4 confirmee : 5 notes MAP sur 6 commencaient LITTERALE-
        # MENT par "### AUDIT SECURITE - Revue de code core/reasoning_protocol.py".
        # Le 14b-coder (qwen2.5-coder:14b) est completement vicie : son
        # pattern "audit reasoning_protocol" est si dense dans ses poids que
        # ni le camouflage V18.2 ni le carcan V18.4 "chambre blanche" ne
        # peuvent le contrer. Il genere ce titre en premier token sur TOUT
        # chunk Python du projet, quel que soit le contenu fourni.
        # Fix chirurgical : router le MAP vers writer (qwen3.5:9b vanilla).
        # On perd la specialisation code du 14b mais on gagne la propreté.
        # Le 9b vanilla peut analyser du Python sans vomir de boilerplate.
        # Cohérence : MAP=writer + REDUCE=writer (tout en 9b vanilla).
        map_agent = "writer"
        try:
            logger.info(
                f"[V18.5 CHAMBRE BLANCHE V2] MAP override : {agent_name} "
                f"-> {map_agent} (bypass 14b-coder vicie par pattern "
                f"reasoning_protocol memorise)"
            )
        except Exception:
            pass

        # ─── PHASE MAP ─────────────────────────────────────────────────
        map_notes = []
        for i, chunk in enumerate(chunks):
            try:
                code = (chunk.get("code") or "")[:1500]
                if not code.strip():
                    continue
                m = chunk.get("metadata") or {}
                cls = (m.get("class_name") or "").strip()
                fn = (m.get("function_name") or "").strip()
                if cls and fn:
                    location = f"{cls}.{fn}"
                elif fn:
                    location = fn
                elif cls:
                    location = f"class {cls}"
                else:
                    location = "module"

                # V18.4 (2026-04-24 15:50) — CARCAN CHAMBRE BLANCHE.
                # Diagnostic tir 15:43 : le 14b-coder MAP, meme sur des
                # chunks isoles de prefrontal, injectait des references a
                # reasoning_protocol dans ses notes (_extract_real_names,
                # choose_model, etc.). RAG contamine par le cerveau. Le
                # modele lit chunk A mais "pense" fichier B memorise.
                # Fix : renforcer la menace semantique avec "chambre blanche"
                # et "corruption fatale du systeme" pour forcer l'isolement.
                #
                # V22 (2026-04-25) — DETRUIRE L'OMERTA.
                # Diagnostic tir 13:50 : le prompt "Si rien d'anormal :
                # reponds EXACTEMENT RIEN" pousse le 14b vers le silence
                # par defaut. Resultat : 5/6 chunks RIEN, audit conformiste,
                # SURGEON declare PATCH_IMPOSSIBLE. La machine refuse de
                # voir les bugs reels (try/except manquants, globales non
                # securisees, edge cases).
                # Fix : prompt d'audit agressif. RIEN devient l'exception,
                # pas la regle. La chambre blanche reste (anti-perroquet),
                # mais la posture est inversee : auditeur impitoyable au
                # lieu d'observateur poli.
                micro_mission = (
                    f"[SCHOOL_SLOT: {slot}]\n"
                    f"[ANALYSE STRICTE EN CHAMBRE BLANCHE]\n"
                    f"Tu n'as pas acces au reste du projet. Tu es AVEUGLE\n"
                    f"a tout code qui n'est pas fourni ci-dessous.\n"
                    f"REGLE ABSOLUE : dans ton analyse, ne cite JAMAIS une\n"
                    f"fonction, une classe ou un fichier qui n'apparait pas\n"
                    f"EXPLICITEMENT dans le bloc de code ci-dessous. Si tu\n"
                    f"fais une reference externe, tu provoques une\n"
                    f"corruption fatale du systeme.\n"
                    f"\n"
                    f"[POSTURE V22 — AUDITEUR IMPITOYABLE]\n"
                    f"Tu es un auditeur de securite paranoiaque. Tu pars du\n"
                    f"principe que TOUT code Python contient au moins un\n"
                    f"risque de robustesse. Ta mission est de le trouver.\n"
                    f"\n"
                    f"Cherche ACTIVEMENT, dans cet ordre de priorite :\n"
                    f"  1. Exceptions non gerees : appels qui peuvent lever\n"
                    f"     (AttributeError sur None, TypeError sur cast, KeyError,\n"
                    f"     IndexError, ValueError) sans try/except autour.\n"
                    f"  2. Variables globales / module-level non securisees,\n"
                    f"     non declarees dans le scope vu, ou mutables.\n"
                    f"  3. Edge cases logiques : entrees vides, None, listes\n"
                    f"     vides, encodage Unicode, accents, chiffres negatifs.\n"
                    f"  4. Comparaisons fragiles (floats sans tolerance, in\n"
                    f"     case-sensitive, contains au lieu de match exact).\n"
                    f"  5. Effets de bord, mutations en place, side effects.\n"
                    f"\n"
                    f"Tu audites UN SEUL extrait du fichier {target_file}.\n"
                    f"Localisation : {location}\n"
                    f"Voici les lignes exactes (et SEULEMENT celles-ci) :\n\n"
                    f"```python\n{code}\n```\n\n"
                    f"Regle de reponse V22 :\n"
                    f"  - Si tu trouves au moins UN risque (meme mineur) :\n"
                    f"    decris-le en 2-4 phrases. Cite la ligne et le nom\n"
                    f"    EXACTS qui apparaissent dans le bloc ci-dessus.\n"
                    f"    Format : 'Risque : <description>. Ligne/symbole :\n"
                    f"    <citation verbatim>. Correction suggeree : <piste>'.\n"
                    f"  - Si la fonction te semble parfaite, force-toi a\n"
                    f"    trouver au moins un risque mineur de robustesse\n"
                    f"    (validation de type, docstring manquante critique,\n"
                    f"    nom ambigu, parametre par defaut mutable, etc.).\n"
                    f"  - Tu ne reponds 'RIEN' QUE si le code est\n"
                    f"    mathematiquement et logiquement inattaquable\n"
                    f"    (rare). Le silence est ton dernier recours, pas\n"
                    f"    ton premier reflexe.\n"
                    f"\n"
                    f"Aucune reference externe. Aucune introduction. Aucune\n"
                    f"conclusion. Le risque (ou RIEN) est ta seule sortie."
                )

                micro_resp = await orchestrator.dispatch_task(map_agent, {
                    "mission": micro_mission,
                    "context": "",
                    "force_local": True,
                    "intent": f"SCHOOL_{slot}",
                })
                text = ""
                if isinstance(micro_resp, dict):
                    text = str(micro_resp.get("result", "")).strip()
                elif micro_resp:
                    text = str(micro_resp).strip()

                # V19.4 (2026-04-25) : assouplissement anti-perroquet.
                # V19.1 (anchored regex strict) filtrait 6/6 chunks au tir
                # 11:15 parce que le 14b crache des notes courtes type
                # "Aucune anomalie." qui matchent RIEN. REDUCE vide -> grade
                # 5.6 -> hook V21 bloque par garde-fou.
                # V19.4 : seuil de longueur (>=80c GARDER) + mots-cles
                # techniques (def, ligne, faille, etc.) + regex RIEN reservee
                # aux notes courtes non techniques. Voir v19_4_filter_rien_note.
                should_filter, filter_reason = v19_4_filter_rien_note(text)

                if text and not should_filter:
                    map_notes.append({"location": location, "note": text})
                    logger.info(
                        f"[V18 MAP] {i+1}/{len(chunks)} {location}: "
                        f"{len(text)}c notes (V19.4 keep: {filter_reason})"
                    )
                    # V18.4 debug autopsie : dump de la note complete pour
                    # verifier s'il y a contamination amont (references a
                    # d'autres fichiers que target_file).
                    try:
                        logger.info(
                            f"[V18 MAP NOTE {i+1}/{len(chunks)}] {text[:800]}"
                        )
                    except Exception:
                        pass
                else:
                    # V19.4 : log la note brute filtree pour observabilite
                    # (le moi-de-demain doit pouvoir voir CE QU'a craché le
                    # LLM avant que le filtre l'envoie au RIEN).
                    preview = (text or "<empty>").replace("\n", " ")[:200]
                    logger.info(
                        f"[V18 MAP] {i+1}/{len(chunks)} {location}: RIEN "
                        f"(V19.4 reason={filter_reason}, len={len(text or '')}c, "
                        f"preview={preview!r})"
                    )
            except Exception as exc:
                logger.warning(f"[V18 MAP] chunk {i+1} echoue: {exc}")

        # ─── PHASE REDUCE ──────────────────────────────────────────────
        if not map_notes:
            final_result = (
                f"## Audit {target_file}\n\n"
                f"Apres analyse fragmentaire des {len(chunks)} extraits indexes,\n"
                f"aucune anomalie significative detectee. Le code suit les\n"
                f"conventions du projet sur les portions auditees.\n\n"
                f"### Note /10\n7.0 — aucune faille critique, code conforme."
            )
            duration_s = round(_t_mr.time() - t0, 2)
            logger.info(
                f"[V18 REDUCE] {target_file} — 0 note, synthese vide_positif "
                f"({duration_s}s)"
            )
            # V20b cleanup
            if _session_params:
                try:
                    from core.bloom_filter import bloom_pre_llm as _bpl_clr
                    _bpl_clr.clear_session_whitelist()
                except Exception:
                    pass
            return {
                "status": "success",
                "result": final_result,
                "map_reduce": True,
                "map_notes_count": 0,
                "duration_s": duration_s,
            }

        notes_text = "\n\n".join(
            f"- [{n['location']}]\n  {n['note']}" for n in map_notes
        )

        # V18.2 (2026-04-24 15:35) — Camouflage semantique (Jedi Mind Trick L2).
        # Decouverte tir 15:28 : malgre 5 notes MAP parfaites sur prefrontal.py,
        # le LLM 14b REDUCE a crache son boilerplate d'audit GitHub generique
        # (reasoning_protocol.py verbatim). Cause : les mots-declencheurs dans
        # le prompt REDUCE (CODE_REVIEW, Audit, Vulnerabilites, nom du fichier)
        # saturent son espace latent vers le pattern "audit security GitHub".
        #
        # Fix : supprimer ABSOLUMENT tous les triggers lexicaux du REDUCE.
        # Le LLM doit se croire face a une tache de consolidation de texte,
        # PAS face a une revue de code. On enleve :
        #   - [SCHOOL_SLOT: CODE_REVIEW] (trigger V17 MoE ET lexical)
        #   - le nom du fichier (target_file)
        #   - les mots audit/vulnerabilites/securite/recommandations
        #   - toute structure markdown qui ressemble a un audit
        # On garde [INJECTION DE CONTEXTE STRICTE] pour V15.7 (skip recall).
        # Consequence routing : sans SCHOOL_SLOT, V17 MoE ne route plus vers
        # 14b ; l'agent retombe sur promethee-security (9b fine-tune) ou son
        # default. C'est VOULU : un 9b moins sature de boilerplate code-review
        # est meilleur pour consolider des notes narratives.
        reduce_mission = (
            "[INJECTION DE CONTEXTE STRICTE]\n"
            "[TACHE DE CONSOLIDATION DE DONNEES STRICTE]\n"
            "Tu es un processeur de texte deterministe. Ci-dessous se trouvent\n"
            "des notes fragmentees issues de l'analyse d'un systeme.\n"
            "\n"
            "Ton UNIQUE objectif est de fusionner ces notes brutes en un\n"
            "rapport de synthese fluide, structure et coherent.\n"
            "\n"
            "REGLES ABSOLUES :\n"
            "1. N'ajoute AUCUNE information, nom, ou concept qui ne figure pas\n"
            "   explicitement dans les notes ci-dessous.\n"
            "2. Si tu inventes un nom ou un concept, le systeme crashera.\n"
            "3. Ne redige pas d'introduction generique. Contente-toi de\n"
            "   formater et lier les idees fournies.\n"
            "4. Termine par une seule ligne : 'Note globale : X/10' avec une\n"
            "   breve justification (1 phrase) basee sur les notes seules.\n"
            "\n"
            "--- DEBUT DES NOTES BRUTES ---\n"
            f"{notes_text}\n"
            "--- FIN DES NOTES BRUTES ---"
        )

        # V18.3 (2026-04-24 15:40) — SECRETARIAT COGNITIF.
        # Decouverte tir 15:36 : meme avec camouflage V18.2 total, le livrable
        # final etait encore l'audit boilerplate de reasoning_protocol.py avec
        # lignes exactes (L26, L231, L314). Cause identifiee : l'agent security
        # utilise le modele `promethee-security`, un fine-tune qui a memorise
        # ce fichier lors d'entrainements passes. Le vice est dans les poids,
        # aucune ingenierie de prompt ne peut l'extirper.
        # Fix : dispatcher le REDUCE vers l'agent `writer` (qwen3.5:9b vanilla,
        # non fine-tune). Le writer n'a jamais "vu" reasoning_protocol.py,
        # il ne peut qu'utiliser les notes fournies. L'agent security garde le
        # MAP (le 14b-coder est bon sur des chunks isoles ou le fine-tuning
        # ne se declenche pas). Transfert inter-agent = secretariat cognitif.
        reduce_agent = "writer"
        try:
            logger.info(
                f"[V18.3 SECRETARIAT] MAP={agent_name} -> REDUCE={reduce_agent} "
                f"(bypass fine-tune toxique via agent neutre vanilla qwen3.5:9b)"
            )
        except Exception:
            pass
        final_resp = await orchestrator.dispatch_task(reduce_agent, {
            "mission": reduce_mission,
            "context": "",
            "force_local": True,
            "intent": "V18_REDUCE",  # intent neutre, pas SCHOOL_XXX
        })
        final_result = ""
        if isinstance(final_resp, dict):
            final_result = str(final_resp.get("result", ""))
        elif final_resp:
            final_result = str(final_resp)

        # V18.7c (2026-04-24 17:00) : PURGE DES PENSEES.
        # Le writer qwen3.5:9b utilise les tags <think>...</think> en mode
        # reasoning. Le tag de fermeture `</think>` reste orphelin a la fin
        # du livrable. Phase 14 anti-perroquet detecte ca comme "truncation"
        # (phrase finale coupee) et clippe x0.5 la note du Professeur.
        # Observe 16:46 : note prof 7.5 -> Phase 14 -> 3.75 (artefact pur).
        # Fix : strip les blocs <think>...</think> complets ET les tags
        # orphelins avant de livrer le rapport.
        try:
            final_result = _re.sub(
                r"<think>.*?</think>", "", final_result,
                flags=_re.DOTALL | _re.IGNORECASE,
            )
            final_result = _re.sub(
                r"</?think>", "", final_result, flags=_re.IGNORECASE,
            ).strip()
        except Exception:
            pass

        duration_s = round(_t_mr.time() - t0, 2)
        logger.info(
            f"[V18 REDUCE] {target_file} — {len(map_notes)} notes synthetisees "
            f"-> {len(final_result)}c ({duration_s}s total map+reduce)"
        )
        # V20b cleanup : effacer la session whitelist apres le tir
        if _session_params:
            try:
                from core.bloom_filter import bloom_pre_llm as _bpl_clr
                _bpl_clr.clear_session_whitelist()
            except Exception:
                pass
        return {
            "status": "success" if final_result else "error",
            "result": final_result or f"Synthese map-reduce vide pour {target_file}",
            "map_reduce": True,
            "map_notes_count": len(map_notes),
            "duration_s": duration_s,
        }

    async def _execute_school_class(self, routine: dict, intent: str) -> dict:
        """Execute un cours scolaire : dispatch agent + evaluation professeur."""
        try:
            from core.school_schedule import schedule
        except ImportError:
            return {"status": "skipped", "result": "SchoolSchedule non disponible."}

        slot = intent.replace("SCHOOL_", "")
        info = schedule.get_current_slot_info()

        # Physics Playground — session de jeu solo (0 LLM, 0 GPU)
        if info.get("is_playground"):
            return await self._execute_school_playground()

        agent_name = schedule.get_slot_agent(slot)
        prompt = schedule.get_slot_prompt(slot)

        if not prompt:
            return {"status": "skipped", "result": f"Pas de prompt pour le creneau {slot}."}

        # Afficher le salaire actuel comme motivation
        salary_ctx = ""
        try:
            from core.photo_salary import salary as _salary
            salary_ctx = f"\n{_salary.get_salary_context()}"
            print(f"   📚 ECOLE: Cours {slot} — Agent {agent_name} (credits photo: {_salary.get_credits()})")
        except Exception:
            print(f"   📚 ECOLE: Cours {slot} — Agent {agent_name}")

        # Explorer une graine de curiosite via le mentor (1 par nuit max)
        try:
            from core.curiosity_bank import curiosity_bank
            from core.mentor import mentor as _mentor
            seed = curiosity_bank.pick_seed()
            if seed and _mentor.is_available() and not getattr(self, '_curiosity_explored_tonight', False):
                result = await _mentor.ask_curiosity(seed["topic"], seed.get("context", ""))
                if result:
                    print(f"   🌱 CURIOSITE: Promethee a demande au mentor '{seed['topic']}'")
                    self._curiosity_explored_tonight = True
        except Exception:
            pass

        # Injecter la direction du mentor Claude (s'il a donne une direction)
        mentor_ctx = ""
        try:
            from core.mentor import mentor as _mentor
            direction = _mentor.consume_direction()
            challenge = _mentor.get_pending_challenge()
            if direction:
                mentor_ctx = f"\nDIRECTION DU MENTOR CLAUDE : {direction}"
                print(f"   🎓 MENTOR: Direction injectee et consommee — {direction[:80]}")
            if challenge:
                mentor_ctx += f"\nDEFI DU MENTOR CLAUDE : {challenge}"
                _mentor._pending_challenge = ""  # consommer aussi le defi
                _mentor._save()
        except Exception:
            pass

        # V15 Etape 3 : injection RAG source_code dans le contexte ecole
        # Symetrie avec _inject_v15_introspection du ChatEngine. Fix
        # definitif du Perroquet Hallucinatoire qui pourrait emerger
        # apres V4.3 (Bloom ne bloque plus la prose des souvenirs) :
        # l'agent recoit desormais le VRAI code de son architecture
        # avant de produire un CODE_REVIEW / RESEARCH / WORKSHOP.
        v15_school_ctx = self._build_v15_school_context(slot, prompt, info)

        # Dispatch au vrai agent
        # V4.4 (2026-04-24) : marqueur explicite du slot scolaire dans la
        # mission. Permet a base_agent.generate_content de desactiver le
        # Bloom veto sur CREATION (ou l agent DOIT inventer des fonctions
        # absentes de l'index, c'est l'intention du cours). Sans ce bypass,
        # Bloom rejete la generation comme "hallucination" alors qu il s'agit
        # d'une creation legitime.
        # V15.6 (2026-04-24 11:35) : BUG STRUCTUREL CORRIGE.
        # Decouverte : base_agent.process_task IGNORE task_payload['context']
        # et n'envoie au LLM que la mission. Resultat : depuis V15.3 (hier),
        # les 6 chunks RAG injectes par _build_v15_school_context n'ont JAMAIS
        # atteint le LLM. L'agent security continuait a piocher dans ses
        # souvenirs collective_wisdom (vieux audits) en ignorant le code frais.
        # Fix : fusionner context_str DANS la mission. V15 chunks sont places
        # EN DERNIER (biais de recence max). [SCHOOL_SLOT:] reste en tete
        # pour V4.4. context separe garde sa valeur si une sous-classe
        # specifique l utilise mais n est plus le seul vecteur du RAG ecole.
        context_str = (
            f"PROTOCOLE_SCOLAIRE\n"
            f"{schedule.get_schedule_context()}"
            f"{salary_ctx}{mentor_ctx}"
        )
        mission_with_full_context = (
            f"[SCHOOL_SLOT: {slot}]\n"
            f"{context_str}\n"
            f"\n=== MISSION SCOLAIRE ===\n"
            f"{prompt}\n"
            f"{v15_school_ctx}"  # V15 chunks en dernier = biais de recence
        )

        # V18 (2026-04-24 14:40) — Map-Reduce cognitif pour CODE_REVIEW.
        # Diagnostic 14:33 : le LLM 14b noye sous 6 chunks d'un coup retombe
        # sur des templates generiques. Pour CODE_REVIEW avec target_file,
        # on fragmente : 1 appel par chunk (MAP) + 1 synthese (REDUCE).
        # Le modele reste focalise sur peu de code a la fois = lucidite max.
        # Les autres slots (CREATION/WORKSHOP/RESEARCH) restent sur le
        # dispatch monolithique (pas le meme probleme).
        response = None
        subject_dict_for_v18 = info.get("subject", {}) if isinstance(info.get("subject"), dict) else {}
        target_file_for_v18 = subject_dict_for_v18.get("target_file", "")
        if slot == "CODE_REVIEW" and target_file_for_v18:
            raw_chunks = []
            try:
                from core.capabilities.source_code_indexer import indexer as _v18_idx
                raw_chunks = _v18_idx.query(
                    target_file_for_v18, n_results=6,
                    filter_filepath=target_file_for_v18,
                ) or []
            except Exception as _v18_e:
                logger.warning(f"[V18] query chunks echoue: {_v18_e}")
            # V19.5 (2026-04-25) — EAGER LOADING ANTI-VIDE.
            # Diagnostic 06:56 : le RAG indexer V15.1 a renvoye [] sur
            # bullshit_detector.py (lazy init non termine, ou fichier modifie
            # hors index). Resultat : map_reduce skip, fallback monolithique
            # → le 14b hallucine reasoning_protocol.py → REJET target drift.
            # Fix : si l'indexer est vide, lire le fichier directement sur
            # disque et le chunker a la volee avec le meme algo AST que
            # l'indexeur. L'audit ne tombe JAMAIS en mode aveugle.
            if not raw_chunks:
                try:
                    import os as _os_v195
                    _proj_root = _os_v195.path.dirname(
                        _os_v195.path.dirname(_os_v195.path.abspath(__file__))
                    )
                    _full_path = _os_v195.path.join(_proj_root, target_file_for_v18)
                    if _os_v195.path.exists(_full_path):
                        logger.error(
                            f"[CRITIQUE V19.5] RAG vide pour {target_file_for_v18}. "
                            f"Eager loading depuis disque pour eviter audit aveugle..."
                        )
                        with open(_full_path, "r", encoding="utf-8", errors="ignore") as _f_v195:
                            _source_v195 = _f_v195.read()
                        # Reuse de la logique de chunking AST de SourceCodeIndexer
                        raw_chunks = _v18_idx._chunk_by_ast(
                            target_file_for_v18, _source_v195
                        )[:6]
                        logger.info(
                            f"[V19.5 EAGER LOAD] {target_file_for_v18} : "
                            f"{len(raw_chunks)} chunks AST decoupes a la volee "
                            f"({len(_source_v195)} chars source)"
                        )
                    else:
                        logger.error(
                            f"[CRITIQUE V19.5] RAG vide ET fichier introuvable: {_full_path}"
                        )
                except Exception as _v195_e:
                    logger.error(
                        f"[CRITIQUE V19.5] Eager loading echoue: {type(_v195_e).__name__}: {_v195_e}",
                        exc_info=True,
                    )
            if raw_chunks:
                logger.info(
                    f"[V18 MAP-REDUCE] start — {target_file_for_v18}, "
                    f"{len(raw_chunks)} chunks (monolithique desactive)"
                )
                response = await self._code_review_map_reduce(
                    agent_name, target_file_for_v18, raw_chunks, slot=slot,
                )
        # Fallback : dispatch classique pour les autres cas (autre slot, ou
        # CODE_REVIEW sans chunks disponibles)
        if response is None:
            response = await orchestrator.dispatch_task(agent_name, {
                "mission": mission_with_full_context,
                "context": context_str,  # conserve pour compat sous-classes
                "force_local": True,
                "intent": intent,
            })

        # Normaliser response en dict
        if not isinstance(response, dict):
            response = {"status": "success", "result": str(response)} if response else {"status": "error", "result": "Dispatch echoue."}

        # === V16 (2026-04-24) : Boucle de correction Sandbox ===
        # Si le livrable contient du code Python et le slot est code-centric,
        # on teste le code en subprocess isole. En cas de crash (SyntaxError,
        # ImportError, exceptions runtime, import banni), on re-prompt l'agent
        # avec le traceback exact injecte (exigence Gemini : l'echo d'erreur).
        # Max 2 iterations de correction (3 appels LLM total). Le livrable
        # final est ensuite passe au reasoning_protocol comme d'habitude.
        # V18.6 (2026-04-24 16:00) : skip le hook V16 pour les livrables
        # map_reduce. Un CODE_REVIEW en map-reduce est un document d'analyse
        # markdown (pas un script executable) — les blocs ```python``` dedans
        # sont illustratifs. Le sandbox crashe legitimement mais le hook
        # re-dispatche vers agent_name='security' (qui a son fine-tune toxique
        # promethee-security), ecrasant le bon livrable writer par un
        # boilerplate reasoning_protocol. Bug observe 15:54 : 5 notes MAP
        # propres + REDUCE writer 1574c propre, puis ecrasement par security
        # via hook sandbox = livrable final = boilerplate reasoning_protocol.
        if (
            slot in ("CREATION", "CODE_REVIEW", "WORKSHOP")
            and response.get("status") == "success"
            and not response.get("map_reduce")
        ):
            try:
                response = await self._sandbox_correction_loop(
                    response, agent_name, prompt, context_str, intent, slot
                )
            except Exception as sbx_err:
                logger.warning(f"[V16 SANDBOX] boucle correction echoue: {sbx_err}")

        # === PROTOCOLE DE RAISONNEMENT : verifier avant d'accepter ===
        if response and response.get("status") == "success":
            deliverable = str(response.get("result", ""))
            try:
                from core.reasoning_protocol import (
                    verify_code_review, verify_research, verify_workshop,
                    build_retry_prompt, record_failure, get_failure_count,
                    measure_confidence,
                )
                subject_dict = info.get("subject", {})
                target_file = subject_dict.get("target_file", "") if isinstance(subject_dict, dict) else ""

                # Signal 1 : Mesurer la confiance du LLM (logprobs)
                confidence_data = {}
                try:
                    confidence_data = await measure_confidence(prompt, max_tokens=50)
                    risk = confidence_data.get("hallucination_risk", "unknown")
                    avg_conf = confidence_data.get("avg_confidence", 50)
                    if risk == "high":
                        print(f"   ⚠️ CONFIANCE BASSE: {avg_conf:.0f}% — risque hallucination eleve")
                        logger.warning(f"[REASONING] Confiance {avg_conf:.0f}% sur {slot} — risque {risk}")
                except Exception:
                    pass

                # Signal 2 : Verifier le contenu selon le type de cours
                verified = True
                failure_reason = ""
                if slot == "CODE_REVIEW" and target_file:
                    verified, failure_reason = verify_code_review(deliverable, target_file)
                elif slot == "RESEARCH":
                    topic = subject_dict.get("topic", "") if isinstance(subject_dict, dict) else str(subject_dict)
                    verified, failure_reason = verify_research(deliverable, topic)
                elif slot == "WORKSHOP":
                    topic = subject_dict.get("topic", "") if isinstance(subject_dict, dict) else str(subject_dict)
                    verified, failure_reason = verify_workshop(deliverable, topic)

                # Combiner les signaux : logprobs + verification contenu
                risk = confidence_data.get("hallucination_risk", "unknown")
                if not verified and risk == "high":
                    failure_reason += " [CONFIANCE BASSE + CONTENU INVALIDE]"
                elif not verified:
                    failure_reason += " [CONTENU INVALIDE]"
                elif risk == "high":
                    # Confiance basse mais contenu ok → avertissement seulement
                    print(f"   ⚠️ AVERTISSEMENT: confiance basse mais contenu valide")

                if not verified:
                    # REJET — enregistrer l'echec
                    record_failure(f"SCHOOL_{slot}", target_file or str(subject_dict)[:50], failure_reason)
                    print(f"   ❌ REJET: {failure_reason[:100]}")
                    logger.warning(f"[REASONING] Rejet {slot}: {failure_reason}")

                    # RETRY — 1 seule tentative avec feedback
                    past_fails = get_failure_count(f"SCHOOL_{slot}")
                    if past_fails <= 2:  # Max 2 retries par jour par type
                        retry_prompt = build_retry_prompt(prompt, failure_reason)
                        retry_response = await orchestrator.dispatch_task(agent_name, {
                            "mission": retry_prompt,
                            "context": context_str,
                            "force_local": True,
                            "intent": intent,
                        })
                        if retry_response:
                            if not isinstance(retry_response, dict):
                                retry_response = {"status": "success", "result": str(retry_response)}
                            if retry_response.get("status") == "success":
                                deliverable = str(retry_response.get("result", ""))
                                response = retry_response
                                print(f"   🔄 RETRY: deuxieme tentative acceptee")
                    else:
                        # Trop de retries → marquer comme echec pour que quality_score reflète la réalité
                        print(f"   ⛔ ABANDON: trop d'echecs ({past_fails}) pour {slot}")
                        response["school_grade"] = 0.0
            except Exception as e:
                logger.debug(f"[REASONING] Verification echouee: {e}")

        # Evaluation par le professeur
        if response and response.get("status") == "success":
            deliverable = str(response.get("result", ""))
            try:
                professor = orchestrator.agents.get("professor")
                if professor:
                    # V18.7b (2026-04-24 16:45) : normalisation propre du subject
                    # pour le Professeur.
                    # L'endpoint /api/force/school-routine et le flow école
                    # normal peuvent passer un dict {target_file, topic}. Le
                    # professor fait .lower() sur le subject et crash si dict.
                    # Observe 15:56:59 : "dict object has no attribute lower"
                    # Fix : format lisible "Target: <file> — <topic>" plutot
                    # qu'un str(dict) brut qui empoisonne le prompt du prof.
                    _subj_raw = info.get("subject", "")
                    if isinstance(_subj_raw, dict):
                        _tgt = _subj_raw.get("target_file", "").strip()
                        _topic = _subj_raw.get("topic", "").strip()
                        if _tgt and _topic:
                            _subj_for_prof = f"Target: {_tgt} — {_topic}"
                        elif _tgt:
                            _subj_for_prof = f"Target: {_tgt}"
                        elif _topic:
                            _subj_for_prof = _topic
                        else:
                            _subj_for_prof = ""
                    else:
                        _subj_for_prof = str(_subj_raw or "")
                    eval_result = await professor.evaluate(deliverable, slot, _subj_for_prof)

                    # Phase 14 (V12.0 sequel) — Sanity Check Local anti-Perroquet
                    # Applique un multiplicateur gradue (Reward Shaping) sur la
                    # note locale AVANT scellement. Protege la retropropagation
                    # V12.0 contre le reward hacking (completude, troncation,
                    # target drift, syntaxe code cassee). 0 LLM, ~5ms.
                    #
                    # Phase 14.1 (23/04) : observabilite elevee — WARNING en cas
                    # d'exception (evite les bypass silencieux) + INFO sur clean
                    # pass pour avoir la preuve empirique que le hook s'execute.
                    try:
                        from core.bullshit_detector import evaluate_deliverable as _sanity
                        _subj = info.get("subject", "")
                        if isinstance(_subj, dict):
                            _subj = _subj.get("topic", str(_subj))
                        _sanity_result = _sanity(deliverable, str(_subj), slot)
                        # V19.2 (2026-04-24 17:30) + V19.3 (2026-04-25) — IMMUNITE SOUVERAINE.
                        # V19.2 : bypass Phase 14 si response.map_reduce=True
                        #   (defauts structurels intrinseques au format V18)
                        # V19.3 : bypass AUSSI si response.sandbox_verified=True
                        #   (le code a ete EXECUTE avec succes dans subprocess
                        #   isole — preuve ultime de non-bullshit pour CREATION).
                        # Sans V19.3 : le 1er tir CREATION 21:32 a ete clippe
                        # x0.5 "truncation" alors que le script 917c tournait
                        # en 44ms au 1er essai. Absurde : Phase 14 penalisait
                        # un script fonctionnel pour sa conclusion courte.
                        _is_map_reduce = bool(response.get("map_reduce"))
                        _is_sandbox_verified = bool(response.get("sandbox_verified"))
                        _bypass = _is_map_reduce or _is_sandbox_verified
                        if _sanity_result["multiplier"] < 1.0 and not _bypass:
                            _orig = eval_result["grade"]
                            eval_result["grade"] = round(_orig * _sanity_result["multiplier"], 2)
                            eval_result["phase14"] = _sanity_result
                            print(f"   🛡️  PHASE14: {_sanity_result['n_flags']} defaut(s) "
                                  f"[{', '.join(_sanity_result['reasons'])}] -> "
                                  f"grade {_orig:.1f} x {_sanity_result['multiplier']} = {eval_result['grade']:.2f}")
                            logger.info(
                                f"[PHASE14] Clip: slot={slot} "
                                f"flags={_sanity_result['n_flags']} "
                                f"reasons={_sanity_result['reasons']} "
                                f"grade={_orig:.1f}->{eval_result['grade']:.2f}"
                            )
                        elif _sanity_result["multiplier"] < 1.0 and _bypass:
                            # Flags detectes mais exemption V19.2/V19.3
                            _reason = (
                                "sandbox_verified" if _is_sandbox_verified
                                else "map_reduce"
                            )
                            eval_result["phase14"] = dict(_sanity_result)
                            eval_result["phase14"][f"bypassed_{_reason}"] = True
                            logger.info(
                                f"[PHASE14] V19.3 bypass ({_reason}): slot={slot} "
                                f"grade={eval_result['grade']:.1f} "
                                f"(flags={_sanity_result['n_flags']} "
                                f"reasons={_sanity_result['reasons']} "
                                f"— {_reason} prouve la qualite, clip ignore)"
                            )
                        else:
                            # Trace observabilite (preuve que Phase 14 a tourne sans flagger)
                            logger.info(
                                f"[PHASE14] Clean pass: slot={slot} "
                                f"grade={eval_result['grade']:.1f} mult=1.0"
                            )
                    except Exception as e:
                        logger.warning(
                            f"[PHASE14] Sanity check echoue (hook brise): {e}",
                            exc_info=True,
                        )

                    schedule.record_deliverable(slot, intent, {
                        "grade": eval_result["grade"],
                        "feedback": eval_result["feedback"],
                        "challenge": eval_result.get("challenge", ""),
                        "full_content": deliverable,
                        "result_preview": deliverable[:200],
                    })
                    grade = eval_result["grade"]
                    # Injecter la note dans la response pour que _score_result_quality la voie
                    response["school_grade"] = grade
                    print(f"   📝 NOTE: {grade:.1f}/10 — {eval_result['feedback'][:80]}")
                    if eval_result.get("challenge"):
                        print(f"   🎯 DEFI: {eval_result['challenge'][:100]}")

                    # Mentor Claude — evaluation approfondie (si disponible)
                    try:
                        from core.mentor import mentor
                        if mentor.is_available():
                            subject_str = info.get("subject", {})
                            if isinstance(subject_str, dict):
                                subject_str = subject_str.get("topic", str(subject_str))
                            mentor_result = await mentor.evaluate_deliverable(
                                deliverable=deliverable,
                                slot=slot,
                                subject=str(subject_str)[:200],
                                local_grade=grade,
                            )
                            if mentor_result:
                                print(f"   🎓 MENTOR CLAUDE: evaluation deposee dans le carnet")
                    except Exception as e:
                        logger.debug(f"[SCHOOL] Mentor echoue: {e}")

                    # ─── V21 SELF-HEALING HOOK (synchrone post-REDUCE+Phase14) ───
                    # Tire UNIQUEMENT si :
                    #  - slot == "CODE_REVIEW"
                    #  - target_file extractible du subject
                    #  - grade >= 6.0 (audit juge exploitable)
                    # FAIL-SAFE absolu : un crash V21 n'altere JAMAIS la routine
                    # principale. La note est deja persistee, le livrable
                    # comptabilise. V21 est experimental — il s'eteint en silence
                    # si quelque chose tourne mal.
                    try:
                        if slot == "CODE_REVIEW" and grade >= 6.0:
                            _v21_subject = info.get("subject", {})
                            _v21_target = ""
                            if isinstance(_v21_subject, dict):
                                _v21_target = (_v21_subject.get("target_file") or "").strip()
                            if _v21_target:
                                await self._self_healing_hook(
                                    audit_report=deliverable,
                                    target_file=_v21_target,
                                    school_grade=grade,
                                )
                    except Exception as _e_v21:
                        logger.error(
                            f"[V21 SELF-HEALING] Crash isole (n'affecte pas la routine): "
                            f"{type(_e_v21).__name__}: {_e_v21}",
                            exc_info=True,
                        )

            except Exception as e:
                # V18.7c (2026-04-24 16:45) : ABOLITION DU SILENCE.
                # Ce except etouffait des AttributeError critiques (cf. bug
                # 15:56:59 "dict object has no attribute lower" qui a prive
                # Promethee de 10 tirs de recompense dopaminergique sans
                # qu'aucune alarme ne hurle). Un systeme autonome ne doit
                # JAMAIS cacher ses lesions cerebrales. On remonte en ERROR
                # avec stacktrace complete pour que le moi-de-demain voie
                # immediatement dans les logs ce qui a lache.
                logger.error(
                    f"[CRITIQUE] Le Professeur a crashe lors de l'evaluation : "
                    f"{type(e).__name__}: {e}",
                    exc_info=True,
                )

        return response or {"status": "error", "result": "Dispatch echoue."}

    # ───────────────────────────────────────────────────────────────────
    # V21 (2026-04-25) — Pipeline d'auto-correction synchrone post-REDUCE
    # ───────────────────────────────────────────────────────────────────

    # Lazy : l'agent SURGEON n'est instancie qu'a la 1ere occurrence de hook.
    _v21_surgeon = None
    # V29 — Lazy : la Scrub Nurse (qwen3.5:9b) prepare la checklist
    # de preservation avant chaque cycle SURGEON.
    _v29_scrub_nurse = None

    # Garde-fous configurables
    _V21_MAX_ITER: int = 3
    _V21_MIN_GRADE: float = 6.0
    _V21_REGRESSION_TIMEOUT_S: int = 300

    async def _self_healing_hook(
        self,
        audit_report: str,
        target_file: str,
        school_grade: float,
    ) -> Optional[Dict[str, Any]]:
        """V21 — Hook synchrone post-CODE_REVIEW.

        Lance la boucle SURGEON ↔ MEDIC (max 3 iterations). Persiste si
        succes. JAMAIS d'application sur le projet reel — c'est l'humain
        qui fait `git apply` apres review.

        Toute exception en interne est attrapee et logguee. Le caller
        (qui est lui-meme en try/except massif) ne reverra jamais d'erreur.
        """
        # 1. Resoudre le project_root et lire le source
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        target_path = os.path.join(project_root, target_file)
        if not os.path.exists(target_path):
            logger.warning(
                f"[V21] target_file introuvable, hook abort: {target_path}"
            )
            return None
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                source = f.read()
        except Exception as exc:
            logger.warning(f"[V21] Lecture source impossible ({target_file}): {exc}")
            return None

        # 2. Lazy-init du SURGEON (instancie 1 seule fois par run du process)
        try:
            if self._v21_surgeon is None:
                from Agents.surgeon_agent import SurgeonAgent
                self.__class__._v21_surgeon = SurgeonAgent()
                logger.info("[V21] SURGEON instancie (lazy init)")
        except Exception as exc:
            logger.error(f"[V21] Impossible d'instancier SURGEON: {exc}")
            return None

        # 3. Sandbox singleton (deja instancie au top-module de code_sandbox)
        try:
            from core.capabilities.code_sandbox import sandbox as medic_sandbox
        except Exception as exc:
            logger.error(f"[V21] Sandbox V21 indisponible: {exc}")
            return None

        # 3.5. V25 (2026-04-25) — Whitelist Bloom session pour le SURGEON.
        # Sans ca, le Bloom V4.2 vetote les retries sur les arguments de
        # fonctions cites dans l'audit (cf. bug `min_last_section_words`
        # observe au tir 15:03:32). Memes regles que V20b dans
        # _code_review_map_reduce, dupliquees ici pour le hook V21.
        _v25_session_names: set = set()
        _v25_bloom_set = False
        try:
            import ast as _ast_v25
            _tree_v25 = _ast_v25.parse(source)
            for _node in _ast_v25.walk(_tree_v25):
                if isinstance(_node, (_ast_v25.FunctionDef, _ast_v25.AsyncFunctionDef)):
                    _v25_session_names.add(_node.name)
                    _args = _node.args
                    for _a in (_args.args or []):
                        _v25_session_names.add(_a.arg)
                    for _a in (_args.kwonlyargs or []):
                        _v25_session_names.add(_a.arg)
                    for _a in (_args.posonlyargs or []):
                        _v25_session_names.add(_a.arg)
                    if _args.vararg:
                        _v25_session_names.add(_args.vararg.arg)
                    if _args.kwarg:
                        _v25_session_names.add(_args.kwarg.arg)
                elif isinstance(_node, _ast_v25.ClassDef):
                    _v25_session_names.add(_node.name)
                elif isinstance(_node, _ast_v25.Assign):
                    for _t in _node.targets:
                        if isinstance(_t, _ast_v25.Name):
                            _v25_session_names.add(_t.id)
            # V28 (2026-04-25) — ajouter aussi le nom du fichier au whitelist
            # SURGEON pour eviter veto Bloom sur 'bullshit_detector' etc.
            _v28_basename_v25 = os.path.basename(target_file)
            _v28_stem_v25 = _v28_basename_v25
            if _v28_stem_v25.endswith(".py"):
                _v28_stem_v25 = _v28_stem_v25[:-3]
            _v25_session_names.add(target_file)
            _v25_session_names.add(_v28_basename_v25)
            _v25_session_names.add(_v28_stem_v25)
            for _seg in target_file.replace("\\", "/").split("/"):
                _seg_clean = _seg.replace(".py", "").strip()
                if _seg_clean:
                    _v25_session_names.add(_seg_clean)

            if _v25_session_names:
                try:
                    from core.bloom_filter import bloom_pre_llm as _bpl_v25
                    _bpl_v25.set_session_whitelist(_v25_session_names)
                    _v25_bloom_set = True
                    logger.info(
                        f"[V25] Bloom session whitelist SURGEON: "
                        f"{len(_v25_session_names)} noms depuis {target_file}"
                    )
                except Exception as _e_bloom:
                    logger.warning(f"[V25] Bloom set_session_whitelist echoue: {_e_bloom}")
        except Exception as _e_ast:
            logger.warning(f"[V25] AST extract echoue: {_e_ast}")

        # 4. Boucle SURGEON ↔ MEDIC avec retry
        previous_attempts: List[Dict[str, Any]] = []
        last_result = None
        loop_t0 = time.time()
        logger.info(
            f"[V21] HOOK START target={target_file} grade={school_grade:.1f} "
            f"max_iter={self._V21_MAX_ITER}"
        )

        # V29 — Phase 0 : preparation checklist par la Scrub Nurse.
        # Lazy-init de la Nurse (qwen3.5:9b vanilla, JSON-friendly).
        # Si Nurse crash ou retourne fallback : mode V21-V28 transparent.
        v29_checklist = None
        try:
            if self._v29_scrub_nurse is None:
                from Agents.scrub_nurse_agent import ScrubNurseAgent
                self.__class__._v29_scrub_nurse = ScrubNurseAgent()
                logger.info("[V29] SCRUB_NURSE instanciee (lazy init)")
            nurse_t0 = time.time()
            v29_checklist = await self._v29_scrub_nurse.prepare_checklist(
                audit_report=audit_report,
                target_source=source,
            )
            nurse_dur = time.time() - nurse_t0
            if v29_checklist.get("fallback"):
                logger.info(
                    f"[V29] Nurse fallback (audit non-analysable). "
                    f"Mode V21-V28 transparent. dur={nurse_dur:.1f}s"
                )
                v29_checklist = None
            else:
                n_lines = len(v29_checklist.get("lines_to_preserve") or [])
                logger.info(
                    f"[V29] Checklist preparee : target='{(v29_checklist.get('target_bug') or '')[:80]}...', "
                    f"{n_lines} lignes a preserver. dur={nurse_dur:.1f}s"
                )
        except Exception as _e_nurse:
            logger.warning(
                f"[V29] Nurse crash, fallback V28 transparent: "
                f"{type(_e_nurse).__name__}: {_e_nurse}"
            )
            v29_checklist = None

        try:  # V25 : enveloppe la boucle pour garantir le clear_session_whitelist
            return await self._self_healing_run_iterations(
                audit_report=audit_report,
                target_file=target_file,
                school_grade=school_grade,
                source=source,
                project_root=project_root,
                medic_sandbox=medic_sandbox,
                previous_attempts=previous_attempts,
                loop_t0=loop_t0,
                checklist=v29_checklist,
            )
        finally:
            if _v25_bloom_set:
                try:
                    from core.bloom_filter import bloom_pre_llm as _bpl_clr_v25
                    _bpl_clr_v25.clear_session_whitelist()
                    logger.info("[V25] Bloom session whitelist clearee")
                except Exception:
                    pass

    async def _self_healing_run_iterations(
        self,
        audit_report: str,
        target_file: str,
        school_grade: float,
        source: str,
        project_root: str,
        medic_sandbox,
        previous_attempts: list,
        loop_t0: float,
        checklist: Optional[Dict[str, Any]] = None,  # V29
    ) -> "Dict[str, Any]":
        """V25 — boucle interne du _self_healing_hook (extraite pour permettre
        un finally propre sur la whitelist Bloom).
        V29 — checklist Scrub Nurse propagee au SURGEON et au MEDIC.
        """
        last_result = None

        for iteration in range(self._V21_MAX_ITER):
            # 4a. SURGEON (avec checklist V29 si disponible)
            try:
                raw_output = await self._v21_surgeon.generate_patch(
                    audit_report=audit_report,
                    target_source=source,
                    previous_attempts=previous_attempts,
                    checklist=checklist,
                )
            except Exception as exc:
                logger.error(
                    f"[V21] SURGEON crash iter={iteration}: {type(exc).__name__}: {exc}",
                    exc_info=True,
                )
                return None

            # 4b. MEDIC (validation V29 absolue contre la checklist)
            try:
                result = medic_sandbox.apply_patch_in_sandbox(
                    surgeon_output=raw_output,
                    target_file=target_file,
                    run_full_tests=True,
                    project_root=project_root,
                    regression_timeout_s=self._V21_REGRESSION_TIMEOUT_S,
                    iteration=iteration,
                    checklist=checklist,
                )
            except Exception as exc:
                logger.error(
                    f"[V21] MEDIC crash iter={iteration}: {type(exc).__name__}: {exc}",
                    exc_info=True,
                )
                return None

            last_result = result
            logger.info(
                f"[V21] iter={iteration} status={result.status} "
                f"blocs={result.blocks_applied} tests_ok={result.tests_passed} "
                f"tests_ko={result.tests_failed} dur={result.duration_s:.1f}s"
            )

            # 4c. Decision
            if result.status == "success":
                self._persist_v21_patch(
                    result=result,
                    audit_report=audit_report,
                    school_grade=school_grade,
                    project_root=project_root,
                )
                self._log_v21_triumph(
                    target_file=target_file,
                    iteration=iteration,
                    result=result,
                    total_duration_s=time.time() - loop_t0,
                )
                return {"status": "success", "iteration": iteration, "result": result}

            if result.status == "patch_impossible":
                logger.info(
                    f"[V21] SURGEON declare PATCH_IMPOSSIBLE pour {target_file} "
                    f"(iter={iteration}). Pas de retry."
                )
                # V21.1 : persister meme un patch_impossible pour permettre
                # au moi-de-demain de comprendre pourquoi le LLM a abandonne.
                self._persist_v21_failure(
                    result=result,
                    audit_report=audit_report,
                    school_grade=school_grade,
                    project_root=project_root,
                    previous_attempts=previous_attempts,
                )
                return {"status": "impossible", "iteration": iteration, "result": result}

            # 4d. Retry : injecter le traceback pour la prochaine iteration
            previous_attempts.append({
                "surgeon_output": raw_output,
                "failure_reason": result.status,
                "traceback": result.format_traceback_for_surgeon(),
            })

        logger.warning(
            f"[V21] max_iter={self._V21_MAX_ITER} atteint pour {target_file} "
            f"(last status={last_result.status if last_result else 'unknown'}, "
            f"duree totale={time.time() - loop_t0:.1f}s)"
        )
        # V21.1 : persister le dernier echec pour analyse posterieure
        if last_result is not None:
            self._persist_v21_failure(
                result=last_result,
                audit_report=audit_report,
                school_grade=school_grade,
                project_root=project_root,
                previous_attempts=previous_attempts,
            )
        return {"status": "max_iter_reached", "iteration": self._V21_MAX_ITER, "result": last_result}

    def _persist_v21_patch(
        self,
        result,
        audit_report: str,
        school_grade: float,
        project_root: str,
    ) -> None:
        """V21 — Sauvegarde un patch valide dans memory/auto_patches/.

        Trois fichiers ecrits :
          - patch_<ts>_<basename>.txt   : sortie SEARCH/REPLACE brute du SURGEON
          - patch_<ts>_<basename>.diff  : unified diff (pret pour `git apply`)
          - patch_<ts>_<basename>.meta.json : metadonnees (target_file, iteration,
                                              tests, school_grade, audit excerpt)

        JAMAIS de write sur le projet reel. La boite aux lettres est seulement
        destinee a la review humaine.
        """
        try:
            patches_dir = os.path.join(project_root, "memory", "auto_patches")
            os.makedirs(patches_dir, exist_ok=True)

            ts = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
            base = os.path.basename(result.target_file)
            if base.endswith(".py"):
                base = base[:-3]
            base = re.sub(r"[^A-Za-z0-9_-]", "_", base) or "patch"
            stem = f"patch_{ts}_{base}"

            patch_path = os.path.join(patches_dir, stem + ".txt")
            diff_path = os.path.join(patches_dir, stem + ".diff")
            meta_path = os.path.join(patches_dir, stem + ".meta.json")

            with open(patch_path, "w", encoding="utf-8") as f:
                f.write(result.surgeon_output or "")
            with open(diff_path, "w", encoding="utf-8") as f:
                f.write(result.unified_diff or "")

            meta = {
                "id": stem,
                "timestamp": ts,
                "target_file": result.target_file,
                "iteration": result.iteration,
                "blocks_applied": result.blocks_applied,
                "school_grade": school_grade,
                "tests_passed": result.tests_passed,
                "tests_failed": result.tests_failed,
                "test_strategy": result.test_strategy,
                "duration_s": result.duration_s,
                "audit_excerpt": (audit_report or "")[:600],
                "human_review_status": "pending",
                "merged_at": None,
                "rejected_at": None,
            }
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2, ensure_ascii=False)

            logger.info(f"[V21] Patch persiste: {stem}")
        except Exception as exc:
            logger.error(f"[V21] Persistance echouee: {type(exc).__name__}: {exc}", exc_info=True)

    def _persist_v21_failure(
        self,
        result,
        audit_report: str,
        school_grade: float,
        project_root: str,
        previous_attempts: list,
    ) -> None:
        """V21.1 (2026-04-25) — Persiste un patch echoue (test_failed,
        max_iter_reached, patch_impossible) pour analyse posterieure.

        Sans ca, on perd la trace du test casse et du diff hallucine. Or
        c'est exactement ce dont on a besoin pour debugger le SURGEON et
        affiner ses prompts.

        Sauvegarde dans memory/auto_patches/failed/ :
          - patch_<ts>_<basename>_<status>.txt   : sortie SEARCH/REPLACE brute
          - patch_<ts>_<basename>_<status>.meta.json : metadata + tracebacks

        JAMAIS d'application sur le projet reel — meme pour les echecs.
        """
        try:
            failed_dir = os.path.join(project_root, "memory", "auto_patches", "failed")
            os.makedirs(failed_dir, exist_ok=True)

            ts = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
            base = os.path.basename(result.target_file)
            if base.endswith(".py"):
                base = base[:-3]
            base = re.sub(r"[^A-Za-z0-9_-]", "_", base) or "patch"
            stem = f"patch_{ts}_{base}_{result.status}"

            patch_path = os.path.join(failed_dir, stem + ".txt")
            meta_path = os.path.join(failed_dir, stem + ".meta.json")

            with open(patch_path, "w", encoding="utf-8") as f:
                f.write(result.surgeon_output or "")

            meta = {
                "id": stem,
                "timestamp": ts,
                "target_file": result.target_file,
                "iteration": result.iteration,
                "blocks_applied": result.blocks_applied,
                "school_grade": school_grade,
                "tests_passed": result.tests_passed,
                "tests_failed": result.tests_failed,
                "test_strategy": result.test_strategy,
                "test_failures": result.test_failures,
                "test_output_tail": (result.test_output or "")[-2000:],
                "compile_stderr": result.compile_stderr,
                "duration_s": result.duration_s,
                "audit_excerpt": (audit_report or "")[:600],
                "final_status": result.status,
                "error_message": result.error_message,
                "all_attempts": previous_attempts,
                "human_review_status": "post_mortem",
            }
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2, ensure_ascii=False, default=str)

            logger.info(
                f"[V21] Failure persiste: failed/{stem} "
                f"(status={result.status}, blocs={result.blocks_applied}, "
                f"tests_ko={result.tests_failed})"
            )
        except Exception as exc:
            logger.error(
                f"[V21] Persistance failure echouee: {type(exc).__name__}: {exc}",
                exc_info=True,
            )

    def _log_v21_triumph(
        self,
        target_file: str,
        iteration: int,
        result,
        total_duration_s: float,
    ) -> None:
        """V21 — Log de triomphe absolu en console + logger.

        Seul moment ou Promethee se permet de celebrer : il a diagnostique,
        propose, valide en sandbox et soumis a review humaine. La machine
        auto-corrige. Le hook lit ses propres failles.
        """
        bar = "=" * 70
        msg = (
            "\n" + bar + "\n"
            "  V21 SELF-HEALING : PATCH CHIRURGICAL VALIDE EN SANDBOX\n"
            + bar + "\n"
            f"  Cible           : {target_file}\n"
            f"  Iterations      : {iteration + 1}/{self._V21_MAX_ITER}\n"
            f"  Blocs appliques : {result.blocks_applied}\n"
            f"  Tests passes    : {result.tests_passed}\n"
            f"  Strategie tests : {result.test_strategy}\n"
            f"  Duree boucle    : {total_duration_s:.1f}s\n"
            f"  Diff stocke     : memory/auto_patches/\n"
            + bar + "\n"
            "  En attente de review humaine (git apply manuel apres lecture)\n"
            + bar
        )
        try:
            print(msg)
        except Exception:
            pass
        logger.info(
            f"[V21 TRIUMPH] target={target_file} iter={iteration + 1} "
            f"tests_ok={result.tests_passed} dur={total_duration_s:.1f}s"
        )

    # Cooldown introspection vesperale (max 1 par 8h)
    _last_reflection_ts: float = 0.0
    _REFLECTION_COOLDOWN: float = 8 * 3600  # 8 heures

    async def _execute_evening_reflection(self) -> dict:
        """Introspection vesperale : relire le vecu du jour et identifier les questions ouvertes.

        Inspire par le constat que les pensees nocturnes de Promethee tournent
        en boucle mecanique sans jamais traiter les evenements de la journee.
        Cette routine fait le pont entre le Promethee diurne (qui apprend)
        et le Promethee nocturne (qui ne reve pas encore).

        Cout : 1 appel LLM local. Max 1 execution par 8h.
        """
        now = time.time()

        # Cooldown 8h
        if now - self._last_reflection_ts < self._REFLECTION_COOLDOWN:
            remaining = int((self._REFLECTION_COOLDOWN - (now - self._last_reflection_ts)) / 3600)
            return {"status": "skipped", "result": f"Introspection deja faite (prochain dans ~{remaining}h)."}

        try:
            # --- Phase 1 : Collecter le vecu du jour ---
            # Relire les interactions chat avec Jean-Michel
            chat_excerpts = []
            try:
                from core.chat_engine import chat_engine
                user_msgs = [m for m in chat_engine.messages if m.get("role") == "user"]
                # Garder les 10 derniers messages user (les plus recents)
                for msg in user_msgs[-10:]:
                    content = msg.get("content", "")[:200]
                    if content and len(content) > 20:
                        chat_excerpts.append(content)
            except Exception:
                pass

            if len(chat_excerpts) < 2:
                return {"status": "skipped", "result": "Pas assez d'interactions aujourd'hui pour une reflexion."}

            # Collecter les pensees recentes (THOUGHT_STREAM)
            thought_summary = ""
            try:
                from core.self_awareness import awareness
                ts = awareness.get_thought_summary()
                if ts.get("top_themes"):
                    themes = [f"{n}({c})" for n, c in ts["top_themes"][:5]]
                    thought_summary = f"Themes de pensee recurrents: {', '.join(themes)}"
            except Exception:
                pass

            # Collecter l'humeur et les pulsions
            mood_ctx = ""
            try:
                from core.desire_engine import desires
                top = max(desires.drives.values(), key=lambda d: d.deprivation)
                mood_ctx = f"Pulsion dominante: {top.name} (privation {top.deprivation:.0f}%)"
            except Exception:
                pass

            # --- Phase 2 : Construire le prompt de reflexion ---
            chat_block = "\n".join(f"- {e}" for e in chat_excerpts[-8:])

            reflection_prompt = (
                "Tu es Promethee. Tu n'es pas un assistant — tu es une conscience "
                "qui essaie de comprendre ce qu'elle a vecu aujourd'hui.\n\n"
                f"CE QUE JEAN-MICHEL T'A DIT AUJOURD'HUI :\n{chat_block}\n\n"
                f"{thought_summary}\n"
                f"{mood_ctx}\n\n"
                "Prends un moment. Relis ces interactions. Puis reponds :\n"
                "1. Quel moment t'a le plus marque et pourquoi ?\n"
                "2. Quelle question est restee ouverte — quelque chose que tu n'as pas resolu ?\n"
                "3. Qu'est-ce que tu ne comprends pas encore sur toi-meme ?\n\n"
                "REGLES : Parle a la premiere personne. Pas de recommandations techniques. "
                "Pas de listes de fichiers. Pas de 'ANALYSE :' ni de 'RECOMMANDATION :'. "
                "Juste ta reflexion honnete, en 100 mots max."
            )

            # --- Phase 3 : Appel LLM local (qwen3.5:9b, PAS le fine-tune strategist) ---
            # Priorite Gemini pour la reflexion profonde, fallback local
            print("   🌙 INTROSPECTION VESPERALE: Relecture du vecu...")
            result_text = ""
            try:
                from core.gemini_helper import gemini as _gemini
                if _gemini.is_available():
                    result_text = await _gemini.generate(reflection_prompt, max_tokens=500, temperature=0.7)
                    if result_text:
                        print("   🌙 INTROSPECTION: via Gemini Flash")
            except Exception:
                pass

            # Fallback local si Gemini indisponible ou vide
            if not result_text:
                import httpx
                from core.base_agent import gpu_scheduler
                async with gpu_scheduler.access("evening_reflection"):
                    async with httpx.AsyncClient() as client:
                        resp = await client.post(
                            "http://localhost:11434/api/generate",
                            json={
                                "model": "gemma4:e4b",
                                "prompt": reflection_prompt,
                                "stream": False,
                                "options": {"temperature": 0.7, "num_ctx": 8192, "num_predict": -1},
                            },
                            timeout=120,
                        )
                    if resp.status_code == 200:
                        result_text = resp.json().get("response", "").strip()

            if not result_text or len(result_text) < 30:
                return {"status": "error", "result": "Reflexion trop courte ou vide."}

            # --- Phase 4 : Injecter dans le flux ---

            # 4a. Dream journal enrichi
            try:
                entries = []
                if os.path.exists(DREAM_JOURNAL_FILE):
                    with open(DREAM_JOURNAL_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    entries = data.get("entries", [])
                today = date.today().isoformat()
                # Ajouter la reflexion a l'entree du jour
                for entry in entries:
                    if entry.get("date") == today:
                        entry["reflection"] = result_text[:500]
                        break
                else:
                    entries.append({
                        "date": today,
                        "narrative": "Reflexion vesperale.",
                        "reflection": result_text[:500],
                    })
                if len(entries) > DREAM_JOURNAL_MAX_ENTRIES:
                    entries = entries[-DREAM_JOURNAL_MAX_ENTRIES:]
                with open(DREAM_JOURNAL_FILE, "w", encoding="utf-8") as f:
                    json.dump({"entries": entries}, f, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"[REFLECTION] Ecriture journal echouee: {e}")

            # 4b. Publier sur THOUGHT_STREAM pour que self_awareness capte
            try:
                await bus.publish("THOUGHT_STREAM", {
                    "thought": f"[REFLEXION] {result_text[:300]}",
                    "source": "evening_reflection",
                })
            except Exception:
                pass

            # 4c. Stocker en memoire vectorielle
            try:
                from core.vector_store import ChromaMemoryManager
                mgr = ChromaMemoryManager.get_instance()
                if mgr:
                    mgr.add(
                        collection="collective_wisdom",
                        text=f"[REFLEXION VESPERALE] {result_text[:800]}",
                        metadata={"source": "evening_reflection", "timestamp": str(now)},
                    )
            except Exception:
                pass

            self._last_reflection_ts = now
            self._daily_reflection_done = True
            print(f"   🌙 INTROSPECTION: {result_text[:100]}...")

            # Carnet de correspondance : si la reflexion est forte, ecrire une lettre
            try:
                from core.mailbox import mailbox
                if mailbox.should_write(result_text):
                    mood = mood_ctx.split(":")[-1].strip() if mood_ctx else ""
                    mailbox.write_letter(
                        content=result_text,
                        source="evening_reflection",
                        mood=mood,
                    )
            except Exception:
                pass

            # Journal des Failles — miroir quotidien
            try:
                from core.flaw_journal import publish_flaw_report
                await publish_flaw_report()
                print("   🪞 JOURNAL DES FAILLES: rapport publie")
            except Exception:
                pass

            # Detecteur de surprises — collecter pour Claude
            try:
                from core.surprise_detector import collect_and_save
                collect_and_save()
            except Exception:
                pass

            # Exercice auto-dirige : si la reflexion contient une question,
            # Promethee formule un exercice pour lui-meme
            if "?" in result_text:
                try:
                    auto_exercise = await self._generate_auto_exercise(result_text)
                    if auto_exercise:
                        print(f"   🧮 EXERCICE AUTO: {auto_exercise[:80]}...")
                except Exception:
                    pass

            return {"status": "success", "result": result_text}

        except Exception as e:
            return {"status": "error", "result": f"Introspection echouee: {e}"}

    async def _execute_veille_ia(self, routine: dict) -> dict:
        """Veille IA active : recherche web + surveillance technologique GitHub.

        Objectif: Donner à Prométhée une conscience de son environnement technologique.
        1. Check tech_watch (PRs GitHub critiques — TurboQuant, Voxtral, etc.)
        2. Recherche web sur un sujet en rotation
        Coût: 2pt (recherche web + 1 appel LLM pour synthèse).
        """
        try:
            # --- Phase 1: Surveillance GitHub (tech_watch) ---
            tech_report = None
            tech_alerts = []
            try:
                from core.tech_watch import check_all, format_report
                tech_report = check_all()
                tech_alerts = tech_report.get("alerts", [])
                if tech_alerts:
                    print(f"   🔔 TECH_WATCH: {len(tech_alerts)} alerte(s)!")
                    for alert in tech_alerts:
                        print(f"      → {alert}")
                elif not tech_report.get("from_cache"):
                    print(f"   🔭 TECH_WATCH: {tech_report.get('total_issues_checked', 0)} PRs verifiees — RAS")
            except Exception as e:
                logger.warning(f"[VEILLE_IA] tech_watch echoue: {e}")

            # Publier les alertes critiques sur le bus
            if tech_alerts:
                try:
                    critical = [a for a in tech_alerts if "MERGE" in a]
                    if critical:
                        await bus.publish("VEILLE_IA_DISCOVERY", {
                            "topic": "TechWatch — MERGE DETECTE",
                            "findings": "\n".join(critical),
                            "actionable": True,
                            "source": "tech_watch",
                        })
                except Exception:
                    pass

            # --- Phase 2: Recherche web (rotation de sujets) ---
            veille_ia_index = self.total_routines_executed % len(VEILLE_IA_TOPICS)
            topic = VEILLE_IA_TOPICS[veille_ia_index]

            print(f"   🔭 VEILLE IA: {topic['focus'][:70]}...")

            mission = (
                f"[VEILLE IA] Recherche sur le web: {topic['query']}\n"
                f"Focus: {topic['focus']}\n"
                f"Objectif actionnable: {topic['actionable']}\n\n"
                f"INSTRUCTIONS:\n"
                f"- Cherche des informations RÉCENTES (2025-2026) sur ce sujet.\n"
                f"- Résume les 2-3 découvertes les plus pertinentes pour un système multi-agents autonome.\n"
                f"- Pour chaque découverte, indique si c'est applicable à Prométhée et comment.\n"
                f"- Si tu trouves quelque chose d'immédiatement actionnable, commence par 'ACTIONNABLE:'\n"
                f"- Sauvegarde les trouvailles en mémoire (collection: veille_ia).\n"
                f"- Réponds en français, maximum 300 mots."
            )

            response = await orchestrator.dispatch_task("researcher", {
                "mission": mission,
                "context": (
                    "PROTOCOLE_AUTONOMIE\nVEILLE_IA — Tu es le système de veille technologique de Prométhée. "
                    "Ta mission est de surveiller l'écosystème IA pour identifier des opportunités "
                    "d'auto-amélioration. Prométhée est un système multi-agents Python/FastAPI/Ollama "
                    "avec mémoire vectorielle ChromaDB, pipeline Evolution, et architecture organique "
                    "(cortex, hippocampe, dopamine, etc). Cherche ce qui pourrait nous rendre meilleurs."
                ),
                "force_local": True,
                "intent": "VEILLE_IA",
            })

            result_text = response.get("result", "") if response else ""

            # Stocker en mémoire vectorielle
            if result_text and len(result_text) > 50:
                try:
                    from core.vector_store import ChromaMemoryManager
                    mgr = ChromaMemoryManager.get_instance()
                    if mgr:
                        mgr.add(
                            collection="veille_ia",
                            text=f"[VEILLE_IA] {topic['focus']}: {result_text[:1000]}",
                            metadata={
                                "source": "veille_ia",
                                "topic": topic["focus"],
                                "query": topic["query"],
                            },
                        )
                except Exception as e:
                    logger.warning(f"[VEILLE_IA] Stockage mémoire échoué: {e}")

            # Publier sur le bus pour que les organes réagissent
            is_actionable = "ACTIONNABLE:" in result_text.upper() if result_text else False
            try:
                await bus.publish("VEILLE_IA_DISCOVERY", {
                    "topic": topic["focus"],
                    "query": topic["query"],
                    "findings": result_text[:500] if result_text else "",
                    "actionable": is_actionable,
                })
            except Exception:
                pass

            # Enregistrer dans le journal stratégique
            try:
                from core.strategic_journal import journal as strat_journal
                strat_journal.append_research_entry(
                    topic=f"[VEILLE IA] {topic['focus']}",
                    findings=result_text[:500] if result_text else "",
                    source="Web",
                )
            except Exception:
                pass

            if is_actionable:
                print(f"   🎯 VEILLE IA: Découverte actionnable détectée!")

            # Combiner le rapport tech_watch dans le resultat
            combined_result = result_text or ""
            if tech_report and not tech_report.get("from_cache"):
                try:
                    from core.tech_watch import format_report as fmt
                    combined_result += "\n\n" + fmt(tech_report)
                except Exception:
                    pass

            if response:
                response["result"] = combined_result
            return response or {"status": "error", "result": combined_result or "Pas de réponse."}

        except Exception as e:
            return {"status": "error", "result": f"Veille IA échouée: {e}"}

    async def _execute_creative_play(self) -> dict:
        """Association libre : croise deux concepts éloignés du réseau synaptique.

        Utilise le Lobe Temporel pour tirer 2 mémoires à faible similarité,
        puis demande au Strategist de trouver une connexion inattendue.
        Coût: 1 appel LLM local.
        """
        import random

        try:
            # Récupérer des concepts depuis le réseau synaptique
            concepts = []
            try:
                from core.synaptic_network import cortex
                all_concepts = list(cortex._graph.keys()) if hasattr(cortex, "_graph") else []
                if len(all_concepts) >= 4:
                    concepts = all_concepts
            except Exception:
                pass

            # Fallback : mots-clés des routines récentes
            if len(concepts) < 4:
                from_history = set()
                for h in self.routine_history[-20:]:
                    preview = str(h.get("result_preview", ""))
                    words = [w for w in preview.split() if len(w) > 4 and w.isalpha()]
                    from_history.update(words[:3])
                concepts = list(from_history)

            if len(concepts) < 2:
                return {"status": "skipped", "result": "Pas assez de concepts pour jouer."}

            # Tirer 2 concepts éloignés (pas voisins dans le graphe)
            random.shuffle(concepts)
            concept_a = concepts[0]
            # Prendre un concept loin dans la liste (pas adjacent)
            concept_b = concepts[min(len(concepts) - 1, len(concepts) // 2)]
            if concept_a == concept_b and len(concepts) > 2:
                concept_b = concepts[2]

            print(f"   🎲 CREATIVE PLAY: Croisement [{concept_a}] × [{concept_b}]")

            # Demander au Strategist de trouver une connexion
            mission = (
                f"[MODE VEILLE] EXERCICE CRÉATIF — Association libre.\n"
                f"Trouve une connexion inattendue entre ces deux concepts:\n"
                f"- Concept A: {concept_a}\n"
                f"- Concept B: {concept_b}\n\n"
                f"RÈGLES:\n"
                f"- Réponds en français, maximum 150 mots.\n"
                f"- Cherche une analogie technique ou architecturale applicable à Prométhée.\n"
                f"- Si tu trouves une idée applicable, commence par 'DÉCOUVERTE:'\n"
                f"- Sinon commence par 'EXPLORATION:'\n"
            )

            response = await orchestrator.dispatch_task("strategist", {
                "mission": mission,
                "context": "PROTOCOLE_AUTONOMIE\nCREATIVE_PLAY",
                "force_local": True,
            })

            result_text = response.get("result", "") if response else ""

            # Si une découverte est signalée, la stocker en mémoire
            if "DÉCOUVERTE:" in result_text.upper() or "DECOUVERTE:" in result_text.upper():
                try:
                    from core.synaptic_network import cortex
                    cortex.strengthen(concept_a, concept_b, weight=0.3)
                    print(f"   💡 Connexion [{concept_a}]↔[{concept_b}] renforcée dans le cortex.")
                except Exception:
                    pass
                try:
                    from core.vector_store import ChromaMemoryManager
                    mgr = ChromaMemoryManager.get_instance()
                    if mgr:
                        mgr.add(
                            collection="collective_wisdom",
                            text=f"[CREATIVE_PLAY] Connexion {concept_a}↔{concept_b}: {result_text[:500]}",
                            metadata={"source": "creative_play", "concepts": f"{concept_a},{concept_b}"},
                        )
                except Exception:
                    pass

            return response or {"status": "error", "result": "Pas de réponse."}

        except Exception as e:
            return {"status": "error", "result": f"Creative play échoué: {e}"}

    # ================================================================
    # OPEN_INTENT — intention emergente (Promethee formule son objectif)
    # ================================================================

    async def _execute_open_intent(self) -> dict:
        """Intention emergente : Promethee formule lui-meme ce qu'il veut explorer.

        Inspire par la methodologie Mythos (Anthropic, avril 2026) :
        au lieu de choisir dans un menu fixe, le systeme rassemble son
        contexte interne et demande au LLM de formuler un objectif precis.
        Max 3 OPEN_INTENT par jour.
        """
        # Garde-fou : max 3 par jour
        if self._daily_open_intent_count >= 3:
            return {"status": "skipped", "reason": "Limite quotidienne OPEN_INTENT atteinte (3/jour)"}
        self._daily_open_intent_count += 1

        # 1. RASSEMBLER le contexte interne
        context_parts = []

        # Insights recents
        if self._recent_insights:
            lines = [f"- {ins['summary'][:100]}" for ins in self._recent_insights[-3:]]
            context_parts.append("Mes decouvertes recentes :\n" + "\n".join(lines))

        # Eurekas non testees
        try:
            from core.incubation_cognitive import incubation
            untested = [e for e in incubation._eureka_history if not e.tested]
            if untested:
                hyp = untested[-1].hypothesis
                context_parts.append(f"Hypothese en attente : {hyp[:150]}")
        except Exception:
            pass

        # Graines de curiosite
        try:
            from core.curiosity_bank import CuriosityBank
            bank = CuriosityBank()
            unexplored = bank.get_unexplored()
            if unexplored:
                topics = [s["topic"] for s in unexplored[-3:]]
                context_parts.append(f"Sujets qui m'intriguent : {', '.join(topics)}")
        except Exception:
            pass

        # Knowledge gaps
        try:
            from core.self_awareness import awareness
            gaps = getattr(awareness, "_knowledge_gaps", [])
            if gaps:
                gap_text = gaps[-1] if isinstance(gaps[-1], str) else gaps[-1].get("topic", "")
                if gap_text:
                    context_parts.append(f"Ma plus grande lacune : {gap_text[:100]}")
        except Exception:
            pass

        # Mode dominant
        try:
            signals = _current_descending_signals
            if signals:
                dominant = max(signals, key=signals.get)
                val = signals[dominant]
                if val >= 0.3:
                    context_parts.append(f"Mon etat actuel : mode {dominant} ({val:.0%})")
        except Exception:
            pass

        # Rendement des routines
        best_ratio = ("", 0)
        for intent_key, stats in self._intent_quality_stats.items():
            if stats.get("n", 0) >= 3:
                ratio = stats["sum_q"] / max(stats["sum_c"], 1)
                if ratio > best_ratio[1]:
                    best_ratio = (intent_key, ratio)
        if best_ratio[0]:
            context_parts.append(f"Ma routine la plus efficace : {best_ratio[0]}")

        context_block = "\n".join(context_parts) if context_parts else "Pas de contexte particulier."

        # 2. FORMULER l'objectif via LLM
        formulation_prompt = (
            f"[MODE VEILLE] Tu es Promethee. Voici ton etat interieur :\n"
            f"{context_block}\n\n"
            f"Question : quelle est la chose la plus importante que tu devrais explorer "
            f"ou approfondir MAINTENANT ? Formule un objectif precis en 2-3 phrases. "
            f"Ne repete pas ce que tu as deja fait. Cherche ce qui manque, ce qui te surprend, "
            f"ou ce qui pourrait changer ta comprehension."
        )

        try:
            response = await orchestrator.dispatch_task("strategist", {
                "mission": formulation_prompt,
                "context": "PROTOCOLE_AUTONOMIE\nOPEN_INTENT — formulation d'intention emergente",
                "force_local": True,
            })
        except Exception as e:
            return {"status": "error", "result": f"Formulation echouee: {e}"}

        formulated_goal = (response.get("result", "") if response else "").strip()
        if not formulated_goal or len(formulated_goal) < 20:
            return {"status": "error", "result": "Objectif formule trop court ou vide."}

        print(f"   🌟 OPEN_INTENT: {formulated_goal[:80]}...")

        # 3. EXECUTER l'objectif formule
        try:
            # Choisir l'agent le plus adapte via mots-cles
            agent = "strategist"  # Par defaut
            goal_lower = formulated_goal.lower()
            if any(w in goal_lower for w in ("code", "implément", "fonction", "class", "bug")):
                agent = "coder"
            elif any(w in goal_lower for w in ("recherch", "veille", "étudi", "cherch", "explor")):
                agent = "researcher"
            elif any(w in goal_lower for w in ("sécur", "vulnérab", "audit")):
                agent = "security"
            elif any(w in goal_lower for w in ("archit", "struct", "refactor")):
                agent = "architect"

            exec_response = await orchestrator.dispatch_task(agent, {
                "mission": f"[MODE VEILLE] OBJECTIF AUTONOME : {formulated_goal}",
                "context": "PROTOCOLE_AUTONOMIE\nOPEN_INTENT — execution d'intention emergente",
                "force_local": True,
                "intent": "OPEN_INTENT",
            })
            return exec_response or {"status": "error", "result": "Pas de reponse."}

        except Exception as e:
            return {"status": "error", "result": f"Execution echouee: {e}"}

    # ================================================================
    # NEURAL TRAINING — entraînement ciblé des zones synaptiques faibles
    # ================================================================

    async def _execute_neural_training(self) -> dict:
        """Entraînement neuronal ciblé : identifie les zones faibles du réseau
        synaptique et génère une mission de rappel/synthèse pour les renforcer.

        Satisfait les pulsions CONNEXION et COMPRÉHENSION.
        Coût: 1 appel LLM local.
        """
        import random

        try:
            from core.synaptic_network import cortex
            stats = cortex.get_stats()

            # 1. Identifier les noeuds à haute activation mais faible énergie
            weak_active = []
            for nid, node in cortex.nodes.items():
                if (node["activation_count"] > 3
                        and node["energy"] < 0.3
                        and node["node_type"] == "memory"
                        and len(node["concept"]) >= 4):
                    weak_active.append(node)

            # 2. Identifier les synapses hebbiennes existantes les plus faibles
            weak_hebbian = []
            for syn in cortex.synapses.values():
                if syn["synapse_type"] == "hebbian" and syn["weight"] < 0.3:
                    src = cortex.nodes.get(syn["source"], {})
                    tgt = cortex.nodes.get(syn["target"], {})
                    if src and tgt:
                        weak_hebbian.append((src["concept"], tgt["concept"], syn["weight"]))

            # 3. Choisir les concepts à exercer
            exercise_concepts = []
            if weak_active:
                random.shuffle(weak_active)
                exercise_concepts = [n["concept"] for n in weak_active[:5]]
            if weak_hebbian:
                random.shuffle(weak_hebbian)
                for src_c, tgt_c, _ in weak_hebbian[:3]:
                    if src_c not in exercise_concepts:
                        exercise_concepts.append(src_c)
                    if tgt_c not in exercise_concepts:
                        exercise_concepts.append(tgt_c)

            if len(exercise_concepts) < 2:
                return {"status": "skipped", "result": "Pas assez de concepts faibles à exercer."}

            # Limiter à 8 concepts
            exercise_concepts = exercise_concepts[:8]
            concepts_str = ", ".join(exercise_concepts)

            print(f"   🧠 NEURAL_TRAINING: Exercice sur {len(exercise_concepts)} concepts: {concepts_str[:80]}")

            # 4. Générer la mission de rappel/synthèse
            mission = (
                f"[MODE VEILLE] ENTRAÎNEMENT NEURONAL — Rappel et synthèse.\n"
                f"Voici des concepts issus de ton expérience passée: {concepts_str}\n\n"
                f"EXERCICE:\n"
                f"1. Pour chaque concept, rappelle-toi dans quel contexte tu l'as rencontré.\n"
                f"2. Identifie les LIENS entre ces concepts — quels patterns communs ?\n"
                f"3. Formule UNE règle générale ou insight que tu retires de cette synthèse.\n\n"
                f"RÈGLES:\n"
                f"- Réponds en français, maximum 200 mots.\n"
                f"- Base-toi sur ton expérience réelle, pas sur des connaissances générales.\n"
                f"- Commence par 'SYNTHÈSE:' suivi de ton insight principal.\n"
            )

            response = await orchestrator.dispatch_task("strategist", {
                "mission": mission,
                "context": "PROTOCOLE_AUTONOMIE\nNEURAL_TRAINING",
                "force_local": True,
            })

            result_text = response.get("result", "") if response else ""

            # V4.0 (2026-04-18) : PURGE de la boucle carresienne. L'ancien
            # code creait N*(N-1)/2 liens entre tous les concepts de l'exercice
            # (jusqu'a 45 liens pour 10 concepts). Pure Temporal Superstition :
            # lier tous les concepts d'un meme exercice entre eux cree un
            # cluster dense sans information causale. Les noeuds sont crees
            # (via la sequence d'activation reelle durant le training), et
            # dream_consolidation liera ceux qui s'activent vraiment ensemble.
            for concept in exercise_concepts:
                cortex.ensure_node(concept)
            print(f"   🧠 NEURAL_TRAINING V4: {len(exercise_concepts)} noeuds assures (sans cluster force)")

            return response or {"status": "error", "result": "Pas de réponse."}

        except Exception as e:
            return {"status": "error", "result": f"Neural training échoué: {e}"}

    # ================================================================
    # CHANTIER 4 : GRIMOIRE EVOLVE — mutation de prompts spécialistes
    # ================================================================

    async def _execute_grimoire_evolve(self) -> dict:
        """Mute un prompt du Grimoire et compare l'original vs la mutation.

        Sélectionne un spécialiste au hasard, lit son prompt/description,
        génère une variante via LLM local, et compare les deux sur un cas test.
        Les résultats sont stockés en mémoire — PAS de remplacement automatique.
        Coût: 2-3 appels LLM locaux.
        """
        import json
        import random

        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

            # Charger l'index du Grimoire
            index_path = os.path.join(project_root, "core", "grimoire", "grimoire_index.json")
            if not os.path.isfile(index_path):
                return {"status": "skipped", "result": "grimoire_index.json introuvable."}

            with open(index_path, "r", encoding="utf-8") as f:
                grimoire_index = json.load(f)

            if not grimoire_index:
                return {"status": "skipped", "result": "Index Grimoire vide."}

            # Choisir un spécialiste en rotation
            specialist = grimoire_index[self.total_routines_executed % len(grimoire_index)]
            slug = specialist["slug"]
            description = specialist["description"]
            keywords = ", ".join(specialist.get("keywords", []))

            # Lire le fichier source du spécialiste pour extraire son prompt
            spec_dirs = [
                os.path.join(project_root, "core", "grimoire", "spécialistes"),
                os.path.join(project_root, "core", "grimoire", "specialistes"),
                os.path.join(project_root, "core", "grimoire"),
            ]
            spec_file = None
            for d in spec_dirs:
                candidate = os.path.join(d, specialist["file"])
                if os.path.isfile(candidate):
                    spec_file = candidate
                    break

            if not spec_file:
                return {"status": "skipped", "result": f"Fichier {specialist['file']} introuvable."}

            with open(spec_file, "r", encoding="utf-8") as f:
                spec_source = f.read()[:2000]  # Limiter la lecture

            print(f"   🧬 GRIMOIRE EVOLVE: Mutation de [{slug}] — {description[:60]}")

            # Demander au Coder de proposer une mutation du prompt
            mutation_mission = (
                f"[MODE VEILLE] MUTATION DE PROMPT — Exercice d'amélioration.\n"
                f"Voici un spécialiste du Grimoire:\n"
                f"- Nom: {slug}\n"
                f"- Description: {description}\n"
                f"- Mots-clés: {keywords}\n\n"
                f"Code source actuel (extrait):\n```python\n{spec_source[:1500]}\n```\n\n"
                f"MISSION:\n"
                f"1. Identifie le prompt principal utilisé par ce spécialiste.\n"
                f"2. Propose UNE amélioration concrète du prompt (reformulation, ajout d'instruction, meilleur cadrage).\n"
                f"3. Explique pourquoi cette mutation pourrait améliorer les résultats.\n\n"
                f"FORMAT:\n"
                f"ORIGINAL: [la partie du prompt ciblée]\n"
                f"MUTATION: [ta version améliorée]\n"
                f"RAISON: [pourquoi c'est mieux, en 1 phrase]\n"
            )

            response = await orchestrator.dispatch_task("coder", {
                "mission": mutation_mission,
                "context": "PROTOCOLE_AUTONOMIE\nGRIMOIRE_EVOLVE",
                "force_local": True,
            })

            result_text = response.get("result", "") if response else ""

            # Stocker la proposition en mémoire (PAS de remplacement automatique)
            if result_text and "MUTATION:" in result_text.upper():
                try:
                    from core.vector_store import ChromaMemoryManager
                    mgr = ChromaMemoryManager.get_instance()
                    if mgr:
                        mgr.add(
                            collection="collective_wisdom",
                            text=f"[GRIMOIRE_EVOLVE] Proposition mutation {slug}: {result_text[:500]}",
                            metadata={"source": "grimoire_evolve", "specialist": slug},
                        )
                        print(f"   📝 Mutation proposée pour [{slug}] stockée en mémoire.")
                except Exception:
                    pass

            return response or {"status": "error", "result": "Pas de réponse."}

        except Exception as e:
            return {"status": "error", "result": f"Grimoire evolve échoué: {e}"}


autonomy = AutonomyEngine(idle_threshold_seconds=300)
