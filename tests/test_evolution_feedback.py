# tests/test_evolution_feedback.py — Tests de la boucle de feedback Evolution
import os
import json
import shutil
import tempfile
import pytest
import asyncio
from datetime import datetime
from unittest.mock import patch, MagicMock, AsyncMock

from core.evolution_feedback import (
    EvolutionFeedbackLoop, feedback_loop, FEEDBACK_STATE_FILE
)

_FAKE_STATE_FILE = os.path.join(tempfile.gettempdir(), "test_evolution_feedback.json")


@pytest.fixture(autouse=True)
def isolate_feedback():
    """Isole chaque test de la persistance feedback."""
    if os.path.exists(_FAKE_STATE_FILE):
        os.remove(_FAKE_STATE_FILE)
    with patch("core.evolution_feedback.FEEDBACK_STATE_FILE", _FAKE_STATE_FILE):
        EvolutionFeedbackLoop.reset_singleton()
        yield
        EvolutionFeedbackLoop.reset_singleton()
    if os.path.exists(_FAKE_STATE_FILE):
        os.remove(_FAKE_STATE_FILE)


def _make_snapshot(success_rate=0.85, error_streak=0, ci_pass=10, ci_fail=1,
                   health_verdict="GO"):
    """Helper pour créer un snapshot de métriques."""
    return {
        "success_rate": success_rate,
        "error_streak": error_streak,
        "ci_pass": ci_pass,
        "ci_fail": ci_fail,
        "health_verdict": health_verdict,
        "timestamp": datetime.now().isoformat(),
    }


class TestFeedbackInit:

    def test_singleton_pattern(self):
        a = EvolutionFeedbackLoop()
        b = EvolutionFeedbackLoop()
        assert a is b

    def test_reset_singleton(self):
        a = EvolutionFeedbackLoop()
        EvolutionFeedbackLoop.reset_singleton()
        b = EvolutionFeedbackLoop()
        assert a is not b

    def test_initial_state(self):
        fb = EvolutionFeedbackLoop()
        assert fb._pending_observations == []
        assert fb._completed_feedbacks == []
        assert fb._subscribed is False

    def test_init_subscribes_to_bus(self):
        fb = EvolutionFeedbackLoop()
        mock_bus = MagicMock()
        with patch("core.evolution_feedback.bus", mock_bus, create=True):
            with patch.dict("sys.modules", {"core.event_bus.bus": MagicMock(bus=mock_bus)}):
                fb.init()
        assert fb._subscribed is True

    def test_double_init_safe(self):
        fb = EvolutionFeedbackLoop()
        mock_bus = MagicMock()
        with patch("core.evolution_feedback.bus", mock_bus, create=True):
            with patch.dict("sys.modules", {"core.event_bus.bus": MagicMock(bus=mock_bus)}):
                fb.init()
                fb.init()  # 2e appel ne crash pas
        assert fb._subscribed is True


class TestMetricsCapture:

    def test_capture_with_snapshot(self):
        fb = EvolutionFeedbackLoop()
        mock_awareness = MagicMock()
        mock_awareness.get_latest_snapshot.return_value = {
            "performance": {
                "success_rate": 0.9,
                "error_streak": 2,
                "ci_pass": 15,
                "ci_fail": 3,
            },
            "health": {"verdict": "GO"},
        }
        mock_module = MagicMock()
        mock_module.awareness = mock_awareness

        with patch.dict("sys.modules", {"core.self_awareness": mock_module}):
            metrics = fb._capture_metrics()

        assert metrics["success_rate"] == 0.9
        assert metrics["error_streak"] == 2
        assert metrics["ci_pass"] == 15
        assert metrics["ci_fail"] == 3
        assert metrics["health_verdict"] == "GO"
        assert "timestamp" in metrics

    def test_capture_without_snapshot(self):
        fb = EvolutionFeedbackLoop()
        mock_awareness = MagicMock()
        mock_awareness.get_latest_snapshot.return_value = None
        mock_module = MagicMock()
        mock_module.awareness = mock_awareness

        with patch.dict("sys.modules", {"core.self_awareness": mock_module}):
            metrics = fb._capture_metrics()

        assert metrics["success_rate"] == 1.0
        assert metrics["error_streak"] == 0

    def test_capture_handles_exception(self):
        fb = EvolutionFeedbackLoop()
        with patch.dict("sys.modules", {"core.self_awareness": None}):
            metrics = fb._capture_metrics()
        assert metrics["success_rate"] == 1.0


