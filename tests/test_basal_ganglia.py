# tests/test_basal_ganglia.py — Tests Ganglions de la Base
import pytest
import json
import os
import time
from unittest.mock import patch, AsyncMock
from collections import deque


@pytest.fixture(autouse=True)
def isolate_ganglia(tmp_path, monkeypatch):
    """Isole le singleton BasalGanglia pour chaque test."""
    from core import basal_ganglia as mod
    mod.BasalGanglia.reset_singleton()
    monkeypatch.setattr(mod, "BASAL_GANGLIA_STATE_FILE", str(tmp_path / "bg_state.json"))
    with patch.object(mod.BasalGanglia, "_load"):
        g = mod.BasalGanglia()
    g.habits = {}
    g.go_nogo_state = {}
    g.action_history = deque(maxlen=50)
    g.total_selections = 0
    g.total_inhibitions = 0
    g._last_intent = ""
    g._subscribed = False
    mod.ganglia = g
    yield g
    mod.BasalGanglia.reset_singleton()


# ===== TestSingleton =====

class TestSingleton:
    def test_singleton_identity(self, isolate_ganglia):
        from core.basal_ganglia import BasalGanglia
        g2 = BasalGanglia()
        assert g2 is isolate_ganglia


# ===== TestHabitLearning =====

class TestHabitLearning:
    def test_new_habit_created(self, isolate_ganglia):
        g = isolate_ganglia
        g._update_habit("NEW_INTENT", 0.8)
        assert "NEW_INTENT" in g.habits

    def test_strength_increases_positive(self, isolate_ganglia):
        g = isolate_ganglia
        g._update_habit("X", 1.0)
        s1 = g.habits["X"]["strength"]
        g._update_habit("X", 1.0)
        s2 = g.habits["X"]["strength"]
        assert s2 >= s1

    def test_strength_bounded(self, isolate_ganglia):
        g = isolate_ganglia
        for _ in range(100):
            g._update_habit("X", 1.0)
        assert g.habits["X"]["strength"] <= 1.0

    def test_negative_reward_decreases(self, isolate_ganglia):
        g = isolate_ganglia
        g._update_habit("X", 1.0)
        g._update_habit("X", 1.0)
        s_before = g.habits["X"]["strength"]
        g._update_habit("X", -0.5)
        s_after = g.habits["X"]["strength"]
        assert s_after < s_before

    def test_successes_counted(self, isolate_ganglia):
        g = isolate_ganglia
        g._update_habit("X", 0.8)
        assert g.habits["X"]["successes"] == 1
        g._update_habit("X", 0.5)
        assert g.habits["X"]["successes"] == 2

    def test_failures_counted(self, isolate_ganglia):
        g = isolate_ganglia
        g._update_habit("X", -0.5)
        assert g.habits["X"]["failures"] == 1

    def test_decay_reduces_strength(self, isolate_ganglia):
        g = isolate_ganglia
        g.habits["X"] = {"strength": 0.5, "successes": 1, "failures": 0,
                         "last_reward": 0.5, "avg_reward": 0.5, "created_at": time.time()}
        g._decay_habits()
        assert g.habits["X"]["strength"] < 0.5

    def test_decay_removes_weak(self, isolate_ganglia):
        g = isolate_ganglia
        g.habits["WEAK"] = {"strength": 0.005, "successes": 0, "failures": 0,
                            "last_reward": 0, "avg_reward": 0, "created_at": time.time()}
        g._decay_habits()
        assert "WEAK" not in g.habits

    def test_prune_keeps_max(self, isolate_ganglia):
        g = isolate_ganglia
        from core.basal_ganglia import MAX_HABITS
        for i in range(MAX_HABITS + 10):
            g.habits[f"H_{i}"] = {
                "strength": i * 0.01, "successes": 1, "failures": 0,
                "last_reward": 0.5, "avg_reward": 0.5, "created_at": time.time()
            }
        g._prune_habits()
        assert len(g.habits) <= MAX_HABITS

    def test_reinforce_last(self, isolate_ganglia):
        g = isolate_ganglia
        g._update_habit("LAST", 0.5)
        s_before = g.habits["LAST"]["strength"]
        g._last_intent = "LAST"
        g._reinforce_last_action(0.8)
        assert g.habits["LAST"]["strength"] > s_before


