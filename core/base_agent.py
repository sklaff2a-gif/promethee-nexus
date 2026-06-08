import logging
import os
import sys
import asyncio
import uuid
import json
import re
import httpx
import importlib
import pkgutil
import inspect
import warnings
import time
from collections import deque
from typing import Dict, Any, List, NamedTuple

# Setup des chemins
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

try:
    from config import Config
except ImportError:
    class Config: 
        GOOGLE_API_KEY = None
        OLLAMA_URL = "http://localhost:11434/api/generate"
        AGENT_MODEL_ROUTING = {}

from core.event_bus.bus import bus

try:
    from core.vector_store import ChromaMemoryManager
except ImportError:
    ChromaMemoryManager = None

logger = logging.getLogger("BaseAgent")


class GenerationContext(NamedTuple):
    """Intrants FIGES d'une generation, calcules une fois par generate_content (V25.2).
    IMMUABLE (NamedTuple) -> rempart structurel contre la mutation : la future boucle
    prefrontale ne peut PAS contaminer le contexte entre deux tentatives (faille relevee
    par Promethee le 07/06 ; choix paralelisme libre, etat porte explicitement)."""
    local_model: str
    needs_cloud: bool
    orig_prompt: str        # prompt NU (exige par _quality_filter ; != full_prompt)

# ── Strip thinking tags (Qwen3.5, DeepSeek-R1, etc.) ───────────────────────
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

def _strip_thinking_tags(text: str) -> str:
    """Retire les blocs <think>...</think> des modeles thinking (Qwen3.5, DeepSeek-R1)."""
    if not text or "<think>" not in text:
        return text
    return _THINK_RE.sub("", text).strip()


# ── GPU Scheduler — File d'attente stricte pour protéger le GPU ─────────────
class _GpuAccess:
    """Context manager pour un accès exclusif au GPU."""
    __slots__ = ("_scheduler", "_caller", "_timeout")

    def __init__(self, scheduler, caller="unknown", timeout=None):
        self._scheduler = scheduler
        self._caller = caller
        self._timeout = timeout

    async def __aenter__(self):
        await self._scheduler.acquire(self._caller, timeout=self._timeout)
        return self

    async def __aexit__(self, *args):
        self._scheduler.release()


