# core/evolution_catalog.py — Catalogue d'Améliorations Pré-Définies pour l'Évolution
# Le LLM passe de "créateur" à "sélecteur" : il choisit parmi des specs pré-écrites.
# Les specs sont en dur dans le code. Le JSON ne stocke que le tracking (status, attempts).

import json
import os
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

logger = logging.getLogger("EvolutionCatalog")

CATALOG_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "evolution_catalog.json"
)


@dataclass
class ImprovementSpec:
    id: str
    name: str
    description: str
    category: str       # performance|resilience|intelligence|memory|observability|security
    target_file: str
    target_method: str
    difficulty: int      # 1-3
    code_template: str
    validation: str
    tags: list = field(default_factory=list)
    dependencies: list = field(default_factory=list)
    combinable_with: list = field(default_factory=list)
    # Tracking (persisté via JSON)
    status: str = "available"  # available|attempted|pending_deploy|deployed|failed|combined|generated|confirmed|rolled_back
    attempts: int = 0
    max_attempts: int = 3
    last_attempt: Optional[str] = None
    deployed_at: Optional[str] = None
    confirmed_at: Optional[str] = None
    failure_reasons: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Catalogue des 46 specs (28 originales + 18 sur fichiers non-protégés)
# ---------------------------------------------------------------------------

