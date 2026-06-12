# core/test_runner.py — TestRunner unifie CI + Sandbox
# Fonctions partagees : execution pytest, pre-validation AST, selection de tests.
# 0 LLM, pur deterministe.

import ast
import asyncio
import logging
import os
import re
import sys
from typing import Optional, List, Dict

logger = logging.getLogger("TestRunner")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Prefixes de modules sources pour la pre-validation
_SOURCE_PREFIXES = ("core.", "Agents.", "tools.")

# Imports aliens (hallucinations LLM) — synchronise avec evolution_agent
_ALIEN_MODULES = frozenset({
    "django", "flask", "fastapi_users", "openai", "pygame", "tensorflow",
    "torch", "keras", "langchain", "streamlit", "plotly", "dash",
    "sqlalchemy", "celery", "scrapy", "boto3", "kubernetes",
})

# Stats globales partagees CI + sandbox
_stats = {
    "total_pytest_calls": 0,
    "total_pre_validation_catches": 0,
    "total_graph_selections": 0,
}


def get_stats() -> dict:
    """Stats globales du test runner."""
    return dict(_stats)


def reset_stats():
    """Reset pour les tests."""
    for k in _stats:
        _stats[k] = 0


# --- Parse sortie pytest ---

def parse_pytest_output(output: str) -> tuple:
    """Parse la ligne summary de pytest pour extraire passed/failed/errors.

    Returns:
        (passed, failed, errors)
    """
    passed = 0
    failed = 0
    errors = 0

    m_passed = re.search(r"(\d+) passed", output)
    m_failed = re.search(r"(\d+) failed", output)
    m_errors = re.search(r"(\d+) error", output)

    if m_passed:
        passed = int(m_passed.group(1))
    if m_failed:
        failed = int(m_failed.group(1))
    if m_errors:
        errors = int(m_errors.group(1))

    return passed, failed, errors


# --- Execution pytest ---

async def run_pytest(
    test_args: List[str],
    cwd: Optional[str] = None,
    timeout: int = 120,
    env_override: Optional[dict] = None,
    truncate: int = 2000,
) -> tuple:
    """Execute pytest de maniere unifiee.

    Args:
        test_args: Arguments pytest (ex: ["tests/test_foo.py", "-v"])
        cwd: Repertoire de travail (defaut: PROJECT_ROOT)
        timeout: Timeout en secondes
        env_override: Variables d'environnement supplementaires
        truncate: Tronquer la sortie a N chars (0 = pas de troncature)

    Returns:
        (success: bool, output: str, passed: int, failed: int, errors: int)
    """
    _stats["total_pytest_calls"] += 1

    cmd = [sys.executable, "-m", "pytest"] + test_args
    work_dir = cwd or PROJECT_ROOT

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    if env_override:
        env.update(env_override)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=work_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return False, f"TIMEOUT apres {timeout}s", 0, 0, 0

        output = stdout.decode("utf-8", errors="replace")
        if truncate and len(output) > truncate:
            output = output[:truncate] + "\n... (tronque)"

        passed, failed, errors = parse_pytest_output(output)
        success = proc.returncode == 0
        return success, output, passed, failed, errors

    except FileNotFoundError:
        return False, "python non trouve", 0, 0, 0
    except Exception as e:
        return False, f"Erreur execution pytest: {e}", 0, 0, 0


# --- Pre-validation AST ---

