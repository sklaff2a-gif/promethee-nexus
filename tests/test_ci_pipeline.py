import pytest
import asyncio
import os
import shutil
from unittest.mock import AsyncMock, MagicMock, patch

from core.ci_pipeline import (
    extract_python_code,
    _extract_api_signatures,
    _validate_test_imports,
    _slugify_filename,
    _build_import_hint,
    _rollback,
    _remember_failure,
    _remember_success,
    _recall_failures,
    _recall_successes,
    run_pipeline,
    run_existing_tests_for_file,
    _on_artifact_created,
    _SUPERVISED_TEST_MAP,
    PROJECT_ROOT,
)
from core.event_bus.bus import bus


# --- Tests extract_python_code ---

class TestExtractPythonCode:

    def test_bloc_markdown_python(self):
        text = "Voici les tests :\n```python\nimport pytest\ndef test_ok():\n    assert True\n```\nFin."
        result = extract_python_code(text)
        assert result == "import pytest\ndef test_ok():\n    assert True"

    def test_bloc_markdown_sans_lang(self):
        text = "```\nimport os\nprint(os.getcwd())\n```"
        result = extract_python_code(text)
        assert result == "import os\nprint(os.getcwd())"

    def test_multiple_blocs_prend_le_dernier(self):
        text = "```python\nprint('premier')\n```\nBla bla\n```python\nimport pytest\ndef test_final():\n    assert 1\n```"
        result = extract_python_code(text)
        assert "test_final" in result
        assert "premier" not in result

    def test_heuristique_fallback(self):
        text = "import os\nfrom pathlib import Path\ndef hello():\n    return 42"
        result = extract_python_code(text)
        assert result is not None
        assert "import os" in result

    def test_pas_de_code_retourne_none(self):
        text = "Ceci est un texte normal sans code Python."
        result = extract_python_code(text)
        assert result is None


# --- Tests _slugify_filename ---

class TestSlugifyFilename:

    def test_nom_simple(self):
        assert _slugify_filename("coder_agent.py") == "coder_agent"

    def test_chemin_imbrique(self):
        assert _slugify_filename("Agents/coder_agent.py") == "coder_agent"

    def test_chemin_windows(self):
        assert _slugify_filename("core\\utils\\helper.py") == "helper"


# --- Tests _build_import_hint ---

class TestBuildImportHint:

    def test_fichier_dans_agents(self):
        hint = _build_import_hint(
            "factory_agent.py",
            "C:/MesProjets/PROMETHEE/Agents/factory_agent.py",
            "class DivineFactory:\n    pass\ndef create_file():\n    pass\n"
        )
        assert "Agents.factory_agent" in hint
        assert "DivineFactory" in hint
        assert "create_file" in hint
        assert "UNIQUEMENT" in hint

    def test_fichier_dans_core(self):
        hint = _build_import_hint(
            "router.py",
            "C:/MesProjets/PROMETHEE/core/router.py",
            "class RouterAgent:\n    pass\n"
        )
        assert "core.router" in hint
        assert "RouterAgent" in hint

    def test_fichier_racine(self):
        """Fichier à la racine du projet, pas dans un package connu."""
        hint = _build_import_hint(
            "merchant_code.py",
            "C:/MesProjets/PROMETHEE/merchant_code.py",
            "def calculate_profit():\n    return 42\n"
        )
        assert "merchant_code" in hint
        assert "N'invente PAS" in hint

    def test_chemin_windows_backslash(self):
        hint = _build_import_hint(
            "coder_agent.py",
            "C:\\MesProjets\\PROMETHEE\\Agents\\coder_agent.py",
            "class DivineCoder:\n    pass\n"
        )
        assert "Agents.coder_agent" in hint
        assert "DivineCoder" in hint

    def test_exclut_fonctions_privees(self):
        hint = _build_import_hint(
            "utils.py",
            "core/utils.py",
            "def public_func():\n    pass\ndef _private_func():\n    pass\n"
        )
        assert "public_func" in hint
        assert "_private_func" not in hint


# --- Tests anti-boucle ---

