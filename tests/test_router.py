import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from core.router import RouterAgent


class TestRouterLevel0BlindTrust:
    """Niveau 0 : Syntaxe directe 'agent: commande'."""

    @pytest.mark.asyncio
    async def test_direct_addressing_simple(self):
        assert await RouterAgent.classify_intent("coder: fais un script") == "coder"

    @pytest.mark.asyncio
    async def test_direct_addressing_unknown_agent(self):
        """Les agents inconnus passent aussi (Grimoire compatible)."""
        assert await RouterAgent.classify_intent("math_wizard: calcule pi") == "math_wizard"

    @pytest.mark.asyncio
    async def test_direct_addressing_with_spaces_rejected(self):
        """Un nom avec espaces n'est PAS un agent, on passe au niveau 1."""
        result = await RouterAgent.classify_intent("mon agent: fais un truc")
        assert result != "mon agent"

    @pytest.mark.asyncio
    async def test_direct_addressing_case_insensitive(self):
        assert await RouterAgent.classify_intent("FACTORY: crée un fichier") == "factory"

    @pytest.mark.asyncio
    async def test_colon_in_middle_of_sentence(self):
        """Les deux-points dans une phrase normale ne doivent pas matcher si le mot avant contient des espaces."""
        result = await RouterAgent.classify_intent("voici le plan: il faut coder")
        # "voici le plan" contient des espaces, donc pas de blind trust
        assert result != "voici le plan"


class TestRouterLevel1Keywords:
    """Niveau 1 : Détection par mots-clés."""

    @pytest.mark.asyncio
    async def test_keyword_cpu(self):
        assert await RouterAgent.classify_intent("montre moi le cpu") == "infra"

    @pytest.mark.asyncio
    async def test_keyword_ram(self):
        assert await RouterAgent.classify_intent("combien de ram dispo") == "infra"

    @pytest.mark.asyncio
    async def test_keyword_scan(self):
        assert await RouterAgent.classify_intent("scan la dropzone") == "researcher"

    @pytest.mark.asyncio
    async def test_keyword_code(self):
        assert await RouterAgent.classify_intent("code moi une fonction") == "coder"

    @pytest.mark.asyncio
    async def test_keyword_python(self):
        assert await RouterAgent.classify_intent("écris un script python") == "coder"

    @pytest.mark.asyncio
    async def test_keyword_evolue(self):
        assert await RouterAgent.classify_intent("évolue le système") == "evolution"

    @pytest.mark.asyncio
    async def test_keyword_audit(self):
        assert await RouterAgent.classify_intent("audit de la structure") == "architect"

    @pytest.mark.asyncio
    async def test_keyword_secu(self):
        # Fix 29/05/2026 : restauration de la phrase originale "analyse secu".
        # Le bug architectural (collision csv_parser sur "analyse") est
        # maintenant resolu par la Priorite 0.4 STRONG_KEYWORDS dans
        # router.py:101-117 qui court-circuite le Niveau 0.5 Grimoire pour
        # les mots-cles vitaux (secu/faille/attack/protect -> security).
        # Si ce test echoue a nouveau, c'est que quelqu'un a touche a la
        # Priorite 0.4 ou ajoute "analyse" dans les keywords d'un agent
        # Grimoire qui passerait avant.
        assert await RouterAgent.classify_intent("analyse secu du serveur") == "security"

    @pytest.mark.asyncio
    async def test_keyword_article(self):
        assert await RouterAgent.classify_intent("rédige un article sur l'IA") == "writer"

    @pytest.mark.asyncio
    async def test_keyword_format(self):
        assert await RouterAgent.classify_intent("formate ce fichier") == "formatter"

    @pytest.mark.asyncio
    async def test_agent_name_first_word(self):
        """Si le premier mot est un nom d'agent connu, on le route directement."""
        assert await RouterAgent.classify_intent("factory crée le fichier") == "factory"


class TestRouterLevel2Fallback:
    """Niveau 2 : Fallback LLM sémantique (mocké)."""

    @pytest.mark.asyncio
    async def test_semantic_fallback_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": "coder"}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch("core.router.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await RouterAgent.classify_intent("transforme cette idée en réalité")
            assert result == "coder"

    @pytest.mark.asyncio
    async def test_semantic_fallback_to_strategist_on_error(self):
        with patch("core.router.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(side_effect=Exception("Ollama down"))
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await RouterAgent.classify_intent("une question très ambiguë")
            assert result == "strategist"

    @pytest.mark.asyncio
    async def test_semantic_fallback_invalid_response(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": "je ne sais pas"}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch("core.router.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await RouterAgent.classify_intent("fais quelque chose de magique")
            assert result == "strategist"


class TestRouterEdgeCases:
    """Cas limites."""

    @pytest.mark.asyncio
    async def test_empty_mission(self):
        """Mission vide ne doit pas planter."""
        result = await RouterAgent.classify_intent("")
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_mission_only_colon(self):
        result = await RouterAgent.classify_intent(":")
        assert isinstance(result, str)
