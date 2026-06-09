# -*- coding: utf-8 -*-
"""TDD de SYNAPTIC_RESONANCE_AUDIT v2 (atelier Darwin-Godel, 'Dynamique de Morphogenese').
Audit READ-ONLY de la plasticite affect<->memoire : distingue courbe d'apprentissage saine
(dispersion ∝ usage) d'une derive erratique. Ne doit modifier AUCUN poids."""
import copy
from core.synaptic_network import SynapticNetwork


def _net(affmem_specs):
    """affmem_specs : liste de (weight, formation_count). Construit 1 noeud affect +
    N noeuds memoire, chacun relie au noeud affect (N synapses affect<->memoire)."""
    n = SynapticNetwork.__new__(SynapticNetwork)
    n.nodes = {"aff": {"node_type": "affect"}}
    n.synapses = {}
    n._last_resonance_audit = None
    for i, (w, fc) in enumerate(affmem_specs):
        mid = "mem%d" % i
        n.nodes[mid] = {"node_type": "memory"}
        n.synapses["aff->%s" % mid] = {"source": "aff", "target": mid,
                                       "weight": w, "formation_count": fc}
    return n


def test_apprentissage_sain_dispersion_croit_avec_usage():
    # usage faible (fc=1) -> poids serres ; usage fort (fc=10) -> poids disperses
    specs = [(0.1, 1)] * 12 + [(0.05 + 0.07 * j, 10) for j in range(12)]
    r = _net(specs).resonance_audit()
    assert r["plasticite_couplee_usage"] is True
    assert r["health"] == "sain_apprentissage"
    assert r["var_usage_fort"] > r["var_usage_faible"]

def test_derive_erratique_dispersion_decouplee():
    # usage faible disperse, usage fort serre -> derive a surveiller
    specs = [(0.05 + 0.07 * j, 1) for j in range(12)] + [(0.5, 10)] * 12
    r = _net(specs).resonance_audit()
    assert r["plasticite_couplee_usage"] is False
    assert r["health"] == "derive_a_surveiller"

def test_promotion_veilleur_alerte_sur_derive():
    # PROMOTION : sur derive, le veilleur leve l'alerte (warning + drapeau)
    specs = [(0.05 + 0.07 * j, 1) for j in range(12)] + [(0.5, 10)] * 12
    net = _net(specs)
    r = net.resonance_audit()
    assert r.get("alert") is True
    assert net._plasticite_drift_alert is True

def test_promotion_pas_d_alerte_si_sain():
    specs = [(0.1, 1)] * 12 + [(0.05 + 0.07 * j, 10) for j in range(12)]
    net = _net(specs)
    r = net.resonance_audit()
    assert "alert" not in r
    assert net._plasticite_drift_alert is False

def test_indetermine_si_trop_peu_de_liens():
    r = _net([(0.5, 3)] * 5).resonance_audit()   # < 20
    assert r["health"] == "indetermine"
    assert r["plasticite_couplee_usage"] is None

def test_ratio_R_ponts_forts():
    # 10 affmem dont 4 forts (>=0.9) ; R = 4 forts affmem / 4 forts inter = 1.0
    specs = [(0.95, 5)] * 4 + [(0.3, 2)] * 16
    r = _net(specs).resonance_audit()
    assert r["ponts_forts_inter"] == 4
    assert r["R"] == 1.0

def test_audit_est_read_only():
    net = _net([(0.4, 3)] * 22)
    avant = copy.deepcopy(net.synapses)
    net.resonance_audit()
    assert net.synapses == avant   # AUCUN poids modifie

def test_kill_switch(monkeypatch):
    monkeypatch.setenv("RESONANCE_AUDIT_ENABLED", "0")
    assert _net([(0.4, 3)] * 22).resonance_audit() == {}

def test_delta_R_entre_deux_audits():
    net = _net([(0.95, 5)] * 4 + [(0.3, 2)] * 16)   # R=1.0
    net.resonance_audit()
    # on retire les liens forts -> R change
    for k in [k for k in net.synapses if net.synapses[k]["weight"] >= 0.9]:
        net.synapses[k]["weight"] = 0.3
    r2 = net.resonance_audit()
    assert r2["delta_R"] != 0.0

def test_borg_sur_synapse_malformee():
    net = _net([(0.4, 3)] * 22)
    net.synapses["casse"] = {"source": "inconnu"}   # pas de target/type
    r = net.resonance_audit()
    assert "health" in r   # ne crashe pas, ignore la synapse orpheline
