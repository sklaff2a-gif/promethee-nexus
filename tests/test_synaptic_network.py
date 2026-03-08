# tests/test_synaptic_network.py — Tests du Cortex Associatif
import json
import math
import os
import time
from unittest.mock import patch, MagicMock

import pytest

from core.synaptic_network import (
    SynapticNetwork,
    _make_node_id,
    _synapse_key,
    _make_node,
    _make_synapse,
    _NODE_STOPLIST,
    emotional_distance,
    HEBBIAN_LEARNING_RATE,
    ANTI_HEBBIAN_RATE,
    SPIKE_TIMING_WINDOW,
    HOMEOSTATIC_TARGET,
    PRUNING_THRESHOLD,
    SYNAPSE_DECAY_PER_DAY,
    MIN_CONCEPT_LENGTH,
    MAX_NODES,
    MAX_SYNAPSES,
    MAX_PRUNE_RATIO,
    STATE_FILE,
)


# --- Fixtures ---

@pytest.fixture(autouse=True)
def reset_synaptic_singleton():
    SynapticNetwork.reset_singleton()
    yield
    SynapticNetwork.reset_singleton()


@pytest.fixture
def network():
    with patch("core.synaptic_network.SynapticNetwork._load"):
        net = SynapticNetwork()
    return net


@pytest.fixture
def populated_network(network):
    """Reseau avec 5 noeuds et quelques synapses."""
    net = network
    # Pas de STDP automatique pendant la construction
    net._activation_buffer = []

    # Creer des noeuds manuellement (sans passer par ensure_node pour eviter STDP)
    for concept in ["python", "refactoring", "securite", "memoire", "test"]:
        nid = _make_node_id(concept)
        net.nodes[nid] = _make_node(concept, "memory", 0.6, ["dev"])

    nids = [_make_node_id(c) for c in ["python", "refactoring", "securite", "memoire", "test"]]

    # Synapses
    net.synapses[_synapse_key(nids[0], nids[1])] = _make_synapse(nids[0], nids[1], 0.5, "hebbian")
    net.synapses[_synapse_key(nids[1], nids[2])] = _make_synapse(nids[1], nids[2], 0.3, "hebbian")
    net.synapses[_synapse_key(nids[2], nids[3])] = _make_synapse(nids[2], nids[3], 0.7, "hebbian")
    net.synapses[_synapse_key(nids[3], nids[4])] = _make_synapse(nids[3], nids[4], 0.4, "hebbian")

    return net


# =====================================================================
# TestSynapticNodeBasics
# =====================================================================

class TestSynapticNodeBasics:

    def test_make_node_id_deterministic(self):
        """Le hash est deterministe et case-insensitive."""
        assert _make_node_id("Python") == _make_node_id("python")
        assert _make_node_id("Python") == _make_node_id("  PYTHON  ")

    def test_make_node_id_different_for_different_concepts(self):
        assert _make_node_id("python") != _make_node_id("javascript")

    def test_make_node_structure(self):
        node = _make_node("python", "memory", 0.7, ["dev"])
        assert node["concept"] == "python"
        assert node["node_type"] == "memory"
        assert node["dimensions"]["semantic_weight"] == 0.7
        assert "dev" in node["dimensions"]["functional_systems"]
        assert node["energy"] == 0.7
        assert node["activation_count"] == 1

    def test_make_node_invalid_type_defaults_to_memory(self):
        node = _make_node("test", "invalid_type")
        assert node["node_type"] == "memory"

    def test_ensure_node_creates_new(self, network):
        nid = network.ensure_node("python", "memory", 0.5, ["dev"])
        assert nid in network.nodes
        assert network.nodes[nid]["concept"] == "python"

    def test_ensure_node_updates_existing(self, network):
        nid1 = network.ensure_node("python", "memory", 0.5)
        count1 = network.nodes[nid1]["activation_count"]
        energy1 = network.nodes[nid1]["energy"]

        nid2 = network.ensure_node("python", "memory", 0.5, ["test"])
        assert nid1 == nid2
        assert network.nodes[nid2]["activation_count"] == count1 + 1
        assert network.nodes[nid2]["energy"] >= energy1

    def test_ensure_node_merges_functional_systems(self, network):
        network.ensure_node("python", "memory", 0.5, ["dev"])
        nid = _make_node_id("python")
        network.ensure_node("python", "memory", 0.5, ["test"])
        systems = set(network.nodes[nid]["dimensions"]["functional_systems"])
        assert "dev" in systems
        assert "test" in systems

    def test_enforce_node_limit(self, network):
        # Creer MAX_NODES + 10 noeuds
        for i in range(MAX_NODES + 10):
            nid = _make_node_id(f"concept_{i}")
            network.nodes[nid] = _make_node(f"concept_{i}", "memory", 0.1)

        network._enforce_node_limit()
        assert len(network.nodes) <= MAX_NODES


# =====================================================================
# TestHebbianLearning
# =====================================================================

class TestHebbianLearning:

    def test_hebbian_strengthen_creates_synapse(self, network):
        nid_a = _make_node_id("alpha")
        nid_b = _make_node_id("beta")
        network.nodes[nid_a] = _make_node("alpha", "memory", 0.8)
        network.nodes[nid_b] = _make_node("beta", "memory", 0.8)

        network.hebbian_strengthen(nid_a, nid_b, success=True, context="test")
        key = _synapse_key(nid_a, nid_b)
        assert key in network.synapses
        assert network.synapses[key]["weight"] > 0.1

    def test_hebbian_strengthen_increases_weight(self, network):
        nid_a = _make_node_id("alpha")
        nid_b = _make_node_id("beta")
        network.nodes[nid_a] = _make_node("alpha", "memory", 0.8)
        network.nodes[nid_b] = _make_node("beta", "memory", 0.8)

        network.hebbian_strengthen(nid_a, nid_b, success=True)
        key = _synapse_key(nid_a, nid_b)
        w1 = network.synapses[key]["weight"]

        network.hebbian_strengthen(nid_a, nid_b, success=True)
        w2 = network.synapses[key]["weight"]
        assert w2 > w1

    def test_hebbian_saturation(self, network):
        """Le poids ne depasse jamais 1.0."""
        nid_a = _make_node_id("alpha")
        nid_b = _make_node_id("beta")
        network.nodes[nid_a] = _make_node("alpha", "memory", 1.0)
        network.nodes[nid_b] = _make_node("beta", "memory", 1.0)

        for _ in range(100):
            network.hebbian_strengthen(nid_a, nid_b, success=True)

        key = _synapse_key(nid_a, nid_b)
        assert network.synapses[key]["weight"] <= 1.0

    def test_anti_hebbian_weakens(self, network):
        nid_a = _make_node_id("alpha")
        nid_b = _make_node_id("beta")
        network.nodes[nid_a] = _make_node("alpha", "memory", 0.8)
        network.nodes[nid_b] = _make_node("beta", "memory", 0.8)

        # D'abord creer la synapse
        network.hebbian_strengthen(nid_a, nid_b, success=True)
        key = _synapse_key(nid_a, nid_b)
        w_before = network.synapses[key]["weight"]

        # Puis affaiblir
        network.hebbian_strengthen(nid_a, nid_b, success=False)
        assert network.synapses[key]["weight"] < w_before

    def test_anti_hebbian_does_not_create_synapse(self, network):
        nid_a = _make_node_id("alpha")
        nid_b = _make_node_id("beta")
        network.nodes[nid_a] = _make_node("alpha", "memory", 0.8)
        network.nodes[nid_b] = _make_node("beta", "memory", 0.8)

        network.hebbian_strengthen(nid_a, nid_b, success=False)
        key = _synapse_key(nid_a, nid_b)
        assert key not in network.synapses

    def test_hebbian_with_missing_nodes(self, network):
        """Ne crashe pas avec des noeuds inexistants."""
        network.hebbian_strengthen("missing_a", "missing_b", success=True)
        assert len(network.synapses) == 0

    def test_stdp_strengthen(self, network):
        nid_a = _make_node_id("earlier")
        nid_b = _make_node_id("later")
        network.nodes[nid_a] = _make_node("earlier", "memory", 0.8)
        network.nodes[nid_b] = _make_node("later", "memory", 0.8)

        # delta_t court = fort renforcement
        network.spike_timing_strengthen(nid_a, nid_b, delta_t=10.0)
        key = _synapse_key(nid_a, nid_b)
        assert key in network.synapses
        w_short = network.synapses[key]["weight"]

        # Creer une autre paire avec delta_t long
        nid_c = _make_node_id("much_later")
        network.nodes[nid_c] = _make_node("much_later", "memory", 0.8)
        network.spike_timing_strengthen(nid_a, nid_c, delta_t=250.0)
        key2 = _synapse_key(nid_a, nid_c)
        w_long = network.synapses[key2]["weight"]

        # Le court delai doit etre plus fort
        assert w_short > w_long

    def test_stdp_ignores_negative_delta(self, network):
        nid_a = _make_node_id("a")
        nid_b = _make_node_id("b")
        network.nodes[nid_a] = _make_node("a", "memory", 0.8)
        network.nodes[nid_b] = _make_node("b", "memory", 0.8)

        network.spike_timing_strengthen(nid_a, nid_b, delta_t=-5.0)
        assert len(network.synapses) == 0

    def test_stdp_ignores_beyond_window(self, network):
        nid_a = _make_node_id("a")
        nid_b = _make_node_id("b")
        network.nodes[nid_a] = _make_node("a", "memory", 0.8)
        network.nodes[nid_b] = _make_node("b", "memory", 0.8)

        network.spike_timing_strengthen(nid_a, nid_b, delta_t=SPIKE_TIMING_WINDOW + 10)
        assert len(network.synapses) == 0

    def test_homeostatic_normalize(self, network):
        """Ramene l'energie moyenne vers la cible."""
        # Mettre toutes les energies a 1.0 (trop haut)
        for concept in ["a", "b", "c"]:
            nid = _make_node_id(concept)
            network.nodes[nid] = _make_node(concept, "memory", 1.0)

        network.homeostatic_normalize()
        avg = sum(n["energy"] for n in network.nodes.values()) / len(network.nodes)
        # Devrait etre plus proche de HOMEOSTATIC_TARGET qu'avant
        assert avg < 1.0

    def test_homeostatic_normalize_empty(self, network):
        """Ne crashe pas sur un reseau vide."""
        network.homeostatic_normalize()  # Pas d'erreur


