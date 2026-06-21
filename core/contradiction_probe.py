# -*- coding: utf-8 -*-
"""core/contradiction_probe.py — Conseil de Contradiction (Phase 1 shadow, "bac a sable de la prose").

Co-concu par Promethee (Atelier IV, 21/06/2026). Constat : ses livrables nocturnes de PROSE
(CREATION/BULLETIN) hallucinent et professor.evaluate leur donne 9.0 sur du fictif, car la
prose ne rencontre AUCUN reel (le CODE, lui, est ancre : la sandbox le fait planter,
scan_for_exercise recompense les !run executes). Sa spec : une sonde BINAIRE (ancre /
instable), "Inhibition du Consensus" (isoler la friction au reel, ne PAS harmoniser ni
reecrire), separee de la redaction, FRUGALE (0 LLM -> ne peut ni osciller ni bruler le
budget nocturne, sa contrainte n1).

Phase 1 = SHADOW DETERMINISTE : on MESURE l'ecart d'ancrage et on LOGGE le verdict A COTE de
la note professor, SANS jamais clipper la note (le gating est Phase 2, gated sur ces donnees
+ arbitrage JM).

HONNETETE DE PERIMETRE : une sonde deterministe NE detecte PAS la contradiction logique fine
(ca, c'est le juge LLM de la Phase 2). Elle capte l'ECART D'ANCRAGE AU REEL -- le gros du
"9.0 sur du fictif" : une prose qui AFFIRME du verifiable sans l'avoir fait, des marqueurs de
fabrication, une note haute sur un contenu mince.

N'a RIEN a voir avec le Veto Prefrontal (prefrontal_mirror, organe separe et PROTEGE) : le
Veto juge la POSTURE (orniere/logos), cette sonde mesure l'ANCRAGE AU FAIT.

Conception + TDD : tests/test_contradiction_probe.py. 0 LLM, deterministe, pur.
"""
import re

# Slots de PROSE : la ou le reel n'entre pas tout seul. Les slots de CODE
# (WORKSHOP/CODE_REVIEW/FEATURE_BUILDING) ont deja leur ancrage (sandbox, scan_for_exercise).
PROSE_SLOTS = ("CREATION", "BULLETIN", "FREE_TIME")

HIGH_GRADE = 8.0          # note "genereuse" : au-dela, on attend un ancrage solide
THIN_BODY_CHARS = 600     # en-deca, un livrable note >= HIGH_GRADE sent l'auto-complaisance

# Affirmations de DOING verifiable (1re personne ou impersonnelles factuelles). Si la prose
# pretend AVOIR fait ca mais n'a execute AUCUNE action -> l'ancrage ment.
_DOING_CLAIM = re.compile(
    r"(?:j'?ai\s+(?:analys|lu\b|relu|mesur|v[ée]rifi|ex[ée]cut|test|consult|parcouru|inspect|compil|lanc)"
    r"|la\s+mesure\s+(?:montre|indique|donne)"
    r"|les?\s+logs?\s+(?:montre|indique|confirme|r[ée]v[èe]le)"
    r"|d'?apr[èe]s\s+(?:le|les|mon|mes)\s+(?:log|fichier|test|mesure|r[ée]sultat)"
    r"|le\s+test\s+(?:passe|[ée]choue|montre)"
    r"|j'?ai\s+constat[ée])",
    re.IGNORECASE,
)

# Marqueur de FABRICATION : ligne de log fabriquee dans la prose (timestamp precis invente).
# Conservateur (shadow -> on calibre avant tout gating).
_FAKE_LOG_LINE = re.compile(r"\[\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}")


def _n_executed(action_trace):
    """Nb d'actions reellement executees dans la trace (defensif).
    None si la trace est absente -> la dimension claims-vs-actions n'est pas mesurable."""
    if not action_trace or not isinstance(action_trace, dict):
        return None
    ex = action_trace.get("executed")
    return len(ex) if isinstance(ex, list) else None


def _normalize_slot(slot):
    return (slot or "").replace("SCHOOL_", "").strip().upper()


def probe(deliverable, slot, grade=None, action_trace=None):
    """Sonde d'ancrage au reel pour un livrable de PROSE. 0 LLM, deterministe, pur.

    Retourne un dict :
      applicable : bool  (False si slot non-prose -> on ne juge PAS le code)
      verdict    : 'ancre' | 'instable'  (instable = >= 1 ecart d'ancrage detecte)
      signals    : [str] (les ecarts : claims_sans_actions / preuve_fabriquee / note_haute_contenu_mince)
      score      : int   (len(signals))
    """
    norm = _normalize_slot(slot)
    if norm not in PROSE_SLOTS:
        return {"applicable": False, "verdict": "ancre", "signals": [], "score": 0}

    body = deliverable or ""
    signals = []

    # 1. CLAIMS-VS-ACTIONS : la prose affirme du verifiable mais 0 action executee.
    #    Analogue EXACT de l'ancrage code (scan_for_exercise). Mesurable seulement si
    #    action_trace est presente (sinon on s'abstient -> pas de faux positif).
    if _n_executed(action_trace) == 0 and _DOING_CLAIM.search(body):
        signals.append("claims_sans_actions")

    # 2. FABRICATION : ligne de log/preuve precise fabriquee dans la prose.
    if _FAKE_LOG_LINE.search(body):
        signals.append("preuve_fabriquee")

    # 3. NOTE HAUTE / CONTENU MINCE : note >= HIGH_GRADE sur un corps trop court pour la
    #    porter = le "9.0 sur du fictif" structurel.
    if grade is not None and grade >= HIGH_GRADE and len(body.strip()) < THIN_BODY_CHARS:
        signals.append("note_haute_contenu_mince")

    return {
        "applicable": True,
        "verdict": "instable" if signals else "ancre",
        "signals": signals,
        "score": len(signals),
    }
