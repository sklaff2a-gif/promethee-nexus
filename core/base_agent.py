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
    Classe Mère V21.0 - Full Architecture + Budget Cloud
    - Stratégie : Local First & Cloud Escalation (Stricte)
    - Mémoire : RAG & Anti-Spam + Decay Temporel
    - Interface : Publication temps réel sur le Bus
    - Budget : Compteur d'appels Cloud partagé entre agents
    """
    # Compteur Cloud partagé entre toutes les instances
    _cloud_call_count = 0
    _cloud_call_reset_time = time.time()
    MAX_CLOUD_CALLS_PER_HOUR = 100

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

    # --- MÉTHODES MÉMOIRE (PROTECTION ANTI-SPAM) ---

    def remember(self, text: str, metadata: Dict = None, collection="collective_wisdom"):
        if not self.has_memory: return
        try:
            # Check Anti-Doublon (Le Pare-Feu)
            existing = self.memory_manager.query_documents([text], n_results=1, collection_name=collection)
            if existing and existing['documents'] and existing['documents'][0]:
                existing_text = existing['documents'][0][0]
                # Si le texte est très similaire, on ignore
                if text.strip() in existing_text.strip() or existing_text.strip() in text.strip():
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
        
        # 2. 🚨 CORRECTIF UI : On publie la réponse sur le Bus pour l'interface Web 🚨
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

    async def _evaluate_complexity(self, prompt: str) -> bool:
        """
        Calibrage V2: "La Pince". On force le local pour tout ce qui est culture G.
        """
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
            f"{context_memory}"
            f"{prompt}"
        )

        # Modèles Locaux
        specific_locals = getattr(Config, "AGENT_SPECIFIC_LOCAL_MODELS", {})
        default_local = "gemma3:12b"
        local_model = specific_locals.get(self.name, default_local)

        # Etape 2 : Évaluation de la nécessité du Cloud
        needs_cloud = await self._evaluate_complexity(prompt)

        # Etape 3 : Exécution Conditionnelle
        
        # CAS A : Tâche Complexe -> On tente le Cloud d'abord
        if needs_cloud:
            # Vérification budget Cloud
            now = time.time()
            if now - BaseAgent._cloud_call_reset_time > 3600:
                BaseAgent._cloud_call_count = 0
                BaseAgent._cloud_call_reset_time = now

            if BaseAgent._cloud_call_count >= BaseAgent.MAX_CLOUD_CALLS_PER_HOUR:
                self.log_thought(f"💰 Budget Cloud atteint ({BaseAgent.MAX_CLOUD_CALLS_PER_HOUR}/h) -> Fallback Local", type="warning")
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
                        if response.text:
                            cloud_response = response.text
                            used_model = model_name.split('/')[-1]

                            # Si succès Cloud sur tâche complexe, on apprend
                            if len(cloud_response) > 50:
                                self.remember(f"Q: {prompt}\nA: {cloud_response}", metadata={"source": used_model, "trigger": "cloud_escalation"})
                            return cloud_response
                    except Exception:
                        continue

                # Si le Cloud échoue, fallback sur le Local
                self.log_thought(f"⚠️ Cloud HS malgré complexité -> Fallback Local ({local_model})", type="warning")

        # CAS B : Tâche Simple OU Fallback Cloud -> On utilise le Local
        else:
            self.log_thought(f"🏠 Traitement Local (Économie) : {local_model}", type="info")

        # Exécution Locale (avec streaming temps réel)
        return await self._call_ollama_stream(full_prompt, local_model)

    async def _call_ollama(self, prompt: str, model: str) -> str:
        try:
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

            # Signal de fin
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