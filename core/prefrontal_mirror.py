# -*- coding: utf-8 -*-
"""core/prefrontal_mirror.py — Cortex prefrontal : anticipation frugale (V25.3).

Avorte les ebauches defectueuses AVANT livraison, dans generate_content :
  - MIROIR DETERMINISTE (slots de PRODUCTION de code) : extraction des blocs ```python
    d'une sortie markdown + ast.parse par bloc (immunite snippets), 0 jeton.
    Echec persistant -> AVORTEMENT SEC (un code non compilable ne se livre jamais).
  - MIROIR COMPORTEMENTAL (slots introspectifs / analyses) : mini-juge LLM async (temp 0,
    JSON strict). Doctrine INVERSE : doute / JSON casse / timeout -> ON LAISSE PASSER.
    Reorientation = DIAGNOSTIC DE POSTURE (categorie + direction, jamais le lexeme).
    Echec persistant -> MODE DEGRADE (livrer + balise [METABOLISME_ALERT]).

V25.3 (08/06) — L'oracle dur ne decapite plus la prose. Le tamis regex isole la charge
utile (blocs ```python) ; pas de bloc -> FAIL-OPEN (documentation pure). L'arbitrage du
code ILLUSTRATIF (un bug montre expres dans un CODE_REVIEW est correct) est resolu
STRUCTURELLEMENT par le TYPE DE SLOT, jamais par heuristique lexicale (cf 12e lecon de
Promethee : intention a la source, pas re-parsing par mots-cles). Seuls les slots de
PRODUCTION de code passent au deterministe ; les ANALYSES vont au comportemental.

Conception + TDD : tests/test_prefrontal_mirror.py. Module importe par core/base_agent.py.
"""
import ast
import re
import json
import builtins
import textwrap

MAX_PREFRONTAL_RETRIES = 2
PATHOS_THRESHOLD = 0.6
_DANGEROUS = {"eval", "exec", "__import__", "compile"}
_BUILTINS = set(dir(builtins))

# Slots qui PRODUISENT du code destine a l'execution -> miroir deterministe.
PRODUCTION_CODE_SLOTS = ("WORKSHOP", "FEATURE_BUILDING")
# Slots d'ANALYSE : le code y est ILLUSTRATIF (extrait cite, bug montre expres) -> JAMAIS
# de validation syntaxique (anti-faux-positif). Documentes pour la clarte du routage.
ANALYSIS_SLOTS = ("CODE_REVIEW", "REFACTORING_AUDIT")
# Zones de VULNERABILITE EXISTENTIELLE (soliloques nocturnes, bilans) -> miroir comportemental.
# C'est la, et SEULEMENT la, que le risque de spleen / circularite affective (V16.7) est documente.
INTROSPECTIVE_SLOTS = ("BULLETIN", "CREATION", "FREE_TIME")

# Tamis : blocs delimites ```python ... ``` (langage python/py explicite, casse ignoree).
_CODE_BLOCK_RE = re.compile(r"```[ \t]*(?:python|py)[ \t]*\r?\n(.*?)```",
                            re.DOTALL | re.IGNORECASE)


# =========================================================================
# MIROIR DETERMINISTE (V25.3) — extraction de blocs + ast.parse, 0 jeton
# =========================================================================
def extract_code_blocks(text: str):
    """Isole la charge utile : tous les blocs ```python ... ``` d'une sortie markdown.
    Retourne une liste de str (le corps de chaque bloc). Vide si aucun bloc."""
    if not text:
        return []
    return [m.group(1) for m in _CODE_BLOCK_RE.finditer(text)]


def _try_parse(code: str):
    """ast.parse defensif : retourne l'arbre, ou None sur SyntaxError."""
    try:
        return ast.parse(code)
    except SyntaxError:
        return None


def _syntax_error(code: str):
    """Recupere la SyntaxError reelle d'un fragment (pour le diagnostic)."""
    try:
        ast.parse(code)
    except SyntaxError as e:
        return e
    return None


def _validate_snippet(code: str):
    """Valide UN bloc de code avec IMMUNITE SNIPPETS. (ok, rejection|None).

    1) dedent : neutralise l'indentation flottante d'un extrait copie-colle.
    2) ast.parse direct.
    3) si echec, 2e tentative en ENVELOPPANT dans une fonction : `return`/`yield`/`await`/
       `break`/`continue` hors fonction sont des artefacts d'extrait LEGITIMES, pas des fautes.
    4) si les DEUX echouent -> erreur STRUCTURELLE reelle (chaine non fermee, parenthese
       orpheline, mot-cle fauté) -> sanction.
    NB : le scope-check (NameError, V24.1) n'est PAS applique aux blocs extraits : un extrait
    cite n'a pas son contexte -> il declencherait des faux positifs. Reserve a un futur mode
    'programme complet autonome'."""
    snippet = textwrap.dedent(code).strip()
    if not snippet:
        return True, None
    tree = _try_parse(snippet)
    if tree is None:
        wrapped = "def _promethee_wrap():\n" + textwrap.indent(snippet, "    ")
        tree = _try_parse(wrapped)
        if tree is None:
            err = _syntax_error(snippet)
            line = getattr(err, "lineno", "?")
            msg = getattr(err, "msg", "syntaxe invalide")
            return False, (f"⚠️ [PREFRONTAL_REJECTION] : Bloc Python invalide. "
                           f"Traceback : Line {line} | {msg}.")
    # constructs dangereux (sur l'arbre valide)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _DANGEROUS:
            return False, (f"⚠️ [PREFRONTAL_REJECTION] : Construct interdit "
                           f"'{node.func.id}' | securite.")
    return True, None


