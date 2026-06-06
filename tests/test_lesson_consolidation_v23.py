# -*- coding: utf-8 -*-
"""Tests V23.0 — consolidation forte des lecons certifiees (cycle eleve-actif).

Deux volets :
  - synaptic : hebbian_strengthen accepte un strength_factor (defaut 1.0 =
    retrocompat totale, clampe a [0,4] -> jamais d'attracteur permanent d'un coup).
  - chat : commande !grave -> extrait les concepts de la lecon certifiee, les
    grave au taux FORT, trace dans le journal. Cortex mocke (pas d'effet de bord).
"""
import pytest
from unittest.mock import patch

from core.chat_engine import ChatEngine, LESSON_STRENGTH_FACTOR
from core.synaptic_network import SynapticNetwork, _synapse_key


# ---------- Volet synaptic : strength_factor ----------

def _fresh_net():
    SynapticNetwork.reset_singleton()
    return SynapticNetwork()


def test_factor_defaut_retrocompatible():
    # strength_factor=1.0 doit donner EXACTEMENT le meme poids que l'ancien appel.
    # Deux paires DISTINCTES sur le MEME reseau frais (la normalisation s'applique
    # identiquement aux deux) -> robuste au singleton.
    net = _fresh_net()
    nids = net._extract_and_ensure("alphaword betaword gammaword deltaword", node_type="t", max_concepts=4)
    if len(nids) < 4:
        pytest.skip("extraction insuffisante")
    for nid in nids:
        net.nodes[nid]["energy"] = 1.0
    net.hebbian_strengthen(nids[0], nids[1])                       # ancien appel (sans factor)
    net.hebbian_strengthen(nids[2], nids[3], strength_factor=1.0)  # factor par defaut explicite
    w_old = net.synapses[_synapse_key(nids[0], nids[1])]["weight"]
    w_new = net.synapses[_synapse_key(nids[2], nids[3])]["weight"]
    assert w_old == w_new, f"defaut casse la retrocompat: {w_old} != {w_new}"


def test_factor_clampe_pas_d_overflow():
    net = _fresh_net()
    nids = net._extract_and_ensure("deltaword epsilonword", node_type="t", max_concepts=2)
    if len(nids) < 2:
        pytest.skip("extraction insuffisante")
    for nid in nids:
        net.nodes[nid]["energy"] = 1.0
    net.hebbian_strengthen(nids[0], nids[1], strength_factor=100.0)  # clampe a 4
    w = net.synapses[_synapse_key(nids[0], nids[1])]["weight"]
    assert 0.0 <= w <= 1.0, f"poids hors bornes: {w}"


# ---------- Volet chat : commande !grave ----------

@pytest.fixture(autouse=True)
def reset_engine():
    ChatEngine.reset_singleton()
    yield
    ChatEngine.reset_singleton()


@pytest.fixture
def engine():
    with patch.object(ChatEngine, "_load"):
        e = ChatEngine()
    return e


class _FakeCortex:
    def __init__(self, n_concepts=3):
        self.extract_text = None
        self.strengthen_calls = []
        self._n = n_concepts

    def _extract_and_ensure(self, text, node_type=None, max_concepts=5):
        self.extract_text = text
        return [f"n:{i}" for i in range(self._n)]

    def hebbian_strengthen(self, src, tgt, success=True, context="", strength_factor=1.0):
        self.strengthen_calls.append((src, tgt, context, strength_factor))


@pytest.mark.asyncio
async def test_grave_la_derniere_reponse(engine, monkeypatch):
    import core.synaptic_network as sn
    fake = _FakeCortex(n_concepts=3)
    monkeypatch.setattr(sn, "cortex", fake)
    traced = {}
    monkeypatch.setattr(engine, "_trace_lesson", lambda lesson, nids: traced.update({"lesson": lesson, "nids": nids}))

    engine.messages.append({"role": "user", "content": "Quel enseignement retiens-tu ?"})
    engine.messages.append({"role": "assistant", "content":
        "Le nombre d'or phi est l'attracteur stable des rapports de Fibonacci."})

    res = await engine._execute_command("grave", [])
    assert "gravee" in res.lower()
    # 3 concepts -> C(3,2) = 3 paires, toutes au taux FORT
    assert len(fake.strengthen_calls) == 3
    assert all(c[3] == LESSON_STRENGTH_FACTOR for c in fake.strengthen_calls)
    assert all(c[2] == "lesson_certified" for c in fake.strengthen_calls)
    # la lecon gravee = la derniere reponse de Promethee
    assert "phi" in fake.extract_text.lower()
    assert traced.get("lesson", "").lower().startswith("le nombre d'or")


@pytest.mark.asyncio
async def test_grave_texte_fourni_par_le_professeur(engine, monkeypatch):
    import core.synaptic_network as sn
    fake = _FakeCortex(n_concepts=4)
    monkeypatch.setattr(sn, "cortex", fake)
    monkeypatch.setattr(engine, "_trace_lesson", lambda lesson, nids: None)

    args = "la suite alternee diverge lentement vers l infini".split()
    res = await engine._execute_command("grave", args)
    assert "gravee" in res.lower()
    # 4 concepts -> C(4,2) = 6 paires
    assert len(fake.strengthen_calls) == 6
    assert "alternee" in fake.extract_text.lower()


@pytest.mark.asyncio
async def test_grave_rejette_si_rien_a_graver(engine, monkeypatch):
    import core.synaptic_network as sn
    monkeypatch.setattr(sn, "cortex", _FakeCortex())
    engine.messages = []  # aucune reponse assistant
    res = await engine._execute_command("grave", [])
    assert "aucune lecon" in res.lower()