class TestOnEvolutionDeployed:

    @pytest.mark.asyncio
    async def test_creates_pending_observation(self):
        fb = EvolutionFeedbackLoop()
        before_snap = _make_snapshot(success_rate=0.85)
        with patch.object(fb, "_capture_metrics", return_value=before_snap):
            await fb._on_evolution_deployed({
                "data": {
                    "spec_id": "PERF-001",
                    "spec_name": "Cache LRU Router",
                    "target_file": "core/router.py",
                }
            })

        assert len(fb._pending_observations) == 1
        obs = fb._pending_observations[0]
        assert obs["spec_id"] == "PERF-001"
        assert obs["spec_name"] == "Cache LRU Router"
        assert obs["target_file"] == "core/router.py"
        assert obs["routines_observed"] == 0
        assert obs["before_snapshot"] == before_snap

    @pytest.mark.asyncio
    async def test_no_duplicate_observation(self):
        fb = EvolutionFeedbackLoop()
        before_snap = _make_snapshot()
        with patch.object(fb, "_capture_metrics", return_value=before_snap):
            await fb._on_evolution_deployed({
                "data": {"spec_id": "PERF-001", "spec_name": "Test", "target_file": "x.py"}
            })
            await fb._on_evolution_deployed({
                "data": {"spec_id": "PERF-001", "spec_name": "Test", "target_file": "x.py"}
            })

        assert len(fb._pending_observations) == 1

    @pytest.mark.asyncio
    async def test_no_spec_id_ignored(self):
        fb = EvolutionFeedbackLoop()
        await fb._on_evolution_deployed({"data": {}})
        assert len(fb._pending_observations) == 0

    @pytest.mark.asyncio
    async def test_event_flat_format(self):
        """Supporte aussi l'événement sans wrapper 'data'."""
        fb = EvolutionFeedbackLoop()
        before_snap = _make_snapshot()
        with patch.object(fb, "_capture_metrics", return_value=before_snap):
            await fb._on_evolution_deployed({
                "spec_id": "RES-001",
                "spec_name": "Retry backoff",
                "target_file": "core/base_agent.py",
            })

        assert len(fb._pending_observations) == 1
        assert fb._pending_observations[0]["spec_id"] == "RES-001"


class TestOnRoutineComplete:

    @pytest.mark.asyncio
    async def test_increments_routines_observed(self):
        fb = EvolutionFeedbackLoop()
        fb._pending_observations = [{
            "spec_id": "PERF-001", "spec_name": "Test",
            "target_file": "x.py", "before_snapshot": _make_snapshot(),
            "routines_observed": 0, "started_at": datetime.now().isoformat(),
        }]

        with patch.object(fb, "_evaluate_deployment", new_callable=AsyncMock):
            await fb._on_routine_complete({"data": {}})

        assert fb._pending_observations[0]["routines_observed"] == 1

    @pytest.mark.asyncio
    async def test_no_pending_noop(self):
        fb = EvolutionFeedbackLoop()
        await fb._on_routine_complete({"data": {}})
        # Pas d'erreur, pas d'observation créée

    @pytest.mark.asyncio
    async def test_triggers_evaluation_at_threshold(self):
        fb = EvolutionFeedbackLoop()
        fb._pending_observations = [{
            "spec_id": "PERF-001", "spec_name": "Test",
            "target_file": "x.py", "before_snapshot": _make_snapshot(),
            "routines_observed": fb.OBSERVATION_PERIOD - 1,
            "started_at": datetime.now().isoformat(),
        }]

        with patch.object(fb, "_evaluate_deployment", new_callable=AsyncMock) as mock_eval:
            await fb._on_routine_complete({"data": {}})

        mock_eval.assert_called_once()
        assert len(fb._pending_observations) == 0

    @pytest.mark.asyncio
    async def test_multiple_observations_independent(self):
        fb = EvolutionFeedbackLoop()
        fb._pending_observations = [
            {
                "spec_id": "PERF-001", "spec_name": "A",
                "target_file": "a.py", "before_snapshot": _make_snapshot(),
                "routines_observed": fb.OBSERVATION_PERIOD - 1,
                "started_at": datetime.now().isoformat(),
            },
            {
                "spec_id": "RES-001", "spec_name": "B",
                "target_file": "b.py", "before_snapshot": _make_snapshot(),
                "routines_observed": 5,
                "started_at": datetime.now().isoformat(),
            },
        ]

        with patch.object(fb, "_evaluate_deployment", new_callable=AsyncMock):
            await fb._on_routine_complete({"data": {}})

        # PERF-001 évalué et retiré, RES-001 encore en cours
        assert len(fb._pending_observations) == 1
        assert fb._pending_observations[0]["spec_id"] == "RES-001"
        assert fb._pending_observations[0]["routines_observed"] == 6


