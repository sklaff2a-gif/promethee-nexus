import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from core.orchestrator import Orchestrator


@pytest.fixture
def orch():
    return Orchestrator()


@pytest.fixture
def mock_agent():
    agent = AsyncMock()
    agent.process_task = AsyncMock(return_value={"status": "success", "result": "ok"})
    return agent


class TestKillSwitch:

    @pytest.mark.asyncio
    async def test_kill_switch_blocks_dispatch(self, orch, mock_agent):
        await orch.register_agent("coder", mock_agent)
        await orch.set_kill_switch(True)

        result = await orch.dispatch_task("coder", {"mission": "test"})
        assert result["status"] == "BLOCKED"
        mock_agent.process_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_kill_switch_off_allows_dispatch(self, orch, mock_agent):
        await orch.register_agent("coder", mock_agent)
        await orch.set_kill_switch(False)

        result = await orch.dispatch_task("coder", {"mission": "test"})
        assert result["status"] == "success"


class TestAgentDispatch:

    @pytest.mark.asyncio
    async def test_dispatch_to_registered_agent(self, orch, mock_agent):
        await orch.register_agent("coder", mock_agent)
        result = await orch.dispatch_task("coder", {"mission": "test"})
        assert result["status"] == "success"
        mock_agent.process_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_to_unknown_agent_returns_error(self, orch):
        """Agent introuvable (et pas de Grimoire) retourne une erreur."""
        with patch("core.orchestrator.inspect"):
            result = await orch.dispatch_task("agent_inexistant", {"mission": "test"})
            assert result["status"] == "ERROR"
            assert "AGENT_NOT_FOUND" in result.get("reason", "")

    @pytest.mark.asyncio
    async def test_dispatch_handles_agent_exception(self, orch):
        agent = AsyncMock()
        agent.process_task = AsyncMock(side_effect=RuntimeError("crash"))
        await orch.register_agent("buggy", agent)

        result = await orch.dispatch_task("buggy", {"mission": "test"})
        assert result["status"] == "ERROR"


class TestValidation:

    def test_is_validated_starts_with_valide(self):
        assert Orchestrator._is_validated("VALIDÉ. Le code est correct.") is True

    def test_is_validated_starts_with_valide_no_accent(self):
        assert Orchestrator._is_validated("VALIDE. Tout est bon.") is True

    def test_is_validated_rejects_negation(self):
        """'Le code n'est pas VALIDÉ' ne doit PAS matcher."""
        assert Orchestrator._is_validated("Le code n'est pas VALIDÉ") is False

    def test_is_validated_rejects_middle_mention(self):
        assert Orchestrator._is_validated("Impossible de considérer ce code comme VALIDÉ") is False

    def test_is_validated_empty_string(self):
        assert Orchestrator._is_validated("") is False

    def test_is_validated_with_whitespace_prefix(self):
        assert Orchestrator._is_validated("  VALIDÉ après nettoyage") is True

    def test_contains_python_code_real_code(self):
        code = "import os\nfrom pathlib import Path\n\nclass MyClass:\n    pass"
        assert Orchestrator._contains_python_code(code) is True

    def test_contains_python_code_just_mention(self):
        text = "On a parlé de la class Teacher et de comment def fonctionne"
        assert Orchestrator._contains_python_code(text) is False

    def test_contains_python_code_single_import(self):
        """Un seul pattern ne suffit pas (seuil >= 2)."""
        text = "import os\nJuste du texte ici."
        assert Orchestrator._contains_python_code(text) is False

    def test_contains_python_code_multiple_patterns(self):
        code = "import logging\n\ndef hello():\n    print('hi')"
        assert Orchestrator._contains_python_code(code) is True


class TestExpansionCapitalisteRemoved:
    """Le bypass EXPANSION_CAPITALISTE a été supprimé."""

    @pytest.mark.asyncio
    async def test_no_bypass_expansion_capitaliste(self, orch, mock_agent):
        await orch.register_agent("architect", mock_agent)

        payload = {"mission": "test EXPANSION_CAPITALISTE", "context": ""}
        result = await orch.dispatch_task("architect", payload)

        # L'agent architect doit être appelé normalement (pas de bypass vers factory)
        mock_agent.process_task.assert_called_once()
        assert result["status"] == "success"