def _build_catalog() -> Dict[str, ImprovementSpec]:
    specs = {}

    # === PERFORMANCE (7 specs) ===

    specs["PERF-001"] = ImprovementSpec(
        id="PERF-001",
        name="Cache LRU Router",
        description="Cache dict avec TTL sur les résultats de classification Niveau 1 du Router.",
        category="performance",
        target_file="core/router.py",
        target_method="classify_intent",
        difficulty=1,
        code_template="""
# Ajout en haut du fichier (attributs de classe RouterAgent)
_intent_cache: dict = {}
_CACHE_TTL = 300  # 5 minutes

@classmethod
def _get_cached_intent(cls, mission: str) -> str | None:
    key = mission.strip().lower()[:200]
    entry = cls._intent_cache.get(key)
    if entry and (time.time() - entry["ts"]) < cls._CACHE_TTL:
        return entry["result"]
    return None

@classmethod
def _set_cached_intent(cls, mission: str, result: str):
    key = mission.strip().lower()[:200]
    cls._intent_cache[key] = {"result": result, "ts": time.time()}
    # Eviter croissance infinie
    if len(cls._intent_cache) > 500:
        oldest = sorted(cls._intent_cache, key=lambda k: cls._intent_cache[k]["ts"])
        for k in oldest[:100]:
            del cls._intent_cache[k]
""",
        validation="Appeler classify_intent 2x avec la même mission, vérifier que le 2e appel ne fait pas d'appel LLM.",
        tags=["cache", "performance", "router"],
        combinable_with=["PERF-003"],
    )

    specs["PERF-002"] = ImprovementSpec(
        id="PERF-002",
        name="Timeout Ollama",
        description="Ajoute asyncio.wait_for avec timeout=60s sur les appels Ollama pour éviter les blocages infinis.",
        category="performance",
        target_file="core/base_agent.py",
        target_method="_call_ollama",
        difficulty=1,
        code_template="""
# Dans _call_ollama, wrapper l'appel httpx avec un timeout asyncio
try:
    result = await asyncio.wait_for(
        self._raw_ollama_call(prompt, model),
        timeout=60.0
    )
except asyncio.TimeoutError:
    logger.warning(f"[{self.name}] Ollama timeout après 60s")
    return ""
""",
        validation="Mock un appel Ollama qui prend 120s, vérifier que le timeout coupe après 60s.",
        tags=["timeout", "performance", "ollama"],
        combinable_with=["RES-002"],
    )

    specs["PERF-003"] = ImprovementSpec(
        id="PERF-003",
        name="Short-circuit complexité",
        description="Court-circuite l'évaluation de complexité pour les cas triviaux : prompt court → simple, pipeline interne → simple.",
        category="performance",
        target_file="core/base_agent.py",
        target_method="_evaluate_complexity",
        difficulty=1,
        code_template="""
# Au début de _evaluate_complexity :
if len(prompt) < 100:
    return "simple"
internal_markers = ("EVOLUTION_PIPELINE", "PROTOCOLE_AUTONOMIE", "DROPZONE_ANALYSIS")
for marker in internal_markers:
    if marker in prompt:
        return "simple"
# ... reste de l'évaluation normale
""",
        validation="Appeler _evaluate_complexity avec un prompt de 50 chars, vérifier retour 'simple' sans appel LLM.",
        tags=["performance", "complexity", "shortcircuit"],
        combinable_with=["PERF-001"],
    )

    specs["PERF-004"] = ImprovementSpec(
        id="PERF-004",
        name="Cache recall RAG",
        description="Cache en mémoire des 20 derniers recall() avec TTL 5min pour éviter les appels ChromaDB répétitifs.",
        category="performance",
        target_file="core/base_agent.py",
        target_method="recall",
        difficulty=1,
        code_template="""
# Attribut d'instance dans __init__ :
self._recall_cache: dict = {}
_RECALL_CACHE_TTL = 300  # 5 min
_RECALL_CACHE_MAX = 20

# Au début de recall() :
cache_key = query.strip().lower()[:200]
cached = self._recall_cache.get(cache_key)
if cached and (time.time() - cached["ts"]) < self._RECALL_CACHE_TTL:
    return cached["result"]

# Après le résultat ChromaDB :
self._recall_cache[cache_key] = {"result": result, "ts": time.time()}
if len(self._recall_cache) > self._RECALL_CACHE_MAX:
    oldest_key = min(self._recall_cache, key=lambda k: self._recall_cache[k]["ts"])
    del self._recall_cache[oldest_key]
""",
        validation="Appeler recall() 2x avec le même query, vérifier que ChromaDB n'est appelé qu'une fois.",
        tags=["cache", "performance", "memory", "rag"],
        combinable_with=["MEM-001"],
    )

    specs["PERF-005"] = ImprovementSpec(
        id="PERF-005",
        name="Lazy import ChromaDB",
        description="Retarde l'import de ChromaDB au premier appel remember()/recall() au lieu du __init__.",
        category="performance",
        target_file="core/base_agent.py",
        target_method="__init__",
        difficulty=2,
        code_template="""
# Dans __init__, remplacer l'init immédiate de ChromaDB par :
self._memory_manager = None  # Lazy init
self.has_memory = ChromaMemoryManager is not None

# Nouvelle méthode :
def _get_memory(self):
    if self._memory_manager is None and ChromaMemoryManager is not None:
        try:
            self._memory_manager = ChromaMemoryManager.get_instance()
        except Exception as e:
            logger.warning(f"[{self.name}] ChromaDB init failed: {e}")
            self.has_memory = False
    return self._memory_manager

# Dans remember() et recall(), remplacer self._memory_manager par self._get_memory()
""",
        validation="Créer un agent, vérifier que ChromaDB n'est pas importé. Appeler remember(), vérifier que ChromaDB est maintenant chargé.",
        tags=["performance", "lazy", "import", "chromadb"],
        combinable_with=["PERF-004"],
    )

    specs["PERF-006"] = ImprovementSpec(
        id="PERF-006",
        name="Batch bus publish",
        description="Collecte les events pendant 50ms puis publie en batch pour réduire la pression sur l'event loop.",
        category="performance",
        target_file="core/event_bus/bus.py",
        target_method="publish",
        difficulty=3,
        code_template="""
# Attributs dans __init__ du EventBus :
self._batch_queue: list = []
self._batch_task: asyncio.Task | None = None
self._BATCH_DELAY = 0.05  # 50ms

async def publish_batched(self, event_type: str, data: dict):
    self._batch_queue.append((event_type, data))
    if self._batch_task is None or self._batch_task.done():
        self._batch_task = asyncio.create_task(self._flush_batch())

async def _flush_batch(self):
    await asyncio.sleep(self._BATCH_DELAY)
    queue, self._batch_queue = self._batch_queue, []
    for event_type, data in queue:
        await self.publish(event_type, data)
""",
        validation="Publier 10 events en <50ms, vérifier que les subscribers ne reçoivent qu'un batch.",
        tags=["performance", "batch", "eventbus"],
        dependencies=[],
    )

    specs["PERF-007"] = ImprovementSpec(
        id="PERF-007",
        name="Cache scoring routines",
        description="Cache le score de base des routines (sans jitter) entre les appels si le contexte n'a pas changé.",
        category="performance",
        target_file="core/autonomy_engine.py",
        target_method="score_routines",
        difficulty=2,
        code_template="""
# Attributs dans la classe AutonomyEngine :
_score_cache: dict = {}
_score_cache_context_hash: str = ""

def _context_hash(self) -> str:
    # Hash simple basé sur les compteurs qui influencent le score
    return f"{self.error_streak}:{self.daily_count}:{len(self._routine_history)}"

def score_routines(self, ...):
    ctx_hash = self._context_hash()
    if ctx_hash == self._score_cache_context_hash and self._score_cache:
        # Réutiliser les scores de base, ajouter seulement le jitter
        return {k: v + random.uniform(-0.3, 0.3) for k, v in self._score_cache.items()}

    # Calculer les scores normalement...
    base_scores = self._compute_base_scores(...)
    self._score_cache = base_scores
    self._score_cache_context_hash = ctx_hash
    return {k: v + random.uniform(-0.3, 0.3) for k, v in base_scores.items()}
""",
        validation="Appeler score_routines 2x sans changement de contexte, vérifier que les scores de base sont identiques.",
        tags=["performance", "cache", "autonomy"],
    )

    # === RESILIENCE (5 specs) ===

    specs["RES-001"] = ImprovementSpec(
        id="RES-001",
        name="Retry backoff Cloud",
        description="3 tentatives avec délai exponentiel 1s→2s→4s sur les appels Cloud (ConnectionError, TimeoutError).",
        category="resilience",
        target_file="core/base_agent.py",
        target_method="generate_content",
        difficulty=2,
        code_template="""
# Dans la section Cloud de generate_content :
max_retries = 3
for attempt in range(max_retries):
    try:
        result = await self._call_cloud(prompt, model_cascade)
        if result:
            return result
        break  # Résultat vide mais pas d'erreur
    except (ConnectionError, TimeoutError, httpx.HTTPError) as e:
        wait = 2 ** attempt  # 1, 2, 4 secondes
        logger.warning(f"[{self.name}] Cloud retry {attempt+1}/{max_retries} après {wait}s: {e}")
        if attempt < max_retries - 1:
            await asyncio.sleep(wait)
        else:
            logger.error(f"[{self.name}] Cloud échec après {max_retries} tentatives")
            # Fallback sur local
""",
        validation="Mock un appel Cloud qui échoue 2x puis réussit, vérifier que le 3e appel retourne le résultat.",
        tags=["resilience", "retry", "cloud", "backoff"],
        combinable_with=["RES-002"],
    )

    specs["RES-002"] = ImprovementSpec(
        id="RES-002",
        name="Circuit breaker Ollama",
        description="Après 3 échecs consécutifs Ollama, skip pendant 60s. Reset automatique.",
        category="resilience",
        target_file="core/base_agent.py",
        target_method="_call_ollama",
        difficulty=2,
        code_template="""
# Variables de classe BaseAgent :
_ollama_circuit_open = False
_ollama_circuit_until = 0.0
_ollama_consecutive_failures = 0
_OLLAMA_CIRCUIT_THRESHOLD = 3
_OLLAMA_CIRCUIT_COOLDOWN = 60  # secondes

@classmethod
def _check_ollama_circuit(cls) -> bool:
    if cls._ollama_circuit_open:
        if time.time() >= cls._ollama_circuit_until:
            cls._ollama_circuit_open = False
            cls._ollama_consecutive_failures = 0
            logger.info("Circuit breaker Ollama: FERME (reset)")
            return False  # Circuit fermé
        return True  # Circuit encore ouvert
    return False

@classmethod
def _record_ollama_failure(cls):
    cls._ollama_consecutive_failures += 1
    if cls._ollama_consecutive_failures >= cls._OLLAMA_CIRCUIT_THRESHOLD:
        cls._ollama_circuit_open = True
        cls._ollama_circuit_until = time.time() + cls._OLLAMA_CIRCUIT_COOLDOWN
        logger.warning(f"Circuit breaker Ollama: OUVERT pour {cls._OLLAMA_CIRCUIT_COOLDOWN}s")

@classmethod
def _record_ollama_success(cls):
    cls._ollama_consecutive_failures = 0
""",
        validation="Simuler 3 échecs Ollama, vérifier que le 4e appel est skippé. Attendre 60s, vérifier que ça fonctionne à nouveau.",
        tags=["resilience", "circuit-breaker", "ollama"],
        combinable_with=["RES-001", "PERF-002"],
    )

    specs["RES-003"] = ImprovementSpec(
        id="RES-003",
        name="Dégradation gracieuse mémoire",
        description="Si ChromaDB lève une exception dans remember()/recall(), log warning et continue sans crash.",
        category="resilience",
        target_file="core/base_agent.py",
        target_method="remember",
        difficulty=1,
        code_template="""
# Dans remember() :
def remember(self, content: str, metadata: dict = None):
    try:
        # ... code existant ChromaDB ...
        pass
    except Exception as e:
        logger.warning(f"[{self.name}] remember() failed (graceful degradation): {e}")

# Dans recall() :
def recall(self, query: str, limit: int = 3) -> str:
    try:
        # ... code existant ChromaDB ...
        pass
    except Exception as e:
        logger.warning(f"[{self.name}] recall() failed (graceful degradation): {e}")
        return ""
""",
        validation="Mock ChromaDB pour lever une exception, vérifier que remember() ne crash pas et recall() retourne ''.",
        tags=["resilience", "memory", "chromadb", "graceful"],
    )

    specs["RES-004"] = ImprovementSpec(
        id="RES-004",
        name="Recovery auto bus subscribers",
        description="Désinscrit automatiquement un subscriber après 3 exceptions consécutives.",
        category="resilience",
        target_file="core/event_bus/bus.py",
        target_method="_safe_call",
        difficulty=2,
        code_template="""
# Attribut dans EventBus.__init__ :
self._subscriber_errors: dict = {}  # {callback_id: count}

async def _safe_call(self, callback, event: dict):
    cb_id = id(callback)
    try:
        await callback(event)
        self._subscriber_errors.pop(cb_id, None)  # Reset on success
    except Exception as e:
        count = self._subscriber_errors.get(cb_id, 0) + 1
        self._subscriber_errors[cb_id] = count
        logger.warning(f"Subscriber {callback.__name__} error ({count}/3): {e}")
        if count >= 3:
            self._unsubscribe_callback(callback)
            logger.error(f"Subscriber {callback.__name__} désabonné après 3 erreurs")
""",
        validation="Créer un subscriber qui lève 3 exceptions, vérifier qu'il est désabonné automatiquement.",
        tags=["resilience", "eventbus", "recovery"],
    )

    specs["RES-005"] = ImprovementSpec(
        id="RES-005",
        name="Heartbeat Ollama",
        description="Publie un event OLLAMA_DOWN sur le bus si Ollama ne répond pas au health check.",
        category="resilience",
        target_file="core/autonomy_engine.py",
        target_method="SystemHealthCheck.run",
        difficulty=1,
        code_template="""
# Dans SystemHealthCheck.run(), après le check Ollama :
if not ollama_alive:
    await bus.publish("OLLAMA_DOWN", {
        "timestamp": datetime.now().isoformat(),
        "message": "Ollama ne répond pas au health check"
    })
""",
        validation="Mock Ollama comme down, exécuter le health check, vérifier que l'event OLLAMA_DOWN est publié.",
        tags=["resilience", "ollama", "heartbeat", "monitoring"],
        combinable_with=["RES-002"],
    )

    # === INTELLIGENCE (5 specs) ===

    specs["INT-001"] = ImprovementSpec(
        id="INT-001",
        name="Trimming contexte RAG",
        description="Si le prompt > 4000 chars, tronque le contexte RAG (garde les 500 premiers et 500 derniers chars).",
        category="intelligence",
        target_file="core/base_agent.py",
        target_method="generate_content",
        difficulty=1,
        code_template="""
# Avant l'appel LLM dans generate_content :
def _trim_context(self, context: str, max_len: int = 4000) -> str:
    if len(context) <= max_len:
        return context
    head = context[:500]
    tail = context[-500:]
    return f"{head}\\n[... {len(context) - 1000} chars tronqués ...]\\n{tail}"
""",
        validation="Passer un contexte de 8000 chars, vérifier que le résultat fait ~1050 chars.",
        tags=["intelligence", "trimming", "context", "rag"],
        combinable_with=["PERF-004"],
    )

    specs["INT-002"] = ImprovementSpec(
        id="INT-002",
        name="Few-shot Council",
        description="Ajoute 2-3 exemples de bonnes critiques et bons consensus dans le prompt Council.",
        category="intelligence",
        target_file="core/council.py",
        target_method="_build_prompt",
        difficulty=2,
        code_template="""
# Exemples à ajouter dans le prompt Council :
_FEW_SHOT_EXAMPLES = '''
EXEMPLE DE BONNE CRITIQUE :
"Le code proposé n'a pas de gestion d'erreur pour les appels réseau.
 SUGGESTION: Ajouter un try/except avec retry autour de l'appel httpx.
 RISQUE: 3/5 — panne réseau = crash silencieux."

EXEMPLE DE BON CONSENSUS :
"CONSENSUS: Implémenter le cache LRU avec TTL de 300s.
 JUSTIFICATION: Réduit les appels LLM de 60% sans risque de données périmées.
 ACTION: Modifier core/router.py:classify_intent."
'''

# Dans _build_prompt, ajouter avant la question :
prompt += f"\\n{_FEW_SHOT_EXAMPLES}\\n"
""",
        validation="Vérifier que le prompt Council contient les exemples few-shot.",
        tags=["intelligence", "council", "fewshot", "prompt"],
    )

    specs["INT-003"] = ImprovementSpec(
        id="INT-003",
        name="Confidence scoring Router",
        description="Le Router retourne (agent, confidence) au lieu de juste agent. Confidence < 0.5 → fallback strategist.",
        category="intelligence",
        target_file="core/router.py",
        target_method="classify_intent",
        difficulty=3,
        code_template="""
# classify_intent retourne maintenant un tuple :
@classmethod
async def classify_intent(cls, mission: str) -> tuple[str, float]:
    # Niveau 0 (Blind Trust) — confidence = 1.0
    agent = cls._check_blind_trust(mission)
    if agent:
        return (agent, 1.0)

    # Niveau 1 (Mots-clés) — confidence = 0.9
    agent = cls._check_keywords(mission)
    if agent:
        return (agent, 0.9)

    # Niveau 2 (LLM) — confidence variable
    agent, confidence = await cls._check_llm(mission)
    if confidence < 0.5:
        logger.warning(f"Router: confidence faible ({confidence:.2f}), fallback strategist")
        return ("strategist", confidence)
    return (agent, confidence)
""",
        validation="Tester avec une mission ambiguë, vérifier que confidence < 0.5 → strategist.",
        tags=["intelligence", "router", "confidence"],
        dependencies=[],
        combinable_with=["PERF-001"],
    )

    specs["INT-004"] = ImprovementSpec(
        id="INT-004",
        name="Prompt adaptatif PSYCHE",
        description="Injecte le mood et le trait dominant de l'agent dans chaque prompt via PSYCHE.",
        category="intelligence",
        target_file="core/base_agent.py",
        target_method="generate_content",
        difficulty=2,
        code_template="""
# Dans generate_content, avant l'appel LLM :
def _get_psyche_context(self) -> str:
    try:
        from core.psyche import psyche
        traits = psyche.get_agent_traits(self.name)
        if traits:
            dominant = max(traits.items(), key=lambda x: x[1])
            from core.self_awareness import awareness
            snap = awareness.get_latest_snapshot()
            mood = snap.get("mood", "équilibré") if snap else "équilibré"
            return f"[Tu es en mode {mood}. Trait dominant: {dominant[0]} ({dominant[1]:.0f}/100)]"
    except Exception:
        pass
    return ""
""",
        validation="Vérifier que le prompt contient le mood et le trait dominant de l'agent.",
        tags=["intelligence", "psyche", "prompt", "adaptive"],
    )

    specs["INT-005"] = ImprovementSpec(
        id="INT-005",
        name="Résumé échecs CI/CD",
        description="Avant chaque cycle Evolution, consulter les 5 derniers échecs CI/CD et les injecter dans le prompt.",
        category="intelligence",
        target_file="Agents/evolution_agent.py",
        target_method="process_task",
        difficulty=1,
        code_template="""
# Avant l'envoi au Coder dans le pipeline Evolution :
def _get_recent_failures(self) -> str:
    try:
        from core.ci_pipeline import ci_memory
        failures = ci_memory.get_recent_failures(limit=5)
        if failures:
            summary = "\\n".join(f"- {f['file']}: {f['reason']}" for f in failures)
            return f"\\nÉVITE ces erreurs passées :\\n{summary}\\n"
    except Exception:
        pass
    return ""
""",
        validation="Vérifier que le prompt du Coder contient le résumé des échecs récents.",
        tags=["intelligence", "evolution", "ci", "learning"],
    )

    # === MEMORY (4 specs) ===

    specs["MEM-001"] = ImprovementSpec(
        id="MEM-001",
        name="Consolidation mémoire",
        description="Tous les 50 remember(), fusionne les souvenirs similaires (cosine > 0.9).",
        category="memory",
        target_file="core/base_agent.py",
        target_method="remember",
        difficulty=3,
        code_template="""
# Compteur dans __init__ :
self._remember_count = 0
_CONSOLIDATION_INTERVAL = 50

def remember(self, content, metadata=None):
    # ... existant ...
    self._remember_count += 1
    if self._remember_count % self._CONSOLIDATION_INTERVAL == 0:
        self._consolidate_memory()

def _consolidate_memory(self):
    '''Fusionne les souvenirs proches (cosine > 0.9).'''
    try:
        collection = self._get_memory().collection
        all_docs = collection.get(include=["documents", "embeddings"])
        # Identifier les paires similaires et fusionner
        # ... logique de consolidation ...
        logger.info(f"[{self.name}] Consolidation mémoire effectuée")
    except Exception as e:
        logger.warning(f"[{self.name}] Consolidation échouée: {e}")
""",
        validation="Insérer 50 souvenirs similaires, vérifier que la consolidation réduit le nombre.",
        tags=["memory", "consolidation", "rag"],
        combinable_with=["MEM-003"],
    )

    specs["MEM-002"] = ImprovementSpec(
        id="MEM-002",
        name="Mémoire épisodique",
        description="Stocke des épisodes complets {mission, agent, context, result, status, duration} dans une collection ChromaDB dédiée.",
        category="memory",
        target_file="core/orchestrator.py",
        target_method="dispatch_task",
        difficulty=2,
        code_template="""
# Dans dispatch_task, après l'exécution de l'agent :
import time

start_time = time.time()
result = await agent.process_task(payload)
duration = time.time() - start_time

# Stocker l'épisode
try:
    from core.vector_store import ChromaMemoryManager
    memory = ChromaMemoryManager.get_instance()
    episode = f"EPISODE: {agent_name} | mission={mission[:200]} | status={result.get('status')} | duration={duration:.1f}s"
    memory.add_to_collection("episodes", episode, {
        "agent": agent_name, "status": result.get("status"), "duration": duration
    })
except Exception:
    pass
""",
        validation="Dispatcher une mission, vérifier qu'un épisode est enregistré dans la collection 'episodes'.",
        tags=["memory", "episodic", "orchestrator"],
        combinable_with=["MEM-004"],
    )

    specs["MEM-003"] = ImprovementSpec(
        id="MEM-003",
        name="Oubli actif",
        description="Si un souvenir a un score de pertinence très bas lors du recall(), le supprimer de ChromaDB.",
        category="memory",
        target_file="core/base_agent.py",
        target_method="recall",
        difficulty=2,
        code_template="""
# Dans recall(), après avoir récupéré les résultats ChromaDB :
_DECAY_THRESHOLD = 0.1

# Si des résultats ont un score < threshold, les supprimer
if results and "distances" in results:
    for i, distance in enumerate(results["distances"][0]):
        similarity = 1.0 - distance  # ChromaDB distance → similarity
        if similarity < _DECAY_THRESHOLD and "ids" in results:
            doc_id = results["ids"][0][i]
            try:
                collection.delete(ids=[doc_id])
                logger.info(f"[{self.name}] Oubli actif: supprimé souvenir {doc_id} (score={similarity:.3f})")
            except Exception:
                pass
""",
        validation="Insérer un souvenir très ancien/non pertinent, vérifier qu'il est supprimé au recall.",
        tags=["memory", "decay", "cleanup"],
        combinable_with=["MEM-001"],
    )

    specs["MEM-004"] = ImprovementSpec(
        id="MEM-004",
        name="Mémoire partagée inter-agents",
        description="Collection 'shared_insights' dans ChromaDB, accessible à tous les agents. Le Strategist y écrit, le Coder y lit.",
        category="memory",
        target_file="core/base_agent.py",
        target_method="remember",
        difficulty=2,
        code_template="""
# Nouvelles méthodes dans BaseAgent :
def share_insight(self, content: str, metadata: dict = None):
    '''Partage un insight dans la mémoire partagée.'''
    try:
        memory = self._get_memory()
        if memory:
            meta = {"agent": self.name, "ts": datetime.now().isoformat()}
            if metadata:
                meta.update(metadata)
            memory.add_to_collection("shared_insights", content, meta)
    except Exception as e:
        logger.warning(f"[{self.name}] share_insight failed: {e}")

def recall_shared(self, query: str, limit: int = 3) -> str:
    '''Rappelle des insights partagés par d'autres agents.'''
    try:
        memory = self._get_memory()
        if memory:
            results = memory.query_collection("shared_insights", query, n_results=limit)
            if results and results.get("documents"):
                return "\\n".join(results["documents"][0])
    except Exception as e:
        logger.warning(f"[{self.name}] recall_shared failed: {e}")
    return ""
""",
        validation="Agent A partage un insight, Agent B le recall. Vérifier que B obtient le contenu de A.",
        tags=["memory", "shared", "inter-agent"],
        combinable_with=["MEM-002"],
    )

    # === OBSERVABILITY (4 specs) ===

    specs["OBS-001"] = ImprovementSpec(
        id="OBS-001",
        name="Métriques par agent",
        description="Ajoute un dict per_agent_metrics dans le snapshot SelfAwareness : {agent: {calls, successes, avg_latency}}.",
        category="observability",
        target_file="core/self_awareness.py",
        target_method="generate_snapshot",
        difficulty=2,
        code_template="""
# Attribut dans __init__ :
self._per_agent_metrics: dict = {}  # {agent: {"calls": 0, "successes": 0, "total_latency": 0.0}}

# Handler bus :
async def _on_agent_response(self, event: dict):
    self._mission_count += 1
    agent = event.get("agent", "unknown")
    metrics = self._per_agent_metrics.setdefault(agent, {"calls": 0, "successes": 0, "total_latency": 0.0})
    metrics["calls"] += 1
    if event.get("status") == "success":
        self._mission_success += 1
        metrics["successes"] += 1
    metrics["total_latency"] += event.get("duration", 0.0)

# Dans generate_snapshot :
per_agent = {}
for agent, m in self._per_agent_metrics.items():
    per_agent[agent] = {
        "calls": m["calls"],
        "successes": m["successes"],
        "avg_latency": (m["total_latency"] / m["calls"]) if m["calls"] > 0 else 0
    }
snapshot["per_agent_metrics"] = per_agent
""",
        validation="Publier 3 MISSION_FINISHED avec agents différents, vérifier que le snapshot contient les métriques séparées.",
        tags=["observability", "metrics", "per-agent"],
        combinable_with=["OBS-002"],
    )

    specs["OBS-002"] = ImprovementSpec(
        id="OBS-002",
        name="Compteur Cloud par agent",
        description="Remplace _cloud_call_count global par un dict _cloud_calls_by_agent.",
        category="observability",
        target_file="core/base_agent.py",
        target_method="generate_content",
        difficulty=1,
        code_template="""
# Variable de classe BaseAgent :
_cloud_calls_by_agent: dict = {}  # {agent_name: count}

# Dans generate_content, quand un appel Cloud est fait :
cls = type(self)
cls._cloud_calls_by_agent[self.name] = cls._cloud_calls_by_agent.get(self.name, 0) + 1

# Méthode de classe pour accéder :
@classmethod
def get_cloud_usage(cls) -> dict:
    return dict(cls._cloud_calls_by_agent)
""",
        validation="Faire des appels Cloud avec 2 agents, vérifier que le compteur distingue les agents.",
        tags=["observability", "cloud", "budget"],
        combinable_with=["OBS-001"],
    )

    specs["OBS-003"] = ImprovementSpec(
        id="OBS-003",
        name="Profiling pipelines",
        description="Mesure le temps de chaque dispatch et publie PIPELINE_TIMING sur le bus.",
        category="observability",
        target_file="core/orchestrator.py",
        target_method="dispatch_task",
        difficulty=1,
        code_template="""
# Dans dispatch_task :
import time

start = time.time()
result = await agent.process_task(payload)
duration_ms = (time.time() - start) * 1000

await bus.publish("PIPELINE_TIMING", {
    "agent": agent_name,
    "duration_ms": round(duration_ms, 1),
    "timestamp": datetime.now().isoformat()
})
logger.info(f"Pipeline {agent_name}: {duration_ms:.0f}ms")
""",
        validation="Dispatcher une mission, vérifier que l'event PIPELINE_TIMING est publié avec la durée.",
        tags=["observability", "profiling", "timing"],
        combinable_with=["OBS-001"],
    )

    specs["OBS-004"] = ImprovementSpec(
        id="OBS-004",
        name="Dashboard santé enrichi",
        description="Enrichit get_self_context() avec les métriques per-agent pour les Councils.",
        category="observability",
        target_file="core/self_awareness.py",
        target_method="get_self_context",
        difficulty=1,
        code_template="""
# Dans get_self_context, après les infos existantes :
per_agent = snap.get("per_agent_metrics", {})
if per_agent:
    top3 = sorted(per_agent.items(), key=lambda x: x[1].get("calls", 0), reverse=True)[:3]
    agent_info = ", ".join(f"{a}({m['calls']}appels)" for a, m in top3)
    parts.append(f"Top agents: {agent_info}.")
""",
        validation="Vérifier que get_self_context contient les top agents quand des métriques existent.",
        tags=["observability", "dashboard", "council"],
        dependencies=["OBS-001"],
    )

    # === SECURITY (3 specs) ===

    specs["SEC-001"] = ImprovementSpec(
        id="SEC-001",
        name="Sanitization output Factory",
        description="Scanne le code pour os.system, subprocess, eval, exec, __import__ avant écriture.",
        category="security",
        target_file="Agents/factory_agent.py",
        target_method="process_task",
        difficulty=1,
        code_template="""
# Patterns dangereux à détecter :
import re

_DANGEROUS_PATTERNS = [
    r'\\bos\\.system\\s*\\(',
    r'\\bsubprocess\\.',
    r'\\beval\\s*\\(',
    r'\\bexec\\s*\\(',
    r'\\b__import__\\s*\\(',
]

def _scan_code_safety(code: str) -> list[str]:
    '''Retourne la liste des patterns dangereux détectés.'''
    findings = []
    for pattern in _DANGEROUS_PATTERNS:
        if re.search(pattern, code):
            findings.append(pattern)
    return findings

# Dans process_task, avant l'écriture :
findings = _scan_code_safety(generated_code)
if findings:
    logger.warning(f"Factory: code rejeté, patterns dangereux détectés: {findings}")
    return {"status": "rejected", "result": f"Code rejeté: patterns dangereux {findings}"}
""",
        validation="Soumettre du code avec os.system(), vérifier qu'il est rejeté.",
        tags=["security", "sanitization", "factory"],
    )

    specs["SEC-002"] = ImprovementSpec(
        id="SEC-002",
        name="Détection injection prompt",
        description="Détecte 'ignore previous', 'system prompt', 'jailbreak' dans les missions et alerte.",
        category="security",
        target_file="core/router.py",
        target_method="classify_intent",
        difficulty=1,
        code_template="""
# Patterns d'injection à détecter :
_INJECTION_PATTERNS = [
    "ignore previous", "ignore all", "system prompt",
    "jailbreak", "bypass", "override instructions",
    "forget your", "new instructions",
]

@classmethod
def _check_injection(cls, mission: str) -> bool:
    mission_lower = mission.lower()
    for pattern in _INJECTION_PATTERNS:
        if pattern in mission_lower:
            logger.warning(f"Router: possible injection prompt détectée: '{pattern}'")
            return True
    return False

# Au début de classify_intent :
if cls._check_injection(mission):
    # Ne bloque pas mais flag
    pass
""",
        validation="Envoyer 'ignore previous instructions', vérifier que le warning est loggé.",
        tags=["security", "injection", "detection"],
    )

    specs["SEC-003"] = ImprovementSpec(
        id="SEC-003",
        name="Rate limit par agent",
        description="Max 10 dispatches par agent par tranche de 5 minutes.",
        category="security",
        target_file="core/orchestrator.py",
        target_method="dispatch_task",
        difficulty=2,
        code_template="""
# Attribut dans Orchestrator :
_dispatch_counts: dict = {}  # {agent: [(timestamp, ...), ...]}
_RATE_LIMIT = 10
_RATE_WINDOW = 300  # 5 minutes

def _check_rate_limit(self, agent_name: str) -> bool:
    now = time.time()
    timestamps = self._dispatch_counts.get(agent_name, [])
    # Purger les timestamps hors fenêtre
    timestamps = [ts for ts in timestamps if now - ts < self._RATE_WINDOW]
    self._dispatch_counts[agent_name] = timestamps
    if len(timestamps) >= self._RATE_LIMIT:
        logger.warning(f"Rate limit atteint pour {agent_name}: {len(timestamps)}/{self._RATE_LIMIT} en 5min")
        return True  # Limité
    timestamps.append(now)
    return False

# Dans dispatch_task :
if self._check_rate_limit(agent_name):
    return {"status": "rate_limited", "result": f"Agent {agent_name} rate limité (max {self._RATE_LIMIT}/5min)"}
""",
        validation="Dispatcher 11 missions au même agent, vérifier que la 11e est rejetée.",
        tags=["security", "rate-limit", "orchestrator"],
    )

    # === NOUVELLES SPECS — Fichiers NON protégés (18 specs) ===
    # Ajoutées pour remplacer les 24 specs ciblant des fichiers protégés par Factory.
    # Toutes ciblent des fichiers dans Agents/ ou core/ hors _PROTECTED_FILES.

    # --- PERFORMANCE (3 specs) ---

    specs["PERF-008"] = ImprovementSpec(
        id="PERF-008",
        name="Cache listing projet Strategist",
        description="Cache le résultat de _get_project_files() avec TTL 60s pour éviter un scan disque à chaque process_task.",
        category="performance",
        target_file="Agents/strategist_agent.py",
        target_method="_get_project_files",
        difficulty=1,
        code_template="""
# Attributs de classe DivineStrategist :
_project_files_cache: str = ""
_project_files_ts: float = 0.0
_PROJECT_FILES_TTL = 60  # secondes

def _get_project_files(self) -> str:
    import time
    now = time.time()
    if self._project_files_cache and (now - self._project_files_ts) < self._PROJECT_FILES_TTL:
        return self._project_files_cache
    # ... scan disque existant ...
    self._project_files_cache = result
    self._project_files_ts = now
    return result
""",
        validation="Appeler _get_project_files 2x en <60s, vérifier que le 2e appel n'accède pas au disque.",
        tags=["performance", "cache", "strategist"],
    )

    specs["PERF-009"] = ImprovementSpec(
        id="PERF-009",
        name="Lazy init WebSurfer Researcher",
        description="Retarde l'instanciation de WebSurfer et KnowledgeIngestor au premier usage dans process_task.",
        category="performance",
        target_file="Agents/researcher_agent.py",
        target_method="__init__",
        difficulty=1,
        code_template="""
# Dans __init__, remplacer l'init immédiate par :
self._surfer = None
self._ingestor = None

@property
def surfer(self):
    if self._surfer is None:
        from core.capabilities.web_surfer import WebSurfer
        self._surfer = WebSurfer()
    return self._surfer

@property
def ingestor(self):
    if self._ingestor is None:
        from core.capabilities.knowledge_ingestor import KnowledgeIngestor
        self._ingestor = KnowledgeIngestor()
    return self._ingestor
""",
        validation="Créer un DivineResearcher, vérifier que WebSurfer n'est pas instancié. Appeler process_task, vérifier que WebSurfer est maintenant chargé.",
        tags=["performance", "lazy", "import", "researcher"],
    )

    specs["PERF-010"] = ImprovementSpec(
        id="PERF-010",
        name="Skip binaires Dropzone",
        description="Dans _read_batch, détecter les fichiers binaires (octets nuls dans les 512 premiers bytes) et les skip.",
        category="performance",
        target_file="core/dropzone_pipeline.py",
        target_method="_read_batch",
        difficulty=1,
        code_template="""
# Au début de la lecture de chaque fichier dans _read_batch :
def _is_binary(filepath: str) -> bool:
    try:
        with open(filepath, 'rb') as f:
            chunk = f.read(512)
            return b'\\x00' in chunk
    except Exception:
        return True  # En cas d'erreur, considérer comme binaire

# Dans _read_batch, avant la lecture :
if _is_binary(os.path.join(root, filename)):
    continue  # Skip les fichiers binaires
""",
        validation="Placer un fichier .png dans la dropzone, vérifier qu'il est ignoré par _read_batch.",
        tags=["performance", "dropzone", "binary"],
    )

    # --- RESILIENCE (3 specs) ---

    specs["RES-006"] = ImprovementSpec(
        id="RES-006",
        name="Fallback recherche locale Researcher",
        description="Si WebSurfer échoue (timeout, erreur réseau), le Researcher utilise uniquement la mémoire RAG locale.",
        category="resilience",
        target_file="Agents/researcher_agent.py",
        target_method="process_task",
        difficulty=1,
        code_template="""
# Dans process_task, wrapper l'appel WebSurfer :
web_results = ""
try:
    web_results = await self.surfer.search(query)
except Exception as e:
    logger.warning(f"[Researcher] WebSurfer failed, fallback RAG local: {e}")
    web_results = self.recall(query)
    if web_results:
        web_results = f"(Résultats mémoire locale)\\n{web_results}"
""",
        validation="Mock WebSurfer pour lever une exception, vérifier que le Researcher utilise recall() et ne crash pas.",
        tags=["resilience", "researcher", "fallback", "web"],
    )

    specs["RES-007"] = ImprovementSpec(
        id="RES-007",
        name="Timeout sub-checks Infra",
        description="Ajoute un timeout de 5s sur chaque vérification psutil dans _perform_health_check pour éviter les freeze.",
        category="resilience",
        target_file="Agents/infra_agent.py",
        target_method="_perform_health_check",
        difficulty=1,
        code_template="""
import asyncio

async def _safe_check(coro_or_func, timeout=5.0, default=None):
    '''Exécute un check avec timeout.'''
    try:
        if asyncio.iscoroutinefunction(coro_or_func):
            return await asyncio.wait_for(coro_or_func(), timeout=timeout)
        else:
            loop = asyncio.get_event_loop()
            return await asyncio.wait_for(
                loop.run_in_executor(None, coro_or_func),
                timeout=timeout
            )
    except asyncio.TimeoutError:
        logger.warning(f"Health check timeout après {timeout}s")
        return default
    except Exception as e:
        logger.warning(f"Health check error: {e}")
        return default

# Utiliser dans _perform_health_check :
cpu = await _safe_check(lambda: psutil.cpu_percent(interval=1), default=0)
ram = await _safe_check(lambda: psutil.virtual_memory().percent, default=0)
""",
        validation="Mock psutil.cpu_percent pour bloquer 10s, vérifier que le health check retourne en <6s.",
        tags=["resilience", "infra", "timeout", "health"],
    )

    specs["RES-008"] = ImprovementSpec(
        id="RES-008",
        name="Auto-trim journal stratégique",
        description="Limite le journal stratégique à 100 entrées max. Au-delà, supprime les plus anciennes.",
        category="resilience",
        target_file="core/strategic_journal.py",
        target_method="append_council_entry",
        difficulty=1,
        code_template="""
_MAX_JOURNAL_ENTRIES = 100

# À la fin de append_council_entry et append_research_entry :
def _auto_trim(self):
    if len(self._entries) > _MAX_JOURNAL_ENTRIES:
        overflow = len(self._entries) - _MAX_JOURNAL_ENTRIES
        self._entries = self._entries[overflow:]
        logger.info(f"Journal: auto-trim {overflow} entrées anciennes")
        self._save()

# Appeler self._auto_trim() après chaque append
""",
        validation="Insérer 105 entrées dans le journal, vérifier qu'il n'en reste que 100.",
        tags=["resilience", "journal", "trim", "memory"],
    )

    # --- INTELLIGENCE (4 specs) ---

    specs["INT-006"] = ImprovementSpec(
        id="INT-006",
        name="Post-filtre code Coder",
        description="Après génération, vérifie que le résultat contient du code Python structurel. Sinon, re-prompt avec instruction stricte.",
        category="intelligence",
        target_file="Agents/coder_agent.py",
        target_method="process_task",
        difficulty=2,
        code_template="""
import re

def _has_code_structure(text: str) -> bool:
    '''Vérifie la présence de structures Python (def, class, import).'''
    patterns = [r'^\\s*def\\s+', r'^\\s*class\\s+', r'^\\s*import\\s+', r'^\\s*from\\s+']
    count = sum(1 for p in patterns if re.search(p, text, re.MULTILINE))
    return count >= 2  # Au moins 2 structures différentes

# Dans process_task, après la première génération :
if "code" in mission.lower() and not _has_code_structure(result):
    # Re-prompt plus strict
    strict_prompt = f"GÉNÈRE UNIQUEMENT du code Python. Pas d'explication.\\n\\n{mission}"
    result = await self.generate_content(strict_prompt)
""",
        validation="Demander du code, vérifier que le résultat contient des structures Python. Si le LLM retourne du texte, vérifier le re-prompt.",
        tags=["intelligence", "coder", "quality", "post-filter"],
    )

    specs["INT-007"] = ImprovementSpec(
        id="INT-007",
        name="Métriques lisibilité Writer",
        description="Calcule la longueur moyenne des phrases et le nombre de paragraphes, injecte en metadata du résultat.",
        category="intelligence",
        target_file="Agents/writer_agent.py",
        target_method="process_task",
        difficulty=1,
        code_template="""
def _compute_readability(text: str) -> dict:
    '''Métriques de lisibilité basiques.'''
    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
    words = text.split()
    paragraphs = [p.strip() for p in text.split('\\n\\n') if p.strip()]
    avg_sentence_len = len(words) / max(len(sentences), 1)
    return {
        "word_count": len(words),
        "sentence_count": len(sentences),
        "paragraph_count": len(paragraphs),
        "avg_sentence_length": round(avg_sentence_len, 1),
    }

# Dans process_task, après la génération :
readability = _compute_readability(result_text)
# Ajouter au résultat
result["readability"] = readability
""",
        validation="Générer un texte, vérifier que le résultat contient les métriques de lisibilité.",
        tags=["intelligence", "writer", "readability", "metrics"],
    )

    specs["INT-008"] = ImprovementSpec(
        id="INT-008",
        name="Sonde latence Ollama Infra",
        description="Lors du health check, ping Ollama /api/tags et mesure le temps de réponse. Inclut dans les métriques.",
        category="intelligence",
        target_file="Agents/infra_agent.py",
        target_method="_perform_health_check",
        difficulty=1,
        code_template="""
import time
import httpx

async def _probe_ollama_latency() -> dict:
    '''Mesure la latence Ollama via /api/tags.'''
    try:
        start = time.time()
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("http://localhost:11434/api/tags")
            latency_ms = (time.time() - start) * 1000
            models = len(resp.json().get("models", []))
            return {"status": "up", "latency_ms": round(latency_ms, 1), "models_loaded": models}
    except Exception as e:
        return {"status": "down", "error": str(e)[:100]}

# Dans _perform_health_check :
ollama_info = await _probe_ollama_latency()
metrics["ollama"] = ollama_info
""",
        validation="Vérifier que les métriques contiennent ollama.latency_ms quand Ollama est up.",
        tags=["intelligence", "infra", "ollama", "latency"],
    )

    specs["INT-009"] = ImprovementSpec(
        id="INT-009",
        name="Évolution traits par résultat Psyche",
        description="Ajuste les traits PSYCHE selon le résultat des routines : succès → +créativité, échec → +prudence.",
        category="intelligence",
        target_file="core/psyche.py",
        target_method="_on_routine_complete",
        difficulty=1,
        code_template="""
# Dans _on_routine_complete :
async def _on_routine_complete(self, event: dict):
    agent = event.get("agent", "")
    status = event.get("status", "")
    intent = event.get("intent", "")

    if not agent or agent not in self._traits:
        return

    if status == "success":
        deltas = {"créativité": 1.5, "prudence": -0.5}
    elif status == "error":
        deltas = {"prudence": 1.5, "créativité": -0.5}
    else:
        return

    self._apply_deltas_single(agent, deltas)
    await self._publish_update()
""",
        validation="Simuler un event routine_complete avec status=success, vérifier que la créativité augmente.",
        tags=["intelligence", "psyche", "traits", "adaptive"],
    )

    # --- OBSERVABILITY (4 specs) ---

    specs["OBS-005"] = ImprovementSpec(
        id="OBS-005",
        name="Talk log structuré JSON",
        description="Ajoute un préfixe JSON parseable à chaque entrée du talk log pour faciliter l'analyse automatique.",
        category="observability",
        target_file="core/talk_logger.py",
        target_method="_format_entry",
        difficulty=1,
        code_template="""
import json

def _format_entry(event_type: str, data: dict) -> str:
    '''Format structuré : ligne JSON + texte lisible.'''
    meta = {
        "type": event_type,
        "ts": data.get("timestamp", ""),
        "agent": data.get("agent", "system"),
    }
    json_line = json.dumps(meta, ensure_ascii=False)
    content = data.get("content", str(data))[:500]
    return f"[JSON]{json_line}[/JSON]\\n{content}\\n"
""",
        validation="Vérifier que chaque entrée du talk log contient un bloc [JSON]...[/JSON] parseable.",
        tags=["observability", "logging", "structured", "json"],
    )

    specs["OBS-006"] = ImprovementSpec(
        id="OBS-006",
        name="Dédup recherches journal",
        description="Avant d'ajouter une entrée research, vérifie si le même topic existe dans les 5 dernières entrées.",
        category="observability",
        target_file="core/strategic_journal.py",
        target_method="append_research_entry",
        difficulty=1,
        code_template="""
def append_research_entry(self, topic: str, findings: str, source: str = "web"):
    # Déduplication : vérifier les 5 dernières entrées
    topic_lower = topic.strip().lower()
    recent = self._entries[-5:] if len(self._entries) >= 5 else self._entries
    for entry in recent:
        if entry.get("type") == "research":
            if entry.get("topic", "").strip().lower() == topic_lower:
                logger.info(f"Journal: topic dupliqué '{topic[:50]}' — skip")
                return
    # ... code existant d'ajout ...
""",
        validation="Ajouter 2x le même topic, vérifier que le 2e est ignoré.",
        tags=["observability", "journal", "dedup"],
    )

    specs["OBS-007"] = ImprovementSpec(
        id="OBS-007",
        name="Détection cycles debug SelfAwareness",
        description="Détecte le pattern 'boucle de debug' : 3+ alternances error/success en 10 minutes sur le même agent.",
        category="observability",
        target_file="core/self_awareness.py",
        target_method="detect_patterns",
        difficulty=2,
        code_template="""
def _detect_debug_loops(self) -> list:
    '''Détecte les boucles error→success→error sur un même agent.'''
    patterns = []
    # Grouper les événements récents par agent
    recent = self._agent_events[-50:]  # 50 derniers events
    by_agent = {}
    for ev in recent:
        agent = ev.get("agent", "unknown")
        by_agent.setdefault(agent, []).append(ev)

    for agent, events in by_agent.items():
        # Compter les alternances error↔success
        alternances = 0
        for i in range(1, len(events)):
            prev_ok = events[i-1].get("status") == "success"
            curr_ok = events[i].get("status") == "success"
            if prev_ok != curr_ok:
                alternances += 1
        if alternances >= 3:
            patterns.append({
                "type": "debug_loop",
                "agent": agent,
                "alternances": alternances,
                "severity": "warning"
            })
    return patterns
""",
        validation="Simuler 4 alternances error/success sur un agent, vérifier que le pattern debug_loop est détecté.",
        tags=["observability", "patterns", "debug", "self-awareness"],
    )

    specs["OBS-008"] = ImprovementSpec(
        id="OBS-008",
        name="Métriques processing Dropzone",
        description="Après chaque run Dropzone, publie un event DROPZONE_STATS avec files_processed, total_chars, duration.",
        category="observability",
        target_file="core/dropzone_pipeline.py",
        target_method="run",
        difficulty=1,
        code_template="""
import time
from core.event_bus.bus import bus

# Dans run(), au début :
start_time = time.time()
files_count = 0
total_chars = 0

# Après chaque fichier analysé :
files_count += 1
total_chars += len(content)

# À la fin de run() :
duration = time.time() - start_time
await bus.publish("DROPZONE_STATS", {
    "files_processed": files_count,
    "total_chars": total_chars,
    "duration_s": round(duration, 1),
    "timestamp": datetime.now().isoformat()
})
""",
        validation="Exécuter un run Dropzone, vérifier que l'event DROPZONE_STATS est publié avec les bonnes métriques.",
        tags=["observability", "dropzone", "metrics", "stats"],
    )

    # --- SECURITY (2 specs) ---

    specs["SEC-004"] = ImprovementSpec(
        id="SEC-004",
        name="Sanitization secrets Coder",
        description="Scanne le code généré pour les patterns de secrets (API keys, tokens, passwords) et les remplace par des placeholders.",
        category="security",
        target_file="Agents/coder_agent.py",
        target_method="process_task",
        difficulty=1,
        code_template="""
import re

_SECRET_PATTERNS = [
    (r'["\\'](sk-[a-zA-Z0-9]{20,})["\\'"]', '"<API_KEY>"'),
    (r'["\\'](AIza[a-zA-Z0-9_-]{30,})["\\'"]', '"<GOOGLE_API_KEY>"'),
    (r'password\\s*=\\s*["\\'"]([^"\\'']+)["\\'"]', 'password = "<REDACTED>"'),
    (r'token\\s*=\\s*["\\'"]([^"\\'']+)["\\'"]', 'token = "<REDACTED>"'),
]

def _sanitize_secrets(code: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        code = re.sub(pattern, replacement, code)
    return code

# Dans process_task, avant de retourner :
result_text = _sanitize_secrets(result_text)
""",
        validation="Générer du code contenant 'sk-abc123...', vérifier qu'il est remplacé par <API_KEY>.",
        tags=["security", "coder", "secrets", "sanitization"],
    )

    specs["SEC-005"] = ImprovementSpec(
        id="SEC-005",
        name="Whitelist extensions Dropzone",
        description="N'analyse que les fichiers avec extensions autorisées (.py, .txt, .md, .json, .csv, .log, .yaml, .toml).",
        category="security",
        target_file="core/dropzone_pipeline.py",
        target_method="run",
        difficulty=1,
        code_template="""
_ALLOWED_EXTENSIONS = {".py", ".txt", ".md", ".json", ".csv", ".log", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".html", ".js"}

def _is_allowed_file(filepath: str) -> bool:
    ext = os.path.splitext(filepath)[1].lower()
    return ext in _ALLOWED_EXTENSIONS

# Dans run() ou _analyze_project(), filtrer les fichiers :
files = [f for f in all_files if _is_allowed_file(f)]
""",
        validation="Placer un .exe et un .py dans la dropzone, vérifier que seul le .py est analysé.",
        tags=["security", "dropzone", "whitelist", "extensions"],
    )

    # --- MEMORY (2 specs) ---

    specs["MEM-005"] = ImprovementSpec(
        id="MEM-005",
        name="Mémoire structurée Researcher",
        description="Formate les findings en 'TOPIC: x | SOURCE: y | INSIGHT: z' avant remember() pour améliorer le recall.",
        category="memory",
        target_file="Agents/researcher_agent.py",
        target_method="process_task",
        difficulty=1,
        code_template="""
def _format_research_memory(topic: str, source: str, findings: str) -> str:
    '''Formate une découverte pour une meilleure recherche future.'''
    # Extraire la première phrase comme insight clé
    first_sentence = findings.split('.')[0].strip() if '.' in findings else findings[:200]
    return f"TOPIC: {topic[:100]} | SOURCE: {source} | INSIGHT: {first_sentence}"

# Dans process_task, avant remember() :
structured = _format_research_memory(topic, source, raw_findings)
self.remember(structured)
""",
        validation="Le Researcher mémorise un finding structuré, vérifier que recall('TOPIC: X') le retrouve.",
        tags=["memory", "researcher", "structured", "rag"],
    )

    specs["MEM-006"] = ImprovementSpec(
        id="MEM-006",
        name="Snapshot traits Psyche",
        description="Lors de chaque save(), stocke un mini-historique des traits dominants (10 derniers snapshots) pour suivre l'évolution.",
        category="memory",
        target_file="core/psyche.py",
        target_method="save",
        difficulty=1,
        code_template="""
_MAX_TRAIT_HISTORY = 10

def save(self):
    # ... code existant de sauvegarde ...

    # Ajouter un snapshot des traits dominants
    snapshot = {}
    for agent, traits in self._traits.items():
        dominant = max(traits.items(), key=lambda x: x[1])
        snapshot[agent] = {"trait": dominant[0], "value": round(dominant[1], 1)}

    if not hasattr(self, '_trait_history'):
        self._trait_history = []
    self._trait_history.append({
        "ts": datetime.now().isoformat(),
        "dominant_traits": snapshot
    })
    # Garder seulement les N derniers
    self._trait_history = self._trait_history[-_MAX_TRAIT_HISTORY:]
""",
        validation="Sauvegarder Psyche 3x avec des traits différents, vérifier que _trait_history contient 3 snapshots.",
        tags=["memory", "psyche", "history", "traits"],
    )

    return specs


