# -*- coding: utf-8 -*-
"""TDD de !recall -- 3e outil de la console agentique, CO-CONCU par Promethee (atelier phase 5).
Interroge sa MEMOIRE REELLE (multilingue depuis le Full Switch). Garantie qu'il a posee :
NE JAMAIS inventer un souvenir -> on rend ce que la memoire contient, ou 'aucun souvenir' net."""
import core.vector_store as vsmod
from core.vector_store import ChromaMemoryManager
from core.chat_engine import ChatEngine


def _eng():
    return ChatEngine.__new__(ChatEngine)


class _FakeMgr:
    def __init__(self, res):
        self._res = res
    def query_with_metadata(self, query_texts, n_results=4, collection_name="collective_wisdom"):
        return self._res


def _patch_mgr(monkeypatch, res):
    monkeypatch.setattr(ChromaMemoryManager, "get_instance",
                        classmethod(lambda cls, *a, **k: _FakeMgr(res)))


# --- parsing BRUT (apostrophe dans la question ne crashe pas shlex) ---
def test_parse_recall_brut():
    cmd, args = _eng()._parse_command("!recall Qu'est-ce que j'ai appris ?")
    assert cmd == "recall"
    assert "Qu'est-ce que j'ai appris" in args[0]   # apostrophes preservees

def test_parse_recall_vide():
    cmd, args = _eng()._parse_command("!recall")
    assert cmd == "recall" and args == [""]


# --- whitelist auto-action ---
def test_recall_dans_whitelist():
    assert "recall" in ChatEngine._AUTO_ACTION_WHITELIST


# --- execution : rend les vrais souvenirs ---
def test_recall_rend_les_souvenirs(monkeypatch):
    res = {
        "documents": [["Le veto prefrontal refuse une tache hors-sujet",
                       "L'honnetete est l'invariant absolu"]],
        "metadatas": [[{"source": "lesson_certified"}, {"tier_status": "PREMIUM"}]],
        "distances": [[0.12, 0.31]],
    }
    _patch_mgr(monkeypatch, res)
    out = _eng()._execute_recall_command("Que sais-tu du veto ?")
    assert "veto prefrontal" in out and "honnetete" in out
    assert "lesson_certified" in out   # la provenance est montree

def test_recall_aucun_souvenir_pas_d_invention(monkeypatch):
    # GARANTIE qu'il a posee : memoire vide -> message net, JAMAIS une hallucination
    _patch_mgr(monkeypatch, {"documents": [[]], "metadatas": [[]], "distances": [[]]})
    out = _eng()._execute_recall_command("sujet totalement inconnu xyz")
    assert "Aucun souvenir" in out and "invention" in out.lower()

def test_recall_usage_si_vide():
    assert "Usage" in _eng()._execute_recall_command("")

def test_recall_erreur_memoire_ne_crashe_pas(monkeypatch):
    def boom(cls, *a, **k):
        raise RuntimeError("chroma down")
    monkeypatch.setattr(ChromaMemoryManager, "get_instance", classmethod(boom))
    out = _eng()._execute_recall_command("test")
    assert "erreur" in out.lower()   # degrade proprement, pas de crash HTTP
