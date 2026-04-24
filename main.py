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
    try:
        from core.self_awareness import awareness
        awareness._save()
    except Exception:
        pass
    try:
        from core.ami import alfred
        alfred._save()
    except Exception:
        pass
    try:
        from core.rival import stefan
        stefan._save()
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    tracemalloc.start()
    from config import Config as _cfg
    # --- GPU POWER CAP (survit pas aux reboots, doit être réappliqué) ---
    import subprocess as _sp
    try:
        _sp.run(["nvidia-smi", "-pl", "250"], capture_output=True, timeout=5)
        print("   ⚡ GPU: Power cap 250W appliqué.")
    except Exception:
        print("   ⚠️ GPU: Power cap 250W non appliqué (droits admin requis).")
    print(f"🤖 PROMÉTHÉE {_cfg.VERSION} (Smart Restart) [Projet: {_cfg.PROJECT_ID}]: Chargement des modules...")
    for slug, class_name, file_name in AGENTS_CONFIG:
        try:
            module = importlib.import_module(f"Agents.{file_name}")
            AgentClass = getattr(module, class_name)
            await orchestrator.register_agent(slug, AgentClass())
            print(f"   [OK] {slug.upper()}")
        except Exception as e:
            print(f"   [ERR] {slug}: {e}")
    
    # --- BLOOM V4.2 : indexes pre-LLM (anti-hallucination) ---
    try:
        from core.bloom_filter import initialize_bloom_at_boot
        _project_root = os.path.dirname(os.path.abspath(__file__))
        _bloom_stats = initialize_bloom_at_boot(_project_root)
        print(
            f"   🧬 BLOOM V4.2: {_bloom_stats['functions']} fns + "
            f"{_bloom_stats['classes']} cls + {_bloom_stats['files']} files "
            f"({_bloom_stats['memory_kb_total']} KB, {_bloom_stats['build_ms']}ms)"
        )
    except Exception as _e:
        print(f"   ⚠️ BLOOM V4.2: init echouee ({_e}) - fallback transparent")

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

    # --- ALFRED (Ami / Coffee Break) ---
    from core.ami import alfred
    alfred.init()
    print(f"   ☕ ALFRED: Ami actif ({alfred.session_count} cafés partagés).")

    # --- STEFAN (Rival / Confrontation) ---
    from core.rival import stefan
    stefan.init()
    print(f"   ⚔️ STEFAN: Rival actif ({stefan.confrontation_count} confrontations).")

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
    awareness._save()
    alfred._save()
    stefan._save()
    soliloque._save()
    try:
        from core.games.game_hub import game_hub
        game_hub._save()
    except Exception:
        pass
    try:
        from core.mailbox import mailbox
        mailbox._save()
    except Exception:
        pass
    try:
        from core.curiosity_bank import curiosity_bank
        curiosity_bank._save()
    except Exception:
        pass
    try:
        from core.mentor import mentor
        mentor._save()
    except Exception:
        pass
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
    """Active ou désactive le mode sieste (hibernation 0-GPU).

    Body params:
        enabled (bool) : True pour activer, False pour desactiver
        mode (str)     : "normal" (defaut, cap 2h), "deep" (cap 24h), "hibernation" (cap 7j)
        duration_hours (float) : duree cible. 0 = comportement classique avec renouvellement.
    """
    data = await request.json()
    enabled = data.get("enabled", False)
    mode = data.get("mode", "normal")
    duration_hours = float(data.get("duration_hours", 0.0))
    if enabled:
        accepted = await autonomy.enter_nap(mode=mode, duration_hours=duration_hours)
        if not accepted:
            elapsed = time.time() - autonomy._nap_last_exit if autonomy._nap_last_exit else 0
            remaining = max(0, int(NAP_COOLDOWN - elapsed))
            return {"status": "cooldown", "is_napping": False, "cooldown_remaining": remaining}
    else:
        await autonomy.exit_nap()
    return {
        "status": "ok",
        "is_napping": autonomy.is_napping,
        "mode": getattr(autonomy, "_nap_mode", "normal"),
        "target_duration_hours": getattr(autonomy, "_nap_target_duration", 0.0) / 3600,
    }

