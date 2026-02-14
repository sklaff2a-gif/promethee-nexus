"""
CI/CD Pipeline interne PROMÉTHÉE.
Remplace le quality_control_listener : Factory écrit du code → Coder génère des tests
→ pytest les exécute → Architect valide → déploiement ou rollback.
"""
import ast
import asyncio
import logging
import os
import re
import shutil
import sys

from core.event_bus.bus import bus
from core.orchestrator import orchestrator

logger = logging.getLogger("CIPipeline")

AUTO_TESTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "auto"
)


# --- Fonctions utilitaires ---

def extract_python_code(text: str) -> str | None:
    """Extrait le code Python depuis une réponse LLM (blocs markdown ou heuristique)."""
    blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    if blocks:
        return blocks[-1].strip()
    lines = text.strip().splitlines()
    code_lines = [
        l for l in lines
        if re.match(r"^(import |from \w|class \w|def \w|@\w|    |if |for |while |return |assert )", l)
    ]
    if len(code_lines) >= 3:
        return "\n".join(code_lines)
    return None


def _extract_api_signatures(source_code: str) -> str:
    """Extrait les classes, méthodes et fonctions publiques du code source via AST.
    Retourne une description lisible de l'API réelle du fichier."""
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return ""

    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = ", ".join(
                getattr(b, "id", getattr(b, "attr", "?")) for b in node.bases
            )
            lines.append(f"  class {node.name}({bases}):")
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and not item.name.startswith("_"):
                    args = ", ".join(a.arg for a in item.args.args if a.arg != "self")
                    lines.append(f"    def {item.name}({args})")
                elif isinstance(item, ast.FunctionDef) and item.name == "__init__":
                    args = ", ".join(a.arg for a in item.args.args if a.arg != "self")
                    lines.append(f"    def __init__({args})")
        elif isinstance(node, ast.FunctionDef) and not isinstance(
            getattr(node, "_parent", None), ast.ClassDef
        ):
            if not node.name.startswith("_") and node.col_offset == 0:
                args = ", ".join(a.arg for a in node.args.args)
                lines.append(f"  def {node.name}({args})")

    return "\n".join(lines) if lines else ""


