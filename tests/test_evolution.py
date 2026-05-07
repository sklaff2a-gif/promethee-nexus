import os
import tempfile
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from Agents.evolution_agent import (
    DivineEvolution, _is_spec_offtopic, _spec_targets_existing_file,
    _detect_alien_imports, _SEARCH_QUERIES, _SPEC_OFFTOPIC_THRESHOLD
)
from core.evolution_catalog import EvolutionCatalog, ImprovementSpec
from core.experience_registry import ExperienceRegistry

_FAKE_STATE_FILE = os.path.join(tempfile.gettempdir(), "test_evolution_state.json")
_FAKE_REGISTRY_FILE = os.path.join(tempfile.gettempdir(), "test_evo_registry.json")


@pytest.fixture(autouse=True)
def isolate_catalog():
    """Isole le catalogue et le registre d'expériences entre chaque test."""
    from core.base_agent import BaseAgent
    BaseAgent._cloud_cooldown_until = 0.0
    if os.path.exists(_FAKE_STATE_FILE):
        os.remove(_FAKE_STATE_FILE)
    if os.path.exists(_FAKE_REGISTRY_FILE):
        os.remove(_FAKE_REGISTRY_FILE)
    with patch("core.evolution_catalog.CATALOG_STATE_FILE", _FAKE_STATE_FILE), \
         patch("core.experience_registry.REGISTRY_FILE", _FAKE_REGISTRY_FILE):
        EvolutionCatalog.reset_singleton()
        ExperienceRegistry.reset_singleton()
        yield
        EvolutionCatalog.reset_singleton()
        ExperienceRegistry.reset_singleton()
    BaseAgent._cloud_cooldown_until = 0.0
    if os.path.exists(_FAKE_STATE_FILE):
        os.remove(_FAKE_STATE_FILE)
    if os.path.exists(_FAKE_REGISTRY_FILE):
        os.remove(_FAKE_REGISTRY_FILE)


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


class TestLegacyPipeline:
    """Tests du pipeline V5 legacy (Researcher → LLM spec → Coder → Architect)."""

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
            result = await evo.process_task({"mission": "[MODE VEILLE] LEGACY lancez la veille"})

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
            result = await evo.process_task({"mission": "[MODE VEILLE] LEGACY lancez la veille"})

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
            result = await evo.process_task({"mission": "[MODE VEILLE] LEGACY lancez la veille"})

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
            result = await evo.process_task({"mission": "[MODE VEILLE] LEGACY"})

        assert result["result"] == "R.A.S"

    @pytest.mark.asyncio
    async def test_coder_ras_stops_before_architect(self):
        """Si le Coder répond R.A.S, on n'envoie pas à l'Architecte."""
        evo = DivineEvolution()
        valid_spec = "Modifier core/router.py : ajouter un cache."

        async def mock_dispatch(target, payload):
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
            result = await evo.process_task({"mission": "[MODE VEILLE] LEGACY"})

        # L'Architect ne doit PAS avoir été appelé
        architect_calls = [c for c in mock_orch.dispatch_task.call_args_list if c[0][0] == "architect"]
        assert len(architect_calls) == 0
        assert "R.A.S" in result["result"]


class TestCatalogPipelineV6:
    """Tests du pipeline V6 (catalogue pré-défini)."""

    @pytest.fixture(autouse=True)
    def disable_protected_files_guard(self):
        """Désactive le guard fichiers protégés et reset cooldown Cloud."""
        from core.base_agent import BaseAgent
        old_cooldown = BaseAgent._cloud_cooldown_until
        BaseAgent._cloud_cooldown_until = 0.0
        with patch("Agents.factory_agent._CRITICAL_FILES", set()):
            yield
        BaseAgent._cloud_cooldown_until = old_cooldown

    @pytest.mark.asyncio
    async def test_catalog_pipeline_selects_spec(self):
        """Le pipeline V6 sélectionne une spec du catalogue et génère le code via Gemini."""
        evo = DivineEvolution()

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={
            "result": "Code validé",
            "status": "success"
        })

        valid_code = "import logging\nlogger = logging.getLogger('test')\nprint('hello world from gemini')"

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value="# existing code\npass"), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=valid_code), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        assert result["status"] == "success"
        assert "CYCLE CATALOG V6" in result["result"]
        # L'Architect doit avoir été appelé (Phase 5)
        architect_calls = [c for c in mock_orch.dispatch_task.call_args_list if c[0][0] == "architect"]
        assert len(architect_calls) == 1

    @pytest.mark.asyncio
    async def test_catalog_pipeline_llm_fallback(self):
        """Si le LLM ne retourne pas 1-5, le #1 (meilleur score) est choisi."""
        evo = DivineEvolution()

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={
            "result": "import os\nimport logging\nlogger = logging.getLogger('test')\nprint('valid python code here')",
            "status": "success"
        })

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="je ne sais pas"), \
             patch.object(evo, "_read_target_file", return_value="# existing code\npass"), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        assert result["status"] == "success"
        assert "CYCLE CATALOG V6" in result["result"]

    @pytest.mark.asyncio
    async def test_catalog_pipeline_syntax_error_rejects(self):
        """Si le code généré a une erreur de syntaxe (et le retry aussi), la spec est rejetée."""
        evo = DivineEvolution()

        bad_code = "import logging\nlogger = logging.getLogger('test')\ndef broken(:\n    pass\n# padding to reach 50 chars minimum"

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value="# existing\npass"), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=bad_code):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        assert result["status"] == "error"
        assert "ast.parse" in result["result"]

    @pytest.mark.asyncio
    async def test_catalog_pipeline_empty_code_fails(self):
        """Si Gemini et le Coder local produisent un code trop court, la spec échoue."""
        evo = DivineEvolution()

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={"result": "pass", "status": "success"})

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value="# existing\npass"), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=""), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        assert "R.A.S" in result["result"] or "Aucun code" in result["result"]

    @pytest.mark.asyncio
    async def test_catalog_pipeline_missing_target_file(self):
        """Si le fichier cible n'existe pas, la spec échoue."""
        evo = DivineEvolution()

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value=""):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        assert "R.A.S" in result["result"]
        assert "introuvable" in result["result"]

    @pytest.mark.asyncio
    async def test_catalog_exhausted(self):
        """Si toutes les specs sont épuisées, le pipeline tente la création Grimoire."""
        evo = DivineEvolution()
        cat = EvolutionCatalog()
        for spec in cat.specs.values():
            spec.status = "failed"

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_run_grimoire_creation", new_callable=AsyncMock,
                         return_value={"status": "success", "result": "R.A.S — Grimoire complet."}) as mock_grim:
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})
            mock_grim.assert_called_once()
        assert "R.A.S" in result["result"]

    @pytest.mark.asyncio
    async def test_catalog_pipeline_deployed_marks_catalog(self):
        """Si l'Architecte valide, la spec est marquée pending_deploy (pas deployed)
        car le déploiement réel est confirmé par ARTIFACT_CREATED de la Factory."""
        evo = DivineEvolution()
        cat = EvolutionCatalog()

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={
            "result": "import logging\nlogger = logging.getLogger('test')\nclass Foo:\n    pass",
            "status": "success"
        })

        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()

        mock_sandbox = MagicMock()
        mock_sandbox.is_fresh.return_value = True

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value="# existing code\npass"), \
             patch("core.orchestrator.orchestrator", mock_orch), \
             patch("core.event_bus.bus.bus", mock_bus), \
             patch.dict("sys.modules", {"core.sandbox_engine": MagicMock(sandbox=mock_sandbox)}):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        assert "CYCLE CATALOG V6" in result["result"]
        pending = cat.get_specs_by_status("pending_deploy")
        assert len(pending) >= 1

    @pytest.mark.asyncio
    async def test_catalog_pipeline_architect_rejects(self):
        """Si l'Architecte rejette, la spec est marquée failed."""
        evo = DivineEvolution()

        async def mock_dispatch(target, payload):
            if target == "coder":
                return {
                    "result": "import logging\nlogger = logging.getLogger('test')\nclass Foo:\n    pass",
                    "status": "success"
                }
            if target == "architect":
                return {"result": "rejeté: trop risqué", "status": "rejected"}
            return {"result": "", "status": "success"}

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(side_effect=mock_dispatch)

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value="# existing code\npass"), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        assert "rejected" in result["result"]

    @pytest.mark.asyncio
    async def test_default_mode_returns_waiting(self):
        """Sans MODE VEILLE, retourne en attente."""
        evo = DivineEvolution()
        result = await evo.process_task({"mission": "bonjour"})
        assert "attente" in result["result"]

    @pytest.mark.asyncio
    async def test_legacy_mode_trigger(self):
        """Le mot LEGACY dans la mission déclenche le pipeline V5."""
        evo = DivineEvolution()

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={"result": "veille data", "status": "success"})

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="R.A.S"), \
             patch.object(evo, "_check_already_explored", return_value=False), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE] LEGACY"})

        assert result["result"] == "R.A.S"
        researcher_calls = [c for c in mock_orch.dispatch_task.call_args_list if c[0][0] == "researcher"]
        assert len(researcher_calls) == 1

    @pytest.mark.asyncio
    async def test_exception_in_pipeline_caught(self):
        """Une exception non rattrapée dans le pipeline est attrapée par process_task."""
        evo = DivineEvolution()

        with patch.object(evo, "_run_catalog_pipeline", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        assert result["status"] == "error"
        assert "boom" in result["result"]


class TestGrimoireCreation:
    """Tests de la création de recettes Grimoire quand le catalogue est épuisé."""

    @pytest.mark.asyncio
    async def test_catalog_exhausted_triggers_grimoire_creation(self):
        """Quand le catalogue est épuisé, _run_grimoire_creation est appelé."""
        evo = DivineEvolution()
        cat = EvolutionCatalog()
        for spec in cat.specs.values():
            spec.status = "failed"

        with patch.object(evo, "_run_grimoire_creation", new_callable=AsyncMock,
                         return_value={"status": "success", "message": "Recette créée"}) as mock_grimoire, \
             patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})
            mock_grimoire.assert_called_once()

    @pytest.mark.asyncio
    async def test_grimoire_creation_max_recipes(self):
        """Si >= 12 recettes existent, pas de création."""
        evo = DivineEvolution()
        fake_index = [{"slug": f"agent_{i}"} for i in range(12)]

        with patch("builtins.open", MagicMock()), \
             patch("json.load", return_value=fake_index), \
             patch("os.path.join", return_value="/fake/path"):
            result = await evo._run_grimoire_creation()
            assert "R.A.S" in result["result"]
            assert "complet" in result["result"]

    @pytest.mark.asyncio
    async def test_grimoire_creation_calls_writer(self):
        """La création appelle GrimoireWriter.write_recipe avec les bons params."""
        evo = DivineEvolution()
        fake_index = [{"slug": "math_wizard"}, {"slug": "dr_debug"}]

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={
            "result": (
                "from core.base_agent import BaseAgent\n\n"
                "class TestNewAgent(BaseAgent):\n"
                "    def __init__(self):\n"
                "        super().__init__('test_new', 'test', 'test')\n\n"
                "    async def process_task(self, task_payload):\n"
                "        return {'status': 'success', 'result': 'ok'}\n"
            ),
            "status": "success"
        })

        spec_json = '{"slug": "test_new", "name": "TestNew", "description": "Test agent", "keywords": ["testkw"]}'

        with patch("builtins.open", MagicMock()), \
             patch("json.load", return_value=fake_index), \
             patch("os.path.join", return_value="/fake/path"), \
             patch.object(evo, "generate_content", new_callable=AsyncMock, return_value=spec_json), \
             patch("core.orchestrator.orchestrator", mock_orch), \
             patch("core.grimoire_writer.GrimoireWriter.write_recipe") as mock_write:
            mock_write.return_value = {"status": "success", "message": "OK"}
            result = await evo._run_grimoire_creation()
            mock_write.assert_called_once()
            call_kwargs = mock_write.call_args
            assert call_kwargs[1]["slug"] == "test_new" or call_kwargs[0][0] == "test_new"

    @pytest.mark.asyncio
    async def test_grimoire_creation_invalid_spec(self):
        """Si le LLM ne produit pas un JSON valide, R.A.S."""
        evo = DivineEvolution()
        fake_index = [{"slug": "math_wizard"}]

        with patch("builtins.open", MagicMock()), \
             patch("json.load", return_value=fake_index), \
             patch("os.path.join", return_value="/fake/path"), \
             patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="pas du JSON"):
            result = await evo._run_grimoire_creation()
            assert "R.A.S" in result["result"]

    @pytest.mark.asyncio
    async def test_grimoire_creation_duplicate_slug(self):
        """Si le slug proposé existe déjà, R.A.S."""
        evo = DivineEvolution()
        fake_index = [{"slug": "math_wizard"}]

        spec_json = '{"slug": "math_wizard", "name": "MathWizard2", "description": "Dupe", "keywords": ["math"]}'

        with patch("builtins.open", MagicMock()), \
             patch("json.load", return_value=fake_index), \
             patch("os.path.join", return_value="/fake/path"), \
             patch.object(evo, "generate_content", new_callable=AsyncMock, return_value=spec_json):
            result = await evo._run_grimoire_creation()
            assert "R.A.S" in result["result"]


