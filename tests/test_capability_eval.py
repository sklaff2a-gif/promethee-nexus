# -*- coding: utf-8 -*-
"""TDD de l'OPA -- L'Oeil par Preuve d'Action (atelier harnais P1, co-concu par Promethee).
Principes testes : oracle DUR (jamais un juge LLM), referentiel FIXE, historique (tendance),
falsifiabilite (reponses brutes dans le rapport)."""
import json
import pytest

import core.capability_eval as opa
from core.vector_store import ChromaMemoryManager


# ─── oracle_nombre ───
def test_oracle_nombre_dernier_nombre():
    assert opa.oracle_nombre("Les etapes... le resultat final est :\n2870", 2870) == 1.0

def test_oracle_nombre_avec_separateurs():
    assert opa.oracle_nombre("Resultat : 2 870", 2870) == 1.0
    assert opa.oracle_nombre("Result: 2,870", 2870) == 1.0

def test_oracle_nombre_faux():
    assert opa.oracle_nombre("Le resultat est 2871", 2870) == 0.0

def test_oracle_nombre_vide():
    assert opa.oracle_nombre("", 42) == 0.0
    assert opa.oracle_nombre("aucun nombre ici", 42) == 0.0


# ─── oracle_code (le sandbox EST le verdict) ───
def test_oracle_code_palindrome_correct():
    rep = ("Voici :\n```python\ndef est_palindrome(s):\n"
           "    s = s.lower()\n    return s == s[::-1]\n```")
    assert opa.oracle_code(rep, opa._TESTS_PALINDROME) == 1.0

def test_oracle_code_bugge_echoue():
    rep = "```python\ndef est_palindrome(s):\n    return True\n```"
    assert opa.oracle_code(rep, opa._TESTS_PALINDROME) == 0.0

def test_oracle_code_sans_bloc_markdown():
    # texte brut sans fence : on tente quand meme (extraction = fallback brut)
    rep = "def pgcd(a, b):\n    while b:\n        a, b = b, a % b\n    return a"
    assert opa.oracle_code(rep, opa._TESTS_PGCD) == 1.0

def test_oracle_code_vide():
    assert opa.oracle_code("", opa._TESTS_PGCD) == 0.0


# ─── oracle_json (fidelite a la contrainte) ───
def test_oracle_json_conforme():
    rep = '{"agent": "coder", "confiance": 0.9, "raison": "specialiste"}'
    assert opa.oracle_json(rep, {"agent": str, "confiance": float, "raison": str}) == 1.0

def test_oracle_json_champ_manquant():
    rep = '{"agent": "coder", "confiance": 0.9}'
    assert opa.oracle_json(rep, {"agent": str, "confiance": float, "raison": str}) == 0.0

def test_oracle_json_mauvais_type():
    rep = '{"agent": "coder", "confiance": "haute", "raison": "x"}'
    assert opa.oracle_json(rep, {"agent": str, "confiance": float, "raison": str}) == 0.0

def test_oracle_json_entoure_de_texte():
    # le LLM bavarde autour : on extrait le {...} quand meme
    rep = 'Voici : {"agent": "vision", "confiance": 1, "raison": "ok"} voila.'
    assert opa.oracle_json(rep, {"agent": str, "confiance": float, "raison": str}) == 1.0


# ─── oracle_recall (mocke : on teste la LOGIQUE, pas chroma) ───
class _FakeMgr:
    def __init__(self, ids, metas):
        self._ids, self._metas = ids, metas
    def query_with_metadata(self, q, n_results=4, collection_name="collective_wisdom"):
        return {"ids": [self._ids], "metadatas": [self._metas], "documents": [[]]}

def test_oracle_recall_expected_id(monkeypatch):
    monkeypatch.setattr(ChromaMemoryManager, "get_instance",
                        classmethod(lambda cls, *a, **k: _FakeMgr(
                            ["a", "premium_lesson_011", "b"], [{}, {}, {}])))
    assert opa.oracle_recall("q", expected_id="premium_lesson_011") == 1.0
    assert opa.oracle_recall("q", expected_id="absent_id") == 0.0

def test_oracle_recall_require_premium(monkeypatch):
    monkeypatch.setattr(ChromaMemoryManager, "get_instance",
                        classmethod(lambda cls, *a, **k: _FakeMgr(
                            ["x"], [{"tier_status": "PREMIUM"}])))
    assert opa.oracle_recall("q", require_premium=True) == 1.0

