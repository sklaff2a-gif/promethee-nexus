# -*- coding: utf-8 -*-
"""V25.1_INTEGRATION — proto de cablage prefrontal dans generate_content (SIMULE).

Sandbox ISOLE (ne touche PAS base_agent.py). Simule la topologie de generate_content :
    [prompt] -> [appel LLM (ebauche)] -> [routage par marqueur de slot]
        code  -> miroir DETERMINISTE (ast) -> echec persistant -> AVORTEMENT SEC (veto)
        intro -> miroir COMPORTEMENTAL (LLM) -> echec persistant -> MODE DEGRADE (balise)

Garde-fous (V25.1) :
  1. Etranglement budgetaire : MAX_RETRIES tentatives, jamais de boucle infinie.
  2. Isolation des contextes : la `friction` (motif de refus) est EPHEMERE — passee a
     l'appel de regeneration, JAMAIS persistee dans l'historique du chat/session.
  3. Asymetrie a l'epuisement : code = veto sec ; intro = livrer le dernier texte + balise.
"""
from anticipation_engine import mirror
from behavioral_mirror import make_behavioral_mirror

MAX_RETRIES = 2

# slots qui produisent du CODE -> miroir deterministe ; le reste = introspectif -> comportemental
CODE_SLOTS = ("CODE_REVIEW", "WORKSHOP", "REFACTORING_AUDIT", "FEATURE_BUILDING")


def _slot_is_code(prompt: str) -> bool:
    p = prompt or ""
    return (any(f"[SCHOOL_SLOT: {s}]" in p for s in CODE_SLOTS)
            or "[V32: FEATURE_BUILDING]" in p)


def route_mirror(prompt, judge=None):
    """Route le miroir selon le marqueur de slot. Retourne (mirror_fn, mode)."""
    if _slot_is_code(prompt):
        return mirror, "code"
    return make_behavioral_mirror(judge), "intro"


def guarded_generate(prompt, llm_generate, judge=None, max_retries=MAX_RETRIES):
    """Controle prefrontal autour de l'appel LLM, avec boucle de reorientation ISOLEE.

    llm_generate(prompt, friction) -> ebauche (str)
        Dans le runtime reel = la partie appel-LLM de generate_content, re-appelable.
        `friction` (None au 1er tour) = la trace du refus precedent, EPHEMERE.

    Retourne un dict : delivered / status (ok|veto|degraded) / mode / attempts / anomaly.
    """
    mirror_fn, mode = route_mirror(prompt, judge)
    friction = None        # contexte ephemere, isole de tout historique permanent
    rejections = []
    last = None
    for attempt in range(1, max_retries + 1):
        last = llm_generate(prompt, friction)
        ok, rejection = mirror_fn(last)
        if ok:
            return {"delivered": last, "status": "ok", "mode": mode,
                    "attempts": attempt, "rejections": rejections, "anomaly": False}
        rejections.append(rejection)
        friction = rejection      # reinjecte EPHEMEREMENT a la tentative suivante

    # --- epuisement du budget : comportement ASYMETRIQUE ---
    if mode == "code":
        return {"delivered": None, "status": "veto", "mode": mode, "attempts": max_retries,
                "rejections": rejections, "anomaly": False,
                "reason": "PREFRONTAL_VETO (code) : ebauche non valide apres reorientation, canal coupe."}
    # intro : mode degrade -> livrer le dernier texte AVEC une balise d'anomalie visible
    balise = (f"[ANOMALIE PREFRONTALE : derive de posture signalee, "
              f"non corrigee apres {max_retries} tentatives — arbitrage mentor requis]")
    return {"delivered": balise + "\n" + (last or ""), "status": "degraded", "mode": mode,
            "attempts": max_retries, "rejections": rejections, "anomaly": True}
