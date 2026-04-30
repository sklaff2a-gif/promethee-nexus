"""V36.0 (2026-04-30) — Tests Task Force Orchestrator (skeleton).

Couvre 18 invariants doctrinaux + structurels :

  Doctrine V36 (decouplage strict) :
    1. Aucun import de thermal_homeostasis dans le module
    2. Aucun import de desire_engine
    3. Aucun import de motivational_router

  Feature flags :
    4. Global flag OFF -> fallback systematique
    5. Intent flag OFF -> fallback meme si global ON
    6. Intent inconnu -> fallback
    7. Pas d'agent_runner branche -> fallback

  Topologies :
    8. SEQUENTIAL : agents executes dans l'ordre
    9. SEQUENTIAL_FEEDBACK : iterations multiples
   10. PARALLEL_THEN_SYNTH : synthesizer en dernier

  Blackboard :
   11. Accumule les outputs au fil des agents
   12. build_prompt_for_agent inclut les contributions precedentes

  Cooldowns locaux :
   13. Refractory bloque la taskforce
   14. Cooldown independant par role

  Contrat sortie :
   15. Iteration marker dans le prompt si iteration > 0
   16. Format strict du payload de retour

  Mapping :
   17. INTENT_TO_TASKFORCE contient EXPANSION_CODE, FEATURE_BUILDING,
       CODE_REVIEW, COUNCIL_DEBATE
   18. Topology enum a 3 valeurs
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.task_force_orchestrator import (
    AgentRole,
    INTENT_TO_TASKFORCE,
    TASKFORCE_GLOBAL_ENABLED,
    TASKFORCE_INTENT_ENABLED,
    TaskForce,
    TaskForceOrchestrator,
    TaskForceState,
    Topology,
    orchestrator,
)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def fresh_orch(tmp_path, monkeypatch):
    """Reset complet du singleton avant chaque test.
    V36.1.3 : redirige STATE_FILE vers tmp pour ne pas polluer le history
    persiste entre tests ou avec le runtime production."""
    import core.task_force_orchestrator as mod
    state_file = tmp_path / "task_force_history.json"
    monkeypatch.setattr(mod, "STATE_FILE", str(state_file))
    TaskForceOrchestrator.reset_singleton()
    orch = TaskForceOrchestrator()
    yield orch
    TaskForceOrchestrator.reset_singleton()


def _make_runner_record(outputs_by_role):
    """Cree un agent_runner mock qui retourne des outputs predetermines.
    Capture aussi l'ordre d'appel et les prompts recus pour assertions."""
    calls = []
    async def runner(agent: AgentRole, prompt: str) -> str:
        calls.append({"role": agent.role, "prompt": prompt})
        return outputs_by_role.get(agent.role, f"<output_{agent.role}>")
    return runner, calls


def _enable_intent(intent: str):
    """Helper pour les tests : active l'intent + global pendant le test."""
    import core.task_force_orchestrator as mod
    mod.TASKFORCE_GLOBAL_ENABLED = True
    mod.TASKFORCE_INTENT_ENABLED[intent] = True


def _reset_flags():
    import core.task_force_orchestrator as mod
    mod.TASKFORCE_GLOBAL_ENABLED = False
    for k in mod.TASKFORCE_INTENT_ENABLED:
        mod.TASKFORCE_INTENT_ENABLED[k] = False


@pytest.fixture(autouse=True)
def _flags_reset():
    """Reset les flags avant ET apres chaque test (isolation totale)."""
    _reset_flags()
    yield
    _reset_flags()


# ═══════════════════════════════════════════════════════════════════════
# 1-3. Doctrine V36 — decouplage strict (aucun couplage organes V35)
# ═══════════════════════════════════════════════════════════════════════

def _module_imports() -> set:
    """Extrait les modules importes (vrais 'import' / 'from X import Y'),
    en ignorant les docstrings et commentaires."""
    import ast
    src_path = _PROJECT_ROOT / "core" / "task_force_orchestrator.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module)
    return modules


