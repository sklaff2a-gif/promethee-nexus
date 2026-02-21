"""Tests pour BaseAgent._request_help() — protocole d'escalade unifie."""
import json
import sys
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch


class TestRequestHelp:
    """Tests de _request_help() dans BaseAgent."""

    @pytest.fixture
    def agent(self):
        """Cree un BaseAgent minimal pour les tests."""
        with patch("core.base_agent.BaseAgent.__init__", return_value=None):
            from core.base_agent import BaseAgent
            a = BaseAgent.__new__(BaseAgent)
            a.name = "test_agent"
            a.role = "test"
            a.description = "test"
            a.logger = MagicMock()
            a.has_memory = False
            a.log_thought = MagicMock()
            return a

    @pytest.mark.asyncio
    async def test_unknown_failure_type(self, agent):
        result = await agent._request_help("unknown_type", {})
        assert result["action"] == "skip"
        assert "no specialist" in result["reason"]

    @pytest.mark.asyncio
    async def test_hallucination_dispatches_to_doctor(self, agent):
        mock_response = {
            "action": "retry",
            "verdict": "retry",
            "corrected_prompt": "prompt corrige",
            "diagnosis": {"type": "alien_imports"},
        }
        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value=mock_response)
        mock_module = MagicMock()
        mock_module.orchestrator = mock_orch

        with patch.dict(sys.modules, {"core.orchestrator": mock_module}):
            result = await agent._request_help("hallucination", {"type": "alien_imports"})

        assert result["action"] == "retry"
        mock_orch.dispatch_task.assert_called_once()
        call_args = mock_orch.dispatch_task.call_args
        assert call_args[0][0] == "hallucination_doctor"

    @pytest.mark.asyncio
    async def test_rejection_dispatches_to_reviewer(self, agent):
        mock_response = {"action": "skip", "verdict": "UNSAFE"}
        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value=mock_response)
        mock_module = MagicMock()
        mock_module.orchestrator = mock_orch

        with patch.dict(sys.modules, {"core.orchestrator": mock_module}):
            result = await agent._request_help("rejection", {"code": "test"})

        assert result["action"] == "skip"
        mock_orch.dispatch_task.assert_called_once()
        assert mock_orch.dispatch_task.call_args[0][0] == "code_reviewer"

    @pytest.mark.asyncio
    async def test_loop_dispatches_to_breaker(self, agent):
        mock_response = {"action": "cooldown", "extra_sleep": 120}
        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value=mock_response)
        mock_module = MagicMock()
        mock_module.orchestrator = mock_orch

        with patch.dict(sys.modules, {"core.orchestrator": mock_module}):
            result = await agent._request_help("loop", {"history": [], "error_streak": 5})

        assert result["action"] == "cooldown"
        mock_orch.dispatch_task.assert_called_once()
        assert mock_orch.dispatch_task.call_args[0][0] == "loop_breaker"

    @pytest.mark.asyncio
    async def test_dispatch_exception_returns_skip(self, agent):
        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(side_effect=RuntimeError("boom"))
        mock_module = MagicMock()
        mock_module.orchestrator = mock_orch

        with patch.dict(sys.modules, {"core.orchestrator": mock_module}):
            result = await agent._request_help("hallucination", {})

        assert result["action"] == "skip"
        assert "boom" in result["reason"]

    @pytest.mark.asyncio
    async def test_none_response_returns_skip(self, agent):
        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value=None)
        mock_module = MagicMock()
        mock_module.orchestrator = mock_orch

        with patch.dict(sys.modules, {"core.orchestrator": mock_module}):
            result = await agent._request_help("hallucination", {})

        assert result["action"] == "skip"
        assert "invalide" in result["reason"]

    @pytest.mark.asyncio
    async def test_verdict_normalized_to_action(self, agent):
        # Quand response a "verdict" mais pas "action"
        mock_response = {"verdict": "retry", "corrected_prompt": "test"}
        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value=mock_response)
        mock_module = MagicMock()
        mock_module.orchestrator = mock_orch

        with patch.dict(sys.modules, {"core.orchestrator": mock_module}):
            result = await agent._request_help("hallucination", {})

        assert result["action"] == "retry"

    @pytest.mark.asyncio
    async def test_context_serialized_as_json(self, agent):
        """Verifie que le context est serialise en JSON dans le payload."""
        mock_response = {"action": "skip", "reason": "test"}
        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value=mock_response)
        mock_module = MagicMock()
        mock_module.orchestrator = mock_orch

        ctx = {"type": "alien_imports", "aliens": ["django"]}
        with patch.dict(sys.modules, {"core.orchestrator": mock_module}):
            await agent._request_help("hallucination", ctx)

        call_args = mock_orch.dispatch_task.call_args
        payload = call_args[0][1]
        # Le context doit etre du JSON valide
        parsed = json.loads(payload["context"])
        assert parsed["type"] == "alien_imports"


class TestEscalationMap:
    def test_map_has_expected_keys(self):
        from core.base_agent import BaseAgent
        assert "hallucination" in BaseAgent._ESCALATION_MAP
        assert "rejection" in BaseAgent._ESCALATION_MAP
        assert "loop" in BaseAgent._ESCALATION_MAP

    def test_map_values(self):
        from core.base_agent import BaseAgent
        assert BaseAgent._ESCALATION_MAP["hallucination"] == "hallucination_doctor"
        assert BaseAgent._ESCALATION_MAP["rejection"] == "code_reviewer"
        assert BaseAgent._ESCALATION_MAP["loop"] == "loop_breaker"
