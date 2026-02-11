import asyncio
import time
import random
import logging
import json
import os
from datetime import date, datetime
from core.orchestrator import orchestrator
from core.event_bus.bus import bus

logger = logging.getLogger("AutonomyEngine")

# Limite quotidienne de routines autonomes
MAX_DAILY_ROUTINES = 20

# Chemin du fichier d'état persistant
STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory", "autonomy_state.json")


class SystemHealthCheck:
    """Bilan de santé système léger (CPU, RAM, Ollama). Pas d'appel LLM."""

    CPU_WARN = 80
    CPU_CRIT = 95
    RAM_WARN = 75
    RAM_CRIT = 90
    OLLAMA_TIMEOUT = 3

    @staticmethod
    async def run() -> dict:
        import psutil
        import httpx

        cpu_percent = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        ram_percent = mem.percent
        ram_used_gb = round(mem.used / (1024 ** 3), 2)
        ram_total_gb = round(mem.total / (1024 ** 3), 2)

        warnings = []
        ollama_alive = False
        ollama_models = []

        # Ping Ollama
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get("http://localhost:11434/api/tags", timeout=SystemHealthCheck.OLLAMA_TIMEOUT)
            if resp.status_code == 200:
                ollama_alive = True
                ollama_models = [m["name"] for m in resp.json().get("models", [])]
            else:
                warnings.append(f"Ollama HTTP {resp.status_code}")
        except (httpx.ConnectError, httpx.ConnectTimeout):
            warnings.append("Ollama DOWN (connection refused)")
        except Exception as e:
            warnings.append(f"Ollama error: {e}")

        # Verdict
        if cpu_percent >= SystemHealthCheck.CPU_CRIT or ram_percent >= SystemHealthCheck.RAM_CRIT or not ollama_alive:
            verdict = "NO_GO"
        elif cpu_percent >= SystemHealthCheck.CPU_WARN or ram_percent >= SystemHealthCheck.RAM_WARN:
            verdict = "DEGRADED"
        else:
            verdict = "GO"

        if cpu_percent >= SystemHealthCheck.CPU_WARN:
            warnings.append(f"CPU élevé: {cpu_percent}%")
        if ram_percent >= SystemHealthCheck.RAM_WARN:
            warnings.append(f"RAM élevée: {ram_percent}%")

        return {
            "verdict": verdict,
            "cpu_percent": cpu_percent,
            "ram_percent": ram_percent,
            "ram_used_gb": ram_used_gb,
            "ram_total_gb": ram_total_gb,
            "ollama_alive": ollama_alive,
            "ollama_models": ollama_models,
            "warnings": warnings,
            "timestamp": datetime.now().isoformat(),
        }


CONTEXT_KEYWORDS = {
    "EXPANSION_CODE": ["code", "optimiser", "refactor", "bug", "python", "fonction"],
    "AUDIT_STRUCTURE": ["fichier", "structure", "nettoyer", "organiser", "tmp", "log"],
    "VEILLE_SILENCIEUSE": ["recherche", "apprendre", "astuce", "documentation", "veille"],
    "DROPZONE_SCAN": ["dropzone", "fichier", "import", "ingestion", "upload"],
}


