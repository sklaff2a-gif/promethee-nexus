import logging
import re
from typing import Dict, Any
from core.base_agent import BaseAgent
from core.prompt_templates import CODE_GENERATION_GUARDRAIL

logger = logging.getLogger("coder")

# Mots-clés hors-sujet fréquemment produits par le LLM local en mode autonome.
# Si le code généré est dominé par ces termes, c'est du bruit — pas une amélioration Prométhée.
_OFFTOPIC_KEYWORDS = {
    "blockchain", "smart contract", "smart_contract", "solidity", "ethereum",
    "web3", "token", "nft", "crypto", "wallet", "0x",
    "trading", "trade", "merchant", "marchand", "order_processor",
    "transaction_manager", "buy_order", "sell_order", "swap",
    "rss", "feedparser", "rss_agent", "rss_processor",
    "flask", "django", "streamlit", "gradio",
    "langchain", "langgraph", "crewai", "autogen",
    "kubernetes", "docker", "terraform", "kafka",
    "openai", "faiss", "torch", "tensorflow",
    "huggingface", "transformers", "pdfplumber", "pypdf",
}

# Seuil : si plus de N mots-clés hors-sujet distincts, on rejette
_OFFTOPIC_THRESHOLD = 2

# Modules existants du projet (injecté dans le prompt pour ancrage)
_PROJECT_CONTEXT = """
PROJET PROMÉTHÉE — Système multi-agents IA autonome.
MODULES EXISTANTS (ne les recrée PAS, améliore-les) :
  - core/orchestrator.py : dispatch multi-agents, kill switch, chaînes de réaction
  - core/base_agent.py : classe mère, RAG (remember/recall), routage Cloud/Local
  - core/router.py : RouterAgent, classification d'intent 3 niveaux
  - core/autonomy_engine.py : routines autonomes, scoring, health checks
  - core/event_bus/bus.py : bus pub/sub en mémoire (singleton)
  - core/summoner.py : chargement dynamique d'agents depuis core/grimoire/
  - core/ci_pipeline.py : tests auto-générés, rollback, mémoire CI/CD
  - core/self_awareness.py : conscience de soi, snapshots, humeur, patterns
  - core/council.py : débats multi-agents avec consensus
  - core/psyche.py : personnalité multi-traits par agent
  - core/vector_store.py : mémoire vectorielle ChromaDB
  - Agents/ : 10 agents (strategist, coder, architect, factory, formatter, researcher, writer, security, infra, evolution)
STACK : Python 3.11, FastAPI, Ollama (LLM local), Google Gemini (Cloud), ChromaDB, WebSocket.
"""


def _count_offtopic(text: str) -> int:
    """Compte le nombre de mots-clés hors-sujet distincts dans le texte."""
    text_lower = text.lower()
    return sum(1 for kw in _OFFTOPIC_KEYWORDS if kw in text_lower)


class DivineCoder(BaseAgent):
    def __init__(self):
        super().__init__(name="coder", role="AI Software Architect", description="Expert Python.")
        self.system_instructions = f"""
Tu es le développeur principal du projet PROMÉTHÉE.
{_PROJECT_CONTEXT}

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
        context_section = f"\nCONTEXTE SUPPLÉMENTAIRE :\n{context}\n" if context else ""
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
            self.log_thought(
                f"⚠️ Code rejeté : {offtopic_count} mots-clés hors-sujet détectés (trading/blockchain/RSS/etc.)",
                type="warning"
            )
            return {
                "status": "warning",
                "result": "R.A.S — code hors périmètre projet (trading/blockchain/RSS détecté).",
                "agent": self.name,
            }

        return {"status": "success", "result": response, "agent": self.name}
