"""
Tests pour le Circuit Breaker Ollama — protection anti-GPU-hang.

Couvre :
- Circuit breaker dans BaseAgent (activation/reset/état)
- Capteur ollama_hung dans ReptilianCore
- Intégration : agents bloqués quand circuit ouvert
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core.base_agent import BaseAgent
from core.reptilian_core import ReptilianCore


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    """Reset le circuit breaker entre chaque test."""
    BaseAgent._ollama_consecutive_timeouts = 0
    BaseAgent._ollama_circuit_open_until = 0.0
    BaseAgent._ollama_last_success = 0.0
    yield
    BaseAgent._ollama_consecutive_timeouts = 0
    BaseAgent._ollama_circuit_open_until = 0.0
    BaseAgent._ollama_last_success = 0.0


@pytest.fixture(autouse=True)
def reset_reptilian(tmp_path, monkeypatch):
    """Reset singleton reptilien + isolation fichier."""
    ReptilianCore.reset_singleton()
    monkeypatch.setattr("core.reptilian_core.REPTILIAN_STATE_FILE", str(tmp_path / "reptilian.json"))
    yield
    ReptilianCore.reset_singleton()


@pytest.fixture
def agent():
    """Agent de test minimal."""
    a = BaseAgent.__new__(BaseAgent)
    a.name = "test_agent"
    a.role = "testeur"
    a.description = "Agent de test"
    a.logger = MagicMock()
    return a


# ============================================================
# 1. Circuit Breaker — Compteur de timeouts
# ============================================================

class TestCircuitBreakerCounter:

    def test_initial_state_closed(self):
        """Le circuit est fermé au départ."""
        assert not BaseAgent._ollama_circuit_is_open()
        assert BaseAgent._ollama_consecutive_timeouts == 0

    def test_timeout_increments_counter(self):
        """Chaque timeout incrémente le compteur."""
        BaseAgent._ollama_record_timeout()
        assert BaseAgent._ollama_consecutive_timeouts == 1
        BaseAgent._ollama_record_timeout()
        assert BaseAgent._ollama_consecutive_timeouts == 2

    def test_success_resets_counter(self):
        """Un succès remet le compteur à 0."""
        BaseAgent._ollama_record_timeout()
        BaseAgent._ollama_record_timeout()
        assert BaseAgent._ollama_consecutive_timeouts == 2
        BaseAgent._ollama_record_success()
        assert BaseAgent._ollama_consecutive_timeouts == 0

    def test_circuit_opens_at_threshold(self):
        """Le circuit s'ouvre après THRESHOLD timeouts consécutifs."""
        for _ in range(BaseAgent.OLLAMA_CIRCUIT_BREAKER_THRESHOLD):
            BaseAgent._ollama_record_timeout()
        assert BaseAgent._ollama_circuit_is_open()

    def test_circuit_stays_closed_below_threshold(self):
        """Le circuit reste fermé en dessous du seuil."""
        for _ in range(BaseAgent.OLLAMA_CIRCUIT_BREAKER_THRESHOLD - 1):
            BaseAgent._ollama_record_timeout()
        assert not BaseAgent._ollama_circuit_is_open()

    def test_circuit_closes_after_duration(self):
        """Le circuit se referme après la durée configurée."""
        for _ in range(BaseAgent.OLLAMA_CIRCUIT_BREAKER_THRESHOLD):
            BaseAgent._ollama_record_timeout()
        assert BaseAgent._ollama_circuit_is_open()
        # Simuler expiration
        BaseAgent._ollama_circuit_open_until = time.time() - 1
        assert not BaseAgent._ollama_circuit_is_open()

    def test_success_closes_circuit(self):
        """Un succès referme le circuit même s'il est ouvert."""
        for _ in range(BaseAgent.OLLAMA_CIRCUIT_BREAKER_THRESHOLD):
            BaseAgent._ollama_record_timeout()
        assert BaseAgent._ollama_circuit_is_open()
        BaseAgent._ollama_record_success()
        assert not BaseAgent._ollama_circuit_is_open()
        assert BaseAgent._ollama_consecutive_timeouts == 0


# ============================================================
# 2. Circuit Breaker — Health API
# ============================================================

class TestOllamaHealth:

    def test_health_closed(self):
        """État de santé quand tout va bien."""
        health = BaseAgent.get_ollama_health()
        assert health["circuit_open"] is False
        assert health["consecutive_timeouts"] == 0

    def test_health_open(self):
        """État de santé quand le circuit est ouvert."""
        for _ in range(BaseAgent.OLLAMA_CIRCUIT_BREAKER_THRESHOLD):
            BaseAgent._ollama_record_timeout()
        health = BaseAgent.get_ollama_health()
        assert health["circuit_open"] is True
        assert health["consecutive_timeouts"] >= BaseAgent.OLLAMA_CIRCUIT_BREAKER_THRESHOLD
        assert health["blocked_until"] > time.time()

    def test_health_reports_last_success(self):
        """Le timestamp du dernier succès est rapporté."""
        before = time.time()
        BaseAgent._ollama_record_success()
        health = BaseAgent.get_ollama_health()
        assert health["last_success"] >= before


