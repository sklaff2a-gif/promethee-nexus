import os
import tempfile
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from core.experience_registry import ExperienceRegistry, Experience, REGISTRY_FILE

_FAKE_REGISTRY_FILE = os.path.join(tempfile.gettempdir(), "test_experience_registry.json")


@pytest.fixture(autouse=True)
def isolate_registry():
    """Isole le registre entre chaque test."""
    if os.path.exists(_FAKE_REGISTRY_FILE):
        os.remove(_FAKE_REGISTRY_FILE)
    with patch("core.experience_registry.REGISTRY_FILE", _FAKE_REGISTRY_FILE):
        ExperienceRegistry.reset_singleton()
        yield
        ExperienceRegistry.reset_singleton()
    if os.path.exists(_FAKE_REGISTRY_FILE):
        os.remove(_FAKE_REGISTRY_FILE)


def _make_experience(spec_id="TEST-001", spec_name="Test Spec",
                     phase_reached="phase5", outcome="failed",
                     failure_reason="raison test", attempt=1,
                     code_source="cloud", code_length=500,
                     alien_imports=None, syntax_error="",
                     truncation_ratio=0.0, architect_verdict="",
                     system_mood="neutre", success_rate=0.85,
                     error_streak=0) -> Experience:
    """Helper pour créer une Experience de test."""
    from datetime import datetime
    return Experience(
        id=f"EXP-{datetime.now().strftime('%Y%m%d%H%M%S')}-{spec_id}",
        spec_id=spec_id,
        spec_name=spec_name,
        attempt_number=attempt,
        timestamp=datetime.now().isoformat(),
        phase_reached=phase_reached,
        outcome=outcome,
        failure_reason=failure_reason,
        code_source=code_source,
        code_length=code_length,
        alien_imports=alien_imports or [],
        syntax_error=syntax_error,
        truncation_ratio=truncation_ratio,
        architect_verdict=architect_verdict,
        system_mood=system_mood,
        success_rate_at_attempt=success_rate,
        error_streak_at_attempt=error_streak,
    )


class TestExperienceRecord:

    def test_record_and_retrieve(self):
        """Enregistre 1 expérience, get_spec_history() la retrouve."""
        registry = ExperienceRegistry()
        exp = _make_experience(spec_id="PERF-001")
        registry.record(exp)
        history = registry.get_spec_history("PERF-001")
        assert len(history) == 1
        assert history[0]["spec_id"] == "PERF-001"
        assert history[0]["outcome"] == "failed"

    def test_fifo_200_limit(self):
        """Enregistre 210 expériences, vérifie qu'il en reste 200."""
        registry = ExperienceRegistry()
        for i in range(210):
            exp = _make_experience(spec_id=f"SPEC-{i:03d}")
            registry.record(exp)
        assert len(registry._experiences) == 200
        # Les 10 premières doivent avoir été supprimées
        spec_ids = [e["spec_id"] for e in registry._experiences]
        assert "SPEC-000" not in spec_ids
        assert "SPEC-209" in spec_ids

    def test_persistence_reload(self):
        """Enregistre, reset singleton, recharge, vérifie les données."""
        registry = ExperienceRegistry()
        exp = _make_experience(spec_id="PERSIST-001", failure_reason="test persistance")
        registry.record(exp)

        # Reset singleton et recréer
        ExperienceRegistry.reset_singleton()
        registry2 = ExperienceRegistry()
        history = registry2.get_spec_history("PERSIST-001")
        assert len(history) == 1
        assert history[0]["failure_reason"] == "test persistance"

    def test_empty_registry(self):
        """get_spec_history sur un spec inexistant retourne []."""
        registry = ExperienceRegistry()
        assert registry.get_spec_history("INEXISTANT") == []

    def test_multiple_specs(self):
        """3 expériences sur 2 specs, vérifier le filtrage par spec_id."""
        registry = ExperienceRegistry()
        registry.record(_make_experience(spec_id="SPEC-A"))
        registry.record(_make_experience(spec_id="SPEC-B"))
        registry.record(_make_experience(spec_id="SPEC-A", attempt=2))

        assert len(registry.get_spec_history("SPEC-A")) == 2
        assert len(registry.get_spec_history("SPEC-B")) == 1


