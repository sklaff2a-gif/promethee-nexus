"""Tests pour core/amygdala.py — Amygdale (mémoire émotionnelle)."""

import asyncio
import json
import time
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from core.amygdala import (
    Amygdala, amygdala, EmotionalMemory,
    AMYGDALA_STATE_FILE, EXTINCTION_THRESHOLD, EXTINCTION_DECAY,
    MIN_MEMORY_AROUSAL, MAX_MEMORIES, EMOTIONAL_BIAS_RANGE,
    _APPRAISAL_MAP, _DEFAULT_APPRAISAL, _INTENT_EMOTION_MAP,
)


# ── Fixture autouse ─────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    """Reset singleton + isoler la persistance."""
    monkeypatch.setattr("core.amygdala.AMYGDALA_STATE_FILE",
                        str(tmp_path / "amygdala.json"))
    Amygdala.reset_singleton()
    with patch.object(Amygdala, "_load"):
        amygdala.__init__()
    yield
    Amygdala.reset_singleton()


# ════════════════════════════════════════════════════════════════════
# TestInit
# ════════════════════════════════════════════════════════════════════

class TestInit:
    def test_singleton(self):
        a1 = Amygdala()
        a2 = Amygdala()
        assert a1 is a2

    def test_reset_singleton(self):
        a1 = Amygdala()
        Amygdala.reset_singleton()
        with patch.object(Amygdala, "_load"):
            a2 = Amygdala()
        assert a1 is not a2

    def test_initial_attributes(self):
        assert isinstance(amygdala.memories, dict)
        assert len(amygdala.memories) == 0
        assert amygdala._extinction_cycle == 0

    def test_repr_empty(self):
        r = repr(amygdala)
        assert "Amygdala" in r
        assert "memories=0" in r

    def test_repr_with_memories(self):
        amygdala.condition("pos_test", valence=0.5, arousal=0.5)
        amygdala.condition("neg_test", valence=-0.5, arousal=0.5)
        r = repr(amygdala)
        assert "pos=1" in r
        assert "neg=1" in r

    def test_init_bus_subscriptions(self):
        mock_bus = MagicMock()
        with patch("core.amygdala.bus", mock_bus, create=True):
            with patch.dict("sys.modules", {"core.event_bus": MagicMock(bus=mock_bus)}):
                amygdala._subscribed = False
                amygdala.init()
        assert amygdala._subscribed is True


# ════════════════════════════════════════════════════════════════════
# TestAppraisal
# ════════════════════════════════════════════════════════════════════

