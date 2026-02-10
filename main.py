# main.py
from fastapi import FastAPI, WebSocket, Request, BackgroundTasks, Depends, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from contextlib import asynccontextmanager
import os
import asyncio
import json
import importlib
import time
import uvicorn
import tracemalloc
import secrets
import sys
import httpx
from core.orchestrator import orchestrator
from core.event_bus.bus import bus
from core.autonomy_engine import autonomy
from core.router import RouterAgent
from core import talk_logger
from core import ci_pipeline

# --- RATE LIMITING ---
class RateLimiter:
    """Sliding window par IP. Zéro dépendance externe."""

    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}

    def _cleanup(self, now: float):
        """Purge les IPs inactives (> 2× la fenêtre)."""
        stale = [ip for ip, ts in self._hits.items() if ts and now - ts[-1] > self.window * 2]
        for ip in stale:
            del self._hits[ip]

    def check(self, ip: str) -> tuple[bool, int]:
        """Retourne (autorisé, secondes_avant_retry). Thread-safe pour asyncio single-thread."""
        now = time.time()
        cutoff = now - self.window
        hits = self._hits.get(ip, [])
        hits = [t for t in hits if t > cutoff]
        if len(hits) >= self.max_requests:
            retry_after = int(hits[0] - cutoff) + 1
            self._hits[ip] = hits
            return False, retry_after
        hits.append(now)
        self._hits[ip] = hits
        if len(self._hits) > 1000:
            self._cleanup(now)
        return True, 0

_rate_limiter = RateLimiter(
    max_requests=int(os.getenv("RATE_LIMIT_MAX", "10")),
    window_seconds=int(os.getenv("RATE_LIMIT_WINDOW", "60")),
)

async def check_rate_limit(request: Request):
    """Dépendance FastAPI : bloque si le client dépasse la limite."""
    ip = request.client.host if request.client else "unknown"
    allowed, retry_after = _rate_limiter.check(ip)
    if not allowed:
        from fastapi.responses import JSONResponse
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Max {_rate_limiter.max_requests} requests per {_rate_limiter.window}s.",
            headers={"Retry-After": str(retry_after)},
        )

# --- AUTHENTIFICATION API ---
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "")
security = HTTPBearer(auto_error=False)

async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Vérifie le token Bearer sur les endpoints API."""
    if not API_SECRET_KEY:
        return  # Pas de clé configurée = mode ouvert (dev local)
    if not credentials:
        raise HTTPException(status_code=401, detail="Token requis")
    if not secrets.compare_digest(credentials.credentials, API_SECRET_KEY):
        raise HTTPException(status_code=401, detail="Token invalide")

def verify_ws_token(token: str) -> bool:
    """Vérifie le token pour les connexions WebSocket."""
    if not API_SECRET_KEY:
        return True
    return secrets.compare_digest(token, API_SECRET_KEY)

# Configuration nettoyée (Agents financiers retirés)
AGENTS_CONFIG = [
    ("strategist", "DivineStrategist", "strategist_agent"),
    ("coder", "DivineCoder", "coder_agent"),
    ("architect", "DivineArchitect", "architect_agent"),
    ("factory", "DivineFactory", "factory_agent"), 
    ("evolution", "DivineEvolution", "evolution_agent"), 
    ("infra", "DivineInfra", "infra_agent"),
    ("security", "DivineSecurity", "security_agent"),
    ("writer", "DivineWriter", "writer_agent"),
    ("researcher", "DivineResearcher", "researcher_agent"),
    ("formatter", "DivineFormatter", "formatter_agent"), # <--- AJOUT VITAL : L'Agent Formatter
]

@asynccontextmanager
async def lifespan(app: FastAPI):
    tracemalloc.start()
    from config import Config as _cfg
    print(f"🤖 PROMÉTHÉE V12.4 (Smart Restart) [Projet: {_cfg.PROJECT_ID}]: Chargement des modules...")
    for slug, class_name, file_name in AGENTS_CONFIG:
        try:
            module = importlib.import_module(f"Agents.{file_name}")
            AgentClass = getattr(module, class_name)
            await orchestrator.register_agent(slug, AgentClass())
            print(f"   [OK] {slug.upper()}")
        except Exception as e:
            print(f"   [ERR] {slug}: {e}")
    
    print("   🧠 Autonomie & Gouvernance : ACTIVES.")
    bus.subscribe("AGENT_TASK_DISPATCH", nervous_system_listener)

    # --- CI/CD Pipeline (remplace quality_control_listener) ---
    ci_pipeline.start()

    talk_logger.start()
    asyncio.create_task(autonomy.start_loop())
    yield
    ci_pipeline.stop()
    talk_logger.stop()
    print("🔌 Arrêt.")
    tracemalloc.stop()

async def nervous_system_listener(event: dict):
    data = event.get("data", {})
    target = data.get("target")
    payload = data.get("payload")
    if target and payload:
        print(f"⚡ [NERVOUS SYSTEM] Réflexe déclenché vers -> {target.upper()}")
        asyncio.create_task(orchestrator.dispatch_task(target, payload))

app = FastAPI(lifespan=lifespan)
_start_time = time.time()

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    if os.path.exists("static/index.html"):
        with open("static/index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>UI Loading...</h1>")

@app.get("/health")
async def health():
    from config import Config
    from core.vector_store import ChromaMemoryManager
    uptime = time.time() - _start_time
    agents = list(orchestrator.agents.keys())
    memory_ok = bool(ChromaMemoryManager._instances)
    return {
        "status": "degraded" if orchestrator.kill_switch_active else "ok",
        "version": Config.VERSION,
        "uptime_seconds": round(uptime, 1),
        "agents": agents,
        "agents_count": len(agents),
        "kill_switch": orchestrator.kill_switch_active,
        "memory_available": memory_ok,
    }

@app.get("/ready")
async def ready():
    from config import Config
    from core.vector_store import ChromaMemoryManager

    checks = {}

    # --- Ollama ---
    ollama_base = Config.OLLAMA_URL.rsplit("/", 2)[0]  # http://localhost:11434
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{ollama_base}/api/tags", timeout=3.0)
        if resp.status_code == 200:
            models = [m["name"] for m in resp.json().get("models", [])]
            checks["ollama"] = {"status": "ok", "models_loaded": len(models), "models": models}
        else:
            checks["ollama"] = {"status": "error", "detail": f"HTTP {resp.status_code}"}
    except httpx.ConnectError:
        checks["ollama"] = {"status": "error", "detail": "connection_refused"}
    except Exception as e:
        checks["ollama"] = {"status": "error", "detail": str(e)}

    # --- ChromaDB ---
    try:
        instances = ChromaMemoryManager._instances
        if not instances:
            checks["chromadb"] = {"status": "error", "detail": "no_instance"}
        else:
            mgr = next(iter(instances.values()))
            col = mgr._get_collection("collective_wisdom")
            doc_count = col.count()
            checks["chromadb"] = {"status": "ok", "project": mgr.project_id, "documents": doc_count}
    except Exception as e:
        checks["chromadb"] = {"status": "error", "detail": str(e)}

    all_ok = all(c["status"] == "ok" for c in checks.values())
    status_code = 200 if all_ok else 503

    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=status_code,
        content={
            "ready": all_ok,
            "checks": checks,
        },
    )

@app.get("/api/dropzone/status")
async def dropzone_status():
    """Retourne l'état de la dropzone (fichiers en attente)."""
    from core.capabilities.dropzone_indexer import DropzoneIndexer
    indexer = DropzoneIndexer()
    pending = indexer.quick_count("USER_DROPZONE")
    return {"pending_files": pending, "dropzone_path": "USER_DROPZONE/"}

