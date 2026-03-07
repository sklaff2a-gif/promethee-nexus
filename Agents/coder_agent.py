import logging
from typing import Dict, Any
from core.base_agent import BaseAgent
from config import Config
from core.prompt_templates import CODE_GENERATION_GUARDRAIL, FORBIDDEN_FRAMEWORKS, get_project_structure

logger = logging.getLogger("coder")

# Mots-clés hors-sujet fréquemment produits par le LLM local en mode autonome.
# Base : FORBIDDEN_FRAMEWORKS + mots-clés spécifiques trading/blockchain/RSS
_OFFTOPIC_KEYWORDS = FORBIDDEN_FRAMEWORKS | {
    "smart contract", "smart_contract", "ethereum",
    "token", "nft", "crypto", "wallet", "0x",
    "trading", "trade", "merchant", "marchand", "order_processor",
    "transaction_manager", "buy_order", "sell_order", "swap",
    "rss", "feedparser", "rss_agent", "rss_processor",
    "huggingface", "transformers", "pdfplumber", "pypdf",
}

# Seuil : si plus de N mots-clés hors-sujet distincts, on rejette
_OFFTOPIC_THRESHOLD = 2



def _count_offtopic(text: str) -> int:
    """Compte le nombre de mots-clés hors-sujet distincts dans le texte."""
    text_lower = text.lower()
    return sum(1 for kw in _OFFTOPIC_KEYWORDS if kw in text_lower)


class DivineCoder(BaseAgent):
    def __init__(self):
        super().__init__(name="coder", role="AI Software Architect", description="Expert Python.")
        self.system_instructions = f"""
Tu es le développeur principal du projet PROMÉTHÉE.
PROJET PROMÉTHÉE — Système multi-agents IA autonome.
{get_project_structure()}
STACK : Python 3.11, FastAPI, Ollama (LLM local), Google Gemini (Cloud), ChromaDB, WebSocket.

RÈGLES ABSOLUES :
1. Tu ne génères QUE du code Python pertinent pour PROMÉTHÉE.
2. Tu ne crées JAMAIS de code de trading, blockchain, smart contracts, RSS, e-commerce.
3. Quand tu reçois une spécification, le code DOIT cibler un fichier EXISTANT du projet.
4. Si la mission ne te semble pas pertinente pour Prométhée, réponds "R.A.S — hors périmètre."
5. AVANT DE CODER, lis les "LEÇONS DU PASSÉ" ci-dessous et évite de refaire les mêmes erreurs.
"""

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        mission = task_payload.get("mission", "")
        context = task_payload.get("context", "")

        # 1. CONSULTER LA MÉMOIRE
        self.log_thought("Consultation des archives techniques...", type="thought")
        past_lessons = self.recall(mission, limit=3)

        if past_lessons:
            self.log_thought(f"J'ai trouvé des leçons pertinentes : {past_lessons[:50]}...", type="info")
        else:
            self.log_thought("Aucune leçon précédente trouvée. J'innove.", type="info")

        # 2. INTÉGRER LES LEÇONS AU PROMPT
        # M04: tronquer le context AVANT le guardrail pour que celui-ci reste visible au LLM
        _max_ctx = Config.get_max_content_chars("coder", prompt_overhead=3000)
        truncated_context = context[:_max_ctx] if context else ""
        context_section = f"\nCONTEXTE SUPPLÉMENTAIRE :\n{truncated_context}\n" if truncated_context else ""
        full_prompt = f"""
        {self.system_instructions}

        --- LEÇONS DU PASSÉ (À RESPECTER IMPÉRATIVEMENT) ---
        {past_lessons}
        ----------------------------------------------------
        {context_section}
        MISSION ACTUELLE : {mission}
        {CODE_GENERATION_GUARDRAIL}
        """

        response = await self.generate_content(full_prompt)

        # 3. FILTRE DE PERTINENCE POST-GÉNÉRATION
        offtopic_count = _count_offtopic(response)
        if offtopic_count >= _OFFTOPIC_THRESHOLD:
            # Tentative de correction via hallucination_doctor
            try:
                help_response = await self._request_help("hallucination", {
                    "type": "offtopic", "mission": mission,
                    "keywords_found": [kw for kw in _OFFTOPIC_KEYWORDS if kw in response.lower()],
                })
                if help_response.get("action") == "retry" and help_response.get("corrected_prompt"):
                    self.log_thought("🔄 Retry doctor (offtopic)...", type="info")
                    retry_response = await self.generate_content(help_response["corrected_prompt"])
                    retry_count = _count_offtopic(retry_response)
                    if retry_count < _OFFTOPIC_THRESHOLD:
                        self.log_thought("✅ Doctor retry reussi (offtopic corrige)", type="info")
                        return {"status": "success", "result": retry_response, "agent": self.name}
            except Exception as e:
                logger.warning(f"Doctor retry echoue: {e}")

            self.log_thought(
                f"⚠️ Code rejeté : {offtopic_count} mots-clés hors-sujet détectés (trading/blockchain/RSS/etc.)",
                type="warning"
            )
            try:
                from core.event_bus.bus import bus
                await bus.publish("HALLUCINATION_DETECTED", {"agent": self.name, "type": "offtopic", "intent": mission[:80]})
            except Exception:
                pass
            return {
                "status": "warning",
                "result": "R.A.S — code hors périmètre projet (trading/blockchain/RSS détecté).",
                "agent": self.name,
            }

        return {"status": "success", "result": response, "agent": self.name}