class TestAppraisal:
    def test_reptilian_alert(self):
        result = amygdala.appraise("REPTILIAN_ALERT")
        assert result["valence"] == -0.8
        assert result["arousal"] == 0.9
        assert "REPTILIAN_ALERT" in result["memory_key"]

    def test_hallucination_detected(self):
        result = amygdala.appraise("HALLUCINATION_DETECTED")
        assert result["valence"] == -0.6
        assert result["arousal"] == 0.7

    def test_dopamine_surge(self):
        result = amygdala.appraise("DOPAMINE_SURGE")
        assert result["valence"] == 0.7
        assert result["arousal"] == 0.6

    def test_prefrontal_goal_complete(self):
        result = amygdala.appraise("PREFRONTAL_GOAL_COMPLETE")
        assert result["valence"] == 0.8
        assert result["arousal"] == 0.5

    def test_council_end(self):
        result = amygdala.appraise("COUNCIL_END")
        assert result["valence"] == 0.3
        assert result["arousal"] == 0.3

    def test_tissue_pattern_emerged(self):
        result = amygdala.appraise("TISSUE_PATTERN_EMERGED")
        assert result["valence"] == 0.5
        assert result["arousal"] == 0.4

    def test_tissue_extinction_risk(self):
        result = amygdala.appraise("TISSUE_EXTINCTION_RISK")
        assert result["valence"] == -0.5
        assert result["arousal"] == 0.6

    def test_unknown_event(self):
        result = amygdala.appraise("UNKNOWN_EVENT_XYZ")
        assert result["valence"] == _DEFAULT_APPRAISAL[0]
        assert result["arousal"] == _DEFAULT_APPRAISAL[1]

    def test_context_severity_boosts_arousal(self):
        result = amygdala.appraise("REPTILIAN_ALERT", {"severity": 8})
        # arousal = 0.9 + 8*0.05 = 1.3 → clamped 1.0
        assert result["arousal"] == 1.0

    def test_context_success_inverts_valence(self):
        result = amygdala.appraise("HALLUCINATION_DETECTED", {"success": True})
        # valence négatif + success → inversé positif
        assert result["valence"] > 0

    def test_context_failure_inverts_valence(self):
        result = amygdala.appraise("DOPAMINE_SURGE", {"success": False})
        # valence positif + failure → inversé négatif
        assert result["valence"] < 0

    def test_memory_key_with_pattern(self):
        result = amygdala.appraise("REPTILIAN_ALERT", {"pattern": "ollama_down"})
        assert result["memory_key"] == "REPTILIAN_ALERT:ollama_down"

    def test_memory_key_with_intent(self):
        result = amygdala.appraise("COUNCIL_END", {"intent": "EXPANSION_CODE"})
        assert result["memory_key"] == "COUNCIL_END:EXPANSION_CODE"

    def test_appraise_creates_memory(self):
        amygdala.appraise("DOPAMINE_SURGE", {"pattern": "reward"})
        assert "DOPAMINE_SURGE:reward" in amygdala.memories


# ════════════════════════════════════════════════════════════════════
# TestConditioning
# ════════════════════════════════════════════════════════════════════

class TestConditioning:
    def test_condition_new(self):
        amygdala.condition("test_pattern", valence=0.5, arousal=0.6)
        mem = amygdala.memories["test_pattern"]
        assert mem.valence == 0.5
        assert mem.arousal == 0.6
        assert mem.occurrences == 1
        assert mem.extinction_counter == 0

    def test_condition_update_moving_average(self):
        amygdala.condition("test_pattern", valence=1.0, arousal=1.0)
        amygdala.condition("test_pattern", valence=0.0, arousal=0.0)
        mem = amygdala.memories["test_pattern"]
        # Moyenne mobile : 1.0*0.7 + 0.0*0.3 = 0.7
        assert abs(mem.valence - 0.7) < 0.01
        assert abs(mem.arousal - 0.7) < 0.01
        assert mem.occurrences == 2

    def test_condition_resets_extinction(self):
        amygdala.condition("test_pattern", valence=0.5, arousal=0.5)
        amygdala.memories["test_pattern"].extinction_counter = 30
        amygdala.condition("test_pattern", valence=0.5, arousal=0.5)
        assert amygdala.memories["test_pattern"].extinction_counter == 0

    def test_reflex_upgrade(self):
        amygdala.condition("test", valence=-0.5, arousal=0.5, reflex="ADRENALINE")
        amygdala.condition("test", valence=-0.5, arousal=0.5, reflex="FREEZE")
        assert amygdala.memories["test"].conditioned_reflex == "FREEZE"

    def test_reflex_no_downgrade(self):
        amygdala.condition("test", valence=-0.5, arousal=0.5, reflex="FREEZE")
        amygdala.condition("test", valence=-0.5, arousal=0.5, reflex="ADRENALINE")
        assert amygdala.memories["test"].conditioned_reflex == "FREEZE"

    def test_valence_clamping(self):
        amygdala.condition("test", valence=5.0, arousal=3.0)
        mem = amygdala.memories["test"]
        assert mem.valence == 1.0
        assert mem.arousal == 1.0

    def test_valence_clamping_negative(self):
        amygdala.condition("test", valence=-5.0, arousal=-1.0)
        mem = amygdala.memories["test"]
        assert mem.valence == -1.0
        assert mem.arousal == 0.0

    def test_condition_with_empty_reflex_sets_reflex(self):
        amygdala.condition("test", valence=-0.5, arousal=0.5, reflex="")
        amygdala.condition("test", valence=-0.5, arousal=0.5, reflex="SHED")
        assert amygdala.memories["test"].conditioned_reflex == "SHED"


