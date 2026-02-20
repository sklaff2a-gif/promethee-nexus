# tests/test_code_smith.py — Tests unitaires et intégration pour CodeSmith
import ast
import os
import sys
import pytest

# Setup du path projet
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.code_smith import (
    CodeSmith, TransformAction, TransformResult, TransformType,
    spec_to_actions, get_supported_specs, _SPEC_HANDLERS,
)


# --- Fixture : code source de test ---

SIMPLE_CLASS = '''\
import logging
import os

logger = logging.getLogger("test")


class MyAgent:
    """Un agent de test."""

    _counter = 0

    def __init__(self, name):
        self.name = name

    def process(self, data):
        """Traite les données."""
        result = data.upper()
        return result

    def helper(self):
        pass
'''

SIMPLE_MODULE = '''\
import os
import sys

logger = None

def existing_func():
    return 42
'''

ASYNC_CLASS = '''\
import asyncio

class AsyncAgent:
    """Agent async."""

    async def run(self, task):
        """Exécute une tâche."""
        result = await self._do_work(task)
        return result

    async def _do_work(self, task):
        await asyncio.sleep(0.1)
        return f"done: {task}"
'''


# =====================================================================
# Tests ADD_IMPORT
# =====================================================================

class TestAddImport:
    def test_ajoute_import_apres_existants(self):
        actions = [TransformAction(type=TransformType.ADD_IMPORT, import_line="import time")]
        result = CodeSmith.apply(SIMPLE_CLASS, actions)
        assert result.success
        assert "import time" in result.modified_source
        # Doit être après les imports existants
        lines = result.modified_source.split("\n")
        time_idx = next(i for i, l in enumerate(lines) if l.strip() == "import time")
        os_idx = next(i for i, l in enumerate(lines) if l.strip() == "import os")
        assert time_idx > os_idx
        ast.parse(result.modified_source)

    def test_skip_import_duplique(self):
        actions = [TransformAction(type=TransformType.ADD_IMPORT, import_line="import os")]
        result = CodeSmith.apply(SIMPLE_CLASS, actions)
        assert result.success
        # import os ne doit apparaître qu'une seule fois
        count = result.modified_source.count("import os")
        assert count == 1

    def test_import_from_duplique(self):
        source = "from os.path import join\n\ndef foo(): pass\n"
        actions = [TransformAction(type=TransformType.ADD_IMPORT, import_line="from os.path import join")]
        result = CodeSmith.apply(source, actions)
        assert result.success
        assert result.modified_source.count("from os.path import join") == 1


# =====================================================================
# Tests ADD_CLASS_ATTRIBUTE
# =====================================================================

class TestAddClassAttribute:
    def test_ajoute_attribut_dans_classe(self):
        actions = [TransformAction(
            type=TransformType.ADD_CLASS_ATTRIBUTE,
            target_class="MyAgent",
            code="_cache: dict = {}",
        )]
        result = CodeSmith.apply(SIMPLE_CLASS, actions)
        assert result.success
        assert "_cache: dict = {}" in result.modified_source
        ast.parse(result.modified_source)

    def test_idempotent_si_attribut_existe(self):
        actions = [TransformAction(
            type=TransformType.ADD_CLASS_ATTRIBUTE,
            target_class="MyAgent",
            code="_counter = 0",
        )]
        result = CodeSmith.apply(SIMPLE_CLASS, actions)
        assert result.success
        # _counter ne doit apparaître qu'une seule fois
        count = result.modified_source.count("_counter")
        assert count == 1

    def test_erreur_classe_inexistante(self):
        actions = [TransformAction(
            type=TransformType.ADD_CLASS_ATTRIBUTE,
            target_class="InexistentClass",
            code="_x = 1",
        )]
        result = CodeSmith.apply(SIMPLE_CLASS, actions)
        assert not result.success
        assert "non trouvée" in result.error


# =====================================================================
# Tests ADD_METHOD
# =====================================================================

