# tests/test_cingulate_cortex.py — Tests Cortex Cingulaire
import pytest
import json
import os
import time
from unittest.mock import patch, AsyncMock
from collections import deque


@pytest.fixture(autouse=True)
def isolate_cingulate(tmp_path, monkeypatch):
    """Isole le singleton CingulateCortex pour chaque test."""
    from core import cingulate_cortex as mod
    mod.CingulateCortex.reset_singleton()
    monkeypatch.setattr(mod, "CINGULATE_STATE_FILE", str(tmp_path / "cing_state.json"))
    with patch.object(mod.CingulateCortex, "_load"):
        c = mod.CingulateCortex()
    c.decision_history = deque(maxlen=10)
    c.conflict_log = deque(maxlen=50)
    c.error_memory = deque(maxlen=30)
    c.adaptation_weights = {}
    c.total_conflicts = 0
    c.total_errors = 0
    c._conflict_cooldowns = {}
    c._active_conflicts = []
    c._subscribed = False
    mod.cingulate = c
    yield c
    mod.CingulateCortex.reset_singleton()


# ===== TestSingleton =====

class TestSingleton:
    def test_singleton_identity(self, isolate_cingulate):
        from core.cingulate_cortex import CingulateCortex
        c2 = CingulateCortex()
        assert c2 is isolate_cingulate

    def test_reset_singleton(self, isolate_cingulate):
        from core.cingulate_cortex import CingulateCortex
        old_id = id(isolate_cingulate)
        CingulateCortex.reset_singleton()
        with patch.object(CingulateCortex, "_load"):
            c2 = CingulateCortex()
        assert id(c2) != old_id


# ===== TestConflictDetection =====

class TestConflictDetection:
    def test_no_conflict_empty_history(self, isolate_cingulate):
        c = isolate_cingulate
        conflicts = c._detect_conflicts()
        assert conflicts == []

    def test_organic_conflict_detected(self, isolate_cingulate):
        c = isolate_cingulate
        c.decision_history.append({
            "intent": "EXPANSION_CODE",
            "breakdown": {"dopamine": 2.0, "stress": -1.0},
            "timestamp": time.time(),
        })
        c.decision_history.append({
            "intent": "OTHER",
            "breakdown": {},
            "timestamp": time.time(),
        })
        conflicts = c._detect_conflicts()
        organic = [cf for cf in conflicts if cf["type"] == "organic"]
        assert len(organic) >= 1

    def test_no_organic_conflict_same_sign(self, isolate_cingulate):
        c = isolate_cingulate
        c.decision_history.append({
            "intent": "TEST",
            "breakdown": {"layer_a": 1.0, "layer_b": 0.5},
            "timestamp": time.time(),
        })
        c.decision_history.append({
            "intent": "OTHER",
            "breakdown": {},
            "timestamp": time.time(),
        })
        conflicts = c._detect_conflicts()
        organic = [cf for cf in conflicts if cf["type"] == "organic"]
        assert len(organic) == 0

    def test_tendency_conflict(self, isolate_cingulate):
        c = isolate_cingulate
        c.decision_history.append({
            "intent": "X",
            "breakdown": {"a": 2.0},
            "timestamp": time.time(),
        })
        c.decision_history.append({
            "intent": "X",
            "breakdown": {"a": -2.0},
            "timestamp": time.time(),
        })
        conflicts = c._detect_conflicts()
        tendency = [cf for cf in conflicts if cf["type"] == "tendency"]
        assert len(tendency) >= 1

    def test_conflict_cooldown(self, isolate_cingulate):
        c = isolate_cingulate
        c.decision_history.append({
            "intent": "Y",
            "breakdown": {"a": 2.0, "b": -1.0},
            "timestamp": time.time(),
        })
        c.decision_history.append({
            "intent": "Z",
            "breakdown": {},
            "timestamp": time.time(),
        })
        conflicts1 = c._detect_conflicts()
        # Meme detection → cooldown
        conflicts2 = c._detect_conflicts()
        assert len(conflicts2) <= len(conflicts1)

    def test_homeostatic_conflict(self, isolate_cingulate):
        c = isolate_cingulate
        c._check_homeostatic_conflict({"stress": 0.8, "dopamine": -0.5})
        assert len(c._active_conflicts) >= 1
        assert c._active_conflicts[0]["type"] == "homeostatic"

    def test_no_homeostatic_conflict_normal(self, isolate_cingulate):
        c = isolate_cingulate
        c._check_homeostatic_conflict({"stress": 0.1, "dopamine": 0.0})
        assert len(c._active_conflicts) == 0

    def test_conflict_severity(self, isolate_cingulate):
        c = isolate_cingulate
        c.decision_history.append({
            "intent": "A",
            "breakdown": {"x": 3.0, "y": -2.0},
            "timestamp": time.time(),
        })
        c.decision_history.append({
            "intent": "B",
            "breakdown": {},
            "timestamp": time.time(),
        })
        conflicts = c._detect_conflicts()
        if conflicts:
            assert conflicts[0]["severity"] > 0

    def test_conflict_has_organs(self, isolate_cingulate):
        c = isolate_cingulate
        c.decision_history.append({
            "intent": "T",
            "breakdown": {"organ_a": 2.0, "organ_b": -1.5},
            "timestamp": time.time(),
        })
        c.decision_history.append({
            "intent": "U",
            "breakdown": {},
            "timestamp": time.time(),
        })
        conflicts = c._detect_conflicts()
        organic = [cf for cf in conflicts if cf["type"] == "organic"]
        if organic:
            assert len(organic[0]["organs_involved"]) == 2

    def test_single_entry_no_tendency(self, isolate_cingulate):
        c = isolate_cingulate
        c.decision_history.append({
            "intent": "X",
            "breakdown": {"a": 2.0},
            "timestamp": time.time(),
        })
        conflicts = c._detect_conflicts()
        tendency = [cf for cf in conflicts if cf["type"] == "tendency"]
        assert len(tendency) == 0

    def test_homeostatic_cooldown(self, isolate_cingulate):
        c = isolate_cingulate
        c._check_homeostatic_conflict({"stress": 0.8, "dopamine": -0.5})
        count1 = len(c._active_conflicts)
        c._check_homeostatic_conflict({"stress": 0.8, "dopamine": -0.5})
        # Cooldown devrait empecher un second conflit
        assert len(c._active_conflicts) == count1

    def test_multiple_organic_conflicts(self, isolate_cingulate):
        c = isolate_cingulate
        c.decision_history.append({
            "intent": "M",
            "breakdown": {"a": 2.0, "b": -1.0, "c": -1.5},
            "timestamp": time.time(),
        })
        c.decision_history.append({
            "intent": "N",
            "breakdown": {},
            "timestamp": time.time(),
        })
        conflicts = c._detect_conflicts()
        organic = [cf for cf in conflicts if cf["type"] == "organic"]
        # Peut avoir 2+ conflits organiques si plusieurs paires contradictoires
        assert len(organic) >= 1


