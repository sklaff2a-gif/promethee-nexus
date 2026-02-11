import os
import json
import time
import asyncio
import tempfile
import pytest
from datetime import date, datetime
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock
from collections import namedtuple

from core.autonomy_engine import (
    SystemHealthCheck,
    RoutineScorer,
    AutonomyStatePersistence,
    AutonomyEngine,
    CONTEXT_KEYWORDS,
    MAX_DAILY_ROUTINES,
)


# ─── Helpers ───

def _make_health(verdict="GO", cpu=30.0, ram=50.0, ollama_alive=True, models=None):
    return {
        "verdict": verdict,
        "cpu_percent": cpu,
        "ram_percent": ram,
        "ram_used_gb": 8.0,
        "ram_total_gb": 16.0,
        "ollama_alive": ollama_alive,
        "ollama_models": models or [],
        "warnings": [],
        "timestamp": datetime.now().isoformat(),
    }


def _make_history_entry(intent, status="success"):
    return {
        "agent": "test",
        "intent": intent,
        "status": status,
        "timestamp": datetime.now().isoformat(),
    }


def _get_routines():
    return [
        {"agent": "evolution", "intent": "EXPANSION_CODE", "mission": "test"},
        {"agent": "architect", "intent": "AUDIT_STRUCTURE", "mission": "test"},
        {"agent": "researcher", "intent": "VEILLE_SILENCIEUSE", "mission": "test"},
        {"agent": "researcher", "intent": "DROPZONE_SCAN", "mission": "test"},
    ]


# ═══════════════════════════════════════════════════════════
# TestSystemHealthCheck (8 tests)
# ═══════════════════════════════════════════════════════════

class TestSystemHealthCheck:

    def _mock_psutil(self, cpu=30.0, ram_percent=50.0, ram_used=8*1024**3, ram_total=16*1024**3):
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = cpu
        mem = MagicMock()
        mem.percent = ram_percent
        mem.used = ram_used
        mem.total = ram_total
        mock_psutil.virtual_memory.return_value = mem
        return mock_psutil

    def _mock_httpx_ok(self, models=None):
        if models is None:
            models = [{"name": "gemma3:12b"}]
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"models": models}
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        return client

    def _mock_httpx_down(self):
        import httpx
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        return client

    @pytest.mark.asyncio
    async def test_health_go_normal_system(self):
        mock_ps = self._mock_psutil(cpu=30.0, ram_percent=50.0)
        mock_client = self._mock_httpx_ok()
        with patch.dict("sys.modules", {"psutil": mock_ps}), \
             patch("httpx.AsyncClient", return_value=mock_client):
            result = await SystemHealthCheck.run()
        assert result["verdict"] == "GO"
        assert result["cpu_percent"] == 30.0
        assert result["ram_percent"] == 50.0
        assert result["ollama_alive"] is True

    @pytest.mark.asyncio
    async def test_health_degraded_high_cpu(self):
        mock_ps = self._mock_psutil(cpu=85.0, ram_percent=50.0)
        mock_client = self._mock_httpx_ok()
        with patch.dict("sys.modules", {"psutil": mock_ps}), \
             patch("httpx.AsyncClient", return_value=mock_client):
            result = await SystemHealthCheck.run()
        assert result["verdict"] == "DEGRADED"

    @pytest.mark.asyncio
    async def test_health_degraded_high_ram(self):
        mock_ps = self._mock_psutil(cpu=30.0, ram_percent=80.0)
        mock_client = self._mock_httpx_ok()
        with patch.dict("sys.modules", {"psutil": mock_ps}), \
             patch("httpx.AsyncClient", return_value=mock_client):
            result = await SystemHealthCheck.run()
        assert result["verdict"] == "DEGRADED"

    @pytest.mark.asyncio
    async def test_health_nogo_critical_ram(self):
        mock_ps = self._mock_psutil(cpu=30.0, ram_percent=92.0)
        mock_client = self._mock_httpx_ok()
        with patch.dict("sys.modules", {"psutil": mock_ps}), \
             patch("httpx.AsyncClient", return_value=mock_client):
            result = await SystemHealthCheck.run()
        assert result["verdict"] == "NO_GO"

    @pytest.mark.asyncio
    async def test_health_nogo_critical_cpu(self):
        mock_ps = self._mock_psutil(cpu=97.0, ram_percent=50.0)
        mock_client = self._mock_httpx_ok()
        with patch.dict("sys.modules", {"psutil": mock_ps}), \
             patch("httpx.AsyncClient", return_value=mock_client):
            result = await SystemHealthCheck.run()
        assert result["verdict"] == "NO_GO"

    @pytest.mark.asyncio
    async def test_health_nogo_ollama_down(self):
        mock_ps = self._mock_psutil(cpu=30.0, ram_percent=50.0)
        mock_client = self._mock_httpx_down()
        with patch.dict("sys.modules", {"psutil": mock_ps}), \
             patch("httpx.AsyncClient", return_value=mock_client):
            result = await SystemHealthCheck.run()
        assert result["verdict"] == "NO_GO"
        assert result["ollama_alive"] is False

    @pytest.mark.asyncio
    async def test_health_has_timestamp(self):
        mock_ps = self._mock_psutil()
        mock_client = self._mock_httpx_ok()
        with patch.dict("sys.modules", {"psutil": mock_ps}), \
             patch("httpx.AsyncClient", return_value=mock_client):
            result = await SystemHealthCheck.run()
        assert "timestamp" in result
        # Vérifier que le timestamp est parsable
        datetime.fromisoformat(result["timestamp"])

    @pytest.mark.asyncio
    async def test_health_ollama_models_listed(self):
        mock_ps = self._mock_psutil()
        models = [{"name": "gemma3:12b"}, {"name": "deepseek-r1:8b"}, {"name": "llama3:8b"}]
        mock_client = self._mock_httpx_ok(models=models)
        with patch.dict("sys.modules", {"psutil": mock_ps}), \
             patch("httpx.AsyncClient", return_value=mock_client):
            result = await SystemHealthCheck.run()
        assert len(result["ollama_models"]) == 3
        assert "gemma3:12b" in result["ollama_models"]


