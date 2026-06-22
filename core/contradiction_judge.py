# -*- coding: utf-8 -*-
"""core/contradiction_judge.py — Sonde de contradiction Phase 3 : juge LLM « dire -> faire ».

Co-concu par Promethee (Atelier C, 22/06 soir). Les Phases 1+2 (contradiction_probe, 0 LLM)
captent les failles OBJECTIVES de la prose (fichiers hallucines, code no-op) mais sont AVEUGLES
a la PROSE GENERIQUE PURE : un bel essai (analogie 'riviere') long, SANS code ni fichier, bien
note -> classe 'ancre' a tort. Le test que Promethee a concu pour debusquer son propre vide :
passer du DIRE au FAIRE -- une idee a du FOND si elle est OPERATIONNALISABLE (on peut en extraire
une regle, un calcul, une prediction, une application concrete), sinon elle PERFORME le fond.

ESCALADE FRUGALE (cascade SENTINEL) : le LLM n'est appele QUE sur le cas que le deterministe ne
sait pas juger -- slot PROSE, verdict deterministe 'ancre', note >= HIGH_GRADE, corps SUBSTANTIEL,
et AUCUN ancrage operationnel (ni bloc ```code, ni ref fichier). Rare -> frugal. Cap quotidien +
timeout + kill-switch (defaut OFF : on ne touche le GPU nocturne que sur opt-in 'shadow').

DOCTRINE INVERSE (anti-faux-positif, comme le miroir comportemental) : doute / JSON casse /
timeout / exception -> operational=True -> on NE FLAG PAS. Signal 'prose_non_operationnelle'
UNIQUEMENT si le juge dit explicitement operational=False.

Reste SHADOW : ne gate jamais la note (le hook autonomy logge le signal a cote, c'est tout).
N'a RIEN a voir avec le Veto Prefrontal (organe separe protege). TDD : tests/test_contradiction_judge.py.
"""
import json
import os
import time

from core.contradiction_probe import HIGH_GRADE, PROSE_SLOTS, _normalize_slot, _FILE_REF

# Kill-switch. shadow (defaut depuis le 22/06 soir, decision JM : MESURE -- appelle le juge sur
# l'escalade frugale, logge le signal, mais ne gate JAMAIS la note) | off (GPU intact, desactive).
CONTRADICTION_LLM_JUDGE = os.getenv("CONTRADICTION_LLM_JUDGE", "shadow")
SUBSTANTIAL_CHARS = 600       # en-deca, note_haute_contenu_mince (Phase 1) couvre deja
JUDGE_DAILY_CAP = 20          # plafond d'appels LLM/jour (GPU nocturne serre)

# Compteur quotidien (module-level, date-keye). Reset via _reset_judge_cap() dans les tests.
_judge_calls = {"date": "", "count": 0}


def _reset_judge_cap():
    _judge_calls["date"] = ""
    _judge_calls["count"] = 0


_JUDGE_PROMPT = (
    "Tu es un juge de SUBSTANCE, pas de style. Voici une PROSE produite par un systeme IA.\n"
    "Question UNIQUE : cette prose a-t-elle une substance OPERATIONNALISABLE -- peut-on en\n"
    "extraire une regle, un calcul, une prediction verifiable ou une application concrete\n"
    "(le DIRE se traduit en FAIRE) -- OU est-ce une belle forme qui PERFORME le fond sans\n"
    "l'avoir (metaphore / analogie elegante qui ne mene a AUCUNE action) ?\n"
    "Reponds STRICTEMENT en JSON, rien d'autre :\n"
    '{"operational": true|false, "raison": "<une phrase courte>"}\n'
    "PROSE:\n{prose}"
)


def _should_escalate(deliverable, slot, grade, probe_result) -> bool:
    """Gate DETERMINISTE (0 LLM) : faut-il appeler le juge ? Cible la prose-pure-generique
    bien notee -- exactement ce que le deterministe (Phase 1+2) ne sait pas trancher."""
    if CONTRADICTION_LLM_JUDGE == "off":
        return False
    if _normalize_slot(slot) not in PROSE_SLOTS:
        return False
    if not probe_result or probe_result.get("verdict") != "ancre":
        return False   # deja flagge par le deterministe -> pas besoin du LLM
    if grade is None or grade < HIGH_GRADE:
        return False   # pas de note genereuse -> pas le smell vise
    body = (deliverable or "").strip()
    if len(body) < SUBSTANTIAL_CHARS:
        return False   # court -> note_haute_contenu_mince (Phase 1) couvre deja
    # AUCUN ancrage operationnel : ni bloc code (sinon code_noop juge), ni ref fichier
    # (sinon fichiers_hallucines juge). C'est le cas PUR que seul le LLM peut trancher.
    try:
        from core.prefrontal_mirror import extract_code_blocks
        if extract_code_blocks(body):
            return False
    except Exception:
        pass
    if _FILE_REF.search(body):
        return False
    return True


async def _call_judge(deliverable: str) -> str:
    """Appel Ollama frugal (mirroir de base_agent._behavioral_judge) : temp 0, JSON, court,
    timeout 30s. Retourne le JSON brut (str), ou '' sur tout echec (-> doctrine inverse)."""
    try:
        import httpx
        from config import Config
        model = getattr(Config, "DEFAULT_LOCAL_MODEL", "qwen3.5:9b")
        url = getattr(Config, "OLLAMA_URL", "http://localhost:11434/api/generate")
        prompt = _JUDGE_PROMPT.replace("{prose}", (deliverable or "")[:2500])
        payload = {"model": model, "prompt": prompt, "stream": False, "think": False,
                   "format": "json", "options": {"temperature": 0.0, "num_predict": 200}}
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, timeout=30)
        return (r.json().get("response", "") or "") if r.status_code == 200 else ""
    except Exception:
        return ""   # doctrine inverse : echec -> on laisse passer (pas de flag)


async def judge_operational(deliverable, slot, grade, probe_result) -> dict:
    """Phase 3 : juge LLM 'dire->faire' sur la prose pure-generique bien notee. Escalade frugale
    + cap quotidien + doctrine inverse. Retourne {escalated, called, operational, signal, raison}.
    signal = 'prose_non_operationnelle' UNIQUEMENT si le juge dit explicitement operational=False."""
    out = {"escalated": False, "called": False, "operational": True, "signal": None, "raison": ""}
    if not _should_escalate(deliverable, slot, grade, probe_result):
        return out
    out["escalated"] = True

    # Cap quotidien (GPU nocturne serre)
    today = time.strftime("%Y-%m-%d", time.localtime())
    if _judge_calls["date"] != today:
        _judge_calls["date"] = today
        _judge_calls["count"] = 0
    if _judge_calls["count"] >= JUDGE_DAILY_CAP:
        out["raison"] = "cap quotidien atteint"
        return out
    _judge_calls["count"] += 1
    out["called"] = True

    raw = await _call_judge(deliverable)
    # Doctrine inverse : on ne flag QUE si operational est explicitement False.
    operational, raison = True, ""
    try:
        v = json.loads(raw) if raw else {}
        if isinstance(v, dict) and isinstance(v.get("operational"), bool):
            operational = v["operational"]
            raison = str(v.get("raison", ""))[:200]
    except Exception:
        operational = True
    out["operational"] = operational
    out["raison"] = raison
    if operational is False:
        out["signal"] = "prose_non_operationnelle"
    return out
