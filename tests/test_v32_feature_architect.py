"""V32 (2026-04-26) — Tests Session 1 : sandbox multi-files +
ScrubNurse decomposition + FeatureArchitectAgent.

Couvre :
  - parse_v30_patch detecte format {"files": [...]}
  - _v32_validate_file_entry rejette les entrees malformees
  - apply_v32_create_file ecrit + leve _V32FileExistsError
  - apply_v32_append_block ajoute en fin
  - _normalize_v32_decomposition (NURSE TDD)
  - FeatureArchitectAgent prompt et generate_feature
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.capabilities.code_sandbox import (
    parse_v30_patch,
    apply_v32_create_file,
    apply_v32_append_block,
    _v32_validate_file_entry,
    _V32FileExistsError,
    _V32InvalidMultiFilesError,
    _V30InvalidJSONError,
    _V30InvalidActionError,
)


# ═══════════════════════════════════════════════════════════════════════
# 1. parse_v30_patch — format multi-files
# ═══════════════════════════════════════════════════════════════════════

def test_parse_multi_files_basic():
    """Format {"files": [...]} avec 2 entrees create_file."""
    raw = """{
        "feature_name": "test_feat",
        "files": [
            {"target_file": "core/x.py", "action": "create_file", "new_code": "x = 1"},
            {"target_file": "tests/test_x.py", "action": "create_file", "new_code": "def test_x(): pass"}
        ]
    }"""
    parsed = parse_v30_patch(raw)
    assert parsed.get("is_multi_files") is True
    assert len(parsed["files"]) == 2
    assert parsed["files"][0]["target_file"] == "core/x.py"
    assert parsed["files"][0]["action"] == "create_file"
    assert parsed["feature_name"] == "test_feat"


def test_parse_multi_files_rejects_missing_target_file():
    """Entree sans target_file -> _V32InvalidMultiFilesError."""
    raw = '{"files": [{"action": "create_file", "new_code": "x"}]}'
    with pytest.raises(_V32InvalidMultiFilesError):
        parse_v30_patch(raw)


def test_parse_multi_files_rejects_invalid_action():
    """Action inconnue dans entree -> erreur."""
    raw = '{"files": [{"target_file": "x.py", "action": "bogus_action", "new_code": "x"}]}'
    with pytest.raises(_V32InvalidMultiFilesError):
        parse_v30_patch(raw)


def test_parse_multi_files_empty_array_rejected():
    """files: [] -> _V32InvalidMultiFilesError."""
    raw = '{"files": []}'
    with pytest.raises(_V32InvalidMultiFilesError):
        parse_v30_patch(raw)


def test_parse_multi_files_v30_action_requires_anchor():
    """Action V30 (replace_line) sans anchor_line -> erreur."""
    raw = """{"files": [
        {"target_file": "x.py", "action": "replace_line", "new_code": "y"}
    ]}"""
    with pytest.raises(_V32InvalidMultiFilesError):
        parse_v30_patch(raw)


def test_parse_multi_files_v30_action_with_anchor_ok():
    """Action V30 (replace_line) avec anchor_line -> OK."""
    raw = """{"files": [
        {"target_file": "x.py", "action": "replace_line",
         "anchor_line": "old", "new_code": "new"}
    ]}"""
    parsed = parse_v30_patch(raw)
    assert parsed["is_multi_files"] is True
    assert parsed["files"][0]["anchor_line"] == "old"


def test_parse_single_patch_unaffected_by_v32():
    """Le format V30 single doit toujours marcher."""
    raw = """{
        "anchor_line": "x = 1",
        "action": "insert_before",
        "new_code": "y = 2"
    }"""
    parsed = parse_v30_patch(raw)
    assert parsed.get("is_multi_files") is None
    assert parsed.get("is_multi") is None
    assert parsed["action"] == "insert_before"


# ═══════════════════════════════════════════════════════════════════════
# 2. apply_v32_create_file — ecriture + securite anti-ecrasement
# ═══════════════════════════════════════════════════════════════════════

def test_create_file_writes_new_file(tmp_path):
    """create_file ecrit correctement le fichier."""
    full = apply_v32_create_file(
        target_file="utils/foo.py",
        new_code="def foo():\n    return 1",
        project_root=str(tmp_path),
    )
    assert os.path.exists(full)
    content = Path(full).read_text(encoding="utf-8")
    assert "def foo():" in content
    assert content.endswith("\n")  # terminaison \n garantie


def test_create_file_creates_parent_dirs(tmp_path):
    """create_file cree les repertoires intermediaires."""
    full = apply_v32_create_file(
        target_file="deep/nested/dir/foo.py",
        new_code="x = 1",
        project_root=str(tmp_path),
    )
    assert os.path.exists(full)


def test_create_file_refuses_existing(tmp_path):
    """create_file leve _V32FileExistsError si le fichier existe."""
    target = tmp_path / "existing.py"
    target.write_text("# existant\n", encoding="utf-8")
    with pytest.raises(_V32FileExistsError) as exc_info:
        apply_v32_create_file(
            target_file="existing.py",
            new_code="overwrite",
            project_root=str(tmp_path),
        )
    assert exc_info.value.target_file == "existing.py"
    # Le fichier n'a PAS ete ecrase
    assert target.read_text(encoding="utf-8") == "# existant\n"


# ═══════════════════════════════════════════════════════════════════════
# 3. apply_v32_append_block — ajout en fin
# ═══════════════════════════════════════════════════════════════════════

def test_append_block_appends_to_existing(tmp_path):
    """append_block ajoute correctement au fichier existant."""
    target = tmp_path / "module.py"
    target.write_text("def first():\n    pass\n", encoding="utf-8")
    apply_v32_append_block(
        target_file="module.py",
        new_code="def second():\n    pass",
        project_root=str(tmp_path),
    )
    content = target.read_text(encoding="utf-8")
    assert "def first():" in content
    assert "def second():" in content
    # Separation : 2 lignes blanches entre les fonctions (PEP 8)
    assert "\n\ndef second():" in content


def test_append_block_refuses_missing(tmp_path):
    """append_block leve FileNotFoundError si target absent."""
    with pytest.raises(FileNotFoundError):
        apply_v32_append_block(
            target_file="missing.py",
            new_code="x = 1",
            project_root=str(tmp_path),
        )


# ═══════════════════════════════════════════════════════════════════════
# 4. ScrubNurse V32 — decomposition TDD
# ═══════════════════════════════════════════════════════════════════════

def test_normalize_v32_decomposition_valid():
    """JSON complet -> decomposition normalisee."""
    from Agents.scrub_nurse_agent import _normalize_v32_decomposition
    raw = {
        "function_signature": "foo(x: int) -> str",
        "module_path": "core/foo.py",
        "test_module_path": "tests/auto/test_foo.py",
        "test_cases": [
            {"description": "happy", "input_repr": "x=1", "expected_repr": "'1'"}
        ],
        "edge_cases": ["zero"],
        "doctrine_hints": ["return str"],
        "forbidden_imports": ["bs4"],
        "confidence": 0.9,
    }
    out = _normalize_v32_decomposition(raw)
    assert out["fallback"] is False
    assert out["function_signature"] == "foo(x: int) -> str"
    assert len(out["test_cases"]) == 1
    assert out["confidence"] == 0.9


def test_normalize_v32_decomposition_fallback_no_signature():
    """Pas de signature -> fallback."""
    from Agents.scrub_nurse_agent import _normalize_v32_decomposition
    out = _normalize_v32_decomposition({
        "module_path": "x.py",
        "test_cases": [{"description": "d", "input_repr": "i", "expected_repr": "e"}],
    })
    assert out["fallback"] is True


def test_normalize_v32_decomposition_infers_test_path():
    """test_module_path manquant -> infere depuis module_path."""
    from Agents.scrub_nurse_agent import _normalize_v32_decomposition
    out = _normalize_v32_decomposition({
        "function_signature": "f(x: int) -> int",
        "module_path": "core/utils/parser.py",
        "test_cases": [{"description": "d", "input_repr": "i", "expected_repr": "e"}],
    })
    assert out["fallback"] is False
    assert out["test_module_path"] == "tests/auto/test_parser.py"


# ═══════════════════════════════════════════════════════════════════════
# 5. FeatureArchitectAgent
# ═══════════════════════════════════════════════════════════════════════

def _make_naked_architect():
    from Agents.feature_architect_agent import (
        FeatureArchitectAgent, FEATURE_ARCHITECT_SYSTEM_PROMPT,
    )
    agent = FeatureArchitectAgent.__new__(FeatureArchitectAgent)
    agent.name = "feature_architect"
    agent.system_instructions = FEATURE_ARCHITECT_SYSTEM_PROMPT
    agent.log_thought = MagicMock()
    return agent


def test_feature_architect_prompt_contains_spec():
    arch = _make_naked_architect()
    decomp = {
        "feature_name": "extract_blocks",
        "function_signature": "extract_blocks(text: str) -> list[str]",
        "module_path": "core/utils/parser.py",
        "test_module_path": "tests/auto/test_parser.py",
        "test_cases": [{"description": "h", "input_repr": "x", "expected_repr": "y"}],
    }
    prompt = arch._build_architect_prompt(decomp)
    assert "---SPEC---" in prompt
    assert "extract_blocks" in prompt
    assert "extract_blocks(text: str) -> list[str]" in prompt
    assert "RAPPEL V32" in prompt


def test_feature_architect_prompt_with_rag():
    arch = _make_naked_architect()
    decomp = {
        "feature_name": "f",
        "function_signature": "f() -> None",
        "module_path": "x.py",
        "test_cases": [{"description": "d", "input_repr": "i", "expected_repr": "e"}],
    }
    prompt = arch._build_architect_prompt(decomp, rag_context="----[RAG ZONE]----\nfoo")
    assert "----[RAG ZONE]----" in prompt


@pytest.mark.asyncio
async def test_feature_architect_rejects_fallback_decomposition():
    arch = _make_naked_architect()
    arch.generate_content = AsyncMock(return_value="...")
    with pytest.raises(ValueError):
        await arch.generate_feature({"fallback": True})


@pytest.mark.asyncio
async def test_feature_architect_returns_raw_llm_output():
    arch = _make_naked_architect()
    fake_json = '{"feature_name": "f", "files": []}'
    arch.generate_content = AsyncMock(return_value=fake_json)
    decomp = {
        "feature_name": "f",
        "function_signature": "f() -> None",
        "module_path": "x.py",
        "test_cases": [{"description": "d", "input_repr": "i", "expected_repr": "e"}],
    }
    out = await arch.generate_feature(decomp)
    assert out == fake_json
