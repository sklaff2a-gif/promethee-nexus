from core.base_agent import BaseAgent


class Translator(BaseAgent):
    def __init__(self):
        super().__init__(
            name="translator",
            role="Traducteur multilingue",
            description="Traduction multilingue de textes"
        )

    async def process_task(self, task_payload):
        mission = task_payload.get("mission", "")
        prompt = (
            "Tu es un traducteur professionnel multilingue. "
            "Traduis le texte suivant avec précision en respectant le ton et le contexte.\n\n"
            f"{mission}"
        )
        response = await self.generate_content(prompt)
        return {"status": "success", "result": response}
