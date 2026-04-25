"""V21 (2026-04-25) — Tests unitaires SurgeonAgent.

Couvre :
  - Le system prompt impose strictement le format SEARCH/REPLACE
  - Le prompt construit contient bien SOURCE, AUDIT, et previous_attempts
  - generate_patch retourne la sortie BRUTE du LLM (pas de transformation)
  - Le pipeline aval (parse_search_replace_blocks du MEDIC) parse bien la
    sortie du SURGEON, qu'elle soit "propre" ou contienne de la narration
  - PATCH_IMPOSSIBLE est préservé dans la sortie
  - process_task retourne le format SurgeonOutput de la spec §3.4

Les tests instancient SurgeonAgent SANS appeler BaseAgent.__init__ (pour
éviter les dépendances lourdes : ChromaDB, GpuScheduler, etc.). Seules les
méthodes nécessaires sont mockées.
"""
from __future__ import annotations

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
from core.capabilities.code_sandbox import parse_search_replace_blocks


# ─── Fixture : SURGEON sans init BaseAgent ────────────────────────────

def _make_naked_surgeon() -> SurgeonAgent:
    """Crée un SurgeonAgent sans appeler BaseAgent.__init__ (évite ChromaDB,
    GpuScheduler, et autres dépendances lourdes inutiles aux tests unitaires).
    """
    agent = SurgeonAgent.__new__(SurgeonAgent)
    agent.name = "surgeon"
    agent.system_instructions = SURGEON_SYSTEM_PROMPT
    agent.log_thought = MagicMock()  # silence
    return agent


@pytest.fixture
def surgeon():
    return _make_naked_surgeon()


# ═══════════════════════════════════════════════════════════════════════
# 1-2. Tests du system prompt — règles strictes
# ═══════════════════════════════════════════════════════════════════════

def test_system_prompt_contains_strict_format_rules():
    """Le prompt impose VERBATIM, unique, pas de rename, pas de narration."""
    p = SURGEON_SYSTEM_PROMPT
    # Règle 1 : VERBATIM
    assert "VERBATIM" in p
    # Règle 2 : unique (anti-ambiguïté)
    assert "unique" in p.lower()
    # Règle 5 : pas de rename
    assert "renommes AUCUNE" in p
    # Format strict
    assert "<<<<<<< SEARCH" in p
    assert "=======" in p
    assert ">>>>>>> REPLACE" in p


def test_system_prompt_demands_no_narration():
    """Le prompt exige explicitement aucune narration/intro/conclusion."""
    p = SURGEON_SYSTEM_PROMPT.lower()
    # Variantes acceptees : "aucune narration", "aucune intro", "pas de narration"
    assert ("aucune narration" in p) or ("aucune explication narrative" in p)
    assert ("aucune intro" in p) or ("aucune introduction" in p)
    assert ("aucune conclusion" in p) or ("ni conclusion" in p)
    # Et le tail reminder répète la règle (anti biais de récence)
    tail = SURGEON_TAIL_REMINDER.lower()
    assert ("aucune" in tail) or ("pas de" in tail)
    # Marqueur PATCH_IMPOSSIBLE explicite pour les cas insolubles
    assert "patch_impossible" in p


# ═══════════════════════════════════════════════════════════════════════
# 3. Construction du prompt — payload complet
# ═══════════════════════════════════════════════════════════════════════

def test_build_prompt_contains_source_audit_and_tail(surgeon):
    """Le prompt construit contient SOURCE, AUDIT et le rappel final."""
    prompt = surgeon._build_surgeon_prompt(
        audit_report="L'audit identifie un bug à la ligne 42",
        target_source="def f():\n    return 1",
    )
    assert "---SOURCE---" in prompt
    assert "---/SOURCE---" in prompt
    assert "def f():\n    return 1" in prompt
    assert "---AUDIT---" in prompt
    assert "---/AUDIT---" in prompt
    assert "L'audit identifie un bug" in prompt
    # Tail reminder présent à la fin (anti biais récence)
    assert "RAPPEL" in prompt
    # Pas de section previous_attempts si non fournie
    assert "PREVIOUS_ATTEMPTS" not in prompt


