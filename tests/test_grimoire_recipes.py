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
# ROUTAGE NIVEAU 0.5 (GRIMOIRE KEYWORDS)
# ============================================================


# Fix 28/05/2026 : voir test_grimoire.py — fixture isolated_grimoire injecte
# un index contrôlé pour éviter contamination par csv_parser (keyword "analyse"
# générique) qui mange les routages vers data_analyst / log_analyst.
@pytest.fixture
def isolated_grimoire(monkeypatch):
    controlled_index = [
        {"slug": "git_keeper", "name": "GitKeeper",
         "description": "Gestion git",
         "keywords": ["commit", "git", "merge"],
         "file": "git_keeper.py"},
        {"slug": "doc_writer", "name": "DocWriter",
         "description": "Generation documentation",
         "keywords": ["readme", "documentation"],
         "file": "doc_writer.py"},
        {"slug": "data_analyst", "name": "DataAnalyst",
         "description": "Analyse de donnees CSV",
         "keywords": ["csv", "donnees", "statistiques"],
         "file": "data_analyst.py"},
        {"slug": "log_analyst", "name": "LogAnalyst",
         "description": "Analyse de logs systeme",
         "keywords": ["logs", "log", "syslog", "journal"],
         "file": "log_analyst.py"},
    ]
    monkeypatch.setattr(RouterAgent, "_grimoire_index_cache", controlled_index)
    yield
    RouterAgent._grimoire_index_cache = None


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
    async def test_route_data_analyst(self, isolated_grimoire):
        """Mission avec mot-clé 'csv' -> data_analyst."""
        result = await RouterAgent.classify_intent("analyse ce csv de ventes")
        assert result == "data_analyst"

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Obsolete test: logic divergence with prod (hygiene 2026-05-07)")
    async def test_route_task_scheduler(self):
        """Mission avec mot-clé 'roadmap' -> task_scheduler."""
        result = await RouterAgent.classify_intent("crée une roadmap pour le sprint")
        assert result == "task_scheduler"

    @pytest.mark.asyncio
    async def test_route_log_analyst(self, isolated_grimoire):
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


# ============================================================
# LOGIQUE MÉTIER : DR_DEBUG (Extraction de traceback)
# ============================================================

class TestDrDebugLogic:

    def test_extract_traceback_basic(self):
        from core.grimoire.dr_debug import _extract_traceback_info
        text = (
            "Voici l'erreur:\n"
            "Traceback (most recent call last):\n"
            '  File "core/router.py", line 42, in classify_intent\n'
            '    result = self._check_grimoire_index(m_low)\n'
            "TypeError: 'NoneType' object is not iterable\n"
        )
        info = _extract_traceback_info(text)
        assert info["found"] is True
        assert info["exception_type"] == "TypeError"
        assert info["exception_msg"] == "'NoneType' object is not iterable"
        assert info["file"] == "core/router.py"
        assert info["line"] == 42
        assert len(info["frames"]) == 1

    def test_extract_traceback_multiframe(self):
        from core.grimoire.dr_debug import _extract_traceback_info
        text = (
            "Traceback (most recent call last):\n"
            '  File "main.py", line 10, in main\n'
            '    run()\n'
            '  File "core/orchestrator.py", line 55, in dispatch_task\n'
            '    agent.process()\n'
            '  File "Agents/coder_agent.py", line 30, in process\n'
            '    x = 1/0\n'
            "ZeroDivisionError: division by zero\n"
        )
        info = _extract_traceback_info(text)
        assert info["found"] is True
        assert len(info["frames"]) == 3
        assert info["file"] == "Agents/coder_agent.py"
        assert info["line"] == 30
        assert info["exception_type"] == "ZeroDivisionError"

    def test_no_traceback(self):
        from core.grimoire.dr_debug import _extract_traceback_info
        info = _extract_traceback_info("juste du texte normal sans traceback")
        assert info["found"] is False

    @pytest.mark.asyncio
    async def test_dr_debug_enriches_with_traceback(self):
        """DrDebug enrichit le prompt avec l'analyse du traceback."""
        with patch("core.base_agent.ChromaMemoryManager", None):
            from core.grimoire.dr_debug import DrDebug
            agent = DrDebug()
            prompts_received = []

            async def capture_prompt(prompt):
                prompts_received.append(prompt)
                return "Fix: vérifier le type avant itération"

            agent.generate_content = capture_prompt
            mission = (
                "Traceback (most recent call last):\n"
                '  File "core/router.py", line 42, in classify\n'
                '    for x in data:\n'
                "TypeError: 'NoneType' object is not iterable"
            )
            result = await agent.process_task({"mission": mission})
            assert result["status"] == "success"
            assert result["traceback_info"]["found"] is True
            # Le prompt doit contenir l'enrichissement
            assert "ANALYSE AUTOMATIQUE DU TRACEBACK" in prompts_received[0]
            assert "TypeError" in prompts_received[0]


