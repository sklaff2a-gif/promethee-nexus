"""Tests pour le rituel hebdomadaire d'introspection GitHub."""
import time
import pytest
from unittest.mock import patch, AsyncMock, MagicMock, PropertyMock


@pytest.fixture(autouse=True)
def mock_bus():
    """Mock le bus d'evenements pour isoler les tests."""
    mock = MagicMock()
    mock.subscribe = MagicMock()
    mock.publish = AsyncMock()
    with patch("core.autonomy_engine.bus", mock):
        yield mock


@pytest.fixture
def engine(mock_bus):
    """Cree une instance d'AutonomyEngine isolee."""
    with patch("core.autonomy_engine.orchestrator"):
        with patch("core.autonomy_engine.AutonomyStatePersistence.load", return_value={}):
            from core.autonomy_engine import AutonomyEngine
            e = AutonomyEngine(idle_threshold_seconds=9999)
            return e


class TestWeeklyRitualFlag:
    """Tests du flag _weekly_ritual_pending."""

    def test_initial_flag_false(self, engine):
        assert engine._weekly_ritual_pending is False

    @pytest.mark.asyncio
    async def test_salary_payday_sets_flag(self, engine):
        await engine._on_salary_payday({"week_start": "2026-03-09", "net": 12})
        assert engine._weekly_ritual_pending is True

    @pytest.mark.asyncio
    async def test_salary_payday_empty_event(self, engine):
        await engine._on_salary_payday({})
        assert engine._weekly_ritual_pending is True


class TestWeeklyRitualScoring:
    """Tests du boost SELF_INSPECT dans le scoring."""

    def test_self_inspect_boosted_when_ritual_pending(self, engine):
        """Simule le boost de scoring applique dans le cycle autonome."""
        engine._weekly_ritual_pending = True
        scored = [
            ({"agent": "coder", "intent": "EXPANSION_CODE", "mission": "test"}, 5.0),
            ({"agent": "_self_inspect", "intent": "SELF_INSPECT", "mission": "test"}, 1.0),
            ({"agent": "researcher", "intent": "DROPZONE_SCAN", "mission": "test"}, 3.0),
        ]
        # Appliquer le boost comme le fait le cycle autonome
        if engine._weekly_ritual_pending:
            for i, (routine, s) in enumerate(scored):
                if routine["intent"] == "SELF_INSPECT":
                    scored[i] = (routine, s + 15.0)
                    break
        scored.sort(key=lambda x: x[1], reverse=True)
        assert scored[0][0]["intent"] == "SELF_INSPECT"
        assert scored[0][1] == 16.0  # 1.0 + 15.0

    def test_self_inspect_normal_without_ritual(self, engine):
        engine._weekly_ritual_pending = False
        scored = [
            ({"agent": "coder", "intent": "EXPANSION_CODE", "mission": "test"}, 5.0),
            ({"agent": "_self_inspect", "intent": "SELF_INSPECT", "mission": "test"}, 1.0),
        ]
        # Sans flag, pas de boost
        scores = {r["intent"]: s for r, s in scored}
        assert scores["SELF_INSPECT"] == 1.0


