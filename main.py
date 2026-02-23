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
import logging
import logging.handlers
import time
import uvicorn
import tracemalloc
import secrets
import sys
import httpx

# --- LOGGING PERSISTANT ---
# Duplique TOUT (logging + print) vers un fichier rotatif quotidien
_LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_LOGS_DIR, exist_ok=True)

_log_formatter = logging.Formatter(
    "[%(asctime)s] [%(name)s] %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)


class _SafeTimedRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """Sous-classe qui catch PermissionError dans doRollover().
    Sur Windows, _TeeStream tient un handle sur le fichier log,
    ce qui empêche la rotation. On skip et on retente au prochain emit."""

    def doRollover(self):
        try:
            super().doRollover()
        except PermissionError:
            # Le fichier est verrouillé (probablement par _TeeStream) — on skip la rotation
            pass


# FileHandler rotatif : 1 fichier par jour, garde 14 jours
_file_handler = _SafeTimedRotatingFileHandler(
    os.path.join(_LOGS_DIR, "promethee.log"),
    when="midnight", backupCount=14, encoding="utf-8"
)
_file_handler.setFormatter(_log_formatter)
_file_handler.suffix = "%Y-%m-%d"

# Console handler (comportement existant)
_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setFormatter(_log_formatter)

# Configurer le root logger
logging.basicConfig(level=logging.INFO, handlers=[_console_handler, _file_handler])


class _TeeStream:
    """Redirige print() vers le fichier log en plus de la console."""
    def __init__(self, original, log_handler):
        self.original = original
        self.log_handler = log_handler

    def write(self, text):
        try:
            self.original.write(text)
        except UnicodeEncodeError:
            # Fallback: encode with errors='replace' for problematic characters
            self.original.write(text.encode('utf-8', errors='replace').decode('utf-8', errors='replace'))
        if text.strip():
            record = logging.LogRecord(
                name="stdout", level=logging.INFO, pathname="", lineno=0,
                msg=text.rstrip(), args=(), exc_info=None
            )
            self.log_handler.emit(record)

    def flush(self):
        self.original.flush()

    def isatty(self):
        return self.original.isatty()

    def fileno(self):
        return self.original.fileno()

sys.stdout = _TeeStream(sys.__stdout__, _file_handler)
sys.stderr = _TeeStream(sys.__stderr__, _file_handler)
from core.orchestrator import orchestrator
from core.event_bus.bus import bus
from core.autonomy_engine import autonomy
from core.router import RouterAgent
from core.psyche import psyche
from core.strategic_journal import journal as strat_journal
from core.self_awareness import awareness
from core.objectives_engine import objectives as objectives_engine, MAX_ACTIVE_OBJECTIVES
from core.desire_engine import desires
from core import talk_logger
from core import interface_logger
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

async def _on_smart_restart(data: dict):
    """Smart Restart propre : laisse les opérations en cours finir, puis exit(65)."""
    filename = data.get("filename", "?")
    logger.info(f"[SMART RESTART] Programmé suite à modification: {filename}")
    await asyncio.sleep(3)
    os._exit(65)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tracemalloc.start()
    from config import Config as _cfg
    print(f"🤖 PROMÉTHÉE {_cfg.VERSION} (Smart Restart) [Projet: {_cfg.PROJECT_ID}]: Chargement des modules...")
    for slug, class_name, file_name in AGENTS_CONFIG:
        try:
            module = importlib.import_module(f"Agents.{file_name}")
            AgentClass = getattr(module, class_name)
            await orchestrator.register_agent(slug, AgentClass())
            print(f"   [OK] {slug.upper()}")
        except Exception as e:
            print(f"   [ERR] {slug}: {e}")
    
    # --- PSYCHE : Moteur de personnalité ---
    psyche.init(list(orchestrator.agents.keys()))
    print("   🧬 PSYCHE: Moteur de personnalité actif.")

    # --- JOURNAL STRATÉGIQUE ---
    print(f"   📖 JOURNAL: Mémoire stratégique active ({strat_journal.entry_count()} entrées).")

    # --- CONSCIENCE DE SOI ---
    awareness.init()
    awareness.generate_snapshot()
    print("   🪞 CONSCIENCE: Moteur de conscience de soi actif.")

    # --- OBJECTIFS AUTONOMES ---
    objectives_engine.init()
    objectives_engine.seed_daily_objectives()
    print(f"   🎯 OBJECTIFS: Moteur d'objectifs actif ({len(objectives_engine.get_active_objectives())} actifs).")

    # --- FEEDBACK EVOLUTION ---
    from core.evolution_feedback import feedback_loop
    feedback_loop.init()
    print("   🔄 FEEDBACK: Boucle de feedback Evolution active.")

    # --- PULSIONS PRIMORDIALES ---
    desires.init()
    print("   💓 DESIRS: Moteur de pulsions primordiales actif.")

    # --- CORTEX ASSOCIATIF ---
    from core.synaptic_network import cortex
    cortex.init()
    print(f"   🧬 SYNAPSE: Cortex associatif actif "
          f"({len(cortex.nodes)} noeuds, {len(cortex.synapses)} synapses).")

    # --- TRONC CÉRÉBRAL (Cerveau Reptilien) ---
    from core.reptilian_core import reptile
    reptile.init()
    reptile_task = asyncio.create_task(reptile.start_watchdog())
    print(f"   🦎 REPTILIEN: Tronc cérébral actif (menace={reptile.threat_level:.1f}).")

    # --- CORTEX PRÉFRONTAL (Fonction Exécutive) ---
    from core.prefrontal import prefrontal
    prefrontal.init()
    delib_task = asyncio.create_task(prefrontal.start_deliberation())
    goals_active = len([g for g in prefrontal.goals if g.status == "active"])
    print(f"   🧠 PRÉFRONTAL: Fonction exécutive active ({goals_active} goals).")

    # --- VOIX INTÉRIEURE (Aires de Broca & Wernicke) ---
    from core.inner_voice import voice as inner_voice
    inner_voice.init()
    print("   🗣️ VOIX INTÉRIEURE: Aires de Broca & Wernicke actives.")

    # --- COEUR (Moteur Cardiaque) ---
    from core.cardiac_engine import heart
    heart.init()
    heart_task = asyncio.create_task(heart.start_beating())
    print(f"   💓 COEUR: Moteur cardiaque actif (BPM={heart.bpm:.0f}).")

    print("   🧠 Autonomie & Gouvernance : ACTIVES.")

    # --- CI/CD Pipeline (remplace quality_control_listener) ---
    ci_pipeline.start()

    # --- Smart Restart via bus (propre, pas de sys.exit dans une Task) ---
    bus.subscribe("SMART_RESTART_REQUESTED", _on_smart_restart)

    talk_logger.start()
    interface_logger.start()
    asyncio.create_task(autonomy.start_loop())
    yield
    ci_pipeline.stop()
    talk_logger.stop()
    interface_logger.stop()
    print("🔌 Arrêt.")
    tracemalloc.stop()

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

    # Kill switch actif → système bloqué, pas prêt
    if orchestrator.kill_switch_active:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={"ready": False, "reason": "kill_switch_active"},
        )

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
            mem_health = mgr.check_health()
            checks["chromadb"] = {
                "status": "ok" if mem_health["status"] == "healthy" else "error",
                "detail": mem_health["status"],
                "persistent": mem_health.get("persistent", False),
                "collections": mem_health.get("collections", {}),
                "probe_ok": mem_health.get("probe_ok", False),
                "warnings": mem_health.get("warnings", []),
            }
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