def test_build_prompt_injects_previous_attempts(surgeon):
    """Si previous_attempts fournies, le prompt liste chaque retry avec sa raison."""
    attempts = [
        {
            "surgeon_output": "<<<<<<< SEARCH\ndef foo():\n=======\ndef foo():\n    pass\n>>>>>>> REPLACE",
            "failure_reason": "search_not_found",
            "traceback": "BLOC 1 : SEARCH introuvable",
        },
        {
            "surgeon_output": "[PATCH_IMPOSSIBLE: trop ambigu]",
            "failure_reason": "patch_impossible",
            "traceback": "",
        },
    ]
    prompt = surgeon._build_surgeon_prompt(
        audit_report="audit X", target_source="src Y", previous_attempts=attempts,
    )
    assert "---PREVIOUS_ATTEMPTS---" in prompt
    assert "---/PREVIOUS_ATTEMPTS---" in prompt
    assert "Iteration 1" in prompt
    assert "search_not_found" in prompt
    assert "Iteration 2" in prompt
    assert "patch_impossible" in prompt
    assert "BLOC 1 : SEARCH introuvable" in prompt
    # Consigne explicite de ne pas reproduire la même erreur
    assert "Ne reproduis pas" in prompt


# ═══════════════════════════════════════════════════════════════════════
# 4-7. generate_patch — sortie brute, parsing aval, robustesse
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_generate_patch_returns_raw_llm_output(surgeon):
    """generate_patch retourne EXACTEMENT ce que le LLM crache, sans modif."""
    expected = "<<<<<<< SEARCH\nA\n=======\nB\n>>>>>>> REPLACE\n"
    surgeon.generate_content = AsyncMock(return_value=expected)
    result = await surgeon.generate_patch(
        audit_report="bug ligne 1", target_source="A\n",
    )
    assert result == expected
    surgeon.generate_content.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_patch_clean_output_parses_via_medic(surgeon):
    """Sortie sans narration → MEDIC parse correctement les blocs."""
    clean_output = (
        "<<<<<<< SEARCH\n"
        "def add(a, b):\n"
        "    return a + b\n"
        "=======\n"
        "def add(a, b):\n"
        "    if not isinstance(a, (int, float)):\n"
        "        raise TypeError\n"
        "    return a + b\n"
        ">>>>>>> REPLACE\n"
    )
    surgeon.generate_content = AsyncMock(return_value=clean_output)
    raw = await surgeon.generate_patch(
        audit_report="add() doit valider les types",
        target_source="def add(a, b):\n    return a + b\n",
    )
    blocks = parse_search_replace_blocks(raw)
    assert len(blocks) == 1
    assert "raise TypeError" in blocks[0][1]


@pytest.mark.asyncio
async def test_generate_patch_with_narrative_still_extractable(surgeon):
    """Le LLM peut désobéir (narration) — le MEDIC extrait quand même les blocs."""
    noisy_output = (
        "Bien sûr, voici le patch que je propose pour corriger ce bug :\n\n"
        "<<<<<<< SEARCH\n"
        "x = 1\n"
        "=======\n"
        "x = 2\n"
        ">>>>>>> REPLACE\n\n"
        "Cela devrait résoudre le problème. N'hésitez pas si vous avez des questions !"
    )
    surgeon.generate_content = AsyncMock(return_value=noisy_output)
    raw = await surgeon.generate_patch(audit_report="audit", target_source="x = 1")
    # generate_patch ne filtre pas la narration (c'est au MEDIC de gérer)
    assert "Bien sûr" in raw
    # Mais les blocs restent parsables
    blocks = parse_search_replace_blocks(raw)
    assert len(blocks) == 1
    assert blocks[0] == ("x = 1", "x = 2")


@pytest.mark.asyncio
async def test_generate_patch_preserves_patch_impossible(surgeon):
    """Si le LLM retourne [PATCH_IMPOSSIBLE: ...], le texte est préservé tel quel."""
    impossible = "[PATCH_IMPOSSIBLE: l'audit ne précise pas la fonction à corriger]"
    surgeon.generate_content = AsyncMock(return_value=impossible)
    raw = await surgeon.generate_patch(audit_report="audit vague", target_source="src")
    assert raw == impossible
    # parse_search_replace_blocks doit lever ValueError (aucun bloc)
    with pytest.raises(ValueError):
        parse_search_replace_blocks(raw)


