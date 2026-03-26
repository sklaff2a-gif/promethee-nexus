# main.py
from fastapi import FastAPI, WebSocket, Request, BackgroundTasks, Depends, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
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

logger = logging.getLogger("prometheev11.main")
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


class _DailyFileHandler(logging.FileHandler):
    """Handler qui écrit dans promethee_YYYY-MM-DD.log.
    Pas de rotation : un nouveau fichier par jour automatiquement.
    Contourne le bug Windows où _TeeStream empêchait la rotation.
    Nettoyage automatique des fichiers > keep_days jours."""

    def __init__(self, logs_dir: str, prefix: str = "promethee", keep_days: int = 14):
        self._logs_dir = logs_dir
        self._prefix = prefix
        self._keep_days = keep_days
        self._current_date = self._today()
        filepath = os.path.join(logs_dir, f"{prefix}_{self._current_date}.log")
        super().__init__(filepath, mode="a", encoding="utf-8")
        self._cleanup_old_files()

    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d")

    def emit(self, record):
        today = self._today()
        if today != self._current_date:
            self._rotate_to_new_day(today)
        super().emit(record)

    def _rotate_to_new_day(self, new_date: str):
        """Ferme le fichier actuel et ouvre celui du nouveau jour."""
        try:
            if self.stream:
                self.stream.close()
            self._current_date = new_date
            self.baseFilename = os.path.abspath(
                os.path.join(self._logs_dir, f"{self._prefix}_{new_date}.log")
            )
            self.stream = self._open()
            self._cleanup_old_files()
        except Exception:
            pass

    def _cleanup_old_files(self):
        """Supprime les fichiers de log plus vieux que keep_days."""
        try:
            import glob as glob_mod
            from datetime import datetime, timedelta
            cutoff = datetime.now() - timedelta(days=self._keep_days)
            # Nettoyer les fichiers au nouveau format (promethee_YYYY-MM-DD.log)
            for pattern in [f"{self._prefix}_*.log", f"{self._prefix}.log.*"]:
                for filepath in glob_mod.glob(os.path.join(self._logs_dir, pattern)):
                    fname = os.path.basename(filepath)
                    try:
                        # Extraire la date du nom de fichier
                        date_str = fname.replace(f"{self._prefix}_", "").replace(f"{self._prefix}.log.", "").replace(".log", "")
                        fdate = datetime.strptime(date_str, "%Y-%m-%d")
                        if fdate < cutoff:
                            os.remove(filepath)
                    except (ValueError, OSError):
                        pass
        except Exception:
            pass


# FileHandler quotidien : 1 fichier par jour, garde 14 jours
_file_handler = _DailyFileHandler(_LOGS_DIR, prefix="promethee", keep_days=14)
_file_handler.setFormatter(_log_formatter)

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
from core.autonomy_engine import autonomy, NAP_COOLDOWN
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
    ("vision", "DivineVision", "vision_agent"),
    ("professor", "ProfessorAgent", "professor_agent"),
]

async def _on_smart_restart(data: dict):
    """Smart Restart propre : sauvegarde état critique, puis exit(65)."""
    filename = data.get("filename", "?")
    logger.info(f"[SMART RESTART] Programmé suite à modification: {filename}")
    await asyncio.sleep(3)
    _emergency_save()
    os._exit(65)