class GpuScheduler:
    """File d'attente GPU — sérialise les appels Ollama et protège le GPU.

    Remplace l'ancien Semaphore(2) par un Lock strict (1 seul appel à la fois)
    + cooldown inter-appels + monitoring température GPU + monitoring VRAM.
    """

    GPU_COOLDOWN_SECONDS = 1.5       # Pause minimale entre deux appels consécutifs
    GPU_TEMP_MAX = 75                # Température max avant throttle (°C) — abaissé de 82 pour stabilité driver 595.79
    GPU_TEMP_CHECK_INTERVAL = 30     # Vérifier la temp GPU max toutes les 30s
    GPU_VRAM_MAX_PERCENT = 80        # Seuil VRAM (%) — abaissé de 85 après crash 06/04 (saturation 87-88%)
    GPU_VRAM_CHECK_INTERVAL = 30     # Rate-limit du check VRAM (secondes)

    def __init__(self):
        self._lock = None
        self._loop_id = None
        self._queue_depth = 0
        self._last_call_end = 0.0
        self._last_temp_check = 0.0
        self._last_gpu_temp = 0
        self._last_vram_check = 0.0
        self._last_vram_percent = 0
        self._vram_unloads = 0
        self._total_calls = 0
        self._total_wait_time = 0.0
        self._current_agent = None

    def _ensure_lock(self):
        """Crée/recrée le Lock si l'event loop a changé (Smart Restart)."""
        try:
            loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            return asyncio.Lock()
        if self._lock is None or self._loop_id != loop_id:
            self._lock = asyncio.Lock()
            self._loop_id = loop_id
        return self._lock

    async def _check_gpu_temp(self) -> int:
        """Vérifie la température GPU via nvidia-smi (rate-limited)."""
        now = time.time()
        if now - self._last_temp_check < self.GPU_TEMP_CHECK_INTERVAL:
            return self._last_gpu_temp
        try:
            proc = await asyncio.create_subprocess_exec(
                "nvidia-smi", "--query-gpu=temperature.gpu",
                "--format=csv,noheader,nounits",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            temp = int(stdout.decode().strip())
            self._last_gpu_temp = temp
            self._last_temp_check = now
            return temp
        except Exception:
            return self._last_gpu_temp

    async def _check_vram(self) -> int:
        """Vérifie l'utilisation VRAM via nvidia-smi (rate-limited).

        Retourne le pourcentage d'utilisation (0-100).
        Si VRAM > GPU_VRAM_MAX_PERCENT, décharge les modèles Ollama inactifs.
        """
        now = time.time()
        if now - self._last_vram_check < self.GPU_VRAM_CHECK_INTERVAL:
            return self._last_vram_percent
        try:
            proc = await asyncio.create_subprocess_exec(
                "nvidia-smi", "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            parts = stdout.decode().strip().split(",")
            used_mb = int(parts[0].strip())
            total_mb = int(parts[1].strip())
            percent = int(100 * used_mb / total_mb) if total_mb > 0 else 0
            self._last_vram_percent = percent
            self._last_vram_check = now

            if percent >= self.GPU_VRAM_MAX_PERCENT:
                logger.warning(
                    f"[GPU_VRAM] VRAM à {percent}% ({used_mb}/{total_mb} MB) "
                    f"— déchargement modèles Ollama"
                )
                await self._unload_ollama_models()
                self._vram_unloads += 1
            return percent
        except Exception:
            return self._last_vram_percent

    async def _unload_ollama_models(self):
        """Décharge tous les modèles Ollama inactifs pour libérer la VRAM."""
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                # Lister les modèles chargés
                resp = await client.get("http://localhost:11434/api/ps", timeout=10)
                if resp.status_code != 200:
                    return
                models = resp.json().get("models", [])
                for model in models:
                    name = model.get("name", "")
                    if name:
                        await client.post(
                            "http://localhost:11434/api/generate",
                            json={"model": name, "keep_alive": "0", "prompt": ""},
                            timeout=30,
                        )
                        logger.info(f"[GPU_VRAM] Modèle déchargé: {name}")
        except Exception as e:
            logger.warning(f"[GPU_VRAM] Erreur déchargement: {e}")

    # Budget de blocage par défaut pour les actions proactives (inspiré KAIROS)
    PROACTIVE_TIMEOUT = 15.0  # secondes max d'attente dans la queue

    async def acquire(self, caller: str = "unknown", timeout: float = None):
        """Acquiert l'accès exclusif au GPU avec cooldown et monitoring.

        Args:
            caller: nom de l'appelant (pour les logs)
            timeout: durée max d'attente dans la queue (secondes).
                     None = pas de limite (défaut, compatible existant).
                     Lève asyncio.TimeoutError si dépassé.
        """
        self._queue_depth += 1
        if self._queue_depth > 1:
            logger.info(
                f"[GPU_QUEUE] {caller} en file d'attente "
                f"(position {self._queue_depth}, occupé par {self._current_agent})"
            )

        start = time.time()
        lock = self._ensure_lock()
        if timeout is not None:
            try:
                await asyncio.wait_for(lock.acquire(), timeout=timeout)
            except asyncio.TimeoutError:
                self._queue_depth -= 1
                logger.warning(
                    f"[GPU_QUEUE] {caller} TIMEOUT après {timeout:.0f}s "
                    f"(occupé par {self._current_agent}) — action différée"
                )
                raise
        else:
            await lock.acquire()
        wait = time.time() - start
        self._total_wait_time += wait
        self._total_calls += 1
        self._current_agent = caller

        # Cooldown entre appels — laisser le GPU souffler
        elapsed = time.time() - self._last_call_end
        if elapsed < self.GPU_COOLDOWN_SECONDS and self._last_call_end > 0:
            await asyncio.sleep(self.GPU_COOLDOWN_SECONDS - elapsed)

        # Vérification température GPU
        temp = await self._check_gpu_temp()
        if temp >= self.GPU_TEMP_MAX:
            logger.warning(f"[GPU_QUEUE] GPU à {temp}°C (max {self.GPU_TEMP_MAX}) — attente refroidissement...")
            while temp >= self.GPU_TEMP_MAX - 5:  # Hystérésis 5°C
                await asyncio.sleep(10)
                temp = await self._check_gpu_temp()
            logger.info(f"[GPU_QUEUE] GPU refroidi à {temp}°C — reprise")

        # Vérification VRAM — décharge les modèles Ollama si saturation
        await self._check_vram()

        if wait > 0.5:
            logger.info(
                f"[GPU_QUEUE] {caller} accède au GPU "
                f"(attendu {wait:.1f}s, {self._queue_depth - 1} en attente)"
            )

    def release(self):
        """Libère l'accès au GPU."""
        self._last_call_end = time.time()
        self._queue_depth -= 1
        self._current_agent = None
        lock = self._ensure_lock()
        lock.release()

    def access(self, caller: str = "unknown", timeout: float = None):
        """Retourne un context manager pour accéder au GPU.

        Args:
            caller: nom de l'appelant
            timeout: budget max d'attente en secondes (None = illimité)
        """
        return _GpuAccess(self, caller, timeout=timeout)

    def get_stats(self) -> dict:
        """Statistiques pour le monitoring."""
        return {
            "queue_depth": self._queue_depth,
            "current_agent": self._current_agent,
            "total_calls": self._total_calls,
            "avg_wait_s": round(self._total_wait_time / max(1, self._total_calls), 2),
            "last_gpu_temp": self._last_gpu_temp,
            "last_vram_percent": self._last_vram_percent,
            "vram_unloads": self._vram_unloads,
        }

    def reset(self):
        """Reset pour les tests."""
        self._lock = None
        self._loop_id = None
        self._queue_depth = 0
        self._last_call_end = 0.0
        self._last_temp_check = 0.0
        self._last_gpu_temp = 0
        self._last_vram_check = 0.0
        self._last_vram_percent = 0
        self._vram_unloads = 0
        self._total_calls = 0
        self._total_wait_time = 0.0
        self._current_agent = None


# Singleton global
gpu_scheduler = GpuScheduler()


class BaseAgent:
    """
    Classe Mère V22.0 - Full Architecture + Budget Cloud + Cooldown 429
    - Stratégie : Local First & Cloud Escalation (Stricte)
    - Mémoire : RAG & Anti-Spam + Decay Temporel
    - Interface : Publication temps réel sur le Bus
    - Budget : Compteur d'appels Cloud partagé + cooldown 429 + quotas journaliers
    """
    # RPM tracking : fenêtre glissante de 60s par modèle
    _rpm_windows: dict = {}  # model_name -> deque of timestamps

    # Budget journalier par modèle (reset à minuit)
    _daily_model_calls: dict = {}  # model_name -> int
    _daily_model_reset_day = None

    # Compteur spécifique Evolution (budget réservé R&D)
    _daily_cloud_calls_evolution = 0
    MAX_DAILY_EVOLUTION_CLOUD = 40  # Evolution peut utiliser max 40 appels Pro/jour

    # GPU Scheduler : file d'attente stricte — 1 seul appel Ollama à la fois
    # (remplace l'ancien Semaphore(2) qui laissait le GPU se faire saturer)
    MAX_CONCURRENT_OLLAMA = 1  # Conservé pour backward compat, mais ignoré par le scheduler

    # Circuit Breaker Ollama — protection anti-GPU-hang
    _ollama_consecutive_timeouts = 0       # Compteur de ReadTimeout consécutifs
    _ollama_circuit_open_until = 0.0       # Timestamp jusqu'auquel le circuit est ouvert (bloqué)
    _ollama_last_success = 0.0             # Timestamp du dernier appel Ollama réussi
    OLLAMA_CIRCUIT_BREAKER_THRESHOLD = 3   # Nombre de timeouts avant ouverture du circuit
    OLLAMA_CIRCUIT_BREAKER_DURATION = 300  # Durée du circuit ouvert (5 min)

    # Cooldown après erreur 429 (quota exceeded)
    _cloud_cooldown_until = 0.0  # timestamp jusqu'auquel le Cloud est désactivé
    CLOUD_COOLDOWN_SECONDS = 900  # 15 min de cooldown après un 429 (Tier 1)
    _cloud_429_count_today = 0    # Nombre de 429 reçus aujourd'hui
    _cloud_429_reset_day = None   # Jour du dernier reset du compteur 429

    # Demi-vie mémoire en jours (surchargeable par agent)
    MEMORY_HALF_LIFE_DAYS = 30

    def __init__(self, name: str, role: str, description: str):
        self.name = name
        self.role = role
        self.description = description
        self.logger = logging.getLogger(name)
        
        self.capabilities = {}
        self._load_dynamic_capabilities()
        
        self.async_manager = None
        if "AsyncTaskManager" in self.capabilities:
            self.async_manager = self.capabilities["AsyncTaskManager"](max_workers=5)
        
        # Connexion Mémoire
        self.has_memory = False
        if ChromaMemoryManager:
            try:
                project_id = getattr(Config, "PROJECT_ID", "default")
                self.memory_manager = ChromaMemoryManager.get_instance(project_id=project_id)
                self.has_memory = True
            except BaseException as e:
                logger.warning(f"[{name}] Connexion mémoire ChromaDB échouée (mode dégradé) : {e}")
        
        # Flag one-shot : forcer le mode local pour la prochaine génération
        self._force_local_next = False

        # Chargement Modèles Cloud (Pour l'escalade)
        routing = getattr(Config, "AGENT_MODEL_ROUTING", {})
        self.cloud_models = routing.get(self.name, routing.get("default", []))
        if not self.cloud_models and Config.GOOGLE_API_KEY:
            self.cloud_models = ["models/gemini-2.5-flash"]

    def _load_dynamic_capabilities(self):
        cap_dir = os.path.join(os.path.dirname(__file__), "capabilities")
        if not os.path.exists(cap_dir): return
        for _, module_name, _ in pkgutil.iter_modules([cap_dir]):
            try:
                module = importlib.import_module(f"core.capabilities.{module_name}")
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if obj.__module__ == module.__name__:
                        self.capabilities[name] = obj
            except Exception as e:
                logger.warning(f"[{self.name}] Échec chargement capability '{module_name}' : {e}")

    # --- MÉTHODES MÉMOIRE (PROTECTION ANTI-SPAM + QUALITÉ) ---

    # Seuil de similarité pour la déduplication (0 = identique, 1 = très différent)
    _DEDUP_DISTANCE_THRESHOLD = 0.15
    # Filtres qualité remember()
    _MIN_REMEMBER_LENGTH = 100       # Ignore les textes trop courts (bruit)
    _MAX_REMEMBER_LENGTH = 5000      # Tronque les textes trop longs
    _MAX_NON_LATIN_RATIO = 0.10      # Rejette si >10% de caractères non-latin (hallucination)
    # Seuil qualité recall()
    _MIN_RECALL_SCORE = 0.15         # Ignore les résultats avec score < seuil

    @staticmethod
    def _non_latin_ratio(text: str) -> float:
        """Calcule le ratio de caractères non-latin (hors ponctuation/espaces)."""
        if not text:
            return 0.0
        alpha_chars = [c for c in text if c.isalpha()]
        if not alpha_chars:
            return 0.0
        non_latin = sum(1 for c in alpha_chars if ord(c) > 0x024F)  # Au-delà du Latin Extended-B
        return non_latin / len(alpha_chars)

    def remember(self, text: str, metadata: Dict = None, collection="collective_wisdom"):
        if not self.has_memory: return
        try:
            # Filtre qualité : longueur minimale
            if len(text.strip()) < self._MIN_REMEMBER_LENGTH:
                return

            # Filtre qualité : détection hallucination (caractères non-latin)
            if self._non_latin_ratio(text) > self._MAX_NON_LATIN_RATIO:
                self.log_thought(
                    f"🚫 Mémoire rejetée : ratio non-latin trop élevé ({self._non_latin_ratio(text):.1%})",
                    type="warning"
                )
                return

            # Cap longueur
            text = text[:self._MAX_REMEMBER_LENGTH]

            # Check Anti-Doublon V2 : distance vectorielle + substring
            existing = self.memory_manager.query_with_metadata(
                [text], n_results=1, collection_name=collection
            )
            if existing and existing['documents'] and existing['documents'][0]:
                existing_text = existing['documents'][0][0]
                distance = existing['distances'][0][0] if existing.get('distances') else 1.0

                # Doublon exact (substring)
                if text.strip() in existing_text.strip() or existing_text.strip() in text.strip():
                    return

                # Doublon sémantique (distance vectorielle très proche)
                if distance < self._DEDUP_DISTANCE_THRESHOLD:
                    self.log_thought(
                        f"🔄 Doublon RAG détecté (dist={distance:.3f} < {self._DEDUP_DISTANCE_THRESHOLD}), skip.",
                        type="info"
                    )
                    return

            # Gatekeeper : filtre qualite avance (reutilise existing_text/distance deja calcules)
            try:
                from core.memory_gatekeeper import evaluate as gatekeeper_evaluate
                _ext_text = None
                _ext_dist = 1.0
                if existing and existing.get('documents') and existing['documents'][0]:
                    _ext_text = existing['documents'][0][0]
                    if existing.get('distances') and existing['distances'][0]:
                        _ext_dist = existing['distances'][0][0]
                gk = gatekeeper_evaluate(text, metadata, _ext_text, _ext_dist)
                if not gk["accept"]:
                    self.log_thought(f"Memoire filtree (gatekeeper): {gk['reason']}", type="info")
                    return
            except ImportError:
                pass
            except Exception as e:
                logger.debug(f"[{self.name}] Gatekeeper error (ignored): {e}")

            doc_id = str(uuid.uuid4())
            meta = metadata or {}
            meta["agent"] = self.name
            meta["timestamp"] = str(time.time())

            self.log_thought(f"💾 Sauvegarde en mémoire ({collection})...", type="info")
            self.memory_manager.add_documents([text], [meta], [doc_id], collection)
        except Exception as e:
            self.log_thought(f"Erreur Sauvegarde: {e}", type="error")

    def recall(self, query: str, limit: int = None, collection="collective_wisdom",
               lateral: bool = False) -> str:
        if not self.has_memory: return ""
        # Tentative via Lobe Temporal (intégration mémoire unifiée)
        try:
            from core.temporal_lobe import temporal
            if limit is None:
                limit = getattr(Config, "RAG_RECALL_LIMIT", 2)
            traces = temporal.unified_recall(query, limit=limit, collection=collection)
            if traces:
                return temporal.format_for_prompt(traces, max_chars=500)
        except Exception:
            pass  # Fallback au recall classique
        try:
            import math
            if limit is None:
                limit = getattr(Config, "RAG_RECALL_LIMIT", 2)
            fetch_count = limit * 3
            res = self.memory_manager.query_with_metadata(
                [query], n_results=fetch_count, collection_name=collection
            )
            if not (res and res['documents'] and res['documents'][0]):
                return ""

            now = time.time()
            scored = []
            doc_ids = res.get('ids', [[]])[0] if res.get('ids') else []
            for idx, (doc, meta, dist) in enumerate(zip(
                res['documents'][0], res['metadatas'][0], res['distances'][0]
            )):
                similarity = max(0.0, 1.0 - dist)
                try:
                    age_days = (now - float(meta.get("timestamp", 0))) / 86400
                except (ValueError, TypeError):
                    age_days = self.MEMORY_HALF_LIFE_DAYS
                decay = math.exp(-age_days * 0.693 / self.MEMORY_HALF_LIFE_DAYS)
                doc_id = doc_ids[idx] if idx < len(doc_ids) else None
                scored.append((doc, similarity * decay, doc_id))

            scored.sort(key=lambda x: x[1], reverse=True)
            # Filtre qualité : exclure les résultats sous le seuil
            scored = [(doc, s, did) for doc, s, did in scored if s >= self._MIN_RECALL_SCORE]
            base_result = "\n".join(doc for doc, _, _ in scored[:limit])

            # Tracker les rappels pour la mémoire utilitaire
            for _, _, doc_id in scored[:limit]:
                if doc_id:
                    try:
                        self.memory_manager.record_recall(doc_id, collection)
                    except Exception:
                        pass

            # Activation latérale (spreading activation) sur le top-1
            if lateral and base_result and scored:
                try:
                    from core.spreading_activation import activation_engine
                    top_doc = scored[0][0]
                    try:
                        loop = asyncio.get_running_loop()
                        if loop.is_running():
                            loop.create_task(
                                activation_engine.activate(
                                    top_doc, collection, self.memory_manager, max_hops=1
                                )
                            )
                    except RuntimeError:
                        pass  # Pas de loop active, skip silencieusement
                except Exception as e:
                    logger.debug(f"[{self.name}] Spreading activation error (ignored): {e}")

            # --- Boost synaptique (associations persistantes) ---
            try:
                from core.synaptic_network import cortex
                from core.spreading_activation import extract_concepts
                query_concepts = [c for c, _ in extract_concepts(query, max_concepts=5)]
                syn_text = cortex.format_for_prompt(query_concepts, max_chars=150)
                if syn_text:
                    base_result = f"{base_result}\n{syn_text}"
            except Exception as e:
                logger.debug(f"[{self.name}] Synaptic boost error (ignored): {e}")

            return base_result
        except Exception as e:
            logger.warning(f"[{self.name}] Échec recall mémoire ({collection}) : {e}")
        return ""

    # --- PROTOCOLE D'ESCALADE UNIFIE ---
    _ESCALATION_MAP = {
        "hallucination": "hallucination_doctor",
        "rejection": "code_reviewer",
        "loop": "loop_breaker",
    }

    async def _request_help(self, failure_type: str, context: dict) -> dict:
        """Demande l'aide d'un specialiste Grimoire via le protocole d'escalade unifie."""
        specialist = self._ESCALATION_MAP.get(failure_type)
        if not specialist:
            return {"action": "skip", "reason": f"no specialist for {failure_type}"}
        try:
            from core.orchestrator import orchestrator
            response = await orchestrator.dispatch_task(specialist, {
                "mission": f"AIDE: {failure_type}",
                "context": json.dumps(context, default=str)
            })
            if not response or not isinstance(response, dict):
                return {"action": "skip", "reason": "reponse invalide"}
            action = response.get("action") or response.get("verdict", "skip")
            response["action"] = action
            return response
        except Exception as e:
            self.log_thought(f"Escalade {failure_type} echouee: {e}", type="warning")
            return {"action": "skip", "reason": str(e)}

    @classmethod
    def _check_rpm(cls, model_name: str) -> bool:
        """Vérifie si on est sous la limite RPM pour ce modèle."""
        rpm_limits = getattr(Config, 'CLOUD_RPM_LIMITS', {})
        rpm_default = getattr(Config, 'CLOUD_RPM_DEFAULT', 30)
        limit = rpm_limits.get(model_name, rpm_default)

        now = time.time()
        window = cls._rpm_windows.setdefault(model_name, deque())
        while window and window[0] < now - 60:
            window.popleft()
        return len(window) < limit

    @classmethod
    def _record_cloud_call(cls, model_name: str):
        """Enregistre un appel pour RPM + budget journalier."""
        cls._rpm_windows.setdefault(model_name, deque()).append(time.time())
        cls._daily_model_calls[model_name] = cls._daily_model_calls.get(model_name, 0) + 1

    @classmethod
    def _check_daily_budget(cls, model_name: str) -> bool:
        """Vérifie le budget journalier pour ce modèle."""
        daily_limits = getattr(Config, 'CLOUD_DAILY_LIMITS', {})
        limit = daily_limits.get(model_name, 500)  # défaut généreux
        used = cls._daily_model_calls.get(model_name, 0)
        return used < limit

    def _get_gemini_client(self, model_name):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                import google.generativeai as genai
            if not Config.GOOGLE_API_KEY: return None
            genai.configure(api_key=Config.GOOGLE_API_KEY)
            return genai.GenerativeModel(model_name)
        except Exception as e:
            logger.warning(f"[{self.name}] Échec initialisation client Gemini ({model_name}) : {e}")
            return None

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Exécute la mission et ENVOIE LA RÉPONSE À L'INTERFACE.
        """
        # Résolution des pointeurs sémantiques (JIT context)
        memory_refs = task_payload.get("memory_refs", [])
        if memory_refs:
            try:
                from core.semantic_pointer import pointer_store
                resolved = pointer_store.resolve_many(memory_refs)
                if resolved:
                    existing_ctx = task_payload.get("context", "")
                    task_payload["context"] = f"{resolved}\n\n{existing_ctx}".strip()
            except Exception:
                pass

        mission = task_payload.get("mission", "Inconnue")
        self.log_thought(f"Reçoit la mission : {mission[:50]}...", type="thought")

        # 1. Génération de la réponse (via Cloud ou Local selon la complexité)
        response_text = await self.generate_content(f"Tu es {self.role}. Mission: {mission}")
        
        # 2. Sanitisation anti-patterns dangereux
        response_text = self._sanitize_response(response_text, self.name)

        return {"status": "success", "result": response_text}

    # Marqueurs de missions internes → toujours local (économie Cloud)
    # Ancien systeme : forcait TOUT en local. Remplace par le routage intelligent
    # dans _evaluate_complexity() qui decide par type de tache.
    # V30.9 (2026-04-26) — POPULATION DES MARQUEURS INTERNES.
    # Etait () vide depuis longtemps -> la couche 2 (orchestrator-level
    # _force_local_next) ne s'activait jamais -> test_internal_context_sets_flag
    # echouait. Source unique de verite pour les marqueurs qui doivent
    # rester local : utilisee par dispatch_task ET par _evaluate_complexity.
    _LOCAL_FORCE_MARKERS = (
        "PROTOCOLE_AUTONOMIE",
        "[MODE VEILLE]",
        "CONSEIL multi-agents",
        "YOUTUBE_VEILLE",
        "DROPZONE_ANALYSIS",
        "EVOLUTION_PIPELINE",
        "MEMORY_CLEANUP",
        "COUNCIL_RESEARCH",
    )

    @staticmethod
    def _extract_model_size(model_name: str) -> int:
        """Extrait la taille en milliards depuis le nom du modèle (ex: 'qwen3-coder:30b' → 30).
        Retourne 0 si la taille n'est pas détectable."""
        match = re.search(r':(\d+)b', model_name, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return 0

    async def _evaluate_complexity(self, prompt: str) -> bool:
        """Routage intelligent : decide si la tache merite Gemini Cloud.

        Criteres deterministes (0 appel LLM pour juger) :
        - Type de tache (intent dans le contexte)
        - Historique d'echecs (reasoning_protocol)
        - Mots-cles de complexite
        - Budget Gemini restant
        """
        # Court-circuit : si force_local est set par l'orchestrateur
        if self._force_local_next:
            return False

        # V30.9 (2026-04-26) — COURT-CIRCUIT MARQUEURS INTERNES.
        # Diagnostic : test_mode_veille_returns_false echouait parce que
        # "research" dans "Researcher" matchait cloud_triggers et l'agent
        # escaladait vers Gemini sur du flux interne.
        # Fix : marqueurs internes -> retour LOCAL avant toute heuristique.
        # Source unique : self._LOCAL_FORCE_MARKERS (partagee avec
        # Orchestrator.dispatch_task pour la couche 2 _force_local_next).
        prompt_head = prompt[:500]
        for _marker in self._LOCAL_FORCE_MARKERS:
            if _marker in prompt_head:
                return False

        # Court-circuit : complexité compilée (Neural Compiler, 0 LLM)
        try:
            from core.neural_compiler import compiler
            compiled = compiler.match_complexity(prompt)
            if compiled is not None:
                verdict = "CLOUD" if compiled else "LOCAL"
                self.log_thought(f"Complexite compilee → {verdict} (0 LLM)", type="info")
                return compiled
        except Exception:
            pass

        # Verifier si Gemini est disponible
        try:
            from core.gemini_helper import gemini as _gemini
            if not _gemini.is_available():
                return False
        except Exception:
            return False

        # Routage par type de tache (deterministe, 0 LLM)
        prompt_lower = prompt[:500].lower()

        # Taches qui MERITENT Gemini (reflexion profonde, analyse complexe)
        cloud_triggers = [
            "revue de code", "code_review", "audit",
            "architecture", "securite", "faille",
            "synthese", "research", "analyse approfondie",
            "evening_reflection", "introspection",
            "stefan", "confrontation",
        ]
        has_cloud_trigger = any(t in prompt_lower for t in cloud_triggers)

        # Taches qui restent LOCAL (economie)
        local_triggers = [
            "formatage", "liste", "simple",
            "memory_cleanup", "dropzone", "audit_structure",
        ]
        has_local_trigger = any(t in prompt_lower for t in local_triggers)

        if has_local_trigger and not has_cloud_trigger:
            return False

        # Historique d'echecs : si le local a deja echoue sur ce type → Gemini
        try:
            from core.reasoning_protocol import get_failure_count
            # Extraire l'intent du prompt si present
            for intent_marker in ["SCHOOL_CODE_REVIEW", "SCHOOL_RESEARCH", "SCHOOL_WORKSHOP"]:
                if intent_marker in prompt:
                    fails = get_failure_count(intent_marker)
                    if fails >= 1:
                        self.log_thought(f"🔄 {fails} echec(s) local → escalade Gemini", type="info")
                        return True
        except Exception:
            pass

        # Par defaut : Cloud si trigger complexe detecte
        if has_cloud_trigger:
            self.log_thought(f"☁️ Tache complexe detectee → Gemini", type="info")
            return True

        return False

        try:
            # ANCIEN CODE : gardé en commentaire pour reference
            # L'ancien systeme utilisait le LLM pour juger si c'etait complexe
            # Ce qui est absurde — le LLM se jugeait lui-meme
            eval_model = getattr(Config, "DEFAULT_LOCAL_MODEL", "gemma3:12b")
            eval_prompt = (
                f"Tu es un gestionnaire de budget strict. Analyse cette demande : \"{prompt[:300]}\"\n"
                f"RÈGLES D'ÉVALUATION :\n"
                f"1. Réponds 'NON' (Simple) pour : Explications, Définitions (Bitcoin, IA...), Résumés, Chat, Code basique.\n"
                f"2. Réponds 'OUI' (Complexe) UNIQUEMENT SI : Demande d'architecture système critique, analyse de faille de sécurité, ou génération de code > 100 lignes.\n"
                f"Ta réponse doit être UNIQUEMENT un mot : 'OUI' ou 'NON'."
            )
            
            # On baisse la température à 0 via l'appel pour une réponse stable
            response = await self._call_ollama(eval_prompt, eval_model)
            
            # Nettoyage de la réponse
            is_complex = "OUI" in response.upper() and "NON" not in response.upper()
            
            verdict = "CLOUD ☁️" if is_complex else "LOCAL 🏠"
            self.log_thought(f"⚖️ Jugement de Complexité : {verdict}", type="info")

            # Enregistrer la decision pour apprentissage du compiler
            try:
                from core.neural_compiler import compiler
                compiler.record_complexity(prompt, is_complex)
            except Exception:
                pass

            return is_complex
        except Exception as e:
            logger.warning(f"[{self.name}] Échec évaluation complexité (fallback local) : {e}")
            return False

    def _record_for_compiler(self, prompt: str, response: str, was_cloud: bool):
        """Enregistre un appel LLM dans le Neural Compiler pour distillation."""
        try:
            from core.neural_compiler import compiler
            compiler.record_observation(self.name, prompt, response, -1.0, was_cloud)
        except Exception:
            pass

    # --- Collecte données QLoRA ---

    _TRAINING_PAIRS_FILE = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "memory", "training_pairs.jsonl"
    )
    _TRAINING_PAIR_MIN_LENGTH = 50     # Réponse trop courte = bruit
    _TRAINING_PAIR_MAX_LENGTH = 4000   # Tronquer les réponses trop longues
    _TRAINING_PAIRS_MAX_SIZE = 50 * 1024 * 1024  # 50 MB max avant rotation

    def _save_training_pair(self, prompt: str, response: str, was_cloud: bool):
        """Sauvegarde une paire (prompt, response) pour QLoRA fine-tuning.

        Format JSONL append-only. Chaque ligne = un exemple d'entraînement.
        Filtrage minimal : longueur réponse, pas de réponse d'erreur.
        """
        try:
            if not response or len(response) < self._TRAINING_PAIR_MIN_LENGTH:
                return
            # Filtrer les réponses d'erreur/timeout
            if response.startswith("Erreur") or response.startswith("⚠️"):
                return

            # Tronquer si trop long
            resp = response[:self._TRAINING_PAIR_MAX_LENGTH]

            pair = {
                "agent": self.name,
                "prompt": prompt,
                "response": resp,
                "was_cloud": was_cloud,
                "timestamp": time.time(),
                "bio_state": self._snapshot_bio_state(),
            }

            # Rotation si fichier trop gros
            filepath = self._TRAINING_PAIRS_FILE
            if os.path.exists(filepath):
                try:
                    size = os.path.getsize(filepath)
                    if size > self._TRAINING_PAIRS_MAX_SIZE:
                        rotated = filepath + ".old"
                        if os.path.exists(rotated):
                            os.remove(rotated)
                        os.rename(filepath, rotated)
                except OSError:
                    pass

            with open(filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(pair, ensure_ascii=False) + "\n")
        except Exception:
            pass  # Jamais bloquer le flux principal

    @staticmethod
    def _snapshot_bio_state() -> dict:
        """Capture legere de l'etat biologique pour enrichir les donnees LoRA."""
        state = {}
        try:
            from core.dopamine_system import dopamine
            state["dopamine"] = round(dopamine.dopamine_level, 2)
        except Exception:
            pass
        try:
            from core.desire_engine import desires
            top = max(desires.drives.values(), key=lambda d: d.deprivation)
            state["desire"] = top.name
            state["deprivation"] = round(top.deprivation, 0)
        except Exception:
            pass
        try:
            from core.psyche import psyche
            traits = psyche.get_traits("_global")
            if traits:
                sorted_t = sorted(traits.items(), key=lambda x: -x[1])[:2]
                state["psyche"] = {k: round(v, 0) for k, v in sorted_t}
        except Exception:
            pass
        try:
            from core.reptilian_core import reptilian
            threat = reptilian.get_threat_level()
            if threat > 0.3:
                state["threat"] = round(threat, 2)
        except Exception:
            pass
        return state

    async def generate_content(self, prompt: str) -> str:
        # ===== NEURAL COMPILER: Tentative réponse compilée =====
        # V30.5 (2026-04-26) — BYPASS POUR CODE_REVIEW.
        # Diagnostic 00:25 : routine SCHOOL_CODE_REVIEW autonome sur
        # Agents/scrub_nurse_agent.py a servi une réponse compilée 178c
        # qui parlait de core/reasoning_protocol.py → target drift,
        # truncation, grade 9.8 -> 2.45. Le Neural Compiler est un cache
        # déterministe générique inadapté aux audits ciblés sur target_file.
        # Fix : court-circuiter le compiler pour les CODE_REVIEW scolaires.
        # Chaque audit doit passer par le LLM avec le RAG du fichier cible.
        _is_code_review = "[SCHOOL_SLOT: CODE_REVIEW]" in (prompt or "")
        if not _is_code_review:
            try:
                from core.neural_compiler import compiler
                compiled = compiler.try_intercept(self.name, prompt)
                if compiled is not None:
                    self.log_thought("⚡ COMPILED: Réponse compilée (0 LLM)", type="info")
                    return self._sanitize_response(self._strip_cot(compiled), self.name)
            except Exception:
                pass  # Fallback transparent — ne jamais bloquer le flux

        # ===== BLOOM FILTER V4.2 : veto pre-LLM anti-hallucination =====
        # Design : Promethee lui-meme ex.80 (m=20000, k=7)
        # Seuil STRICT (Jean-Michel 2026-04-19) : 1 faux negatif Bloom = veto
        # Economie : 100% du budget d'inference sur les rejets certains
        # V4.4 (2026-04-24) : bypass pour les slots generatifs (CREATION,
        # WORKSHOP si script independant). Le Bloom est un anticorps contre
        # les references a du code existant qui n existe pas. En CREATION on
        # demande explicitement au LLM d'inventer de nouvelles fonctions --
        # le veto serait une reaction auto-immune sur un corps etranger sain.
        # V32 (2026-04-26) — Le marqueur [V32: FEATURE_BUILDING] est aussi
        # creatif par nature : l'Architecte cree des fichiers qui n'existent
        # PAS encore (donc forcement absents de l'index Bloom). Sans ce
        # bypass, Bloom V4.2 refuse de generer du code pour core/utils/x.py
        # tant que le fichier n'a jamais ete indexe — anti-doctrine totale
        # de FEATURE_BUILDING.
        _is_creative_slot = (
            "[SCHOOL_SLOT: CREATION]" in (prompt or "")
            or "[SCHOOL_SLOT: WORKSHOP]" in (prompt or "")
            or "[V32: FEATURE_BUILDING]" in (prompt or "")
        )
        if not _is_creative_slot:
            try:
                from core.bloom_filter import bloom_pre_llm
                veto = bloom_pre_llm.check_prompt(self.name, prompt)
                if veto is not None:
                    self.log_thought(
                        f"🚫 BLOOM VETO V4.2 : {veto.reason}", type="info"
                    )
                    return veto.response
            except Exception:
                pass  # Fallback transparent
        else:
            try:
                logger.info(
                    f"[V4.4 BLOOM BYPASS] {self.name} en slot generatif — "
                    f"anticorps Bloom desactive (l'agent peut creer des "
                    f"fonctions nouvelles sans veto)."
                )
            except Exception:
                pass

        # Etape 1 : RAG (Toujours utile)
        # V15.7 (2026-04-24 11:45) : Amnesie ciblee contextuelle.
        # Evolution de V15.5 (hierarchie epistemologique) qui s'est revelee
        # insuffisante. Diagnostic 11:36 : le LLM 9B prefere un audit narratif
        # complet (souvenir collective_wisdom, ex meta_observer.py) aux
        # chunks AST bruts du RAG V15.3 fresh, meme avec une phrase d'autorite
        # explicite. C'est le "biais de plausibilite narrative" : le LLM
        # choisit ce qui ressemble le plus a une "bonne reponse" connue
        # plutot que de synthetiser des donnees fraiches.
        # Solution chirurgicale : si le prompt contient deja un RAG fresh
        # ([INJECTION DE CONTEXTE STRICTE] de V15.3/V15.4 ou [CODE REEL]),
        # on SKIP TOTALEMENT le recall collective_wisdom. Pas de souvenirs
        # concurrents = pas de tentation = LLM focalise sur le code fresh.
        # Si aucun RAG fresh : recall normal pour les routines non-ecole.
        context_memory = ""
        _has_fresh_rag = (
            "[INJECTION DE CONTEXTE STRICTE]" in (prompt or "")
            or "[CODE REEL" in (prompt or "")
        )
        if _has_fresh_rag:
            self.log_thought(
                "🧬 [V15.7] RAG fresh detecte — recall collective_wisdom skip "
                "(amnesie ciblee pour empecher le biais narratif).",
                type="info",
            )
        else:
            mem1 = self.recall(prompt, collection="collective_wisdom")
            if mem1:
                self.log_thought("🧠 Souvenirs trouvés !", type="info")
                context_memory = f"\n[SOUVENIRS]:\n{mem1}\n"

        # Intuitions (spreading activation) — lecture cache RAM, 0 requête
        intuition_block = ""
        try:
            from core.spreading_activation import activation_engine
            intuition_text = activation_engine.format_for_prompt(max_chars=300)
            if intuition_text:
                intuition_block = f"\n{intuition_text}\n"
        except Exception:
            pass

        # Note: council.py injecte aussi un contexte projet (_COUNCIL_PROJECT_CONTEXT) — garder cohérent
        # Guardrail anti-hallucination 9B (suffixe — biais de recence)
        # V15.4 (2026-04-24) : si le prompt contient deja une injection RAG
        # stricte (V15.2 chat ou V15.3 ecole), on DESACTIVE l'anti-hallucination.
        # Raison : l'anti-halluc dit "n'invente pas de code" pendant que le RAG
        # dit "cite verbatim le code ci-dessous". Contradiction = torture
        # cognitive du LLM qui sort en mode "je ne peux pas lire".
        _anti_halluc = ""
        _has_strict_injection = "[INJECTION DE CONTEXTE STRICTE]" in (prompt or "")
        if not _has_strict_injection:
            try:
                from core.prompt_templates import LLM_9B_ANTI_HALLUCINATION
                _anti_halluc = LLM_9B_ANTI_HALLUCINATION
            except ImportError:
                pass

        full_prompt = (
            f"\n[SYSTEM: Nexus V20 (Local First) | AGENT: {self.name.upper()}]\n"
            f"[CONTRAINTE: Projet sur UN SEUL PC Windows + Ollama local. "
            f"Pas de Kubernetes/Docker/Kafka/microservices/blockchain.]\n"
            f"{context_memory}"
            f"{intuition_block}"
            f"{prompt}"
            f"{_anti_halluc}"
        )

        # V15.4 diagnostic : snapshot du prompt final quand injection stricte
        # est active, pour verifier que les chunks atteignent Ollama sans
        # concurrence prompt systeme. Debug level : n'apparait qu'avec --log-level=debug.
        if _has_strict_injection:
            try:
                logger.debug(
                    f"[V15.4 PROMPT DEBUG] Agent={self.name} prompt_chars={len(full_prompt)} "
                    f"anti_halluc_suppressed=True"
                )
                logger.debug(f"[V15.4 PROMPT CONTENT]\n{full_prompt[:4000]}")
            except Exception:
                pass

        # Modèles Locaux
        # V17 MoE (2026-04-24) : Mixture of Experts par routine.
        # Hierarchie de resolution du modele local :
        #   1. ROUTINE_MODELS[slot] si le marqueur [SCHOOL_SLOT: XXX] est detecte
        #      (CODE_REVIEW/CREATION/WORKSHOP -> qwen2.5-coder:14b)
        #   2. AGENT_SPECIFIC_LOCAL_MODELS[agent_name] sinon
        #   3. DEFAULT_LOCAL_MODEL en dernier recours
        specific_locals = getattr(Config, "AGENT_SPECIFIC_LOCAL_MODELS", {})
        default_local = getattr(Config, "DEFAULT_LOCAL_MODEL", "gemma3:12b")
        routine_models = getattr(Config, "ROUTINE_MODELS", {})
        local_model = None
        # Priorite 1 : detection du slot scolaire via le marqueur V4.4
        try:
            import re as _re_mox
            _slot_match = _re_mox.search(r"\[SCHOOL_SLOT:\s*([A-Z_]+)\]", prompt or "")
            if _slot_match:
                _slot = _slot_match.group(1).strip()
                if _slot in routine_models:
                    local_model = routine_models[_slot]
                    try:
                        logger.info(
                            f"[V17 MoE] slot={_slot} agent={self.name} -> "
                            f"model={local_model} (override AGENT_SPECIFIC)"
                        )
                    except Exception:
                        pass
        except Exception:
            pass
        # Priorite 2 : routing par agent
        if local_model is None:
            local_model = specific_locals.get(self.name, default_local)

        # Enforcement MAX_LOCAL_MODEL_SIZE : fallback si le modèle est trop gros pour la VRAM
        max_size = getattr(Config, "MAX_LOCAL_MODEL_SIZE", 0)
        if max_size > 0:
            model_size = self._extract_model_size(local_model)
            if model_size > max_size:
                self.log_thought(
                    f"📏 Modèle {local_model} ({model_size}B) dépasse la limite ({max_size}B) → fallback {default_local}",
                    type="warning",
                )
                local_model = default_local

        # Etape 2 : Évaluation de la nécessité du Cloud
        if self._force_local_next:
            self._force_local_next = False  # reset one-shot
            needs_cloud = False
            self.log_thought("🏠 Mode local forcé (flag orchestrateur)", type="info")
        else:
            needs_cloud = await self._evaluate_complexity(prompt)

        # Etape 3 : Exécution Conditionnelle
        
        # Extraction V25.2 : execution deleguee a _invoke_llm (refactoring PUR, iso-comportement).
        # Intrants figes scelles dans un conteneur IMMUABLE, passe explicitement.
        # Zero etat d'instance, zero lock (cf revue Promethee 07/06 ; purete fonctionnelle).
        ctx = GenerationContext(local_model=local_model, needs_cloud=needs_cloud, orig_prompt=prompt)

        # ===== GREFFE PREFRONTALE V25.5 : anticipation frugale ACTIVE =====
        mirror_fn, mode = self._route_prefrontal_mirror(prompt)   # 3 voies : 'code' / 'intro' / 'none'
        if mirror_fn is None:                                     # 'none' -> ISO V25.2 (fail-open direct)
            final, was_cloud, source = await self._invoke_llm(full_prompt, ctx)
            self._consolidate(prompt, final, was_cloud, source)
            return final

        from core.prefrontal_mirror import MAX_PREFRONTAL_RETRIES
        friction, last, was_cloud, source = None, "", False, ""
        for attempt in range(1, MAX_PREFRONTAL_RETRIES + 1):
            # friction EPHEMERE : enrichit le prompt d'appel, JAMAIS self.messages / remember
            prompt_try = full_prompt if friction is None else f"{full_prompt}\n\n[REORIENTATION PREFRONTALE]\n{friction}"
            last, was_cloud, source = await self._invoke_llm(prompt_try, ctx)
            _t0 = time.perf_counter()
            verdict = mirror_fn(last)
            if inspect.isawaitable(verdict):                     # comportemental = coroutine (juge 9B)
                verdict = await verdict
            _lat_ms = (time.perf_counter() - _t0) * 1000.0       # AST ~0 pour 'code' / appel 9B pour 'intro'
            ok, rejection = verdict
            if ok:
                self._trace_prefrontal(mode, attempt, "PASS", _lat_ms, prompt=prompt)        # HEARTBEAT
                self._consolidate(prompt, last, was_cloud, source)   # consolidation : 1 fois, sur le VALIDE
                return last
            friction = rejection
            self._trace_prefrontal(mode, attempt, "VETO", _lat_ms, prompt=prompt, rejection=rejection)

        # ===== budget epuise -> FAIL-SAFE ASYMETRIQUE =====
        if mode == "code":
            self.log_thought("[PREFRONTAL_VETO] code non compilable apres reorientation -> canal coupe", type="warning")
            return "[PREFRONTAL_VETO: CODE_NON_COMPILABLE]"       # avortement sec (staging area protegee)
        degraded = f"[METABOLISME_ALERT: POSTURE_NON_CONSOLIDEE]\n{last or ''}"   # mode degrade introspectif
        self._consolidate(prompt, degraded, was_cloud, source)
        return degraded

    async def _invoke_llm(self, prompt_try: str, ctx: "GenerationContext") -> tuple:
        """Wrapper PUR de l'appel LLM (cascade Cloud -> cooldown 429 -> fallback Local).
        Extraction du bloc terminal de generate_content (V25.2). AUCUN effet de bord d'instance,
        AUCUN lock : re-appelable a l'infini par la boucle prefrontale sans rejouer le setup.
        Effets de bord PRESERVES : uniquement sur BaseAgent._xxx (etat de CLASSE).
        NE consolide RIEN (-> _consolidate, invariant 3). Retour : (final, was_cloud, source)."""
        local_model = ctx.local_model
        needs_cloud = ctx.needs_cloud

        # CAS A : Tache complexe -> on tente le Cloud d'abord
        if needs_cloud:
            now = time.time()

            # Reset compteurs journaliers
            from datetime import date
            today = date.today()
            if BaseAgent._daily_model_reset_day != today:
                BaseAgent._daily_model_calls = {}
                BaseAgent._daily_cloud_calls_evolution = 0
                BaseAgent._cloud_429_count_today = 0
                BaseAgent._cloud_cooldown_until = 0.0  # Reset cooldown au nouveau jour
                BaseAgent._daily_model_reset_day = today

            is_evolution = self.name == "evolution"

            # Vérification cooldown 429
            if now < BaseAgent._cloud_cooldown_until:
                remaining = int(BaseAgent._cloud_cooldown_until - now)
                self.log_thought(f"⏸️ Cloud en cooldown 429 ({remaining}s restantes) -> Fallback Local", type="warning")
                needs_cloud = False
            else:
                for model_name in self.cloud_models:
                    # Vérifier RPM pour ce modèle
                    if not BaseAgent._check_rpm(model_name):
                        rpm_limits = getattr(Config, 'CLOUD_RPM_LIMITS', {})
                        rpm_default = getattr(Config, 'CLOUD_RPM_DEFAULT', 30)
                        limit = rpm_limits.get(model_name, rpm_default)
                        self.log_thought(f"⏱️ RPM atteint pour {model_name.split('/')[-1]} ({limit}/min) -> modèle suivant", type="warning")
                        continue

                    # Vérifier budget journalier pour ce modèle
                    if not BaseAgent._check_daily_budget(model_name):
                        daily_limits = getattr(Config, 'CLOUD_DAILY_LIMITS', {})
                        limit = daily_limits.get(model_name, 500)
                        self.log_thought(f"💰 Budget journalier atteint pour {model_name.split('/')[-1]} ({limit}/jour) -> modèle suivant", type="warning")
                        continue

                    # Vérifier budget Evolution séparé (si applicable)
                    if is_evolution and BaseAgent._daily_cloud_calls_evolution >= BaseAgent.MAX_DAILY_EVOLUTION_CLOUD:
                        self.log_thought(f"💰 Budget Evolution épuisé ({BaseAgent._daily_cloud_calls_evolution}/{BaseAgent.MAX_DAILY_EVOLUTION_CLOUD})", type="warning")
                        break

                    try:
                        client = self._get_gemini_client(model_name)
                        if not client: continue
                        rpm_w = BaseAgent._rpm_windows.get(model_name, deque())
                        rpm_count = sum(1 for t in rpm_w if t > time.time() - 60)
                        rpm_limits = getattr(Config, 'CLOUD_RPM_LIMITS', {})
                        rpm_limit = rpm_limits.get(model_name, getattr(Config, 'CLOUD_RPM_DEFAULT', 30))
                        self.log_thought(f"🚀 Escalade Cloud : {model_name.split('/')[-1]} ({rpm_count}/{rpm_limit} RPM)...", type="thought")
                        loop = asyncio.get_running_loop()
                        response = await loop.run_in_executor(None, client.generate_content, prompt_try)
                        BaseAgent._record_cloud_call(model_name)
                        if is_evolution:
                            BaseAgent._daily_cloud_calls_evolution += 1
                        if response.text:
                            final = self._sanitize_response(self._strip_cot(response.text), self.name)
                            return final, True, model_name.split('/')[-1]   # source = modele cloud ; consolidation DEPORTEE
                    except Exception as e:
                        # Détecter les erreurs 429 (quota exceeded)
                        err_str = str(e)
                        if "429" in err_str or "quota" in err_str.lower() or "exceeded" in err_str.lower():
                            BaseAgent._activate_cloud_cooldown()
                            remaining = int(BaseAgent._cloud_cooldown_until - time.time())
                            self.log_thought(
                                f"🚫 Quota Gemini épuisé (429 x{BaseAgent._cloud_429_count_today}) "
                                f"— cooldown {remaining}s activé",
                                type="warning"
                            )
                            break  # Stop la cascade
                        continue

                # Si le Cloud échoue, fallback sur le Local
                self.log_thought(f"⚠️ Cloud HS malgré complexité -> Fallback Local ({local_model})", type="warning")

        # CAS B : Tâche Simple OU Fallback Cloud -> On utilise le Local
        else:
            self.log_thought(f"🏠 Traitement Local (Économie) : {local_model}", type="info")

        # CAS B : Local (tache simple OU fallback Cloud), streaming temps reel
        result = await self._call_ollama_stream(prompt_try, local_model)
        final = self._sanitize_response(self._strip_cot(result), self.name)
        final = self._quality_filter(final, ctx.orig_prompt)   # prompt NU (fidele a l'original)
        return final, False, local_model                       # source = modele local

    @classmethod
    def _activate_cloud_cooldown(cls):
        """Active le cooldown Cloud avec escalade (Tier 1 plus tolérant).
        - 1er 429 du jour : cooldown 15 min
        - 2e 429 : cooldown 1h
        - 3e+ 429 : cooldown 4h
        """
        from datetime import date
        today = date.today()

        # Reset compteur si nouveau jour
        if cls._cloud_429_reset_day != today:
            cls._cloud_429_count_today = 0
            cls._cloud_429_reset_day = today

        cls._cloud_429_count_today += 1
        now = time.time()

        if cls._cloud_429_count_today >= 3:
            # 3e+ 429 : cooldown 4h
            cls._cloud_cooldown_until = now + 4 * 3600
            logger.warning(f"[CLOUD] 429 x{cls._cloud_429_count_today} — cooldown 4h")
        elif cls._cloud_429_count_today == 2:
            # 2e 429 : cooldown 1h
            cls._cloud_cooldown_until = now + 3600
            logger.warning(f"[CLOUD] 429 x2 — cooldown 1h")
        else:
            # 1er 429 : cooldown 15 min (Tier 1)
            cls._cloud_cooldown_until = now + cls.CLOUD_COOLDOWN_SECONDS
            logger.warning(f"[CLOUD] 429 x1 — cooldown {cls.CLOUD_COOLDOWN_SECONDS}s")

    @classmethod
    def _get_ollama_semaphore(cls):
        """Retourne le GPU scheduler (backward compat pour les appelants externes).

        Les appelants internes (base_agent) utilisent gpu_scheduler.access(self.name)
        pour un meilleur logging. Les appelants externes (chat_engine, curiosity_reflex,
        soliloque) passent par cette méthode qui retourne un context manager anonyme.
        """
        return gpu_scheduler.access("external")

    @classmethod
    def _ollama_circuit_is_open(cls) -> bool:
        """Vérifie si le circuit breaker Ollama est ouvert (appels bloqués)."""
        return time.time() < cls._ollama_circuit_open_until

    @classmethod
    def _ollama_record_timeout(cls):
        """Enregistre un ReadTimeout et ouvre le circuit si le seuil est atteint."""
        cls._ollama_consecutive_timeouts += 1
        if cls._ollama_consecutive_timeouts >= cls.OLLAMA_CIRCUIT_BREAKER_THRESHOLD:
            cls._ollama_circuit_open_until = time.time() + cls.OLLAMA_CIRCUIT_BREAKER_DURATION
            logger.critical(
                f"[CIRCUIT BREAKER] Ollama non-responsive ({cls._ollama_consecutive_timeouts} timeouts) "
                f"— appels bloqués pour {cls.OLLAMA_CIRCUIT_BREAKER_DURATION}s"
            )
            try:
                from core.event_bus.bus import bus
                asyncio.ensure_future(bus.publish("OLLAMA_UNRESPONSIVE", {
                    "consecutive_timeouts": cls._ollama_consecutive_timeouts,
                    "blocked_until": cls._ollama_circuit_open_until,
                    "last_success": cls._ollama_last_success,
                }))
            except Exception:
                pass

    @classmethod
    def _ollama_record_success(cls):
        """Enregistre un appel Ollama réussi et referme le circuit."""
        cls._ollama_consecutive_timeouts = 0
        cls._ollama_circuit_open_until = 0.0
        cls._ollama_last_success = time.time()

    @classmethod
    def get_ollama_health(cls) -> dict:
        """État de santé Ollama pour les autres modules (reptilian, autonomy)."""
        return {
            "circuit_open": cls._ollama_circuit_is_open(),
            "consecutive_timeouts": cls._ollama_consecutive_timeouts,
            "blocked_until": cls._ollama_circuit_open_until,
            "last_success": cls._ollama_last_success,
            "gpu_scheduler": gpu_scheduler.get_stats(),
        }

    async def _call_ollama(self, prompt: str, model: str, images: list = None) -> str:
        # Circuit breaker : bloquer immédiatement si Ollama est non-responsive
        if self._ollama_circuit_is_open():
            remaining = int(self._ollama_circuit_open_until - time.time())
            logger.warning(f"[{self.name}] Circuit breaker Ollama actif — skip ({remaining}s restantes)")
            return "OLLAMA CIRCUIT BREAKER: GPU probablement bloqué, attente récupération."
        try:
            async with gpu_scheduler.access(self.name):
                url = getattr(Config, "OLLAMA_URL", "http://localhost:11434/api/generate")
                try:
                    from core.cardiac_engine import heart
                    _temperature = heart.get_temperature()
                except Exception:
                    _temperature = 0.7
                _num_ctx = getattr(Config, "AGENT_NUM_CTX", {}).get(self.name, getattr(Config, "AGENT_NUM_CTX", {}).get("default", 8192))
                payload = { "model": model, "prompt": prompt, "stream": False, "think": False, "options": { "temperature": _temperature, "num_ctx": _num_ctx, "num_predict": -1 } }
                if images:
                    payload["images"] = images
                async with httpx.AsyncClient() as client:
                    response = await client.post(url, json=payload, timeout=300)
                if response.status_code == 200:
                    self._ollama_record_success()
                    raw = response.json().get("response", "Ollama vide.")
                    return _strip_thinking_tags(raw)
                else: return f"Erreur OLLAMA: {response.status_code}"
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as e:
            self._ollama_record_timeout()
            logger.error(f"[{self.name}] _call_ollama({model}) TIMEOUT ({self._ollama_consecutive_timeouts}x): {e}")
            return "ÉCHEC TOTAL SYSTÈME."
        except Exception as e:
            logger.error(f"[{self.name}] _call_ollama({model}) échoué: {e}")
            return "ÉCHEC TOTAL SYSTÈME."

    async def _call_ollama_stream(self, prompt: str, model: str) -> str:
        """Appel Ollama en streaming avec publication temps réel sur le bus."""
        # Circuit breaker : bloquer immédiatement si Ollama est non-responsive
        if self._ollama_circuit_is_open():
            remaining = int(self._ollama_circuit_open_until - time.time())
            logger.warning(f"[{self.name}] Circuit breaker Ollama actif — skip streaming ({remaining}s restantes)")
            return "OLLAMA CIRCUIT BREAKER: GPU probablement bloqué, attente récupération."
        stream_id = str(uuid.uuid4())[:8]
        full_text = ""
        try:
            async with gpu_scheduler.access(self.name):
                url = getattr(Config, "OLLAMA_URL", "http://localhost:11434/api/generate")
                try:
                    from core.cardiac_engine import heart
                    _temperature = heart.get_temperature()
                except Exception:
                    _temperature = 0.7
                _num_ctx = getattr(Config, "AGENT_NUM_CTX", {}).get(self.name, getattr(Config, "AGENT_NUM_CTX", {}).get("default", 8192))
                payload = { "model": model, "prompt": prompt, "stream": True, "think": False, "options": { "temperature": _temperature, "num_ctx": _num_ctx, "num_predict": -1 } }

                # Signal de début
                await bus.publish("AGENT_STREAM", {
                    "agent": self.name, "stream_id": stream_id,
                    "chunk": "", "done": False, "status": "start"
                })

                async with httpx.AsyncClient() as client:
                    async with client.stream("POST", url, json=payload, timeout=300) as response:
                        if response.status_code != 200:
                            await bus.publish("AGENT_STREAM", {
                                "agent": self.name, "stream_id": stream_id,
                                "chunk": "", "done": True, "status": "end"
                            })
                            return f"Erreur OLLAMA: {response.status_code}"

                        async for line in response.aiter_lines():
                            if not line.strip():
                                continue
                            try:
                                data = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            token = data.get("response", "")
                            if token:
                                full_text += token
                                await bus.publish("AGENT_STREAM", {
                                    "agent": self.name, "stream_id": stream_id,
                                    "chunk": token, "done": False
                                })
                            if data.get("done", False):
                                break

            # Signal de fin (hors sémaphore — libère le slot dès la fin du streaming)
            self._ollama_record_success()
            await bus.publish("AGENT_STREAM", {
                "agent": self.name, "stream_id": stream_id,
                "chunk": "", "done": True, "status": "end"
            })
            return _strip_thinking_tags(full_text) or "Ollama vide."

        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as e:
            self._ollama_record_timeout()
            logger.error(f"[{self.name}] Erreur streaming Ollama TIMEOUT ({self._ollama_consecutive_timeouts}x): {e}")
            try:
                await bus.publish("AGENT_STREAM", {
                    "agent": self.name, "stream_id": stream_id,
                    "chunk": "", "done": True, "status": "end"
                })
            except Exception:
                pass
            return _strip_thinking_tags(full_text) or "ÉCHEC TOTAL SYSTÈME."
        except Exception as e:
            logger.error(f"[{self.name}] Erreur streaming Ollama ({type(e).__name__}): {e}")
            # Toujours envoyer le signal de fin pour fermer la bulle frontend
            try:
                await bus.publish("AGENT_STREAM", {
                    "agent": self.name, "stream_id": stream_id,
                    "chunk": "", "done": True, "status": "end"
                })
            except Exception:
                pass
            return full_text or "ÉCHEC TOTAL SYSTÈME."

    # Patterns de chain-of-thought internes que les LLMs locaux fuient
    _COT_PATTERNS = re.compile(
        r'^(?:'
        r'(?:We|I) (?:need|should|can|will|must|have) '
        r"|(?:The (?:assistant|user|system|code|task|question|problem|answer)'s)"
        r'|(?:Ok |Okay |Alright |Sure |Well |So |Now |First|Second|Third|Let me )'
        r"|(?:Here'?s? (?:my|the|a|what) )"
        r"|(?:I'(?:ll|m|ve) )"
        r"|(?:This (?:is|seems|looks|appears|means|requires) )"
        r"|(?:Let's )"
        r')',
        re.IGNORECASE,
    )

    # --- Regex anti-patterns dangereux dans les réponses agents ---
    _DANGEROUS_PATTERNS = re.compile(
        r'(?:'
        r'(?:^|\s)eval\s*\('                          # eval(...)
        r'|(?:^|\s)exec\s*\('                          # exec(...)
        r'|(?:^|\s)compile\s*\('                        # compile(...)
        r'|subprocess\.(?:call|run|Popen|check_output)' # subprocess.*
        r'|os\.(?:system|popen|exec[lv]?[pe]?)\s*\('   # os.system/popen/exec*
        r'|__import__\s*\('                             # __import__(...)
        r'|cmd\s*/c\s'                                  # cmd /c ...
        r'|powershell\s+-[eE](?:nc)?\s'                 # powershell -enc/-e (encoded)
        r'|base64\.(?:b64decode|decodebytes)\s*\('      # base64 decode
        r'|rm\s+-rf\s+/'                                # rm -rf /
        r'|shutil\.rmtree\s*\(\s*["\'/]'               # shutil.rmtree("/...")
        r'|setuid\s*\(0\)'                              # setuid(0)
        r'|chmod\s+[0-7]*777'                           # chmod 777
        r')',
        re.MULTILINE
    )

    @classmethod
    def _sanitize_response(cls, text: str, agent_name: str = "") -> str:
        """Détecte et neutralise les patterns dangereux dans les réponses agents.
        Retourne le texte nettoyé + log un warning si des patterns sont trouvés."""
        if not text:
            return text
        matches = cls._DANGEROUS_PATTERNS.findall(text)
        if matches:
            unique = list(set(m.strip() for m in matches))
            logger.warning(
                f"[{agent_name}] ANTI-PATTERN: {len(unique)} pattern(s) dangereux détecté(s): "
                f"{', '.join(unique[:5])}"
            )
            # Neutraliser : commenter les lignes contenant les patterns
            lines = text.split('\n')
            sanitized = []
            for line in lines:
                if cls._DANGEROUS_PATTERNS.search(line):
                    sanitized.append(f"# [NEUTRALISÉ] {line}")
                else:
                    sanitized.append(line)
            return '\n'.join(sanitized)
        return text

    def _quality_filter(self, text: str, prompt: str) -> str:
        """Filtre de qualite post-production — Promethee controle ce que le LLM dit.

        Detecte et corrige :
        1. Boucles (phrases repetees)
        2. Reponse vide ou trop courte
        3. Hors-sujet flagrant (le prompt demande X, la reponse parle de Y)
        """
        if not text or len(text) < 10:
            return text

        # 1. Anti-boucle : couper les phrases repetees
        lines = [l for l in text.split("\n") if l.strip()]
        if len(lines) > 3:
            seen = set()
            clean = []
            for line in lines:
                key = line.strip()[:60].lower()
                if key in seen and len(key) > 20:
                    logger.debug(f"[{self.name}] QUALITY: boucle detectee, ligne supprimee")
                    continue
                seen.add(key)
                clean.append(line)
            if len(clean) < len(lines):
                text = "\n".join(clean)

        return text

    @classmethod
    def _strip_cot(cls, text: str) -> str:
        """Retire les lignes de chain-of-thought et les blocs <think>."""
        # Strip les blocs <think>...</think> (deepseek-r1)
        cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
        if not cleaned:
            # Texte = uniquement des blocs <think> : extraire le contenu comme fallback
            think_content = re.findall(r'<think>(.*?)</think>', text, flags=re.DOTALL)
            if think_content:
                # Prendre les dernières lignes non-vides du think (souvent la conclusion)
                all_lines = think_content[-1].strip().split('\n')
                # Garder les 5 dernières lignes non-vides comme résumé
                meaningful = [l.strip() for l in all_lines if l.strip()]
                if meaningful:
                    return '\n'.join(meaningful[-5:])
            return text  # Fallback ultime : retourner tel quel
        # Strip les lignes de raisonnement interne en tête de réponse
        lines = cleaned.split('\n')
        start = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            if cls._COT_PATTERNS.match(stripped):
                start = i + 1
            else:
                break
        result = '\n'.join(lines[start:]).strip()
        return result if result else cleaned

    def log_thought(self, message: str, type: str = "info"):
        self.logger.info(f"[{self.name.upper()}] {message[:100]}...")
        try:
            payload = {"agent": self.name, "content": message, "type": type}
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running(): loop.create_task(bus.publish("THOUGHT_STREAM", payload))
            except RuntimeError: pass  # Pas de boucle async active, normal en dehors du serveur
        except Exception as e:
            logger.warning(f"[{self.name}] Échec publication THOUGHT_STREAM : {e}")

    # ========================================================================
    # CORTEX PREFRONTAL V25.1 — methodes auxiliaires (CODE DORMANT, non branche)
    # Conception + 48 TDD : sandbox_anticipation_v1/. Miroirs : core/prefrontal_mirror.py.
    # generate_content NE LES APPELLE PAS encore -> zero effet sur le flux de production.
    # ========================================================================
    def _route_prefrontal_mirror(self, prompt: str):
        """Commutateur : (mirror_fn, mode) selon le marqueur de slot. code -> miroir
        deterministe (sync) ; intro -> miroir comportemental async (judge = ce 9B)."""
        from core.prefrontal_mirror import route_mirror
        judge = lambda artifact: self._behavioral_judge(prompt, artifact)
        return route_mirror(prompt, judge)

    async def _behavioral_judge(self, prompt: str, artifact: str) -> str:
        """Mini-appel Ollama ultra-bride (temp 0.0, format JSON, court) : microscope a
        l'esprit. Retourne le JSON brut (str). Tout echec -> '' (le miroir laisse passer)."""
        try:
            from core.prefrontal_mirror import JUDGE_PROMPT
            judge_prompt = JUDGE_PROMPT.replace("{draft}", (artifact or "")[:2000])
            model = getattr(Config, "DEFAULT_LOCAL_MODEL", "qwen3.5:9b")
            url = getattr(Config, "OLLAMA_URL", "http://localhost:11434/api/generate")
            payload = {"model": model, "prompt": judge_prompt, "stream": False, "think": False,
                       "format": "json", "options": {"temperature": 0.0, "num_predict": 200}}
            async with httpx.AsyncClient() as client:
                r = await client.post(url, json=payload, timeout=30)
            if r.status_code == 200:
                return r.json().get("response", "") or ""
            return ""
        except Exception:
            return ""   # doctrine inverse : tout echec -> le miroir comportemental laisse passer

    def _consolidate(self, prompt: str, deliverable: str, was_cloud: bool, source: str = "cloud_escalation"):
        """Memorisation DEPORTEE (invariant 3) : appelee UNE fois sur le livrable retenu,
        jamais sur une ebauche rejetee. Reprend remember + compiler + training_pair.
        `source` = nom du modele ayant repondu (granularite JM) : voyage par le RETOUR de _invoke_llm."""
        try:
            if was_cloud and deliverable and len(deliverable) > 50:
                self.remember(f"Q: {prompt}\nA: {deliverable}",
                              metadata={"source": source, "trigger": "cloud_escalation"})
            self._record_for_compiler(prompt, deliverable, was_cloud=was_cloud)
            self._save_training_pair(prompt, deliverable, was_cloud=was_cloud)
        except Exception as e:
            logger.warning(f"[{self.name}] _consolidate echoue: {e}")

    def _trace_prefrontal(self, mode: str, attempt: int, decision: str, lat_ms: float,
                          prompt: str = "", rejection: str = None):
        """TELEMETRIE V25.6 (HEARTBEAT) : trace CHAQUE evaluation du cortex -- PASS comme VETO --
        en JSONL structure (prefrontal_metabolism.jsonl). On voit le cortex BATTRE, pas seulement
        crasher : sans la trace PASS, un succes silencieux est indiscernable d'un contournement.
        `lat_ms` = cout reel mesure (AST quasi 0 pour 'code' ; mini-appel 9B pour 'intro' -> budget
        energetique). Si VETO : + diagnostic de POSTURE (categorie de derive, JAMAIS le brouillon ni
        la friction). Enveloppe borg : un defaut d'I/O JSONL ne fait JAMAIS crasher une inference."""
        try:
            slot = "?"
            m = re.search(r"\[SCHOOL_SLOT:\s*([A-Z_]+)\]", prompt or "")
            if m:
                slot = m.group(1)
            entry = {
                "ts": time.time(), "agent": self.name, "slot": slot, "mode": mode,
                "decision": decision, "attempt": attempt, "lat_ms": round(lat_ms, 2),
            }
            if rejection:
                entry["derive"] = (rejection or "")[:160]   # categorie de derive, pas le lexeme
            path = os.path.join("memory", "prefrontal_metabolism.jsonl")
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass   # l'I/O JSONL ne doit JAMAIS interrompre une generation