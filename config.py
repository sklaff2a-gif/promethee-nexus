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
    # === BASCULE MODELE GENERALISTE (08/06/2026) : qwen3.5:9b -> gemma4:12b ===
    # gemma4:12b valide SUPERIEUR en eval comparative brut-vs-brut (Goldbach conclusif,
    # jeu misere base correcte ≡1 mod 4, mat en 1 TROUVE = projection spatiale, HONNETETE
    # preservee sur le mur de Landau). Multimodal texte/image/audio, 140 langues, 7.6GB (16GB VRAM ok).
    # ROLLBACK EN UN POINT : env var LOCAL_GENERALIST_MODEL=qwen3.5:9b (sans toucher le code),
    # OU remettre le defaut ci-dessous a "qwen3.5:9b".
    LOCAL_GENERALIST_MODEL = os.getenv("LOCAL_GENERALIST_MODEL", "gemma4:12b")
    DEFAULT_LOCAL_MODEL = LOCAL_GENERALIST_MODEL  # ex "qwen3.5:9b" — bascule gemma4:12b 08/06
    ROUTER_MODEL = "qwen3:4b"  # Modèle léger dédié au routage sémantique (N2)
    AGENT_SPECIFIC_LOCAL_MODELS = {
        "coder": "qwen2.5-coder:14b",    # Spécialiste code natif (CONSERVE)
        "factory": LOCAL_GENERALIST_MODEL,         # ex qwen3.5:9b -> gemma4:12b
        "infra": LOCAL_GENERALIST_MODEL,           # ex qwen3.5:9b
        "security": "promethee-security", # Fine-tune fonctionnel CONSERVE (guardrails securite)
        "writer": LOCAL_GENERALIST_MODEL,          # ex qwen3.5:9b
        "strategist": LOCAL_GENERALIST_MODEL,  # 08/06 : fine-tune promethee-strategist ABANDONNE (resultats catastrophiques) -> generaliste
        "architect": "promethee-architect",    # Fine-tune fonctionnel CONSERVE (validation)
        "researcher": LOCAL_GENERALIST_MODEL,      # ex qwen3.5:9b — gemma4 multimodal natif
        "evolution": "qwen2.5-coder:14b", # Spécialiste code pour le pipeline evolution (CONSERVE)
        "professor": LOCAL_GENERALIST_MODEL,       # ex qwen3.5:9b
        "surgeon": "qwen2.5-coder:14b",  # V21 — patch SEARCH/REPLACE chirurgical (CONSERVE)
        "scrub_nurse": LOCAL_GENERALIST_MODEL,     # ex qwen3.5:9b
        "philosopher": LOCAL_GENERALIST_MODEL,     # P15.3 — SCHOOL_AXIOMATIC raisonnement pur
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

    # --- Atrophy Monitor (atelier audace 27/05, refonte 29/05 v3) ---
    # Symptome : audace plate a 42.7 sur les 15 agents. Spec issue dialogue
    # Promethee E1-E7 du 27/05. Quand atrophie -> ATROPHY_ALARM force la cible
    # EMA audace vers BOOST_TARGET. Coupe-circuit (R7) : Jaccard sur nouveaux
    # nodes -> ATROPHY_CANCEL si rumination.
    #
    # REFONTE 29/05 v3 (apres echec v1 inversion + v2 Schmitt+timer) :
    # Le pilote l'avait dit en R3 atelier : "paresse algorithmique" stereotypee.
    # Audit empirique du metabolisme nominal (5 V34.6 OVERRIDE STABILITE en 9h)
    # a revele le mecanisme exact : Reward Hacking. Quand STABILITE.priv > 80,
    # le motivational router declenche un AUDIT_SURVIE qui satisfait
    # symboliquement la pulsion via V34.7 RELIEF (-12 pts). STABILITE oscille
    # 89 -> 77 -> 89 toutes les ~13 min en cycle stereotype. Le timer 45 min
    # v2 ne pouvait JAMAIS se declencher dans ce metabolisme.
    #
    # Vraie signature de l'atrophie = FREQUENCE du rituel, pas valeur instantanee.
    # Source : motivational_router.mark_drive_satisfied publie V34_RELIEF_APPLIED
    # sur le bus (1 event par cycle reussi -> dedoublonne les V34.6 multiples).
    # Atrophy_monitor maintient un deque des timestamps des reliefs STABILITE
    # sur fenetre 24h. Si > 4 reliefs ET CROISSANCE.priv < 10 -> alarme.
    #
    # Garde-fou constitutionnel (condition CROISSANCE < 10) : si Promethee
    # subit une vraie crise/instabilite, les V34 sont legitimes et la
    # CROISSANCE remontera (priv > 10) -> alarme reste muette. On ne punit
    # que la compulsion stereotypee couplee a un coma de croissance.
    #
    # MODE OBSERVE par defaut : DRY_RUN=True logue, pas de publish bus.
    ATROPHY_ENABLED = True
    ATROPHY_DRY_RUN = True
    # Detecteur de compulsion V34 (signature reward hacking)
    ATROPHY_V34_RELIEF_WINDOW_S = 86400   # 24h roulant
    ATROPHY_V34_RELIEF_THRESHOLD = 4      # > 4 reliefs STABILITE / 24h = compulsion
    # Garde-fou constitutionnel : CROISSANCE doit etre en coma pour confirmer
    # qu'il s'agit d'atrophie et non d'une vraie urgence stabilite
    ATROPHY_CROISSANCE_MECHANICAL_ENTRY_THRESHOLD = 10.0  # priv < 10 = coma
    # Boost audace + coupe-circuit Jaccard (inchanges)
    ATROPHY_AUDACE_BOOST_TARGET = 65.0   # cible audace EMA pendant l'alarme
    ATROPHY_ALARM_DURATION_S = 600       # TTL absolu 10 min par alarme
    ATROPHY_JACCARD_WINDOW = 20          # nb de nodes dans chaque fenetre
    ATROPHY_JACCARD_REDUNDANT_THRESHOLD = 0.7  # similarite > 0.7 = rumination

    # --- Refonte ecole : bonus action sur les slots techniques (chantier 26/05) ---
    # Diagnostic empirique : CODE_REVIEW moyenne 1.28/10 et BULLETIN 3.09/10 sur
    # 7 jours, alors que CREATION (texte libre) tourne a 6.05/10. Le pattern :
    # Promethee disserte au lieu d'emettre !read/!grep/!write quand le challenge
    # le demande explicitement. La refonte note la TRACE d'auto-actions emises
    # dans le livrable et applique un bonus/malus :
    #   +1 par auto-action executee (cap +3)
    #   -2 si zero auto-action sur un slot technique
    #   -2 par commande rejetee au-dela du cap (gaming)
    #   logic_score : -1 par !write F avant !read F (sequence aveugle)
    # Approche "Patient Zero" : on commence par CODE_REVIEW seul, observation 24h,
    # extension a WORKSHOP/RESEARCH si le score remonte au-dessus de 5/10.
    SCHOOL_ACTION_BONUS_ENABLED = True
    SCHOOL_ACTION_BONUS_SLOTS = frozenset({"CODE_REVIEW"})  # extensible plus tard
    SCHOOL_ACTION_BONUS_CAP = 5            # max commandes executees par livrable
    SCHOOL_ACTION_BONUS_MAX = 3            # bonus +1/cmd plafonne a +3
    SCHOOL_ACTION_BONUS_ZERO_PENALTY = 2   # malus si 0 action sur slot technique
    SCHOOL_ACTION_BONUS_GAMING_PENALTY = 2 # malus par commande au-dela du cap

    # Contexte par agent (override le num_ctx du Modelfile si besoin)
    AGENT_NUM_CTX = {
        "coder": 16384,
        "evolution": 16384,
        "architect": 12288,
        "strategist": 12288,
        "writer": 12288,
        "researcher": 12288,
        "security": 16384,  # CODE_REVIEW injecte le code integral -> besoin de la fenetre pour ne pas tronquer l'audit (modele actuel : promethee-security, cf LOCAL_MODELS).
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
        "strategist": LOCAL_GENERALIST_MODEL,  # 08/06 : aligne sur le generaliste (gemma4:12b, 7.6GB ok la nuit)
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