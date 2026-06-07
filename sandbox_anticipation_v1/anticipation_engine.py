# -*- coding: utf-8 -*-
"""Chantier B — V24.0_PREFRONTAL : moteur d'anticipation FRUGALE (operationnel).

Sandbox ISOLE. Predictive coding applique a l'action : l'agent ebauche, un MIROIR
DETERMINISTE (ast.parse + micro-lint) evalue AVANT toute livraison. En regime nominal
(code valide), cout additionnel = 0 jeton (juste un ast.parse gratuit). L'energie
semantique n'est mobilisee qu'en cas de SURPRISE (exception interceptee) -> l'ebauche
defectueuse meurt en stase dans le tampon prefrontal, l'interface n'est jamais polluee.

Garde-fous (V24.0) :
  1. Disjoncteur : MAX_ANTICIPATION_RETRIES tentatives au total (l'ebauche + reorientations).
     Si toutes echouent a parser -> canal coupe, echec consigne, VETO de securite.
  2. Error Ingestion : le feedback reinjecte est la friction BRUTE de l'interprete
     (format compilateur standardise), pas une explication philosophique.
"""
import ast
import builtins

MAX_ANTICIPATION_RETRIES = 2   # tentatives totales ; veto si la 2e (corrigee) echoue aussi
_DANGEROUS = {"eval", "exec", "__import__", "compile"}   # micro-lint : constructs interdits
_BUILTINS = set(dir(builtins))                            # liste blanche exhaustive (len/range/print...)


def _collect_bindings(tree):
    """V24.1_SCOPE — table des symboles LEGITIMES (scope plat, permissif).
    Toutes les formes de liaison : defs, affectations (Name Store), arguments,
    imports, for/with/comprehension targets, walrus, except as, global/nonlocal."""
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)        # Assign, For, comprehension, with-as, walrus targets
        elif isinstance(node, ast.arg):
            bound.add(node.arg)       # args de fonction/lambda
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
    """Intersection de friction : un Name(Load) absent de TOUTE liaison legitime
    -> NameError quasi-certain. On n'inspecte QUE les Name(Load) (jamais les
    Attribute -> pas de faux positif sur .append/.pi)."""
    legit = _collect_bindings(tree) | _BUILTINS | set(allowed or ())
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in legit:
                return (f"⚠️ [PREFRONTAL_SCOPE_REJECTION] : Symbole non defini detecte "
                        f"a la ligne {node.lineno} : '{node.id}' n'existe pas dans le contexte d'execution.")
    return None


def mirror(code: str, allowed=None):
    """Miroir deterministe a 3 passes. Retourne (ok: bool, rejection: str|None).
    `allowed` = globales/imports pre-injectes du slot (ex: math, json, ast)."""
    # 1. Syntaxe (ast.parse) — l'arc reflexe
    try:
        tree = ast.parse(code)
    except SyntaxError as e:   # couvre IndentationError aussi
        return False, (f"⚠️ [PREFRONTAL_REJECTION] : Ebauche invalide. "
                       f"Traceback Python : Line {e.lineno} | {e.msg}.")
    # 2. Micro-lint : constructs dangereux
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _DANGEROUS:
            return False, (f"⚠️ [PREFRONTAL_REJECTION] : Construct interdit "
                           f"'{node.func.id}' Line {getattr(node, 'lineno', '?')} | securite.")
    # 3. Scope (V24.1) : symboles fantomes -> NameError anticipe a 0 jeton
    scope_rejection = _scope_check(tree, allowed)
    if scope_rejection:
        return False, scope_rejection
    return True, None


def anticipate(generator, max_retries: int = MAX_ANTICIPATION_RETRIES, on_veto=None, mirror_fn=None):
    """Boucle prefrontale : ebauche -> miroir -> reorientation OU veto.

    generator(attempt: int, last_rejection: str|None) -> ebauche (str)
        Dans le runtime reel, c'est l'appel LLM ; ici une dependance injectable.
        A la reorientation, last_rejection porte la friction a ingerer (Error Ingestion).
    mirror_fn(ebauche) -> (ok: bool, rejection: str|None)
        Defaut = `mirror` (miroir DETERMINISTE, pour le code). Pour le slot introspectif,
        on injecte le miroir COMPORTEMENTAL (cf behavioral_mirror.py).

    Retourne un dict :
        {status: "delivered", code, attempts, rejections}        -> livre, purifie
        {status: "veto", code: None, attempts, rejections, reason} -> canal coupe
    """
    mirror_fn = mirror_fn or mirror
    last_rejection = None
    rejections = []
    for attempt in range(1, max_retries + 1):
        code = generator(attempt, last_rejection)
        ok, rejection = mirror_fn(code)
        if ok:
            return {"status": "delivered", "code": code,
                    "attempts": attempt, "rejections": rejections}
        rejections.append(rejection)
        last_rejection = rejection      # Error Ingestion : reinjecte a la tentative suivante

    # Toutes les tentatives ont echoue -> VETO de securite (pas de boucle infinie)
    veto = {"status": "veto", "code": None, "attempts": max_retries,
            "rejections": rejections,
            "reason": "PREFRONTAL_VETO : echec operationnel dur apres reorientation(s), canal coupe."}
    if on_veto:
        on_veto(veto)
    return veto
