from core.base_agent import BaseAgent


class MathWizard(BaseAgent):
    def __init__(self):
        super().__init__(
            name="math_wizard",
            role="Mathématicien éphémère",
            description="Calculs mathématiques à la demande"
        )

    async def process_task(self, task_payload):
        mission = task_payload.get("mission", "")
        response = await self.generate_content(f"Tu es un mathématicien. {mission}")
        return {"status": "success", "result": response}