class RoutineScorer:
    """Scoring déterministe des routines autonomes. Pas de LLM."""

    @staticmethod
    def score_routines(routines: list, recent_context: list, routine_history: list,
                       dropzone_count: int = 0, health_verdict: str = "GO") -> list:
        """
        Retourne une liste de (routine, score) triée par score décroissant.
        """
        scored = []

        # Extraire les intents récents depuis l'historique (fenêtre élargie à 5)
        recent_intents = [h["intent"] for h in routine_history[-5:]] if routine_history else []

        # Contexte sous forme de mots
        context_text = " ".join(recent_context).lower()

        for routine in routines:
            intent = routine["intent"]
            score = 1.0

            # Context bonus : mots-clés du contexte matchent l'intent
            keywords = CONTEXT_KEYWORDS.get(intent, [])
            matches = sum(1 for kw in keywords if kw in context_text)
            context_bonus = min(matches * 0.4, 2.0)
            score += context_bonus

            # Reactivity bonus : fichiers en dropzone
            if intent == "DROPZONE_SCAN" and dropzone_count > 0:
                score += 3.0

            # Repetition penalty : basée sur le TOTAL d'occurrences récentes (pas juste consécutives)
            total_recent = sum(1 for h in recent_intents if h == intent)
            if total_recent >= 3:
                score -= 3.0
            elif total_recent == 2:
                score -= 1.5
            elif total_recent == 1:
                score -= 0.5

            # Health penalty : si DEGRADED, pénaliser les routines lourdes
            if health_verdict == "DEGRADED" and intent == "EXPANSION_CODE":
                score -= 1.5

            # Jitter aléatoire pour casser les égalités et favoriser la diversité
            score += random.uniform(-0.3, 0.3)

            scored.append((routine, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored


class AutonomyStatePersistence:
    """Lecture/écriture JSON atomique pour l'état du moteur d'autonomie."""

    DEFAULT_STATE = {
        "version": "24.0",
        "daily_count": 0,
        "last_reset_day": None,
        "routine_history": [],
        "last_health_check": None,
        "error_streak": 0,
        "total_routines_executed": 0,
    }

    @staticmethod
    def load(path: str = None) -> dict:
        path = path or STATE_FILE
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data
        except (FileNotFoundError, json.JSONDecodeError):
            return dict(AutonomyStatePersistence.DEFAULT_STATE)

    @staticmethod
    def save(state: dict, path: str = None):
        path = path or STATE_FILE
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)


class AutonomyEngine:
    """
    AutonomyEngine V24.0 (Health-Aware Sentinel)
    - Health check système avant chaque routine (CPU, RAM, Ollama)
    - Scoring intelligent au lieu de random.choice
    - Persistance de l'état entre restarts
    - Heartbeat publié sur le bus à chaque cycle
    - Conserve : verrou is_processing, cooldown 30s, budget quotidien, kill_switch
    """
    def __init__(self, idle_threshold_seconds=300):
        self.idle_threshold = idle_threshold_seconds
        self.last_user_interaction = time.time()
        self.is_running = False
        self.is_processing = False  # VERROU DE SÉCURITÉ
        self.recent_context = []

        # Charger l'état persistant
        persisted = AutonomyStatePersistence.load()
        self.daily_count = persisted.get("daily_count", 0)
        last_day = persisted.get("last_reset_day")
        self.last_reset_day = date.fromisoformat(last_day) if last_day else date.today()
        self.routine_history = persisted.get("routine_history", [])
        self.error_streak = persisted.get("error_streak", 0)
        self.total_routines_executed = persisted.get("total_routines_executed", 0)
        self.last_health_check = persisted.get("last_health_check")

        bus.subscribe("USER_COMMAND", self.reset_timer)

    def _check_daily_budget(self) -> bool:
        """Vérifie et reset le compteur quotidien. Retourne True si budget disponible."""
        today = date.today()
        if today != self.last_reset_day:
            self.daily_count = 0
            self.last_reset_day = today

        if self.daily_count >= MAX_DAILY_ROUTINES:
            logger.warning(f"[AUTONOMY] Budget quotidien atteint ({MAX_DAILY_ROUTINES} routines). Pause jusqu'à demain.")
            return False
        return True

    def reset_timer(self, event):
        self.last_user_interaction = time.time()
        if "mission" in event:
            self.recent_context.append(event["mission"][:50])
            if len(self.recent_context) > 5: self.recent_context.pop(0)

    def _get_routines(self) -> list:
        return [
            {"agent": "evolution", "intent": "EXPANSION_CODE", "mission": "Analyse un fichier aléatoire. Propose une petite optimisation (typage/docstring)."},
            {"agent": "architect", "intent": "AUDIT_STRUCTURE", "mission": "Vérifie qu'aucun fichier temporaire (.tmp, .log) ne traîne à la racine."},
            {"agent": "researcher", "intent": "VEILLE_SILENCIEUSE", "mission": "Cherche une astuce Python 'One-Liner' utile et sauvegarde-la."},
            {"agent": "researcher", "intent": "DROPZONE_SCAN", "mission": "dropzone: Scanne la dropzone pour de nouveaux fichiers."},
        ]

    def _persist_state(self):
        state = {
            "version": "24.0",
            "daily_count": self.daily_count,
            "last_reset_day": self.last_reset_day.isoformat() if self.last_reset_day else None,
            "routine_history": self.routine_history,
            "last_health_check": self.last_health_check,
            "error_streak": self.error_streak,
            "total_routines_executed": self.total_routines_executed,
        }
        AutonomyStatePersistence.save(state)

    def _record_routine(self, agent: str, intent: str, status: str):
        self.routine_history.append({
            "agent": agent,
            "intent": intent,
            "status": status,
            "timestamp": datetime.now().isoformat(),
        })
        # FIFO max 20
        if len(self.routine_history) > 20:
            self.routine_history = self.routine_history[-20:]

    def get_status(self) -> dict:
        return {
            "version": "24.0",
            "is_running": self.is_running,
            "is_processing": self.is_processing,
            "daily_count": self.daily_count,
            "max_daily_routines": MAX_DAILY_ROUTINES,
            "last_reset_day": self.last_reset_day.isoformat() if self.last_reset_day else None,
            "error_streak": self.error_streak,
            "total_routines_executed": self.total_routines_executed,
            "routine_history": self.routine_history[-5:],
            "last_health_check": self.last_health_check,
            "recent_context": self.recent_context,
            "idle_threshold": self.idle_threshold,
        }

    async def _execute_scored_routine(self, health: dict):
        """Scoring → dispatch → record → persist."""
        routines = self._get_routines()

        # Compter les fichiers en dropzone
        try:
            from core.capabilities.dropzone_indexer import DropzoneIndexer
            dropzone_count = DropzoneIndexer().quick_count("USER_DROPZONE")
        except Exception:
            dropzone_count = 0

        scored = RoutineScorer.score_routines(
            routines=routines,
            recent_context=self.recent_context,
            routine_history=self.routine_history,
            dropzone_count=dropzone_count,
            health_verdict=health["verdict"],
        )

        selected, score = scored[0]
        agent = selected["agent"]
        intent = selected["intent"]

        print(f"   ✨ AUTONOMY: Routine [{intent}] (score={score:.1f}) -> [{agent.upper()}] ({self.daily_count + 1}/{MAX_DAILY_ROUTINES})")

        response = await orchestrator.dispatch_task(agent, {
            "mission": f"[MODE VEILLE] {selected['mission']}\nAgis de ta propre initiative.",
            "context": "PROTOCOLE_AUTONOMIE"
        })

        if response and response.get("status") == "success":
            print(f"   ✅ Fin Routine {agent.upper()}")
            self._record_routine(agent, intent, "success")
            self.error_streak = 0
        else:
            self._record_routine(agent, intent, "error")
            self.error_streak += 1

        self.daily_count += 1
        self.total_routines_executed += 1
        logger.info(f"[AUTONOMY] Routine {self.daily_count}/{MAX_DAILY_ROUTINES} du jour exécutée.")

    async def start_loop(self):
        self.is_running = True
        print(f"   🧠 AUTONOMY: Moteur V24 (Health-Aware Sentinel) activé. Limite: {MAX_DAILY_ROUTINES} routines/jour.")

        while self.is_running:
            # Sleep adaptatif : doublé si error_streak >= 3
            sleep_time = random.randint(600, 1200)
            if self.error_streak >= 3:
                sleep_time *= 2
                logger.warning(f"[AUTONOMY] Mode prudent (error_streak={self.error_streak}), sleep doublé: {sleep_time}s")

            await asyncio.sleep(sleep_time)

            if orchestrator.kill_switch_active or self.is_processing:
                continue

            if not self._check_daily_budget():
                continue

            idle_time = time.time() - self.last_user_interaction

            if idle_time > self.idle_threshold:
                # Health check
                try:
                    health = await SystemHealthCheck.run()
                except Exception as e:
                    health = {"verdict": "NO_GO", "warnings": [str(e)], "timestamp": datetime.now().isoformat(),
                              "cpu_percent": 0, "ram_percent": 0, "ollama_alive": False, "ollama_models": []}
                    logger.warning(f"[AUTONOMY] Health check échoué: {e}")

                self.last_health_check = health

                # Heartbeat publié à chaque cycle (même si NO_GO)
                await bus.publish("AUTONOMY_HEARTBEAT", {
                    "health": health,
                    "daily_count": self.daily_count,
                    "error_streak": self.error_streak,
                    "is_processing": self.is_processing,
                })

                if health["verdict"] == "NO_GO":
                    logger.warning(f"[AUTONOMY] NO_GO : {health.get('warnings', [])}. Routine annulée.")
                    self._persist_state()
                    continue

                self.is_processing = True  # ON VERROUILLE
                try:
                    await self._execute_scored_routine(health)
                except Exception as e:
                    logger.warning(f"[AUTONOMY] Erreur Routine: {e}")
                    self.error_streak += 1
                finally:
                    self._persist_state()
                    # COOLDOWN FORCÉ : 30s après une action avant de déverrouiller
                    await asyncio.sleep(30)
                    self.is_processing = False
                    self.last_user_interaction = time.time()


autonomy = AutonomyEngine(idle_threshold_seconds=300)
