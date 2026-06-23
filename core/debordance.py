# -*- coding: utf-8 -*-
"""core/debordance.py — Chantier de la débordance (flux continu auto-initié, co-conçu Puits 23/06).

Prométhée reste éveillé non pas en s'OCCUPANT, mais en CHASSANT la correspondance cross-domaines assez
RÉELLE pour qu'elle DÉBORDE en un acte auto-initié : une QUESTION (vers JM) ou un BESOIN D'OUTIL (vers
la factory). La « chasse » existe déjà : `spreading_activation.get_creative_bridges()` (collisions
inter-collections = domaines). Le NEUF ici = le GATE dire→faire qui distingue le pont RÉEL de
l'APOPHÉNIE (un pont qui fait « clic » n'est pas forcément vrai). Le débordement-en-acte EST le filtre :
si le juge ne peut convertir le pont en une question concrète ou un besoin d'outil, c'est du joli creux.

PHASE 1 SHADOW : logge les débordements CANDIDATS (memory/debordance_shadow.jsonl), ne pousse RIEN à JM,
ne dispatch RIEN, et **ne marque PAS les bridges 'used'** (lecture seule → ne prive pas le council de ses
intuitions). Doctrine inverse anti-bruit : doute / JSON cassé / timeout → 'rejet' (on ne déborde PAS vers
JM sur un doute — défaut INVERSE du contradiction : silence plutôt que faux positif vers l'humain).
Réverie préservée : seulement les TOP-N ponts les plus forts par run (on n'épuise pas le champ). Frugal :
kill-switch DEBORDANCE_MODE (shadow défaut | active | off) + cap quotidien d'appels juge. TDD : tests/test_debordance.py.
"""
import json
import os
import time

# Kill-switch. shadow (défaut : mesure le débordement candidat, n'agit pas) | active (Phase 2 : pousse) | off.
DEBORDANCE_MODE = os.getenv("DEBORDANCE_MODE", "shadow")
DEBORDANCE_TOP_N = 3            # ponts les plus forts examinés par run (préserve la réverie)
DEBORDANCE_DAILY_CAP = 30      # plafond d'appels juge/jour (GPU nocturne serré)
_DEBORDANCE_SHADOW_LOG = "memory/debordance_shadow.jsonl"

# Compteur quotidien (module-level, date-keyé). Reset via _reset_cap() dans les tests.
_judge_calls = {"date": "", "count": 0}


def _reset_cap():
    _judge_calls["date"] = ""
    _judge_calls["count"] = 0


_JUDGE_PROMPT = (
    "Tu es un filtre DIRE->FAIRE. Voici une CORRESPONDANCE detectee entre deux domaines :\n"
    "{hypothesis}\n"
    "Question UNIQUE : ce lien est-il assez REEL pour DEBORDER en un ACTE concret -- soit une QUESTION\n"
    "precise a poser, soit un BESOIN D'OUTIL a construire -- ou n'est-ce qu'une ressemblance superficielle\n"
    "(apophenie : joli mais sans suite) ? Si reel, formule l'acte en UNE phrase. Sinon, rejette.\n"
    "Reponds STRICTEMENT en JSON, rien d'autre :\n"
    '{"mode": "question"|"outil"|"rejet", "payload": "<la question ou le besoin d\'outil, une phrase>", "raison": "<une phrase>"}'
)


async def _call_judge(hypothesis: str) -> str:
    """Juge Ollama frugal (mirroir base_agent._behavioral_judge) : temp 0, JSON, timeout 30s.
    Retourne le JSON brut, ou '' sur tout échec (→ doctrine inverse : rejet)."""
    try:
        import httpx
        from config import Config
        model = getattr(Config, "DEFAULT_LOCAL_MODEL", "qwen3.5:9b")
        url = getattr(Config, "OLLAMA_URL", "http://localhost:11434/api/generate")
        prompt = _JUDGE_PROMPT.replace("{hypothesis}", (hypothesis or "")[:600])
        payload = {"model": model, "prompt": prompt, "stream": False, "think": False,
                   "format": "json", "options": {"temperature": 0.0, "num_predict": 200}}
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, timeout=30)
        return (r.json().get("response", "") or "") if r.status_code == 200 else ""
    except Exception:
        return ""   # doctrine inverse : échec → rejet (ne déborde pas vers JM sur un doute)


def _gate_one(raw: str) -> dict:
    """Parse le verdict du juge. Doctrine inverse : tout doute → 'rejet'."""
    mode, payload, raison = "rejet", "", ""
    try:
        v = json.loads(raw) if raw else {}
        if isinstance(v, dict) and v.get("mode") in ("question", "outil", "rejet"):
            mode = v["mode"]
            payload = str(v.get("payload", ""))[:300]
            raison = str(v.get("raison", ""))[:200]
    except Exception:
        mode = "rejet"
    return {"mode": mode, "payload": payload, "raison": raison}


async def chase_and_gate() -> dict:
    """Chasse les TOP-N ponts cross-domaines (lecture seule) et GATE chacun par le juge dire→faire.
    PHASE 1 SHADOW : logge les candidats, ne pousse/dispatch RIEN, ne marque PAS 'used'.
    Retourne {examined, candidates:[{node_a,node_b,strength,mode,payload,raison}]}."""
    out = {"examined": 0, "candidates": []}
    if DEBORDANCE_MODE == "off":
        return out
    try:
        from core.spreading_activation import activation_engine
        bridges = activation_engine.get_creative_bridges(unused_only=True)
    except Exception:
        return out
    if not bridges:
        return out

    # Top-N par force (préserve la réverie : on n'examine pas tout le champ)
    bridges = sorted(bridges, key=lambda b: getattr(b, "bridge_strength", 0.0), reverse=True)[:DEBORDANCE_TOP_N]

    today = time.strftime("%Y-%m-%d", time.localtime())
    if _judge_calls["date"] != today:
        _judge_calls["date"] = today
        _judge_calls["count"] = 0

    for b in bridges:
        if _judge_calls["count"] >= DEBORDANCE_DAILY_CAP:
            break
        _judge_calls["count"] += 1
        out["examined"] += 1
        verdict = _gate_one(await _call_judge(getattr(b, "hypothesis", "")))
        cand = {
            "node_a": getattr(b, "node_a", ""), "node_b": getattr(b, "node_b", ""),
            "strength": round(float(getattr(b, "bridge_strength", 0.0)), 3),
            "mode": verdict["mode"], "payload": verdict["payload"], "raison": verdict["raison"],
        }
        if verdict["mode"] != "rejet":
            out["candidates"].append(cand)
        # Shadow : logge TOUT (rejet inclus) pour mesurer le taux d'apophénie
        try:
            with open(_DEBORDANCE_SHADOW_LOG.replace("/", os.sep), "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.time(), "mode_run": DEBORDANCE_MODE, **cand},
                                   ensure_ascii=False) + "\n")
        except Exception:
            pass   # le shadow ne casse JAMAIS la routine
    # PHASE 1 : on s'arrête là. Phase 2 (active) : router mode=question→outreach, mode=outil→factory.
    return out