class TestEvaluateDeployment:

    @pytest.mark.asyncio
    async def test_verdict_improved(self):
        fb = EvolutionFeedbackLoop()
        before = _make_snapshot(success_rate=0.80, error_streak=2, ci_fail=3)
        after = _make_snapshot(success_rate=0.92, error_streak=0, ci_fail=3)
        obs = {
            "spec_id": "PERF-001", "spec_name": "Cache LRU",
            "target_file": "core/router.py", "before_snapshot": before,
        }

        mock_catalog = MagicMock()
        mock_journal = MagicMock()
        mock_bus = AsyncMock()

        with patch.object(fb, "_capture_metrics", return_value=after), \
             patch.dict("sys.modules", {
                 "core.evolution_catalog": MagicMock(catalog=mock_catalog),
                 "core.strategic_journal": MagicMock(journal=mock_journal),
                 "core.event_bus.bus": MagicMock(bus=mock_bus),
             }):
            await fb._evaluate_deployment(obs)

        mock_catalog.mark_confirmed.assert_called_once_with("PERF-001")
        mock_journal.append_evolution_report.assert_called_once()
        assert len(fb._completed_feedbacks) == 1
        assert fb._completed_feedbacks[0]["verdict"] == "improved"

    @pytest.mark.asyncio
    async def test_verdict_neutral(self):
        fb = EvolutionFeedbackLoop()
        before = _make_snapshot(success_rate=0.85, error_streak=1, ci_fail=2)
        after = _make_snapshot(success_rate=0.87, error_streak=1, ci_fail=2)
        obs = {
            "spec_id": "RES-001", "spec_name": "Retry backoff",
            "target_file": "core/base_agent.py", "before_snapshot": before,
        }

        mock_catalog = MagicMock()
        mock_journal = MagicMock()
        mock_bus = AsyncMock()

        with patch.object(fb, "_capture_metrics", return_value=after), \
             patch.dict("sys.modules", {
                 "core.evolution_catalog": MagicMock(catalog=mock_catalog),
                 "core.strategic_journal": MagicMock(journal=mock_journal),
                 "core.event_bus.bus": MagicMock(bus=mock_bus),
             }):
            await fb._evaluate_deployment(obs)

        mock_catalog.mark_confirmed.assert_called_once_with("RES-001")
        assert fb._completed_feedbacks[0]["verdict"] == "neutral"

    @pytest.mark.asyncio
    async def test_verdict_degraded_success_rate_drop(self):
        fb = EvolutionFeedbackLoop()
        before = _make_snapshot(success_rate=0.85, error_streak=0, ci_fail=1)
        after = _make_snapshot(success_rate=0.65, error_streak=0, ci_fail=1)
        obs = {
            "spec_id": "INT-001", "spec_name": "Trimming",
            "target_file": "core/base_agent.py", "before_snapshot": before,
        }

        mock_catalog = MagicMock()
        mock_journal = MagicMock()
        mock_bus = AsyncMock()

        with patch.object(fb, "_capture_metrics", return_value=after), \
             patch.object(fb, "_rollback_spec", new_callable=AsyncMock) as mock_rb, \
             patch.dict("sys.modules", {
                 "core.evolution_catalog": MagicMock(catalog=mock_catalog),
                 "core.strategic_journal": MagicMock(journal=mock_journal),
                 "core.event_bus.bus": MagicMock(bus=mock_bus),
             }):
            await fb._evaluate_deployment(obs)

        mock_rb.assert_called_once_with(obs)
        mock_catalog.mark_rolled_back.assert_called_once()
        assert fb._completed_feedbacks[0]["verdict"] == "degraded"

    @pytest.mark.asyncio
    async def test_verdict_degraded_error_streak(self):
        fb = EvolutionFeedbackLoop()
        before = _make_snapshot(success_rate=0.85, error_streak=0, ci_fail=1)
        after = _make_snapshot(success_rate=0.85, error_streak=3, ci_fail=1)
        obs = {
            "spec_id": "INT-002", "spec_name": "Few-shot",
            "target_file": "core/council.py", "before_snapshot": before,
        }

        mock_catalog = MagicMock()
        mock_journal = MagicMock()
        mock_bus = AsyncMock()

        with patch.object(fb, "_capture_metrics", return_value=after), \
             patch.object(fb, "_rollback_spec", new_callable=AsyncMock), \
             patch.dict("sys.modules", {
                 "core.evolution_catalog": MagicMock(catalog=mock_catalog),
                 "core.strategic_journal": MagicMock(journal=mock_journal),
                 "core.event_bus.bus": MagicMock(bus=mock_bus),
             }):
            await fb._evaluate_deployment(obs)

        assert fb._completed_feedbacks[0]["verdict"] == "degraded"

    @pytest.mark.asyncio
    async def test_verdict_degraded_ci_fail(self):
        fb = EvolutionFeedbackLoop()
        before = _make_snapshot(success_rate=0.85, error_streak=0, ci_fail=1)
        after = _make_snapshot(success_rate=0.85, error_streak=0, ci_fail=3)
        obs = {
            "spec_id": "SEC-001", "spec_name": "Sanitization",
            "target_file": "Agents/factory_agent.py", "before_snapshot": before,
        }

        mock_catalog = MagicMock()
        mock_journal = MagicMock()
        mock_bus = AsyncMock()

        with patch.object(fb, "_capture_metrics", return_value=after), \
             patch.object(fb, "_rollback_spec", new_callable=AsyncMock), \
             patch.dict("sys.modules", {
                 "core.evolution_catalog": MagicMock(catalog=mock_catalog),
                 "core.strategic_journal": MagicMock(journal=mock_journal),
                 "core.event_bus.bus": MagicMock(bus=mock_bus),
             }):
            await fb._evaluate_deployment(obs)

        assert fb._completed_feedbacks[0]["verdict"] == "degraded"


