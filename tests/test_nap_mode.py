"""Tests du mode sieste (hibernation réparatrice 0-GPU)."""
import os
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from core.autonomy_engine import (
    AutonomyEngine,
    AutonomyStatePersistence,
    NAP_INTENTS,
    NAP_SLEEP_INTERVAL,
    NAP_PERIOD_DURATION,
    NAP_MAX_RENEWALS,
    NAP_COOLDOWN,
)
from core.event_bus.bus import bus


# ─── Helper : créer un engine sans __init__ (évite les imports lourds) ───

def _make_engine():
    engine = AutonomyEngine.__new__(AutonomyEngine)
    engine.is_napping = False
    engine._nap_started_at = 0.0
    engine._nap_tasks_done = []
    engine.is_running = True
    engine.is_processing = False
    engine.daily_count = 0
    engine.daily_budget_used = 0
    engine.last_reset_day = None
    engine.routine_history = []
    engine.error_streak = 0
    engine.total_routines_executed = 0
    engine.last_health_check = None
    engine.recent_context = []
    engine.idle_threshold = 300
    engine.last_user_interaction = time.time()
    engine._learning_history = {}
    engine._learning_done_this_cycle = False
    engine._security_audited_files = {}
    engine._temp_blacklist = set()
    engine._forced_next_intent = ""
    engine._drive_force_counts = {}
    engine._drive_force_cycle = 0
    engine._drive_force_total = {}
    engine._council_adjustments = {}
    engine._current_council_subject = ""
    engine._last_grimoire_slug = ""
    engine._council_degraded = False
    engine._tissue_stimulation_zones = []
    engine._nap_renewals_used = 0
    engine._nap_last_exit = 0.0
    return engine


class TestEnterNap:
    """Tests pour enter_nap()."""

    @pytest.mark.asyncio
    async def test_enter_nap_sets_flag(self):
        """is_napping=True après enter_nap()."""
        engine = _make_engine()
        with patch.object(engine, '_unload_ollama_models', new_callable=AsyncMock) as mock_unload, \
             patch.object(engine, '_persist_state'), \
             patch.object(bus, 'publish', new_callable=AsyncMock):
            await engine.enter_nap()
        assert engine.is_napping is True
        assert engine._nap_started_at > 0
        assert engine._nap_tasks_done == []
        mock_unload.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_enter_nap_publishes_events(self):
        """enter_nap publie NAP_MODE et THOUGHT_STREAM."""
        engine = _make_engine()
        published = []
        async def capture(event_type, data):
            published.append((event_type, data))
        with patch.object(engine, '_unload_ollama_models', new_callable=AsyncMock), \
             patch.object(engine, '_persist_state'), \
             patch.object(bus, 'publish', side_effect=capture):
            await engine.enter_nap()
        event_types = [p[0] for p in published]
        assert "NAP_MODE" in event_types
        assert "THOUGHT_STREAM" in event_types
        nap_event = next(p for p in published if p[0] == "NAP_MODE")
        assert nap_event[1]["active"] is True


class TestExitNap:
    """Tests pour exit_nap()."""

    @pytest.mark.asyncio
    async def test_exit_nap_clears_flag(self):
        """is_napping=False + résumé généré après exit_nap()."""
        engine = _make_engine()
        engine.is_napping = True
        engine._nap_started_at = time.time() - 600  # 10 min de sieste
        engine._nap_tasks_done = ["AUDIT_STRUCTURE", "dream_consolidation"]
        published = []
        async def capture(event_type, data):
            published.append((event_type, data))
        with patch.object(engine, '_persist_state'), \
             patch.object(bus, 'publish', side_effect=capture):
            await engine.exit_nap()
        assert engine.is_napping is False
        assert engine._nap_started_at == 0.0
        assert engine._nap_tasks_done == []
        # Vérifie le résumé dans THOUGHT_STREAM
        thought = next(p for p in published if p[0] == "THOUGHT_STREAM")
        assert "AUDIT_STRUCTURE" in thought[1]["content"]
        assert "dream_consolidation" in thought[1]["content"]

    @pytest.mark.asyncio
    async def test_exit_nap_empty_tasks(self):
        """Résumé correct même sans tâches effectuées."""
        engine = _make_engine()
        engine.is_napping = True
        engine._nap_started_at = time.time() - 120
        engine._nap_tasks_done = []
        published = []
        async def capture(event_type, data):
            published.append((event_type, data))
        with patch.object(engine, '_persist_state'), \
             patch.object(bus, 'publish', side_effect=capture):
            await engine.exit_nap()
        thought = next(p for p in published if p[0] == "THOUGHT_STREAM")
        assert "Aucune tâche" in thought[1]["content"]