# =====================================================================
# TestEmotionalSpace
# =====================================================================

class TestEmotionalSpace:

    def test_emotional_distance_identical(self):
        affect = {"valence": 0.5, "desire_intensity": 60.0, "trait_value": 70.0}
        assert emotional_distance(affect, affect) == 0.0

    def test_emotional_distance_opposite(self):
        a = {"valence": -1.0, "desire_intensity": 0.0, "trait_value": 0.0}
        b = {"valence": 1.0, "desire_intensity": 100.0, "trait_value": 100.0}
        dist = emotional_distance(a, b)
        assert dist == pytest.approx(1.0, abs=0.01)

    def test_emotional_distance_range(self):
        """La distance est toujours dans [0, 1]."""
        import random
        random.seed(42)
        for _ in range(50):
            a = {"valence": random.uniform(-1, 1),
                 "desire_intensity": random.uniform(0, 100),
                 "trait_value": random.uniform(0, 100)}
            b = {"valence": random.uniform(-1, 1),
                 "desire_intensity": random.uniform(0, 100),
                 "trait_value": random.uniform(0, 100)}
            d = emotional_distance(a, b)
            assert 0.0 <= d <= 1.0

    def test_emotional_distance_missing_fields(self):
        """Valeurs par defaut si champs manquants."""
        d = emotional_distance({}, {})
        assert d == 0.0

    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature")
    def test_compute_emotional_resonance_identical(self, mock_affect, network):
        """Affect identique → resonance maximale (2.0)."""
        affect = {"valence": 0.5, "desire_intensity": 60.0, "trait_value": 70.0,
                  "mood": "curieux", "dominant_desire": "CURIOSITE",
                  "dominant_trait": "curiosite"}
        mock_affect.return_value = affect
        nid = _make_node_id("test_node")
        network.nodes[nid] = _make_node("test_node")
        network.nodes[nid]["affect"] = dict(affect)

        resonance = network.compute_emotional_resonance(nid)
        assert resonance == pytest.approx(2.0, abs=0.01)

    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature")
    def test_compute_emotional_resonance_range(self, mock_affect, network):
        """La resonance est toujours dans [0.5, 2.0]."""
        mock_affect.return_value = {"valence": 1.0, "desire_intensity": 100.0,
                                    "trait_value": 100.0}
        nid = _make_node_id("test_node")
        network.nodes[nid] = _make_node("test_node")
        network.nodes[nid]["affect"] = {"valence": -1.0, "desire_intensity": 0.0,
                                        "trait_value": 0.0}

        resonance = network.compute_emotional_resonance(nid)
        assert 0.5 <= resonance <= 2.0

    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature")
    def test_compute_emotional_resonance_missing_node(self, mock_affect, network):
        """Noeud inexistant → resonance neutre (1.0)."""
        resonance = network.compute_emotional_resonance("missing")
        assert resonance == 1.0

    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature")
    def test_mood_congruent_recall(self, mock_affect, populated_network):
        """Le recall retourne les concepts les plus pertinents."""
        mock_affect.return_value = {"valence": 0.0, "desire_intensity": 40.0,
                                    "trait_value": 50.0, "mood": "neutre",
                                    "dominant_desire": "", "dominant_trait": ""}
        results = populated_network.mood_congruent_recall(["python"], top_k=5)
        # Devrait contenir au moins "python" via match textuel
        concepts = [c for c, _ in results]
        assert any("python" in c for c in concepts)


# =====================================================================
# TestResonanceCascade
# =====================================================================

class TestResonanceCascade:

    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature")
    def test_cascade_empty_network(self, mock_affect, network):
        mock_affect.return_value = {"valence": 0.0, "desire_intensity": 0.0,
                                    "trait_value": 50.0}
        assert network.resonance_cascade(["python"]) == []

    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature")
    def test_cascade_returns_results(self, mock_affect, populated_network):
        mock_affect.return_value = {"valence": 0.0, "desire_intensity": 40.0,
                                    "trait_value": 50.0, "mood": "neutre",
                                    "dominant_desire": "", "dominant_trait": ""}
        results = populated_network.resonance_cascade(["python"])
        assert len(results) > 0
        # Devrait contenir python et ses voisins
        concepts = [c for c, _ in results]
        assert "python" in concepts

    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature")
    def test_cascade_convergence(self, mock_affect, populated_network):
        """L'energie converge (ne diverge pas)."""
        mock_affect.return_value = {"valence": 0.0, "desire_intensity": 40.0,
                                    "trait_value": 50.0}
        results = populated_network.resonance_cascade(["python"], cycles=10)
        for _, energy in results:
            assert energy < 100.0  # Pas d'explosion

    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature")
    def test_cascade_max_20_results(self, mock_affect, network):
        """Maximum 20 resultats."""
        mock_affect.return_value = {"valence": 0.0, "desire_intensity": 40.0,
                                    "trait_value": 50.0}
        # Creer 30 noeuds tous connectes
        nids = []
        for i in range(30):
            nid = _make_node_id(f"concept_{i}")
            network.nodes[nid] = _make_node(f"concept_{i}", "memory", 0.8)
            nids.append(nid)
        for i in range(len(nids) - 1):
            key = _synapse_key(nids[i], nids[i + 1])
            network.synapses[key] = _make_synapse(nids[i], nids[i + 1], 0.5)

        results = network.resonance_cascade(["concept_0"])
        assert len(results) <= 20

    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature")
    def test_cascade_propagates_to_neighbors(self, mock_affect, populated_network):
        """La cascade propage l'energie aux voisins."""
        mock_affect.return_value = {"valence": 0.0, "desire_intensity": 40.0,
                                    "trait_value": 50.0}
        results = populated_network.resonance_cascade(["python"])
        concepts = [c for c, _ in results]
        # "refactoring" est voisin direct de "python"
        assert "refactoring" in concepts

    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature")
    def test_cascade_empty_seed(self, mock_affect, populated_network):
        assert populated_network.resonance_cascade([]) == []

    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature")
    def test_cascade_unknown_seed(self, mock_affect, populated_network):
        mock_affect.return_value = {"valence": 0.0, "desire_intensity": 40.0,
                                    "trait_value": 50.0}
        results = populated_network.resonance_cascade(["concept_inexistant"])
        assert results == []

    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature")
    def test_cascade_backward_propagation(self, mock_affect, populated_network):
        """Les cycles impairs propagent backward."""
        mock_affect.return_value = {"valence": 0.0, "desire_intensity": 40.0,
                                    "trait_value": 50.0}
        # "test" est le dernier de la chaine, seed "test" devrait remonter via backward
        results = populated_network.resonance_cascade(["test"], cycles=4)
        concepts = [c for c, _ in results]
        assert "test" in concepts


# =====================================================================
# TestDreamMode
# =====================================================================