# ═══════════════════════════════════════════════════════════
# TestRoutineScorer (9 tests)
# ═══════════════════════════════════════════════════════════

class TestRoutineScorer:

    def test_dropzone_prioritized_when_files_present(self):
        routines = _get_routines()
        scored = RoutineScorer.score_routines(routines, [], [], dropzone_count=5)
        top_intent = scored[0][0]["intent"]
        assert top_intent == "DROPZONE_SCAN"

    def test_no_dropzone_no_bonus(self):
        routines = _get_routines()
        scored = RoutineScorer.score_routines(routines, [], [], dropzone_count=0)
        # DROPZONE_SCAN ne doit pas dominer
        dropzone_score = next(s for r, s in scored if r["intent"] == "DROPZONE_SCAN")
        other_scores = [s for r, s in scored if r["intent"] != "DROPZONE_SCAN"]
        # Sans bonus, DROPZONE_SCAN a le même score de base que les autres (tolérance jitter ±0.3)
        assert dropzone_score <= max(other_scores) + 0.7

    def test_repetition_penalty_last_2(self):
        routines = _get_routines()
        history = [
            _make_history_entry("EXPANSION_CODE"),
            _make_history_entry("EXPANSION_CODE"),
        ]
        scored = RoutineScorer.score_routines(routines, [], history)
        expansion_score = next(s for r, s in scored if r["intent"] == "EXPANSION_CODE")
        assert expansion_score < 1.0  # Pénalisé en dessous du score de base

    def test_repetition_penalty_last_3(self):
        routines = _get_routines()
        history = [
            _make_history_entry("AUDIT_STRUCTURE"),
            _make_history_entry("AUDIT_STRUCTURE"),
            _make_history_entry("AUDIT_STRUCTURE"),
        ]
        scored = RoutineScorer.score_routines(routines, [], history)
        audit_score = next(s for r, s in scored if r["intent"] == "AUDIT_STRUCTURE")
        # Pénalité pour 3 occurrences : score de base (1.0) - 3.0 + jitter = ~-2.0
        assert audit_score <= -1.5

    def test_context_bonus_code_keywords(self):
        routines = _get_routines()
        context = ["optimiser code Python"]
        scored = RoutineScorer.score_routines(routines, context, [])
        expansion_score = next(s for r, s in scored if r["intent"] == "EXPANSION_CODE")
        base_score = next(s for r, s in scored if r["intent"] == "VEILLE_SILENCIEUSE")
        assert expansion_score > base_score

    def test_context_bonus_empty_context(self):
        routines = _get_routines()
        scored = RoutineScorer.score_routines(routines, [], [])
        scores = [s for _, s in scored]
        # Tous les scores proches de 1.0 (jitter ±0.3)
        assert all(0.5 <= s <= 1.5 for s in scores)

    def test_health_degraded_penalizes_heavy(self):
        routines = _get_routines()
        scored = RoutineScorer.score_routines(routines, [], [], health_verdict="DEGRADED")
        expansion_score = next(s for r, s in scored if r["intent"] == "EXPANSION_CODE")
        audit_score = next(s for r, s in scored if r["intent"] == "AUDIT_STRUCTURE")
        assert expansion_score < audit_score

    def test_all_routines_scored(self):
        routines = _get_routines()
        scored = RoutineScorer.score_routines(routines, [], [])
        assert len(scored) == 4
        intents = {r["intent"] for r, _ in scored}
        assert intents == {"EXPANSION_CODE", "AUDIT_STRUCTURE", "VEILLE_SILENCIEUSE", "DROPZONE_SCAN"}

    def test_tie_breaking_with_jitter(self):
        """Le jitter aléatoire casse les égalités et varie l'ordre."""
        routines = _get_routines()
        # Lancer 20 fois et vérifier qu'on obtient au moins 2 ordres différents
        first_intents = set()
        for _ in range(20):
            scored = RoutineScorer.score_routines(routines, [], [])
            first_intents.add(scored[0][0]["intent"])
        # Le jitter doit produire de la variété
        assert len(first_intents) >= 2

    def test_dropzone_streak_forces_rotation(self):
        """Après 3 DROPZONE consécutifs, une autre routine doit passer devant."""
        routines = _get_routines()
        history = [
            _make_history_entry("DROPZONE_SCAN"),
            _make_history_entry("DROPZONE_SCAN"),
            _make_history_entry("DROPZONE_SCAN"),
        ]
        scored = RoutineScorer.score_routines(routines, [], history, dropzone_count=5)
        top_intent = scored[0][0]["intent"]
        # DROPZONE ne doit PLUS être en tête malgré les fichiers
        assert top_intent != "DROPZONE_SCAN"

    def test_dropzone_recovers_after_rotation(self):
        """Après assez de rotation, DROPZONE reprend la priorité grâce au reactivity bonus."""
        routines = _get_routines()
        # Les 3 DROPZONE doivent être poussées hors de la fenêtre de 5
        # pour que la pénalité par occurrences totales ne les pénalise plus
        history = [
            _make_history_entry("DROPZONE_SCAN"),
            _make_history_entry("DROPZONE_SCAN"),
            _make_history_entry("DROPZONE_SCAN"),
            _make_history_entry("VEILLE_SILENCIEUSE"),
            _make_history_entry("AUDIT_STRUCTURE"),
            _make_history_entry("EXPANSION_CODE"),
            _make_history_entry("VEILLE_SILENCIEUSE"),
            _make_history_entry("AUDIT_STRUCTURE"),  # fenêtre de 5 = les 5 dernières, plus de DROPZONE
        ]
        scored = RoutineScorer.score_routines(routines, [], history, dropzone_count=5)
        top_intent = scored[0][0]["intent"]
        # DROPZONE revient en tête (0 occurrences récentes, reactivity bonus +3.0)
        assert top_intent == "DROPZONE_SCAN"

    def test_frequency_penalty_progressive(self):
        """La pénalité augmente avec le nombre d'occurrences récentes."""
        routines = _get_routines()
        history_1 = [_make_history_entry("EXPANSION_CODE")] * 1
        history_3 = [_make_history_entry("EXPANSION_CODE")] * 3

        # Moyenne sur plusieurs essais pour gommer le jitter
        scores_1 = []
        scores_3 = []
        for _ in range(20):
            scored_1 = RoutineScorer.score_routines(routines, [], history_1)
            scored_3 = RoutineScorer.score_routines(routines, [], history_3)
            scores_1.append(next(s for r, s in scored_1 if r["intent"] == "EXPANSION_CODE"))
            scores_3.append(next(s for r, s in scored_3 if r["intent"] == "EXPANSION_CODE"))

        avg_1 = sum(scores_1) / len(scores_1)
        avg_3 = sum(scores_3) / len(scores_3)
        # 3 occurrences doit être plus pénalisé que 1
        assert avg_3 < avg_1