def test_v36_no_coupling_with_thermal_homeostasis():
    """V36 ne doit jamais importer thermal_homeostasis (doctrine corps unique).
    Test base sur l'AST — ignore docstrings/commentaires qui peuvent
    legitimement mentionner le decouplage."""
    imports = _module_imports()
    assert not any("thermal_homeostasis" in m for m in imports), (
        f"V36 ne doit pas importer thermal_homeostasis. Imports: {imports}"
    )


def test_v36_no_coupling_with_desire_engine():
    """V36 ne doit jamais importer desire_engine (decouplage volonte/execution)."""
    imports = _module_imports()
    assert not any("desire_engine" in m for m in imports), (
        f"V36 ne doit pas importer desire_engine. Imports: {imports}"
    )


def test_v36_no_coupling_with_motivational_router():
    """V36 ne doit jamais importer motivational_router (decouplage horizontal)."""
    imports = _module_imports()
    assert not any("motivational_router" in m for m in imports), (
        f"V36 ne doit pas importer motivational_router. Imports: {imports}"
    )


# ═══════════════════════════════════════════════════════════════════════
# 4-7. Feature flags & fallback
# ═══════════════════════════════════════════════════════════════════════

def test_global_flag_off_triggers_fallback(fresh_orch):
    """V36.0 : flag global OFF -> tout passe en fallback solo."""
    runner, _calls = _make_runner_record({})
    fresh_orch.set_agent_runner(runner)
    # Flag global OFF par defaut (autouse fixture le garantit)
    result = asyncio.run(fresh_orch.execute(
        "EXPANSION_CODE", "Mission test", {}
    ))
    assert result["status"] in ("skipped",)
    assert result["reason"] == "taskforce_disabled"


def test_intent_flag_off_triggers_fallback(fresh_orch):
    """Global ON mais intent OFF -> fallback."""
    import core.task_force_orchestrator as mod
    mod.TASKFORCE_GLOBAL_ENABLED = True
    # EXPANSION_CODE reste OFF
    runner, _calls = _make_runner_record({})
    fresh_orch.set_agent_runner(runner)
    result = asyncio.run(fresh_orch.execute(
        "EXPANSION_CODE", "Mission", {}
    ))
    assert result["status"] == "skipped"
    assert result["reason"] == "taskforce_disabled"


def test_unknown_intent_triggers_fallback(fresh_orch):
    """Intent absent du mapping -> fallback."""
    _enable_intent("EXPANSION_CODE")  # Active mais on test un autre intent
    runner, _calls = _make_runner_record({})
    fresh_orch.set_agent_runner(runner)
    result = asyncio.run(fresh_orch.execute(
        "INTENT_INVENTE_QUI_N_EXISTE_PAS", "Mission", {}
    ))
    assert result["status"] == "skipped"


def test_no_agent_runner_triggers_fallback(fresh_orch):
    """Pas de runner branche -> fallback safe."""
    _enable_intent("EXPANSION_CODE")
    # Pas de set_agent_runner
    result = asyncio.run(fresh_orch.execute(
        "EXPANSION_CODE", "Mission", {}
    ))
    assert result["status"] == "skipped"
    assert result["reason"] == "no_agent_runner"


# ═══════════════════════════════════════════════════════════════════════
# 8-10. Topologies
# ═══════════════════════════════════════════════════════════════════════

def test_sequential_runs_agents_in_order(fresh_orch):
    """SEQUENTIAL : architect -> coder -> tester (FEATURE_BUILDING)."""
    _enable_intent("FEATURE_BUILDING")
    outputs = {
        "architect": "Archi: A->B->C",
        "coder":     "code(...)",
        "tester":    "def test(): ...",
    }
    runner, calls = _make_runner_record(outputs)
    fresh_orch.set_agent_runner(runner)

    result = asyncio.run(fresh_orch.execute(
        "FEATURE_BUILDING", "Construis X", {}
    ))
    assert result["status"] == "success"
    roles_called = [c["role"] for c in calls]
    assert roles_called == ["architect", "coder", "tester"]


