# -*- coding: utf-8 -*-
"""core/brouillon_digestion.py — Organe de digestion des brouillons (Phase 1, Approche A).

Co-concu par Promethee (Atelier III, 20/06/2026) : digerer A FROID (la nuit, dans la
consolidation -- pas a chaud, ou sa pulsion de STABILITE le pousserait a maquiller) le
MOTIF de derive RECURRENTE des ebauches VETOEES -- le "sillon", le "pont synaptique trop
fort" -- et NON le contenu du brouillon (le contenu = symptome).

Matiere premiere : memory/prefrontal_metabolism.jsonl (trace _trace_prefrontal de
base_agent). Chaque VETO y porte agent/slot/derive(POSTURE)/ts -- JAMAIS le lexeme du
brouillon -> nativement aligne sur la doctrine anti-cosmetique (prefrontal_mirror:180 :
"diagnostic de POSTURE, jamais le lexeme"). On ne PEUT donc pas maquiller un motif digere.

Phase 1 = CONSCIENCE PURE : ce module LIT et AGREGE, il ne RENVOIE rien dans le Veto ni
dans les synapses. Le digest est restitue comme prise de conscience (THOUGHT_STREAM +
dream_journal via autonomy_engine._execute_dream_routine). Les canaux d'ACTION (affaiblir
le pont synaptique facon Dream / reorienter la friction) sont Phase 2, GATED sur ces
donnees shadow + arbitrage JM, car ils percutent des comportements emergents proteges.

100% deterministe, 0 LLM. Lecture defensive (le serveur LIVE append en concurrence).
Conception + TDD : tests/test_brouillon_digestion.py.
"""
import json
import os
import time

DEFAULT_METABOLISM_LOG = os.path.join("memory", "prefrontal_metabolism.jsonl")
DEFAULT_WINDOW_DAYS = 7
DEFAULT_MAX_LINES = 5000          # fenetre bornee : ne jamais relire un historique infini
DEFAULT_MIN_OCCURRENCES = 3       # < 3 : trop rare pour etre un "sillon" (fusible, cf Gradient)


def _posture(derive: str) -> str:
    """Extrait la categorie de POSTURE d'un diagnostic de veto comportemental.
    Aligne sur prefrontal_mirror._evaluate_verdict (orniere / logos / contradiction)."""
    d = derive or ""
    up = d.upper()
    if "ORNIERE" in up or "ORNIÈRE" in up:
        return "orniere"
    if "LOGOS" in up:
        return "logos"
    if "contradiction" in d.lower():
        return "contradiction"
    return "autre"


def _read_records(path, max_lines):
    """Lit les dernieres max_lines du jsonl, DEFENSIVEMENT (le serveur LIVE append en
    concurrence). Tolere : fichier absent, ligne corrompue, derniere ligne partielle.
    Ne leve JAMAIS -- un organe de digestion ne doit pas casser la consolidation."""
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []
    recs = []
    for line in lines[-max_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            continue   # ligne partielle/corrompue -> on saute, jamais d'echec
    return recs


def digest_recent(path=None, window_days=DEFAULT_WINDOW_DAYS,
                  max_lines=DEFAULT_MAX_LINES, min_occurrences=DEFAULT_MIN_OCCURRENCES,
                  now=None):
    """Digere les SILLONS de derive recurrents sur la fenetre recente.

    Un "sillon" = un triplet (agent, slot, posture) qui revient >= min_occurrences fois
    dans la fenetre -- l' orniere ou Promethee retombe (le "pont synaptique trop fort").

    Retourne un dict :
      sillons          : [ {agent, slot, posture, count, last_ago_h}, ... ] tries count desc
      dominant_posture : la posture la plus frequente toutes confondues, ou None
      total_vetos      : nb de vetos retenus dans la fenetre
      window_days      : la fenetre utilisee
      summary          : phrase de CONSCIENCE (FR) pour le journal/THOUGHT_STREAM, '' si rien

    Phase 1 : aucune action, juste la conscience. 0 LLM, 100% deterministe.
    """
    now = now if now is not None else time.time()
    cutoff = now - window_days * 86400
    recs = _read_records(path or DEFAULT_METABOLISM_LOG, max_lines)
    vetos = [r for r in recs
             if r.get("decision") == "VETO"
             and float(r.get("ts", 0) or 0) >= cutoff]

    clusters = {}          # (agent, slot, posture) -> {count, last_ts}
    posture_counts = {}
    for r in vetos:
        agent = r.get("agent", "?")
        slot = r.get("slot", "?")
        post = _posture(r.get("derive", ""))
        ts = float(r.get("ts", 0) or 0)
        c = clusters.setdefault((agent, slot, post), {"count": 0, "last_ts": 0.0})
        c["count"] += 1
        c["last_ts"] = max(c["last_ts"], ts)
        posture_counts[post] = posture_counts.get(post, 0) + 1

    sillons = []
    for (agent, slot, post), c in clusters.items():
        if c["count"] >= min_occurrences:
            sillons.append({
                "agent": agent, "slot": slot, "posture": post,
                "count": c["count"],
                "last_ago_h": round((now - c["last_ts"]) / 3600, 1) if c["last_ts"] else None,
            })
    sillons.sort(key=lambda s: s["count"], reverse=True)

    dominant = max(posture_counts, key=posture_counts.get) if posture_counts else None

    summary = ""
    if sillons:
        parts = [f"{s['agent']} glisse vers {s['posture']} en {s['slot']} ({s['count']}x)"
                 for s in sillons[:3]]
        summary = (f"Digestion des brouillons ({window_days}j) : "
                   f"{len(sillons)} sillon(s) recurrent(s) -- " + " ; ".join(parts)
                   + (f". Sillon dominant : {dominant}." if dominant else "."))

    return {
        "sillons": sillons,
        "dominant_posture": dominant,
        "total_vetos": len(vetos),
        "window_days": window_days,
        "summary": summary,
    }