def _emergency_save():
    """Sauvegarde d'urgence avant os._exit() — le lifespan cleanup ne tourne PAS avec _exit."""
    try:
        from core.synaptic_network import cortex
        cortex.save()
        logger.info("[MAIN] Emergency save: cortex synaptique OK")
    except Exception as e:
        logger.warning(f"[MAIN] Emergency save cortex échoué: {e}")
    try:
        from core.hippocampus import hippocampus
        hippocampus._save()
    except Exception:
        pass
    try:
        from core.autonomy_engine import autonomy
        autonomy._persist_state()
    except Exception:
        pass


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

    # --- SIGNAL BUS (Bus de Signaux Neuraux) ---
    from core.signal_bus import signal_bus
    signal_bus.init()
    print(f"   📡 SIGNAL BUS: Bus neural actif ({len(signal_bus._throttle_rules)} throttles).")

    # --- CORTEX ASSOCIATIF ---
    from core.synaptic_network import cortex
    cortex.init()
    purge_report = cortex.purge_noise_nodes()
    if purge_report["purged_nodes"] > 0:
        print(
            f"   🧹 CORTEX: Purge bruit — {purge_report['purged_nodes']} noeuds, "
            f"{purge_report['purged_synapses']} synapses supprimés"
        )
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

    # --- DOPAMINE (Systeme de Recompense) ---
    from core.dopamine_system import dopamine
    dopamine.init()
    print(f"   🧪 DOPAMINE: Systeme de recompense actif (niveau={dopamine.dopamine_level:.2f}).")

    # --- COEUR (Moteur Cardiaque) — m15: initialisé tôt pour que les organes suivants aient le heartbeat ---
    from core.cardiac_engine import heart
    heart.init()
    heart_task = asyncio.create_task(heart.start_beating())
    print(f"   💓 COEUR: Moteur cardiaque actif (BPM={heart.bpm:.0f}).")

    # --- CORPUS CALLOSUM (Resonance Inter-Organes) ---
    from core.corpus_callosum import callosum
    callosum.init()
    callosum_task = asyncio.create_task(callosum.start_resonance())
    print(f"   🌉 CORPUS CALLOSUM: Pont inter-organes actif (coherence={callosum.global_coherence:.2f}).")

    # --- BRAIN VM (Machine Virtuelle Cerebrale) ---
    from core.brain_vm import brain
    brain.start()
    print(f"   🧬 BRAIN VM: Machine virtuelle cerebrale activee (tick #{brain.tick_count}).")

    # --- ATTENTION CODELETS (LIDA) ---
    from core.attention_codelets import codelet_system
    print(f"   👁 CODELETS: {len(codelet_system.get_registered_names())} codelets d'attention enregistres.")

    # --- NEUROCHEMISTRY (Pools neurochimiques) ---
    from core.neurochemistry import neurochemistry
    nc = neurochemistry.get_status()
    print(f"   🧪 NEUROCHIMIE: S={nc['serotonin']:.2f} NA={nc['noradrenaline']:.2f} ACh={nc['acetylcholine']:.2f}")

    # --- CHUNKING SOAR (Règles apprises depuis les Councils) ---
    from core.event_bus.bus import bus as _bus
    _bus.subscribe("COUNCIL_RULE_LEARNED", RouterAgent.on_council_rule_learned)
    RouterAgent._load_learned_rules()
    print(f"   🧠 CHUNKING: {len(RouterAgent._learned_rules)} regles apprises chargees.")

    # --- HIPPOCAMPE (Mémoire Épisodique) ---
    from core.hippocampus import hippocampus
    hippocampus.init()
    print(f"   🧠 HIPPOCAMPE: Mémoire épisodique active ({len(hippocampus._episodes)} épisodes, {len(hippocampus._arcs)} arcs).")

    # --- LOBE TEMPORAL (Intégration Mémoire Unifiée) ---
    from core.temporal_lobe import temporal as temporal_lobe
    temporal_lobe.init()
    stats = temporal_lobe.get_stats()
    print(f"   🧠 LOBE TEMPORAL: Mémoire unifiée active ({stats['indexed_intents']} intents indexés).")

    # --- NEURAL COMPILER (Knowledge Distillation) ---
    from core.neural_compiler import compiler as neural_compiler
    neural_compiler.init()
    print(f"   🧬 COMPILER: {len(neural_compiler._rules)} règles compilées, "
          f"{len(neural_compiler._observations)} observations.")

    # --- IMPACT ANALYZER (Dépendances & Santé) ---
    from core.impact_analyzer import analyzer as impact_analyzer
    impact_analyzer.init()
    print("   📊 IMPACT: Analyseur de dépendances actif.")

    # --- SANDBOX ENGINE (Tests securises) ---
    from core.sandbox_engine import sandbox as sandbox_engine
    sandbox_engine.init()
    print("   🧪 SANDBOX: Moteur de test securise actif.")

    # --- SUBSTRAT CELLULAIRE (Computation Emergente) ---
    from core.neural_tissue import tissue as neural_tissue
    neural_tissue.init()
    neural_tissue.start_loop()
    print(f"   🧬 TISSUE: {len(neural_tissue.cells)} cellules, gen {max((c.generation for c in neural_tissue.cells), default=0)}.")

    # --- ROADMAP ENGINE (Plan de Route Vivant) ---
    from core.roadmap_engine import roadmap as roadmap_engine
    roadmap_engine.init()
    print(f"   🗺️ ROADMAP: {len(roadmap_engine.modules)} modules trackes.")

    # --- SOLILOQUE (Dialogue Introspectif) ---
    from core.soliloque import soliloque
    soliloque.init()
    print(f"   🪞 SOLILOQUE: Compagnon intérieur actif ({soliloque.session_count} sessions).")

    # --- CHAT DIRECT (Conversation Humain <-> Promethee) ---
    from core.chat_engine import chat_engine
    print(f"   💬 CHAT: Canal direct actif ({len(chat_engine.messages)} messages en memoire).")

    # --- CYCLE CIRCADIEN ---
    from core.circadian_rhythm import circadian
    circadian.init()
    print(f"   🌙 CIRCADIEN: phase={circadian.phase}, cycles={circadian._total_sleep_cycles}.")

    # --- THALAMUS (Relais Sensoriel) ---
    from core.thalamus import thalamus
    thalamus.init()
    print("   🔬 THALAMUS: Relais sensoriel actif.")

    # --- AMYGDALE (Mémoire Émotionnelle) ---
    from core.amygdala import amygdala
    amygdala.init()
    print("   🫀 AMYGDALE: Mémoire émotionnelle active.")

    # --- HYPOTHALAMUS (Régulateur Homéostatique) ---
    from core.hypothalamus import hypothalamus
    hypothalamus.init()
    print(f"   🌡️ HYPOTHALAMUS: Régulateur homéostatique actif (stabilité={hypothalamus._compute_stability_score():.2f}).")

    # --- INSULA (Conscience Intéroceptive) ---
    from core.insula import insula
    insula.init()
    print(f"   🫁 INSULA: Conscience intéroceptive active (cohérence={insula._compute_coherence():.2f}).")

    # --- CINGULATE CORTEX (Détecteur de Conflits) ---
    from core.cingulate_cortex import cingulate
    cingulate.init()
    print(f"   ⚡ CINGULATE: Détecteur de conflits actif ({cingulate.total_conflicts} conflits).")

    # --- BASAL GANGLIA (Habitudes & Renforcement) ---
    from core.basal_ganglia import ganglia
    ganglia.init()
    print(f"   🔄 BASAL GANGLIA: {len(ganglia.habits)} habitudes, {ganglia.total_inhibitions} inhibitions.")

    # --- DEFAULT MODE NETWORK (Vagabondage Mental) ---
    from core.default_mode_network import dmn
    dmn.init()
    dmn_task = asyncio.create_task(dmn.start_wandering())
    print(f"   💭 DMN: Réseau du mode par défaut actif ({dmn.total_wanderings} vagabondages).")

    # --- INCUBATION COGNITIVE (Subconscient Asynchrone) ---
    from core.incubation_cognitive import incubation as incubation_cognitive
    incubation_cognitive.init()
    print(f"   🧬 INCUBATION: Subconscient actif ({len(incubation_cognitive._queue)} problèmes).")

    # --- REFLEXE CURIOSITE (Apprentissage Instinctif) ---
    from core.curiosity_reflex import curiosity as curiosity_reflex
    curiosity_reflex.init()
    print(f"   🔍 CURIOSITE: Reflexe actif ({curiosity_reflex._total_queries} queries).")

    # --- SENSORIUM (Perception Corporelle Hardware) ---
    from core.sensorium import sensorium as sensorium_organ
    sensorium_organ.init()
    await sensorium_organ.start_sampling()
    print(f"   👁️ SENSORIUM: Backend {sensorium_organ._gpu_backend}, {sensorium_organ._tick_count} ticks.")

    # --- OUTREACH : Voix Proactive ---
    from core.outreach import outreach
    outreach.init()
    print("   📬 OUTREACH: Voix proactive active.")

    print("   🧠 Autonomie & Gouvernance : ACTIVES.")

    # --- CI/CD Pipeline (remplace quality_control_listener) ---
    ci_pipeline.start()

    # --- Smart Restart via bus (propre, pas de sys.exit dans une Task) ---
    bus.subscribe("SMART_RESTART_REQUESTED", _on_smart_restart)

    # --- Emploi du temps scolaire ---
    try:
        from core.school_schedule import schedule as school_schedule
        school_schedule.init()
        print(f"   📚 ECOLE: Creneau={school_schedule.get_current_slot()}, jour #{school_schedule._total_school_days}")
    except Exception as e:
        print(f"   [WARN] SchoolSchedule: {e}")

    talk_logger.start()
    interface_logger.start()

    # Task autonomie avec watchdog — détecte la mort silencieuse
    def _on_autonomy_done(task):
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            exc = None
        if exc:
            logger.error(f"[MAIN] ☠️ AUTONOMY TASK MORTE: {exc} — relancement...")
            print(f"   ☠️ AUTONOMY TASK MORTE: {exc}")
            asyncio.create_task(_resilient_autonomy())

    async def _resilient_autonomy():
        """Relance le loop d'autonomie si le task meurt."""
        try:
            await autonomy.start_loop()
        except Exception as e:
            logger.error(f"[MAIN] Autonomy loop fatale: {e}", exc_info=True)

    _autonomy_task = asyncio.create_task(autonomy.start_loop())
    _autonomy_task.add_done_callback(_on_autonomy_done)
    yield
    ci_pipeline.stop()
    talk_logger.stop()
    interface_logger.stop()
    # Persistance de TOUS les organes au shutdown
    signal_bus.shutdown()
    thalamus._save()
    amygdala.save()
    hypothalamus._save()
    insula._save()
    cingulate._save()
    ganglia._save()
    dmn.stop()
    dmn._save()
    incubation_cognitive._save()
    curiosity_reflex._save()
    sensorium_organ.stop()
    sensorium_organ._save()
    reptile.save()
    prefrontal.save()
    inner_voice.save()
    dopamine._save()
    callosum.save()
    heart.save()
    circadian.save()
    cortex.save()
    desires.save()
    chat_engine._save()
    outreach._save()
    hippocampus._save()
    print("🔌 Arrêt.")
    tracemalloc.stop()

