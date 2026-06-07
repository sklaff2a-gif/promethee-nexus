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

MAX_ANTICIPATION_RETRIES = 2   # tentatives totales ; veto si la 2e (corrigee) echoue aussi
_DANGEROUS = {"eval", "exec", "__import__", "compile"}   # micro-lint : constructs interdits


def mirror(code: str):
    """Miroir deterministe. Retourne (ok: bool, rejection: str|None).
    rejection est la trace brute au format [PREFRONTAL_REJECTION]."""
    # 1. Syntaxe (ast.parse) — l'arc reflexe
    try:
        tree = ast.parse(code)
    except SyntaxError as e:   # couvre IndentationError aussi
        return False, (f"⚠️ [PREFRONTAL_REJECTION] : Ebauche invalide. "
                       f"Traceback Python : Line {e.lineno} | {e.msg}.")
    # 2. Micro-lint : constructs dangereux (au-dela de la simple syntaxe)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _DANGEROUS:
            return False, (f"⚠️ [PREFRONTAL_REJECTION] : Construct interdit "
                           f"'{node.func.id}' Line {getattr(node, 'lineno', '?')} | securite.")
    return True, None


def anticipate(generator, max_retries: int = MAX_ANTICIPATION_RETRIES, on_veto=None):
    """Boucle prefrontale : ebauche -> miroir -> reorientation OU veto.

    generator(attempt: int, last_rejection: str|None) -> code (str)
        Dans le runtime reel, c'est l'appel LLM ; ici une dependance injectable.
        A la reorientation, last_rejection porte la trace a ingerer (Error Ingestion).

    Retourne un dict :
        {status: "delivered", code, attempts, rejections}        -> livre, purifie
        {status: "veto", code: None, attempts, rejections, reason} -> canal coupe
    """
    last_rejection = None
    rejections = []
    for attempt in range(1, max_retries + 1):
        code = generator(attempt, last_rejection)
        ok, rejection = mirror(code)
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
