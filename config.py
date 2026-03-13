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
    DEFAULT_LOCAL_MODEL = "qwen3.5:9b"  # Qwen3.5 9B — bat gpt-oss-120B, 6.6GB, 262K ctx
    ROUTER_MODEL = "qwen3:4b"  # Modèle léger dédié au routage sémantique (N2)
    AGENT_SPECIFIC_LOCAL_MODELS = {
        "coder": "qwen2.5-coder:14b",    # Spécialiste code natif (remplace promethee-coder)
        "factory": "qwen3.5:9b",         # Upgrade de qwen3:8b — +50% benchmarks
        "infra": "qwen3.5:9b",           # Upgrade de qwen3:8b
        "security": "promethee-security", # Fine-tune conservé (guardrails sécurité bakés)
        "writer": "qwen3.5:9b",          # Upgrade de promethee-general
        "strategist": "promethee-strategist",  # Fine-tune conservé (personnalité bakée)
        "architect": "promethee-architect",    # Fine-tune conservé (validation bakée)
        "researcher": "qwen3.5:9b",      # Upgrade — multimodal + 262K contexte
        "evolution": "qwen2.5-coder:14b", # Spécialiste code pour le pipeline evolution
    }

    # Contexte par agent (override le num_ctx du Modelfile si besoin)
    AGENT_NUM_CTX = {
        "coder": 16384,
        "evolution": 16384,
        "architect": 12288,
        "strategist": 12288,
        "writer": 12288,
        "researcher": 12288,
        "security": 8192,
        "infra": 8192,
        "factory": 8192,
        "formatter": 8192,
        "default": 8192,
    }
    
    @staticmethod
    def get_max_content_chars(agent_name: str, prompt_overhead: int = 2000) -> int:
        """Limite de contenu en chars, proportionnelle au num_ctx de l'agent."""
        num_ctx = Config.AGENT_NUM_CTX.get(agent_name, Config.AGENT_NUM_CTX["default"])
        return num_ctx * 2 - prompt_overhead

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
        "coder": "qwen3:14b",
        "strategist": "gemma3:12b",
    }
    # Limite de taille modèle local (0 = pas de limite). Ex: 16 pour bloquer les 30b sur 16GB VRAM.
    MAX_LOCAL_MODEL_SIZE = int(os.getenv("MAX_LOCAL_MODEL_SIZE", "16"))

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