class TestAntiLoopProtection:

    @pytest.mark.asyncio
    async def test_fichier_dans_tests_ignore(self):
        """Les fichiers dans tests/ ne déclenchent pas le pipeline."""
        with patch("core.ci_pipeline.run_pipeline", new_callable=AsyncMock) as mock_run:
            await _on_artifact_created({
                "filepath": "tests/auto/test_foo.py",
                "filename": "test_foo.py"
            })
            mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_fichier_non_py_ignore(self):
        """Les fichiers non-.py ne déclenchent pas le pipeline."""
        with patch("core.ci_pipeline.run_pipeline", new_callable=AsyncMock) as mock_run:
            await _on_artifact_created({
                "filepath": "docs/readme.md",
                "filename": "readme.md"
            })
            mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_fichier_py_valide_declenche(self):
        """Un .py hors tests/ déclenche le pipeline."""
        with patch("core.ci_pipeline.run_pipeline", new_callable=AsyncMock) as mock_run:
            await _on_artifact_created({
                "filepath": "Agents/new_agent.py",
                "filename": "new_agent.py"
            })
            # run_pipeline est lancé via asyncio.create_task, on attend un tick
            await asyncio.sleep(0.05)
            mock_run.assert_called_once()


# --- Tests _rollback ---

class TestRollback:

    def test_rollback_avec_bak(self, tmp_path):
        original = tmp_path / "module.py"
        bak = tmp_path / "module.py.bak"

        original.write_text("code modifié")
        bak.write_text("code original")

        result = _rollback(str(original))
        assert result is True
        assert original.read_text() == "code original"

    def test_rollback_nouveau_fichier_supprime(self, tmp_path):
        """Fichier nouveau (pas de .bak) : supprimé au lieu de laisser un orphelin."""
        original = tmp_path / "module.py"
        original.write_text("code défaillant")

        result = _rollback(str(original))
        assert result is True
        assert not original.exists()

    def test_rollback_fichier_inexistant(self, tmp_path):
        result = _rollback(str(tmp_path / "fantome.py"))
        assert result is False


# --- Tests run_pipeline ---

class TestRunPipeline:

    @pytest.fixture
    def setup_agents(self):
        """Configure les agents mockés dans orchestrator.agents."""
        coder = AsyncMock()
        coder.has_memory = True
        coder.remember = MagicMock()
        coder.recall = MagicMock(return_value="")
        architect = AsyncMock()
        strategist = MagicMock()
        strategist.has_memory = True
        strategist.remember = MagicMock()
        strategist.recall = MagicMock(return_value="")

        agents = {
            "coder": coder,
            "architect": architect,
            "strategist": strategist,
        }
        return agents, coder, architect, strategist

    @pytest.mark.asyncio
    async def test_pipeline_succes_complet(self, tmp_path, setup_agents):
        agents, coder, architect, strategist = setup_agents

        # Fichier source
        src = tmp_path / "module.py"
        src.write_text("def hello():\n    return 42\n")

        # Coder génère des tests
        coder.generate_content = AsyncMock(return_value=(
            "```python\nimport pytest\ndef test_hello():\n    assert True\n```"
        ))

        # Architect valide
        architect.generate_content = AsyncMock(return_value="VALIDÉ - Code propre et fonctionnel")

        # Pytest réussit
        auto_dir = tmp_path / "auto"

        with patch("core.ci_pipeline.orchestrator") as mock_orch, \
             patch("core.ci_pipeline._run_pytest", new_callable=AsyncMock, return_value=(True, "1 passed")), \
             patch("core.ci_pipeline.AUTO_TESTS_DIR", str(auto_dir)), \
             patch("core.ci_pipeline.sys") as mock_sys:
            mock_orch.agents = agents
            await run_pipeline("module.py", str(src))

        strategist.remember.assert_called_once()

    @pytest.mark.asyncio
    async def test_pipeline_rollback_tests_echoues(self, tmp_path, setup_agents):
        agents, coder, architect, strategist = setup_agents

        src = tmp_path / "module.py"
        src.write_text("def broken():\n    pass\n")
        bak = tmp_path / "module.py.bak"
        bak.write_text("def original():\n    pass\n")

        coder.generate_content = AsyncMock(return_value=(
            "```python\ndef test_x():\n    assert True\n```"
        ))

        auto_dir = tmp_path / "auto"

        with patch("core.ci_pipeline.orchestrator") as mock_orch, \
             patch("core.ci_pipeline._run_pytest", new_callable=AsyncMock, return_value=(False, "FAILED")), \
             patch("core.ci_pipeline.AUTO_TESTS_DIR", str(auto_dir)):
            mock_orch.agents = agents
            await run_pipeline("module.py", str(src))

        # Rollback effectué
        assert src.read_text() == "def original():\n    pass\n"

    @pytest.mark.asyncio
    async def test_pipeline_rollback_architect_refuse(self, tmp_path, setup_agents):
        agents, coder, architect, strategist = setup_agents

        src = tmp_path / "module.py"
        src.write_text("def risky():\n    pass\n")
        bak = tmp_path / "module.py.bak"
        bak.write_text("def safe():\n    pass\n")

        coder.generate_content = AsyncMock(return_value=(
            "```python\ndef test_x():\n    assert True\n```"
        ))
        architect.generate_content = AsyncMock(return_value="REFUSÉ - Vulnérabilité détectée")

        auto_dir = tmp_path / "auto"

        with patch("core.ci_pipeline.orchestrator") as mock_orch, \
             patch("core.ci_pipeline._run_pytest", new_callable=AsyncMock, return_value=(True, "1 passed")), \
             patch("core.ci_pipeline.AUTO_TESTS_DIR", str(auto_dir)):
            mock_orch.agents = agents
            await run_pipeline("module.py", str(src))

        # Rollback effectué
        assert src.read_text() == "def safe():\n    pass\n"

    @pytest.mark.asyncio
    async def test_pipeline_fichier_introuvable(self, tmp_path):
        """Pipeline publie un échec si le fichier n'existe pas."""
        events = []

        async def capture(data):
            events.append(data)

        bus.subscribe("CI_PIPELINE_RESULT", capture)

        await run_pipeline("fantome.py", str(tmp_path / "fantome.py"))

        # Laisser le bus traiter
        await asyncio.sleep(0.05)
        assert any(not e.get("success", True) for e in events)


