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
        # Reset les compteurs de classe
        BaseAgent._cloud_call_count = 0
        BaseAgent._cloud_cooldown_until = 0.0
        BaseAgent._daily_cloud_calls = 0
        BaseAgent._daily_cloud_calls_evolution = 0
        BaseAgent._daily_cloud_reset_day = date.today()

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

        BaseAgent._daily_cloud_calls_evolution = BaseAgent.MAX_DAILY_EVOLUTION_CALLS

        with patch.object(evo, "_get_gemini_client") as mock_client:
            result = await evo._generate_code_cloud("Génère du code Python")
            assert result == ""
            mock_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_call_increments_counters(self):
        """Un appel Cloud réussi incrémente les 3 compteurs."""
        from Agents.evolution_agent import DivineEvolution
        evo = DivineEvolution()

        mock_response = MagicMock()
        mock_response.text = "def hello():\n    return 'world'\n# Plus de 50 chars de code généré ici"

        mock_client = MagicMock()
        mock_client.generate_content = MagicMock(return_value=mock_response)

        with patch.object(evo, "_get_gemini_client", return_value=mock_client):
            result = await evo._generate_code_cloud("Génère du code Python")

        assert result == mock_response.text
        assert BaseAgent._cloud_call_count == 1
        assert BaseAgent._daily_cloud_calls == 1
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

    def test_markers_exist_on_orchestrator(self):
        """_INTERNAL_CONTEXT_MARKERS est défini sur Orchestrator."""
        assert hasattr(Orchestrator, "_INTERNAL_CONTEXT_MARKERS")
        assert len(Orchestrator._INTERNAL_CONTEXT_MARKERS) >= 5

    def test_orchestrator_markers_subset_of_agent(self):
        """Les marqueurs orchestrateur sont un sous-ensemble des marqueurs agent."""
        for marker in Orchestrator._INTERNAL_CONTEXT_MARKERS:
            assert marker in BaseAgent._LOCAL_FORCE_MARKERS, (
                f"Marqueur '{marker}' dans Orchestrator mais absent de BaseAgent._LOCAL_FORCE_MARKERS"
            )