class TestFailureSummary:

    def test_failure_summary_format(self):
        """Le résumé contient HISTORIQUE et les raisons."""
        registry = ExperienceRegistry()
        registry.record(_make_experience(
            spec_id="SUM-001", phase_reached="phase4b",
            failure_reason="Imports aliens détectés (django)"
        ))
        summary = registry.get_failure_summary("SUM-001")
        assert "HISTORIQUE" in summary
        assert "phase4b" in summary
        assert "django" in summary

    def test_failure_summary_max_3(self):
        """Avec 5 échecs, seuls les 3 derniers apparaissent."""
        registry = ExperienceRegistry()
        for i in range(5):
            registry.record(_make_experience(
                spec_id="MANY-001", attempt=i + 1,
                failure_reason=f"Erreur numero {i}"
            ))
        summary = registry.get_failure_summary("MANY-001")
        assert "HISTORIQUE (5" in summary
        # Les 3 derniers : 2, 3, 4
        assert "numero 2" in summary
        assert "numero 3" in summary
        assert "numero 4" in summary
        # Le premier ne doit PAS y être
        assert "numero 0" not in summary

    def test_failure_summary_no_failures(self):
        """Spec avec que des succès -> retourne ''."""
        registry = ExperienceRegistry()
        registry.record(_make_experience(
            spec_id="OK-001", outcome="deployed", failure_reason=""
        ))
        assert registry.get_failure_summary("OK-001") == ""

    def test_failure_summary_empty(self):
        """Spec inexistante -> retourne ''."""
        registry = ExperienceRegistry()
        assert registry.get_failure_summary("NOPE-001") == ""


class TestLearningInsights:

    def test_repeated_phase_failure(self):
        """3 échecs phase4b sur même spec -> insight detected."""
        registry = ExperienceRegistry()
        for i in range(3):
            registry.record(_make_experience(
                spec_id="RPT-001", phase_reached="phase4b", attempt=i + 1
            ))
        insights = registry.get_learning_insights()
        phase_insights = [i for i in insights if i["type"] == "repeated_phase_failure"]
        assert len(phase_insights) >= 1
        assert phase_insights[0]["spec_id"] == "RPT-001"
        assert phase_insights[0]["phase"] == "phase4b"
        assert phase_insights[0]["count"] == 3

    def test_recurring_alien(self):
        """'django' apparaît 3 fois -> insight recurring_alien."""
        registry = ExperienceRegistry()
        for i in range(3):
            registry.record(_make_experience(
                spec_id=f"ALN-{i:03d}", phase_reached="phase4b",
                alien_imports=["django"]
            ))
        insights = registry.get_learning_insights()
        alien_insights = [i for i in insights if i["type"] == "recurring_alien"]
        assert len(alien_insights) >= 1
        assert alien_insights[0]["import"] == "django"
        assert alien_insights[0]["count"] == 3

    def test_no_insights_clean_registry(self):
        """Que des succès -> insights vide."""
        registry = ExperienceRegistry()
        for i in range(5):
            registry.record(_make_experience(
                spec_id=f"CLN-{i:03d}", outcome="deployed", failure_reason=""
            ))
        insights = registry.get_learning_insights()
        assert insights == []


class TestRegistryStats:

    def test_stats_counts(self):
        """Vérifie by_outcome et failure_phases."""
        registry = ExperienceRegistry()
        registry.record(_make_experience(outcome="failed", phase_reached="phase4b"))
        registry.record(_make_experience(outcome="failed", phase_reached="phase4b"))
        registry.record(_make_experience(outcome="failed", phase_reached="phase3"))
        registry.record(_make_experience(outcome="deployed"))

        stats = registry.get_stats()
        assert stats["total"] == 4
        assert stats["by_outcome"]["failed"] == 3
        assert stats["by_outcome"]["deployed"] == 1
        assert stats["failure_phases"]["phase4b"] == 2
        assert stats["failure_phases"]["phase3"] == 1

    def test_stats_empty(self):
        """Registre vide -> total=0."""
        registry = ExperienceRegistry()
        stats = registry.get_stats()
        assert stats["total"] == 0
        assert stats["by_outcome"] == {}
        assert stats["failure_phases"] == {}


