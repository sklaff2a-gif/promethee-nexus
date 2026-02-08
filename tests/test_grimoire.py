import pytest
import sys
import os
import json
import shutil
import tempfile
from unittest.mock import patch, MagicMock, AsyncMock

from core.summoner import Summoner
from core.router import RouterAgent
from core.orchestrator import Orchestrator
from core.grimoire_writer import GrimoireWriter


# ============================================================
# SUMMONER
# ============================================================

class TestSummoner:

    def test_load_valid_module(self):
        """Chargement d'un module valide (math_wizard)."""
        summoner = Summoner("math_wizard")
        module = summoner.load()
        assert hasattr(module, "MathWizard")

    def test_load_nonexistent_module(self):
        """Module inexistant -> ImportError."""
        summoner = Summoner("agent_fantome_inexistant")
        with pytest.raises(ImportError):
            summoner.load()

    def test_invalid_module_name_injection(self):
        """Nom de module avec caractères spéciaux -> ValueError."""
        with pytest.raises(ValueError):
            Summoner("../../etc/passwd")

    def test_invalid_module_name_dots(self):
        """Nom de module avec points -> ValueError."""
        with pytest.raises(ValueError):
            Summoner("malicious.module")


# ============================================================
# ROUTER - NIVEAU 1.5 (GRIMOIRE)
# ============================================================

class TestRouterGrimoire:

    def setup_method(self):
        """Reset le cache Grimoire avant chaque test."""
        RouterAgent._grimoire_index_cache = None

    @pytest.mark.asyncio
    async def test_grimoire_keyword_equation(self):
        """Mission avec mot-clé 'equation' -> math_wizard."""
        result = await RouterAgent.classify_intent("résous cette equation du second degré")
        assert result == "math_wizard"

    @pytest.mark.asyncio
    async def test_grimoire_keyword_traceback(self):
        """Mission avec mot-clé 'traceback' -> dr_debug."""
        result = await RouterAgent.classify_intent("analyse ce traceback svp")
        assert result == "dr_debug"

    @pytest.mark.asyncio
    async def test_grimoire_keyword_traduis(self):
        """Mission avec mot-clé 'traduis' -> translator."""
        result = await RouterAgent.classify_intent("traduis ce texte en anglais")
        assert result == "translator"

    @pytest.mark.asyncio
    async def test_grimoire_keyword_calcul(self):
        """Mission avec mot-clé 'calcul' -> math_wizard."""
        result = await RouterAgent.classify_intent("fais un calcul de probabilités")
        assert result == "math_wizard"

    @pytest.mark.asyncio
    async def test_grimoire_no_match_falls_through(self):
        """Mission sans match Grimoire -> passe au Niveau 2 (mocké)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": "strategist"}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch("core.router.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await RouterAgent.classify_intent("quelle est la philosophie du système")
            assert result == "strategist"

    def test_check_grimoire_index_cache(self):
        """Le cache est rempli après le premier appel."""
        RouterAgent._grimoire_index_cache = None
        RouterAgent._check_grimoire_index("test equation")
        assert RouterAgent._grimoire_index_cache is not None
        assert len(RouterAgent._grimoire_index_cache) >= 1

    def test_invalidate_grimoire_cache(self):
        """invalidate_grimoire_cache() vide le cache."""
        RouterAgent._grimoire_index_cache = [{"slug": "test"}]
        RouterAgent.invalidate_grimoire_cache()
        assert RouterAgent._grimoire_index_cache is None


# ============================================================
# ORCHESTRATEUR - INVOCATION ÉPHÉMÈRE
# ============================================================

class TestOrchestratorGrimoire:

    @pytest.fixture
    def orch(self):
        return Orchestrator()

    @pytest.mark.asyncio
    async def test_dispatch_grimoire_agent(self, orch):
        """Dispatch vers un agent du Grimoire via le Summoner (mocké)."""
        mock_agent = AsyncMock()
        mock_agent.process_task = AsyncMock(return_value={"status": "success", "result": "42"})

        mock_module = MagicMock()
        mock_module.__name__ = "math_wizard"

        mock_class = MagicMock(return_value=mock_agent)
        mock_class.__module__ = "math_wizard"

        mock_module.MathWizard = mock_class

        mock_summoner = MagicMock()
        mock_summoner.load.return_value = mock_module

        with patch("core.summoner.Summoner", return_value=mock_summoner) as MockSummoner:
            # Patcher l'import local dans dispatch_task
            with patch.dict("sys.modules", {"core.summoner": MagicMock(Summoner=MockSummoner)}):
                with patch("core.orchestrator.inspect.getmembers", return_value=[("MathWizard", mock_class)]):
                    with patch("core.orchestrator.inspect.isclass", return_value=True):
                        result = await orch.dispatch_task("math_wizard", {"mission": "calcule 6*7"})
                        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_dissipation_after_execution(self, orch):
        """Après exécution d'un eidolon, sys.modules est nettoyé."""
        mock_agent = AsyncMock()
        mock_agent.process_task = AsyncMock(return_value={"status": "success", "result": "ok"})

        mock_module = MagicMock()
        mock_module.__name__ = "test_eidolon"

        mock_class = MagicMock(return_value=mock_agent)
        mock_class.__module__ = "test_eidolon"

        mock_summoner_instance = MagicMock()
        mock_summoner_instance.load.return_value = mock_module
        MockSummonerClass = MagicMock(return_value=mock_summoner_instance)

        # Simuler que le module est dans sys.modules
        sys.modules["test_eidolon"] = mock_module

        with patch.dict("sys.modules", {"core.summoner": MagicMock(Summoner=MockSummonerClass)}):
            with patch("core.orchestrator.inspect.getmembers", return_value=[("TestEidolon", mock_class)]):
                with patch("core.orchestrator.inspect.isclass", return_value=True):
                    await orch.dispatch_task("test_eidolon", {"mission": "test"})
                    # Le module doit avoir été supprimé de sys.modules
                    assert "test_eidolon" not in sys.modules


