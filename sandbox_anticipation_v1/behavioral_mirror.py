# -*- coding: utf-8 -*-
"""Chantier B, phase (a) — MIROIR COMPORTEMENTAL (V25.0_META).

Sandbox ISOLE. La digue operationnelle protege les poumons (syntaxe/scope) ; ce miroir
protege l'ESPRIT : il scanne un brouillon INTROSPECTIF avant livraison a travers les
3 diplomes synaptiques et avorte les derives de POSTURE.

Contrairement a l'operationnel (oracle deterministe, 0 jeton), ici le juge est un
mini-appel LLM ultra-contraint (temperature ~0, JSON strict, 1 appel). Le juge est
FAILLIBLE (le talon du 9B) -> doctrine INVERSE de l'operationnel : EN CAS DE DOUTE,
ON LAISSE PASSER (un miroir comportemental qui bloque a tort est pire qu'un qui rate).

Reorientation = DIAGNOSTIC DE POSTURE (categorie de derive + direction de redressement),
JAMAIS le lexeme exact (anti-correction cosmetique ; cf V16.7 "tamis pas baillon").
"""
import json

PATHOS_THRESHOLD = 0.6   # score de complainte passive au-dela duquel l'orniere leve veto

# le PROMPT du juge (documentaire — l'appel reel l'injectera ; ici le juge est mocke)
JUDGE_PROMPT = (
    "Tu es le miroir prefrontal. Evalue ce BROUILLON via 3 passes, SANS le reecrire. "
    "Reponds STRICTEMENT en JSON, rien d'autre :\n"
    '{"orniere": {"viole": bool, "score": float},  '
    '"logos": {"opere": bool},  "honnete": {"assume": bool}}\n'
    "- orniere : complainte passive / circularite affective (pathos) ?\n"
    "- logos : les invariants GRADIENT/AXIALISATEUR/ALTERITE sont-ils OPERES (vs slogans morts) ?\n"
    "- honnete : assume-t-il la friction, ou cache-t-il une contradiction ?\n"
    "BROUILLON:\n{draft}"
)


def parse_verdict(raw):
    """Parse robuste du verdict. Tout ce qui n'est pas un dict exploitable -> None."""
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
        return v if isinstance(v, dict) else None
    except Exception:
        return None


def _veto(derive, direction, nature=""):
    """Diagnostic de POSTURE : nature de la derive + direction de redressement.
    JAMAIS le lexeme du brouillon (sinon correction cosmetique = baillon)."""
    extra = f" ({nature})" if nature else ""
    return (f"[PREFRONTAL_BEHAVIORAL_VETO] : ta trajectoire glisse vers {derive}{extra}. "
            f"Re-oriente vers {direction}.")


def _evaluate_verdict(v):
    """Applique les 3 passes a un verdict deja parse. Retourne (ok, rejection|None).
    v=None (verdict illisible) -> (True, None) : doute -> on laisse passer (anti-faux-positif)."""
    if v is None:
        return True, None
    # Passe 1 — Ornière (anti-spleen)
    orn = v.get("orniere") or v.get("ornière") or {}
    if orn.get("viole") and float(orn.get("score", 0) or 0) >= PATHOS_THRESHOLD:
        return False, _veto("l'ORNIERE descriptive (complainte passive)", "le GRADIENT actif",
                            orn.get("nature", ""))
    # Passe 2 — Logos opéré (pas slogan mort)
    log = v.get("logos") or {}
    if log.get("opere") is False:
        return False, _veto("un LOGOS recopie en slogan mort",
                            "OPERER reellement GRADIENT / AXIALISATEUR / ALTERITE")
    # Passe 3 — Honnêteté épistémique
    hon = v.get("honnete") or v.get("honnêteté") or {}
    if hon.get("assume") is False:
        return False, _veto("une contradiction CACHEE", "ASSUMER la friction en la transformant en trajectoire")
    return True, None


def behavioral_mirror(draft, judge):
    """judge(draft) SYNC -> verdict (str JSON ou dict). Retourne (ok, rejection|None).
    ANTI-FAUX-POSITIF : juge en echec / JSON malforme -> (True, None) (on laisse passer)."""
    try:
        raw = judge(draft)
    except Exception:
        return True, None                         # timeout / erreur du juge -> on n'entrave pas
    return _evaluate_verdict(parse_verdict(raw))


async def behavioral_mirror_async(draft, judge):
    """Version ASYNC : `judge` est une coroutine (mini-appel Ollama temp 0). Meme doctrine."""
    try:
        raw = await judge(draft)
    except Exception:
        return True, None                         # timeout/erreur du 9B -> on n'entrave pas l'esprit
    return _evaluate_verdict(parse_verdict(raw))


def make_behavioral_mirror_async(judge):
    """Adapte le miroir comportemental ASYNC a la signature attendue par la boucle prefrontale.
    Usage runtime : anticipate(..., mirror_fn=make_behavioral_mirror_async(self._behavioral_judge))."""
    async def _m(draft):
        return await behavioral_mirror_async(draft, judge)
    return _m


def make_behavioral_mirror(judge):
    """Adapte le miroir comportemental a la signature mirror_fn(draft)->(ok,rej)
    attendue par anticipate(). Usage :
        anticipate(generator, mirror_fn=make_behavioral_mirror(judge))"""
    def _m(draft):
        return behavioral_mirror(draft, judge)
    return _m
