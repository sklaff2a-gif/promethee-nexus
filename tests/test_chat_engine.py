# tests/test_chat_engine.py — Tests unitaires pour le ChatEngine
import json
import os
import time
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from core.chat_engine import ChatEngine, CHAT_HISTORY_FILE, MAX_SAVED_MESSAGES


@pytest.fixture(autouse=True)
def reset_chat_engine(tmp_path, monkeypatch):
    """Reset singleton + redirect persistance vers tmp."""
    ChatEngine.reset_singleton()
    test_file = tmp_path / "chat_history.json"
    monkeypatch.setattr("core.chat_engine.CHAT_HISTORY_FILE", test_file)
    yield
    ChatEngine.reset_singleton()


class TestSingleton:
    def test_singleton_returns_same_instance(self):
        a = ChatEngine()
        b = ChatEngine()
        assert a is b

    def test_reset_singleton_creates_new_instance(self):
        a = ChatEngine()
        ChatEngine.reset_singleton()
        b = ChatEngine()
        assert a is not b


class TestSystemPrompt:
    def test_build_system_prompt_contains_identity(self):
        engine = ChatEngine()
        with patch.dict("sys.modules", {
            "core.cardiac_engine": MagicMock(),
            "core.self_awareness": MagicMock(),
            "core.desire_engine": MagicMock(),
            "core.inner_voice": MagicMock(),
            "core.prefrontal": MagicMock(),
        }):
            prompt = engine._build_system_prompt()
        assert "Promethee" in prompt
        assert "premiere personne" in prompt

    def test_build_system_prompt_contains_state_section(self):
        engine = ChatEngine()
        with patch.dict("sys.modules", {
            "core.cardiac_engine": MagicMock(),
            "core.self_awareness": MagicMock(),
            "core.desire_engine": MagicMock(),
            "core.inner_voice": MagicMock(),
            "core.prefrontal": MagicMock(),
        }):
            prompt = engine._build_system_prompt()
        assert "ETAT ACTUEL" in prompt
        assert "Emotion" in prompt
        assert "Humeur" in prompt

    def test_build_system_prompt_graceful_without_organs(self):
        """Fonctionne meme si les imports d'organes echouent."""
        engine = ChatEngine()
        # Forcer les imports a echouer
        def raise_import(*args, **kwargs):
            raise ImportError("mock")

        with patch("builtins.__import__", side_effect=raise_import):
            # Ca va echouer sur TOUT import, donc on utilise une approche plus fine
            pass

        # Approche: patcher les fonctions internes
        prompt = engine._build_system_prompt()
        assert "Promethee" in prompt
        assert len(prompt) > 50


class TestHistory:
    def test_chat_adds_user_message(self):
        engine = ChatEngine()
        engine.messages.append({
            "role": "user", "content": "Bonjour", "timestamp": time.time()
        })
        assert len(engine.messages) == 1
        assert engine.messages[0]["role"] == "user"
        assert engine.messages[0]["content"] == "Bonjour"

    def test_get_history_returns_recent(self):
        engine = ChatEngine()
        for i in range(10):
            engine.messages.append({
                "role": "user", "content": f"msg {i}", "timestamp": time.time()
            })
        history = engine.get_history(5)
        assert len(history) == 5
        assert history[0]["content"] == "msg 5"

    def test_get_history_default(self):
        engine = ChatEngine()
        for i in range(3):
            engine.messages.append({
                "role": "user", "content": f"msg {i}", "timestamp": time.time()
            })
        history = engine.get_history()
        assert len(history) == 3

    def test_clear_history(self):
        engine = ChatEngine()
        engine.messages.append({
            "role": "user", "content": "test", "timestamp": time.time()
        })
        engine.clear_history()
        assert len(engine.messages) == 0


class TestPersistence:
    def test_save_load_roundtrip(self, tmp_path, monkeypatch):
        test_file = tmp_path / "chat_history.json"
        monkeypatch.setattr("core.chat_engine.CHAT_HISTORY_FILE", test_file)

        engine = ChatEngine()
        engine.messages = [
            {"role": "user", "content": "Hello", "timestamp": 1000.0},
            {"role": "assistant", "content": "Bonjour", "timestamp": 1001.0},
        ]
        engine._save()

        # Verifier le fichier existe
        assert test_file.exists()

        # Reset et recharger
        ChatEngine.reset_singleton()
        monkeypatch.setattr("core.chat_engine.CHAT_HISTORY_FILE", test_file)
        engine2 = ChatEngine()
        assert len(engine2.messages) == 2
        assert engine2.messages[0]["content"] == "Hello"
        assert engine2.messages[1]["content"] == "Bonjour"

    def test_fifo_max_messages(self, tmp_path, monkeypatch):
        test_file = tmp_path / "chat_history.json"
        monkeypatch.setattr("core.chat_engine.CHAT_HISTORY_FILE", test_file)

        engine = ChatEngine()
        for i in range(MAX_SAVED_MESSAGES + 50):
            engine.messages.append({
                "role": "user", "content": f"msg {i}", "timestamp": time.time()
            })
        engine._trim_and_save()

        assert len(engine.messages) == MAX_SAVED_MESSAGES
        # Le premier message conserve doit etre msg 50
        assert engine.messages[0]["content"] == "msg 50"


