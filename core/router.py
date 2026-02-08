import re
import os
import requests
import json
import logging
from typing import Optional

# Configuration minimale pour l'appel LLM autonome du routeur
try:
    from config import Config
except ImportError:
    class Config: 
        OLLAMA_URL = "http://localhost:11434/api/generate"
        AGENT_SPECIFIC_LOCAL_MODELS = {}

class RouterAgent:
    """
    RouterAgent V2.2 - Blind Trust
    - Niveau 0 : Adressage Direct (Syntaxe 'Nom: Action') -> Passe tout (Grimoire compatible).
    - Niveau 1 : Détection par Mots-clés (Réflexe instantané sur liste connue).
    - Niveau 2 : Analyse Sémantique LLM (Réflexion en cas d'ambiguïté).
    """
    
    @staticmethod
    def classify_intent(mission: str) -> str:
        m_low = mission.strip().lower()
        
        # --- NIVEAU 0 : BLIND TRUST (Adressage Direct) ---
        # Si l'utilisateur utilise la syntaxe "Agent: ...", on obéit aveuglément.
        # Cela permet d'appeler des agents éphémères (Grimoire) inconnus de la liste statique.
        if ':' in mission:
            # On prend tout ce qu'il y a avant les deux points
            potential_agent = mission.split(':')[0].strip()
            
            # Sécurité basique : Un nom d'agent ne doit pas contenir d'espace
            # Ex: "math_wizard" (OK) vs "Calcul le" (KO - ignoré)
            if ' ' not in potential_agent:
                return potential_agent.lower()

        # --- NIVEAU 1 : SYSTÈME RÉFLEXE (Règles strictes sur Agents Connus) ---
        
        # 1. Priorité Mots-clés (Nom de l'agent en début de phrase sans :)
        agents = ["factory", "coder", "researcher", "architect", "strategist", 
                  "writer", "infra", "security", "evolution", "formatter"]
        
        first_word = m_low.split(' ')[0].strip()
        if first_word in agents:
            return first_word
        
        # 2. Déduction Contextuelle (Mots-clés forts)
        if any(x in m_low for x in ["cpu", "ram", "gpu", "vram", "status", "santé", "check system"]): return "infra"
        if any(x in m_low for x in ["scan", "dropzone", "ingest", "archive", "lecture", "lire fichier", "recherche web", "veille"]): return "researcher"
        if any(x in m_low for x in ["reset", "nuke", "crée dossier", "supprime", "tree", "arborescence"]): return "factory"
        if any(x in m_low for x in ["code", "script", "fonction", "python", "bug", "dev", "class ", "def "]): return "coder"
        if any(x in m_low for x in ["évolue", "améliore", "optimise", "mutation", "upgrade"]): return "evolution"
        if any(x in m_low for x in ["plan", "structure", "audit", "valide", "architecture"]): return "architect"
        if any(x in m_low for x in ["secu", "faille", "attack", "protect"]): return "security"
        if any(x in m_low for x in ["rédige", "écris", "article", "tweet", "post", "seo"]): return "writer"
        if any(x in m_low for x in ["format", "clean", "nettoie", "indente", "syntaxe"]): return "formatter"

        # --- NIVEAU 2 : AUTO-RÉFLEXION (Appel LLM Local) ---
        # Si aucune règle ne matche, on demande au LLM de trancher
        print(f"🤔 ROUTER: Ambiguïté détectée sur '{mission[:30]}...'. Analyse Sémantique en cours...")
        return RouterAgent._semantic_reflection(mission)

    @staticmethod
    def _semantic_reflection(mission: str) -> str:
        """Demande à un petit modèle local de classer l'intention."""
        try:
            # On utilise un petit modèle rapide pour le routing
            model = "gemma3:12b" 
            
            # Prompt mis à jour avec la liste exacte des agents actifs
            prompt = (
                f"Tu es le Routeur du système Nexus. Classifie cette mission vers l'agent le plus approprié.\n"
                f"AGENTS DISPONIBLES : [coder, researcher, strategist, writer, architect, infra, security, evolution, factory, formatter]\n"
                f"MISSION : \"{mission}\"\n"
                f"RÈGLE : Réponds UNIQUEMENT par le nom de l'agent en minuscule. Rien d'autre."
            )
            
            payload = {
                "model": model, 
                "prompt": prompt, 
                "stream": False,
                "options": {"temperature": 0.1} # Très déterministe
            }
            
            url = getattr(Config, "OLLAMA_URL", "http://localhost:11434/api/generate")
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                choice = response.json().get("response", "").strip().lower()
                # Nettoyage au cas où le LLM soit bavard
                valid_agents = ["coder", "researcher", "strategist", "writer", "architect", "infra", "security", "evolution", "factory", "formatter"]
                for agent in valid_agents:
                    if agent in choice:
                        print(f"💡 ROUTER: Décision IA -> {agent.upper()}")
                        return agent
            
            print("⚠️ ROUTER: Echec réflexion IA, repli sur STRATEGIST.")
            return "strategist"
            
        except Exception as e:
            print(f"❌ ROUTER ERROR: {e}")
            return "strategist"