app = FastAPI(lifespan=lifespan)
_start_time = time.time()

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("static/favicon.svg", media_type="image/svg+xml")

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
        "is_napping": autonomy.is_napping,
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

@app.post("/api/sieste", dependencies=[Depends(verify_token)])
async def toggle_nap_mode(request: Request):
    """Active ou désactive le mode sieste (hibernation 0-GPU)."""
    data = await request.json()
    enabled = data.get("enabled", False)
    if enabled:
        accepted = await autonomy.enter_nap()
        if not accepted:
            elapsed = time.time() - autonomy._nap_last_exit if autonomy._nap_last_exit else 0
            remaining = max(0, int(NAP_COOLDOWN - elapsed))
            return {"status": "cooldown", "is_napping": False, "cooldown_remaining": remaining}
    else:
        await autonomy.exit_nap()
    return {"status": "ok", "is_napping": autonomy.is_napping}

@app.post("/api/autoresearch", dependencies=[Depends(verify_token)])
async def toggle_autoresearch(request: Request):
    """Active ou désactive le mode Autoresearch (expérimentation paramètres 4h)."""
    data = await request.json()
    enabled = data.get("enabled", False)
    if enabled:
        accepted = await autonomy.enter_autoresearch()
        if not accepted:
            return {"status": "blocked", "is_autoresearch": False, "reason": "Mode sieste actif"}
    else:
        await autonomy.exit_autoresearch()
    return {
        "status": "ok",
        "is_autoresearch": autonomy.is_autoresearch,
        "autoresearch_info": {
            "experiments": autonomy._autoresearch_experiments,
            "kept": autonomy._autoresearch_kept,
        } if autonomy.is_autoresearch else None,
    }

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

