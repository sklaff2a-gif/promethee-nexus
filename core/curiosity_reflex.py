# core/curiosity_reflex.py — CuriosityReflex : Reflexe Instinctif d'Apprentissage
# Quand la curiosite est haute et qu'un concept inconnu est detecte,
# Promethee ouvre instinctivement un "livre" (Ollama), evalue la reponse,
# et escalade vers le web ou l'incubation si besoin.
# Cascade a 3 niveaux : Ollama (rapide, reflechir) -> Web search (profond, chercher) -> Incubation (ruminer)

import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, List

from core.event_bus.bus import bus

logger = logging.getLogger("CuriosityReflex")

# --- Constantes ---

CURIOSITY_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "curiosity_reflex_state.json"
)

CURIOSITE_DEPRIVATION_THRESHOLD = 50.0  # Plus bas que narratif (60) = instinctif
DAILY_QUERY_CAP = 15
PER_QUESTION_COOLDOWN = 300             # 5 min par sujet
GLOBAL_COOLDOWN = 60                    # 1 min entre queries
MIN_ANSWER_LENGTH = 80
MIN_QUALITY_SCORE = 0.4                 # Seuil Ollama satisfaisant
WEB_SEARCH_QUALITY_THRESHOLD = 0.3      # En dessous -> direct incubation
HISTORY_MAX = 50

# Marqueurs d'ignorance dans les reponses LLM
IGNORANCE_MARKERS = [
    "je ne sais pas", "i don't know", "je ne peux pas",
    "i cannot", "pas assez d'information", "aucune information",
    "je n'ai pas", "i'm not sure", "unknown", "pas de reponse",
    "impossible de", "je manque de", "beyond my",
]

# Templates de questions par source
QUESTION_TEMPLATES = {
    "KNOWLEDGE_GAP_DETECTED": "Explique brievement: {topic}. Contexte: systeme multi-agents Python.",
    "CURIOSITY_SPARK": "Qu'est-ce que {topic} ? Definition concise et utilite pour un projet Python.",
    "COUNCIL_QUESTION": "Resume en 3-5 phrases: {topic}. Points cles et implications pratiques.",
}


@dataclass
class CuriosityQuery:
    """Une question posee par le reflexe de curiosite."""
    id: str = ""
    question: str = ""
    topic: str = ""
    source_event: str = ""
    asked_at: float = 0.0
    ollama_answer: str = ""
    quality_score: float = 0.0
    escalated_to: str = ""      # "web_search" | "incubation" | ""
    web_answer: str = ""
    resolved: bool = False
    stored_in_memory: bool = False


