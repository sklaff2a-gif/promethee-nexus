"""V30 (2026-04-25) — Tests SurgeonAgent (Exosquelette JSON).

Burn the boats : burned all SEARCH/REPLACE assertions. Tests the new
JSON-based prompt and parse pipeline.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from Agents.surgeon_agent import (
    SURGEON_SYSTEM_PROMPT,
    SURGEON_TAIL_REMINDER,
    SurgeonAgent,
)
from core.capabilities.code_sandbox import (
    _V30InvalidJSONError,
    parse_v30_patch,
)


def _make_naked_surgeon() -> SurgeonAgent:
    agent = SurgeonAgent.__new__(SurgeonAgent)
    agent.name = "surgeon"
    agent.system_instructions = SURGEON_SYSTEM_PROMPT
    agent.log_thought = MagicMock()
    return agent


@pytest.fixture
def surgeon():
    return _make_naked_surgeon()


# ═══════════════════════════════════════════════════════════════════════
# 1-2. System prompt V30 — règles JSON
# ═══════════════════════════════════════════════════════════════════════

def test_system_prompt_v30_describes_json_schema():
    """Le prompt V30 décrit le schema JSON et liste les actions valides."""
    p = SURGEON_SYSTEM_PROMPT
    # Champs requis
    assert "target_bug" in p
    assert "anchor_line" in p
    assert "anchor_function" in p
    assert "action" in p
    assert "new_code" in p
    # Actions valides
    assert "insert_before" in p
    assert "insert_after" in p
    assert "replace_line" in p
    # Format
    assert "JSON" in p


def test_system_prompt_v30_demands_no_narration():
    p = SURGEON_SYSTEM_PROMPT.lower()
    # Variantes acceptées
    assert "narration" in p or "narrative" in p
    assert "json" in p
    # PATCH_IMPOSSIBLE marker
    assert "patch_impossible" in p


def test_tail_reminder_v30_lists_required_fields():
    tail = SURGEON_TAIL_REMINDER
    assert "JSON" in tail
    assert "target_bug" in tail
    assert "anchor_line" in tail
    assert "action" in tail
    assert "new_code" in tail


# ═══════════════════════════════════════════════════════════════════════
# 3. _build_surgeon_prompt — structure SOURCE/AUDIT/CHECKLIST
# ═══════════════════════════════════════════════════════════════════════

def test_build_prompt_contains_source_audit_and_tail(surgeon):
    prompt = surgeon._build_surgeon_prompt(
        audit_report="audit description SENTINEL_AUDIT",
        target_source="def f(): return 1",
    )
    assert "---SOURCE---" in prompt
    assert "def f(): return 1" in prompt
    assert "---AUDIT---" in prompt
    assert "SENTINEL_AUDIT" in prompt
    assert "RAPPEL" in prompt  # tail reminder
    # Pas d'injection de checklist si non fournie : on cherche le marqueur
    # exact d'injection (entre balises ---), pas la mention descriptive
    # qui est dans le system prompt par defaut.
    assert "---CHECKLIST DE PRESERVATION V29---" not in prompt


def test_build_prompt_injects_checklist(surgeon):
    checklist = {
        "fallback": False,
        "target_bug": "X bug",
        "lines_to_preserve": [
            {"line_text": "    parts = x.split()", "reason": "definit parts"}
        ],
        "forbidden_actions": ["supprimer parts = ..."],
    }
    prompt = surgeon._build_surgeon_prompt(
        audit_report="a", target_source="b", checklist=checklist,
    )
    assert "CHECKLIST DE PRESERVATION" in prompt
    assert "    parts = x.split()" in prompt
    assert "definit parts" in prompt
    assert "supprimer parts" in prompt


def test_build_prompt_skips_checklist_on_fallback(surgeon):
    checklist = {"fallback": True}
    prompt = surgeon._build_surgeon_prompt(
        audit_report="a", target_source="b", checklist=checklist,
    )
    # Pas d'injection effective si fallback
    assert "---CHECKLIST DE PRESERVATION V29---" not in prompt


def test_build_prompt_injects_previous_attempts(surgeon):
    attempts = [
        {
            "surgeon_output": '{"action":"insert_before"...}',
            "failure_reason": "anchor_not_found",
            "traceback": "anchor_line introuvable",
        }
    ]
    prompt = surgeon._build_surgeon_prompt(
        audit_report="a", target_source="b", previous_attempts=attempts,
    )
    assert "PREVIOUS_ATTEMPTS" in prompt
    assert "anchor_not_found" in prompt
    assert "anchor_line introuvable" in prompt


# ═══════════════════════════════════════════════════════════════════════
# 4-7. generate_patch — sortie brute + parsing aval
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_generate_patch_returns_raw_llm_output(surgeon):
    """generate_patch retourne EXACTEMENT ce que le LLM crache."""
    expected = json.dumps({
        "anchor_line": "x", "action": "insert_before", "new_code": "y"
    })
    surgeon.generate_content = AsyncMock(return_value=expected)
    result = await surgeon.generate_patch(
        audit_report="bug", target_source="x\n",
    )
    assert result == expected


@pytest.mark.asyncio
async def test_generate_patch_clean_json_parses_via_medic(surgeon):
    """JSON propre → parse_v30_patch retourne le dict."""
    raw = json.dumps({
        "target_bug": "div zero",
        "anchor_function": "f",
        "anchor_line": "    return a / b",
        "action": "insert_before",
        "new_code": "if b == 0:\n    return 0",
    })
    surgeon.generate_content = AsyncMock(return_value=raw)
    out = await surgeon.generate_patch(audit_report="audit", target_source="src")
    parsed = parse_v30_patch(out)
    assert parsed["action"] == "insert_before"
    assert parsed["anchor_function"] == "f"
    assert parsed["new_code"] == "if b == 0:\n    return 0"


@pytest.mark.asyncio
async def test_generate_patch_with_narration_still_extractable(surgeon):
    """JSON enveloppé de narration → parse extrait quand même."""
    noisy = (
        "Voici mon patch :\n"
        '{"anchor_line":"return x","action":"insert_after","new_code":"# done"}\n'
        "J'espère que ça aide."
    )
    surgeon.generate_content = AsyncMock(return_value=noisy)
    raw = await surgeon.generate_patch(audit_report="a", target_source="x")
    # generate_patch ne filtre rien
    assert "Voici mon patch" in raw
    # Mais le parser extrait
    parsed = parse_v30_patch(raw)
    assert parsed["action"] == "insert_after"


@pytest.mark.asyncio
async def test_generate_patch_preserves_patch_impossible(surgeon):
    """[PATCH_IMPOSSIBLE: ...] préservé tel quel."""
    msg = "[PATCH_IMPOSSIBLE: audit trop vague]"
    surgeon.generate_content = AsyncMock(return_value=msg)
    raw = await surgeon.generate_patch(audit_report="a", target_source="x")
    assert raw == msg
    # parse_v30_patch lève ValueError sur ce texte (pas de JSON)
    with pytest.raises(_V30InvalidJSONError):
        parse_v30_patch(raw)


@pytest.mark.asyncio
async def test_generate_patch_validates_inputs(surgeon):
    surgeon.generate_content = AsyncMock(return_value="ignored")
    with pytest.raises(ValueError):
        await surgeon.generate_patch(audit_report="", target_source="x")
    with pytest.raises(ValueError):
        await surgeon.generate_patch(audit_report="a", target_source="")


# ═══════════════════════════════════════════════════════════════════════
# 8. process_task — format SurgeonOutput
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_process_task_returns_patched_status(surgeon):
    raw = json.dumps({
        "anchor_line": "x", "action": "insert_after", "new_code": "y",
    })
    surgeon.generate_content = AsyncMock(return_value=raw)
    result = await surgeon.process_task({
        "intent": "SURGEON_PATCH",
        "target_file": "core/foo.py",
        "source_full": "x\n",
        "audit_report": "bug",
        "iteration": 0,
        "previous_attempts": [],
    })
    assert result["status"] == "patched"
    assert result["agent"] == "surgeon"
    assert result["target_file"] == "core/foo.py"


@pytest.mark.asyncio
async def test_process_task_returns_impossible_status(surgeon):
    surgeon.generate_content = AsyncMock(
        return_value="[PATCH_IMPOSSIBLE: audit insuffisant]"
    )
    result = await surgeon.process_task({
        "target_file": "core/foo.py",
        "source_full": "x = 1",
        "audit_report": "audit vague",
    })
    assert result["status"] == "impossible"


@pytest.mark.asyncio
async def test_process_task_handles_validation_errors(surgeon):
    surgeon.generate_content = AsyncMock(return_value="ignored")
    result = await surgeon.process_task({
        "target_file": "core/foo.py",
        "source_full": "",
        "audit_report": "audit valide",
    })
    assert result["status"] == "error"


# ═══════════════════════════════════════════════════════════════════════
# 9. Souveraineté locale forcée
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_surgeon_evaluate_complexity_always_returns_false(surgeon):
    triggering_prompts = [
        "audit complet de securite avec analyse de faille",
        "[ROLE: SURGEON] revue de code architecture securite",
        "synthese research analyse approfondie",
        "code_review audit security",
    ]
    for p in triggering_prompts:
        result = await surgeon._evaluate_complexity(p)
        assert result is False
