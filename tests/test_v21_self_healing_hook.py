"""V21 (2026-04-25) — Tests du hook synchrone _self_healing_hook.

Couvre :
  - Garde-fous (grade < 6, target_file manquant)
  - Boucle SURGEON ↔ MEDIC : success direct, retry, max_iter, patch_impossible
  - Fail-safe : surgeon crash et medic crash sont isolés (le hook ne raise pas)
  - Persistance : 3 fichiers (.txt, .diff, .meta.json) écrits dans
    memory/auto_patches/

Tests sans réseau, sans LLM réel, sans pytest sandbox subprocess. Tout est
mocké pour valider la logique du hook.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.autonomy_engine import AutonomyEngine
from core.capabilities.code_sandbox import PatchResult


# ─── Fixtures ─────────────────────────────────────────────────────────

def _make_naked_engine() -> AutonomyEngine:
    """Crée un AutonomyEngine sans appeler son __init__ lourd (bus, scheduler,
    chromaDB, etc.). Suffisant pour tester _self_healing_hook en isolation.
    """
    engine = AutonomyEngine.__new__(AutonomyEngine)
    # Reset l'attribut de classe partagé (le surgeon est lazy)
    AutonomyEngine._v21_surgeon = None
    return engine


@pytest.fixture
def engine():
    return _make_naked_engine()


@pytest.fixture
def fake_project(tmp_path: Path):
    """Mini-projet temporaire avec target.py + memory/ vide."""
    (tmp_path / "target.py").write_text("def f(): return 1\n", encoding="utf-8")
    (tmp_path / "memory").mkdir()
    return tmp_path


def _make_patch_result(status: str, **kw) -> PatchResult:
    """Helper : PatchResult minimal avec status donné."""
    defaults = dict(
        status=status,
        surgeon_output="<<<<<<< SEARCH\nA\n=======\nB\n>>>>>>> REPLACE",
        blocks_applied=1,
        target_file="target.py",
        iteration=0,
        unified_diff="--- a/target.py\n+++ b/target.py\n@@ -1 +1 @@\n-A\n+B\n",
        tests_passed=42 if status == "success" else 0,
        test_strategy="full_suite",
        duration_s=12.3,
    )
    defaults.update(kw)
    return PatchResult(**defaults)


def _patch_module_funcs(monkeypatch, engine, project_root: str,
                       surgeon_outputs: list, medic_results: list):
    """Patch les dépendances externes du hook :
      - project_root : pointe sur le tmp_path
      - SurgeonAgent : remplacé par un mock dont generate_patch retourne
        successivement les éléments de surgeon_outputs
      - sandbox.apply_patch_in_sandbox : retourne successivement les
        éléments de medic_results
    """
    # 1. Monkey-patch project_root via __file__ : on substitue
    #    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    #    par notre tmp_path.
    import core.autonomy_engine as ae_mod
    original_dirname = ae_mod.os.path.dirname
    original_abspath = ae_mod.os.path.abspath

    def fake_abspath(p):
        # Si on demande le abspath de __file__ d'autonomy_engine, on retourne
        # un chemin synthetique dont le 2-niveaux-up == project_root
        if "autonomy_engine" in str(p):
            return os.path.join(project_root, "core", "autonomy_engine.py")
        return original_abspath(p)

    monkeypatch.setattr(ae_mod.os.path, "abspath", fake_abspath)

    # 2. SurgeonAgent : on patche directement self._v21_surgeon avec un mock.
    fake_surgeon = MagicMock()
    fake_surgeon.generate_patch = AsyncMock(side_effect=list(surgeon_outputs))
    AutonomyEngine._v21_surgeon = fake_surgeon

    # 3. medic_sandbox.apply_patch_in_sandbox
    from core.capabilities import code_sandbox as cs_mod
    fake_apply = MagicMock(side_effect=list(medic_results))
    monkeypatch.setattr(cs_mod.sandbox, "apply_patch_in_sandbox", fake_apply)

    # 4. Empêcher l'instanciation réelle d'un nouveau SurgeonAgent
    import Agents.surgeon_agent as sa_mod
    monkeypatch.setattr(sa_mod, "SurgeonAgent", lambda: fake_surgeon)

    return fake_surgeon, fake_apply


# ═══════════════════════════════════════════════════════════════════════
# Garde-fous
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_hook_aborts_when_target_missing(engine, fake_project, monkeypatch):
    """target_file inexistant → return None silencieusement."""
    _patch_module_funcs(
        monkeypatch, engine, str(fake_project),
        surgeon_outputs=[], medic_results=[],
    )
    result = await engine._self_healing_hook(
        audit_report="audit",
        target_file="does_not_exist.py",
        school_grade=8.0,
    )
    assert result is None


# ═══════════════════════════════════════════════════════════════════════
# Boucle SURGEON ↔ MEDIC
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_hook_success_persists_patch(engine, fake_project, monkeypatch):
    """Success à la 1ère iter → 3 fichiers écrits dans memory/auto_patches/."""
    surgeon_out = "<<<<<<< SEARCH\ndef f(): return 1\n=======\ndef f(): return 2\n>>>>>>> REPLACE"
    fake_surgeon, fake_apply = _patch_module_funcs(
        monkeypatch, engine, str(fake_project),
        surgeon_outputs=[surgeon_out],
        medic_results=[_make_patch_result("success", surgeon_output=surgeon_out)],
    )
    result = await engine._self_healing_hook(
        audit_report="audit du fichier", target_file="target.py", school_grade=8.5,
    )
    assert result is not None
    assert result["status"] == "success"
    assert result["iteration"] == 0
    fake_surgeon.generate_patch.assert_awaited_once()
    fake_apply.assert_called_once()

    patches_dir = fake_project / "memory" / "auto_patches"
    assert patches_dir.exists()
    files = sorted(p.name for p in patches_dir.iterdir())
    assert any(f.endswith(".txt") for f in files)
    assert any(f.endswith(".diff") for f in files)
    assert any(f.endswith(".meta.json") for f in files)
    # Les 3 fichiers ont le même stem (timestamp + basename)
    stems = {os.path.splitext(f)[0].replace(".meta", "") for f in files}
    assert len(stems) == 1

    # Meta JSON contient les bonnes infos
    meta_file = next(p for p in patches_dir.iterdir() if p.name.endswith(".meta.json"))
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    assert meta["target_file"] == "target.py"
    assert meta["school_grade"] == 8.5
    assert meta["tests_passed"] == 42
    assert meta["human_review_status"] == "pending"


@pytest.mark.asyncio
async def test_hook_retries_then_succeeds(engine, fake_project, monkeypatch):
    """1ère iter `search_not_found`, 2e iter success → previous_attempts injectées."""
    fake_surgeon, fake_apply = _patch_module_funcs(
        monkeypatch, engine, str(fake_project),
        surgeon_outputs=["BAD_FORMAT", "<<<<<<< SEARCH\nA\n=======\nB\n>>>>>>> REPLACE"],
        medic_results=[
            _make_patch_result("search_not_found", failed_block_index=0,
                              failed_block_search="missing_func", error_message="not found"),
            _make_patch_result("success"),
        ],
    )
    result = await engine._self_healing_hook(
        audit_report="audit", target_file="target.py", school_grade=7.0,
    )
    assert result["status"] == "success"
    assert result["iteration"] == 1
    assert fake_surgeon.generate_patch.await_count == 2
    # Le 2e appel au SURGEON DOIT inclure previous_attempts
    second_call_kwargs = fake_surgeon.generate_patch.await_args_list[1].kwargs
    assert "previous_attempts" in second_call_kwargs
    assert len(second_call_kwargs["previous_attempts"]) == 1
    assert second_call_kwargs["previous_attempts"][0]["failure_reason"] == "search_not_found"


@pytest.mark.asyncio
async def test_hook_max_iter_reached(engine, fake_project, monkeypatch):
    """3 iters sans succès → return max_iter_reached + patch persisté dans failed/."""
    fake_surgeon, fake_apply = _patch_module_funcs(
        monkeypatch, engine, str(fake_project),
        surgeon_outputs=["BAD"] * 3,
        medic_results=[_make_patch_result("syntax_error", compile_stderr="oops")] * 3,
    )
    result = await engine._self_healing_hook(
        audit_report="audit", target_file="target.py", school_grade=7.0,
    )
    assert result["status"] == "max_iter_reached"
    assert result["iteration"] == 3
    assert fake_surgeon.generate_patch.await_count == 3
    # V21.1 : pas de patch SUCCESS dans auto_patches/ racine
    success_files = list((fake_project / "memory" / "auto_patches").glob("patch_*.txt"))
    assert len(success_files) == 0
    # V21.1 : patch ECHEC persisté dans auto_patches/failed/
    failed_dir = fake_project / "memory" / "auto_patches" / "failed"
    assert failed_dir.exists()
    failed_files = list(failed_dir.iterdir())
    assert len(failed_files) >= 2  # .txt + .meta.json
    txt_file = next(f for f in failed_files if f.suffix == ".txt")
    assert "syntax_error" in txt_file.name
    meta_file = next(f for f in failed_files if f.name.endswith(".meta.json"))
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    assert meta["final_status"] == "syntax_error"
    assert meta["compile_stderr"] == "oops"


@pytest.mark.asyncio
async def test_hook_patch_impossible_no_retry(engine, fake_project, monkeypatch):
    """SURGEON déclare PATCH_IMPOSSIBLE → 1 seule iter, pas de retry, persistance failed/."""
    fake_surgeon, fake_apply = _patch_module_funcs(
        monkeypatch, engine, str(fake_project),
        surgeon_outputs=["[PATCH_IMPOSSIBLE: audit trop vague]"],
        medic_results=[_make_patch_result("patch_impossible",
                                          error_message="audit trop vague")],
    )
    result = await engine._self_healing_hook(
        audit_report="audit", target_file="target.py", school_grade=7.0,
    )
    assert result["status"] == "impossible"
    # Une SEULE iteration, pas de retry
    assert fake_surgeon.generate_patch.await_count == 1
    # V21.1 : meme un patch_impossible doit etre persiste pour analyse
    failed_dir = fake_project / "memory" / "auto_patches" / "failed"
    assert failed_dir.exists()
    meta_files = list(failed_dir.glob("*.meta.json"))
    assert len(meta_files) == 1
    meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
    assert meta["final_status"] == "patch_impossible"
    assert meta["error_message"] == "audit trop vague"


# ═══════════════════════════════════════════════════════════════════════
# Fail-safe : crash SURGEON / MEDIC ne doit PAS lever
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_hook_surgeon_crash_isolated(engine, fake_project, monkeypatch):
    """SURGEON.generate_patch raise → hook return None sans propager."""
    fake_surgeon, fake_apply = _patch_module_funcs(
        monkeypatch, engine, str(fake_project),
        surgeon_outputs=[], medic_results=[],
    )
    fake_surgeon.generate_patch = AsyncMock(side_effect=RuntimeError("LLM down"))

    # Doit retourner None sans lever
    result = await engine._self_healing_hook(
        audit_report="audit", target_file="target.py", school_grade=8.0,
    )
    assert result is None
    fake_apply.assert_not_called()


@pytest.mark.asyncio
async def test_hook_medic_crash_isolated(engine, fake_project, monkeypatch):
    """MEDIC.apply_patch_in_sandbox raise → hook return None sans propager."""
    fake_surgeon, fake_apply = _patch_module_funcs(
        monkeypatch, engine, str(fake_project),
        surgeon_outputs=["<<<<<<< SEARCH\nA\n=======\nB\n>>>>>>> REPLACE"],
        medic_results=[],
    )
    fake_apply.side_effect = RuntimeError("Sandbox catastrophique")

    result = await engine._self_healing_hook(
        audit_report="audit", target_file="target.py", school_grade=8.0,
    )
    assert result is None


# ═══════════════════════════════════════════════════════════════════════
# Helper _persist_v21_patch en isolation
# ═══════════════════════════════════════════════════════════════════════

def test_persist_v21_patch_writes_three_files(engine, fake_project):
    """Smoke test direct de _persist_v21_patch."""
    pr = _make_patch_result("success",
                            surgeon_output="<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE",
                            unified_diff="diff --git a/x\n@@\n-x\n+y\n")
    engine._persist_v21_patch(
        result=pr,
        audit_report="audit text",
        school_grade=9.1,
        project_root=str(fake_project),
    )
    patches_dir = fake_project / "memory" / "auto_patches"
    files = list(patches_dir.iterdir())
    assert len(files) == 3
    # Vérifier le contenu
    txt = next(f for f in files if f.suffix == ".txt").read_text(encoding="utf-8")
    diff = next(f for f in files if f.suffix == ".diff").read_text(encoding="utf-8")
    meta_path = next(f for f in files if f.name.endswith(".meta.json"))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert "<<<<<<< SEARCH" in txt
    assert "diff --git" in diff
    assert meta["school_grade"] == 9.1


def test_log_v21_triumph_runs_without_exception(engine, capsys):
    """Smoke test du log de triomphe (le print ne doit pas crasher)."""
    pr = _make_patch_result("success", tests_passed=137)
    engine._log_v21_triumph(
        target_file="core/foo.py",
        iteration=0,
        result=pr,
        total_duration_s=87.3,
    )
    captured = capsys.readouterr()
    assert "V21 SELF-HEALING" in captured.out
    assert "core/foo.py" in captured.out
    assert "137" in captured.out