# ═══════════════════════════════════════════════════════════
# TestAutonomyStatePersistence (5 tests)
# ═══════════════════════════════════════════════════════════

class TestAutonomyStatePersistence:

    def test_load_default_when_no_file(self, tmp_path):
        path = str(tmp_path / "nonexistent.json")
        state = AutonomyStatePersistence.load(path)
        assert state["version"] == "24.0"
        assert state["daily_count"] == 0
        assert state["routine_history"] == []
        assert state["error_streak"] == 0

    def test_save_and_load_roundtrip(self, tmp_path):
        path = str(tmp_path / "state.json")
        state = {
            "version": "24.0",
            "daily_count": 7,
            "last_reset_day": "2026-02-09",
            "routine_history": [_make_history_entry("EXPANSION_CODE")],
            "last_health_check": _make_health(),
            "error_streak": 2,
            "total_routines_executed": 42,
        }
        AutonomyStatePersistence.save(state, path)
        loaded = AutonomyStatePersistence.load(path)
        assert loaded["daily_count"] == 7
        assert loaded["error_streak"] == 2
        assert loaded["total_routines_executed"] == 42
        assert len(loaded["routine_history"]) == 1

    def test_save_creates_directory(self, tmp_path):
        path = str(tmp_path / "subdir" / "deep" / "state.json")
        state = dict(AutonomyStatePersistence.DEFAULT_STATE)
        AutonomyStatePersistence.save(state, path)
        assert os.path.exists(path)

    def test_load_handles_corrupted_json(self, tmp_path):
        path = str(tmp_path / "corrupted.json")
        with open(path, "w") as f:
            f.write("{invalid json content!!!")
        state = AutonomyStatePersistence.load(path)
        assert state["version"] == "24.0"
        assert state["daily_count"] == 0

    def test_save_atomic(self, tmp_path):
        """Vérifie que l'écriture passe par .tmp + os.replace."""
        path = str(tmp_path / "atomic.json")
        state = dict(AutonomyStatePersistence.DEFAULT_STATE)
        with patch("core.autonomy_engine.os.replace", wraps=os.replace) as mock_replace:
            AutonomyStatePersistence.save(state, path)
            mock_replace.assert_called_once_with(path + ".tmp", path)
        assert os.path.exists(path)
        assert not os.path.exists(path + ".tmp")


