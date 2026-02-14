import logging
import asyncio
from typing import Dict, Any
from core.base_agent import BaseAgent

logger = logging.getLogger("evolution")

# Requêtes de recherche diversifiées et pertinentes pour le projet
_SEARCH_QUERIES = [
    "python asyncio best practices multi-agent system 2026",
    "FastAPI middleware performance optimization 2026",
    "chromadb vector store RAG optimization tips",
    "python event bus pub/sub patterns async",
    "ollama local LLM inference optimization batch",
    "python autonomous agent error recovery patterns",
    "pytest async testing patterns best practices",
    "python logging rotating file handler best practices",
    "python singleton pattern thread safety async",
    "websocket real-time notification system python",
]

# Modules existants du projet (pour le contexte de pertinence)
_PROJECT_MODULES = [
    "core/orchestrator.py — dispatch multi-agents, kill switch, chaînes de réaction",
    "core/base_agent.py — classe mère, RAG (remember/recall), routage Cloud/Local",
    "core/router.py — RouterAgent : classification d'intent 3 niveaux",
    "core/autonomy_engine.py — routines autonomes après inactivité, scoring, health checks",
    "core/event_bus/bus.py — bus pub/sub en mémoire",
    "core/summoner.py — chargement dynamique d'agents depuis core/grimoire/",
    "core/ci_pipeline.py — tests auto-générés, rollback, mémoire CI/CD",
    "core/self_awareness.py — conscience de soi, snapshots, PSYCHE",
    "core/council.py — débats multi-agents avec consensus",
    "Agents/ — 10 agents (strategist, coder, architect, factory, formatter, researcher, writer, security, infra, evolution)",
]

# Mots-clés hors-sujet dans les specs — si la spec en contient trop, c'est du bruit
_SPEC_OFFTOPIC_KEYWORDS = {
    "blockchain", "smart contract", "solidity", "ethereum", "web3",
    "trading", "trade", "merchant", "marchand", "order",
    "rss", "feedparser", "rss_agent",
    "flask", "django", "streamlit",
    "langchain", "langgraph", "crewai", "autogen",
    "kubernetes", "docker", "terraform", "kafka",
    "nft", "crypto", "wallet", "token",
}
_SPEC_OFFTOPIC_THRESHOLD = 2

# Fichiers existants valides (préfixes) — la spec doit cibler un de ces chemins
_VALID_TARGET_PREFIXES = ("core/", "Agents/", "config.py", "main.py")


def _is_spec_offtopic(spec: str) -> bool:
    """Vérifie si la spec contient trop de mots-clés hors-sujet."""
    spec_lower = spec.lower()
    count = sum(1 for kw in _SPEC_OFFTOPIC_KEYWORDS if kw in spec_lower)
    return count >= _SPEC_OFFTOPIC_THRESHOLD


def _spec_targets_existing_file(spec: str) -> bool:
    """Vérifie que la spec mentionne au moins un fichier existant du projet."""
    for prefix in _VALID_TARGET_PREFIXES:
        if prefix in spec:
            return True
    return False


