"""Reasoning Protocol — controle qualite des productions LLM de Promethee.

Promethee ne delegue plus sa pensee aveuglement au LLM.
Chaque production critique passe par : preparer → produire → verifier → retry/accepter → apprendre.

Ce module est le FILTRE entre le LLM et le monde.
Il ne remplace pas le LLM — il le supervise.
"""

import json
import os
import time
import logging
import ast
from datetime import date, datetime
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILURES_FILE = os.path.join(_PROJECT_ROOT, "memory", "reasoning_failures.json")


# --- VERIFICATION : le resultat correspond-il a la realite ? ---

def verify_code_review(result: str, target_file: str) -> Tuple[bool, str]:
    """Verifie qu'un audit de code mentionne des elements REELS du fichier.

    Retourne (valide, raison).
    """
    if not result or not target_file:
        return False, "Resultat ou fichier vide"

    filepath = os.path.join(_PROJECT_ROOT, target_file)
    if not os.path.exists(filepath):
        return True, "Fichier introuvable — verification impossible"

    # Extraire les noms reels du fichier (classes, fonctions)
    real_names = _extract_real_names(filepath)
    if not real_names:
        return True, "Aucun nom extrait — verification impossible"

    # Extraire les noms cites dans le resultat
    cited_names = _extract_cited_names(result)
    if not cited_names:
        return False, "Aucune fonction/classe citee dans l'audit — travail vide"

    # Comparer : combien de noms cites existent reellement ?
    real_set = {n.lower() for n in real_names}
    cited_set = {n.lower() for n in cited_names}
    matches = cited_set & real_set
    fabricated = cited_set - real_set

    match_ratio = len(matches) / len(cited_set) if cited_set else 0

    if match_ratio < 0.3:
        # Moins de 30% des noms cites existent → hallucination
        return False, (f"HALLUCINATION: {len(fabricated)}/{len(cited_set)} noms cites "
                       f"n'existent pas dans {target_file}. "
                       f"Inventes: {list(fabricated)[:5]}. "
                       f"Reels: {list(matches)[:5]}")

    if fabricated:
        return True, (f"PARTIEL: {len(matches)} noms reels, {len(fabricated)} inventes. "
                      f"Inventes: {list(fabricated)[:3]}")

    return True, f"OK: {len(matches)} noms reels verifies"


def _extract_real_names(filepath: str) -> List[str]:
    """Extrait les noms de classes et fonctions d'un fichier Python via AST."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()
        tree = ast.parse(source)
        names = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.append(node.name)
            elif isinstance(node, ast.ClassDef):
                names.append(node.name)
        return names
    except Exception:
        return []


def _extract_cited_names(result: str) -> List[str]:
    """Extrait les noms de fonctions/classes cites dans un audit."""
    import re
    # Chercher les patterns : `nom_func`, nom_func(), class NomClass
    patterns = [
        r'`(\w{3,})`',                    # `function_name`
        r'(\w{3,})\(\)',                   # function_name()
        r'(?:fonction|function|methode|method|class|classe)\s+[`"]?(\w{3,})',
        r'def\s+(\w{3,})',                 # def function_name
        r'class\s+(\w{3,})',              # class ClassName
    ]
    names = set()
    for pattern in patterns:
        for match in re.finditer(pattern, result, re.IGNORECASE):
            name = match.group(1)
            # Filtrer les mots generiques
            if name.lower() not in {"self", "none", "true", "false", "return",
                                     "import", "from", "class", "print", "str",
                                     "int", "float", "dict", "list", "async",
                                     "await", "except", "raise", "pass", "try",
                                     "the", "and", "for", "with", "not"}:
                names.add(name)
    return list(names)


# --- VERIFICATION GENERIQUE ---

def verify_research(result: str, topic: str) -> Tuple[bool, str]:
    """Verifie qu'une synthese de recherche est substantielle."""
    if not result:
        return False, "Resultat vide"
    if len(result) < 200:
        return False, f"Synthese trop courte ({len(result)} chars, min 200)"
    # Verifier que le sujet est mentionne
    topic_words = [w.lower() for w in topic.split() if len(w) > 4]
    topic_found = sum(1 for w in topic_words if w in result.lower())
    if topic_words and topic_found == 0:
        return False, f"Le sujet '{topic}' n'est pas mentionne dans la synthese — hors sujet"
    return True, "OK"