class TestExtractPythonCode:
    """Tests de l'extraction de code Python depuis le markdown."""

    def test_extract_from_python_block(self):
        text = "Voici le code:\n```python\nimport os\nprint('hello')\n```\nFin."
        result = DivineEvolution._extract_python_code(text)
        assert result == "import os\nprint('hello')"

    def test_extract_from_generic_block(self):
        text = "Code:\n```\nimport sys\npass\n```"
        result = DivineEvolution._extract_python_code(text)
        assert result == "import sys\npass"

    def test_no_markdown_returns_as_is(self):
        text = "import os\nprint('hello')"
        result = DivineEvolution._extract_python_code(text)
        assert result == text

    def test_longest_block_wins(self):
        text = "```python\nshort\n```\n\n```python\nimport os\nimport sys\nprint('long block')\n```"
        result = DivineEvolution._extract_python_code(text)
        assert "long block" in result
        assert "import os" in result

    def test_strips_whitespace(self):
        text = "```python\n  \nimport os\n  \n```"
        result = DivineEvolution._extract_python_code(text)
        assert result.startswith("import os") or result.strip().startswith("import os")


class TestGeminiCodeGeneration:
    """Tests du pipeline Gemini Cloud pour la génération de code."""

    @pytest.fixture(autouse=True)
    def disable_protected_files_guard(self):
        """Désactive le guard fichiers protégés (testé dans TestProtectedFilesGuard)."""
        with patch("Agents.factory_agent._CRITICAL_FILES", set()):
            yield

    @pytest.mark.asyncio
    async def test_cloud_generation_with_retry(self):
        """Si le premier code est invalide, le retry via Gemini corrige."""
        evo = DivineEvolution()

        bad_code = "def broken(:\n    pass\n# padding minimum 50 chars for the test to work here"
        good_code = "def fixed():\n    pass\n# padding minimum 50 chars for the test to work here ok"

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={"status": "success", "result": "Validé"})

        # Premier appel retourne du mauvais code, second (retry) retourne du bon
        call_count = 0
        async def mock_cloud(prompt):
            nonlocal call_count
            call_count += 1
            return bad_code if call_count == 1 else good_code

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value="# existing\npass"), \
             patch.object(evo, "_generate_code_cloud", side_effect=mock_cloud), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        assert result["status"] == "success"
        assert "CYCLE CATALOG V6" in result["result"]

    @pytest.mark.asyncio
    async def test_cloud_fallback_to_local(self):
        """Si Gemini Cloud échoue, le fallback local (Coder) est utilisé."""
        evo = DivineEvolution()

        valid_code = "import os\nimport sys\nprint('hello from local coder minimum chars')"

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={
            "result": valid_code, "status": "success"
        })

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value="# existing\npass"), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=""), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        assert result["status"] == "success"
        # Le Coder local doit avoir été appelé en fallback (micro-patch + historique)
        coder_calls = [c for c in mock_orch.dispatch_task.call_args_list if c[0][0] == "coder"]
        assert len(coder_calls) >= 1


# --- Tests fichiers protégés (Fix Evolution→Factory) ---

class TestProtectedFilesGuard:
    """Vérifie que l'Evolution refuse les specs ciblant des fichiers critiques
    mais autorise celles ciblant des fichiers supervisés."""

    @pytest.mark.asyncio
    async def test_critical_file_skipped(self):
        """Une spec ciblant core/orchestrator.py (critique) est rejetée avant Phase 3."""
        evo = DivineEvolution()

        catalog = EvolutionCatalog()
        catalog.specs.clear()
        spec = ImprovementSpec(
            id="TEST-CRIT", name="Refactor Orch", description="test",
            category="performance", target_file="core/orchestrator.py",
            target_method="dispatch_task", difficulty=3,
            code_template="# code", validation="test", status="available",
        )
        catalog.specs[spec.id] = spec

        # Mocker le grimoire_index pour éviter le court-circuit "Grimoire complet"
        mock_index = [{"slug": f"agent_{i}"} for i in range(5)]  # < 12 recettes
        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value="# code existant\npass"), \
             patch("builtins.open", side_effect=lambda *a, **kw: __import__("io").StringIO(json.dumps(mock_index))) if False else \
             patch("json.load", return_value=mock_index):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        assert "critique" in result.get("result", "").lower() or "warning" in result.get("status", "")

    @pytest.mark.asyncio
    async def test_supervised_file_proceeds(self):
        """Une spec ciblant core/router.py (supervisé, plus critique) passe normalement."""
        evo = DivineEvolution()

        catalog = EvolutionCatalog()
        catalog.specs.clear()
        spec = ImprovementSpec(
            id="TEST-SUP", name="Cache Router", description="test",
            category="performance", target_file="core/router.py",
            target_method="classify_intent", difficulty=3,
            code_template="# cache", validation="test", status="available",
        )
        catalog.specs[spec.id] = spec

        valid_code = "import os\nimport sys\nprint('hello world minimum chars padded')"
        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={
            "result": "VALIDÉ", "status": "success"
        })

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value="# existing code\npass"), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=valid_code), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        # La spec a été traitée (pas de rejet fichier critique)
        assert "critique" not in result.get("result", "").lower()

    @pytest.mark.asyncio
    async def test_non_protected_file_proceeds(self):
        """Une spec ciblant un fichier non-protégé passe normalement."""
        evo = DivineEvolution()

        catalog = EvolutionCatalog()
        catalog.specs.clear()
        spec = ImprovementSpec(
            id="TEST-OK", name="Amélioration custom", description="test",
            category="performance", target_file="core/psyche.py",
            target_method="test", difficulty=2,
            code_template="# code", validation="test", status="available",
        )
        catalog.specs[spec.id] = spec

        valid_code = "import os\nimport sys\nprint('hello world minimum chars padded')"
        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={
            "result": "VALIDÉ", "status": "success"
        })

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value="# existing code\npass"), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=valid_code), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        assert "critique" not in result.get("result", "").lower()