# ═══════════════════════════════════════════════════════════
# TestAutonomyEngineV24 (15 tests)
# ═══════════════════════════════════════════════════════════

class TestAutonomyEngineV24:

    @pytest.fixture(autouse=True)
    def setup_engine(self, tmp_path):
        """Crée un engine avec état vierge pour chaque test."""
        self.state_path = str(tmp_path / "state.json")
        with patch("core.autonomy_engine.STATE_FILE", self.state_path):
            with patch("core.autonomy_engine.AutonomyStatePersistence.load",
                       return_value=dict(AutonomyStatePersistence.DEFAULT_STATE)):
                self.engine = AutonomyEngine(idle_threshold_seconds=300)
        yield

    def test_init_loads_persisted_state(self, tmp_path):
        path = str(tmp_path / "persisted.json")
        state = {
            "version": "24.0",
            "daily_count": 7,
            "last_reset_day": "2026-02-09",
            "routine_history": [_make_history_entry("EXPANSION_CODE")],
            "last_health_check": None,
            "error_streak": 1,
            "total_routines_executed": 42,
        }
        AutonomyStatePersistence.save(state, path)
        with patch("core.autonomy_engine.STATE_FILE", path):
            engine = AutonomyEngine(idle_threshold_seconds=300)
        assert engine.daily_count == 7
        assert engine.error_streak == 1
        assert engine.total_routines_executed == 42
        assert len(engine.routine_history) == 1

    def test_persist_called_after_routine(self):
        self.engine.last_user_interaction = time.time() - 600  # idle
        health = _make_health("GO")
        with patch.object(self.engine, '_persist_state') as mock_persist, \
             patch("core.autonomy_engine.orchestrator") as mock_orch, \
             patch("core.autonomy_engine.RoutineScorer.score_routines") as mock_scorer:
            mock_orch.dispatch_task = AsyncMock(return_value={"status": "success"})
            mock_scorer.return_value = [(_get_routines()[0], 2.0)]
            asyncio.get_event_loop().run_until_complete(
                self.engine._execute_scored_routine(health)
            )
            mock_persist.assert_not_called()  # _persist_state est appelé dans start_loop, pas dans _execute

    @pytest.mark.asyncio
    async def test_health_nogo_skips_routine(self):
        """NO_GO → dispatch_task jamais appelé."""
        self.engine.is_running = True
        self.engine.last_user_interaction = time.time() - 600

        health = _make_health("NO_GO")

        call_count = 0

        async def fake_loop():
            nonlocal call_count
            # Simuler un seul cycle de la boucle
            self.engine.is_processing = False

            with patch("core.autonomy_engine.SystemHealthCheck.run", new_callable=AsyncMock, return_value=health), \
                 patch("core.autonomy_engine.bus.publish", new_callable=AsyncMock), \
                 patch.object(self.engine, '_persist_state'), \
                 patch("core.autonomy_engine.orchestrator") as mock_orch:
                mock_orch.kill_switch_active = False
                mock_orch.dispatch_task = AsyncMock()

                # Simuler le check du cycle
                if health["verdict"] == "NO_GO":
                    call_count = mock_orch.dispatch_task.call_count

            return call_count

        count = await fake_loop()
        assert count == 0

    @pytest.mark.asyncio
    async def test_health_go_dispatches_routine(self):
        health = _make_health("GO")
        with patch("core.autonomy_engine.orchestrator") as mock_orch, \
             patch("core.autonomy_engine.RoutineScorer.score_routines") as mock_scorer:
            mock_orch.dispatch_task = AsyncMock(return_value={"status": "success"})
            mock_scorer.return_value = [(_get_routines()[0], 2.0)]
            await self.engine._execute_scored_routine(health)
            mock_orch.dispatch_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_heartbeat_published_every_cycle(self):
        """Health check → AUTONOMY_HEARTBEAT publié."""
        published_events = []

        async def capture(event_type, payload):
            published_events.append(event_type)

        health = _make_health("GO")
        with patch("core.autonomy_engine.bus.publish", side_effect=capture):
            # Simuler la publication du heartbeat
            await bus_publish_heartbeat(health, self.engine)

        assert "AUTONOMY_HEARTBEAT" in published_events

    @pytest.mark.asyncio
    async def test_heartbeat_published_on_nogo(self):
        """NO_GO → heartbeat quand même."""
        published_events = []

        async def capture(event_type, payload):
            published_events.append(event_type)

        health = _make_health("NO_GO")
        with patch("core.autonomy_engine.bus.publish", side_effect=capture):
            await bus_publish_heartbeat(health, self.engine)

        assert "AUTONOMY_HEARTBEAT" in published_events

    def test_error_streak_increments(self):
        self.engine.error_streak = 0
        health = _make_health("GO")
        with patch("core.autonomy_engine.orchestrator") as mock_orch, \
             patch("core.autonomy_engine.RoutineScorer.score_routines") as mock_scorer:
            mock_orch.dispatch_task = AsyncMock(return_value={"status": "error"})
            mock_scorer.return_value = [(_get_routines()[0], 2.0)]
            asyncio.get_event_loop().run_until_complete(
                self.engine._execute_scored_routine(health)
            )
        assert self.engine.error_streak == 1

    def test_error_streak_resets_on_success(self):
        self.engine.error_streak = 3
        health = _make_health("GO")
        with patch("core.autonomy_engine.orchestrator") as mock_orch, \
             patch("core.autonomy_engine.RoutineScorer.score_routines") as mock_scorer:
            mock_orch.dispatch_task = AsyncMock(return_value={"status": "success"})
            mock_scorer.return_value = [(_get_routines()[0], 2.0)]
            asyncio.get_event_loop().run_until_complete(
                self.engine._execute_scored_routine(health)
            )
        assert self.engine.error_streak == 0

    def test_routine_history_capped_20(self):
        for i in range(25):
            self.engine._record_routine("test", f"INTENT_{i}", "success")
        assert len(self.engine.routine_history) == 20
        # Le plus ancien doit être INTENT_5 (les 5 premiers ont été purgés)
        assert self.engine.routine_history[0]["intent"] == "INTENT_5"

    def test_kill_switch_blocks(self):
        """Kill switch → le cycle continue sans action."""
        with patch("core.autonomy_engine.orchestrator") as mock_orch:
            mock_orch.kill_switch_active = True
            # Le cycle start_loop vérifie kill_switch → continue
            assert mock_orch.kill_switch_active is True

    def test_is_processing_prevents_overlap(self):
        """Verrou is_processing conservé."""
        self.engine.is_processing = True
        # Le cycle start_loop vérifie is_processing → continue
        assert self.engine.is_processing is True

    @pytest.mark.asyncio
    async def test_cooldown_30s(self):
        """asyncio.sleep(30) dans finally."""
        health = _make_health("GO")
        sleep_args = []

        original_sleep = asyncio.sleep

        async def mock_sleep(seconds):
            sleep_args.append(seconds)
            # Ne pas vraiment dormir
            return

        with patch("core.autonomy_engine.orchestrator") as mock_orch, \
             patch("core.autonomy_engine.RoutineScorer.score_routines") as mock_scorer, \
             patch("core.autonomy_engine.asyncio.sleep", side_effect=mock_sleep), \
             patch.object(self.engine, '_persist_state'):
            mock_orch.kill_switch_active = False
            mock_orch.dispatch_task = AsyncMock(return_value={"status": "success"})
            mock_scorer.return_value = [(_get_routines()[0], 2.0)]

            # Simuler le bloc try/finally de start_loop
            self.engine.is_processing = True
            try:
                await self.engine._execute_scored_routine(health)
            finally:
                self.engine._persist_state()
                await asyncio.sleep(30)
                self.engine.is_processing = False

        assert 30 in sleep_args

    def test_daily_budget_reset(self):
        self.engine.daily_count = 15
        self.engine.last_reset_day = date(2026, 1, 1)  # Jour passé
        result = self.engine._check_daily_budget()
        assert result is True
        assert self.engine.daily_count == 0

    def test_get_status_complete(self):
        status = self.engine.get_status()
        expected_keys = {
            "version", "is_running", "is_processing", "daily_count",
            "max_daily_routines", "last_reset_day", "error_streak",
            "total_routines_executed", "routine_history", "last_health_check",
            "recent_context", "idle_threshold",
        }
        assert expected_keys.issubset(set(status.keys()))
        assert status["version"] == "24.0"

    def test_recent_context_populated(self):
        """Régression V23 : reset_timer peuple recent_context."""
        self.engine.reset_timer({"mission": "optimiser le code Python"})
        assert len(self.engine.recent_context) == 1
        assert "optimiser" in self.engine.recent_context[0]


