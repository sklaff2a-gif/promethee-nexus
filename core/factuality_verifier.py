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


# ============================================================
# V3.3 Factuality CREATION (2026-04-19, Jean-Michel + Gemini)
# ============================================================
# Veto pour slots non-structurels (CREATION) sans LLM. 3 vecteurs :
#   V1 Ratio tech (anti-fuite code Python dans une fable)
#   V2 Key-terms (livrable ignore les noms propres de la consigne)
#   V3 Format (haiku demande, roman livre)
# Seuil veto : < 0.6 (coherent avec compute_factuality_score structurel).
# Bypass (-1.0) : aucun vecteur applicable (consigne totalement libre).

# V1 : patterns syntaxiques techniques (Gemini verdict : STRICTE uniquement)
# Les mots isoles ("algorithme", "neurone") NE sont PAS sanctionnes.
_TECH_PATTERNS = [
    re.compile(r'```[a-zA-Z]*\n'),                   # debut bloc code
    re.compile(r'```'),                              # fin bloc
    re.compile(r'`[\w\.]+\([^)]*\)`'),               # `function(args)` inline
    re.compile(r'`[\w_]+\.[\w_]+`'),                 # `module.function` inline
    re.compile(r'\bimport\s+[\w_\.]+'),              # import module
    re.compile(r'\bfrom\s+[\w_\.]+\s+import\b'),     # from X import Y
    re.compile(r'\bdef\s+\w+\s*\('),                 # def func(
    re.compile(r'\basync\s+def\s+\w+\s*\('),         # async def
    re.compile(r'\bclass\s+\w+\s*[\(:]'),            # class Foo(: or class Foo:
    re.compile(r'\breturn\s+[\{\[\(]'),              # return {..., return [..., return (
]
_TECH_RATIO_VETO = 0.03  # > 3 tokens techniques par 100 tokens prose -> veto
_CREATION_VETO_THRESHOLD = 0.6  # coherent avec V3.2

# V2 : extraction noms propres + mots rares de la consigne
_PROPER_NOUN_PATTERN = re.compile(
    r'\b[A-ZÉÈÊÀÂÎÔÛÙÇ][a-zéèêàâîôûùç]{2,}(?:\s+[A-ZÉÈÊÀÂÎÔÛÙÇ][a-zéèêàâîôûùç]{2,})*\b'
)
_CHALLENGE_STOP_WORDS = frozenset({
    # Mots francais ultra-frequents qu on doit filtrer meme capitalizes en debut de phrase
    "Pour", "Une", "Un", "Des", "Les", "La", "Le", "Dans", "Avec", "Sur", "Par",
    "Apres", "Avant", "Mais", "Et", "Ou", "Si", "Tu", "Il", "Elle", "Ils", "Elles",
    "Je", "Nous", "Vous", "Mon", "Ma", "Mes", "Ton", "Ta", "Tes", "Son", "Sa", "Ses",
    "Ecris", "Écris", "Invente", "Imagine", "Compose", "Redige", "Rédige",
    "Creer", "Créer", "Peux", "Dois", "Faut", "Quand", "Comment", "Pourquoi",
    # Anglais au cas ou
    "The", "A", "An", "And", "Or", "If", "You", "We",
})

# V3 : regles de format (regex -> predicat structure)
def _count_non_empty_lines(text: str) -> int:
    return sum(1 for line in text.split('\n') if line.strip())

_FORMAT_RULES = [
    (re.compile(r'\bhaiku\b', re.IGNORECASE),
     lambda c: 2 <= _count_non_empty_lines(c.strip()) <= 7),
    (re.compile(r'\bsonnet\b', re.IGNORECASE),
     lambda c: 10 <= _count_non_empty_lines(c) <= 18),
    (re.compile(r'\bquatrain\b', re.IGNORECASE),
     lambda c: _count_non_empty_lines(c) == 4),
    (re.compile(r'\b(?:un seul|unique|1\s+seul)\s+paragraphe\b', re.IGNORECASE),
     lambda c: c.count('\n\n') <= 1),
    # "reecris la fin" : ne doit PAS etre une version complete
    (re.compile(r'\br[eé]écris?\s+(?:la\s+)?fin\b', re.IGNORECASE),
     lambda c: len(c) < 2000),  # heuristique : une "fin" < 2000 chars
]


def compute_tech_ratio(content: str) -> float:
    """Nb tokens techniques / nb mots total. Mots isoles non sanctionnes."""
    if not content:
        return 0.0
    tech_count = sum(len(pat.findall(content)) for pat in _TECH_PATTERNS)
    word_count = max(1, len(content.split()))
    return tech_count / word_count


def extract_key_terms(challenge: str) -> List[str]:
    """Noms propres + mots rares de la consigne (filtre stop-words)."""
    if not challenge:
        return []
    terms = set()
    for m in _PROPER_NOUN_PATTERN.finditer(challenge):
        token = m.group(0)
        # Filtrer stop-words (y compris composes)
        parts = token.split()
        if any(p in _CHALLENGE_STOP_WORDS for p in parts):
            continue
        terms.add(token)
    return sorted(terms)


def compute_coverage(content: str, key_terms: List[str]) -> float:
    """Fraction des key_terms presents (case-insensitive) dans le livrable."""
    if not key_terms:
        return 1.0  # pas de terme extrait -> couverture triviale
    content_lower = content.lower()
    hits = sum(1 for t in key_terms if t.lower() in content_lower)
    return hits / len(key_terms)


def detect_format_rule(challenge: str):
    """Retourne le predicat de format si une regle declenche sur la consigne."""
    if not challenge:
        return None
    for pattern, predicate in _FORMAT_RULES:
        if pattern.search(challenge):
            return predicate
    return None


def compute_creation_factuality(
    content: str, challenge: str
) -> Tuple[float, Dict]:
    """Orchestre les 3 vecteurs pour slot CREATION.

    Retourne (score, details). score in [0, 1] ou -1.0 (bypass).
    Pondérations : V1=1.0, V2=1.5, V3=2.0 (verdict Gemini).
    """
    if not content:
        return (-1.0, {"reason": "no_content"})

    scores = []
    weights = []
    details = {"vectors": {}}

    # V1 : ratio tech (toujours applicable si contenu)
    ratio = compute_tech_ratio(content)
    v1_score = 1.0 - min(ratio / _TECH_RATIO_VETO, 1.0)
    scores.append(v1_score)
    weights.append(1.0)
    details["vectors"]["v1_tech_ratio"] = {
        "raw_ratio": round(ratio, 4),
        "score": round(v1_score, 3),
    }

    # V2 : coverage (actif seulement si >= 2 key terms extraits)
    key_terms = extract_key_terms(challenge)
    if len(key_terms) >= 2:
        coverage = compute_coverage(content, key_terms)
        scores.append(coverage)
        weights.append(1.5)
        details["vectors"]["v2_coverage"] = {
            "key_terms": key_terms,
            "score": round(coverage, 3),
        }

    # V3 : regle de format (actif seulement si pattern detecte)
    format_rule = detect_format_rule(challenge)
    if format_rule is not None:
        v3_score = 1.0 if format_rule(content) else 0.0
        scores.append(v3_score)
        weights.append(2.0)
        details["vectors"]["v3_format"] = {"score": v3_score}

    if not scores:
        details["reason"] = "no_vector_applicable"
        return (-1.0, details)

    weighted = sum(s * w for s, w in zip(scores, weights)) / sum(weights)
    details["final_score"] = round(weighted, 3)
    details["active_vectors"] = len(scores)
    return (weighted, details)


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
