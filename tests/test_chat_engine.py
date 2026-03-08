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
        prompt = engine._build_system_prompt()
        assert "Promethee" in prompt
        assert len(prompt) > 50

    def test_build_system_prompt_contains_new_sections(self):
        """Verifie que les nouvelles sections d'introspection sont presentes."""
        engine = ChatEngine()

        # Mock InnerVoice
        mock_voice = MagicMock()
        mock_voice.get_identity.return_value = {
            "core_identity": "Je suis un systeme en evolution",
            "aspiration": "Comprendre et m'ameliorer",
        }
        mock_voice.get_stream.return_value = []
        mock_inner_voice_mod = MagicMock()
        mock_inner_voice_mod.voice = mock_voice

        # Mock CardiacEngine
        mock_heart = MagicMock()
        mock_heart.current_emotion = "curiosite"
        mock_heart.emotional_intensity = 0.7
        mock_heart.get_stats.return_value = {"bpm": 72, "coherence": 0.85}
        mock_heart.get_narrative.return_value = "Je me sens vivant"
        mock_cardiac_mod = MagicMock()
        mock_cardiac_mod.heart = mock_heart

        # Mock DopamineSystem
        mock_dopamine = MagicMock()
        mock_dopamine.dopamine_level = 65.0
        mock_dopamine.get_narrative.return_value = "Motivation elevee"
        mock_dopamine_mod = MagicMock()
        mock_dopamine_mod.dopamine = mock_dopamine

        # Mock CorpusCallosum
        mock_callosum = MagicMock()
        mock_callosum.get_cognitive_context.return_value = "Etat cognitif: flow"
        mock_callosum_mod = MagicMock()
        mock_callosum_mod.callosum = mock_callosum

        # Mock ReptilianCore
        mock_reptile = MagicMock()
        mock_reptile.get_stats.return_value = {"threat_level": 2.5, "adrenaline": 1.0}
        mock_reptile_mod = MagicMock()
        mock_reptile_mod.reptile = mock_reptile

        # Mock SynapticNetwork
        mock_cortex = MagicMock()
        mock_cortex.get_stats.return_value = {"total_nodes": 42, "total_synapses": 128}
        mock_cortex_mod = MagicMock()
        mock_cortex_mod.cortex = mock_cortex

        # Mock Hippocampus
        mock_hippo = MagicMock()
        mock_hippo.get_hippocampus_context.return_value = "Dernier arc: exploration reussie"
        mock_hippo_mod = MagicMock()
        mock_hippo_mod.hippocampus = mock_hippo

        # Mock AutonomyEngine
        mock_autonomy = MagicMock()
        mock_autonomy.get_status.return_value = {
            "routine_history": [
                {"intent": "MEMORY_CLEANUP"},
                {"intent": "AUDIT_STRUCTURE"},
                {"intent": "COUNCIL_DEBATE"},
            ]
        }
        mock_autonomy_mod = MagicMock()
        mock_autonomy_mod.autonomy = mock_autonomy

        # Mock Psyche
        mock_psyche = MagicMock()
        mock_psyche.get_system_average.return_value = {
            "curiosite": 1.5, "prudence": -0.3, "creativite": 0.8
        }
        mock_psyche_mod = MagicMock()
        mock_psyche_mod.psyche = mock_psyche

        with patch.dict("sys.modules", {
            "core.inner_voice": mock_inner_voice_mod,
            "core.cardiac_engine": mock_cardiac_mod,
            "core.self_awareness": MagicMock(),
            "core.desire_engine": MagicMock(),
            "core.prefrontal": MagicMock(),
            "core.dopamine_system": mock_dopamine_mod,
            "core.corpus_callosum": mock_callosum_mod,
            "core.reptilian_core": mock_reptile_mod,
            "core.synaptic_network": mock_cortex_mod,
            "core.hippocampus": mock_hippo_mod,
            "core.autonomy_engine": mock_autonomy_mod,
            "core.psyche": mock_psyche_mod,
        }):
            prompt = engine._build_system_prompt()

        assert "[IDENTITE]" in prompt
        assert "evolution" in prompt
        assert "[CORPS]" in prompt
        assert "72 bpm" in prompt
        assert "[DOPAMINE]" in prompt
        assert "65.0" in prompt
        assert "[RESONANCE]" in prompt
        assert "flow" in prompt
        assert "[MENACES]" in prompt
        assert "[TISSU NEURAL]" in prompt
        assert "42 concepts" in prompt
        assert "[MEMOIRE]" in prompt
        assert "[ROUTINES]" in prompt
        assert "COUNCIL_DEBATE" in prompt
        assert "[PERSONNALITE]" in prompt
        assert "curiosite" in prompt
        assert "etat REEL" in prompt

    def test_build_system_prompt_with_memories(self):
        """Verifie que les souvenirs RAG sont injectes dans la section MEMOIRE."""
        engine = ChatEngine()
        memories = "Souvenir 1 | Souvenir 2 | Souvenir 3"

        # Mock hippocampus pour que la section MEMOIRE existe
        mock_hippo = MagicMock()
        mock_hippo.get_hippocampus_context.return_value = "Arc test"
        mock_hippo_mod = MagicMock()
        mock_hippo_mod.hippocampus = mock_hippo

        with patch.dict("sys.modules", {
            "core.hippocampus": mock_hippo_mod,
        }):
            prompt = engine._build_system_prompt(memories_text=memories)

        assert "[MEMOIRE]" in prompt
        assert "Souvenir 1" in prompt
        assert "Souvenir 3" in prompt

    def test_build_system_prompt_memories_without_hippocampus(self):
        """Les souvenirs RAG apparaissent meme si hippocampus echoue."""
        engine = ChatEngine()
        memories = "Un souvenir important"
        prompt = engine._build_system_prompt(memories_text=memories)
        assert "Un souvenir important" in prompt


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


class TestQueryRelevantMemories:
    def test_query_relevant_memories_graceful(self):
        """Ne crashe pas si ChromaDB est indisponible."""
        engine = ChatEngine()
        result = engine._query_relevant_memories("test query")
        # Retourne une chaine vide si ChromaDB echoue
        assert isinstance(result, str)

    def test_query_relevant_memories_returns_docs(self):
        """Retourne les documents trouves dans ChromaDB."""
        engine = ChatEngine()
        mock_mem = MagicMock()
        mock_mem.query_documents.return_value = {
            "documents": [["Souvenir alpha", "Souvenir beta", "Souvenir gamma"]]
        }
        mock_class = MagicMock()
        mock_class.get_instance.return_value = mock_mem
        mock_module = MagicMock()
        mock_module.ChromaMemoryManager = mock_class

        with patch.dict("sys.modules", {"core.vector_store": mock_module}):
            result = engine._query_relevant_memories("question test")

        assert "Souvenir alpha" in result
        assert "Souvenir beta" in result
        assert "Souvenir gamma" in result

    def test_query_relevant_memories_truncates_long_docs(self):
        """Tronque les documents trop longs a 150 chars."""
        engine = ChatEngine()
        long_doc = "X" * 300
        mock_mem = MagicMock()
        mock_mem.query_documents.return_value = {
            "documents": [[long_doc]]
        }
        mock_class = MagicMock()
        mock_class.get_instance.return_value = mock_mem
        mock_module = MagicMock()
        mock_module.ChromaMemoryManager = mock_class

        with patch.dict("sys.modules", {"core.vector_store": mock_module}):
            result = engine._query_relevant_memories("test")

        assert len(result) == 150
