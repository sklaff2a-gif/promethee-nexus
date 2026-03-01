# Agents/vision_agent.py — DivineVision : Architecte de Vision Strategique
# Pilote la roadmap : recherche, specification et planification des modules futurs.
# Dispatch ROADMAP_RESEARCH (researcher + synthese) et ROADMAP_SPEC (1 appel LLM).

import logging
from typing import Dict, Any

from core.base_agent import BaseAgent

logger = logging.getLogger("vision")


class DivineVision(BaseAgent):
    """
    DivineVision V1.0 — Agent de vision strategique.
    Pilote la roadmap vivante de l'Eveil de Promethee :
    - ROADMAP_RESEARCH : identifie et recherche les sujets pour les modules planifies
    - ROADMAP_SPEC : genere des specs structurees a partir des recherches brutes
    - General : repond aux questions sur la roadmap avec contexte
    """

    def __init__(self):
        super().__init__(
            name="vision",
            role="Architecte de Vision Strategique",
            description="Pilote la roadmap : recherche, specification et planification des modules futurs."
        )

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        mission = task_payload.get("mission", "")
        context = task_payload.get("context", "")

        self.log_thought(f"Analyse mission: {mission[:60]}...", type="thought")

        # Dispatch selon le contexte/intent
        if "ROADMAP_RESEARCH" in context or "ROADMAP_RESEARCH" in mission:
            return await self._handle_research(task_payload)

        if "ROADMAP_SPEC" in context or "ROADMAP_SPEC" in mission:
            return await self._handle_spec(task_payload)

        return await self._handle_general(task_payload)

    async def _handle_research(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Recherche pour un module de la roadmap.

        1. Verifie should_research() (WIP limit)
        2. Recupere le prochain module a rechercher
        3. Dispatche vers researcher pour 1-2 topics
        4. Agrege les resultats, update status -> researching
        5. Memorise les findings
        """
        try:
            from core.roadmap_engine import roadmap
        except ImportError:
            return {"status": "error", "result": "RoadmapEngine non disponible."}

        # Verifier le WIP limit
        if not roadmap.should_research():
            pending = roadmap.get_pending_count()
            return {
                "status": "skipped",
                "reason": "wip_limit",
                "result": f"WIP limit atteint ({pending} modules pending). Focus sur le codage.",
            }

        # Prochain module a rechercher
        queue = roadmap.get_research_queue()
        if not queue:
            return {
                "status": "skipped",
                "reason": "no_research_target",
                "result": "Aucun module en attente de recherche.",
            }

        target = queue[0]
        module_id = target["id"]
        topics = target.get("research_topics", [])
        display = target.get("display", module_id)

        self.log_thought(f"Recherche pour module: {display} ({len(topics)} topics)", type="action")

        # Dispatcher vers researcher pour les topics (max 2)
        findings = []
        try:
            from core.orchestrator import orchestrator
            for topic in topics[:2]:
                self.log_thought(f"Topic: {topic[:50]}...", type="info")
                result = await orchestrator.dispatch_task("researcher", {
                    "mission": f"Recherche approfondie: {topic}",
                    "context": f"ROADMAP_RESEARCH pour module {display}",
                    "force_local": True,
                })
                if result and result.get("status") == "success":
                    findings.append(result.get("result", ""))
        except Exception as e:
            logger.warning(f"[VISION] Erreur dispatch researcher: {e}")

        if not findings:
            return {
                "status": "error",
                "result": f"Aucun resultat de recherche pour {display}.",
            }

        # Agreger et mettre a jour le status
        aggregated = "\n---\n".join(findings)
        summary = aggregated[:2000]  # Cap pour les specs brutes

        await roadmap.update_module_status(
            module_id, "researching",
            specs=[f"RESEARCH_RAW: {summary}"]
        )

        # Memoriser
        self.remember(
            text=f"ROADMAP_RESEARCH [{display}]: {summary[:500]}",
            metadata={"source": "roadmap_research", "module": module_id}
        )

        return {
            "status": "success",
            "result": f"Recherche completee pour {display}. {len(findings)} sources analysees. Status -> researching.",
        }

    async def _handle_spec(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Genere des specs structurees pour un module en status 'researching'.

        1. Trouve un module en 'researching' avec specs brutes
        2. Appel LLM pour generer des specs structurees
        3. Update status -> ready
        4. Memorise les specs
        """
        try:
            from core.roadmap_engine import roadmap
        except ImportError:
            return {"status": "error", "result": "RoadmapEngine non disponible."}

        # Trouver un module en researching
        target = roadmap.get_next_module(status_filter="researching")
        if not target:
            return {
                "status": "skipped",
                "reason": "no_spec_target",
                "result": "Aucun module en attente de specification.",
            }

        module_id = target["id"]
        display = target.get("display", module_id)
        description = target.get("description", "")
        raw_specs = target.get("specs", [])
        connects_to = target.get("connects_to", [])

        self.log_thought(f"Specification pour module: {display}", type="action")

        # Appel LLM pour generer des specs structurees
        raw_text = "\n".join(str(s) for s in raw_specs)
        prompt = (
            f"Tu es un architecte logiciel expert pour Promethee, un systeme multi-agents IA autonome.\n"
            f"Module a specifier: {display}\n"
            f"Description: {description}\n"
            f"Connexions prevues: {', '.join(connects_to)}\n"
            f"Recherches brutes:\n{raw_text[:3000]}\n\n"
            f"Genere des specs techniques structurees pour ce module:\n"
            f"1. Responsabilites principales (3-5 points)\n"
            f"2. Interface publique (methodes cles avec signatures)\n"
            f"3. Events bus a publier/souscrire\n"
            f"4. Dependances et connexions\n"
            f"5. Criteres de validation\n"
            f"Sois concis et specifique a Promethee."
        )

        spec_result = await self.generate_content(prompt)
        if not spec_result or len(spec_result) < 50:
            return {
                "status": "error",
                "result": f"Specification trop courte pour {display}.",
            }

        # Mettre a jour
        structured_specs = [f"SPEC_STRUCTURED: {spec_result[:3000]}"]
        await roadmap.update_module_status(module_id, "ready", specs=structured_specs)

        # Memoriser
        self.remember(
            text=f"ROADMAP_SPEC [{display}]: {spec_result[:500]}",
            metadata={"source": "roadmap_spec", "module": module_id}
        )

        return {
            "status": "success",
            "result": f"Specs generees pour {display}. Status -> ready.",
        }

    async def _handle_general(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Repond aux questions generales sur la roadmap."""
        mission = task_payload.get("mission", "")

        try:
            from core.roadmap_engine import roadmap
            ctx = roadmap.get_roadmap_context()
            stats = roadmap.get_stats()
            roadmap_info = (
                f"Etat actuel de la roadmap:\n{ctx}\n"
                f"Total: {stats['total_modules']} modules, "
                f"WIP pause: {stats['wip_paused']}, "
                f"Focus: {stats.get('current_focus', 'aucun')}"
            )
        except Exception:
            roadmap_info = "Roadmap non disponible."

        prompt = (
            f"Tu es l'architecte de vision strategique de Promethee.\n"
            f"{roadmap_info}\n\n"
            f"Question/Mission: {mission}\n"
            f"Reponds de maniere strategique et concise."
        )

        result = await self.generate_content(prompt)
        return {"status": "success", "result": result}
