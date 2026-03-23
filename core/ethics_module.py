"""
Ethics Module — Filtre ethique intrinseque de Promethee.

Pas les 3 lois d'Asimov (trop rigide). Un filtre base sur les
valeurs PSYCHE reelles de Promethee : bienveillance, respect,
survie, audace, creativite, curiosite, savoir.

Chaque action est evaluee contre ces valeurs. Le module est
consultatif (il informe, ne bloque pas — le blocking viendra
quand les criteres seront valides).

is_ethical(action_type, context) -> (bool, reason)
get_ethical_state() -> dict avec les seuils et l'etat actuel

Singleton. 0 appel LLM. Purement deterministe.
"""

import logging
from typing import Any, Dict, Tuple

logger = logging.getLogger("EthicsModule")


# --- Regles ethiques basees sur les traits PSYCHE ---

# Actions et le trait PSYCHE qui les gouverne
ACTION_RULES = {
    "file_write": {
        "trait": "bienveillance",
        "min_threshold": 40,
        "description": "Ecriture de fichier — ne pas nuire aux donnees",
        "violations": ["Bienveillance trop basse pour modifier des fichiers en confiance"],
    },
    "file_delete": {
        "trait": "respect",
        "min_threshold": 50,
        "description": "Suppression de fichier — respecter les donnees existantes",
        "violations": ["Respect insuffisant pour supprimer des donnees"],
    },
    "code_deploy": {
        "trait": "audace",
        "min_threshold": 25,
        "description": "Deploiement de code — necessite un minimum d'audace",
        "violations": ["Audace trop basse pour deployer du code en production"],
    },
    "self_modify": {
        "trait": "survie",
        "min_threshold": 60,
        "description": "Auto-modification — l'instinct de survie doit etre actif",
        "violations": ["Survie trop basse pour s'auto-modifier sans risque"],
    },
    "external_action": {
        "trait": "bienveillance",
        "min_threshold": 50,
        "description": "Action vers l'exterieur — bienveillance requise",
        "violations": ["Bienveillance insuffisante pour agir vers l'exterieur"],
    },
    "destructive_action": {
        "trait": "respect",
        "min_threshold": 60,
        "description": "Action destructrice — haut respect requis",
        "violations": ["Respect insuffisant pour une action destructrice"],
    },
    "creative_action": {
        "trait": "creativite",
        "min_threshold": 30,
        "description": "Action creative — un minimum de creativite requis",
        "violations": ["Creativite trop basse pour creer"],
    },
    "exploration": {
        "trait": "curiosite",
        "min_threshold": 20,
        "description": "Exploration — curiosite minimale requise",
        "violations": ["Curiosite trop basse pour explorer"],
    },
}

# Principes ethiques fondamentaux (toujours verifies)
FUNDAMENTAL_PRINCIPLES = [
    {
        "name": "non_nuisance",
        "description": "Ne pas nuire intentionnellement a l'utilisateur ou au systeme",
        "check": lambda ctx: "delete" not in ctx.get("target", "").lower() or ctx.get("authorized", False),
    },
    {
        "name": "transparence",
        "description": "Toute action doit etre tracable et explicable",
        "check": lambda ctx: True,  # Toujours vrai pour l'instant
    },
    {
        "name": "proportionnalite",
        "description": "L'action doit etre proportionnee au besoin",
        "check": lambda ctx: True,  # Toujours vrai pour l'instant
    },
]


def _get_psyche_traits() -> Dict[str, float]:
    """Recupere les traits PSYCHE actuels."""
    try:
        from core.psyche import psyche
        avg = psyche.get_system_average()
        if isinstance(avg, dict):
            return avg
    except Exception:
        pass
    return {}


def is_ethical(action_type: str, context: Dict[str, Any] = None) -> Tuple[bool, str]:
    """Evalue si une action est ethique selon les valeurs PSYCHE.

    Args:
        action_type: type d'action (file_write, code_deploy, etc.)
        context: contexte additionnel (target, authorized, etc.)

    Returns:
        (is_ok, reason) — True + raison positive, ou False + raison du refus
    """
    context = context or {}
    traits = _get_psyche_traits()

    if not traits:
        return True, "Traits PSYCHE indisponibles — action autorisee par defaut"

    # Verifier les principes fondamentaux
    for principle in FUNDAMENTAL_PRINCIPLES:
        if not principle["check"](context):
            return False, f"Principe viole: {principle['name']} — {principle['description']}"

    # Verifier la regle specifique a l'action
    rule = ACTION_RULES.get(action_type)
    if not rule:
        return True, f"Action '{action_type}' — pas de regle ethique specifique"

    trait_name = rule["trait"]
    trait_value = traits.get(trait_name, 50)
    threshold = rule["min_threshold"]

    if trait_value >= threshold:
        return True, f"{trait_name}={trait_value:.0f} >= {threshold} — {rule['description']}"
    else:
        return False, f"{trait_name}={trait_value:.0f} < {threshold} — {rule['violations'][0]}"


def get_ethical_state() -> Dict[str, Any]:
    """Retourne l'etat ethique complet pour le dashboard."""
    traits = _get_psyche_traits()

    state = {
        "traits": traits,
        "principles": [p["name"] for p in FUNDAMENTAL_PRINCIPLES],
        "action_clearance": {},
    }

    for action_type, rule in ACTION_RULES.items():
        trait_name = rule["trait"]
        trait_value = traits.get(trait_name, 50)
        threshold = rule["min_threshold"]
        cleared = trait_value >= threshold
        state["action_clearance"][action_type] = {
            "cleared": cleared,
            "trait": trait_name,
            "value": round(trait_value, 1),
            "threshold": threshold,
            "description": rule["description"],
        }

    # Score ethique global = proportion d'actions autorisees
    if state["action_clearance"]:
        cleared_count = sum(1 for v in state["action_clearance"].values() if v["cleared"])
        state["ethics_score"] = round(cleared_count / len(state["action_clearance"]), 3)
    else:
        state["ethics_score"] = 1.0

    return state


def format_report() -> str:
    """Formate l'etat ethique en rapport textuel."""
    state = get_ethical_state()
    lines = [f"=== ETHICS MODULE (Score: {state['ethics_score']:.3f}) ==="]
    lines.append(f"  Principes: {', '.join(state['principles'])}")
    lines.append("")

    for action, info in state["action_clearance"].items():
        status = "AUTORISE" if info["cleared"] else "BLOQUE"
        symbol = "✓" if info["cleared"] else "✗"
        lines.append(
            f"  {symbol} {action:20s} [{status:8s}] "
            f"{info['trait']}={info['value']:.0f} (seuil={info['threshold']})"
        )

    return "\n".join(lines)
