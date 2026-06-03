# -*- coding: utf-8 -*-
"""Tests V19.0 — Sanctuaire des Ebauches (Incubateur Cinetique).

Valide : passeport (formation>=10, w<0.5), revocation (w>=0.5), triple bouclier
(decay/4, immunite couperet, immunite structural_growth), cap a 3000.
Isolation : on sauvegarde/restaure nodes+synapses et on neutralise _auto_save.
"""
import time
import pytest

from core import synaptic_network as sn
from core.synaptic_network import (
    SynapticNetwork, _make_synapse, _make_node,
    INCUBATION_FORMATION_THRESHOLD, INCUBATION_MATURITY_WEIGHT,
    INCUBATION_DECAY_DIVISOR,
)


@pytest.fixture
def net(monkeypatch):
    n = SynapticNetwork()
    saved_nodes, saved_syn, saved_dream = dict(n.nodes), dict(n.synapses), n._last_dream_time
    monkeypatch.setattr(n, "_auto_save", lambda *a, **k: None)
    n.nodes = {}
    n.synapses = {}
    yield n
    n.nodes, n.synapses, n._last_dream_time = saved_nodes, saved_syn, saved_dream


def _syn(net, key, src, tgt, w, formation):
    s = _make_synapse(src, tgt, w)
    s["formation_count"] = formation
    net.synapses[key] = s
    return s


class TestStructure:
    def test_make_synapse_incubated_false_par_defaut(self):
        assert _make_synapse("a", "b")["is_incubated"] is False


class TestDouane:
    def test_passeport_revocation_filtre(self, net):
        net.nodes = {f"n{i}": _make_node(f"concept{i}") for i in range(4)}
        _syn(net, "n0->n1", "n0", "n1", 0.20, 15)   # ebauche prouvee -> incubee
        _syn(net, "n1->n2", "n1", "n2", 0.20, 5)    # formation faible -> NON incubee
        _syn(net, "n0->n2", "n0", "n2", 0.60, 15)   # mature (w>=0.5) -> NON incubee (revoquee)
        net._last_dream_time = time.time() - 86400
        net.dream_consolidation()
        assert net.synapses["n0->n1"]["is_incubated"] is True
        if "n1->n2" in net.synapses:
            assert net.synapses["n1->n2"]["is_incubated"] is False
        if "n0->n2" in net.synapses:
            assert net.synapses["n0->n2"]["is_incubated"] is False


class TestBouclier:
    def test_decay_divise_par_4(self, net):
        net.nodes = {"n0": _make_node("c0"), "n1": _make_node("c1")}
        _syn(net, "n0->n1", "n0", "n1", 0.30, 15)   # incubee
        net._last_dream_time = time.time() - 86400   # 1 jour -> decay normal 0.02
        net.dream_consolidation()
        # incubee : decay = 0.02/4 = 0.005 -> ~0.295 (aurait ete 0.28 sans bouclier)
        assert net.synapses["n0->n1"]["weight"] > 0.29

    def test_incubee_jamais_prunee_sous_seuil(self, net):
        net.nodes = {"n0": _make_node("c0"), "n1": _make_node("c1")}
        # incubee a 0.085 (juste au-dessus du seuil 0.08) : meme si elle descend
        # sous 0.08 au decay, le bouclier l'empeche d'etre prunee.
        _syn(net, "n0->n1", "n0", "n1", 0.085, 15)
        net._last_dream_time = time.time() - 5 * 86400  # 5 jours : decay normal la tuerait
        net.dream_consolidation()
        assert "n0->n1" in net.synapses  # survit grace au bouclier (decay/4 + pas de prune)
        assert net.synapses["n0->n1"]["is_incubated"] is True

    def test_couperet_immunise_les_incubees(self, net, monkeypatch):
        monkeypatch.setattr(sn, "MAX_SYNAPSES", 4)
        # 7 synapses : 3 incubees faibles + 4 communes. to_remove = 3 -> doit
        # frapper les 3 communes les plus faibles, JAMAIS les incubees.
        for i in range(3):
            s = _syn(net, f"inc{i}", f"a{i}", f"b{i}", 0.05, 15)
            s["is_incubated"] = True
        for i, w in enumerate([0.10, 0.11, 0.12, 0.90]):
            _syn(net, f"com{i}", f"c{i}", f"d{i}", w, 1)
        net._enforce_synapse_limit()
        restantes = set(net.synapses.keys())
        for i in range(3):
            assert f"inc{i}" in restantes        # incubees protegees
        assert "com3" in restantes               # la commune forte survit
        assert len(net.synapses) == 4            # ramene a MAX

    def test_structural_growth_epargne_les_incubees(self, net):
        # une incubee a poids < 0.1 ne doit PAS etre candidate au remplacement
        s = _syn(net, "inc", "a", "b", 0.05, 15)
        s["is_incubated"] = True
        _syn(net, "com", "c", "d", 0.05, 1)
        # reproduit la selection de structural_growth (network_full)
        weak = [(k, x["weight"]) for k, x in net.synapses.items()
                if x["weight"] < 0.1 and not x.get("is_incubated")]
        keys = [k for k, _ in weak]
        assert "com" in keys
        assert "inc" not in keys


