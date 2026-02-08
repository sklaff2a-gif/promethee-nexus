import pytest
from unittest.mock import patch, MagicMock
from core.router import RouterAgent


class TestRouterLevel0BlindTrust:
    """Niveau 0 : Syntaxe directe 'agent: commande'."""

    def test_direct_addressing_simple(self):
        assert RouterAgent.classify_intent("coder: fais un script") == "coder"

    def test_direct_addressing_unknown_agent(self):
        """Les agents inconnus passent aussi (Grimoire compatible)."""
        assert RouterAgent.classify_intent("math_wizard: calcule pi") == "math_wizard"

    def test_direct_addressing_with_spaces_rejected(self):
        """Un nom avec espaces n'est PAS un agent, on passe au niveau 1."""
        result = RouterAgent.classify_intent("mon agent: fais un truc")
        assert result != "mon agent"

    def test_direct_addressing_case_insensitive(self):
        assert RouterAgent.classify_intent("FACTORY: crée un fichier") == "factory"

    def test_colon_in_middle_of_sentence(self):
        """Les deux-points dans une phrase normale ne doivent pas matcher si le mot avant contient des espaces."""
        result = RouterAgent.classify_intent("voici le plan: il faut coder")
        # "voici le plan" contient des espaces, donc pas de blind trust
        assert result != "voici le plan"


class TestRouterLevel1Keywords:
    """Niveau 1 : Détection par mots-clés."""

    def test_keyword_cpu(self):
        assert RouterAgent.classify_intent("montre moi le cpu") == "infra"

    def test_keyword_ram(self):
        assert RouterAgent.classify_intent("combien de ram dispo") == "infra"

    def test_keyword_scan(self):
        assert RouterAgent.classify_intent("scan la dropzone") == "researcher"

    def test_keyword_code(self):
        assert RouterAgent.classify_intent("code moi une fonction") == "coder"

    def test_keyword_python(self):
        assert RouterAgent.classify_intent("écris un script python") == "coder"

    def test_keyword_evolue(self):
        assert RouterAgent.classify_intent("évolue le système") == "evolution"

    def test_keyword_audit(self):
        assert RouterAgent.classify_intent("audit de la structure") == "architect"

    def test_keyword_secu(self):
        assert RouterAgent.classify_intent("analyse secu du serveur") == "security"

    def test_keyword_article(self):
        assert RouterAgent.classify_intent("rédige un article sur l'IA") == "writer"

    def test_keyword_format(self):
        assert RouterAgent.classify_intent("formate ce fichier") == "formatter"

    def test_agent_name_first_word(self):
        """Si le premier mot est un nom d'agent connu, on le route directement."""
        assert RouterAgent.classify_intent("factory crée le fichier") == "factory"


class TestRouterLevel2Fallback:
    """Niveau 2 : Fallback LLM sémantique (mocké)."""

    @patch("core.router.requests.post")
    def test_semantic_fallback_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": "coder"}
        mock_post.return_value = mock_response

        result = RouterAgent.classify_intent("transforme cette idée en réalité")
        assert result == "coder"

    @patch("core.router.requests.post")
    def test_semantic_fallback_to_strategist_on_error(self, mock_post):
        mock_post.side_effect = Exception("Ollama down")
        result = RouterAgent.classify_intent("une question très ambiguë")
        assert result == "strategist"

    @patch("core.router.requests.post")
    def test_semantic_fallback_invalid_response(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": "je ne sais pas"}
        mock_post.return_value = mock_response

        result = RouterAgent.classify_intent("fais quelque chose de magique")
        assert result == "strategist"


class TestRouterEdgeCases:
    """Cas limites."""

    def test_empty_mission(self):
        """Mission vide ne doit pas planter."""
        result = RouterAgent.classify_intent("")
        assert isinstance(result, str)

    def test_mission_only_colon(self):
        result = RouterAgent.classify_intent(":")
        assert isinstance(result, str)