def pre_validate_ast(
    source_code: str,
    check_alien_imports: bool = False,
    check_project_imports: bool = False,
    search_dirs: Optional[List[str]] = None,
) -> Optional[str]:
    """Pre-validation AST du code source.

    Verifie syntaxe, imports aliens, imports projet existants.

    Args:
        source_code: Code source a valider
        check_alien_imports: Detecter les imports aliens (Django, Flask, etc.)
        check_project_imports: Verifier que les imports projet existent
        search_dirs: Repertoires ou chercher les modules projet

    Returns:
        None si OK, message d'erreur sinon
    """
    _stats["total_pre_validation_catches"]  # juste reference, on incremente en cas d'erreur

    # Check 1: Syntaxe
    try:
        tree = ast.parse(source_code)
    except SyntaxError as e:
        _stats["total_pre_validation_catches"] += 1
        return f"SyntaxError ligne {e.lineno}: {e.msg}"

    # Check 2: Imports aliens
    if check_alien_imports:
        aliens = _detect_alien_imports_ast(tree)
        if aliens:
            _stats["total_pre_validation_catches"] += 1
            return f"Imports aliens detectes ({', '.join(aliens)}) — hallucination LLM probable"

    # Check 3: Imports projet existants
    if check_project_imports and search_dirs:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if any(node.module.startswith(p) for p in _SOURCE_PREFIXES):
                    parts = node.module.split(".")
                    candidates = []
                    for d in search_dirs:
                        candidates.append(os.path.join(d, *parts) + ".py")
                        candidates.append(os.path.join(d, *parts, "__init__.py"))
                    if not any(os.path.isfile(c) for c in candidates):
                        _stats["total_pre_validation_catches"] += 1
                        return f"ImportError: module '{node.module}' introuvable"

    return None


def _detect_alien_imports_ast(tree: ast.AST) -> List[str]:
    """Detecte les imports aliens dans un AST deja parse."""
    aliens = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _ALIEN_MODULES:
                    aliens.append(root)
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in _ALIEN_MODULES:
                aliens.append(root)
    return sorted(set(aliens))


# --- Selection de tests ---

def select_tests_for_file(
    filepath: str,
    test_graph=None,
    supervised_map: Optional[Dict[str, List[str]]] = None,
    project_root: Optional[str] = None,
    verify_existence: bool = False,
    verify_dir: Optional[str] = None,
) -> Optional[List[str]]:
    """Selectionne les tests pertinents pour un fichier source.

    Utilise le graphe de dependances AST, le mapping supervise, et la derivation.

    Args:
        filepath: Chemin relatif du fichier source (ex: 'core/foo.py')
        test_graph: Instance TestGraph (optionnel)
        supervised_map: Mapping fichier → tests (ex: _SUPERVISED_TEST_MAP)
        project_root: Racine projet pour resolution
        verify_existence: Verifier que les fichiers tests existent
        verify_dir: Repertoire ou verifier l'existence des tests

    Returns:
        None si module hub (run complet necessaire)
        Liste de chemins tests relatifs
    """
    root = project_root or PROJECT_ROOT
    normalized = filepath.replace("\\", "/")

    # 1. Graphe de dependances AST (prioritaire)
    if test_graph is not None:
        relevant = test_graph.get_relevant_tests(normalized)
        if relevant is None:
            return None  # Module hub

        if relevant:
            _stats["total_graph_selections"] += 1
            if verify_existence and verify_dir:
                valid = [tf for tf in relevant
                         if os.path.isfile(os.path.join(verify_dir, tf))]
                if valid:
                    return valid
            else:
                return relevant

    # 2. Mapping supervise
    if supervised_map:
        # Extraire le chemin relatif
        proj_norm = root.replace("\\", "/")
        rel = normalized
        if rel.startswith(proj_norm):
            rel = rel[len(proj_norm):].lstrip("/")
        tests = supervised_map.get(rel)
        if tests:
            if verify_existence:
                check_dir = verify_dir or root
                valid = [tf for tf in tests
                         if os.path.isfile(os.path.join(check_dir, tf))]
                if valid:
                    return valid
            else:
                return list(tests)

    # 3. Derivation par nom (fallback)
    test_file = derive_test_file(normalized)
    if test_file:
        check_dir = verify_dir or root
        if os.path.isfile(os.path.join(check_dir, test_file)):
            return [test_file]

    return []


def derive_test_file(target_file: str) -> Optional[str]:
    """Derive le fichier de test associe a un fichier source.

    Ex: core/foo.py -> tests/test_foo.py
        Agents/bar_agent.py -> tests/test_bar_agent.py
    """
    normalized = target_file.replace("\\", "/")
    basename = os.path.basename(normalized)
    name, _ = os.path.splitext(basename)
    return f"tests/test_{name}.py"
