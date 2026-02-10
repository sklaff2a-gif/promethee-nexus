import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.council import Council, parse_council_mission, _is_consensus, _strip_markdown_prefix
from core.orchestrator import Orchestrator
from core.event_bus.bus import bus


# ============================================================
# TESTS PARSING
# ============================================================

class TestParseCouncilMission:

    def test_syntaxe_standard_deux_agents(self):
        result = parse_council_mission("coder, security - revois ce code")
        assert result is not None
        assert result["participants"] == ["coder", "security"]
        assert result["mission"] == "revois ce code"

    def test_syntaxe_trois_agents(self):
        result = parse_council_mission("coder, security, architect - analyse complète")
        assert result is not None
        assert result["participants"] == ["coder", "security", "architect"]
        assert result["mission"] == "analyse complète"

    def test_separateur_deux_points(self):
        result = parse_council_mission("coder, security : revois ce code")
        assert result is not None
        assert result["participants"] == ["coder", "security"]
        assert result["mission"] == "revois ce code"

    def test_pas_de_separateur_retourne_none(self):
        result = parse_council_mission("coder security revois ce code")
        assert result is None

    def test_un_seul_agent_retourne_none(self):
        result = parse_council_mission("coder - revois ce code")
        assert result is None

    def test_espaces_autour_virgules(self):
        result = parse_council_mission("coder ,  security  -  mission test")
        assert result is not None
        assert result["participants"] == ["coder", "security"]


# ============================================================
# TESTS CONSENSUS
# ============================================================

class TestConsensus:

    def test_consensus_marqueur(self):
        assert _is_consensus("CONSENSUS : le code est bon.") is True

    def test_approuve_marqueur(self):
        assert _is_consensus("APPROUVE. Tout est correct.") is True

    def test_approuve_accent_marqueur(self):
        assert _is_consensus("APPROUVÉ. Rien à redire.") is True

    def test_accord_final_marqueur(self):
        assert _is_consensus("ACCORD FINAL. Nous convergeons.") is True

    def test_pas_de_marqueur(self):
        assert _is_consensus("Je pense qu'il y a des problèmes.") is False

    def test_marqueur_au_milieu_pas_au_debut(self):
        assert _is_consensus("Je suis d'accord. CONSENSUS atteint.") is False

    def test_marqueur_en_minuscule_debut(self):
        # Le nettoyage passe en upper, donc "consensus" doit matcher
        assert _is_consensus("consensus trouvé") is True

    def test_chaine_vide(self):
        assert _is_consensus("") is False

    # --- Variantes markdown (bug fix V24) ---

    def test_consensus_markdown_bold(self):
        """**CONSENSUS** en gras markdown."""
        assert _is_consensus("**CONSENSUS** La solution est robuste.") is True

    def test_consensus_markdown_bold_with_colon(self):
        """**CONSENSUS :** gras + deux-points."""
        assert _is_consensus("**CONSENSUS :** Approuvé.") is True

    def test_consensus_markdown_h2(self):
        """## CONSENSUS en header markdown."""
        assert _is_consensus("## CONSENSUS") is True

    def test_consensus_markdown_h3(self):
        """### CONSENSUS en header markdown."""
        assert _is_consensus("### CONSENSUS\nTout est validé.") is True

    def test_consensus_markdown_h1(self):
        """# CONSENSUS en header markdown."""
        assert _is_consensus("# CONSENSUS") is True

    def test_approuve_markdown_bold(self):
        """**APPROUVÉ** en gras."""
        assert _is_consensus("**APPROUVÉ**") is True

    def test_consensus_markdown_italic(self):
        """*CONSENSUS* en italique."""
        assert _is_consensus("*CONSENSUS* je valide.") is True

    def test_consensus_markdown_blockquote(self):
        """> CONSENSUS en blockquote."""
        assert _is_consensus("> CONSENSUS trouvé.") is True

    def test_non_consensus_markdown_bold(self):
        """**Analyse** ne doit pas matcher."""
        assert _is_consensus("**Analyse** détaillée du problème.") is False

    def test_strip_markdown_prefix_double_bold(self):
        """Vérifie le stripping de **texte**."""
        assert _strip_markdown_prefix("**CONSENSUS**") == "CONSENSUS"

    def test_strip_markdown_prefix_header(self):
        """Vérifie le stripping de ## texte."""
        assert _strip_markdown_prefix("## CONSENSUS final") == "CONSENSUS final"


# ============================================================
# TESTS DEBAT (Council.run)
# ============================================================

