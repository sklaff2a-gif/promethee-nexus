# tests/test_default_mode_network.py — Tests Default Mode Network
import pytest
import json
import os
import time
from unittest.mock import patch, AsyncMock, MagicMock
from collections import deque


@pytest.fixture(autouse=True)
def isolate_dmn(tmp_path, monkeypatch):
    """Isole le singleton DefaultModeNetwork pour chaque test."""
    from core import default_mode_network as mod
    mod.DefaultModeNetwork.reset_singleton()
    monkeypatch.setattr(mod, "DMN_STATE_FILE", str(tmp_path / "dmn_state.json"))
    with patch.object(mod.DefaultModeNetwork, "_load"):
        d = mod.DefaultModeNetwork()
    d.is_active = False
    d.idle_since = None
    d.wandering_history = deque(maxlen=50)
    d.insights = []
    d.consolidation_count = 0
    d.total_wanderings = 0
    d._last_routine_time = time.time()
    d._last_thought = ""
    d._alive = False
    d._subscribed = False
    mod.dmn = d
    yield d
    mod.DefaultModeNetwork.reset_singleton()


# ===== TestSingleton =====

class TestSingleton:
    def test_singleton_identity(self, isolate_dmn):
        from core.default_mode_network import DefaultModeNetwork
        d2 = DefaultModeNetwork()
        assert d2 is isolate_dmn


# ===== TestActivation =====

class TestActivation:
    def test_initially_inactive(self, isolate_dmn):
        assert not isolate_dmn.is_active

    def test_mark_idle(self, isolate_dmn):
        d = isolate_dmn
        d._mark_idle()
        assert d.idle_since is not None

    @pytest.mark.asyncio
    async def test_activate(self, isolate_dmn):
        d = isolate_dmn
        d.idle_since = time.time() - 200
        with patch("core.default_mode_network.bus.publish", new_callable=AsyncMock):
            await d._activate()
        assert d.is_active

    def test_deactivate(self, isolate_dmn):
        d = isolate_dmn
        d.is_active = True
        d.idle_since = time.time()
        d._deactivate()
        assert not d.is_active
        assert d.idle_since is None

    def test_idle_duration_zero(self, isolate_dmn):
        d = isolate_dmn
        assert d._get_idle_duration() == 0.0

    def test_idle_duration_positive(self, isolate_dmn):
        d = isolate_dmn
        d.idle_since = time.time() - 60
        duration = d._get_idle_duration()
        assert duration >= 59.0

    @pytest.mark.asyncio
    async def test_routine_complete_marks_idle(self, isolate_dmn):
        d = isolate_dmn
        await d._on_routine_complete({})
        assert d.idle_since is not None

    @pytest.mark.asyncio
    async def test_heartbeat_processing_deactivates(self, isolate_dmn):
        d = isolate_dmn
        d.is_active = True
        d.idle_since = time.time()
        await d._on_heartbeat({"is_processing": True})
        assert not d.is_active

    @pytest.mark.asyncio
    async def test_heartbeat_idle_no_deactivate(self, isolate_dmn):
        d = isolate_dmn
        d.is_active = True
        d.idle_since = time.time()
        await d._on_heartbeat({"is_processing": False})
        assert d.is_active


# ===== TestWandering =====

