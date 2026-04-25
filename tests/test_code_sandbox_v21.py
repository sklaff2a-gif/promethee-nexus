"""V21 (2026-04-25) — Tests du pipeline d'auto-correction (MEDIC).

Couvre :
  - parse_search_replace_blocks : bloc unique, vide, multiples
  - apply_search_replace : unique, not_found, ambiguous
  - apply_patch_in_sandbox : success path, syntax_error path, test_failed path

Les tests E2E utilisent une mini-fixture projet temporaire pour éviter
de copier le vrai projet (50MB+) à chaque test.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

# Ajout du parent au path pour `import core.capabilities.code_sandbox`
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.capabilities.code_sandbox import (
    CodeSandbox,
    PatchResult,
    _SearchAmbiguousError,
    _SearchNotFoundError,
    apply_search_replace,
    parse_search_replace_blocks,
)


# ─── Helpers fixtures ─────────────────────────────────────────────────

def _build_mini_project(root: Path, target_content: str, test_content: str) -> None:
    """Construit un mini-projet Python testable : target.py + tests/test_target.py."""
    (root / "target.py").write_text(target_content, encoding="utf-8")
    tests_dir = root / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "__init__.py").write_text("", encoding="utf-8")
    (tests_dir / "test_target.py").write_text(test_content, encoding="utf-8")
    # conftest.py vide explicite pour éviter de remonter à un conftest parent
    (root / "conftest.py").write_text("", encoding="utf-8")


@pytest.fixture
def mini_project():
    """Mini-projet temporaire jetable : target.py + tests/test_target.py."""
    tmpdir = tempfile.mkdtemp(prefix="promethee_test_v21_")
    try:
        yield Path(tmpdir)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def sandbox_singleton():
    """Reset du singleton pour isolation entre tests."""
    CodeSandbox.reset_singleton()
    yield CodeSandbox()
    CodeSandbox.reset_singleton()


# ═══════════════════════════════════════════════════════════════════════
# 1-3. Tests parse_search_replace_blocks
# ═══════════════════════════════════════════════════════════════════════

def test_parse_single_valid_block():
    """1 bloc bien formé → liste de 1 tuple (search, replace)."""
    text = textwrap.dedent("""\
        Une intro narrative ignorée.
        <<<<<<< SEARCH
        def foo(x):
            return x
        =======
        def foo(x):
            return x * 2
        >>>>>>> REPLACE
        Et du texte après.
    """)
    blocks = parse_search_replace_blocks(text)
    assert len(blocks) == 1
    search, replace = blocks[0]
    assert "def foo(x):\n    return x" == search
    assert "def foo(x):\n    return x * 2" == replace


def test_parse_no_block_raises():
    """0 bloc → ValueError explicite."""
    with pytest.raises(ValueError, match="Aucun bloc"):
        parse_search_replace_blocks("Juste du texte sans marqueurs SEARCH.")
    with pytest.raises(ValueError):
        parse_search_replace_blocks("")


def test_parse_three_sequential_blocks():
    """3 blocs consécutifs → liste ordonnée de 3 tuples."""
    text = (
        "<<<<<<< SEARCH\nA1\n=======\nB1\n>>>>>>> REPLACE\n"
        "noise\n"
        "<<<<<<< SEARCH\nA2\nA2bis\n=======\nB2\nB2bis\n>>>>>>> REPLACE\n"
        "more noise\n"
        "<<<<<<< SEARCH\nA3\n=======\nB3\n>>>>>>> REPLACE\n"
    )
    blocks = parse_search_replace_blocks(text)
    assert len(blocks) == 3
    assert blocks[0] == ("A1", "B1")
    assert blocks[1] == ("A2\nA2bis", "B2\nB2bis")
    assert blocks[2] == ("A3", "B3")


# ═══════════════════════════════════════════════════════════════════════
# 4-6. Tests apply_search_replace
# ═══════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════
# V29 — Validation checklist Scrub Nurse (absolue sur source patche final)
# ═══════════════════════════════════════════════════════════════════════

def test_v29_checklist_required_line_kept_in_patched_source():
    """V29 : la checklist required_line est preservee → no raise."""
    from core.capabilities.code_sandbox import _ChecklistViolationError
    source = "    parts = x.split()\n    return parts[1]\n    return text\n"
    # SEARCH 2 lignes, REPLACE 4 lignes (3 dont 'parts = x.split()' verbatim)
    search = "    parts = x.split()\n    return parts[1]\n    return text"
    replace = (
        "    parts = x.split()\n"
        "    if not parts:\n"
        "        return None\n"
        "    return parts[1]\n"
        "    return text"
    )
    checklist = {
        "fallback": False,
        "lines_to_preserve": [
            {"line_text": "    parts = x.split()", "reason": "definit parts"}
        ],
    }
    res = apply_search_replace(source, [(search, replace)], checklist=checklist)
    assert "    parts = x.split()" in res
    assert "if not parts:" in res


def test_v29_checklist_violation_raises():
    """V29 : si une required_line disparait du source patche, raise."""
    from core.capabilities.code_sandbox import _ChecklistViolationError
    source = "    parts = x.split()\n    return parts[1]\n    return text\n"
    # SEARCH = 3 lignes, REPLACE = 3 lignes (perd parts=, garde return parts[1] et return text)
    # V27 : 2/3 lignes preservees → seuil V27 OK (max(2, 3//2)=2)
    # V29 : 'parts = x.split()' n'est plus dans le patched final → REJECT
    search = "    parts = x.split()\n    return parts[1]\n    return text"
    replace = "    if x:\n        return parts[1]\n    return text"
    checklist = {
        "fallback": False,
        "lines_to_preserve": [
            {"line_text": "    parts = x.split()", "reason": "definit parts"}
        ],
    }
    with pytest.raises(_ChecklistViolationError) as exc_info:
        apply_search_replace(source, [(search, replace)], checklist=checklist)
    err = exc_info.value
    assert err.missing_line == "    parts = x.split()"
    assert "definit parts" in err.reason
    assert len(err.violations) == 1


def test_v29_checklist_fallback_skips_validation():
    """V29 : si checklist.fallback=True, aucune validation, comportement V28."""
    source = "x = 1\n"
    blocks = [("x = 1", "y = 2")]
    checklist = {"fallback": True}
    # Pas de raise — V29 skip
    res = apply_search_replace(source, blocks, checklist=checklist)
    assert res == "y = 2\n"


def test_v29_checklist_none_skips_validation():
    """V29 : checklist=None (pas de Nurse) → comportement V28 transparent."""
    source = "x = 1\n"
    blocks = [("x = 1", "y = 2")]
    res = apply_search_replace(source, blocks, checklist=None)
    assert res == "y = 2\n"


def test_apply_search_replace_unique_success():
    """SEARCH unique → patched contient REPLACE."""
    source = "def foo(x):\n    return x\n\ndef bar(y):\n    return y\n"
    blocks = [("def foo(x):\n    return x", "def foo(x):\n    return x * 2")]
    patched = apply_search_replace(source, blocks)
    assert "return x * 2" in patched
    assert "def bar(y):" in patched  # le reste intact


def test_apply_search_replace_not_found_raises():
    """SEARCH absent → _SearchNotFoundError avec block_index et search_text."""
    source = "def foo(x):\n    return x\n"
    blocks = [("def baz(z):\n    return z", "REPLACE")]
    with pytest.raises(_SearchNotFoundError) as exc_info:
        apply_search_replace(source, blocks)
    err = exc_info.value
    assert err.block_index == 0
    assert err.search_text == "def baz(z):\n    return z"
    assert err.applied_count == 0
    assert "introuvable" in str(err).lower()


def test_apply_search_replace_ambiguous_raises():
    """SEARCH apparaissant 2 fois → _SearchAmbiguousError avec count=2."""
    source = "x = 1\nx = 1\nx = 2\n"
    blocks = [("x = 1", "x = 99")]
    with pytest.raises(_SearchAmbiguousError) as exc_info:
        apply_search_replace(source, blocks)
    err = exc_info.value
    assert err.block_index == 0
    assert err.count == 2
    assert err.applied_count == 0
    # Recommandation explicite au LLM
    assert "contexte" in str(err).lower()


# ═══════════════════════════════════════════════════════════════════════
# 7-8. Tests E2E apply_patch_in_sandbox
# ═══════════════════════════════════════════════════════════════════════

@patch("core.capabilities.code_sandbox._check_pytest_plugin_available", return_value=False)
def test_apply_patch_in_sandbox_success(mock_no_testmon, mini_project, sandbox_singleton):
    """Flow complet : patch trivial qui n'altère pas le comportement → success."""
    target = textwrap.dedent("""\
        def add(a, b):
            return a + b
    """)
    test = textwrap.dedent("""\
        from target import add

        def test_add():
            assert add(2, 3) == 5

        def test_add_zero():
            assert add(0, 0) == 0
    """)
    _build_mini_project(mini_project, target, test)

    surgeon_output = textwrap.dedent("""\
        <<<<<<< SEARCH
        def add(a, b):
            return a + b
        =======
        def add(a, b):
            # V21 patch test
            return a + b
        >>>>>>> REPLACE
    """)

    result = sandbox_singleton.apply_patch_in_sandbox(
        surgeon_output=surgeon_output,
        target_file="target.py",
        run_full_tests=True,
        project_root=str(mini_project),
        regression_timeout_s=60,
    )

    assert result.status == "success", (
        f"Expected success, got {result.status}. "
        f"Test output:\n{result.test_output}\nError: {result.error_message}"
    )
    assert result.blocks_applied == 1
    assert result.tests_passed >= 2
    assert result.tests_failed == 0
    assert result.unified_diff != ""
    assert "V21 patch test" in result.unified_diff
    assert result.test_strategy == "full_suite"
    # Garantie chirurgicale : le fichier réel n'a PAS été modifié
    real_target = (mini_project / "target.py").read_text(encoding="utf-8")
    assert "V21 patch test" not in real_target


