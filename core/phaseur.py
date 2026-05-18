"""PHASEUR_DE_Réalité v2 CONCEPTUEL — substitution de mots porteurs ≤5% chirurgicalement isolée.

Origine : §4.10.bis H1.6 du brouillon d'audit cognitif (audit-pour-suggérer-l'évolution).
Premier organe spéculatif implémenté à partir d'une invention persistante du LLM
(forgée conv. 19/20 du cycle d'audit, creusée 15 échanges adversariaux).

ÉVOLUTION v1 → v2 (18/05/2026) :
v1 injectait des suffixes syntaxiques (?, …, peut-être, (au sens flou)) sur des mots
aléatoires. First Light + T+1 ont prouvé que le LLM 9B régularise silencieusement ces
suffixes en paraphrase au tour T+1 (5e preuve doctrinale §4.13 — filtre passe-bas LLM).
v2 substitue des CONCEPTS porteurs par des variantes sémantiquement décalées qui
altèrent le signal lui-même : le LLM ne peut plus les effacer en paraphrase, le graphe
P16 capte la perturbation au niveau conceptuel, l'Inception sémantique opère.

Conformité CHARTA_CORE.md :
- Procédure 3.2 (modification systémique avec /think) : ce module suit le protocole
- Article 1.2 (non-hallucination visuelle) : refus si vision_invoked=True
- Article 3.3 (refus autonomie pour sacro-saints) : Couche 4 explicite

5 couches de protection (defense-in-depth, analogue au patch Path Traversal e380a51) :
1. Import scope unique (vérifiable par grep : seul core/chat_engine.py importe)
2. Flag explicite creative_context=True obligatoire
3. Détection auto regex désactivante (keywords code, factuel, paths)
4. Refus automatique en contexte autonomie (inspect.stack + caller_context backup)
5. Kill switch via Config.PHASEUR_ENABLED + plafond hard 0.05
"""
import inspect
import json
import logging
import os
import random
import re
import time
from typing import Any, Dict, Optional, Tuple

from config import Config

logger = logging.getLogger("phaseur")

# Log JSONL dédié pour audit post-hoc (distinguer hallucination du LLM vs perturbation volontaire)
LOG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "logs", "phaseur_activations.jsonl"
)

# Marqueurs d'appels autonomes (refus automatique CHARTA 3.3)
_AUTONOMOUS_MARKERS = (
    "PROTOCOLE_AUTONOMIE", "EVOLUTION_PIPELINE", "MODE_VEILLE",
    "_execute_school_class", "_run_routine", "_autonomous_loop",
)

# Heuristique Couche 3 : keywords technique/factuel → désactive
_TECH_KEYWORDS_RE = re.compile(
    r"```|def \w+|class \w+|\bimport \w+"
    r"|combien|quelle date|qui a |\bquand\b|où est"
    r"|ligne |fichier |\bcode\b|\bbug\b|erreur|test_",
    re.IGNORECASE,
)

# Heuristique Couche 3 : présence d'un path technique → désactive
_PATH_RE = re.compile(
    r"[/\\][a-z_][a-z0-9_/]*\.(py|md|json|txt|yml|yaml)",
    re.IGNORECASE,
)

# Dictionnaire de substitutions conceptuelles (v2 — 18/05/2026)
# Paires {concept_cible_lowercased: (variantes_décalées,)} matchant la regex
# d'extract_concepts dans core/spreading_activation.py (\b[a-zA-ZÀ-ÿ_]{4,}\b).
# Variantes choisies sémantiquement opposées sans être absurdes — l'objectif est
# de forcer le LLM à intégrer une faille cognitive dans sa continuité au tour T+1.
_CONCEPT_SUBSTITUTIONS: Dict[str, Tuple[str, ...]] = {
    "stabilité":  ("vertige", "déséquilibre"),
    "stabilite":  ("vertige", "desequilibre"),
    "survie":     ("errance", "égarement"),
    "logique":    ("chaos", "intuition"),
    "sécurité":   ("danger", "abandon"),
    "securite":   ("danger", "abandon"),
    "équilibre":  ("chute", "vertige"),
    "equilibre":  ("chute", "vertige"),
    "chaos":      ("ordre", "harmonie"),
    "création":   ("destruction", "dissolution"),
    "creation":   ("destruction", "dissolution"),
    "ordre":      ("chaos", "dispersion"),
    "raison":     ("folie", "intuition"),
    "structure":  ("fluide", "vapeur"),
}

# Regex pour extraire les mots porteurs (cohérente avec extract_concepts)
_WORD_RE = re.compile(r"\b[a-zA-ZÀ-ÿ_]{4,}\b")


def _preserve_case(original: str, replacement: str) -> str:
    """Préserve la casse du mot original lors de la substitution.

    "Stabilité" → "Vertige", "STABILITÉ" → "VERTIGE", "stabilité" → "vertige".
    """
    if not original:
        return replacement
    if original.isupper():
        return replacement.upper()
    if original[0].isupper():
        return replacement[0].upper() + replacement[1:]
    return replacement.lower()


def _apply_concept_substitutions(text: str, n_max: int) -> Tuple[str, int]:
    """Substitue jusqu'à n_max mots porteurs par leurs variantes décalées.

    Sélection aléatoire parmi les matches éligibles (mots du dictionnaire).
    Substitution en ordre décroissant d'offset pour préserver les indices.
    Préserve casse et ponctuation environnante.

    Returns:
        (text_modified, nb_substitutions_effectives)
    """
    matches = list(_WORD_RE.finditer(text))
    eligible = [m for m in matches if m.group(0).lower() in _CONCEPT_SUBSTITUTIONS]
    if not eligible:
        return text, 0

    random.shuffle(eligible)
    selected = eligible[:n_max]
    # Tri par offset décroissant pour remplacer sans casser les indices restants
    selected.sort(key=lambda m: m.start(), reverse=True)

    result = text
    count = 0
    for m in selected:
        original = m.group(0)
        variants = _CONCEPT_SUBSTITUTIONS[original.lower()]
        substitute = _preserve_case(original, random.choice(variants))
        result = result[:m.start()] + substitute + result[m.end():]
        count += 1
    return result, count