@pytest.mark.asyncio
async def test_generate_patch_validates_inputs(surgeon):
    """audit_report et target_source vides/None → ValueError."""
    surgeon.generate_content = AsyncMock(return_value="ignored")
    with pytest.raises(ValueError):
        await surgeon.generate_patch(audit_report="", target_source="x")
    with pytest.raises(ValueError):
        await surgeon.generate_patch(audit_report="a", target_source="")
    with pytest.raises(ValueError):
        await surgeon.generate_patch(audit_report=None, target_source="x")  # type: ignore


# ═══════════════════════════════════════════════════════════════════════
# 8. process_task — format SurgeonOutput conforme à la spec §3.4
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_process_task_returns_patched_status_with_blocks_count(surgeon):
    """process_task → dict avec status='patched', surgeon_output, blocks_count."""
    output = (
        "<<<<<<< SEARCH\nA\n=======\nB\n>>>>>>> REPLACE\n"
        "<<<<<<< SEARCH\nC\n=======\nD\n>>>>>>> REPLACE\n"
    )
    surgeon.generate_content = AsyncMock(return_value=output)
    result = await surgeon.process_task({
        "intent": "SURGEON_PATCH",
        "target_file": "core/foo.py",
        "source_full": "A\nC\n",
        "audit_report": "bugs sur A et C",
        "iteration": 0,
        "previous_attempts": [],
    })
    assert result["status"] == "patched"
    assert result["agent"] == "surgeon"
    assert result["target_file"] == "core/foo.py"
    assert result["iteration"] == 0
    assert result["blocks_count"] == 2
    assert result["surgeon_output"] == output


@pytest.mark.asyncio
async def test_process_task_returns_impossible_status(surgeon):
    """process_task détecte [PATCH_IMPOSSIBLE: ...] et bascule status."""
    surgeon.generate_content = AsyncMock(
        return_value="[PATCH_IMPOSSIBLE: audit insuffisant]"
    )
    result = await surgeon.process_task({
        "target_file": "core/foo.py",
        "source_full": "x = 1",
        "audit_report": "audit vague",
    })
    assert result["status"] == "impossible"
    assert result["blocks_count"] == 0
    assert "[PATCH_IMPOSSIBLE:" in result["surgeon_output"]


@pytest.mark.asyncio
async def test_process_task_handles_validation_errors(surgeon):
    """Payload incomplet → status='error', pas d'exception remontée."""
    surgeon.generate_content = AsyncMock(return_value="ignored")
    result = await surgeon.process_task({
        "target_file": "core/foo.py",
        "source_full": "",  # invalide
        "audit_report": "audit valide",
    })
    assert result["status"] == "error"
    assert result["blocks_count"] == 0
    assert "error_message" in result


# ═══════════════════════════════════════════════════════════════════════
# Souverainete locale forcee — _evaluate_complexity override
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_surgeon_evaluate_complexity_always_returns_false(surgeon):
    """V21 souverainete : SurgeonAgent._evaluate_complexity retourne TOUJOURS
    False, peu importe le contenu du prompt.

    Sans cet override, le BaseAgent._evaluate_complexity escalade en Cloud
    Gemini sur tous les triggers du prompt SURGEON ('audit', 'revue de code',
    'securite', 'faille' — tous présents par construction).
    """
    # Prompts qui DEVRAIENT trigger Cloud chez BaseAgent
    triggering_prompts = [
        "Effectue un audit complet de securite avec analyse de faille",
        "[ROLE: SURGEON] revue de code architecture securite",
        "synthese research analyse approfondie",
        "evening_reflection introspection stefan confrontation",
        "code_review audit security",
    ]
    for p in triggering_prompts:
        result = await surgeon._evaluate_complexity(p)
        assert result is False, (
            f"V21 souverainete violee : SurgeonAgent escalade Cloud sur {p!r}"
        )
