from core.base_agent import BaseAgent


class GitKeeper(BaseAgent):
    def __init__(self):
        super().__init__(
            name="git_keeper",
            role="Expert Git",
            description="Génère des commandes Git, explique les workflows, rédige des messages de commit conventionnels, résout des conflits"
        )

    async def process_task(self, task_payload):
        mission = task_payload.get("mission", "")
        prompt = (
            "Tu es un expert Git. "
            "Génère des commandes précises, explique les workflows, "
            "rédige des messages de commit conventionnels, "
            "et aide à résoudre les conflits de merge. "
            "Ne fournis que des conseils, pas d'exécution directe.\n\n"
            f"{mission}"
        )
        response = await self.generate_content(prompt)
        return {"status": "success", "result": response}
