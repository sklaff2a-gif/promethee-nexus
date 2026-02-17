"""Tests pour Agents/infra_agent.py — DivineInfra health check & monitoring."""
import pytest
from unittest.mock import patch, Mock, AsyncMock, MagicMock
from Agents.infra_agent import DivineInfra


class TestDivineInfraInit:
    """Tests d'initialisation de DivineInfra."""

    def test_init_name_and_role(self):
        infra = DivineInfra()
        assert infra.name == "infra"
        assert infra.role == "DevOps SRE & Hardware Guardian"

    def test_init_limits_default(self):
        infra = DivineInfra()
        assert "RAM_GB" in infra.limits
        assert "VRAM_GB" in infra.limits


class TestSimpleMetrics:
    """Tests pour _get_simple_metrics()."""

    @patch('Agents.infra_agent.psutil')
    def test_get_simple_metrics_format(self, mock_psutil):
        mock_ram = Mock()
        mock_ram.percent = 50
        mock_psutil.virtual_memory.return_value = mock_ram
        mock_psutil.cpu_percent.return_value = 25

        infra = DivineInfra()
        metrics = infra._get_simple_metrics()
        assert "CPU: 25%" in metrics
        assert "RAM: 50%" in metrics


class TestHealthCheck:
    """Tests pour _perform_health_check() (async)."""

    @pytest.mark.asyncio
    @patch('Agents.infra_agent.bus', new_callable=MagicMock)
    @patch('Agents.infra_agent.psutil')
    async def test_health_check_green(self, mock_psutil, mock_bus):
        """Status GREEN quand RAM < 75%."""
        mock_ram = Mock()
        mock_ram.percent = 50
        mock_ram.used = 16 * (1024**3)
        mock_psutil.virtual_memory.return_value = mock_ram
        mock_psutil.cpu_percent.return_value = 25
        mock_bus.publish = AsyncMock()

        infra = DivineInfra()
        result = await infra._perform_health_check()
        assert result["status"] == "success"
        assert "GREEN" in result["result"]
        assert "RAM Sys" in result["result"]

    @pytest.mark.asyncio
    @patch('Agents.infra_agent.bus', new_callable=MagicMock)
    @patch('Agents.infra_agent.psutil')
    async def test_health_check_orange_ram(self, mock_psutil, mock_bus):
        """Status ORANGE quand RAM > 75%."""
        mock_ram = Mock()
        mock_ram.percent = 80
        mock_ram.used = 25.6 * (1024**3)
        mock_psutil.virtual_memory.return_value = mock_ram
        mock_psutil.cpu_percent.return_value = 40
        mock_bus.publish = AsyncMock()

        infra = DivineInfra()
        result = await infra._perform_health_check()
        assert result["status"] == "success"
        assert "ORANGE" in result["result"]

    @pytest.mark.asyncio
    @patch('Agents.infra_agent.bus', new_callable=MagicMock)
    @patch('Agents.infra_agent.psutil')
    async def test_health_check_red_ram(self, mock_psutil, mock_bus):
        """Status RED quand RAM > 90%."""
        mock_ram = Mock()
        mock_ram.percent = 95
        mock_ram.used = 30.4 * (1024**3)
        mock_psutil.virtual_memory.return_value = mock_ram
        mock_psutil.cpu_percent.return_value = 60
        mock_bus.publish = AsyncMock()

        infra = DivineInfra()
        result = await infra._perform_health_check()
        assert result["status"] == "success"
        assert "RED" in result["result"]
        assert "CRITIQUE" in result["result"]

    @pytest.mark.asyncio
    @patch('Agents.infra_agent.bus', new_callable=MagicMock)
    @patch('Agents.infra_agent.psutil')
    async def test_health_check_with_gpu(self, mock_psutil, mock_bus):
        """Health check inclut les métriques GPU quand GPUtil est disponible."""
        import Agents.infra_agent as infra_mod

        mock_ram = Mock()
        mock_ram.percent = 50
        mock_ram.used = 16 * (1024**3)
        mock_psutil.virtual_memory.return_value = mock_ram
        mock_psutil.cpu_percent.return_value = 25
        mock_bus.publish = AsyncMock()

        # GPUtil retourne memoryUsed en MB et load en ratio 0-1
        mock_gputil = MagicMock()
        mock_gpu = Mock()
        mock_gpu.memoryUsed = 4096  # 4 GB en MB
        mock_gpu.load = 0.35       # 35%
        mock_gputil.getGPUs.return_value = [mock_gpu]

        # Injecter GPUtil dans le module (pas installé normalement)
        orig_gpu_available = infra_mod.GPU_AVAILABLE
        infra_mod.GPU_AVAILABLE = True
        infra_mod.GPUtil = mock_gputil
        try:
            infra = DivineInfra()
            result = await infra._perform_health_check()
            assert result["status"] == "success"
            assert "VRAM GPU" in result["result"]
        finally:
            infra_mod.GPU_AVAILABLE = orig_gpu_available
            if hasattr(infra_mod, 'GPUtil') and infra_mod.GPUtil is mock_gputil:
                delattr(infra_mod, 'GPUtil')

    @pytest.mark.asyncio
    @patch('Agents.infra_agent.bus', new_callable=MagicMock)
    @patch('Agents.infra_agent.psutil')
    async def test_health_check_publishes_event(self, mock_psutil, mock_bus):
        """Health check publie un événement THOUGHT_STREAM."""
        mock_ram = Mock()
        mock_ram.percent = 50
        mock_ram.used = 16 * (1024**3)
        mock_psutil.virtual_memory.return_value = mock_ram
        mock_psutil.cpu_percent.return_value = 25
        mock_bus.publish = AsyncMock()

        infra = DivineInfra()
        await infra._perform_health_check()
        mock_bus.publish.assert_called_once()
        call_args = mock_bus.publish.call_args
        assert call_args[0][0] == "THOUGHT_STREAM"


class TestProcessTask:
    """Tests pour process_task() dispatch."""

    @pytest.mark.asyncio
    @patch('Agents.infra_agent.bus', new_callable=MagicMock)
    @patch('Agents.infra_agent.psutil')
    async def test_health_keyword_triggers_guardian(self, mock_psutil, mock_bus):
        """Les mots-clés santé déclenchent le mode gardien."""
        mock_ram = Mock()
        mock_ram.percent = 50
        mock_ram.used = 16 * (1024**3)
        mock_psutil.virtual_memory.return_value = mock_ram
        mock_psutil.cpu_percent.return_value = 25
        mock_bus.publish = AsyncMock()

        infra = DivineInfra()
        for keyword in ["status", "santé", "health", "cpu", "ram"]:
            result = await infra.process_task({"mission": f"check {keyword}"})
            assert result["status"] == "success"
            assert "RAPPORT INFRA" in result["result"]

    @pytest.mark.asyncio
    async def test_expert_mode_adds_metrics_to_context(self):
        """Le mode expert injecte les métriques dans le contexte."""
        infra = DivineInfra()
        payload = {"mission": "optimise la mémoire pour Ollama", "context": "test"}

        with patch.object(infra, 'generate_content', new_callable=AsyncMock, return_value="Réponse expert"):
            with patch.object(infra, '_get_simple_metrics', return_value="CPU: 25% | RAM: 50%"):
                with patch('Agents.infra_agent.bus'):
                    result = await infra.process_task(payload)
                    assert "ÉTAT ACTUEL DU SERVEUR" in payload["context"]