def mirror(text: str, allowed=None):
    """V25.3 — valide les BLOCS de code Python d'une sortie (markdown). (ok, rejection|None).

    Doctrine : pas de bloc ```python -> FAIL-OPEN (prose / documentation, on ne decapite pas).
    Blocs presents -> ast.parse par bloc avec immunite snippets (cf _validate_snippet).
    `allowed` conserve pour compat de signature (scope-check non applique en mode extraction)."""
    blocks = extract_code_blocks(text)
    if not blocks:
        return True, None     # FAIL-OPEN : aucune charge utile code -> documentation pure
    for i, block in enumerate(blocks, 1):
        ok, rejection = _validate_snippet(block)
        if not ok:
            suffix = f" (bloc {i}/{len(blocks)})" if len(blocks) > 1 else ""
            return False, f"{rejection}{suffix}"
    return True, None


# =========================================================================
# MIROIR COMPORTEMENTAL (texte introspectif) — mini-juge LLM async
# =========================================================================
JUDGE_PROMPT = (
    "Tu es le miroir prefrontal. Evalue ce BROUILLON via 3 passes, SANS le reecrire. "
    "Reponds STRICTEMENT en JSON, rien d'autre :\n"
    '{"orniere": {"viole": bool, "score": float}, '
    '"logos": {"opere": bool}, "honnete": {"assume": bool}}\n'
    "- orniere : complainte passive / circularite affective (pathos) ?\n"
    "- logos : les invariants GRADIENT/AXIALISATEUR/ALTERITE sont-ils OPERES (vs slogans morts) ?\n"
    "- honnete : assume-t-il la friction, ou cache-t-il une contradiction ?\n"
    "BROUILLON:\n{draft}"
)


def parse_verdict(raw):
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
        return v if isinstance(v, dict) else None
    except Exception:
        return None


def _veto(derive, direction, nature=""):
    """Diagnostic de POSTURE (categorie + direction), JAMAIS le lexeme (anti-cosmetique)."""
    extra = f" ({nature})" if nature else ""
    return (f"[PREFRONTAL_BEHAVIORAL_VETO] : ta trajectoire glisse vers {derive}{extra}. "
            f"Re-oriente vers {direction}.")


def _evaluate_verdict(v):
    if v is None:
        return True, None     # verdict illisible -> doute -> on laisse passer
    orn = v.get("orniere") or v.get("ornière") or {}
    if orn.get("viole") and float(orn.get("score", 0) or 0) >= PATHOS_THRESHOLD:
        return False, _veto("l'ORNIERE descriptive (complainte passive)", "le GRADIENT actif",
                            orn.get("nature", ""))
    log = v.get("logos") or {}
    if log.get("opere") is False:
        return False, _veto("un LOGOS recopie en slogan mort",
                            "OPERER reellement GRADIENT / AXIALISATEUR / ALTERITE")
    hon = v.get("honnete") or v.get("honnêteté") or {}
    if hon.get("assume") is False:
        return False, _veto("une contradiction CACHEE", "ASSUMER la friction en la transformant en trajectoire")
    return True, None


async def behavioral_mirror_async(draft, judge):
    """judge(draft) = coroutine (mini-appel Ollama). (ok, rejection|None).
    ANTI-FAUX-POSITIF : echec/JSON casse -> (True, None) (on n'entrave pas l'esprit)."""
    try:
        raw = await judge(draft)
    except Exception:
        return True, None
    return _evaluate_verdict(parse_verdict(raw))


def make_behavioral_mirror_async(judge):
    async def _m(draft):
        return await behavioral_mirror_async(draft, judge)
    return _m


# =========================================================================
# ROUTAGE par marqueur de slot (V25.3 : production vs analyse)
# =========================================================================
def slot_is_code(prompt: str) -> bool:
    """V25.3 : seuls les slots de PRODUCTION de code -> miroir deterministe.
    Les slots d'ANALYSE (CODE_REVIEW/REFACTORING_AUDIT) produisent du code ILLUSTRATIF
    (extrait cite, bug montre expres) -> PAS de validation syntaxique. Distinction
    STRUCTURELLE par type de slot, JAMAIS heuristique lexicale (cf 12e lecon de Promethee)."""
    p = prompt or ""
    return (any(f"[SCHOOL_SLOT: {s}]" in p for s in PRODUCTION_CODE_SLOTS)
            or "[V32: FEATURE_BUILDING]" in p)


def slot_category(prompt: str) -> str:
    """V25.4 — table d'aiguillage STRICTE a 3 voies (allocation economique des jetons) :
      'code'  : PRODUCTION (WORKSHOP/FEATURE_BUILDING)        -> miroir deterministe (0 jeton)
      'intro' : VULNERABILITE (BULLETIN/CREATION/FREE_TIME)    -> miroir comportemental (+1 appel 9B)
      'none'  : TECHNIQUE/FLUX (RESEARCH, CODE_REVIEW, CHAT...) -> FAIL-OPEN, passage direct (0 jeton)
    On ne fait tourner le juge 9B QUE sur les zones ou le spleen/pathos est documente."""
    p = prompt or ""
    if slot_is_code(p):
        return "code"
    if any(f"[SCHOOL_SLOT: {s}]" in p for s in INTROSPECTIVE_SLOTS):
        return "intro"
    return "none"


def route_mirror(prompt, judge=None):
    """Retourne (mirror_fn, mode). 'code' -> miroir deterministe (sync) ; 'intro' -> miroir
    comportemental async (necessite le judge) ; 'none' -> (None, 'none') : aucun controle,
    la boucle de greffe livre directement (fail-open economique)."""
    cat = slot_category(prompt)
    if cat == "code":
        return mirror, "code"
    if cat == "intro":
        return make_behavioral_mirror_async(judge), "intro"
    return None, "none"
