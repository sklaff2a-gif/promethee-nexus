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
    EPISTEMIC_NOTE_FLOOR,
    EPISTEMIC_PARTIAL_LR_FACTOR,
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
    async def test_f1_below_floor_skipped(self, network):
        """Score < EPISTEMIC_NOTE_FLOOR (4.0) -> skip total (bruit pur)."""
        await network._learn_from_epistemic_closure(_build_event(grade=3.5))
        assert network.stats.get("epistemic_skipped_below_floor") == 1
        assert "epistemic_reinforcements" not in network.stats

    @pytest.mark.asyncio
    async def test_f1_ramp_zone_consolidates_partially(self, network):
        """Score dans [4.0, 7.0) -> micro-consolidation, pas de skip."""
        await network._learn_from_epistemic_closure(_build_event(grade=5.0))
        assert network.stats.get("epistemic_reinforcements") == 1
        assert "epistemic_skipped_below_floor" not in network.stats
        # Le delta doit etre plus faible qu un score 8.0 (meme expected initial)

    @pytest.mark.asyncio
    async def test_f1_ramp_micro_delta_smaller_than_full(self, network):
        """Note moyenne (5.0) doit consolider moins qu une note pleine (8.0)."""
        await network._learn_from_epistemic_closure(
            _build_event(grade=5.0, slot="RESEARCH")
        )
        delta_low = network.stats["epistemic_total_delta_applied"]
        # Reset pour bypass cooldown sur autre slot
        await network._learn_from_epistemic_closure(
            _build_event(grade=8.0, slot="CODE_REVIEW")
        )
        delta_full = (
            network.stats["epistemic_total_delta_applied"] - delta_low
        )
        assert 0 < delta_low < delta_full

    @pytest.mark.asyncio
    async def test_f1_floor_boundary_applies_partial_factor(self, network):
        """Score exactement a EPISTEMIC_NOTE_FLOOR -> partial_factor minimal."""
        await network._learn_from_epistemic_closure(
            _build_event(grade=EPISTEMIC_NOTE_FLOOR)
        )
        delta = network.stats["epistemic_total_delta_applied"]
        # partial_factor = 0.25 a la frontiere basse
        # surprise = exp(4-5) = 0.368 (au-dessus du floor 0.1, pas clip)
        # normalized_drop = (4/10) * 0.368 = 0.147
        # sum_triangular = 1.0 (3 steps : 0.25 + 0.5 + 0.25)
        # delta_total = 0.147 * 1.0 * 0.18 * 0.25 = 0.0066
        assert delta > 0
        assert delta < 0.01

    @pytest.mark.asyncio
    async def test_f1_threshold_boundary_applies_full_factor(self, network):
        """Score >= EPISTEMIC_MIN_NOTE_FOR_CLOSURE -> partial_factor=1.0."""
        await network._learn_from_epistemic_closure(
            _build_event(grade=EPISTEMIC_MIN_NOTE_FOR_CLOSURE)
        )
        delta_at_threshold = network.stats["epistemic_total_delta_applied"]
        # Comparer avec un score juste sous le seuil (sur autre slot pour cooldown)
        await network._learn_from_epistemic_closure(
            _build_event(grade=EPISTEMIC_MIN_NOTE_FOR_CLOSURE - 0.01,
                         slot="CODE_REVIEW")
        )
        delta_below_threshold = (
            network.stats["epistemic_total_delta_applied"] - delta_at_threshold
        )
        # Avec rampe lineaire, juste sous 7.0 -> partial_factor ~ 0.9975
        # Donc delta presque egal mais legerement inferieur
        assert delta_below_threshold < delta_at_threshold

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


class TestFactualityVeto:
    """Filtre F5 : Veto Epistemique V3.2 (Jean-Michel 2026-04-18)."""

    @pytest.mark.asyncio
    async def test_f5_skip_when_no_proof_of_work(self, network):
        """CODE_REVIEW avec factuality=-1.0 (0 ref parsable) -> skip."""
        event = _build_event(slot="CODE_REVIEW")
        event["factuality_score"] = -1.0
        event["factuality_total_refs"] = 0
        await network._learn_from_epistemic_closure(event)
        assert network.stats.get("epistemic_skipped_no_proof_of_work") == 1
        assert "epistemic_reinforcements" not in network.stats

    @pytest.mark.asyncio
    async def test_f5_skip_only_on_structural_slots(self, network):
        """RESEARCH avec factuality=-1.0 : continue normalement (pas structurel)."""
        event = _build_event(slot="RESEARCH")
        event["factuality_score"] = -1.0
        await network._learn_from_epistemic_closure(event)
        assert network.stats.get("epistemic_reinforcements") == 1
        assert "epistemic_skipped_no_proof_of_work" not in network.stats

    @pytest.mark.asyncio
    async def test_f5_veto_triggers_extinction(self, network):
        """factuality < 0.6 -> extinction sur synapses pre-existantes."""
        # Premiere fermeture valide pour creer les synapses
        ok_event = _build_event(slot="CODE_REVIEW", grade=9.0)
        ok_event["factuality_score"] = 0.9
        ok_event["factuality_total_refs"] = 10
        await network._learn_from_epistemic_closure(ok_event)
        # Bypass cooldown
        network._epistemic_last_closure["CODE_REVIEW"] = 0
        # Capturer les poids AVANT veto
        from core.synaptic_network import _make_node_id, _synapse_key
        drive_nid = _make_node_id("pulsion:maitrise_epistemic")
        conclude_nid = _make_node_id("SCHOOL_CODE_REVIEW_CONCLUDE")
        key = _synapse_key(conclude_nid, drive_nid)
        weight_before = network.synapses[key]["weight"]
        # Second livrable hallucine
        bad_event = _build_event(slot="CODE_REVIEW", grade=8.7)
        bad_event["factuality_score"] = 0.3
        bad_event["factuality_total_refs"] = 10
        await network._learn_from_epistemic_closure(bad_event)
        assert network.stats.get("epistemic_veto_hallucination") == 1
        # Le poids doit avoir DIMINUE (extinction)
        weight_after = network.synapses[key]["weight"]
        assert weight_after < weight_before

    @pytest.mark.asyncio
    async def test_f5_above_threshold_proceeds(self, network):
        """factuality >= 0.6 -> fermeture normale."""
        event = _build_event(slot="CODE_REVIEW", grade=8.5)
        event["factuality_score"] = 0.75
        event["factuality_total_refs"] = 4
        await network._learn_from_epistemic_closure(event)
        assert network.stats.get("epistemic_reinforcements") == 1
        assert "epistemic_veto_hallucination" not in network.stats

    @pytest.mark.asyncio
    async def test_f5_veto_when_drive_does_not_exist(self, network):
        """factuality bas au tout premier livrable : drive pas encore ne -> warning."""
        event = _build_event(slot="CODE_REVIEW")
        event["factuality_score"] = 0.2
        event["factuality_total_refs"] = 5
        await network._learn_from_epistemic_closure(event)
        assert network.stats.get("epistemic_veto_no_drive_yet") == 1

    @pytest.mark.asyncio
    async def test_f5_veto_no_cooldown_set(self, network):
        """Un veto NE DOIT PAS enregistrer de cooldown (prochain livrable libre)."""
        event = _build_event(slot="CODE_REVIEW")
        event["factuality_score"] = 0.2
        event["factuality_total_refs"] = 5
        await network._learn_from_epistemic_closure(event)
        assert "CODE_REVIEW" not in network._epistemic_last_closure