@app.get("/api/thalamus/status")
async def thalamus_status():
    """Retourne l'etat du thalamus (scorecard, focus, seuil)."""
    from core.thalamus import thalamus
    return thalamus.get_stats()

@app.get("/api/hypothalamus/status")
async def hypothalamus_status():
    """Retourne l'etat de l'hypothalamus (homeostasie, erreurs, corrections)."""
    from core.hypothalamus import hypothalamus
    return hypothalamus.get_stats()

@app.get("/api/insula/status")
async def insula_status():
    """Retourne l'etat de l'insula (corps, marqueurs somatiques, coherence)."""
    from core.insula import insula
    return insula.get_stats()

@app.get("/api/cingulate/status")
async def cingulate_status():
    """Retourne l'etat du cortex cingulaire (conflits, erreurs, adaptation)."""
    from core.cingulate_cortex import cingulate
    return cingulate.get_stats()

@app.get("/api/basal-ganglia/status")
async def basal_ganglia_status():
    """Retourne l'etat des ganglions de la base (habitudes, GO/NO-GO)."""
    from core.basal_ganglia import ganglia
    return ganglia.get_stats()

@app.get("/api/dmn/status")
async def dmn_status():
    """Retourne l'etat du reseau du mode par defaut (vagabondage, insights)."""
    from core.default_mode_network import dmn
    return dmn.get_stats()

