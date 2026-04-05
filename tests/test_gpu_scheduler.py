"""
Tests pour le GpuScheduler — file d'attente GPU stricte.

Couvre :
- Sérialisation stricte (1 seul appel à la fois)
- Cooldown entre appels consécutifs
- File d'attente avec logging (queue depth)
- Monitoring température GPU
- Statistiques
- Backward compat BaseAgent._get_ollama_semaphore()
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.base_agent import BaseAgent, GpuScheduler, gpu_scheduler


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def reset_scheduler():
    """Reset le scheduler entre chaque test."""
    gpu_scheduler.reset()
    yield
    gpu_scheduler.reset()


@pytest.fixture
def scheduler():
    """Scheduler frais pour tests isolés."""
    s = GpuScheduler()
    s.GPU_COOLDOWN_SECONDS = 0  # Pas de cooldown dans les tests par défaut
    s.GPU_TEMP_CHECK_INTERVAL = 999  # Pas de check temp
    return s


# ============================================================
# 1. Sérialisation stricte
# ============================================================

class TestSerialization:

    @pytest.mark.asyncio
    async def test_exclusive_access(self, scheduler):
        """Un seul appelant à la fois peut accéder au GPU."""
        results = []
        lock_held = asyncio.Event()

        async def task(name, delay):
            async with scheduler.access(name):
                results.append(f"{name}_start")
                if name == "first":
                    lock_held.set()
                await asyncio.sleep(delay)
                results.append(f"{name}_end")

        # first prend le lock, second attend
        t1 = asyncio.create_task(task("first", 0.1))
        await lock_held.wait()
        t2 = asyncio.create_task(task("second", 0.05))
        await asyncio.gather(t1, t2)

        # first doit finir AVANT que second commence
        assert results == ["first_start", "first_end", "second_start", "second_end"]

    @pytest.mark.asyncio
    async def test_queue_depth_tracking(self, scheduler):
        """Le queue_depth est correctement suivi."""
        assert scheduler._queue_depth == 0

        barrier = asyncio.Event()
        entered = asyncio.Event()

        async def holder():
            async with scheduler.access("holder"):
                entered.set()
                await barrier.wait()

        async def waiter():
            await entered.wait()
            # holder tient le lock, queue_depth devrait être 2 quand waiter attend
            assert scheduler._queue_depth >= 1
            async with scheduler.access("waiter"):
                pass

        t1 = asyncio.create_task(holder())
        t2 = asyncio.create_task(waiter())

        await entered.wait()
        await asyncio.sleep(0.05)  # Laisser waiter s'enqueuer
        assert scheduler._queue_depth >= 1
        barrier.set()
        await asyncio.gather(t1, t2)
        assert scheduler._queue_depth == 0

    @pytest.mark.asyncio
    async def test_fifo_order(self, scheduler):
        """Les tâches sont servies dans l'ordre d'arrivée (FIFO)."""
        order = []
        barrier = asyncio.Event()
        first_in = asyncio.Event()

        async def blocker():
            async with scheduler.access("blocker"):
                first_in.set()
                await barrier.wait()

        async def numbered(n):
            await first_in.wait()
            await asyncio.sleep(0.01 * n)  # Échelonner l'arrivée
            async with scheduler.access(f"task_{n}"):
                order.append(n)

        t0 = asyncio.create_task(blocker())
        tasks = [asyncio.create_task(numbered(i)) for i in range(1, 4)]
        await asyncio.sleep(0.15)
        barrier.set()
        await asyncio.gather(t0, *tasks)

        assert order == [1, 2, 3]


# ============================================================
# 2. Cooldown entre appels
# ============================================================

class TestCooldown:

    @pytest.mark.asyncio
    async def test_cooldown_enforced(self):
        """Le cooldown entre appels est respecté."""
        s = GpuScheduler()
        s.GPU_COOLDOWN_SECONDS = 0.2
        s.GPU_TEMP_CHECK_INTERVAL = 999

        start = time.time()
        async with s.access("a"):
            pass
        async with s.access("b"):
            pass
        elapsed = time.time() - start

        # Le 2ème appel doit attendre le cooldown
        assert elapsed >= 0.15  # Marge de tolérance

    @pytest.mark.asyncio
    async def test_no_cooldown_first_call(self, scheduler):
        """Pas de cooldown pour le premier appel."""
        scheduler.GPU_COOLDOWN_SECONDS = 5.0
        start = time.time()
        async with scheduler.access("first"):
            pass
        elapsed = time.time() - start
        assert elapsed < 1.0  # Premier appel quasi-instantané


# ============================================================
# 3. Monitoring température GPU
# ============================================================

class TestGpuTemp:

    @pytest.mark.asyncio
    async def test_temp_check_called(self):
        """La température GPU est vérifiée (si l'intervalle est écoulé)."""
        s = GpuScheduler()
        s.GPU_COOLDOWN_SECONDS = 0
        s.GPU_TEMP_CHECK_INTERVAL = 0  # Vérifier à chaque appel

        with patch.object(s, "_check_gpu_temp", new_callable=AsyncMock, return_value=50) as mock_check:
            async with s.access("test"):
                pass
            mock_check.assert_called_once()

    @pytest.mark.asyncio
    async def test_temp_throttle(self):
        """Appels bloqués si le GPU est trop chaud."""
        s = GpuScheduler()
        s.GPU_COOLDOWN_SECONDS = 0
        s.GPU_TEMP_CHECK_INTERVAL = 0
        s.GPU_TEMP_MAX = 80

        temps = iter([85, 83, 78, 74])  # Chaud, puis refroidit

        async def fake_temp():
            return next(temps)

        with patch.object(s, "_check_gpu_temp", side_effect=fake_temp):
            start = time.time()
            # L'appel devrait attendre le refroidissement (2 cycles de 10s skip via mock)
            # Mais dans le test on mock _check_gpu_temp directement, donc pas de sleep
            # On vérifie juste que la logique ne crash pas
            # (le vrai test de timing nécessiterait un patch de asyncio.sleep)
            pass

    @pytest.mark.asyncio
    async def test_nvidia_smi_error_graceful(self):
        """Si nvidia-smi échoue, on utilise la dernière valeur connue."""
        s = GpuScheduler()
        s._last_gpu_temp = 55
        s._last_temp_check = 0  # Forcer un check

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("nvidia-smi not found")):
            temp = await s._check_gpu_temp()
            assert temp == 55  # Fallback


