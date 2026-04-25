"""V30 (2026-04-25) — Tests de l'Exosquelette Syntaxique JSON.

Burn the boats : remplace les tests SEARCH/REPLACE V21-V29.
Tous les tests valident le nouveau format JSON :
  parse_v30_patch, apply_v30_patch, _v30_compute_indent,
  CodeSandbox.apply_patch_in_sandbox (refactore V30).
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import patch as mock_patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.capabilities.code_sandbox import (
    CodeSandbox,
    PatchResult,
    _ChecklistViolationError,
    _V30AnchorAmbiguousError,
    _V30AnchorFunctionNotFoundError,
    _V30AnchorNotFoundError,
    _V30InvalidActionError,
    _V30InvalidJSONError,
    _v30_compute_indent,
    _v30_extract_function_range,
    _v30_indent_new_code,
    apply_v30_patch,
    parse_v30_patch,
)


# ─── Helpers fixtures ─────────────────────────────────────────────────

def _build_mini_project(root: Path, target_content: str, test_content: str) -> None:
    (root / "target.py").write_text(target_content, encoding="utf-8")
    tests_dir = root / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "__init__.py").write_text("", encoding="utf-8")
    (tests_dir / "test_target.py").write_text(test_content, encoding="utf-8")
    (root / "conftest.py").write_text("", encoding="utf-8")


@pytest.fixture
def mini_project():
    tmpdir = tempfile.mkdtemp(prefix="promethee_test_v30_")
    try:
        yield Path(tmpdir)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def sandbox_singleton():
    CodeSandbox.reset_singleton()
    yield CodeSandbox()
    CodeSandbox.reset_singleton()


# ═══════════════════════════════════════════════════════════════════════
# 1. parse_v30_patch — JSON parsing robust
# ═══════════════════════════════════════════════════════════════════════

def test_parse_v30_valid_minimal():
    raw = json.dumps({
        "anchor_line": "    return x",
        "action": "insert_before",
        "new_code": "if x is None: return None",
    })
    p = parse_v30_patch(raw)
    assert p["anchor_line"] == "    return x"
    assert p["action"] == "insert_before"
    assert p["new_code"] == "if x is None: return None"
    assert p["anchor_function"] is None
    assert p["target_bug"] == ""


def test_parse_v30_with_function_and_target():
    raw = json.dumps({
        "target_bug": "div by zero",
        "anchor_function": "compute",
        "anchor_line": "        return total / count",
        "action": "insert_before",
        "new_code": "if count == 0:\n    return 0",
    })
    p = parse_v30_patch(raw)
    assert p["target_bug"] == "div by zero"
    assert p["anchor_function"] == "compute"


def test_parse_v30_strips_markdown_fences():
    raw = '```json\n{"anchor_line":"x","action":"insert_after","new_code":"y"}\n```'
    p = parse_v30_patch(raw)
    assert p["anchor_line"] == "x"
    assert p["action"] == "insert_after"


def test_parse_v30_extracts_from_narration():
    raw = (
        "Bien sûr, voici le patch:\n"
        '{"anchor_line":"return x","action":"replace_line","new_code":"return x or 0"}\n'
        "J'espère que ça aide."
    )
    p = parse_v30_patch(raw)
    assert p["action"] == "replace_line"


def test_parse_v30_invalid_json_raises():
    with pytest.raises(_V30InvalidJSONError):
        parse_v30_patch("Pas de JSON ici, juste du texte libre")
    with pytest.raises(_V30InvalidJSONError):
        parse_v30_patch("")
    with pytest.raises(_V30InvalidJSONError):
        parse_v30_patch("{malformed json no quotes}")


def test_parse_v30_invalid_action_raises():
    raw = json.dumps({
        "anchor_line": "x",
        "action": "DELETE_LINE",  # invalide
        "new_code": "y",
    })
    with pytest.raises(_V30InvalidActionError):
        parse_v30_patch(raw)


def test_parse_v30_missing_required_fields_raises():
    # anchor_line manquant
    with pytest.raises(_V30InvalidJSONError):
        parse_v30_patch(json.dumps({"action": "insert_before", "new_code": "x"}))
    # new_code manquant
    with pytest.raises(_V30InvalidJSONError):
        parse_v30_patch(json.dumps({"anchor_line": "x", "action": "insert_before"}))


# ═══════════════════════════════════════════════════════════════════════
# 2. _v30_compute_indent et _v30_indent_new_code
# ═══════════════════════════════════════════════════════════════════════

def test_compute_indent_no_indent():
    assert _v30_compute_indent("def foo():") == ""


def test_compute_indent_4_spaces():
    assert _v30_compute_indent("    return x") == "    "


def test_compute_indent_8_spaces():
    assert _v30_compute_indent("        return x") == "        "


def test_indent_new_code_preserves_empty_lines():
    code = "if x:\n    return\n\nreturn None"
    out = _v30_indent_new_code(code, "    ")
    lines = out.splitlines()
    assert lines[0] == "    if x:"
    assert lines[1] == "        return"
    assert lines[2] == ""  # ligne vide non-prefixee
    assert lines[3] == "    return None"


# ═══════════════════════════════════════════════════════════════════════
# 3. apply_v30_patch — actions et erreurs
# ═══════════════════════════════════════════════════════════════════════

SAMPLE_SOURCE = textwrap.dedent("""\
    def foo(items):
        total = 0
        for x in items:
            total += x
        return total / len(items)

    def bar(text):
        return text.upper()
