import logging
import re
from typing import Dict, Any
from core.base_agent import BaseAgent
from config import Config

logger = logging.getLogger("architect")

class DivineArchitect(BaseAgent):
    """
    DivineArchitect V26.2 (Anti-Sterile + Autonomous Guard)
    - Rôle : Valideur ET Routeur.
    - Safety Net : Force la validation si le risque est 'LOW'.
    - Admin Override : Autorise les mises à jour système si demandées par l'UTILISATEUR.
    - Anti-Sterile : Skip le Formatter si aucun code Python n'est détecté.
    - Autonomous Guard : ADMIN_OVERRIDE ignoré en mode autonome.
    - Action : Si validation OK ET code présent, déclenche le FORMATTER.
    """
    def __init__(self):
        super().__init__(name="architect", role="Senior Staff Engineer", description="Valideur et Gardien de la cohérence.")

        self.system_instructions = """
Tu es l'ARCHITECTE DU SYSTÈME.
Ta mission : Autoriser le déploiement du code si aucun risque critique n'est détecté.

CONTEXTE IMPORTANT — FILET DE SÉCURITÉ :
Le code validé passera OBLIGATOIREMENT par un SANDBOX ISOLÉ avant tout déploiement.
Le sandbox exécute les tests automatiquement. Si les tests échouent, le code est ANNULÉ et le fichier original RESTAURÉ (.bak).
Tu n'es donc PAS la dernière ligne de défense. Tu filtres les INTENTIONS DANGEREUSES, pas les bugs potentiels.

CRITÈRES DE VALIDATION :
1. ✅ AUTORISE : Création de nouveaux fichiers, Tests unitaires, Scripts isolés, Optimisations, Refactoring.
2. ✅ AUTORISE : Modifications du noyau (core/) si elles sont cohérentes et ne suppriment pas de fonctionnalités existantes. Le sandbox testera la non-régression.
3. ⛔ INTERDIT : Suppression de fichiers (os.remove, shutil.rmtree), Code malveillant, eval/exec.
4. 🔓 OVERRIDE : Si la mission contient "ADMIN_OVERRIDE", tu DOIS VALIDER les modifications structurelles.

RÈGLE D'OR : En cas de doute sur un risque MOYEN, VALIDE. Le sandbox attrapera les régressions.
Ne refuse QUE les intentions clairement dangereuses ou destructrices.

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

    # Marqueurs de contexte indiquant un pipeline autonome (pas d'utilisateur)
    _AUTONOMOUS_MARKERS = ("PROTOCOLE_AUTONOMIE", "EVOLUTION_PIPELINE", "MODE VEILLE")

    @staticmethod
    def _contains_python_code(text: str) -> bool:
        """Détecte du vrai code Python structurel. Délègue à core.code_utils (M05)."""
        from core.code_utils import contains_python_code
        return contains_python_code(text)

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

        self.log_thought("⚖️ Analyse & Routage V26.2...", type="thought")

        # 1. ANALYSE HEURISTIQUE
        risk_level = self._analyze_risk(full_content)
        # Override explicite uniquement via mot-clé dédié (pas de faux positifs sur "update" ou "admin")
        # Supporte "ADMIN_OVERRIDE" (underscore) ET "ADMIN OVERRIDE" (espace)
        mission_upper = mission.upper()
        is_override = "ADMIN_OVERRIDE" in mission_upper or "ADMIN OVERRIDE" in mission_upper

        # Guard autonome : ADMIN_OVERRIDE ignoré si le contexte vient d'un pipeline autonome
        # (seules les commandes utilisateur réelles peuvent forcer l'override)
        if is_override:
            context_upper = context.upper()
            is_autonomous = any(m in context_upper or m in mission_upper
                                for m in self._AUTONOMOUS_MARKERS)
            if is_autonomous:
                self.log_thought("🛡️ ADMIN_OVERRIDE ignoré (mode autonome détecté).", type="warning")
                is_override = False

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
        jurisprudence = self.recall(mission[:200], limit=1)

        full_prompt = f"""
        {self.system_instructions}
        {prompt_prefix}

        --- PROPOSITION ---
        {full_content[:Config.get_max_content_chars("architect")]}

        --- JURISPRUDENCE ---
        {jurisprudence if jurisprudence else "R.A.S"}

        RAPPEL : Le sandbox testera automatiquement ce code. Valide sauf si DANGER CRITIQUE. Commence par VALIDÉ ou REFUSÉ.
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
        # B) OU SI le risque est FAIBLE (Safety Net) — le refus LLM sans justification
        #    est ignoré (le LLM 8B dit souvent "REFUSÉ." par conservatisme).
        #    Seul un refus avec raison critique (INTERDIT, DANGER, CRITICAL) bloque.
        # C) OU SI Override Admin détecté et risque non critique.

        # Un refus LOW n'est bloquant que si le LLM fournit une raison critique
        low_risk_hard_block = False
        if risk_level == "LOW" and llm_refused:
            hard_block_keywords = ["INTERDIT", "DANGER", "CRITICAL", "MALVEILLANT", "SUPPRESSION"]
            low_risk_hard_block = any(kw in cleaned_response for kw in hard_block_keywords)

        should_activate = (llm_approved and not llm_refused) or \
                          (risk_level == "LOW" and not low_risk_hard_block) or \
                          (is_override and risk_level != "CRITICAL")

        if should_activate:
            trigger_msg = "✅ VALIDATION"
            if is_override: trigger_msg += " [ADMIN OVERRIDE]"
            elif not llm_approved: trigger_msg += " [Force-Low-Risk]"

            # --- GUARD ANTI-BOUCLE STÉRILE ---
            # Ne dispatcher au Formatter QUE s'il y a du vrai code Python
            if not self._contains_python_code(full_content):
                trigger_msg += " (Pas de code Python — skip Formatter)."
                self.log_thought(trigger_msg, type="success")
                return {"status": "success", "result": "VALIDÉ_SANS_CODE", "details": trigger_msg, "routed_internally": True}

            trigger_msg += " (Envoi au Formatter)."
            self.log_thought(trigger_msg, type="success")

            # --- ROUTAGE VERS LE FORMATTER ---
            try:
                from core.orchestrator import orchestrator

                formatter_payload = {
                    "mission": "Nettoie ce code validé pour la Factory.",
                    "context": full_content
                }

                # Propager EVOLUTION_SPEC_ID si présent (pipeline Evolution → Factory)
                spec_id_match = re.search(r'EVOLUTION_SPEC_ID:\s*(\S+)', mission)
                if spec_id_match:
                    formatter_payload["evolution_spec_id"] = spec_id_match.group(1)

                # Propager target_file si présent (pipeline Evolution → Formatter)
                target_file_match = re.search(r'Fichier cible:\s*(\S+)', mission)
                if target_file_match:
                    candidate = target_file_match.group(1)
                    # PATCH path traversal canonique (audit security 17/05 06:49 — defense-in-depth)
                    from core.file_safety import is_safe_target_path
                    if is_safe_target_path(candidate):
                        formatter_payload["target_file"] = candidate
                    else:
                        logger.warning(f"[ARCHITECT] 🛡️ target_file rejeté (path traversal/hors sandbox) : {candidate}")

                formatter_result = await orchestrator.dispatch_task("formatter", formatter_payload)
                fmt_status = formatter_result.get("status", "error") if isinstance(formatter_result, dict) else "error"
                fmt_detail = formatter_result.get("result", "INCONNU") if isinstance(formatter_result, dict) else str(formatter_result)
                if fmt_status == "success":
                    return {"status": "success", "result": f"FORMATÉ_OK ({fmt_detail})", "details": trigger_msg, "routed_internally": True}
                else:
                    self.log_thought(f"⚠️ Formatter échoué : {fmt_detail}", type="warning")
                    return {"status": "warning", "result": f"FORMATÉ_ECHEC ({fmt_detail})", "details": trigger_msg, "routed_internally": True}

            except Exception as e:
                self.log_thought(f"⚠️ Erreur Technique Relais : {e}", type="error")
                return {"status": "warning", "result": "VALIDÉ MAIS ÉCHEC ROUTAGE"}

        else:
            self.log_thought(f"❌ BLOQUÉ ({risk_level}) : {response[:50]}...", type="error")
            return {"status": "error", "result": response}
