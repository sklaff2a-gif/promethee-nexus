"""
Master Prompts — Guardrails anti-hallucination pour LLMs locaux.

Les petits LLMs (8B params) ont un biais de récence : ils accordent plus
d'importance aux instructions en FIN de prompt qu'en début. Ces guardrails
sont des SUFFIXES à ajouter APRÈS la mission, pour que le LLM les voie
en dernier et les respecte davantage.
"""
import os

# ---------------------------------------------------------------------------
# Guardrail : GÉNÉRATION DE CODE (Coder, Evolution, Grimoire)
# ---------------------------------------------------------------------------
CODE_GENERATION_GUARDRAIL = (
    "\n\n--- RAPPEL CRITIQUE (OBLIGATION ABSOLUE) ---\n"
    "Tu génères du code UNIQUEMENT pour le projet PROMÉTHÉE.\n"
    "BIBLIOTHÈQUES INTERDITES : langchain, openai, flask, django, streamlit, "
    "torch, tensorflow, FAISS, crewai, autogen, pandas, gradio.\n"
    "Le code DOIT cibler un fichier EXISTANT du projet (core/, Agents/).\n"
    "Retourne UNIQUEMENT un bloc ```python, RIEN d'autre.\n"
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
    "PAS de code utilisant des frameworks externes (langchain, openai, django, flask, etc.).\n"
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
        "1. Cite UNIQUEMENT des fichiers listés ci-dessous.\n"
        "2. TECHNOLOGIES INTERDITES : Kubernetes, Docker, Kafka, microservices, blockchain, "
        "load balancing, cluster, conteneurs, cloud infra, Redis, RabbitMQ.\n"
        "3. Toute proposition DOIT cibler des fichiers EXISTANTS (core/, Agents/).\n"
        "4. RÉPONDS EN FRANÇAIS UNIQUEMENT.\n"
        "5. Ne propose PAS de fichiers qui n'existent pas.\n"
        "6. Si tu cites un fichier absent de la liste, ta réponse sera REJETÉE.\n"
        f"{files_block}"
    )


# Backward compat : constante statique pour les imports existants
COUNCIL_GUARDRAIL = council_guardrail()

# ---------------------------------------------------------------------------
# Guardrail : GÉNÉRATION DE TESTS (CI pipeline)
# ---------------------------------------------------------------------------
TEST_GENERATION_GUARDRAIL = (
    "\n\n--- RAPPEL CRITIQUE ---\n"
    "Les tests DOIVENT importer UNIQUEMENT les symboles listés dans 'API RÉELLE' ci-dessus.\n"
    "N'invente AUCUNE classe ou fonction. Utilise unittest.mock pour les dépendances.\n"
    "PAS d'import langchain, openai, flask, django, torch.\n"
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
                    "core/capabilities", "core/memory"]:
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
