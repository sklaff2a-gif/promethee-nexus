import asyncio
import json
import logging
import re
from typing import Dict, Any, List
from core.base_agent import BaseAgent
from core.capabilities.web_surfer import WebSurfer
from core.prompt_templates import AUTONOMY_GUARDRAIL
from core.decision_log import log_decision

logger = logging.getLogger("researcher")

# Seuil critique de longueur de requête (garde-fou terminal — circuit breaker
# du moteur de recherche). Une vraie requête de sujet fait 20-80 chars ; au-dela
# de ce seuil, on a forcement embarque du prompt parasite -> on tronque.
_MAX_QUERY_CHARS = 200

class DivineResearcher(BaseAgent):
    """
    DivineResearcher V14.0 (Hybrid Surfer Edition)
    - Utilise WebSurfer pour la recherche (Google/DDG)
    - Utilise KnowledgeIngestor pour la lecture locale
    """
    def __init__(self):
        super().__init__(
            name="researcher",
            role="Analyste de Données & Veilleur Stratégique",
            description="Scanne le web via Google/DDG et les documents locaux pour extraire du savoir."
        )
        self.surfer = WebSurfer()

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        mission = task_payload.get("mission", "")
        context = task_payload.get("context", "")
        self.log_thought(f"🔍 Mission reçue : {mission[:50]}...", type="thought")

        # --- 1. MODE INGESTION LOCALE (Dropzone) ---
        # Guard : si on est appelé PAR le pipeline (context DROPZONE_ANALYSIS),
        # ne pas re-déclencher le pipeline (sinon boucle infinie).
        # On ne cherche les mots-clés que dans les 200 premiers chars (pas le contenu des fichiers).
        if not context.startswith("DROPZONE_ANALYSIS"):
            mission_header = mission[:200].lower()
            if "dropzone" in mission_header:
                return await self._run_ingestion_routine()

        # --- 2. MODE RECHERCHE WEB (Par défaut ou explicite) ---
        # Si la mission contient des mots de recherche OU si aucune autre action n'est détectée
        query = self._extract_search_query(mission)

        self.log_thought(f"🌍 Lancement WebSurfer Hybride : {query[:30]}...", "info")
        
        # Appel à l'outil dans un executor (search() est synchrone/bloquant)
        loop = asyncio.get_running_loop()
        web_results = await loop.run_in_executor(None, lambda: self.surfer.search(query, max_results=5))
        
        self.log_thought("✅ Résultats récupérés. Analyse et Synthèse...", "info")
        
        # On demande au cerveau de l'agent de synthétiser les résultats bruts
        synthesis = await self.generate_content(
            f"Tu es un analyste expert. Voici des résultats de recherche bruts concernant '{query}'.\n"
            f"Fais-en une synthèse structurée et exploitable pour un système IA.\n"
            f"TERMINE OBLIGATOIREMENT par une ligne 'PRINCIPE: <une regle actionnable en "
            f"1-2 phrases>' — la lecon GENERALISABLE de cette veille (ce qui change ta facon "
            f"de traiter les problemes futurs), pas un resume.\n\n"
            f"[DONNÉES WEB]:\n{web_results[:4000]}"
            f"{AUTONOMY_GUARDRAIL}"
        )

        # GATE DU PRINCIPE (atelier RESEARCH 10/06, design CO-SIGNE par Promethee :
        # « transformee en principe actionnable, sinon bruit »). Avant : remember()
        # inconditionnel du bloc entier -> 17% de la memoire canonique etait de la VEILLE
        # brute qui noyait les lecons. Desormais : on ne memorise QUE si un PRINCIPE est
        # extrait, et le principe passe EN TETE (la regle d'abord, le contexte ensuite).
        principe = self._extraire_principe(synthesis)
        if principe:
            self.remember(
                text=f"PRINCIPE (veille '{query}'): {principe}\nContexte: {synthesis[:300]}",
                metadata={"source": "web_search", "query": query},
            )
        else:
            logger.info(f"[RESEARCHER] Veille sans PRINCIPE -> non memorisee "
                        f"(gate du principe): {query[:60]}")

        return {"status": "success", "result": synthesis}

    @staticmethod
    def _extraire_principe(synthesis: str) -> str:
        """Extrait la regle actionnable de la section PRINCIPE: (gate du principe).
        Retourne '' si absente ou trop courte pour etre une regle (anti-placebo)."""
        if not synthesis:
            return ""
        m = re.search(r"PRINCIPE\s*:?\s*\**\s*(.+)", synthesis,
                      re.IGNORECASE | re.DOTALL)
        if not m:
            return ""
        principe = m.group(1).strip().strip("*").strip()
        # une regle tient en 1-2 phrases : tronquer proprement, refuser le squelette
        principe = principe[:400]
        return principe if len(principe) >= 20 else ""

    # Préfixes d'instruction connus à retirer en mode non-scolaire.
    _QUERY_PREFIXES = (
        "researcher:", "scanne le web pour", "cherche", "recherche",
        "[school_slot:", "veille sur", "fais une veille sur",
    )

    def _extract_search_query(self, mission: str) -> str:
        """Extrait une requête de recherche PURE depuis une mission (cascade 3 niveaux).

        Remplace la blacklist fragile (Fix D 2026-04-16) qui ne reconnaissait plus
        le format école actuel (PROTOCOLE_SCOLAIRE / SUJET DU JOUR), laissant partir
        2000+ chars de prompt comme query -> SERP vide -> hallucination par carence
        (incident RESEARCH 2026-05-20 01h07).

        Cascade :
          1. SCOLAIRE : si la balise "SUJET DU JOUR (PRIORITE ABSOLUE)" est présente,
             on capture le bloc entre cette balise et le séparateur "====" / double saut.
          2. NON-SCOLAIRE : sinon, on retire les préfixes d'instruction connus et on
             garde la PREMIÈRE LIGNE NON-VIDE (= le sujet dans les missions libres).
          3. GARDE-FOU TERMINAL : si la query dépasse _MAX_QUERY_CHARS, on tronque
             (circuit breaker du moteur de recherche). query vide -> trace + "".
        """
        if not mission or not mission.strip():
            log_decision(
                module="researcher_agent",
                function="_extract_search_query",
                reason="query_empty",
                context={"cause": "mission_vide"},
            )
            return ""

        query = None

        # --- Niveau 1 : extraction scolaire (whitelist regex) ---
        m = re.search(
            r"SUJET DU JOUR\s*\(PRIORITE ABSOLUE\)\s*:\s*\n(.+?)(?:\n\s*=+|\n\s*\n)",
            mission,
            re.DOTALL | re.IGNORECASE,
        )
        if m:
            query = m.group(1).strip()

        # --- Niveau 2 : fallback non-scolaire (première ligne non-vide nettoyée) ---
        if not query:
            cleaned = mission.strip()
            low = cleaned.lower()
            for prefix in self._QUERY_PREFIXES:
                if low.startswith(prefix):
                    cleaned = cleaned[len(prefix):].strip()
                    low = cleaned.lower()
            # Première ligne non-vide
            for line in cleaned.splitlines():
                if line.strip():
                    query = line.strip()
                    break
            if not query:
                query = cleaned.strip()

        # --- Niveau 3 : garde-fou terminal (circuit breaker longueur) ---
        if query and len(query) > _MAX_QUERY_CHARS:
            log_decision(
                module="researcher_agent",
                function="_extract_search_query",
                reason="query_too_long",
                context={"original_len": len(query), "truncated_to": _MAX_QUERY_CHARS},
            )
            query = query[:_MAX_QUERY_CHARS].strip()

        if not query:
            log_decision(
                module="researcher_agent",
                function="_extract_search_query",
                reason="query_empty",
                context={"cause": "extraction_vide"},
            )
            return ""

        return query

    async def _run_ingestion_routine(self):
        """Sous-routine pour analyser les fichiers de la dropzone via le pipeline intelligent."""
        self.log_thought("📂 Lancement pipeline Dropzone 2.0...", type="action")

        from core.dropzone_pipeline import DropzonePipeline
        from core.orchestrator import orchestrator

        pipeline = DropzonePipeline(orchestrator)
        result = await pipeline.run()

        if result["status"] == "success":
            summary = result["manifest"]
            return {
                "status": "success",
                "result": (
                    f"INGESTION DROPZONE 2.0 TERMINÉE\n"
                    f"Projets analysés: {summary['total_projects']}\n"
                    f"Fichiers uniques: {summary['unique_files']}\n"
                    f"Doublons ignorés: {summary['duplicates']}\n"
                    f"Analyses produites: {result['analyses']}"
                )
            }
        return {"status": "warning", "result": "Pipeline Dropzone : aucun résultat."}