import asyncio
import logging
import inspect
import re
import sys
from typing import Dict, Any

logger = logging.getLogger("Orchestrator")

class Orchestrator:
    def __init__(self):
        self.agents = {}
        self.kill_switch_active = False

    async def register_agent(self, slug: str, agent_instance: Any):
        self.agents[slug] = agent_instance
        logger.info(f"✅ [ORCHESTRATOR] Agent enregistré : {slug}")

    async def set_kill_switch(self, active: bool):
        self.kill_switch_active = active
        status = "ACTIVÉ 🛑" if active else "DÉSACTIVÉ 🟢"
        logger.warning(f"⚠️ [ORCHESTRATOR] KILL SWITCH {status}")

    @staticmethod
    def _is_validated(text: str) -> bool:
        """Vérifie que la réponse COMMENCE par VALIDÉ/VALIDE (pas juste un substring)."""
        cleaned = text.strip().upper()
        return cleaned.startswith("VALIDÉ") or cleaned.startswith("VALIDE") or cleaned.startswith("VALIDATED")

    @staticmethod
    def _contains_python_code(text: str) -> bool:
        """Détecte du vrai code Python structurel. Délègue à core.code_utils (M05)."""
        from core.code_utils import contains_python_code
        return contains_python_code(text)

    # Marqueurs de contextes internes — importé depuis BaseAgent (source unique)
    @staticmethod
    def _get_internal_markers():
        from core.base_agent import BaseAgent
        return BaseAgent._LOCAL_FORCE_MARKERS

    async def dispatch_task(self, target_slug: str, task_payload: Dict[str, Any]):
        if self.kill_switch_active:
            return {"status": "BLOCKED", "reason": "KILL_SWITCH_ACTIVE"}

        agent = self.agents.get(target_slug)

        # --- [V18.2] PROTOCOLE D'INVOCATION (Grimoire / Serverless - DEBUG MODE) ---
        # Si l'agent n'est pas trouvé en mémoire, on tente de l'invoquer via le Summoner
        if not agent:
            try:
                # Import dynamique robuste
                try:
                    from core.summoner import Summoner
                except ImportError:
                    # Fallback si lancé depuis un dossier différent
                    from summoner import Summoner

                # 1. On charge le module depuis le disque
                summoner = Summoner(target_slug)
                module = summoner.load()

                # 2. On instancie la première classe trouvée dans ce module
                class_found = False
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    # On vérifie que la classe est bien définie DANS le fichier (pas un import)
                    if obj.__module__ == module.__name__:
                        agent = obj() # Instanciation Éphémère
                        logger.info(f"🔮 [GRIMOIRE] SUCCÈS : Agent '{name}' invoqué pour '{target_slug}'")
                        class_found = True
                        break

                if not class_found:
                    logger.warning(f"👻 [GRIMOIRE] Module '{target_slug}' trouvé mais aucune classe compatible détectée à l'intérieur.")

            except Exception as e:
                logger.error(f"👻 [GRIMOIRE] ÉCHEC INVOCATION '{target_slug}' : {e}")

        # Si après tout ça on n'a toujours pas d'agent...
        if not agent:
            return {"status": "ERROR", "reason": f"AGENT_NOT_FOUND: {target_slug}"}

        try:
            # --- DÉTECTION CONTEXTE INTERNE → FORCER LOCAL ---
            context = str(task_payload.get("context", ""))
            mission = str(task_payload.get("mission", ""))
            if task_payload.get("force_local", False) or any(
                m in context or m in mission for m in self._get_internal_markers()
            ):
                if hasattr(agent, "_force_local_next"):
                    agent._force_local_next = True

            # --- Publication dispatch pour loggers/frontend ---
            from core.event_bus.bus import bus as _bus
            await _bus.publish("AGENT_TASK_DISPATCH", {
                "target": target_slug,
                "mission": mission[:200],
            })

            # --- EXÉCUTION ---
            response = await agent.process_task(task_payload)

            # Cleanup du flag (sécurité — normalement déjà reset par generate_content)
            if hasattr(agent, "_force_local_next"):
                agent._force_local_next = False

            # --- Publication du statut pour SelfAwareness ---
            from core.event_bus.bus import bus
            resp_status = response.get("status", "success") if isinstance(response, dict) else "success"
            await bus.publish("MISSION_FINISHED", {
                "agent": target_slug,
                "status": resp_status,
            })

            # --- Publication AGENT_RESPONSE pour le frontend WebSocket ---
            import time as _time
            result_text = response.get("result", "") if isinstance(response, dict) else str(response)
            if result_text and resp_status != "BLOCKED":
                await bus.publish("AGENT_RESPONSE", {
                    "agent": target_slug,
                    "content": str(result_text)[:2000],
                    "timestamp": str(_time.time()),
                })

            # --- [V18.3] DISSIPATION D'EIDOLON ---
            if target_slug not in self.agents:
                module_key = target_slug
                if module_key in sys.modules:
                    del sys.modules[module_key]
                del agent
                logger.info(f"👻 [GRIMOIRE] Eidolon '{target_slug}' dissipé.")

            # --- Guard : pas de réactions en chaîne pour les pipelines internes ---
            # DROPZONE_ANALYSIS : le pipeline Dropzone gère son propre flux
            # EVOLUTION_PIPELINE : l'agent Evolution gère Coder→Architect lui-même
            task_context = str(task_payload.get("context", ""))
            is_internal_pipeline = (
                task_payload.get("is_pipeline", False)
                or "DROPZONE_ANALYSIS" in task_context
                or "EVOLUTION_PIPELINE" in task_context
            )

            # --- [V17.0] LE PONT D'EXÉCUTION (Architecte -> Factory) ---
            # M06: L'Architecte route désormais en interne vers le Formatter (retourne FORMATÉ_*).
            # Le Bridge ne déclenche que si l'Architecte retourne un VALIDÉ brut sans avoir
            # déjà routé lui-même (safety net pour appels directs sans routage interne).
            if target_slug == "architect" and response.get("status") == "success" and not is_internal_pipeline:
                res_text = str(response.get("result", ""))

                # Skip si l'Architecte a déjà géré le routage (FORMATÉ_*, SANS_CODE)
                if self._is_validated(res_text) and "SANS_CODE" not in res_text and "FORMATÉ" not in res_text:
                    logger.info("🚀 [BRIDGE] Architecte a validé (sans routage interne). Transfert vers Factory...")

                    code_to_apply = task_payload.get("context", "")

                    if self._contains_python_code(code_to_apply):
                        asyncio.create_task(self.dispatch_task("factory", {
                            "mission": "Applique ce code validé par l'Architecte.",
                            "context": code_to_apply,
                            "evolution_spec_id": "BRIDGE_VALIDATED",
                        }))
                    else:
                        logger.warning("⚠️ [BRIDGE] Validation reçue mais aucun code Python structurel trouvé dans le contexte.")

            # --- [V16.3] RÉACTION EN CHAÎNE (Evolution/Coder -> Architecte) ---
            if target_slug in ["evolution", "coder"] and response.get("status") == "success" and not is_internal_pipeline:
                result_text = str(response.get("result", ""))
                if self._contains_python_code(result_text):
                    logger.info("⚡ DÉCLENCHEMENT ARCHITECTE...")

                    asyncio.create_task(self.dispatch_task("architect", {
                        "mission": f"VALIDATION REQUISE : {result_text[:100]}...",
                        "context": result_text
                    }))

            return response
        except Exception as e:
            logger.error(f"❌ Erreur exécution {target_slug}: {e}")
            return {"status": "ERROR", "error": str(e)}

    async def dispatch_council(self, participants: list, mission: str,
                               max_rounds: int = 5, enable_student: bool = True,
                               enable_advocate: bool = True) -> Dict[str, Any]:
        """Lance un débat multi-agents via le système Council."""
        if self.kill_switch_active:
            return {"status": "BLOCKED", "reason": "KILL_SWITCH_ACTIVE"}
        from core.council import Council
        council = Council(self.agents, participants, mission, max_rounds,
                          enable_student=enable_student, enable_advocate=enable_advocate)
        return await council.run()

orchestrator = Orchestrator()