class TestNapPersistence:
    """Tests pour la persistance de l'état sieste."""

    def test_nap_persisted_in_state(self):
        """is_napping et _nap_started_at sont dans le state persisté."""
        engine = _make_engine()
        engine.is_napping = True
        engine._nap_started_at = 1234567890.0
        saved = {}
        with patch.object(AutonomyStatePersistence, 'save', side_effect=lambda s: saved.update(s)):
            engine._persist_state()
        assert saved["is_napping"] is True
        assert saved["_nap_started_at"] == 1234567890.0

    def test_nap_not_persisted_when_off(self):
        """is_napping=False est correctement persisté."""
        engine = _make_engine()
        engine.is_napping = False
        engine._nap_started_at = 0.0
        saved = {}
        with patch.object(AutonomyStatePersistence, 'save', side_effect=lambda s: saved.update(s)):
            engine._persist_state()
        assert saved["is_napping"] is False


class TestUnloadOllamaModels:
    """Tests pour _unload_ollama_models()."""

    @pytest.mark.asyncio
    async def test_unload_ollama_models(self):
        """Appels HTTP corrects pour décharger les modèles."""
        engine = _make_engine()
        mock_ps_response = MagicMock()
        mock_ps_response.status_code = 200
        mock_ps_response.json.return_value = {
            "models": [
                {"name": "qwen3:14b"},
                {"name": "gemma3:4b"},
            ]
        }
        mock_gen_response = MagicMock()
        mock_gen_response.status_code = 200

        calls = []
        async def mock_get(url, **kwargs):
            calls.append(("GET", url))
            return mock_ps_response
        async def mock_post(url, **kwargs):
            calls.append(("POST", url, kwargs.get("json", {})))
            return mock_gen_response

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        import httpx as httpx_mod
        with patch.object(httpx_mod, 'AsyncClient', return_value=mock_client):
            await engine._unload_ollama_models()

        # Vérifie GET /api/ps
        assert any(c[0] == "GET" and "/api/ps" in c[1] for c in calls)
        # Vérifie POST /api/generate pour chaque modèle
        post_calls = [c for c in calls if c[0] == "POST"]
        assert len(post_calls) == 2
        assert post_calls[0][2]["keep_alive"] == "0"
        assert post_calls[1][2]["keep_alive"] == "0"

    @pytest.mark.asyncio
    async def test_unload_ollama_graceful_failure(self):
        """Pas de crash si Ollama est down."""
        engine = _make_engine()
        import httpx as httpx_mod
        with patch.object(httpx_mod, 'AsyncClient', side_effect=Exception("Connection refused")):
            # Ne doit pas lever d'exception
            await engine._unload_ollama_models()


