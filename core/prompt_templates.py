"""
Master Prompts — Guardrails anti-hallucination pour LLMs locaux.

Les petits LLMs (8B params) ont un biais de récence : ils accordent plus
d'importance aux instructions en FIN de prompt qu'en début. Ces guardrails
sont des SUFFIXES à ajouter APRÈS la mission, pour que le LLM les voie
en dernier et les respecte davantage.
"""
import os

# ---------------------------------------------------------------------------
# Frameworks/bibliothèques interdits — source unique de vérité
# Utilisé par : guardrails, coder_agent (_OFFTOPIC_KEYWORDS), evolution_agent (_ALIEN_IMPORTS)
# ---------------------------------------------------------------------------
FORBIDDEN_FRAMEWORKS = frozenset({
    "langchain", "langgraph", "crewai", "autogen",
    "openai", "anthropic", "cohere",
    "flask", "django", "streamlit", "gradio",
    "torch", "tensorflow", "keras", "sklearn",
    "pandas", "numpy", "scipy",
    "kubernetes", "docker", "terraform", "kafka",
    "blockchain", "web3", "solidity", "brownie",
    "faiss", "pinecone", "weaviate", "qdrant",
    "pygame", "tkinter",
    "sqlalchemy", "peewee", "mongoengine",
    "fastapi_users", "starlette_admin",
    "express", "react", "vue", "angular",
})


def forbidden_frameworks_str() -> str:
    """Liste triée des frameworks interdits pour injection dans les prompts."""
    return ", ".join(sorted(FORBIDDEN_FRAMEWORKS))

# ---------------------------------------------------------------------------
# Guardrail : GÉNÉRATION DE CODE (Coder, Evolution, Grimoire)
# ---------------------------------------------------------------------------
CODE_GENERATION_GUARDRAIL = (
    "\n\n--- RAPPEL CRITIQUE (OBLIGATION ABSOLUE) ---\n"
    "Tu génères du code UNIQUEMENT pour le projet PROMÉTHÉE.\n"
    f"BIBLIOTHÈQUES INTERDITES : {forbidden_frameworks_str()}.\n"
    "Le code DOIT cibler un fichier EXISTANT du projet (core/, Agents/).\n"
    "Retourne UNIQUEMENT un bloc ```python, RIEN d'autre.\n"
    "\n--- PROTOCOLE AVANT DE CODER ---\n"
    "1. STRUCTURE : Décris en 1 ligne ce que le code fait AVANT de l'écrire.\n"
    "2. IMPORTS : Liste UNIQUEMENT les imports standard (os, json, asyncio, time, logging) "
    "ou ceux DÉJÀ utilisés dans le fichier cible. AUCUN import externe.\n"
    "3. CODE MINIMAL : Écris le minimum de code nécessaire. Chaque fonction < 20 lignes.\n"
    "4. VÉRIFICATION : Le code contient-il def ou class ? Les types sont-ils cohérents ? "
    "Les imports existent-ils vraiment ? Si non, CORRIGE avant de retourner.\n"
)

# ---------------------------------------------------------------------------
# Guardrail : ANALYSE (Security, Strategist, etc.)
# ---------------------------------------------------------------------------
def analysis_guardrail(project_files: str) -> str:
    """Guardrail pour les tâches d'analyse — injecte la liste des fichiers réels."""
    return (
        "\n\n--- RAPPEL CRITIQUE ---\n"
        "Tu analyses le projet PROMÉTHÉE. Réfère-toi UNIQUEMENT aux fichiers RÉELS :\n"
        f"{project_files}\n"
        "NE MENTIONNE PAS de fichiers qui n'existent pas.\n"
    )

# ---------------------------------------------------------------------------
# Guardrail : MISSIONS AUTONOMES (Mode Veille)
# ---------------------------------------------------------------------------
AUTONOMY_GUARDRAIL = (
    "\n\n--- RAPPEL ---\n"
    "Tu agis pour le projet PROMÉTHÉE (Python/FastAPI/Ollama sur UN SEUL PC Windows).\n"
    "Toute suggestion DOIT cibler des fichiers EXISTANTS du projet.\n"
    f"FRAMEWORKS INTERDITS : {forbidden_frameworks_str()}.\n"
)