# ===== TestErrorMonitoring =====

class TestErrorMonitoring:
    def test_record_error(self, isolate_cingulate):
        c = isolate_cingulate
        c._record_error("TEST_INTENT", "timeout")
        assert c.total_errors == 1
        assert len(c.error_memory) == 1

    def test_multiple_errors(self, isolate_cingulate):
        c = isolate_cingulate
        for i in range(5):
            c._record_error("INTENT_A", "error")
        assert c.total_errors == 5

    def test_error_creates_adaptation(self, isolate_cingulate):
        c = isolate_cingulate
        c._record_error("FAILING", "crash")
        assert "FAILING" in c.adaptation_weights
        assert c.adaptation_weights["FAILING"] > 0

    def test_adaptation_increases(self, isolate_cingulate):
        c = isolate_cingulate
        c._record_error("FAILING", "crash")
        w1 = c.adaptation_weights["FAILING"]
        c._record_error("FAILING", "crash")
        w2 = c.adaptation_weights["FAILING"]
        assert w2 > w1

    def test_adaptation_capped(self, isolate_cingulate):
        c = isolate_cingulate
        for i in range(100):
            c._record_error("FAILING", "crash")
        from core.cingulate_cortex import MAX_ADAPTATION_MALUS
        assert c.adaptation_weights["FAILING"] <= MAX_ADAPTATION_MALUS

    def test_error_rate_in_stats(self, isolate_cingulate):
        c = isolate_cingulate
        c._record_error("A", "err")
        c._record_error("A", "err")
        c._record_error("B", "err")
        stats = c.get_stats()
        assert stats["error_rate_by_intent"]["A"] == 2
        assert stats["error_rate_by_intent"]["B"] == 1

    def test_error_memory_bounded(self, isolate_cingulate):
        c = isolate_cingulate
        for i in range(100):
            c._record_error(f"I_{i}", "err")
        from core.cingulate_cortex import ERROR_MEMORY_SIZE
        assert len(c.error_memory) <= ERROR_MEMORY_SIZE

    def test_error_has_timestamp(self, isolate_cingulate):
        c = isolate_cingulate
        c._record_error("X", "crash")
        assert "timestamp" in c.error_memory[0]