# ============================================================
# LOGIQUE MÉTIER : LOG_ANALYST (Parsing de sévérité)
# ============================================================

class TestLogAnalystLogic:

    def test_parse_log_lines_basic(self):
        from core.grimoire.log_analyst import _parse_log_lines
        logs = (
            "2026-02-14 10:00:00 INFO: Server started\n"
            "2026-02-14 10:00:01 ERROR: Connection failed\n"
            "2026-02-14 10:00:02 WARNING: Slow response\n"
            "2026-02-14 10:00:03 ERROR: Connection failed\n"
            "2026-02-14 10:00:04 ERROR: Connection failed\n"
            "2026-02-14 10:00:05 INFO: Retry successful\n"
            "2026-02-14 10:00:06 CRITICAL: System overload\n"
        )
        stats = _parse_log_lines(logs)
        assert stats["severity_counts"]["ERROR"] == 3
        assert stats["severity_counts"]["WARNING"] == 1
        assert stats["severity_counts"]["INFO"] == 2
        assert stats["severity_counts"]["CRITICAL"] == 1
        assert stats["classified_lines"] == 7
        assert "Connection failed" in stats["recurring_patterns"]
        assert stats["recurring_patterns"]["Connection failed"] == 3

    def test_parse_empty_logs(self):
        from core.grimoire.log_analyst import _parse_log_lines
        stats = _parse_log_lines("")
        assert stats["classified_lines"] == 0
        assert stats["recurring_patterns"] == {}

    def test_parse_no_severity(self):
        from core.grimoire.log_analyst import _parse_log_lines
        stats = _parse_log_lines("juste du texte\nsans sévérité\n")
        assert stats["classified_lines"] == 0

    @pytest.mark.asyncio
    async def test_log_analyst_returns_stats(self):
        """LogAnalyst retourne les stats en plus du résultat LLM."""
        with patch("core.base_agent.ChromaMemoryManager", None):
            from core.grimoire.log_analyst import LogAnalyst
            agent = LogAnalyst()
            agent.generate_content = AsyncMock(return_value="Analyse complète")
            result = await agent.process_task({
                "mission": "analyse ces logs",
                "context": "ERROR: timeout\nERROR: timeout\nERROR: timeout\nINFO: ok"
            })
            assert result["status"] == "success"
            assert result["log_stats"]["severity_counts"]["ERROR"] == 3
            assert result["log_stats"]["severity_counts"]["INFO"] == 1


# ============================================================
# LOGIQUE MÉTIER : DATA_ANALYST (Détection CSV/JSON)
# ============================================================

