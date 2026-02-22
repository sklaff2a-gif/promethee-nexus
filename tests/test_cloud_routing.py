"""Tests pour le routage Cloud intelligent — économie de budget Gemini.

Couche 1 : Court-circuit _evaluate_complexity() sur marqueurs internes
Couche 2 : Flag _force_local_next via orchestrateur
Couche 3 : Payload force_local dans l'autonomie
Bonus   : Budget quotidien + cooldown 429 dans _generate_code_cloud()
"""
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date

from core.base_agent import BaseAgent
from core.orchestrator import Orchestrator


# ─── Couche 1 : Court-circuit _evaluate_complexity() ───

class TestEvaluateComplexityShortCircuit:
    """Vérifie que les marqueurs internes court-circuitent l'évaluateur de complexité."""

    def setup_method(self):
        self.agent = BaseAgent("test_agent", "Testeur", "Agent de test")

    @pytest.mark.asyncio
    async def test_protocole_autonomie_returns_false(self):
        """Prompt avec PROTOCOLE_AUTONOMIE → local (pas d'appel LLM)."""
        result = await self.agent._evaluate_complexity(
            "Tu es Coder. Mission: PROTOCOLE_AUTONOMIE analyse du code"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_mode_veille_returns_false(self):
        """Prompt avec [MODE VEILLE] → local."""
        result = await self.agent._evaluate_complexity(
            "Tu es Researcher. Mission: [MODE VEILLE] cherche une astuce Python"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_conseil_multi_agents_returns_false(self):
        """Prompt avec CONSEIL multi-agents → local (Council)."""
        result = await self.agent._evaluate_complexity(
            "CONSEIL multi-agents : débat sur l'architecture du système"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_youtube_veille_returns_false(self):
        """Prompt avec YOUTUBE_VEILLE → local."""
        result = await self.agent._evaluate_complexity(
            "YOUTUBE_VEILLE — cherche des vidéos récentes sur les agents IA"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_dropzone_analysis_returns_false(self):
        """Prompt avec DROPZONE_ANALYSIS → local."""
        result = await self.agent._evaluate_complexity(
            "DROPZONE_ANALYSIS — analyse du fichier uploadé"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_evolution_pipeline_returns_false(self):
        """Prompt avec EVOLUTION_PIPELINE → local."""
        result = await self.agent._evaluate_complexity(
            "EVOLUTION_PIPELINE — spec code pour base_agent.py"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_memory_cleanup_returns_false(self):
        """Prompt avec MEMORY_CLEANUP → local."""
        result = await self.agent._evaluate_complexity(
            "MEMORY_CLEANUP — nettoyage de la mémoire RAG"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_council_research_returns_false(self):
        """Prompt avec COUNCIL_RESEARCH → local."""
        result = await self.agent._evaluate_complexity(
            "COUNCIL_RESEARCH — recherche pré-débat"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_normal_prompt_calls_llm(self):
        """Un prompt utilisateur normal doit appeler le LLM (pas de court-circuit)."""
        with patch.object(self.agent, "_call_ollama", new_callable=AsyncMock) as mock_ollama:
            mock_ollama.return_value = "NON"
            result = await self.agent._evaluate_complexity(
                "Explique-moi comment fonctionne le pattern observer en Python"
            )
            # L'appel LLM doit avoir été fait (pas de court-circuit)
            mock_ollama.assert_called_once()
            assert result is False


# ─── Couche 2 : Flag _force_local_next ───

class TestForceLocalFlag:
    """Vérifie que le flag _force_local_next bypass le Cloud dans generate_content()."""

    def setup_method(self):
        self.agent = BaseAgent("test_agent", "Testeur", "Agent de test")

    def test_flag_initialized_false(self):
        """Le flag est False par défaut."""
        assert self.agent._force_local_next is False

    @pytest.mark.asyncio
    async def test_force_local_skips_evaluate(self):
        """Avec _force_local_next=True, generate_content() ne fait pas d'évaluation Cloud."""
        self.agent._force_local_next = True

        with patch.object(self.agent, "_evaluate_complexity", new_callable=AsyncMock) as mock_eval, \
             patch.object(self.agent, "_call_ollama_stream", new_callable=AsyncMock) as mock_stream, \
             patch.object(self.agent, "recall", return_value=""):
            mock_stream.return_value = "Réponse locale"
            result = await self.agent.generate_content("Test prompt")

            # _evaluate_complexity ne doit PAS être appelé
            mock_eval.assert_not_called()
            # Le traitement local doit avoir eu lieu
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_flag_reset_after_use(self):
        """Le flag est reset (one-shot) après utilisation."""
        self.agent._force_local_next = True

        with patch.object(self.agent, "_call_ollama_stream", new_callable=AsyncMock) as mock_stream, \
             patch.object(self.agent, "recall", return_value=""):
            mock_stream.return_value = "Réponse locale"
            await self.agent.generate_content("Test prompt")

        assert self.agent._force_local_next is False


# ─── Couche 2bis : Orchestrateur force_local ───

class TestOrchestratorForceLocal:
    """Vérifie que l'orchestrateur détecte les contextes internes et force le local."""

    def setup_method(self):
        self.orchestrator = Orchestrator()
        self.agent = BaseAgent("coder", "Coder", "Génère du code")
        self.agent._force_local_next = False

    @pytest.mark.asyncio
    async def test_internal_context_sets_flag(self):
        """Un dispatch avec contexte PROTOCOLE_AUTONOMIE → flag True."""
        # Enregistrer l'agent
        await self.orchestrator.register_agent("coder", self.agent)

        # Mock process_task pour capturer le flag AVANT l'appel
        captured_flag = {}
        original_process = self.agent.process_task

        async def capture_and_process(payload):
            captured_flag["value"] = self.agent._force_local_next
            return {"status": "success", "result": "ok"}

        self.agent.process_task = capture_and_process

        await self.orchestrator.dispatch_task("coder", {
            "mission": "[MODE VEILLE] Analyse du code",
            "context": "PROTOCOLE_AUTONOMIE",
        })

        assert captured_flag["value"] is True

    @pytest.mark.asyncio
    async def test_user_mission_no_flag(self):
        """Un dispatch sans contexte interne (mission utilisateur) → flag False."""
        await self.orchestrator.register_agent("coder", self.agent)

        captured_flag = {}

        async def capture_and_process(payload):
            captured_flag["value"] = self.agent._force_local_next
            return {"status": "success", "result": "ok"}

        self.agent.process_task = capture_and_process

        await self.orchestrator.dispatch_task("coder", {
            "mission": "Écris une fonction de tri en Python",
            "context": "",
        })

        assert captured_flag["value"] is False

    @pytest.mark.asyncio
    async def test_force_local_payload_sets_flag(self):
        """Un dispatch avec force_local=True dans le payload → flag True."""
        await self.orchestrator.register_agent("coder", self.agent)

        captured_flag = {}

        async def capture_and_process(payload):
            captured_flag["value"] = self.agent._force_local_next
            return {"status": "success", "result": "ok"}

        self.agent.process_task = capture_and_process

        await self.orchestrator.dispatch_task("coder", {
            "mission": "Analyse du code",
            "context": "",
            "force_local": True,
        })

        assert captured_flag["value"] is True

    @pytest.mark.asyncio
    async def test_flag_cleaned_after_dispatch(self):
        """Le flag est nettoyé après le dispatch (sécurité)."""
        await self.orchestrator.register_agent("coder", self.agent)

        async def mock_process(payload):
            return {"status": "success", "result": "ok"}

        self.agent.process_task = mock_process

        await self.orchestrator.dispatch_task("coder", {
            "mission": "[MODE VEILLE] test",
            "context": "PROTOCOLE_AUTONOMIE",
        })

        assert self.agent._force_local_next is False


# ─── Couche 4 : Budget Evolution _generate_code_cloud() ───

class TestEvolutionBudget:
    """Vérifie que _generate_code_cloud() respecte le budget et le cooldown."""

    def setup_method(self):
        # Reset les compteurs de classe (nouveau système RPM)
        BaseAgent._rpm_windows = {}
        BaseAgent._daily_model_calls = {}
        BaseAgent._cloud_cooldown_until = 0.0
        BaseAgent._daily_cloud_calls_evolution = 0
        BaseAgent._daily_model_reset_day = date.today()
        BaseAgent._cloud_429_count_today = 0
        BaseAgent._cloud_429_reset_day = None

    @pytest.mark.asyncio
    async def test_cooldown_429_blocks_cloud(self):
        """Si en cooldown 429, _generate_code_cloud() retourne '' sans appel."""
        from Agents.evolution_agent import DivineEvolution
        evo = DivineEvolution()

        # Simuler un cooldown actif
        BaseAgent._cloud_cooldown_until = time.time() + 3600

        with patch.object(evo, "_get_gemini_client") as mock_client:
            result = await evo._generate_code_cloud("Génère du code Python")
            assert result == ""
            mock_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_budget_evolution_exhausted_blocks_cloud(self):
        """Si budget Evolution épuisé, _generate_code_cloud() retourne ''."""
        from Agents.evolution_agent import DivineEvolution
        evo = DivineEvolution()

        BaseAgent._daily_cloud_calls_evolution = BaseAgent.MAX_DAILY_EVOLUTION_CLOUD

        with patch.object(evo, "_get_gemini_client") as mock_client:
            result = await evo._generate_code_cloud("Génère du code Python")
            assert result == ""
            mock_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_call_increments_counters(self):
        """Un appel Cloud réussi incrémente les compteurs RPM + daily + evolution."""
        from Agents.evolution_agent import DivineEvolution
        evo = DivineEvolution()

        mock_response = MagicMock()
        mock_response.text = "def hello():\n    return 'world'\n# Plus de 50 chars de code généré ici"

        mock_client = MagicMock()
        mock_client.generate_content = MagicMock(return_value=mock_response)

        with patch.object(evo, "_get_gemini_client", return_value=mock_client):
            result = await evo._generate_code_cloud("Génère du code Python")

        assert result == mock_response.text
        # Le modèle utilisé est le premier dans cloud_models
        model = evo.cloud_models[0]
        assert BaseAgent._daily_model_calls.get(model, 0) == 1
        assert len(BaseAgent._rpm_windows.get(model, [])) == 1
        assert BaseAgent._daily_cloud_calls_evolution == 1

    @pytest.mark.asyncio
    async def test_429_error_activates_cooldown(self):
        """Une erreur 429 active le cooldown."""
        from Agents.evolution_agent import DivineEvolution
        evo = DivineEvolution()

        mock_client = MagicMock()
        mock_client.generate_content = MagicMock(
            side_effect=Exception("429 Resource has been exhausted (quota exceeded)")
        )

        with patch.object(evo, "_get_gemini_client", return_value=mock_client):
            result = await evo._generate_code_cloud("Génère du code Python")

        assert result == ""
        assert BaseAgent._cloud_cooldown_until > time.time()


# ─── Tests marqueurs dans _LOCAL_FORCE_MARKERS ───

class TestLocalForceMarkers:
    """Vérifie la cohérence des marqueurs entre base_agent et orchestrator."""

    def test_markers_exist_on_base_agent(self):
        """_LOCAL_FORCE_MARKERS est défini sur BaseAgent."""
        assert hasattr(BaseAgent, "_LOCAL_FORCE_MARKERS")
        assert len(BaseAgent._LOCAL_FORCE_MARKERS) >= 8

    def test_orchestrator_markers_from_base_agent(self):
        """L'orchestrateur utilise les mêmes marqueurs que BaseAgent (source unique)."""
        markers = Orchestrator._get_internal_markers()
        assert markers is BaseAgent._LOCAL_FORCE_MARKERS
        assert len(markers) >= 8


# ─── Tests RPM Enforcement ───

class TestRpmEnforcement:
    """Vérifie que _check_rpm bloque correctement après N appels dans la minute."""

    def setup_method(self):
        BaseAgent._rpm_windows = {}

    def test_check_rpm_allows_under_limit(self):
        """RPM sous la limite → autorisé."""
        assert BaseAgent._check_rpm("models/gemini-2.5-pro") is True

    def test_check_rpm_blocks_at_limit(self):
        """RPM à la limite → bloqué."""
        from collections import deque
        now = time.time()
        # Remplir la fenêtre à la limite (50 pour Pro)
        BaseAgent._rpm_windows["models/gemini-2.5-pro"] = deque(
            [now - i * 0.5 for i in range(50)]
        )
        assert BaseAgent._check_rpm("models/gemini-2.5-pro") is False

    def test_check_rpm_unblocks_after_expiry(self):
        """Après 60s, les timestamps expirent et le RPM se libère."""
        from collections import deque
        old = time.time() - 61  # Plus de 60s
        BaseAgent._rpm_windows["models/gemini-2.5-pro"] = deque(
            [old - i for i in range(50)]
        )
        assert BaseAgent._check_rpm("models/gemini-2.5-pro") is True

    def test_record_cloud_call_increments_rpm_and_daily(self):
        """_record_cloud_call incrémente RPM et daily."""
        BaseAgent._daily_model_calls = {}
        BaseAgent._record_cloud_call("models/gemini-2.5-flash")
        assert len(BaseAgent._rpm_windows["models/gemini-2.5-flash"]) == 1
        assert BaseAgent._daily_model_calls["models/gemini-2.5-flash"] == 1

    def test_unknown_model_uses_default_rpm(self):
        """Un modèle inconnu utilise CLOUD_RPM_DEFAULT (30)."""
        assert BaseAgent._check_rpm("models/unknown-model") is True
        from collections import deque
        now = time.time()
        BaseAgent._rpm_windows["models/unknown-model"] = deque(
            [now - i * 0.5 for i in range(30)]
        )
        assert BaseAgent._check_rpm("models/unknown-model") is False


# ─── Tests Daily Budget Per Model ───

class TestDailyBudgetPerModel:
    """Vérifie que _check_daily_budget bloque après le quota par modèle."""

    def setup_method(self):
        BaseAgent._daily_model_calls = {}

    def test_budget_allows_under_limit(self):
        """Budget sous la limite → autorisé."""
        assert BaseAgent._check_daily_budget("models/gemini-2.5-pro") is True

    def test_budget_blocks_at_limit(self):
        """Budget Pro à 100 → bloqué."""
        BaseAgent._daily_model_calls["models/gemini-2.5-pro"] = 100
        assert BaseAgent._check_daily_budget("models/gemini-2.5-pro") is False

    def test_budget_flash_higher_limit(self):
        """Flash a une limite de 2000, bien plus haute que Pro."""
        BaseAgent._daily_model_calls["models/gemini-2.5-flash"] = 100
        assert BaseAgent._check_daily_budget("models/gemini-2.5-flash") is True

    def test_budget_unknown_model_default(self):
        """Un modèle inconnu a un budget par défaut de 500."""
        BaseAgent._daily_model_calls["models/unknown"] = 499
        assert BaseAgent._check_daily_budget("models/unknown") is True
        BaseAgent._daily_model_calls["models/unknown"] = 500
        assert BaseAgent._check_daily_budget("models/unknown") is False
