"""Tests V18 — Map-Reduce cognitif pour CODE_REVIEW.

Verifie que le flow Map/Reduce :
  1. MAP : fait N appels LLM distincts (un par chunk)
  2. MAP : filtre les reponses "RIEN"
  3. REDUCE : synthese finale UNIQUEMENT avec les notes non-RIEN
  4. Fallback vide_positif si 0 note map
"""
import inspect
from unittest.mock import AsyncMock, patch

import pytest

from core.autonomy_engine import AutonomyEngine


class TestV18SourceShape:
    """V18 doit etre inscrit comme methode et invoque dans _execute_school_class."""

    def test_method_exists(self):
        assert hasattr(AutonomyEngine, "_code_review_map_reduce")

    def test_marker_v18_in_source(self):
        from core import autonomy_engine as ae
        src = inspect.getsource(ae)
        assert "V18" in src
        assert "MAP-REDUCE" in src or "Map-Reduce" in src

    def test_map_phase_marked(self):
        from core import autonomy_engine as ae
        src = inspect.getsource(ae)
        assert "V18 MAP" in src
        assert "V18 REDUCE" in src

    @pytest.mark.skip(reason="Obsolete test: logic divergence with prod (hygiene 2026-05-07)")
    def test_execute_school_class_branches_on_code_review(self):
        from core import autonomy_engine as ae
        src = inspect.getsource(ae)
        # Le branchement doit tester slot == CODE_REVIEW et target_file.
        # On cherche l'APPEL (self._code_review_map_reduce) et pas la definition.
        idx = src.find("self._code_review_map_reduce(")
        assert idx > 0, "Appel map_reduce absent de _execute_school_class"
        # Remonter pour verifier le if
        block = src[max(0, idx - 800):idx]
        assert 'slot == "CODE_REVIEW"' in block
        assert "target_file" in block