class TestCap:
    def test_cap_ejecte_les_plus_faibles(self, net, monkeypatch):
        monkeypatch.setattr(sn, "INCUBATION_CAP", 2)
        net.nodes = {f"n{i}": _make_node(f"c{i}") for i in range(5)}
        for i, w in enumerate([0.12, 0.22, 0.32, 0.42]):
            _syn(net, f"n{i}->n{i+1}", f"n{i}", f"n{i+1}", w, 15)
        net._last_dream_time = time.time() - 1
        net.dream_consolidation()
        incub = [k for k, s in net.synapses.items() if s.get("is_incubated")]
        assert len(incub) <= 2  # cap respecte : seules les 2 plus fortes gardent le passeport


class TestMicroDopage:
    def test_ebauche_incubee_dopee(self, net):
        net.nodes = {"n0": _make_node("c0"), "n1": _make_node("c1")}
        _syn(net, "n0->n1", "n0", "n1", 0.20, 15)   # incubee immature
        net._last_dream_time = time.time() - 1        # decay negligeable
        net.dream_consolidation()
        # +0.08 de pousse (decay/4 negligeable) -> ~0.28
        assert net.synapses["n0->n1"]["weight"] > 0.26

    def test_tissu_sauvage_non_dope(self, net):
        net.nodes = {"n0": _make_node("c0"), "n1": _make_node("c1")}
        _syn(net, "n0->n1", "n0", "n1", 0.20, 3)    # formation faible -> NON incubee
        net._last_dream_time = time.time() - 1
        net.dream_consolidation()
        # pas de passeport -> pas de dopage : le poids ne monte pas
        assert net.synapses.get("n0->n1", {}).get("weight", 1.0) <= 0.21

    def test_verrou_de_retenue_exclusif(self, net):
        net.nodes = {"n0": _make_node("c0"), "n1": _make_node("c1")}
        _syn(net, "n0->n1", "n0", "n1", 0.45, 15)   # incubee, proche maturite
        net._last_dream_time = time.time() - 1
        net.dream_consolidation()
        w = net.synapses["n0->n1"]["weight"]
        # 0.45 + 0.08 = 0.53 ; PAS de x1.05 en plus (mutuellement exclusif -> sinon 0.556)
        assert 0.52 <= w <= 0.54

    def test_au_dela_de_05_passe_au_x105(self, net):
        net.nodes = {"n0": _make_node("c0"), "n1": _make_node("c1")}
        # deja mature (0.6) ET formation elevee : la douane revoque (w>=0.5),
        # le dopage ne s'applique pas, le x1.05 natif prend le relais.
        _syn(net, "n0->n1", "n0", "n1", 0.60, 15)
        net._last_dream_time = time.time() - 1
        net.dream_consolidation()
        assert net.synapses["n0->n1"]["is_incubated"] is False
        assert net.synapses["n0->n1"]["weight"] > 0.60   # x1.05 applique