@app.get("/api/incubation/stats")
async def incubation_stats():
    """Retourne l'etat de l'incubation cognitive (problemes, eurekas)."""
    from core.incubation_cognitive import incubation
    return incubation.get_stats()

@app.get("/api/roadmap/candidates")
async def roadmap_candidates():
    """Retourne les prochains modules candidats de la roadmap."""
    from core.roadmap_curator import get_next_candidates, suggest_priority_order
    candidates = get_next_candidates()
    return suggest_priority_order(candidates)

@app.get("/api/amygdala/stats")
async def amygdala_stats():
    """Retourne les statistiques de l'amygdale (mémoires émotionnelles)."""
    from core.amygdala import amygdala
    return amygdala.get_stats()

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

@app.get("/api/health/impact")
async def health_impact():
    """Retourne le graphe de dépendances et santé des modules."""
    from core.impact_analyzer import analyzer as impact_analyzer
    return impact_analyzer.build_graph()

@app.get("/api/callosum/status")
async def callosum_status():
    """Retourne l'etat du corpus callosum (resonance inter-organes)."""
    from core.corpus_callosum import callosum
    return callosum.get_stats()

@app.get("/api/connectivity/status")
async def connectivity_status():
    """Retourne l'etat de la matrice de connectivite inter-organes."""
    try:
        from core.connectivity_matrix import matrix
        return matrix.get_matrix_summary()
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/brain/status")
async def brain_status():
    """Retourne l'etat de la machine virtuelle cerebrale (Brain VM)."""
    try:
        from core.brain_vm import brain
        return brain.get_status()
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/workspace/status")
async def workspace_status():
    """Retourne l'etat du Global Workspace (conscience)."""
    try:
        from core.global_workspace import workspace
        return workspace.get_status()
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/hippocampus/status")
async def hippocampus_status():
    """Retourne l'etat de la memoire episodique (hippocampe)."""
    from core.hippocampus import hippocampus
    return hippocampus.get_stats()

@app.get("/api/circadian/status")
async def circadian_status():
    """Retourne l'état du cycle circadien."""
    from core.circadian_rhythm import circadian
    return circadian.get_stats()

@app.get("/api/compiler/status")
async def compiler_status():
    """Retourne l'état du Neural Compiler (distillation LLM)."""
    from core.neural_compiler import compiler
    return compiler.get_stats()

@app.get("/api/roadmap")
async def roadmap_status():
    """Retourne l'etat de la roadmap vivante (modules, phases, WIP)."""
    from core.roadmap_engine import roadmap as roadmap_engine
    return roadmap_engine.get_stats()

@app.get("/api/sandbox/status")
async def sandbox_status():
    """Retourne les statistiques unifiees du moteur de test (sandbox + CI + graphe)."""
    from core.sandbox_engine import sandbox as sandbox_engine
    from core.test_runner import get_stats as get_runner_stats
    sandbox_stats = sandbox_engine.get_stats()
    # Enrichir avec les stats du test_runner unifie
    sandbox_stats["test_runner"] = get_runner_stats()
    # Ajouter les stats du graphe de dependances
    try:
        graph = sandbox_engine._get_test_graph()
        if graph:
            sandbox_stats["test_graph"] = graph.get_stats()
    except Exception:
        sandbox_stats["test_graph"] = {"built": False}
    return sandbox_stats

@app.get("/api/tissue/status")
async def tissue_status():
    """Retourne les statistiques du substrat cellulaire neuronal."""
    from core.neural_tissue import tissue as neural_tissue
    return neural_tissue.get_stats()

