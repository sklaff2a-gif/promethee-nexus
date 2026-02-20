# tests/test_memory_health.py — Tests Surveillance Santé Mémoire
import os
import sys
import time
import pytest
import tempfile
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

import chromadb

# Ajouter le dossier parent au path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.vector_store import ChromaMemoryManager


@pytest.fixture(autouse=True)
def isolate_chroma(tmp_path):
    """Isole ChromaDB dans un tmpdir pour chaque test."""
    old_instances = ChromaMemoryManager._instances.copy()
    ChromaMemoryManager._instances.clear()
    yield tmp_path
    ChromaMemoryManager._instances.clear()
    ChromaMemoryManager._instances.update(old_instances)


def _make_manager(tmp_path, use_persistent=True):
    """Crée un ChromaMemoryManager avec un chemin temporaire."""
    db_path = os.path.join(str(tmp_path), "chroma_test")
    os.makedirs(db_path, exist_ok=True)
    mgr = ChromaMemoryManager.__new__(ChromaMemoryManager)
    mgr.project_id = "test"
    mgr.db_path = db_path
    if use_persistent:
        mgr.client = chromadb.PersistentClient(path=db_path)
    else:
        mgr.client = chromadb.EphemeralClient()
    mgr.collections = {
        "collective_wisdom": mgr.client.get_or_create_collection(name="collective_wisdom"),
    }
    ChromaMemoryManager._instances["test"] = mgr
    return mgr


# ============================================================
# TestSanitizeMetadata
# ============================================================

class TestSanitizeMetadata:
    """Tests pour _sanitize_metadata (Couche 1 — Prévention)."""

    def test_strings_pass_through(self):
        meta = [{"name": "test", "count": 42, "ratio": 3.14, "active": True}]
        result = ChromaMemoryManager._sanitize_metadata(meta)
        assert result == meta

    def test_list_serialized(self):
        meta = [{"tags": ["a", "b", "c"]}]
        result = ChromaMemoryManager._sanitize_metadata(meta)
        assert result[0]["tags"] == "['a', 'b', 'c']"

    def test_none_to_empty_string(self):
        meta = [{"value": None}]
        result = ChromaMemoryManager._sanitize_metadata(meta)
        assert result[0]["value"] == ""

    def test_dict_serialized(self):
        meta = [{"config": {"key": "val"}}]
        result = ChromaMemoryManager._sanitize_metadata(meta)
        assert isinstance(result[0]["config"], str)
        assert "key" in result[0]["config"]


# ============================================================
# TestCheckHealth
# ============================================================

class TestCheckHealth:
    """Tests pour check_health() (Couche 2 — Détection)."""

    def test_healthy_persistent(self, isolate_chroma):
        mgr = _make_manager(isolate_chroma, use_persistent=True)
        result = mgr.check_health()
        assert result["status"] == "healthy"
        assert result["persistent"] is True
        assert result["probe_ok"] is True
        assert "collective_wisdom" in result["collections"]

    def test_healthy_ephemeral(self, isolate_chroma):
        mgr = _make_manager(isolate_chroma, use_persistent=False)
        result = mgr.check_health()
        assert result["status"] == "degraded"
        assert result["persistent"] is False
        assert result["probe_ok"] is True
        assert any("EphemeralClient" in w for w in result["warnings"])

    def test_collection_error(self, isolate_chroma):
        mgr = _make_manager(isolate_chroma, use_persistent=True)
        # Simuler une collection cassée
        mock_col = MagicMock()
        mock_col.count.side_effect = Exception("Corruption HNSW")
        mgr.collections["broken_col"] = mock_col
        result = mgr.check_health()
        assert result["collections"]["broken_col"] == -1
        assert result["status"] in ("degraded", "down")
        assert any("broken_col" in w for w in result["warnings"])

    def test_probe_failure(self, isolate_chroma):
        mgr = _make_manager(isolate_chroma, use_persistent=True)
        # Simuler un client dont get_or_create_collection échoue pour la probe
        original_get = mgr.client.get_or_create_collection

        def _fail_probe(name, **kwargs):
            if name == "health-probe":
                raise Exception("Disk full")
            return original_get(name, **kwargs)

        mgr.client.get_or_create_collection = _fail_probe
        result = mgr.check_health()
        assert result["probe_ok"] is False
        assert any("Probe" in w for w in result["warnings"])


