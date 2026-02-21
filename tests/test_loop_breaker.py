"""Tests pour core/grimoire/loop_breaker.py — detection de boucles."""
import json
import pytest
import pytest_asyncio
from unittest.mock import MagicMock, patch

from core.grimoire.loop_breaker import (
    detect_cycles,
    find_trapped_intents,
    suggest_alternative_intents,
    analyze_and_prescribe,
    LoopBreaker,
)


class TestDetectCycles:
    def test_no_cycle_short_history(self):
        history = [
            {"intent": "A"},
            {"intent": "B"},
        ]
        assert detect_cycles(history) == []

    def test_simple_ab_cycle(self):
        history = [
            {"intent": "A"}, {"intent": "B"},
            {"intent": "A"}, {"intent": "B"},
            {"intent": "A"}, {"intent": "B"},
        ]
        cycles = detect_cycles(history)
        assert len(cycles) >= 1
        assert ["A", "B"] in cycles

    def test_abc_cycle(self):
        history = [
            {"intent": "A"}, {"intent": "B"}, {"intent": "C"},
            {"intent": "A"}, {"intent": "B"}, {"intent": "C"},
        ]
        cycles = detect_cycles(history)
        assert len(cycles) >= 1
        assert ["A", "B", "C"] in cycles

    def test_no_cycle_diverse(self):
        history = [
            {"intent": "A"}, {"intent": "B"}, {"intent": "C"},
            {"intent": "D"}, {"intent": "E"}, {"intent": "F"},
        ]
        assert detect_cycles(history) == []

    def test_empty_history(self):
        assert detect_cycles([]) == []


class TestFindTrappedIntents:
    def test_no_trapped(self):
        history = [
            {"intent": "A", "status": "success"},
            {"intent": "B", "status": "success"},
            {"intent": "A", "status": "success"},
        ]
        assert find_trapped_intents(history) == []

    def test_trapped_intent(self):
        history = [
            {"intent": "A", "status": "error"},
            {"intent": "B", "status": "success"},
            {"intent": "A", "status": "error"},
            {"intent": "A", "status": "error"},
            {"intent": "A", "status": "low_quality"},
        ]
        trapped = find_trapped_intents(history)
        assert "A" in trapped

    def test_multiple_trapped(self):
        history = [
            {"intent": "A", "status": "error"},
            {"intent": "A", "status": "error"},
            {"intent": "A", "status": "error"},
            {"intent": "B", "status": "low_quality"},
            {"intent": "B", "status": "error"},
            {"intent": "B", "status": "error"},
        ]
        trapped = find_trapped_intents(history)
        assert "A" in trapped
        assert "B" in trapped

    def test_empty_history(self):
        assert find_trapped_intents([]) == []

    def test_max_rounds_low_counts_as_failure(self):
        history = [
            {"intent": "X", "status": "max_rounds_low"},
            {"intent": "X", "status": "max_rounds_low"},
            {"intent": "X", "status": "max_rounds_low"},
        ]
        trapped = find_trapped_intents(history)
        assert "X" in trapped


class TestSuggestAlternativeIntents:
    def test_avoids_blacklist(self):
        history = [
            {"intent": "EXPANSION_CODE"},
            {"intent": "EXPANSION_CODE"},
        ]
        suggestions = suggest_alternative_intents(history, ["EXPANSION_CODE"])
        assert "EXPANSION_CODE" not in suggestions
        assert len(suggestions) > 0

    def test_prefers_least_recent(self):
        history = [
            {"intent": "EXPANSION_CODE"},
            {"intent": "EXPANSION_CODE"},
            {"intent": "EXPANSION_CODE"},
            {"intent": "AUDIT_STRUCTURE"},
        ]
        suggestions = suggest_alternative_intents(history, [])
        # Les intents non utilises doivent etre en premier
        assert suggestions[0] != "EXPANSION_CODE"

    def test_empty_history(self):
        suggestions = suggest_alternative_intents([], [])
        assert len(suggestions) == 3

    def test_all_blacklisted_fallback(self):
        from core.grimoire.loop_breaker import _ALL_INTENTS
        suggestions = suggest_alternative_intents([], _ALL_INTENTS)
        # Fallback : retourne quand meme quelque chose
        assert len(suggestions) > 0