class TestAddMethod:
    def test_ajoute_methode_en_fin_de_classe(self):
        actions = [TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="MyAgent",
            code='def new_method(self):\n    return "hello"',
        )]
        result = CodeSmith.apply(SIMPLE_CLASS, actions)
        assert result.success
        assert "def new_method(self):" in result.modified_source
        ast.parse(result.modified_source)

    def test_preserve_commentaires(self):
        source_with_comments = SIMPLE_CLASS + "# Fin du fichier\n"
        actions = [TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="MyAgent",
            code='def added(self):\n    pass',
        )]
        result = CodeSmith.apply(source_with_comments, actions)
        assert result.success
        assert "# Fin du fichier" in result.modified_source
        ast.parse(result.modified_source)

    def test_idempotent_si_methode_existe(self):
        actions = [TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="MyAgent",
            code='def process(self, data):\n    return data.lower()',
        )]
        result = CodeSmith.apply(SIMPLE_CLASS, actions)
        assert result.success
        # 'def process' ne doit apparaître qu'une seule fois
        count = result.modified_source.count("def process(")
        assert count == 1

    def test_indentation_correcte(self):
        actions = [TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="MyAgent",
            code='def check(self):\n    if True:\n        return 1\n    return 0',
        )]
        result = CodeSmith.apply(SIMPLE_CLASS, actions)
        assert result.success
        # Vérifier que le code est parseable (indentation correcte)
        ast.parse(result.modified_source)
        # Vérifier que la méthode est bien dans la classe
        tree = ast.parse(result.modified_source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "MyAgent":
                method_names = [m.name for m in node.body if isinstance(m, ast.FunctionDef)]
                assert "check" in method_names


# =====================================================================
# Tests ADD_MODULE_FUNCTION
# =====================================================================

class TestAddModuleFunction:
    def test_ajoute_fonction_module(self):
        actions = [TransformAction(
            type=TransformType.ADD_MODULE_FUNCTION,
            code='def utility():\n    return True',
        )]
        result = CodeSmith.apply(SIMPLE_MODULE, actions)
        assert result.success
        assert "def utility():" in result.modified_source
        ast.parse(result.modified_source)

    def test_idempotent_si_fonction_existe(self):
        actions = [TransformAction(
            type=TransformType.ADD_MODULE_FUNCTION,
            code='def existing_func():\n    return 0',
        )]
        result = CodeSmith.apply(SIMPLE_MODULE, actions)
        assert result.success
        count = result.modified_source.count("def existing_func()")
        assert count == 1


# =====================================================================
# Tests ADD_MODULE_CONSTANT
# =====================================================================

class TestAddModuleConstant:
    def test_constante_apres_imports(self):
        actions = [TransformAction(
            type=TransformType.ADD_MODULE_CONSTANT,
            code='MAX_RETRIES = 3',
        )]
        result = CodeSmith.apply(SIMPLE_MODULE, actions)
        assert result.success
        assert "MAX_RETRIES = 3" in result.modified_source
        lines = result.modified_source.split("\n")
        const_idx = next(i for i, l in enumerate(lines) if "MAX_RETRIES" in l)
        import_idx = next(i for i, l in enumerate(lines) if "import sys" in l)
        assert const_idx > import_idx
        ast.parse(result.modified_source)

    def test_idempotent_si_constante_existe(self):
        source = "import os\n\nMAX_SIZE = 100\n"
        actions = [TransformAction(
            type=TransformType.ADD_MODULE_CONSTANT,
            code='MAX_SIZE = 100',
        )]
        result = CodeSmith.apply(source, actions)
        assert result.success
        assert result.modified_source.count("MAX_SIZE") == 1


# =====================================================================
# Tests WRAP_TRY_EXCEPT
# =====================================================================

class TestWrapTryExcept:
    def test_wrap_methode_simple(self):
        actions = [TransformAction(
            type=TransformType.WRAP_TRY_EXCEPT,
            target_class="MyAgent",
            target_method="process",
            except_body='logger.warning(f"process failed: {e}")',
        )]
        result = CodeSmith.apply(SIMPLE_CLASS, actions)
        assert result.success
        assert "try:" in result.modified_source
        assert "except Exception as e:" in result.modified_source
        assert 'logger.warning(f"process failed: {e}")' in result.modified_source
        ast.parse(result.modified_source)

    def test_preserve_docstring(self):
        actions = [TransformAction(
            type=TransformType.WRAP_TRY_EXCEPT,
            target_class="MyAgent",
            target_method="process",
            except_body='pass',
        )]
        result = CodeSmith.apply(SIMPLE_CLASS, actions)
        assert result.success
        assert '"""Traite les données."""' in result.modified_source
        ast.parse(result.modified_source)

    def test_wrap_methode_async(self):
        actions = [TransformAction(
            type=TransformType.WRAP_TRY_EXCEPT,
            target_class="AsyncAgent",
            target_method="run",
            except_body='return None',
        )]
        result = CodeSmith.apply(ASYNC_CLASS, actions)
        assert result.success
        assert "try:" in result.modified_source
        assert "except Exception as e:" in result.modified_source
        ast.parse(result.modified_source)

    def test_idempotent_si_deja_wrappe(self):
        """Si le body commence déjà par try:, ne pas re-wrapper."""
        actions = [TransformAction(
            type=TransformType.WRAP_TRY_EXCEPT,
            target_class="MyAgent",
            target_method="process",
            except_body='pass',
        )]
        result1 = CodeSmith.apply(SIMPLE_CLASS, actions)
        assert result1.success
        # Appliquer une 2e fois
        result2 = CodeSmith.apply(result1.modified_source, actions)
        assert result2.success
        # Ne doit y avoir qu'un seul "try:" dans la méthode
        assert result2.modified_source.count("try:") == 1


# =====================================================================
# Tests INSERT_EARLY_RETURN
# =====================================================================

class TestInsertEarlyReturn:
    def test_guard_avant_body(self):
        actions = [TransformAction(
            type=TransformType.INSERT_EARLY_RETURN,
            target_class="MyAgent",
            target_method="process",
            code='if not data:\n    return ""',
        )]
        result = CodeSmith.apply(SIMPLE_CLASS, actions)
        assert result.success
        assert "if not data:" in result.modified_source
        ast.parse(result.modified_source)
        # Le guard doit être avant le result = data.upper()
        lines = result.modified_source.split("\n")
        guard_idx = next(i for i, l in enumerate(lines) if "if not data:" in l)
        upper_idx = next(i for i, l in enumerate(lines) if "data.upper()" in l)
        assert guard_idx < upper_idx

    def test_guard_apres_docstring(self):
        """Le guard doit être inséré après la docstring, pas avant."""
        actions = [TransformAction(
            type=TransformType.INSERT_EARLY_RETURN,
            target_class="MyAgent",
            target_method="process",
            code='if data is None:\n    return None',
        )]
        result = CodeSmith.apply(SIMPLE_CLASS, actions)
        assert result.success
        lines = result.modified_source.split("\n")
        doc_idx = next(i for i, l in enumerate(lines) if "Traite les données" in l)
        guard_idx = next(i for i, l in enumerate(lines) if "if data is None:" in l)
        assert guard_idx > doc_idx

    def test_idempotent(self):
        actions = [TransformAction(
            type=TransformType.INSERT_EARLY_RETURN,
            target_class="MyAgent",
            target_method="process",
            code='if not data:\n    return ""',
        )]
        r1 = CodeSmith.apply(SIMPLE_CLASS, actions)
        r2 = CodeSmith.apply(r1.modified_source, actions)
        assert r2.success
        assert r2.modified_source.count("if not data:") == 1


# =====================================================================
# Tests INSERT_BEFORE_RETURN
# =====================================================================

class TestInsertBeforeReturn:
    def test_insertion_avant_return(self):
        actions = [TransformAction(
            type=TransformType.INSERT_BEFORE_RETURN,
            target_class="MyAgent",
            target_method="process",
            code='logger.info(f"result={result}")',
        )]
        result = CodeSmith.apply(SIMPLE_CLASS, actions)
        assert result.success
        assert 'logger.info(f"result={result}")' in result.modified_source
        lines = result.modified_source.split("\n")
        log_idx = next(i for i, l in enumerate(lines) if "logger.info" in l and "result=" in l)
        ret_idx = next(i for i, l in enumerate(lines) if l.strip() == "return result")
        assert log_idx < ret_idx
        ast.parse(result.modified_source)


# =====================================================================
# Tests REPLACE_METHOD_BODY
# =====================================================================

class TestReplaceMethodBody:
    def test_remplace_body_garde_def_et_docstring(self):
        actions = [TransformAction(
            type=TransformType.REPLACE_METHOD_BODY,
            target_class="MyAgent",
            target_method="helper",
            code='return "replaced"',
        )]
        result = CodeSmith.apply(SIMPLE_CLASS, actions)
        assert result.success
        assert 'return "replaced"' in result.modified_source
        assert "def helper(self):" in result.modified_source
        ast.parse(result.modified_source)

    def test_remplace_body_avec_docstring(self):
        actions = [TransformAction(
            type=TransformType.REPLACE_METHOD_BODY,
            target_class="MyAgent",
            target_method="process",
            code='return data.lower()',
        )]
        result = CodeSmith.apply(SIMPLE_CLASS, actions)
        assert result.success
        assert "return data.lower()" in result.modified_source
        # La docstring doit être préservée
        assert '"""Traite les données."""' in result.modified_source
        # L'ancien code ne doit plus être là
        assert "data.upper()" not in result.modified_source
        ast.parse(result.modified_source)


# =====================================================================
# Tests spec_to_actions et registre
# =====================================================================

class TestSpecToActions:
    def test_spec_supportee_retourne_actions(self):
        """Vérifie qu'une spec enregistrée retourne des actions."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="PERF-003", name="test", description="test",
            category="performance", target_file="core/base_agent.py",
            target_method="_evaluate_complexity", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, SIMPLE_CLASS)
        assert actions is not None
        assert len(actions) >= 1

    def test_spec_non_supportee_retourne_none(self):
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="UNKNOWN-999", name="test", description="test",
            category="test", target_file="test.py",
            target_method="test", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, SIMPLE_CLASS)
        assert actions is None

    def test_get_supported_specs(self):
        supported = get_supported_specs()
        assert len(supported) >= 33  # 18 Phase A + 15 Phase B
        assert "PERF-003" in supported
        assert "RES-003" in supported
        assert "SEC-001" in supported
        # Phase B
        assert "PERF-002" in supported
        assert "RES-002" in supported
        assert "INT-004" in supported
        assert "SEC-003" in supported


# =====================================================================
# Tests d'intégration actions multiples
# =====================================================================

class TestMultipleActions:
    def test_import_plus_methode(self):
        """Plusieurs actions chaînées."""
        actions = [
            TransformAction(type=TransformType.ADD_IMPORT, import_line="import time"),
            TransformAction(
                type=TransformType.ADD_METHOD,
                target_class="MyAgent",
                code='def elapsed(self):\n    return time.time()',
            ),
        ]
        result = CodeSmith.apply(SIMPLE_CLASS, actions)
        assert result.success
        assert result.actions_applied == 2
        assert "import time" in result.modified_source
        assert "def elapsed(self):" in result.modified_source
        ast.parse(result.modified_source)

    def test_attribut_plus_guard(self):
        actions = [
            TransformAction(
                type=TransformType.ADD_CLASS_ATTRIBUTE,
                target_class="MyAgent",
                code="_processed_count = 0",
            ),
            TransformAction(
                type=TransformType.INSERT_EARLY_RETURN,
                target_class="MyAgent",
                target_method="process",
                code='if data is None:\n    return ""',
            ),
        ]
        result = CodeSmith.apply(SIMPLE_CLASS, actions)
        assert result.success
        assert result.actions_applied == 2
        ast.parse(result.modified_source)


# =====================================================================
# Tests d'intégration sur les vrais fichiers du projet
# =====================================================================

class TestIntegrationFichiersReels:
    """Tests sur les vrais fichiers du projet (lecture seule)."""

    @pytest.fixture
    def base_agent_source(self):
        path = os.path.join(project_root, "core", "base_agent.py")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    @pytest.fixture
    def router_source(self):
        path = os.path.join(project_root, "core", "router.py")
        if not os.path.exists(path):
            pytest.skip("core/router.py non trouvé")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def test_perf003_sur_base_agent(self, base_agent_source):
        """PERF-003 : insert early return dans _evaluate_complexity."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="PERF-003", name="Short-circuit complexité", description="",
            category="performance", target_file="core/base_agent.py",
            target_method="_evaluate_complexity", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, base_agent_source)
        assert actions is not None
        result = CodeSmith.apply(base_agent_source, actions)
        assert result.success, f"Erreur: {result.error}"
        assert result.actions_applied >= 1
        ast.parse(result.modified_source)
        # Vérifier que le guard est bien présent
        assert "if len(prompt) < 100:" in result.modified_source

    def test_res003_sur_base_agent(self, base_agent_source):
        """RES-003 : wrap try/except sur remember()."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="RES-003", name="Dégradation gracieuse mémoire", description="",
            category="resilience", target_file="core/base_agent.py",
            target_method="remember", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, base_agent_source)
        assert actions is not None
        result = CodeSmith.apply(base_agent_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)

    def test_int001_sur_base_agent(self, base_agent_source):
        """INT-001 : ajout méthode _trim_context."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="INT-001", name="Trimming contexte RAG", description="",
            category="intelligence", target_file="core/base_agent.py",
            target_method="generate_content", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, base_agent_source)
        assert actions is not None
        result = CodeSmith.apply(base_agent_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_trim_context" in result.modified_source

    def test_obs002_sur_base_agent(self, base_agent_source):
        """OBS-002 : compteur Cloud par agent."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="OBS-002", name="Compteur Cloud", description="",
            category="observability", target_file="core/base_agent.py",
            target_method="generate_content", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, base_agent_source)
        assert actions is not None
        result = CodeSmith.apply(base_agent_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "get_cloud_usage" in result.modified_source

    def test_perf001_sur_router(self, router_source):
        """PERF-001 : cache LRU Router."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="PERF-001", name="Cache LRU Router", description="",
            category="performance", target_file="core/router.py",
            target_method="classify_intent", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, router_source)
        assert actions is not None
        result = CodeSmith.apply(router_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_get_cached_intent" in result.modified_source
        assert "_set_cached_intent" in result.modified_source

    # --- Fixtures fichiers additionnels ---

    @pytest.fixture
    def strategist_source(self):
        path = os.path.join(project_root, "Agents", "strategist_agent.py")
        if not os.path.exists(path):
            pytest.skip("Agents/strategist_agent.py non trouvé")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    @pytest.fixture
    def researcher_source(self):
        path = os.path.join(project_root, "Agents", "researcher_agent.py")
        if not os.path.exists(path):
            pytest.skip("Agents/researcher_agent.py non trouvé")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    @pytest.fixture
    def strategic_journal_source(self):
        path = os.path.join(project_root, "core", "strategic_journal.py")
        if not os.path.exists(path):
            pytest.skip("core/strategic_journal.py non trouvé")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    @pytest.fixture
    def evolution_source(self):
        path = os.path.join(project_root, "Agents", "evolution_agent.py")
        if not os.path.exists(path):
            pytest.skip("Agents/evolution_agent.py non trouvé")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    @pytest.fixture
    def writer_source(self):
        path = os.path.join(project_root, "Agents", "writer_agent.py")
        if not os.path.exists(path):
            pytest.skip("Agents/writer_agent.py non trouvé")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    @pytest.fixture
    def orchestrator_source(self):
        path = os.path.join(project_root, "core", "orchestrator.py")
        if not os.path.exists(path):
            pytest.skip("core/orchestrator.py non trouvé")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    @pytest.fixture
    def factory_source(self):
        path = os.path.join(project_root, "Agents", "factory_agent.py")
        if not os.path.exists(path):
            pytest.skip("Agents/factory_agent.py non trouvé")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    @pytest.fixture
    def psyche_source(self):
        path = os.path.join(project_root, "core", "psyche.py")
        if not os.path.exists(path):
            pytest.skip("core/psyche.py non trouvé")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    # --- Tests d'intégration specs sur fichiers réels ---

    def test_perf008_sur_strategist(self, strategist_source):
        """PERF-008 : cache listing projet Strategist."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="PERF-008", name="Cache listing projet", description="",
            category="performance", target_file="Agents/strategist_agent.py",
            target_method="", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, strategist_source)
        assert actions is not None
        result = CodeSmith.apply(strategist_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_project_files_cache" in result.modified_source
        assert "_PROJECT_FILES_TTL" in result.modified_source

    def test_perf010_sur_researcher(self, researcher_source):
        """PERF-010 : skip binaires Dropzone."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="PERF-010", name="Skip binaires", description="",
            category="performance", target_file="Agents/researcher_agent.py",
            target_method="", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, researcher_source)
        assert actions is not None
        result = CodeSmith.apply(researcher_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_is_binary" in result.modified_source

    def test_res005_sur_base_agent(self, base_agent_source):
        """RES-005 : heartbeat Ollama."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="RES-005", name="Heartbeat Ollama", description="",
            category="resilience", target_file="core/base_agent.py",
            target_method="", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, base_agent_source)
        assert actions is not None
        result = CodeSmith.apply(base_agent_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_OLLAMA_HEALTH_URL" in result.modified_source
        assert "_check_ollama_heartbeat" in result.modified_source

    def test_res008_sur_strategic_journal(self, strategic_journal_source):
        """RES-008 : auto-trim journal stratégique."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="RES-008", name="Auto-trim journal", description="",
            category="resilience", target_file="core/strategic_journal.py",
            target_method="append_council_entry", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, strategic_journal_source)
        assert actions is not None
        result = CodeSmith.apply(strategic_journal_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_MAX_JOURNAL_ENTRIES" in result.modified_source
        assert "_auto_trim" in result.modified_source

    def test_int005_sur_evolution(self, evolution_source):
        """INT-005 : résumé échecs CI/CD."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="INT-005", name="Résumé échecs CI/CD", description="",
            category="intelligence", target_file="Agents/evolution_agent.py",
            target_method="", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, evolution_source)
        assert actions is not None
        result = CodeSmith.apply(evolution_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_get_recent_failures" in result.modified_source

    def test_int007_sur_writer(self, writer_source):
        """INT-007 : métriques lisibilité Writer."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="INT-007", name="Métriques lisibilité", description="",
            category="intelligence", target_file="Agents/writer_agent.py",
            target_method="", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, writer_source)
        assert actions is not None
        result = CodeSmith.apply(writer_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_compute_readability" in result.modified_source

    def test_obs003_sur_orchestrator(self, orchestrator_source):
        """OBS-003 : profiling pipelines."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="OBS-003", name="Profiling pipelines", description="",
            category="observability", target_file="core/orchestrator.py",
            target_method="dispatch_task", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, orchestrator_source)
        assert actions is not None
        result = CodeSmith.apply(orchestrator_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_publish_pipeline_timing" in result.modified_source

    def test_sec001_sur_factory(self, factory_source):
        """SEC-001 : sanitization output Factory."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="SEC-001", name="Sanitization output", description="",
            category="security", target_file="Agents/factory_agent.py",
            target_method="", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, factory_source)
        assert actions is not None
        result = CodeSmith.apply(factory_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_DANGEROUS_PATTERNS" in result.modified_source
        assert "_scan_code_safety" in result.modified_source

    def test_sec002_sur_router(self, router_source):
        """SEC-002 : détection injection prompt."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="SEC-002", name="Détection injection", description="",
            category="security", target_file="core/router.py",
            target_method="classify_intent", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, router_source)
        assert actions is not None
        result = CodeSmith.apply(router_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_INJECTION_PATTERNS" in result.modified_source
        assert "_check_injection" in result.modified_source

    def test_sec005_sur_researcher(self, researcher_source):
        """SEC-005 : whitelist extensions Dropzone."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="SEC-005", name="Whitelist extensions", description="",
            category="security", target_file="Agents/researcher_agent.py",
            target_method="", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, researcher_source)
        assert actions is not None
        result = CodeSmith.apply(researcher_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_ALLOWED_EXTENSIONS" in result.modified_source
        assert "_is_allowed_file" in result.modified_source

    def test_mem005_sur_researcher(self, researcher_source):
        """MEM-005 : mémoire structurée Researcher."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="MEM-005", name="Mémoire structurée", description="",
            category="memory", target_file="Agents/researcher_agent.py",
            target_method="", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, researcher_source)
        assert actions is not None
        result = CodeSmith.apply(researcher_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_format_research_memory" in result.modified_source

    def test_mem006_sur_psyche(self, psyche_source):
        """MEM-006 : snapshot traits Psyche."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="MEM-006", name="Snapshot traits", description="",
            category="memory", target_file="core/psyche.py",
            target_method="save", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, psyche_source)
        assert actions is not None
        result = CodeSmith.apply(psyche_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_MAX_TRAIT_HISTORY" in result.modified_source
        assert "_take_trait_snapshot" in result.modified_source

    def test_resultat_passe_ast_parse(self, base_agent_source):
        """Vérifie que toutes les specs supportées produisent du code valide sur base_agent.py."""
        from core.evolution_catalog import ImprovementSpec
        # Tester seulement les specs ciblant base_agent
        base_agent_specs = ["PERF-003", "PERF-004", "RES-003", "RES-005", "INT-001", "OBS-002"]
        for spec_id in base_agent_specs:
            spec = ImprovementSpec(
                id=spec_id, name="test", description="",
                category="test", target_file="core/base_agent.py",
                target_method="", difficulty=1,
                code_template="", validation="",
            )
            actions = spec_to_actions(spec, base_agent_source)
            if actions is None:
                continue
            result = CodeSmith.apply(base_agent_source, actions)
            assert result.success, f"{spec_id}: {result.error}"
            try:
                ast.parse(result.modified_source)
            except SyntaxError as e:
                pytest.fail(f"{spec_id} produit du code avec syntaxe invalide: {e}")

    # --- Fixtures fichiers additionnels Phase B ---

    @pytest.fixture
    def infra_source(self):
        path = os.path.join(project_root, "Agents", "infra_agent.py")
        if not os.path.exists(path):
            pytest.skip("Agents/infra_agent.py non trouvé")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    @pytest.fixture
    def coder_source(self):
        path = os.path.join(project_root, "Agents", "coder_agent.py")
        if not os.path.exists(path):
            pytest.skip("Agents/coder_agent.py non trouvé")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    @pytest.fixture
    def talk_logger_source(self):
        path = os.path.join(project_root, "core", "talk_logger.py")
        if not os.path.exists(path):
            pytest.skip("core/talk_logger.py non trouvé")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    @pytest.fixture
    def dropzone_source(self):
        path = os.path.join(project_root, "core", "dropzone_pipeline.py")
        if not os.path.exists(path):
            pytest.skip("core/dropzone_pipeline.py non trouvé")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    @pytest.fixture
    def self_awareness_source(self):
        path = os.path.join(project_root, "core", "self_awareness.py")
        if not os.path.exists(path):
            pytest.skip("core/self_awareness.py non trouvé")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    # ===================================================================
    # Tests d'intégration Phase B (15 specs)
    # ===================================================================

    def test_perf002_sur_base_agent(self, base_agent_source):
        """PERF-002 : timeout Ollama — constante + méthode wrapper."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="PERF-002", name="Timeout Ollama", description="",
            category="performance", target_file="core/base_agent.py",
            target_method="_call_ollama", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, base_agent_source)
        assert actions is not None
        result = CodeSmith.apply(base_agent_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_OLLAMA_TIMEOUT" in result.modified_source
        assert "_call_ollama_with_timeout" in result.modified_source

    def test_perf009_sur_researcher(self, researcher_source):
        """PERF-009 : lazy init WebSurfer Researcher."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="PERF-009", name="Lazy init WebSurfer", description="",
            category="performance", target_file="Agents/researcher_agent.py",
            target_method="__init__", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, researcher_source)
        assert actions is not None
        result = CodeSmith.apply(researcher_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_get_surfer" in result.modified_source
        assert "_get_ingestor" in result.modified_source

    def test_res002_sur_base_agent(self, base_agent_source):
        """RES-002 : circuit breaker Ollama."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="RES-002", name="Circuit breaker Ollama", description="",
            category="resilience", target_file="core/base_agent.py",
            target_method="_call_ollama", difficulty=2,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, base_agent_source)
        assert actions is not None
        result = CodeSmith.apply(base_agent_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_ollama_circuit_open" in result.modified_source
        assert "_check_ollama_circuit" in result.modified_source
        assert "_record_ollama_failure" in result.modified_source
        assert "_record_ollama_success" in result.modified_source

    def test_res006_sur_researcher(self, researcher_source):
        """RES-006 : fallback recherche locale Researcher."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="RES-006", name="Fallback recherche locale", description="",
            category="resilience", target_file="Agents/researcher_agent.py",
            target_method="process_task", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, researcher_source)
        assert actions is not None
        result = CodeSmith.apply(researcher_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_safe_web_search" in result.modified_source

    def test_res007_sur_infra(self, infra_source):
        """RES-007 : timeout sub-checks Infra."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="RES-007", name="Timeout sub-checks Infra", description="",
            category="resilience", target_file="Agents/infra_agent.py",
            target_method="_perform_health_check", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, infra_source)
        assert actions is not None
        result = CodeSmith.apply(infra_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_safe_check" in result.modified_source

    def test_int004_sur_base_agent(self, base_agent_source):
        """INT-004 : prompt adaptatif PSYCHE."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="INT-004", name="Prompt adaptatif PSYCHE", description="",
            category="intelligence", target_file="core/base_agent.py",
            target_method="generate_content", difficulty=2,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, base_agent_source)
        assert actions is not None
        result = CodeSmith.apply(base_agent_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_get_psyche_context" in result.modified_source

    def test_int006_sur_coder(self, coder_source):
        """INT-006 : post-filtre code Coder."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="INT-006", name="Post-filtre code Coder", description="",
            category="intelligence", target_file="Agents/coder_agent.py",
            target_method="process_task", difficulty=2,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, coder_source)
        assert actions is not None
        result = CodeSmith.apply(coder_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_has_code_structure" in result.modified_source

    def test_int008_sur_infra(self, infra_source):
        """INT-008 : sonde latence Ollama Infra."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="INT-008", name="Sonde latence Ollama", description="",
            category="intelligence", target_file="Agents/infra_agent.py",
            target_method="_perform_health_check", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, infra_source)
        assert actions is not None
        result = CodeSmith.apply(infra_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_probe_ollama_latency" in result.modified_source

    def test_int009_sur_psyche(self, psyche_source):
        """INT-009 : évolution traits par résultat Psyche."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="INT-009", name="Évolution traits", description="",
            category="intelligence", target_file="core/psyche.py",
            target_method="_on_routine_complete", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, psyche_source)
        assert actions is not None
        result = CodeSmith.apply(psyche_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "apply_routine_result" in result.modified_source

    def test_obs005_sur_talk_logger(self, talk_logger_source):
        """OBS-005 : talk log structuré JSON."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="OBS-005", name="Talk log JSON", description="",
            category="observability", target_file="core/talk_logger.py",
            target_method="_format_entry", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, talk_logger_source)
        assert actions is not None
        result = CodeSmith.apply(talk_logger_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_format_structured_entry" in result.modified_source

    def test_obs006_sur_strategic_journal(self, strategic_journal_source):
        """OBS-006 : dédup recherches journal."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="OBS-006", name="Dédup recherches", description="",
            category="observability", target_file="core/strategic_journal.py",
            target_method="append_research_entry", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, strategic_journal_source)
        assert actions is not None
        result = CodeSmith.apply(strategic_journal_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_is_duplicate_topic" in result.modified_source

    def test_obs007_sur_self_awareness(self, self_awareness_source):
        """OBS-007 : détection cycles debug."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="OBS-007", name="Détection cycles debug", description="",
            category="observability", target_file="core/self_awareness.py",
            target_method="detect_patterns", difficulty=2,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, self_awareness_source)
        assert actions is not None
        result = CodeSmith.apply(self_awareness_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_detect_debug_loops" in result.modified_source

    def test_obs008_sur_dropzone(self, dropzone_source):
        """OBS-008 : métriques processing Dropzone."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="OBS-008", name="Métriques Dropzone", description="",
            category="observability", target_file="core/dropzone_pipeline.py",
            target_method="run", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, dropzone_source)
        assert actions is not None
        result = CodeSmith.apply(dropzone_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_compute_dropzone_stats" in result.modified_source

    def test_sec003_sur_orchestrator(self, orchestrator_source):
        """SEC-003 : rate limit par agent."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="SEC-003", name="Rate limit", description="",
            category="security", target_file="core/orchestrator.py",
            target_method="dispatch_task", difficulty=2,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, orchestrator_source)
        assert actions is not None
        result = CodeSmith.apply(orchestrator_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_dispatch_counts" in result.modified_source
        assert "_check_rate_limit" in result.modified_source

    def test_sec004_sur_coder(self, coder_source):
        """SEC-004 : sanitization secrets Coder."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="SEC-004", name="Sanitization secrets", description="",
            category="security", target_file="Agents/coder_agent.py",
            target_method="process_task", difficulty=1,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, coder_source)
        assert actions is not None
        result = CodeSmith.apply(coder_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "_SECRET_PATTERNS" in result.modified_source
        assert "_sanitize_secrets" in result.modified_source

    def test_mem004_sur_base_agent(self, base_agent_source):
        """MEM-004 : mémoire partagée inter-agents."""
        from core.evolution_catalog import ImprovementSpec
        spec = ImprovementSpec(
            id="MEM-004", name="Mémoire partagée", description="",
            category="memory", target_file="core/base_agent.py",
            target_method="remember", difficulty=2,
            code_template="", validation="",
        )
        actions = spec_to_actions(spec, base_agent_source)
        assert actions is not None
        result = CodeSmith.apply(base_agent_source, actions)
        assert result.success, f"Erreur: {result.error}"
        ast.parse(result.modified_source)
        assert "share_insight" in result.modified_source
        assert "recall_shared" in result.modified_source

    def test_phase_b_toutes_specs_base_agent(self, base_agent_source):
        """Vérifie que les specs Phase B ciblant base_agent produisent du code valide."""
        from core.evolution_catalog import ImprovementSpec
        phase_b_base = ["PERF-002", "RES-002", "INT-004", "MEM-004"]
        for spec_id in phase_b_base:
            spec = ImprovementSpec(
                id=spec_id, name="test", description="",
                category="test", target_file="core/base_agent.py",
                target_method="", difficulty=1,
                code_template="", validation="",
            )
            actions = spec_to_actions(spec, base_agent_source)
            assert actions is not None, f"{spec_id}: handler retourne None"
            result = CodeSmith.apply(base_agent_source, actions)
            assert result.success, f"{spec_id}: {result.error}"
            try:
                ast.parse(result.modified_source)
            except SyntaxError as e:
                pytest.fail(f"{spec_id} produit du code avec syntaxe invalide: {e}")