@app.post("/api/autonomy/reset-budget", dependencies=[Depends(verify_token)])
async def autonomy_reset_budget():
    """Reset le compteur quotidien de routines autonomes (pour debug/observation)."""
    old_count = autonomy.daily_count
    autonomy.daily_count = 0
    autonomy.daily_budget_used = 0
    autonomy.error_streak = 0
    autonomy._persist_state()
    await bus.publish("THOUGHT_STREAM", {
        "agent": "SYSTEM",
        "content": f"Budget autonomie réinitialisé ({old_count} → 0). Routines relancées.",
        "type": "info"
    })
    return {"status": "ok", "previous_count": old_count, "new_count": 0}

@app.get("/api/journal")
async def api_journal():
    """Retourne le journal stratégique complet + métadonnées."""
    return {
        "entry_count": strat_journal.entry_count(),
        "recent_context": strat_journal.get_recent_context(5, max_chars=3000),
        "full_journal": strat_journal.get_full_journal(),
    }

@app.get("/api/psyche/status")
async def psyche_status():
    """Retourne l'état complet du moteur de personnalité PSYCHE."""
    return {
        "system_average": psyche.get_system_average(),
        "agents": psyche.get_all_traits(),
        "last_decay_day": psyche.last_decay_day,
        "history_count": len(psyche.history),
    }

