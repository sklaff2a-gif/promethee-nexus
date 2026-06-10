# -*- coding: utf-8 -*-
"""TDD de la VISION SYNAPTIQUE REPRESENTATIVE (10/06, demande JM) : le graph doit correler
avec la realite et rendre les PATHOLOGIES visibles (agonie, orphelins) pour servir de
source de detection. Methodes READ-ONLY (aucun poids touche — zone protegee respectee)."""
import pytest

from core.synaptic_network import SynapticNetwork


def _reseau():
    """Mini-reseau forge : 6 noeuds, 5 synapses (2 en agonie), 1 orphelin."""
    net = SynapticNetwork.__new__(SynapticNetwork)
    net.nodes = {
        "hub":  {"concept": "le hub", "node_type": "event", "energy": 0.9, "activation_count": 50, "affect": {"valence": 0.2}},
        "a":    {"concept": "noeud a", "node_type": "event", "energy": 0.5, "activation_count": 5, "affect": {}},
        "b":    {"concept": "noeud b", "node_type": "event", "energy": 0.4, "activation_count": 3, "affect": {}},
        "c":    {"concept": "noeud c", "node_type": "lesson", "energy": 0.2, "activation_count": 1, "affect": {}},
        "d":    {"concept": "noeud d", "node_type": "event", "energy": 0.1, "activation_count": 1, "affect": {}},
        "orph": {"concept": "l oublie", "node_type": "chat", "energy": 0.05, "activation_count": 0, "affect": {}},
    }
    net.synapses = {
        "k1": {"source": "hub", "target": "a", "weight": 0.85, "synapse_type": "hebbian"},
        "k2": {"source": "hub", "target": "b", "weight": 0.45, "synapse_type": "hebbian"},
        "k3": {"source": "hub", "target": "c", "weight": 0.15, "synapse_type": "causal"},
        "k4": {"source": "a",   "target": "d", "weight": 0.05, "synapse_type": "hebbian"},   # agonie
        "k5": {"source": "b",   "target": "c", "weight": 0.09, "synapse_type": "emotional"}, # agonie
    }
    return net


# ─── health_stats : la verite des comptes + les marqueurs de pathologie ───
def test_health_stats_comptes_exacts():
    s = _reseau().health_stats()
    assert s["total_nodes"] == 6 and s["total_synapses"] == 5

def test_health_stats_agonie():
    s = _reseau().health_stats()
    assert s["bandes_poids"]["agonie"] == 2          # 0.05 et 0.09
    assert s["pct_agonie"] == 40.0                   # 2/5

def test_health_stats_orphelins():
    s = _reseau().health_stats()
    assert s["orphelins"] == 1                       # 'orph' n'a aucun lien
    assert abs(s["pct_orphelins"] - 16.7) < 0.1

def test_health_stats_hubs():
    s = _reseau().health_stats()
    assert s["hubs"][0]["id"] == "hub" and s["hubs"][0]["degre"] == 3

def test_health_stats_read_only():
    net = _reseau()
    avant = {k: dict(v) for k, v in net.synapses.items()}
    net.health_stats()
    assert {k: dict(v) for k, v in net.synapses.items()} == avant   # AUCUN poids touche


# ─── graph_sample : la maladie VISIBLE dans l'echantillon ───
def test_sample_contient_toutes_les_strates():
    out = _reseau().graph_sample(max_nodes=6, max_links=10)
    strates = {n["strate"] for n in out["nodes"]}
    assert "hub" in strates and "agonie" in strates and "orphelin" in strates

def test_sample_orphelin_present():
    out = _reseau().graph_sample(max_nodes=6, max_links=10)
    assert any(n["id"] == "orph" for n in out["nodes"])   # l'oublie n'est plus invisible

def test_sample_liens_valides_et_verite_des_comptes():
    out = _reseau().graph_sample(max_nodes=6, max_links=10)
    ids = {n["id"] for n in out["nodes"]}
    assert all(l["source"] in ids and l["target"] in ids for l in out["links"])
    info = out["sample_info"]
    assert info["reels_nodes"] == 6 and info["reels_synapses"] == 5   # les comptes REELS voyagent

def test_sample_bornes_respectees():
    out = _reseau().graph_sample(max_nodes=3, max_links=2)
    assert len(out["nodes"]) <= 3      # budget STRICT
    assert len(out["links"]) <= 2

def test_sample_read_only():
    net = _reseau()
    avant = {k: dict(v) for k, v in net.synapses.items()}
    net.graph_sample()
    assert {k: dict(v) for k, v in net.synapses.items()} == avant

def test_sample_stable_dans_la_journee():
    # seed = date du jour -> deux appels identiques (pas de scintillement UI)
    n1 = _reseau().graph_sample(max_nodes=4, max_links=5)
    n2 = _reseau().graph_sample(max_nodes=4, max_links=5)
    assert [n["id"] for n in n1["nodes"]] == [n["id"] for n in n2["nodes"]]