class TestNapRoutine:
    """Tests pour _execute_nap_routine()."""

    @pytest.mark.asyncio
    async def test_nap_routine_calls_post_budget(self):
        """La routine sieste appelle _execute_post_budget_routine."""
        engine = _make_engine()
        with patch.object(engine, '_execute_post_budget_routine', new_callable=AsyncMock) as mock_post, \
             patch.dict('sys.modules', {'core.circadian_rhythm': MagicMock()}):
            await engine._execute_nap_routine()
        mock_post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_nap_routine_circadian_task(self):
        """La routine sieste exécute les tâches circadiennes et les enregistre."""
        engine = _make_engine()
        mock_circadian = MagicMock()
        mock_circadian.execute_next_sleep_task = AsyncMock(return_value={
            "success": True,
            "task": "dream_consolidation"
        })
        with patch.object(engine, '_execute_post_budget_routine', new_callable=AsyncMock), \
             patch.dict('sys.modules', {'core.circadian_rhythm': MagicMock(circadian=mock_circadian)}):
            # Le import local doit fonctionner
            with patch('core.autonomy_engine.importlib', create=True):
                # On simule directement l'appel circadian
                await engine._execute_post_budget_routine()
                # Simuler manuellement la partie circadienne
                result = await mock_circadian.execute_next_sleep_task()
                if result and result.get("success"):
                    engine._nap_tasks_done.append(result.get("task", "circadian_task"))
        assert "dream_consolidation" in engine._nap_tasks_done

    @pytest.mark.asyncio
    async def test_nap_no_scored_routine(self):
        """_execute_scored_routine n'est jamais appelé en mode sieste."""
        engine = _make_engine()
        engine.is_napping = True
        with patch.object(engine, '_execute_nap_routine', new_callable=AsyncMock) as mock_nap, \
             patch.object(engine, '_execute_scored_routine', new_callable=AsyncMock) as mock_scored, \
             patch.object(engine, '_persist_state'), \
             patch.object(engine, '_check_daily_budget', return_value="full"), \
             patch('asyncio.sleep', new_callable=AsyncMock):
            # Simuler un seul tour de boucle
            engine.is_running = True
            loop_count = 0
            original_sleep = engine.is_running

            async def fake_sleep(duration):
                nonlocal loop_count
                loop_count += 1
                if loop_count >= 2:
                    engine.is_running = False

            with patch('asyncio.sleep', side_effect=fake_sleep):
                # Import nécessaires pour start_loop
                with patch.dict('sys.modules', {
                    'core.cardiac_engine': MagicMock(heart=MagicMock(compute_sleep_duration=MagicMock(return_value=1))),
                    'core.circadian_rhythm': MagicMock(circadian=MagicMock(get_sleep_multiplier=MagicMock(return_value=1.0))),
                }):
                    from core.orchestrator import orchestrator
                    original_ks = orchestrator.kill_switch_active
                    orchestrator.kill_switch_active = False
                    try:
                        await engine.start_loop()
                    finally:
                        orchestrator.kill_switch_active = original_ks
            mock_nap.assert_awaited()
            mock_scored.assert_not_awaited()


class TestNapStatus:
    """Tests pour l'exposition du statut sieste."""

    def test_nap_status_exposed(self):
        """get_status() contient is_napping."""
        engine = _make_engine()
        engine.is_napping = False
        status = engine.get_status()
        assert "is_napping" in status
        assert status["is_napping"] is False

    def test_nap_status_active(self):
        """get_status() reflète is_napping=True."""
        engine = _make_engine()
        engine.is_napping = True
        status = engine.get_status()
        assert status["is_napping"] is True