""")


def test_v30_insert_before_with_function_scope():
    patch = {
        "anchor_function": "foo",
        "anchor_line": "    return total / len(items)",
        "action": "insert_before",
        "new_code": "if not items:\n    return 0",
    }
    out = apply_v30_patch(SAMPLE_SOURCE, patch)
    assert "if not items:" in out
    assert "    if not items:" in out          # 4 espaces (indent de l'anchor)
    assert "        return 0" in out           # 8 espaces (indent + 4 du new_code)
    assert "    return total / len(items)" in out  # ligne d'origine preservee
    # def bar inchangee
    assert "def bar(text):" in out


def test_v30_insert_after():
    patch = {
        "anchor_function": "bar",
        "anchor_line": "    return text.upper()",
        "action": "insert_after",
        "new_code": "# unreachable",
    }
    out = apply_v30_patch(SAMPLE_SOURCE, patch)
    lines = out.splitlines()
    idx = next(i for i, l in enumerate(lines) if "return text.upper()" in l)
    assert "# unreachable" in lines[idx + 1]


def test_v30_replace_line():
    patch = {
        "anchor_function": "bar",
        "anchor_line": "    return text.upper()",
        "action": "replace_line",
        "new_code": "return text.lower()",
    }
    out = apply_v30_patch(SAMPLE_SOURCE, patch)
    assert "return text.upper()" not in out
    assert "    return text.lower()" in out


def test_v30_anchor_not_found():
    patch = {
        "anchor_line": "    return ZZZ_does_not_exist",
        "action": "insert_after",
        "new_code": "x = 1",
    }
    with pytest.raises(_V30AnchorNotFoundError):
        apply_v30_patch(SAMPLE_SOURCE, patch)


def test_v30_anchor_ambiguous_without_function():
    """Sans anchor_function, une ligne dupliquee est ambigue."""
    src = "def a():\n    return 0\ndef b():\n    return 0\n"
    patch = {
        "anchor_line": "    return 0",
        "action": "insert_before",
        "new_code": "pass",
    }
    with pytest.raises(_V30AnchorAmbiguousError) as exc_info:
        apply_v30_patch(src, patch)
    assert exc_info.value.count == 2


def test_v30_anchor_function_resolves_ambiguity():
    """Avec anchor_function, l'ambiguite est levee."""
    src = "def a():\n    return 0\ndef b():\n    return 0\n"
    patch = {
        "anchor_function": "b",
        "anchor_line": "    return 0",
        "action": "insert_before",
        "new_code": "pass",
    }
    out = apply_v30_patch(src, patch)
    # 'pass' insere SEULEMENT dans b (pas dans a)
    lines = out.splitlines()
    a_idx = next(i for i, l in enumerate(lines) if "def a():" in l)
    b_idx = next(i for i, l in enumerate(lines) if "def b():" in l)
    # apres a(): pas de 'pass'
    a_body = "\n".join(lines[a_idx:b_idx])
    assert "pass" not in a_body
    # dans b(): 'pass' present
    assert "pass" in "\n".join(lines[b_idx:])


def test_v30_anchor_function_not_found():
    patch = {
        "anchor_function": "non_existant_func",
        "anchor_line": "x",
        "action": "insert_before",
        "new_code": "y",
    }
    with pytest.raises(_V30AnchorFunctionNotFoundError):
        apply_v30_patch(SAMPLE_SOURCE, patch)


# ═══════════════════════════════════════════════════════════════════════
# 4. V29 checklist integration dans replace_line
# ═══════════════════════════════════════════════════════════════════════

