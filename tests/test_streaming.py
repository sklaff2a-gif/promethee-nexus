import pytest
import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock
from core.event_bus.bus import bus


# --- Mocks pour simuler le streaming httpx ---

class FakeStreamResponse:
    """Simule une réponse httpx en streaming avec des chunks NDJSON."""

    def __init__(self, chunks, status_code=200):
        self.status_code = status_code
        self._chunks = chunks

    async def aiter_lines(self):
        for chunk in self._chunks:
            yield json.dumps(chunk)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class FakeStreamClient:
    """Simule httpx.AsyncClient avec support du context manager .stream()."""

    def __init__(self, response):
        self._response = response

    def stream(self, method, url, json=None, timeout=None):
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class FakeStreamClientError:
    """Simule httpx.AsyncClient qui lève une exception pendant le streaming."""

    def stream(self, method, url, json=None, timeout=None):
        raise ConnectionError("Connexion refusée")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _make_agent():
    """Crée un BaseAgent minimal sans dépendances externes."""
    with patch("core.base_agent.ChromaMemoryManager", None):
        from core.base_agent import BaseAgent
        agent = BaseAgent.__new__(BaseAgent)
        agent.name = "test_agent"
        agent.role = "testeur"
        agent.description = "Agent de test"
        agent.has_memory = False
        agent.cloud_models = []
        agent.logger = MagicMock()
    return agent


class TestStreamRetourComplet:

    @pytest.mark.asyncio
    async def test_retourne_texte_complet(self):
        """Les chunks ['Bon', 'jour', ' !'] doivent retourner 'Bonjour !'."""
        agent = _make_agent()
        chunks = [
            {"response": "Bon", "done": False},
            {"response": "jour", "done": False},
            {"response": " !", "done": False},
            {"response": "", "done": True},
        ]
        fake_response = FakeStreamResponse(chunks)
        fake_client = FakeStreamClient(fake_response)

        with patch("httpx.AsyncClient", return_value=fake_client):
            result = await agent._call_ollama_stream("test prompt", "test-model")

        assert result == "Bonjour !"


class TestStreamEvenementsBus:

    @pytest.mark.asyncio
    async def test_publie_start_chunks_end(self):
        """Vérifie que start + N chunks + end sont publiés sur AGENT_STREAM."""
        agent = _make_agent()
        chunks = [
            {"response": "A", "done": False},
            {"response": "B", "done": False},
            {"response": "", "done": True},
        ]
        fake_response = FakeStreamResponse(chunks)
        fake_client = FakeStreamClient(fake_response)

        received = []
        bus.subscribe("AGENT_STREAM", lambda data: received.append(data))

        with patch("httpx.AsyncClient", return_value=fake_client):
            await agent._call_ollama_stream("test", "model")

        # start + 2 chunks (A, B) + end = 4 événements
        assert len(received) == 4
        assert received[0]["status"] == "start"
        assert received[0]["done"] is False
        assert received[1]["chunk"] == "A"
        assert received[2]["chunk"] == "B"
        assert received[3]["status"] == "end"
        assert received[3]["done"] is True


class TestStreamIdCoherent:

    @pytest.mark.asyncio
    async def test_stream_id_identique_pour_tous_les_events(self):
        """Tous les événements d'un stream partagent le même stream_id."""
        agent = _make_agent()
        chunks = [
            {"response": "X", "done": False},
            {"response": "", "done": True},
        ]
        fake_response = FakeStreamResponse(chunks)
        fake_client = FakeStreamClient(fake_response)

        received = []
        bus.subscribe("AGENT_STREAM", lambda data: received.append(data))

        with patch("httpx.AsyncClient", return_value=fake_client):
            await agent._call_ollama_stream("test", "model")

        stream_ids = {ev["stream_id"] for ev in received}
        assert len(stream_ids) == 1, f"IDs multiples détectés : {stream_ids}"


class TestStreamErreurSignalFin:

    @pytest.mark.asyncio
    async def test_erreur_envoie_signal_end(self):
        """En cas d'erreur httpx, un signal end doit quand même être publié."""
        agent = _make_agent()

        received = []
        bus.subscribe("AGENT_STREAM", lambda data: received.append(data))

        with patch("httpx.AsyncClient", return_value=FakeStreamClientError()):
            result = await agent._call_ollama_stream("test", "model")

        # On doit avoir au moins le start et le end
        assert any(ev.get("status") == "start" for ev in received)
        assert any(ev.get("status") == "end" and ev["done"] is True for ev in received)
        assert "ÉCHEC" in result


class TestEvaluateComplexityUtiliseCallOllama:

    @pytest.mark.asyncio
    async def test_evaluate_complexity_appelle_call_ollama_pas_stream(self):
        """_evaluate_complexity doit utiliser _call_ollama, pas _call_ollama_stream."""
        agent = _make_agent()

        agent._call_ollama = AsyncMock(return_value="NON")
        agent._call_ollama_stream = AsyncMock(return_value="NON")
        agent.log_thought = MagicMock()

        await agent._evaluate_complexity("test prompt")

        agent._call_ollama.assert_called_once()
        agent._call_ollama_stream.assert_not_called()