def _validate_test_imports(test_code: str, source_code: str, module_path: str) -> tuple[bool, str]:
    """Vérifie que les symboles importés dans le test existent dans le code source.
    Retourne (valide, message_erreur)."""
    try:
        test_tree = ast.parse(test_code)
        source_tree = ast.parse(source_code)
    except SyntaxError:
        return True, ""  # en cas d'erreur de parse, laisser pytest décider

    # Collecter les symboles définis dans le source
    source_symbols = set()
    for node in ast.walk(source_tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            source_symbols.add(node.name)

    if not source_symbols:
        return True, ""

    # Vérifier les imports du test qui ciblent le module source
    bad_imports = []
    for node in ast.walk(test_tree):
        if isinstance(node, ast.ImportFrom) and node.module and module_path:
            if module_path.replace(".", "/") in (node.module or "").replace(".", "/") or \
               node.module == module_path:
                for alias in node.names:
                    if alias.name not in source_symbols and alias.name != "*":
                        bad_imports.append(alias.name)

    if bad_imports:
        return False, f"Imports invalides (n'existent pas dans le source) : {', '.join(bad_imports)}"
    return True, ""


def _build_import_hint(filename: str, filepath: str, source_code: str) -> str:
    """Construit un hint d'imports DYNAMIQUE basé sur le fichier réel.
    Analyse le code source pour extraire les classes/fonctions et le chemin module."""
    normalized = filepath.replace("\\", "/")

    # Déterminer le chemin module Python (ex: Agents.factory_agent, core.router)
    module_path = None
    for marker in ("Agents/", "core/", "capabilities/"):
        idx = normalized.find(marker)
        if idx >= 0:
            rel = normalized[idx:]
            if rel.endswith(".py"):
                rel = rel[:-3]
            module_path = rel.replace("/", ".")
            break

    # Extraire les classes et fonctions publiques du code source
    classes = re.findall(r"^class (\w+)", source_code, re.MULTILINE)
    functions = [f for f in re.findall(r"^def (\w+)", source_code, re.MULTILINE)
                 if not f.startswith("_")]

    if module_path:
        examples = []
        if classes:
            examples.append(f"  `from {module_path} import {', '.join(classes[:3])}`")
        if functions:
            examples.append(f"  `from {module_path} import {', '.join(functions[:3])}`")

        examples_str = "\n".join(examples) if examples else f"  `import {module_path}`"
        return (
            f"CONTEXTE IMPORTS DU PROJET :\n"
            f"- Le fichier testé est : {filename} (chemin complet : {filepath})\n"
            f"- Module Python : {module_path}\n"
            f"- Imports corrects pour CE fichier :\n{examples_str}\n"
            f"- IMPORTANT : importe UNIQUEMENT depuis {module_path}. Ne devine PAS d'autres modules.\n"
            f"- NE PAS utiliser `import {filename}` directement.\n"
            f"- Pour les dépendances externes (ChromaDB, httpx, etc.), utilise des mocks.\n"
        )
    else:
        # Fichier hors package connu (ex: merchant_code.py à la racine)
        return (
            f"CONTEXTE IMPORTS DU PROJET :\n"
            f"- Le fichier testé est : {filename} (chemin complet : {filepath})\n"
            f"- Ce fichier est à la racine du projet, PAS dans un package.\n"
            f"- Pour l'importer : `import {filename.replace('.py', '')}` ou utilise importlib.\n"
            f"- IMPORTANT : N'invente PAS d'imports vers des modules qui n'existent pas.\n"
            f"- Pour les dépendances externes, utilise des mocks.\n"
        )


def _slugify_filename(filepath: str) -> str:
    """'Agents/coder_agent.py' → 'coder_agent'"""
    normalized = filepath.replace("\\", "/")
    base = os.path.basename(normalized)
    return os.path.splitext(base)[0]


def _rollback(filepath: str) -> bool:
    """Restaure filepath.bak vers filepath, ou supprime le fichier s'il est nouveau."""
    bak = filepath + ".bak"
    if os.path.exists(bak):
        shutil.copy2(bak, filepath)
        logger.info(f"[ROLLBACK] {filepath} restauré depuis .bak")
        return True
    # Fichier nouveau (pas de .bak) → le supprimer pour ne pas laisser un fichier défaillant
    if os.path.exists(filepath):
        os.remove(filepath)
        logger.info(f"[ROLLBACK] {filepath} supprimé (fichier nouveau, pas de .bak)")
        return True
    logger.warning(f"[ROLLBACK] Pas de .bak trouvé pour {filepath}")
    return False


# --- Mémoire CI/CD ---

CI_FAILURES_COLLECTION = "ci_failures"
CI_SUCCESSES_COLLECTION = "ci_successes"


def _remember_failure(filename: str, source_code: str, step: str, error_detail: str):
    """Mémorise un échec CI/CD pour ne pas le reproduire."""
    coder = orchestrator.agents.get("coder")
    if not coder or not coder.has_memory:
        return
    text = (
        f"[CI/CD ÉCHEC] {filename}\n"
        f"[ÉTAPE] {step}\n"
        f"[ERREUR] {error_detail[:500]}\n"
        f"[CODE SOURCE]\n{source_code[:1000]}"
    )
    coder.remember(text, {"source": "ci_pipeline", "file": filename, "step": step, "outcome": "failure"}, CI_FAILURES_COLLECTION)
    logger.info(f"[MÉMOIRE] Échec archivé : {filename} @ {step}")


def _remember_success(filename: str, source_code: str, test_code: str, arch_verdict: str):
    """Mémorise un succès CI/CD complet pour réutilisation future."""
    strategist = orchestrator.agents.get("strategist")
    if not strategist or not strategist.has_memory:
        return
    text = (
        f"[CI/CD SUCCÈS] {filename}\n"
        f"[CODE SOURCE]\n{source_code[:1500]}\n"
        f"[TESTS VALIDÉS]\n{test_code[:1000]}\n"
        f"[VERDICT ARCHITECT] {arch_verdict[:300]}"
    )
    strategist.remember(text, {"source": "ci_pipeline", "file": filename, "outcome": "success"}, CI_SUCCESSES_COLLECTION)
    logger.info(f"[MÉMOIRE] Succès archivé : {filename}")


def _recall_failures(filename: str, source_code: str) -> str:
    """Consulte la mémoire des échecs passés pour enrichir le prompt du Coder."""
    coder = orchestrator.agents.get("coder")
    if not coder or not coder.has_memory:
        return ""
    query = f"CI/CD ÉCHEC {filename} {source_code[:200]}"
    return coder.recall(query, limit=3, collection=CI_FAILURES_COLLECTION)


def _recall_successes(filename: str, source_code: str) -> str:
    """Consulte la mémoire des succès passés pour réutiliser des patterns validés."""
    strategist = orchestrator.agents.get("strategist")
    if not strategist or not strategist.has_memory:
        return ""
    query = f"CI/CD SUCCÈS {filename} {source_code[:200]}"
    return strategist.recall(query, limit=2, collection=CI_SUCCESSES_COLLECTION)


# --- Pipeline principal ---

async def _run_pytest(test_file: str) -> tuple[bool, str]:
    """Exécute pytest sur un fichier de test. Retourne (success, output)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pytest", test_file, "-v", "--tb=short",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        output = stdout.decode("utf-8", errors="replace")
        return proc.returncode == 0, output
    except asyncio.TimeoutError:
        return False, "TIMEOUT: pytest a dépassé 60 secondes"
    except Exception as e:
        return False, f"Erreur exécution pytest: {e}"


async def run_pipeline(filename: str, filepath: str):
    """Pipeline CI/CD complet pour un fichier créé par Factory."""

    await bus.publish("CI_PIPELINE_START", {"filename": filename, "filepath": filepath})

    # 1. Lecture du fichier
    if not os.path.exists(filepath):
        await bus.publish("CI_PIPELINE_RESULT", {
            "filename": filename, "success": False,
            "detail": f"Fichier introuvable : {filepath}"
        })
        return

    with open(filepath, "r", encoding="utf-8") as f:
        source_code = f.read()

    # 2. Génération de tests (appel DIRECT au coder, pas via dispatch_task)
    coder = orchestrator.agents.get("coder")
    if not coder:
        await bus.publish("CI_PIPELINE_RESULT", {
            "filename": filename, "success": False,
            "detail": "Agent coder non disponible"
        })
        return

    # 2a. Consultation mémoire : échecs passés + succès réutilisables
    past_failures = _recall_failures(filename, source_code)
    past_successes = _recall_successes(filename, source_code)

    memory_context = ""
    if past_failures:
        memory_context += (
            f"\n⚠️ ATTENTION — Échecs CI/CD passés sur du code similaire :\n"
            f"{past_failures}\n"
            f"Évite de reproduire ces erreurs.\n"
        )
    if past_successes:
        memory_context += (
            f"\n✅ Référence — Succès CI/CD passés sur du code similaire :\n"
            f"{past_successes}\n"
            f"Inspire-toi de ces patterns validés.\n"
        )

    # Validation syntaxique du code SOURCE avant de lancer la génération de tests
    try:
        ast.parse(source_code)
    except SyntaxError as e:
        detail = f"Code source invalide (SyntaxError ligne {e.lineno}): {e.msg}"
        logger.warning(f"[CI/CD] {detail} — pipeline annulé pour {filename}")
        await bus.publish("CI_PIPELINE_RESULT", {
            "filename": filename, "success": False,
            "detail": detail
        })
        _rollback(filepath)
        return

    # Contexte d'imports DYNAMIQUE basé sur le fichier réel
    import_hint = _build_import_hint(filename, filepath, source_code)

    # Extraction des signatures réelles (classes, méthodes, fonctions)
    api_signatures = _extract_api_signatures(source_code)
    api_section = ""
    if api_signatures:
        api_section = (
            f"\nAPI RÉELLE DU FICHIER (classes et fonctions qui EXISTENT) :\n"
            f"{api_signatures}\n"
            f"Teste UNIQUEMENT ces classes/fonctions. N'invente PAS d'autres symboles.\n"
        )

    test_prompt = (
        f"Génère des tests pytest pour le code suivant. "
        f"Réponds UNIQUEMENT avec un bloc de code Python contenant les tests.\n"
        f"{import_hint}\n"
        f"{api_section}\n"
        f"{memory_context}\n"
        f"```python\n{source_code}\n```"
    )

    try:
        test_response = await coder.generate_content(test_prompt)
    except Exception as e:
        await bus.publish("CI_PIPELINE_STEP", {
            "filename": filename, "step": "test_generation",
            "status": "error", "detail": str(e)
        })
        _remember_failure(filename, source_code, "test_generation", str(e))
        _rollback(filepath)
        await bus.publish("CI_PIPELINE_RESULT", {
            "filename": filename, "success": False,
            "detail": f"Échec génération tests : {e}"
        })
        return

    test_code = extract_python_code(test_response)
    if not test_code:
        await bus.publish("CI_PIPELINE_STEP", {
            "filename": filename, "step": "test_generation",
            "status": "error", "detail": "Aucun code test extrait de la réponse LLM"
        })
        _remember_failure(filename, source_code, "test_generation", "Aucun code test extrait de la réponse LLM")
        _rollback(filepath)
        await bus.publish("CI_PIPELINE_RESULT", {
            "filename": filename, "success": False,
            "detail": "Échec extraction tests depuis la réponse LLM"
        })
        return

    await bus.publish("CI_PIPELINE_STEP", {
        "filename": filename, "step": "test_generation",
        "status": "success", "detail": "Tests générés"
    })

    # 3. Validation syntaxique du code de test AVANT écriture
    try:
        compile(test_code, "<generated_test>", "exec")
    except SyntaxError as e:
        detail = f"Code test invalide (SyntaxError ligne {e.lineno}): {e.msg}"
        await bus.publish("CI_PIPELINE_STEP", {
            "filename": filename, "step": "test_validation",
            "status": "error", "detail": detail
        })
        _remember_failure(filename, source_code, "test_validation", detail)
        # PAS de rollback : le code source n'est pas en cause
        await bus.publish("CI_PIPELINE_RESULT", {
            "filename": filename, "success": False,
            "detail": f"Tests auto-générés invalides — source conservée"
        })
        return

    # 3b. Validation des imports : les symboles importés doivent exister dans le source
    normalized_fp = filepath.replace("\\", "/")
    mod_path = None
    for marker in ("Agents/", "core/", "capabilities/"):
        idx = normalized_fp.find(marker)
        if idx >= 0:
            rel = normalized_fp[idx:]
            if rel.endswith(".py"):
                rel = rel[:-3]
            mod_path = rel.replace("/", ".")
            break

    imports_ok, import_err = _validate_test_imports(test_code, source_code, mod_path or "")
    if not imports_ok:
        detail = f"Tests auto-générés avec imports invalides : {import_err}"
        await bus.publish("CI_PIPELINE_STEP", {
            "filename": filename, "step": "test_validation",
            "status": "error", "detail": detail
        })
        _remember_failure(filename, source_code, "test_validation", detail)
        await bus.publish("CI_PIPELINE_RESULT", {
            "filename": filename, "success": False,
            "detail": f"Tests rejetés (imports fantômes) — source conservée"
        })
        return

    # 3c. Écriture des tests dans tests/auto/
    os.makedirs(AUTO_TESTS_DIR, exist_ok=True)
    slug = _slugify_filename(filepath)
    test_file = os.path.join(AUTO_TESTS_DIR, f"test_{slug}.py")
    with open(test_file, "w", encoding="utf-8") as f:
        f.write(test_code)

    # 4. Exécution pytest
    success, output = await _run_pytest(test_file)
    status = "success" if success else "error"
    await bus.publish("CI_PIPELINE_STEP", {
        "filename": filename, "step": "test_execution",
        "status": status, "detail": output[-500:] if len(output) > 500 else output
    })

    if not success:
        _remember_failure(filename, source_code, "test_execution", output[-500:])

        # Distinguer : erreur d'import (test cassé) vs assertion (bug dans le source)
        is_test_broken = "ImportError" in output or "ModuleNotFoundError" in output or "SyntaxError" in output
        if is_test_broken:
            logger.warning(f"[CI/CD] Tests auto-générés défaillants (import/syntax) — PAS de rollback pour {filename}")
            await bus.publish("CI_PIPELINE_RESULT", {
                "filename": filename, "success": False,
                "detail": f"Tests auto-générés défaillants — source conservée"
            })
        else:
            _rollback(filepath)
            await bus.publish("CI_PIPELINE_RESULT", {
                "filename": filename, "success": False,
                "detail": f"Tests échoués — rollback effectué"
            })
        return

    # 5. Validation Architect (appel DIRECT)
    architect = orchestrator.agents.get("architect")
    if not architect:
        await bus.publish("CI_PIPELINE_RESULT", {
            "filename": filename, "success": False,
            "detail": "Agent architect non disponible"
        })
        return

    validation_prompt = (
        f"VALIDATION CI/CD : Le fichier '{filename}' a passé tous les tests automatiques.\n"
        f"Code source :\n```python\n{source_code[:3000]}\n```\n\n"
        f"Résultat pytest :\n{output[-1000:]}\n\n"
        f"Si le code est valide et sûr, réponds par 'VALIDÉ' suivi d'un commentaire. "
        f"Sinon, explique le problème."
    )

    try:
        arch_response = await architect.generate_content(validation_prompt)
    except Exception as e:
        await bus.publish("CI_PIPELINE_STEP", {
            "filename": filename, "step": "validation",
            "status": "error", "detail": str(e)
        })
        _remember_failure(filename, source_code, "validation", str(e))
        _rollback(filepath)
        await bus.publish("CI_PIPELINE_RESULT", {
            "filename": filename, "success": False,
            "detail": f"Échec validation Architect : {e}"
        })
        return

    validated = arch_response.strip().upper().startswith("VALIDÉ") or \
                arch_response.strip().upper().startswith("VALIDE")

    await bus.publish("CI_PIPELINE_STEP", {
        "filename": filename, "step": "validation",
        "status": "success" if validated else "error",
        "detail": arch_response[:300]
    })

    # 6. Résultat final
    if not validated:
        _remember_failure(filename, source_code, "validation", f"Architect a refusé : {arch_response[:300]}")
        _rollback(filepath)
        await bus.publish("CI_PIPELINE_RESULT", {
            "filename": filename, "success": False,
            "detail": f"Architect a refusé — rollback effectué"
        })
        return

    # Succès : mémorisation riche (code + tests + verdict)
    _remember_success(filename, source_code, test_code, arch_response)

    await bus.publish("CI_PIPELINE_RESULT", {
        "filename": filename, "success": True,
        "detail": f"Pipeline complet — {filename} déployé avec succès"
    })

    # Smart Restart si fichier système
    is_system_file = any(k in filepath for k in ["Agents", "core", "main.py", "config.py"])
    if is_system_file:
        logger.info(f"[SMART RESTART] Modification système détectée ({filename})")
        await asyncio.sleep(3)
        sys.exit(65)


# --- Listener bus ---

async def _on_artifact_created(data: dict):
    """Callback du bus ARTIFACT_CREATED. Filtre et lance le pipeline."""
    filepath = data.get("filepath", "")
    filename = data.get("filename", "")

    # Anti-boucle : ignorer les fichiers dans tests/
    normalized = filepath.replace("\\", "/")
    if "tests/" in normalized or "tests\\" in filepath:
        return

    # Ignorer les non-.py
    if not filename.endswith(".py"):
        return

    logger.info(f"[CI/CD] Pipeline déclenché pour : {filename}")
    asyncio.create_task(run_pipeline(filename, filepath))


# --- Start / Stop ---

def start():
    """Abonne le pipeline au bus."""
    bus.subscribe("ARTIFACT_CREATED", _on_artifact_created)
    os.makedirs(AUTO_TESTS_DIR, exist_ok=True)
    logger.info("[CI/CD] Pipeline démarré")


def stop():
    """Désabonne le pipeline du bus."""
    bus.unsubscribe("ARTIFACT_CREATED", _on_artifact_created)
    logger.info("[CI/CD] Pipeline arrêté")