# --- Tests mémoire CI/CD ---

class TestMemoryFailure:

    def test_remember_failure_appelle_coder_remember(self):
        """Un échec est archivé dans la collection ci_failures via le coder."""
        coder = MagicMock()
        coder.has_memory = True
        coder.remember = MagicMock()

        with patch("core.ci_pipeline.orchestrator") as mock_orch:
            mock_orch.agents = {"coder": coder}
            _remember_failure("module.py", "def broken(): pass", "test_execution", "AssertionError")

        coder.remember.assert_called_once()
        call_args = coder.remember.call_args
        assert "ÉCHEC" in call_args[0][0]
        assert "module.py" in call_args[0][0]
        assert "test_execution" in call_args[0][0]
        assert call_args[0][1]["outcome"] == "failure"
        assert call_args[0][2] == "ci_failures"

    def test_remember_failure_sans_coder_ne_crash_pas(self):
        """Pas de crash si le coder n'est pas disponible."""
        with patch("core.ci_pipeline.orchestrator") as mock_orch:
            mock_orch.agents = {}
            _remember_failure("x.py", "code", "step", "err")  # Ne doit pas lever d'exception

    def test_remember_failure_sans_memoire_ne_crash_pas(self):
        """Pas de crash si le coder n'a pas de mémoire."""
        coder = MagicMock()
        coder.has_memory = False
        with patch("core.ci_pipeline.orchestrator") as mock_orch:
            mock_orch.agents = {"coder": coder}
            _remember_failure("x.py", "code", "step", "err")
        coder.remember.assert_not_called()


class TestMemorySuccess:

    def test_remember_success_appelle_strategist_remember(self):
        """Un succès est archivé dans ci_successes via le strategist."""
        strategist = MagicMock()
        strategist.has_memory = True
        strategist.remember = MagicMock()

        with patch("core.ci_pipeline.orchestrator") as mock_orch:
            mock_orch.agents = {"strategist": strategist}
            _remember_success("module.py", "def ok(): pass", "def test_ok(): assert True", "VALIDÉ")

        strategist.remember.assert_called_once()
        call_args = strategist.remember.call_args
        assert "SUCCÈS" in call_args[0][0]
        assert "module.py" in call_args[0][0]
        assert "def ok(): pass" in call_args[0][0]
        assert "def test_ok()" in call_args[0][0]
        assert "VALIDÉ" in call_args[0][0]
        assert call_args[0][1]["outcome"] == "success"
        assert call_args[0][2] == "ci_successes"