class TestRollback:

    @pytest.mark.asyncio
    async def test_rollback_with_bak(self):
        """Si un .bak existe, le fichier est restauré."""
        fb = EvolutionFeedbackLoop()
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "test.py")
            bak = target + ".bak"
            with open(target, "w") as f:
                f.write("# modified code")
            with open(bak, "w") as f:
                f.write("# original code")

            obs = {
                "spec_id": "PERF-001", "spec_name": "Test",
                "target_file": "test.py",
            }

            mock_bus = AsyncMock()
            with patch("core.evolution_feedback.PROJECT_ROOT", tmpdir), \
                 patch.dict("sys.modules", {
                     "core.event_bus.bus": MagicMock(bus=mock_bus),
                 }):
                await fb._rollback_spec(obs)

            with open(target) as f:
                assert f.read() == "# original code"

    @pytest.mark.asyncio
    async def test_rollback_no_bak(self):
        """Si pas de .bak, log warning mais pas de crash."""
        fb = EvolutionFeedbackLoop()
        with tempfile.TemporaryDirectory() as tmpdir:
            obs = {
                "spec_id": "PERF-001", "spec_name": "Test",
                "target_file": "nonexistent.py",
            }
            mock_bus = AsyncMock()
            with patch("core.evolution_feedback.PROJECT_ROOT", tmpdir), \
                 patch.dict("sys.modules", {
                     "core.event_bus.bus": MagicMock(bus=mock_bus),
                 }):
                await fb._rollback_spec(obs)
            # Pas d'erreur levée

    @pytest.mark.asyncio
    async def test_rollback_no_target_file(self):
        """Si pas de target_file, skip."""
        fb = EvolutionFeedbackLoop()
        obs = {"spec_id": "X", "spec_name": "Y", "target_file": ""}
        await fb._rollback_spec(obs)
        # Pas d'erreur