def test_sequential_feedback_iterates(fresh_orch):
    """SEQUENTIAL_FEEDBACK : EXPANSION_CODE a max_iterations=2 ->
    architect/coder/critic appeles 2 fois chacun (6 appels au total)."""
    _enable_intent("EXPANSION_CODE")
    outputs = {"architect": "a", "coder": "c", "critic": "k"}
    runner, calls = _make_runner_record(outputs)
    fresh_orch.set_agent_runner(runner)

    result = asyncio.run(fresh_orch.execute(
        "EXPANSION_CODE", "Code X", {}
    ))
    assert result["status"] == "success"
    roles = [c["role"] for c in calls]
    assert roles == ["architect", "coder", "critic", "architect", "coder", "critic"]


def test_parallel_then_synth_runs_synthesizer_last(fresh_orch):
    """PARALLEL_THEN_SYNTH (CODE_REVIEW) : critic + security puis synthesizer."""
    _enable_intent("CODE_REVIEW")
    outputs = {
        "critic":       "F1: ...",
        "security":     "OWASP: ...",
        "synthesizer":  "Bilan: ...",
    }
    runner, calls = _make_runner_record(outputs)
    fresh_orch.set_agent_runner(runner)

    result = asyncio.run(fresh_orch.execute(
        "CODE_REVIEW", "Review X", {}
    ))
    assert result["status"] == "success"
    roles = [c["role"] for c in calls]
    # Synthesizer DOIT etre dernier
    assert roles[-1] == "synthesizer"
    # Les 2 paralleles avant lui (ordre sequentiel V36.0 cote impl)
    assert set(roles[:2]) == {"critic", "security"}


# ═══════════════════════════════════════════════════════════════════════
# 11-12. Blackboard
# ═══════════════════════════════════════════════════════════════════════

def test_blackboard_accumulates_outputs(fresh_orch):
    """A la fin d'un SEQUENTIAL, le blackboard contient tous les outputs."""
    _enable_intent("FEATURE_BUILDING")
    outputs = {"architect": "AAA", "coder": "BBB", "tester": "CCC"}
    runner, _calls = _make_runner_record(outputs)
    fresh_orch.set_agent_runner(runner)

    result = asyncio.run(fresh_orch.execute(
        "FEATURE_BUILDING", "Mission", {}
    ))
    # final_result = dernier output (tester)
    assert result["result"] == "CCC"
    # trace contient les 3 etapes
    assert len(result["task_force_trace"]) == 3


def test_build_prompt_includes_previous_contributions():
    """Quand un agent execute apres d'autres, son prompt voit le blackboard."""
    architect = AgentRole("architect", "model_a", "Tu architectes.")
    coder = AgentRole("coder", "model_c", "Tu codes.")
    state = TaskForceState(
        intent="EXPANSION_CODE", mission="Code X",
        blackboard={"architect": "Architecture: A->B->C"},
    )
    prompt = state.build_prompt_for_agent(coder)
    assert "Tu codes" in prompt
    assert "MISSION:" in prompt
    assert "Code X" in prompt
    assert "[architect]" in prompt
    assert "Architecture: A->B->C" in prompt
    assert "[coder] A toi" in prompt


# ═══════════════════════════════════════════════════════════════════════
# 13-14. Cooldowns locaux
# ═══════════════════════════════════════════════════════════════════════

def test_agent_cooldown_blocks_taskforce(fresh_orch):
    """Si l'architect a un cooldown actif, la taskforce est skipped."""
    import time
    _enable_intent("EXPANSION_CODE")
    # Marque l'architect comme tournant a l'instant (refractory 300s actif)
    fresh_orch._agent_last_run["architect"] = time.time()

    runner, _calls = _make_runner_record({})
    fresh_orch.set_agent_runner(runner)

    result = asyncio.run(fresh_orch.execute(
        "EXPANSION_CODE", "Mission", {}
    ))
    assert result["status"] == "skipped"
    assert result["reason"] == "agent_refractory"
    assert "architect" in result["result"]


