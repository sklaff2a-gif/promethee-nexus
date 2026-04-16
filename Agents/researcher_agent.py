import asyncio
import json
import logging
from typing import Dict, Any, List
from core.base_agent import BaseAgent
from core.capabilities.web_surfer import WebSurfer
from core.prompt_templates import AUTONOMY_GUARDRAIL

logger = logging.getLogger("researcher")

class DivineResearcher(BaseAgent):
    """
    DivineResearcher V14.0 (Hybrid Surfer Edition)
    - Utilise WebSurfer pour la recherche (Google/DDG)
    - Utilise KnowledgeIngestor pour la lecture locale
    """
    def __init__(self):
        super().__init__(
            name="researcher",
            role="Analyste de Données & Veilleur Stratégique",
            description="Scanne le web via Google/DDG et les documents locaux pour extraire du savoir."
        )
        self.surfer = WebSurfer()

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        mission = task_payload.get("mission", "")
        context = task_payload.get("context", "")
        self.log_thought(f"🔍 Mission reçue : {mission[:50]}...", type="thought")

        # --- 1. MODE INGESTION LOCALE (Dropzone) ---
        # Guard : si on est appelé PAR le pipeline (context DROPZONE_ANALYSIS),
        # ne pas re-déclencher le pipeline (sinon boucle infinie).
        # On ne cherche les mots-clés que dans les 200 premiers chars (pas le contenu des fichiers).
        if not context.startswith("DROPZONE_ANALYSIS"):
            mission_header = mission[:200].lower()
            if "dropzone" in mission_header:
                return await self._run_ingestion_routine()

        # --- 2. MODE RECHERCHE WEB (Par défaut ou explicite) ---
        # Si la mission contient des mots de recherche OU si aucune autre action n'est détectée
        
        # Nettoyage de la requête — Fix D (Trio Adversarial 2026-04-16)
        # Le prompt école contient des sections (CONTRAINTE SECONDAIRE, REGLES
        # ABSOLUES, etc.) qui polluent le moteur de recherche si elles sont
        # envoyees comme query. On extrait uniquement le sujet principal.
        query = mission.replace("Researcher:", "").replace("Scanne le web pour", "").replace("Cherche", "").strip()
        # Tronquer aux delimiteurs de structure connus (garde uniquement le sujet)
        for delimiter in [
            "[CONTRAINTE SECONDAIRE",
            "REGLES ABSOLUES",
            "CAHIER DE BROUILLON",
            "CONTENU REEL DU FICHIER",
            "DIRECTION DU MENTOR",
            "DEFI DU MENTOR",
        ]:
            idx = query.find(delimiter)
            if idx > 0:
                query = query[:idx].strip()

        self.log_thought(f"🌍 Lancement WebSurfer Hybride : {query[:30]}...", "info")
        
        # Appel à l'outil dans un executor (search() est synchrone/bloquant)
        loop = asyncio.get_running_loop()
        web_results = await loop.run_in_executor(None, lambda: self.surfer.search(query, max_results=5))
        
        self.log_thought("✅ Résultats récupérés. Analyse et Synthèse...", "info")
        
        # On demande au cerveau de l'agent de synthétiser les résultats bruts
        synthesis = await self.generate_content(
            f"Tu es un analyste expert. Voici des résultats de recherche bruts concernant '{query}'.\n"
            f"Fais-en une synthèse structurée et exploitable pour un système IA.\n\n"
            f"[DONNÉES WEB]:\n{web_results[:4000]}"
            f"{AUTONOMY_GUARDRAIL}"
        )
        
        # Sauvegarde en mémoire pour le futur (RAG)
        self.remember(text=f"VEILLE '{query}': {synthesis}", metadata={"source": "web_search", "query": query})
        
        return {"status": "success", "result": synthesis}

    async def _run_ingestion_routine(self):
        """Sous-routine pour analyser les fichiers de la dropzone via le pipeline intelligent."""
        self.log_thought("📂 Lancement pipeline Dropzone 2.0...", type="action")

        from core.dropzone_pipeline import DropzonePipeline
        from core.orchestrator import orchestrator

        pipeline = DropzonePipeline(orchestrator)
        result = await pipeline.run()

        if result["status"] == "success":
            summary = result["manifest"]
            return {
                "status": "success",
                "result": (
                    f"INGESTION DROPZONE 2.0 TERMINÉE\n"
                    f"Projets analysés: {summary['total_projects']}\n"
                    f"Fichiers uniques: {summary['unique_files']}\n"
                    f"Doublons ignorés: {summary['duplicates']}\n"
                    f"Analyses produites: {result['analyses']}"
                )
            }
        return {"status": "warning", "result": "Pipeline Dropzone : aucun résultat."}