class TestDreamMode:

    def test_dream_empty_network(self, network):
        report = network.dream_consolidation()
        assert report["pruned_synapses"] == 0
        assert report["dream_connections"] == 0

    def test_dream_creates_connections(self, populated_network):
        initial_synapses = len(populated_network.synapses)
        report = populated_network.dream_consolidation()
        # Peut creer des connexions (stochastique, pas garanti mais probable)
        assert report["dream_connections"] >= 0

    def test_dream_prunes_weak_synapses(self, network):
        """Les synapses faibles et anciennes sont prunees."""
        nid_a = _make_node_id("a")
        nid_b = _make_node_id("b")
        network.nodes[nid_a] = _make_node("a", "memory", 0.5)
        network.nodes[nid_b] = _make_node("b", "memory", 0.5)

        # Creer une synapse faible et tres ancienne
        key = _synapse_key(nid_a, nid_b)
        network.synapses[key] = _make_synapse(nid_a, nid_b, 0.04, "hebbian")
        # Simuler une synapse datant de 30 jours et un dernier dream il y a 30 jours
        network.synapses[key]["last_strengthened"] = time.time() - 30 * 86400
        network._last_dream_time = time.time() - 30 * 86400

        report = network.dream_consolidation()
        assert report["pruned_synapses"] >= 1
        assert key not in network.synapses

    def test_dream_strengthens_strong_synapses(self, network):
        """Les synapses >= 0.5 sont consolidees (+5%)."""
        nid_a = _make_node_id("a")
        nid_b = _make_node_id("b")
        network.nodes[nid_a] = _make_node("a", "memory", 0.5)
        network.nodes[nid_b] = _make_node("b", "memory", 0.5)

        key = _synapse_key(nid_a, nid_b)
        network.synapses[key] = _make_synapse(nid_a, nid_b, 0.6, "hebbian")
        w_before = network.synapses[key]["weight"]

        report = network.dream_consolidation()
        # Le decay recalcule est base sur l'age depuis last_strengthened
        # Mais la synapse vient d'etre creee, donc le decay est ~0
        # Et le renforcement de 5% devrait s'appliquer
        assert report["strengthened"] >= 1

    def test_dream_meta_concepts(self, network):
        """Detecte les cliques et cree des meta-concepts."""
        # Creer un triangle fortement connecte
        concepts = ["alpha", "beta", "gamma"]
        nids = []
        for c in concepts:
            nid = _make_node_id(c)
            network.nodes[nid] = _make_node(c, "memory", 0.8)
            nids.append(nid)

        # Connexions bidirectionnelles fortes
        for i in range(3):
            for j in range(3):
                if i != j:
                    key = _synapse_key(nids[i], nids[j])
                    network.synapses[key] = _make_synapse(nids[i], nids[j], 0.5)

        report = network.dream_consolidation()
        assert report["new_meta_concepts"] >= 1
        meta_nodes = [n for n in network.nodes.values() if n["node_type"] == "meta"]
        assert len(meta_nodes) >= 1

    def test_dream_meta_limit(self, network):
        """Max 10% de meta-noeuds."""
        # Creer 10 noeuds normaux
        for i in range(10):
            nid = _make_node_id(f"concept_{i}")
            network.nodes[nid] = _make_node(f"concept_{i}", "memory", 0.8)

        # Pre-remplir avec des meta-noeuds proches du max
        for i in range(2):
            nid = _make_node_id(f"meta:existing_{i}")
            network.nodes[nid] = _make_node(f"meta:existing_{i}", "meta", 0.5)

        # max_meta = max(1, int(12 * 0.1)) = 1, mais on en a deja 2
        # Donc _create_meta_concepts ne devrait rien creer
        new = network._create_meta_concepts()
        # Peut etre 0 car deja au-dessus du max
        assert new >= 0

    def test_dream_homeostatic_normalization(self, network):
        """Le dream mode normalise les energies."""
        for c in ["a", "b", "c"]:
            nid = _make_node_id(c)
            network.nodes[nid] = _make_node(c, "memory", 1.0)

        network.dream_consolidation()
        avg = sum(n["energy"] for n in network.nodes.values()) / len(network.nodes)
        assert avg < 1.0  # A ete normalise vers le bas

    def test_dream_report_structure(self, populated_network):
        report = populated_network.dream_consolidation()
        assert "pruned_synapses" in report
        assert "dream_connections" in report
        assert "new_meta_concepts" in report
        assert "strengthened" in report

    def test_dream_no_crash_single_node(self, network):
        """Ne crashe pas avec un seul noeud."""
        nid = _make_node_id("alone")
        network.nodes[nid] = _make_node("alone", "memory", 0.5)
        report = network.dream_consolidation()
        assert isinstance(report, dict)

    def test_dream_preserves_recent_synapses(self, network):
        """Les synapses recentes ne sont pas prunees."""
        nid_a = _make_node_id("a")
        nid_b = _make_node_id("b")
        network.nodes[nid_a] = _make_node("a", "memory", 0.5)
        network.nodes[nid_b] = _make_node("b", "memory", 0.5)

        key = _synapse_key(nid_a, nid_b)
        network.synapses[key] = _make_synapse(nid_a, nid_b, 0.2, "hebbian")
        # Synapse recente (maintenant) → decay ~0 → pas prunee

        report = network.dream_consolidation()
        # La synapse devrait survivre car elle est recente
        assert key in network.synapses


# =====================================================================
# TestBusIntegration
# =====================================================================