@app.post("/api/coffee-mode")
async def toggle_coffee_mode(request: Request):
    """Active ou désactive le mode café (socialisation libre 20 min)."""
    data = await request.json()
    enabled = data.get("enabled", False)
    if enabled:
        force = data.get("force", False)
        if force:
            autonomy._coffee_last_exit = 0.0
        accepted = await autonomy.enter_coffee_mode()
        if not accepted:
            elapsed = time.time() - autonomy._coffee_last_exit if autonomy._coffee_last_exit else 0
            from core.autonomy_engine import COFFEE_MODE_COOLDOWN
            remaining = max(0, int(COFFEE_MODE_COOLDOWN - elapsed))
            reason = "cooldown"
            if autonomy.is_napping:
                reason = "sieste active"
            elif autonomy.is_autoresearch:
                reason = "autoresearch actif"
            return {"status": "blocked", "is_coffee_mode": False, "reason": reason, "cooldown_remaining": remaining}
    else:
        await autonomy.exit_coffee_mode()
    return {"status": "ok", "is_coffee_mode": autonomy.is_coffee_mode}

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

@app.post("/api/tissue/sauna")
async def tissue_sauna():
    """Déclenche manuellement le mode Sauna — nettoyage du tissu neural."""
    from core.sauna_mode import sauna
    result = await sauna.start()
    return result

@app.get("/api/tissue/sauna/status")
async def tissue_sauna_status():
    """Retourne l'état du mode Sauna."""
    from core.sauna_mode import sauna
    return sauna.get_status()

@app.get("/api/routine-stats")
async def routine_stats():
    """Retourne le ratio qualite/cout par intent (feedback loop)."""
    stats = getattr(autonomy, "_intent_quality_stats", {})
    result = {}
    for intent, s in stats.items():
        n = s.get("n", 0)
        if n > 0:
            avg_q = round(s["sum_q"] / n, 3)
            avg_c = round(s["sum_c"] / n, 1)
            ratio = round(avg_q / max(avg_c, 1), 3)
            result[intent] = {"avg_quality": avg_q, "avg_cost": avg_c, "ratio": ratio, "n": n}
    return {"stats": result, "total_intents": len(result)}

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

@app.get("/api/alfred/status")
async def alfred_status():
    """Retourne l'état d'Alfred."""
    from core.ami import alfred
    return alfred.get_status()

@app.get("/api/alfred/conversations")
async def alfred_conversations():
    """Retourne les journaux des cafés avec Alfred (markdown)."""
    import os, glob
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "coffee_breaks")
    if not os.path.isdir(log_dir):
        return {"conversations": [], "total_files": 0}
    files = sorted(glob.glob(os.path.join(log_dir, "cafe_*.md")), reverse=True)
    conversations = []
    for fpath in files[:10]:
        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
            conversations.append({
                "date": os.path.basename(fpath).replace("cafe_", "").replace(".md", ""),
                "content": f.read(),
            })
    return {"conversations": conversations, "total_files": len(files)}

@app.post("/api/alfred/coffee")
async def alfred_force_coffee():
    """Force un café avec Alfred (ignore le cooldown)."""
    from core.ami import alfred
    alfred.last_coffee = 0.0  # Reset cooldown
    result = await alfred.coffee_break()
    return result

@app.get("/api/stefan/status")
async def stefan_status():
    """Retourne l'état de Stefan."""
    from core.rival import stefan
    return stefan.get_status()

@app.get("/api/stefan/confrontations")
async def stefan_confrontations():
    """Retourne les journaux des confrontations avec Stefan."""
    import os, glob
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "confrontations")
    if not os.path.isdir(log_dir):
        return {"confrontations": [], "total_files": 0}
    files = sorted(glob.glob(os.path.join(log_dir, "confrontation_*.txt")), reverse=True)
    confrontations = []
    for fpath in files[:10]:
        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
            confrontations.append({
                "date": os.path.basename(fpath).replace("confrontation_", "").replace(".txt", ""),
                "content": f.read(),
            })
    return {"confrontations": confrontations, "total_files": len(files)}

