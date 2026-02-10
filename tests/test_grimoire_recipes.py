import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from core.summoner import Summoner
from core.router import RouterAgent


# ============================================================
# CHARGEMENT DES RECETTES VIA SUMMONER
# ============================================================

class TestRecipeLoading:

    def test_load_git_keeper(self):
        """git_keeper se charge via Summoner sans erreur."""
        summoner = Summoner("git_keeper")
        module = summoner.load()
        assert hasattr(module, "GitKeeper")

    def test_load_doc_writer(self):
        """doc_writer se charge via Summoner sans erreur."""
        summoner = Summoner("doc_writer")
        module = summoner.load()
        assert hasattr(module, "DocWriter")

    def test_load_data_analyst(self):
        """data_analyst se charge via Summoner sans erreur."""
        summoner = Summoner("data_analyst")
        module = summoner.load()
        assert hasattr(module, "DataAnalyst")

    def test_load_task_scheduler(self):
        """task_scheduler se charge via Summoner sans erreur."""
        summoner = Summoner("task_scheduler")
        module = summoner.load()
        assert hasattr(module, "TaskScheduler")

    def test_load_log_analyst(self):
        """log_analyst se charge via Summoner sans erreur."""
        summoner = Summoner("log_analyst")
        module = summoner.load()
        assert hasattr(module, "LogAnalyst")


# ============================================================
# ROUTAGE NIVEAU 1.5 (GRIMOIRE KEYWORDS)
# ============================================================

class TestRecipeRouting:

    def setup_method(self):
        """Reset le cache Grimoire avant chaque test."""
        RouterAgent._grimoire_index_cache = None

    @pytest.mark.asyncio
    async def test_route_git_keeper(self):
        """Mission avec mot-clé 'commit' -> git_keeper."""
        result = await RouterAgent.classify_intent("fais un commit propre")
        assert result == "git_keeper"

    @pytest.mark.asyncio
    async def test_route_doc_writer(self):
        """Mission avec mot-clé 'readme' -> doc_writer."""
        result = await RouterAgent.classify_intent("génère le readme du projet")
        assert result == "doc_writer"

    @pytest.mark.asyncio
    async def test_route_data_analyst(self):
        """Mission avec mot-clé 'csv' -> data_analyst."""
        result = await RouterAgent.classify_intent("analyse ce csv de ventes")
        assert result == "data_analyst"

    @pytest.mark.asyncio
    async def test_route_task_scheduler(self):
        """Mission avec mot-clé 'roadmap' -> task_scheduler."""
        result = await RouterAgent.classify_intent("crée une roadmap pour le sprint")
        assert result == "task_scheduler"

    @pytest.mark.asyncio
    async def test_route_log_analyst(self):
        """Mission avec mot-clé 'logs' -> log_analyst."""
        result = await RouterAgent.classify_intent("analyse les logs d'erreur")
        assert result == "log_analyst"


# ============================================================
# EXÉCUTION DES RECETTES (generate_content mocké)
# ============================================================

class TestRecipeExecution:

    @pytest.mark.asyncio
    async def test_execute_git_keeper(self):
        """git_keeper retourne success quand generate_content est mocké."""
        with patch("core.base_agent.ChromaMemoryManager", None):
            from core.grimoire.git_keeper import GitKeeper
            agent = GitKeeper()
            agent.generate_content = AsyncMock(return_value="git add . && git commit -m 'feat: init'")
            result = await agent.process_task({"mission": "fais un commit propre"})
            assert result["status"] == "success"
            assert "git add" in result["result"]

    @pytest.mark.asyncio
    async def test_execute_doc_writer(self):
        """doc_writer retourne success quand generate_content est mocké."""
        with patch("core.base_agent.ChromaMemoryManager", None):
            from core.grimoire.doc_writer import DocWriter
            agent = DocWriter()
            agent.generate_content = AsyncMock(return_value="# README\n\nProjet PROMÉTHÉE")
            result = await agent.process_task({"mission": "rédige le readme"})
            assert result["status"] == "success"
            assert "README" in result["result"]

    @pytest.mark.asyncio
    async def test_execute_data_analyst(self):
        """data_analyst retourne success quand generate_content est mocké."""
        with patch("core.base_agent.ChromaMemoryManager", None):
            from core.grimoire.data_analyst import DataAnalyst
            agent = DataAnalyst()
            agent.generate_content = AsyncMock(return_value="Moyenne: 42.5, Médiane: 40")
            result = await agent.process_task({"mission": "analyse ce csv"})
            assert result["status"] == "success"
            assert "Moyenne" in result["result"]

    @pytest.mark.asyncio
    async def test_execute_task_scheduler(self):
        """task_scheduler retourne success quand generate_content est mocké."""
        with patch("core.base_agent.ChromaMemoryManager", None):
            from core.grimoire.task_scheduler import TaskScheduler
            agent = TaskScheduler()
            agent.generate_content = AsyncMock(return_value="1. Analyse des besoins\n2. Développement")
            result = await agent.process_task({"mission": "planifie le sprint"})
            assert result["status"] == "success"
            assert "Analyse" in result["result"]

    @pytest.mark.asyncio
    async def test_execute_log_analyst(self):
        """log_analyst retourne success quand generate_content est mocké."""
        with patch("core.base_agent.ChromaMemoryManager", None):
            from core.grimoire.log_analyst import LogAnalyst
            agent = LogAnalyst()
            agent.generate_content = AsyncMock(return_value="Pattern détecté: TimeoutError récurrent à 03h00")
            result = await agent.process_task({"mission": "analyse les logs d'erreur"})
            assert result["status"] == "success"
            assert "Pattern" in result["result"]