class TestPipelineIntegration:
    """Tests d'intégration avec le pipeline Evolution."""

    @pytest.fixture(autouse=True)
    def isolate_catalog(self):
        """Isole le catalogue evolution."""
        from core.evolution_catalog import EvolutionCatalog
        _cat_file = os.path.join(tempfile.gettempdir(), "test_exp_catalog.json")
        if os.path.exists(_cat_file):
            os.remove(_cat_file)
        with patch("core.evolution_catalog.CATALOG_STATE_FILE", _cat_file):
            EvolutionCatalog.reset_singleton()
            yield
            EvolutionCatalog.reset_singleton()
        if os.path.exists(_cat_file):
            os.remove(_cat_file)

    @pytest.fixture(autouse=True)
    def disable_protected_files(self):
        with patch("Agents.factory_agent._PROTECTED_FILES", set()):
            yield

    @pytest.mark.asyncio
    async def test_alien_rejection_recorded(self):
        """Pipeline avec code Django -> expérience enregistrée avec phase4b."""
        from Agents.evolution_agent import DivineEvolution

        evo = DivineEvolution()
        registry = ExperienceRegistry()

        django_code = (
            "from django.db import models\n"
            "class User(models.Model):\n"
            "    name = models.CharField(max_length=100)\n"
            "    email = models.EmailField()\n"
            "# padding for min 50 chars requirement here ok\n"
        )

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={"status": "success"})

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value="# existing\npass"), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=django_code), \
             patch("core.orchestrator.orchestrator", mock_orch):
            await evo.process_task({"mission": "[MODE VEILLE]"})

        # Vérifier qu'une expérience phase4b a été enregistrée
        all_exp = registry._experiences
        phase4b = [e for e in all_exp if e["phase_reached"] == "phase4b"]
        assert len(phase4b) >= 1
        assert "django" in phase4b[0].get("alien_imports", [])

    @pytest.mark.asyncio
    async def test_successful_deploy_recorded(self):
        """Pipeline complet -> expérience avec outcome='deployed'."""
        from Agents.evolution_agent import DivineEvolution

        evo = DivineEvolution()
        registry = ExperienceRegistry()

        valid_code = (
            "import os\nimport logging\n\nlogger = logging.getLogger('test')\n\n"
            "def hello():\n    return 42\n\nclass Helper:\n    pass\n"
        )

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={"status": "success"})
        mock_orch._contains_python_code = MagicMock(return_value=True)

        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value="# existing code\npass"), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=valid_code), \
             patch("core.orchestrator.orchestrator", mock_orch), \
             patch("core.event_bus.bus.bus", mock_bus):
            await evo.process_task({"mission": "[MODE VEILLE]"})

        deployed = [e for e in registry._experiences if e["outcome"] == "deployed"]
        assert len(deployed) >= 1
        assert deployed[0]["phase_reached"] == "phase5"
        assert deployed[0]["code_source"] == "cloud"

    @pytest.mark.asyncio
    async def test_past_failures_injected_in_prompt(self):
        """Spec avec échecs passés -> prompt contient HISTORIQUE."""
        from Agents.evolution_agent import DivineEvolution
        from core.evolution_catalog import EvolutionCatalog, ImprovementSpec

        evo = DivineEvolution()
        registry = ExperienceRegistry()

        # Pré-remplir le registre avec un échec pour TOUTES les specs
        # (on ne sait pas laquelle sera sélectionnée par le scoring)
        cat = EvolutionCatalog()
        for sid in cat.specs:
            registry.record(_make_experience(
                spec_id=sid,
                phase_reached="phase4b",
                failure_reason="Imports aliens (django) dans le code"
            ))

        captured_prompts = []
        original_cloud = evo._generate_code_cloud

        async def capture_prompt(prompt):
            captured_prompts.append(prompt)
            return ""  # Pas de code -> pipeline s'arrête

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={"result": "", "status": "success"})

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value="# existing\npass"), \
             patch.object(evo, "_generate_code_cloud", side_effect=capture_prompt), \
             patch("core.orchestrator.orchestrator", mock_orch):
            await evo.process_task({"mission": "[MODE VEILLE]"})

        # Vérifier que le prompt contient l'historique des échecs
        assert len(captured_prompts) >= 1
        assert "HISTORIQUE" in captured_prompts[0]
        assert "django" in captured_prompts[0]