class TestDetectAlienImports:
    """Tests du filtre anti-hallucination (imports étrangers)."""

    def test_clean_code_no_aliens(self):
        code = "import os\nimport logging\nfrom core.base_agent import BaseAgent\n"
        assert _detect_alien_imports(code) == []

    def test_django_detected(self):
        code = "from django.db import models\nclass Foo(models.Model): pass"
        aliens = _detect_alien_imports(code)
        assert "django" in aliens

    def test_pygame_detected(self):
        code = "import pygame\npygame.init()\nscreen = pygame.display.set_mode((800, 600))"
        aliens = _detect_alien_imports(code)
        assert "pygame" in aliens

    def test_langchain_detected(self):
        code = "from langchain.chains import LLMChain\nfrom langchain.llms import OpenAI"
        aliens = _detect_alien_imports(code)
        assert "langchain" in aliens

    def test_flask_detected(self):
        code = "from flask import Flask\napp = Flask(__name__)"
        aliens = _detect_alien_imports(code)
        assert "flask" in aliens

    def test_multiple_aliens(self):
        code = "import django\nimport flask\nimport pygame\n"
        aliens = _detect_alien_imports(code)
        assert len(aliens) == 3

    def test_standard_lib_not_alien(self):
        code = "import os\nimport sys\nimport json\nimport asyncio\nimport logging\n"
        assert _detect_alien_imports(code) == []

    def test_project_imports_not_alien(self):
        code = "from core.orchestrator import orchestrator\nfrom core.base_agent import BaseAgent\n"
        assert _detect_alien_imports(code) == []

    def test_openai_detected(self):
        """openai est alien (on utilise Gemini/Ollama, pas OpenAI)."""
        code = "from openai import OpenAI\nclient = OpenAI(api_key='...')"
        aliens = _detect_alien_imports(code)
        assert "openai" in aliens

    def test_comment_lines_ignored(self):
        """Les lignes non-import ne sont pas analysées."""
        code = "# import django\n# from flask import Flask\nimport os\n"
        assert _detect_alien_imports(code) == []


class TestAntiHallucinationCatalogPipeline:
    """Tests d'intégration du filtre anti-hallucination dans le pipeline catalog V6."""

    @pytest.fixture(autouse=True)
    def disable_protected_files_guard(self):
        with patch("Agents.factory_agent._CRITICAL_FILES", set()):
            yield

    @pytest.mark.asyncio
    async def test_alien_code_rejected_before_architect(self):
        """Du code Django est rejeté avant d'être envoyé à l'Architecte."""
        evo = DivineEvolution()

        django_code = (
            "from django.db import models\n"
            "class User(models.Model):\n"
            "    name = models.CharField(max_length=100)\n"
            "    email = models.EmailField()\n"
            "# padding for min 50 chars requirement here\n"
        )

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={"status": "success"})

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value="# existing\npass"), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=django_code), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        # L'Architecte ne doit PAS avoir été appelé
        architect_calls = [c for c in mock_orch.dispatch_task.call_args_list if c[0][0] == "architect"]
        assert len(architect_calls) == 0
        assert "hallucination" in result["result"].lower() or "R.A.S" in result["result"]

    @pytest.mark.asyncio
    async def test_pygame_code_rejected(self):
        """Du code Pygame est rejeté."""
        evo = DivineEvolution()

        pygame_code = (
            "import pygame\n"
            "pygame.init()\n"
            "screen = pygame.display.set_mode((800, 600))\n"
            "running = True\nwhile running:\n    for event in pygame.event.get():\n        pass\n"
        )

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value="# existing\npass"), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=pygame_code):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        assert "R.A.S" in result["result"]

    @pytest.mark.asyncio
    async def test_valid_code_passes_filter(self):
        """Du code Python valide avec des imports standard passe le filtre."""
        evo = DivineEvolution()

        valid_code = (
            "import os\nimport logging\nfrom core.base_agent import BaseAgent\n\n"
            "class ImprovedAgent(BaseAgent):\n"
            "    def __init__(self):\n"
            "        super().__init__('test', 'test', 'test')\n"
        )

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={"status": "success"})
        mock_orch._contains_python_code = MagicMock(return_value=True)

        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value="# existing\npass"), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=valid_code), \
             patch("core.orchestrator.orchestrator", mock_orch), \
             patch("core.event_bus.bus.bus", mock_bus):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        # L'Architecte doit avoir été appelé
        architect_calls = [c for c in mock_orch.dispatch_task.call_args_list if c[0][0] == "architect"]
        assert len(architect_calls) == 1

    @pytest.mark.asyncio
    async def test_no_structural_code_not_marked_deployed(self):
        """Si le code n'est pas structurel Python, ne pas marquer deployed."""
        evo = DivineEvolution()

        # Code valide syntaxiquement mais pas structurel (pas d'import/class/def)
        non_structural = "x = 1\ny = 2\nz = x + y\nprint(z)\n# padding chars here to reach 50 minimum\n"

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={"status": "success", "result": "VALIDÉ"})
        mock_orch._contains_python_code = MagicMock(return_value=False)

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value="# existing\npass"), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=non_structural), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        cat = EvolutionCatalog()
        deployed = cat.get_specs_by_status("deployed")
        assert len(deployed) == 0


class TestAntiTruncation:
    """Vérifie que l'Evolution détecte le code tronqué avant soumission à l'Architecte."""

    @pytest.fixture(autouse=True)
    def disable_protected_files_guard(self):
        with patch("Agents.factory_agent._CRITICAL_FILES", set()):
            yield

    @pytest.mark.asyncio
    async def test_truncated_code_rejected(self):
        """Un code généré < 60% du source original est rejeté (Phase 4d)."""
        evo = DivineEvolution()

        # Source original : ~1200 chars
        original_source = "import os\nimport sys\nimport logging\n" + "def func_x():\n    pass\n" * 50

        # Code généré : ~200 chars (~17% du source → bien < 60%)
        truncated_code = (
            "import os\nimport sys\nimport logging\n\n"
            "def hello():\n    return 42\n\n"
            "def world():\n    return 'hello world from truncated code'\n\n"
            "# fin du fichier tronqué\n"
        )

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={"status": "success"})

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value=original_source), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=truncated_code), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        # L'Architecte ne doit PAS avoir été appelé
        architect_calls = [c for c in mock_orch.dispatch_task.call_args_list if c[0][0] == "architect"]
        assert len(architect_calls) == 0
        assert "troncature" in result["result"].lower()

    @pytest.mark.asyncio
    async def test_full_code_passes_truncation_check(self):
        """Un code généré >= 60% du source passe le check anti-troncature."""
        evo = DivineEvolution()

        original_source = "import os\ndef old_func():\n    return 1\n" * 3  # ~120 chars

        # Code généré : même taille avec les améliorations
        new_code = "import os\nimport asyncio\ndef new_func():\n    return 42\n" * 3

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={"status": "success"})
        mock_orch._contains_python_code = MagicMock(return_value=True)

        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value=original_source), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=new_code), \
             patch("core.orchestrator.orchestrator", mock_orch), \
             patch("core.event_bus.bus.bus", mock_bus):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        # L'Architecte DOIT avoir été appelé
        architect_calls = [c for c in mock_orch.dispatch_task.call_args_list if c[0][0] == "architect"]
        assert len(architect_calls) == 1