@app.get("/api/awareness/status")
async def awareness_status():
    """Retourne le dernier snapshot de conscience + patterns détectés."""
    return {
        "snapshot": awareness.get_latest_snapshot(),
        "patterns": awareness.detect_patterns(),
        "snapshot_count": len(awareness.get_all_snapshots()),
    }

@app.get("/api/awareness/history")
async def awareness_history():
    """Retourne l'historique complet des snapshots de conscience."""
    return {"snapshots": awareness.get_all_snapshots()}

@app.get("/api/synaptic/graph")
async def synaptic_graph():
    """Retourne le graphe synaptique optimise pour D3.js (VISION)."""
    from core.synaptic_network import cortex
    from core.cardiac_engine import heart

    nodes = []
    for nid, node in cortex.nodes.items():
        nodes.append({
            "id": nid,
            "concept": node["concept"],
            "type": node["node_type"],
            "energy": round(node["energy"], 3),
            "activation": node["activation_count"],
            "valence": node.get("affect", {}).get("valence", 0.0),
        })

    links = []
    for key, syn in cortex.synapses.items():
        if syn["source"] in cortex.nodes and syn["target"] in cortex.nodes:
            links.append({
                "source": syn["source"],
                "target": syn["target"],
                "weight": round(syn["weight"], 3),
                "type": syn["synapse_type"],
            })

    cardiac = heart.get_stats()
    return {"nodes": nodes, "links": links, "cardiac": cardiac, "stats": cortex.get_stats()}

@app.get("/api/reptilian/status")
async def reptilian_status():
    """Retourne l'état du tronc cérébral reptilien."""
    from core.reptilian_core import reptile
    return reptile.get_stats()

@app.get("/api/prefrontal/status")
async def prefrontal_status():
    """Retourne l'état du cortex préfrontal (goals, stratégies, narratif)."""
    from core.prefrontal import prefrontal
    return prefrontal.get_stats()

@app.get("/api/inner-voice/status")
async def inner_voice_status():
    """Retourne l'état de la voix intérieure (stats, précision, pensée courante)."""
    from core.inner_voice import voice as inner_voice
    return inner_voice.get_stats()

@app.get("/api/inner-voice/stream")
async def inner_voice_stream():
    """Retourne les 30 dernières pensées du flux de conscience."""
    from core.inner_voice import voice as inner_voice
    return inner_voice.get_stream(n=30)

@app.get("/api/objectives")
async def api_objectives():
    """Retourne les objectifs actifs + historique récent (10 derniers complétés/expirés)."""
    all_objs = objectives_engine.get_all_objectives()
    active = [o for o in all_objs if o["status"] == "active"]
    recent_done = [o for o in all_objs if o["status"] in ("completed", "failed")][-10:]
    return {"active": active, "recent": recent_done}

@app.post("/api/objectives", dependencies=[Depends(verify_token)])
async def api_create_objective(request: Request):
    """Création manuelle d'un objectif (source=user)."""
    data = await request.json()
    active = objectives_engine.get_active_objectives()
    if len(active) >= MAX_ACTIVE_OBJECTIVES:
        raise HTTPException(status_code=409, detail=f"Maximum d'objectifs actifs atteint ({MAX_ACTIVE_OBJECTIVES})")
    obj = objectives_engine.create_objective(
        title=data.get("title", "Objectif utilisateur"),
        obj_type=data.get("type", "performance"),
        priority=data.get("priority", "medium"),
        source="user",
        criteria=data.get("criteria", {"metric": "success_rate", "operator": ">=", "target": 0.8}),
        routine_affinities=data.get("routine_affinities", {}),
        deadline_routines=data.get("deadline_routines", 30),
    )
    if obj is None:
        raise HTTPException(status_code=409, detail="Création échouée")
    return {"status": "created", "objective": obj}

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

    # Commande admin : reset budget autonomie
    if msn.strip().lower() in ("reset budget", "reset autonomy", "relance autonomie"):
        old_count = autonomy.daily_count
        autonomy.daily_count = 0
        autonomy.error_streak = 0
        autonomy._persist_state()
        await bus.publish("THOUGHT_STREAM", {
            "agent": "SYSTEM",
            "content": f"Budget autonomie réinitialisé ({old_count} → 0). Routines relancées.",
            "type": "success"
        })
        return {"status": "ok", "target": "system", "action": "reset_budget"}

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