class TestMemoryRecall:

    def test_recall_failures_interroge_ci_failures(self):
        """recall_failures consulte la bonne collection."""
        coder = MagicMock()
        coder.has_memory = True
        coder.recall = MagicMock(return_value="échec passé sur module similaire")

        with patch("core.ci_pipeline.orchestrator") as mock_orch:
            mock_orch.agents = {"coder": coder}
            result = _recall_failures("module.py", "def hello(): pass")

        coder.recall.assert_called_once()
        assert coder.recall.call_args[1]["collection"] == "ci_failures"
        assert result == "échec passé sur module similaire"

    def test_recall_successes_interroge_ci_successes(self):
        """recall_successes consulte la bonne collection."""
        strategist = MagicMock()
        strategist.has_memory = True
        strategist.recall = MagicMock(return_value="pattern validé")

        with patch("core.ci_pipeline.orchestrator") as mock_orch:
            mock_orch.agents = {"strategist": strategist}
            result = _recall_successes("module.py", "def hello(): pass")

        strategist.recall.assert_called_once()
        assert strategist.recall.call_args[1]["collection"] == "ci_successes"
        assert result == "pattern validé"

    def test_recall_sans_agent_retourne_vide(self):
        """Pas de crash si agent absent, retourne chaîne vide."""
        with patch("core.ci_pipeline.orchestrator") as mock_orch:
            mock_orch.agents = {}
            assert _recall_failures("x.py", "code") == ""
            assert _recall_successes("x.py", "code") == ""


class TestMemoryIntegrationPipeline:

    @pytest.fixture
    def setup_agents(self):
        coder = AsyncMock()
        coder.has_memory = True
        coder.remember = MagicMock()
        coder.recall = MagicMock(return_value="")
        architect = AsyncMock()
        strategist = MagicMock()
        strategist.has_memory = True
        strategist.remember = MagicMock()
        strategist.recall = MagicMock(return_value="")
        return {"coder": coder, "architect": architect, "strategist": strategist}, coder, architect, strategist

    @pytest.mark.asyncio
    async def test_echec_tests_memorise_failure(self, tmp_path, setup_agents):
        """Quand pytest échoue, l'échec est archivé dans ci_failures."""
        agents, coder, architect, strategist = setup_agents

        src = tmp_path / "bad.py"
        src.write_text("def broken(): pass\n")
        (tmp_path / "bad.py.bak").write_text("def original(): pass\n")

        coder.generate_content = AsyncMock(return_value="```python\ndef test_x(): assert True\n```")
        auto_dir = tmp_path / "auto"

        with patch("core.ci_pipeline.orchestrator") as mock_orch, \
             patch("core.ci_pipeline._run_pytest", new_callable=AsyncMock, return_value=(False, "FAILED")), \
             patch("core.ci_pipeline.AUTO_TESTS_DIR", str(auto_dir)):
            mock_orch.agents = agents
            await run_pipeline("bad.py", str(src))

        # L'échec doit être mémorisé
        coder.remember.assert_called_once()
        call_text = coder.remember.call_args[0][0]
        assert "ÉCHEC" in call_text
        assert "test_execution" in call_text

    @pytest.mark.asyncio
    async def test_succes_complet_memorise_riche(self, tmp_path, setup_agents):
        """Quand le pipeline réussit, le succès est archivé avec code+tests+verdict."""
        agents, coder, architect, strategist = setup_agents

        src = tmp_path / "good.py"
        src.write_text("def hello(): return 42\n")

        coder.generate_content = AsyncMock(return_value="```python\ndef test_hello(): assert True\n```")
        architect.generate_content = AsyncMock(return_value="VALIDÉ - Excellent code")
        auto_dir = tmp_path / "auto"

        with patch("core.ci_pipeline.orchestrator") as mock_orch, \
             patch("core.ci_pipeline._run_pytest", new_callable=AsyncMock, return_value=(True, "1 passed")), \
             patch("core.ci_pipeline.AUTO_TESTS_DIR", str(auto_dir)), \
             patch("core.ci_pipeline.sys"):
            mock_orch.agents = agents
            await run_pipeline("good.py", str(src))

        # Le succès doit être mémorisé avec contenu riche
        strategist.remember.assert_called_once()
        call_text = strategist.remember.call_args[0][0]
        assert "SUCCÈS" in call_text
        assert "def hello()" in call_text
        assert "def test_hello()" in call_text
        assert "VALIDÉ" in call_text

    @pytest.mark.asyncio
    async def test_recall_injecte_dans_prompt_coder(self, tmp_path, setup_agents):
        """Les souvenirs d'échecs passés sont injectés dans le prompt du Coder."""
        agents, coder, architect, strategist = setup_agents

        src = tmp_path / "module.py"
        src.write_text("def func(): pass\n")

        # Simuler un souvenir d'échec passé
        coder.recall = MagicMock(return_value="[CI/CD ÉCHEC] module.py — ImportError: no module named X")
        coder.generate_content = AsyncMock(return_value="```python\ndef test_func(): assert True\n```")
        architect.generate_content = AsyncMock(return_value="VALIDÉ")
        auto_dir = tmp_path / "auto"

        with patch("core.ci_pipeline.orchestrator") as mock_orch, \
             patch("core.ci_pipeline._run_pytest", new_callable=AsyncMock, return_value=(True, "1 passed")), \
             patch("core.ci_pipeline.AUTO_TESTS_DIR", str(auto_dir)), \
             patch("core.ci_pipeline.sys"):
            mock_orch.agents = agents
            await run_pipeline("module.py", str(src))

        # Vérifier que le prompt envoyé au coder contient l'avertissement
        prompt_sent = coder.generate_content.call_args[0][0]
        assert "ATTENTION" in prompt_sent
        assert "Échecs CI/CD passés" in prompt_sent