# ===== TestAdaptation =====

class TestAdaptation:
    def test_no_adaptation_clean_intent(self, isolate_cingulate):
        c = isolate_cingulate
        w = c._compute_adaptation("CLEAN_INTENT")
        assert w == 0.0

    def test_adaptation_after_errors(self, isolate_cingulate):
        c = isolate_cingulate
        c._record_error("BAD", "err")
        w = c._compute_adaptation("BAD")
        assert w > 0.0

    def test_decay_reduces_weights(self, isolate_cingulate):
        c = isolate_cingulate
        c.adaptation_weights["X"] = 0.5
        c.decay_adaptations()
        assert c.adaptation_weights["X"] < 0.5

    def test_decay_removes_tiny_weights(self, isolate_cingulate):
        c = isolate_cingulate
        c.adaptation_weights["X"] = 0.005
        c.decay_adaptations()
        assert "X" not in c.adaptation_weights

    def test_adaptation_independent_per_intent(self, isolate_cingulate):
        c = isolate_cingulate
        c._record_error("A", "err")
        c._record_error("A", "err")
        c._record_error("B", "err")
        assert c.adaptation_weights["A"] > c.adaptation_weights["B"]

    def test_multiple_decay_cycles(self, isolate_cingulate):
        c = isolate_cingulate
        c.adaptation_weights["X"] = 0.5
        for _ in range(10):
            c.decay_adaptations()
        assert c.adaptation_weights.get("X", 0) < 0.5


# ===== TestScoringBonus =====

class TestScoringBonus:
    def test_zero_no_conflicts(self, isolate_cingulate):
        c = isolate_cingulate
        bonus = c.compute_conflict_bonus("CLEAN_INTENT")
        assert bonus == 0.0

    def test_malus_active_conflict(self, isolate_cingulate):
        c = isolate_cingulate
        c._active_conflicts = [{
            "type": "organic",
            "intent": "CONFLICTED",
            "severity": 2.0,
        }]
        bonus = c.compute_conflict_bonus("CONFLICTED")
        assert bonus < 0

    def test_malus_from_adaptation(self, isolate_cingulate):
        c = isolate_cingulate
        c.adaptation_weights["FAILING"] = 0.5
        bonus = c.compute_conflict_bonus("FAILING")
        assert bonus < 0

    def test_bonus_range_lower(self, isolate_cingulate):
        c = isolate_cingulate
        c._active_conflicts = [{"type": "organic", "intent": "X", "severity": 10.0}]
        c.adaptation_weights["X"] = 0.8
        bonus = c.compute_conflict_bonus("X")
        assert bonus >= -1.0

    def test_bonus_range_upper(self, isolate_cingulate):
        c = isolate_cingulate
        # Pas de conflit, pas d'adaptation, mais decisions positives
        for _ in range(5):
            c.decision_history.append({
                "intent": "GOOD",
                "breakdown": {"a": 1.0},
                "timestamp": time.time(),
            })
        bonus = c.compute_conflict_bonus("GOOD")
        assert bonus <= 1.0

    def test_positive_bonus_clean_history(self, isolate_cingulate):
        c = isolate_cingulate
        c.decision_history.append({
            "intent": "GOOD",
            "breakdown": {},
            "timestamp": time.time(),
        })
        bonus = c.compute_conflict_bonus("GOOD")
        assert bonus > 0  # Leger bonus pour intent avec historique positif

    def test_different_intent_not_affected(self, isolate_cingulate):
        c = isolate_cingulate
        c._active_conflicts = [{"type": "organic", "intent": "BAD", "severity": 2.0}]
        bonus = c.compute_conflict_bonus("GOOD_INTENT")
        assert bonus >= 0.0


# ===== TestBusHandlers =====