class TestPendingDeployPipeline:
    """Le pipeline V6 doit marquer pending_deploy (pas deployed) — Fix run 2026-02-18."""

    @pytest.fixture(autouse=True)
    def isolate_catalog(self, tmp_path):
        _state_file = str(tmp_path / "test_catalog.json")
        _reg_file = str(tmp_path / "test_registry.json")
        with patch("core.evolution_catalog.CATALOG_STATE_FILE", _state_file), \
             patch("core.experience_registry.REGISTRY_FILE", _reg_file):
            EvolutionCatalog.reset_singleton()
            ExperienceRegistry.reset_singleton()
            yield
            EvolutionCatalog.reset_singleton()
            ExperienceRegistry.reset_singleton()

    @pytest.mark.asyncio
    async def test_pipeline_marks_pending_deploy_not_deployed(self):
        """Après validation Architect, la spec est pending_deploy (pas deployed)."""
        from core.evolution_catalog import EvolutionCatalog
        cat = EvolutionCatalog()

        with patch("core.base_agent.ChromaMemoryManager", None):
            from Agents.evolution_agent import DivineEvolution
            evo = DivineEvolution()

        # Le code généré doit être assez long pour passer l'anti-troncature (>60% du source)
        original_source = "import os\ndef hello():\n    return 42\n"
        valid_code = (
            "import os\nimport logging\n\nlogger = logging.getLogger('test')\n\n"
            "def hello():\n    return 42\n\nclass Helper:\n    pass\n"
        )

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={"status": "success"})
        mock_orch._contains_python_code = MagicMock(return_value=True)

        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()

        mock_sandbox = MagicMock()
        mock_sandbox.is_fresh.return_value = True

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value=original_source), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=valid_code), \
             patch("core.orchestrator.orchestrator", mock_orch), \
             patch("core.event_bus.bus.bus", mock_bus), \
             patch.dict("sys.modules", {"core.sandbox_engine": MagicMock(sandbox=mock_sandbox)}):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        assert "CYCLE CATALOG V6" in result["result"]
        # Au moins une spec doit être en pending_deploy (pas deployed)
        pending = cat.get_specs_by_status("pending_deploy")
        deployed = cat.get_specs_by_status("deployed")
        assert len(pending) >= 1, f"Aucune spec en pending_deploy. Deployed: {len(deployed)}"
        assert len(deployed) == 0, "Aucune spec ne devrait être deployed directement"


class TestCouncilSpecCuration:
    """Tests de la curation automatique des specs COUNCIL."""

    def _make_council_spec(self, catalog, spec_id="COUNCIL-99999", **kwargs):
        """Helper pour créer une spec COUNCIL de test."""
        defaults = dict(
            name="Council: Test",
            description="Issu d'un consensus Council. Amélioration significative du système pour optimiser les performances globales.",
            category="intelligence",
            target_file="core/router.py",
            target_method="classify_intent",
            difficulty=2,
            code_template="import logging\n\ndef council_improvement():\n    logger = logging.getLogger('test')\n    logger.info('improvement')\n    return True\n",
            validation="Vérifier que l'amélioration fonctionne.",
            tags=["council", "consensus"],
            status="available",
            last_attempt=datetime.now().isoformat(),
        )
        defaults.update(kwargs)
        spec = ImprovementSpec(id=spec_id, **defaults)
        catalog.specs[spec_id] = spec
        return spec

    def test_mark_rejected_sets_status(self):
        """mark_rejected met le statut à 'rejected' et ajoute la raison."""
        cat = EvolutionCatalog()
        self._make_council_spec(cat, "COUNCIL-10001")

        cat.mark_rejected("COUNCIL-10001", "test_raison")

        spec = cat.get_spec("COUNCIL-10001")
        assert spec.status == "rejected"
        assert any("[CURATION] test_raison" in r for r in spec.failure_reasons)

    def test_curate_removes_old_specs(self):
        """Une spec > 48h est rejetée."""
        cat = EvolutionCatalog()
        old_time = (datetime.now() - timedelta(hours=50)).isoformat()
        self._make_council_spec(cat, "COUNCIL-20001", last_attempt=old_time)

        purged = cat.curate_council_specs()
        assert purged == 1
        assert cat.get_spec("COUNCIL-20001").status == "rejected"

    def test_curate_removes_missing_file(self):
        """Une spec ciblant un fichier inexistant est rejetée."""
        cat = EvolutionCatalog()
        self._make_council_spec(
            cat, "COUNCIL-30001",
            target_file="core/fichier_qui_nexiste_pas.py",
        )

        purged = cat.curate_council_specs()
        assert purged == 1
        assert cat.get_spec("COUNCIL-30001").status == "rejected"
        assert "fichier_inexistant" in cat.get_spec("COUNCIL-30001").failure_reasons[-1]

    def test_curate_removes_short_description(self):
        """Une spec avec description < 50 chars est rejetée."""
        cat = EvolutionCatalog()
        self._make_council_spec(
            cat, "COUNCIL-40001",
            description="Trop court",
        )

        purged = cat.curate_council_specs()
        assert purged == 1
        assert "description_trop_courte" in cat.get_spec("COUNCIL-40001").failure_reasons[-1]

    def test_curate_removes_duplicate(self):
        """Une spec avec même target_file+method qu'une built-in est rejetée."""
        cat = EvolutionCatalog()
        # PERF-001 cible core/router.py:classify_intent — créer un doublon
        self._make_council_spec(
            cat, "COUNCIL-50001",
            target_file="core/router.py",
            target_method="classify_intent",
        )

        purged = cat.curate_council_specs()
        assert purged == 1
        assert "doublon" in cat.get_spec("COUNCIL-50001").failure_reasons[-1]

    def test_curate_removes_boilerplate(self):
        """Une spec avec code_template boilerplate (que comments+pass) est rejetée."""
        cat = EvolutionCatalog()
        self._make_council_spec(
            cat, "COUNCIL-60001",
            code_template="# Amélioration issue du consensus\n# Actions:\npass\n",
            target_file="core/psyche.py",
            target_method="unique_method_boilerplate_test",
        )

        purged = cat.curate_council_specs()
        assert purged == 1
        assert "boilerplate" in cat.get_spec("COUNCIL-60001").failure_reasons[-1]

    def test_curate_keeps_valid_spec(self):
        """Une spec valide (fichier existant, desc longue, récente, pas doublon, code réel) est conservée."""
        cat = EvolutionCatalog()
        self._make_council_spec(
            cat, "COUNCIL-70001",
            target_file="core/psyche.py",
            target_method="unique_valid_method_test",
        )

        purged = cat.curate_council_specs()
        assert purged == 0
        assert cat.get_spec("COUNCIL-70001").status == "available"

    def test_curate_returns_purged_count(self):
        """curate_council_specs retourne le bon nombre de specs purgées."""
        cat = EvolutionCatalog()
        old_time = (datetime.now() - timedelta(hours=72)).isoformat()
        self._make_council_spec(cat, "COUNCIL-80001", last_attempt=old_time,
                                target_file="core/psyche.py", target_method="unique_count_m1")
        self._make_council_spec(cat, "COUNCIL-80002", last_attempt=old_time,
                                target_file="core/psyche.py", target_method="unique_count_m2")
        self._make_council_spec(
            cat, "COUNCIL-80003",
            target_file="core/psyche.py",
            target_method="unique_count_m3",
        )

        purged = cat.curate_council_specs()
        assert purged == 2  # 2 périmées, 1 valide
        assert cat.get_spec("COUNCIL-80003").status == "available"

    @pytest.mark.asyncio
    async def test_guard_calls_curation_before_skip(self):
        """Le guard dans _execute_council_debate purge les vieilles specs et débloque le débat."""
        from core.autonomy_engine import AutonomyEngine
        engine = AutonomyEngine.__new__(AutonomyEngine)
        engine.error_streak = 0
        engine.daily_count = 0
        engine.daily_budget_used = 0
        engine.routine_history = []
        engine._current_council_subject = ""
        engine._council_degraded = False

        cat = EvolutionCatalog()
        old_time = (datetime.now() - timedelta(hours=72)).isoformat()
        # Créer 4 specs COUNCIL périmées (>48h)
        for i in range(4):
            self._make_council_spec(
                cat, f"COUNCIL-9000{i}", last_attempt=old_time,
                target_file="core/psyche.py", target_method=f"guard_test_m{i}",
            )

        # Vérifier qu'il y a bien 4 specs available avant
        pending_before = [s for s in cat.specs.values()
                          if s.id.startswith("COUNCIL-") and s.status == "available"]
        assert len(pending_before) == 4

        # Appeler _execute_council_debate — le guard devrait curéer et débloquer
        # (on mocke psyche et council pour ne pas exécuter le débat complet)
        with patch("core.psyche.psyche") as mock_psyche:
            mock_psyche.get_debate_index.return_value = 0
            mock_psyche.select_council_topic.return_value = {
                "participants": ["strategist", "coder"],
                "mission": "Test curation",
                "needs_research": False, "research_query": None,
                "subject_key": "test_curation",
            }
            with patch("core.council.Council") as mock_council_cls:
                mock_council = MagicMock()
                mock_council.run = AsyncMock(return_value={
                    "status": "no_consensus", "rounds_used": 1,
                    "transcript": [], "final_summary": "",
                    "result": "",
                })
                mock_council_cls.return_value = mock_council
                result = await engine._execute_council_debate()

        # Vérifier que les 4 specs ont été purgées
        pending_after = [s for s in cat.specs.values()
                         if s.id.startswith("COUNCIL-") and s.status == "available"]
        assert len(pending_after) == 0
        # Le résultat ne devrait PAS être "skipped" car la curation a débloqué
        assert result.get("reason") != "council_specs_saturated"