class TestLesson:

    def test_format_improved(self):
        fb = EvolutionFeedbackLoop()
        obs = {"spec_id": "PERF-001", "spec_name": "Cache LRU", "target_file": "core/router.py"}
        comparison = {
            "before": _make_snapshot(success_rate=0.80),
            "after": _make_snapshot(success_rate=0.92),
            "delta_success": 0.12,
            "delta_error_streak": -1,
            "delta_ci_fail": 0,
            "verdict": "improved",
            "reasons": [],
        }
        lesson = fb._format_lesson(obs, comparison)
        assert "PERF-001" in lesson
        assert "Cache LRU" in lesson
        assert "AMELIORE" in lesson
        assert "core/router.py" in lesson

    def test_format_degraded(self):
        fb = EvolutionFeedbackLoop()
        obs = {"spec_id": "INT-001", "spec_name": "Trimming", "target_file": "core/base_agent.py"}
        comparison = {
            "before": _make_snapshot(success_rate=0.85),
            "after": _make_snapshot(success_rate=0.65),
            "delta_success": -0.20,
            "delta_error_streak": 2,
            "delta_ci_fail": 0,
            "verdict": "degraded",
            "reasons": ["success_rate chute"],
        }
        lesson = fb._format_lesson(obs, comparison)
        assert "DEGRADE" in lesson
        assert "rollback" in lesson.lower()

    def test_format_neutral(self):
        fb = EvolutionFeedbackLoop()
        obs = {"spec_id": "RES-001", "spec_name": "Retry", "target_file": "core/base_agent.py"}
        comparison = {
            "before": _make_snapshot(success_rate=0.85),
            "after": _make_snapshot(success_rate=0.86),
            "delta_success": 0.01,
            "delta_error_streak": 0,
            "delta_ci_fail": 0,
            "verdict": "neutral",
            "reasons": [],
        }
        lesson = fb._format_lesson(obs, comparison)
        assert "NEUTRE" in lesson


class TestPersistence:

    def test_save_and_load(self):
        fb = EvolutionFeedbackLoop()
        fb._pending_observations = [{
            "spec_id": "PERF-001", "spec_name": "Test",
            "target_file": "x.py", "before_snapshot": _make_snapshot(),
            "routines_observed": 5, "started_at": datetime.now().isoformat(),
        }]
        fb._completed_feedbacks = [{
            "spec_id": "RES-001", "verdict": "improved",
            "evaluated_at": datetime.now().isoformat(),
        }]
        fb._save()

        assert os.path.exists(_FAKE_STATE_FILE)

        EvolutionFeedbackLoop.reset_singleton()
        fb2 = EvolutionFeedbackLoop()
        assert len(fb2._pending_observations) == 1
        assert fb2._pending_observations[0]["spec_id"] == "PERF-001"
        assert fb2._pending_observations[0]["routines_observed"] == 5
        assert len(fb2._completed_feedbacks) == 1

    def test_load_missing_file(self):
        fb = EvolutionFeedbackLoop()
        assert fb._pending_observations == []
        assert fb._completed_feedbacks == []

    def test_load_corrupt_file(self):
        with open(_FAKE_STATE_FILE, "w") as f:
            f.write("not json")
        EvolutionFeedbackLoop.reset_singleton()
        fb = EvolutionFeedbackLoop()
        assert fb._pending_observations == []

    def test_completed_feedbacks_trimmed_to_50(self):
        fb = EvolutionFeedbackLoop()
        fb._completed_feedbacks = [
            {"spec_id": f"X-{i}", "verdict": "neutral"}
            for i in range(60)
        ]
        fb._save()

        with open(_FAKE_STATE_FILE, "r") as f:
            data = json.load(f)
        assert len(data["completed_feedbacks"]) == 50