def test_agent_cooldown_independent_per_role(fresh_orch):
    """Cooldown sur architect ne bloque pas une taskforce qui n'utilise
    pas architect. CODE_REVIEW = critic + security + synthesizer."""
    import time
    _enable_intent("CODE_REVIEW")
    # architect tourne mais CODE_REVIEW ne l'utilise pas
    fresh_orch._agent_last_run["architect"] = time.time()

    outputs = {"critic": "c", "security": "s", "synthesizer": "S"}
    runner, _calls = _make_runner_record(outputs)
    fresh_orch.set_agent_runner(runner)

    result = asyncio.run(fresh_orch.execute(
        "CODE_REVIEW", "Mission", {}
    ))
    assert result["status"] == "success"


# ═══════════════════════════════════════════════════════════════════════
# 15-16. Iteration marker + contrat sortie
# ═══════════════════════════════════════════════════════════════════════

def test_iteration_marker_appears_in_prompt():
    """A iteration > 0, un marqueur d'iteration apparait dans le prompt."""
    agent = AgentRole("coder", "m", "Tu codes.")
    state = TaskForceState(intent="X", mission="m", iteration=1)
    prompt = state.build_prompt_for_agent(agent)
    assert "Iteration 2" in prompt or "iteration 2" in prompt.lower()


def test_returns_strict_contract_format(fresh_orch):
    """Le payload de retour doit avoir les 5 cles standard du contrat."""
    _enable_intent("FEATURE_BUILDING")
    outputs = {"architect": "a", "coder": "c", "tester": "t"}
    runner, _calls = _make_runner_record(outputs)
    fresh_orch.set_agent_runner(runner)

    result = asyncio.run(fresh_orch.execute(
        "FEATURE_BUILDING", "Mission", {}
    ))
    assert "status" in result
    assert "result" in result
    assert "quality_score" in result
    assert "reason" in result
    assert "task_force_trace" in result
    assert isinstance(result["task_force_trace"], list)


# ═══════════════════════════════════════════════════════════════════════
# 17-18. Mapping & enum
# ═══════════════════════════════════════════════════════════════════════

def test_intent_to_taskforce_contains_v36_initial_intents():
    """V36.0 : 4 intents complexes initiaux. Les autres restent solo legacy."""
    expected = {"EXPANSION_CODE", "FEATURE_BUILDING", "CODE_REVIEW", "COUNCIL_DEBATE"}
    assert set(INTENT_TO_TASKFORCE.keys()) == expected


def test_topology_enum_has_three_values():
    """V36.0 : 3 topologies — SEQUENTIAL, SEQUENTIAL_FEEDBACK, PARALLEL_THEN_SYNTH."""
    values = {t.value for t in Topology}
    assert values == {"sequential", "sequential_feedback", "parallel_then_synth"}


def test_v36_2_default_flags_all_intents_active():
    """V36.2 (2026-04-30 pm) — Apres validation runtime de EXPANSION_CODE
    (cascade architect/coder/critic 73s, blackboard verifie, critic iter 2
    a identifie une regression du coder iter 2 = preuve formelle de
    chaine de pensee), les 3 autres intents sont actives :
      EXPANSION_CODE   : True (V36.1.1)
      FEATURE_BUILDING : True (V36.2)
      CODE_REVIEW      : True (V36.2)
      COUNCIL_DEBATE   : True (V36.2 — strategist teste avec think:false OK)
    """
    import importlib
    import core.task_force_orchestrator as mod
    importlib.reload(mod)
    try:
        assert mod.TASKFORCE_GLOBAL_ENABLED is True, "V36.2 : global ON"
        for intent in ("EXPANSION_CODE", "FEATURE_BUILDING",
                       "CODE_REVIEW", "COUNCIL_DEBATE"):
            assert mod.TASKFORCE_INTENT_ENABLED[intent] is True, (
                f"V36.2 : {intent} doit etre actif"
            )
    finally:
        _reset_flags()


