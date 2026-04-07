# tests/test_chat_engine.py — Tests ChatEngine : system prompt, sections organes
import pytest
from unittest.mock import patch, MagicMock

from core.chat_engine import ChatEngine


@pytest.fixture(autouse=True)
def reset_chat_engine():
    """Reset le singleton ChatEngine avant chaque test."""
    ChatEngine.reset_singleton()
    yield
    ChatEngine.reset_singleton()


@pytest.fixture
def engine():
    """Cree un ChatEngine sans I/O fichier."""
    with patch.object(ChatEngine, "_load"):
        e = ChatEngine()
    return e


# --- TestBuildSystemPrompt ---

class TestBuildSystemPrompt:

    def test_prompt_contains_etat_actuel(self, engine):
        """Section [ETAT ACTUEL] presente dans le prompt."""
        prompt = engine._build_system_prompt()
        assert "[ETAT ACTUEL]" in prompt

    def test_prompt_contains_valeurs_fondamentales(self, engine):
        """Section [VALEURS FONDAMENTALES] presente."""
        prompt = engine._build_system_prompt()
        assert "[VALEURS FONDAMENTALES]" in prompt

    def test_prompt_contains_bienveillance(self, engine):
        """Le mot BIENVEILLANCE apparait (valeur premiere)."""
        prompt = engine._build_system_prompt()
        assert "BIENVEILLANCE" in prompt

    def test_prompt_contains_style(self, engine):
        """Section [STYLE DE CONVERSATION] presente."""
        prompt = engine._build_system_prompt()
        assert "[STYLE DE CONVERSATION" in prompt

    def test_prompt_anti_sycophancy(self, engine):
        """Anti-flatterie present dans le prompt."""
        prompt = engine._build_system_prompt()
        assert "flatte" in prompt.lower()

    def test_prompt_mentions_jean_michel(self, engine):
        """Createur mentionne."""
        prompt = engine._build_system_prompt()
        assert "Jean-Michel" in prompt

    def test_prompt_mentions_authenticite(self, engine):
        """AUTHENTICITE mentionnee comme valeur."""
        prompt = engine._build_system_prompt()
        assert "AUTHENTICITE" in prompt

    def test_prompt_mentions_humilite(self, engine):
        """HUMILITE mentionnee comme valeur."""
        prompt = engine._build_system_prompt()
        assert "HUMILITE" in prompt


# --- TestOrganSections ---

class TestOrganSections:

    def test_sensorium_section_when_stressed(self, engine):
        """Comfort < 0.7 -> section [PERCEPTION CORPORELLE] presente."""
        mock_sens = MagicMock()
        mock_sens.get_comfort_index.return_value = 0.5
        mock_sens.get_sensorium_context.return_value = "CPU eleve"
        mock_module = MagicMock()
        mock_module.sensorium = mock_sens

        with patch.dict("sys.modules", {"core.sensorium": mock_module}):
            prompt = engine._build_system_prompt()
        assert "[PERCEPTION CORPORELLE]" in prompt
        assert "50%" in prompt

    def test_sensorium_section_absent_when_ok(self, engine):
        """Comfort >= 0.7 -> pas de section perception."""
        mock_sens = MagicMock()
        mock_sens.get_comfort_index.return_value = 0.9
        mock_module = MagicMock()
        mock_module.sensorium = mock_sens

        with patch.dict("sys.modules", {"core.sensorium": mock_module}):
            prompt = engine._build_system_prompt()
        assert "[PERCEPTION CORPORELLE]" not in prompt

    def test_insula_section(self, engine):
        """Mock insula -> section [INTEROCEPTION] presente."""
        mock_insula = MagicMock()
        mock_insula.get_body_awareness_context.return_value = "Coherence corporelle elevee"
        mock_module = MagicMock()
        mock_module.insula = mock_insula

        with patch.dict("sys.modules", {"core.insula": mock_module}):
            prompt = engine._build_system_prompt()
        assert "[INTEROCEPTION]" in prompt

    def test_curiosity_section(self, engine):
        """Mock curiosity -> section [CURIOSITE] presente."""
        mock_curiosity = MagicMock()
        mock_curiosity.get_curiosity_context.return_value = "3 sujets en exploration"
        mock_module = MagicMock()
        mock_module.curiosity = mock_curiosity

        with patch.dict("sys.modules", {"core.curiosity_reflex": mock_module}):
            prompt = engine._build_system_prompt()
        assert "[CURIOSITE]" in prompt

    def test_dmn_section(self, engine):
        """Mock DMN -> section [INTROSPECTION] presente."""
        mock_dmn = MagicMock()
        mock_dmn.get_dmn_context.return_value = "Reflexion sur architecture"
        mock_module = MagicMock()
        mock_module.dmn = mock_dmn

        with patch.dict("sys.modules", {"core.default_mode_network": mock_module}):
            prompt = engine._build_system_prompt()
        assert "[INTROSPECTION]" in prompt

    def test_hypothalamus_section_with_alarms(self, engine):
        """Alarmes actives -> section [HOMEOSTASIE] presente."""
        mock_hypo = MagicMock()
        mock_hypo.get_stats.return_value = {"active_alarms": 2}
        mock_module = MagicMock()
        mock_module.hypothalamus = mock_hypo

        with patch.dict("sys.modules", {"core.hypothalamus": mock_module}):
            prompt = engine._build_system_prompt()
        assert "[HOMEOSTASIE]" in prompt
        assert "2 alarme" in prompt

    def test_all_organ_sections_graceful_failure(self, engine):
        """Tous les imports echouent -> prompt valide quand meme."""
        # Pas de mock = imports echouent silencieusement (try/except)
        prompt = engine._build_system_prompt()
        assert "Promethee" in prompt
        assert "[VALEURS FONDAMENTALES]" in prompt
        assert "[STYLE DE CONVERSATION" in prompt