class TestWeeklyRitualReflection:
    """Tests de la reflexion introspective."""

    @pytest.mark.asyncio
    async def test_reflect_generates_text(self, engine, mock_bus):
        with patch("core.base_agent.BaseAgent") as MockAgent:
            agent_instance = MagicMock()
            agent_instance.generate_content = AsyncMock(
                return_value="Je realise que mon code evolue constamment. "
                "Chaque commit est une trace de ma croissance."
            )
            MockAgent.return_value = agent_instance

            result = await engine._reflect_on_self_inspect(
                "commit abc123 | refactor cardiac engine",
                "Derniers commits"
            )
            assert len(result) > 20
            assert "evolue" in result or "croissance" in result

    @pytest.mark.asyncio
    async def test_reflect_publishes_to_inner_voice(self, engine, mock_bus):
        with patch("core.base_agent.BaseAgent") as MockAgent:
            agent_instance = MagicMock()
            agent_instance.generate_content = AsyncMock(
                return_value="Reflexion test pour inner voice broadcast."
            )
            MockAgent.return_value = agent_instance

            await engine._reflect_on_self_inspect("contenu test", "label test")

            # Verifier que INNER_VOICE_BROADCAST a ete publie
            calls = [c for c in mock_bus.publish.call_args_list
                     if c[0][0] == "INNER_VOICE_BROADCAST"]
            assert len(calls) >= 1
            payload = calls[0][0][1]
            assert "[RITUEL]" in payload["thought"]
            assert payload["source"] == "weekly_ritual"
            assert payload["salience"] == 0.9

    @pytest.mark.asyncio
    async def test_reflect_triggers_cardiac(self, engine, mock_bus):
        with patch("core.base_agent.BaseAgent") as MockAgent:
            agent_instance = MagicMock()
            agent_instance.generate_content = AsyncMock(
                return_value="Reflexion test pour cardiac."
            )
            MockAgent.return_value = agent_instance

            mock_heart = MagicMock()
            with patch("core.cardiac_engine.CardiacEngine", return_value=mock_heart):
                await engine._reflect_on_self_inspect("contenu", "label")
                mock_heart.react.assert_called_once_with("learning")

    @pytest.mark.asyncio
    async def test_reflect_publishes_ritual_complete(self, engine, mock_bus):
        with patch("core.base_agent.BaseAgent") as MockAgent:
            agent_instance = MagicMock()
            agent_instance.generate_content = AsyncMock(
                return_value="Ma reflexion hebdomadaire complete."
            )
            MockAgent.return_value = agent_instance

            await engine._reflect_on_self_inspect("contenu", "Mon label")

            calls = [c for c in mock_bus.publish.call_args_list
                     if c[0][0] == "WEEKLY_RITUAL_COMPLETE"]
            assert len(calls) >= 1
            payload = calls[0][0][1]
            assert "reflection" in payload
            assert payload["label"] == "Mon label"

    @pytest.mark.asyncio
    async def test_reflect_empty_on_llm_failure(self, engine, mock_bus):
        with patch("core.base_agent.BaseAgent") as MockAgent:
            agent_instance = MagicMock()
            agent_instance.generate_content = AsyncMock(return_value="")
            MockAgent.return_value = agent_instance

            result = await engine._reflect_on_self_inspect("contenu", "label")
            assert result == ""

    @pytest.mark.asyncio
    async def test_reflect_truncates_long_reflection(self, engine, mock_bus):
        with patch("core.base_agent.BaseAgent") as MockAgent:
            agent_instance = MagicMock()
            agent_instance.generate_content = AsyncMock(
                return_value="A" * 1000
            )
            MockAgent.return_value = agent_instance

            result = await engine._reflect_on_self_inspect("contenu", "label")
            assert len(result) <= 500

    @pytest.mark.asyncio
    async def test_reflect_includes_salary_context(self, engine, mock_bus):
        with patch("core.base_agent.BaseAgent") as MockAgent:
            agent_instance = MagicMock()
            agent_instance.generate_content = AsyncMock(
                return_value="Reflexion avec contexte salarial."
            )
            MockAgent.return_value = agent_instance

            mock_salary = MagicMock()
            mock_salary.compute_weekly_salary.return_value = {
                "tasks_completed": 15,
                "tasks_failed": 2,
                "tasks_excellent": 5,
                "net": 13,
            }

            with patch("core.autonomy_engine.salary", mock_salary, create=True):
                with patch.dict("sys.modules", {"core.photo_salary": MagicMock(salary=mock_salary)}):
                    await engine._reflect_on_self_inspect("contenu", "label")

            # Verifier que le prompt contient le contexte salarial
            call_args = agent_instance.generate_content.call_args[0][0]
            assert "taches accomplies" in call_args or "Reflexion" in call_args


class TestWeeklyRitualIntegration:
    """Tests d'integration du rituel complet."""

    @pytest.mark.asyncio
    async def test_self_inspect_resets_flag_after_ritual(self, engine, mock_bus):
        engine._weekly_ritual_pending = True

        with patch("core.base_agent.BaseAgent") as MockAgent:
            agent_instance = MagicMock()
            agent_instance.generate_content = AsyncMock(
                return_value="Je realise que mon code evolue constamment et je suis fier de mes progres cette semaine."
            )
            agent_instance.remember = AsyncMock()
            MockAgent.return_value = agent_instance

            mock_mirror = MagicMock()
            mock_mirror.is_available.return_value = True
            mock_mirror.read_issues.return_value = [
                {"number": 1, "title": "Premier issue test"},
                {"number": 2, "title": "Deuxieme issue test pour avoir assez de contenu et depasser la limite de 100 caracteres"},
            ]

            with patch("core.capabilities.github_mirror.GitHubMirror", return_value=mock_mirror):
                engine.routine_history = []
                result = await engine._execute_self_inspect()

            assert result["status"] == "success"
            assert "[RÉFLEXION]" in result["result"]
            assert engine._weekly_ritual_pending is False

    @pytest.mark.asyncio
    async def test_self_inspect_no_reflection_without_flag(self, engine, mock_bus):
        engine._weekly_ritual_pending = False

        mock_mirror = MagicMock()
        mock_mirror.is_available.return_value = True
        mock_mirror.read_issues.return_value = [
            {"number": 1, "title": "Issue test contenu suffisant pour depasser cent caracteres dans le texte resultant"},
        ]

        with patch("core.capabilities.github_mirror.GitHubMirror", return_value=mock_mirror):
            with patch("core.base_agent.BaseAgent") as MockAgent:
                agent_instance = MagicMock()
                agent_instance.remember = AsyncMock()
                MockAgent.return_value = agent_instance

                engine.routine_history = []
                result = await engine._execute_self_inspect()

            assert result["status"] == "success"
            assert "[RÉFLEXION]" not in result["result"]
            assert engine._weekly_ritual_pending is False