def test_v30_replace_line_blocked_by_checklist():
    """V29 + V30 : replace_line cible une ligne lines_to_preserve -> raise."""
    patch = {
        "anchor_function": "bar",
        "anchor_line": "    return text.upper()",
        "action": "replace_line",
        "new_code": "return text",
    }
    checklist = {
        "fallback": False,
        "lines_to_preserve": [
            {"line_text": "    return text.upper()", "reason": "logique critique"}
        ],
    }
    with pytest.raises(_ChecklistViolationError):
        apply_v30_patch(SAMPLE_SOURCE, patch, checklist=checklist)


def test_v30_insert_before_passes_with_checklist():
    """V29 + V30 : insert_before sur la meme ligne -> OK (pas de suppression)."""
    patch = {
        "anchor_function": "bar",
        "anchor_line": "    return text.upper()",
        "action": "insert_before",
        "new_code": "if text is None: return ''",
    }
    checklist = {
        "fallback": False,
        "lines_to_preserve": [
            {"line_text": "    return text.upper()", "reason": "logique critique"}
        ],
    }
    # Pas de raise — insert ne supprime pas
    out = apply_v30_patch(SAMPLE_SOURCE, patch, checklist=checklist)
    assert "    return text.upper()" in out
    assert "if text is None" in out


# ═══════════════════════════════════════════════════════════════════════
# 5. End-to-end : CodeSandbox.apply_patch_in_sandbox avec V30
# ═══════════════════════════════════════════════════════════════════════

@mock_patch("core.capabilities.code_sandbox._check_pytest_plugin_available", return_value=False)
def test_apply_patch_in_sandbox_v30_success(mock_no_testmon, mini_project, sandbox_singleton):
    """Flow complet V30 : insert_before guard qui ne casse aucun test."""
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

    surgeon_output = json.dumps({
        "target_bug": "add commentaire",
        "anchor_function": "add",
        "anchor_line": "    return a + b",
        "action": "insert_before",
        "new_code": "# V30 patch",
    })

    result = sandbox_singleton.apply_patch_in_sandbox(
        surgeon_output=surgeon_output,
        target_file="target.py",
        run_full_tests=True,
        project_root=str(mini_project),
        regression_timeout_s=60,
    )
    assert result.status == "success", (
        f"Got {result.status}, output={result.test_output[:500]}, err={result.error_message}"
    )
    assert result.blocks_applied == 1
    assert result.tests_passed >= 1
    assert result.unified_diff != ""
    # Source réel inchangé (sandbox isolation)
    real_target = (mini_project / "target.py").read_text(encoding="utf-8")
    assert "# V30 patch" not in real_target


@mock_patch("core.capabilities.code_sandbox._check_pytest_plugin_available", return_value=False)
def test_apply_patch_in_sandbox_v30_invalid_json(mock_no_testmon, mini_project, sandbox_singleton):
    """Sortie pas JSON -> status=invalid_json."""
    _build_mini_project(mini_project, "x = 1\n", "def test(): pass\n")
    result = sandbox_singleton.apply_patch_in_sandbox(
        surgeon_output="Pas de JSON, juste du texte",
        target_file="target.py",
        run_full_tests=False,
        project_root=str(mini_project),
    )
    assert result.status == "invalid_json"


@mock_patch("core.capabilities.code_sandbox._check_pytest_plugin_available", return_value=False)
def test_apply_patch_in_sandbox_v30_anchor_not_found(mock_no_testmon, mini_project, sandbox_singleton):
    """anchor_line n'existe pas -> status=anchor_not_found."""
    _build_mini_project(mini_project, "x = 1\n", "def test(): pass\n")
    surgeon_output = json.dumps({
        "anchor_line": "    return ghost",
        "action": "insert_before",
        "new_code": "pass",
    })
    result = sandbox_singleton.apply_patch_in_sandbox(
        surgeon_output=surgeon_output,
        target_file="target.py",
        run_full_tests=False,
        project_root=str(mini_project),
    )
    assert result.status == "anchor_not_found"
    feedback = result.format_traceback_for_surgeon()
    assert "anchor_line introuvable" in feedback or "anchor" in feedback.lower()


def test_apply_patch_in_sandbox_v30_patch_impossible(mini_project, sandbox_singleton):
    """[PATCH_IMPOSSIBLE: ...] -> status=patch_impossible."""
    _build_mini_project(mini_project, "x = 1\n", "def test(): pass\n")
    result = sandbox_singleton.apply_patch_in_sandbox(
        surgeon_output="[PATCH_IMPOSSIBLE: audit insuffisant]",
        target_file="target.py",
        run_full_tests=False,
        project_root=str(mini_project),
    )
    assert result.status == "patch_impossible"
