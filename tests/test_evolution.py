import os
import tempfile
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from Agents.evolution_agent import (
    DivineEvolution, _is_spec_offtopic, _spec_targets_existing_file,
    _SEARCH_QUERIES, _SPEC_OFFTOPIC_THRESHOLD
)
from core.evolution_catalog import EvolutionCatalog, ImprovementSpec

_FAKE_STATE_FILE = os.path.join(tempfile.gettempdir(), "test_evolution_state.json")


@pytest.fixture(autouse=True)
def isolate_catalog():
    """Isole le catalogue entre chaque test."""
    if os.path.exists(_FAKE_STATE_FILE):
        os.remove(_FAKE_STATE_FILE)
    with patch("core.evolution_catalog.CATALOG_STATE_FILE", _FAKE_STATE_FILE):
        EvolutionCatalog.reset_singleton()
        yield
        EvolutionCatalog.reset_singleton()
    if os.path.exists(_FAKE_STATE_FILE):
        os.remove(_FAKE_STATE_FILE)


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
            result = await evo.process_task({"mission": "[MODE VEILLE]"})

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
            result = await evo.process_task({"mission": "[MODE VEILLE]"})

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
            result = await evo.process_task({"mission": "[MODE VEILLE]"})

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
            result = await evo.process_task({"mission": "[MODE VEILLE]"})

        assert "R.A.S" in result["result"] or "Aucun code" in result["result"]

    @pytest.mark.asyncio
    async def test_catalog_pipeline_missing_target_file(self):
        """Si le fichier cible n'existe pas, la spec échoue."""
        evo = DivineEvolution()

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value=""):
            result = await evo.process_task({"mission": "[MODE VEILLE]"})

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
            result = await evo.process_task({"mission": "[MODE VEILLE]"})
            mock_grim.assert_called_once()
        assert "R.A.S" in result["result"]

    @pytest.mark.asyncio
    async def test_catalog_pipeline_deployed_marks_catalog(self):
        """Si l'Architecte valide, la spec est marquée deployed dans le catalogue."""
        evo = DivineEvolution()
        cat = EvolutionCatalog()

        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(return_value={
            "result": "import logging\nlogger = logging.getLogger('test')\nclass Foo:\n    pass",
            "status": "success"
        })

        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()

        with patch.object(evo, "generate_content", new_callable=AsyncMock, return_value="1"), \
             patch.object(evo, "_read_target_file", return_value="# existing code\npass"), \
             patch("core.orchestrator.orchestrator", mock_orch), \
             patch("core.event_bus.bus.bus", mock_bus):
            result = await evo.process_task({"mission": "[MODE VEILLE]"})

        assert "CYCLE CATALOG V6" in result["result"]
        deployed = cat.get_specs_by_status("deployed")
        assert len(deployed) >= 1

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
            result = await evo.process_task({"mission": "[MODE VEILLE]"})

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
            result = await evo.process_task({"mission": "[MODE VEILLE]"})

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
            result = await evo.process_task({"mission": "[MODE VEILLE]"})
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
            result = await evo.process_task({"mission": "[MODE VEILLE]"})

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
            result = await evo.process_task({"mission": "[MODE VEILLE]"})

        assert result["status"] == "success"
        # Le Coder local doit avoir été appelé en fallback
        coder_calls = [c for c in mock_orch.dispatch_task.call_args_list if c[0][0] == "coder"]
        assert len(coder_calls) == 1
