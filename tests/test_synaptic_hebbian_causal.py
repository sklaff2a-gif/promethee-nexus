"""Tests unitaires pour le Hebbian causal V3 (Phase C Etape 3, 2026-04-14).

Ces tests valident la regle d'apprentissage V3 en isolation totale :
- Pas de bus event reel
- Pas de dependances externes (desire_engine, prefrontal, dopamine)
- Mock minimal de la classe SynapticNetwork pour tester les filtres
  et la distribution triangulaire sans toucher au graphe reel

Couvre les 8 cas limites du design doc + les 11 invariants du §6.
Ref : docs/phase_c_etape_3_hebbian_causal.md
"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock

from core.synaptic_network import (
    SynapticNetwork,
    HEBBIAN_CAUSAL_LEARNING_RATE,
    HEBBIAN_CAUSAL_EXTINCTION_DELTA,
    HEBBIAN_CAUSAL_EXTINCTION_FLOOR,
    HEBBIAN_CAUSAL_KNOWN_DRIVES,
    HEBBIAN_CAUSAL_DROP_CAP,
    _make_node_id,
)


# ═══════════════════════════════════════════════════════════════════════
# §6.1 Invariants sur la distribution triangulaire (pur, pas d'event)
# ═══════════════════════════════════════════════════════════════════════


class TestTriangularWeightInvariants:
    """Les 3 invariants mathematiques de la distribution triangulaire."""

    def test_conserves_total_to_one(self):
        """Invariant A : somme des poids == 1.0 pour tout n."""
        for n in [1, 2, 3, 5, 10, 50, 100]:
            weights = [SynapticNetwork._triangular_weight(k, n) for k in range(n)]
            total = sum(weights)
            assert abs(total - 1.0) < 1e-9, f"n={n}: total={total}"

    def test_monotone_increasing(self):
        """Invariant B : weight(k) < weight(k+1) strictement croissant."""
        for n in [2, 3, 5, 10, 20]:
            weights = [SynapticNetwork._triangular_weight(k, n) for k in range(n)]
            for i in range(n - 1):
                assert weights[i] < weights[i + 1], (
                    f"n={n}, idx={i}: {weights[i]} >= {weights[i+1]}"
                )

    def test_last_step_dominant_formula(self):
        """Invariant C : weight(n-1, n) == 2/(n+1) (formule explicite)."""
        for n in [1, 2, 3, 5, 10, 100]:
            w_last = SynapticNetwork._triangular_weight(n - 1, n)
            expected = 2.0 / (n + 1)
            assert abs(w_last - expected) < 1e-9, f"n={n}"

    def test_n_equals_one_full_credit(self):
        """Cas limite #2 : n=1 donne 100% du credit au step unique."""
        w = SynapticNetwork._triangular_weight(0, 1)
        assert w == 1.0

    def test_n_equals_zero_returns_zero(self):
        """Defensif : n=0 ne crash pas, retourne 0."""
        w = SynapticNetwork._triangular_weight(0, 0)
        assert w == 0.0


# ═══════════════════════════════════════════════════════════════════════
# §6.2 Tests du handler complet avec mock minimal
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_network():
    """Cree une instance SynapticNetwork minimale sans toucher au disque."""
    with patch.object(SynapticNetwork, "_load", lambda self: None), \
         patch.object(SynapticNetwork, "_auto_save", lambda self: None), \
         patch.object(SynapticNetwork, "_publish_delta", lambda self, *a, **kw: None), \
         patch.object(SynapticNetwork, "_subscribe_events", lambda self: None):
        SynapticNetwork._instance = None  # reset singleton
        sn = SynapticNetwork()
        sn.nodes = {}
        sn.synapses = {}
        sn.stats = {}
        # Seed : noeuds drive + quelques intents (clés via _make_node_id)
        for drive in ["MAITRISE", "STABILITE", "CURIOSITE"]:
            concept = f"pulsion:{drive.lower()}"
            nid = _make_node_id(concept)
            sn.nodes[nid] = {
                "id": nid, "concept": concept, "type": "drive",
                "energy": 0.5, "activation_count": 0, "affect": {},
                "tags": ["drive"], "created_at": 0,
            }
        for intent in ["REFACTORING_AUDIT", "CI_PIPELINE_RUN", "VEILLE_SILENCIEUSE",
                       "EXPANSION_CODE", "AUDIT_STRUCTURE"]:
            nid = _make_node_id(intent)
            sn.nodes[nid] = {
                "id": nid, "concept": intent, "type": "event",
                "energy": 0.6, "activation_count": 0, "affect": {},
                "tags": ["autonomy"], "created_at": 0,
            }
        yield sn
        SynapticNetwork._instance = None