# --- Tests _extract_api_signatures ---

class TestExtractApiSignatures:

    def test_classe_avec_methodes(self):
        code = "class MyAgent:\n    def __init__(self, name):\n        pass\n    def run(self, task):\n        pass\n    def _private(self):\n        pass\n"
        result = _extract_api_signatures(code)
        assert "class MyAgent" in result
        assert "__init__(name)" in result
        assert "run(task)" in result
        assert "_private" not in result

    def test_fonction_top_level(self):
        code = "def hello(name):\n    return f'Hi {name}'\n\ndef _internal():\n    pass\n"
        result = _extract_api_signatures(code)
        assert "def hello(name)" in result
        assert "_internal" not in result

    def test_code_invalide_retourne_vide(self):
        result = _extract_api_signatures("def broken(:\n    pass")
        assert result == ""

    def test_code_vide(self):
        result = _extract_api_signatures("")
        assert result == ""


# --- Tests _validate_test_imports ---

class TestValidateTestImports:

    def test_imports_valides(self):
        source = "class OrderGenerator:\n    pass\ndef process():\n    pass\n"
        test = "from core.module import OrderGenerator, process\ndef test_ok(): pass\n"
        ok, err = _validate_test_imports(test, source, "core.module")
        assert ok is True

    def test_imports_invalides_detectes(self):
        source = "class OrderGenerator:\n    pass\n"
        test = "from core.module import ResourceManager\ndef test_ok(): pass\n"
        ok, err = _validate_test_imports(test, source, "core.module")
        assert ok is False
        assert "ResourceManager" in err

    def test_imports_autre_module_ignores(self):
        """Les imports vers d'autres modules (pas le source) sont ignorés."""
        source = "class MyClass:\n    pass\n"
        test = "import pytest\nfrom unittest.mock import MagicMock\nfrom core.module import MyClass\ndef test_ok(): pass\n"
        ok, err = _validate_test_imports(test, source, "core.module")
        assert ok is True

    def test_source_invalide_passe(self):
        """Si le source est invalide, on laisse pytest décider."""
        ok, err = _validate_test_imports("def test(): pass", "def broken(:", "mod")
        assert ok is True


# --- Tests constantes dans _extract_api_signatures ---

class TestApiSignaturesConstants:

    def test_constantes_upper_extraites(self):
        """Les constantes UPPER_CASE de module sont extraites."""
        code = "MAX_RETRIES = 5\nDEFAULT_TIMEOUT = 30\n\ndef run(): pass\n"
        result = _extract_api_signatures(code)
        assert "MAX_RETRIES" in result
        assert "DEFAULT_TIMEOUT" in result
        assert "def run()" in result

    def test_variables_lowercase_ignorees(self):
        """Les variables lowercase ne sont pas extraites."""
        code = "counter = 0\nlogger = logging.getLogger()\n\ndef func(): pass\n"
        result = _extract_api_signatures(code)
        assert "counter" not in result
        assert "logger" not in result


# --- Tests retry dans le pipeline ---