# ════════════════════════════════════════════════════════════════════
# TestExtinction
# ════════════════════════════════════════════════════════════════════

class TestExtinction:
    def test_counter_increment(self):
        amygdala.condition("test", valence=0.5, arousal=0.8)
        amygdala.tick_extinction()
        assert amygdala.memories["test"].extinction_counter == 1

    def test_no_decay_before_threshold(self):
        amygdala.condition("test", valence=0.5, arousal=0.8)
        for _ in range(EXTINCTION_THRESHOLD):
            amygdala.tick_extinction()
        # Counter == THRESHOLD, pas encore > THRESHOLD
        assert amygdala.memories["test"].arousal == 0.8

    def test_decay_after_threshold(self):
        amygdala.condition("test", valence=0.5, arousal=0.8)
        for _ in range(EXTINCTION_THRESHOLD + 1):
            amygdala.tick_extinction()
        assert amygdala.memories["test"].arousal == pytest.approx(0.8 * EXTINCTION_DECAY, abs=0.001)

    def test_suppression_below_min(self):
        amygdala.condition("test", valence=0.5, arousal=MIN_MEMORY_AROUSAL + 0.01)
        # Forcer extinction_counter au-delà du seuil
        amygdala.memories["test"].extinction_counter = EXTINCTION_THRESHOLD
        # Un tick de plus → decay → sous le min → supprimé
        amygdala.tick_extinction()
        # arousal = (MIN + 0.01) * 0.95 ≈ 0.057 > MIN → pas encore supprimé
        # Forçons avec un arousal très bas
        amygdala.memories["test"].arousal = MIN_MEMORY_AROUSAL * 0.9
        amygdala.memories["test"].extinction_counter = EXTINCTION_THRESHOLD
        amygdala.tick_extinction()
        assert "test" not in amygdala.memories

    def test_extinction_cycle_counter(self):
        amygdala.tick_extinction()
        amygdala.tick_extinction()
        assert amygdala._extinction_cycle == 2

    def test_max_memories_eviction(self):
        for i in range(MAX_MEMORIES + 5):
            amygdala.condition(f"mem_{i}", valence=0.5, arousal=float(i) / MAX_MEMORIES)
        assert len(amygdala.memories) <= MAX_MEMORIES

    def test_evict_weakest(self):
        amygdala.condition("strong", valence=0.5, arousal=0.9)
        amygdala.condition("weak", valence=0.5, arousal=0.01)
        amygdala._evict_weakest()
        assert "strong" in amygdala.memories
        assert "weak" not in amygdala.memories


# ════════════════════════════════════════════════════════════════════
# TestEmotionalBias
# ════════════════════════════════════════════════════════════════════