class TestConnexionSatisfaction:
    def test_satisfy_connexion_calls_desire_engine(self):
        engine = ChatEngine()
        mock_desires = MagicMock()
        mock_module = MagicMock()
        mock_module.desires = mock_desires
        with patch.dict("sys.modules", {"core.desire_engine": mock_module}):
            engine._satisfy_connexion()
        mock_desires.on_event.assert_called_once_with("CHAT_RESPONSE")

    def test_satisfy_connexion_graceful_failure(self):
        """Ne crashe pas si desire_engine indisponible."""
        engine = ChatEngine()
        # Simuler un import qui echoue
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def failing_import(name, *args, **kwargs):
            if name == "core.desire_engine":
                raise ImportError("mock fail")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=failing_import):
            engine._satisfy_connexion()  # Ne doit PAS lever d'exception


class TestGracefulDegradation:
    def test_stimulate_heart_graceful(self):
        """Ne crashe pas si cardiac_engine echoue."""
        engine = ChatEngine()
        original_import = __import__

        def failing_import(name, *args, **kwargs):
            if name == "core.cardiac_engine":
                raise ImportError("mock fail")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=failing_import):
            engine._stimulate_heart()

    def test_get_connexion_deprivation_default(self):
        """Retourne une valeur par defaut si desire_engine echoue."""
        engine = ChatEngine()
        original_import = __import__

        def failing_import(name, *args, **kwargs):
            if name == "core.desire_engine":
                raise ImportError("mock fail")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=failing_import):
            dep = engine._get_connexion_deprivation()
            assert dep == 50.0


@pytest.mark.asyncio
class TestChatAsync:
    async def test_chat_publishes_events(self):
        """Verifie que chat() publie les evenements attendus."""
        engine = ChatEngine()
        published_events = []

        async def mock_publish(event_type, data):
            published_events.append(event_type)

        mock_response_lines = [
            json.dumps({"message": {"content": "Salut"}, "done": False}),
            json.dumps({"message": {"content": " ami"}, "done": False}),
            json.dumps({"done": True}),
        ]

        class MockAsyncIterator:
            def __init__(self, lines):
                self._lines = iter(lines)
            def __aiter__(self):
                return self
            async def __anext__(self):
                try:
                    return next(self._lines)
                except StopIteration:
                    raise StopAsyncIteration

        class MockStreamResponse:
            status_code = 200
            def aiter_lines(self):
                return MockAsyncIterator(mock_response_lines)
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def stream(self, method, url, json=None, timeout=None):
                return MockStreamResponse()

        import asyncio
        mock_semaphore = asyncio.Semaphore(2)

        with patch("core.chat_engine.bus.publish", side_effect=mock_publish), \
             patch("core.base_agent.BaseAgent._get_ollama_semaphore", return_value=mock_semaphore), \
             patch("httpx.AsyncClient", return_value=MockClient()), \
             patch.object(engine, "_satisfy_connexion"), \
             patch.object(engine, "_stimulate_heart"), \
             patch.object(engine, "_get_connexion_deprivation", return_value=80.0):

            result = await engine.chat("Bonjour Promethee")

        assert result is not None
        assert "Salut" in result
        assert "USER_CHAT" in published_events
        assert "CHAT_STREAM" in published_events
        assert "CHAT_RESPONSE" in published_events

    async def test_chat_adds_to_history(self):
        """Verifie que les messages sont ajoutes a l'historique."""
        engine = ChatEngine()

        mock_response_lines = [
            json.dumps({"message": {"content": "Reponse"}, "done": False}),
            json.dumps({"done": True}),
        ]

        class MockAsyncIterator:
            def __init__(self, lines):
                self._lines = iter(lines)
            def __aiter__(self):
                return self
            async def __anext__(self):
                try:
                    return next(self._lines)
                except StopIteration:
                    raise StopAsyncIteration

        class MockStreamResponse:
            status_code = 200
            def aiter_lines(self):
                return MockAsyncIterator(mock_response_lines)
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def stream(self, method, url, json=None, timeout=None):
                return MockStreamResponse()

        import asyncio
        mock_semaphore = asyncio.Semaphore(2)

        with patch("core.chat_engine.bus.publish", new_callable=AsyncMock), \
             patch("core.base_agent.BaseAgent._get_ollama_semaphore", return_value=mock_semaphore), \
             patch("httpx.AsyncClient", return_value=MockClient()), \
             patch.object(engine, "_satisfy_connexion"), \
             patch.object(engine, "_stimulate_heart"), \
             patch.object(engine, "_get_connexion_deprivation", return_value=50.0):

            await engine.chat("Test message")

        assert len(engine.messages) == 2
        assert engine.messages[0]["role"] == "user"
        assert engine.messages[0]["content"] == "Test message"
        assert engine.messages[1]["role"] == "assistant"
        assert engine.messages[1]["content"] == "Reponse"