class TestFiltersF1F2F3F4:
    """Les 4 filtres de securite du handler (§3.2)."""

    @pytest.mark.asyncio
    async def test_F1_skip_non_homeostatic(self, mock_network):
        """F1 : completion_mode != homeostatic -> skip."""
        before = dict(mock_network.synapses)
        await mock_network._learn_from_homeostatic_closure({
            "completion_mode": "bureaucratic",
            "source_drive": "MAITRISE",
            "causal_drop": 50,
            "step_intents": ["REFACTORING_AUDIT"],
        })
        assert mock_network.synapses == before
        assert mock_network.stats.get("hebbian_causal_skipped_non_homeostatic") == 1

    @pytest.mark.asyncio
    async def test_F1_skip_abandoned_fruitless(self, mock_network):
        """F1 : completion_mode=abandoned_fruitless ne renforce PAS
        (c'est le chemin d'extinction, pas de renforcement)."""
        before = dict(mock_network.synapses)
        await mock_network._learn_from_homeostatic_closure({
            "completion_mode": "abandoned_fruitless",
            "source_drive": "MAITRISE",
            "causal_drop": 50,
            "step_intents": ["REFACTORING_AUDIT"],
        })
        assert mock_network.synapses == before

    @pytest.mark.asyncio
    async def test_F2_skip_zero_causal_drop(self, mock_network):
        """F2 : causal_drop == 0 -> skip."""
        before = dict(mock_network.synapses)
        await mock_network._learn_from_homeostatic_closure({
            "completion_mode": "homeostatic",
            "source_drive": "MAITRISE",
            "causal_drop": 0,
            "step_intents": ["REFACTORING_AUDIT"],
        })
        assert mock_network.synapses == before
        assert mock_network.stats.get("hebbian_causal_skipped_zero_drop") == 1

    @pytest.mark.asyncio
    async def test_F2_skip_negative_causal_drop(self, mock_network):
        """F2 : causal_drop < 0 -> skip."""
        before = dict(mock_network.synapses)
        await mock_network._learn_from_homeostatic_closure({
            "completion_mode": "homeostatic",
            "source_drive": "MAITRISE",
            "causal_drop": -10,
            "step_intents": ["REFACTORING_AUDIT"],
        })
        assert mock_network.synapses == before

    @pytest.mark.asyncio
    async def test_F3_skip_empty_steps(self, mock_network):
        """F3 : step_intents vide -> skip, pas de crash."""
        before = dict(mock_network.synapses)
        await mock_network._learn_from_homeostatic_closure({
            "completion_mode": "homeostatic",
            "source_drive": "MAITRISE",
            "causal_drop": 50,
            "step_intents": [],
        })
        assert mock_network.synapses == before
        assert mock_network.stats.get("hebbian_causal_skipped_empty_steps") == 1

    @pytest.mark.asyncio
    async def test_F3_skip_none_steps(self, mock_network):
        """F3 : step_intents absent -> skip."""
        await mock_network._learn_from_homeostatic_closure({
            "completion_mode": "homeostatic",
            "source_drive": "MAITRISE",
            "causal_drop": 50,
        })
        assert mock_network.stats.get("hebbian_causal_skipped_empty_steps") == 1

    @pytest.mark.asyncio
    async def test_F4_skip_unknown_drive(self, mock_network):
        """F4 : source_drive inconnu -> skip avec WARNING (cas limite #3)."""
        before = dict(mock_network.synapses)
        await mock_network._learn_from_homeostatic_closure({
            "completion_mode": "homeostatic",
            "source_drive": "knowledge_gap:python_asyncio",
            "causal_drop": 50,
            "step_intents": ["VEILLE_SILENCIEUSE"],
        })
        assert mock_network.synapses == before
        assert mock_network.stats.get("hebbian_causal_skipped_unknown_drive") == 1

    @pytest.mark.asyncio
    async def test_F4_skip_empty_drive(self, mock_network):
        """F4 : source_drive vide -> skip."""
        await mock_network._learn_from_homeostatic_closure({
            "completion_mode": "homeostatic",
            "source_drive": "",
            "causal_drop": 50,
            "step_intents": ["REFACTORING_AUDIT"],
        })
        assert mock_network.stats.get("hebbian_causal_skipped_unknown_drive") == 1