@app.get("/api/tissue/grid")
async def tissue_grid():
    """Retourne la grille, les cellules vivantes et les signaux de zone."""
    from core.neural_tissue import tissue as neural_tissue, SIGNAL_ZONES
    alive = [c for c in neural_tissue.cells if c.alive]
    return {
        "grid": [list(row) for row in neural_tissue.grid],
        "waste_grid": [list(row) for row in neural_tissue.waste_grid],
        "toxic_grid": [list(row) for row in neural_tissue.toxic_grid],
        "zone_signals": neural_tissue.get_zone_signals(),
        "cells": [{"x": c.x, "y": c.y, "genome": c.genome,
                    "energy": round(c.energy, 1), "age": c.age,
                    "generation": c.generation} for c in alive],
        "zones": {n: {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
                  for n, (x1, y1, x2, y2) in SIGNAL_ZONES.items()},
        "tick_count": neural_tissue.tick_count,
        "tick_ms": round(neural_tissue._last_tick_ms, 2),
    }

@app.get("/api/soliloque/status")
async def soliloque_status():
    """Retourne l'état du moteur de soliloque."""
    from core.soliloque import soliloque
    return soliloque.get_status()

@app.get("/api/soliloque/history")
async def soliloque_history():
    """Retourne les 10 dernières sessions de soliloque."""
    from core.soliloque import soliloque
    return soliloque.get_history(n=10)

@app.get("/api/signal-bus/status")
async def signal_bus_status():
    """Retourne les metriques du bus de signaux neuraux."""
    from core.signal_bus import signal_bus
    return signal_bus.get_metrics()

@app.get("/api/curiosity/stats")
async def curiosity_stats():
    """Retourne les stats du reflexe de curiosite."""
    from core.curiosity_reflex import curiosity
    return curiosity.get_stats()

@app.get("/api/sensorium/status")
async def sensorium_status():
    """Retourne les 5 sens corporels et metriques hardware."""
    from core.sensorium import sensorium
    return sensorium.get_stats()

@app.get("/api/outreach/pending")
async def outreach_pending(request: Request):
    """Messages proactifs en attente d'envoi."""
    from core.outreach import outreach
    telegram = request.query_params.get("telegram", "false").lower() == "true"
    return outreach.get_pending(telegram=telegram)

@app.post("/api/outreach/ack")
async def outreach_ack():
    """Acquitte les messages proactifs envoyés."""
    from core.outreach import outreach
    outreach.acknowledge()
    return {"status": "ok"}

@app.get("/api/outreach/stats")
async def outreach_stats():
    """Statistiques des notifications proactives."""
    from core.outreach import outreach
    return outreach.get_stats()

@app.post("/api/outreach/silent")
async def outreach_silent(request: Request):
    """Active/desactive le mode silencieux."""
    from core.outreach import outreach
    data = await request.json()
    outreach.set_silent_mode(data.get("active", False))
    return {"status": "ok", "silent_mode": outreach._silent_mode}

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

@app.post("/api/research-topic", dependencies=[Depends(verify_token)])
async def api_add_research_topic(request: Request):
    """Ajoute un sujet de recherche utilisateur — oriente les routines VEILLE."""
    data = await request.json()
    subject = data.get("subject", "").strip()
    if not subject or len(subject) < 3:
        raise HTTPException(status_code=400, detail="Sujet trop court (min 3 caractères)")
    if len(subject) > 200:
        raise HTTPException(status_code=400, detail="Sujet trop long (max 200 caractères)")
    topic = objectives_engine.add_user_topic(subject)
    if topic is None:
        raise HTTPException(status_code=409, detail="Maximum de topics atteint (3)")
    await bus.publish("THOUGHT_STREAM", {
        "agent": "SYSTEM",
        "content": f"Nouvel objectif de recherche: {subject}",
        "type": "success",
    })
    return {"status": "created", "topic": topic}

@app.get("/api/research-topics")
async def api_research_topics():
    """Retourne les topics de recherche utilisateur actifs."""
    return {"topics": objectives_engine.get_active_user_topics()}

@app.delete("/api/research-topic/{topic_id}", dependencies=[Depends(verify_token)])
async def api_cancel_research_topic(topic_id: str):
    """Annule un topic de recherche utilisateur."""
    ok = objectives_engine.cancel_user_topic(topic_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Topic non trouvé ou déjà annulé")
    return {"status": "cancelled", "id": topic_id}

@app.post("/api/reboot", dependencies=[Depends(verify_token)])
async def api_reboot(request: Request):
    """Redémarrage propre : bloque les nouvelles missions, flush, exit(65)."""
    await orchestrator.set_kill_switch(True)
    await bus.publish("THOUGHT_STREAM", {
        "agent": "SYSTEM", "content": "Redémarrage en cours...", "type": "info"
    })
    await bus.publish("SYSTEM_OVERRIDE", {"active": True, "reboot": True})
    await asyncio.sleep(2)
    _emergency_save()
    os._exit(65)

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

# --- CHAT DIRECT (Conversation Humain <-> Promethee) ---

@app.post("/api/chat", dependencies=[Depends(check_rate_limit), Depends(verify_token)])
async def chat_message(request: Request, background_tasks: BackgroundTasks):
    """Message chat direct — reponse streamee via WebSocket (CHAT_STREAM)."""
    from core.chat_engine import chat_engine
    data = await request.json()
    message = data.get("message", "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message vide")

    async def _do_chat():
        try:
            await chat_engine.chat(message)
        except Exception as e:
            logger.error(f"CHAT: Erreur — {e}")

    background_tasks.add_task(_do_chat)
    return {"status": "ok", "message": "Chat en cours..."}

@app.get("/api/chat/history", dependencies=[Depends(verify_token)])
async def chat_history(n: int = Query(default=50, ge=1, le=200)):
    """Retourne les N derniers messages du chat."""
    from core.chat_engine import chat_engine
    return {"messages": chat_engine.get_history(n)}

@app.delete("/api/chat/clear", dependencies=[Depends(verify_token)])
async def chat_clear():
    """Efface l'historique de chat."""
    from core.chat_engine import chat_engine
    chat_engine.clear_history()
    return {"status": "ok", "message": "Historique efface"}

# --- SALARY (Photo Salary) ---

@app.get("/api/salary/status", dependencies=[Depends(verify_token)])
async def salary_status():
    """Status complet du salaire visuel."""
    try:
        from core.photo_salary import salary
        return salary.get_status()
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/salary/wish", dependencies=[Depends(verify_token)])
async def salary_add_wish(request: Request):
    """Ajoute un souhait visuel a la wishlist."""
    try:
        from core.photo_salary import salary
        data = await request.json()
        category = data.get("category", "").strip()
        if not category:
            raise HTTPException(status_code=400, detail="Categorie vide")
        added = salary.add_wish(category)
        return {"status": "ok", "added": added, "wishlist": salary.get_wishlist()}
    except HTTPException:
        raise
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/school/today", dependencies=[Depends(verify_token)])
async def school_today():
    """P0: Resume complet de la journee scolaire."""
    try:
        from core.school_schedule import schedule
        return schedule.get_today_summary()
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/school/deliverable/{filename}")
async def school_deliverable(filename: str):
    """P0: Lire un livrable complet."""
    try:
        from core.school_schedule import DELIVERABLES_DIR
        filepath = os.path.join(DELIVERABLES_DIR, filename)
        if not os.path.exists(filepath):
            raise HTTPException(status_code=404, detail="Livrable non trouve")
        with open(filepath, "r", encoding="utf-8") as f:
            return {"filename": filename, "content": f.read()}
    except HTTPException:
        raise
    except Exception as e:
        return {"error": str(e)}

# ============================================================
# API Stimulation — Interface externe des organes (Piste 7 bio-inspired)
# Inspire de Cortical Labs CL1 "Cortical Cloud" API
# ============================================================

# Stimuli cardiaques autorises
_CARDIAC_STIMULI = frozenset({
    "success", "failure", "eureka", "routine_done", "learning",
    "council", "idle", "dream", "veto", "creation", "threat",
    "adrenaline", "sleep_deep", "dawn", "soothe",
})

# Drives autorises
_VALID_DRIVES = frozenset({
    "CURIOSITE", "MAITRISE", "STABILITE", "CONNEXION",
    "CROISSANCE", "CREATION", "COMPREHENSION",
})

# Events bus autorises pour stimulation externe
_ALLOWED_BUS_EVENTS = frozenset({
    "DOPAMINE_SURGE", "DOPAMINE_DIP", "REPTILIAN_ALERT",
    "KNOWLEDGE_GAP_DETECTED", "EUREKA_BRIDGE",
    "CARDIAC_EMOTION_CHANGE",
})


@app.post("/api/stimulate", dependencies=[Depends(verify_token)])
async def api_stimulate(request: Request):
    """Stimulation directe des organes de Promethee.

    Body JSON : {target, action, params}
    Targets : cardiac, desire, reptilian, dopamine, bus
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON invalide")

    target = body.get("target", "")
    action = body.get("action", "")
    params = body.get("params", {})

    if not target or not action:
        raise HTTPException(status_code=400, detail="target et action requis")

    result = {"target": target, "action": action, "status": "ok"}

    try:
        if target == "cardiac":
            # Stimulation cardiaque : react(stimulus)
            stimulus = params.get("stimulus", action)
            if stimulus not in _CARDIAC_STIMULI:
                raise HTTPException(
                    status_code=400,
                    detail=f"Stimulus inconnu. Autorises: {sorted(_CARDIAC_STIMULI)}"
                )
            from core.cardiac_engine import heart
            heart.react(stimulus)
            result["detail"] = f"Cardiac react({stimulus}): emotion={heart.current_emotion}, bpm={heart.bpm:.0f}"

        elif target == "desire":
            # Stimulation pulsions : satisfy ou frustrate
            drive = params.get("drive", "").upper()
            if drive not in _VALID_DRIVES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Drive inconnu. Autorises: {sorted(_VALID_DRIVES)}"
                )
            from core.desire_engine import desires
            if action == "satisfy":
                d = desires.drives.get(drive)
                if d:
                    amount = min(30.0, max(1.0, float(params.get("amount", 10.0))))
                    d.deprivation = max(0.0, d.deprivation - amount)
                    d.frustration_streak = 0
                    result["detail"] = f"Drive {drive} satisfait: deprivation={d.deprivation:.0f}"
            elif action == "frustrate":
                d = desires.drives.get(drive)
                if d:
                    amount = min(20.0, max(1.0, float(params.get("amount", 10.0))))
                    d.deprivation = min(100.0, d.deprivation + amount)
                    d.frustration_streak += 1
                    result["detail"] = f"Drive {drive} frustre: deprivation={d.deprivation:.0f}"
            else:
                raise HTTPException(status_code=400, detail="Actions desire: satisfy, frustrate")

        elif target == "reptilian":
            # Stimulation reptilienne : set_threat
            if action == "set_threat":
                level = min(10.0, max(0.0, float(params.get("level", 5.0))))
                from core.event_bus.bus import bus as _bus
                await _bus.publish("REPTILIAN_ALERT", {
                    "threat_level": level,
                    "source": "api_stimulate",
                })
                result["detail"] = f"Alerte reptilienne publiee: threat={level}"
            else:
                raise HTTPException(status_code=400, detail="Actions reptilian: set_threat")

        elif target == "dopamine":
            # Stimulation dopaminergique via bus
            from core.event_bus.bus import bus as _bus
            if action == "surge":
                magnitude = min(1.0, max(0.1, float(params.get("magnitude", 0.5))))
                await _bus.publish("DOPAMINE_SURGE", {
                    "magnitude": magnitude,
                    "source": "api_stimulate",
                })
                result["detail"] = f"Dopamine surge publie: magnitude={magnitude}"
            elif action == "dip":
                magnitude = min(1.0, max(0.1, float(params.get("magnitude", 0.3))))
                await _bus.publish("DOPAMINE_DIP", {
                    "magnitude": magnitude,
                    "source": "api_stimulate",
                })
                result["detail"] = f"Dopamine dip publie: magnitude={magnitude}"
            else:
                raise HTTPException(status_code=400, detail="Actions dopamine: surge, dip")

        elif target == "bus":
            # Publication directe sur le bus (whitelist)
            event = params.get("event", "")
            data = params.get("data", {})
            if event not in _ALLOWED_BUS_EVENTS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Event non autorise. Autorises: {sorted(_ALLOWED_BUS_EVENTS)}"
                )
            from core.event_bus.bus import bus as _bus
            await _bus.publish(event, data)
            result["detail"] = f"Event {event} publie sur le bus"

        else:
            raise HTTPException(
                status_code=400,
                detail="Targets autorises: cardiac, desire, reptilian, dopamine, bus"
            )

    except HTTPException:
        raise
    except Exception as e:
        result["status"] = "error"
        result["detail"] = str(e)

    return result


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
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "8000"))
    uvicorn.run("main:app", host=host, port=port, reload=False)