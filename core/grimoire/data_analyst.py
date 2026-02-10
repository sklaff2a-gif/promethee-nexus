from core.base_agent import BaseAgent


class DataAnalyst(BaseAgent):
    def __init__(self):
        super().__init__(
            name="data_analyst",
            role="Analyste de données",
            description="Interprète CSV/JSON, statistiques, tendances, visualisations pandas/matplotlib"
        )

    async def process_task(self, task_payload):
        mission = task_payload.get("mission", "")
        prompt = (
            "Tu es un analyste de données expert. "
            "Analyse des données, identifie des patterns et tendances, "
            "propose des visualisations, et génère du code pandas/matplotlib.\n\n"
            f"{mission}"
        )
        response = await self.generate_content(prompt)
        return {"status": "success", "result": response}
