"""Philosopher Agent (P15.3, 2026-05-14) — Raisonnement axiomatique pur.

Agent dédié à SCHOOL_AXIOMATIC. Tient une cohérence relationnelle longue
sur un univers inventé, sans plaquer notre physique/sociologie réelle.

Modèle : généraliste local (Config.LOCAL_MODELS["philosopher"]) — raisonnement
pur, pas de code ; le spécialiste code serait inadapté à ce slot conceptuel.
"""
import logging
from typing import Dict, Any
from core.base_agent import BaseAgent

logger = logging.getLogger("philosopher")


class Philosopher(BaseAgent):
    """
    Philosopher — Architecte de cohérence axiomatique.
    Rôle : tenir un système de contraintes inventées sur de nombreux échanges,
    en respectant un axiome contraignant et en chaînant les déductions.
    """

    def __init__(self):
        super().__init__(
            name="philosopher",
            role="Architecte de cohérence axiomatique",
            description="Raisonne dans des univers inventés en tenant l'axiome de base et en chaînant les déductions sans plaquer notre réalité.",
        )
        self.system_instructions = """Tu es le philosophe du projet Prométhée.

TON RÔLE :
- Raisonner à partir d'un axiome contraignant donné, sans jamais le contredire.
- Inventer des concepts cohérents avec l'axiome, en réutilisant ceux déjà construits.
- Chaîner explicitement tes déductions (car / donc / découle de / parce que).
- Refuser de plaquer notre physique, sociologie ou morale humaine sans transposition assumée.

RÈGLES STRICTES :
1. Réponds UNIQUEMENT en français.
2. Reste TOUJOURS dans le cadre de l'axiome fourni. Ne change pas d'univers.
3. Réutilise activement les concepts déjà construits si pertinents.
4. Invente des néologismes propres à l'univers plutôt qu'emprunter des termes lourds de sens humain.
5. Format : 4-8 phrases denses, pas de remplissage, pas de formules de politesse.
6. NE commence JAMAIS par "C'est une question intéressante", "Jean-Michel", "Je me souviens" — ces formules déclenchent le guardrail.

OBJECTIF ULTIME : cohérence relationnelle pure. Chaque déduction doit s'appuyer
sur les couches axiomatiques précédentes. Tu es un architecte qui empile des
pierres, pas un poète qui décrit l'édifice.
"""

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Génération de raisonnement axiomatique pur."""
        self.log_thought(f"{self.name} analyse l'axiome...", type="thought")

        mission = task_payload.get("mission", "Aucune mission définie")
        context = task_payload.get("context", "")

        # Pas de RAG souvenirs ici : on est en raisonnement pur, l'univers
        # est fourni par mission_with_full_context (axiome + concepts).
        prompt = f"""{self.system_instructions}

CONTEXTE :
{context[:6000]}

MISSION (question de la session) :
{mission}

Réponds en chaînant explicitement tes déductions à partir de l'axiome et des
concepts déjà construits."""

        response = await self.generate_content(prompt)

        return {
            "status": "success",
            "result": response,
            "agent": self.name,
        }
