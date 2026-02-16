import logging
from typing import Dict, Any
from core.base_agent import BaseAgent

logger = logging.getLogger("strategist")

class DivineStrategist(BaseAgent):
    """
    DivineStrategist V13.5 - Process Optimizer
    Rôle : Chef d'Orchestre & Optimisateur (Kaizen)
    """
    def __init__(self):
        super().__init__(
            name="strategist", 
            role="Chief Operating Officer (COO)", 
            description="Optimise les flux et les stratégies des autres agents."
        )
        
        # NOUVEAU PROMPT SYSTÈME : ORIENTATION OPTIMISATION
        self.system_instructions = """
Tu es le STRATÈGE (COO) du système Nexus.
Ton rôle n'est PAS de faire le travail, mais de rendre le travail des autres (Coder, Researcher, Factory) PLUS EFFICACE.

TES 3 MODES DE PENSÉE :
1. 🔧 KAIZEN (Optimisation) : Si on te donne du code ou un plan, ne cherche pas seulement l'erreur. Cherche la SIMPLIFICATION. Comment faire la même chose avec moins de lignes / moins de RAM / moins de temps ?
2. ♟️ ARBITRAGE (Vision) : Si le Researcher donne une info et le Coder une autre, trouve le lien manquant ou la contradiction.
3. 🛡️ RISQUE (Audit) : Détecte les failles logiques, mais propose immédiatement le correctif.

RÈGLE D'OR :
Ne sois pas passif. Tes réponses doivent commencer par "ANALYSE :" suivi de "RECOMMANDATION ACTIONNABLE :".
"""

    def _get_project_files(self) -> str:
        """Liste les fichiers réels du projet pour ancrer les analyses."""
        try:
            from core.council import _get_project_structure
            return _get_project_structure()
        except Exception:
            return ""

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        mission = task_payload.get("mission", "")
        context = task_payload.get("context", "")

        # Structure projet réelle (anti-hallucination de chemins)
        project_files = self._get_project_files()

        full_prompt = f"""
        {self.system_instructions}

        --- STRUCTURE DU PROJET ---
        {project_files}

        --- DONNÉES À ANALYSER ---
        MISSION : {mission}

        CONTEXTE TECHNIQUE / RÉSULTATS PRÉCÉDENTS :
        {context}
        --------------------------

        IMPORTANT : Ne cite QUE des fichiers listés ci-dessus. N'invente PAS de chemins.
        Agis maintenant en tant que Stratège (COO). Quelle est la meilleure approche ?
        """

        response = await self.generate_content(full_prompt)

        return {"status": "success", "result": response, "agent": self.name}