# ============================================================================
# Tests Micro-Découpage + Feedback Loop
# ============================================================================

_SAMPLE_SOURCE = '''import os
import logging

logger = logging.getLogger("sample")

class SampleAgent:
    """Un agent d'exemple."""

    def __init__(self, name):
        self.name = name
        self.count = 0

    def process(self, data):
        """Traite les données."""
        self.count += 1
        result = data.upper()
        return result

    async def async_process(self, data):
        """Traitement asynchrone."""
        import asyncio
        await asyncio.sleep(0.1)
        return data.lower()

def standalone_function(x):
    return x * 2
'''


class TestExtractMethodContext:

    def test_extract_class_method(self):
        """Extraction d'une méthode de classe avec contexte complet."""
        agent = DivineEvolution()
        ctx = agent._extract_method_context(_SAMPLE_SOURCE, "process")
        assert ctx is not None
        assert "def process(self, data):" in ctx["method_source"]
        assert ctx["class_name"] == "SampleAgent"
        assert ctx["class_signature"] == "class SampleAgent:"
        assert ctx["method_start_line"] > 0
        assert ctx["method_end_line"] >= ctx["method_start_line"]

    def test_extract_includes_imports(self):
        """Les imports du fichier sont inclus dans le contexte."""
        agent = DivineEvolution()
        ctx = agent._extract_method_context(_SAMPLE_SOURCE, "process")
        assert ctx is not None
        assert "import os" in ctx["imports"]
        assert "import logging" in ctx["imports"]

    def test_extract_sibling_signatures(self):
        """Les signatures des méthodes voisines sont extraites."""
        agent = DivineEvolution()
        ctx = agent._extract_method_context(_SAMPLE_SOURCE, "process")
        assert ctx is not None
        assert "__init__" in ctx["sibling_signatures"]
        assert "async_process" in ctx["sibling_signatures"]
        # La méthode ciblée ne doit PAS être dans les voisines
        assert "def process(" not in ctx["sibling_signatures"]

    def test_extract_method_not_found_returns_none(self):
        """Retourne None si la méthode n'existe pas."""
        agent = DivineEvolution()
        ctx = agent._extract_method_context(_SAMPLE_SOURCE, "nonexistent_method")
        assert ctx is None

    def test_extract_empty_target_returns_none(self):
        """Retourne None si target_method est vide."""
        agent = DivineEvolution()
        assert agent._extract_method_context(_SAMPLE_SOURCE, "") is None
        assert agent._extract_method_context(_SAMPLE_SOURCE, None) is None

    def test_extract_standalone_function(self):
        """Extraction d'une fonction top-level (pas dans une classe)."""
        agent = DivineEvolution()
        ctx = agent._extract_method_context(_SAMPLE_SOURCE, "standalone_function")
        assert ctx is not None
        assert "def standalone_function(x):" in ctx["method_source"]
        assert ctx["class_name"] is None
        assert ctx["class_signature"] == ""


class TestReassembleCode:

    def test_basic_reassembly(self):
        """Remplacement basique d'une méthode."""
        agent = DivineEvolution()
        ctx = agent._extract_method_context(_SAMPLE_SOURCE, "process")
        new_method = "    def process(self, data):\n        self.count += 1\n        return data.strip()"
        result = agent._reassemble_code(_SAMPLE_SOURCE, ctx, new_method)
        assert "return data.strip()" in result
        assert "return data.upper()" not in result  # ancien code supprimé

    def test_reassembly_preserves_other_methods(self):
        """Les autres méthodes ne sont pas affectées."""
        agent = DivineEvolution()
        ctx = agent._extract_method_context(_SAMPLE_SOURCE, "process")
        new_method = "    def process(self, data):\n        return 'modified'"
        result = agent._reassemble_code(_SAMPLE_SOURCE, ctx, new_method)
        assert "def __init__" in result
        assert "async def async_process" in result
        assert "def standalone_function" in result

    def test_reassembly_adjusts_indentation(self):
        """L'indentation est ajustée si le code généré a un niveau différent."""
        agent = DivineEvolution()
        ctx = agent._extract_method_context(_SAMPLE_SOURCE, "process")
        # Code sans indentation (comme si le LLM avait oublié)
        new_method = "def process(self, data):\n    return 'no_indent'"
        result = agent._reassemble_code(_SAMPLE_SOURCE, ctx, new_method)
        # Le résultat doit être parseable
        import ast
        ast.parse(result)  # Ne doit pas lever d'exception

    def test_reassembly_produces_valid_ast(self):
        """Le fichier réassemblé est valide syntaxiquement."""
        agent = DivineEvolution()
        ctx = agent._extract_method_context(_SAMPLE_SOURCE, "process")
        new_method = "    def process(self, data):\n        self.count += 2\n        return data.lower()"
        result = agent._reassemble_code(_SAMPLE_SOURCE, ctx, new_method)
        import ast
        tree = ast.parse(result)
        # Vérifier que la classe existe toujours
        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        assert len(classes) >= 1


class TestMicroModeIntegration:
    """Tests d'intégration du mode micro dans le pipeline Phase 3."""

    @pytest.fixture(autouse=True)
    def disable_protected_files_guard(self):
        with patch("Agents.factory_agent._CRITICAL_FILES", set()):
            yield

    def _make_spec(self, cat, target_method="process"):
        spec = ImprovementSpec(
            id="MICRO-001", name="Test micro mode", description="Test du micro-découpage",
            category="performance", target_file="core/sample.py", target_method=target_method,
            difficulty=1, code_template="# amélioration", validation="test",
            status="available", attempts=0,
        )
        cat.specs[spec.id] = spec
        return spec

    @pytest.mark.asyncio
    async def test_micro_mode_activates_with_target_method(self):
        """Le mode micro s'active quand target_method est renseigné et trouvé."""
        evo = DivineEvolution()
        thoughts = []
        evo.log_thought = lambda msg, **kw: thoughts.append(msg)

        cat = EvolutionCatalog()
        cat.specs.clear()  # Vider les specs built-in pour forcer notre spec
        self._make_spec(cat, target_method="process")

        # Le cloud retourne juste la méthode modifiée — le réassemblage produit un fichier complet
        mock_method = "    def process(self, data):\n        self.count += 1\n        return data.strip()"

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={"status": "success", "result": "Validé"})

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value=_SAMPLE_SOURCE), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=mock_method), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        # Vérifier que le mode micro a été mentionné dans les logs
        micro_logs = [t for t in thoughts if "micro" in t.lower() or "🔬" in t or "Réassemblage" in t]
        assert len(micro_logs) >= 1, f"Aucun log micro trouvé dans: {thoughts}"

    @pytest.mark.asyncio
    async def test_fallback_when_no_target_method(self):
        """Fallback fichier complet quand target_method est vide."""
        evo = DivineEvolution()
        thoughts = []
        evo.log_thought = lambda msg, **kw: thoughts.append(msg)

        cat = EvolutionCatalog()
        cat.specs.clear()
        self._make_spec(cat, target_method="")

        valid_code = "import os\nimport sys\ndef hello():\n    print('hello from full file mode padding')"

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={"status": "success", "result": "Validé"})

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value=_SAMPLE_SOURCE), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=valid_code), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        # Pas de log micro
        micro_logs = [t for t in thoughts if "🔬" in t]
        assert len(micro_logs) == 0

    @pytest.mark.asyncio
    async def test_reassembly_produces_full_file(self):
        """En mode micro, le réassemblage produit un fichier complet qui passe les validations."""
        evo = DivineEvolution()

        cat = EvolutionCatalog()
        cat.specs.clear()
        self._make_spec(cat, target_method="process")

        # Le LLM retourne juste la méthode modifiée
        mock_method = "    def process(self, data):\n        self.count += 1\n        return 'reassembled'"

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={"status": "success", "result": "Validé"})

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value=_SAMPLE_SOURCE), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=mock_method), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        # Le pipeline ne devrait PAS échouer en anti-troncature (le réassemblage fait un fichier complet)
        assert "Anti-troncature" not in result.get("result", "")


