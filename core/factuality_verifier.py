"""Veto epistemique V3.2 (2026-04-18) — Hard filter deterministe.

Detection des hallucinations structurelles dans les livrables CODE_REVIEW
et WORKSHOP via AST + regex. Conception adversariale Jean-Michel apres
premiere nuit V3.1 qui a renforce des synapses sur du travail fictif
(mentor Claude 2026-04-18 00:05 : "encore des hallucinations 8.7/10").

Architecture GAN : Professeur = generateur, ce verifier = discriminateur
strict (deterministe). Le Mentor Claude (LLM distant) reste le filtre
asynchrone couvrant les slots non-structurels (RESEARCH, CREATION).

Integration :
  - school_schedule.record_deliverable calcule factuality_score
  - Event SCHOOL_DELIVERABLE enrichi de factuality_score + factuality_total_refs
  - synaptic_network._learn_from_epistemic_closure applique le filtre F5 :
      * ratio >= 0.6          -> fermeture epistemique normale
      * 0 <= ratio < 0.6      -> veto + extinction causale (_learn_from_fruitless_goal)
      * ratio == -1.0         -> skip (bypass, pas de dopamine mais pas de punition)
"""

from __future__ import annotations

import ast
import logging
import os
import re
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


# --- Extraction des references ---
# Ligne : "L26", "L70:", "(ligne L26)", "ligne 70", "a la ligne 123,"
_LINE_PATTERN = re.compile(r'(?:^|[\s\(\*\`])[Ll](?:igne\s+)?(\d{1,5})(?=[\s\:\)\,\.\`])')
# Fonction en backticks : `verify_code_review`, `_extract_real_names()`
_FUNC_BACKTICK_PATTERN = re.compile(r'`([a-zA-Z_][a-zA-Z0-9_]{2,})(?:\(\))?`')
# Definition explicite dans bloc code : `def verify_code_review(`
_DEF_PATTERN = re.compile(r'def\s+([a-zA-Z_][a-zA-Z0-9_]{2,})\s*\(')

# Mots a filtrer : builtins Python, mots-cles communs, noms generiques
# qui ne sont pas de vraies references de code.
_IGNORE_NAMES = frozenset({
    "str", "int", "list", "dict", "set", "bool", "float", "tuple", "bytes",
    "True", "False", "None", "self", "cls",
    "print", "type", "len", "range", "isinstance", "hasattr", "getattr",
    "setattr", "enumerate", "zip", "map", "filter", "open", "sum", "min",
    "max", "abs", "all", "any", "sorted", "reversed", "super",
    "def", "class", "return", "import", "from", "yield", "async", "await",
    "try", "except", "finally", "raise", "with", "as", "lambda", "global",
    "Exception", "ValueError", "TypeError", "RuntimeError", "KeyError",
    "IndexError", "AttributeError", "OSError", "FileNotFoundError",
    "Dict", "List", "Tuple", "Set", "Optional", "Any", "Union", "Callable",
})


def extract_references(content: str) -> Dict[str, List]:
    """Extrait les references structurelles d'un livrable.

    Retourne un dict avec :
      - line_numbers : List[int] ordonne
      - function_names : List[str] ordonne (sans doublons)
    """
    lines = set()
    for m in _LINE_PATTERN.finditer(content):
        try:
            num = int(m.group(1))
            if 1 <= num <= 99999:
                lines.add(num)
        except (ValueError, AttributeError):
            continue

    funcs = set()
    for m in _FUNC_BACKTICK_PATTERN.finditer(content):
        name = m.group(1)
        if name not in _IGNORE_NAMES:
            funcs.add(name)
    for m in _DEF_PATTERN.finditer(content):
        name = m.group(1)
        if name not in _IGNORE_NAMES:
            funcs.add(name)

    return {
        "line_numbers": sorted(lines),
        "function_names": sorted(funcs),
    }


def verify_against_file(
    refs: Dict[str, List], target_path: str
) -> Tuple[int, int, Dict]:
    """Verifie les references contre le fichier cible reel.

    Retourne (true_refs, total_refs, details). true_refs = nombre de
    refs qui existent effectivement dans le fichier source. Une ligne
    est valide si son numero <= total de lignes du fichier. Une fonction
    est valide si elle apparait dans l'AST (FunctionDef ou AsyncFunctionDef).
    """
    if not target_path or not os.path.exists(target_path):
        return (0, 0, {"error": f"target_not_found:{target_path}"})

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            source = f.read()
    except Exception as e:
        return (0, 0, {"error": f"read_failed:{e}"})

    source_lines = source.count("\n") + 1

    real_funcs = set()
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                real_funcs.add(node.name)
    except SyntaxError:
        # Fallback regex : les fichiers non-python ou syntaxe cassee
        for m in _DEF_PATTERN.finditer(source):
            real_funcs.add(m.group(1))

    line_refs = refs.get("line_numbers", [])
    func_refs = refs.get("function_names", [])

    line_hits = sum(1 for n in line_refs if n <= source_lines)
    func_hits = sum(1 for name in func_refs if name in real_funcs)

    total_refs = len(line_refs) + len(func_refs)
    true_refs = line_hits + func_hits

    return (true_refs, total_refs, {
        "line_hits": line_hits,
        "line_total": len(line_refs),
        "func_hits": func_hits,
        "func_total": len(func_refs),
        "source_lines": source_lines,
        "real_funcs_count": len(real_funcs),
    })


def compute_factuality_score(
    content: str, target_file: str, project_root: str
) -> Tuple[float, int, Dict]:
    """Orchestre extraction + verification.

    Retourne (ratio, total_refs, details).
      - ratio in [0.0, 1.0]  : total_refs > 0, ratio = true/total
      - ratio == -1.0        : cas limite (pas de target_file ou 0 ref extraite)
                               -> bypass en amont, ni dopamine ni punition
    """
    if not target_file or not content:
        return (-1.0, 0, {"reason": "no_target_or_content"})

    if os.path.isabs(target_file):
        target_path = target_file
    else:
        target_path = os.path.join(project_root, target_file)

    refs = extract_references(content)
    true_refs, total_refs, details = verify_against_file(refs, target_path)

    details["refs_extracted"] = refs
    details["true_refs"] = true_refs

    if total_refs == 0:
        details["reason"] = "no_refs_parsable"
        return (-1.0, 0, details)

    ratio = true_refs / total_refs
    return (ratio, total_refs, details)