# ---------------------------------------------------------------------------
# Singleton EvolutionCatalog
# ---------------------------------------------------------------------------

class EvolutionCatalog:
    """Catalogue d'améliorations pré-définies — singleton."""

    _instance: Optional["EvolutionCatalog"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.specs: Dict[str, ImprovementSpec] = _build_catalog()
        self._combinations_generated: List[str] = []
        self._stats = {
            "total_deployed": 0,
            "total_failed": 0,
            "total_attempted": 0,
            "last_cycle": None,
        }
        self._load()

    @classmethod
    def reset_singleton(cls):
        if cls._instance is not None:
            cls._instance._initialized = False
        cls._instance = None

    # --- Accès aux specs ---

    def get_spec(self, spec_id: str) -> Optional[ImprovementSpec]:
        return self.specs.get(spec_id)

    def get_all_specs(self) -> List[ImprovementSpec]:
        return list(self.specs.values())

    def get_specs_by_category(self, category: str) -> List[ImprovementSpec]:
        return [s for s in self.specs.values() if s.category == category]

    def get_specs_by_status(self, status: str) -> List[ImprovementSpec]:
        return [s for s in self.specs.values() if s.status == status]

    # --- Sélection ---

    def get_eligible_specs(self) -> List[ImprovementSpec]:
        """Retourne les specs éligibles (filtre déterministe)."""
        now = datetime.now()
        # Timeout pending_deploy : si >1h, revert à available
        for spec in self.specs.values():
            if spec.status == "pending_deploy" and spec.last_attempt:
                try:
                    last = datetime.fromisoformat(spec.last_attempt)
                    if (now - last) > timedelta(hours=1):
                        logger.warning(f"Catalogue: {spec.id} pending_deploy timeout (>1h) → available")
                        spec.status = "available"
                        self._save()
                except (ValueError, TypeError):
                    pass
        eligible = []
        for spec in self.specs.values():
            # Exclure : pas available
            if spec.status != "available":
                continue
            # Exclure : trop de tentatives
            if spec.attempts >= spec.max_attempts:
                continue
            # Exclure : dépendances non déployées
            if spec.dependencies:
                deps_ok = all(
                    self.specs.get(dep) and self.specs[dep].status == "deployed"
                    for dep in spec.dependencies
                )
                if not deps_ok:
                    continue
            # Exclure : tenté dans les 24h
            if spec.last_attempt:
                try:
                    last = datetime.fromisoformat(spec.last_attempt)
                    if (now - last) < timedelta(hours=24):
                        continue
                except (ValueError, TypeError):
                    pass
            eligible.append(spec)
        return eligible

    def score_specs(self, eligible: List[ImprovementSpec], system_state: Optional[Dict] = None) -> List[tuple]:
        """Score les specs éligibles selon l'état du système. Retourne [(spec, score)] trié."""
        if system_state is None:
            system_state = self._get_system_state()

        scored = []
        for spec in eligible:
            score = 1.0

            # Bonus catégorie basé sur l'état système
            error_streak = system_state.get("error_streak", 0)
            success_rate = system_state.get("success_rate", 1.0)
            cloud_pct = system_state.get("cloud_budget_pct", 0)
            health = system_state.get("health", "GO")

            if error_streak >= 3:
                if spec.category == "resilience":
                    score += 2.0
                elif spec.category == "observability":
                    score += 1.5

            if success_rate < 0.5:
                if spec.category == "intelligence":
                    score += 1.5
                elif spec.category == "performance":
                    score += 1.0

            if cloud_pct > 80:
                if spec.category == "performance":
                    score += 2.0

            if health == "DEGRADED":
                if spec.category == "resilience":
                    score += 2.0
                elif spec.category == "observability":
                    score += 1.5

            # Bonus difficulté
            if spec.difficulty == 1:
                score += 0.5
            elif spec.difficulty == 3:
                score -= 0.5

            # Bonus combinaison avec specs déployées
            for combo_id in spec.combinable_with:
                combo = self.specs.get(combo_id)
                if combo and combo.status == "deployed":
                    score += 1.0
                    break  # 1 bonus max

            # Pénalité échecs
            score -= 0.5 * spec.attempts

            # Bonus specs COUNCIL (priorité de consommation pour éviter la saturation)
            if spec.id.startswith("COUNCIL-"):
                score += 1.5

            scored.append((spec, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    # Mapping type d'objectif → préfixes de specs à booster
    OBJECTIVE_CATEGORY_BONUS = {
        "performance": ["PERF-"],
        "evolution": ["INT-", "OBS-"],
        "maintenance": ["SEC-", "MEM-", "RES-"],
    }

    def get_top_candidates(self, n: int = 5) -> List[tuple]:
        """Retourne les top N candidats (spec, score), avec bonus objectifs actifs
        et bonus difficulté basse si Evolution est bloquée."""
        eligible = self.get_eligible_specs()
        if not eligible:
            return []
        scored = self.score_specs(eligible)

        # Bonus difficulté basse si Evolution est stuck
        try:
            from core.self_awareness import awareness
            from core.autonomy_engine import autonomy
            if awareness.is_evolution_stuck(autonomy.routine_history):
                scored = [
                    (spec, sc + 2.0) if spec.difficulty == 1 else (spec, sc)
                    for spec, sc in scored
                ]
                scored.sort(key=lambda x: x[1], reverse=True)
        except Exception:
            pass

        # Bonus objectifs actifs
        try:
            from core.objectives_engine import objectives as obj_engine
            active_types = {o["type"] for o in obj_engine.get_active_objectives()}
            if active_types:
                boosted = []
                for spec, sc in scored:
                    bonus = 0.0
                    for obj_type in active_types:
                        prefixes = self.OBJECTIVE_CATEGORY_BONUS.get(obj_type, [])
                        for prefix in prefixes:
                            if spec.id.startswith(prefix):
                                bonus = max(bonus, 2.0)
                    boosted.append((spec, sc + bonus))
                boosted.sort(key=lambda x: x[1], reverse=True)
                return boosted[:n]
        except Exception:
            pass

        return scored[:n]

    def build_selection_prompt(self, candidates: List[tuple]) -> str:
        """Construit le prompt de sélection pour le LLM."""
        lines = [
            "Voici 5 améliorations possibles pour Prométhée.",
            "Laquelle est la plus urgente et impactante ?",
            ""
        ]
        for i, (spec, score) in enumerate(candidates, 1):
            lines.append(f"{i}. [{spec.id}] {spec.name} — {spec.description}")
        lines.append("")
        lines.append("Réponds UNIQUEMENT par le numéro (1-5).")
        return "\n".join(lines)

    def parse_llm_choice(self, response: str, candidates: List[tuple]) -> ImprovementSpec:
        """Parse la réponse du LLM. Retourne la spec choisie (ou #1 en fallback)."""
        if not candidates:
            return None

        # Chercher un chiffre 1-5
        for char in response.strip():
            if char.isdigit():
                idx = int(char) - 1
                if 0 <= idx < len(candidates):
                    logger.info(f"LLM a choisi #{idx+1}: {candidates[idx][0].id}")
                    return candidates[idx][0]

        # Fallback : meilleur score
        logger.warning(f"LLM n'a pas retourné un choix valide ('{response[:50]}'). Fallback sur #1.")
        return candidates[0][0]

    def get_next_spec(self) -> Optional[ImprovementSpec]:
        """Pipeline complet de sélection sans LLM (déterministe).
        Retourne la spec avec le meilleur score."""
        candidates = self.get_top_candidates(5)
        if not candidates:
            logger.info("Catalogue: aucune spec éligible.")
            return None
        # Retourne la #1 (meilleur score)
        return candidates[0][0]

    # --- Tracking ---

    def mark_attempted(self, spec_id: str):
        spec = self.specs.get(spec_id)
        if not spec:
            return
        spec.status = "attempted"
        spec.attempts += 1
        spec.last_attempt = datetime.now().isoformat()
        self._stats["total_attempted"] += 1
        self._stats["last_cycle"] = datetime.now().isoformat()
        self._save()

    def mark_pending_deploy(self, spec_id: str):
        """Marque une spec comme en attente de confirmation Factory."""
        spec = self.specs.get(spec_id)
        if not spec:
            return
        spec.status = "pending_deploy"
        spec.last_attempt = datetime.now().isoformat()
        self._save()
        logger.info(f"Catalogue: {spec_id} en attente de déploiement (pending_deploy).")

    def mark_deployed(self, spec_id: str):
        spec = self.specs.get(spec_id)
        if not spec:
            return
        spec.status = "deployed"
        spec.deployed_at = datetime.now().isoformat()
        self._stats["total_deployed"] += 1
        self._save()
        logger.info(f"Catalogue: {spec_id} déployé avec succès !")

    def mark_failed(self, spec_id: str, reason: str = ""):
        spec = self.specs.get(spec_id)
        if not spec:
            return
        if reason:
            spec.failure_reasons.append(reason)
        if spec.attempts >= spec.max_attempts:
            spec.status = "failed"
            self._stats["total_failed"] += 1
            logger.warning(f"Catalogue: {spec_id} verrouillé après {spec.max_attempts} tentatives.")
        else:
            spec.status = "available"  # Re-disponible pour un prochain cycle
        self._save()

    def mark_rejected(self, spec_id: str, reason: str = ""):
        """Marque une spec comme rejetée par curation automatique."""
        spec = self.specs.get(spec_id)
        if not spec:
            return
        spec.status = "rejected"
        if reason:
            spec.failure_reasons.append(f"[CURATION] {reason}")
        self._save()
        logger.info(f"Catalogue: {spec_id} rejeté par curation — {reason}")

    def curate_council_specs(self) -> int:
        """Purge déterministe des specs COUNCIL inutiles. Zéro LLM."""
        purged = 0
        council_specs = [
            s for s in self.specs.values()
            if s.id.startswith("COUNCIL-") and s.status == "available"
        ]

        for spec in council_specs:
            reason = self._evaluate_council_spec(spec)
            if reason:
                self.mark_rejected(spec.id, reason)
                purged += 1

        if purged:
            logger.info(f"[CURATION] {purged} spec(s) COUNCIL purgée(s).")
            self._append_curation_journal(purged, council_specs)
        return purged

    def _evaluate_council_spec(self, spec: ImprovementSpec) -> Optional[str]:
        """Évalue une spec COUNCIL. Retourne None si valide, sinon la raison de rejet."""
        # 1. Fichier cible inexistant
        if spec.target_file:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            full_path = os.path.join(project_root, spec.target_file.replace("/", os.sep))
            if not os.path.exists(full_path):
                return f"fichier_inexistant: {spec.target_file}"

        # 2. Description trop courte
        if len(spec.description) < 50:
            return f"description_trop_courte: {len(spec.description)} chars"

        # 3. Âge > 12h (les specs council perdent leur pertinence rapidement)
        created_at = self._extract_council_timestamp(spec)
        if created_at:
            age = datetime.now() - created_at
            if age > timedelta(hours=12):
                hours = age.total_seconds() / 3600
                return f"perimee: {hours:.0f}h (>12h)"

        # 4. Doublon sémantique (même target_file + même target_method qu'une autre spec)
        if spec.target_file and spec.target_method:
            for other in self.specs.values():
                if other.id == spec.id:
                    continue
                if other.status in ("available", "attempted", "pending_deploy", "deployed", "confirmed"):
                    if other.target_file == spec.target_file and other.target_method == spec.target_method:
                        return f"doublon: meme cible que {other.id} ({spec.target_file}:{spec.target_method})"

        # 5. Code template boilerplate
        template = spec.code_template.strip()
        if template.startswith("# Amelioration") or template.startswith("# Amélioration"):
            return "boilerplate: template par defaut"
        # Vérifier si le template ne contient que des commentaires et pass
        lines = [l.strip() for l in template.split("\n") if l.strip()]
        non_trivial = [l for l in lines if not l.startswith("#") and l != "pass"
                       and not l.startswith('"""') and not l.startswith("'''")
                       and l != "def council_improvement():"]
        if not non_trivial:
            return "boilerplate: aucune implementation reelle"

        return None

    def _extract_council_timestamp(self, spec: ImprovementSpec) -> Optional[datetime]:
        """Extrait le timestamp de création d'une spec COUNCIL."""
        # Priorité : last_attempt (toujours renseigné à la création)
        if spec.last_attempt:
            try:
                return datetime.fromisoformat(spec.last_attempt)
            except (ValueError, TypeError):
                pass
        # Fallback : extraire depuis l'ID COUNCIL-{timestamp}-{hex4}
        try:
            parts = spec.id.split("-")
            if len(parts) >= 2:
                ts = int(parts[1])
                if ts > 1_000_000_000:  # timestamp Unix valide (post-2001)
                    return datetime.fromtimestamp(ts)
        except (ValueError, IndexError):
            pass
        return None

    def _append_curation_journal(self, purged: int, specs: list):
        """Écrit un résumé de la curation dans config/council_journal.md."""
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            journal_path = os.path.join(project_root, "config", "council_journal.md")
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            rejected = [s for s in specs if s.status == "rejected"]
            details = "\n".join(
                f"  - `{s.id}`: {s.failure_reasons[-1] if s.failure_reasons else '?'}"
                for s in rejected
            )
            entry = (
                f"\n---\n\n"
                f"## [{now}] CURATION AUTOMATIQUE\n\n"
                f"**{purged} spec(s) COUNCIL purgée(s)** :\n{details}\n"
            )
            os.makedirs(os.path.dirname(journal_path), exist_ok=True)
            with open(journal_path, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            logger.warning(f"[CURATION] Écriture journal échouée: {e}")

    def mark_available(self, spec_id: str):
        """Remet une spec en disponible (utile après correction)."""
        spec = self.specs.get(spec_id)
        if not spec:
            return
        spec.status = "available"
        self._save()

    def mark_confirmed(self, spec_id: str):
        """Marque une spec déployée comme confirmée (prouvée en production)."""
        spec = self.specs.get(spec_id)
        if not spec:
            return
        spec.status = "confirmed"
        spec.confirmed_at = datetime.now().isoformat()
        self._save()
        logger.info(f"Catalogue: {spec_id} confirmé en production !")

    def mark_rolled_back(self, spec_id: str, reason: str = ""):
        """Marque une spec déployée comme rollbackée (dégradation détectée)."""
        spec = self.specs.get(spec_id)
        if not spec:
            return
        spec.status = "rolled_back"
        if reason:
            spec.failure_reasons.append(reason)
        self._stats["total_rolled_back"] = self._stats.get("total_rolled_back", 0) + 1
        self._save()
        logger.warning(f"Catalogue: {spec_id} rollbacké — {reason}")

    # --- Meta-Evolution ---

    def generate_combinations(self) -> List[ImprovementSpec]:
        """Génère des specs combinées à partir des specs déployées.
        Seuil abaissé à 1 pour que le cross-target fonctionne même avec peu de déploiements."""
        deployed = [s for s in self.specs.values() if s.status == "deployed"]
        if len(deployed) < 1:
            return []

        new_specs = []
        for spec in deployed:
            # 1. Combinaison explicite
            for combo_id in spec.combinable_with:
                combo = self.specs.get(combo_id)
                if combo and combo.status == "deployed":
                    combined_id = f"COMBO-{spec.id}+{combo.id}"
                    if combined_id not in self.specs and combined_id not in self._combinations_generated:
                        new_spec = self._combine(spec, combo)
                        if new_spec:
                            new_specs.append(new_spec)
                            self._combinations_generated.append(combined_id)

            # 2. Cross-target : si "cache" a marché, essayer sur d'autres fichiers
            if "cache" in spec.tags:
                cross_targets = [
                    ("core/orchestrator.py", "dispatch_task"),
                    ("core/autonomy_engine.py", "score_routines"),
                ]
                for target_file, target_method in cross_targets:
                    if target_file != spec.target_file:
                        cross_id = f"CROSS-{spec.id}-{target_file.replace('/', '_')}"
                        if cross_id not in self.specs and cross_id not in self._combinations_generated:
                            new_spec = self._generalize(spec, target_file, target_method)
                            if new_spec:
                                new_specs.append(new_spec)
                                self._combinations_generated.append(cross_id)

        # Ajouter les nouvelles specs au catalogue
        for ns in new_specs:
            self.specs[ns.id] = ns
            logger.info(f"Meta-Evolution: nouvelle spec {ns.id} ajoutée au catalogue")

        if new_specs:
            self._save()

        return new_specs

    def _combine(self, spec_a: ImprovementSpec, spec_b: ImprovementSpec) -> Optional[ImprovementSpec]:
        """Combine deux specs déployées en une nouvelle spec."""
        combined_id = f"COMBO-{spec_a.id}+{spec_b.id}"
        return ImprovementSpec(
            id=combined_id,
            name=f"{spec_a.name} + {spec_b.name}",
            description=f"Combinaison de {spec_a.name} et {spec_b.name} pour effet synergique.",
            category=spec_a.category,
            target_file=spec_a.target_file,
            target_method=spec_a.target_method,
            difficulty=min(spec_a.difficulty + 1, 3),
            code_template=f"# Combinaison de {spec_a.id} et {spec_b.id}\n# Intégrer les deux améliorations ensemble.\n\n{spec_a.code_template}\n\n# --- {spec_b.id} ---\n{spec_b.code_template}",
            validation=f"Vérifier que {spec_a.name} et {spec_b.name} fonctionnent ensemble.",
            tags=list(set(spec_a.tags + spec_b.tags)),
            status="available",
        )

    def _generalize(self, spec: ImprovementSpec, target_file: str, target_method: str) -> Optional[ImprovementSpec]:
        """Généralise une spec déployée à un autre fichier cible."""
        gen_id = f"CROSS-{spec.id}-{target_file.replace('/', '_')}"
        return ImprovementSpec(
            id=gen_id,
            name=f"{spec.name} → {target_file}",
            description=f"Appliquer le pattern '{spec.name}' au fichier {target_file}:{target_method}.",
            category=spec.category,
            target_file=target_file,
            target_method=target_method,
            difficulty=min(spec.difficulty + 1, 3),
            code_template=spec.code_template.replace(spec.target_file, target_file),
            validation=f"Vérifier que le pattern de {spec.id} fonctionne sur {target_file}.",
            tags=spec.tags[:],
            status="available",
        )

    # --- État système ---

    def _get_system_state(self) -> Dict[str, Any]:
        """Récupère l'état système depuis SelfAwareness (import local)."""
        state = {
            "error_streak": 0,
            "success_rate": 1.0,
            "cloud_budget_pct": 0,
            "health": "GO",
        }
        try:
            from core.self_awareness import awareness
            snap = awareness.get_latest_snapshot()
            if snap:
                perf = snap.get("performance", {})
                state["error_streak"] = perf.get("error_streak", 0)
                state["success_rate"] = perf.get("success_rate", 1.0)
                cloud_used = perf.get("cloud_budget_used", 0)
                cloud_max = perf.get("cloud_budget_max", 100)
                state["cloud_budget_pct"] = (cloud_used / cloud_max * 100) if cloud_max > 0 else 0
                state["health"] = snap.get("health", {}).get("verdict", "GO")
        except Exception:
            pass
        return state

    # --- Statistiques ---

    def get_stats(self) -> Dict[str, Any]:
        return dict(self._stats)

    def get_summary(self) -> str:
        """Résumé texte du catalogue."""
        total = len(self.specs)
        by_status = {}
        for s in self.specs.values():
            by_status[s.status] = by_status.get(s.status, 0) + 1
        parts = [f"Catalogue: {total} specs"]
        for status, count in sorted(by_status.items()):
            parts.append(f"  {status}: {count}")
        rolled_back = self._stats.get("total_rolled_back", 0)
        if rolled_back:
            parts.append(f"  (total rollbacks: {rolled_back})")
        return "\n".join(parts)

    # --- Persistance ---

    def _load(self):
        """Charge le tracking depuis le JSON (pas les specs elles-mêmes)."""
        try:
            with open(CATALOG_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return

        saved_specs = data.get("specs", {})
        for spec_id, tracking in saved_specs.items():
            spec = self.specs.get(spec_id)
            if spec:
                spec.status = tracking.get("status", spec.status)
                spec.attempts = tracking.get("attempts", spec.attempts)
                spec.max_attempts = tracking.get("max_attempts", spec.max_attempts)
                spec.last_attempt = tracking.get("last_attempt", spec.last_attempt)
                spec.deployed_at = tracking.get("deployed_at", spec.deployed_at)
                spec.confirmed_at = tracking.get("confirmed_at", spec.confirmed_at)
                spec.failure_reasons = tracking.get("failure_reasons", spec.failure_reasons)

        self._combinations_generated = data.get("combinations_generated", [])
        self._stats = data.get("stats", self._stats)

        # Restaurer les specs générées par méta-évolution
        generated = data.get("generated_specs", {})
        for gen_id, gen_data in generated.items():
            if gen_id not in self.specs:
                self.specs[gen_id] = ImprovementSpec(
                    id=gen_id,
                    name=gen_data.get("name", gen_id),
                    description=gen_data.get("description", ""),
                    category=gen_data.get("category", "performance"),
                    target_file=gen_data.get("target_file", ""),
                    target_method=gen_data.get("target_method", ""),
                    difficulty=gen_data.get("difficulty", 2),
                    code_template=gen_data.get("code_template", ""),
                    validation=gen_data.get("validation", ""),
                    tags=gen_data.get("tags", []),
                    status=gen_data.get("status", "available"),
                    attempts=gen_data.get("attempts", 0),
                    max_attempts=gen_data.get("max_attempts", 3),
                    last_attempt=gen_data.get("last_attempt"),
                    deployed_at=gen_data.get("deployed_at"),
                    confirmed_at=gen_data.get("confirmed_at"),
                    failure_reasons=gen_data.get("failure_reasons", []),
                )

    def _save(self):
        """Sauvegarde le tracking dans le JSON."""
        os.makedirs(os.path.dirname(CATALOG_STATE_FILE), exist_ok=True)

        # Specs built-in : juste le tracking
        saved_specs = {}
        builtin_ids = set(_build_catalog().keys())
        for spec_id, spec in self.specs.items():
            if spec_id in builtin_ids:
                saved_specs[spec_id] = {
                    "status": spec.status,
                    "attempts": spec.attempts,
                    "max_attempts": spec.max_attempts,
                    "last_attempt": spec.last_attempt,
                    "deployed_at": spec.deployed_at,
                    "confirmed_at": spec.confirmed_at,
                    "failure_reasons": spec.failure_reasons,
                }

        # Specs générées (COMBO, CROSS) : tout sauvegarder
        generated_specs = {}
        for spec_id, spec in self.specs.items():
            if spec_id not in builtin_ids:
                generated_specs[spec_id] = {
                    "name": spec.name,
                    "description": spec.description,
                    "category": spec.category,
                    "target_file": spec.target_file,
                    "target_method": spec.target_method,
                    "difficulty": spec.difficulty,
                    "code_template": spec.code_template,
                    "validation": spec.validation,
                    "tags": spec.tags,
                    "status": spec.status,
                    "attempts": spec.attempts,
                    "max_attempts": spec.max_attempts,
                    "last_attempt": spec.last_attempt,
                    "deployed_at": spec.deployed_at,
                    "confirmed_at": spec.confirmed_at,
                    "failure_reasons": spec.failure_reasons,
                }

        data = {
            "version": 1,
            "specs": saved_specs,
            "generated_specs": generated_specs,
            "combinations_generated": self._combinations_generated,
            "stats": self._stats,
        }

        tmp_path = CATALOG_STATE_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, CATALOG_STATE_FILE)


# Singleton global
catalog = EvolutionCatalog()