@app.post("/api/stefan/confront")
async def stefan_force_confront():
    """Force une confrontation Stefan (ignore le cooldown, cherche le matériel)."""
    from core.rival import stefan
    stefan.last_confrontation = 0.0  # Reset cooldown
    material = stefan.find_confrontation_material()
    if not material:
        return {"status": "skipped", "result": "Rien à confronter — aucun texte avec affirmation sur soi."}
    result = await stefan.confront(material["text"], material["source"])
    return result

# ─── JEUX ────────────────────────────────────────────────────────────────

@app.get("/api/games/status")
async def games_status():
    """Retourne l'etat du hub de jeux (parties actives, stats, competences)."""
    from core.games.game_hub import game_hub
    return game_hub.get_status()

@app.post("/api/games/new")
async def games_new(request: Request):
    """Cree une nouvelle partie. Body: {game, opponent?, promethee_starts?}"""
    from core.games.game_hub import game_hub
    body = await request.json()
    game_type = body.get("game", "")
    opponent = body.get("opponent", "alfred")
    promethee_starts = body.get("promethee_starts", True)
    difficulty = body.get("difficulty", "hard")
    return game_hub.new_game(game_type, opponent, promethee_starts, difficulty)

@app.post("/api/games/move")
async def games_move(request: Request):
    """Joue un coup. Body: {move, player?}"""
    from core.games.game_hub import game_hub
    body = await request.json()
    move = body.get("move")
    player = body.get("player", "promethee")
    return game_hub.play_move(move, player)

@app.post("/api/games/say")
async def games_say(request: Request):
    """Envoyer un message pendant une partie. Body: {message}"""
    from core.games.game_hub import game_hub
    body = await request.json()
    message = body.get("message", "")
    return await game_hub.game_say("human", message)

# ─── MAILBOX (Carnet de correspondance) ──────────────────────────────

@app.get("/api/mentor/status")
async def mentor_status():
    """Retourne l'etat du mentor Claude (budget, historique)."""
    from core.mentor import mentor
    return mentor.get_status()

@app.get("/api/curiosity/status")
async def curiosity_status():
    """Retourne les graines de curiosite."""
    from core.curiosity_bank import curiosity_bank
    return curiosity_bank.get_status()

@app.get("/api/session/latest")
async def session_latest():
    """Retourne la derniere synthese de session."""
    from core.session_synthesis import get_latest, list_sessions
    return {"latest": get_latest(), "sessions": list_sessions()}

@app.get("/api/claude-journal")
async def claude_journal():
    """Retourne le journal de Claude."""
    from core.claude_journal import get_recent
    return {"entries": get_recent(10)}

@app.get("/api/surprises")
async def surprises():
    """Retourne les moments surprenants detectes pour Claude."""
    from core.surprise_detector import get_for_claude
    return {"surprises": get_for_claude()}

@app.get("/api/reasoning/failures")
async def reasoning_failures():
    """Retourne les statistiques des echecs de raisonnement."""
    from core.reasoning_protocol import get_failure_stats
    return get_failure_stats()

@app.get("/api/flaw-journal")
async def flaw_journal():
    """Genere et retourne le rapport des failles du jour."""
    from core.flaw_journal import generate_flaw_report
    return generate_flaw_report()

@app.get("/api/mailbox/status")
async def mailbox_status():
    """Retourne l'etat de la boite aux lettres (nombre non lues, recentes)."""
    from core.mailbox import mailbox
    return mailbox.get_status()

@app.get("/api/mailbox/read")
async def mailbox_read():
    """Lit toutes les lettres non lues. Les marque comme lues."""
    from core.mailbox import mailbox
    letters = mailbox.read_letters(unread_only=True)
    return {"letters": letters, "count": len(letters)}

@app.get("/api/mailbox/all")
async def mailbox_all():
    """Lit toutes les lettres (lues et non lues)."""
    from core.mailbox import mailbox
    return {"letters": mailbox.read_letters(unread_only=False)}

# ─── JEUX ────────────────────────────────────────────────────────────────

@app.post("/api/games/tournament/start")
async def games_tournament_start(request: Request):
    """Lance un tournoi round-robin. Body: {game?}"""
    from core.games.game_hub import game_hub
    body = await request.json()
    game_type = body.get("game", "morpion")
    return game_hub.start_tournament(game_type)