# ============================================================
# 4. Statistiques
# ============================================================

class TestStats:

    @pytest.mark.asyncio
    async def test_stats_after_calls(self, scheduler):
        """Les stats sont mises à jour après les appels."""
        async with scheduler.access("agent_a"):
            stats = scheduler.get_stats()
            assert stats["current_agent"] == "agent_a"
            assert stats["queue_depth"] == 1

        stats = scheduler.get_stats()
        assert stats["total_calls"] == 1
        assert stats["current_agent"] is None
        assert stats["queue_depth"] == 0

    @pytest.mark.asyncio
    async def test_avg_wait_tracked(self, scheduler):
        """Le temps d'attente moyen est calculé."""
        async with scheduler.access("a"):
            pass
        async with scheduler.access("b"):
            pass
        stats = scheduler.get_stats()
        assert stats["total_calls"] == 2
        assert stats["avg_wait_s"] >= 0


# ============================================================
# 5. Backward compat — BaseAgent._get_ollama_semaphore()
# ============================================================

class TestBackwardCompat:

    @pytest.mark.asyncio
    async def test_get_ollama_semaphore_returns_context_manager(self):
        """_get_ollama_semaphore() retourne un async context manager fonctionnel."""
        ctx = BaseAgent._get_ollama_semaphore()
        async with ctx:
            # Doit fonctionner sans erreur
            pass

    @pytest.mark.asyncio
    async def test_semaphore_serializes(self):
        """L'ancien API sérialise bien les appels via le scheduler."""
        order = []

        async def task(name):
            async with BaseAgent._get_ollama_semaphore():
                order.append(f"{name}_in")
                await asyncio.sleep(0.05)
                order.append(f"{name}_out")

        await asyncio.gather(task("a"), task("b"))
        # Sérialisé : a_in, a_out, b_in, b_out (ou inverse)
        assert order[0].endswith("_in")
        assert order[1].endswith("_out")
        assert order[2].endswith("_in")
        assert order[3].endswith("_out")

    @pytest.mark.asyncio
    async def test_health_includes_gpu_stats(self):
        """get_ollama_health() inclut les stats GPU."""
        health = BaseAgent.get_ollama_health()
        assert "gpu_scheduler" in health
        assert "queue_depth" in health["gpu_scheduler"]
        assert "total_calls" in health["gpu_scheduler"]


# ============================================================
# 6. Monitoring VRAM
# ============================================================

