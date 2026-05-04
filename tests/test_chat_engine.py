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


# --- TestSocialBypass (04/05/2026 — Fix B derive centripete) ---

class TestSocialBypass:
    """Detecteur de salutations + prompt minimal pour eviter regurgitation
    metriques somatiques sur "bonjour"/"salut"/etc."""

    def test_detect_bonjour(self, engine):
        assert engine._is_simple_social_message("bonjour") is True
        assert engine._is_simple_social_message("Bonjour Prométhée") is True
        assert engine._is_simple_social_message("BONJOUR") is True

    def test_detect_salut_hello_coucou(self, engine):
        assert engine._is_simple_social_message("salut") is True
        assert engine._is_simple_social_message("Hello") is True
        assert engine._is_simple_social_message("coucou") is True
        assert engine._is_simple_social_message("hey") is True

    def test_detect_ca_va(self, engine):
        assert engine._is_simple_social_message("ca va ?") is True
        assert engine._is_simple_social_message("ça va") is True
        assert engine._is_simple_social_message("comment vas-tu ?") is True

    def test_detect_merci_au_revoir(self, engine):
        assert engine._is_simple_social_message("merci") is True
        assert engine._is_simple_social_message("au revoir") is True
        assert engine._is_simple_social_message("bonne nuit") is True

    def test_reject_long_message(self, engine):
        """Message > 50 chars n'est pas social meme si commence par bonjour."""
        long_msg = "bonjour, peux-tu m'expliquer comment fonctionne ton RAG aujourd'hui ?"
        assert engine._is_simple_social_message(long_msg) is False

    def test_reject_technical_question(self, engine):
        """Question technique n'est pas sociale."""
        assert engine._is_simple_social_message("comment marche le RAG ?") is False
        assert engine._is_simple_social_message("explique le pipeline") is False

    def test_reject_empty(self, engine):
        assert engine._is_simple_social_message("") is False
        assert engine._is_simple_social_message("   ") is False

    def test_minimal_prompt_no_metrics(self, engine):
        """Le prompt social minimal liste les metriques a NE PAS evoquer."""
        prompt = engine._build_social_minimal_prompt()
        # Mots qui doivent apparaitre comme interdits (cf liste REGLES)
        forbidden_listed = ["BPM", "coherence", "pulsions", "circuits", "cardiaque"]
        for word in forbidden_listed:
            assert word in prompt, f"prompt doit lister {word!r} comme interdit"
        # Le prompt doit etre court (< 1500 chars)
        assert len(prompt) < 1500

    def test_minimal_prompt_includes_examples(self, engine):
        """Le prompt social inclut des exemples de reponses naturelles."""
        prompt = engine._build_social_minimal_prompt()
        assert "Bonjour" in prompt
        assert "Jean-Michel" in prompt


# --- TestLastExternalChatTsRestoration (04/05/2026 — Fix A bug init) ---

class TestLastExternalChatTsRestoration:
    """Le ts du dernier message external doit etre restaure au _load
    pour que le 1er retour user apres reboot puisse trigger Editor."""

    def test_load_restores_last_external_ts(self, tmp_path, monkeypatch):
        """Apres _load, _last_external_chat_ts == ts du dernier msg external."""
        import json
        from core import chat_engine as ce_mod

        # Construire un faux chat_history sur disque
        fake_history = {
            "version": "1.0",
            "messages": [
                {"role": "user", "content": "premier", "timestamp": 1000.0, "source": "external"},
                {"role": "assistant", "content": "rep1", "timestamp": 1001.0},
                {"role": "user", "content": "auto", "timestamp": 1002.0, "source": "internal"},
                {"role": "user", "content": "dernier", "timestamp": 1003.0, "source": "external"},
                {"role": "assistant", "content": "rep2", "timestamp": 1004.0},
            ],
        }
        history_path = tmp_path / "chat_history.json"
        history_path.write_text(json.dumps(fake_history), encoding="utf-8")
        monkeypatch.setattr(ce_mod, "CHAT_HISTORY_FILE", history_path)

        ChatEngine.reset_singleton()
        e = ChatEngine()
        # Le dernier msg external a ts=1003.0
        assert e._last_external_chat_ts == 1003.0

    def test_load_no_external_keeps_zero(self, tmp_path, monkeypatch):
        """Si chat_history sans message external, _last_external_chat_ts reste 0."""
        import json
        from core import chat_engine as ce_mod

        fake_history = {
            "version": "1.0",
            "messages": [
                {"role": "user", "content": "auto", "timestamp": 500.0, "source": "internal"},
                {"role": "assistant", "content": "rep", "timestamp": 501.0},
            ],
        }
        history_path = tmp_path / "chat_history.json"
        history_path.write_text(json.dumps(fake_history), encoding="utf-8")
        monkeypatch.setattr(ce_mod, "CHAT_HISTORY_FILE", history_path)

        ChatEngine.reset_singleton()
        e = ChatEngine()
        assert e._last_external_chat_ts == 0.0

    def test_load_empty_history_keeps_zero(self, tmp_path, monkeypatch):
        """Pas de fichier chat_history -> _last_external_chat_ts == 0.0."""
        from core import chat_engine as ce_mod
        history_path = tmp_path / "chat_history.json"  # n'existe pas
        monkeypatch.setattr(ce_mod, "CHAT_HISTORY_FILE", history_path)

        ChatEngine.reset_singleton()
        e = ChatEngine()
        assert e._last_external_chat_ts == 0.0
