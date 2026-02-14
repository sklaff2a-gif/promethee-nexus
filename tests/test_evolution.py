import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from Agents.evolution_agent import (
    DivineEvolution, _is_spec_offtopic, _spec_targets_existing_file,
    _SEARCH_QUERIES, _SPEC_OFFTOPIC_THRESHOLD
)


class TestSpecOfftopic:

    def test_clean_spec_passes(self):
        spec = "Améliorer core/router.py : ajouter un cache LRU dans classify_intent()."
        assert _is_spec_offtopic(spec) is False

    def test_blockchain_spec_rejected(self):
        spec = "Créer un smart contract sur Ethereum pour gérer les transactions blockchain."
        assert _is_spec_offtopic(spec) is True

    def test_rss_spec_rejected(self):
        spec = "Implémenter un agent RSS avec feedparser pour surveiller les flux."
        assert _is_spec_offtopic(spec) is True

    def test_trading_spec_rejected(self):
        spec = "Ajouter un module de trading pour les marchands avec gestion des orders."
        assert _is_spec_offtopic(spec) is True

    def test_langchain_crewai_rejected(self):
        spec = "Remplacer l'orchestrateur par LangChain et CrewAI."
        assert _is_spec_offtopic(spec) is True

    def test_kubernetes_rejected(self):
        spec = "Déployer sur Kubernetes avec Docker et Terraform."
        assert _is_spec_offtopic(spec) is True

    def test_single_keyword_tolerated(self):
        """1 seul mot-clé hors-sujet est toléré (seuil = 2)."""
        spec = "Améliorer core/router.py en s'inspirant du pattern de LangChain."
        assert _is_spec_offtopic(spec) is False

    def test_threshold_is_2(self):
        assert _SPEC_OFFTOPIC_THRESHOLD == 2


class TestSpecTargetsExistingFile:

    def test_core_module(self):
        assert _spec_targets_existing_file("Modifier core/router.py") is True

    def test_agents_module(self):
        assert _spec_targets_existing_file("Améliorer Agents/coder_agent.py") is True

    def test_config(self):
        assert _spec_targets_existing_file("Ajouter un paramètre dans config.py") is True

    def test_main(self):
        assert _spec_targets_existing_file("Modifier main.py pour ajouter un endpoint") is True

    def test_random_file_rejected(self):
        assert _spec_targets_existing_file("Créer merchant_code.py avec du trading") is False

    def test_no_file_rejected(self):
        assert _spec_targets_existing_file("Implémenter un système de cache global") is False


class TestSearchQueryRotation:

    def test_rotation_cycles(self):
        """Les requêtes tournent et reviennent au début."""
        DivineEvolution._query_index = 0
        queries = [DivineEvolution._next_search_query() for _ in range(len(_SEARCH_QUERIES) + 1)]
        # La dernière doit être la même que la première (cycle complet)
        assert queries[-1] == queries[0]

    def test_all_queries_distinct(self):
        """Toutes les requêtes dans la liste sont distinctes."""
        assert len(_SEARCH_QUERIES) == len(set(_SEARCH_QUERIES))


class TestEvolutionPipeline:

    @pytest.fixture(autouse=True)
    def reset_query_index(self):
        DivineEvolution._query_index = 0
        yield

    @pytest.fixture(autouse=True)
    def disable_dedup(self, monkeypatch):
        """Désactive la dédup RAG pour que les tests atteignent le pipeline complet."""
        monkeypatch.setattr(DivineEvolution, "_check_already_explored", lambda self, q: False)

    @pytest.mark.asyncio
    async def test_offtopic_spec_stops_pipeline(self):
        """Si la spec est hors-sujet, le pipeline s'arrête avant le Coder."""
        evo = DivineEvolution()
        offtopic_spec = "Créer un agent RSS avec feedparser et un module de trading blockchain."

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={"result": "veille data", "status": "success"})

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value=offtopic_spec), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE] lancez la veille"})

        assert "R.A.S" in result["result"]
        assert "hors périmètre" in result["result"]
        # Le Coder ne doit PAS avoir été appelé
        calls = [c for c in mock_orch.dispatch_task.call_args_list if c[0][0] == "coder"]
        assert len(calls) == 0

    @pytest.mark.asyncio
    async def test_spec_without_existing_file_stops_pipeline(self):
        """Si la spec ne cible aucun fichier existant, pipeline arrêté."""
        evo = DivineEvolution()
        no_target_spec = "Implémenter un système de cache global avec Redis et mémoire partagée."

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={"result": "veille data", "status": "success"})

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value=no_target_spec), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE] lancez la veille"})

        assert "R.A.S" in result["result"]
        assert "aucun module existant" in result["result"]

    @pytest.mark.asyncio
    async def test_valid_spec_reaches_coder(self):
        """Une spec pertinente passe le filtre et atteint le Coder."""
        evo = DivineEvolution()
        valid_spec = "Modifier core/router.py : ajouter un cache LRU dans classify_intent() pour éviter les appels LLM redondants."

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={"result": "code pertinent", "status": "success"})

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value=valid_spec), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE] lancez la veille"})

        # Le Coder doit avoir été appelé
        coder_calls = [c for c in mock_orch.dispatch_task.call_args_list if c[0][0] == "coder"]
        assert len(coder_calls) == 1

    @pytest.mark.asyncio
    async def test_ras_response_ends_cycle(self):
        """Si l'Evolution répond R.A.S, le cycle s'arrête."""
        evo = DivineEvolution()

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={"result": "veille data", "status": "success"})

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="R.A.S — rien de pertinent."), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE]"})

        assert result["result"] == "R.A.S"

    @pytest.mark.asyncio
    async def test_coder_ras_stops_before_architect(self):
        """Si le Coder répond R.A.S, on n'envoie pas à l'Architecte."""
        evo = DivineEvolution()
        valid_spec = "Modifier core/router.py : ajouter un cache."

        call_count = 0

        async def mock_dispatch(target, payload):
            nonlocal call_count
            call_count += 1
            if target == "researcher":
                return {"result": "veille data", "status": "success"}
            if target == "coder":
                return {"result": "R.A.S — hors périmètre.", "status": "warning"}
            if target == "architect":
                return {"result": "validé", "status": "success"}
            return {"result": "", "status": "success"}

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(side_effect=mock_dispatch)

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value=valid_spec), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE]"})

        # L'Architect ne doit PAS avoir été appelé
        architect_calls = [c for c in mock_orch.dispatch_task.call_args_list if c[0][0] == "architect"]
        assert len(architect_calls) == 0
        assert "R.A.S" in result["result"]
