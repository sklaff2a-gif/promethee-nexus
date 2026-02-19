import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

fastapi = pytest.importorskip("fastapi")
httpx_mod = pytest.importorskip("httpx")

from httpx import AsyncClient, ASGITransport


@pytest.fixture
def mock_lifespan():
    """Remplace le lifespan pour éviter le chargement réel des agents."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def noop_lifespan(app):
        yield

    return noop_lifespan


@pytest.fixture
def app(mock_lifespan):
    """Crée une app FastAPI avec lifespan neutralisé."""
    import main
    original_lifespan = main.app.router.lifespan_context
    main.app.router.lifespan_context = mock_lifespan
    yield main.app
    main.app.router.lifespan_context = original_lifespan


@pytest.fixture
def reset_orchestrator():
    """Remet l'orchestrateur dans un état propre."""
    from core.orchestrator import orchestrator
    original_agents = orchestrator.agents.copy()
    original_ks = orchestrator.kill_switch_active
    orchestrator.agents.clear()
    orchestrator.kill_switch_active = False
    yield orchestrator
    orchestrator.agents = original_agents
    orchestrator.kill_switch_active = original_ks


class TestHealthEndpoint:

    @pytest.mark.asyncio
    async def test_health_returns_200(self, app, reset_orchestrator):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health_status_ok(self, app, reset_orchestrator):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
        data = resp.json()
        assert data["status"] == "ok"
        assert data["kill_switch"] is False
        assert "version" in data
        assert isinstance(data["version"], str)
        assert len(data["version"]) > 0

    @pytest.mark.asyncio
    async def test_health_status_degraded_when_kill_switch(self, app, reset_orchestrator):
        reset_orchestrator.kill_switch_active = True
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["kill_switch"] is True

    @pytest.mark.asyncio
    async def test_health_lists_agents(self, app, reset_orchestrator):
        reset_orchestrator.agents["coder"] = MagicMock()
        reset_orchestrator.agents["strategist"] = MagicMock()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
        data = resp.json()
        assert set(data["agents"]) == {"coder", "strategist"}
        assert data["agents_count"] == 2

    @pytest.mark.asyncio
    async def test_health_has_uptime(self, app, reset_orchestrator):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
        data = resp.json()
        assert "uptime_seconds" in data
        assert isinstance(data["uptime_seconds"], float)
        assert data["uptime_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_health_no_auth_required(self, app, reset_orchestrator):
        """Le health check ne doit PAS exiger de token Bearer."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health_memory_field(self, app, reset_orchestrator):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
        data = resp.json()
        assert "memory_available" in data
        assert isinstance(data["memory_available"], bool)


# ─── Helpers pour mocker httpx dans /ready ───

def _mock_ollama_response(status_code=200, models=None):
    """Crée un mock de réponse httpx pour Ollama /api/tags."""
    resp = MagicMock()
    resp.status_code = status_code
    if models is None:
        models = [{"name": "gemma3:12b"}, {"name": "deepseek-r1:8b"}]
    resp.json.return_value = {"models": models}
    return resp


def _mock_chromadb_instance(project_id="default", doc_count=42):
    """Crée un mock de ChromaMemoryManager avec une collection fonctionnelle."""
    col = MagicMock()
    col.count.return_value = doc_count
    mgr = MagicMock()
    mgr.project_id = project_id
    mgr._get_collection.return_value = col
    # Mock check_health() to return a proper dict
    mgr.check_health.return_value = {
        "status": "healthy",
        "persistent": True,
        "collections": {project_id: {"doc_count": doc_count}},
        "probe_ok": True,
        "warnings": [],
    }
    return mgr


class TestReadyEndpoint:

    @pytest.mark.asyncio
    async def test_ready_all_ok(self, app, reset_orchestrator):
        """Ollama + ChromaDB OK → 200, ready=true."""
        mock_mgr = _mock_chromadb_instance(doc_count=10)

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=_mock_ollama_response())
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("main.httpx.AsyncClient", return_value=mock_client_instance), \
             patch.dict("core.vector_store.ChromaMemoryManager._instances", {"default": mock_mgr}, clear=True):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/ready")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is True
        assert data["checks"]["ollama"]["status"] == "ok"
        assert data["checks"]["ollama"]["models_loaded"] == 2
        assert data["checks"]["chromadb"]["status"] == "ok"
        # chromadb response contains collections dict not a direct documents count
        # Just verify the status is ok

    @pytest.mark.asyncio
    async def test_ready_ollama_down(self, app, reset_orchestrator):
        """Ollama injoignable → 503, ready=false, ollama en erreur."""
        import httpx as httpx_lib
        mock_mgr = _mock_chromadb_instance()

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(side_effect=httpx_lib.ConnectError("refused"))
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("main.httpx.AsyncClient", return_value=mock_client_instance), \
             patch.dict("core.vector_store.ChromaMemoryManager._instances", {"default": mock_mgr}, clear=True):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/ready")

        assert resp.status_code == 503
        data = resp.json()
        assert data["ready"] is False
        assert data["checks"]["ollama"]["status"] == "error"
        assert "connection_refused" in data["checks"]["ollama"]["detail"]
        # ChromaDB toujours ok
        assert data["checks"]["chromadb"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_ready_chromadb_no_instance(self, app, reset_orchestrator):
        """ChromaDB pas initialisé → 503, ready=false."""
        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=_mock_ollama_response())
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("main.httpx.AsyncClient", return_value=mock_client_instance), \
             patch.dict("core.vector_store.ChromaMemoryManager._instances", {}, clear=True):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/ready")

        assert resp.status_code == 503
        data = resp.json()
        assert data["ready"] is False
        assert data["checks"]["chromadb"]["status"] == "error"
        assert data["checks"]["chromadb"]["detail"] == "no_instance"

    @pytest.mark.asyncio
    async def test_ready_both_down(self, app, reset_orchestrator):
        """Tout est down → 503, les deux checks en erreur."""
        import httpx as httpx_lib

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(side_effect=httpx_lib.ConnectError("refused"))
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("main.httpx.AsyncClient", return_value=mock_client_instance), \
             patch.dict("core.vector_store.ChromaMemoryManager._instances", {}, clear=True):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/ready")

        assert resp.status_code == 503
        data = resp.json()
        assert data["ready"] is False
        assert data["checks"]["ollama"]["status"] == "error"
        assert data["checks"]["chromadb"]["status"] == "error"

    @pytest.mark.asyncio
    async def test_ready_no_auth_required(self, app, reset_orchestrator):
        """Le readiness check ne doit PAS exiger de token."""
        import httpx as httpx_lib

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(side_effect=httpx_lib.ConnectError("refused"))
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("main.httpx.AsyncClient", return_value=mock_client_instance), \
             patch.dict("core.vector_store.ChromaMemoryManager._instances", {}, clear=True):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/ready")

        # Pas de 401/403, même si on n'envoie pas de Bearer
        assert resp.status_code in (200, 503)

    @pytest.mark.asyncio
    async def test_ready_ollama_http_error(self, app, reset_orchestrator):
        """Ollama répond mais avec un code HTTP inattendu → erreur avec détail."""
        mock_mgr = _mock_chromadb_instance()

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=_mock_ollama_response(status_code=500))
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("main.httpx.AsyncClient", return_value=mock_client_instance), \
             patch.dict("core.vector_store.ChromaMemoryManager._instances", {"default": mock_mgr}, clear=True):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/ready")

        assert resp.status_code == 503
        data = resp.json()
        assert data["checks"]["ollama"]["status"] == "error"
        assert "500" in data["checks"]["ollama"]["detail"]


# ─── Rate Limiting ───

class TestRateLimiter:
    """Tests unitaires du RateLimiter (sans HTTP)."""

    def test_allows_under_limit(self):
        from main import RateLimiter
        rl = RateLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            allowed, _ = rl.check("1.2.3.4")
            assert allowed is True

    def test_blocks_over_limit(self):
        from main import RateLimiter
        rl = RateLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            rl.check("1.2.3.4")
        allowed, retry_after = rl.check("1.2.3.4")
        assert allowed is False
        assert retry_after > 0

    def test_different_ips_independent(self):
        from main import RateLimiter
        rl = RateLimiter(max_requests=1, window_seconds=60)
        allowed_a, _ = rl.check("10.0.0.1")
        allowed_b, _ = rl.check("10.0.0.2")
        assert allowed_a is True
        assert allowed_b is True
        # a est maintenant bloqué, b encore ok pour sa prochaine
        blocked_a, _ = rl.check("10.0.0.1")
        assert blocked_a is False

    def test_window_expires(self):
        from main import RateLimiter
        rl = RateLimiter(max_requests=1, window_seconds=1)
        rl.check("5.5.5.5")
        # Simuler l'expiration en antidatant les hits
        rl._hits["5.5.5.5"] = [time.time() - 2]
        allowed, _ = rl.check("5.5.5.5")
        assert allowed is True

    def test_retry_after_is_positive(self):
        from main import RateLimiter
        rl = RateLimiter(max_requests=1, window_seconds=30)
        rl.check("9.9.9.9")
        _, retry_after = rl.check("9.9.9.9")
        assert 0 < retry_after <= 31

    def test_cleanup_stale_ips(self):
        from main import RateLimiter
        rl = RateLimiter(max_requests=5, window_seconds=1)
        # Remplir > 1000 IPs stales pour déclencher le cleanup
        old = time.time() - 10
        for i in range(1010):
            rl._hits[f"10.0.{i // 256}.{i % 256}"] = [old]
        # Le prochain check déclenche _cleanup
        rl.check("fresh.ip")
        assert len(rl._hits) < 1010


class TestRateLimitEndpoint:
    """Test d'intégration : /api/mission retourne 429 après flood."""

    @pytest.mark.asyncio
    async def test_mission_returns_429_after_limit(self, app, reset_orchestrator):
        import main
        # Rate limiter très strict pour le test
        original_rl = main._rate_limiter
        main._rate_limiter = main.RateLimiter(max_requests=2, window_seconds=60)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                with patch("main.orchestrator.dispatch_task", new_callable=AsyncMock,
                           return_value={"status": "success", "result": "ok"}), \
                     patch("main.RouterAgent.classify_intent", new_callable=AsyncMock,
                           return_value="strategist"):
                    # Les 2 premières passent
                    for _ in range(2):
                        await client.post("/api/mission", json={"mission": "test"})
                    # La 3ème doit être bloquée par le rate limiter
                    resp = await client.post("/api/mission", json={"mission": "test"})
            assert resp.status_code == 429
            assert "Retry-After" in resp.headers
        finally:
            main._rate_limiter = original_rl

    @pytest.mark.asyncio
    async def test_health_not_rate_limited(self, app, reset_orchestrator):
        """/health ne doit PAS être affecté par le rate limiter."""
        import main
        original_rl = main._rate_limiter
        main._rate_limiter = main.RateLimiter(max_requests=1, window_seconds=60)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                with patch("main.orchestrator.dispatch_task", new_callable=AsyncMock,
                           return_value={"status": "success", "result": "ok"}), \
                     patch("main.RouterAgent.classify_intent", new_callable=AsyncMock,
                           return_value="strategist"):
                    # Épuiser la limite via /api/mission
                    await client.post("/api/mission", json={"mission": "x"})
                # /health doit toujours répondre 200
                resp = await client.get("/health")
            assert resp.status_code == 200
        finally:
            main._rate_limiter = original_rl
