from core.base_agent import BaseAgent


class DocWriter(BaseAgent):
    def __init__(self):
        super().__init__(
            name="doc_writer",
            role="Rédacteur de documentation",
            description="Rédige de la documentation technique : README, docstrings, guides API, changelogs"
        )

    async def process_task(self, task_payload):
        mission = task_payload.get("mission", "")
        prompt = (
            "Tu es un rédacteur de documentation technique. "
            "Rédige de la documentation claire, structurée en Markdown, "
            "avec des exemples de code pertinents.\n\n"
            f"{mission}"
        )
        response = await self.generate_content(prompt)
        return {"status": "success", "result": response}
