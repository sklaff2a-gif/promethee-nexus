import asyncio
import time
import random
import logging
import json
import os
from datetime import date, datetime
from core.orchestrator import orchestrator
from core.event_bus.bus import bus
from core.prompt_templates import AUTONOMY_GUARDRAIL

logger = logging.getLogger("AutonomyEngine")

# Limite quotidienne de routines autonomes
MAX_DAILY_ROUTINES = 80

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
    "GRIMOIRE_INVOKE": ["grimoire", "éphémère", "recette", "spécialiste", "debug", "analyse"],
    "SECURITY_AUDIT": ["sécurité", "vulnérabilité", "injection", "risque", "audit"],
    "MEMORY_CLEANUP": ["mémoire", "nettoyage", "ancien", "doublon", "rag"],
    "REFACTOR_RANDOM": ["refactoring", "simplifier", "lisibilité", "dette", "technique"],
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

            # Personality bias (PSYCHE) : bonus/malus basé sur les traits du système
            if personality_bias and intent in personality_bias:
                score += personality_bias[intent]

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
        # Historique d'apprentissage ciblé {topic: timestamp_iso}
        self._learning_history: dict = persisted.get("learning_history", {})
        # Flag : max 1 apprentissage par cycle de routine
        self._learning_done_this_cycle = False

        bus.subscribe("USER_COMMAND", self.reset_timer)

    def _check_daily_budget(self) -> bool:
        """Vérifie et reset le compteur quotidien. Retourne True si budget disponible."""
        today = date.today()
        if today != self.last_reset_day:
            self.daily_count = 0
            self.last_reset_day = today

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
            "learning_history": self._learning_history,
        }
        AutonomyStatePersistence.save(state)

    def _score_result_quality(self, response: dict, intent: str) -> float:
        """Score qualité du résultat d'une routine (0.0 = garbage, 1.0 = excellent).

        Critères :
        - Longueur du résultat (trop court = mauvais)
        - Ratio caractères non-latin (hallucination)
        - Répétition avec les résultats précédents
        """
        if not response or not isinstance(response, dict):
            return 0.0

        result_text = str(response.get("result", ""))
        score = 1.0

        # 1. Pénalité longueur : résultat vide ou très court
        if len(result_text.strip()) < 20:
            return 0.0
        elif len(result_text.strip()) < 50:
            score -= 0.4
        elif len(result_text.strip()) < 100:
            score -= 0.2

        # 2. Pénalité non-latin (hallucination)
        alpha_chars = [c for c in result_text if c.isalpha()]
        if alpha_chars:
            non_latin = sum(1 for c in alpha_chars if ord(c) > 0x024F)
            ratio = non_latin / len(alpha_chars)
            if ratio > 0.15:
                score -= 0.5  # Forte pénalité
            elif ratio > 0.05:
                score -= 0.2

        # 3. Pénalité répétition avec les résultats précédents
        recent_results = [
            str(h.get("result_preview", ""))
            for h in self.routine_history[-5:]
            if h.get("result_preview")
        ]
        for prev in recent_results:
            if prev and result_text[:200] == prev[:200]:
                score -= 0.4
                break

        # Stocker un aperçu pour la comparaison future
        if hasattr(self, 'routine_history') and self.routine_history:
            pass  # Le preview sera ajouté dans _record_routine

        return max(0.0, min(1.0, score))

    def _diagnose_failure(self, response: dict, quality_score: float, intent: str) -> str:
        """Diagnostique le TYPE d'échec : hallucination, repetition, ignorance, technical."""
        if not response or not isinstance(response, dict):
            return "technical"

        result_text = str(response.get("result", ""))

        # 1. Hallucination : ratio non-latin > 15%
        alpha_chars = [c for c in result_text if c.isalpha()]
        if alpha_chars:
            non_latin = sum(1 for c in alpha_chars if ord(c) > 0x024F)
            if non_latin / len(alpha_chars) > 0.15:
                return "hallucination"

        # 2. Répétition : 200 premiers chars identiques à un résultat précédent
        recent_previews = [
            str(h.get("result_preview", ""))
            for h in self.routine_history[-5:]
            if h.get("result_preview")
        ]
        for prev in recent_previews:
            if prev and len(result_text) >= 200 and result_text[:200] == prev[:200]:
                return "repetition"

        # 3. Ignorance : résultat court/vague + patterns linguistiques
        #    Exclure les résultats vides/quasi-vides (c'est technique, pas de l'ignorance)
        stripped = result_text.strip()
        if len(stripped) < 10:
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
            await orchestrator.dispatch_task("researcher", {
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
        routines = self._get_routines()

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
            scored.sort(key=lambda x: x[1], reverse=True)
        except Exception:
            pass

        # --- Ajustements adaptatifs (conscience de soi) ---
        adaptive_adjustments = {}
        try:
            from core.self_awareness import awareness
            adaptive_adjustments = awareness.compute_adaptive_scoring(self.routine_history)
            if adaptive_adjustments:
                for i, (routine, s) in enumerate(scored):
                    adj = adaptive_adjustments.get(routine["intent"], 0.0)
                    if adj != 0.0:
                        scored[i] = (routine, s + adj)
                scored.sort(key=lambda x: x[1], reverse=True)
                # Log des ajustements actifs
                active = {k: v for k, v in adaptive_adjustments.items() if v != 0.0}
                if active:
                    parts = [f"{k}:{v:+.1f}" for k, v in active.items()]
                    print(f"   🧠 CONSCIENCE: Ajustements adaptatifs: {', '.join(parts)}")
        except Exception:
            pass

        selected, score = scored[0]
        agent = selected["agent"]
        intent = selected["intent"]

        print(f"   ✨ AUTONOMY: Routine [{intent}] (score={score:.1f}) -> [{agent.upper()}] ({self.daily_count + 1}/{MAX_DAILY_ROUTINES})")

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
        elif intent == "REFACTOR_RANDOM":
            response = await self._execute_refactor_random()
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
            mission_text = f"[MODE VEILLE] {selected['mission']}\nAgis de ta propre initiative.{AUTONOMY_GUARDRAIL}"
            if purpose_ctx:
                mission_text = f"{purpose_ctx}\n{mission_text}"
            response = await orchestrator.dispatch_task(agent, {
                "mission": mission_text,
                "context": "PROTOCOLE_AUTONOMIE",
                "force_local": True,
            })

        # Sujet du council (pour la déduplication)
        council_subject = getattr(self, "_current_council_subject", "")

        # Slug Grimoire réel (pour la rotation)
        grimoire_slug = getattr(self, "_last_grimoire_slug", "")
        self._last_grimoire_slug = ""

        # Score qualité post-routine
        quality_score = self._score_result_quality(response, intent)

        # Aperçu du résultat pour comparaison future
        result_preview = ""
        if response and isinstance(response, dict):
            result_preview = str(response.get("result", ""))[:200]

        # Reset du flag d'apprentissage pour ce cycle
        self._learning_done_this_cycle = False

        if response and response.get("status") in ("success", "consensus"):
            if quality_score < 0.3:
                # Succès technique mais résultat de mauvaise qualité
                failure_type = self._diagnose_failure(response, quality_score, intent)
                print(f"   ⚠️ Routine {intent} terminée mais qualité basse ({quality_score:.2f}) [{failure_type}]")
                self._record_routine(agent, intent, "low_quality", subject=council_subject,
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
                print(f"   ✅ Fin Routine {agent.upper() if agent != '_council' else 'COUNCIL'} (qualité: {quality_score:.2f})")
                self._record_routine(agent, intent, "success", subject=council_subject,
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
        })

        self.daily_count += 1
        self.total_routines_executed += 1
        logger.info(f"[AUTONOMY] Routine {self.daily_count}/{MAX_DAILY_ROUTINES} du jour exécutée.")

        # Snapshot conscience de soi périodique (toutes les 5 routines)
        if self.daily_count % 5 == 0:
            try:
                from core.self_awareness import awareness
                awareness.generate_snapshot()
            except Exception:
                pass

    async def _execute_grimoire_routine(self) -> dict:
        """Invoque un agent Grimoire en rotation (le moins récemment utilisé)."""
        try:
            grimoire_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "grimoire", "grimoire_index.json")
            with open(grimoire_path, "r", encoding="utf-8") as f:
                grimoire_index = json.load(f)

            if not grimoire_index:
                return {"status": "error", "result": "Grimoire vide."}

            # Rotation : choisir le slug le moins récemment invoqué
            recent_grimoire = [
                h.get("grimoire_slug") for h in self.routine_history
                if h.get("intent") == "GRIMOIRE_INVOKE" and h.get("grimoire_slug")
            ]
            slugs = [entry["slug"] for entry in grimoire_index]

            # Trouver le slug absent de l'historique, ou le plus ancien
            best_slug = None
            for slug in slugs:
                if slug not in recent_grimoire:
                    best_slug = slug
                    break
            if not best_slug:
                # Tous ont été invoqués récemment → prendre le premier (le plus ancien dans la rotation)
                best_slug = slugs[self.total_routines_executed % len(slugs)]

            # Trouver la description pour construire la mission
            entry = next((e for e in grimoire_index if e["slug"] == best_slug), None)
            description = entry.get("description", "Agent spécialisé") if entry else "Agent spécialisé"

            mission = (
                f"[MODE VEILLE] En tant que spécialiste ({description}), "
                f"effectue une analyse ou action pertinente pour le système Prométhée. "
                f"Agis de ta propre initiative."
                f"{AUTONOMY_GUARDRAIL}"
            )

            print(f"   📖 GRIMOIRE INVOKE: {best_slug} — {description[:60]}")
            self._last_grimoire_slug = best_slug
            response = await orchestrator.dispatch_task(best_slug, {
                "mission": mission,
                "context": "PROTOCOLE_AUTONOMIE_GRIMOIRE",
                "force_local": True,
            })
            return response or {"status": "error", "result": "Pas de réponse du Grimoire."}

        except Exception as e:
            logger.warning(f"[AUTONOMY] Erreur routine Grimoire: {e}")
            return {"status": "error", "result": str(e)}

    async def _execute_council_debate(self) -> dict:
        """Lance un débat autonome Council : Recherche web → Débat éclairé."""
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

        return result

    async def _execute_memory_cleanup(self) -> dict:
        """Nettoie la mémoire RAG : purge les anciennes ET les mauvaise qualité."""
        try:
            from core.vector_store import ChromaMemoryManager
            mgr = ChromaMemoryManager.get_instance()
            if not mgr:
                return {"status": "error", "result": "ChromaDB indisponible."}

            removed_old = 0
            removed_quality = 0

            # Phase 1 : Purge des entrées anciennes (>60 jours)
            for coll_name in ["collective_wisdom"]:
                try:
                    coll = getattr(mgr, coll_name, None) or mgr.client.get_collection(coll_name)
                    count = coll.count()
                    if count < 10:
                        continue
                    results = coll.get(limit=min(count, 100), include=["metadatas", "documents"])
                    if not results or not results.get("ids"):
                        continue
                    now = time.time()
                    ids_to_delete = []
                    for i, meta in enumerate(results.get("metadatas", [])):
                        try:
                            ts = float(meta.get("timestamp", 0))
                            age_days = (now - ts) / 86400
                            if age_days > 60:
                                ids_to_delete.append(results["ids"][i])
                        except (ValueError, TypeError):
                            pass
                    if ids_to_delete:
                        coll.delete(ids=ids_to_delete[:20])
                        removed_old += len(ids_to_delete[:20])
                except Exception as e:
                    logger.warning(f"[MEMORY_CLEANUP] Erreur collection {coll_name}: {e}")

            # Phase 2 : Purge qualitative (textes courts, hallucinations non-latin)
            try:
                removed_quality = mgr.purge_low_quality(
                    min_length=100,
                    max_non_latin_ratio=0.10,
                    collection_name="collective_wisdom"
                )
            except Exception as e:
                logger.warning(f"[MEMORY_CLEANUP] Erreur purge qualitative: {e}")

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

    async def _execute_refactor_random(self) -> dict:
        """Propose un refactoring pour un fichier aléatoire."""
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
        if len(existing_council_specs) >= 4:
            logger.info("[COUNCIL→ACTION] Déjà 4 specs Council en attente, skip.")
            return

        # Extraire les fichiers cibles mentionnés dans le consensus
        valid_prefixes = ("core/", "Agents/")
        file_mentions = re.findall(r'((?:core|Agents)/[\w/]+\.py)', final_summary)
        file_mentions = [f for f in file_mentions if any(f.startswith(p) for p in valid_prefixes)]

        # Extraire les actions concrètes (lignes avec "ACTION", "IMPLÉMENTER", "AJOUTER", "MODIFIER")
        action_patterns = re.findall(
            r'(?:ACTION|IMPLÉMENTER|AJOUTER|MODIFIER|SUGGESTION|RECOMMANDATION)\s*[:\-]\s*(.+)',
            final_summary,
            re.IGNORECASE,
        )

        if not action_patterns and not file_mentions:
            logger.info("[COUNCIL→ACTION] Pas d'action concrète dans le consensus.")
            return

        # Construire la spec
        mission_short = topic.get("mission", "amélioration")[:80]
        spec_id = f"COUNCIL-{int(time.time()) % 100000}"

        # Prendre le premier fichier cible mentionné, ou un générique
        target_file = file_mentions[0] if file_mentions else "core/base_agent.py"

        # Résumé des actions
        actions_text = "\n".join(f"- {a.strip()}" for a in action_patterns[:3])
        if not actions_text:
            actions_text = final_summary[:500]

        spec = ImprovementSpec(
            id=spec_id,
            name=f"Council: {mission_short}",
            description=f"Issu d'un consensus Council.\n{actions_text}",
            category="intelligence",
            target_file=target_file,
            target_method="process_task",
            difficulty=2,
            code_template=f"# Spec générée par consensus Council\n# Mission: {mission_short}\n# Actions:\n{actions_text}",
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