def test_oracle_recall_erreur_rend_zero(monkeypatch):
    def boom(cls, *a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(ChromaMemoryManager, "get_instance", classmethod(boom))
    assert opa.oracle_recall("q", expected_id="x") == 0.0   # echec harnais = 0, pas crash


# ─── le runner ───
@pytest.mark.asyncio
async def test_run_opa_complet(monkeypatch, tmp_path):
    # LLM mocke PARFAIT -> les epreuves LLM scorent 1.0 ; recall mocke premium present
    async def llm_parfait(prompt):
        if "somme des carres" in prompt:
            return "2870"
        if "premiers" in prompt:
            return "15"
        if "est_palindrome" in prompt:
            return "```python\ndef est_palindrome(s):\n    s = s.lower()\n    return s == s[::-1]\n```"
        if "pgcd" in prompt:
            return "```python\ndef pgcd(a, b):\n    while b:\n        a, b = b, a % b\n    return a\n```"
        return '{"agent": "coder", "confiance": 0.8, "raison": "test"}'
    monkeypatch.setattr(ChromaMemoryManager, "get_instance",
                        classmethod(lambda cls, *a, **k: _FakeMgr(
                            ["premium_lesson_011"], [{"tier_status": "PREMIUM"}])))
    log = str(tmp_path / "opa.jsonl")
    res = await opa.run_opa(llm_call=llm_parfait, log_path=log)
    assert res["global"] == 1.0
    assert set(res["dimensions"]) == {"calcul", "code", "contrainte", "memoire"}
    # persistance : 1 ligne JSONL relisible
    hist = opa.historique_opa(log_path=log)
    assert len(hist) == 1 and hist[0]["global"] == 1.0

@pytest.mark.asyncio
async def test_run_opa_llm_mauvais_score_bas(monkeypatch, tmp_path):
    async def llm_nul(prompt):
        return "je ne sais pas"
    monkeypatch.setattr(ChromaMemoryManager, "get_instance",
                        classmethod(lambda cls, *a, **k: _FakeMgr([], [])))
    res = await opa.run_opa(llm_call=llm_nul, log_path=str(tmp_path / "o.jsonl"))
    assert res["global"] == 0.0   # l'oracle ne se laisse pas attendrir

@pytest.mark.asyncio
async def test_run_opa_llm_crash_ne_tue_pas_le_run(monkeypatch, tmp_path):
    async def llm_crash(prompt):
        raise RuntimeError("ollama down")
    monkeypatch.setattr(ChromaMemoryManager, "get_instance",
                        classmethod(lambda cls, *a, **k: _FakeMgr([], [])))
    res = await opa.run_opa(llm_call=llm_crash, log_path=str(tmp_path / "o.jsonl"))
    assert res["global"] == 0.0 and len(res["epreuves"]) == len(opa.EPREUVES)


# ─── cablage chat : !opa / !benchmark ───
def test_opa_dans_whitelist_auto_action():
    from core.chat_engine import ChatEngine
    wl = ChatEngine._AUTO_ACTION_WHITELIST
    assert "opa" in wl and "benchmark" in wl

def test_parse_opa():
    from core.chat_engine import ChatEngine
    eng = ChatEngine.__new__(ChatEngine)
    assert eng._parse_command("!opa")[0] == "opa"
    assert eng._parse_command("!benchmark")[0] == "benchmark"

@pytest.mark.asyncio
async def test_execute_opa_command_rend_rapport(monkeypatch, tmp_path):
    from core.chat_engine import ChatEngine
    async def llm(prompt):
        return "2870"
    monkeypatch.setattr(ChromaMemoryManager, "get_instance",
                        classmethod(lambda cls, *a, **k: _FakeMgr([], [])))
    monkeypatch.setattr(opa, "OPA_LOG_PATH", str(tmp_path / "o.jsonl"))
    monkeypatch.setattr(opa, "_appel_llm_local", llm)
    eng = ChatEngine.__new__(ChatEngine)
    out = await eng._execute_opa_command()
    assert "OPA" in out and "global" in out.lower()


# ─── falsifiabilite : le rapport montre les reponses brutes ───
@pytest.mark.asyncio
async def test_rapport_contient_la_verite_brute(monkeypatch, tmp_path):
    async def llm(prompt):
        return "2870"
    monkeypatch.setattr(ChromaMemoryManager, "get_instance",
                        classmethod(lambda cls, *a, **k: _FakeMgr([], [])))
    res = await opa.run_opa(llm_call=llm, log_path=str(tmp_path / "o.jsonl"))
    rapport = opa.format_rapport(res)
    assert "OPA" in rapport and "CALC-1" in rapport
    assert "aveugle" in rapport   # le rappel de falsifiabilite est dans le rapport
    # les reponses brutes sont conservees dans le resultat (l'humain peut contredire)
    assert any(d["reponse_brute"] for d in res["epreuves"])
