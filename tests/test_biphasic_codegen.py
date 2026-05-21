"""Tests Code Engine V4 — pipeline biphasé (2026-05-21).

Prouve :
  1. La separation Architecte/Ouvrier (2 appels distincts, bons prompts).
  2. Le livrable combine (spec + bloc python pour la sandbox).
  3. Le fallback monolithique si la Phase 1 est vide.
  4. Le wrapping automatique du code en bloc python.
  5. Que black detecte deterministiquement l'IndentationError (parse) avec un
     message precis -> casse la boucle aveugle du retry.
"""
import copy
import pytest
from unittest.mock import patch, AsyncMock

from core.autonomy_engine import AutonomyEngine, AutonomyStatePersistence
from config import Config


@pytest.fixture
def engine(tmp_path):
    state_path = str(tmp_path / "state.json")
    with patch("core.autonomy_engine.STATE_FILE", state_path):
        with patch("core.autonomy_engine.AutonomyStatePersistence.load",
                   return_value=copy.deepcopy(AutonomyStatePersistence.DEFAULT_STATE)):
            return AutonomyEngine(idle_threshold_seconds=300)


# --- Pipeline biphasé : séparation des deux phases ---

class TestBiphasicPipeline:

    @pytest.mark.asyncio
    async def test_two_phases_distinct_calls(self, engine):
        """Phase 1 (spec) puis Phase 2 (code) = 2 appels avec prompts distincts."""
        spec = "PLAN : 1. classe Veto. 2. methode should_veto(score). Pseudo-code detaille."
        code = "```python\nclass Veto:\n    def should_veto(self, score):\n        return score < 0.5\n```"
        with patch("core.autonomy_engine.orchestrator") as orch:
            orch.dispatch_task = AsyncMock(side_effect=[
                {"status": "success", "result": spec},
                {"status": "success", "result": code},
            ])
            res = await engine._biphasic_codegen(
                "evolution", "Sujet: mecanisme de veto", "ctx", "SCHOOL_WORKSHOP", "WORKSHOP"
            )
        assert orch.dispatch_task.call_count == 2
        # Phase 1 utilise le prompt Architecte (interdiction de code)
        m1 = orch.dispatch_task.call_args_list[0].args[1]["mission"]
        assert "ARCHITECTE" in m1 and "INTERDICTION" in m1
        # Phase 2 utilise le prompt Ouvrier + la spec produite
        m2 = orch.dispatch_task.call_args_list[1].args[1]["mission"]
        assert "COMPILATEUR PYTHON STRICT" in m2
        assert "should_veto" in m2  # la spec de la Phase 1 est injectee
        # Livrable combine : spec + bloc python
        assert res["biphasic"] is True
        assert "should_veto" in res["result"]
        assert "```python" in res["result"]

    @pytest.mark.asyncio
    async def test_phase1_empty_falls_back_monolithic(self, engine):
        """Si l'Architecte renvoie une spec vide -> fallback monolithique + telemetrie."""
        with patch("core.autonomy_engine.orchestrator") as orch, \
             patch("core.autonomy_engine.log_decision") as mock_log:
            orch.dispatch_task = AsyncMock(side_effect=[
                {"status": "success", "result": "   "},  # Phase 1 vide
                {"status": "success", "result": "```python\nx = 1\n```"},  # fallback
            ])
            res = await engine._biphasic_codegen(
                "evolution", "Sujet", "ctx", "SCHOOL_WORKSHOP", "WORKSHOP"
            )
        assert orch.dispatch_task.call_count == 2  # phase1 (vide) + fallback monolithique
        # le 2e appel est le fallback monolithique (mission = sujet brut, pas le prompt Ouvrier)
        m2 = orch.dispatch_task.call_args_list[1].args[1]["mission"]
        assert "COMPILATEUR PYTHON STRICT" not in m2
        # Telemetrie Phase 3 : l'echec de l'Architecte est trace
        mock_log.assert_called_once()
        assert mock_log.call_args.kwargs["reason"] == "biphasic_phase1_empty_fallback"
        assert mock_log.call_args.kwargs["context"]["slot"] == "WORKSHOP"

    @pytest.mark.asyncio
    async def test_worker_code_wrapped_in_block(self, engine):
        """Si l'Ouvrier renvoie du code brut sans ```python, on le wrappe."""
        with patch("core.autonomy_engine.orchestrator") as orch:
            orch.dispatch_task = AsyncMock(side_effect=[
                {"status": "success", "result": "Spec: une fonction add."},
                {"status": "success", "result": "def add(a, b):\n    return a + b"},  # pas de bloc
            ])
            res = await engine._biphasic_codegen(
                "evolution", "Sujet", "ctx", "SCHOOL_CREATION", "CREATION"
            )
        assert "```python" in res["result"]
        assert "def add" in res["result"]


# --- Flag config ---

class TestFlag:

    def test_flag_and_slots_defined(self):
        assert Config.BIPHASIC_CODEGEN_ENABLED is True
        assert "WORKSHOP" in Config.BIPHASIC_CODEGEN_SLOTS
        assert "CREATION" in Config.BIPHASIC_CODEGEN_SLOTS
        assert "CODE_REVIEW" not in Config.BIPHASIC_CODEGEN_SLOTS  # garde son map-reduce


# --- black : parseur déterministe (casse la boucle aveugle) ---

class TestBlackParser:

    def test_black_reformats_valid_code(self):
        import black
        ugly = "def f( x ):\n  return  x+1"
        formatted = black.format_str(ugly, mode=black.Mode())
        assert "def f(x):" in formatted  # indentation/espaces normalises

    def test_black_rejects_indentation_error(self):
        """Le coeur du fix : black detecte l'IndentationError deterministiquement."""
        import black
        broken = "def f():\nreturn 1"  # corps non indente
        with pytest.raises(black.InvalidInput):
            black.format_str(broken, mode=black.Mode())

    def test_black_rejects_unclosed_structure(self):
        import black
        broken = "d = {'a': 1\nprint(d)"  # dict non ferme
        with pytest.raises(black.InvalidInput):
            black.format_str(broken, mode=black.Mode())