class CuriosityReflex:
    """Singleton — reflexe instinctif d'apprentissage de Promethee."""

    _instance: Optional["CuriosityReflex"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._history: List[CuriosityQuery] = []
        self._total_queries: int = 0
        self._daily_queries: int = 0
        self._daily_date: str = ""
        self._last_query_time: float = 0.0
        self._topic_cooldowns: Dict[str, float] = {}  # topic -> timestamp
        self._processing: bool = False
        self._subscribed: bool = False
        self._load()

    # --- Init & Reset ---

    def init(self):
        """Souscrit aux evenements bus."""
        self._subscribe_events()
        logger.info("CURIOSITE: Reflexe d'apprentissage actif.")

    def reset(self):
        """Reset complet (utilise par les tests)."""
        self._history = []
        self._total_queries = 0
        self._daily_queries = 0
        self._daily_date = ""
        self._last_query_time = 0.0
        self._topic_cooldowns = {}
        self._processing = False
        self._subscribed = False
        self._initialized = False

    @classmethod
    def reset_singleton(cls):
        """Reset le singleton (utilise par les tests)."""
        if cls._instance is not None:
            cls._instance.reset()
            cls._instance = None

    # --- Souscriptions Event Bus ---

    def _subscribe_events(self):
        if self._subscribed:
            return
        self._subscribed = True
        bus.subscribe("KNOWLEDGE_GAP_DETECTED", self._on_knowledge_gap)
        bus.subscribe("CURIOSITY_SPARK", self._on_curiosity_spark)
        bus.subscribe("COUNCIL_QUESTION", self._on_council_question)

    async def _on_knowledge_gap(self, event: dict):
        topic = event.get("topic", "")
        if topic:
            asyncio.create_task(self._maybe_trigger(topic, "KNOWLEDGE_GAP_DETECTED"))

    async def _on_curiosity_spark(self, event: dict):
        topic = event.get("topic", "")
        if topic:
            asyncio.create_task(self._maybe_trigger(topic, "CURIOSITY_SPARK"))

    async def _on_council_question(self, event: dict):
        topic = event.get("topic", "")
        if topic:
            asyncio.create_task(self._maybe_trigger(topic, "COUNCIL_QUESTION"))

    # --- Declenchement (deterministe, 0 LLM) ---

    def _reset_daily_if_needed(self):
        """Remet a zero le compteur quotidien si le jour a change."""
        from datetime import date
        today = date.today().isoformat()
        if self._daily_date != today:
            self._daily_queries = 0
            self._daily_date = today

    def _should_trigger(self, topic: str) -> bool:
        """Verifie TOUTES les conditions de declenchement (deterministe)."""
        # 1. Pas deja en traitement
        if self._processing:
            return False

        # 2. Cap quotidien
        self._reset_daily_if_needed()
        if self._daily_queries >= DAILY_QUERY_CAP:
            return False

        # 3. Cooldown global
        now = time.time()
        if now - self._last_query_time < GLOBAL_COOLDOWN:
            return False

        # 4. Cooldown per-topic
        topic_lower = topic.lower().strip()[:200]
        last_topic_time = self._topic_cooldowns.get(topic_lower, 0.0)
        if now - last_topic_time < PER_QUESTION_COOLDOWN:
            return False

        # 5. CURIOSITE deprivation >= seuil
        try:
            from core.desire_engine import desires
            curiosite = desires.drives.get("CURIOSITE")
            if not curiosite or curiosite.deprivation < CURIOSITE_DEPRIVATION_THRESHOLD:
                return False
        except Exception:
            return False

        # 6. Circuit breaker Ollama pas ouvert
        try:
            from core.base_agent import BaseAgent
            if BaseAgent._ollama_circuit_is_open():
                return False
        except Exception:
            pass

        # 7. Pas deja appris ce sujet recemment (dedup historique)
        for q in self._history[-HISTORY_MAX:]:
            if q.topic.lower().strip() == topic_lower and q.resolved:
                return False

        return True

    # --- Cascade d'apprentissage ---

    async def _maybe_trigger(self, topic: str, source_event: str):
        """Point d'entree : verifie les conditions et lance le reflexe si OK."""
        if not self._should_trigger(topic):
            return
        await self._fire_reflex(topic, source_event)

    async def _fire_reflex(self, topic: str, source_event: str):
        """Cascade : Ollama -> Web -> Incubation."""
        self._processing = True
        try:
            topic = topic.strip()[:200]
            query = CuriosityQuery(
                id=uuid.uuid4().hex[:8],
                question=self._formulate_question(topic, source_event),
                topic=topic,
                source_event=source_event,
                asked_at=time.time(),
            )

            # Phase 1 : Demander a Ollama
            answer = await self._ask_ollama(query.question)
            query.ollama_answer = answer
            query.quality_score = self._evaluate_quality(query.question, answer)

            # Phase 2 : Evaluer et escalader si necessaire
            if query.quality_score >= MIN_QUALITY_SCORE:
                # Reponse satisfaisante → stocker et satisfaire
                await self._store_learning(query)
                await self._satisfy_curiosity(query)
                query.resolved = True
            elif query.quality_score >= WEB_SEARCH_QUALITY_THRESHOLD:
                # Reponse partielle → tenter web search
                query.escalated_to = "web_search"
                web_answer = await self._ask_web(topic)
                query.web_answer = web_answer
                if web_answer and len(web_answer) > MIN_ANSWER_LENGTH:
                    await self._store_learning(query)
                    await self._satisfy_curiosity(query)
                    query.resolved = True
                else:
                    # Web aussi insuffisant → incubation
                    query.escalated_to = "incubation"
                    await self._incubate(topic)
            else:
                # Reponse trop mauvaise → incubation directe
                query.escalated_to = "incubation"
                await self._incubate(topic)

            # Enregistrer
            self._record_query(query)

            # Publier l'evenement
            try:
                await bus.publish("CURIOSITY_LEARNING", {
                    "query_id": query.id,
                    "topic": query.topic,
                    "quality": query.quality_score,
                    "resolved": query.resolved,
                    "escalated_to": query.escalated_to,
                })
            except Exception:
                pass

            logger.info(
                f"CURIOSITE: '{topic[:40]}' → q={query.quality_score:.2f} "
                f"{'RESOLU' if query.resolved else 'ESCALADE→' + query.escalated_to}"
            )
        except Exception as e:
            logger.error(f"CURIOSITE: Erreur reflexe: {e}")
        finally:
            self._processing = False

    def _formulate_question(self, topic: str, source_event: str) -> str:
        """Genere une question a partir de templates deterministes."""
        template = QUESTION_TEMPLATES.get(source_event, QUESTION_TEMPLATES["CURIOSITY_SPARK"])
        return template.format(topic=topic)

    async def _ask_ollama(self, question: str) -> str:
        """Interroge Ollama directement via httpx (modele leger)."""
        try:
            import httpx
            from config import Config
            from core.base_agent import BaseAgent

            model = Config.ROUTER_MODEL  # qwen3:4b — leger et rapide
            url = getattr(Config, "OLLAMA_URL", "http://localhost:11434/api/generate")

            from core.base_agent import gpu_scheduler
            async with gpu_scheduler.access("curiosity_reflex"):
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        url,
                        json={
                            "model": model,
                            "prompt": question,
                            "stream": False,
                            "think": False,
                            "options": {"temperature": 0.3, "num_predict": 512},
                        },
                        timeout=60.0,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    answer = data.get("response", "").strip()

                    # Enregistrer succes circuit breaker
                    BaseAgent._ollama_record_success()
                    return answer

        except Exception as e:
            # Enregistrer timeout si applicable
            try:
                import httpx as _httpx
                from core.base_agent import BaseAgent as _BA
                if isinstance(e, (_httpx.ReadTimeout, _httpx.WriteTimeout, _httpx.PoolTimeout)):
                    _BA._ollama_record_timeout()
            except Exception:
                pass
            logger.warning(f"CURIOSITE: Ollama erreur: {e}")
            return ""

    def _evaluate_quality(self, question: str, answer: str) -> float:
        """Evalue la qualite d'une reponse avec 5 criteres heuristiques [0.0, 1.0]."""
        if not answer:
            return 0.0

        score = 0.0
        checks = 0
        total = 5

        # 1. Longueur suffisante (> MIN_ANSWER_LENGTH chars)
        checks += 1
        if len(answer) >= MIN_ANSWER_LENGTH:
            score += 1.0

        # 2. Keyword overlap >= 1 (mots de la question dans la reponse)
        checks += 1
        q_words = set(re.findall(r'\w{4,}', question.lower()))
        a_words = set(re.findall(r'\w{4,}', answer.lower()))
        overlap = len(q_words & a_words)
        if overlap >= 1:
            score += 1.0

        # 3. Pas de marqueurs d'ignorance
        checks += 1
        answer_lower = answer.lower()
        has_ignorance = any(m in answer_lower for m in IGNORANCE_MARKERS)
        if not has_ignorance:
            score += 1.0

        # 4. Ratio non-latin < 10%
        checks += 1
        if answer:
            non_latin = sum(1 for c in answer if ord(c) > 0x024F and not c.isspace())
            ratio = non_latin / len(answer)
            if ratio < 0.10:
                score += 1.0

        # 5. Au moins 2 phrases (point ou point-virgule ou retour ligne)
        checks += 1
        sentences = re.split(r'[.!?;\n]', answer)
        meaningful_sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
        if len(meaningful_sentences) >= 2:
            score += 1.0

        return round(score / total, 2)

    async def _ask_web(self, topic: str) -> str:
        """Tente une recherche web via WebSurfer (fallback DDG)."""
        try:
            from core.capabilities.web_surfer import WebSurfer
            surfer = WebSurfer()
            results = surfer.search(f"{topic} Python")
            if results:
                # Prendre les 2 premiers resultats
                snippets = []
                for r in results[:2]:
                    snippet = r.get("snippet", r.get("body", ""))
                    if snippet:
                        snippets.append(snippet)
                return " ".join(snippets)
            return ""
        except Exception as e:
            logger.warning(f"CURIOSITE: Web search erreur: {e}")
            return ""

    async def _incubate(self, topic: str):
        """Depose le sujet dans l'incubateur cognitif pour rumination asynchrone."""
        try:
            from core.incubation_cognitive import incubation
            incubation.deposit(topic, source="curiosity_reflex")
        except Exception as e:
            logger.debug(f"CURIOSITE: Incubation indisponible: {e}")

    async def _store_learning(self, query: CuriosityQuery):
        """Stocke l'apprentissage en memoire vectorielle (ChromaDB)."""
        try:
            from core.vector_store import ChromaMemoryManager
            mgr = ChromaMemoryManager("curiosity_learnings")
            content = query.ollama_answer or query.web_answer
            if content:
                mgr.store(
                    content=content[:1000],
                    metadata={
                        "topic": query.topic,
                        "source": query.source_event,
                        "quality": query.quality_score,
                        "query_id": query.id,
                    }
                )
                query.stored_in_memory = True
        except Exception as e:
            logger.debug(f"CURIOSITE: Stockage memoire echoue: {e}")

    async def _satisfy_curiosity(self, query: CuriosityQuery):
        """Notifie le DesireEngine que la curiosite a ete satisfaite."""
        try:
            from core.desire_engine import desires
            desires.on_event("CURIOSITY_SATISFIED")
        except Exception:
            pass

    def _record_query(self, query: CuriosityQuery):
        """Enregistre la query dans l'historique et met a jour les compteurs."""
        self._history.append(query)
        if len(self._history) > HISTORY_MAX:
            self._history = self._history[-HISTORY_MAX:]

        self._total_queries += 1
        self._daily_queries += 1
        self._last_query_time = time.time()
        self._topic_cooldowns[query.topic.lower().strip()[:200]] = time.time()
        self._save()

    # --- Scoring (Couche 22) ---

    def compute_curiosity_bonus(self, intent: str) -> float:
        """Bonus [0.0, +1.5] pour les routines liees a la curiosite.
        Booste VEILLE_SILENCIEUSE et COUNCIL_DEBATE quand le reflexe est actif."""
        affinity = {
            "VEILLE_SILENCIEUSE": 1.0,
            "COUNCIL_DEBATE": 0.5,
            "DROPZONE_SCAN": 0.7,
            "ROADMAP_RESEARCH": 0.8,
        }
        base = affinity.get(intent, 0.0)
        if base == 0.0:
            return 0.0

        # Bonus proportionnel aux queries recentes (dernieres 2h)
        now = time.time()
        recent = sum(1 for q in self._history if now - q.asked_at < 7200 and q.resolved)
        if recent == 0:
            return 0.0

        # Plus il y a eu de questions recentes resolues, plus le bonus est fort
        factor = min(1.0, recent / 5.0)
        return round(min(1.5, base * factor * 1.5), 2)

    # --- Contexte (purpose_context) ---

    def get_curiosity_context(self) -> str:
        """Genere un contexte injectable dans purpose_context."""
        recent_topics = []
        now = time.time()
        for q in reversed(self._history[-5:]):
            if now - q.asked_at < 7200 and q.resolved:
                recent_topics.append(q.topic[:50])
        if not recent_topics:
            return ""
        topics_str = ", ".join(recent_topics[:3])
        return f"[CURIOSITE] Sujets recemment appris: {topics_str}"

    # --- Stats (API + snapshot) ---

    def get_stats(self) -> Dict[str, Any]:
        """Stats pour l'API et le snapshot SelfAwareness."""
        self._reset_daily_if_needed()
        now = time.time()
        recent_resolved = sum(1 for q in self._history if now - q.asked_at < 7200 and q.resolved)
        recent_escalated = sum(1 for q in self._history if now - q.asked_at < 7200 and q.escalated_to)
        return {
            "total_queries": self._total_queries,
            "daily_queries": self._daily_queries,
            "daily_cap": DAILY_QUERY_CAP,
            "recent_resolved_2h": recent_resolved,
            "recent_escalated_2h": recent_escalated,
            "history_size": len(self._history),
            "processing": self._processing,
            "last_topics": [q.topic[:50] for q in self._history[-3:]],
        }

    # --- Persistance ---

    def _save(self):
        """Sauvegarde atomique de l'etat."""
        try:
            os.makedirs(os.path.dirname(CURIOSITY_STATE_FILE), exist_ok=True)
            data = {
                "version": "1.0",
                "total_queries": self._total_queries,
                "daily_queries": self._daily_queries,
                "daily_date": self._daily_date,
                "last_query_time": self._last_query_time,
                "topic_cooldowns": self._topic_cooldowns,
                "history": [asdict(q) for q in self._history[-HISTORY_MAX:]],
            }
            tmp_path = CURIOSITY_STATE_FILE + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, CURIOSITY_STATE_FILE)
        except Exception as e:
            logger.error(f"CURIOSITE: Sauvegarde echouee: {e}")

    def _load(self):
        """Charge l'etat depuis le fichier JSON."""
        try:
            with open(CURIOSITY_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._total_queries = data.get("total_queries", 0)
            self._daily_queries = data.get("daily_queries", 0)
            self._daily_date = data.get("daily_date", "")
            self._last_query_time = data.get("last_query_time", 0.0)
            self._topic_cooldowns = data.get("topic_cooldowns", {})
            history_data = data.get("history", [])
            self._history = []
            for h in history_data[-HISTORY_MAX:]:
                self._history.append(CuriosityQuery(
                    id=h.get("id", ""),
                    question=h.get("question", ""),
                    topic=h.get("topic", ""),
                    source_event=h.get("source_event", ""),
                    asked_at=h.get("asked_at", 0.0),
                    ollama_answer=h.get("ollama_answer", ""),
                    quality_score=h.get("quality_score", 0.0),
                    escalated_to=h.get("escalated_to", ""),
                    web_answer=h.get("web_answer", ""),
                    resolved=h.get("resolved", False),
                    stored_in_memory=h.get("stored_in_memory", False),
                ))
            self._reset_daily_if_needed()
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        except Exception as e:
            logger.warning(f"CURIOSITE: Chargement echoue: {e}")


# Singleton global
curiosity = CuriosityReflex()
