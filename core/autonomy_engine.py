import asyncio
import copy
import time
import random
import logging
import json
import os
import uuid
from datetime import date, datetime
from core.orchestrator import orchestrator
from core.event_bus.bus import bus
from core.prompt_templates import AUTONOMY_GUARDRAIL

logger = logging.getLogger("AutonomyEngine")

# Limite quotidienne de routines autonomes
MAX_DAILY_ROUTINES = 80

# Budget quotidien en points (chaque routine a un coût différent)
DAILY_BUDGET_POINTS = 200

def _load_resource_costs() -> dict:
    """Charge les coûts par routine depuis config/resource_costs.json."""
    costs_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "config", "resource_costs.json")
    try:
        with open(costs_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {k: v["cost"] for k, v in data.items() if isinstance(v, dict) and "cost" in v}
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return {}

RESOURCE_COSTS = _load_resource_costs()

# Veille YouTube IA — rotation quand la dropzone est vide
# Veille silencieuse — rotation de sujets (évite les doublons)
VEILLE_TOPICS = [
    "Cherche une astuce Python 'One-Liner' utile et sauvegarde-la.",
    "Cherche une technique de debugging Python avancée (pdb, traceback, logging).",
    "Cherche un pattern de conception Python utile pour un système multi-agents.",
    "Cherche une astuce d'optimisation mémoire Python (generators, __slots__, weakref).",
    "Cherche une nouveauté récente de Python 3.12+ (typing, match/case, perf).",
    "Cherche une technique de gestion d'erreurs robuste en Python async.",
    "Cherche un outil Python utile pour le monitoring système (psutil, watchdog).",
    "Cherche une astuce FastAPI pour améliorer les performances ou la sécurité.",
]

YOUTUBE_AI_VEILLE = [
    {
        "query": "YouTube AI autonomous agents framework latest 2025 2026",
        "focus": "nouveaux frameworks d'agents IA autonomes présentés sur YouTube",
    },
    {
        "query": "YouTube local LLM Ollama llama optimization deployment 2025 2026",
        "focus": "techniques d'optimisation de LLMs locaux (Ollama, llama.cpp) vues sur YouTube",
    },
    {
        "query": "YouTube multi-agent AI orchestration system 2025 2026",
        "focus": "systèmes d'orchestration multi-agents IA présentés sur YouTube",
    },
    {
        "query": "YouTube RAG retrieval augmented generation vector database 2025 2026",
        "focus": "avancées en RAG et mémoire vectorielle partagées sur YouTube",
    },
    {
        "query": "YouTube AI coding assistant copilot new tools 2025 2026",
        "focus": "nouveaux outils d'assistance au codage IA présentés sur YouTube",
    },
    {
        "query": "YouTube open source AI model release breakthrough 2025 2026",
        "focus": "modèles IA open source récemment sortis et présentés sur YouTube",
    },
    {
        "query": "YouTube AI agent skills plugins MCP tools 2025 2026",
        "focus": "skills et plugins game-changer pour agents IA vus sur YouTube",
    },
    {
        "query": "YouTube AI self-improvement autonomous learning system 2025 2026",
        "focus": "systèmes IA capables d'auto-amélioration présentés sur YouTube",
    },
]

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

        # Ping ChromaDB (via lock async pour protéger la probe write/delete)
        memory_status = {"status": "unknown"}
        try:
            from core.vector_store import ChromaMemoryManager
            instances = ChromaMemoryManager._instances
            if instances:
                mgr = next(iter(instances.values()))
                memory_status = await mgr.async_check_health()
            else:
                memory_status = {"status": "down", "warnings": ["Aucune instance ChromaDB"]}
        except Exception as e:
            memory_status = {"status": "down", "warnings": [str(e)]}

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

        # Warning mémoire (ne bloque pas les routines)
        if memory_status.get("status") in ("degraded", "down"):
            warnings.append(f"Mémoire ChromaDB: {memory_status['status']}")

        return {
            "verdict": verdict,
            "cpu_percent": cpu_percent,
            "ram_percent": ram_percent,
            "ram_used_gb": ram_used_gb,
            "ram_total_gb": ram_total_gb,
            "ollama_alive": ollama_alive,
            "ollama_models": ollama_models,
            "memory": memory_status,
            "warnings": warnings,
            "timestamp": datetime.now().isoformat(),
        }


CONTEXT_KEYWORDS = {
    "EXPANSION_CODE": ["code", "optimiser", "refactor", "bug", "python", "fonction"],
    "AUDIT_STRUCTURE": ["fichier", "structure", "nettoyer", "organiser", "tmp", "log"],
    "VEILLE_SILENCIEUSE": ["recherche", "apprendre", "astuce", "documentation", "veille"],
    "DROPZONE_SCAN": ["dropzone", "fichier", "import", "ingestion", "upload"],
    "GRIMOIRE_INVOKE": ["grimoire", "éphémère", "recette", "spécialiste", "debug", "analyse"],
    "SECURITY_AUDIT": ["sécurité", "vulnérabilité", "injection", "risque", "audit"],
    "MEMORY_CLEANUP": ["mémoire", "nettoyage", "ancien", "doublon", "rag"],
    "REFACTOR_RANDOM": ["refactoring", "simplifier", "lisibilité", "dette", "technique"],
    "MEMORY_CONSOLIDATION": ["consolidation", "synthèse", "résumé", "regrouper", "mémoire"],
    "SOLILOQUE_INTERNE": ["soliloque", "dialogue", "introspection", "connexion", "réflexion", "compagnon"],
}


class RoutineScorer:
    """Scoring déterministe des routines autonomes. Pas de LLM."""

    @staticmethod
    def score_routines(routines: list, recent_context: list, routine_history: list,
                       dropzone_count: int = 0, health_verdict: str = "GO",
                       personality_bias: dict = None,
                       cloud_in_cooldown: bool = False) -> list:
        """
        Retourne une liste de (routine, score) triée par score décroissant.
        personality_bias: dict optionnel {intent: float} provenant de PsycheEngine.
        cloud_in_cooldown: True si le Cloud est en cooldown 429 (pénalise les routines lourdes).
        """
        scored = []

        # Extraire les intents récents depuis l'historique (fenêtre élargie à 10)
        recent_intents = [h["intent"] for h in routine_history[-10:]] if routine_history else []

        # Contexte sous forme de mots
        context_text = " ".join(recent_context).lower()

        # Timestamp courant pour le cooldown temporel
        now = datetime.now()

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

            # Repetition penalty : basée sur le TOTAL d'occurrences récentes (fenêtre 10)
            total_recent = sum(1 for h in recent_intents if h == intent)
            if total_recent >= 4:
                score -= 5.0
            elif total_recent >= 3:
                score -= 3.0
            elif total_recent == 2:
                score -= 1.5
            elif total_recent == 1:
                score -= 0.5

            # Cooldown temporel : pénaliser si le même intent a été exécuté récemment
            for h in reversed(routine_history):
                if h["intent"] == intent and "timestamp" in h:
                    try:
                        last_exec = datetime.fromisoformat(h["timestamp"])
                        hours_ago = (now - last_exec).total_seconds() / 3600
                        if hours_ago < 2:
                            score -= 5.0  # Quasi-bloquant dans les 2 premières heures
                        elif hours_ago < 4:
                            score -= 2.5
                        elif hours_ago < 6:
                            score -= 1.0
                    except (ValueError, TypeError):
                        pass
                    break  # Seule la dernière occurrence compte

            # Health penalty : si DEGRADED, pénaliser les routines lourdes
            if health_verdict == "DEGRADED" and intent == "EXPANSION_CODE":
                score -= 1.5

            # Cloud cooldown penalty : pénaliser les routines qui hallucinent en local
            if cloud_in_cooldown and intent in ("EXPANSION_CODE", "REFACTOR_RANDOM"):
                score -= 10.0

            # Personality bias (PSYCHE) : bonus/malus basé sur les traits du système (clampé [-2, +2])
            if personality_bias and intent in personality_bias:
                score += max(-2.0, min(2.0, personality_bias[intent]))

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
        "daily_budget_used": 0,
        "last_reset_day": None,
        "routine_history": [],
        "last_health_check": None,
        "error_streak": 0,
        "total_routines_executed": 0,
        "learning_history": {},
        "security_audited_files": {},
    }

    @staticmethod
    def load(path: str = None) -> dict:
        path = path or STATE_FILE
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data
        except (FileNotFoundError, json.JSONDecodeError):
            return copy.deepcopy(AutonomyStatePersistence.DEFAULT_STATE)

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
        # Historique d'apprentissage ciblé {topic: timestamp_iso}
        self._learning_history: dict = persisted.get("learning_history", {})
        # Flag : max 1 apprentissage par cycle de routine
        self._learning_done_this_cycle = False
        # Budget en points (chaque routine a un coût différent)
        self.daily_budget_used = persisted.get("daily_budget_used", 0)
        # Cache des fichiers déjà audités par Security {filename: timestamp}
        self._security_audited_files: dict = persisted.get("security_audited_files", {})
        # Loop breaker : intents a eviter au prochain cycle
        self._temp_blacklist: set = set()
        # Loop breaker : intent force par le loop_breaker (bypass scoring)
        self._forced_next_intent: str = ""
        # Transients pour feedback council/grimoire
        self._current_council_subject: str = ""
        self._last_grimoire_slug: str = ""

        bus.subscribe("USER_COMMAND", self.reset_timer)

    def _check_daily_budget(self) -> bool:
        """Vérifie et reset le compteur quotidien. Retourne True si budget disponible."""
        today = date.today()
        if today != self.last_reset_day:
            self.daily_count = 0
            self.daily_budget_used = 0
            self.last_reset_day = today
            self._persist_state()

            # Bilan et seed objectifs quotidiens
            try:
                from core.objectives_engine import objectives as obj_engine
                obj_engine.generate_daily_report()
                obj_engine.seed_daily_objectives()
            except Exception as e:
                logger.warning(f"[AUTONOMY] Objectifs daily reset échoué: {e}")

        if self.daily_count >= MAX_DAILY_ROUTINES:
            logger.warning(f"[AUTONOMY] Budget quotidien atteint ({MAX_DAILY_ROUTINES} routines). Pause jusqu'à demain.")
            return False
        if self.daily_budget_used >= DAILY_BUDGET_POINTS:
            logger.warning(f"[AUTONOMY] Budget points épuisé ({self.daily_budget_used}/{DAILY_BUDGET_POINTS}). Pause jusqu'à demain.")
            return False
        return True

    def reset_timer(self, event):
        self.last_user_interaction = time.time()
        if "mission" in event:
            self.recent_context.append(event["mission"][:50])
            if len(self.recent_context) > 5: self.recent_context.pop(0)

    def _get_routines(self) -> list:
        # Rotation du sujet de veille silencieuse
        veille_index = self.total_routines_executed % len(VEILLE_TOPICS)
        veille_mission = f"[MODE VEILLE] {VEILLE_TOPICS[veille_index]}"

        return [
            {"agent": "evolution", "intent": "EXPANSION_CODE", "mission": "Analyse un fichier aléatoire. Propose une petite optimisation (typage/docstring)."},
            {"agent": "architect", "intent": "AUDIT_STRUCTURE", "mission": "Vérifie qu'aucun fichier temporaire (.tmp, .log) ne traîne à la racine."},
            {"agent": "researcher", "intent": "VEILLE_SILENCIEUSE", "mission": veille_mission},
            {"agent": "researcher", "intent": "DROPZONE_SCAN", "mission": "dropzone: Scanne la dropzone pour de nouveaux fichiers."},
            {"agent": "_council", "intent": "COUNCIL_DEBATE", "mission": "Débat autonome entre agents."},
            {"agent": "_grimoire", "intent": "GRIMOIRE_INVOKE", "mission": "Invoque un agent éphémère du Grimoire."},
            {"agent": "security", "intent": "SECURITY_AUDIT", "mission": "Audite un module aléatoire du projet pour des vulnérabilités (injection, eval, subprocess, fichiers non sanitisés)."},
            {"agent": "_memory_cleanup", "intent": "MEMORY_CLEANUP", "mission": "Nettoie la mémoire RAG ancienne et les doublons."},
            {"agent": "coder", "intent": "REFACTOR_RANDOM", "mission": "Choisis un fichier Python aléatoire du projet et propose un refactoring pour améliorer la lisibilité (noms de variables, simplification de logique)."},
            {"agent": "_memory_consolidation", "intent": "MEMORY_CONSOLIDATION", "mission": "Consolide les mémoires récentes en synthèses thématiques."},
            {"agent": "_soliloque", "intent": "SOLILOQUE_INTERNE", "mission": "Engage un dialogue introspectif avec le compagnon intérieur."},
        ]

    def _persist_state(self):
        state = {
            "version": "24.0",
            "daily_count": self.daily_count,
            "daily_budget_used": self.daily_budget_used,
            "last_reset_day": self.last_reset_day.isoformat() if self.last_reset_day else None,
            "routine_history": self.routine_history,
            "last_health_check": self.last_health_check,
            "error_streak": self.error_streak,
            "total_routines_executed": self.total_routines_executed,
            "learning_history": self._learning_history,
            "security_audited_files": self._security_audited_files,
        }
        AutonomyStatePersistence.save(state)

    def _analyze_result_text(self, result_text: str) -> dict:
        """Analyse partagée du texte de résultat : non-latin ratio + répétition.

        Retourne {"non_latin_ratio": float, "is_repetition": bool}.
        """
        # Ratio non-latin
        non_latin_ratio = 0.0
        alpha_chars = [c for c in result_text if c.isalpha()]
        if alpha_chars:
            non_latin = sum(1 for c in alpha_chars if ord(c) > 0x024F)
            non_latin_ratio = non_latin / len(alpha_chars)

        # Répétition avec les résultats précédents
        is_repetition = False
        recent_previews = [
            str(h.get("result_preview", ""))
            for h in self.routine_history[-5:]
            if h.get("result_preview")
        ]
        for prev in recent_previews:
            if prev and len(result_text) >= 200 and result_text[:200] == prev[:200]:
                is_repetition = True
                break

        return {"non_latin_ratio": non_latin_ratio, "is_repetition": is_repetition}

    def _score_result_quality(self, response: dict, intent: str) -> float:
        """Score qualité du résultat d'une routine (0.0 = garbage, 1.0 = excellent)."""
        if not response or not isinstance(response, dict):
            return 0.0

        result_text = str(response.get("result", ""))
        score = 1.0

        # 1. Pénalité longueur : résultat vide ou très court
        stripped_len = len(result_text.strip())
        if stripped_len < 20:
            return 0.0
        elif stripped_len < 50:
            score -= 0.4
        elif stripped_len < 100:
            score -= 0.2

        # 2-3. Analyse partagée (non-latin + répétition)
        analysis = self._analyze_result_text(result_text)
        if analysis["non_latin_ratio"] > 0.15:
            score -= 0.5
        elif analysis["non_latin_ratio"] > 0.05:
            score -= 0.2
        if analysis["is_repetition"]:
            score -= 0.4

        return max(0.0, min(1.0, score))

    def _diagnose_failure(self, response: dict, quality_score: float, intent: str) -> str:
        """Diagnostique le TYPE d'échec : hallucination, repetition, ignorance, technical."""
        if not response or not isinstance(response, dict):
            return "technical"

        result_text = str(response.get("result", ""))

        # 1-2. Analyse partagée (non-latin + répétition)
        analysis = self._analyze_result_text(result_text)
        if analysis["non_latin_ratio"] > 0.15:
            return "hallucination"
        if analysis["is_repetition"]:
            return "repetition"

        # 3. Ignorance : résultat court/vague + patterns linguistiques
        stripped = result_text.strip()
        if len(stripped) < 10:
            return "technical"

        # Routines sans LLM : un résultat court est normal, pas de l'ignorance
        no_llm_intents = {"AUDIT_STRUCTURE", "MEMORY_CLEANUP"}
        if intent in no_llm_intents:
            return "technical"

        ignorance_markers = [
            "je ne sais pas", "aucune information", "pas d'information",
            "je n'ai pas", "impossible de", "je ne peux pas",
            "hors de mes compétences", "pas de données",
            "i don't know", "no information", "unable to",
        ]
        lower_text = result_text.lower()
        has_ignorance_marker = any(m in lower_text for m in ignorance_markers)
        is_short = len(stripped) < 150
        if has_ignorance_marker or (is_short and quality_score < 0.4):
            return "ignorance"

        # 4. Défaut : technique
        return "technical"

    async def _trigger_targeted_learning(self, mission: str, agent: str, intent: str):
        """Déclenche un apprentissage ciblé quand failure_type == 'ignorance'."""
        # Garde-fou : pas d'apprentissage si le Researcher lui-même a échoué
        if agent == "researcher":
            return

        # Garde-fou : max 1 apprentissage par cycle
        if self._learning_done_this_cycle:
            return

        # Extraire le sujet de la mission (les 100 premiers caractères significatifs)
        topic = mission[:100].strip()
        if topic.startswith("[MODE VEILLE]"):
            topic = topic[len("[MODE VEILLE]"):].strip()

        # Garde-fou : cooldown 2h sur le même sujet
        if topic in self._learning_history:
            try:
                last_learn = datetime.fromisoformat(self._learning_history[topic])
                hours_ago = (datetime.now() - last_learn).total_seconds() / 3600
                if hours_ago < 2:
                    logger.info(f"[AUTONOMY] Apprentissage en cooldown pour: {topic[:50]}...")
                    return
            except (ValueError, TypeError):
                pass

        # Dispatcher une recherche ciblée au Researcher
        print(f"   📚 APPRENTISSAGE: Recherche ciblée sur '{topic[:60]}...'")
        try:
            result = await orchestrator.dispatch_task("researcher", {
                "mission": f"[APPRENTISSAGE CIBLÉ] Recherche approfondie sur: {topic}",
                "context": (
                    "APPRENTISSAGE_CIBLE — Le système a détecté une lacune de connaissance "
                    "sur ce sujet. Recherche des informations pertinentes et sauvegarde-les "
                    "en mémoire (remember) pour que les agents puissent les utiliser à l'avenir."
                ),
                "force_local": True,
            })
            self._learning_history[topic] = datetime.now().isoformat()
            self._learning_done_this_cycle = True

            # Enregistrer le gap dans la conscience de soi
            try:
                from core.self_awareness import awareness
                awareness.record_knowledge_gap(topic, intent)
                # Ne marquer comme appris que si le Researcher a réellement répondu
                researcher_ok = (
                    result and isinstance(result, dict)
                    and result.get("status") == "success"
                    and len(str(result.get("result", ""))) > 50
                )
                if researcher_ok:
                    awareness.mark_gap_learned(topic)
            except Exception:
                pass

            logger.info(f"[AUTONOMY] Apprentissage ciblé terminé: {topic[:50]}")
        except Exception as e:
            logger.warning(f"[AUTONOMY] Apprentissage ciblé échoué: {e}")

    def _record_routine(self, agent: str, intent: str, status: str, subject: str = "",
                        quality_score: float = 0.0, failure_type: str = None,
                        result_preview: str = "", grimoire_slug: str = ""):
        entry = {
            "agent": agent,
            "intent": intent,
            "status": status,
            "timestamp": datetime.now().isoformat(),
            "quality_score": quality_score,
        }
        if subject:
            entry["subject"] = subject
        if failure_type:
            entry["failure_type"] = failure_type
        if result_preview:
            entry["result_preview"] = result_preview[:200]
        if grimoire_slug:
            entry["grimoire_slug"] = grimoire_slug
        self.routine_history.append(entry)
        # FIFO max 40 (étendu pour l'analyse temporelle)
        if len(self.routine_history) > 40:
            self.routine_history = self.routine_history[-40:]

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
        # Loop breaker : si un intent est force, bypass le scoring
        if self._forced_next_intent:
            forced = self._forced_next_intent
            self._forced_next_intent = ""
            routines = self._get_routines()
            forced_routine = next((r for r in routines if r["intent"] == forced), None)
            if forced_routine:
                print(f"   🔀 LOOP_BREAKER: Intent force -> [{forced}]")
                # Deleguer l'execution directe (sauter tout le scoring)
                return await self._execute_forced_routine(forced_routine, health)
            else:
                logger.warning(f"[AUTONOMY] Intent forcé '{forced}' introuvable dans les routines, fallback au scoring normal.")

        routines = self._get_routines()

        # Loop breaker : filtrer les routines blacklistees
        if self._temp_blacklist:
            filtered = [r for r in routines if r["intent"] not in self._temp_blacklist]
            if filtered:
                blacklisted = self._temp_blacklist.copy()
                self._temp_blacklist.clear()
                print(f"   🚫 LOOP_BREAKER: Blacklist temporaire: {', '.join(blacklisted)}")
                routines = filtered
            else:
                self._temp_blacklist.clear()  # Eviter de bloquer tout

        # Compter les fichiers en dropzone
        try:
            from core.capabilities.dropzone_indexer import DropzoneIndexer
            dropzone_count = DropzoneIndexer().quick_count("USER_DROPZONE")
        except Exception:
            dropzone_count = 0

        # Personality bias (PSYCHE)
        personality_bias = {}
        try:
            from core.psyche import psyche
            for r in routines:
                bias = psyche.compute_personality_bias(r["intent"])
                if bias != 0.0:
                    personality_bias[r["intent"]] = bias
            # Decay quotidien (vérifié 1x/jour par le moteur)
            psyche.apply_daily_decay()
        except Exception:
            pass

        # Détecter si le Cloud est en cooldown 429
        cloud_in_cooldown = False
        try:
            from core.base_agent import BaseAgent
            cloud_in_cooldown = time.time() < BaseAgent._cloud_cooldown_until
        except Exception:
            pass

        scored = RoutineScorer.score_routines(
            routines=routines,
            recent_context=self.recent_context,
            routine_history=self.routine_history,
            dropzone_count=dropzone_count,
            health_verdict=health["verdict"],
            personality_bias=personality_bias,
            cloud_in_cooldown=cloud_in_cooldown,
        )

        # --- Bonus objectifs ---
        try:
            from core.objectives_engine import objectives as obj_engine
            for i, (routine, s) in enumerate(scored):
                obj_bonus = obj_engine.get_routine_bonus(routine["intent"])
                scored[i] = (routine, s + obj_bonus)
        except Exception:
            pass

        # --- Ajustements adaptatifs (conscience de soi) ---
        # Clamping : les ajustements cumulatifs sont bornés pour éviter les valeurs extrêmes
        ADAPTIVE_CLAMP_MIN = -10.0
        ADAPTIVE_CLAMP_MAX = 5.0
        adaptive_adjustments = {}
        try:
            from core.self_awareness import awareness
            raw_adjustments = awareness.compute_adaptive_scoring(self.routine_history)
            # Clamping des ajustements dans [min, max]
            adaptive_adjustments = {
                intent: max(ADAPTIVE_CLAMP_MIN, min(ADAPTIVE_CLAMP_MAX, val))
                for intent, val in raw_adjustments.items()
            }
            if adaptive_adjustments:
                for i, (routine, s) in enumerate(scored):
                    adj = adaptive_adjustments.get(routine["intent"], 0.0)
                    if adj != 0.0:
                        scored[i] = (routine, s + adj)
                # Log des ajustements actifs
                active = {k: v for k, v in adaptive_adjustments.items() if v != 0.0}
                if active:
                    parts = [f"{k}:{v:+.1f}" for k, v in active.items()]
                    print(f"   🧠 CONSCIENCE: Ajustements adaptatifs: {', '.join(parts)}")
        except Exception:
            pass

        # --- Bonus spreading activation (affinité sémantique) ---
        try:
            from core.spreading_activation import activation_engine as sa_engine
            for i, (routine, s) in enumerate(scored):
                sa_bonus = sa_engine.compute_routine_affinity(routine["intent"])
                if sa_bonus != 0.0:
                    scored[i] = (routine, s + sa_bonus)
        except Exception:
            pass

        # --- Bonus cortex synaptique (associations persistantes) ---
        try:
            from core.synaptic_network import cortex
            for i, (routine, s) in enumerate(scored):
                syn_bonus = cortex.compute_routine_affinity(routine["intent"])
                if syn_bonus != 0.0:
                    scored[i] = (routine, s + syn_bonus)
        except Exception:
            pass

        # --- Bonus pulsions (desirs) ---
        try:
            from core.desire_engine import desires
            desires.tick()
            for i, (routine, s) in enumerate(scored):
                desire_bonus = desires.compute_desire_bonus(routine["intent"])
                if desire_bonus > 0:
                    scored[i] = (routine, s + desire_bonus)
            urgent = [d.name for d in desires.drives.values() if d.deprivation >= 75]
            if urgent:
                print(f"   \U0001FA90 DESIRS: Pulsions urgentes: {', '.join(urgent)}")
        except Exception:
            pass

        # --- Bonus somatique (intuitions viscérales du coeur) ---
        try:
            from core.cardiac_engine import heart
            for i, (routine, s) in enumerate(scored):
                somatic = heart.get_somatic_signal(routine["intent"])
                if somatic != 0.0:
                    scored[i] = (routine, s + somatic)
        except Exception:
            pass

        # --- Bonus préfrontal (focus exécutif) ---
        try:
            from core.prefrontal import prefrontal
            for i, (routine, s) in enumerate(scored):
                focus = prefrontal.compute_focus_bonus(routine["intent"])
                if focus != 0.0:
                    scored[i] = (routine, s + focus)
            wm = prefrontal.get_working_memory()
            if wm:
                print(f"   🎯 PRÉFRONTAL: Focus sur '{wm[0]['goal_title']}' ({wm[0]['progress']:.0%})")
        except Exception:
            pass

        # --- Bonus voix intérieure (influence cognitive — Couche 8) ---
        try:
            from core.inner_voice import voice as inner_voice
            for i, (routine, s) in enumerate(scored):
                voice_bonus = inner_voice.compute_voice_bonus(routine["intent"])
                if voice_bonus != 0.0:
                    scored[i] = (routine, s + voice_bonus)
        except Exception:
            pass

        # --- Bonus dopaminique (motivation — Couche 9) ---
        try:
            from core.dopamine_system import dopamine
            for i, (routine, s) in enumerate(scored):
                dopa_bonus = dopamine.compute_motivation_bonus(routine["intent"])
                if dopa_bonus != 0.0:
                    scored[i] = (routine, s + dopa_bonus)
            scored.sort(key=lambda x: x[1], reverse=True)
        except Exception:
            pass

        if not scored:
            logger.warning("[AUTONOMY] Aucune routine disponible apres filtrage. Cycle avorte.")
            self._persist_state()
            return

        selected, score = scored[0]
        agent = selected["agent"]
        intent = selected["intent"]

        # --- Veto proactif ---
        veto_reason = self._should_veto(intent, agent)
        if veto_reason:
            # Le préfrontal peut overrider certains vetos (SHED, FLINCH) si un goal est avancé
            overridden = False
            try:
                from core.prefrontal import prefrontal
                inhibition = prefrontal.compute_inhibition(intent, veto_reason)
                if inhibition["action"] == "override":
                    print(f"   🧠 PRÉFRONTAL: Override {inhibition['override_target']} — {inhibition['reason']}")
                    veto_reason = ""  # Annuler le veto
                    overridden = True
            except Exception:
                pass
            if veto_reason:
                print(f"   🚫 VETO: {veto_reason}")
                try:
                    from core.cardiac_engine import heart
                    heart.react("veto")
                except Exception:
                    pass
                # Fallback : prendre la prochaine routine non-vetoed
                fallback_found = False
                for alt_selected, alt_score in scored[1:]:
                    alt_intent = alt_selected["intent"]
                    alt_agent = alt_selected["agent"]
                    alt_veto = self._should_veto(alt_intent, alt_agent)
                    if not alt_veto:
                        selected, score = alt_selected, alt_score
                        agent = selected["agent"]
                        intent = alt_intent
                        fallback_found = True
                        break
                if not fallback_found:
                    return  # Aucune alternative non-vetoed

        routine_cost_preview = RESOURCE_COSTS.get(intent, 2)
        print(f"   ✨ AUTONOMY: Routine [{intent}] (score={score:.1f}, coût={routine_cost_preview}pt) -> [{agent.upper()}] ({self.daily_count + 1}/{MAX_DAILY_ROUTINES}, budget: {self.daily_budget_used}/{DAILY_BUDGET_POINTS}pt)")

        # Notification préfrontale pre-routine
        try:
            from core.prefrontal import prefrontal
            prefrontal.on_routine_start(intent)
        except Exception:
            pass
        # Voix intérieure : routine commence → désactiver DMN
        try:
            from core.inner_voice import voice as inner_voice
            inner_voice.set_idle(False)
        except Exception:
            pass

        # Annonce de l'objectif associé
        try:
            from core.objectives_engine import objectives as obj_engine
            best_affinity = 0.0
            best_obj = None
            for obj in obj_engine.get_active_objectives():
                affinity = obj.get("routine_affinities", {}).get(intent, 0.0)
                if affinity > best_affinity:
                    best_affinity = affinity
                    best_obj = obj
            if best_obj:
                print(f"   🎯 Contribue à: {best_obj['title']} ({best_obj['progress']:.0%})")
        except Exception:
            pass

        # Gestion spéciale des routines non-standard
        if intent == "COUNCIL_DEBATE":
            response = await self._execute_council_debate()
        elif intent == "GRIMOIRE_INVOKE":
            response = await self._execute_grimoire_routine()
        elif intent == "MEMORY_CLEANUP":
            response = await self._execute_memory_cleanup()
        elif intent == "SECURITY_AUDIT":
            response = await self._execute_security_audit()
        elif intent == "AUDIT_STRUCTURE":
            response = await self._execute_audit_structure()
        elif intent == "REFACTOR_RANDOM":
            response = await self._execute_refactor_random()
        elif intent == "MEMORY_CONSOLIDATION":
            response = await self._execute_memory_consolidation()
        elif intent == "SOLILOQUE_INTERNE":
            response = await self._execute_soliloque()
        elif intent == "DROPZONE_SCAN" and dropzone_count == 0:
            # Dropzone vide → veille YouTube IA (rotation des sujets)
            yt_index = self.total_routines_executed % len(YOUTUBE_AI_VEILLE)
            yt_topic = YOUTUBE_AI_VEILLE[yt_index]
            print(f"   📺 DROPZONE vide → Veille YouTube IA: {yt_topic['focus'][:60]}...")
            response = await orchestrator.dispatch_task("researcher", {
                "mission": f"VEILLE YOUTUBE IA: Recherche des vidéos YouTube récentes sur: {yt_topic['query']}",
                "context": (
                    "YOUTUBE_VEILLE — La dropzone est vide. "
                    f"Cherche sur le web des vidéos YouTube récentes sur: {yt_topic['focus']}. "
                    "Résume les 2-3 découvertes les plus pertinentes pour un système multi-agents "
                    "autonome comme Prométhée. Sauvegarde les trouvailles en mémoire."
                ),
                "force_local": True,
            })
            # Enregistrer la veille YouTube dans le journal stratégique
            try:
                from core.strategic_journal import journal as strat_journal
                strat_journal.append_research_entry(
                    topic=yt_topic["focus"],
                    findings=response.get("result", "") if response else "",
                    source="YouTube",
                )
            except Exception as e:
                logger.warning(f"[AUTONOMY] Écriture journal veille échouée: {e}")
        else:
            # Injection du purpose_context dans les missions autonomes standard
            purpose_ctx = ""
            try:
                from core.self_awareness import awareness
                purpose_ctx = awareness.get_purpose_context()
            except Exception:
                pass
            # Enrichir avec le narratif interieur (pulsions)
            try:
                from core.desire_engine import desires
                narrative = desires.get_dominant_narrative()
                if narrative:
                    purpose_ctx += f"\n[DESIRS] {narrative}"
            except Exception:
                pass
            # Contexte délibératif (objectifs préfrontaux)
            try:
                from core.prefrontal import prefrontal
                delib_ctx = prefrontal.get_deliberation_context()
                if delib_ctx:
                    purpose_ctx += f"\n{delib_ctx}"
            except Exception:
                pass
            # Voix intérieure (flux de conscience)
            try:
                from core.inner_voice import voice as inner_voice
                voice_ctx = inner_voice.get_voice_context()
                if voice_ctx:
                    purpose_ctx += f"\n{voice_ctx}"
            except Exception:
                pass
            # Mission propre (sans wrapper ni guardrail — évite la fuite de prompt dans les recherches web)
            raw_mission = selected["mission"]
            # Retirer le préfixe [MODE VEILLE] déjà présent dans certaines missions
            clean_mission = raw_mission.replace("[MODE VEILLE] ", "").replace("[MODE VEILLE]", "").strip()
            mission_text = f"[MODE VEILLE] {clean_mission}\nAgis de ta propre initiative."
            # Guardrails et purpose dans le context (pas dans la mission envoyée aux moteurs de recherche)
            context_parts = ["PROTOCOLE_AUTONOMIE"]
            if purpose_ctx and isinstance(purpose_ctx, str):
                context_parts.append(purpose_ctx)
            context_parts.append(AUTONOMY_GUARDRAIL)
            response = await orchestrator.dispatch_task(agent, {
                "mission": mission_text,
                "context": "\n".join(context_parts),
                "force_local": True,
            })

        # --- Guard : routines "skipped" (council saturé, etc.) — pas de budget consommé ---
        if response and response.get("status") == "skipped":
            reason = response.get("reason", "unknown")
            print(f"   ⏭️ Routine {intent} skippée ({reason})")
            self._record_routine(agent, intent, "skipped", quality_score=0.0)
            # Pas d'incrémentation daily_count, budget, error_streak
            return

        # Sujet du council (pour la déduplication)
        council_subject = getattr(self, "_current_council_subject", "")

        # Slug Grimoire réel (pour la rotation)
        grimoire_slug = getattr(self, "_last_grimoire_slug", "")
        self._last_grimoire_slug = ""

        # Score qualité post-routine
        quality_score = self._score_result_quality(response, intent)

        # Feedback reptilien (apaisement si succès — pas de handler bus dupliqué)
        try:
            from core.reptilian_core import reptile
            if quality_score >= 0.6:
                reptile.on_routine_success(intent)
        except Exception:
            pass

        # Aperçu du résultat pour comparaison future
        result_preview = ""
        if response and isinstance(response, dict):
            result_preview = str(response.get("result", ""))[:200]

        # NOTE: desires, cardiac, prefrontal recoivent le feedback via le bus
        # (AUTONOMY_ROUTINE_COMPLETE) — pas d'appel direct pour eviter le double-comptage.
        # desires.save() est appele via le handler bus _on_routine_complete.

        # Voix intérieure : routine terminée → réactiver DMN
        try:
            from core.inner_voice import voice as inner_voice
            inner_voice.set_idle(True)
        except Exception:
            pass

        # Reset du flag d'apprentissage pour ce cycle
        self._learning_done_this_cycle = False
        failure_type = ""

        if response and response.get("status") in ("success", "consensus", "max_rounds"):
            # Distinguer consensus réel vs max_rounds (timeout sans accord)
            actual_status = response.get("status", "success")
            is_max_rounds = actual_status == "max_rounds"

            if quality_score < 0.3:
                # Succès technique mais résultat de mauvaise qualité
                failure_type = self._diagnose_failure(response, quality_score, intent)
                record_status = "max_rounds_low" if is_max_rounds else "low_quality"
                print(f"   ⚠️ Routine {intent} terminée mais qualité basse ({quality_score:.2f}) [{failure_type}]")
                self._record_routine(agent, intent, record_status, subject=council_subject,
                                     quality_score=quality_score, failure_type=failure_type,
                                     result_preview=result_preview, grimoire_slug=grimoire_slug)
                self.error_streak += 1
                # Apprentissage ciblé si ignorance détectée
                if failure_type == "ignorance":
                    try:
                        from core.self_awareness import awareness
                        awareness.record_knowledge_gap(selected["mission"][:100], intent)
                    except Exception:
                        pass
                    await self._trigger_targeted_learning(selected["mission"], agent, intent)
            else:
                record_status = "max_rounds" if is_max_rounds else "success"
                emoji = "⚖️" if is_max_rounds else "✅"
                print(f"   {emoji} Fin Routine {agent.upper() if agent != '_council' else 'COUNCIL'} (qualité: {quality_score:.2f}{', max_rounds' if is_max_rounds else ''})")
                self._record_routine(agent, intent, record_status, subject=council_subject,
                                     quality_score=quality_score, result_preview=result_preview,
                                     grimoire_slug=grimoire_slug)
                self.error_streak = 0
        else:
            failure_type = self._diagnose_failure(response, quality_score, intent)
            self._record_routine(agent, intent, "error", subject=council_subject,
                                 quality_score=quality_score, failure_type=failure_type,
                                 result_preview=result_preview, grimoire_slug=grimoire_slug)
            self.error_streak += 1
            # Apprentissage ciblé si ignorance détectée
            if failure_type == "ignorance":
                try:
                    from core.self_awareness import awareness
                    awareness.record_knowledge_gap(selected["mission"][:100], intent)
                except Exception:
                    pass
                await self._trigger_targeted_learning(selected["mission"], agent, intent)

        # --- Frustration DesireEngine : forcer l'intent suivant si pulsion frustrée ---
        if not self._forced_next_intent:
            try:
                from core.desire_engine import desires as _desires, DRIVE_ROUTINE_AFFINITY
                frustrated = [
                    (name, d) for name, d in _desires.drives.items()
                    if d.frustration_streak >= 4 and d.deprivation >= 70
                ]
                if frustrated:
                    drive_name, drive = frustrated[0]
                    forced_intent_map = DRIVE_ROUTINE_AFFINITY.get(drive_name, {})
                    if forced_intent_map:
                        best_intent = max(forced_intent_map, key=forced_intent_map.get)
                        self._forced_next_intent = best_intent
                        logger.warning(f"[EVEIL] Pulsion {drive_name} frustrée x{drive.frustration_streak} (dep={drive.deprivation:.0f}) → force {best_intent}")
            except Exception:
                pass

        # Loop breaker : si repetition ou error_streak eleve -> consulter le specialiste
        if failure_type == "repetition" or self.error_streak >= 5:
            try:
                loop_response = await orchestrator.dispatch_task("loop_breaker", {
                    "mission": "AIDE: loop",
                    "context": json.dumps({
                        "history": self.routine_history[-10:],
                        "error_streak": self.error_streak,
                    }, default=str)
                })
                if loop_response and isinstance(loop_response, dict):
                    loop_action = loop_response.get("action", "skip")
                    if loop_action == "skip" and loop_response.get("blacklist"):
                        self._temp_blacklist = set(loop_response["blacklist"])
                        print(f"   🔀 LOOP_BREAKER: Blacklist {self._temp_blacklist}")
                    elif loop_action == "redirect" and loop_response.get("forced_intent"):
                        self._forced_next_intent = loop_response["forced_intent"]
                        print(f"   🔀 LOOP_BREAKER: Redirect -> {self._forced_next_intent}")
                    elif loop_action == "cooldown":
                        extra = loop_response.get("extra_sleep", 120)
                        print(f"   ⏸️ LOOP_BREAKER: Cooldown {extra}s")
                        await asyncio.sleep(extra)
                    elif loop_action == "escalate":
                        print(f"   🚨 LOOP_BREAKER: Escalade Council recommandee (streak={self.error_streak})")
                        self._forced_next_intent = "COUNCIL_DEBATE"
            except Exception as e:
                logger.warning(f"[AUTONOMY] Loop breaker echoue: {e}")

        # Alimentation spreading activation (non bloquant)
        if result_preview and len(result_preview) > 50:
            try:
                from core.spreading_activation import activation_engine
                asyncio.create_task(
                    activation_engine.activate(
                        result_preview, "collective_wisdom", max_hops=0
                    )
                )
            except Exception:
                pass

        # Publier AUTONOMY_ROUTINE_COMPLETE pour les handlers PSYCHE
        participants = []
        if intent == "COUNCIL_DEBATE" and response:
            participants = response.get("participants", [])
        await bus.publish("AUTONOMY_ROUTINE_COMPLETE", {
            "intent": intent,
            "agent": agent,
            "participants": participants,
            "status": "success" if response and response.get("status") in ("success", "consensus") and quality_score >= 0.3 else "error",
            "quality_score": quality_score,
            "result": result_preview,
        })

        self.daily_count += 1
        self.total_routines_executed += 1
        # Décompter le coût en points de budget
        routine_cost = RESOURCE_COSTS.get(intent, 2)
        self.daily_budget_used += routine_cost
        logger.info(f"[AUTONOMY] Routine {self.daily_count}/{MAX_DAILY_ROUTINES} du jour (coût: {routine_cost}pt, budget: {self.daily_budget_used}/{DAILY_BUDGET_POINTS}pt)")

        # Snapshot conscience de soi périodique (toutes les 5 routines)
        if self.daily_count % 5 == 0:
            try:
                from core.self_awareness import awareness
                awareness.generate_snapshot()
            except Exception:
                pass
            # Decay + cleanup spreading activation
            try:
                from core.spreading_activation import activation_engine
                activation_engine.decay_all()
                activation_engine.cleanup()
            except Exception:
                pass

    async def _execute_forced_routine(self, routine: dict, health: dict):
        """Execute une routine forcee par le loop_breaker (bypass scoring)."""
        agent = routine["agent"]
        intent = routine["intent"]
        routine_cost = RESOURCE_COSTS.get(intent, 2)

        # Veto FREEZE — même forcée, un FREEZE reptilien bloque tout
        try:
            from core.reptilian_core import reptile
            if reptile.should_freeze():
                logger.warning(f"[AUTONOMY] Routine FORCED {intent} bloquée par FREEZE reptilien")
                return
        except Exception:
            pass

        print(f"   ✨ AUTONOMY [FORCED]: [{intent}] -> [{agent.upper()}] (cout={routine_cost}pt)")

        # Reutiliser la logique standard de dispatch
        if intent == "COUNCIL_DEBATE":
            response = await self._execute_council_debate()
        elif intent == "GRIMOIRE_INVOKE":
            response = await self._execute_grimoire_routine()
        elif intent == "MEMORY_CLEANUP":
            response = await self._execute_memory_cleanup()
        elif intent == "SECURITY_AUDIT":
            response = await self._execute_security_audit()
        elif intent == "AUDIT_STRUCTURE":
            response = await self._execute_audit_structure()
        elif intent == "REFACTOR_RANDOM":
            response = await self._execute_refactor_random()
        elif intent == "MEMORY_CONSOLIDATION":
            response = await self._execute_memory_consolidation()
        elif intent == "SOLILOQUE_INTERNE":
            response = await self._execute_soliloque()
        else:
            response = await orchestrator.dispatch_task(agent, {
                "mission": f"[MODE VEILLE] {routine['mission']}",
                "context": f"PROTOCOLE_AUTONOMIE\n{AUTONOMY_GUARDRAIL}",
                "force_local": True,
            })

        quality = self._score_result_quality(response, intent)
        status = "success" if response and response.get("status") in ("success", "consensus") else "error"
        self._record_routine(agent, intent, status, quality_score=quality)
        if status == "success" and quality >= 0.3:
            self.error_streak = 0
        else:
            self.error_streak += 1
        self.daily_count += 1
        self.total_routines_executed += 1
        self.daily_budget_used += routine_cost
        logger.info(f"[AUTONOMY] Routine FORCED {self.daily_count}/{MAX_DAILY_ROUTINES} (cout: {routine_cost}pt, budget: {self.daily_budget_used}/{DAILY_BUDGET_POINTS}pt)")

        # Feedback reptilien (apaisement si succès — pas de handler bus dupliqué)
        try:
            from core.reptilian_core import reptile
            if quality >= 0.6:
                reptile.on_routine_success(intent)
        except Exception:
            pass

        # Voix intérieure : routine terminée → réactiver DMN
        try:
            from core.inner_voice import voice as inner_voice
            inner_voice.set_idle(True)
        except Exception:
            pass

        # Feedback bus (desires, cardiac, prefrontal via handlers)
        result_preview = str(response.get("result", ""))[:200] if response else ""
        participants = []
        if intent == "COUNCIL_DEBATE" and response:
            participants = response.get("participants", [])
        await bus.publish("AUTONOMY_ROUTINE_COMPLETE", {
            "intent": intent,
            "agent": agent,
            "participants": participants,
            "status": status,
            "quality_score": quality,
            "result": result_preview,
        })

    def _should_veto(self, intent: str, agent: str) -> str:
        """Veto proactif basé sur les signatures d'échec apprises. Retourne la raison ou ''."""
        # 0. RÉFLEXE REPTILIEN — court-circuite tout
        try:
            from core.reptilian_core import reptile
            if reptile.should_freeze():
                return f"veto-reptilien: FREEZE actif (menace={reptile.threat_level:.1f})"
            flinch = reptile.should_flinch(intent)
            if flinch:
                return f"veto-reptilien: {flinch}"
            shed, max_cost = reptile.should_shed()
            if shed:
                cost = RESOURCE_COSTS.get(intent, 3)
                if cost > max_cost:
                    return f"veto-reptilien: SHED actif, coût {cost} > max {max_cost}"
        except Exception:
            pass  # Le reptilien tombe → on continue sans lui (résilience)

        # 0b. MARQUEURS SOMATIQUES — intuitions viscérales
        try:
            from core.cardiac_engine import heart
            signal = heart.get_somatic_signal(intent)
            if signal < -1.0:
                return f"veto-somatique: signal viscéral très négatif ({signal:.2f}) pour {intent}"
        except Exception:
            pass

        # 1. Vérifier les échecs répétés dans l'historique
        recent_failures = [
            r for r in self.routine_history[-20:]
            if r.get("intent") == intent and r.get("agent") == agent
            and r.get("status") in ("error", "low_quality")
        ]
        if len(recent_failures) >= 5:
            successes = [
                r for r in self.routine_history[-20:]
                if r.get("intent") == intent and r.get("agent") == agent
                and r.get("status") == "success"
            ]
            if not successes:
                return f"veto: {intent}/{agent} a échoué {len(recent_failures)}x sans succès récent"

        # 2. Vérifier santé système
        if self.last_health_check and isinstance(self.last_health_check, dict):
            if self.last_health_check.get("verdict") == "NO_GO" and intent in ("EXPANSION_CODE", "GRIMOIRE_INVOKE"):
                return f"veto: santé NO_GO, routine risquée {intent} reportée"

        # 3. INHIBITION PRÉFRONTALE — arbitrage cognitif
        try:
            from core.prefrontal import prefrontal
            # Collecter le veto en cours (reptilien/somatique déjà passé sans retourner)
            # → on passe "" car aucun veto n'a été déclenché à ce stade
            inhibition = prefrontal.compute_inhibition(intent, "")
            if inhibition["action"] == "inhibit":
                return f"veto-prefrontal: {inhibition['reason']}"
        except Exception:
            pass

        return ""

    async def _execute_memory_consolidation(self) -> dict:
        """Consolide les mémoires récentes en synthèses thématiques. Zero LLM."""
        try:
            from core.vector_store import ChromaMemoryManager
            mgr = ChromaMemoryManager.get_instance()
            if not mgr:
                return {"status": "error", "result": "ChromaDB indisponible."}

            col = mgr._get_collection("collective_wisdom")
            all_docs = col.get(include=["documents", "metadatas"])

            if not all_docs["ids"]:
                return {"status": "success", "result": "Consolidation: aucun document à consolider."}

            now = time.time()
            recent = []
            for doc, meta, doc_id in zip(all_docs["documents"], all_docs["metadatas"], all_docs["ids"]):
                ts = float(meta.get("timestamp", 0))
                if now - ts < 30 * 86400:  # 30 jours
                    recent.append((doc, meta, doc_id, int(meta.get("recall_count", 0))))

            # Grouper par source
            groups = {}
            for doc, meta, doc_id, rc in recent:
                source = meta.get("source", "unknown")
                groups.setdefault(source, []).append(doc[:200])

            # Pour chaque groupe avec 5+ entrées, créer un résumé déterministe
            consolidated = 0
            for source, docs in groups.items():
                if len(docs) >= 5:
                    summary = f"[CONSOLIDATION {source}] {len(docs)} observations récentes:\n"
                    summary += "\n".join(f"- {d[:100]}" for d in docs[:10])
                    mgr.add_documents(
                        [summary],
                        [{"source": "consolidation", "timestamp": str(now), "original_count": len(docs)}],
                        [f"consol-{source}-{int(now)}"],
                        "collective_wisdom"
                    )
                    consolidated += 1

            # --- Dream Mode (consolidation synaptique) ---
            try:
                from core.cardiac_engine import heart
                heart.react("dream")
            except Exception:
                pass
            try:
                from core.synaptic_network import cortex
                dream_report = cortex.dream_consolidation()
                if dream_report.get("dream_connections", 0) > 0:
                    result_msg = (f"Consolidation: {consolidated} groupes synthétisés"
                                  f" à partir de {len(recent)} documents récents."
                                  f" | Dream: +{dream_report['dream_connections']} connexions"
                                  f", -{dream_report['pruned_synapses']} pruned")
                    return {"status": "success", "result": result_msg}
            except Exception:
                pass

            return {"status": "success", "result": f"Consolidation: {consolidated} groupes synthétisés à partir de {len(recent)} documents récents."}
        except Exception as e:
            return {"status": "error", "result": f"Erreur consolidation: {e}"}

    async def _execute_soliloque(self) -> dict:
        """Dialogue introspectif avec le compagnon intérieur."""
        try:
            from core.soliloque import soliloque
            result = await soliloque.engage()
            return result
        except Exception as e:
            return {"status": "error", "result": f"Erreur soliloque: {e}"}

    async def _execute_grimoire_routine(self) -> dict:
        """Invoque un agent Grimoire en rotation (le moins récemment utilisé)."""
        try:
            grimoire_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "grimoire", "grimoire_index.json")
            with open(grimoire_path, "r", encoding="utf-8") as f:
                grimoire_index = json.load(f)

            if not grimoire_index:
                return {"status": "error", "result": "Grimoire vide."}

            slugs = [entry["slug"] for entry in grimoire_index]

            # Suggestion ciblée de la voix intérieure
            best_slug = None
            try:
                from core.inner_voice import voice as inner_voice
                suggestion = inner_voice.get_grimoire_suggestion()
                if suggestion and suggestion in slugs:
                    best_slug = suggestion
                    print(f"   \U0001f5e3\ufe0f VOIX\u2192GRIMOIRE: Ciblage '{best_slug}'")
            except Exception:
                pass

            # Fallback : rotation LRU (le moins récemment invoqué)
            if not best_slug:
                recent_grimoire = [
                    h.get("grimoire_slug") for h in self.routine_history
                    if h.get("intent") == "GRIMOIRE_INVOKE" and h.get("grimoire_slug")
                ]
                for slug in slugs:
                    if slug not in recent_grimoire:
                        best_slug = slug
                        break
                if not best_slug:
                    best_slug = slugs[self.total_routines_executed % len(slugs)]

            # Trouver la description pour construire la mission
            entry = next((e for e in grimoire_index if e["slug"] == best_slug), None)
            description = entry.get("description", "Agent spécialisé") if entry else "Agent spécialisé"

            mission = (
                f"[MODE VEILLE] En tant que spécialiste ({description}), "
                f"effectue une analyse ou action pertinente pour le système Prométhée. "
                f"Agis de ta propre initiative."
            )

            print(f"   📖 GRIMOIRE INVOKE: {best_slug} — {description[:60]}")
            self._last_grimoire_slug = best_slug
            response = await orchestrator.dispatch_task(best_slug, {
                "mission": mission,
                "context": f"PROTOCOLE_AUTONOMIE_GRIMOIRE\n{AUTONOMY_GUARDRAIL}",
                "force_local": True,
            })
            return response or {"status": "error", "result": "Pas de réponse du Grimoire."}

        except Exception as e:
            logger.warning(f"[AUTONOMY] Erreur routine Grimoire: {e}")
            return {"status": "error", "result": str(e)}

    async def _execute_council_debate(self) -> dict:
        """Lance un débat autonome Council : Recherche web → Débat éclairé."""
        # --- Guard : skip si trop de specs Council en attente ---
        try:
            from core.evolution_catalog import EvolutionCatalog
            catalog = EvolutionCatalog()
            pending_council = [
                s for s in catalog.specs.values()
                if s.id.startswith("COUNCIL-") and s.status == "available"
            ]
            if len(pending_council) >= 3:
                # Tenter une curation avant de skipper
                purged = catalog.curate_council_specs()
                if purged > 0:
                    # Recompter après curation
                    pending_council = [
                        s for s in catalog.specs.values()
                        if s.id.startswith("COUNCIL-") and s.status == "available"
                    ]
                if len(pending_council) >= 3:
                    # Éviction forcée : expirer les specs les plus anciennes pour garder max 2
                    sorted_by_age = sorted(pending_council, key=lambda s: s.id)
                    to_evict = sorted_by_age[:len(pending_council) - 2]
                    for spec in to_evict:
                        catalog.mark_rejected(spec.id, "eviction_forcee: place au nouveau debat")
                        purged += 1
                    logger.info(f"[COUNCIL] Éviction forcée: {len(to_evict)} spec(s) expirée(s), débat débloqué !")
                elif purged > 0:
                    logger.info(f"[COUNCIL] Curation: {purged} specs purgées, débat débloqué !")
        except Exception:
            pass  # Catalogue inaccessible — laisser tourner

        # Extraire les sujets des derniers councils pour la déduplication
        recent_subjects = [
            h.get("subject", "")
            for h in self.routine_history
            if h.get("intent") == "COUNCIL_DEBATE" and h.get("subject")
        ][-5:]

        try:
            from core.psyche import psyche
            debate_index = psyche.get_debate_index()
            topic = psyche.select_council_topic(
                error_streak=self.error_streak,
                daily_count=self.daily_count,
                debate_index=debate_index,
                recent_subjects=recent_subjects,
            )
        except Exception:
            topic = {
                "participants": ["strategist", "coder", "architect"],
                "mission": "Quelle amélioration prioritaire pour le système ?",
                "needs_research": False, "research_query": None,
                "subject_key": "default",
            }

        # Stocker la clé du sujet pour la déduplication future
        self._current_council_subject = topic.get("subject_key", "")

        # Phase 1 : Recherche web si le sujet le demande
        research_context = ""
        if topic.get("needs_research") and topic.get("research_query"):
            print(f"   🔍 COUNCIL PRE-RESEARCH: {topic['research_query'][:60]}...")
            try:
                res = await orchestrator.dispatch_task("researcher", {
                    "mission": f"VEILLE TECHNO: {topic['research_query']}",
                    "context": "COUNCIL_RESEARCH — Résume les découvertes clés en 5-10 lignes pour alimenter un débat.",
                    "force_local": True,
                })
                if res and res.get("status") == "success":
                    research_context = str(res.get("result", ""))[:2000]
                    print(f"   📚 Recherche terminée ({len(research_context)} chars)")
            except Exception as e:
                logger.warning(f"[COUNCIL] Recherche pré-débat échouée: {e}")

        # Phase 2 : Construire la mission du débat
        mission = topic["mission"]
        if research_context:
            mission = (
                f"{topic['mission']}\n\n"
                f"RÉSULTATS DE RECHERCHE DU RESEARCHER :\n"
                f"{research_context}\n\n"
                f"Débattez de ces découvertes : lesquelles sont applicables à Prométhée ? "
                f"Proposez des actions concrètes."
            )

        # Injection du journal stratégique (mémoire des débats précédents)
        try:
            from core.strategic_journal import journal as strat_journal
            journal_context = strat_journal.get_recent_context(3)
            if journal_context:
                mission += "\n\nMÉMOIRE DES DÉBATS PRÉCÉDENTS :\n" + journal_context
        except Exception as e:
            logger.warning(f"[COUNCIL] Journal stratégique indisponible: {e}")

        # Injection de la conscience de soi
        try:
            from core.self_awareness import awareness
            self_context = awareness.get_self_context()
            if self_context:
                mission += "\n\nCONSCIENCE DU SYSTÈME :\n" + self_context
        except Exception as e:
            logger.warning(f"[COUNCIL] Conscience indisponible: {e}")

        print(f"   🗣️ COUNCIL DEBATE: {topic['participants']} — {topic['mission'][:80]}")
        result = await orchestrator.dispatch_council(
            participants=topic["participants"],
            mission=f"[DÉBAT AUTONOME] {mission}",
        )
        result["participants"] = topic["participants"]
        # Injecter "result" pour le scoring qualité (Council retourne final_summary, pas result)
        if "result" not in result:
            result["result"] = result.get("final_summary", "")

        # Pipeline Council → Action : si consensus, créer des specs Evolution
        if result.get("status") == "consensus":
            try:
                await self._process_council_consensus(result, topic)
            except Exception as e:
                logger.warning(f"[COUNCIL→ACTION] Extraction specs échouée: {e}")

        # Enregistrer le débat dans le journal stratégique
        try:
            from core.strategic_journal import journal as strat_journal
            strat_journal.append_council_entry(
                participants=topic["participants"],
                subject=topic["mission"],
                status=result.get("status"),
                conclusion=result.get("final_summary", ""),
                research_context=research_context,
            )
        except Exception as e:
            logger.warning(f"[COUNCIL] Écriture journal échouée: {e}")

        # Écrire dans le journal des councils persistant (memory/council_journal.md)
        try:
            self._append_council_journal(topic, result)
        except Exception as e:
            logger.warning(f"[COUNCIL] Écriture council_journal échouée: {e}")

        return result

    def _append_council_journal(self, topic: dict, result: dict):
        """Ajoute une entrée au journal persistant des councils (memory/council_journal.md)."""
        import re
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        journal_path = os.path.join(project_root, "config", "council_journal.md")

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        participants = ", ".join(topic.get("participants", []))
        mission = topic.get("mission", "Sujet inconnu")
        status = result.get("status", "inconnu")
        rounds_used = result.get("rounds_used", "?")

        # Extraire les propositions clés depuis le transcript du dernier tour
        proposals = []
        files_mentioned = set()
        transcript = result.get("transcript", [])
        if transcript:
            last_round = max(e["round"] for e in transcript)
            for entry in transcript:
                if entry["round"] == last_round:
                    content = entry["content"][:1000]
                    # Extraire les fichiers mentionnés
                    for f in re.findall(r'(?:core|Agents)/[\w/]+\.py', content):
                        files_mentioned.add(f)
                    # Extraire les éléments de liste (tirets)
                    for line in content.split("\n"):
                        line = line.strip()
                        if line.startswith(("-", "•", "*")) and len(line) > 15:
                            proposals.append(line[:120])

        # Limiter à 5 propositions
        proposals = proposals[:5]
        proposals_text = "\n".join(f"  {p}" for p in proposals) if proposals else "  (Aucune proposition extraite automatiquement)"
        files_text = ", ".join(f"`{f}`" for f in sorted(files_mentioned)) if files_mentioned else "(aucun fichier cité)"

        entry = (
            f"\n---\n\n"
            f"## [{now}] {mission[:80]}\n\n"
            f"**Participants** : {participants} | **Tours** : {rounds_used} | **Consensus** : {'oui' if status == 'consensus' else 'non'}\n\n"
            f"**Propositions clés** :\n{proposals_text}\n\n"
            f"**Fichiers cibles** : {files_text}\n"
            f"**Verdict** : (à curé manuellement)\n"
        )

        # Append au fichier
        os.makedirs(os.path.dirname(journal_path), exist_ok=True)
        with open(journal_path, "a", encoding="utf-8") as f:
            f.write(entry)

        logger.info(f"[COUNCIL] Journal council_journal.md mis à jour : {mission[:50]}")

    async def _execute_memory_cleanup(self) -> dict:
        """Nettoie la mémoire RAG : purge les anciennes ET les mauvaise qualité.

        Utilise les méthodes async lockées de ChromaMemoryManager pour éviter
        les race conditions sur les opérations composées (get→filter→delete).
        """
        try:
            from core.vector_store import ChromaMemoryManager
            mgr = ChromaMemoryManager.get_instance()
            if not mgr:
                return {"status": "error", "result": "ChromaDB indisponible."}

            # Phase 1 : Purge des entrées anciennes (>60 jours) — protégé par lock
            removed_old = await mgr.async_purge_expired(max_age_days=60)

            # Phase 2 : Purge qualitative (textes courts, hallucinations non-latin) — protégé par lock
            removed_quality = await mgr.async_purge_low_quality(
                min_length=100,
                max_non_latin_ratio=0.10,
            )

            total = removed_old + removed_quality
            msg = f"Nettoyage mémoire : {removed_old} anciennes + {removed_quality} basse qualité = {total} supprimées."
            print(f"   🧹 {msg}")
            return {"status": "success", "result": msg}
        except Exception as e:
            return {"status": "error", "result": str(e)}

    async def _execute_security_audit(self) -> dict:
        """Audite un fichier aléatoire du projet pour des vulnérabilités."""
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            # Lister les fichiers Python du projet
            target_dirs = [
                os.path.join(project_root, "core"),
                os.path.join(project_root, "Agents"),
            ]
            py_files = []
            for d in target_dirs:
                if os.path.isdir(d):
                    py_files.extend(
                        os.path.join(d, f) for f in os.listdir(d) if f.endswith(".py")
                    )
            if not py_files:
                return {"status": "error", "result": "Aucun fichier Python trouvé."}

            # Choisir un fichier en rotation
            target = py_files[self.total_routines_executed % len(py_files)]
            filename = os.path.basename(target)

            # Anti-doublon : skip si ce fichier a été audité dans les dernières 6h
            last_audit_ts = self._security_audited_files.get(filename, 0)
            if time.time() - last_audit_ts < 6 * 3600:
                # Avancer au prochain fichier non-audité récemment
                found = False
                for offset in range(1, len(py_files)):
                    alt_target = py_files[(self.total_routines_executed + offset) % len(py_files)]
                    alt_name = os.path.basename(alt_target)
                    if time.time() - self._security_audited_files.get(alt_name, 0) >= 6 * 3600:
                        target, filename = alt_target, alt_name
                        found = True
                        break
                if not found:
                    return {"status": "skipped", "result": "Tous les fichiers ont été audités récemment."}

            # Marquer comme audité
            self._security_audited_files[filename] = time.time()

            # Lire le contenu (limité à 3000 chars)
            with open(target, "r", encoding="utf-8") as f:
                code = f.read()[:3000]

            print(f"   🔒 SECURITY AUDIT: {filename}")
            response = await orchestrator.dispatch_task("security", {
                "mission": (
                    f"[MODE VEILLE] Audite le fichier {filename} pour des vulnérabilités.\n"
                    f"RÈGLES STRICTES :\n"
                    f"- Réponds UNIQUEMENT en français.\n"
                    f"- Analyse UNIQUEMENT le code fourni ci-dessous, pas de code inventé.\n"
                    f"- NE GÉNÈRE PAS de code. Liste seulement les vulnérabilités trouvées.\n"
                    f"- Format : une liste numérotée de vulnérabilités (ou 'Aucune vulnérabilité détectée').\n"
                    f"- Maximum 500 mots."
                ),
                "context": (
                    f"PROTOCOLE_AUTONOMIE\n"
                    f"FICHIER: {filename}\n"
                    f"CODE À AUDITER (ne génère pas de nouveau code, analyse celui-ci) :\n{code}"
                ),
                "force_local": True,
            })

            # Post-filtre anti-hallucination : détecter les réponses hors-sujet
            if response and response.get("result"):
                result_text = response["result"]
                # Détection de caractères non-latins massifs (chinois, etc.)
                non_latin = sum(1 for c in result_text if ord(c) > 0x024F)
                if non_latin > len(result_text) * 0.1:
                    logger.warning(f"[SECURITY_AUDIT] Hallucination détectée ({non_latin} chars non-latins)")
                    response["result"] = f"Audit de {filename} : résultat filtré (hallucination LLM détectée)."
                # Tronquer les réponses excessivement longues
                elif len(result_text) > 3000:
                    response["result"] = result_text[:3000] + "\n\n[... tronqué — réponse trop longue]"

            return response or {"status": "error", "result": "Pas de réponse."}
        except Exception as e:
            return {"status": "error", "result": str(e)}

    async def _execute_audit_structure(self) -> dict:
        """Audit structure réel : scanne le filesystem pour fichiers temporaires/orphelins."""
        # Rafraîchir le cache de structure projet (anti-hallucination basé sur des données fraîches)
        try:
            from core.prompt_templates import reset_project_structure_cache
            reset_project_structure_cache()
        except Exception:
            pass
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

            # Extensions à détecter à la racine du projet
            temp_extensions = {".tmp", ".temp", ".bak", ".old", ".orig", ".swp", ".swo"}
            log_extensions = {".log"}  # Séparé car certains sont légitimes
            # Fichiers de log légitimes (à ne pas signaler)
            legit_logs = {"promethee.log"}

            temp_files = []
            log_files = []
            pycache_dirs = []
            large_files = []  # > 10 MB

            # Scan de la racine uniquement (pas récursif pour les .tmp/.log)
            for entry in os.scandir(project_root):
                if entry.is_file():
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext in temp_extensions:
                        size_kb = entry.stat().st_size / 1024
                        temp_files.append(f"{entry.name} ({size_kb:.0f} KB)")
                    elif ext in log_extensions and entry.name not in legit_logs:
                        size_kb = entry.stat().st_size / 1024
                        log_files.append(f"{entry.name} ({size_kb:.0f} KB)")
                    # Fichiers volumineux (> 10 MB)
                    if entry.stat().st_size > 10 * 1024 * 1024:
                        size_mb = entry.stat().st_size / (1024 * 1024)
                        large_files.append(f"{entry.name} ({size_mb:.1f} MB)")
                elif entry.is_dir() and entry.name == "__pycache__":
                    pycache_dirs.append(entry.name)

            # Scan récursif limité pour __pycache__ (profondeur 2)
            for subdir in ["core", "Agents", "tests"]:
                subpath = os.path.join(project_root, subdir)
                if os.path.isdir(subpath):
                    for entry in os.scandir(subpath):
                        if entry.is_dir() and entry.name == "__pycache__":
                            pycache_dirs.append(f"{subdir}/{entry.name}")

            # Construire le rapport
            issues = []
            if temp_files:
                issues.append(f"Fichiers temporaires à la racine : {', '.join(temp_files)}")
            if log_files:
                issues.append(f"Fichiers .log non-système à la racine : {', '.join(log_files)}")
            if large_files:
                issues.append(f"Fichiers volumineux (>10 MB) : {', '.join(large_files)}")
            if pycache_dirs:
                issues.append(f"Dossiers __pycache__ trouvés : {', '.join(pycache_dirs)}")

            if issues:
                report = f"AUDIT STRUCTURE — {len(issues)} problème(s) détecté(s) :\n" + "\n".join(f"- {i}" for i in issues)
                report += "\n\nRecommandation : nettoyer les fichiers temporaires et les caches __pycache__ inutiles."
            else:
                report = (
                    "AUDIT STRUCTURE — Aucun problème détecté.\n"
                    "La racine du projet est propre : pas de fichiers .tmp/.bak/.log orphelins, "
                    "pas de fichiers volumineux anormaux."
                )

            logger.info(f"[AUDIT_STRUCTURE] Scan terminé : {len(issues)} problème(s)")
            return {"status": "success", "result": report}
        except Exception as e:
            logger.warning(f"[AUDIT_STRUCTURE] Erreur scan: {e}")
            return {"status": "error", "result": f"Erreur lors du scan structure : {e}"}

    async def _execute_refactor_random(self) -> dict:
        """Propose un refactoring pour un fichier aléatoire."""
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            target_dirs = [
                os.path.join(project_root, "core"),
                os.path.join(project_root, "Agents"),
            ]

            # Charger la liste des fichiers protégés
            try:
                from Agents.factory_agent import _PROTECTED_FILES
            except ImportError:
                _PROTECTED_FILES = set()

            py_files = []
            for d in target_dirs:
                if os.path.isdir(d):
                    for f in os.listdir(d):
                        if f.endswith(".py"):
                            # Calculer le chemin relatif pour vérifier la protection
                            rel_path = os.path.relpath(os.path.join(d, f), project_root).replace("\\", "/")
                            if rel_path not in _PROTECTED_FILES:
                                py_files.append(os.path.join(d, f))
            if not py_files:
                return {"status": "error", "result": "Aucun fichier Python non-protégé trouvé."}

            # Rotation différente du security audit (offset +7)
            target = py_files[(self.total_routines_executed + 7) % len(py_files)]
            filename = os.path.basename(target)

            with open(target, "r", encoding="utf-8") as f:
                code = f.read()[:3000]

            print(f"   🔧 REFACTOR: {filename}")
            response = await orchestrator.dispatch_task("coder", {
                "mission": (
                    f"[MODE VEILLE] Analyse {filename} et propose UN SEUL refactoring précis "
                    f"pour améliorer la lisibilité ou réduire la complexité. "
                    f"Pas de réécriture complète, juste une suggestion ciblée."
                ),
                "context": f"PROTOCOLE_AUTONOMIE\nFICHIER: {filename}\nCODE:\n{code}",
                "force_local": True,
            })
            return response or {"status": "error", "result": "Pas de réponse."}
        except Exception as e:
            return {"status": "error", "result": str(e)}

    async def _process_council_consensus(self, council_result: dict, topic: dict):
        """Transforme un consensus Council en specs Evolution (Council → Action)."""
        import re
        from core.evolution_catalog import EvolutionCatalog, ImprovementSpec

        final_summary = council_result.get("final_summary", "")
        if not final_summary or len(final_summary) < 50:
            return

        catalog = EvolutionCatalog()

        # Limiter à 2 specs générées par session pour éviter le spam
        existing_council_specs = [
            s for s in catalog.specs.values()
            if s.id.startswith("COUNCIL-") and s.status == "available"
        ]
        if len(existing_council_specs) >= 3:
            logger.info("[COUNCIL→ACTION] Déjà 3 specs Council en attente, skip.")
            return

        # Construire le texte d'analyse à partir du transcript COMPLET du dernier tour
        # (le final_summary tronque à 200 chars/participant, perdant les détails concrets)
        transcript = council_result.get("transcript", [])
        if transcript:
            participants = council_result.get("participants", [])
            last_round = max(e["round"] for e in transcript)
            last_round_entries = [e for e in transcript if e["round"] == last_round]
            # Utiliser le contenu complet (max 1500 chars/participant au lieu de 200)
            analysis_text = "\n".join(
                f"[{e['agent'].upper()}] {e['content'][:1500]}" for e in last_round_entries
            )
        else:
            analysis_text = final_summary

        # Extraire les fichiers cibles (avec ET sans préfixe de dossier)
        file_mentions = re.findall(r'((?:core|Agents)/[\w/]+\.py)', analysis_text)
        # Aussi capturer les .py mentionnés seuls (ex: "bus.py", "router.py")
        standalone_py = re.findall(r'\b(\w+\.py)\b', analysis_text)
        # Mapper les fichiers standalone vers leur chemin probable
        known_dirs = {"core/": ["orchestrator", "router", "bus", "autonomy_engine", "council",
                                "summoner", "base_agent", "event_bus", "self_awareness",
                                "prompt_templates", "ci_pipeline", "grimoire_writer",
                                "psyche", "evolution_catalog", "strategic_journal"],
                      "Agents/": ["coder_agent", "architect_agent", "security_agent",
                                  "evolution_agent", "factory_agent", "researcher_agent",
                                  "strategist_agent", "writer_agent", "infra_agent",
                                  "formatter_agent"]}
        for py_file in standalone_py:
            stem = py_file.replace(".py", "")
            for prefix, known in known_dirs.items():
                if stem in known:
                    qualified = f"{prefix}{py_file}"
                    if qualified not in file_mentions:
                        file_mentions.append(qualified)

        # Valider que les fichiers mentionnés existent réellement (anti-hallucination)
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        verified_files = []
        for f in file_mentions:
            full_path = os.path.join(project_root, f.replace("/", os.sep))
            if os.path.exists(full_path):
                verified_files.append(f)
            else:
                logger.warning(f"[COUNCIL→ACTION] Fichier halluciné ignoré : {f}")
        file_mentions = verified_files

        # Extraire les actions concrètes — regex élargie pour le langage naturel des LLMs
        action_patterns = re.findall(
            r'(?:ACTION|IMPLÉMENTER|IMPL[ÉE]MENTATION|AJOUTER|AJOUT|MODIFIER|MODIFICATION'
            r'|SUGGESTION|RECOMMANDATION|CRÉER|CRÉATION|AMÉLIORER|AMÉLIORATION'
            r'|IMPLEMENT|ADD|MODIFY|CREATE|IMPROVE)\s*[:\-]\s*(.+)',
            analysis_text,
            re.IGNORECASE,
        )
        # Fallback : chercher des verbes d'action en début de ligne (tirets de liste)
        if not action_patterns:
            action_patterns = re.findall(
                r'[-•]\s*(?:Ajouter|Créer|Modifier|Implémenter|Améliorer|Intégrer|Remplacer|Refactorer)\s+(.+)',
                analysis_text,
                re.IGNORECASE,
            )

        if not action_patterns and not file_mentions:
            logger.info("[COUNCIL→ACTION] Pas d'action concrète dans le consensus.")
            return

        # Construire la spec
        mission_short = topic.get("mission", "amélioration")[:80]
        spec_id = f"COUNCIL-{int(time.time())}-{uuid.uuid4().hex[:4]}"

        # Prendre le premier fichier cible vérifié (pas de fallback générique)
        if not file_mentions:
            logger.info("[COUNCIL→ACTION] Aucun fichier vérifié — spec non créée.")
            return
        target_file = file_mentions[0]

        # Résumé des actions
        actions_text = "\n".join(f"- {a.strip()}" for a in action_patterns[:3])
        if not actions_text:
            actions_text = analysis_text[:500]

        # Extraire la méthode cible depuis l'analyse (ou fallback générique)
        method_match = re.search(r'(?:méthode|method|def)\s+(\w+)', analysis_text, re.IGNORECASE)
        target_method = method_match.group(1) if method_match else ""

        # code_template valide (doit contenir def/class/import pour passer Phase 4c)
        code_template = (
            f"import logging\n\n"
            f"def council_improvement():\n"
            f"    \"\"\"Amélioration issue du consensus Council.\n"
            f"    Mission: {mission_short}\n"
            f"    \"\"\"\n"
            f"    # Actions identifiées:\n"
        )
        for action_line in actions_text.split("\n")[:5]:
            code_template += f"    {action_line}\n"
        code_template += "    pass\n"

        spec = ImprovementSpec(
            id=spec_id,
            name=f"Council: {mission_short}",
            description=f"Issu d'un consensus Council.\n{actions_text}",
            category="intelligence",
            target_file=target_file,
            target_method=target_method,
            difficulty=2,
            code_template=code_template,
            validation="Vérifier que l'amélioration proposée par le Council fonctionne.",
            tags=["council", "consensus", "auto-generated"],
            status="available",
        )

        catalog.specs[spec_id] = spec
        catalog._save()
        logger.info(f"[COUNCIL→ACTION] Spec {spec_id} créée : {mission_short}")
        print(f"   📋 COUNCIL→ACTION: Spec {spec_id} ajoutée au catalogue Evolution")

    async def start_loop(self):
        self.is_running = True
        print(f"   🧠 AUTONOMY: Moteur V24 (Health-Aware Sentinel) activé. Limite: {MAX_DAILY_ROUTINES} routines/jour.")

        while self.is_running:
            # Sleep adaptatif : piloté par le coeur (cohérence cardiaque)
            try:
                from core.cardiac_engine import heart
                sleep_time = heart.compute_sleep_duration()
            except Exception:
                sleep_time = random.randint(600, 1200)
            if self.error_streak >= 3:
                sleep_time = int(sleep_time * 1.5)
                logger.warning(f"[AUTONOMY] Mode prudent (error_streak={self.error_streak}), sleep: {sleep_time}s")
                try:
                    from core.cardiac_engine import heart as _h
                    _h.react("error_streak")
                except Exception:
                    pass
                # Décroissance progressive : réduire l'error_streak de 1 à chaque cycle pour sortir de la spirale
                if self.error_streak >= 5:
                    self.error_streak -= 1

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

                # Alerte mémoire
                memory = health.get("memory", {})
                if memory.get("status") in ("degraded", "down"):
                    await bus.publish("MEMORY_HEALTH_ALERT", {
                        "status": memory["status"],
                        "warnings": memory.get("warnings", []),
                        "persistent": memory.get("persistent", False),
                        "collections": memory.get("collections", {}),
                    })
                    logger.warning(f"[AUTONOMY] MÉMOIRE {memory['status'].upper()}: {memory.get('warnings', [])}")

                # Retry dead letters (1 par cycle, supprime si échec pour éviter boucle infinie)
                if bus.dead_letter_count > 0:
                    try:
                        retried = await bus.retry_dead_letter(0)
                        if retried:
                            logger.info("[AUTONOMY] Dead letter re-publiée avec succès.")
                        else:
                            # Échec du retry → supprimer pour ne pas boucler indéfiniment
                            dl_list = bus.get_dead_letters()
                            if dl_list:
                                bus._dead_letters.pop(0)
                                logger.info("[AUTONOMY] Dead letter irrécupérable supprimée.")
                    except Exception:
                        pass

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