class TestEmotionalBias:
    def test_bias_positive(self):
        amygdala.condition("code_success", valence=0.8, arousal=0.6)
        bias = amygdala.compute_emotional_bias("EXPANSION_CODE")
        assert bias > 0

    def test_bias_negative(self):
        amygdala.condition("code_error", valence=-0.8, arousal=0.6)
        bias = amygdala.compute_emotional_bias("EXPANSION_CODE")
        assert bias < 0

    def test_bias_neutral_no_memories(self):
        bias = amygdala.compute_emotional_bias("EXPANSION_CODE")
        assert bias == 0.0

    def test_bias_clamping_positive(self):
        # Forcer des mémoires très intenses
        amygdala.condition("code_success", valence=1.0, arousal=1.0)
        amygdala.condition("code_error", valence=1.0, arousal=1.0)
        amygdala.condition("HALLUCINATION_DETECTED", valence=1.0, arousal=1.0)
        bias = amygdala.compute_emotional_bias("EXPANSION_CODE")
        assert bias <= EMOTIONAL_BIAS_RANGE[1]

    def test_bias_clamping_negative(self):
        amygdala.condition("code_success", valence=-1.0, arousal=1.0)
        amygdala.condition("code_error", valence=-1.0, arousal=1.0)
        amygdala.condition("HALLUCINATION_DETECTED", valence=-1.0, arousal=1.0)
        bias = amygdala.compute_emotional_bias("EXPANSION_CODE")
        assert bias >= EMOTIONAL_BIAS_RANGE[0]

    def test_intent_without_mapping(self):
        bias = amygdala.compute_emotional_bias("NONEXISTENT_INTENT_XYZ")
        assert bias == 0.0

    def test_bias_includes_intent_substring_match(self):
        amygdala.condition("expansion_code_related", valence=0.6, arousal=0.5)
        bias = amygdala.compute_emotional_bias("EXPANSION_CODE")
        assert bias > 0

    def test_bias_mixed_valence(self):
        amygdala.condition("code_success", valence=0.8, arousal=0.6)
        amygdala.condition("code_error", valence=-0.8, arousal=0.6)
        bias = amygdala.compute_emotional_bias("EXPANSION_CODE")
        # Devrait être proche de 0 (positif et négatif s'annulent)
        assert abs(bias) < 0.5


# ════════════════════════════════════════════════════════════════════
# TestBusHandlers
# ════════════════════════════════════════════════════════════════════

class TestBusHandlers:
    @pytest.mark.asyncio
    async def test_on_reptilian_alert(self):
        await amygdala._on_reptilian_alert({
            "threat_level": 7,
            "reflex": "FREEZE",
        })
        # Doit créer une mémoire négative
        found = [m for m in amygdala.memories.values() if m.valence < 0]
        assert len(found) >= 1

    @pytest.mark.asyncio
    async def test_on_hallucination(self):
        await amygdala._on_hallucination({"type": "alien_import"})
        found = [m for m in amygdala.memories.values()
                 if "HALLUCINATION" in m.pattern]
        assert len(found) >= 1

    @pytest.mark.asyncio
    async def test_on_dopamine_surge(self):
        await amygdala._on_dopamine_surge({"trigger": "reward"})
        found = [m for m in amygdala.memories.values() if m.valence > 0]
        assert len(found) >= 1

    @pytest.mark.asyncio
    async def test_on_goal_complete(self):
        await amygdala._on_goal_complete({"goal_id": "goal_123"})
        found = [m for m in amygdala.memories.values() if m.valence > 0]
        assert len(found) >= 1

    @pytest.mark.asyncio
    async def test_on_council_end(self):
        await amygdala._on_council_end({"topic": "architecture"})
        assert len(amygdala.memories) >= 1

    @pytest.mark.asyncio
    async def test_on_tissue_pattern(self):
        await amygdala._on_tissue_pattern({"pattern": "cluster_A"})
        found = [m for m in amygdala.memories.values() if m.valence > 0]
        assert len(found) >= 1

    @pytest.mark.asyncio
    async def test_on_tissue_extinction(self):
        await amygdala._on_tissue_extinction({"zone": "cognition"})
        found = [m for m in amygdala.memories.values() if m.valence < 0]
        assert len(found) >= 1

    @pytest.mark.asyncio
    async def test_on_routine_complete_success(self):
        await amygdala._on_routine_complete({
            "intent": "EXPANSION_CODE",
            "status": "success",
        })
        assert "expansion_code_success" in amygdala.memories
        mem = amygdala.memories["expansion_code_success"]
        assert mem.valence > 0

    @pytest.mark.asyncio
    async def test_on_routine_complete_error(self):
        await amygdala._on_routine_complete({
            "intent": "EXPANSION_CODE",
            "status": "error",
        })
        assert "expansion_code_error" in amygdala.memories
        mem = amygdala.memories["expansion_code_error"]
        assert mem.valence < 0

    @pytest.mark.asyncio
    async def test_on_routine_complete_unknown_status(self):
        await amygdala._on_routine_complete({
            "intent": "TEST",
            "status": "pending",
        })
        # Pas de conditionnement pour statut inconnu
        assert "test_pending" not in amygdala.memories


