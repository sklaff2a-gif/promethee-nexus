import json
import logging
from typing import Dict, Any, List
from core.base_agent import BaseAgent
from core.capabilities.web_surfer import WebSurfer
from core.capabilities.knowledge_ingestor import KnowledgeIngestor

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
        # On charge les outils externes
        self.surfer = WebSurfer()
        self.ingestor = KnowledgeIngestor()

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        mission = task_payload.get("mission", "")
        self.log_thought(f"🔍 Mission reçue : {mission[:50]}...", type="thought")
        
        # --- 1. MODE INGESTION LOCALE (Dropzone) ---
        if "dropzone" in mission.lower() or "scan" in mission.lower() or "lecture" in mission.lower():
            return await self._run_ingestion_routine()

        # --- 2. MODE RECHERCHE WEB (Par défaut ou explicite) ---
        # Si la mission contient des mots de recherche OU si aucune autre action n'est détectée
        
        # Nettoyage de la requête
        query = mission.replace("Researcher:", "").replace("Scanne le web pour", "").replace("Cherche", "").strip()
        
        self.log_thought(f"🌍 Lancement WebSurfer Hybride : {query[:30]}...", "info")
        
        # Appel à l'outil (C'est ici que la magie opère : Google ou DDG ?)
        web_results = self.surfer.search(query, max_results=5)
        
        self.log_thought("✅ Résultats récupérés. Analyse et Synthèse...", "info")
        
        # On demande au cerveau de l'agent de synthétiser les résultats bruts
        synthesis = await self.generate_content(
            f"Tu es un analyste expert. Voici des résultats de recherche bruts concernant '{query}'.\n"
            f"Fais-en une synthèse structurée et exploitable pour un système IA.\n\n"
            f"[DONNÉES WEB]:\n{web_results}"
        )
        
        # Sauvegarde en mémoire pour le futur (RAG)
        self.remember(text=f"VEILLE '{query}': {synthesis}", metadata={"source": "web_search", "query": query})
        
        return {"status": "success", "result": synthesis}

    async def _run_ingestion_routine(self):
        """Sous-routine pour lire les fichiers locaux."""
        self.log_thought("📂 Ingestion locale...", type="action")
        files = self.ingestor.scan_new_files()
        
        if not files: 
            return {"status": "warning", "result": "Dropzone vide. Aucun nouveau fichier à lire."}
            
        report = []
        full_knowledge = ""
        
        for filepath in files:
            filename, content = self.ingestor.read_and_archive(filepath)
            if content:
                # Analyse rapide du contenu
                analysis = await self.generate_content(f"Analyse ce document technique ({filename}) en 3 points clés :\n{content[:4000]}...")
                
                # Mémorisation
                self.remember(text=f"ARCHIVE ({filename}): {analysis}", metadata={"source": filename})
                
                report.append(f"✅ {filename} : Traité et mémorisé.")
                full_knowledge += f"\n--- {filename} ---\n{analysis}\n"
                
        return {"status": "success", "result": f"INGESTION TERMINÉE:\n" + "\n".join(report)}