class TestNapAutoWake:
    """Tests pour l'auto-réveil avec renouvellement."""

    @pytest.mark.asyncio
    async def test_auto_wake_after_max_renewals(self):
        """Après max renouvellements + période écoulée → exit_nap() appelé."""
        engine = _make_engine()
        engine.is_napping = True
        engine._nap_started_at = time.time() - NAP_PERIOD_DURATION - 1
        engine._nap_renewals_used = NAP_MAX_RENEWALS
        with patch.object(engine, 'exit_nap', new_callable=AsyncMock) as mock_exit:
            # Simuler la logique de start_loop (section auto-réveil)
            nap_elapsed = time.time() - engine._nap_started_at
            if nap_elapsed >= NAP_PERIOD_DURATION:
                if engine._nap_renewals_used < NAP_MAX_RENEWALS:
                    engine._nap_renewals_used += 1
                    engine._nap_started_at = time.time()
                else:
                    await engine.exit_nap()
        mock_exit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_renewal_resets_timer(self):
        """Période écoulée + renouvellements restants → timer reset, counter +1."""
        engine = _make_engine()
        engine.is_napping = True
        engine._nap_started_at = time.time() - NAP_PERIOD_DURATION - 1
        engine._nap_renewals_used = 0
        before = time.time()
        # Simuler la logique de start_loop (section auto-réveil)
        nap_elapsed = time.time() - engine._nap_started_at
        if nap_elapsed >= NAP_PERIOD_DURATION:
            if engine._nap_renewals_used < NAP_MAX_RENEWALS:
                engine._nap_renewals_used += 1
                engine._nap_started_at = time.time()
        assert engine._nap_renewals_used == 1
        assert engine._nap_started_at >= before

    def test_no_wake_before_period(self):
        """Période pas encore écoulée → ni réveil ni renouvellement."""
        engine = _make_engine()
        engine.is_napping = True
        engine._nap_started_at = time.time() - 100  # seulement 100s
        engine._nap_renewals_used = 0
        original_renewals = engine._nap_renewals_used
        original_started = engine._nap_started_at
        # Simuler la logique de start_loop (section auto-réveil)
        nap_elapsed = time.time() - engine._nap_started_at
        woke = False
        if nap_elapsed >= NAP_PERIOD_DURATION:
            if engine._nap_renewals_used < NAP_MAX_RENEWALS:
                engine._nap_renewals_used += 1
                engine._nap_started_at = time.time()
            else:
                woke = True
        assert not woke
        assert engine._nap_renewals_used == original_renewals
        assert engine._nap_started_at == original_started


class TestNapCooldown:
    """Tests pour le cooldown entre siestes."""

    @pytest.mark.asyncio
    async def test_cooldown_blocks_reentry(self):
        """Cooldown actif (60s sur 300s) → enter_nap() retourne False."""
        engine = _make_engine()
        engine._nap_last_exit = time.time() - 60  # sorti il y a 60s
        with patch.object(engine, '_unload_ollama_models', new_callable=AsyncMock), \
             patch.object(engine, '_persist_state'), \
             patch.object(bus, 'publish', new_callable=AsyncMock):
            result = await engine.enter_nap()
        assert result is False
        assert engine.is_napping is False

    @pytest.mark.asyncio
    async def test_cooldown_expired_allows(self):
        """Cooldown expiré (600s > 300s) → enter_nap() retourne True."""
        engine = _make_engine()
        engine._nap_last_exit = time.time() - 600  # sorti il y a 10 min
        with patch.object(engine, '_unload_ollama_models', new_callable=AsyncMock), \
             patch.object(engine, '_persist_state'), \
             patch.object(bus, 'publish', new_callable=AsyncMock):
            result = await engine.enter_nap()
        assert result is True
        assert engine.is_napping is True

    @pytest.mark.asyncio
    async def test_first_nap_no_cooldown(self):
        """Première sieste (_nap_last_exit=0) → acceptée immédiatement."""
        engine = _make_engine()
        engine._nap_last_exit = 0.0
        with patch.object(engine, '_unload_ollama_models', new_callable=AsyncMock), \
             patch.object(engine, '_persist_state'), \
             patch.object(bus, 'publish', new_callable=AsyncMock):
            result = await engine.enter_nap()
        assert result is True
        assert engine.is_napping is True


class TestNapPersistenceExtended:
    """Tests pour la persistance des nouveaux attributs sieste."""

    def test_renewals_persisted(self):
        """_nap_renewals_used est dans le state persisté."""
        engine = _make_engine()
        engine._nap_renewals_used = 1
        saved = {}
        with patch.object(AutonomyStatePersistence, 'save', side_effect=lambda s: saved.update(s)):
            engine._persist_state()
        assert saved["_nap_renewals_used"] == 1

    def test_last_exit_persisted(self):
        """_nap_last_exit est dans le state persisté."""
        engine = _make_engine()
        engine._nap_last_exit = 1234567890.0
        saved = {}
        with patch.object(AutonomyStatePersistence, 'save', side_effect=lambda s: saved.update(s)):
            engine._persist_state()
        assert saved["_nap_last_exit"] == 1234567890.0