@patch("core.capabilities.code_sandbox._check_pytest_plugin_available", return_value=False)
def test_apply_patch_in_sandbox_syntax_error(mock_no_testmon, mini_project, sandbox_singleton):
    """REPLACE introduit une syntax error → status=syntax_error, pas de pytest lancé."""
    target = textwrap.dedent("""\
        def add(a, b):
            return a + b
    """)
    test = "from target import add\ndef test_x(): assert add(1, 2) == 3\n"
    _build_mini_project(mini_project, target, test)

    surgeon_output = textwrap.dedent("""\
        <<<<<<< SEARCH
        def add(a, b):
            return a + b
        =======
        def add(a, b):
            return a +
        >>>>>>> REPLACE
    """)

    result = sandbox_singleton.apply_patch_in_sandbox(
        surgeon_output=surgeon_output,
        target_file="target.py",
        run_full_tests=True,
        project_root=str(mini_project),
        regression_timeout_s=60,
    )

    assert result.status == "syntax_error", f"Got {result.status}: {result.error_message}"
    assert result.blocks_applied == 1
    assert "SyntaxError" in result.compile_stderr or "invalid syntax" in result.compile_stderr.lower()
    assert result.tests_passed == 0
    # format_traceback_for_surgeon doit fournir un message exploitable
    feedback = result.format_traceback_for_surgeon()
    assert "SYNTAX" in feedback.upper()


