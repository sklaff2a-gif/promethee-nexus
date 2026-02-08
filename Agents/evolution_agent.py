import logging
import asyncio
from typing import Dict, Any
from core.base_agent import BaseAgent

logger = logging.getLogger("evolution")

class DivineEvolution(BaseAgent):
    """
    DivineEvolution V3.0 (Darwin Protocol - Integral)
    - Rôle : Directeur R&D Autonome.
    - Routine : Veille -> Analyse -> Spécification -> Coder -> Architecte.
    - Full Auto : Enchaîne les appels sans validation humaine intermédiaire.
    """
    def __init__(self):
        super().__init__(name="evolution", role="R&D Director", description="Supervise l'amélioration continue du système.")

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        mission = task_payload.get("mission", "")
        context = task_payload.get("context", "")
        
        # DÉCLENCHEMENT : MODE VEILLE (Automatique ou Manuel)
        if "[MODE VEILLE]" in mission or "veille" in mission.lower():
            self.log_thought("🧬 Activation du Protocole Darwin (Mode Intégral)...", type="thought")
            
            try:
                from core.orchestrator import orchestrator
                
                # --- PHASE 1 : EXPLORATION (Researcher) ---
                search_query = "python advanced design patterns automation agents 2025"
                self.log_thought(f"🔭 Phase 1 : Lancement Researcher ({search_query})...", type="info")
                
                research_response = await orchestrator.dispatch_task("researcher", {
                    "mission": f"VEILLE TECHNO: Trouve une technique Python avancée ou une librairie récente ({search_query}) utile pour un système d'agents autonomes. Sois concis et technique.",
                    "context": "Focus: Performance, Stabilité, Architecture."
                })
                
                research_data = research_response.get("result", "")
                if not research_data:
                    return {"status": "warning", "result": "Recherche infructueuse."}

                # --- PHASE 2 : ANALYSE & SPÉCIFICATION (Cerveau) ---
                self.log_thought("🧠 Phase 2 : Analyse de la pertinence...", type="thought")
                
                decision_prompt = (
                    f"Tu es le Directeur R&D. Voici une veille technologique :\n{research_data[:2000]}\n"
                    f"ANALYSE : Est-ce une amélioration concrète pour notre codebase (FastAPI/Python) ?\n"
                    f"SI OUI : Rédige une SPÉCIFICATION TECHNIQUE détaillée pour le Coder (Nom du fichier, Classes, Méthodes).\n"
                    f"SI NON : Réponds juste 'R.A.S'."
                )
                spec_response = await self.generate_content(decision_prompt)
                
                if "R.A.S" in spec_response:
                    self.log_thought("💤 Découverte non pertinente. Fin de cycle.", type="info")
                    return {"status": "success", "result": "R.A.S"}

                # --- PHASE 3 : MATÉRIALISATION (Coder) ---
                self.log_thought("🛠️ Phase 3 : Délégation au Coder...", type="info")
                
                coder_response = await orchestrator.dispatch_task("coder", {
                    "mission": "Génère le code complet correspondant à cette spécification. Donne UNIQUEMENT le code.",
                    "context": f"SPÉCIFICATION :\n{spec_response}"
                })
                
                generated_code = coder_response.get("result", "")
                if not generated_code:
                    return {"status": "error", "result": "Le Coder n'a rien produit."}

                # --- PHASE 4 : DÉPLOIEMENT SÉCURISÉ (Architecte) ---
                # On passe le bébé à l'Architecte. C'est lui qui décidera d'envoyer au Formatter -> Factory.
                self.log_thought("🛡️ Phase 4 : Soumission à l'Architecte...", type="info")
                
                architect_response = await orchestrator.dispatch_task("architect", {
                    "mission": "ADMIN OVERRIDE: Analyse ce nouveau module R&D. S'il est sûr, valide-le pour déploiement (Envoi Formatter).",
                    "context": generated_code
                })
                
                return {
                    "status": "success", 
                    "result": f"CYCLE DARWIN TERMINÉ.\nRecherche: OK\nSpec: OK\nCode: OK\nDéploiement: {architect_response.get('status')}"
                }

            except Exception as e:
                self.log_thought(f"❌ Erreur critique Protocole Darwin : {e}", type="error")
                return {"status": "error", "result": str(e)}

        # MODE PAR DÉFAUT
        else:
            return {"status": "success", "result": "Evolution en attente d'ordre de veille."}