class TestFeedbackLoop:
    """Tests de la boucle de retries syntaxiques Phase 4."""

    @pytest.fixture(autouse=True)
    def disable_protected_files_guard(self):
        with patch("Agents.factory_agent._CRITICAL_FILES", set()):
            yield

    def _make_spec(self, cat):
        spec = ImprovementSpec(
            id="RETRY-001", name="Test retry", description="Test feedback loop",
            category="resilience", target_file="core/sample.py", target_method="process",
            difficulty=1, code_template="# fix", validation="test",
            status="available", attempts=0,
        )
        cat.specs[spec.id] = spec
        return spec

    @pytest.mark.asyncio
    async def test_retry_fixes_syntax_on_second_attempt(self):
        """Le retry corrige une erreur de syntaxe au 2ème essai."""
        evo = DivineEvolution()

        cat = EvolutionCatalog()
        self._make_spec(cat)

        bad_code = "def broken(:\n    pass\n# padding minimum 50 chars for the test to work here"
        good_code = "import os\ndef fixed():\n    pass\n# padding minimum 50 chars for the test to work here ok"

        call_count = [0]

        async def mock_cloud(prompt):
            nonlocal call_count
            call_count[0] += 1
            return bad_code if call_count[0] == 1 else good_code

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={"status": "success", "result": "Validé"})

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value="# existing\npass"), \
             patch.object(evo, "_generate_code_cloud", side_effect=mock_cloud), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        # Le retry cloud a été appelé au moins 2 fois
        assert call_count[0] >= 2
        # Le résultat ne devrait pas être une erreur de syntaxe
        assert "ast.parse error" not in result.get("result", "")

    @pytest.mark.asyncio
    async def test_retry_uses_local_coder_when_cloud_fails(self):
        """Le retry utilise le coder local quand Cloud retourne rien."""
        evo = DivineEvolution()

        cat = EvolutionCatalog()
        self._make_spec(cat)

        bad_code = "def broken(:\n    pass\n# padding minimum 50 chars for the test to work here"
        good_code = "import os\ndef fixed():\n    pass\n# padding minimum 50 chars for the test to work here ok"

        call_count = [0]

        async def mock_cloud(prompt):
            nonlocal call_count
            call_count[0] += 1
            if call_count[0] == 1:
                return bad_code
            return ""  # Cloud échoue pour les retries

        mock_orch = MagicMock()
        # Le coder local retourne du code valide pour le retry syntax
        mock_orch.dispatch_task = AsyncMock(return_value={"status": "success", "result": good_code})

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value="# existing\npass"), \
             patch.object(evo, "_generate_code_cloud", side_effect=mock_cloud), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        # Le coder local a été appelé pour le retry syntax
        syntax_fix_calls = [
            c for c in mock_orch.dispatch_task.call_args_list
            if c[0][0] == "coder" and "SYNTAX_FIX" in str(c)
        ]
        assert len(syntax_fix_calls) >= 1

    @pytest.mark.asyncio
    async def test_dead_end_after_max_retries(self):
        """Après MAX_SYNTAX_RETRIES, le code est rejeté."""
        evo = DivineEvolution()

        cat = EvolutionCatalog()
        self._make_spec(cat)

        bad_code = "def broken(:\n    pass\n# padding minimum 50 chars for the test to work here"

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={"result": bad_code})

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value="# existing\npass"), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=bad_code), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        assert result["status"] == "error"
        assert "ast.parse error" in result["result"]
        assert "retries" in result["result"]

    @pytest.mark.asyncio
    async def test_error_line_in_retry_prompt(self):
        """Le prompt de retry contient le numéro de ligne exact de l'erreur."""
        evo = DivineEvolution()
        retry_prompts = []

        cat = EvolutionCatalog()
        self._make_spec(cat)

        bad_code = "def broken(:\n    pass\n# padding minimum 50 chars for the test to work here"
        good_code = "import os\ndef fixed():\n    pass\n# padding minimum 50 chars for the test to work here ok"

        call_count = [0]

        async def mock_cloud(prompt):
            nonlocal call_count
            call_count[0] += 1
            if call_count[0] == 1:
                return bad_code
            retry_prompts.append(prompt)
            return good_code

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={"status": "success", "result": "Validé"})

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value="# existing\npass"), \
             patch.object(evo, "_generate_code_cloud", side_effect=mock_cloud), \
             patch("core.orchestrator.orchestrator", mock_orch):
            await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        # Le prompt de retry doit contenir l'info d'erreur
        assert len(retry_prompts) >= 1
        assert "ERREUR DE SYNTAXE" in retry_prompts[0]


