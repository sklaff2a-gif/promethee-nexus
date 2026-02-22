"""
LoopBreaker — Detection et resolution de boucles repetitives dans les routines autonomes.

Grimoire 100% deterministe (zero LLM).
Analyse l'historique des routines pour detecter cycles, intents bloques,
et prescrit une strategie de sortie.
"""
import json
import logging
from collections import Counter
from typing import Dict, Any, List
from core.base_agent import BaseAgent

logger = logging.getLogger("loop_breaker")

# Tous les intents connus du moteur d'autonomie
_ALL_INTENTS = [
    "EXPANSION_CODE", "AUDIT_STRUCTURE", "VEILLE_SILENCIEUSE",
    "DROPZONE_SCAN", "COUNCIL_DEBATE", "GRIMOIRE_INVOKE",
    "SECURITY_AUDIT", "MEMORY_CLEANUP", "REFACTOR_RANDOM",
]


def detect_cycles(history: list) -> list:
    """Detecte les sequences d'intents qui se repetent (min 3 elements).

    Retourne une liste de cycles detectes (chaque cycle = liste d'intents).
    """
    if len(history) < 6:
        return []

    intents = [h.get("intent", "") for h in history]
    cycles = []

    # Chercher des patterns de longueur 2 a 4 qui se repetent >= 2 fois
    for pattern_len in range(2, 5):
        for start in range(len(intents) - pattern_len * 2 + 1):
            pattern = tuple(intents[start:start + pattern_len])
            # Compter les occurrences consecutives
            count = 1
            pos = start + pattern_len
            while pos + pattern_len <= len(intents):
                if tuple(intents[pos:pos + pattern_len]) == pattern:
                    count += 1
                    pos += pattern_len
                else:
                    break
            if count >= 2:
                cycle = list(pattern)
                if cycle not in cycles:
                    cycles.append(cycle)

    return cycles


def find_trapped_intents(history: list) -> list:
    """Intents avec >= 3 echecs consecutifs (les plus recents)."""
    if not history:
        return []

    trapped = []
    # Parcourir en reverse pour trouver les echecs recents par intent
    intent_failures = {}  # intent -> compteur d'echecs consecutifs recents

    for entry in reversed(history):
        intent = entry.get("intent", "")
        status = entry.get("status", "")
        if not intent:
            continue

        if intent not in intent_failures:
            # Premier encounter (en reverse) — verifier si c'est un echec
            if status in ("error", "low_quality", "max_rounds_low"):
                intent_failures[intent] = 1
            else:
                intent_failures[intent] = 0  # Marquer comme vu (succes)
        elif intent_failures[intent] > 0:
            # On continue a compter les echecs consecutifs
            if status in ("error", "low_quality", "max_rounds_low"):
                intent_failures[intent] += 1
            else:
                pass  # Stop counting, on a trouve un succes avant

    for intent, count in intent_failures.items():
        if count >= 3:
            trapped.append(intent)

    return trapped


def suggest_alternative_intents(history: list, blacklist: list) -> list:
    """Propose les intents les moins recemment utilises, hors blacklist."""
    recent_intents = [h.get("intent", "") for h in history[-10:]]
    intent_counts = Counter(recent_intents)

    # Intents disponibles (hors blacklist)
    available = [i for i in _ALL_INTENTS if i not in blacklist]
    if not available:
        return _ALL_INTENTS[:3]  # Fallback : les 3 premiers

    # Trier par nombre d'occurrences recentes (croissant = le moins utilise d'abord)
    available.sort(key=lambda i: intent_counts.get(i, 0))
    return available[:3]


def analyze_and_prescribe(history: list, error_streak: int) -> dict:
    """Analyse principale -> strategie de sortie.

    Retourne {action, details, ...} :
    - action="escalate" : error_streak >= 8 -> demander un Council
    - action="skip" : intents bloques -> blacklister
    - action="redirect" : cycles detectes -> forcer un intent alternatif
    - action="cooldown" : error_streak >= 5 -> ralentir
    - action="skip" : rien de special
    """
    # 1. Error streak critique -> escalade Council
    if error_streak >= 8:
        return {
            "action": "escalate",
            "details": f"Error streak critique ({error_streak}) — recommandation: Council de crise",
            "error_streak": error_streak,
        }

    # 2. Intents bloques -> blacklister
    trapped = find_trapped_intents(history)
    if trapped:
        alternatives = suggest_alternative_intents(history, trapped)
        return {
            "action": "skip",
            "details": f"Intents bloques: {', '.join(trapped)} — blacklister",
            "blacklist": trapped,
            "alternatives": alternatives,
        }

    # 3. Cycles detectes -> redirection
    cycles = detect_cycles(history)
    if cycles:
        # Extraire tous les intents dans les cycles pour les eviter
        cycle_intents = list(set(i for c in cycles for i in c))
        alternatives = suggest_alternative_intents(history, cycle_intents)
        forced = alternatives[0] if alternatives else ""
        return {
            "action": "redirect",
            "details": f"Cycle detecte: {cycles[0]} — forcer {forced}",
            "cycles": cycles,
            "forced_intent": forced,
            "alternatives": alternatives,
        }

    # 4. Error streak modere -> cooldown
    if error_streak >= 5:
        return {
            "action": "cooldown",
            "details": f"Error streak modere ({error_streak}) — rallonger le sleep",
            "extra_sleep": min(error_streak * 60, 600),  # Max 10 min de sleep supplementaire
            "error_streak": error_streak,
        }

    # 5. Rien de special
    return {
        "action": "skip",
        "details": "Pas de boucle ou blocage detecte",
    }


class LoopBreaker(BaseAgent):
    """Agent Grimoire : detection et resolution de boucles repetitives."""

    def __init__(self):
        super().__init__(
            name="loop_breaker",
            role="Detecteur de boucles",
            description="Detection et resolution de boucles repetitives dans les routines autonomes"
        )

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        context_raw = task_payload.get("context", "{}")

        # Deserialiser history + error_streak depuis context JSON
        try:
            context = json.loads(context_raw) if isinstance(context_raw, str) else context_raw
        except (json.JSONDecodeError, TypeError):
            context = {}

        history = context.get("history", [])
        error_streak = context.get("error_streak", 0)

        # Analyse et prescription
        prescription = analyze_and_prescribe(history, error_streak)

        return {
            "status": "success",
            "result": prescription.get("details", ""),
            "action": prescription.get("action", "skip"),
            "verdict": prescription.get("action", "skip"),
            **prescription,
        }