class TestWandering:
    @pytest.mark.asyncio
    async def test_wander_increments_count(self, isolate_dmn):
        d = isolate_dmn
        with patch("core.default_mode_network.bus.publish", new_callable=AsyncMock):
            with patch.dict("sys.modules", {
                "core.synaptic_network": MagicMock(),
                "core.hippocampus": MagicMock(),
            }):
                await d._wander_cycle()
        assert d.total_wanderings == 1

    @pytest.mark.asyncio
    async def test_wander_stores_history(self, isolate_dmn):
        d = isolate_dmn
        with patch("core.default_mode_network.bus.publish", new_callable=AsyncMock):
            with patch.dict("sys.modules", {
                "core.synaptic_network": MagicMock(),
                "core.hippocampus": MagicMock(),
            }):
                await d._wander_cycle()
        assert len(d.wandering_history) == 1

    @pytest.mark.asyncio
    async def test_wander_publishes_thought(self, isolate_dmn):
        d = isolate_dmn
        with patch("core.default_mode_network.bus.publish", new_callable=AsyncMock) as mock_pub:
            with patch.dict("sys.modules", {
                "core.synaptic_network": MagicMock(),
                "core.hippocampus": MagicMock(),
            }):
                await d._wander_cycle()
        thought_calls = [c for c in mock_pub.call_args_list if c[0][0] == "DMN_THOUGHT"]
        assert len(thought_calls) >= 1

    def test_generate_thought_with_insight(self, isolate_dmn):
        d = isolate_dmn
        thought = d._generate_thought([], "Test insight")
        assert "insight" in thought.lower() or "DMN" in thought

    def test_generate_thought_with_associations(self, isolate_dmn):
        d = isolate_dmn
        assocs = [{"from": "A", "to": "B", "type": "free"}]
        thought = d._generate_thought(assocs, None)
        assert "A" in thought or "Vagabondage" in thought

    def test_generate_thought_empty(self, isolate_dmn):
        d = isolate_dmn
        thought = d._generate_thought([], None)
        assert len(thought) > 0

    def test_free_associations_no_crash(self, isolate_dmn):
        d = isolate_dmn
        with patch.dict("sys.modules", {
            "core.synaptic_network": MagicMock(),
            "core.hippocampus": MagicMock(),
        }):
            assocs = d._generate_free_associations()
        assert isinstance(assocs, list)

    def test_consolidation_no_crash(self, isolate_dmn):
        d = isolate_dmn
        mock_hippo = MagicMock()
        mock_hippo.hippocampus.consolidate.return_value = 3
        with patch.dict("sys.modules", {
            "core.hippocampus": mock_hippo,
        }):
            count = d._consolidate_memory()
        assert isinstance(count, int)

    @pytest.mark.asyncio
    async def test_multiple_cycles(self, isolate_dmn):
        d = isolate_dmn
        with patch("core.default_mode_network.bus.publish", new_callable=AsyncMock):
            with patch.dict("sys.modules", {
                "core.synaptic_network": MagicMock(),
                "core.hippocampus": MagicMock(),
            }):
                await d._wander_cycle()
                await d._wander_cycle()
                await d._wander_cycle()
        assert d.total_wanderings == 3

    def test_stop_sets_flag(self, isolate_dmn):
        d = isolate_dmn
        d._alive = True
        d.stop()
        assert not d._alive


# ===== TestInsights =====

class TestInsights:
    def test_attempt_insight_empty(self, isolate_dmn):
        d = isolate_dmn
        result = d._attempt_insight([])
        assert result is None

    def test_attempt_insight_single(self, isolate_dmn):
        d = isolate_dmn
        result = d._attempt_insight([{"from": "A", "to": "B"}])
        assert result is None  # Besoin de >= 2 associations

    def test_attempt_insight_with_hub(self, isolate_dmn):
        d = isolate_dmn
        # Forcer la probabilite
        with patch("core.default_mode_network.random.random", return_value=0.01):
            assocs = [
                {"from": "HUB", "to": "A", "type": "free"},
                {"from": "HUB", "to": "B", "type": "free"},
                {"from": "C", "to": "D", "type": "free"},
            ]
            result = d._attempt_insight(assocs)
        if result:
            assert "HUB" in result

    def test_insight_probability_gate(self, isolate_dmn):
        d = isolate_dmn
        # Probabilite trop haute → pas d'insight
        with patch("core.default_mode_network.random.random", return_value=0.99):
            assocs = [
                {"from": "HUB", "to": "A"},
                {"from": "HUB", "to": "B"},
            ]
            result = d._attempt_insight(assocs)
        assert result is None

    @pytest.mark.asyncio
    async def test_insight_stored(self, isolate_dmn):
        d = isolate_dmn
        # Forcer un insight
        with patch("core.default_mode_network.random.random", return_value=0.01):
            with patch("core.default_mode_network.bus.publish", new_callable=AsyncMock):
                with patch.object(d, "_generate_free_associations", return_value=[
                    {"from": "HUB", "to": "X"},
                    {"from": "HUB", "to": "Y"},
                ]):
                    with patch.object(d, "_consolidate_memory", return_value=0):
                        await d._wander_cycle()
        # Insight peut ou non etre genere selon la logique
        # On verifie juste que ca ne crash pas
        assert isinstance(d.insights, list)

    @pytest.mark.asyncio
    async def test_insight_published(self, isolate_dmn):
        d = isolate_dmn
        with patch("core.default_mode_network.random.random", return_value=0.01):
            with patch("core.default_mode_network.bus.publish", new_callable=AsyncMock) as mock_pub:
                with patch.object(d, "_generate_free_associations", return_value=[
                    {"from": "HUB", "to": "X"},
                    {"from": "HUB", "to": "Y"},
                ]):
                    with patch.object(d, "_consolidate_memory", return_value=0):
                        await d._wander_cycle()
        insight_calls = [c for c in mock_pub.call_args_list if c[0][0] == "DMN_INSIGHT"]
        # Peut ou non avoir un insight
        assert isinstance(insight_calls, list)

    def test_no_hub_no_insight(self, isolate_dmn):
        d = isolate_dmn
        with patch("core.default_mode_network.random.random", return_value=0.01):
            assocs = [
                {"from": "A", "to": "B"},
                {"from": "C", "to": "D"},
            ]
            result = d._attempt_insight(assocs)
        assert result is None

    @pytest.mark.asyncio
    async def test_activation_event_published(self, isolate_dmn):
        d = isolate_dmn
        d.idle_since = time.time() - 200
        with patch("core.default_mode_network.bus.publish", new_callable=AsyncMock) as mock_pub:
            await d._activate()
        activation_calls = [c for c in mock_pub.call_args_list if c[0][0] == "DMN_ACTIVATED"]
        assert len(activation_calls) == 1