@app.get("/api/games/tournament/status")
async def games_tournament_status():
    """Retourne l'etat du tournoi en cours."""
    from core.games.game_hub import game_hub
    return {"tournament": game_hub.get_tournament_status()}

@app.post("/api/games/tournament/next")
async def games_tournament_next():
    """Lance le prochain match du tournoi."""
    from core.games.game_hub import game_hub
    return game_hub._start_next_tournament_match()

# ─── SYNTHEBRISE (jeu invente par Promethee) ────────────────────────

@app.post("/api/games/synthebrise/new")
async def synthebrise_new(request: Request):
    """Nouvelle partie de Synthebrise. Body: {first_word?, opponent?}"""
    from core.games.synthebrise import SynthebriseEngine
    if not hasattr(app.state, 'synthebrise'):
        app.state.synthebrise = SynthebriseEngine()
    body = await request.json()
    first_word = body.get("first_word", "")
    opponent = body.get("opponent", "promethee")
    return app.state.synthebrise.new_game("human", opponent, first_word)

@app.post("/api/games/synthebrise/play")
async def synthebrise_play(request: Request):
    """Jouer un mot. Body: {word}"""
    from core.games.synthebrise import SynthebriseEngine, ai_play_word
    if not hasattr(app.state, 'synthebrise'):
        return {"error": "Pas de partie en cours"}
    engine = app.state.synthebrise
    body = await request.json()
    word = body.get("word", "")
    # Humain joue
    result = await engine.play_word(word, "human")
    # Le frontend detectera que c'est le tour de l'IA et appellera /ai-play
    return result

@app.post("/api/games/synthebrise/ai-play")
async def synthebrise_ai_play():
    """Force le tour de l'IA."""
    from core.games.synthebrise import ai_play_word
    if not hasattr(app.state, 'synthebrise') or not app.state.synthebrise.game:
        return {"error": "Pas de partie en cours"}
    engine = app.state.synthebrise
    if engine.game.game_over:
        return {"error": "Partie terminee"}
    ai_word = await ai_play_word(engine.game, personality=engine.game.player2)
    result = await engine.play_word(ai_word, engine.game.player2)
    return result

@app.get("/api/games/synthebrise/status")
async def synthebrise_status():
    """Etat de la partie en cours."""
    if not hasattr(app.state, 'synthebrise') or not app.state.synthebrise.game:
        return {"game": None}
    return {"game": app.state.synthebrise.get_state()}

@app.post("/api/games/forfeit")
async def games_forfeit():
    """Abandonne la partie en cours."""
    from core.games.game_hub import game_hub
    return game_hub.forfeit()

# ─── SIGNAL BUS ──────────────────────────────────────────────────────────

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
    """Message chat direct — reponse streamee via WebSocket (CHAT_STREAM).

    Payload JSON:
        message (str): texte du message (obligatoire)
        image_b64 (str, optionnel): image encodee en base64
        image_path (str, optionnel): chemin relatif dans USER_DROPZONE/photos/
        image_filename (str, optionnel): nom du fichier (pour classifier le type)
    """
    from core.chat_engine import chat_engine
    data = await request.json()
    message = data.get("message", "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message vide")

    # Recuperer l'image si fournie (b64 direct ou chemin fichier)
    image_b64 = data.get("image_b64")
    image_filename = data.get("image_filename")
    image_path = data.get("image_path")

    # Si chemin relatif fourni, encoder le fichier en base64
    if image_path and not image_b64:
        import base64
        photos_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "USER_DROPZONE", "photos")
        full_path = os.path.join(photos_dir, image_path)
        if os.path.isfile(full_path):
            try:
                with open(full_path, "rb") as f:
                    image_b64 = base64.b64encode(f.read()).decode("utf-8")
                if not image_filename:
                    image_filename = os.path.basename(full_path)
            except Exception as e:
                logger.error(f"CHAT: Impossible de lire l'image {full_path}: {e}")
        else:
            logger.warning(f"CHAT: Image introuvable: {full_path}")

    async def _do_chat():
        try:
            await chat_engine.chat(message, image_b64=image_b64,
                                   image_filename=image_filename)
        except Exception as e:
            logger.error(f"CHAT: Erreur — {e}")

    background_tasks.add_task(_do_chat)
    has_image = bool(image_b64)
    return {"status": "ok", "message": "Chat en cours...",
            **({"image": True} if has_image else {})}

