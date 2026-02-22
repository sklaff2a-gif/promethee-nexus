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
    
    # --- CATALOGUE DES MODÈLES DISPONIBLES (Tier 1 Google AI Pro) ---
    MODELS = {
        "FAST": "models/gemini-2.5-flash",      # Rapide + économique (gros du trafic)
        "SMART": "models/gemini-2.5-pro",        # Puissant (à économiser, coûte cher)
    }

    # --- Limites RPM Tier 1 (valeurs conservatrices à 80% du max) ---
    CLOUD_RPM_LIMITS = {
        "models/gemini-2.5-pro": 50,       # API: 60-150, on prend 50 (marge)
        "models/gemini-2.5-flash": 250,    # API: 300-500, on prend 250 (marge)
    }
    CLOUD_RPM_DEFAULT = 30  # Pour tout modèle inconnu

    # Budget journalier par modèle (protection $10/mois)
    CLOUD_DAILY_LIMITS = {
        "models/gemini-2.5-pro": 100,      # Pro: économiser (coûte ~10x plus que Flash)
        "models/gemini-2.5-flash": 2000,   # Flash: quasi-illimité dans le budget
    }

    # --- STRATÉGIE D'ATTRIBUTION (Cascade de repli) ---
    AGENT_MODEL_ROUTING = {
        # Cerveaux → Pro en premier, Flash en fallback
        "strategist": [MODELS["SMART"], MODELS["FAST"]],
        "architect":  [MODELS["SMART"], MODELS["FAST"]],
        "writer":     [MODELS["SMART"], MODELS["FAST"]],
        # Spécialistes → Flash par défaut, Pro en escalade (économie budget)
        "researcher": [MODELS["FAST"], MODELS["SMART"]],
        "coder":      [MODELS["FAST"], MODELS["SMART"]],
        "evolution":  [MODELS["SMART"], MODELS["FAST"]],
        # Ouvriers → Flash uniquement (pas besoin de Pro)
        "factory":    [MODELS["FAST"]],
        "infra":      [MODELS["FAST"]],
        "security":   [MODELS["FAST"]],
        # Fallback global
        "default":    [MODELS["FAST"]],
    }

    # Configuration Locale (Ollama)
    OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
    DEFAULT_LOCAL_MODEL = "gemma3:12b"  # Modèle local par défaut (évaluation complexité + routing + fallback)
    AGENT_SPECIFIC_LOCAL_MODELS = {
        "coder": "qwen3-coder:30b",
        "factory": "qwen3:8b",
        "infra": "qwen3:8b",
        "security": "deepseek-r1:8b",
        "writer": "gemma3:12b",
        "strategist": "gemma3:12b",
        "architect": "gemma3:12b",
        "researcher": "qwen3-vl:8b"
    }
    
    # Matériel du serveur (utilisé par infra_agent pour les seuils d'alerte)
    HARDWARE = {"RAM_GB": 32, "VRAM_GB": 16}

    PROJECT_ID = os.getenv("PROJECT_ID", "default")
    CHROMA_PERSIST_PATH = os.getenv("CHROMA_DB_PATH", "./memory/chroma_db")

    # --- RAG (Retrieval-Augmented Generation) ---
    RAG_DEFAULT_N_RESULTS = int(os.getenv("RAG_N_RESULTS", "3"))
    RAG_RECALL_LIMIT = int(os.getenv("RAG_RECALL_LIMIT", "2"))

    # --- MODE NUIT (modèles réduits pour éviter crash GPU) ---
    NIGHT_MODE = os.getenv("NIGHT_MODE", "0") == "1"
    NIGHT_MODE_LOCAL_MODELS = {
        "coder": "qwen3-coder:14b",
        "strategist": "gemma3:12b",
    }
    # Limite de taille modèle local (0 = pas de limite). Ex: 16 pour bloquer les 30b sur 16GB VRAM.
    MAX_LOCAL_MODEL_SIZE = int(os.getenv("MAX_LOCAL_MODEL_SIZE", "0"))

    if NIGHT_MODE:
        for agent_name, night_model in NIGHT_MODE_LOCAL_MODELS.items():
            if agent_name in AGENT_SPECIFIC_LOCAL_MODELS:
                AGENT_SPECIFIC_LOCAL_MODELS[agent_name] = night_model

    if not GOOGLE_API_KEY:
        print(f"⚠️ [CONFIG] Mode 100% LOCAL activé.")
    else:
        print(f"✅ [CONFIG] Tier 1 Google AI Pro : ACTIVE ({len(MODELS)} modèles).")
    if NIGHT_MODE:
        print(f"🌙 [CONFIG] Mode Nuit : modèles réduits ({', '.join(f'{k}→{v}' for k, v in NIGHT_MODE_LOCAL_MODELS.items())})")
    if MAX_LOCAL_MODEL_SIZE > 0:
        print(f"📏 [CONFIG] Limite modèle local : {MAX_LOCAL_MODEL_SIZE}B max")