@app.get("/api/autonomy/status")
async def autonomy_status():
    """Retourne l'état complet du moteur d'autonomie."""
    return autonomy.get_status()

@app.post("/api/override", dependencies=[Depends(verify_token)])
async def api_override(request: Request):
    data = await request.json()
    active = data.get("active", False)
    await orchestrator.set_kill_switch(active)
    await bus.publish("SYSTEM_OVERRIDE", {"active": active})
    return {"status": "ok", "kill_switch": active}

async def strategic_feedback_loop(agent_name: str, mission: str, result: str):
    if agent_name in ["strategist", "architect", "factory"]: return
    await bus.publish("THOUGHT_STREAM", {
        "agent": "GOUVERNANCE", 
        "content": f"Analyse de la performance de {agent_name}...", 
        "type": "info"
    })
    audit_mission = f"AUDIT_STRATEGIQUE: L'agent {agent_name} a fini une tâche. Analyse le résultat ci-joint. S'il est incomplet ou erroné, propose un correctif."
    audit_context = f"MISSION: {mission}\n\nRÉSULTAT:\n{result[:3000]}"
    
    strat_res = await orchestrator.dispatch_task("strategist", {
        "mission": audit_mission,
        "context": audit_context
    })
    
    if not strat_res or strat_res.get("status") != "success": return
    strategy_proposal = strat_res.get("result", "")

    if "R.A.S" in strategy_proposal or "OPTIMAL" in strategy_proposal: return

    validation_mission = f"VALIDATION_STRATEGIE: Le Stratège propose cette amélioration pour {agent_name}. Si valide, transforme en 'ORDRE_USINE'."
    await orchestrator.dispatch_task("architect", {
        "mission": validation_mission,
        "context": strategy_proposal
    })

@app.post("/api/mission", dependencies=[Depends(check_rate_limit), Depends(verify_token)])
async def mission(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    msn = data.get("mission", "")
    await bus.publish("USER_COMMAND", {"mission": msn})
    
    # [V13.3] Utilisation du Router dédié
    target = await RouterAgent.classify_intent(msn)

    if target == "conseil":
        from core.council import parse_council_mission
        council_text = msn.split(":", 1)[1].strip() if ":" in msn else msn
        parsed = parse_council_mission(council_text)
        if parsed:
            response = await orchestrator.dispatch_council(
                participants=parsed["participants"],
                mission=parsed["mission"]
            )
        else:
            response = await orchestrator.dispatch_task("strategist", {
                "mission": f"L'utilisateur a demandé un conseil mais la syntaxe est incorrecte. "
                           f"Syntaxe attendue: conseil: agent1, agent2 - mission. "
                           f"Sa demande: {msn}"
            })
    else:
        response = await orchestrator.dispatch_task(target, {"mission": msn})
    
    if response and response.get("status") == "success":
        result_text = str(response.get("result", ""))
        background_tasks.add_task(strategic_feedback_loop, target, msn, result_text)

    return {"status": "dispatched", "target": target}

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, token: str = Query(default="")):
    if not verify_ws_token(token):
        await websocket.close(code=1008, reason="Token invalide")
        return
    await websocket.accept()
    async def sender(event):
        try:
            await websocket.send_text(json.dumps({"type": event.get("type"), "payload": event.get("data")}))
        except Exception:
            pass  # WebSocket déconnecté, normal en fin de session
    bus.subscribe("*", sender)
    try:
        while True: await websocket.receive_text()
    except Exception:
        pass  # Client déconnecté, fin normale du WebSocket
    finally:
        bus.unsubscribe("*", sender)

if __name__ == "__main__":
    print("🔥 Démarrage via Lanceur Direct...")
    # RELOAD=FALSE OBLIGATOIRE : On gère le restart nous-mêmes via sys.exit(65)
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)