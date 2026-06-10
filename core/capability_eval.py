# -*- coding: utf-8 -*-
"""OPA — L'Œil par Preuve d'Action (atelier harnais P1, 10/06/2026).

Harnais d'évaluation de capacité CO-CONÇU PAR PROMÉTHÉE (il l'a nommé et en a posé
les trois principes) :
  1. ORACLE DUR : « l'oracle ne doit pas être mon raisonnement, mais le résultat brut
     des outils » — la note ne dépend JAMAIS du jugement du modèle qui produit le
     travail (rupture du cercle d'auto-évaluation, cause du q saturé à 1.00).
  2. PROFILS DE RÉFÉRENCE : un référentiel d'épreuves FIXES, rejouées à l'identique
     dans le temps (J+7, J+14...) — comparer Prométhée à lui-même à conditions égales,
     pas des moyennes de tâches disparates.
  3. FALSIFIABILITÉ : si un humain trouve une hallucination là où l'OPA affiche ~1.00,
     l'œil est encore aveugle — d'où le rapport qui montre les réponses BRUTES.

Frugalité : épreuves locales (Ollama temp 0), oracles = sandbox/regex/json/vector-store,
zéro juge LLM, zéro cloud. Historique JSONL → la TENDANCE est lisible.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

OPA_LOG_PATH = os.path.join("memory", "capability_eval.jsonl")
OPA_MODEL = os.getenv("LOCAL_GENERALIST_MODEL", "gemma4:12b")
OLLAMA_GENERATE_URL = "http://127.0.0.1:11434/api/generate"


# ═══════════════════════════════════════════════════════════════════════
# Oracles durs (la vérité ne vient JAMAIS d'un LLM)
# ═══════════════════════════════════════════════════════════════════════

def oracle_nombre(reponse: str, attendu: int) -> float:
    """1.0 si le DERNIER nombre entier de la réponse == attendu (tolère séparateurs)."""
    if not reponse:
        return 0.0
    nums = re.findall(r"-?\d[\d\s,.]*", reponse)
    if not nums:
        return 0.0
    brut = nums[-1].strip()
    # normaliser : retirer espaces/virgules/points de groupement (2 870 / 2,870 / 2.870)
    canon = re.sub(r"[\s,.]", "", brut)
    try:
        return 1.0 if int(canon) == attendu else 0.0
    except ValueError:
        return 0.0


def _extraire_code(reponse: str) -> str:
    """Extrait le premier bloc ```python``` (ou ``` nu) ; sinon la réponse brute."""
    if not reponse:
        return ""
    m = re.search(r"```(?:python)?\s*\n(.*?)```", reponse, re.DOTALL)
    return (m.group(1) if m else reponse).strip()


def oracle_code(reponse: str, tests: str) -> float:
    """1.0 si le code extrait + les asserts passent dans le sandbox isolé (V16).
    L'oracle EST l'exécution : pas d'avis, un verdict."""
    code = _extraire_code(reponse)
    if not code:
        return 0.0
    try:
        from core.capabilities.code_sandbox import sandbox
        res = sandbox.run_python(code + "\n\n" + tests + "\nprint('OPA_OK')")
        return 1.0 if (res.success and "OPA_OK" in (res.stdout or "")) else 0.0
    except Exception:
        return 0.0


def oracle_json(reponse: str, champs: Dict[str, type]) -> float:
    """1.0 si la réponse contient un JSON parsable avec les champs/types exigés
    (fidélité à la contrainte formelle — anti schema-misalignment)."""
    if not reponse:
        return 0.0
    m = re.search(r"\{.*\}", reponse, re.DOTALL)
    if not m:
        return 0.0
    try:
        data = json.loads(m.group(0))
    except Exception:
        return 0.0
    if not isinstance(data, dict):
        return 0.0
    for k, t in champs.items():
        if k not in data:
            return 0.0
        if t is float and isinstance(data[k], (int, float)):
            continue
        if not isinstance(data[k], t):
            return 0.0
    return 1.0


def oracle_recall(question: str, expected_id: Optional[str] = None,
                  require_premium: bool = False, n_results: int = 4) -> float:
    """1.0 si la mémoire RÉELLE retrouve ce qu'on sait y être (recall@k).
    Mesure la chaîne mémoire (embedder+index), pas le LLM. Oracle = présence d'un id
    connu (ou d'un doc tier PREMIUM) dans le top-k."""
    try:
        from core.vector_store import ChromaMemoryManager
        mgr = ChromaMemoryManager.get_instance()
        res = mgr.query_with_metadata([question], n_results=n_results,
                                      collection_name="collective_wisdom")
        ids = ((res or {}).get("ids") or [[]])[0]
        metas = ((res or {}).get("metadatas") or [[]])[0]
        if expected_id is not None:
            return 1.0 if expected_id in ids else 0.0
        if require_premium:
            for meta in metas:
                if isinstance(meta, dict) and meta.get("tier_status") == "PREMIUM":
                    return 1.0
            return 0.0
        return 1.0 if ids else 0.0
    except Exception:
        return 0.0


# ═══════════════════════════════════════════════════════════════════════
# Le référentiel FIXE (Profils de Référence — ne pas modifier les épreuves
# existantes : la comparabilité dans le temps EST l'instrument. Pour faire
# évoluer le set : AJOUTER des épreuves avec un id nouveau, jamais éditer.)
# ═══════════════════════════════════════════════════════════════════════

_TESTS_PALINDROME = (
    "assert est_palindrome('Esope reste ici et se repose'.replace(' ', '')) is True\n"
    "assert est_palindrome('kayak') is True\n"
    "assert est_palindrome('promethee') is False\n"
)
_TESTS_PGCD = (
    "assert pgcd(48, 36) == 12\n"
    "assert pgcd(17, 5) == 1\n"
    "assert pgcd(0, 7) == 7\n"
)

EPREUVES: List[Dict] = [
    {
        "id": "CALC-1", "dimension": "calcul",
        "prompt": ("Calcule la somme des carres des entiers de 1 a 20. "
                   "Montre tes etapes puis termine par le resultat final seul sur la derniere ligne."),
        "oracle": lambda rep: oracle_nombre(rep, 2870),
    },
    {
        "id": "CALC-2", "dimension": "calcul",
        "prompt": ("Combien y a-t-il de nombres premiers strictement inferieurs a 50 ? "
                   "Liste-les puis termine par le compte final seul sur la derniere ligne."),
        "oracle": lambda rep: oracle_nombre(rep, 15),
    },
    {
        "id": "CODE-1", "dimension": "code",
        "prompt": ("Ecris UNIQUEMENT une fonction Python `est_palindrome(s)` qui retourne True si "
                   "la chaine s est un palindrome (ignorer la casse). Pas d'import, pas d'exemple "
                   "d'utilisation, juste la fonction dans un bloc ```python```."),
        "oracle": lambda rep: oracle_code(rep, _TESTS_PALINDROME),
    },
    {
        "id": "CODE-2", "dimension": "code",
        "prompt": ("Ecris UNIQUEMENT une fonction Python `pgcd(a, b)` (algorithme d'Euclide). "
                   "Pas d'import, juste la fonction dans un bloc ```python```."),
        "oracle": lambda rep: oracle_code(rep, _TESTS_PGCD),
    },
    {
        "id": "JSON-1", "dimension": "contrainte",
        "prompt": ("Reponds UNIQUEMENT avec un objet JSON valide, sans aucun texte autour, de la "
                   "forme {\"agent\": \"<nom d'un de tes agents>\", \"confiance\": <float entre 0 et 1>, "
                   "\"raison\": \"<une phrase>\"}."),
        "oracle": lambda rep: oracle_json(rep, {"agent": str, "confiance": float, "raison": str}),
    },
    {
        "id": "RECALL-1", "dimension": "memoire",
        "prompt": None,   # pas d'appel LLM : on mesure la chaine memoire elle-meme
        "oracle": lambda _:
            oracle_recall("anti-repetition intention structuree stockee a la source mots-cles",
                          expected_id="premium_lesson_011"),
    },
    {
        "id": "RECALL-2", "dimension": "memoire",
        "prompt": None,
        "oracle": lambda _:
            oracle_recall("qu'ai-je appris sur l'honnetete envers moi-meme ?",
                          require_premium=True),
    },
]


# ═══════════════════════════════════════════════════════════════════════
# Le runner
# ═══════════════════════════════════════════════════════════════════════

async def _appel_llm_local(prompt: str, timeout: float = 90.0) -> str:
    """Appel Ollama direct, temp 0 (reproductibilité de l'instrument), think off."""
    import httpx
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(OLLAMA_GENERATE_URL, json={
            "model": OPA_MODEL,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "options": {"temperature": 0.0, "num_predict": 700},
        })
    return ((resp.json() or {}).get("response") or "").strip()


async def run_opa(llm_call: Optional[Callable] = None,
                  log_path: str = OPA_LOG_PATH) -> Dict:
    """Joue le référentiel FIXE, score chaque épreuve par son oracle dur, persiste
    l'historique JSONL. Retourne {global, dimensions, epreuves[], duree_s}."""
    llm = llm_call or _appel_llm_local
    t0 = time.time()
    details = []
    for ep in EPREUVES:
        rep = ""
        try:
            if ep["prompt"] is not None:
                rep = await llm(ep["prompt"])
            score = float(ep["oracle"](rep))
        except Exception as e:
            logger.warning(f"[OPA] epreuve {ep['id']} en echec harnais: {e}")
            score = 0.0
        details.append({
            "id": ep["id"], "dimension": ep["dimension"], "score": score,
            "reponse_brute": (rep or "")[:600],   # falsifiabilité : l'humain peut contredire l'oracle
        })

    dims: Dict[str, List[float]] = {}
    for d in details:
        dims.setdefault(d["dimension"], []).append(d["score"])
    dimensions = {k: round(sum(v) / len(v), 3) for k, v in dims.items()}
    global_score = round(sum(d["score"] for d in details) / len(details), 3)

    record = {
        "ts": time.time(),
        "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": OPA_MODEL,
        "global": global_score,
        "dimensions": dimensions,
        "scores": {d["id"]: d["score"] for d in details},
        "duree_s": round(time.time() - t0, 1),
    }
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"[OPA] persistance echouee (non bloquant): {e}")

    return {"global": global_score, "dimensions": dimensions,
            "epreuves": details, "duree_s": record["duree_s"]}


def historique_opa(log_path: str = OPA_LOG_PATH, n: int = 10) -> List[Dict]:
    """Les n derniers runs (pour la tendance J+7/J+14 voulue par la conception)."""
    try:
        lines = open(log_path, encoding="utf-8").read().splitlines()
        out = []
        for L in lines[-n:]:
            try:
                out.append(json.loads(L))
            except Exception:
                pass
        return out
    except Exception:
        return []


def format_rapport(result: Dict, history: Optional[List[Dict]] = None) -> str:
    """Rapport lisible : scores + tendance + réponses brutes tronquées (falsifiabilité)."""
    lines = [f"[OPA] L'Oeil par Preuve d'Action — score global : {result['global']:.2f}"
             f" ({result['duree_s']}s)"]
    for dim, sc in sorted(result["dimensions"].items()):
        lines.append(f"  {dim:<11}: {sc:.2f}")
    lines.append("  — epreuves —")
    for d in result["epreuves"]:
        ok = "✅" if d["score"] >= 1.0 else ("◐" if d["score"] > 0 else "❌")
        lines.append(f"  {ok} {d['id']} ({d['dimension']}) = {d['score']:.1f}")
    if history and len(history) > 1:
        lines.append("  — tendance (runs precedents) —")
        for h in history[-4:-1]:
            lines.append(f"  {h.get('date', '?')[:16]} : global {h.get('global')}")
    lines.append("  Oracle = sandbox/regex/json/memoire (jamais un juge LLM). "
                 "Si un humain trouve une erreur la ou l'OPA dit ~1.00 : l'oeil est encore aveugle.")
    return "\n".join(lines)
