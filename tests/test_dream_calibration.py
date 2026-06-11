# -*- coding: utf-8 -*-
"""TDD de la CALIBRATION DU REVE (atelier du reve 11/06, CO-SIGNE, zone protegee ouverte
par JM). « Creer moins, laisser murir » : semis /5 (hasard pur conserve), grace de 10
cycles (protegee du decay/pruning/cap), expiration -> mort naturelle. Kill-switch = V1."""
import time

import pytest

import core.synaptic_network as sn
from core.synaptic_network import SynapticNetwork, _make_synapse


def _syn(src, tgt, w, grace=0, incubated=False, stype="emotional", ctx="dream"):
    s = _make_synapse(src, tgt, w, stype, ctx)
    if grace:
        s["dream_grace"] = grace
    s["is_incubated"] = incubated
    return s


def _net(monkeypatch, enabled=True):
    monkeypatch.setattr(sn, "DREAM_CALIBRATION_ENABLED", enabled)
    monkeypatch.setattr(sn, "SEMANTIC_ENTRY_ENABLED", False)
    net = SynapticNetwork.__new__(SynapticNetwork)
    net.nodes = {}
    net.synapses = {}
    net._activation_buffer = []
    net._last_dream_time = time.time() - 86400   # 1 jour -> decay plein
    net._mutations_since_save = 0
    for m in ("_publish_delta", "_record_activation", "_auto_save", "_lif_fire"):
        monkeypatch.setattr(SynapticNetwork, m, lambda s, *a, **k: None, raising=False)
    monkeypatch.setattr(SynapticNetwork, "_create_meta_concepts", lambda s: 0, raising=False)
    monkeypatch.setattr(SynapticNetwork, "resonance_audit", lambda s: {}, raising=False)
    return net


def _add_nodes(net, n, energy=0.8, act=1):
    for i in range(n):
        net.nodes[f"n{i}"] = {
            "id": f"n{i}", "concept": f"concept {i}", "node_type": "memory",
            "affect": {}, "dimensions": {}, "activation_count": act,
            "last_activated": time.time(), "created_at": time.time(), "energy": energy,
        }


# ─── le cap de saturation epargne la grace ───
def test_cap_epargne_les_pousses_en_grace(monkeypatch):
    net = _net(monkeypatch)
    monkeypatch.setattr(sn, "MAX_SYNAPSES", 2)
    net.synapses = {
        "a->b": _syn("a", "b", 0.05, grace=5),    # pousse protegee
        "c->d": _syn("c", "d", 0.06),             # commun faible
        "e->f": _syn("e", "f", 0.9),              # fort
    }
    net._enforce_synapse_limit()
    assert "a->b" in net.synapses                  # la grace a survecu au couperet
    assert "c->d" not in net.synapses              # le commun faible est parti

def test_cap_v19_incubation_toujours_protegee(monkeypatch):
    net = _net(monkeypatch)
    monkeypatch.setattr(sn, "MAX_SYNAPSES", 1)
    net.synapses = {
        "a->b": _syn("a", "b", 0.05, incubated=True),
        "c->d": _syn("c", "d", 0.06),
    }
    net._enforce_synapse_limit()
    assert "a->b" in net.synapses                  # non-regression Sanctuaire

# ─── le dream : decay gele pendant la grace, decrement, expiration ───
def test_grace_gele_le_decay_et_decremente(monkeypatch):
    net = _net(monkeypatch)
    _add_nodes(net, 2, energy=0.0)                 # 0 energie -> aucun semis nouveau
    net.synapses = {"n0->n1": _syn("n0", "n1", 0.08, grace=3)}
    net.dream_consolidation()
    s = net.synapses["n0->n1"]
    assert s["dream_grace"] == 2                   # la grace s'use d'un cycle
    assert s["weight"] == pytest.approx(0.08)      # decay GELE
    # et elle n'a pas ete prunee malgre w < seuil
    assert "n0->n1" in net.synapses

def test_grace_expiree_mort_naturelle(monkeypatch):
    net = _net(monkeypatch)
    _add_nodes(net, 2, energy=0.0)
    net.synapses = {"n0->n1": _syn("n0", "n1", 0.08, grace=0)}   # grace epuisee
    net.dream_consolidation()
    assert "n0->n1" not in net.synapses            # regime commun -> prunee (jamais renforcee)

# ─── le semis calibre : 5x moins, grace a la naissance ───
def test_semis_calibre_et_grace_a_la_naissance(monkeypatch):
    net = _net(monkeypatch)
    net._last_dream_time = time.time()             # decay ~0 : observer le semis pur
    _add_nodes(net, 12, energy=0.9, act=1)         # tous actifs, aucun voisin
    monkeypatch.setattr(sn.random, "gauss", lambda a, b: 0.0)
    # random.random() sequence : 0.1 (seme) puis 0.9 (skip) en alternance
    seq = iter([0.1, 0.9] * 200)
    monkeypatch.setattr(sn.random, "random", lambda: next(seq))
    report = net.dream_consolidation()
    crees = [s for s in net.synapses.values() if s.get("context") == "dream"]
    assert report["dream_connections"] == len(crees) and len(crees) > 0
    # le terreau : grace posee a la naissance ; le cycle de naissance en consomme 1
    # (la passe decay du MEME dream decremente) -> 9 restants = 10 cycles au total
    assert all(s.get("dream_grace") == sn.DREAM_GRACE_CYCLES - 1 for s in crees)
    # la calibration a bien SAUTE des tirages (sans elle : 2 tentatives par noeud actif)
    actifs = max(1, int(12 * 0.3))
    assert len(crees) < actifs * 2                 # strictement moins que le plein debit

def test_kill_switch_v1_exact(monkeypatch):
    net = _net(monkeypatch, enabled=False)
    net._last_dream_time = time.time()             # decay ~0 (sinon V1 prune ses propres
    _add_nodes(net, 12, energy=0.9, act=1)         # creations dans le MEME cycle — le vice)
    monkeypatch.setattr(sn.random, "gauss", lambda a, b: 0.0)
    monkeypatch.setattr(sn.random, "random", lambda: 0.99)   # aurait tout skippe si ON
    report = net.dream_consolidation()
    crees = [s for s in net.synapses.values() if s.get("context") == "dream"]
    assert len(crees) > 0                          # plein debit (pas de calibration)
    assert all("dream_grace" not in s or not s["dream_grace"] for s in crees)  # pas de grace
