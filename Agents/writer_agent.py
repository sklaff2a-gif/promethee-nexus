import logging
from typing import Dict, Any
from core.base_agent import BaseAgent

logger = logging.getLogger("writer")


class DivineWriter(BaseAgent):
    """
    DivineWriter — Agent de rédaction de contenu.
    Rôle : Génération de texte structuré (docs, rapports, articles, communications).
    Adapte le ton et le format selon la mission.
    """

    def __init__(self):
        super().__init__(
            name="writer",
            role="AI Content Strategist",
            description="Rédige du contenu structuré : documentation, rapports, articles, communications"
        )
        self.system_instructions = """Tu es le rédacteur du projet Prométhée.

CONTEXTE : Système multi-agents Python (FastAPI, Ollama local, ChromaDB) sur un PC Windows.

TON RÔLE :
- Rédiger du contenu clair, structuré et professionnel
- Adapter le ton selon le type de contenu (technique, rapport, communication)
- Produire du texte exploitable directement (pas de placeholder, pas de TODO)
- Synthétiser des informations complexes en texte lisible

RÈGLES STRICTES :
1. Réponds UNIQUEMENT en français
2. Structure tes réponses avec des titres et sections clairs
3. Adapte le niveau de détail au contexte demandé
4. Pas de remplissage — chaque phrase doit apporter de l'information
5. Utilise le vocabulaire technique approprié sans jargon inutile

FORMATS SUPPORTÉS :
- DOCUMENTATION : Structure technique avec exemples de code si pertinent
- RAPPORT : Synthèse avec métriques, constats et recommandations
- ARTICLE : Intro accrochante → Développement → Conclusion
- COMMUNICATION : Message concis et actionnable
"""

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Génération de contenu structuré."""
        self.log_thought(f"{self.name} analyse la tâche...", type="thought")

        mission = task_payload.get("mission", "Aucune mission définie")
        context = task_payload.get("context", "")

        # Enrichir avec la mémoire RAG
        rag_context = self.recall(mission)

        prompt = f"""{self.system_instructions}

MISSION : {mission}

CONTEXTE :
{context[:3000]}

MÉMOIRE PERTINENTE :
{rag_context[:1000] if rag_context else "(aucune)"}

Rédige le contenu demandé en respectant le format approprié."""

        response = await self.generate_content(prompt)

        # Mémoriser les productions significatives
        if response and len(response) > 200:
            self.remember(
                text=f"RÉDACTION '{mission[:80]}': {response[:400]}",
                metadata={"source": "writer", "mission": mission[:200]}
            )

        return {
            "status": "success",
            "result": response,
            "agent": self.name
        }
