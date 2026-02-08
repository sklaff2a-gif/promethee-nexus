from core.base_agent import BaseAgent


class DrDebug(BaseAgent):
    def __init__(self):
        super().__init__(
            name="dr_debug",
            role="Diagnosticien Python",
            description="Diagnostic de bugs Python, lecture de tracebacks, patchs chirurgicaux"
        )

    async def process_task(self, task_payload):
        mission = task_payload.get("mission", "")
        prompt = (
            "Tu es un expert en debugging Python. "
            "Analyse le problème suivant, identifie la cause racine, "
            "et propose un patch précis.\n\n"
            f"{mission}"
        )
        response = await self.generate_content(prompt)
        return {"status": "success", "result": response}
