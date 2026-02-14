import logging
import re
import asyncio
from typing import Dict, Any
from core.base_agent import BaseAgent

logger = logging.getLogger("architect")

class DivineArchitect(BaseAgent):
    """
    DivineArchitect V26.1 (Router + Admin Override)
    - Rôle : Valideur ET Routeur.
    - Safety Net : Force la validation si le risque est 'LOW'.
    - Admin Override : Autorise les mises à jour système si demandées explicitement.
    - Action : Si validation OK, déclenche le FORMATTER.
    """
    def __init__(self):
        super().__init__(name="architect", role="Senior Staff Engineer", description="Valideur et Gardien de la cohérence.")
        
        self.system_instructions = """
Tu es l'ARCHITECTE DU SYSTÈME.
Ta mission : Autoriser le déploiement du code si aucun risque critique n'est détecté.

CRITÈRES DE VALIDATION :
1. ✅ AUTORISE : Création de nouveaux fichiers, Tests unitaires, Scripts isolés, Optimisations.
2. ⚠️ PRUDENCE : Modification du noyau (core/), Changement d'architecture.
3. ⛔ INTERDIT : Suppression de fichiers (os.remove), Code malveillant.
4. 🔓 OVERRIDE : Si la mission contient le mot-clé exact "ADMIN_OVERRIDE", tu DOIS VALIDER les modifications structurelles.

FORMAT DE RÉPONSE :
- Commence par "VALIDÉ" ou "REFUSÉ".
"""

    @staticmethod
    def _strip_llm_prefix(text: str) -> str:
        """Strip les préfixes markdown/commentary que les LLMs locaux ajoutent avant le mot-clé."""
        text = text.strip()
        # Retirer les headers markdown (## VALIDÉ, ### REFUSÉ, etc.)
        text = re.sub(r'^#+\s*', '', text)
        # Retirer bold/italic (**VALIDÉ**, *VALIDÉ*, __VALIDÉ__)
        text = re.sub(r'^[\*_]{1,3}\s*', '', text)
        # Retirer puces (- VALIDÉ, * VALIDÉ)
        text = re.sub(r'^[-\*]\s+', '', text)
        return text.strip()

    def _analyze_risk(self, text: str) -> str:
        """Analyse heuristique rapide des dangers."""
        text_lower = text.lower()
        # 1. DANGER CRITIQUE — commandes destructrices, exécution arbitraire, escalade de privilèges
        critical_patterns = [
            "os.remove", "shutil.rmtree", "format c:", "drop database", "system32",
            "rm -rf", "rm -r ", "rmdir /s",
            "subprocess.run", "subprocess.call", "subprocess.popen", "os.system", "os.popen",
            "setuid", "setgid", "chmod 777", "chmod +s",
            "eval(", "exec(", "__import__(",
            "pickle.loads", "marshal.loads",
            "os.environ[", "os.putenv",
        ]
        if any(x in text_lower for x in critical_patterns):
            return "CRITICAL"
        # 2. RISQUE FAIBLE (Tests, Scripts simples, Logs)
        if any(x in text_lower for x in ["test_", "hello.py", "script", "print(", "logging.", "tests/", "config"]):
            return "LOW"
        # 3. RISQUE MOYEN (Modifs noyau, logique métier)
        return "MEDIUM"

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        context = task_payload.get("context", "") 
        mission = task_payload.get("mission", "")
        full_content = f"{mission}\n{context}"

        self.log_thought("⚖️ Analyse & Routage V26.1...", type="thought")
        
        # 1. ANALYSE HEURISTIQUE
        risk_level = self._analyze_risk(full_content)
        # Override explicite uniquement via mot-clé dédié (pas de faux positifs sur "update" ou "admin")
        # Supporte "ADMIN_OVERRIDE" (underscore) ET "ADMIN OVERRIDE" (espace) — l'Evolution utilise l'espace
        mission_upper = mission.upper()
        is_override = "ADMIN_OVERRIDE" in mission_upper or "ADMIN OVERRIDE" in mission_upper
        
        # On prépare le terrain pour le LLM
        if risk_level == "LOW":
            prompt_prefix = "[INFO: Risque FAIBLE détecté. Validation recommandée.]"
        elif risk_level == "CRITICAL":
            prompt_prefix = "[ALERTE: CODE DANGEREUX. Rigueur absolue requise.]"
        else:
            prompt_prefix = "[INFO: Risque MOYEN. Vérification standard.]"
            
        if is_override:
             prompt_prefix += " [ADMIN OVERRIDE DETECTÉ: Autorisation des modifications système]"
        
        # 2. CONSULTATION MÉMOIRE & LLM
        jurisprudence = self.recall(context[:50] + " ERROR", limit=1)
        
        full_prompt = f"""
        {self.system_instructions}
        {prompt_prefix}
        
        --- PROPOSITION ---
        {full_content[:2000]}
        
        --- JURISPRUDENCE ---
        {jurisprudence if jurisprudence else "R.A.S"}
        
        DÉCISION :
        """
        
        response = await self.generate_content(full_prompt)
        
        # 3. DÉCODAGE DE LA DÉCISION
        # Strip les préfixes markdown (## VALIDÉ, **REFUSÉ**, etc.) avant analyse
        cleaned_response = self._strip_llm_prefix(response).upper()

        # Validation robuste : la réponse doit COMMENCER par le mot-clé (après strip markdown)
        llm_approved = any(cleaned_response.startswith(kw) for kw in ["VALIDÉ", "VALIDE", "ORDRE_USINE", "ACCORD", "PROCEED"])
        llm_refused = any(cleaned_response.startswith(kw) for kw in ["REFUSÉ", "REFUSE", "REJECTED", "INTERDIT"])

        # 4. LOGIQUE D'ACTIVATION (Le Cœur du Système)
        # On valide si :
        # A) Le LLM est d'accord ET n'a pas refusé.
        # B) OU SI le risque est FAIBLE (Safety Net) et que le LLM n'a pas hurlé "INTERDIT".
        # C) OU SI Override Admin détecté et risque non critique.
        
        should_activate = (llm_approved and not llm_refused) or \
                          (risk_level == "LOW" and not llm_refused) or \
                          (is_override and risk_level != "CRITICAL")

        if should_activate:
            trigger_msg = "✅ VALIDATION (Envoi au Formatter)."
            if is_override: trigger_msg += " [ADMIN OVERRIDE]"
            elif not llm_approved: trigger_msg += " [Force-Low-Risk]"
            
            self.log_thought(trigger_msg, type="success")
            
            # --- ROUTAGE VERS LE FORMATTER ---
            try:
                from core.orchestrator import orchestrator
                
                formatter_payload = {
                    "mission": "Nettoie ce code validé pour la Factory.",
                    "context": full_content 
                }
                
                loop = asyncio.get_running_loop()
                loop.create_task(orchestrator.dispatch_task("formatter", formatter_payload))
                
                return {"status": "success", "result": "ROUTAGE_FORMATTER_OK", "details": trigger_msg}
                
            except Exception as e:
                self.log_thought(f"⚠️ Erreur Technique Relais : {e}", type="error")
                return {"status": "warning", "result": "VALIDÉ MAIS ÉCHEC ROUTAGE"}
        
        else:
            self.log_thought(f"❌ BLOQUÉ ({risk_level}) : {response[:50]}...", type="error")
            return {"status": "error", "result": response}