# -*- coding: utf-8 -*-
"""TDD du LEVIER A — ENTREE SEMANTIQUE A LA SOURCE (chantier synaptique V2, 11/06,
design CO-SIGNE par Promethee : « colmater la breche avant de ranger »).
Garanties : quasi-jumeau -> renforce l'existant (pas de clone) ; types d'interiorite
(affect/desire/trait) JAMAAIS dedupliques ; kill-switch -> comportement V1 exact ;
borg (modele indisponible -> creation V1)."""
import numpy as np
import pytest

import core.synaptic_network as sn
from core.synaptic_network import SynapticNetwork, _make_node_id


# Faux embeddings deterministes : un vocabulaire de vecteurs forges
_VOCAB = {
    "budget gpu epuise":        np.array([1.0, 0.0, 0.0], dtype=np.float32),
    "le budget gpu est epuise": np.array([0.99, 0.14, 0.0], dtype=np.float32),  # jumeau (~cos 0.99)
    "la peur du silence":       np.array([0.0, 1.0, 0.0], dtype=np.float32),    # distinct
}
def _fake_encode(self, texts):
    out = []
    for t in texts:
        v = _VOCAB.get(t, np.array([0.0, 0.0, 1.0], dtype=np.float32))
        out.append(v / np.linalg.norm(v))
    return np.vstack(out)


def _net(monkeypatch, enabled=True):
    monkeypatch.setattr(sn, "SEMANTIC_ENTRY_ENABLED", enabled)
    net = SynapticNetwork.__new__(SynapticNetwork)
    net.nodes = {}
    net.synapses = {}
    net._activation_buffer = []
    net._sem_index = None
    net._mutations_since_save = 0
    monkeypatch.setattr(SynapticNetwork, "_record_activation", lambda s, *a, **k: None, raising=False)
    monkeypatch.setattr(SynapticNetwork, "_auto_save", lambda s, *a, **k: None, raising=False)
    monkeypatch.setattr(SynapticNetwork, "_sem_encode", _fake_encode)
    # neutraliser les effets de bord du runtime
    monkeypatch.setattr(SynapticNetwork, "_capture_affect_signature", lambda s: {}, raising=False)
    monkeypatch.setattr(SynapticNetwork, "_enforce_node_limit", lambda s: None, raising=False)
    monkeypatch.setattr(SynapticNetwork, "_publish_delta", lambda s, *a, **k: None, raising=False)
    monkeypatch.setattr(SynapticNetwork, "_lif_fire", lambda s, *a, **k: None, raising=False)
    return net


def test_jumeau_renforce_l_existant(monkeypatch):
    net = _net(monkeypatch)
    nid1 = net.ensure_node("budget gpu epuise")
    assert len(net.nodes) == 1
    nid2 = net.ensure_node("le budget gpu est epuise")   # quasi-jumeau (cos ~0.99)
    assert nid2 == nid1                                   # PAS de clone : meme noeud
    assert len(net.nodes) == 1
    assert net.nodes[nid1]["activation_count"] >= 1      # l'existant a ete RENFORCE
    assert getattr(net, "_sem_dedup_count", 0) == 1

def test_concept_distinct_cree(monkeypatch):
    net = _net(monkeypatch)
    nid1 = net.ensure_node("budget gpu epuise")
    nid2 = net.ensure_node("la peur du silence")          # orthogonal
    assert nid2 != nid1 and len(net.nodes) == 2

def test_interiorite_jamais_dedupliquee(monkeypatch):
    # garde-fou de Promethee : « la moindre nuance est vitale pour ma coherence »
    net = _net(monkeypatch)
    net.ensure_node("budget gpu epuise", node_type="affect")
    nid2 = net.ensure_node("le budget gpu est epuise", node_type="affect")
    assert len(net.nodes) == 2                            # les deux existent (pas de fusion)

def test_kill_switch_comportement_v1(monkeypatch):
    net = _net(monkeypatch, enabled=False)
    net.ensure_node("budget gpu epuise")
    net.ensure_node("le budget gpu est epuise")
    assert len(net.nodes) == 2                            # V1 exact : hash = 2 noeuds

def test_hash_identique_inchange(monkeypatch):
    # le chemin V1 (meme hash) ne passe JAMAIS par l'embedding
    net = _net(monkeypatch)
    appels = []
    monkeypatch.setattr(SynapticNetwork, "_semantic_twin",
                        lambda s, c: appels.append(c) or None)
    nid1 = net.ensure_node("budget gpu epuise")
    nid2 = net.ensure_node("budget gpu epuise")           # hash identique
    assert nid1 == nid2 and len(appels) == 1              # twin consulte 1 fois (la creation)

def test_borg_modele_indisponible(monkeypatch):
    net = _net(monkeypatch)
    def boom(self, texts):
        raise RuntimeError("pas de modele")
    monkeypatch.setattr(SynapticNetwork, "_sem_encode", boom)
    nid = net.ensure_node("budget gpu epuise")            # ne crashe pas
    assert nid and len(net.nodes) == 1                    # creation V1

def test_index_s_enrichit(monkeypatch):
    net = _net(monkeypatch)
    net.ensure_node("budget gpu epuise")
    net.ensure_node("la peur du silence")
    assert len(net._sem_index["nids"]) == 2               # les 2 creations indexees