# ---------------------------------------------------------------------------
# Guardrail : DÉBATS COUNCIL (recency bias — en fin de prompt)
# ---------------------------------------------------------------------------
def council_guardrail(project_files: str = "") -> str:
    """Guardrail Council avec rappel des fichiers réels en fin de prompt (biais de récence)."""
    files_block = ""
    if project_files:
        files_block = f"\n{project_files}\n"
    return (
        "\n\n--- RAPPEL CRITIQUE (DÉBAT COUNCIL) ---\n"
        "Tu débats pour le projet PROMÉTHÉE (Python/FastAPI/Ollama sur UN SEUL PC Windows).\n"
        "RÈGLES ABSOLUES :\n"
        "1. Cite UNIQUEMENT des fichiers listés ci-dessus.\n"
        f"2. TECHNOLOGIES INTERDITES : {forbidden_frameworks_str()}, "
        "microservices, load balancing, cluster, conteneurs, cloud infra, Redis, RabbitMQ.\n"
        "3. Toute proposition DOIT cibler des fichiers EXISTANTS (core/, Agents/).\n"
        "4. RÉPONDS EN FRANÇAIS UNIQUEMENT.\n"
        "5. Ne propose PAS de fichiers qui n'existent pas.\n"
        "6. Si tu cites un fichier absent de la liste, ta réponse sera REJETÉE.\n"
        f"{files_block}"
    )


# Backward compat : constante statique pour les imports existants
COUNCIL_GUARDRAIL = council_guardrail()

# ---------------------------------------------------------------------------
# Guardrail : ANTI-HALLUCINATION LLMs 9B (inspire Claude Code / KAIROS)
#
# Regles specifiques ciblees sur les erreurs REELLES observees chez qwen3.5:9b
# et les petits modeles locaux. Chaque regle correspond a un bug constate.
# A/B testable : formulations precises > instructions vagues.
# ---------------------------------------------------------------------------
LLM_9B_ANTI_HALLUCINATION = (
    "\n\n--- REGLES ANTI-HALLUCINATION (OBLIGATION ABSOLUE) ---\n"
    "1. Ne dis JAMAIS 'tous les tests passent' si tu n'as pas vu la sortie reelle.\n"
    "2. Ne FABRIQUE PAS de logs, de references, de numeros de ligne ou de noms de fichiers.\n"
    "   Si tu ne connais pas le nom exact, dis 'je ne sais pas' plutot qu'inventer.\n"
    "3. Ne DECRIS PAS une image, une photo ou un fichier que tu n'as pas observe.\n"
    "4. Si on te demande un CALCUL, montre les etapes. Ne donne pas un chiffre sans calcul.\n"
    "5. Ne REPETE PAS le meme pattern plus de 3 fois — si tu boucles, arrete et dis-le.\n"
    "6. Ne commence PAS ta reponse par des flatteries ('Excellente question !').\n"
    "7. Si ta reponse depasse tes capacites, dis-le. Le silence honnete vaut mieux\n"
    "   qu'une reponse inventee.\n"
    "8. REPONDS EN FRANCAIS sauf si le code l'exige.\n"
)

# ---------------------------------------------------------------------------
# Guardrail : GÉNÉRATION DE TESTS (CI pipeline)
# ---------------------------------------------------------------------------
TEST_GENERATION_GUARDRAIL = (
    "\n\n--- RAPPEL CRITIQUE ---\n"
    "Les tests DOIVENT importer UNIQUEMENT les symboles listés dans 'API RÉELLE' ci-dessus.\n"
    "N'invente AUCUNE classe ou fonction. Utilise unittest.mock pour les dépendances.\n"
    f"IMPORTS INTERDITS : {forbidden_frameworks_str()}.\n"
)

# ---------------------------------------------------------------------------
# Structure projet dynamique (partagée entre council, security, etc.)
# ---------------------------------------------------------------------------
_PROJECT_STRUCTURE_CACHE = None


def get_project_structure() -> str:
    """Liste dynamique des fichiers réels du projet (lazy, cached)."""
    global _PROJECT_STRUCTURE_CACHE
    if _PROJECT_STRUCTURE_CACHE is not None:
        return _PROJECT_STRUCTURE_CACHE
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    lines = ["FICHIERS RÉELS DU PROJET :"]
    for subdir in ["core", "Agents", "core/grimoire", "core/event_bus",
                    "core/capabilities", "core/memory", "core/games",
                    "core/plugins", "core/domains"]:
        dir_path = os.path.join(project_root, subdir.replace("/", os.sep))
        if os.path.isdir(dir_path):
            py_files = sorted(f for f in os.listdir(dir_path) if f.endswith(".py"))
            if py_files:
                lines.append(f"  {subdir}/ : {', '.join(py_files)}")
    root_py = sorted(f for f in os.listdir(project_root)
                     if f.endswith(".py") and os.path.isfile(os.path.join(project_root, f)))
    if root_py:
        lines.append(f"  ./ : {', '.join(root_py)}")
    _PROJECT_STRUCTURE_CACHE = "\n".join(lines)
    return _PROJECT_STRUCTURE_CACHE


def reset_project_structure_cache():
    """Reset le cache de la structure projet (utile dans les tests)."""
    global _PROJECT_STRUCTURE_CACHE
    _PROJECT_STRUCTURE_CACHE = None
