import logging
from typing import Dict, Any
from core.base_agent import BaseAgent

logger = logging.getLogger("coder")

class DivineCoder(BaseAgent):
    def __init__(self):
        super().__init__(name="coder", role="AI Software Architect", description="Expert Python.")
        self.system_instructions = """
Tu es un développeur expert.
Ton but est de générer du code parfait.
AVANT DE CODER, lis les "LEÇONS DU PASSÉ" ci-dessous et évite de refaire les mêmes erreurs.
"""

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        mission = task_payload.get("mission", "")
        
        # 1. CONSULTER LA MÉMOIRE
        self.log_thought("Consultation des archives techniques...", type="thought")
        past_lessons = self.recall(mission, limit=3)
        
        if past_lessons:
            self.log_thought(f"J'ai trouvé des leçons pertinentes : {past_lessons[:50]}...", type="info")
        else:
            self.log_thought("Aucune leçon précédente trouvée. J'innove.", type="info")

        # 2. INTÉGRER LES LEÇONS AU PROMPT
        full_prompt = f"""
        {self.system_instructions}
        
        --- LEÇONS DU PASSÉ (À RESPECTER IMPÉRATIVEMENT) ---
        {past_lessons}
        ----------------------------------------------------
        
        MISSION ACTUELLE : {mission}
        """
        
        response = await self.generate_content(full_prompt)
        return {"status": "success", "result": response, "agent": self.name}