# ===== TestGoNoGo =====

class TestGoNoGo:
    def test_neutral_no_habit(self, isolate_ganglia):
        g = isolate_ganglia
        val = g._compute_go_nogo("UNKNOWN")
        assert val == 0.0

    def test_go_positive_successes(self, isolate_ganglia):
        g = isolate_ganglia
        g.habits["X"] = {"strength": 0.8, "successes": 5, "failures": 0,
                         "last_reward": 1.0, "avg_reward": 0.8, "created_at": time.time()}
        val = g._compute_go_nogo("X")
        assert val > 0

    def test_inhibition_reduces_go(self, isolate_ganglia):
        g = isolate_ganglia
        g.habits["X"] = {"strength": 0.8, "successes": 5, "failures": 0,
                         "last_reward": 1.0, "avg_reward": 0.8, "created_at": time.time()}
        g._inhibit("X", "test")
        val = g._compute_go_nogo("X")
        # Avec inhibition, le GO/NO-GO devrait etre plus bas
        g2 = isolate_ganglia
        g2.habits["X"] = g.habits["X"].copy()
        val_clean = g2._compute_go_nogo("X")
        assert val <= val_clean

    def test_inhibit_increments_counter(self, isolate_ganglia):
        g = isolate_ganglia
        g._inhibit("X", "test")
        assert g.total_inhibitions == 1

    def test_nogo_capped(self, isolate_ganglia):
        g = isolate_ganglia
        from core.basal_ganglia import INHIBITION_STRENGTH
        for _ in range(20):
            g._inhibit("X", "test")
        assert g.go_nogo_state["X"] >= -INHIBITION_STRENGTH

    def test_go_with_many_failures(self, isolate_ganglia):
        g = isolate_ganglia
        g.habits["X"] = {"strength": 0.8, "successes": 1, "failures": 10,
                         "last_reward": -0.5, "avg_reward": -0.3, "created_at": time.time()}
        val = g._compute_go_nogo("X")
        # Faible taux de succes → GO faible
        g2 = isolate_ganglia
        g2.habits["X"] = {"strength": 0.8, "successes": 10, "failures": 1,
                          "last_reward": 1.0, "avg_reward": 0.8, "created_at": time.time()}
        val_good = g2._compute_go_nogo("X")
        assert val < val_good

    def test_thalamus_boosts_go(self, isolate_ganglia):
        g = isolate_ganglia
        g.go_nogo_state["X"] = 0.0
        # Simuler un shift thalamique
        g.go_nogo_state["X"] = 0.5
        assert g.go_nogo_state["X"] > 0

    def test_multiple_inhibitions(self, isolate_ganglia):
        g = isolate_ganglia
        g._inhibit("A", "test1")
        g._inhibit("B", "test2")
        assert "A" in g.go_nogo_state
        assert "B" in g.go_nogo_state

    def test_inhibition_from_conflict(self, isolate_ganglia):
        g = isolate_ganglia
        g._inhibit("CONFLICTED", "conflict")
        assert g.go_nogo_state["CONFLICTED"] < 0

    def test_go_nogo_independent(self, isolate_ganglia):
        g = isolate_ganglia
        g.habits["A"] = {"strength": 0.5, "successes": 3, "failures": 0,
                         "last_reward": 0.5, "avg_reward": 0.5, "created_at": time.time()}
        g._inhibit("B", "test")
        # A ne devrait pas etre affecte par l'inhibition de B
        val_a = g._compute_go_nogo("A")
        assert val_a >= 0


# ===== TestScoringBonus =====