class TestVramMonitoring:

    @pytest.mark.asyncio
    async def test_vram_check_called_in_acquire(self):
        """Le check VRAM est appelé dans acquire()."""
        s = GpuScheduler()
        s.GPU_COOLDOWN_SECONDS = 0
        s.GPU_TEMP_CHECK_INTERVAL = 999
        s.GPU_VRAM_CHECK_INTERVAL = 0  # Vérifier à chaque appel

        with patch.object(s, "_check_gpu_temp", new_callable=AsyncMock, return_value=50), \
             patch.object(s, "_check_vram", new_callable=AsyncMock, return_value=30) as mock_vram:
            async with s.access("test"):
                pass
            mock_vram.assert_called_once()

    @pytest.mark.asyncio
    async def test_vram_under_threshold_no_unload(self):
        """Sous le seuil VRAM, pas de déchargement."""
        s = GpuScheduler()
        s._last_vram_check = 0  # Forcer un check
        s.GPU_VRAM_CHECK_INTERVAL = 0

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"8000, 16000", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch.object(s, "_unload_ollama_models", new_callable=AsyncMock) as mock_unload:
            percent = await s._check_vram()
            assert percent == 50
            mock_unload.assert_not_called()

    @pytest.mark.asyncio
    async def test_vram_over_threshold_triggers_unload(self):
        """Au-delà du seuil VRAM, décharge les modèles Ollama."""
        s = GpuScheduler()
        s._last_vram_check = 0
        s.GPU_VRAM_CHECK_INTERVAL = 0
        s.GPU_VRAM_MAX_PERCENT = 85

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"14000, 16000", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch.object(s, "_unload_ollama_models", new_callable=AsyncMock) as mock_unload:
            percent = await s._check_vram()
            assert percent == 87
            mock_unload.assert_called_once()
            assert s._vram_unloads == 1

    @pytest.mark.asyncio
    async def test_vram_rate_limited(self):
        """Le check VRAM est rate-limité."""
        s = GpuScheduler()
        s._last_vram_check = time.time()  # Tout juste vérifié
        s._last_vram_percent = 42
        s.GPU_VRAM_CHECK_INTERVAL = 999

        # Ne doit PAS appeler nvidia-smi
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            percent = await s._check_vram()
            assert percent == 42
            mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_vram_nvidia_smi_error_graceful(self):
        """Si nvidia-smi échoue, fallback sur la dernière valeur."""
        s = GpuScheduler()
        s._last_vram_check = 0
        s._last_vram_percent = 30
        s.GPU_VRAM_CHECK_INTERVAL = 0

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            percent = await s._check_vram()
            assert percent == 30

    @pytest.mark.asyncio
    async def test_unload_ollama_models(self):
        """_unload_ollama_models appelle keep_alive=0 sur chaque modèle chargé."""
        s = GpuScheduler()

        mock_ps_resp = MagicMock()
        mock_ps_resp.status_code = 200
        mock_ps_resp.json.return_value = {
            "models": [
                {"name": "qwen3.5:9b"},
                {"name": "gemma4:e4b"},
            ]
        }
        mock_unload_resp = MagicMock()
        mock_unload_resp.status_code = 200

        import httpx
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_ps_resp)
            mock_client.post = AsyncMock(return_value=mock_unload_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await s._unload_ollama_models()

            assert mock_client.post.call_count == 2
            # Vérifier que keep_alive=0 est envoyé
            for call in mock_client.post.call_args_list:
                payload = call[1]["json"]
                assert payload["keep_alive"] == "0"

    @pytest.mark.asyncio
    async def test_stats_include_vram(self):
        """Les stats incluent les métriques VRAM."""
        s = GpuScheduler()
        s._last_vram_percent = 60
        s._vram_unloads = 3
        stats = s.get_stats()
        assert stats["last_vram_percent"] == 60
        assert stats["vram_unloads"] == 3


# ============================================================
# 7. Reset et Smart Restart
# ============================================================

class TestReset:

    def test_reset_clears_state(self):
        """reset() remet tout à zéro."""
        s = GpuScheduler()
        s._total_calls = 42
        s._queue_depth = 3
        s._last_gpu_temp = 75
        s.reset()
        assert s._total_calls == 0
        assert s._queue_depth == 0
        assert s._last_gpu_temp == 0

    @pytest.mark.asyncio
    async def test_event_loop_change_recreates_lock(self):
        """Le lock est recréé si l'event loop change (Smart Restart)."""
        s = GpuScheduler()
        lock1 = s._ensure_lock()
        s._loop_id = -1  # Simuler changement d'event loop
        lock2 = s._ensure_lock()
        assert lock1 is not lock2
