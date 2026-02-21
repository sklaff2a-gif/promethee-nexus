"""
HallucinationDoctor — Diagnostic d'hallucinations LLM et correction de prompts pour retry.

Grimoire hybride : fonctions deterministes module-level + agent BaseAgent.
V1 : 100% deterministe (zero appel LLM). Le LLM pourra etre ajoute plus tard.
"""
import json
import logging
from typing import Dict, Any
from core.base_agent import BaseAgent

logger = logging.getLogger("hallucination_doctor")

# Modules autorises dans le projet Promethee
_ALLOWED_MODULES = [
    "os", "sys", "re", "ast", "json", "time", "logging", "asyncio", "uuid",
    "typing", "collections", "datetime", "pathlib", "hashlib", "math",
    "httpx", "fastapi", "uvicorn", "websockets", "pydantic",
    "chromadb", "psutil", "importlib", "inspect", "pkgutil",
    "core", "Agents", "config",
]

# Modules aliens connus (hallucinations frequentes)
_ALIEN_MODULES = [
    "django", "flask", "streamlit", "gradio", "pygame", "tkinter",
    "langchain", "langgraph", "crewai", "autogen",
    "tensorflow", "torch", "keras", "sklearn",
    "pandas", "numpy", "scipy",
    "kubernetes", "docker", "terraform", "kafka",
    "web3", "solidity", "brownie",
    "sqlalchemy", "peewee", "mongoengine",
    "openai", "anthropic", "cohere",
    "faiss", "pinecone", "weaviate", "qdrant",
]


def diagnose(hallucination_type: str, context: dict) -> dict:
    """Diagnostic 100% deterministe par type d'hallucination.

    Args:
        hallucination_type: "alien_imports" | "non_structural" | "truncation" | "offtopic"
        context: dict avec les details du cas (aliens, code, spec, ratio, etc.)

    Returns:
        dict avec type, details, severity, suggestion
    """
    if hallucination_type == "alien_imports":
        aliens = context.get("aliens", [])
        return {
            "type": "alien_imports",
            "details": f"Imports interdits detectes: {', '.join(aliens)}",
            "severity": "high",
            "suggestion": f"Retirer les imports {', '.join(aliens)} et utiliser uniquement les modules du projet",
            "aliens": aliens,
        }

    elif hallucination_type == "non_structural":
        return {
            "type": "non_structural",
            "details": "Code genere sans def/class/import — pas une vraie amelioration",
            "severity": "high",
            "suggestion": "Le code DOIT contenir au moins un def, class ou import",
        }

    elif hallucination_type == "truncation":
        ratio = context.get("ratio", 0)
        source_len = context.get("source_length", 0)
        generated_len = context.get("generated_length", 0)
        return {
            "type": "truncation",
            "details": f"Code tronque: {generated_len} chars = {ratio:.0%} du source ({source_len} chars)",
            "severity": "critical",
            "suggestion": "Pas de retry possible — le modele ne peut pas generer assez de tokens",
        }

    elif hallucination_type == "offtopic":
        keywords = context.get("keywords_found", [])
        return {
            "type": "offtopic",
            "details": f"Mots-cles hors-sujet detectes: {', '.join(keywords) if keywords else 'inconnus'}",
            "severity": "medium",
            "suggestion": "Recentrer le prompt sur le projet Promethee",
            "keywords": keywords,
        }

    return {
        "type": "unknown",
        "details": f"Type d'hallucination inconnu: {hallucination_type}",
        "severity": "low",
        "suggestion": "",
    }