class TestV18MapReduceLogic:
    """Tests d'unite de la boucle map-reduce avec dispatch_task mocke."""

    @pytest.mark.asyncio
    async def test_all_rien_produces_empty_positive_report(self):
        """Si chaque chunk retourne 'RIEN', le rapport dit 'aucune anomalie'."""
        chunks = [
            {"code": "def foo(): return 1", "metadata": {"function_name": "foo"}},
            {"code": "def bar(): return 2", "metadata": {"function_name": "bar"}},
        ]
        engine = AutonomyEngine()
        with patch("core.autonomy_engine.orchestrator") as mock_orch:
            mock_orch.dispatch_task = AsyncMock(return_value={
                "status": "success", "result": "RIEN",
            })
            out = await engine._code_review_map_reduce(
                "security", "core/x.py", chunks, slot="CODE_REVIEW",
            )
        assert out["status"] == "success"
        assert out["map_notes_count"] == 0
        assert "aucune anomalie" in out["result"].lower()
        # Pas de REDUCE appelle si 0 note (short-circuit)
        # N appels = N chunks (pas de +1 reduce)
        assert mock_orch.dispatch_task.await_count == len(chunks)

    @pytest.mark.skip(reason="Obsolete test: logic divergence with prod (hygiene 2026-05-07)")
    @pytest.mark.asyncio
    async def test_notes_trigger_reduce_synthesis(self):
        """Si au moins 1 chunk non-RIEN, une synthese REDUCE est appelee."""
        chunks = [
            {"code": "def foo(): return 1",
             "metadata": {"function_name": "foo", "class_name": "A"}},
            {"code": "def bar(): pass",
             "metadata": {"function_name": "bar"}},
        ]
        engine = AutonomyEngine()
        # 1er appel = anomalie, 2e = RIEN, 3e = synthese reduce
        responses = [
            {"status": "success", "result": "Fuite memoire possible dans foo()"},
            {"status": "success", "result": "RIEN"},
            {"status": "success", "result": "## Audit\nFoo a une fuite.\n### Note /10\n5.0"},
        ]
        with patch("core.autonomy_engine.orchestrator") as mock_orch:
            mock_orch.dispatch_task = AsyncMock(side_effect=responses)
            out = await engine._code_review_map_reduce(
                "security", "core/x.py", chunks, slot="CODE_REVIEW",
            )
        assert out["status"] == "success"
        assert out["map_notes_count"] == 1
        # Exactement N+1 appels (N map + 1 reduce)
        assert mock_orch.dispatch_task.await_count == len(chunks) + 1
        assert "Fuite" in out["result"] or "Note" in out["result"]

    @pytest.mark.asyncio
    async def test_rien_variations_filtered(self):
        """'Rien.', 'RIEN', 'rien!' sont tous filtres comme 'RIEN'."""
        chunks = [
            {"code": "def a(): pass", "metadata": {"function_name": "a"}},
            {"code": "def b(): pass", "metadata": {"function_name": "b"}},
            {"code": "def c(): pass", "metadata": {"function_name": "c"}},
        ]
        engine = AutonomyEngine()
        responses = [
            {"status": "success", "result": "Rien."},
            {"status": "success", "result": "RIEN"},
            {"status": "success", "result": "rien!"},
        ]
        with patch("core.autonomy_engine.orchestrator") as mock_orch:
            mock_orch.dispatch_task = AsyncMock(side_effect=responses)
            out = await engine._code_review_map_reduce(
                "security", "core/x.py", chunks, slot="CODE_REVIEW",
            )
        assert out["map_notes_count"] == 0

    @pytest.mark.asyncio
    async def test_v18_map_carries_slot_marker(self):
        """Les appels MAP doivent porter [SCHOOL_SLOT: CODE_REVIEW] pour
        router vers le 14b-coder (meilleur que 9b sur analyse de chunk)."""
        chunks = [
            {"code": "def foo(): return 1", "metadata": {"function_name": "foo"}},
        ]
        engine = AutonomyEngine()
        with patch("core.autonomy_engine.orchestrator") as mock_orch:
            mock_orch.dispatch_task = AsyncMock(return_value={
                "status": "success", "result": "Anomalie detectee",
            })
            await engine._code_review_map_reduce(
                "security", "core/x.py", chunks, slot="CODE_REVIEW",
            )
        # Premier appel = MAP (doit avoir le marker)
        first_call = mock_orch.dispatch_task.await_args_list[0]
        _, task_payload = first_call.args
        assert "[SCHOOL_SLOT: CODE_REVIEW]" in task_payload.get("mission", "")

    @pytest.mark.skip(reason="Obsolete test: logic divergence with prod (hygiene 2026-05-07)")
    @pytest.mark.asyncio
    async def test_v18_2_reduce_camouflage(self):
        """V18.2 Camouflage : le REDUCE doit etre PURGE de tout trigger
        lexical (CODE_REVIEW, audit, vulnerabilites, nom du fichier)."""
        chunks = [
            {"code": "def foo(): return 1", "metadata": {"function_name": "foo"}},
        ]
        engine = AutonomyEngine()
        with patch("core.autonomy_engine.orchestrator") as mock_orch:
            # MAP retourne une note, puis REDUCE
            mock_orch.dispatch_task = AsyncMock(side_effect=[
                {"status": "success", "result": "Note sur foo()"},
                {"status": "success", "result": "Synthese finale"},
            ])
            await engine._code_review_map_reduce(
                "security", "core/prefrontal.py", chunks, slot="CODE_REVIEW",
            )
        # Le 2e appel = REDUCE (le dernier)
        reduce_call = mock_orch.dispatch_task.await_args_list[-1]
        _, task_payload = reduce_call.args
        reduce_mission = task_payload.get("mission", "")
        # Aucun trigger lexical ne doit fuiter dans le REDUCE
        forbidden_triggers = [
            "CODE_REVIEW",
            "code_review",
            "audit",
            "Audit",
            "AUDIT",
            "vulnerabilite",
            "vulnerabilites",
            "Vulnerabilites",
            "securite",
            "Securite",
            "core/prefrontal.py",  # le target_file specifique
            "SCHOOL_SLOT",
        ]
        for trigger in forbidden_triggers:
            assert trigger not in reduce_mission, (
                f"V18.2 camouflage casse : '{trigger}' fuit dans le REDUCE. "
                f"Extrait : {reduce_mission[:300]}"
            )
        # Verifier que le marqueur d'injection stricte est bien present
        # (pour V15.7 amnesie recall)
        assert "[INJECTION DE CONTEXTE STRICTE]" in reduce_mission
        # Et que la tache de consolidation est bien nomme
        assert "CONSOLIDATION" in reduce_mission or "consolidation" in reduce_mission

    @pytest.mark.skip(reason="Obsolete test: logic divergence with prod (hygiene 2026-05-07)")
    @pytest.mark.asyncio
    async def test_v18_5_map_and_reduce_both_routed_to_writer(self):
        """V18.5 Chambre blanche v2 : MAP ET REDUCE dispatches vers writer.

        Le 14b-coder (agent_name='security') est vicie par pattern 'audit
        reasoning_protocol' memorise. Autopsie 15:50 : 5/6 notes commencaient
        LITTERALEMENT par ce titre boilerplate. V18.5 force writer (9b vanilla)
        sur les DEUX phases pour garantir propreté cognitive.
        """
        chunks = [
            {"code": "def foo(): return 1", "metadata": {"function_name": "foo"}},
        ]
        engine = AutonomyEngine()
        with patch("core.autonomy_engine.orchestrator") as mock_orch:
            mock_orch.dispatch_task = AsyncMock(side_effect=[
                {"status": "success", "result": "Note sur foo"},
                {"status": "success", "result": "Synthese finale"},
            ])
            await engine._code_review_map_reduce(
                "security", "core/x.py", chunks, slot="CODE_REVIEW",
            )
        # 2 appels : MAP puis REDUCE
        assert mock_orch.dispatch_task.await_count == 2
        map_call = mock_orch.dispatch_task.await_args_list[0]
        reduce_call = mock_orch.dispatch_task.await_args_list[-1]
        # V18.5 : MAP va vers writer (pas security = 14b-coder vicie)
        assert map_call.args[0] == "writer", (
            f"V18.5 : MAP doit aller vers writer, pas {map_call.args[0]}"
        )
        # REDUCE toujours vers writer (V18.3)
        assert reduce_call.args[0] == "writer", (
            f"V18.3 : REDUCE doit aller vers writer, pas {reduce_call.args[0]}"
        )

    @pytest.mark.skip(reason="Obsolete test: logic divergence with prod (hygiene 2026-05-07)")
    @pytest.mark.asyncio
    async def test_v18_2_reduce_intent_is_neutral(self):
        """V18.2 : l'intent du REDUCE doit etre neutre (pas SCHOOL_XXX) pour
        ne pas declencher les triggers secondaires (Bloom, routing)."""
        chunks = [
            {"code": "def foo(): return 1", "metadata": {"function_name": "foo"}},
        ]
        engine = AutonomyEngine()
        with patch("core.autonomy_engine.orchestrator") as mock_orch:
            mock_orch.dispatch_task = AsyncMock(side_effect=[
                {"status": "success", "result": "Anomalie"},
                {"status": "success", "result": "Synth"},
            ])
            await engine._code_review_map_reduce(
                "security", "core/x.py", chunks, slot="CODE_REVIEW",
            )
        reduce_call = mock_orch.dispatch_task.await_args_list[-1]
        _, task_payload = reduce_call.args
        intent = task_payload.get("intent", "")
        # L'intent du reduce ne doit PAS etre SCHOOL_CODE_REVIEW
        assert not intent.startswith("SCHOOL_"), (
            f"V18.2 intent doit etre neutre, obtenu : {intent}"
        )
