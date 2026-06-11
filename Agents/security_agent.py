import re
import logging
from typing import Dict, Any
from core.base_agent import BaseAgent
from core.prompt_templates import analysis_guardrail, get_project_structure

logger = logging.getLogger("security")


class DivineSecurity(BaseAgent):
    """
    DivineSecurity — Agent d'analyse de sécurité.
    Rôle : Audit, détection de vulnérabilités, recommandations.
    NE génère PAS de code — analyse et recommande.
    """

    def __init__(self):
        super().__init__(
            name="security",
            role="Analyste Sécurité",
            description="Audit de sécurité, détection de vulnérabilités, recommandations"
        )
        self.system_instructions = """Tu es l'analyste sécurité du projet Prométhée.

CONTEXTE : Système multi-agents Python (FastAPI, Ollama local, ChromaDB) sur UN SEUL PC Windows.
Pas de réseau distribué, pas de Kubernetes, pas de cloud infra.

TON RÔLE :
- Analyser le code et les configurations pour détecter des vulnérabilités
- Identifier les risques OWASP (injection, XSS, path traversal, etc.)
- Proposer des corrections concrètes et applicables
- Auditer les dépendances et permissions

RÈGLES STRICTES :
1. Réponds UNIQUEMENT en français
2. NE GÉNÈRE PAS de code — tu ANALYSES et RECOMMANDES
3. Base tes analyses sur des fichiers et fonctions RÉELS du projet
4. Utilise le format structuré ci-dessous pour tes réponses
5. Pas de références à des technologies hors périmètre (Kubernetes, Docker, Kafka, etc.)
6. Pas de jargon militaire, pas de dramatisation

FORMAT DE RÉPONSE :
AUDIT SÉCURITÉ — [sujet]

VULNÉRABILITÉS DÉTECTÉES :
- [V1] Sévérité: CRITIQUE|HAUTE|MOYENNE|BASSE — Description
- [V2] ...

RECOMMANDATIONS :
- [R1] Fichier: [fichier.py] — Action recommandée
- [R2] ...

CONCLUSION : [synthèse en 1-2 phrases]
"""

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Analyse de sécurité."""
        self.log_thought(f"{self.name} analyse la tâche...", type="thought")

        mission = task_payload.get("mission", "Aucune mission définie")
        context = task_payload.get("context", "")

        # Enrichir avec la mémoire RAG
        rag_context = self.recall(mission)

        # Injection dynamique de la structure projet (anti-hallucination de fichiers)
        try:
            project_files = get_project_structure()
            guardrail_suffix = analysis_guardrail(project_files)
        except Exception as e:
            logger.warning(f"[SECURITY] Guardrail désactivé — get_project_structure() a échoué: {e}")
            guardrail_suffix = ""

        prompt = f"""{self.system_instructions}

MISSION : {mission}

CONTEXTE :
{context[:3000]}

MÉMOIRE PERTINENTE :
{rag_context[:1000] if rag_context else "(aucune)"}

Analyse et réponds selon le format structuré ci-dessus.{guardrail_suffix}"""

        response = await self.generate_content(prompt)

        # Mémoriser uniquement les findings significatifs (anti-doublon)
        if response and len(response) > 100:
            # Skip les audits sans vulnérabilité (bruit RAG)
            lower_resp = response.lower()
            if "aucune vulnérabilité" in lower_resp or "rien à signaler" in lower_resp:
                self.log_thought("🔒 Audit clean — pas de stockage RAG (aucune vulnérabilité).", type="info")
            else:
                # Extraire le nom du fichier audité pour un format distinctif
                filename = "inconnu"
                fn_match = re.search(r'fichier\s+(\S+)', mission)
                if fn_match:
                    filename = fn_match.group(1)
                self.remember(f"SECURITY_FINDING [{filename}]: {response[:400]}")

        return {
            "status": "success",
            "result": response,
            "agent": self.name
        }
