# -*- coding: utf-8 -*-
"""TDD du Contraste Contextuel en SHADOW (atelier chat protocole Sakana). Mesure A COTE de
l'injection memoire reelle le BIAIS soliloque (reel vs contraste = 1 soliloque + 2 interactions).
Lecture seule, kill-switch, borg ; ne change JAMAIS l'injection reelle du chat."""
import json
import core.vector_store as vs
from core.chat_engine import ChatEngine


class _FakeMem:
    def __init__(self, ids, raise_=False):
        self._ids = ids; self._raise = raise_
    def query_with_metadata(self, qs, n_results=3):
        if self._raise:
            raise RuntimeError("chroma down")
        return {"ids": [self._ids]}


def _patch_mem(monkeypatch, ids, raise_=False):
    class _Mgr:
        @staticmethod
        def get_instance():
            return _FakeMem(ids, raise_)
    monkeypatch.setattr(vs, "ChromaMemoryManager", _Mgr)


def _engine(messages=None):
    e = ChatEngine.__new__(ChatEngine)
    e.messages = messages or []
    return e


def _run(monkeypatch, tmp_path, ids, msg="epsilon bruit memoire vectorielle", messages=None, raise_=False):
    (tmp_path / "memory").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    _patch_mem(monkeypatch, ids, raise_)
    _engine(messages)._contraste_contextuel_shadow(msg)
    p = tmp_path / "memory" / "chat_contraste_shadow.jsonl"
    if not p.exists():
        return None
    lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return json.loads(lines[-1]) if lines else None


def test_biais_3_soliloques_reduit_a_1(monkeypatch, tmp_path):
    e = _run(monkeypatch, tmp_path, ["soliloque-1", "soliloque_v2-2", "soliloque-3"])
    assert e["real_soliloque"] == 3
    assert e["contraste_soliloque"] == 1
    assert e["biais_soliloque_reduit"] == 2

def test_aucun_soliloque(monkeypatch, tmp_path):
    e = _run(monkeypatch, tmp_path, ["lesson-a", "consol-b", "self-c"])
    assert e["real_soliloque"] == 0
    assert e["contraste_soliloque"] == 0
    assert e["biais_soliloque_reduit"] == 0

def test_kill_switch_aucune_ecriture(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTRASTE_SHADOW_ENABLED", "0")
    assert _run(monkeypatch, tmp_path, ["soliloque-1"]) is None

def test_message_court_noop(monkeypatch, tmp_path):
    assert _run(monkeypatch, tmp_path, ["soliloque-1"], msg="ok") is None

def test_borg_query_echoue_pas_de_crash(monkeypatch, tmp_path):
    # la requete leve -> aucune exception ne remonte, aucun log
    assert _run(monkeypatch, tmp_path, ["soliloque-1"], raise_=True) is None

def test_interactions_recentes_extraites(monkeypatch, tmp_path):
    msgs = [{"role": "user", "content": "parlons de epsilon et de la memoire vectorielle"},
            {"role": "assistant", "content": "le tissu cellulaire et la famine"}]
    e = _run(monkeypatch, tmp_path, ["soliloque-1", "x-2", "y-3"],
             msg="epsilon memoire vectorielle", messages=msgs)
    assert len(e["contraste_interactions"]) >= 1
    # le message le plus pertinent (epsilon/memoire) doit avoir le Jaccard le plus haut
    assert e["contraste_interactions"][0]["jac"] >= e["contraste_interactions"][-1]["jac"]
