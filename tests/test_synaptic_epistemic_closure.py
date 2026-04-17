"""Tests pour _learn_from_epistemic_closure (V3.1 famine synaptique fix).

Design valide par Gemini 2026-04-17 (challenge famine_synaptique).
Couvre : filtres F1-F4, RPE multiplicatif, historique glissant N=10,
cooldown anti-farming, compartimentage drive epistemique.
"""
import os

os.environ.setdefault("PROMETHEE_TEST_MODE", "1")

import math
import time

import pytest

from core.synaptic_network import (
    SynapticNetwork,
    EPISTEMIC_LEARNING_RATE,
    EPISTEMIC_MIN_NOTE_FOR_CLOSURE,
    EPISTEMIC_COOLDOWN_SECONDS,
    EPISTEMIC_HISTORY_WINDOW,
    EPISTEMIC_RPE_UPPER_BOUND,
    EPISTEMIC_RPE_LOWER_BOUND,
    _make_node_id,
)


@pytest.fixture
def network():
    SynapticNetwork.reset_singleton()
    net = SynapticNetwork()
    yield net
    SynapticNetwork.reset_singleton()


def _build_event(grade=8.0, slot="RESEARCH", intent="RESEARCH", task_entropy=1.0):
    return {
        "grade": grade,
        "slot": slot,
        "intent": intent,
        "task_entropy": task_entropy,
    }


class TestFilters:
    @pytest.mark.asyncio
    async def test_f1_low_score_skipped(self, network):
        """Score < 7 -> pas de fermeture epistemique."""
        await network._learn_from_epistemic_closure(_build_event(grade=6.5))
        assert network.stats.get("epistemic_skipped_low_score") == 1
        assert "epistemic_reinforcements" not in network.stats

    @pytest.mark.asyncio
    async def test_f2_empty_slot_skipped(self, network):
        await network._learn_from_epistemic_closure(_build_event(slot=""))
        assert network.stats.get("epistemic_skipped_empty_meta") == 1

    @pytest.mark.asyncio
    async def test_f2_empty_intent_skipped(self, network):
        await network._learn_from_epistemic_closure(_build_event(intent=""))
        assert network.stats.get("epistemic_skipped_empty_meta") == 1

    @pytest.mark.asyncio
    async def test_f3_cooldown_blocks_same_slot(self, network):
        """Deux fermetures consecutives sur meme slot < 5min -> 2e skippee."""
        await network._learn_from_epistemic_closure(_build_event(slot="RESEARCH"))
        assert network.stats.get("epistemic_reinforcements") == 1
        await network._learn_from_epistemic_closure(_build_event(slot="RESEARCH"))
        assert network.stats.get("epistemic_skipped_cooldown") == 1

    @pytest.mark.asyncio
    async def test_f3_cooldown_isolated_per_slot(self, network):
        """Deux slots differents -> pas de collision cooldown."""
        await network._learn_from_epistemic_closure(_build_event(slot="RESEARCH"))
        await network._learn_from_epistemic_closure(_build_event(slot="CODE_REVIEW"))
        assert network.stats.get("epistemic_reinforcements") == 2

    @pytest.mark.asyncio
    async def test_f4_low_entropy_skipped(self, network):
        await network._learn_from_epistemic_closure(
            _build_event(task_entropy=0.1)
        )
        assert network.stats.get("epistemic_skipped_low_entropy") == 1


class TestReinforcement:
    @pytest.mark.asyncio
    async def test_reinforcement_creates_drive_node(self, network):
        """Premiere fermeture -> cree pulsion:maitrise_epistemic."""
        drive_nid = _make_node_id("pulsion:maitrise_epistemic")
        assert drive_nid not in network.nodes
        await network._learn_from_epistemic_closure(_build_event())
        assert drive_nid in network.nodes

    @pytest.mark.asyncio
    async def test_reinforcement_creates_3_step_synapses(self, network):
        """Pattern [PREPARE, INTENT, CONCLUDE] -> 3 synapses vers drive."""
        await network._learn_from_epistemic_closure(_build_event(slot="RESEARCH"))
        drive_nid = _make_node_id("pulsion:maitrise_epistemic")
        links = [
            s for s in network.synapses.values()
            if s["source"] == drive_nid or s["target"] == drive_nid
        ]
        assert len(links) == 3

    @pytest.mark.asyncio
    async def test_triangular_favors_conclude_step(self, network):
        """Le step CONCLUDE (idx=2, triangular=0.5) doit avoir le plus gros poids."""
        await network._learn_from_epistemic_closure(_build_event(grade=10.0))
        drive_nid = _make_node_id("pulsion:maitrise_epistemic")
        prepare_nid = _make_node_id("SCHOOL_RESEARCH_PREPARE")
        conclude_nid = _make_node_id("SCHOOL_RESEARCH_CONCLUDE")
        from core.synaptic_network import _synapse_key
        prepare_key = _synapse_key(prepare_nid, drive_nid)
        conclude_key = _synapse_key(conclude_nid, drive_nid)
        assert network.synapses[conclude_key]["weight"] > \
               network.synapses[prepare_key]["weight"]