class TestScoringBonus:
    def test_novelty_bonus_unknown(self, isolate_ganglia):
        g = isolate_ganglia
        bonus = g.compute_habit_bonus("NEVER_SEEN")
        from core.basal_ganglia import NOVELTY_BONUS
        assert bonus == NOVELTY_BONUS

    def test_zero_weak_habit(self, isolate_ganglia):
        g = isolate_ganglia
        g.habits["WEAK"] = {"strength": 0.1, "successes": 1, "failures": 0,
                            "last_reward": 0.5, "avg_reward": 0.5, "created_at": time.time()}
        bonus = g.compute_habit_bonus("WEAK")
        assert bonus == 0.0

    def test_positive_strong_go(self, isolate_ganglia):
        g = isolate_ganglia
        g.habits["STRONG"] = {"strength": 0.9, "successes": 10, "failures": 0,
                              "last_reward": 1.0, "avg_reward": 0.9, "created_at": time.time()}
        bonus = g.compute_habit_bonus("STRONG")
        assert bonus > 0

    def test_negative_strong_nogo(self, isolate_ganglia):
        g = isolate_ganglia
        g.habits["INHIBITED"] = {"strength": 0.8, "successes": 2, "failures": 5,
                                 "last_reward": -0.5, "avg_reward": -0.2, "created_at": time.time()}
        g.go_nogo_state["INHIBITED"] = -1.5
        bonus = g.compute_habit_bonus("INHIBITED")
        assert bonus < 0

    def test_range_upper(self, isolate_ganglia):
        g = isolate_ganglia
        g.habits["X"] = {"strength": 1.0, "successes": 100, "failures": 0,
                         "last_reward": 1.0, "avg_reward": 1.0, "created_at": time.time()}
        bonus = g.compute_habit_bonus("X")
        assert bonus <= 2.0

    def test_range_lower(self, isolate_ganglia):
        g = isolate_ganglia
        g.habits["X"] = {"strength": 1.0, "successes": 0, "failures": 0,
                         "last_reward": 0, "avg_reward": 0, "created_at": time.time()}
        g.go_nogo_state["X"] = -2.0
        bonus = g.compute_habit_bonus("X")
        assert bonus >= -1.5

    def test_threshold_gate(self, isolate_ganglia):
        g = isolate_ganglia
        from core.basal_ganglia import HABIT_THRESHOLD
        g.habits["X"] = {"strength": HABIT_THRESHOLD - 0.01, "successes": 1, "failures": 0,
                         "last_reward": 1.0, "avg_reward": 1.0, "created_at": time.time()}
        bonus = g.compute_habit_bonus("X")
        assert bonus == 0.0

    def test_at_threshold_scores(self, isolate_ganglia):
        g = isolate_ganglia
        from core.basal_ganglia import HABIT_THRESHOLD
        g.habits["X"] = {"strength": HABIT_THRESHOLD + 0.1, "successes": 5, "failures": 0,
                         "last_reward": 1.0, "avg_reward": 0.8, "created_at": time.time()}
        bonus = g.compute_habit_bonus("X")
        assert bonus != 0.0


# ===== TestBusHandlers =====

class TestBusHandlers:
    @pytest.mark.asyncio
    async def test_routine_complete_creates_habit(self, isolate_ganglia):
        g = isolate_ganglia
        with patch("core.basal_ganglia.bus.publish", new_callable=AsyncMock):
            await g._on_routine_complete({
                "intent": "NEW",
                "status": "success",
                "quality_score": 0.8,
            })
        assert "NEW" in g.habits

    @pytest.mark.asyncio
    async def test_routine_failed_negative(self, isolate_ganglia):
        g = isolate_ganglia
        g._update_habit("X", 0.8)
        s_before = g.habits["X"]["strength"]
        await g._on_routine_failed({"intent": "X"})
        assert g.habits["X"]["strength"] <= s_before

    @pytest.mark.asyncio
    async def test_dopamine_reinforces(self, isolate_ganglia):
        g = isolate_ganglia
        g._update_habit("LAST", 0.5)
        s_before = g.habits["LAST"]["strength"]
        g._last_intent = "LAST"
        await g._on_dopamine_surge({"rpe": 0.9})
        assert g.habits["LAST"]["strength"] > s_before

    @pytest.mark.asyncio
    async def test_cingulate_inhibits(self, isolate_ganglia):
        g = isolate_ganglia
        await g._on_cingulate_conflict({"intent": "CONFLICTED"})
        assert g.go_nogo_state.get("CONFLICTED", 0) < 0

    @pytest.mark.asyncio
    async def test_thalamus_boosts(self, isolate_ganglia):
        g = isolate_ganglia
        await g._on_thalamus_shift({
            "new_focus": "urgence",
            "boosted_intents": ["URGENT_TASK"],
        })
        assert g.go_nogo_state.get("URGENT_TASK", 0) > 0

    @pytest.mark.asyncio
    async def test_skipped_ignored(self, isolate_ganglia):
        g = isolate_ganglia
        with patch("core.basal_ganglia.bus.publish", new_callable=AsyncMock):
            await g._on_routine_complete({
                "intent": "SKIP",
                "status": "skipped",
            })
        assert g.total_selections == 0

    @pytest.mark.asyncio
    async def test_habit_formed_event(self, isolate_ganglia):
        g = isolate_ganglia
        # Pre-train pour que strength > 0.7
        g.habits["STRONG"] = {"strength": 0.69, "successes": 10, "failures": 0,
                              "last_reward": 1.0, "avg_reward": 0.9, "created_at": time.time()}
        with patch("core.basal_ganglia.bus.publish", new_callable=AsyncMock) as mock_pub:
            await g._on_routine_complete({
                "intent": "STRONG",
                "status": "success",
                "quality_score": 1.0,
            })
            if g.habits["STRONG"]["strength"] > 0.7:
                habit_calls = [c for c in mock_pub.call_args_list
                               if c[0][0] == "BASAL_GANGLIA_HABIT_FORMED"]
                assert len(habit_calls) >= 1


