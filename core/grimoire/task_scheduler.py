from core.base_agent import BaseAgent


class TaskScheduler(BaseAgent):
    def __init__(self):
        super().__init__(
            name="task_scheduler",
            role="Planificateur de tâches",
            description="Décompose des projets en étapes, estime les priorités, génère des plans d'action et roadmaps"
        )

    async def process_task(self, task_payload):
        mission = task_payload.get("mission", "")
        prompt = (
            "Tu es un planificateur de projet expert. "
            "Décompose les objectifs en tâches concrètes, "
            "estime les priorités, propose des roadmaps structurées "
            "avec dépendances et jalons.\n\n"
            f"{mission}"
        )
        response = await self.generate_content(prompt)
        return {"status": "success", "result": response}
