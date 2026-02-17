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
from typing import Dict, Any, List

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

class BaseAgent:
    """
    Classe Mère V22.0 - Full Architecture + Budget Cloud + Cooldown 429
    - Stratégie : Local First & Cloud Escalation (Stricte)
    - Mémoire : RAG & Anti-Spam + Decay Temporel
    - Interface : Publication temps réel sur le Bus
    - Budget : Compteur d'appels Cloud partagé + cooldown 429 + quotas journaliers
    """
    # Compteur Cloud partagé entre toutes les instances
    _cloud_call_count = 0
    _cloud_call_reset_time = time.time()
    MAX_CLOUD_CALLS_PER_HOUR = 100

    # Sémaphore global : limite les appels Ollama concurrents (évite saturation RAM/CPU)
    _ollama_semaphore = None  # Initialisé lazily (nécessite une event loop)
    MAX_CONCURRENT_OLLAMA = 2

    # Cooldown après erreur 429 (quota exceeded)
    _cloud_cooldown_until = 0.0  # timestamp jusqu'auquel le Cloud est désactivé
    CLOUD_COOLDOWN_SECONDS = 3600  # 1 heure de cooldown après un 429

    # Quotas journaliers Gemini Free Tier (RPD = requests per day)
    _daily_cloud_calls = 0
    _daily_cloud_calls_evolution = 0  # Compteur séparé pour Evolution
    _daily_cloud_reset_day = None
    MAX_DAILY_CLOUD_CALLS = 50        # Budget total conservateur
    MAX_DAILY_EVOLUTION_CALLS = 15    # Réservé pour Evolution (R&D)
    # Les autres agents partagent MAX_DAILY_CLOUD_CALLS - MAX_DAILY_EVOLUTION_CALLS = 35

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

            doc_id = str(uuid.uuid4())
            meta = metadata or {}
            meta["agent"] = self.name
            meta["timestamp"] = str(time.time())

            self.log_thought(f"💾 Sauvegarde en mémoire ({collection})...", type="info")
            self.memory_manager.add_documents([text], [meta], [doc_id], collection)
        except Exception as e:
            self.log_thought(f"Erreur Sauvegarde: {e}", type="error")

    def recall(self, query: str, limit: int = 2, collection="collective_wisdom") -> str:
        if not self.has_memory: return ""
        try:
            import math
            fetch_count = limit * 3
            res = self.memory_manager.query_with_metadata(
                [query], n_results=fetch_count, collection_name=collection
            )
            if not (res and res['documents'] and res['documents'][0]):
                return ""

            now = time.time()
            scored = []
            for doc, meta, dist in zip(
                res['documents'][0], res['metadatas'][0], res['distances'][0]
            ):
                similarity = max(0.0, 1.0 - dist)
                try:
                    age_days = (now - float(meta.get("timestamp", 0))) / 86400
                except (ValueError, TypeError):
                    age_days = self.MEMORY_HALF_LIFE_DAYS
                decay = math.exp(-age_days * 0.693 / self.MEMORY_HALF_LIFE_DAYS)
                scored.append((doc, similarity * decay))

            scored.sort(key=lambda x: x[1], reverse=True)
            # Filtre qualité : exclure les résultats sous le seuil
            scored = [(doc, s) for doc, s in scored if s >= self._MIN_RECALL_SCORE]
            return "\n".join(doc for doc, _ in scored[:limit])
        except Exception as e:
            logger.warning(f"[{self.name}] Échec recall mémoire ({collection}) : {e}")
        return ""

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
        mission = task_payload.get("mission", "Inconnue")
        self.log_thought(f"Reçoit la mission : {mission[:50]}...", type="thought")
        
        # 1. Génération de la réponse (via Cloud ou Local selon la complexité)
        response_text = await self.generate_content(f"Tu es {self.role}. Mission: {mission}")
        
        # 2. Sanitisation anti-patterns dangereux
        response_text = self._sanitize_response(response_text, self.name)

        # 3. 🚨 CORRECTIF UI : On publie la réponse sur le Bus pour l'interface Web 🚨
        try:
            # On formate le message pour que le frontend le comprenne
            ui_payload = {
                "type": "AGENT_RESPONSE",  # Le mot-clé que le JS écoute
                "agent": self.name,
                "content": response_text,
                "timestamp": str(time.time())
            }
            # On l'envoie dans le tuyau
            await bus.publish("AGENT_RESPONSE", ui_payload)
            self.log_thought("✅ Réponse envoyée à l'interface.", type="success")
        except Exception as e:
            self.log_thought(f"❌ Erreur d'affichage UI : {e}", type="error")

        return {"status": "success", "result": response_text}

    # Marqueurs de missions internes → toujours local (économie Cloud)
    _LOCAL_FORCE_MARKERS = (
        "PROTOCOLE_AUTONOMIE",
        "[MODE VEILLE]",
        "YOUTUBE_VEILLE",
        "DROPZONE_ANALYSIS",
        "PROTOCOLE_AUTONOMIE_GRIMOIRE",
        "CONSEIL multi-agents",
        "EVOLUTION_PIPELINE",
        "MEMORY_CLEANUP",
        "COUNCIL_RESEARCH",
    )

    async def _evaluate_complexity(self, prompt: str) -> bool:
        """
        Calibrage V2: "La Pince". On force le local pour tout ce qui est culture G.
        """
        # Court-circuit : missions internes → toujours local (économie Cloud)
        for marker in self._LOCAL_FORCE_MARKERS:
            if marker in prompt:
                self.log_thought("🏠 Mission interne → local forcé (économie Cloud)", type="info")
                return False

        try:
            # On utilise le modèle local pour juger
            eval_model = "gemma3:12b"
            
            # PROMPT RENDU BEAUCOUP PLUS STRICT
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
            
            return is_complex
        except Exception as e:
            logger.warning(f"[{self.name}] Échec évaluation complexité (fallback local) : {e}")
            return False

    async def generate_content(self, prompt: str) -> str:
        # Etape 1 : RAG (Toujours utile)
        context_memory = ""
        mem1 = self.recall(prompt, collection="collective_wisdom")
        if mem1:
            self.log_thought("🧠 Souvenirs trouvés !", type="info")
            context_memory = f"\n[SOUVENIRS]:\n{mem1}\n"

        full_prompt = (
            f"\n[SYSTEM: Nexus V20 (Local First) | AGENT: {self.name.upper()}]\n"
            f"[CONTRAINTE: Projet sur UN SEUL PC Windows + Ollama local. "
            f"Pas de Kubernetes/Docker/Kafka/microservices/blockchain.]\n"
            f"{context_memory}"
            f"{prompt}"
        )

        # Modèles Locaux
        specific_locals = getattr(Config, "AGENT_SPECIFIC_LOCAL_MODELS", {})
        default_local = "gemma3:12b"
        local_model = specific_locals.get(self.name, default_local)

        # Etape 2 : Évaluation de la nécessité du Cloud
        if self._force_local_next:
            self._force_local_next = False  # reset one-shot
            needs_cloud = False
            self.log_thought("🏠 Mode local forcé (flag orchestrateur)", type="info")
        else:
            needs_cloud = await self._evaluate_complexity(prompt)

        # Etape 3 : Exécution Conditionnelle
        
        # CAS A : Tâche Complexe -> On tente le Cloud d'abord
        if needs_cloud:
            now = time.time()

            # Reset compteur horaire
            if now - BaseAgent._cloud_call_reset_time > 3600:
                BaseAgent._cloud_call_count = 0
                BaseAgent._cloud_call_reset_time = now

            # Reset compteur journalier
            from datetime import date
            today = date.today()
            if BaseAgent._daily_cloud_reset_day != today:
                BaseAgent._daily_cloud_calls = 0
                BaseAgent._daily_cloud_calls_evolution = 0
                BaseAgent._daily_cloud_reset_day = today

            # Budget Cloud séparé : Evolution a son propre quota réservé
            is_evolution = self.name == "evolution"
            if is_evolution:
                daily_limit = BaseAgent.MAX_DAILY_EVOLUTION_CALLS
                daily_used = BaseAgent._daily_cloud_calls_evolution
            else:
                daily_limit = BaseAgent.MAX_DAILY_CLOUD_CALLS - BaseAgent.MAX_DAILY_EVOLUTION_CALLS
                daily_used = BaseAgent._daily_cloud_calls - BaseAgent._daily_cloud_calls_evolution

            # Vérification cooldown 429
            if now < BaseAgent._cloud_cooldown_until:
                remaining = int(BaseAgent._cloud_cooldown_until - now)
                self.log_thought(f"⏸️ Cloud en cooldown 429 ({remaining}s restantes) -> Fallback Local", type="warning")
                needs_cloud = False
            elif BaseAgent._cloud_call_count >= BaseAgent.MAX_CLOUD_CALLS_PER_HOUR:
                self.log_thought(f"💰 Budget Cloud horaire atteint ({BaseAgent.MAX_CLOUD_CALLS_PER_HOUR}/h) -> Fallback Local", type="warning")
                needs_cloud = False
            elif daily_used >= daily_limit:
                self.log_thought(f"💰 Budget Cloud {'Evolution' if is_evolution else 'général'} atteint ({daily_used}/{daily_limit}) -> Fallback Local", type="warning")
                needs_cloud = False
            else:
                cloud_response = None
                used_model = "Aucun"
                for model_name in self.cloud_models:
                    try:
                        client = self._get_gemini_client(model_name)
                        if not client: continue
                        self.log_thought(f"🚀 Escalade Cloud (Tâche Complexe) : {model_name.split('/')[-1]}...", type="thought")
                        loop = asyncio.get_running_loop()
                        response = await loop.run_in_executor(None, client.generate_content, full_prompt)
                        BaseAgent._cloud_call_count += 1
                        BaseAgent._daily_cloud_calls += 1
                        if is_evolution:
                            BaseAgent._daily_cloud_calls_evolution += 1
                        if response.text:
                            cloud_response = response.text
                            used_model = model_name.split('/')[-1]

                            # Si succès Cloud sur tâche complexe, on apprend
                            if len(cloud_response) > 50:
                                self.remember(f"Q: {prompt}\nA: {cloud_response}", metadata={"source": used_model, "trigger": "cloud_escalation"})
                            return self._strip_cot(cloud_response)
                    except Exception as e:
                        # Détecter les erreurs 429 (quota exceeded)
                        err_str = str(e)
                        if "429" in err_str or "quota" in err_str.lower() or "exceeded" in err_str.lower():
                            BaseAgent._cloud_cooldown_until = now + BaseAgent.CLOUD_COOLDOWN_SECONDS
                            self.log_thought(
                                f"🚫 Quota Gemini épuisé (429) — cooldown {BaseAgent.CLOUD_COOLDOWN_SECONDS}s activé",
                                type="warning"
                            )
                            break  # Stop la cascade, pas la peine d'essayer les autres modèles
                        continue

                # Si le Cloud échoue, fallback sur le Local
                self.log_thought(f"⚠️ Cloud HS malgré complexité -> Fallback Local ({local_model})", type="warning")

        # CAS B : Tâche Simple OU Fallback Cloud -> On utilise le Local
        else:
            self.log_thought(f"🏠 Traitement Local (Économie) : {local_model}", type="info")

        # Exécution Locale (avec streaming temps réel)
        result = await self._call_ollama_stream(full_prompt, local_model)
        return self._strip_cot(result)

    @classmethod
    def _get_ollama_semaphore(cls):
        """Initialisation lazy du sémaphore (nécessite une event loop active)."""
        if cls._ollama_semaphore is None:
            cls._ollama_semaphore = asyncio.Semaphore(cls.MAX_CONCURRENT_OLLAMA)
        return cls._ollama_semaphore

    async def _call_ollama(self, prompt: str, model: str) -> str:
        try:
            async with self._get_ollama_semaphore():
                url = getattr(Config, "OLLAMA_URL", "http://localhost:11434/api/generate")
                payload = { "model": model, "prompt": prompt, "stream": False, "options": { "temperature": 0.7, "num_ctx": 4096 } }
                async with httpx.AsyncClient() as client:
                    response = await client.post(url, json=payload, timeout=300)
                if response.status_code == 200: return response.json().get("response", "Ollama vide.")
                else: return f"Erreur OLLAMA: {response.status_code}"
        except Exception as e: return "ÉCHEC TOTAL SYSTÈME."

    async def _call_ollama_stream(self, prompt: str, model: str) -> str:
        """Appel Ollama en streaming avec publication temps réel sur le bus."""
        stream_id = str(uuid.uuid4())[:8]
        full_text = ""
        try:
            async with self._get_ollama_semaphore():
                url = getattr(Config, "OLLAMA_URL", "http://localhost:11434/api/generate")
                payload = { "model": model, "prompt": prompt, "stream": True, "options": { "temperature": 0.7, "num_ctx": 4096 } }

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
            await bus.publish("AGENT_STREAM", {
                "agent": self.name, "stream_id": stream_id,
                "chunk": "", "done": True, "status": "end"
            })
            return full_text or "Ollama vide."

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