class TestSoftCap:
    """V4.1 Soft Cap plasticite (2026-04-19, Gemini)."""

    def test_soft_cap_activates_above_threshold(self, network):
        """Si weight > 0.85, delta est divise par 4."""
        from core.synaptic_network import _synapse_key, SOFT_CAP_DIVISOR
        # Creer deux noeuds + une synapse pre-chargee a ~0.90
        src_nid = network.ensure_node("concept_a", "event", 0.5, ["test"])
        tgt_nid = network.ensure_node("concept_b", "event", 0.5, ["test"])
        # Preload : naissance 0.05 + 0.40 + 0.45 = 0.90
        network._apply_causal_delta(src_nid, tgt_nid, 0.40, context="preload1")
        network._apply_causal_delta(src_nid, tgt_nid, 0.45, context="preload2")
        key = _synapse_key(src_nid, tgt_nid)
        w_after_push = network.synapses[key]["weight"]
        assert w_after_push == pytest.approx(0.90, abs=0.001)
        # Nouveau delta de 0.20 : avec soft cap divise par 4 -> 0.05
        network._apply_causal_delta(src_nid, tgt_nid, 0.20, context="soft_capped")
        w_final = network.synapses[key]["weight"]
        # Attendu = 0.90 + 0.20/4 = 0.95
        expected = 0.90 + 0.20 / SOFT_CAP_DIVISOR
        assert w_final == pytest.approx(expected, abs=0.001)
        # Sans soft cap : aurait ete min(1.0, 0.90 + 0.20) = 1.0
        assert w_final < 1.0

    def test_soft_cap_inactive_below_threshold(self, network):
        """Si weight < 0.85, le delta est applique plein."""
        src_nid = network.ensure_node("concept_c", "event", 0.5, ["test"])
        tgt_nid = network.ensure_node("concept_d", "event", 0.5, ["test"])
        # Synapse creee a 0.05 + 0.30 = 0.35. Nouveau delta 0.10 -> 0.45.
        network._apply_causal_delta(src_nid, tgt_nid, 0.30, context="preload")
        from core.synaptic_network import _synapse_key
        key = _synapse_key(src_nid, tgt_nid)
        w_before = network.synapses[key]["weight"]
        network._apply_causal_delta(src_nid, tgt_nid, 0.10, context="normal")
        w_after = network.synapses[key]["weight"]
        assert w_after == pytest.approx(w_before + 0.10, abs=0.001)

    def test_procedural_saturation_blocked(self, network):
        """Simule l'attracteur procedural_v4 qui allait geler a 1.0."""
        from core.synaptic_network import _synapse_key, PROCEDURAL_DELTA
        src_nid = network.ensure_node("REFACTORING_AUDIT", "event", 0.5, ["test"])
        tgt_nid = network.ensure_node("emotion:determination", "affect", 0.5, ["test"])
        # 10 applications de PROCEDURAL_DELTA (0.08 chaque)
        for _ in range(10):
            network._apply_causal_delta(
                src_nid, tgt_nid, PROCEDURAL_DELTA,
                context="procedural_v4:REFACTORING_AUDIT<>determination"
            )
        key = _synapse_key(src_nid, tgt_nid)
        w_final = network.synapses[key]["weight"]
        # Sans soft cap : aurait grimpe bien au-dessus de 0.85 (plafond 1.0)
        # Avec soft cap : monte vers 0.85 puis ralentit
        # 10 * 0.08 = 0.80 cumulatif + naissance 0.05 = 0.85 theorique. Avec
        # soft cap on reste juste autour de 0.85-0.87.
        assert w_final < 1.0
        assert w_final > 0.80  # quand meme monte
