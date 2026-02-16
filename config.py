import os
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(".env")
if not env_path.exists():
    env_path = Path("../.env")
load_dotenv(dotenv_path=env_path)

class Config:
    APP_NAME = "Prométhée New Age"
    VERSION = "14.0.0 (Multi-Model Matrix)"
    
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    
    # --- CATALOGUE DES MODÈLES DISPONIBLES (Selon ton audit 2026) ---
    MODELS = {
        "FAST": "models/gemini-2.5-flash",          # Le standard rapide
        "SMART": "models/gemini-2.5-pro",           # Le cerveau (Raisonnement)
        "STABLE": "models/gemini-2.0-flash",        # Le vieux fiable (Fallback)
        "RESEARCH": "models/deep-research-pro-preview-12-2025", # Spécialiste Web
        "VISION": "models/gemini-2.5-flash-image",  # Spécialiste Image
        "CODE": "models/gemma-3-27b-it",            # Spécialiste Code (via API)
        "AGENTIC": "models/gemini-2.5-computer-use-preview-10-2025" # Spécialiste Actions
    }

    # --- STRATÉGIE D'ATTRIBUTION (Cascade de repli) ---
    # Chaque agent reçoit une liste : [Choix 1 (Idéal), Choix 2 (Rapide), Choix 3 (Secours)]
    AGENT_MODEL_ROUTING = {
        # Les Cerveaux (Besoin de QI élevé)
        "strategist": [MODELS["SMART"], MODELS["FAST"], MODELS["STABLE"]],
        "architect":  [MODELS["SMART"], MODELS["FAST"], MODELS["STABLE"]],
        
        # Les Spécialistes
        "researcher": [MODELS["RESEARCH"], MODELS["SMART"], MODELS["FAST"]], # Utilise Deep Research !
        "coder":      [MODELS["CODE"], MODELS["SMART"], MODELS["FAST"]],     # Utilise Gemma 3 27B !
        "writer":     [MODELS["SMART"], MODELS["FAST"], MODELS["STABLE"]],
        
        # Les Ouvriers (Besoin de vitesse)
        "factory":    [MODELS["FAST"], MODELS["STABLE"]],
        "infra":      [MODELS["FAST"], MODELS["STABLE"]],
        "security":   [MODELS["FAST"], MODELS["STABLE"]],
        "evolution":  [MODELS["AGENTIC"], MODELS["SMART"], MODELS["FAST"]], # Test l'agentic
        
        # Par défaut (Fallback global)
        "default":    [MODELS["FAST"], MODELS["STABLE"]]
    }

    # Configuration Locale (Ollama) inchangée
    OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
    AGENT_SPECIFIC_LOCAL_MODELS = {
        "coder": "qwen3-coder:30b",
        "factory": "qwen3:8b",
        "infra": "qwen3:8b",
        "security": "deepseek-r1:8b",
        "writer": "gemma3:12b",
        "strategist": "gpt-oss:20b",
        "architect": "gemma3:12b",
        "researcher": "qwen3-vl:8b"
    }
    
    PROJECT_ID = os.getenv("PROJECT_ID", "default")
    CHROMA_PERSIST_PATH = os.getenv("CHROMA_DB_PATH", "./memory/chroma_db")

    if not GOOGLE_API_KEY:
        print(f"⚠️ [CONFIG] Mode 100% LOCAL activé.")
    else:
        print(f"✅ [CONFIG] Matrice Multi-Modèles : ACTIVE ({len(MODELS)} modèles chargés).")