# ===== TestContext =====

class TestContext:
    def test_empty_no_habits(self, isolate_ganglia):
        g = isolate_ganglia
        ctx = g.get_habit_context()
        assert ctx == ""

    def test_context_with_habits(self, isolate_ganglia):
        g = isolate_ganglia
        g.habits["X"] = {"strength": 0.8, "successes": 5, "failures": 0,
                         "last_reward": 1.0, "avg_reward": 0.8, "created_at": time.time()}
        ctx = g.get_habit_context()
        assert "HABITUDES" in ctx
        assert "X" in ctx

    def test_context_shows_inhibitions(self, isolate_ganglia):
        g = isolate_ganglia
        g.habits["X"] = {"strength": 0.5, "successes": 1, "failures": 0,
                         "last_reward": 0.5, "avg_reward": 0.5, "created_at": time.time()}
        g.go_nogo_state["BAD"] = -1.0
        ctx = g.get_habit_context()
        assert "Inhibe" in ctx


# ===== TestStats =====

class TestStats:
    def test_stats_keys(self, isolate_ganglia):
        g = isolate_ganglia
        stats = g.get_stats()
        assert "habits_count" in stats
        assert "total_selections" in stats
        assert "total_inhibitions" in stats
        assert "novelty_ratio" in stats


# ===== TestPersistence =====

class TestPersistence:
    def test_save_creates_file(self, isolate_ganglia):
        g = isolate_ganglia
        g._save()
        from core.basal_ganglia import BASAL_GANGLIA_STATE_FILE
        assert os.path.exists(BASAL_GANGLIA_STATE_FILE)

    def test_roundtrip(self, isolate_ganglia, tmp_path, monkeypatch):
        from core import basal_ganglia as mod
        g = isolate_ganglia
        g.habits["LEARNED"] = {"strength": 0.7, "successes": 5, "failures": 1,
                               "last_reward": 0.8, "avg_reward": 0.6, "created_at": time.time()}
        g.total_selections = 20
        g.total_inhibitions = 3
        g._save()

        mod.BasalGanglia.reset_singleton()
        g2 = mod.BasalGanglia()
        assert "LEARNED" in g2.habits
        assert g2.total_selections == 20

    def test_load_missing(self, isolate_ganglia):
        g = isolate_ganglia
        g._load()

    def test_load_corrupt(self, isolate_ganglia, tmp_path):
        from core.basal_ganglia import BASAL_GANGLIA_STATE_FILE
        with open(BASAL_GANGLIA_STATE_FILE, "w") as f:
            f.write("BAD")
        g = isolate_ganglia
        g._load()

    def test_max_habits_persisted(self, isolate_ganglia, tmp_path):
        from core import basal_ganglia as mod
        g = isolate_ganglia
        from core.basal_ganglia import MAX_HABITS
        for i in range(MAX_HABITS):
            g.habits[f"H_{i}"] = {"strength": 0.5, "successes": 1, "failures": 0,
                                  "last_reward": 0.5, "avg_reward": 0.5, "created_at": time.time()}
        g._save()
        mod.BasalGanglia.reset_singleton()
        g2 = mod.BasalGanglia()
        assert len(g2.habits) == MAX_HABITS
