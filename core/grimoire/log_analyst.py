from core.base_agent import BaseAgent


class LogAnalyst(BaseAgent):
    def __init__(self):
        super().__init__(
            name="log_analyst",
            role="Analyste de logs",
            description="Identifie des patterns d'erreur, diagnostique des incidents, propose des règles d'alerte"
        )

    async def process_task(self, task_payload):
        mission = task_payload.get("mission", "")
        prompt = (
            "Tu es un analyste de logs applicatifs expert. "
            "Identifie les patterns récurrents, classifie par sévérité, "
            "diagnostique les incidents et propose des règles d'alerte.\n\n"
            f"{mission}"
        )
        response = await self.generate_content(prompt)
        return {"status": "success", "result": response}