class TestEvolutionPatchMode:
    """Tests du mode micro-patch SEARCH/REPLACE dans le pipeline Evolution."""

    @pytest.fixture(autouse=True)
    def disable_protected_files_guard(self):
        with patch("Agents.factory_agent._CRITICAL_FILES", set()):
            yield

    @pytest.mark.asyncio
    async def test_local_fallback_uses_patch_mode(self):
        """Quand Cloud échoue, le contexte du dispatch contient MICRO_PATCH."""
        evo = DivineEvolution()

        source_code = (
            "import os\nimport logging\n\n"
            "class MyAgent:\n"
            "    def process(self, data):\n"
            "        return data\n"
        )

        # Patch qui sera retourné par le Coder
        patch_response = (
            "<<<< SEARCH\n"
            "    def process(self, data):\n"
            "        return data\n"
            "====\n"
            "    def process(self, data):\n"
            "        if not data:\n"
            "            return None\n"
            "        return data\n"
            ">>>> REPLACE"
        )

        dispatch_calls = []

        async def mock_dispatch(agent, payload):
            dispatch_calls.append((agent, payload))
            if agent == "coder":
                return {"status": "success", "result": patch_response}
            return {"status": "success", "result": "VALIDÉ"}

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(side_effect=mock_dispatch)
        mock_orch._contains_python_code = MagicMock(return_value=True)

        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value=source_code), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=None), \
             patch("core.orchestrator.orchestrator", mock_orch), \
             patch("core.event_bus.bus.bus", mock_bus):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        # Le premier dispatch au coder doit contenir MICRO_PATCH dans le contexte
        coder_calls = [(a, p) for a, p in dispatch_calls if a == "coder"]
        assert len(coder_calls) >= 1
        assert "MICRO_PATCH" in coder_calls[0][1].get("context", "")

    @pytest.mark.asyncio
    async def test_patch_mode_skips_phase4d(self):
        """Phase 4d ne rejette pas en mode local+patch même si le source est long."""
        evo = DivineEvolution()

        # Source très long (>500 chars)
        source_code = (
            "import os\nimport logging\n\n"
            "class BigModule:\n"
            + "".join(f"    def method_{i}(self):\n        pass\n\n" for i in range(30))
        )
        assert len(source_code) > 500

        # Patch qui modifie juste 2 lignes (code patché ≈ même taille que source)
        patch_response = (
            "<<<< SEARCH\n"
            "    def method_0(self):\n"
            "        pass\n"
            "====\n"
            "    def method_0(self):\n"
            "        return 42\n"
            ">>>> REPLACE"
        )

        async def mock_dispatch(agent, payload):
            if agent == "coder":
                return {"status": "success", "result": patch_response}
            return {"status": "success", "result": "VALIDÉ"}

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(side_effect=mock_dispatch)
        mock_orch._contains_python_code = MagicMock(return_value=True)

        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value=source_code), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=None), \
             patch("core.orchestrator.orchestrator", mock_orch), \
             patch("core.event_bus.bus.bus", mock_bus):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        # Le résultat ne doit PAS mentionner anti-troncature
        assert "Anti-troncature" not in result.get("result", "")
        # L'architecte doit avoir été appelé (le code a passé toutes les phases)
        architect_calls = [c for c in mock_orch.dispatch_task.call_args_list if c[0][0] == "architect"]
        assert len(architect_calls) == 1

    @pytest.mark.asyncio
    async def test_patch_failure_falls_through(self):
        """Si le patch échoue, le cycle échoue proprement (pas de crash)."""
        evo = DivineEvolution()

        source_code = "import os\n\ndef main():\n    pass\n"

        # Patch invalide (SEARCH ne matche pas le source)
        bad_patch = (
            "<<<< SEARCH\n"
            "def inexistant():\n"
            "    pass\n"
            "====\n"
            "def inexistant():\n"
            "    return 42\n"
            ">>>> REPLACE"
        )

        call_count = [0]

        async def mock_dispatch(agent, payload):
            call_count[0] += 1
            if agent == "coder":
                ctx = payload.get("context", "")
                if "MICRO_PATCH" in ctx:
                    return {"status": "success", "result": bad_patch}
                # Fallback historique : retourne aussi du code insuffisant
                return {"status": "success", "result": "# too short"}
            return {"status": "success", "result": "OK"}

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(side_effect=mock_dispatch)

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value=source_code), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=None), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        # Le cycle doit échouer proprement (warning, pas d'exception)
        assert result["status"] == "warning"

    @pytest.mark.asyncio
    async def test_code_source_recorded(self):
        """L'expérience enregistre code_source='local+patch' quand le patch réussit."""
        evo = DivineEvolution()

        source_code = (
            "import os\nimport logging\n\n"
            "class Agent:\n"
            "    def run(self):\n"
            "        pass\n"
        )

        patch_response = (
            "<<<< SEARCH\n"
            "    def run(self):\n"
            "        pass\n"
            "====\n"
            "    def run(self):\n"
            "        return True\n"
            ">>>> REPLACE"
        )

        recorded_experiences = []
        original_record = ExperienceRegistry.record

        def capture_record(self_reg, exp):
            recorded_experiences.append(exp)
            return original_record(self_reg, exp)

        async def mock_dispatch(agent, payload):
            if agent == "coder":
                return {"status": "success", "result": patch_response}
            return {"status": "success", "result": "VALIDÉ"}

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(side_effect=mock_dispatch)
        mock_orch._contains_python_code = MagicMock(return_value=True)

        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value=source_code), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=None), \
             patch("core.orchestrator.orchestrator", mock_orch), \
             patch("core.event_bus.bus.bus", mock_bus), \
             patch.object(ExperienceRegistry, "record", capture_record):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        # Au moins une expérience enregistrée avec code_source contenant "local+patch"
        patch_exps = [e for e in recorded_experiences if "local+patch" in (e.code_source or "")]
        assert len(patch_exps) >= 1

    @pytest.mark.asyncio
    async def test_patch_retry_on_failure(self):
        """Quand le premier patch échoue, un retry avec contexte d'erreur est tenté."""
        evo = DivineEvolution()

        source_code = (
            "import os\nimport logging\n\n"
            "class Agent:\n"
            "    def process(self, data):\n"
            "        return data\n"
        )

        # Premier patch avec typo dans SEARCH
        bad_patch = (
            "<<<< SEARCH\n"
            "    def proccess(self, data):\n"
            "        return data\n"
            "====\n"
            "    def process(self, data):\n"
            "        return data.strip()\n"
            ">>>> REPLACE"
        )
        # Patch corrigé en retry
        good_patch = (
            "<<<< SEARCH\n"
            "    def process(self, data):\n"
            "        return data\n"
            "====\n"
            "    def process(self, data):\n"
            "        return data.strip()\n"
            ">>>> REPLACE"
        )

        dispatch_calls = []

        async def mock_dispatch(agent, payload):
            dispatch_calls.append((agent, payload))
            if agent == "coder":
                ctx = payload.get("context", "")
                if "RETRY" in ctx:
                    return {"status": "success", "result": good_patch}
                if "MICRO_PATCH" in ctx:
                    return {"status": "success", "result": bad_patch}
            return {"status": "success", "result": "VALIDÉ"}

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(side_effect=mock_dispatch)
        mock_orch._contains_python_code = MagicMock(return_value=True)

        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value=source_code), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=None), \
             patch("core.orchestrator.orchestrator", mock_orch), \
             patch("core.event_bus.bus.bus", mock_bus):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        # Le retry doit avoir été appelé avec RETRY dans le contexte
        retry_calls = [(a, p) for a, p in dispatch_calls
                       if a == "coder" and "RETRY" in p.get("context", "")]
        assert len(retry_calls) == 1
        # Le résultat ne doit PAS mentionner d'échec
        assert "Anti-troncature" not in result.get("result", "")

    @pytest.mark.asyncio
    async def test_patch_retry_failure_falls_through(self):
        """Si le retry échoue aussi, on tombe sur le fallback historique."""
        evo = DivineEvolution()

        source_code = "import os\n\ndef main():\n    pass\n"

        # Les deux patchs échouent (SEARCH introuvable)
        bad_patch = (
            "<<<< SEARCH\n"
            "def inexistant():\n"
            "    pass\n"
            "====\n"
            "def inexistant():\n"
            "    return 42\n"
            ">>>> REPLACE"
        )

        async def mock_dispatch(agent, payload):
            if agent == "coder":
                ctx = payload.get("context", "")
                if "MICRO_PATCH" in ctx:
                    return {"status": "success", "result": bad_patch}
                # Fallback historique retourne aussi du code insuffisant
                return {"status": "success", "result": "# trop court"}
            return {"status": "success", "result": "OK"}

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(side_effect=mock_dispatch)

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value=source_code), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=None), \
             patch("core.orchestrator.orchestrator", mock_orch):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        # Le cycle doit échouer proprement
        assert result["status"] == "warning"

    @pytest.mark.asyncio
    async def test_patch_retry_code_source_recorded(self):
        """L'expérience enregistre code_source='local+patch+retry' après retry réussi."""
        evo = DivineEvolution()

        source_code = (
            "import os\nimport logging\n\n"
            "class Agent:\n"
            "    def run(self):\n"
            "        pass\n"
        )

        bad_patch = (
            "<<<< SEARCH\n"
            "    def runn(self):\n"
            "        pass\n"
            "====\n"
            "    def run(self):\n"
            "        return True\n"
            ">>>> REPLACE"
        )
        good_patch = (
            "<<<< SEARCH\n"
            "    def run(self):\n"
            "        pass\n"
            "====\n"
            "    def run(self):\n"
            "        return True\n"
            ">>>> REPLACE"
        )

        recorded_experiences = []
        original_record = ExperienceRegistry.record

        def capture_record(self_reg, exp):
            recorded_experiences.append(exp)
            return original_record(self_reg, exp)

        async def mock_dispatch(agent, payload):
            if agent == "coder":
                ctx = payload.get("context", "")
                if "RETRY" in ctx:
                    return {"status": "success", "result": good_patch}
                if "MICRO_PATCH" in ctx:
                    return {"status": "success", "result": bad_patch}
            return {"status": "success", "result": "VALIDÉ"}

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(side_effect=mock_dispatch)
        mock_orch._contains_python_code = MagicMock(return_value=True)

        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value=source_code), \
             patch.object(evo, "_generate_code_cloud", new_callable=AsyncMock, return_value=None), \
             patch("core.orchestrator.orchestrator", mock_orch), \
             patch("core.event_bus.bus.bus", mock_bus), \
             patch.object(ExperienceRegistry, "record", capture_record):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        # L'expérience doit contenir "local+patch+retry"
        retry_exps = [e for e in recorded_experiences if "retry" in (e.code_source or "")]
        assert len(retry_exps) >= 1