# ============================================================
# GRIMOIRE WRITER
# ============================================================

class TestGrimoireWriter:

    @pytest.fixture
    def temp_grimoire(self, tmp_path):
        """Crée un répertoire grimoire temporaire pour les tests."""
        grimoire_dir = tmp_path / "grimoire"
        grimoire_dir.mkdir()

        # Créer un index initial vide
        index_path = grimoire_dir / "grimoire_index.json"
        index_path.write_text("[]", encoding="utf-8")

        # Patcher les chemins
        original_dir = GrimoireWriter.GRIMOIRE_DIR
        original_index = GrimoireWriter.INDEX_PATH

        GrimoireWriter.GRIMOIRE_DIR = str(grimoire_dir)
        GrimoireWriter.INDEX_PATH = str(index_path)

        yield grimoire_dir

        GrimoireWriter.GRIMOIRE_DIR = original_dir
        GrimoireWriter.INDEX_PATH = original_index

    def _valid_code(self):
        return (
            "from core.base_agent import BaseAgent\n\n"
            "class TestAgent(BaseAgent):\n"
            "    def __init__(self):\n"
            "        super().__init__('test', 'test', 'test')\n\n"
            "    async def process_task(self, task_payload):\n"
            "        return {'status': 'success', 'result': 'ok'}\n"
        )

    def test_write_valid_recipe(self, temp_grimoire):
        """Ecriture d'une recette valide."""
        result = GrimoireWriter.write_recipe(
            slug="test_agent",
            name="TestAgent",
            description="Agent de test",
            keywords=["test"],
            code=self._valid_code()
        )
        assert result["status"] == "success"
        assert (temp_grimoire / "test_agent.py").exists()

        # Vérifier l'index
        with open(temp_grimoire / "grimoire_index.json", "r") as f:
            index = json.load(f)
        assert len(index) == 1
        assert index[0]["slug"] == "test_agent"

    def test_reject_invalid_slug_special_chars(self, temp_grimoire):
        """Rejet d'un slug avec caractères spéciaux."""
        result = GrimoireWriter.write_recipe(
            slug="../../hack",
            name="HackAgent",
            description="test",
            keywords=["test"],
            code=self._valid_code()
        )
        assert result["status"] == "error"
        assert "Slug invalide" in result["message"]

    def test_reject_invalid_slug_starts_with_number(self, temp_grimoire):
        """Rejet d'un slug commençant par un chiffre."""
        result = GrimoireWriter.write_recipe(
            slug="123agent",
            name="NumAgent",
            description="test",
            keywords=["test"],
            code=self._valid_code()
        )
        assert result["status"] == "error"

    def test_reject_dangerous_os_system(self, temp_grimoire):
        """Rejet de code contenant os.system."""
        dangerous = self._valid_code() + "\nos.system('rm -rf /')\n"
        result = GrimoireWriter.write_recipe(
            slug="evil_agent",
            name="EvilAgent",
            description="test",
            keywords=["test"],
            code=dangerous
        )
        assert result["status"] == "error"
        assert "os.system" in result["message"]

    def test_reject_dangerous_subprocess(self, temp_grimoire):
        """Rejet de code contenant subprocess."""
        dangerous = self._valid_code() + "\nimport subprocess\n"
        result = GrimoireWriter.write_recipe(
            slug="evil_agent",
            name="EvilAgent",
            description="test",
            keywords=["test"],
            code=dangerous
        )
        assert result["status"] == "error"
        assert "subprocess" in result["message"]

    def test_reject_dangerous_eval(self, temp_grimoire):
        """Rejet de code contenant eval(."""
        dangerous = self._valid_code() + "\neval('malicious')\n"
        result = GrimoireWriter.write_recipe(
            slug="evil_agent",
            name="EvilAgent",
            description="test",
            keywords=["test"],
            code=dangerous
        )
        assert result["status"] == "error"
        assert "eval(" in result["message"]

    def test_reject_no_baseagent_inheritance(self, temp_grimoire):
        """Rejet de code sans héritage BaseAgent."""
        bad_code = (
            "class RogueAgent:\n"
            "    def process_task(self):\n"
            "        return 'rogue'\n"
        )
        result = GrimoireWriter.write_recipe(
            slug="rogue_agent",
            name="RogueAgent",
            description="test",
            keywords=["test"],
            code=bad_code
        )
        assert result["status"] == "error"
        assert "BaseAgent" in result["message"]

    def test_index_updated_after_write(self, temp_grimoire):
        """L'index JSON est mis à jour après écriture."""
        GrimoireWriter.write_recipe(
            slug="agent_a",
            name="AgentA",
            description="Premier agent",
            keywords=["alpha"],
            code=self._valid_code()
        )

        # Deuxième recette (avec code légèrement différent pour que le fichier soit distinct)
        code_b = self._valid_code().replace("TestAgent", "AgentB").replace("'test', 'test', 'test'", "'agent_b', 'beta', 'test'")
        GrimoireWriter.write_recipe(
            slug="agent_b",
            name="AgentB",
            description="Deuxième agent",
            keywords=["beta"],
            code=code_b
        )

        with open(temp_grimoire / "grimoire_index.json", "r") as f:
            index = json.load(f)
        assert len(index) == 2
        slugs = [e["slug"] for e in index]
        assert "agent_a" in slugs
        assert "agent_b" in slugs

    def test_reject_duplicate_slug(self, temp_grimoire):
        """Rejet si le fichier existe déjà."""
        GrimoireWriter.write_recipe(
            slug="unique_agent",
            name="UniqueAgent",
            description="test",
            keywords=["test"],
            code=self._valid_code()
        )
        result = GrimoireWriter.write_recipe(
            slug="unique_agent",
            name="UniqueAgent",
            description="test",
            keywords=["test"],
            code=self._valid_code()
        )
        assert result["status"] == "error"
        assert "existe déjà" in result["message"]
