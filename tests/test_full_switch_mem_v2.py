# -*- coding: utf-8 -*-
"""TDD Phase 4 — FULL SWITCH Memoire V2 (10/06) : le temoin MULTILINGUE devient canonique.
Sous MEM_V2_FULL_SWITCH, TOUTES les lectures ET ecritures de `collective_wisdom` vont au
temoin (embedder francais), pas seulement FREE_TIME (canary). Filet : temoin indispo -> ancien.
Kill-switch : MEM_V2_FULL_SWITCH=False -> retour integral a l'ancien index anglais."""
import pytest
import core.vector_store as vsmod
from core.vector_store import ChromaMemoryManager


class _FakeCol:
    def __init__(self, tag):
        self.tag = tag
        self.added = []
        self.deleted = []
    def query(self, query_texts, n_results):
        return {"ids": [[self.tag + "-id"]], "documents": [["doc"]]}
    def add(self, documents, metadatas, ids):
        self.added.append((documents, metadatas, ids))
    def count(self):
        return {"OLD": 11, "NEW": 22}[self.tag]
    def get(self, ids=None, include=None):
        # un doc vieux (purgeable) avec un id traceur du tag
        return {"ids": [self.tag + "-doc"],
                "metadatas": [{"timestamp": "0", "recall_count": 0}],
                "documents": [["x"]]}
    def delete(self, ids):
        self.deleted.extend(ids)


def _mgr(old, new):
    m = ChromaMemoryManager.__new__(ChromaMemoryManager)
    m._get_collection = lambda name: old
    m._get_shadow_collection = lambda name: new
    m._shadow_observe = lambda *a, **k: None
    return m


# ---------- LECTURE ----------
def test_full_switch_read_sert_temoin(monkeypatch):
    monkeypatch.setattr(vsmod, "MEM_V2_FULL_SWITCH", True)
    m = _mgr(_FakeCol("OLD"), _FakeCol("NEW"))
    r = m.query_documents(["q"], n_results=1, collection_name="collective_wisdom")
    assert r["ids"][0][0] == "NEW-id"   # multilingue, SANS canary

def test_full_switch_off_sert_ancien(monkeypatch):
    monkeypatch.setattr(vsmod, "MEM_V2_FULL_SWITCH", False)
    m = _mgr(_FakeCol("OLD"), _FakeCol("NEW"))
    r = m.query_documents(["q"], n_results=1, collection_name="collective_wisdom")
    assert r["ids"][0][0] == "OLD-id"   # kill-switch -> ancien

def test_full_switch_read_confine_collective_wisdom(monkeypatch):
    monkeypatch.setattr(vsmod, "MEM_V2_FULL_SWITCH", True)
    m = _mgr(_FakeCol("OLD"), _FakeCol("NEW"))
    r = m.query_documents(["q"], n_results=1, collection_name="code_snippets")
    assert r["ids"][0][0] == "OLD-id"   # autre collection -> inchangee

def test_full_switch_read_fallback_si_temoin_indispo(monkeypatch):
    monkeypatch.setattr(vsmod, "MEM_V2_FULL_SWITCH", True)
    m = _mgr(_FakeCol("OLD"), _FakeCol("NEW"))
    m._get_shadow_collection = lambda name: None   # embedder multilingue KO
    r = m.query_documents(["q"], n_results=1, collection_name="collective_wisdom")
    assert r["ids"][0][0] == "OLD-id"   # filet -> ancien, jamais de RAG casse


# ---------- ECRITURE ----------
def test_full_switch_write_va_au_temoin(monkeypatch):
    monkeypatch.setattr(vsmod, "MEM_V2_FULL_SWITCH", True)
    old, new = _FakeCol("OLD"), _FakeCol("NEW")
    m = _mgr(old, new)
    ok = m.add_documents(["doc"], [{"source": "chat"}], ["id1"], collection_name="collective_wisdom")
    assert ok and len(new.added) == 1 and len(old.added) == 0   # ecrit au temoin

def test_full_switch_write_off_va_a_ancien(monkeypatch):
    monkeypatch.setattr(vsmod, "MEM_V2_FULL_SWITCH", False)
    old, new = _FakeCol("OLD"), _FakeCol("NEW")
    m = _mgr(old, new)
    m.add_documents(["doc"], [{"source": "chat"}], ["id1"], collection_name="collective_wisdom")
    assert len(old.added) == 1 and len(new.added) == 0   # kill-switch -> ancien

def test_full_switch_write_confine(monkeypatch):
    monkeypatch.setattr(vsmod, "MEM_V2_FULL_SWITCH", True)
    old, new = _FakeCol("OLD"), _FakeCol("NEW")
    m = _mgr(old, new)
    m.add_documents(["doc"], [{"k": "v"}], ["id1"], collection_name="code_snippets")
    assert len(old.added) == 1 and len(new.added) == 0   # autre collection -> ancien

def test_full_switch_write_fallback_si_temoin_indispo(monkeypatch):
    monkeypatch.setattr(vsmod, "MEM_V2_FULL_SWITCH", True)
    old, new = _FakeCol("OLD"), _FakeCol("NEW")
    m = _mgr(old, new)
    m._get_shadow_collection = lambda name: None
    ok = m.add_documents(["doc"], [{"source": "chat"}], ["id1"], collection_name="collective_wisdom")
    assert ok and len(old.added) == 1   # filet -> ancien, ecriture jamais perdue


# ---------- COHERENCE : count / recall / purge ciblent AUSSI le temoin (sinon jamais purge) ----------
def test_full_switch_count_cible_temoin(monkeypatch):
    monkeypatch.setattr(vsmod, "MEM_V2_FULL_SWITCH", True)
    m = _mgr(_FakeCol("OLD"), _FakeCol("NEW"))
    assert m.count_documents("collective_wisdom") == 22   # compte le temoin, pas l'ancien (11)

def test_full_switch_purge_cible_temoin(monkeypatch):
    monkeypatch.setattr(vsmod, "MEM_V2_FULL_SWITCH", True)
    old, new = _FakeCol("OLD"), _FakeCol("NEW")
    m = _mgr(old, new)
    n = m.purge_expired(max_age_days=1, collection_name="collective_wisdom")
    assert n == 1 and new.deleted == ["NEW-doc"] and old.deleted == []   # purge le temoin

def test_full_switch_canonical_helper(monkeypatch):
    # le helper EST la source unique de verite du routage
    old, new = _FakeCol("OLD"), _FakeCol("NEW")
    m = _mgr(old, new)
    monkeypatch.setattr(vsmod, "MEM_V2_FULL_SWITCH", True)
    assert m._canonical_collection("collective_wisdom").tag == "NEW"
    assert m._canonical_collection("code_snippets").tag == "OLD"
    monkeypatch.setattr(vsmod, "MEM_V2_FULL_SWITCH", False)
    assert m._canonical_collection("collective_wisdom").tag == "OLD"