class TestCouncilRun:

    def _make_mock_agent(self, responses=None):
        """Crée un mock agent avec generate_content configurable."""
        agent = MagicMock()
        if responses:
            agent.generate_content = AsyncMock(side_effect=responses)
        else:
            agent.generate_content = AsyncMock(return_value="Voici mon analyse.")
        return agent

    @pytest.mark.asyncio
    async def test_debat_sans_consensus_dure_max_rounds(self):
        agents = {
            "coder": self._make_mock_agent(),
            "security": self._make_mock_agent(),
        }
        council = Council(agents, ["coder", "security"], "test mission", max_rounds=3)
        result = await council.run()

        assert result["status"] == "max_rounds"
        assert result["rounds_used"] == 3
        # 2 participants * 3 rounds = 6 entrées
        assert len(result["transcript"]) == 6

    @pytest.mark.asyncio
    async def test_consensus_au_tour_2(self):
        # Tour 1 : pas de consensus. Tour 2 : consensus des deux.
        coder_responses = ["Voici ma proposition.", "CONSENSUS : c'est bon."]
        security_responses = ["Il y a un problème.", "CONSENSUS : corrigé, j'approuve."]

        agents = {
            "coder": self._make_mock_agent(coder_responses),
            "security": self._make_mock_agent(security_responses),
        }
        council = Council(agents, ["coder", "security"], "revois ce code", max_rounds=5)
        result = await council.run()

        assert result["status"] == "consensus"
        assert result["rounds_used"] == 2

    @pytest.mark.asyncio
    async def test_consensus_markdown_stops_at_round_2(self):
        """Bug fix V24: les agents envoient CONSENSUS en markdown, le débat doit s'arrêter."""
        # Tour 1 : contributions normales. Tour 2 : consensus en markdown.
        researcher_responses = [
            "Voici mes trouvailles sur les agents IA.",
            "## CONSENSUS\nLes 3 candidats sont validés.",
        ]
        strategist_responses = [
            "L'analyse est pertinente mais incomplète.",
            "**CONSENSUS** La feuille de route est approuvée.",
        ]
        evolution_responses = [
            "### CRITIQUE\nIl manque l'aspect adaptatif.",
            "**CONSENSUS :** J'approuve avec les amendements.",
        ]

        agents = {
            "researcher": self._make_mock_agent(researcher_responses),
            "strategist": self._make_mock_agent(strategist_responses),
            "evolution": self._make_mock_agent(evolution_responses),
        }
        council = Council(agents, ["researcher", "strategist", "evolution"], "test", max_rounds=5)
        result = await council.run()

        assert result["status"] == "consensus"
        assert result["rounds_used"] == 2  # Arrêt au tour 2, pas 5

    @pytest.mark.asyncio
    async def test_consensus_partiel_ne_suffit_pas(self):
        """Un seul agent en consensus ne suffit pas, il faut tous."""
        coder_responses = ["CONSENSUS : ok"] * 3
        security_responses = ["Non, je refuse."] * 3

        agents = {
            "coder": self._make_mock_agent(coder_responses),
            "security": self._make_mock_agent(security_responses),
        }
        council = Council(agents, ["coder", "security"], "test", max_rounds=3)
        result = await council.run()

        assert result["status"] == "max_rounds"

    @pytest.mark.asyncio
    async def test_agent_manquant_retourne_error(self):
        agents = {"coder": self._make_mock_agent()}
        council = Council(agents, ["coder", "security"], "test", max_rounds=3)
        result = await council.run()

        assert result["status"] == "error"
        assert "introuvable" in result["reason"]

    @pytest.mark.asyncio
    async def test_un_seul_participant_retourne_error(self):
        agents = {"coder": self._make_mock_agent()}
        council = Council(agents, ["coder"], "test", max_rounds=3)
        result = await council.run()

        assert result["status"] == "error"
        assert "2 participants" in result["reason"]

    @pytest.mark.asyncio
    async def test_evenements_bus_publies(self):
        """Vérifie que COUNCIL_START, COUNCIL_TURN, COUNCIL_END, AGENT_RESPONSE sont publiés."""
        events_received = []

        async def capture(payload):
            events_received.append(payload)

        bus.subscribe("COUNCIL_START", capture)
        bus.subscribe("COUNCIL_TURN", capture)
        bus.subscribe("COUNCIL_END", capture)
        bus.subscribe("AGENT_RESPONSE", capture)

        agents = {
            "coder": self._make_mock_agent(),
            "security": self._make_mock_agent(),
        }
        council = Council(agents, ["coder", "security"], "test", max_rounds=1)
        await council.run()

        # On attend que les tâches asynchrones du bus soient terminées
        import asyncio
        await asyncio.sleep(0.1)

        # COUNCIL_START (1) + COUNCIL_TURN (2) + COUNCIL_END (1) + AGENT_RESPONSE (1) = 5
        assert len(events_received) == 5

    @pytest.mark.asyncio
    async def test_generate_content_appele_pas_process_task(self):
        """Vérifie que generate_content est appelé (pas process_task)."""
        agent = MagicMock()
        agent.generate_content = AsyncMock(return_value="ok")
        agent.process_task = AsyncMock()

        agents = {"coder": agent, "security": self._make_mock_agent()}
        council = Council(agents, ["coder", "security"], "test", max_rounds=1)
        await council.run()

        agent.generate_content.assert_called()
        agent.process_task.assert_not_called()


# ============================================================
# TESTS ORCHESTRATEUR - dispatch_council
# ============================================================

class TestOrchestratorCouncil:

    @pytest.fixture
    def orch(self):
        return Orchestrator()

    @pytest.mark.asyncio
    async def test_kill_switch_bloque_council(self, orch):
        await orch.set_kill_switch(True)
        result = await orch.dispatch_council(["coder", "security"], "test")
        assert result["status"] == "BLOCKED"
        assert result["reason"] == "KILL_SWITCH_ACTIVE"

    @pytest.mark.asyncio
    async def test_dispatch_council_lance_debat(self, orch):
        mock_agent_1 = MagicMock()
        mock_agent_1.generate_content = AsyncMock(return_value="CONSENSUS : ok")
        mock_agent_2 = MagicMock()
        mock_agent_2.generate_content = AsyncMock(return_value="CONSENSUS : ok")

        await orch.register_agent("coder", mock_agent_1)
        await orch.register_agent("security", mock_agent_2)

        result = await orch.dispatch_council(["coder", "security"], "test mission")
        assert result["status"] == "consensus"
        assert result["rounds_used"] == 1