class TestIntegration:

    @pytest.mark.asyncio
    async def test_full_cycle_confirm(self):
        """Cycle complet : deploy → observe → evaluate → confirm."""
        fb = EvolutionFeedbackLoop()

        before = _make_snapshot(success_rate=0.80, error_streak=2, ci_fail=1)
        after = _make_snapshot(success_rate=0.90, error_streak=0, ci_fail=1)

        mock_catalog = MagicMock()
        mock_journal = MagicMock()
        mock_bus = AsyncMock()

        # Phase 1 : Déploiement
        with patch.object(fb, "_capture_metrics", return_value=before):
            await fb._on_evolution_deployed({
                "data": {
                    "spec_id": "PERF-001",
                    "spec_name": "Cache LRU Router",
                    "target_file": "core/router.py",
                }
            })

        assert len(fb._pending_observations) == 1

        # Phase 2 : Observer 14 routines (pas encore le seuil)
        with patch.object(fb, "_evaluate_deployment", new_callable=AsyncMock) as mock_eval:
            for _ in range(fb.OBSERVATION_PERIOD - 1):
                await fb._on_routine_complete({"data": {}})
            mock_eval.assert_not_called()

        assert fb._pending_observations[0]["routines_observed"] == fb.OBSERVATION_PERIOD - 1

        # Phase 3 : 15e routine → évaluation
        with patch.object(fb, "_capture_metrics", return_value=after), \
             patch.dict("sys.modules", {
                 "core.evolution_catalog": MagicMock(catalog=mock_catalog),
                 "core.strategic_journal": MagicMock(journal=mock_journal),
                 "core.event_bus.bus": MagicMock(bus=mock_bus),
             }):
            await fb._on_routine_complete({"data": {}})

        # Vérifications
        assert len(fb._pending_observations) == 0
        assert len(fb._completed_feedbacks) == 1
        mock_catalog.mark_confirmed.assert_called_once_with("PERF-001")

    @pytest.mark.asyncio
    async def test_full_cycle_rollback(self):
        """Cycle complet : deploy → observe → evaluate → rollback."""
        fb = EvolutionFeedbackLoop()

        before = _make_snapshot(success_rate=0.85, error_streak=0, ci_fail=1)
        after = _make_snapshot(success_rate=0.60, error_streak=4, ci_fail=3)

        mock_catalog = MagicMock()
        mock_journal = MagicMock()
        mock_bus = AsyncMock()

        # Phase 1 : Déploiement
        with patch.object(fb, "_capture_metrics", return_value=before):
            await fb._on_evolution_deployed({
                "data": {
                    "spec_id": "INT-001",
                    "spec_name": "Trimming contexte",
                    "target_file": "core/base_agent.py",
                }
            })

        # Phase 2 : Avancer directement au seuil
        fb._pending_observations[0]["routines_observed"] = fb.OBSERVATION_PERIOD - 1

        # Phase 3 : Dernière routine → évaluation avec dégradation
        with patch.object(fb, "_capture_metrics", return_value=after), \
             patch.object(fb, "_rollback_spec", new_callable=AsyncMock) as mock_rb, \
             patch.dict("sys.modules", {
                 "core.evolution_catalog": MagicMock(catalog=mock_catalog),
                 "core.strategic_journal": MagicMock(journal=mock_journal),
                 "core.event_bus.bus": MagicMock(bus=mock_bus),
             }):
            await fb._on_routine_complete({"data": {}})

        # Vérifications
        mock_rb.assert_called_once()
        mock_catalog.mark_rolled_back.assert_called_once()
        assert fb._completed_feedbacks[0]["verdict"] == "degraded"