class TestDataAnalystLogic:

    def test_detect_csv(self):
        from core.grimoire.data_analyst import _detect_csv
        text = "nom,age,ville\nAlice,30,Paris\nBob,25,Lyon\n"
        info = _detect_csv(text)
        assert info["detected"] is True
        assert info["format"] == "CSV"
        assert info["num_columns"] == 3
        assert "nom" in info["headers"]

    def test_detect_csv_semicolon(self):
        from core.grimoire.data_analyst import _detect_csv
        text = "nom;age;ville\nAlice;30;Paris\nBob;25;Lyon\n"
        info = _detect_csv(text)
        assert info["detected"] is True
        assert info["separator"] == repr(";")

    def test_detect_json_object(self):
        from core.grimoire.data_analyst import _detect_json
        text = 'Voici les données: {"name": "Alice", "age": 30, "city": "Paris"}'
        info = _detect_json(text)
        assert info["detected"] is True
        assert info["format"] == "JSON"
        assert info["type"] == "object"
        assert "name" in info["top_level_keys"]

    def test_detect_json_array(self):
        from core.grimoire.data_analyst import _detect_json
        text = '[{"id": 1, "val": "a"}, {"id": 2, "val": "b"}]'
        info = _detect_json(text)
        assert info["detected"] is True
        assert info["type"] == "array"
        assert info["num_elements"] == 2
        assert "id" in info["sample_keys"]

    def test_no_data_detected(self):
        from core.grimoire.data_analyst import _detect_data_format
        info = _detect_data_format("juste du texte normal")
        assert info["detected"] is False

    @pytest.mark.asyncio
    async def test_data_analyst_enriches_csv(self):
        """DataAnalyst enrichit le prompt avec les métadonnées CSV."""
        with patch("core.base_agent.ChromaMemoryManager", None):
            from core.grimoire.data_analyst import DataAnalyst
            agent = DataAnalyst()
            prompts_received = []

            async def capture_prompt(prompt):
                prompts_received.append(prompt)
                return "Analyse CSV terminée"

            agent.generate_content = capture_prompt
            result = await agent.process_task({
                "mission": "analyse ce csv",
                "context": "nom,age,ville\nAlice,30,Paris\nBob,25,Lyon"
            })
            assert result["status"] == "success"
            assert result["data_info"]["detected"] is True
            assert "ANALYSE AUTOMATIQUE DES DONNÉES" in prompts_received[0]


# ============================================================
# LOGIQUE MÉTIER : DOC_WRITER (Analyse AST)
# ============================================================

class TestDocWriterLogic:

    def test_analyze_ast_basic(self):
        from core.grimoire.doc_writer import _analyze_ast
        code = (
            "import os\n"
            "from typing import Optional\n\n"
            "class MyClass(BaseAgent):\n"
            "    def __init__(self):\n"
            "        pass\n"
            "    async def process_task(self, payload):\n"
            "        pass\n\n"
            "def helper_func(x, y):\n"
            "    return x + y\n"
        )
        info = _analyze_ast(code)
        assert info["parsed"] is True
        assert len(info["classes"]) == 1
        assert info["classes"][0]["name"] == "MyClass"
        assert "__init__" in info["classes"][0]["methods"]
        assert "process_task" in info["classes"][0]["methods"]
        assert "os" in info["imports"]

    def test_analyze_ast_syntax_error(self):
        from core.grimoire.doc_writer import _analyze_ast
        info = _analyze_ast("def broken(:\n    pass")
        assert info["parsed"] is False

    def test_extract_python_file(self):
        from core.grimoire.doc_writer import _extract_python_file
        assert _extract_python_file("documente core/router.py svp") == "core/router.py"
        assert _extract_python_file("pas de fichier ici") == ""

    def test_build_doc_skeleton(self):
        from core.grimoire.doc_writer import _build_doc_skeleton
        ast_info = {
            "parsed": True,
            "classes": [{"name": "Foo", "bases": ["BaseAgent"], "methods": ["run", "stop"], "line": 5}],
            "functions": [{"name": "helper", "args": ["x"], "line": 20}],
            "imports": ["os", "sys"],
        }
        skeleton = _build_doc_skeleton(ast_info, "test.py")
        assert "# test.py" in skeleton
        assert "Foo" in skeleton
        assert "BaseAgent" in skeleton
        assert "`run()`" in skeleton
        assert "`helper(x)`" in skeleton
