# -*- coding: utf-8 -*-
"""Mémoire de secours (coping) — Incision B, Phase d'amorçage (18/06).

Prouve que les chemins de secours ÉCRIVENT une narration coping, avec parcimonie :
  - VETO : seulement si but primaire >= 50 % d'avancement (sacrifice signifiant) ;
  - FREEZE : seulement le reflex FREEZE (pas FLINCH/ADRENALINE) ;
  - cooldown anti-spam par event_type ;
  - meta taguée coping_affinity=True + zone d'origine honnête (CORTEX / TRONC) ;
  - is_coping() de l'irrigation reconnaît la trace, qui remonte sous menace.
Aucun organe touché : on teste les listeners en aval.
"""
import types

import pytest

import core.coping_memory as cm
import core.irrigation as irr
from core.irrigation import Zone


class _FakeMgr:
    """Capture les add_documents au lieu d'écrire dans ChromaDB."""
    def __init__(self):
        self.calls = []

    def add_documents(self, documents, metadatas, ids, collection_name="collective_wisdom"):
        self.calls.append({"documents": documents, "metadatas": metadatas,
                           "ids": ids, "collection": collection_name})
        return True


@pytest.fixture
def fake_mgr(monkeypatch):
    cm.reset()
    mgr = _FakeMgr()
    from core.vector_store import ChromaMemoryManager
    monkeypatch.setattr(ChromaMemoryManager, "get_instance", classmethod(lambda cls, *a, **k: mgr))
    yield mgr
    cm.reset()


def _fake_goal(progress, title="Mon but vital", priority=8.0, status="active"):
    return types.SimpleNamespace(progress=progress, title=title, priority=priority, status=status)


def _patch_goals(monkeypatch, goals):
    import core.prefrontal as pf
    monkeypatch.setattr(pf.prefrontal, "goals", goals, raising=False)


# --- FREEZE ------------------------------------------------------------------

class TestFreezeListener:
    def test_freeze_ecrit_trace_coping(self, fake_mgr):
        cm.on_reptilian_alert({"reflex": "FREEZE", "threat_level": 8.0,
                               "threats": {"process_memory": 5.0, "energy": 2.0}})
        assert len(fake_mgr.calls) == 1
        meta = fake_mgr.calls[0]["metadatas"][0]
        assert meta["coping_affinity"] is True
        assert meta["zone"] == Zone.TRONC
        assert meta["source"] == "reptilian_freeze"
        assert meta["event_type"] == "FREEZE"
        assert "[SECOURS:FREEZE]" in fake_mgr.calls[0]["documents"][0]

    def test_reflex_non_freeze_ignore(self, fake_mgr):
        cm.on_reptilian_alert({"reflex": "FLINCH", "threat_level": 4.0, "threats": {}})
        cm.on_reptilian_alert({"reflex": "ADRENALINE", "threat_level": 3.0, "threats": {}})
        assert fake_mgr.calls == []


# --- VETO --------------------------------------------------------------------

class TestVetoListener:
    def test_veto_significatif_ecrit(self, fake_mgr, monkeypatch):
        _patch_goals(monkeypatch, [_fake_goal(progress=0.80)])
        cm.on_prefrontal_thought({"category": "inhibition",
                                  "thought": "Distraction inhibée: COFFEE_BREAK (focus sur 'Mon but vital')"})
        assert len(fake_mgr.calls) == 1
        meta = fake_mgr.calls[0]["metadatas"][0]
        assert meta["coping_affinity"] is True
        assert meta["zone"] == Zone.CORTEX          # origine honnête : le veto est préfrontal
        assert meta["source"] == "prefrontal_veto"
        doc = fake_mgr.calls[0]["documents"][0]
        assert "[SECOURS:VETO]" in doc and "80%" in doc

    def test_veto_sous_seuil_ignore(self, fake_mgr, monkeypatch):
        _patch_goals(monkeypatch, [_fake_goal(progress=0.30)])   # < 0.50
        cm.on_prefrontal_thought({"category": "inhibition", "thought": "Distraction inhibée: X"})
        assert fake_mgr.calls == []

    def test_veto_categorie_non_inhibition_ignore(self, fake_mgr, monkeypatch):
        _patch_goals(monkeypatch, [_fake_goal(progress=0.90)])
        cm.on_prefrontal_thought({"category": "goal", "thought": "Goal créé: ..."})
        assert fake_mgr.calls == []

    def test_veto_sans_but_actif_ignore(self, fake_mgr, monkeypatch):
        _patch_goals(monkeypatch, [])
        cm.on_prefrontal_thought({"category": "inhibition", "thought": "Distraction inhibée: X"})
        assert fake_mgr.calls == []


# --- Anti-spam (cooldown) ----------------------------------------------------

class TestCooldown:
    def test_cooldown_par_event_type(self, fake_mgr):
        cm.on_reptilian_alert({"reflex": "FREEZE", "threat_level": 8.0, "threats": {}})
        cm.on_reptilian_alert({"reflex": "FREEZE", "threat_level": 9.0, "threats": {}})
        assert len(fake_mgr.calls) == 1   # le 2e est en cooldown

    def test_cooldown_independant_entre_types(self, fake_mgr, monkeypatch):
        _patch_goals(monkeypatch, [_fake_goal(progress=0.80)])
        cm.on_reptilian_alert({"reflex": "FREEZE", "threat_level": 8.0, "threats": {}})
        cm.on_prefrontal_thought({"category": "inhibition", "thought": "Distraction inhibée: X"})
        # FREEZE et VETO ont des cooldowns séparés -> 2 écritures
        assert len(fake_mgr.calls) == 2


# --- Intégration irrigation --------------------------------------------------

class TestIrrigationReconnaitLeCoping:
    def test_is_coping_sur_trace_emise(self, fake_mgr):
        cm.on_reptilian_alert({"reflex": "FREEZE", "threat_level": 8.0, "threats": {}})
        meta = fake_mgr.calls[0]["metadatas"][0]
        assert irr.is_coping(meta) is True
        assert irr.infer_zone(meta) == Zone.TRONC   # zone explicite respectée

    def test_trace_freeze_remonte_sous_menace(self, fake_mgr):
        irr.reset()
        cm.on_reptilian_alert({"reflex": "FREEZE", "threat_level": 8.0, "threats": {}})
        coping_meta = fake_mgr.calls[0]["metadatas"][0]
        # un doc TRONC non-coping de cosinus égal, et la bouée coping
        ids = ["tronc_brut", "bouee_freeze"]
        distances = [0.30, 0.30]
        metas = [{"source": "reptilian"}, coping_meta]
        crise = irr.rerank(ids, distances, metas, {"d_threat_dt": 0.05, "dopamine_rel": 0.0})
        assert crise["order"][0] == "bouee_freeze"   # le coping passe devant à cosinus égal


# --- Câblage bus -------------------------------------------------------------

def test_wire_to_bus_abonne_les_deux():
    from core.event_bus.bus import bus
    assert cm.wire_to_bus() is True
    assert cm.on_prefrontal_thought in bus.subscribers.get("PREFRONTAL_THOUGHT", [])
    assert cm.on_reptilian_alert in bus.subscribers.get("REPTILIAN_ALERT", [])