class TestPipelineRetry:

    @pytest.fixture
    def setup_retry(self, tmp_path):
        """Prépare un pipeline avec fichier source valide."""
        src = tmp_path / "module.py"
        src.write_text("class MyClass:\n    def run(self):\n        pass\n")
        auto_dir = tmp_path / "auto"

        coder = AsyncMock()
        coder.has_memory = True
        coder.remember = MagicMock()
        coder.recall = MagicMock(return_value="")
        architect = AsyncMock()
        strategist = MagicMock()
        strategist.has_memory = True
        strategist.recall = MagicMock(return_value="")
        agents = {"coder": coder, "architect": architect, "strategist": strategist}
        return src, auto_dir, agents, coder, architect

    @pytest.mark.asyncio
    async def test_retry_on_syntax_error(self, setup_retry):
        """Si la 1ère tentative a une SyntaxError, retry avec correction."""
        src, auto_dir, agents, coder, architect = setup_retry

        # 1ère tentative : SyntaxError, 2ème : OK
        coder.generate_content = AsyncMock(side_effect=[
            "```python\ndef test_broken( assert True\n```",  # SyntaxError
            "```python\ndef test_ok():\n    assert True\n```",  # OK
        ])
        architect.generate_content = AsyncMock(return_value="VALIDÉ")

        with patch("core.ci_pipeline.orchestrator") as mock_orch, \
             patch("core.ci_pipeline._run_pytest", new_callable=AsyncMock, return_value=(True, "1 passed")), \
             patch("core.ci_pipeline.AUTO_TESTS_DIR", str(auto_dir)), \
             patch("core.ci_pipeline.sys"):
            mock_orch.agents = agents
            await run_pipeline("module.py", str(src))

        # Le coder a été appelé 2 fois (retry)
        assert coder.generate_content.call_count == 2
        # Le 2ème prompt contient "CORRECTION"
        second_prompt = coder.generate_content.call_args_list[1][0][0]
        assert "CORRECTION" in second_prompt

    @pytest.mark.asyncio
    async def test_retry_on_bad_imports(self, setup_retry):
        """Si la 1ère tentative a des imports fantômes, retry avec correction."""
        src, auto_dir, agents, coder, architect = setup_retry

        # 1ère tentative : import invalide, 2ème : OK
        coder.generate_content = AsyncMock(side_effect=[
            "```python\nfrom core.module import FakeClass\ndef test_x(): pass\n```",
            "```python\nfrom core.module import MyClass\ndef test_ok():\n    assert True\n```",
        ])
        architect.generate_content = AsyncMock(return_value="VALIDÉ")

        # Écrire le fichier en chemin reconnaissable (core/)
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            core_dir = os.path.join(td, "core")
            os.makedirs(core_dir)
            src_file = os.path.join(core_dir, "module.py")
            with open(src_file, "w") as f:
                f.write("class MyClass:\n    def run(self):\n        pass\n")
            auto_dir_path = os.path.join(td, "auto")

            with patch("core.ci_pipeline.orchestrator") as mock_orch, \
                 patch("core.ci_pipeline._run_pytest", new_callable=AsyncMock, return_value=(True, "1 passed")), \
                 patch("core.ci_pipeline.AUTO_TESTS_DIR", auto_dir_path), \
                 patch("core.ci_pipeline.sys"):
                mock_orch.agents = agents
                await run_pipeline("module.py", src_file)

        assert coder.generate_content.call_count == 2
        second_prompt = coder.generate_content.call_args_list[1][0][0]
        assert "imports invalides" in second_prompt

    @pytest.mark.asyncio
    async def test_no_retry_when_first_attempt_ok(self, setup_retry):
        """Pas de retry quand la 1ère tentative réussit."""
        src, auto_dir, agents, coder, architect = setup_retry

        coder.generate_content = AsyncMock(
            return_value="```python\ndef test_ok():\n    assert True\n```"
        )
        architect.generate_content = AsyncMock(return_value="VALIDÉ")

        with patch("core.ci_pipeline.orchestrator") as mock_orch, \
             patch("core.ci_pipeline._run_pytest", new_callable=AsyncMock, return_value=(True, "1 passed")), \
             patch("core.ci_pipeline.AUTO_TESTS_DIR", str(auto_dir)), \
             patch("core.ci_pipeline.sys"):
            mock_orch.agents = agents
            await run_pipeline("module.py", str(src))

        # Le coder n'est appelé qu'une seule fois
        assert coder.generate_content.call_count == 1


# --- Tests prompt restructuré ---

