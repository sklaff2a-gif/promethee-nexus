"""Système immunitaire P16 — GC des fichiers-fantômes (2026-05-22).

Prouve :
  1. Un noeud referent-fichier vers un fichier INEXISTANT est purge (+ ses aretes).
  2. Un fichier REEL (core/synaptic_network.py) survit.
  3. Un namespace semantique legitime (reflex:shed = vrai reflexe reptilien) survit.
     -> garde-fou contre l'erreur du 22/05 (ne PAS euthanasier les reflexes).
  4. Un concept abstrait (stabilité) survit.
  5. Mode DRY_RUN : observe et logue sans supprimer.
  6. Flag OFF : no-op total.
"""
import os
os.environ["PROMETHEE_TEST_MODE"] = "1"

import pytest
from core.synaptic_network import SynapticNetwork
from config import Config


@pytest.fixture
def net():
    n = SynapticNetwork()
    n.nodes.clear()
    n.synapses.clear()
    return n


def _node(concept, ntype="memory"):
    return {"concept": concept, "node_type": ntype, "energy": 0.5, "activation_count": 1}


def _seed(net):
    net.nodes["ph1"] = _node("core/fantome_inexistant_xyz.py")   # FANTOME
    net.nodes["real1"] = _node("core/synaptic_network.py")        # fichier REEL
    net.nodes["reflex1"] = _node("reflex:shed", "event")          # namespace LEGITIME
    net.nodes["assoc1"] = _node("stabilité")                      # concept abstrait
    net.synapses["ph1->real1"] = {"source": "ph1", "target": "real1",
                                  "weight": 0.5, "synapse_type": "temporal"}
    net.synapses["assoc1->ph1"] = {"source": "assoc1", "target": "ph1",
                                   "weight": 0.3, "synapse_type": "temporal"}


def test_phantom_purged_legitimate_survive(net, monkeypatch):
    monkeypatch.setattr(Config, "IMMUNE_SYSTEM_ENABLED", True, raising=False)
    monkeypatch.setattr(Config, "IMMUNE_SYSTEM_DRY_RUN", False, raising=False)
    _seed(net)
    removed = net._purge_phantom_referents()
    assert removed == 1
    assert "ph1" not in net.nodes            # fantome eradique
    assert "real1" in net.nodes              # fichier reel intact
    assert "reflex1" in net.nodes            # reflexe reptilien intact (LEÇON 22/05)
    assert "assoc1" in net.nodes             # concept abstrait intact
    assert "ph1->real1" not in net.synapses  # aretes du fantome nettoyees
    assert "assoc1->ph1" not in net.synapses


def test_dry_run_observes_without_deleting(net, monkeypatch):
    monkeypatch.setattr(Config, "IMMUNE_SYSTEM_ENABLED", True, raising=False)
    monkeypatch.setattr(Config, "IMMUNE_SYSTEM_DRY_RUN", True, raising=False)
    _seed(net)
    removed = net._purge_phantom_referents()
    assert removed == 0
    assert "ph1" in net.nodes  # conserve en mode observe


def test_disabled_is_noop(net, monkeypatch):
    monkeypatch.setattr(Config, "IMMUNE_SYSTEM_ENABLED", False, raising=False)
    _seed(net)
    assert net._purge_phantom_referents() == 0
    assert "ph1" in net.nodes
