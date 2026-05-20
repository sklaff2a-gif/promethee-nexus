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
    """Extrait les noms de classes, fonctions ET parametres d'un fichier Python via AST.

    V20a (2026-04-25) : ajout des `ast.arg` (parametres de fonctions). Sans
    ca, le verify_code_review rejetait les livrables qui citaient des params
    legitimes comme `min_last_section_words` (parametre de d2_truncation
    dans bullshit_detector.py) en les classant "inventes". Une CODE_REVIEW
    serieuse DOIT pouvoir citer les arguments des fonctions analysees.
    """
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()
        tree = ast.parse(source)
        names = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.append(node.name)
                # V20a : tous les parametres de la signature sont des "realites"
                args = node.args
                for arg in (args.args or []):
                    names.append(arg.arg)
                for arg in (args.kwonlyargs or []):
                    names.append(arg.arg)
                for arg in (args.posonlyargs or []):
                    names.append(arg.arg)
                if args.vararg:
                    names.append(args.vararg.arg)
                if args.kwarg:
                    names.append(args.kwarg.arg)
            elif isinstance(node, ast.ClassDef):
                names.append(node.name)
        return names
    except Exception:
        return []


# --- LOGPROBS : detecter l'hesitation du LLM en temps reel ---

async def measure_confidence(prompt: str, model: str = "qwen3.5:9b",
                              max_tokens: int = 100) -> Dict[str, Any]:
    """Mesure la confiance du LLM via les logprobs.

    Retourne :
      - avg_confidence : confiance moyenne (0-100%)
      - min_confidence : token le moins sur
      - uncertain_tokens : nombre de tokens < 50%
      - hallucination_risk : "low", "medium", "high"
    """
    import math
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "think": False,
                    "logprobs": True,
                    "top_logprobs": 3,
                    "options": {"num_predict": max_tokens, "temperature": 0.3},
                },
                timeout=30,
            )
        if resp.status_code != 200:
            return {"avg_confidence": 50, "hallucination_risk": "unknown"}

        data = resp.json()
        logprobs = data.get("logprobs", [])
        response_text = data.get("response", "")

        if not logprobs:
            return {"avg_confidence": 50, "hallucination_risk": "unknown",
                    "response": response_text}

        confidences = []
        uncertain_count = 0
        min_conf = 100.0

        for lp in logprobs:
            prob = lp.get("logprob", 0)
            confidence = math.exp(prob) * 100 if prob else 0
            confidences.append(confidence)
            if confidence < 50:
                uncertain_count += 1
            min_conf = min(min_conf, confidence)

        avg = sum(confidences) / len(confidences) if confidences else 50

        # Evaluer le risque d'hallucination
        if avg < 50 or uncertain_count > len(confidences) * 0.4:
            risk = "high"
        elif avg < 70 or uncertain_count > len(confidences) * 0.2:
            risk = "medium"
        else:
            risk = "low"

        return {
            "avg_confidence": round(avg, 1),
            "min_confidence": round(min_conf, 1),
            "uncertain_tokens": uncertain_count,
            "total_tokens": len(confidences),
            "hallucination_risk": risk,
            "response": response_text,
        }

    except Exception as e:
        logger.debug(f"REASONING: Mesure confiance echouee: {e}")
        return {"avg_confidence": 50, "hallucination_risk": "unknown"}


def _extract_cited_names(result: str) -> List[str]:
    """Extrait les noms de fonctions/classes cites dans un audit.

    V24 (2026-04-24) : regex renforcee pour eliminer les faux positifs
    grammaticaux francais ('soient', 'avec', 'dans'...) qui etaient captures
    par le pattern `(\\w{3,})`. Nouvelle strategie :
      1. Regex extrait les candidats
      2. Filtrage stoplist (anglais + francais courants)
      3. Heuristique "ressemble a un identifier Python" : un nom legitime
         contient au moins UN signal typique (underscore, digit, ou CamelCase
         avec >=2 majuscules). Les mots de langue naturelle (tout minuscules,
         pas d'underscore) sont ecartes meme s'ils passent la stoplist.
    """
    import re
    # Chercher les patterns : `nom_func`, nom_func(), class NomClass
    patterns = [
        r'`(\w{3,})`',                    # `function_name`
        r'(\w{3,})\(\)',                   # function_name()
        r'(?:fonction|function|methode|method|class|classe)\s+[`"]?(\w{3,})',
        r'def\s+(\w{3,})',                 # def function_name
        r'class\s+(\w{3,})',              # class ClassName
    ]
    # Stoplist etendue EN + FR (mots courants qu on ne veut pas compter
    # comme des noms de fonctions meme si ils passent la regex)
    _STOPLIST = {
        # Keywords Python / anglais courants
        "self", "none", "true", "false", "return", "import", "from",
        "class", "print", "str", "int", "float", "dict", "list", "set",
        "tuple", "async", "await", "except", "raise", "pass", "try",
        "the", "and", "for", "with", "not", "any", "all", "def",
        # V24 Francais : mots grammaticaux les plus frequents dans les audits
        "soient", "soit", "sont", "est", "etre", "avec", "sans", "dans",
        "pour", "par", "sur", "sous", "entre", "avant", "apres", "meme",
        "tous", "toutes", "tout", "cette", "cela", "ces", "son", "sa",
        "ses", "notre", "votre", "leur", "leurs", "donc", "aussi", "mais",
        "plus", "moins", "tres", "bien", "fait", "faire", "avoir", "ont",
        "peut", "peuvent", "doit", "doivent", "lors", "selon", "comme",
        "pendant", "alors", "ainsi", "puis", "encore", "quand", "car",
        "ailleurs", "autre", "autres", "chaque", "plusieurs", "aucun",
        "aucune", "deja", "jamais", "toujours", "souvent", "parfois",
        "rarement", "certes", "certain", "certaine", "certains", "certaines",
    }

    def _looks_like_py_identifier(n: str) -> bool:
        """Heuristique : un vrai identifier Python contient au moins UN
        signal typique. Les mots de langue naturelle sont tout minuscules
        sans underscore et sont rejetes ici."""
        if not n or len(n) < 3:
            return False
        if "_" in n:
            return True  # foo_bar, _private, __dunder__
        if any(c.isdigit() for c in n):
            return True  # foo42, v12b
        if n[0].isupper():
            # CamelCase reel : au moins 2 majuscules OU tout majuscule (intent like COUNCIL_DEBATE)
            upper_count = sum(1 for c in n if c.isupper())
            if upper_count >= 2:
                return True
        return False

    names = set()
    for pattern in patterns:
        for match in re.finditer(pattern, result, re.IGNORECASE):
            name = match.group(1)
            if name.lower() in _STOPLIST:
                continue
            if not _looks_like_py_identifier(name):
                continue
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