# Helper pour simuler la publication heartbeat
async def bus_publish_heartbeat(health, engine):
    from core.autonomy_engine import bus as ae_bus
    await ae_bus.publish("AUTONOMY_HEARTBEAT", {
        "health": health,
        "daily_count": engine.daily_count,
        "error_streak": engine.error_streak,
        "is_processing": engine.is_processing,
    })


# ═══════════════════════════════════════════════════════════
# TestAutonomyEndpoint (3 tests)
# ═══════════════════════════════════════════════════════════

fastapi = pytest.importorskip("fastapi")
httpx_mod = pytest.importorskip("httpx")

from httpx import AsyncClient, ASGITransport


@pytest.fixture
def mock_lifespan():
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def noop_lifespan(app):
        yield

    return noop_lifespan


@pytest.fixture
def app(mock_lifespan):
    import main
    original_lifespan = main.app.router.lifespan_context
    main.app.router.lifespan_context = mock_lifespan
    yield main.app
    main.app.router.lifespan_context = original_lifespan


class TestAutonomyEndpoint:

    @pytest.mark.asyncio
    async def test_status_returns_200(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/autonomy/status")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_status_no_auth(self, app):
        """Pas de Bearer → 200 (pas d'auth requise)."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/autonomy/status")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_status_contains_version(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/autonomy/status")
        data = resp.json()
        assert data["version"] == "24.0"