# ============================================================
# TestSystemHealthCheckMemory
# ============================================================

class TestSystemHealthCheckMemory:
    """Tests pour l'intégration mémoire dans SystemHealthCheck (Couche 3)."""

    @pytest.mark.asyncio
    async def test_health_includes_memory(self, isolate_chroma):
        mgr = _make_manager(isolate_chroma, use_persistent=True)

        import psutil as _psutil
        import httpx as _httpx

        with patch.object(_psutil, "cpu_percent", return_value=30.0), \
             patch.object(_psutil, "virtual_memory") as mock_vm, \
             patch.object(_httpx, "AsyncClient") as mock_httpx_client:
            mem = MagicMock()
            mem.percent = 50.0
            mem.used = 8 * 1024**3
            mem.total = 16 * 1024**3
            mock_vm.return_value = mem

            # Mock httpx pour Ollama
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"models": []}
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_httpx_client.return_value = mock_client

            from core.autonomy_engine import SystemHealthCheck
            result = await SystemHealthCheck.run()

            assert "memory" in result
            assert result["memory"]["status"] == "healthy"
            assert result["memory"]["probe_ok"] is True

    @pytest.mark.asyncio
    async def test_memory_down_warning(self, isolate_chroma):
        # Pas d'instance ChromaDB → mémoire down
        ChromaMemoryManager._instances.clear()

        import psutil as _psutil
        import httpx as _httpx

        with patch.object(_psutil, "cpu_percent", return_value=30.0), \
             patch.object(_psutil, "virtual_memory") as mock_vm, \
             patch.object(_httpx, "AsyncClient") as mock_httpx_client:
            mem = MagicMock()
            mem.percent = 50.0
            mem.used = 8 * 1024**3
            mem.total = 16 * 1024**3
            mock_vm.return_value = mem

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"models": []}
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_httpx_client.return_value = mock_client

            from core.autonomy_engine import SystemHealthCheck
            result = await SystemHealthCheck.run()

            assert result["memory"]["status"] == "down"
            assert any("ChromaDB" in w for w in result["warnings"])


# ============================================================
# TestMemoryAlert
# ============================================================

class TestMemoryAlert:
    """Tests pour l'alerte MEMORY_HEALTH_ALERT (Couche 4)."""

    @pytest.mark.asyncio
    async def test_alert_published_on_degraded(self, isolate_chroma):
        from core.event_bus.bus import bus
        received = []

        async def _capture(event):
            received.append(event)

        bus.subscribe("MEMORY_HEALTH_ALERT", _capture)
        try:
            await bus.publish("MEMORY_HEALTH_ALERT", {
                "status": "degraded",
                "warnings": ["Mode EphemeralClient"],
                "persistent": False,
                "collections": {"collective_wisdom": 10},
            })
            # Laisser le bus dispatcher les handlers async
            await asyncio.sleep(0.1)
            assert len(received) >= 1
            data = received[0].get("data", received[0])
            assert data.get("status") == "degraded"
        finally:
            bus.unsubscribe("MEMORY_HEALTH_ALERT", _capture)

    def test_interface_logger_formats_alert(self):
        from core.interface_logger import _format_event
        # Capturer ce qui est écrit
        written_lines = []
        with patch("core.interface_logger._write", side_effect=written_lines.append):
            _format_event("MEMORY_HEALTH_ALERT", {
                "status": "down",
                "warnings": ["Probe échoué: Disk full"],
            })
        assert len(written_lines) == 1
        assert "MÉMOIRE" in written_lines[0]
        assert "DOWN" in written_lines[0]
        assert "critical" in written_lines[0]