# ════════════════════════════════════════════════════════════════════
# TestPersistence
# ════════════════════════════════════════════════════════════════════

class TestPersistence:
    def test_save_creates_file(self, tmp_path, monkeypatch):
        path = str(tmp_path / "amygdala.json")
        monkeypatch.setattr("core.amygdala.AMYGDALA_STATE_FILE", path)
        amygdala.condition("test", valence=0.5, arousal=0.6)
        amygdala.save()
        assert os.path.exists(path)

    def test_roundtrip(self, tmp_path, monkeypatch):
        path = str(tmp_path / "amygdala.json")
        monkeypatch.setattr("core.amygdala.AMYGDALA_STATE_FILE", path)
        amygdala.condition("pattern_a", valence=0.7, arousal=0.8, reflex="SHED")
        amygdala.condition("pattern_b", valence=-0.3, arousal=0.4)
        amygdala._extinction_cycle = 42
        amygdala.save()

        # Reset et recharger
        Amygdala.reset_singleton()
        new_amyg = Amygdala()
        assert len(new_amyg.memories) == 2
        assert "pattern_a" in new_amyg.memories
        assert new_amyg.memories["pattern_a"].conditioned_reflex == "SHED"
        assert new_amyg._extinction_cycle == 42

    def test_load_empty(self, tmp_path, monkeypatch):
        path = str(tmp_path / "amygdala.json")
        monkeypatch.setattr("core.amygdala.AMYGDALA_STATE_FILE", path)
        # Pas de fichier → pas d'erreur
        Amygdala.reset_singleton()
        new_amyg = Amygdala()
        assert len(new_amyg.memories) == 0

    def test_load_bad_data(self, tmp_path, monkeypatch):
        path = str(tmp_path / "amygdala.json")
        monkeypatch.setattr("core.amygdala.AMYGDALA_STATE_FILE", path)
        with open(path, "w") as f:
            f.write("{invalid json")
        Amygdala.reset_singleton()
        new_amyg = Amygdala()
        assert len(new_amyg.memories) == 0

    def test_migration_threat_memory(self, tmp_path, monkeypatch):
        """Vérifie la migration depuis l'ancien format ThreatMemory."""
        path = str(tmp_path / "amygdala.json")
        monkeypatch.setattr("core.amygdala.AMYGDALA_STATE_FILE", path)
        old_data = {
            "memories": {
                "ollama_down": {
                    "pattern": "ollama_down",
                    "severity": 7.0,
                    "occurrences": 3,
                    "last_seen": time.time(),
                    "conditioned_reflex": "FREEZE",
                }
            },
            "extinction_cycle": 5,
        }
        with open(path, "w") as f:
            json.dump(old_data, f)

        Amygdala.reset_singleton()
        new_amyg = Amygdala()
        assert "ollama_down" in new_amyg.memories
        mem = new_amyg.memories["ollama_down"]
        assert mem.valence < 0  # Converti en négatif
        assert mem.arousal > 0
        assert mem.conditioned_reflex == "FREEZE"

    def test_to_dict_format(self):
        amygdala.condition("test", valence=0.5, arousal=0.6)
        from dataclasses import asdict
        d = asdict(amygdala.memories["test"])
        assert "pattern" in d
        assert "valence" in d
        assert "arousal" in d
        assert "occurrences" in d
        assert "extinction_counter" in d


# ════════════════════════════════════════════════════════════════════
# TestGetConditionedResponse
# ════════════════════════════════════════════════════════════════════

class TestGetConditionedResponse:
    def test_existing_pattern(self):
        amygdala.condition("test_p", valence=-0.5, arousal=0.7, reflex="SHED")
        mem = amygdala.get_conditioned_response("test_p")
        assert mem is not None
        assert mem.conditioned_reflex == "SHED"

    def test_missing_pattern(self):
        mem = amygdala.get_conditioned_response("nonexistent")
        assert mem is None


import os