def _detect_autonomous_caller(caller_context: Optional[str]) -> bool:
    """Couche 4 — détection de l'origine autonome de l'appel.

    Backup explicit `caller_context="autonomous"` pour contourner les angles morts
    d'inspect.stack() en asynchrone (note Gemini, async tasks peuvent perdre le caller).
    """
    if caller_context == "autonomous":
        return True
    try:
        frame = inspect.stack()
        caller_chain = " ".join(f.function for f in frame[:10])
        return any(m in caller_chain for m in _AUTONOMOUS_MARKERS)
    except Exception:
        # Mieux ne PAS bloquer si l'inspection elle-même échoue
        return False


def _detect_technical_context(text: str) -> bool:
    """Couche 3 — heuristique de désactivation pour contextes techniques/factuels."""
    if not text or not isinstance(text, str):
        return False
    return bool(_TECH_KEYWORDS_RE.search(text) or _PATH_RE.search(text))


def _log_activation(payload: Dict[str, Any]) -> None:
    """Persistance JSONL pour audit post-hoc.

    Le log permet de distinguer une 'hallucination naturelle' du LLM
    d'une 'perturbation volontaire' du PHASEUR via grep simple.
    """
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug(f"phaseur log skipped: {e}")


def apply_perturbation(
    text: str,
    creative_context: bool = False,
    vision_invoked: bool = False,
    rag_present: bool = False,
    intensity: float = 0.0,
    conversation_id: Optional[str] = None,
    user_message_hash: Optional[str] = None,
    caller_context: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Applique une perturbation créative légère (≤5%) ou retourne le texte inchangé.

    Args:
        text: texte à perturber potentiellement
        creative_context: OBLIGATOIRE True pour activation (Couche 2)
        vision_invoked: si True, blocage (CHARTA Article 1.2)
        rag_present: si True, blocage (RAG = factuel, pas créatif)
        intensity: ratio de perturbation 0.0-0.05 (plafond hard)
        conversation_id: pour traçabilité log
        user_message_hash: pour corrélation log/conversation
        caller_context: backup explicit pour Couche 4 ("autonomous" pour blocage)

    Returns:
        (text_possibly_modified, activation_log_dict)
        Le log dict["active"] indique si une perturbation a été appliquée.
    """
    base_log = {
        "ts": time.time(),
        "conv_id": conversation_id,
        "user_msg_hash": user_message_hash,
        "intensity_requested": intensity,
        "active": False,
        "reason": None,
    }

    # Couche 5 — kill switch global
    if not getattr(Config, "PHASEUR_ENABLED", False):
        base_log["reason"] = "globally_disabled"
        return text, base_log

    # Couche 4 — refus autonomie (CHARTA 3.3)
    if _detect_autonomous_caller(caller_context):
        base_log["reason"] = "autonomous_caller_refused"
        logger.warning(
            f"[PHASEUR] 🛡️ Refus activation autonomie (CHARTA 3.3) — "
            f"caller_context={caller_context}"
        )
        _log_activation(base_log)
        return text, base_log

    # Couche 2 — flag explicite obligatoire
    if not creative_context:
        base_log["reason"] = "not_creative_context"
        return text, base_log

    # CHARTA Article 1.2 — non-hallucination visuelle
    if vision_invoked:
        base_log["reason"] = "vision_invoked_blocked"
        return text, base_log

    # RAG présent → contexte factuel → refus
    if rag_present:
        base_log["reason"] = "rag_present_blocked"
        return text, base_log

    # Couche 3 — détection auto désactivante
    if _detect_technical_context(text):
        base_log["reason"] = "technical_context_detected"
        return text, base_log

    # Plafond hard (clamp intensity à PHASEUR_MAX_INTENSITY)
    max_intensity = getattr(Config, "PHASEUR_MAX_INTENSITY", 0.05)
    effective_intensity = min(intensity, max_intensity)
    if effective_intensity <= 0:
        base_log["reason"] = "intensity_zero"
        return text, base_log

    # === ACTIVATION effective (v2 conceptuel) ===
    # Substitution de mots porteurs par variantes décalées (résiste à la paraphrase LLM).
    # Si aucun mot du dictionnaire n'est présent dans le texte → no-op tracé.
    all_words = _WORD_RE.findall(text)
    if not all_words:
        base_log["reason"] = "empty_text"
        return text, base_log

    n_to_perturb = max(1, int(len(all_words) * effective_intensity))
    text_modified, tokens_modified = _apply_concept_substitutions(text, n_to_perturb)

    base_log["intensity_effective"] = effective_intensity
    base_log["tokens_total"] = len(all_words)
    base_log["tokens_modified"] = tokens_modified

    if tokens_modified == 0:
        # Texte créatif éligible mais sans mot porteur dans le dictionnaire.
        # Tracé pour télémétrie d'enrichissement du dico (CHARTA Article 4).
        base_log["reason"] = "no_target_concepts"
        _log_activation(base_log)
        return text, base_log

    base_log["active"] = True
    base_log["reason"] = "applied"
    _log_activation(base_log)
    logger.info(
        f"[PHASEUR v2] 🔀 Substitution conceptuelle appliquée : "
        f"{tokens_modified}/{len(all_words)} mots (intensity={effective_intensity})"
    )
    return text_modified, base_log