class TestAnalyzeAndPrescribe:
    def test_critical_error_streak(self):
        result = analyze_and_prescribe([], error_streak=8)
        assert result["action"] == "escalate"

    def test_very_high_error_streak(self):
        result = analyze_and_prescribe([], error_streak=15)
        assert result["action"] == "escalate"

    def test_trapped_intents_skip(self):
        history = [
            {"intent": "A", "status": "error"},
            {"intent": "A", "status": "error"},
            {"intent": "A", "status": "error"},
        ]
        result = analyze_and_prescribe(history, error_streak=3)
        assert result["action"] == "skip"
        assert "blacklist" in result

    def test_cycle_redirect(self):
        history = [
            {"intent": "A"}, {"intent": "B"},
            {"intent": "A"}, {"intent": "B"},
            {"intent": "A"}, {"intent": "B"},
        ]
        result = analyze_and_prescribe(history, error_streak=2)
        assert result["action"] == "redirect"
        assert result.get("forced_intent")

    def test_moderate_error_streak_cooldown(self):
        result = analyze_and_prescribe([], error_streak=5)
        assert result["action"] == "cooldown"
        assert result.get("extra_sleep", 0) > 0

    def test_normal_situation_skip(self):
        history = [
            {"intent": "A", "status": "success"},
            {"intent": "B", "status": "success"},
        ]
        result = analyze_and_prescribe(history, error_streak=1)
        assert result["action"] == "skip"
        assert "boucle" not in result.get("details", "").lower() or "pas de" in result.get("details", "").lower()

    def test_empty_history_low_streak(self):
        result = analyze_and_prescribe([], error_streak=0)
        assert result["action"] == "skip"

    def test_cooldown_extra_sleep_capped(self):
        result = analyze_and_prescribe([], error_streak=7)
        # error_streak=7 < 8 donc pas escalate, mais >= 5 -> cooldown
        assert result["action"] == "cooldown"
        assert result["extra_sleep"] <= 600  # Cap a 10 min


class TestLoopBreakerAgent:
    @pytest.fixture
    def breaker(self):
        with patch("core.grimoire.loop_breaker.BaseAgent.__init__", return_value=None):
            lb = LoopBreaker()
            lb.name = "loop_breaker"
            lb.role = "test"
            lb.description = "test"
            lb.logger = MagicMock()
            lb.has_memory = False
            lb.log_thought = MagicMock()
            return lb

    @pytest.mark.asyncio
    async def test_high_error_streak(self, breaker):
        result = await breaker.process_task({
            "mission": "AIDE: loop",
            "context": json.dumps({"history": [], "error_streak": 10}),
        })
        assert result["action"] == "escalate"

    @pytest.mark.asyncio
    async def test_normal_situation(self, breaker):
        result = await breaker.process_task({
            "mission": "AIDE: loop",
            "context": json.dumps({"history": [], "error_streak": 1}),
        })
        assert result["action"] == "skip"

    @pytest.mark.asyncio
    async def test_invalid_json(self, breaker):
        result = await breaker.process_task({
            "mission": "AIDE: loop",
            "context": "not json",
        })
        assert result["action"] == "skip"

    @pytest.mark.asyncio
    async def test_cycle_detected(self, breaker):
        history = [
            {"intent": "A"}, {"intent": "B"},
            {"intent": "A"}, {"intent": "B"},
            {"intent": "A"}, {"intent": "B"},
        ]
        result = await breaker.process_task({
            "mission": "AIDE: loop",
            "context": json.dumps({"history": history, "error_streak": 2}),
        })
        assert result["action"] == "redirect"
        assert result.get("forced_intent")

    @pytest.mark.asyncio
    async def test_trapped_intents(self, breaker):
        history = [
            {"intent": "X", "status": "error"},
            {"intent": "X", "status": "error"},
            {"intent": "X", "status": "error"},
        ]
        result = await breaker.process_task({
            "mission": "AIDE: loop",
            "context": json.dumps({"history": history, "error_streak": 3}),
        })
        assert result["action"] == "skip"
        assert "X" in result.get("blacklist", [])
