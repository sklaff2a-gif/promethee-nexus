"""Tests pour la boucle agentique et _scan_response_actions."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from core.chat_engine import ChatEngine


@pytest.fixture
def chat():
    """ChatEngine frais."""
    engine = ChatEngine.__new__(ChatEngine)
    engine._init_done = True
    engine.messages = []
    engine._cached_organ_parts = []
    engine._cache_timestamp = 0.0
    engine._last_subfolder_hint = None
    engine._auto_action_in_progress = False
    ChatEngine._last_dispatch_time = 0.0
    return engine


class TestScanResponseActionsReturnCount:
    """Verifie que _scan_response_actions retourne le nombre d'actions."""

    @pytest.mark.asyncio
    async def test_returns_zero_no_commands(self, chat):
        result = await chat._scan_response_actions("Bonjour, tout va bien.")
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_empty(self, chat):
        result = await chat._scan_response_actions("")
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_none(self, chat):
        result = await chat._scan_response_actions(None)
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_unknown_command(self, chat):
        result = await chat._scan_response_actions("!unknown_cmd arg")
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_count_status(self, chat):
        with patch.object(chat, "_execute_status_command", return_value="OK"):
            result = await chat._scan_response_actions("!status")
        assert result == 1

    @pytest.mark.asyncio
    async def test_returns_positive_on_action(self, chat):
        with patch.object(chat, "_execute_status_command", return_value="OK"):
            result = await chat._scan_response_actions("Voici mon analyse\n!status\nConclusion")
        assert result >= 1
        assert isinstance(result, int)

    @pytest.mark.asyncio
    async def test_anti_reentrance(self, chat):
        chat._auto_action_in_progress = True
        result = await chat._scan_response_actions("!status")
        assert result == 0


class TestReadMaxLines:
    """Verifie que _READ_MAX_LINES a ete augmente."""

    def test_read_max_lines_150(self):
        assert ChatEngine._READ_MAX_LINES == 150


class TestAgenticLoopConstants:
    """Verifie les constantes de la boucle agentique dans le code."""

    def test_max_agentic_loops_in_source(self):
        """La constante _MAX_AGENTIC_LOOPS doit etre definie dans chat()."""
        import inspect
        source = inspect.getsource(ChatEngine.chat)
        assert "_MAX_AGENTIC_LOOPS = 3" in source

    def test_agentic_continuation_badge_in_source(self):
        """Le badge agentic_continuation doit etre utilise."""
        import inspect
        source = inspect.getsource(ChatEngine.chat)
        assert "agentic_continuation" in source