class TestReinforcementMath:
    """Validation numerique de la formule de renforcement."""

    @pytest.mark.asyncio
    async def test_single_step_full_credit(self, mock_network):
        """n=1 : le step unique recoit normalized_drop * 1.0 * LEARNING_RATE."""
        await mock_network._learn_from_homeostatic_closure({
            "completion_mode": "homeostatic",
            "source_drive": "MAITRISE",
            "causal_drop": 50,  # normalized = 0.5
            "step_intents": ["REFACTORING_AUDIT"],
        })
        # Expected delta = 0.5 * 1.0 * 0.10 = 0.05
        assert mock_network.stats.get("hebbian_causal_reinforcements") == 1
        total_delta = mock_network.stats.get("hebbian_causal_total_delta_applied")
        assert abs(total_delta - 0.05) < 1e-6

    @pytest.mark.asyncio
    async def test_triangular_distribution_n_equals_3(self, mock_network):
        """n=3 : le dernier step recoit 3x plus que le premier."""
        await mock_network._learn_from_homeostatic_closure({
            "completion_mode": "homeostatic",
            "source_drive": "MAITRISE",
            "causal_drop": 100,  # normalized = 1.0
            "step_intents": [
                "VEILLE_SILENCIEUSE",  # idx 0, weight 1/6
                "AUDIT_STRUCTURE",     # idx 1, weight 2/6
                "REFACTORING_AUDIT",   # idx 2, weight 3/6
            ],
        })
        # Expected total = 1.0 * (1/6 + 2/6 + 3/6) * 0.10 = 0.10
        total_delta = mock_network.stats.get("hebbian_causal_total_delta_applied")
        assert abs(total_delta - 0.10) < 1e-6

    @pytest.mark.asyncio
    async def test_causal_drop_cap_at_100(self, mock_network):
        """Cas limite #7 : causal_drop > 100 est cappe a 100."""
        await mock_network._learn_from_homeostatic_closure({
            "completion_mode": "homeostatic",
            "source_drive": "MAITRISE",
            "causal_drop": 150,  # normalized should cap to 1.0
            "step_intents": ["REFACTORING_AUDIT"],
        })
        total_delta = mock_network.stats.get("hebbian_causal_total_delta_applied")
        # Max delta = 1.0 * 1.0 * 0.10 = 0.10
        assert abs(total_delta - 0.10) < 1e-6

    @pytest.mark.asyncio
    async def test_small_drop_small_delta(self, mock_network):
        """Cas limite #7 bis : petit drop -> petit delta proportionnel."""
        await mock_network._learn_from_homeostatic_closure({
            "completion_mode": "homeostatic",
            "source_drive": "MAITRISE",
            "causal_drop": 5,  # normalized = 0.05
            "step_intents": ["REFACTORING_AUDIT"],
        })
        total_delta = mock_network.stats.get("hebbian_causal_total_delta_applied")
        # 0.05 * 1.0 * 0.10 = 0.005
        assert abs(total_delta - 0.005) < 1e-6