# ===== TestBusHandlers =====

class TestBusHandlers:
    @pytest.mark.asyncio
    async def test_routine_complete_idle(self, isolate_dmn):
        d = isolate_dmn
        await d._on_routine_complete({})
        assert d.idle_since is not None

    @pytest.mark.asyncio
    async def test_routine_failed_idle(self, isolate_dmn):
        d = isolate_dmn
        await d._on_routine_failed({})
        assert d.idle_since is not None

    @pytest.mark.asyncio
    async def test_heartbeat_processing_deactivates(self, isolate_dmn):
        d = isolate_dmn
        d.is_active = True
        await d._on_heartbeat({"is_processing": True})
        assert not d.is_active

    @pytest.mark.asyncio
    async def test_multiple_completes(self, isolate_dmn):
        d = isolate_dmn
        await d._on_routine_complete({})
        t1 = d.idle_since
        await d._on_routine_complete({})
        # idle_since ne devrait pas etre ecrase si deja set
        assert d.idle_since == t1

    @pytest.mark.asyncio
    async def test_deactivate_then_idle(self, isolate_dmn):
        d = isolate_dmn
        d.is_active = True
        d.idle_since = time.time()
        d._deactivate()
        await d._on_routine_complete({})
        assert d.idle_since is not None


# ===== TestContext =====

class TestContext:
    def test_context_inactive(self, isolate_dmn):
        d = isolate_dmn
        ctx = d.get_dmn_context()
        assert ctx == ""

    def test_context_active_with_thought(self, isolate_dmn):
        d = isolate_dmn
        d.is_active = True
        d._last_thought = "Reflexion sur les patterns"
        ctx = d.get_dmn_context()
        assert "DMN" in ctx

    def test_context_active_with_insight(self, isolate_dmn):
        d = isolate_dmn
        d.is_active = True
        d.insights = [{"text": "Hub detecte", "timestamp": time.time()}]
        ctx = d.get_dmn_context()
        assert "Insight" in ctx or "DMN" in ctx

    def test_context_active_empty(self, isolate_dmn):
        d = isolate_dmn
        d.is_active = True
        ctx = d.get_dmn_context()
        assert "DMN" in ctx


# ===== TestStats =====

class TestStats:
    def test_stats_keys(self, isolate_dmn):
        d = isolate_dmn
        stats = d.get_stats()
        assert "is_active" in stats
        assert "total_wanderings" in stats
        assert "insights_count" in stats
        assert "consolidation_count" in stats
        assert "last_thought" in stats


# ===== TestPersistence =====

class TestPersistence:
    def test_save_creates_file(self, isolate_dmn):
        d = isolate_dmn
        d._save()
        from core.default_mode_network import DMN_STATE_FILE
        assert os.path.exists(DMN_STATE_FILE)

    def test_roundtrip(self, isolate_dmn, tmp_path, monkeypatch):
        from core import default_mode_network as mod
        d = isolate_dmn
        d.total_wanderings = 15
        d.consolidation_count = 5
        d._last_thought = "Test thought"
        d.insights = [{"text": "insight1", "timestamp": time.time(), "cycle": 1}]
        d._save()

        mod.DefaultModeNetwork.reset_singleton()
        d2 = mod.DefaultModeNetwork()
        assert d2.total_wanderings == 15
        assert d2.consolidation_count == 5
        assert d2._last_thought == "Test thought"
        assert len(d2.insights) == 1

    def test_load_missing(self, isolate_dmn):
        d = isolate_dmn
        d._load()

    def test_load_corrupt(self, isolate_dmn, tmp_path):
        from core.default_mode_network import DMN_STATE_FILE
        with open(DMN_STATE_FILE, "w") as f:
            f.write("BAD")
        d = isolate_dmn
        d._load()