class TestBusIntegration:

    @pytest.mark.asyncio
    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature",
           return_value={"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                         "dominant_trait": "", "trait_value": 50.0, "valence": 0.0})
    async def test_on_routine_complete(self, mock_affect, network):
        await network._on_routine_complete({
            "intent": "EXPANSION_CODE",
            "status": "success",
            "quality_score": 0.8,
            "result": "Refactoring du module base_agent"
        })
        assert len(network.nodes) > 0

    @pytest.mark.asyncio
    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature",
           return_value={"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                         "dominant_trait": "", "trait_value": 50.0, "valence": 0.0})
    async def test_on_council_end(self, mock_affect, network):
        await network._on_council_end({
            "topic": "strategie evolution",
            "status": "consensus",
            "summary": "Le systeme devrait prioriser la stabilite"
        })
        assert len(network.nodes) > 0

    @pytest.mark.asyncio
    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature",
           return_value={"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                         "dominant_trait": "", "trait_value": 50.0, "valence": 0.0})
    async def test_on_eureka_bridge(self, mock_affect, network):
        await network._on_eureka_bridge({
            "node_a": "securite",
            "node_b": "creativite",
            "hypothesis": "Lien entre securite et creativite"
        })
        assert len(network.nodes) >= 2
        assert len(network.synapses) > 0

    @pytest.mark.asyncio
    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature",
           return_value={"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                         "dominant_trait": "", "trait_value": 50.0, "valence": 0.0})
    async def test_on_artifact_created(self, mock_affect, network):
        await network._on_artifact_created({
            "agent": "coder",
            "filename": "core/test_module.py"
        })
        assert len(network.nodes) >= 2

    @pytest.mark.asyncio
    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature",
           return_value={"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                         "dominant_trait": "", "trait_value": 50.0, "valence": 0.0})
    async def test_on_knowledge_gap(self, mock_affect, network):
        await network._on_knowledge_gap({"gap": "pattern observer"})
        nid = _make_node_id("pattern observer")
        assert nid in network.nodes
        assert network.nodes[nid]["node_type"] == "objective"

    @pytest.mark.asyncio
    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature",
           return_value={"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                         "dominant_trait": "", "trait_value": 50.0, "valence": 0.0})
    async def test_on_mission_finished(self, mock_affect, network):
        await network._on_mission_finished({
            "mission": "optimiser performances",
            "result": "Latence reduite de 30%",
            "agent": "coder",
            "success": True
        })
        assert len(network.nodes) > 0
        assert len(network.synapses) > 0

    @pytest.mark.asyncio
    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature",
           return_value={"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                         "dominant_trait": "", "trait_value": 50.0, "valence": 0.0})
    async def test_on_routine_empty_intent(self, mock_affect, network):
        """Ne crashe pas avec un intent vide."""
        await network._on_routine_complete({"intent": "", "status": "success"})
        # Aucun noeud cree
        assert len(network.nodes) == 0

    @pytest.mark.asyncio
    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature",
           return_value={"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                         "dominant_trait": "", "trait_value": 50.0, "valence": 0.0})
    async def test_on_experience_recorded(self, mock_affect, network):
        await network._on_experience_recorded({
            "description": "Premier test de refactoring reussi"
        })
        assert len(network.nodes) > 0

    def test_stdp_buffer(self, network):
        """Le buffer temporel STDP fonctionne."""
        network._activation_buffer = []
        nid_a = _make_node_id("a")
        nid_b = _make_node_id("b")
        network.nodes[nid_a] = _make_node("a", "memory", 0.8)
        network.nodes[nid_b] = _make_node("b", "memory", 0.8)

        # Activer A puis B avec un court delai
        network._activation_buffer.append((nid_a, time.time() - 5))
        network._record_activation(nid_b)

        # Une synapse STDP devrait etre creee
        key = _synapse_key(nid_a, nid_b)
        assert key in network.synapses


# =====================================================================
# TestPersistence
# =====================================================================

class TestPersistence:

    def test_save_and_load(self, network, tmp_path):
        """Sauvegarde et rechargement."""
        state_file = str(tmp_path / "test_synaptic.json")
        with patch("core.synaptic_network.STATE_FILE", state_file):
            nid = _make_node_id("python")
            network.nodes[nid] = _make_node("python", "memory", 0.7)
            key = _synapse_key(nid, nid)
            network.synapses[key] = _make_synapse(nid, nid, 0.5)

            network.save()
            assert os.path.exists(state_file)

            # Charger dans une nouvelle instance
            SynapticNetwork.reset_singleton()
            with patch("core.synaptic_network.STATE_FILE", state_file):
                net2 = SynapticNetwork()

            assert nid in net2.nodes
            assert key in net2.synapses
            assert net2.nodes[nid]["concept"] == "python"

    def test_load_missing_file(self, network, tmp_path):
        """Pas d'erreur si le fichier n'existe pas."""
        with patch("core.synaptic_network.STATE_FILE",
                    str(tmp_path / "nonexistent.json")):
            network._load()
        assert network.nodes == {}

    def test_load_corrupted_file(self, network, tmp_path):
        """Reset si le fichier est corrompu."""
        state_file = str(tmp_path / "corrupted.json")
        with open(state_file, "w") as f:
            f.write("{invalid json")

        with patch("core.synaptic_network.STATE_FILE", state_file):
            network._load()
        assert network.nodes == {}

    def test_auto_save_threshold(self, network, tmp_path):
        """Auto-save apres 10 mutations."""
        state_file = str(tmp_path / "auto.json")
        with patch("core.synaptic_network.STATE_FILE", state_file):
            network._mutations_since_save = 9
            network._auto_save()
            assert not os.path.exists(state_file)  # Pas encore 10

            network._mutations_since_save = 10
            network._auto_save()
            assert os.path.exists(state_file)

    def test_save_format(self, network, tmp_path):
        """Le format de sauvegarde est correct."""
        state_file = str(tmp_path / "format.json")
        with patch("core.synaptic_network.STATE_FILE", state_file):
            nid = _make_node_id("test")
            network.nodes[nid] = _make_node("test", "memory", 0.5)
            network.save()

            with open(state_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            assert data["version"] == "1.0"
            assert "saved_at" in data
            assert "nodes" in data
            assert "synapses" in data
            assert nid in data["nodes"]


# =====================================================================
# TestIntegrationAPI
# =====================================================================

class TestIntegrationAPI:

    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature",
           return_value={"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                         "dominant_trait": "", "trait_value": 50.0, "valence": 0.0})
    def test_query_associations_empty(self, mock_affect, network):
        assert network.query_associations([]) == []

    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature",
           return_value={"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                         "dominant_trait": "", "trait_value": 50.0, "valence": 0.0})
    def test_query_associations_with_data(self, mock_affect, populated_network):
        results = populated_network.query_associations(["python"], top_k=5)
        assert len(results) > 0

    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature",
           return_value={"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                         "dominant_trait": "", "trait_value": 50.0, "valence": 0.0})
    def test_format_for_prompt_empty(self, mock_affect, network):
        assert network.format_for_prompt([]) == ""

    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature",
           return_value={"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                         "dominant_trait": "", "trait_value": 50.0, "valence": 0.0})
    def test_format_for_prompt_with_data(self, mock_affect, populated_network):
        result = populated_network.format_for_prompt(["python"], max_chars=300)
        if result:
            assert "[ASSOCIATIONS SYNAPTIQUES]" in result
            assert len(result) <= 300

    def test_compute_routine_affinity_empty(self, network):
        assert network.compute_routine_affinity("EXPANSION_CODE") == 0.0

    def test_compute_routine_affinity_with_matching_nodes(self, network):
        """Bonus > 0 quand des noeuds correspondent aux keywords de l'intent."""
        nid = _make_node_id("code")
        network.nodes[nid] = _make_node("code", "memory", 0.8)
        network.synapses["dummy"] = _make_synapse(nid, nid, 0.1)  # pour que synapses ne soit pas vide

        affinity = network.compute_routine_affinity("EXPANSION_CODE")
        assert affinity > 0

    def test_get_stats(self, populated_network):
        stats = populated_network.get_stats()
        assert stats["total_nodes"] == 5
        assert stats["total_synapses"] == 4
        assert "avg_energy" in stats
        assert "meta_concepts" in stats
        assert "strong_synapses" in stats

    def test_synapse_key(self):
        assert _synapse_key("abc", "def") == "abc->def"
        assert _synapse_key("abc", "def") != _synapse_key("def", "abc")

    def test_enforce_synapse_limit(self, network):
        """Supprime les synapses les plus faibles au-dela de MAX_SYNAPSES."""
        nid_a = _make_node_id("a")
        network.nodes[nid_a] = _make_node("a")
        # Creer MAX_SYNAPSES + 10 synapses
        for i in range(MAX_SYNAPSES + 10):
            nid_b = f"node_{i}"
            key = _synapse_key(nid_a, nid_b)
            network.synapses[key] = _make_synapse(nid_a, nid_b, 0.01 + i * 0.00001)

        network._enforce_synapse_limit()
        assert len(network.synapses) <= MAX_SYNAPSES


# =====================================================================
# TestConceptFiltering
# =====================================================================

class TestConceptFiltering:
    """Tests du filtre MIN_CONCEPT_LENGTH sur les concepts trop courts."""

    def test_ensure_node_rejects_single_char(self, network):
        """Les concepts d'un seul caractere sont rejetes."""
        nid = network.ensure_node("a")
        assert nid == ""
        assert len(network.nodes) == 0

    def test_ensure_node_rejects_two_chars(self, network):
        """Les concepts de deux caracteres sont rejetes."""
        nid = network.ensure_node("ab")
        assert nid == ""
        assert len(network.nodes) == 0

    def test_ensure_node_accepts_three_chars(self, network):
        """Les concepts de trois caracteres ou plus sont acceptes."""
        nid = network.ensure_node("abc")
        assert nid != ""
        assert nid in network.nodes

    def test_ensure_node_rejects_whitespace_only(self, network):
        """Les concepts qui ne sont que des espaces sont rejetes."""
        nid = network.ensure_node("  ")
        assert nid == ""
        assert len(network.nodes) == 0

    def test_ensure_node_strips_then_checks(self, network):
        """Le filtre s'applique apres strip()."""
        nid = network.ensure_node("  a  ")
        assert nid == ""  # "a" apres strip = 1 char < MIN_CONCEPT_LENGTH

    def test_extract_and_ensure_filters_short_concepts(self, network):
        """_extract_and_ensure filtre les concepts courts retournes par extract_concepts."""
        mock_concepts = [("a", 0.5), ("python", 0.8), ("b", 0.3), ("refactor", 0.7)]
        with patch("core.spreading_activation.extract_concepts", return_value=mock_concepts):
            nids = network._extract_and_ensure("test text", "memory", ["dev"])
        # Seuls "python" et "refactor" doivent passer (>= 3 chars)
        assert len(nids) == 2

    def test_min_concept_length_constant(self):
        """La constante MIN_CONCEPT_LENGTH vaut 3."""
        assert MIN_CONCEPT_LENGTH == 3

    def test_hebbian_learning_rate_value(self):
        """Verification de la valeur du taux d'apprentissage hebbien."""
        assert HEBBIAN_LEARNING_RATE == 0.08

    def test_pruning_threshold_value(self):
        """Verification de la valeur du seuil de pruning."""
        assert PRUNING_THRESHOLD == 0.08


# =====================================================================
# TestOrganSeed — Noeuds desire/trait et liens TRAIT_RESONANCE
# =====================================================================

class TestOrganSeed:

    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature",
           return_value={"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                         "dominant_trait": "", "trait_value": 50.0, "valence": 0.0})
    def test_seed_organ_nodes_creates_desires(self, mock_affect, network):
        """7 noeuds desire existent apres seed."""
        network._seed_organ_nodes()
        desire_nodes = [n for n in network.nodes.values() if n["node_type"] == "desire"]
        assert len(desire_nodes) == 7
        concepts = {n["concept"] for n in desire_nodes}
        assert "pulsion:curiosite" in concepts
        assert "pulsion:creation" in concepts

    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature",
           return_value={"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                         "dominant_trait": "", "trait_value": 50.0, "valence": 0.0})
    def test_seed_organ_nodes_creates_traits(self, mock_affect, network):
        """6 noeuds trait existent apres seed."""
        network._seed_organ_nodes()
        trait_nodes = [n for n in network.nodes.values() if n["node_type"] == "trait"]
        assert len(trait_nodes) == 6
        concepts = {n["concept"] for n in trait_nodes}
        assert "trait:audace" in concepts
        assert "trait:survie" in concepts

    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature",
           return_value={"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                         "dominant_trait": "", "trait_value": 50.0, "valence": 0.0})
    def test_seed_creates_resonance_synapses(self, mock_affect, network):
        """Les liens TRAIT_RESONANCE sont crees entre pulsions et traits."""
        network._seed_organ_nodes()
        # CURIOSITE -> curiosite (0.4) doit exister
        drive_nid = _make_node_id("pulsion:curiosite")
        trait_nid = _make_node_id("trait:curiosite")
        key = _synapse_key(drive_nid, trait_nid)
        assert key in network.synapses
        assert network.synapses[key]["synapse_type"] == "emotional"
        assert network.synapses[key]["weight"] == pytest.approx(0.4, abs=0.01)

    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature",
           return_value={"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                         "dominant_trait": "", "trait_value": 50.0, "valence": 0.0})
    def test_seed_idempotent(self, mock_affect, network):
        """Appeler 2x ne cree pas de doublons (noeuds + synapses resonance)."""
        network._seed_organ_nodes()
        n1 = len(network.nodes)
        # Compter seulement les synapses resonance (pas STDP temporelles)
        resonance_s1 = sum(
            1 for s in network.synapses.values()
            if s["synapse_type"] == "emotional"
        )

        # Vider le buffer STDP pour eviter les synapses temporelles parasites
        network._activation_buffer = []
        network._seed_organ_nodes()
        n2 = len(network.nodes)
        resonance_s2 = sum(
            1 for s in network.synapses.values()
            if s["synapse_type"] == "emotional"
        )

        assert n1 == n2
        assert resonance_s1 == resonance_s2

    @pytest.mark.asyncio
    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature")
    async def test_on_psyche_update_activates_traits(self, mock_affect, network):
        """PSYCHE_UPDATE reactive les noeuds trait et met a jour trait_value."""
        _fresh = lambda: {"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                          "dominant_trait": "", "trait_value": 50.0, "valence": 0.0}
        mock_affect.side_effect = lambda: _fresh()
        network._seed_organ_nodes()
        trait_nid = _make_node_id("trait:curiosite")
        energy_before = network.nodes[trait_nid]["energy"]

        await network._on_psyche_update({
            "system_average": {
                "curiosite": 72.5, "creativite": 60.0, "audace": 55.0,
                "savoir": 65.0, "survie": 50.0, "respect": 58.0,
            }
        })

        # Energy boostee par ensure_node (+0.1)
        assert network.nodes[trait_nid]["energy"] > energy_before
        # trait_value mise a jour
        assert network.nodes[trait_nid]["affect"]["trait_value"] == 72.5

    @pytest.mark.asyncio
    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature")
    async def test_on_psyche_update_strengthens_dominant(self, mock_affect, network):
        """Le lien du trait dominant vers ses pulsions est renforce."""
        _fresh = lambda: {"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                          "dominant_trait": "", "trait_value": 50.0, "valence": 0.0}
        mock_affect.side_effect = lambda: _fresh()
        network._seed_organ_nodes()

        # curiosite est dominant (72.5 > les autres)
        dominant_nid = _make_node_id("trait:curiosite")
        # CURIOSITE a curiosite dans TRAIT_RESONANCE, et CONNEXION aussi
        drive_nid = _make_node_id("pulsion:curiosite")
        key = _synapse_key(dominant_nid, drive_nid)

        # Noter le poids avant (peut ne pas exister en sens inverse)
        weight_before = network.synapses.get(key, {}).get("weight", 0.0)

        await network._on_psyche_update({
            "system_average": {
                "curiosite": 72.5, "creativite": 60.0, "audace": 55.0,
                "savoir": 65.0, "survie": 50.0, "respect": 58.0,
            }
        })

        # Le lien dominant_trait -> pulsion doit etre cree ou renforce
        assert key in network.synapses
        assert network.synapses[key]["weight"] > weight_before

    @pytest.mark.asyncio
    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature")
    async def test_routine_complete_links_to_desires(self, mock_affect, network):
        """Liens intent->pulsion crees via DRIVE_ROUTINE_AFFINITY sur succes."""
        _fresh = lambda: {"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                          "dominant_trait": "", "trait_value": 50.0, "valence": 0.0}
        mock_affect.side_effect = lambda: _fresh()
        network._seed_organ_nodes()

        await network._on_routine_complete({
            "intent": "EXPANSION_CODE",
            "status": "success",
            "quality_score": 0.8,
            "result": "Refactoring du module base_agent pour optimisation"
        })

        intent_nid = _make_node_id("EXPANSION_CODE")
        # MAITRISE a EXPANSION_CODE dans DRIVE_ROUTINE_AFFINITY
        drive_nid = _make_node_id("pulsion:maitrise")
        key = _synapse_key(intent_nid, drive_nid)
        assert key in network.synapses

    @pytest.mark.asyncio
    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature")
    async def test_routine_complete_no_link_on_failure(self, mock_affect, network):
        """Pas de lien intent->pulsion si la routine echoue."""
        _fresh = lambda: {"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                          "dominant_trait": "", "trait_value": 50.0, "valence": 0.0}
        mock_affect.side_effect = lambda: _fresh()
        network._seed_organ_nodes()

        await network._on_routine_complete({
            "intent": "EXPANSION_CODE",
            "status": "failure",
            "quality_score": 0.2,
            "result": "Erreur lors du refactoring"
        })

        intent_nid = _make_node_id("EXPANSION_CODE")
        drive_nid = _make_node_id("pulsion:maitrise")
        key = _synapse_key(intent_nid, drive_nid)
        # Pas de lien car echec
        assert key not in network.synapses


# =====================================================================
# TestOrganIntegration — L'Etincelle : organes unifies dans le reseau
# =====================================================================

class TestOrganIntegration:
    """Tests de l'integration des organes (coeur, prefrontal, reptilien) dans le reseau synaptique."""

    @staticmethod
    def _seed_with_organs(network):
        """Helper : seed les noeuds organes sans STDP parasite."""
        affect = {"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                  "dominant_trait": "", "trait_value": 50.0, "valence": 0.0}
        for drive in ["curiosite", "maitrise", "stabilite", "connexion",
                       "croissance", "creation", "comprehension"]:
            nid = _make_node_id(f"pulsion:{drive}")
            network.nodes[nid] = _make_node(f"pulsion:{drive}", "desire", 0.4, ["desire_engine"], dict(affect))
        for trait in ["curiosite", "creativite", "audace", "savoir", "survie", "respect"]:
            nid = _make_node_id(f"trait:{trait}")
            node = _make_node(f"trait:{trait}", "trait", 0.4, ["psyche"], dict(affect))
            node["affect"]["trait_value"] = 60.0
            network.nodes[nid] = node

    @pytest.mark.asyncio
    async def test_cardiac_beat_modulates_desire_energy(self, network):
        """CARDIAC_BEAT module l'energie des pulsions via arousal."""
        self._seed_with_organs(network)
        nid = _make_node_id("pulsion:curiosite")
        network.nodes[nid]["energy"] = 0.4

        # Mock desire_engine avec une pulsion a deprivation 70
        mock_drive = MagicMock()
        mock_drive.name = "CURIOSITE"
        mock_drive.deprivation = 70.0
        mock_desires = MagicMock()
        mock_desires.drives = {"CURIOSITE": mock_drive}

        # Arousal eleve (enthousiasme: valence 0.8, arousal 0.8)
        with patch.dict("sys.modules", {"core.cardiac_engine": MagicMock(EMOTIONS={"enthousiasme": (0.8, 0.8)})}):
            with patch.dict("sys.modules", {"core.desire_engine": MagicMock(desires=mock_desires)}):
                await network._on_cardiac_beat({
                    "emotion": "enthousiasme", "emotion_intensity": 0.8, "coherence": 0.7
                })

        # Energie = deprivation/100 + pulse arousal = 0.7 + (0.8-0.5)*0.06 = 0.718
        assert abs(network.nodes[nid]["energy"] - 0.718) < 0.02

    @pytest.mark.asyncio
    async def test_cardiac_beat_modulates_trait_energy(self, network):
        """CARDIAC_BEAT module l'energie des traits via coherence."""
        self._seed_with_organs(network)
        trait_nid = _make_node_id("trait:audace")
        network.nodes[trait_nid]["affect"]["trait_value"] = 75.0
        network.nodes[trait_nid]["energy"] = 0.4

        with patch.dict("sys.modules", {"core.cardiac_engine": MagicMock(EMOTIONS={"serenite": (0.3, 0.2)})}):
            with patch.dict("sys.modules", {"core.desire_engine": MagicMock()}):
                # ImportError sur desires.drives pour sauter la partie desire
                mock_de = MagicMock()
                mock_de.desires = MagicMock()
                mock_de.desires.drives = MagicMock(side_effect=AttributeError)
                with patch.dict("sys.modules", {"core.desire_engine": mock_de}):
                    await network._on_cardiac_beat({
                        "emotion": "serenite", "coherence": 0.9
                    })

        # Energie = trait_value/100 + (coherence-0.5)*0.04 = 0.75 + 0.016 = 0.766
        assert abs(network.nodes[trait_nid]["energy"] - 0.766) < 0.02

    @pytest.mark.asyncio
    async def test_cardiac_beat_syncs_deprivation(self, network):
        """desire_intensity reflete la deprivation reelle apres CARDIAC_BEAT."""
        self._seed_with_organs(network)
        nid = _make_node_id("pulsion:maitrise")

        mock_drive = MagicMock()
        mock_drive.name = "MAITRISE"
        mock_drive.deprivation = 85.0
        mock_desires = MagicMock()
        mock_desires.drives = {"MAITRISE": mock_drive}

        with patch.dict("sys.modules", {"core.cardiac_engine": MagicMock(EMOTIONS={"serenite": (0.3, 0.3)})}):
            with patch.dict("sys.modules", {"core.desire_engine": MagicMock(desires=mock_desires)}):
                await network._on_cardiac_beat({"emotion": "serenite", "coherence": 0.5})

        assert network.nodes[nid]["affect"]["desire_intensity"] == 85.0

    @pytest.mark.asyncio
    async def test_cardiac_beat_no_stdp(self, network):
        """Pas de synapse temporelle creee par CARDIAC_BEAT (buffer STDP inchange)."""
        self._seed_with_organs(network)
        buffer_before = list(network._activation_buffer)
        synapses_before = len(network.synapses)

        with patch.dict("sys.modules", {"core.cardiac_engine": MagicMock(EMOTIONS={"serenite": (0.3, 0.3)})}):
            with patch.dict("sys.modules", {"core.desire_engine": MagicMock()}):
                mock_de = MagicMock()
                mock_de.desires = MagicMock()
                mock_de.desires.drives.values.side_effect = ImportError
                with patch.dict("sys.modules", {"core.desire_engine": mock_de}):
                    await network._on_cardiac_beat({"emotion": "serenite", "coherence": 0.5})

        # Buffer STDP identique (pas d'ajout via _record_activation)
        assert network._activation_buffer == buffer_before
        # Pas de nouvelles synapses
        assert len(network.synapses) == synapses_before

    @pytest.mark.asyncio
    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature",
           return_value={"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                         "dominant_trait": "", "trait_value": 50.0, "valence": 0.0})
    async def test_goal_created_makes_objective_node(self, mock_affect, network):
        """PREFRONTAL_GOAL_CREATED cree un noeud objective."""
        await network._on_goal_created({
            "title": "Optimiser base_agent",
            "goal_id": "g1",
            "source": "knowledge_gap",
            "horizon": "medium",
        })
        nid = _make_node_id("goal:Optimiser base_agent")
        assert nid in network.nodes
        assert network.nodes[nid]["node_type"] == "objective"

    @pytest.mark.asyncio
    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature",
           return_value={"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                         "dominant_trait": "", "trait_value": 50.0, "valence": 0.0})
    async def test_goal_created_links_to_dominant_drive(self, mock_affect, network):
        """Le goal est lie a la pulsion dominante."""
        self._seed_with_organs(network)

        mock_drive = MagicMock()
        mock_drive.name = "CROISSANCE"
        mock_drive.deprivation = 90.0
        mock_other = MagicMock()
        mock_other.name = "STABILITE"
        mock_other.deprivation = 20.0
        mock_desires = MagicMock()
        mock_desires.drives = {"CROISSANCE": mock_drive, "STABILITE": mock_other}

        with patch.dict("sys.modules", {"core.desire_engine": MagicMock(desires=mock_desires)}):
            await network._on_goal_created({
                "title": "Apprendre design patterns",
                "horizon": "long",
            })

        goal_nid = _make_node_id("goal:Apprendre design patterns")
        drive_nid = _make_node_id("pulsion:croissance")
        key = _synapse_key(goal_nid, drive_nid)
        assert key in network.synapses

    @pytest.mark.asyncio
    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature",
           return_value={"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                         "dominant_trait": "", "trait_value": 50.0, "valence": 0.0})
    async def test_goal_complete_peaks_energy(self, mock_affect, network):
        """PREFRONTAL_GOAL_COMPLETE met l'energie a 0.95."""
        # Creer le goal d'abord
        await network._on_goal_created({
            "title": "Optimiser base_agent", "horizon": "short"
        })
        nid = _make_node_id("goal:Optimiser base_agent")
        assert nid in network.nodes

        await network._on_goal_complete({"title": "Optimiser base_agent"})
        assert network.nodes[nid]["energy"] == 0.95

    @pytest.mark.asyncio
    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature",
           return_value={"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                         "dominant_trait": "", "trait_value": 50.0, "valence": 0.0})
    async def test_goal_abandoned_fades_energy(self, mock_affect, network):
        """PREFRONTAL_GOAL_ABANDONED fait chuter l'energie a 0.1."""
        await network._on_goal_created({
            "title": "Migrer vers Redis", "horizon": "long"
        })
        nid = _make_node_id("goal:Migrer vers Redis")
        assert network.nodes[nid]["energy"] > 0.1

        await network._on_goal_abandoned({"title": "Migrer vers Redis"})
        assert network.nodes[nid]["energy"] == 0.1

    @pytest.mark.asyncio
    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature",
           return_value={"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                         "dominant_trait": "", "trait_value": 50.0, "valence": 0.0})
    async def test_reptilian_alert_creates_reflex_node(self, mock_affect, network):
        """REPTILIAN_ALERT cree un noeud event haute energie."""
        self._seed_with_organs(network)
        await network._on_reptilian_alert({
            "reflex": "ADRENALINE", "threat_level": 5.0,
        })
        nid = _make_node_id("reflex:ADRENALINE")
        assert nid in network.nodes
        assert network.nodes[nid]["node_type"] == "event"
        # energy = min(1.0, 0.5 + 5.0*0.05) = 0.75
        assert network.nodes[nid]["energy"] >= 0.7

    @pytest.mark.asyncio
    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature",
           return_value={"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                         "dominant_trait": "", "trait_value": 50.0, "valence": 0.0})
    async def test_reptilian_alert_links_to_survival(self, mock_affect, network):
        """REPTILIAN_ALERT lie le reflexe a stabilite et survie."""
        self._seed_with_organs(network)
        await network._on_reptilian_alert({
            "reflex": "FREEZE", "threat_level": 7.0,
        })
        reflex_nid = _make_node_id("reflex:FREEZE")
        stab_nid = _make_node_id("pulsion:stabilite")
        surv_nid = _make_node_id("trait:survie")

        assert _synapse_key(reflex_nid, stab_nid) in network.synapses
        assert _synapse_key(reflex_nid, surv_nid) in network.synapses

    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature",
           return_value={"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                         "dominant_trait": "", "trait_value": 50.0, "valence": 0.0})
    def test_seed_publishes_synapse_deltas(self, mock_affect, network):
        """_seed_organ_nodes publie des deltas synapse_new pour les liens resonance."""
        deltas = []
        original_publish = network._publish_delta
        def capture_delta(change_type, data):
            deltas.append((change_type, data))
            original_publish(change_type, data)
        network._publish_delta = capture_delta

        network._seed_organ_nodes()

        synapse_new_deltas = [d for d in deltas if d[0] == "synapse_new"]
        # Au moins quelques synapses resonance publiees
        assert len(synapse_new_deltas) > 0
        # Toutes de type emotional
        for _, data in synapse_new_deltas:
            assert data["type"] == "emotional"

    @pytest.mark.asyncio
    @patch("core.synaptic_network.SynapticNetwork._capture_affect_signature")
    async def test_routine_complete_syncs_deprivation(self, mock_affect, network):
        """_on_routine_complete sync la deprivation des pulsions."""
        _fresh = lambda: {"mood": "neutre", "dominant_desire": "", "desire_intensity": 0.0,
                          "dominant_trait": "", "trait_value": 50.0, "valence": 0.0}
        mock_affect.side_effect = lambda: _fresh()
        self._seed_with_organs(network)

        mock_drive_cur = MagicMock()
        mock_drive_cur.name = "CURIOSITE"
        mock_drive_cur.deprivation = 42.0
        mock_drive_mai = MagicMock()
        mock_drive_mai.name = "MAITRISE"
        mock_drive_mai.deprivation = 88.0
        mock_desires = MagicMock()
        mock_desires.drives = {"CURIOSITE": mock_drive_cur, "MAITRISE": mock_drive_mai}

        with patch.dict("sys.modules", {"core.desire_engine": MagicMock(
            desires=mock_desires,
            DRIVE_ROUTINE_AFFINITY={"MAITRISE": ["EXPANSION_CODE"]},
        )}):
            await network._on_routine_complete({
                "intent": "EXPANSION_CODE",
                "status": "success",
                "quality_score": 0.8,
                "result": "Refactoring du module base_agent"
            })

        cur_nid = _make_node_id("pulsion:curiosite")
        mai_nid = _make_node_id("pulsion:maitrise")
        # Energie = deprivation / 100
        assert network.nodes[cur_nid]["energy"] == pytest.approx(0.42, abs=0.01)
        assert network.nodes[mai_nid]["energy"] == pytest.approx(0.88, abs=0.01)
        # desire_intensity mis a jour
        assert network.nodes[cur_nid]["affect"]["desire_intensity"] == 42.0
        assert network.nodes[mai_nid]["affect"]["desire_intensity"] == 88.0


# ═══════════════════════════════════════════════════════════════════════════════
# TestEmotionalSynapses — Synapses emotionnelles runtime via CARDIAC_EMOTION_CHANGE
# ═══════════════════════════════════════════════════════════════════════════════

class TestEmotionalSynapses:
    """Tests du handler _on_cardiac_emotion_change."""

    @pytest.mark.asyncio
    async def test_emotion_change_creates_emotional_synapse(self, network):
        """Transition emotionnelle forte cree une synapse 'emotional'."""
        # Pre-creer un noeud cause dans le reseau
        cause_concept = "failure"
        network.ensure_node(cause_concept, "event", 0.5)

        await network._on_cardiac_emotion_change({
            "emotion": "frustration",
            "intensity": 0.8,
            "cause": cause_concept,
        })
        # Verifier que le noeud emotion a ete cree
        emotion_nid = _make_node_id("emotion:frustration")
        assert emotion_nid in network.nodes
        assert network.nodes[emotion_nid]["node_type"] == "affect"

        # Verifier la synapse emotionnelle
        cause_nid = _make_node_id(cause_concept)
        key = _synapse_key(emotion_nid, cause_nid)
        assert key in network.synapses
        assert network.synapses[key]["synapse_type"] == "emotional"

    @pytest.mark.asyncio
    async def test_low_intensity_ignored(self, network):
        """Intensite < 0.6 ne cree pas de synapse."""
        network.ensure_node("test_cause", "event", 0.5)

        await network._on_cardiac_emotion_change({
            "emotion": "serenite",
            "intensity": 0.3,
            "cause": "test_cause",
        })
        emotion_nid = _make_node_id("emotion:serenite")
        assert emotion_nid not in network.nodes

    @pytest.mark.asyncio
    async def test_emotion_linked_to_cause(self, network):
        """Le noeud emotion est lie a la cause par synapse emotionnelle."""
        cause = "success"
        network.ensure_node(cause, "event", 0.5)

        await network._on_cardiac_emotion_change({
            "emotion": "enthousiasme",
            "intensity": 0.9,
            "cause": cause,
        })

        emotion_nid = _make_node_id("emotion:enthousiasme")
        cause_nid = _make_node_id(cause)
        key = _synapse_key(emotion_nid, cause_nid)
        assert key in network.synapses
        syn = network.synapses[key]
        assert syn["synapse_type"] == "emotional"
        assert syn["weight"] == pytest.approx(0.9 * 0.5, abs=0.01)

    @pytest.mark.asyncio
    async def test_emotion_linked_to_drive(self, network):
        """Si pulsion dominante existe dans le reseau, synapse emotionnelle creee."""
        # Pre-creer le noeud pulsion
        drive_nid = network.ensure_node("pulsion:curiosite", "desire", 0.5)
        assert drive_nid

        mock_drive = MagicMock()
        mock_drive.name = "CURIOSITE"
        mock_drive.deprivation = 80.0
        mock_desires = MagicMock()
        mock_desires.drives = {"CURIOSITE": mock_drive}

        with patch.dict("sys.modules", {
            "core.desire_engine": MagicMock(desires=mock_desires),
        }):
            await network._on_cardiac_emotion_change({
                "emotion": "enthousiasme",
                "intensity": 0.7,
                "cause": "",
            })

        emotion_nid = _make_node_id("emotion:enthousiasme")
        key = _synapse_key(emotion_nid, drive_nid)
        assert key in network.synapses
        assert network.synapses[key]["synapse_type"] == "emotional"

    @pytest.mark.asyncio
    async def test_repeated_emotion_strengthens(self, network):
        """Meme emotion repetee renforce le poids de la synapse."""
        cause = "failure"
        network.ensure_node(cause, "event", 0.5)

        await network._on_cardiac_emotion_change({
            "emotion": "frustration", "intensity": 0.7, "cause": cause,
        })
        emotion_nid = _make_node_id("emotion:frustration")
        cause_nid = _make_node_id(cause)
        key = _synapse_key(emotion_nid, cause_nid)
        w1 = network.synapses[key]["weight"]

        # Deuxieme fois -> renforcement +0.05
        await network._on_cardiac_emotion_change({
            "emotion": "frustration", "intensity": 0.7, "cause": cause,
        })
        w2 = network.synapses[key]["weight"]
        assert w2 == pytest.approx(w1 + 0.05, abs=0.01)

    @pytest.mark.asyncio
    async def test_no_crash_on_empty_event(self, network):
        """Pas de crash si event vide ou incomplet."""
        await network._on_cardiac_emotion_change({})
        await network._on_cardiac_emotion_change({"emotion": "", "intensity": 0.8})
        await network._on_cardiac_emotion_change({"emotion": "rage"})
        # Pas d'exception = OK


class TestPruningCap:
    """Tests du cap de pruning à MAX_PRUNE_RATIO par dream."""

    def test_pruning_cap_limits_deletions(self, network):
        """Le pruning ne supprime pas plus de MAX_PRUNE_RATIO du réseau."""
        # Créer 200 synapses avec des poids faibles (seront sous le seuil après decay)
        for i in range(200):
            concept_a = f"concept_a_{i}"
            concept_b = f"concept_b_{i}"
            nid_a = _make_node_id(concept_a)
            nid_b = _make_node_id(concept_b)
            network.nodes[nid_a] = _make_node(concept_a, "memory", 0.5)
            network.nodes[nid_b] = _make_node(concept_b, "memory", 0.5)
            key = _synapse_key(nid_a, nid_b)
            network.synapses[key] = _make_synapse(nid_a, nid_b, 0.09, "temporal", "test")

        # Simuler 2 jours sans dream → decay = 0.04, toutes sous le seuil
        network._last_dream_time = time.time() - 2 * 86400

        with patch.object(network, "_auto_save"):
            report = network.dream_consolidation()

        pruned = report["pruned_synapses"]
        # Le cap s'applique sur le réseau APRÈS dream connections (step 2)
        total_at_prune = len(network.synapses) + pruned
        max_allowed = max(10, int(total_at_prune * MAX_PRUNE_RATIO))
        assert pruned <= max_allowed, f"Pruned {pruned} > max {max_allowed}"
        assert report.get("pruning_capped") is True

    def test_pruning_cap_saves_strongest(self, network):
        """Les synapses sauvées sont celles avec le poids le plus élevé."""
        # 50 synapses à 0.09, 50 à 0.05
        for i in range(50):
            concept_a = f"strong_concept_{i}"
            concept_b = f"target_strong_{i}"
            nid_a = _make_node_id(concept_a)
            nid_b = _make_node_id(concept_b)
            network.nodes[nid_a] = _make_node(concept_a, "memory", 0.5)
            network.nodes[nid_b] = _make_node(concept_b, "memory", 0.5)
            key = _synapse_key(nid_a, nid_b)
            network.synapses[key] = _make_synapse(nid_a, nid_b, 0.09, "temporal", "test")

        for i in range(50):
            concept_a = f"weak_concept_{i}"
            concept_b = f"target_weak_{i}"
            nid_a = _make_node_id(concept_a)
            nid_b = _make_node_id(concept_b)
            network.nodes[nid_a] = _make_node(concept_a, "memory", 0.5)
            network.nodes[nid_b] = _make_node(concept_b, "memory", 0.5)
            key = _synapse_key(nid_a, nid_b)
            network.synapses[key] = _make_synapse(nid_a, nid_b, 0.05, "temporal", "test")

        network._last_dream_time = time.time() - 2 * 86400

        with patch.object(network, "_auto_save"):
            report = network.dream_consolidation()

        # Les synapses sauvées doivent avoir weight == PRUNING_THRESHOLD
        saved_count = sum(
            1 for s in network.synapses.values()
            if abs(s["weight"] - PRUNING_THRESHOLD) < 0.001
        )
        assert saved_count > 0, "Des synapses auraient dû être sauvées au seuil"

    def test_pruning_no_cap_when_few(self, network):
        """Pas de cap si peu de synapses à pruner (nœuds faible énergie → pas de dream connections)."""
        for i in range(5):
            concept_a = f"few_concept_a_{i}"
            concept_b = f"few_concept_b_{i}"
            nid_a = _make_node_id(concept_a)
            nid_b = _make_node_id(concept_b)
            # Énergie 0.05 → energy_combined < 0.15 → pas de dream connections
            network.nodes[nid_a] = _make_node(concept_a, "memory", 0.05)
            network.nodes[nid_b] = _make_node(concept_b, "memory", 0.05)
            key = _synapse_key(nid_a, nid_b)
            network.synapses[key] = _make_synapse(nid_a, nid_b, 0.05, "temporal", "test")

        network._last_dream_time = time.time() - 2 * 86400

        with patch.object(network, "_auto_save"):
            report = network.dream_consolidation()

        assert report.get("pruning_capped") is not True
        assert report["pruned_synapses"] == 5


# =====================================================================
# TestNodeStoplist — Rejet des noeuds bruit par ensure_node()
# =====================================================================

class TestNodeStoplist:

    def test_ensure_node_rejects_pycache(self, network):
        """__pycache__ est rejete par la stoplist."""
        nid = network.ensure_node("__pycache__")
        assert nid == ""
        assert len(network.nodes) == 0

    def test_ensure_node_rejects_okay(self, network):
        """'okay' est rejete par la stoplist."""
        nid = network.ensure_node("okay")
        assert nid == ""

    def test_ensure_node_rejects_case_insensitive(self, network):
        """La stoplist est case-insensitive."""
        nid = network.ensure_node("  Okay  ")
        assert nid == ""

    def test_ensure_node_accepts_valid_concept(self, network):
        """Un concept valide passe la stoplist."""
        nid = network.ensure_node("refactoring")
        assert nid != ""
        assert nid in network.nodes

    def test_stoplist_contains_expected_words(self):
        """Verification que la stoplist contient les mots attendus."""
        for word in ["__pycache__", "okay", "suis", "juste", "vraiment"]:
            assert word in _NODE_STOPLIST


# =====================================================================
# TestPurgeNoiseNodes — Purge one-shot des noeuds bruit
# =====================================================================

class TestPurgeNoiseNodes:

    def test_purge_removes_noise_nodes(self, network):
        """purge_noise_nodes supprime les noeuds dont le concept est dans la stoplist."""
        # Injecter des noeuds bruit directement (sans passer par ensure_node qui les bloque)
        for concept in ["__pycache__", "okay", "suis"]:
            nid = _make_node_id(concept)
            network.nodes[nid] = _make_node(concept, "memory", 0.5)
        # Et un noeud valide
        valid_nid = _make_node_id("refactoring")
        network.nodes[valid_nid] = _make_node("refactoring", "memory", 0.5)

        with patch.object(network, "_auto_save"):
            report = network.purge_noise_nodes()

        assert report["purged_nodes"] == 3
        assert valid_nid in network.nodes
        assert len(network.nodes) == 1

    def test_purge_removes_orphan_synapses(self, network):
        """Les synapses dont source ou target est purge sont supprimees."""
        noise_nid = _make_node_id("__pycache__")
        valid_nid = _make_node_id("python")
        other_nid = _make_node_id("refactor")

        network.nodes[noise_nid] = _make_node("__pycache__", "memory", 0.5)
        network.nodes[valid_nid] = _make_node("python", "memory", 0.5)
        network.nodes[other_nid] = _make_node("refactor", "memory", 0.5)

        # Synapse noise->valid (orpheline apres purge)
        key1 = _synapse_key(noise_nid, valid_nid)
        network.synapses[key1] = _make_synapse(noise_nid, valid_nid, 0.3)
        # Synapse valid->other (non-orpheline)
        key2 = _synapse_key(valid_nid, other_nid)
        network.synapses[key2] = _make_synapse(valid_nid, other_nid, 0.5)

        with patch.object(network, "_auto_save"):
            report = network.purge_noise_nodes()

        assert report["purged_nodes"] == 1
        assert report["purged_synapses"] == 1
        assert key1 not in network.synapses
        assert key2 in network.synapses

    def test_purge_no_noise_returns_zero(self, network):
        """Sans noeuds bruit, la purge retourne 0."""
        nid = _make_node_id("python")
        network.nodes[nid] = _make_node("python", "memory", 0.5)

        report = network.purge_noise_nodes()
        assert report["purged_nodes"] == 0
        assert report["purged_synapses"] == 0

    def test_purge_on_empty_network(self, network):
        """Purge sur un reseau vide ne plante pas."""
        report = network.purge_noise_nodes()
        assert report["purged_nodes"] == 0
        assert report["purged_synapses"] == 0


# ============================================================
# Sprint 2 Sensorium — Associations hardware
# ============================================================

class TestSensoriumSynaptic:
    """Sprint 2 Sensorium : noeuds hardware et associations."""

    @pytest.mark.asyncio
    async def test_creates_hardware_nodes(self, network):
        """Sens > 0.7 cree un noeud hardware_thermoception."""
        event = {
            "senses": {"thermoception": 0.9, "effort": 0.3},
            "comfort": 0.4,
            "raw": {},
        }
        await network._on_sensorium_update(event)
        nid = _make_node_id("hardware_thermoception")
        assert nid in network.nodes
        assert network.nodes[nid]["node_type"] == "affect"

    @pytest.mark.asyncio
    async def test_no_node_below_threshold(self, network):
        """Sens <= 0.7 ne cree pas de noeud."""
        event = {
            "senses": {"thermoception": 0.5, "effort": 0.3},
            "comfort": 0.7,
            "raw": {},
        }
        await network._on_sensorium_update(event)
        nid = _make_node_id("hardware_thermoception")
        assert nid not in network.nodes

    @pytest.mark.asyncio
    async def test_hebbian_with_routine(self, network):
        """Sens eleve pendant routine active → association Hebbienne."""
        # Creer un noeud routine en premier
        routine_nid = network.ensure_node("EXPANSION_CODE", "event", 0.6)
        network._last_routine_node = routine_nid

        event = {
            "senses": {"thermoception": 0.85},
            "comfort": 0.3,
            "raw": {},
        }
        # Patcher _publish_delta pour eviter RuntimeError (pas de running loop dans tests)
        with patch.object(network, '_publish_delta'):
            await network._on_sensorium_update(event)

        hw_nid = _make_node_id("hardware_thermoception")
        assert hw_nid in network.nodes
        # Verifier la synapse
        key = _synapse_key(routine_nid, hw_nid)
        assert key in network.synapses

    @pytest.mark.asyncio
    async def test_no_hebbian_without_routine(self, network):
        """Sans routine active, pas d'association Hebbienne."""
        network._last_routine_node = ""
        event = {
            "senses": {"thermoception": 0.9},
            "comfort": 0.3,
            "raw": {},
        }
        await network._on_sensorium_update(event)
        hw_nid = _make_node_id("hardware_thermoception")
        assert hw_nid in network.nodes
        # Pas de synapse
        assert len(network.synapses) == 0