@app.post("/api/chat/upload", dependencies=[Depends(check_rate_limit), Depends(verify_token)])
async def chat_upload(request: Request, background_tasks: BackgroundTasks):
    """Chat avec upload image multipart/form-data.

    Form fields:
        message (str): texte du message (obligatoire)
        image (UploadFile): fichier image (optionnel)
    """
    from fastapi import UploadFile, Form, File
    import base64

    form = await request.form()
    message = form.get("message", "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message vide")

    image_b64 = None
    image_filename = None
    image_file = form.get("image")

    if image_file and hasattr(image_file, "read"):
        # Verifier la taille (max 10 MB)
        contents = await image_file.read()
        if len(contents) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Image trop volumineuse (max 10 MB)")
        image_b64 = base64.b64encode(contents).decode("utf-8")
        image_filename = getattr(image_file, "filename", "upload.jpg")
        logger.info(f"CHAT: Upload image recu: {image_filename} ({len(contents)} bytes)")

    from core.chat_engine import chat_engine

    async def _do_chat():
        try:
            await chat_engine.chat(message, image_b64=image_b64,
                                   image_filename=image_filename)
        except Exception as e:
            logger.error(f"CHAT: Erreur upload chat — {e}")

    background_tasks.add_task(_do_chat)
    return {"status": "ok", "message": "Chat en cours...",
            **({"image": True, "filename": image_filename} if image_b64 else {})}

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


@app.post("/api/force/school-routine", dependencies=[Depends(verify_token)])
async def force_school_routine(payload: dict):
    """V16 (2026-04-24) — Force une routine scolaire hors creneau.

    Outil d'audit : permet de tester V15.3 (RAG ecole) et V16 (sandbox)
    immediatement au lieu d'attendre le creneau naturel (ecole = minuit).

    Body JSON attendu :
      {
        "slot": "CODE_REVIEW" | "WORKSHOP" | "CREATION" | "RESEARCH" | "BULLETIN",
        "target_file": "core/prefrontal.py",   // optionnel, utilise par RAG
        "topic": "Revue de code ..."          // optionnel, libelle du cours
      }

    Reponse : le dict retourne par _execute_school_class, augmente de
    'duration_s' et (si V16 actif) 'sandbox_verified', 'sandbox_iterations'.
    """
    slot = (payload.get("slot") or "").upper()
    valid_slots = ("CODE_REVIEW", "WORKSHOP", "CREATION", "RESEARCH", "BULLETIN")
    if slot not in valid_slots:
        raise HTTPException(
            status_code=400,
            detail=f"slot invalide: {slot!r}. Doit etre un de {valid_slots}",
        )
    target_file = payload.get("target_file", "")
    topic = payload.get("topic", f"Cours force: {slot}")
    prompt_override = payload.get("prompt")  # si fourni, remplace le prompt du slot

    from core.school_schedule import schedule

    original_get_info = schedule.get_current_slot_info
    original_get_prompt = schedule.get_slot_prompt

    def _forced_slot_info():
        return {
            "slot": slot,
            "subject": {"target_file": target_file, "topic": topic},
            "topic": topic,
            "is_playground": False,
        }

    def _forced_get_prompt(slot_arg):
        # N override que pour le slot demande ; les autres restent intacts.
        if prompt_override and slot_arg.upper() == slot:
            return prompt_override
        return original_get_prompt(slot_arg)

    schedule.get_current_slot_info = _forced_slot_info
    if prompt_override:
        schedule.get_slot_prompt = _forced_get_prompt

    import time as _t
    t0 = _t.time()
    try:
        result = await autonomy._execute_school_class({}, f"SCHOOL_{slot}")
        if isinstance(result, dict):
            result["duration_s"] = round(_t.time() - t0, 2)
            result["forced"] = True
        return result
    except Exception as exc:
        return {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "duration_s": round(_t.time() - t0, 2),
            "forced": True,
        }
    finally:
        schedule.get_current_slot_info = original_get_info
        schedule.get_slot_prompt = original_get_prompt

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