# ============================================================
# 3. Agent — Appels bloqués par circuit breaker
# ============================================================

class TestAgentCircuitBreaker:

    @pytest.mark.asyncio
    async def test_call_ollama_blocked_when_circuit_open(self, agent):
        """_call_ollama retourne immédiatement quand le circuit est ouvert."""
        # Ouvrir le circuit
        BaseAgent._ollama_circuit_open_until = time.time() + 300
        result = await agent._call_ollama("test prompt", "test-model")
        assert "CIRCUIT BREAKER" in result

    @pytest.mark.asyncio
    async def test_call_ollama_stream_blocked_when_circuit_open(self, agent):
        """_call_ollama_stream retourne immédiatement quand le circuit est ouvert."""
        BaseAgent._ollama_circuit_open_until = time.time() + 300
        result = await agent._call_ollama_stream("test prompt", "test-model")
        assert "CIRCUIT BREAKER" in result

    @pytest.mark.asyncio
    async def test_timeout_opens_circuit_after_threshold(self, agent):
        """3 ReadTimeouts consécutifs ouvrent le circuit."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=httpx.ReadTimeout("timeout"))
            mock_client_cls.return_value = mock_client

            for _ in range(BaseAgent.OLLAMA_CIRCUIT_BREAKER_THRESHOLD):
                await agent._call_ollama("test", "model")

            assert BaseAgent._ollama_circuit_is_open()

    @pytest.mark.asyncio
    async def test_successful_call_resets_circuit(self, agent):
        """Un appel réussi remet le compteur à 0."""
        BaseAgent._ollama_consecutive_timeouts = 2  # Presque au seuil

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": "ok"}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await agent._call_ollama("test", "model")
            assert result == "ok"
            assert BaseAgent._ollama_consecutive_timeouts == 0


# ============================================================
# 4. ReptilianCore — Capteur ollama_hung
# ============================================================

class TestReptilianOllamaHung:

    @pytest.mark.asyncio
    async def test_no_threat_when_ollama_healthy(self):
        """Pas de menace ollama_hung quand le circuit est fermé."""
        rept = ReptilianCore()
        # Mock Ollama ping OK + autonomy
        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch.dict("sys.modules", {"core.autonomy_engine": MagicMock()}):
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            threats = await rept._sense_threats()
            assert "ollama_hung" not in threats

    @pytest.mark.asyncio
    async def test_threat_when_circuit_open(self):
        """Menace ollama_hung=8.0 quand le circuit breaker est ouvert."""
        rept = ReptilianCore()
        # Ouvrir le circuit
        BaseAgent._ollama_circuit_open_until = time.time() + 300
        BaseAgent._ollama_consecutive_timeouts = 5

        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch.dict("sys.modules", {"core.autonomy_engine": MagicMock()}):
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200  # Ollama ping OK (process vivant)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            threats = await rept._sense_threats()
            assert "ollama_hung" in threats
            assert threats["ollama_hung"] == 8.0
            # Le process Ollama est UP mais le GPU est bloqué
            assert "ollama" not in threats  # Pas de menace process

    @pytest.mark.asyncio
    async def test_warning_threat_at_2_timeouts(self):
        """Menace ollama_hung=5.0 avec 2 timeouts (pré-alerte)."""
        rept = ReptilianCore()
        BaseAgent._ollama_consecutive_timeouts = 2

        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch.dict("sys.modules", {"core.autonomy_engine": MagicMock()}):
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            threats = await rept._sense_threats()
            assert threats.get("ollama_hung") == 5.0


# ============================================================
# 5. Scénario complet — Simulation GPU hang nocturne
# ============================================================

class TestGPUHangScenario:

    @pytest.mark.asyncio
    async def test_full_scenario_timeouts_then_blocked(self, agent):
        """Scénario réel : 3 timeouts → circuit ouvert → 4ème appel bloqué immédiatement."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=httpx.ReadTimeout("GPU stuck"))
            mock_client_cls.return_value = mock_client

            # 3 appels qui timeout
            for i in range(3):
                result = await agent._call_ollama("prompt", "model")
                assert "ÉCHEC" in result

        # Le 4ème appel est bloqué IMMÉDIATEMENT (pas de timeout de 5 min)
        assert BaseAgent._ollama_circuit_is_open()
        result = await agent._call_ollama("prompt", "model")
        assert "CIRCUIT BREAKER" in result

    @pytest.mark.asyncio
    async def test_recovery_after_circuit_expires(self, agent):
        """Après expiration du circuit, un appel réussi remet tout à la normale."""
        # Simuler circuit ouvert puis expiré
        BaseAgent._ollama_consecutive_timeouts = 5
        BaseAgent._ollama_circuit_open_until = time.time() - 1  # Expiré

        assert not BaseAgent._ollama_circuit_is_open()

        # Simuler un appel réussi
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": "récupéré!"}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await agent._call_ollama("test", "model")
            assert result == "récupéré!"
            assert BaseAgent._ollama_consecutive_timeouts == 0
            assert not BaseAgent._ollama_circuit_is_open()