class TestRPE:
    @pytest.mark.asyncio
    async def test_rpe_positive_surprise_amplifies(self, network):
        """Moyenne basse + note haute -> surprise_factor > 1."""
        network._epistemic_history["RESEARCH"] = [4.0] * 5
        await network._learn_from_epistemic_closure(_build_event(grade=9.0))
        delta = network.stats["epistemic_total_delta_applied"]
        assert delta > 0
        # surprise = exp(9-4) = 148 -> cap 3.0 -> delta = 0.9*3*0.18 = 0.486 sum triangular
        assert delta > 0.15  # borne basse confortable

    @pytest.mark.asyncio
    async def test_rpe_negative_surprise_dampens(self, network):
        """Moyenne haute + note moyenne -> surprise_factor < 1."""
        network._epistemic_history["RESEARCH"] = [9.5] * 5
        await network._learn_from_epistemic_closure(_build_event(grade=7.5))
        delta = network.stats["epistemic_total_delta_applied"]
        # surprise = exp(7.5-9.5) = 0.135 -> clip a 0.135 (> 0.1 floor)
        assert 0.0 < delta < 0.04  # tres attenue

    @pytest.mark.asyncio
    async def test_rpe_upper_bound(self, network):
        """Delta max total par fermeture <= LR * 1.0 * 3.0 = 0.54 (sum triangular=1)."""
        network._epistemic_history["RESEARCH"] = [2.0] * 5
        await network._learn_from_epistemic_closure(_build_event(grade=10.0))
        delta = network.stats["epistemic_total_delta_applied"]
        # max theorique = (10/10) * 3.0 * 0.18 * 1.0 = 0.54
        assert delta <= 0.54 + 0.001

    @pytest.mark.asyncio
    async def test_rpe_lower_bound(self, network):
        """Delta min > 0 meme en cas de surprise tres negative."""
        network._epistemic_history["RESEARCH"] = [10.0] * 5
        await network._learn_from_epistemic_closure(_build_event(grade=7.0))
        delta = network.stats["epistemic_total_delta_applied"]
        # surprise = exp(-3) = 0.05 -> floor 0.1. Delta = 0.7 * 0.1 * 0.18 = 0.0126
        assert delta > 0


class TestHistory:
    @pytest.mark.asyncio
    async def test_history_grows(self, network):
        await network._learn_from_epistemic_closure(_build_event(grade=8.0))
        assert network._epistemic_history["RESEARCH"] == [8.0]

    @pytest.mark.asyncio
    async def test_history_window_capped(self, network):
        """Fenetre glissante N=10 : seuls les 10 derniers scores gardes."""
        network._epistemic_last_closure = {}
        for i in range(15):
            network._epistemic_last_closure["RESEARCH"] = 0  # bypass cooldown
            await network._learn_from_epistemic_closure(_build_event(grade=8.0))
        assert len(network._epistemic_history["RESEARCH"]) == EPISTEMIC_HISTORY_WINDOW

    @pytest.mark.asyncio
    async def test_history_per_slot_isolated(self, network):
        await network._learn_from_epistemic_closure(_build_event(slot="RESEARCH", grade=9.0))
        await network._learn_from_epistemic_closure(_build_event(slot="CODE_REVIEW", grade=7.0))
        assert network._epistemic_history["RESEARCH"] == [9.0]
        assert network._epistemic_history["CODE_REVIEW"] == [7.0]


class TestCompartimentage:
    @pytest.mark.asyncio
    async def test_epistemic_drive_separate_from_homeostatic(self, network):
        """La fermeture epistemique ne touche PAS les drives vitaux (Gemini Q1)."""
        await network._learn_from_epistemic_closure(_build_event())
        # Aucune synapse epistemique ne doit toucher pulsion:stabilite, maitrise, etc.
        vital_drives = ["stabilite", "maitrise", "connexion", "creation"]
        for prop_name in network.synapses:
            syn = network.synapses[prop_name]
            ctx = syn.get("context", "")
            if "epistemic" in ctx:
                for vd in vital_drives:
                    assert f"pulsion:{vd}" not in ctx
