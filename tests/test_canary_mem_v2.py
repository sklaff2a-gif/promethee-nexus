# -*- coding: utf-8 -*-
"""TDD Canary Memoire V2 (V26.0) : basculement EXCLUSIF confine via contextvars.

Sur FREE_TIME, query_documents sert le temoin MULTILINGUE (collective_wisdom_v2_test) au lieu
de l'ancien index anglais. Confinement par contextvar (zero etat singleton). Filet : si le
temoin renvoie vide/erreur -> fallback sur l'ancien (on ne casse jamais le RAG).
"""
import pytest
import core.vector_store as vsmod
from core.context import canary_mem_v2
from core.vector_store import ChromaMemoryManager


@pytest.fixture(autouse=True)
def _isole_le_canary(monkeypatch):
    """Phase 4 (10/06) : le FULL SWITCH est ON par defaut en prod et subsume le canary.
    Ces tests valident le mecanisme CANARY en isolation -> on coupe le full switch ici."""
    monkeypatch.setattr(vsmod, "MEM_V2_FULL_SWITCH", False)


class _FakeCol:
    def __init__(self, tag): self.tag = tag
    def query(self, query_texts, n_results):
        return {"ids": [[self.tag + "-id"]], "documents": [["doc"]]}


def _mgr(shadow=None):
    m = ChromaMemoryManager.__new__(ChromaMemoryManager)
    m._get_collection = lambda name: _FakeCol("OLD")
    m._get_shadow_collection = lambda name: (shadow if shadow is not None else _FakeCol("NEW"))
    m._shadow_observe = lambda *a, **k: None
    return m


def test_contextvar_defaut_false():
    assert canary_mem_v2.get() is False

def test_contextvar_set_reset():
    tok = canary_mem_v2.set(True)
    assert canary_mem_v2.get() is True
    canary_mem_v2.reset(tok)
    assert canary_mem_v2.get() is False

def test_sans_canary_sert_ancien():
    r = _mgr().query_documents(["q"], n_results=1, collection_name="collective_wisdom")
    assert r["ids"][0][0] == "OLD-id"

def test_avec_canary_sert_temoin_multilingue():
    m = _mgr()
    tok = canary_mem_v2.set(True)
    try:
        r = m.query_documents(["q"], n_results=1, collection_name="collective_wisdom")
    finally:
        canary_mem_v2.reset(tok)
    assert r["ids"][0][0] == "NEW-id"

def test_canary_confine_a_collective_wisdom():
    m = _mgr()
    tok = canary_mem_v2.set(True)
    try:
        r = m.query_documents(["q"], n_results=1, collection_name="autre_collection")
    finally:
        canary_mem_v2.reset(tok)
    assert r["ids"][0][0] == "OLD-id"   # canary on mais hors collective_wisdom -> ancien

def test_fallback_si_temoin_vide():
    vide = type("E", (), {"query": lambda s, query_texts, n_results: {"ids": [[]]}})()
    m = _mgr(shadow=vide)
    tok = canary_mem_v2.set(True)
    try:
        r = m.query_documents(["q"], n_results=1, collection_name="collective_wisdom")
    finally:
        canary_mem_v2.reset(tok)
    assert r["ids"][0][0] == "OLD-id"   # neuf vide -> fallback ancien (securite)

def test_fallback_si_temoin_indispo():
    m = _mgr(shadow=None)   # _get_shadow_collection -> _FakeCol NEW ; on force None ci-dessous
    m._get_shadow_collection = lambda name: None
    tok = canary_mem_v2.set(True)
    try:
        r = m.query_documents(["q"], n_results=1, collection_name="collective_wisdom")
    finally:
        canary_mem_v2.reset(tok)
    assert r["ids"][0][0] == "OLD-id"   # embedder multilingue indispo -> fallback ancien