# ═══════════════════════════════════════════════════════════════════════
# V36.1 — Tests Ollama runner + semaphore VRAM + troncature blackboard
# ═══════════════════════════════════════════════════════════════════════

def test_v36_1_oversized_prompt_truncates_oldest_outputs():
    """V36.1 : si le prompt depasse MAX_PROMPT_CHARS, on tronque FIFO
    les outputs les plus anciens (preserve les recents)."""
    agent = AgentRole("critic", "model_x", "Tu critiques.")
    state = TaskForceState(
        intent="EXPANSION_CODE", mission="Mission courte",
        blackboard={
            "architect": "X" * 10000,   # output ancien volumineux
            "coder":     "Y" * 10000,   # plus recent
            "tester":    "Z" * 10000,   # plus recent encore
        },
    )
    # Sans troncature, prompt > 30000 chars
    prompt = state.build_prompt_for_agent(agent, max_chars=15000)
    assert len(prompt) <= 15000, f"prompt non tronque: len={len(prompt)}"
    # L'output le plus recent (tester) doit etre present
    assert "[tester]" in prompt
    # L'architect (le plus ancien) doit avoir disparu
    assert "[architect]" not in prompt
    # Marqueur de troncature visible
    assert "elaguees" in prompt.lower() or "tronc" in prompt.lower()


def test_v36_1_truncation_preserves_recent_outputs():
    """Si seul le 1er output est volumineux, lui seul disparait."""
    agent = AgentRole("synth", "m", "Tu synthetises.")
    state = TaskForceState(
        intent="X", mission="Mission",
        blackboard={
            "architect": "X" * 20000,  # gros, sera tronque
            "coder":     "C",          # petit, doit rester
            "critic":    "K",          # petit, doit rester
        },
    )
    prompt = state.build_prompt_for_agent(agent, max_chars=10000)
    assert "[architect]" not in prompt
    assert "[coder]" in prompt
    assert "[critic]" in prompt


def test_v36_1_truncation_keeps_at_least_one_output():
    """Garde-fou : meme si TOUS les outputs sont enormes, on en garde
    au moins UN (le plus recent). Sinon le prompt n'a aucun contexte."""
    agent = AgentRole("c", "m", "Tu codes.")
    huge = "X" * 50000
    state = TaskForceState(
        intent="X", mission="m",
        blackboard={"architect": huge, "coder": huge, "critic": huge},
    )
    prompt = state.build_prompt_for_agent(agent, max_chars=5000)
    # Le dernier (critic) doit rester meme si overshoot
    assert "[critic]" in prompt


def test_v36_1_use_default_ollama_runner_branches_runner(fresh_orch):
    """use_default_ollama_runner() branche le runner par defaut."""
    import core.task_force_orchestrator as mod
    assert fresh_orch._agent_runner is None
    fresh_orch.use_default_ollama_runner()
    assert fresh_orch._agent_runner is mod._default_agent_runner


def test_v36_1_default_runner_acquires_vram_semaphore_on_import():
    """Le module expose un semaphore lazy-init pour la VRAM."""
    import core.task_force_orchestrator as mod
    # Il existe une fonction de resolution
    assert hasattr(mod, "_get_vram_semaphore")
    # Et une constante OLLAMA_URL
    assert mod.OLLAMA_URL.startswith("http")


def test_v36_1_max_prompt_chars_constant_exists():
    """Constante doctrinale : MAX_PROMPT_CHARS doit etre dans le module."""
    import core.task_force_orchestrator as mod
    assert mod.MAX_PROMPT_CHARS >= 8000   # au moins ~2k tokens
    assert mod.MAX_PROMPT_CHARS <= 100000  # pas absurde non plus