class TestPromptStructure:

    @pytest.mark.asyncio
    async def test_prompt_contient_regles_strictes(self, tmp_path):
        """Le prompt envoyé au coder contient les RÈGLES STRICTES."""
        src = tmp_path / "mod.py"
        src.write_text("class Foo:\n    def bar(self): pass\n")
        auto_dir = tmp_path / "auto"

        coder = AsyncMock()
        coder.has_memory = True
        coder.remember = MagicMock()
        coder.recall = MagicMock(return_value="")
        coder.generate_content = AsyncMock(
            return_value="```python\ndef test_foo(): assert True\n```"
        )
        architect = AsyncMock()
        architect.generate_content = AsyncMock(return_value="VALIDÉ")
        strategist = MagicMock()
        strategist.has_memory = True
        strategist.recall = MagicMock(return_value="")

        with patch("core.ci_pipeline.orchestrator") as mock_orch, \
             patch("core.ci_pipeline._run_pytest", new_callable=AsyncMock, return_value=(True, "1 passed")), \
             patch("core.ci_pipeline.AUTO_TESTS_DIR", str(auto_dir)), \
             patch("core.ci_pipeline.sys"):
            mock_orch.agents = {"coder": coder, "architect": architect, "strategist": strategist}
            await run_pipeline("mod.py", str(src))

        prompt = coder.generate_content.call_args[0][0]
        assert "RÈGLES STRICTES" in prompt
        assert "MISSION" in prompt
        assert "N'invente AUCUNE" in prompt


# --- Tests run_existing_tests_for_file ---

class TestRunExistingTestsForFile:

    def test_supervised_test_map_has_entries(self):
        """Le mapping spécial contient au moins les fichiers documentés."""
        assert "core/vector_store.py" in _SUPERVISED_TEST_MAP
        assert "core/grimoire_writer.py" in _SUPERVISED_TEST_MAP
        assert "core/event_bus/bus.py" in _SUPERVISED_TEST_MAP

    @pytest.mark.asyncio
    async def test_run_existing_tests_for_base_agent(self):
        """Le mapping par défaut trouve test_base_agent.py pour core/base_agent.py."""
        test_file = os.path.join(PROJECT_ROOT, "tests", "test_base_agent.py")
        if not os.path.exists(test_file):
            pytest.skip("test_base_agent.py non trouvé")

        # Mock _run_pytest pour ne pas exécuter les vrais tests
        with patch("core.ci_pipeline._run_pytest", new_callable=AsyncMock,
                    return_value=(True, "46 passed")) as mock_pytest:
            ok, output = await run_existing_tests_for_file(
                os.path.join(PROJECT_ROOT, "core", "base_agent.py")
            )

        assert ok is True
        assert "46 passed" in output
        # Vérifie que le bon fichier de test a été appelé
        called_path = mock_pytest.call_args[0][0]
        assert "test_base_agent.py" in called_path

    @pytest.mark.asyncio
    async def test_run_existing_tests_no_tests_found(self, tmp_path):
        """Un fichier sans tests retourne (True, 'Aucun test...')."""
        fake_file = str(tmp_path / "core" / "totally_unknown_module.py")
        ok, output = await run_existing_tests_for_file(fake_file)
        assert ok is True
        assert "Aucun test" in output

    @pytest.mark.asyncio
    async def test_run_existing_tests_special_mapping(self):
        """Le mapping spécial pour vector_store.py trouve les bons fichiers."""
        test_file_1 = os.path.join(PROJECT_ROOT, "tests", "test_memory_health.py")
        if not os.path.exists(test_file_1):
            pytest.skip("test_memory_health.py non trouvé")

        with patch("core.ci_pipeline._run_pytest", new_callable=AsyncMock,
                    return_value=(True, "19 passed")) as mock_pytest:
            ok, output = await run_existing_tests_for_file(
                os.path.join(PROJECT_ROOT, "core", "vector_store.py")
            )

        assert ok is True
        # Vérifie que le fichier du mapping supervisé est PARMI les tests lancés.
        # (On scanne TOUS les appels, pas seulement le dernier : depuis l'atelier console,
        # d'autres tests importent core.vector_store -- test_full_switch_mem_v2, test_recall --
        # et le graphe de dependances les decouvre aussi legitimement.)
        called_paths = [c.args[0] for c in mock_pytest.call_args_list]
        assert any(("test_memory_health.py" in p or "test_multiproject_memory.py" in p)
                   for p in called_paths), f"mapping supervisé absent des appels: {called_paths}"
