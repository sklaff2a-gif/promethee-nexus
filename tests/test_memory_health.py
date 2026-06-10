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

import core.vector_store as vsmod
from core.vector_store import ChromaMemoryManager


@pytest.fixture(autouse=True)
def isolate_chroma(tmp_path, monkeypatch):
    """Isole ChromaDB dans un tmpdir pour chaque test.
    Phase 4 (10/06) : on teste la mecanique sante/recall sur le chemin ancien -> full switch
    OFF ici (le routage temoin est teste a part, mocke, dans test_full_switch_mem_v2)."""
    monkeypatch.setattr(vsmod, "MEM_V2_FULL_SWITCH", False)
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
    mgr._lock = None
    mgr._lock_loop_id = None
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


# ============================================================
# TestAsyncLock — Méthodes async protégées par asyncio.Lock
# ============================================================

class TestAsyncLock:
    """Tests pour les méthodes async lockées de ChromaMemoryManager."""

    def test_lock_lazy_init(self, isolate_chroma):
        mgr = _make_manager(isolate_chroma, use_persistent=True)
        assert hasattr(mgr, "_get_lock")
        # Lazy-init : _lock est None avant le premier appel
        assert mgr._lock is None
        # Après appel, le lock est créé
        lock = mgr._get_lock()
        assert isinstance(lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_async_purge_expired_returns_int(self, isolate_chroma):
        mgr = _make_manager(isolate_chroma, use_persistent=True)
        # Ajouter un document ancien pour vérifier la purge
        old_ts = str(time.time() - 200 * 86400)  # 200 jours
        mgr.add_documents(
            documents=["vieux souvenir obsolète pour test de purge"],
            metadatas=[{"timestamp": old_ts, "source": "test"}],
            ids=["old-doc-1"],
            collection_name="collective_wisdom",
        )
        result = await mgr.async_purge_expired(max_age_days=90)
        assert isinstance(result, int)
        assert result >= 1

    @pytest.mark.asyncio
    async def test_async_purge_expired_matches_sync(self, isolate_chroma):
        """async_purge_expired retourne le même résultat que purge_expired."""
        mgr = _make_manager(isolate_chroma, use_persistent=True)
        # Pas de documents anciens → les deux retournent 0
        sync_result = mgr.purge_expired(max_age_days=90)
        async_result = await mgr.async_purge_expired(max_age_days=90)
        assert sync_result == async_result == 0

    @pytest.mark.asyncio
    async def test_async_purge_low_quality_returns_int(self, isolate_chroma):
        mgr = _make_manager(isolate_chroma, use_persistent=True)
        # Ajouter un document trop court
        mgr.add_documents(
            documents=["court"],
            metadatas=[{"timestamp": str(time.time()), "source": "test"}],
            ids=["short-doc-1"],
            collection_name="collective_wisdom",
        )
        result = await mgr.async_purge_low_quality(min_length=100)
        assert isinstance(result, int)
        assert result >= 1

    @pytest.mark.asyncio
    async def test_async_check_health_returns_dict(self, isolate_chroma):
        mgr = _make_manager(isolate_chroma, use_persistent=True)
        result = await mgr.async_check_health()
        assert isinstance(result, dict)
        assert "status" in result
        assert "persistent" in result
        assert "probe_ok" in result
        assert "collections" in result
        assert result["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_concurrent_async_purges_no_error(self, isolate_chroma):
        """Deux purges concurrentes ne causent pas d'erreur grâce au lock."""
        mgr = _make_manager(isolate_chroma, use_persistent=True)
        # Lancer les deux en parallèle
        results = await asyncio.gather(
            mgr.async_purge_expired(max_age_days=90),
            mgr.async_purge_low_quality(min_length=100),
            mgr.async_check_health(),
        )
        assert isinstance(results[0], int)
        assert isinstance(results[1], int)
        assert isinstance(results[2], dict)

    @pytest.mark.asyncio
    async def test_system_health_check_uses_async(self, isolate_chroma):
        """SystemHealthCheck.run() utilise async_check_health (vérifie l'intégration)."""
        mgr = _make_manager(isolate_chroma, use_persistent=True)

        import psutil as _psutil
        import httpx as _httpx

        with patch.object(_psutil, "cpu_percent", return_value=30.0), \
             patch.object(_psutil, "virtual_memory") as mock_vm, \
             patch.object(_httpx, "AsyncClient") as mock_httpx_client, \
             patch.object(mgr, "async_check_health", wraps=mgr.async_check_health) as spy:
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

            # Vérifier que async_check_health a bien été appelé
            spy.assert_awaited_once()
            assert result["memory"]["status"] == "healthy"


# ═══════════════════════════════════════════════════════════
# TestRecordRecall — Phase 2.1 Mémoire utilitaire
# ═══════════════════════════════════════════════════════════

class TestRecordRecall:
    """Tests record_recall() et protection purge."""

    def test_record_recall_increments_count(self, isolate_chroma):
        """record_recall incrémente recall_count de 0 à 1."""
        mgr = _make_manager(isolate_chroma)
        mgr.add_documents(
            ["test doc"], [{"timestamp": str(time.time()), "source": "test"}],
            ["recall-test-1"], "collective_wisdom"
        )
        mgr.record_recall("recall-test-1", "collective_wisdom")
        col = mgr._get_collection("collective_wisdom")
        result = col.get(ids=["recall-test-1"], include=["metadatas"])
        assert int(result["metadatas"][0].get("recall_count", 0)) == 1

    def test_record_recall_multiple_increments(self, isolate_chroma):
        """3 rappels → recall_count = 3."""
        mgr = _make_manager(isolate_chroma)
        mgr.add_documents(
            ["test doc"], [{"timestamp": str(time.time()), "source": "test"}],
            ["recall-test-2"], "collective_wisdom"
        )
        for _ in range(3):
            mgr.record_recall("recall-test-2", "collective_wisdom")
        col = mgr._get_collection("collective_wisdom")
        result = col.get(ids=["recall-test-2"], include=["metadatas"])
        assert int(result["metadatas"][0].get("recall_count", 0)) == 3

    def test_record_recall_sets_last_recalled_at(self, isolate_chroma):
        """record_recall met à jour last_recalled_at."""
        mgr = _make_manager(isolate_chroma)
        mgr.add_documents(
            ["test doc"], [{"timestamp": str(time.time()), "source": "test"}],
            ["recall-test-3"], "collective_wisdom"
        )
        mgr.record_recall("recall-test-3", "collective_wisdom")
        col = mgr._get_collection("collective_wisdom")
        result = col.get(ids=["recall-test-3"], include=["metadatas"])
        assert "last_recalled_at" in result["metadatas"][0]

    def test_record_recall_nonexistent_id(self, isolate_chroma):
        """record_recall sur un ID inexistant ne plante pas."""
        mgr = _make_manager(isolate_chroma)
        mgr.record_recall("nonexistent-id", "collective_wisdom")

    def test_purge_preserves_high_recall(self, isolate_chroma):
        """Purge ne supprime pas les docs avec recall_count >= 3."""
        mgr = _make_manager(isolate_chroma)
        old_ts = str(time.time() - 200 * 86400)  # 200 jours
        mgr.add_documents(
            ["precious memory"], [{"timestamp": old_ts, "source": "test", "recall_count": "5"}],
            ["high-recall-1"], "collective_wisdom"
        )
        purged = mgr.purge_expired(max_age_days=90)
        assert purged == 0
        col = mgr._get_collection("collective_wisdom")
        remaining = col.get(ids=["high-recall-1"])
        assert len(remaining["ids"]) == 1

    def test_purge_removes_low_recall(self, isolate_chroma):
        """Purge supprime les docs anciens avec recall_count < 3."""
        mgr = _make_manager(isolate_chroma)
        old_ts = str(time.time() - 200 * 86400)  # 200 jours
        mgr.add_documents(
            ["forgettable memory"], [{"timestamp": old_ts, "source": "test", "recall_count": "1"}],
            ["low-recall-1"], "collective_wisdom"
        )
        purged = mgr.purge_expired(max_age_days=90)
        assert purged == 1