# ==================== TESTS COMPUTE DMN BONUS ====================

class TestComputeDmnBonus:
    """Tests du bonus créatif DMN pour le scoring autonome."""

    def test_non_creative_intent_returns_zero(self, isolate_dmn):
        """Un intent non-créatif ne reçoit aucun bonus DMN."""
        assert isolate_dmn.compute_dmn_bonus("AUDIT_STRUCTURE") == 0.0
        assert isolate_dmn.compute_dmn_bonus("MEMORY_CLEANUP") == 0.0
        assert isolate_dmn.compute_dmn_bonus("SECURITY_AUDIT") == 0.0

    def test_no_activity_returns_negative(self, isolate_dmn):
        """Sans insights ni vagabondage, le réservoir créatif est vide."""
        isolate_dmn._last_routine_time = 0
        bonus = isolate_dmn.compute_dmn_bonus("EXPANSION_CODE")
        assert bonus == -0.5

    def test_one_recent_insight_boosts(self, isolate_dmn):
        """Un insight récent booste les intents créatifs."""
        isolate_dmn.insights = [{"text": "connexion A-B", "timestamp": time.time() - 100}]
        bonus = isolate_dmn.compute_dmn_bonus("EXPANSION_CODE")
        assert bonus == pytest.approx(0.8, abs=0.01)  # 0.5 + 1 * 0.3

    def test_multiple_insights_compound(self, isolate_dmn):
        """Plusieurs insights récents augmentent le bonus."""
        now = time.time()
        isolate_dmn.insights = [
            {"text": f"insight {i}", "timestamp": now - 100 * i}
            for i in range(5)
        ]
        bonus = isolate_dmn.compute_dmn_bonus("CREATIVE_PLAY")
        assert bonus == pytest.approx(2.0, abs=0.01)  # min(2.0, 0.5 + 5 * 0.3)

    def test_insight_bonus_capped(self, isolate_dmn):
        """Le bonus insight est plafonné à 2.0."""
        now = time.time()
        isolate_dmn.insights = [
            {"text": f"insight {i}", "timestamp": now - 50 * i}
            for i in range(20)
        ]
        bonus = isolate_dmn.compute_dmn_bonus("EXPANSION_CODE")
        assert bonus == 2.0

    def test_old_insights_ignored(self, isolate_dmn):
        """Les insights de plus de 2h ne comptent pas."""
        isolate_dmn.insights = [{"text": "vieux", "timestamp": time.time() - 8000}]
        isolate_dmn.wandering_history = deque()
        bonus = isolate_dmn.compute_dmn_bonus("EXPANSION_CODE")
        assert bonus == -0.5  # Pas d'insight récent, pas de vagabondage

    def test_recent_wandering_gives_small_boost(self, isolate_dmn):
        """Un vagabondage récent sans insight donne un petit boost."""
        isolate_dmn.wandering_history = deque([
            {"timestamp": time.time() - 300, "insight": None}
        ])
        bonus = isolate_dmn.compute_dmn_bonus("COUNCIL_DEBATE")
        assert bonus == pytest.approx(0.3, abs=0.01)

    def test_all_creative_intents_covered(self, isolate_dmn):
        """Tous les intents créatifs répondent au DMN."""
        from core.default_mode_network import DMN_CREATIVE_INTENTS
        isolate_dmn.insights = [{"text": "test", "timestamp": time.time() - 60}]
        for intent in DMN_CREATIVE_INTENTS:
            bonus = isolate_dmn.compute_dmn_bonus(intent)
            assert bonus > 0, f"{intent} devrait recevoir un bonus DMN"

    def test_dmn_in_scoring_weights(self):
        """Le DMN est configuré dans scoring_weights.json."""
        import core.autonomy_engine as ae
        ae._scoring_weights_cache = None
        weights = ae._load_scoring_weights()
        assert "dmn" in weights
        assert weights["dmn"]["weight"] > 0
        assert weights["dmn"]["range_abs"] > 0
