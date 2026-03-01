import re
import os
import httpx
import json
import logging
import unicodedata
from typing import Optional

logger = logging.getLogger("router")

# Configuration minimale pour l'appel LLM autonome du routeur
try:
    from config import Config
except ImportError:
    class Config: 
        OLLAMA_URL = "http://localhost:11434/api/generate"
        AGENT_SPECIFIC_LOCAL_MODELS = {}

class RouterAgent:
    """
    RouterAgent V2.4 - Grimoire-First
    - Niveau 0 : Adressage Direct (Syntaxe 'Nom: Action') -> Passe tout (Grimoire compatible).
    - Niveau 0.5 : Consultation du Grimoire (agents éphémères spécialisés, scoring + normalisation Unicode).
    - Niveau 1 : Détection par Mots-clés (Réflexe instantané sur liste connue).
    - Niveau 2 : Analyse Sémantique LLM (Réflexion en cas d'ambiguïté).
    """

    # Cache de l'index Grimoire (chargé une seule fois)
    _grimoire_index_cache = None
    
    @staticmethod
    async def classify_intent(mission: str) -> str:
        m_low = mission.strip().lower()

        # --- NIVEAU 0 : BLIND TRUST (Adressage Direct) ---
        # Si l'utilisateur utilise la syntaxe "Agent: ...", on obéit aveuglément.
        # Cela permet d'appeler des agents éphémères (Grimoire) inconnus de la liste statique.
        if ':' in mission:
            # On prend tout ce qu'il y a avant les deux points
            potential_agent = mission.split(':')[0].strip()

            # Sécurité basique : Un nom d'agent ne doit pas contenir d'espace
            # Ex: "math_wizard" (OK) vs "Calcul le" (KO - ignoré)
            if ' ' not in potential_agent:
                return potential_agent.lower()

        # --- NIVEAU 0.5 : CONSULTATION DU GRIMOIRE (Spécialistes éphémères) ---
        # Les recettes Grimoire sont spécialisées et matchent avant les mots-clés génériques.
        grimoire_match = RouterAgent._check_grimoire_index(m_low)
        if grimoire_match:
            logger.info(f"📖 ROUTER: Grimoire match -> {grimoire_match.upper()}")
            return grimoire_match

        # --- NIVEAU 1 : SYSTÈME RÉFLEXE (Règles strictes sur Agents Connus) ---

        # 1. Priorité Mots-clés (Nom de l'agent en début de phrase sans :)
        agents = ["factory", "coder", "researcher", "architect", "strategist",
                  "writer", "infra", "security", "evolution", "formatter", "vision"]

        first_word = m_low.split(' ')[0].strip()
        if first_word in agents:
            return first_word

        # 2. Déduction Contextuelle (Mots-clés forts)
        if any(x in m_low for x in ["cpu", "ram", "gpu", "vram", "status", "santé", "check system"]): return "infra"
        if any(x in m_low for x in ["scan", "dropzone", "ingest", "archive", "lecture", "lire fichier", "recherche web", "veille"]): return "researcher"
        if any(x in m_low for x in ["reset", "nuke", "crée dossier", "supprime", "tree", "arborescence"]): return "factory"
        if any(x in m_low for x in ["code", "script", "fonction", "python", "bug", "dev", "class ", "def "]): return "coder"
        if any(x in m_low for x in ["évolue", "améliore", "optimise", "mutation", "upgrade"]): return "evolution"
        if any(x in m_low for x in ["plan", "structure", "audit", "valide", "architecture"]): return "architect"
        if any(x in m_low for x in ["secu", "faille", "attack", "protect"]): return "security"
        if any(x in m_low for x in ["rédige", "écris", "article", "tweet", "post", "seo"]): return "writer"
        if any(x in m_low for x in ["format", "clean", "nettoie", "indente", "syntaxe"]): return "formatter"
        if any(x in m_low for x in ["roadmap", "vision", "module planif", "planification"]): return "vision"
        if any(x in m_low for x in ["conseil", "débat", "council", "débattre"]): return "conseil"

        # --- NIVEAU 2 : AUTO-RÉFLEXION (Appel LLM Local) ---
        # Si aucune règle ne matche, on demande au LLM de trancher
        logger.info(f"🤔 ROUTER: Ambiguïté détectée sur '{mission[:30]}...'. Analyse Sémantique en cours...")
        return await RouterAgent._semantic_reflection(mission)

    @staticmethod
    def _normalize(text: str) -> str:
        """Retire les accents et met en minuscule pour comparaison insensible aux accents."""
        nfkd = unicodedata.normalize('NFKD', text)
        return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()

    @staticmethod
    def _load_grimoire_index():
        """Charge le cache de l'index Grimoire si nécessaire."""
        if RouterAgent._grimoire_index_cache is None:
            index_path = os.path.join(os.path.dirname(__file__), "grimoire", "grimoire_index.json")
            if not os.path.exists(index_path):
                return False
            with open(index_path, "r", encoding="utf-8") as f:
                RouterAgent._grimoire_index_cache = json.load(f)
        return True

    @staticmethod
    def _check_grimoire_index(mission_lower: str) -> Optional[str]:
        """Consulte l'index du Grimoire avec matching robuste (scoring + normalisation Unicode)."""
        try:
            if not RouterAgent._load_grimoire_index():
                return None

            mission_norm = RouterAgent._normalize(mission_lower)
            best_slug = None
            best_score = 0

            for entry in RouterAgent._grimoire_index_cache:
                for keyword in entry.get("keywords", []):
                    kw_norm = RouterAgent._normalize(keyword)
                    if len(kw_norm) < 3:
                        continue  # Trop court, risque de faux positif
                    if kw_norm in mission_norm:
                        score = len(kw_norm)  # Les mots-clés longs sont plus précis
                        if score > best_score:
                            best_score = score
                            best_slug = entry["slug"]

            return best_slug
        except Exception as e:
            logger.warning(f"⚠️ ROUTER: Erreur lecture index Grimoire : {e}")
        return None

    @staticmethod
    def invalidate_grimoire_cache():
        """Invalide le cache de l'index Grimoire (après ajout d'une recette)."""
        RouterAgent._grimoire_index_cache = None

    @staticmethod
    def _get_grimoire_slugs() -> list:
        """Retourne la liste des slugs des agents du Grimoire."""
        try:
            if RouterAgent._grimoire_index_cache is None:
                RouterAgent._check_grimoire_index("")  # Force le chargement
            if RouterAgent._grimoire_index_cache:
                return [entry["slug"] for entry in RouterAgent._grimoire_index_cache]
        except Exception:
            pass
        return []

    @staticmethod
    async def _semantic_reflection(mission: str) -> str:
        """Demande au modèle routeur léger (4B) de classer l'intention."""
        try:
            from config import Config
            model = getattr(Config, "ROUTER_MODEL", "qwen3:4b")

            # Ajout des agents Grimoire dans la liste
            grimoire_slugs = RouterAgent._get_grimoire_slugs()
            all_agents = ["coder", "researcher", "strategist", "writer", "architect", "infra", "security", "evolution", "factory", "formatter"] + grimoire_slugs

            prompt = (
                f"Classifie cette mission vers l'agent le plus approprié.\n"
                f"AGENTS : {', '.join(all_agents)}\n"
                f"MISSION : \"{mission[:200]}\"\n"
                f"Réponds UNIQUEMENT par le nom de l'agent. UN SEUL MOT."
            )

            payload = {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "num_predict": 800,
                    "num_ctx": 2048,
                }
            }

            url = getattr(Config, "OLLAMA_URL", "http://localhost:11434/api/generate")
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=15)

            if response.status_code == 200:
                data = response.json()
                # Priorité 1 : réponse directe (après le thinking)
                choice = data.get("response", "").strip().lower()
                # Priorité 2 : si vide, chercher dans le thinking (modèle a manqué de tokens)
                if not choice:
                    choice = data.get("thinking", "").strip().lower()
                # Extraire le dernier agent mentionné (conclusion du raisonnement)
                last_match = None
                for agent in all_agents:
                    if agent in choice:
                        last_match = agent
                if last_match:
                    logger.info(f"💡 ROUTER: {model} -> {last_match.upper()}")
                    return last_match

            logger.warning("⚠️ ROUTER: Echec réflexion IA, repli sur STRATEGIST.")
            return "strategist"

        except Exception as e:
            logger.error(f"❌ ROUTER ERROR: {e}")
            return "strategist"