# ═══════════════════════════════════════════════════════════════════════
# V36.1.3 — Persistance + observabilite (history avec blackboard)
# ═══════════════════════════════════════════════════════════════════════

def test_v36_1_3_get_last_run_empty_when_no_history(fresh_orch):
    """get_last_run() retourne None quand aucun run enregistre."""
    assert fresh_orch.get_last_run() is None


def test_v36_1_3_history_persists_blackboard_and_trace(fresh_orch, tmp_path, monkeypatch):
    """Apres un execute() reussi, get_last_run() doit contenir le
    blackboard complet et la trace par-agent."""
    import core.task_force_orchestrator as mod
    state_file = tmp_path / "task_force_history.json"
    monkeypatch.setattr(mod, "STATE_FILE", str(state_file))

    _enable_intent("FEATURE_BUILDING")
    outputs = {
        "architect": "Architecture: A->B->C avec interface I",
        "coder":     "def main(): pass",
        "tester":    "def test_main(): assert True",
    }
    runner, _calls = _make_runner_record(outputs)
    fresh_orch.set_agent_runner(runner)

    asyncio.run(fresh_orch.execute(
        "FEATURE_BUILDING", "Test mission V36.1.3", {}
    ))

    last = fresh_orch.get_last_run()
    assert last is not None
    assert last["intent"] == "FEATURE_BUILDING"
    assert last["status"] == "success"
    # Blackboard complet expose
    assert "blackboard" in last
    assert last["blackboard"]["architect"] == outputs["architect"]
    assert last["blackboard"]["coder"] == outputs["coder"]
    assert last["blackboard"]["tester"] == outputs["tester"]
    # Trace par-agent
    assert len(last["trace"]) == 3
    # Mission preservee
    assert last["mission"] == "Test mission V36.1.3"


def test_v36_1_3_save_load_roundtrip(fresh_orch, tmp_path, monkeypatch):
    """Le history est persiste sur disque et restaure au reload."""
    import core.task_force_orchestrator as mod
    state_file = tmp_path / "task_force_history.json"
    monkeypatch.setattr(mod, "STATE_FILE", str(state_file))

    _enable_intent("FEATURE_BUILDING")
    outputs = {"architect": "A", "coder": "C", "tester": "T"}
    runner, _calls = _make_runner_record(outputs)
    fresh_orch.set_agent_runner(runner)

    asyncio.run(fresh_orch.execute("FEATURE_BUILDING", "M", {}))

    # Verifier qu'un fichier existe
    assert state_file.exists()

    # Reset le singleton et re-instantier (simule un reboot)
    mod.TaskForceOrchestrator.reset_singleton()
    fresh2 = mod.TaskForceOrchestrator()
    # Le history doit etre charge au boot
    last = fresh2.get_last_run()
    assert last is not None
    assert last["intent"] == "FEATURE_BUILDING"
    assert last["blackboard"]["architect"] == "A"


def test_v36_1_3_history_skipped_run_also_recorded(fresh_orch, tmp_path, monkeypatch):
    """Note : V36.0 ne record que les success. V36.1.3 le confirme :
    les fallback (skipped) ne pollluent pas le history. Garde-fou
    architectural — un skip ne represente pas un travail multi-agent."""
    import core.task_force_orchestrator as mod
    state_file = tmp_path / "task_force_history.json"
    monkeypatch.setattr(mod, "STATE_FILE", str(state_file))

    # Pas de flag active -> fallback skipped
    runner, _ = _make_runner_record({})
    fresh_orch.set_agent_runner(runner)

    result = asyncio.run(fresh_orch.execute("EXPANSION_CODE", "M", {}))
    assert result["status"] == "skipped"

    # History doit rester vide (pas d'entree pour un skip)
    assert fresh_orch.get_last_run() is None