class DivineEvolution(BaseAgent):
    """
    DivineEvolution V5.0 (Darwin Protocol - Project-Aware + Relevance Filter)
    - Rôle : Directeur R&D Autonome.
    - Routine : Veille -> Analyse -> Spécification -> Coder -> Architecte.
    - V5 : Filtre de pertinence dur (mots-clés + ciblage fichier existant).
    """
    _query_index = 0

    def __init__(self):
        super().__init__(name="evolution", role="R&D Director", description="Supervise l'amélioration continue du système.")

    @classmethod
    def _next_search_query(cls) -> str:
        """Sélectionne la prochaine requête de recherche (rotation + jitter)."""
        query = _SEARCH_QUERIES[cls._query_index % len(_SEARCH_QUERIES)]
        cls._query_index += 1
        return query

    def _check_already_explored(self, query: str) -> bool:
        """Vérifie si ce sujet a déjà été exploré récemment via la mémoire RAG."""
        if not self.has_memory:
            return False
        past = self.recall(f"VEILLE DARWIN {query}", limit=1)
        if past and len(past) > 50:
            return True
        return False

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        mission = task_payload.get("mission", "")
        context = task_payload.get("context", "")

        # DÉCLENCHEMENT : MODE VEILLE (Automatique ou Manuel)
        if "[MODE VEILLE]" in mission or "veille" in mission.lower():
            self.log_thought("🧬 Activation du Protocole Darwin (V5 Relevance Filter)...", type="thought")

            try:
                from core.orchestrator import orchestrator

                # --- PHASE 1 : EXPLORATION (Researcher) ---
                search_query = self._next_search_query()

                # Dédup : skip si déjà exploré récemment
                if self._check_already_explored(search_query):
                    self.log_thought(f"💤 Sujet déjà exploré : {search_query}. Skip.", type="info")
                    return {"status": "success", "result": "R.A.S — sujet déjà exploré."}

                self.log_thought(f"🔭 Phase 1 : Lancement Researcher ({search_query})...", type="info")

                research_response = await orchestrator.dispatch_task("researcher", {
                    "mission": f"VEILLE TECHNO: Trouve une technique Python avancée ou une librairie récente ({search_query}) utile pour un système d'agents autonomes. Sois concis et technique.",
                    "context": "Focus: Performance, Stabilité, Architecture."
                })

                research_data = research_response.get("result", "")
                if not research_data:
                    return {"status": "warning", "result": "Recherche infructueuse."}

                # Mémoriser la veille pour éviter les doublons futurs
                if self.has_memory:
                    self.remember(
                        f"VEILLE DARWIN {search_query}\n{research_data[:500]}",
                        {"source": "darwin_protocol", "query": search_query}
                    )

                # --- PHASE 2 : ANALYSE & SPÉCIFICATION (Cerveau) ---
                self.log_thought("🧠 Phase 2 : Analyse de la pertinence...", type="thought")

                modules_list = "\n".join(f"  - {m}" for m in _PROJECT_MODULES)
                decision_prompt = (
                    f"Tu es le Directeur R&D du projet PROMÉTHÉE (système multi-agents IA autonome).\n"
                    f"MODULES EXISTANTS DU PROJET :\n{modules_list}\n\n"
                    f"Voici une veille technologique :\n{research_data[:2000]}\n\n"
                    f"ANALYSE : Est-ce une amélioration CONCRÈTE et APPLICABLE à un module existant de PROMÉTHÉE ?\n"
                    f"ATTENTION : Ne propose PAS de nouveau module générique (trading, commerce, smart contracts, RSS, etc.).\n"
                    f"La spécification doit cibler un fichier EXISTANT (core/*.py ou Agents/*.py) et proposer une modification précise.\n\n"
                    f"SI OUI : Rédige une SPÉCIFICATION TECHNIQUE pour le Coder :\n"
                    f"  - Fichier cible existant (ex: core/orchestrator.py)\n"
                    f"  - Modification précise (quelle méthode améliorer, quel pattern appliquer)\n"
                    f"SI NON : Réponds juste 'R.A.S'."
                )
                spec_response = await self.generate_content(decision_prompt)

                if "R.A.S" in spec_response:
                    self.log_thought("💤 Découverte non pertinente. Fin de cycle.", type="info")
                    return {"status": "success", "result": "R.A.S"}

                # --- FILTRE DE PERTINENCE DUR ---
                # Vérification 1 : mots-clés hors-sujet
                if _is_spec_offtopic(spec_response):
                    self.log_thought(
                        "🚫 Spec rejetée : contient des mots-clés hors-périmètre (trading/blockchain/RSS/etc.).",
                        type="warning"
                    )
                    return {"status": "success", "result": "R.A.S — spec hors périmètre projet."}

                # Vérification 2 : la spec doit cibler un fichier existant
                if not _spec_targets_existing_file(spec_response):
                    self.log_thought(
                        "🚫 Spec rejetée : ne cible aucun fichier existant (core/*.py ou Agents/*.py).",
                        type="warning"
                    )
                    return {"status": "success", "result": "R.A.S — spec ne cible aucun module existant."}

                # --- PHASE 3 : MATÉRIALISATION (Coder) ---
                self.log_thought("🛠️ Phase 3 : Délégation au Coder...", type="info")

                coder_response = await orchestrator.dispatch_task("coder", {
                    "mission": (
                        "Génère le code complet correspondant à cette spécification. "
                        "Le code DOIT modifier un fichier EXISTANT du projet PROMÉTHÉE. "
                        "Donne UNIQUEMENT le code Python."
                    ),
                    "context": f"EVOLUTION_PIPELINE\nSPÉCIFICATION :\n{spec_response}"
                })

                generated_code = coder_response.get("result", "")
                if not generated_code or "R.A.S" in generated_code:
                    self.log_thought("💤 Coder n'a rien produit de pertinent.", type="info")
                    return {"status": "success", "result": "R.A.S — code non pertinent."}

                # --- PHASE 4 : DÉPLOIEMENT SÉCURISÉ (Architecte) ---
                self.log_thought("🛡️ Phase 4 : Soumission à l'Architecte...", type="info")

                architect_response = await orchestrator.dispatch_task("architect", {
                    "mission": "Analyse ce nouveau module R&D. S'il est sûr, valide-le pour déploiement (Envoi Formatter).",
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