class TestReinforcementCreatesSynapse:
    """Verifie que le renforcement cree effectivement des synapses."""

    @pytest.mark.asyncio
    async def test_synapse_created_on_first_learn(self, mock_network):
        """Premier apprentissage : la synapse intent<->drive doit etre creee."""
        assert len(mock_network.synapses) == 0
        await mock_network._learn_from_homeostatic_closure({
            "completion_mode": "homeostatic",
            "source_drive": "MAITRISE",
            "causal_drop": 50,
            "step_intents": ["REFACTORING_AUDIT"],
        })
        assert len(mock_network.synapses) == 1

    @pytest.mark.asyncio
    async def test_synapse_strengthened_on_second_learn(self, mock_network):
        """Deuxieme apprentissage sur meme lien : le poids augmente."""
        event = {
            "completion_mode": "homeostatic",
            "source_drive": "MAITRISE",
            "causal_drop": 50,
            "step_intents": ["REFACTORING_AUDIT"],
        }
        await mock_network._learn_from_homeostatic_closure(event)
        weight_1 = list(mock_network.synapses.values())[0]["weight"]
        await mock_network._learn_from_homeostatic_closure(event)
        weight_2 = list(mock_network.synapses.values())[0]["weight"]
        assert weight_2 > weight_1

    @pytest.mark.asyncio
    async def test_learning_rate_cap(self, mock_network):
        """Un event ne peut jamais appliquer plus que LEARNING_RATE en delta total."""
        await mock_network._learn_from_homeostatic_closure({
            "completion_mode": "homeostatic",
            "source_drive": "MAITRISE",
            "causal_drop": 1000,  # astronomical
            "step_intents": ["REFACTORING_AUDIT"],
        })
        total_delta = mock_network.stats.get("hebbian_causal_total_delta_applied")
        assert total_delta <= HEBBIAN_CAUSAL_LEARNING_RATE + 1e-9


# ═══════════════════════════════════════════════════════════════════════
# Tests de l'extinction (_learn_from_fruitless_goal)
# ═══════════════════════════════════════════════════════════════════════


class TestExtinction:
    """Validation de l'extinction causale (Gemini Q1 : EGA, Q2 : floor 0.0)."""

    @pytest.mark.asyncio
    async def test_extinction_requires_abandoned_fruitless(self, mock_network):
        """Seul abandoned_fruitless enseigne par extinction."""
        # Creer une synapse d'abord
        await mock_network._learn_from_homeostatic_closure({
            "completion_mode": "homeostatic",
            "source_drive": "MAITRISE",
            "causal_drop": 50,
            "step_intents": ["REFACTORING_AUDIT"],
        })
        weight_before = list(mock_network.synapses.values())[0]["weight"]
        # Tenter une extinction avec completion_mode != abandoned_fruitless
        await mock_network._learn_from_fruitless_goal({
            "completion_mode": "bureaucratic",
            "source_drive": "MAITRISE",
            "step_intents": ["REFACTORING_AUDIT"],
        })
        weight_after = list(mock_network.synapses.values())[0]["weight"]
        assert weight_before == weight_after

    @pytest.mark.asyncio
    async def test_extinction_uniform_distribution(self, mock_network):
        """Gemini Q1 : l'extinction est UNIFORME sur tous les tried_intents."""
        # Creer 3 synapses avec poids eleves
        for intent in ["VEILLE_SILENCIEUSE", "AUDIT_STRUCTURE", "REFACTORING_AUDIT"]:
            await mock_network._learn_from_homeostatic_closure({
                "completion_mode": "homeostatic",
                "source_drive": "MAITRISE",
                "causal_drop": 100,
                "step_intents": [intent],
            })
        # Toutes doivent avoir weight ~0.15 (0.05 init + 0.10 boost)
        weights_before = {
            key: syn["weight"] for key, syn in mock_network.synapses.items()
        }
        # Extinction fruitless
        await mock_network._learn_from_fruitless_goal({
            "completion_mode": "abandoned_fruitless",
            "source_drive": "MAITRISE",
            "step_intents": ["VEILLE_SILENCIEUSE", "AUDIT_STRUCTURE", "REFACTORING_AUDIT"],
        })
        weights_after = {
            key: syn["weight"] for key, syn in mock_network.synapses.items()
        }
        # Chaque synapse doit avoir perdu EXACTEMENT EXTINCTION_DELTA
        for key in weights_before:
            loss = weights_before[key] - weights_after[key]
            assert abs(loss - HEBBIAN_CAUSAL_EXTINCTION_DELTA) < 1e-9

    @pytest.mark.asyncio
    async def test_extinction_respects_floor_strict_zero(self, mock_network):
        """Gemini Q2 : le poids ne descend jamais en dessous de 0.0."""
        # Creer une synapse avec un poids bas
        await mock_network._learn_from_homeostatic_closure({
            "completion_mode": "homeostatic",
            "source_drive": "MAITRISE",
            "causal_drop": 10,  # petit drop -> petit weight
            "step_intents": ["REFACTORING_AUDIT"],
        })
        # Appliquer plusieurs extinctions jusqu'a ce que le weight doive passer sous 0
        for _ in range(20):
            await mock_network._learn_from_fruitless_goal({
                "completion_mode": "abandoned_fruitless",
                "source_drive": "MAITRISE",
                "step_intents": ["REFACTORING_AUDIT"],
            })
        # Le poids doit etre >= 0.0 (pas negatif)
        final_weight = list(mock_network.synapses.values())[0]["weight"]
        assert final_weight >= HEBBIAN_CAUSAL_EXTINCTION_FLOOR
        assert final_weight >= 0.0  # plancher strict

    @pytest.mark.asyncio
    async def test_extinction_does_not_create_synapse(self, mock_network):
        """Extinction sur un lien inexistant : no-op (pas de synapse creee)."""
        assert len(mock_network.synapses) == 0
        await mock_network._learn_from_fruitless_goal({
            "completion_mode": "abandoned_fruitless",
            "source_drive": "MAITRISE",
            "step_intents": ["REFACTORING_AUDIT"],
        })
        assert len(mock_network.synapses) == 0

    @pytest.mark.asyncio
    async def test_extinction_skips_unknown_drive(self, mock_network):
        """Cas limite #3 sur extinction : drive inconnu -> skip + WARNING."""
        # Creer une synapse puis tenter extinction sur mauvais drive
        await mock_network._learn_from_homeostatic_closure({
            "completion_mode": "homeostatic",
            "source_drive": "MAITRISE",
            "causal_drop": 50,
            "step_intents": ["REFACTORING_AUDIT"],
        })
        before = list(mock_network.synapses.values())[0]["weight"]
        await mock_network._learn_from_fruitless_goal({
            "completion_mode": "abandoned_fruitless",
            "source_drive": "knowledge_gap:quantum",
            "step_intents": ["REFACTORING_AUDIT"],
        })
        after = list(mock_network.synapses.values())[0]["weight"]
        assert before == after  # pas d'extinction appliquee