# ═══════════════════════════════════════════════════════════════════════
# Tests bonus — couverture des autres statuses
# ═══════════════════════════════════════════════════════════════════════

@patch("core.capabilities.code_sandbox._check_pytest_plugin_available", return_value=False)
def test_apply_patch_in_sandbox_test_failed(mock_no_testmon, mini_project, sandbox_singleton):
    """Patch valide syntaxiquement mais qui casse un test existant."""
    target = textwrap.dedent("""\
        def add(a, b):
            return a + b
    """)
    test = textwrap.dedent("""\
        from target import add
        def test_add():
            assert add(2, 3) == 5
    """)
    _build_mini_project(mini_project, target, test)

    surgeon_output = textwrap.dedent("""\
        <<<<<<< SEARCH
        def add(a, b):
            return a + b
        =======
        def add(a, b):
            return a - b
        >>>>>>> REPLACE
    """)

    result = sandbox_singleton.apply_patch_in_sandbox(
        surgeon_output=surgeon_output,
        target_file="target.py",
        run_full_tests=True,
        project_root=str(mini_project),
        regression_timeout_s=60,
    )

    assert result.status == "test_failed", f"Got {result.status}"
    assert result.tests_failed >= 1
    feedback = result.format_traceback_for_surgeon()
    assert "REGRESSION" in feedback.upper() or "TESTS" in feedback.upper()


def test_apply_patch_in_sandbox_no_blocks(sandbox_singleton, mini_project):
    """Surgeon a oublié les marqueurs → status=no_blocks, pas de sandbox monté."""
    _build_mini_project(mini_project, "x = 1\n", "def test(): assert True\n")
    result = sandbox_singleton.apply_patch_in_sandbox(
        surgeon_output="J'ai oublié de mettre les marqueurs SEARCH/REPLACE.",
        target_file="target.py",
        run_full_tests=False,
        project_root=str(mini_project),
    )
    assert result.status == "no_blocks"
    assert result.blocks_applied == 0
    feedback = result.format_traceback_for_surgeon()
    assert "FORMAT" in feedback.upper()


def test_apply_patch_in_sandbox_patch_impossible(sandbox_singleton, mini_project):
    """Surgeon a déclaré PATCH_IMPOSSIBLE → status court-circuité."""
    _build_mini_project(mini_project, "x = 1\n", "def test(): assert True\n")
    result = sandbox_singleton.apply_patch_in_sandbox(
        surgeon_output="[PATCH_IMPOSSIBLE: l'audit ne précise pas la fonction à patcher]",
        target_file="target.py",
        run_full_tests=False,
        project_root=str(mini_project),
    )
    assert result.status == "patch_impossible"
    assert "audit ne précise pas" in result.error_message
    # format_traceback_for_surgeon vide pour ce status (pas de retry utile)
    assert result.format_traceback_for_surgeon() == ""
