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
    # NOTE: gemini-2.5-pro désactivé sur Free Tier (quota=0 depuis ~mars 2026)
    # Tout le trafic passe par Flash jusqu'à activation Tier 1 (facturation)
    AGENT_MODEL_ROUTING = {
        # Cerveaux → Flash (Pro désactivé Free Tier)
        "strategist": [MODELS["FAST"]],
        "architect":  [MODELS["FAST"]],
        "writer":     [MODELS["FAST"]],
        # Spécialistes → Flash uniquement
        "researcher": [MODELS["FAST"]],
        "coder":      [MODELS["FAST"]],
        "evolution":  [MODELS["FAST"]],
        # Ouvriers → Flash uniquement
        "factory":    [MODELS["FAST"]],
        "infra":      [MODELS["FAST"]],
        "security":   [MODELS["FAST"]],
        # Evaluateur → Flash (Pro désactivé Free Tier)
        "professor":  [MODELS["FAST"]],
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
        "professor": "qwen3.5:9b",       # Evaluation locale (Cloud en primaire via routing)
        "surgeon": "qwen2.5-coder:14b",  # V21 — patch SEARCH/REPLACE chirurgical
        "scrub_nurse": "qwen3.5:9b",     # V29 — checklist preservation (JSON)
        "philosopher": "qwen3.5:9b",     # P15.3 (14/05) — SCHOOL_AXIOMATIC raisonnement pur
    }

    # V17 MoE (2026-04-24) — Mixture of Experts par routine scolaire.
    # Diagnostic 24/04 matinee : le LLM 9B generique (promethee-security,
    # writer qwen3.5:9b) est incapable de suivre les chunks AST fournis par
    # V15.3 RAG sur les CODE_REVIEW. Il genere un audit security "generique"
    # tire de ses poids parametriques au lieu d analyser le code reel.
    # Solution MoE : router les slots a forte composante code vers un modele
    # specialise code (qwen2.5-coder:14b, deja utilise pour coder/evolution).
    # Prend PRIORITE sur AGENT_SPECIFIC_LOCAL_MODELS quand le marqueur
    # [SCHOOL_SLOT: XXX] est detecte dans le prompt (injecte par V4.4).
    # Les slots generatifs (RESEARCH, BULLETIN) restent en 9b pour la
    # narration/synthese (domaines non-code).
    ROUTINE_MODELS = {
        "CODE_REVIEW": "qwen2.5-coder:14b",  # revue AST rigoureuse
        "CREATION":    "qwen2.5-coder:14b",  # script executable
        "WORKSHOP":    "qwen2.5-coder:14b",  # prototypage code
        # RESEARCH / BULLETIN / autres : fallback default (9b generique)
    }

    # === Code Engine V4 — Pipeline biphasé (2026-05-21) ===
    # Diagnostic WORKSHOP 1.57/10 : qwen2.5-coder:14b sature sa bande passante
    # d'attention quand il doit penser l'abstraction ET produire la syntaxe
    # dans le meme souffle -> IndentationError en boucle. Fix : separer les
    # preoccupations cognitives en 2 appels (Architecte = quoi, Ouvrier = comment).
    # Flag opt-in : rollback instantane en le passant a False.
    BIPHASIC_CODEGEN_ENABLED = True
    BIPHASIC_CODEGEN_SLOTS = {"WORKSHOP", "CREATION"}  # CODE_REVIEW garde son map-reduce

    BIPHASIC_ARCHITECT_PROMPT = (
        "Tu es un ARCHITECTE LOGICIEL. On te confie un sujet, parfois abstrait.\n"
        "Ta mission : concevoir la LOGIQUE — structures de donnees, algorithme,\n"
        "responsabilites de chaque fonction — sous forme de PLAN DETAILLE ou de\n"
        "PSEUDO-CODE commente. Reste fidele au sujet impose (cite ses termes-cles).\n"
        "INTERDICTION ABSOLUE d'ecrire du code Python executable ou des blocs ```python.\n"
        "Tu produis une SPECIFICATION, pas une implementation. Concentre 100% de ton\n"
        "attention sur le QUOI, jamais sur la syntaxe."
    )
    BIPHASIC_WORKER_PROMPT = (
        "Tu es un COMPILATEUR PYTHON STRICT. On te donne une specification detaillee.\n"
        "Ta seule mission : la traduire en code Python VALIDE et executable.\n"
        "REGLES ABSOLUES :\n"
        "- AUCUNE explication, AUCUN commentaire conceptuel, AUCun texte hors-code.\n"
        "- UN SEUL bloc ```python contenant tout le code, rien d'autre avant ou apres.\n"
        "- Indentation rigoureuse (4 espaces), toutes les structures fermees.\n"
        "Tu ne reflechis plus au QUOI (c'est fait), uniquement au COMMENT syntaxique."
    )

    # --- Système immunitaire P16 : GC des fichiers-fantômes ---
    # Supprime, en fin de dream_consolidation, les noeuds dont le concept designe
    # un fichier source (core/*.py, Agents/*.py, tests/*.py) ABSENT du disque
    # (verite POSIX). Cible les references mortes / typos / fichiers renommes.
    # Ne touche JAMAIS les namespaces semantiques (reflex:/pulsion:/trait:/zone:
    # sont legitimes — leçon reflex:shed du 22/05). Opt-in.
    # IMMUNE_SYSTEM_DRY_RUN=True : observe et logue les fantomes SANS supprimer
    # (valider sur les cas reels avant d'armer la suppression).
    IMMUNE_SYSTEM_ENABLED = True
    IMMUNE_SYSTEM_DRY_RUN = False  # armé le 22/05 après validation locale (5 fantômes confirmés en observe)

    # --- Journal des Échecs Silencieux (atelier créatif 26/05) ---
    # Filtre pré-veto qui détecte les hypothèses tuées par veto émotionnel répété
    # et accorde un "sursis de curiosité" 5 min sur celles qui n'ont jamais vraiment
    # été explorées. Ne ressuscite PAS les morts, empêche de tuer à nouveau ce qui
    # n'a pas vécu. Spec issue dialogue Prométhée E1-E7 (26/05).
    # MODE OBSERVE par défaut : journalise les inscriptions et les détections sans
    # accorder de sursis. Armer SILENT_FAILURES_DRY_RUN=False après 24h de logs.
    SILENT_FAILURES_ENABLED = True
    SILENT_FAILURES_DRY_RUN = True  # mode OBSERVE — log sans bloquer le veto

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
        "professor": 8192,
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
        "coder": "qwen2.5-coder:14b",
        "strategist": "qwen3.5:9b",
    }
    # Limite de taille modèle local (0 = pas de limite). Ex: 16 pour bloquer les 30b sur 16GB VRAM.
    MAX_LOCAL_MODEL_SIZE = int(os.getenv("MAX_LOCAL_MODEL_SIZE", "16"))

    # PHASEUR_DE_Réalité LITE POC (§4.10.bis H1.6 du brouillon, CHARTA procédure 3.2)
    # Off par défaut. Plafond hard non-bypassable. Kill switch via /api/phaseur/disable.
    PHASEUR_ENABLED = os.getenv("PHASEUR_ENABLED", "false").lower() == "true"
    PHASEUR_MAX_INTENSITY = 0.05
    PHASEUR_CURRENT_INTENSITY = float(os.getenv("PHASEUR_INTENSITY", "0.0"))

    # PONT SUBCONSCIENT (19/05/2026, médiation P16 → LLM, 8e preuve §4.13)
    # Lecture observationnelle du synaptic_network pour injecter les concepts
    # énergisés dans le system_prompt avant call Ollama. Off par défaut, opt-in.
    SUBCONSCIENT_ENABLED = os.getenv("SUBCONSCIENT_ENABLED", "false").lower() == "true"
    SUBCONSCIENT_TOP_N = int(os.getenv("SUBCONSCIENT_TOP_N", "5"))
    SUBCONSCIENT_MIN_ENERGY = float(os.getenv("SUBCONSCIENT_MIN_ENERGY", "0.1"))

    # FOIE COGNITIF (19/05/2026, Context Compressor heuristique pre-LLM)
    # Spec issue de Promethee : agent gardien anneau autonome parallele qui filtre
    # le sang d'informations avant qu'il n'irrigue les organes (analogie systeme
    # reticulo-endothelial). 3 regles : truncation messages longs, elision paires
    # user-court/assistant-verbose, dedup approximatif §4.5.bis. Latence <10ms.
    COMPRESSOR_ENABLED = os.getenv("COMPRESSOR_ENABLED", "false").lower() == "true"
    COMPRESSOR_MIN_MESSAGES = int(os.getenv("COMPRESSOR_MIN_MESSAGES", "10"))
    COMPRESSOR_TARGET_RATIO = float(os.getenv("COMPRESSOR_TARGET_RATIO", "0.5"))

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