# ═══════════════════════════════════════════════════════════════════════
# Integration : les 2 handlers via _on_goal_complete / _on_goal_abandoned
# ═══════════════════════════════════════════════════════════════════════


class TestHandlerIntegration:
    """Verifie que _on_goal_complete / _on_goal_abandoned routent
    correctement vers les handlers V3."""

    @pytest.mark.asyncio
    async def test_on_goal_complete_calls_learn_homeostatic(self, mock_network):
        """_on_goal_complete doit declencher l'apprentissage V3."""
        await mock_network._on_goal_complete({
            "title": "Test goal",
            "completion_mode": "homeostatic",
            "source_drive": "MAITRISE",
            "causal_drop": 50,
            "step_intents": ["REFACTORING_AUDIT"],
        })
        assert mock_network.stats.get("hebbian_causal_reinforcements") == 1

    @pytest.mark.asyncio
    async def test_on_goal_abandoned_calls_learn_fruitless(self, mock_network):
        """_on_goal_abandoned doit declencher l'extinction V3."""
        # Creer une synapse d'abord
        await mock_network._learn_from_homeostatic_closure({
            "completion_mode": "homeostatic",
            "source_drive": "MAITRISE",
            "causal_drop": 50,
            "step_intents": ["REFACTORING_AUDIT"],
        })
        await mock_network._on_goal_abandoned({
            "title": "Test abandoned",
            "completion_mode": "abandoned_fruitless",
            "source_drive": "MAITRISE",
            "step_intents": ["REFACTORING_AUDIT"],
        })
        assert mock_network.stats.get("hebbian_causal_extinctions") == 1

    @pytest.mark.asyncio
    async def test_on_goal_complete_bureaucratic_no_learn(self, mock_network):
        """_on_goal_complete avec mode bureaucratic : pas d'apprentissage V3."""
        await mock_network._on_goal_complete({
            "title": "Test bureaucratic",
            "completion_mode": "bureaucratic",
            "source_drive": "MAITRISE",
            "causal_drop": 50,
            "step_intents": ["REFACTORING_AUDIT"],
        })
        assert mock_network.stats.get("hebbian_causal_reinforcements", 0) == 0