def build_corrected_prompt(diagnosis: dict, original_context: dict) -> str:
    """Construit un prompt corrige avec guardrails renforces specifiques au type.

    Retourne "" si pas de retry possible (truncation, unknown).
    """
    diag_type = diagnosis.get("type", "unknown")

    if diag_type == "truncation" or diag_type == "unknown":
        return ""

    spec_name = original_context.get("spec_name", "")
    spec_desc = original_context.get("spec_description", "")
    target_file = original_context.get("target_file", "")
    source_code = original_context.get("source_code", "")[:3000]
    mission = original_context.get("mission", "")

    if diag_type == "alien_imports":
        aliens = diagnosis.get("aliens", [])
        allowed_str = ", ".join(_ALLOWED_MODULES[:15])
        nl = "\n"
        file_line = f"FICHIER CIBLE : {target_file}" if target_file else ""
        spec_line = f"SPEC : {spec_name} — {spec_desc}" if spec_name else f"MISSION : {mission}"
        source_block = f"CODE SOURCE ACTUEL :{nl}{source_code}{nl}{nl}" if source_code else ""
        return (
            f"CORRECTION REQUISE — Le code precedent contenait des imports INTERDITS.\n"
            f"IMPORTS INTERDITS (NE PAS UTILISER) : {', '.join(aliens)}\n"
            f"MODULES AUTORISES : {allowed_str}\n\n"
            f"PROJET : Promethee (multi-agents IA, Python/FastAPI/Ollama, UN SEUL PC Windows)\n"
            f"{file_line}\n"
            f"{spec_line}\n\n"
            f"{source_block}"
            f"RETOURNE LE FICHIER COMPLET MODIFIE. UNIQUEMENT du code Python. "
            f"PAS de django, flask, langchain, torch, pandas, numpy, openai."
        )

    elif diag_type == "non_structural":
        nl = "\n"
        file_line = f"FICHIER CIBLE : {target_file}" if target_file else ""
        spec_line = f"SPEC : {spec_name} — {spec_desc}" if spec_name else f"MISSION : {mission}"
        source_block = f"CODE SOURCE ACTUEL :{nl}{source_code}{nl}{nl}" if source_code else ""
        return (
            f"CORRECTION REQUISE — Le code precedent etait NON-STRUCTUREL (pas de def/class/import).\n"
            f"OBLIGATOIRE : le code DOIT contenir au moins :\n"
            f"- Un `import` valide\n"
            f"- Une `def` ou `class`\n\n"
            f"PROJET : Promethee (multi-agents IA, Python/FastAPI/Ollama, UN SEUL PC Windows)\n"
            f"{file_line}\n"
            f"{spec_line}\n\n"
            f"{source_block}"
            f"RETOURNE LE FICHIER COMPLET MODIFIE avec de vraies fonctions/classes Python."
        )

    elif diag_type == "offtopic":
        keywords = diagnosis.get("keywords", [])
        mission_line = f"MISSION : {mission}" if mission else ""
        return (
            f"CORRECTION REQUISE — Le code precedent contenait du contenu HORS-SUJET.\n"
            f"MOTS-CLES INTERDITS : {', '.join(keywords)}\n\n"
            f"CONTEXTE : Projet Promethee — systeme multi-agents IA autonome.\n"
            f"Stack : Python 3.11, FastAPI, Ollama (LLM local), Gemini (Cloud), ChromaDB, WebSocket.\n"
            f"{mission_line}\n\n"
            f"Le code doit etre 100% pertinent pour Promethee. "
            f"PAS de trading, blockchain, RSS, e-commerce, smart contracts."
        )

    return ""


def parse_doctor_verdict(response: str) -> str:
    """Parse retry/abandon depuis la reponse du doctor."""
    if not response or not isinstance(response, dict):
        return "abandon"
    action = response.get("action") or response.get("verdict", "abandon")
    if action in ("retry", "abandon", "skip"):
        return action
    return "abandon"


class HallucinationDoctor(BaseAgent):
    """Agent Grimoire : diagnostic d'hallucinations LLM et correction de prompts."""

    def __init__(self):
        super().__init__(
            name="hallucination_doctor",
            role="Diagnosticien d'hallucinations LLM",
            description="Diagnostic d'hallucinations LLM et correction de prompts pour retry"
        )

    async def process_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        mission = task_payload.get("mission", "")
        context_raw = task_payload.get("context", "{}")

        # 1. Deserialiser le contexte JSON
        try:
            context = json.loads(context_raw) if isinstance(context_raw, str) else context_raw
        except (json.JSONDecodeError, TypeError):
            context = {}

        hallucination_type = context.get("type", "unknown")

        # 2. Diagnostic deterministe
        diag = diagnose(hallucination_type, context)

        # 3. Si truncation ou unknown -> verdict="abandon"
        if diag["severity"] == "critical" or diag["type"] == "unknown":
            return {
                "status": "success",
                "result": f"Diagnostic: {diag['details']}",
                "action": "abandon",
                "verdict": "abandon",
                "diagnosis": diag,
                "corrected_prompt": "",
            }

        # 4. Construire le prompt corrige
        corrected = build_corrected_prompt(diag, context)

        # 5. Si prompt vide -> verdict="abandon"
        if not corrected:
            return {
                "status": "success",
                "result": f"Diagnostic: {diag['details']} — pas de correction possible",
                "action": "abandon",
                "verdict": "abandon",
                "diagnosis": diag,
                "corrected_prompt": "",
            }

        # 6. Retourner verdict retry avec le prompt corrige
        return {
            "status": "success",
            "result": f"Diagnostic: {diag['details']} — prompt corrige genere",
            "action": "retry",
            "verdict": "retry",
            "diagnosis": diag,
            "corrected_prompt": corrected,
        }