def verify_workshop(result: str, topic: str) -> Tuple[bool, str]:
    """Verifie qu'un workshop contient du code."""
    if not result:
        return False, "Resultat vide"
    # Un workshop doit contenir du code Python
    code_markers = ["def ", "class ", "import ", "return ", "for ", "if ", "async def "]
    has_code = any(m in result for m in code_markers)
    if not has_code:
        return False, "Aucun code Python dans le livrable WORKSHOP — travail hors sujet"
    return True, "OK"


# --- RETRY : reformuler et reessayer ---

def build_retry_prompt(original_prompt: str, failure_reason: str) -> str:
    """Construit un prompt de retry avec le feedback de l'echec."""
    return (
        f"ATTENTION — TON TRAVAIL PRECEDENT A ETE REJETE.\n"
        f"RAISON DU REJET : {failure_reason}\n\n"
        f"CORRIGE ton travail en tenant compte de cette erreur.\n"
        f"Ne repete PAS la meme erreur. Lis le code REEL fourni.\n\n"
        f"--- CONSIGNE ORIGINALE ---\n{original_prompt}"
    )


# --- ROUTAGE INTELLIGENT ---

def choose_model(task_type: str, past_failures: int = 0) -> str:
    """Choisit le meilleur LLM selon la tache et l'historique d'echecs.

    Retourne : "local", "gemini", "claude"
    """
    # Taches qui beneficient de Gemini (reflexion profonde)
    gemini_tasks = {"EVENING_REFLECTION", "STEFAN_CONFRONTATION", "deep_chat"}

    # Apres 2+ echecs sur la meme tache → escalader
    if past_failures >= 2:
        return "gemini"

    if task_type in gemini_tasks:
        return "gemini"

    # Tout le reste → local (economie)
    return "local"


# --- MEMOIRE DES ECHECS ---

_failures_cache: List[Dict] = []
_loaded = False


def record_failure(task_type: str, target: str, reason: str):
    """Enregistre un echec pour apprentissage futur."""
    global _failures_cache, _loaded
    if not _loaded:
        _load_failures()

    entry = {
        "date": datetime.now().isoformat(),
        "task_type": task_type,
        "target": target[:100],
        "reason": reason[:300],
    }
    _failures_cache.append(entry)
    if len(_failures_cache) > 100:
        _failures_cache = _failures_cache[-100:]

    _save_failures()
    logger.info(f"REASONING: Echec enregistre — {task_type} : {reason[:80]}")


def get_failure_count(task_type: str, target: str = "") -> int:
    """Compte les echecs recents pour une tache."""
    global _loaded
    if not _loaded:
        _load_failures()

    today = date.today().isoformat()
    count = 0
    for f in _failures_cache:
        if (f.get("date", "").startswith(today)
                and f.get("task_type") == task_type
                and (not target or target in f.get("target", ""))):
            count += 1
    return count


def get_failure_stats() -> Dict[str, Any]:
    """Retourne les statistiques des echecs."""
    global _loaded
    if not _loaded:
        _load_failures()

    today = date.today().isoformat()
    today_failures = [f for f in _failures_cache if f.get("date", "").startswith(today)]

    by_type = {}
    for f in today_failures:
        t = f.get("task_type", "?")
        by_type[t] = by_type.get(t, 0) + 1

    return {
        "total": len(_failures_cache),
        "today": len(today_failures),
        "by_type": by_type,
        "recent": _failures_cache[-5:],
    }


def _save_failures():
    try:
        os.makedirs(os.path.dirname(FAILURES_FILE), exist_ok=True)
        with open(FAILURES_FILE, "w", encoding="utf-8") as f:
            json.dump(_failures_cache[-100:], f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _load_failures():
    global _failures_cache, _loaded
    _loaded = True
    try:
        if os.path.exists(FAILURES_FILE):
            with open(FAILURES_FILE, "r", encoding="utf-8") as f:
                _failures_cache = json.load(f)
    except Exception:
        _failures_cache = []
