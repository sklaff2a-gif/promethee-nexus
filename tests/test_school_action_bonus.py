"""Tests scan_for_exercise + compute_logic_score (atelier ecole 26/05).

Couvre :
- compute_logic_score : 0 si lecture avant write, -1 si write aveugle,
  -1 par write aveugle cumulatif, grep ne credite pas une lecture specifique
- _extract_action_target pour les commandes principales
- scan_for_exercise expose la trace structuree
- cap max_actions respecte (rejected_cap rempli quand surplus)
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.chat_engine import (
    ChatEngine,
    compute_logic_score,
)


# ============================================================================
# 1. compute_logic_score — fonction pure
# ============================================================================


def test_logic_score_zero_when_no_actions():
    assert compute_logic_score([]) == 0.0


def test_logic_score_zero_when_read_before_write():
    seq = [("read", "core/foo.py"), ("write", "core/foo.py")]
    assert compute_logic_score(seq) == 0.0


def test_logic_score_minus_one_on_blind_write():
    seq = [("write", "core/foo.py")]
    assert compute_logic_score(seq) == -1.0


def test_logic_score_grep_does_not_credit_specific_read():
    """grep core/ NE creditent PAS la lecture de core/foo.py specifiquement."""
    seq = [("grep", "core/"), ("write", "core/foo.py")]
    assert compute_logic_score(seq) == -1.0


def test_logic_score_multiple_blind_writes_cumulate():
    seq = [("write", "a.py"), ("write", "b.py")]
    assert compute_logic_score(seq) == -2.0


def test_logic_score_status_does_not_credit_anything():
    seq = [("status", ""), ("write", "core/foo.py")]
    assert compute_logic_score(seq) == -1.0


def test_logic_score_ignores_malformed_entries():
    seq = [("read",), None, "garbage", ("write", "f.py")]
    # ("read",) tuple < 2 elts -> ignore. ("write","f.py") aveugle -> -1.0
    assert compute_logic_score(seq) == -1.0


# ============================================================================
# 2. _extract_action_target
# ============================================================================


def test_extract_target_read():
    assert ChatEngine._extract_action_target("read", "core/foo.py") == "core/foo.py"


def test_extract_target_grep_with_path():
    assert ChatEngine._extract_action_target("grep", "pattern core/") == "core/"


def test_extract_target_grep_without_path():
    assert ChatEngine._extract_action_target("grep", "pattern") == "."


def test_extract_target_write():
    assert ChatEngine._extract_action_target("write", "core/foo.py contenu...") == "core/foo.py"


def test_extract_target_status_empty():
    assert ChatEngine._extract_action_target("status", "") == ""


def test_extract_target_none_args():
    assert ChatEngine._extract_action_target("read", None) == ""


# ============================================================================
# 3. scan_for_exercise — integration
# ============================================================================


@pytest.fixture
def engine():
    ChatEngine._instance = None
    e = ChatEngine()
    # Empecher l'execution reelle des commandes (on teste juste la mecanique
    # de scan/cap/trace, pas l'I/O)
    e._auto_action_in_progress = True
    yield e
    ChatEngine._instance = None


@pytest.mark.asyncio
async def test_scan_for_exercise_trace_structure(engine):
    """Verifie la presence des cles structurees dans la trace."""
    engine._auto_action_in_progress = False
    response = "!status\n"  # pas executable car _auto_action_in_progress True initialement
    engine._auto_action_in_progress = True  # bloquer execution
    trace = await engine.scan_for_exercise(response, max_actions=5)
    # On verifie que la trace est bien structuree, meme si rien n'est execute
    assert "max_actions" in trace
    assert "parsed_count" in trace
    assert "executed" in trace
    assert "rejected_cap" in trace
    assert "sequence" in trace
    assert trace["max_actions"] == 5


@pytest.mark.asyncio
async def test_scan_for_exercise_no_commands_means_empty_trace(engine):
    trace = await engine.scan_for_exercise("Pas de commandes ici, juste du texte.")
    assert trace["parsed_count"] == 0
    assert trace["executed"] == []
    assert trace["rejected_cap"] == []


@pytest.mark.asyncio
async def test_scan_for_exercise_filters_non_whitelisted(engine):
    """Les commandes hors whitelist (!hack par ex) ne comptent PAS dans parsed_count."""
    response = "!read foo.py\n!hack server\n!grep pattern\n"
    trace = await engine.scan_for_exercise(response, max_actions=5)
    # status, read, grep sont whitelist ; hack ne l'est pas
    # read + grep = 2 valides
    assert trace["parsed_count"] == 2


@pytest.mark.asyncio
async def test_scan_for_exercise_cap_5_rejects_surplus(engine):
    """Au-dela de 5 commandes valides, le surplus va dans rejected_cap."""
    response = "\n".join([
        "!read a.py", "!read b.py", "!read c.py",
        "!read d.py", "!read e.py",
        "!read f.py",  # 6e -> rejected
        "!read g.py",  # 7e -> rejected
    ])
    trace = await engine.scan_for_exercise(response, max_actions=5)
    assert trace["parsed_count"] == 7
    # 2 rejections (les 6e et 7e)
    assert len(trace["rejected_cap"]) == 2
    assert trace["rejected_cap"][0]["target"] == "f.py"
    assert trace["rejected_cap"][1]["target"] == "g.py"


@pytest.mark.asyncio
async def test_scan_for_exercise_under_cap_no_rejection(engine):
    response = "!read a.py\n!grep pattern\n"
    trace = await engine.scan_for_exercise(response, max_actions=5)
    assert trace["parsed_count"] == 2
    assert trace["rejected_cap"] == []


# ============================================================================
# 4. Prompt CODE_REVIEW — annonce du cap + invitation aux commandes
# ============================================================================


def test_code_review_prompt_announces_action_cap(monkeypatch, tmp_path):
    """Le prompt CODE_REVIEW doit annoncer les outils + le cap (atelier 26/05)."""
    from core import school_schedule as mod
    monkeypatch.setattr(mod, "DELIVERABLES_DIR", str(tmp_path / "deliverables"))
    monkeypatch.setattr(mod, "CREATIONS_DIR", str(tmp_path / "creations"))
    monkeypatch.setattr(mod, "BULLETINS_DIR", str(tmp_path / "bulletins"))
    mod.SchoolSchedule._instance = None
    sched = mod.SchoolSchedule()
    sched._state_file = str(tmp_path / "state.json")
    sched._daily_subjects = {}
    sched._deliverables_today = []

    prompt = sched.get_slot_prompt(mod.SLOT_CODE_REVIEW)
    # La graine doctrinale doit etre dans le prompt
    assert "Je ne sais pas, donc je tends la main" in prompt
    # Les outils doivent etre listes explicitement
    assert "!read" in prompt
    assert "!grep" in prompt
    # Le cap doit etre annonce
    assert "5 commandes maximum" in prompt
    # Le scoring doit etre explique (transparence)
    assert "gaming" in prompt.lower() or "cap" in prompt.lower()


# ============================================================================
# 5. Scoring composite (bonus + logic + gaming)
# ============================================================================


def test_composite_scoring_executed_within_cap_gives_bonus():
    """3 actions executees, sequence coherente -> bonus +3 capped, logic 0."""
    n_exec, n_rej = 3, 0
    seq = [("read", "a.py"), ("read", "b.py"), ("write", "a.py")]
    bonus = min(3, n_exec)
    if n_exec == 0:
        bonus -= 2
    bonus -= 2 * n_rej
    bonus += compute_logic_score(seq)
    assert bonus == 3.0


def test_composite_scoring_zero_action_gets_penalty():
    """Aucune action sur slot technique -> -2."""
    n_exec, n_rej = 0, 0
    bonus = min(3, n_exec)
    if n_exec == 0:
        bonus -= 2
    bonus -= 2 * n_rej
    bonus += compute_logic_score([])
    assert bonus == -2.0


def test_composite_scoring_blind_writes_penalized():
    """2 writes aveugles dans 3 actions -> +3 (capped) - 2 = +1."""
    n_exec, n_rej = 3, 0
    seq = [("write", "a.py"), ("write", "b.py"), ("read", "c.py")]
    bonus = min(3, n_exec) - 2 * n_rej + compute_logic_score(seq)
    assert bonus == 1.0


def test_composite_scoring_gaming_punished():
    """3 executees + 2 rejetees (gaming) -> bonus +3 - 2*2 = -1."""
    n_exec, n_rej = 3, 2
    seq = [("read", "a.py"), ("read", "b.py"), ("read", "c.py")]
    bonus = min(3, n_exec) - 2 * n_rej + compute_logic_score(seq)
    assert bonus == -1.0
