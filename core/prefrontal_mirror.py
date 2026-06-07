# -*- coding: utf-8 -*-
"""core/prefrontal_mirror.py — Cortex prefrontal : anticipation frugale (V25.1).

Avorte les ebauches defectueuses AVANT livraison, dans generate_content :
  - MIROIR DETERMINISTE (slots code) : ast.parse + micro-lint + scope, 0 jeton.
    Echec persistant -> AVORTEMENT SEC (un code non compilable ne se livre jamais).
  - MIROIR COMPORTEMENTAL (slots introspectifs) : mini-juge LLM async (temp 0, JSON
    strict). Doctrine INVERSE : doute / JSON casse / timeout -> ON LAISSE PASSER.
    Reorientation = DIAGNOSTIC DE POSTURE (categorie + direction, jamais le lexeme).
    Echec persistant -> MODE DEGRADE (livrer + balise [METABOLISME_ALERT]).

Conception + 48 TDD : sandbox_anticipation_v1/. Ce module est la version de production
importee par core/base_agent.py.
"""
import ast
import builtins
import json

MAX_PREFRONTAL_RETRIES = 2
PATHOS_THRESHOLD = 0.6
_DANGEROUS = {"eval", "exec", "__import__", "compile"}
_BUILTINS = set(dir(builtins))
# slots qui produisent du CODE -> miroir deterministe ; le reste = introspectif -> comportemental
CODE_SLOTS = ("CODE_REVIEW", "WORKSHOP", "REFACTORING_AUDIT", "FEATURE_BUILDING")


# =========================================================================
# MIROIR DETERMINISTE (code) — 0 jeton
# =========================================================================
def _collect_bindings(tree):
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.Import):
            for a in node.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                bound.add(a.asname or a.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
    return bound


def _scope_check(tree, allowed=None):
    legit = _collect_bindings(tree) | _BUILTINS | set(allowed or ())
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in legit:
                return (f"⚠️ [PREFRONTAL_SCOPE_REJECTION] : Symbole non defini detecte "
                        f"a la ligne {node.lineno} : '{node.id}' n'existe pas dans le contexte d'execution.")
    return None


def mirror(code: str, allowed=None):
    """(ok, rejection|None). 3 passes deterministes : syntaxe, constructs interdits, scope."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, (f"⚠️ [PREFRONTAL_REJECTION] : Ebauche invalide. "
                       f"Traceback Python : Line {e.lineno} | {e.msg}.")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _DANGEROUS:
            return False, (f"⚠️ [PREFRONTAL_REJECTION] : Construct interdit "
                           f"'{node.func.id}' Line {getattr(node, 'lineno', '?')} | securite.")
    scope_rejection = _scope_check(tree, allowed)
    if scope_rejection:
        return False, scope_rejection
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
# ROUTAGE par marqueur de slot
# =========================================================================
def slot_is_code(prompt: str) -> bool:
    p = prompt or ""
    return (any(f"[SCHOOL_SLOT: {s}]" in p for s in CODE_SLOTS)
            or "[V32: FEATURE_BUILDING]" in p)


def route_mirror(prompt, judge=None):
    """Retourne (mirror_fn, mode). code -> miroir deterministe (sync) ;
    intro -> miroir comportemental async (necessite le judge)."""
    if slot_is_code(prompt):
        return mirror, "code"
    return make_behavioral_mirror_async(judge), "intro"