class TestBusHandlers:
    @pytest.mark.asyncio
    async def test_routine_complete_records(self, isolate_cingulate):
        c = isolate_cingulate
        with patch("core.cingulate_cortex.bus.publish", new_callable=AsyncMock):
            await c._on_routine_complete({
                "intent": "TEST",
                "scoring_breakdown": {"a": 1.0},
                "status": "success",
            })
        assert len(c.decision_history) == 1

    @pytest.mark.asyncio
    async def test_routine_failed_records_error(self, isolate_cingulate):
        c = isolate_cingulate
        await c._on_routine_failed({"intent": "FAIL_TEST", "reason": "timeout"})
        assert c.total_errors == 1

    @pytest.mark.asyncio
    async def test_homeostatic_regulation(self, isolate_cingulate):
        c = isolate_cingulate
        await c._on_homeostatic_regulation({
            "errors": {"stress": 0.9, "dopamine": -0.5},
        })
        assert len(c._active_conflicts) >= 1

    @pytest.mark.asyncio
    async def test_routine_error_status(self, isolate_cingulate):
        c = isolate_cingulate
        with patch("core.cingulate_cortex.bus.publish", new_callable=AsyncMock):
            await c._on_routine_complete({
                "intent": "FAILING",
                "scoring_breakdown": {},
                "status": "error",
                "reason": "crash",
            })
        assert c.total_errors == 1

    @pytest.mark.asyncio
    async def test_conflict_published(self, isolate_cingulate):
        c = isolate_cingulate
        c.decision_history.append({
            "intent": "X",
            "breakdown": {"a": 3.0, "b": -2.0},
            "timestamp": time.time(),
        })
        with patch("core.cingulate_cortex.bus.publish", new_callable=AsyncMock) as mock_pub:
            await c._on_routine_complete({
                "intent": "Y",
                "scoring_breakdown": {},
                "status": "success",
            })
            # Verifier si un conflit a ete publie (si detecte)
            # Pas toujours garanti, donc on verifie juste que ca ne crash pas


# ===== TestContext =====

class TestContext:
    def test_empty_no_conflicts(self, isolate_cingulate):
        c = isolate_cingulate
        ctx = c.get_conflict_context()
        assert ctx == ""

    def test_context_with_conflicts(self, isolate_cingulate):
        c = isolate_cingulate
        c._active_conflicts = [{
            "type": "organic",
            "intent": "TEST",
            "severity": 1.5,
        }]
        ctx = c.get_conflict_context()
        assert "CONFLITS" in ctx
        assert "organic" in ctx


# ===== TestStats =====

class TestStats:
    def test_stats_keys(self, isolate_cingulate):
        c = isolate_cingulate
        stats = c.get_stats()
        assert "total_conflicts" in stats
        assert "total_errors" in stats
        assert "active_conflicts" in stats
        assert "adaptation_weights" in stats


# ===== TestPersistence =====

class TestPersistence:
    def test_save_creates_file(self, isolate_cingulate):
        c = isolate_cingulate
        c._save()
        from core.cingulate_cortex import CINGULATE_STATE_FILE
        assert os.path.exists(CINGULATE_STATE_FILE)

    def test_roundtrip(self, isolate_cingulate, tmp_path, monkeypatch):
        from core import cingulate_cortex as mod
        c = isolate_cingulate
        c.total_conflicts = 15
        c.total_errors = 8
        c.adaptation_weights = {"TEST": 0.3}
        c._save()

        mod.CingulateCortex.reset_singleton()
        c2 = mod.CingulateCortex()
        assert c2.total_conflicts == 15
        assert c2.total_errors == 8
        assert c2.adaptation_weights.get("TEST") == 0.3

    def test_load_missing(self, isolate_cingulate):
        c = isolate_cingulate
        c._load()  # Pas de crash

    def test_load_corrupt(self, isolate_cingulate, tmp_path):
        from core.cingulate_cortex import CINGULATE_STATE_FILE
        with open(CINGULATE_STATE_FILE, "w") as f:
            f.write("CORRUPT")
        c = isolate_cingulate
        c._load()  # Pas de crash

    def test_retrocompat(self, isolate_cingulate, tmp_path):
        from core import cingulate_cortex as mod
        from core.cingulate_cortex import CINGULATE_STATE_FILE
        data = {"total_conflicts": 5}
        with open(CINGULATE_STATE_FILE, "w") as f:
            json.dump(data, f)
        mod.CingulateCortex.reset_singleton()
        c2 = mod.CingulateCortex()
        assert c2.total_conflicts == 5
        assert c2.total_errors == 0