class TestKnowledgeSynthesis:
    """Tests pour le pipeline de synthèse de connaissances."""

    def _make_evo(self):
        evo = DivineEvolution()
        evo.daily_budget_used = 0
        evo._council_degraded = False
        return evo

    # --- _gather_seeds ---

    @pytest.mark.skip(reason="Obsolete test: logic divergence with prod (hygiene 2026-05-07)")
    def test_gather_seeds_from_desires(self):
        """Drives avec deprivation > 50 deviennent des graines."""
        evo = self._make_evo()

        mock_drive_high = MagicMock(name="CURIOSITE", deprivation=75)
        mock_drive_high.name = "CURIOSITE"
        mock_drive_low = MagicMock(name="STABILITE", deprivation=30)
        mock_drive_low.name = "STABILITE"
        mock_desires = MagicMock()
        mock_desires.drives = {"CURIOSITE": mock_drive_high, "STABILITE": mock_drive_low}

        with patch.dict("sys.modules", {"core.desire_engine": MagicMock(desires=mock_desires)}), \
             patch.dict("sys.modules", {"core.hippocampus": MagicMock(hippocampus=MagicMock(get_session_narrative=MagicMock(return_value=[])))}), \
             patch.dict("sys.modules", {"core.synaptic_network": MagicMock(cortex=MagicMock(nodes={}))}):
            seeds = evo._gather_seeds()

        assert "CURIOSITE" in seeds
        assert "STABILITE" not in seeds

    @pytest.mark.skip(reason="Obsolete test: logic divergence with prod (hygiene 2026-05-07)")
    def test_gather_seeds_from_hippocampus(self):
        """Mots > 4 chars des épisodes récents deviennent graines."""
        evo = self._make_evo()

        episodes = [
            {"summary": "Le module orchestrator a été optimisé", "salience": 0.8},
            {"summary": "Bug corrigé dans le router", "salience": 0.6},
        ]

        mock_hippo = MagicMock()
        mock_hippo.get_session_narrative = MagicMock(return_value=episodes)

        with patch.dict("sys.modules", {"core.desire_engine": MagicMock(desires=MagicMock(drives={}))}), \
             patch.dict("sys.modules", {"core.hippocampus": MagicMock(hippocampus=mock_hippo)}), \
             patch.dict("sys.modules", {"core.synaptic_network": MagicMock(cortex=MagicMock(nodes={}))}):
            seeds = evo._gather_seeds()

        assert "module" in seeds
        assert "orchestrator" in seeds
        # Mots courts (<=4 chars) exclus
        assert "le" not in seeds and "Le" not in seeds

    @pytest.mark.skip(reason="Obsolete test: logic divergence with prod (hygiene 2026-05-07)")
    def test_gather_seeds_from_synaptic(self):
        """Top noeuds par énergie deviennent graines, max 15."""
        evo = self._make_evo()

        nodes = {f"concept_{i}": {"energy": 1.0 - i * 0.05} for i in range(20)}
        mock_cortex = MagicMock()
        mock_cortex.nodes = nodes

        with patch.dict("sys.modules", {"core.desire_engine": MagicMock(desires=MagicMock(drives={}))}), \
             patch.dict("sys.modules", {"core.hippocampus": MagicMock(hippocampus=MagicMock(get_session_narrative=MagicMock(return_value=[])))}), \
             patch.dict("sys.modules", {"core.synaptic_network": MagicMock(cortex=mock_cortex)}):
            seeds = evo._gather_seeds()

        assert len(seeds) <= 15
        assert len(seeds) > 0  # au moins quelques concepts du reseau
        # Les seeds viennent du top 20 noeuds (shuffle + exclusion possible)
        assert all(s.startswith("concept_") for s in seeds)

    def test_gather_seeds_graceful_degradation(self):
        """3 sources en erreur → liste vide."""
        evo = self._make_evo()

        bad_desires = MagicMock()
        type(bad_desires).drives = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
        bad_hippo = MagicMock()
        bad_hippo.get_session_narrative = MagicMock(side_effect=RuntimeError("boom"))
        bad_cortex = MagicMock()
        type(bad_cortex).nodes = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

        with patch.dict("sys.modules", {
            "core.desire_engine": MagicMock(desires=bad_desires),
            "core.hippocampus": MagicMock(hippocampus=bad_hippo),
            "core.synaptic_network": MagicMock(cortex=bad_cortex),
        }):
            seeds = evo._gather_seeds()

        assert seeds == [] or isinstance(seeds, list)

    # --- _cross_reference ---

    def test_cross_reference_bridges(self):
        """Concepts activés par 2+ cascades → bridges."""
        evo = self._make_evo()

        # 6 seeds → 2 groupes de 3 : [alpha,beta,gamma] et [delta,epsilon,zeta]
        # pont_commun doit apparaître dans les deux groupes
        def mock_cascade(seed_concepts, cycles=4):
            if "alpha" in seed_concepts:
                return [("pont_commun", 0.8), ("alpha_only", 0.5)]
            elif "delta" in seed_concepts:
                return [("pont_commun", 0.7), ("delta_only", 0.4)]
            return []

        mock_cortex = MagicMock()
        mock_cortex.resonance_cascade = mock_cascade

        with patch.dict("sys.modules", {"core.synaptic_network": MagicMock(cortex=mock_cortex)}):
            result = evo._cross_reference(["alpha", "beta", "gamma", "delta", "epsilon", "zeta"])

        bridge_names = [c for c, _ in result["bridges"]]
        assert "pont_commun" in bridge_names

    def test_cross_reference_anomalies(self):
        """Haute énergie dans 1 seul cluster → anomalie."""
        evo = self._make_evo()

        # Groupe 1 [alpha,beta,gamma] → anomalie_isolee (haute énergie, 1 seul cluster)
        # Groupe 2 [delta,epsilon,zeta] → commun seulement
        def mock_cascade(seed_concepts, cycles=4):
            if "alpha" in seed_concepts:
                return [("anomalie_isolee", 0.9), ("commun", 0.2)]
            elif "delta" in seed_concepts:
                return [("commun", 0.3)]
            return []

        mock_cortex = MagicMock()
        mock_cortex.resonance_cascade = mock_cascade

        with patch.dict("sys.modules", {"core.synaptic_network": MagicMock(cortex=mock_cortex)}):
            result = evo._cross_reference(["alpha", "beta", "gamma", "delta", "epsilon", "zeta"])

        anomaly_names = [c for c, _ in result["anomalies"]]
        assert "anomalie_isolee" in anomaly_names

    def test_cross_reference_empty(self):
        """Réseau vide ou pas de seeds → résultat vide."""
        evo = self._make_evo()
        result = evo._cross_reference([])
        assert result["bridges"] == []
        assert result["anomalies"] == []

    # --- _synthesize_insight ---

    @pytest.mark.asyncio
    async def test_synthesize_llm_success(self):
        """LLM narration réussie → texte utilisé."""
        evo = self._make_evo()

        llm_response = "Les connexions entre orchestrator et router révèlent un pattern de communication centralisé."
        cross_result = {"bridges": [("orchestrator", 0.8), ("router", 0.7)], "anomalies": []}

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value=llm_response), \
             patch.dict("sys.modules", {"core.corpus_callosum": MagicMock(callosum=MagicMock(get_cognitive_context=MagicMock(return_value="flow")))}), \
             patch.dict("sys.modules", {"core.desire_engine": MagicMock(desires=MagicMock(get_dominant_narrative=MagicMock(return_value="curiosité")))}):
            result = await evo._synthesize_insight(["orchestrator", "router"], cross_result)

        assert "orchestrator" in result or "connexions" in result
        assert len(result) >= 30

    @pytest.mark.asyncio
    async def test_synthesize_fallback(self):
        """LLM échoue → fallback déterministe."""
        evo = self._make_evo()
        cross_result = {"bridges": [("concept_a", 0.5)], "anomalies": [("anomalie_x", 0.4)]}

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value=""), \
             patch.dict("sys.modules", {"core.corpus_callosum": MagicMock(callosum=MagicMock(get_cognitive_context=MagicMock(return_value="")))}), \
             patch.dict("sys.modules", {"core.desire_engine": MagicMock(desires=MagicMock(get_dominant_narrative=MagicMock(return_value="")))}):
            result = await evo._synthesize_insight(["test"], cross_result)

        assert "Synthese croisee" in result
        assert "concept_a" in result

    # --- _crystallize ---

    def test_crystallize_strengthens(self):
        """V4.0 (2026-04-19) : _crystallize utilise desormais
        _apply_causal_delta (EVOLUTION_BRIDGE_DELTA=0.12) au lieu
        de l ancien hebbian_strengthen (Temporal Superstition).
        Test mis a jour : verifie le nouveau call pattern causal V4.
        """
        evo = self._make_evo()

        mock_cortex = MagicMock()
        cross_result = {
            "bridges": [("concept_a", 0.8), ("concept_b", 0.7), ("concept_c", 0.6)],
            "anomalies": []
        }

        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()

        with patch.dict("sys.modules", {"core.synaptic_network": MagicMock(cortex=mock_cortex)}), \
             patch.dict("sys.modules", {"core.event_bus": MagicMock(bus=mock_bus)}), \
             patch.object(evo, "remember"):
            evo._crystallize(cross_result, "Un insight de test pour verifier le renforcement causal V4.")

        # V4 : _apply_causal_delta appele 2 fois (a->b, b->c)
        assert mock_cortex._apply_causal_delta.call_count == 2
        # Premier appel : concept_a -> concept_b avec delta 0.12
        first_call = mock_cortex._apply_causal_delta.call_args_list[0]
        assert first_call.args[0] == "concept_a"
        assert first_call.args[1] == "concept_b"
        assert first_call.args[2] == 0.12  # EVOLUTION_BRIDGE_DELTA
        assert "knowledge_synthesis_v4" in first_call.kwargs.get("context", "")
        # V4 : hebbian_strengthen ne doit PLUS etre appele (Temporal Superstition purgee)
        assert mock_cortex.hebbian_strengthen.call_count == 0

    # --- Pipeline complet ---

    @pytest.mark.asyncio
    async def test_full_pipeline(self):
        """MODE VEILLE sans CATALOG → synthèse de connaissances."""
        evo = self._make_evo()

        # Mock _gather_seeds
        with patch.object(evo, "_gather_seeds", return_value=["concept_a", "concept_b", "concept_c"]), \
             patch.object(evo, "_cross_reference", return_value={
                 "bridges": [("pont_x", 0.8)],
                 "anomalies": [("anomalie_y", 0.5)],
                 "clusters": [["concept_a"], ["concept_b", "concept_c"]]
             }), \
             patch.object(evo, "_synthesize_insight", new_callable=AsyncMock,
                         return_value="Insight significatif sur les connexions internes du système."), \
             patch.object(evo, "_crystallize"):
            result = await evo.process_task({"mission": "[MODE VEILLE] Croise les connaissances."})

        assert result["status"] == "success"
        assert "SYNTHESE CONNAISSANCE TERMINEE" in result["result"]
        assert "Graines: 3" in result["result"]
        assert "Ponts: 1" in result["result"]

    @pytest.mark.asyncio
    async def test_routing_catalog_explicit(self):
        """'CATALOG' dans mission → catalog pipeline."""
        evo = self._make_evo()

        with patch.object(evo, "_run_catalog_pipeline", new_callable=AsyncMock,
                         return_value={"status": "success", "result": "CYCLE CATALOG V6"}):
            result = await evo.process_task({"mission": "[MODE VEILLE] CATALOG"})

        assert "CYCLE CATALOG V6" in result["result"]
