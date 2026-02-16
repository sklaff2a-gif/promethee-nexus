import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.council import (
    Council, parse_council_mission, _is_consensus, _strip_markdown_prefix,
    MIN_ROUNDS_BEFORE_CONSENSUS, _COUNCIL_PROJECT_CONTEXT,
    _get_project_structure,
)
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

    # Contenu suffisant pour dépasser MIN_CONSENSUS_CONTENT_LENGTH (100 chars)
    _SUBSTANCE = (
        "L'implémentation proposée améliore significativement les performances du routeur "
        "en ajoutant un cache LRU avec TTL de 5 minutes sur les classifications de niveau 1."
    )

    def test_consensus_marqueur(self):
        assert _is_consensus(f"CONSENSUS : {self._SUBSTANCE}") is True

    def test_approuve_marqueur(self):
        assert _is_consensus(f"APPROUVE. {self._SUBSTANCE}") is True

    def test_approuve_accent_marqueur(self):
        assert _is_consensus(f"APPROUVÉ. {self._SUBSTANCE}") is True

    def test_accord_final_marqueur(self):
        assert _is_consensus(f"ACCORD FINAL. {self._SUBSTANCE}") is True

    def test_pas_de_marqueur(self):
        assert _is_consensus("Je pense qu'il y a des problèmes.") is False

    def test_marqueur_au_milieu_pas_au_debut(self):
        assert _is_consensus("Je suis d'accord. CONSENSUS atteint.") is False

    def test_marqueur_en_minuscule_debut(self):
        # Le nettoyage passe en upper, donc "consensus" doit matcher
        assert _is_consensus(f"consensus {self._SUBSTANCE}") is True

    def test_chaine_vide(self):
        assert _is_consensus("") is False

    def test_shallow_consensus_rejected(self):
        """Un consensus sans contenu substantiel est rejeté."""
        assert _is_consensus("CONSENSUS : oui.") is False

    # --- Variantes markdown (bug fix V24) ---

    def test_consensus_markdown_bold(self):
        """**CONSENSUS** en gras markdown."""
        assert _is_consensus(f"**CONSENSUS** {self._SUBSTANCE}") is True

    def test_consensus_markdown_bold_with_colon(self):
        """**CONSENSUS :** gras + deux-points."""
        assert _is_consensus(f"**CONSENSUS :** {self._SUBSTANCE}") is True

    def test_consensus_markdown_h2(self):
        """## CONSENSUS en header markdown."""
        assert _is_consensus(f"## CONSENSUS {self._SUBSTANCE}") is True

    def test_consensus_markdown_h3(self):
        """### CONSENSUS en header markdown."""
        assert _is_consensus(f"### CONSENSUS\n{self._SUBSTANCE}") is True

    def test_consensus_markdown_h1(self):
        """# CONSENSUS en header markdown."""
        assert _is_consensus(f"# CONSENSUS {self._SUBSTANCE}") is True

    def test_approuve_markdown_bold(self):
        """**APPROUVÉ** en gras."""
        assert _is_consensus(f"**APPROUVÉ** {self._SUBSTANCE}") is True

    def test_consensus_markdown_italic(self):
        """*CONSENSUS* en italique."""
        assert _is_consensus(f"*CONSENSUS* {self._SUBSTANCE}") is True

    def test_consensus_markdown_blockquote(self):
        """> CONSENSUS en blockquote."""
        assert _is_consensus(f"> CONSENSUS {self._SUBSTANCE}") is True

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
    async def test_consensus_at_min_round(self):
        # Consensus au premier tour autorisé (MIN_ROUNDS_BEFORE_CONSENSUS).
        _S = TestConsensus._SUBSTANCE
        non_consensus = "Voici mon analyse."
        consensus_resp = f"CONSENSUS : {_S}"
        # Réponses : non-consensus pour les tours < MIN, consensus au tour MIN
        coder_responses = [non_consensus] * (MIN_ROUNDS_BEFORE_CONSENSUS - 1) + [consensus_resp]
        security_responses = [non_consensus] * (MIN_ROUNDS_BEFORE_CONSENSUS - 1) + [consensus_resp]

        agents = {
            "coder": self._make_mock_agent(coder_responses),
            "security": self._make_mock_agent(security_responses),
        }
        council = Council(agents, ["coder", "security"], "revois ce code", max_rounds=10)
        result = await council.run()

        assert result["status"] == "consensus"
        assert result["rounds_used"] == MIN_ROUNDS_BEFORE_CONSENSUS

    @pytest.mark.asyncio
    async def test_consensus_markdown_stops_at_min_round(self):
        """Bug fix V24: les agents envoient CONSENSUS en markdown, le débat doit s'arrêter."""
        _S = TestConsensus._SUBSTANCE
        non_consensus = "Voici mon analyse détaillée."
        # Réponses non-consensus pour les tours < MIN, consensus markdown au tour MIN
        researcher_responses = [non_consensus] * (MIN_ROUNDS_BEFORE_CONSENSUS - 1) + [f"## CONSENSUS\n{_S}"]
        strategist_responses = [non_consensus] * (MIN_ROUNDS_BEFORE_CONSENSUS - 1) + [f"**CONSENSUS** {_S}"]
        evolution_responses = [non_consensus] * (MIN_ROUNDS_BEFORE_CONSENSUS - 1) + [f"**CONSENSUS :** {_S}"]

        agents = {
            "researcher": self._make_mock_agent(researcher_responses),
            "strategist": self._make_mock_agent(strategist_responses),
            "evolution": self._make_mock_agent(evolution_responses),
        }
        council = Council(agents, ["researcher", "strategist", "evolution"], "test", max_rounds=10)
        result = await council.run()

        assert result["status"] == "consensus"
        assert result["rounds_used"] == MIN_ROUNDS_BEFORE_CONSENSUS

    @pytest.mark.asyncio
    async def test_consensus_early_rounds_ignored(self):
        """Anti-écho : CONSENSUS avant MIN_ROUNDS est ignoré."""
        # Les deux agents disent CONSENSUS à chaque tour
        _S = TestConsensus._SUBSTANCE
        n = MIN_ROUNDS_BEFORE_CONSENSUS + 1  # assez de réponses
        coder_responses = [f"CONSENSUS : {_S}"] * n
        security_responses = [f"CONSENSUS : {_S}"] * n

        agents = {
            "coder": self._make_mock_agent(coder_responses),
            "security": self._make_mock_agent(security_responses),
        }
        council = Council(agents, ["coder", "security"], "test", max_rounds=n)
        result = await council.run()

        assert result["status"] == "consensus"
        # Le consensus ne peut PAS arriver avant MIN_ROUNDS_BEFORE_CONSENSUS
        assert result["rounds_used"] >= MIN_ROUNDS_BEFORE_CONSENSUS
        assert result["rounds_used"] == MIN_ROUNDS_BEFORE_CONSENSUS

    @pytest.mark.asyncio
    async def test_prompt_tour_1_contient_critique_obligatoire(self):
        """Le prompt du tour 1 demande la critique obligatoire."""
        agents = {
            "coder": self._make_mock_agent(),
            "security": self._make_mock_agent(),
        }
        council = Council(agents, ["coder", "security"], "test mission", max_rounds=3)
        prompt = council._build_prompt("coder", 1)

        assert "CRITIQUE OBLIGATOIRE" in prompt
        assert "CONSENSUS" in prompt  # Mentionne qu'on ne peut PAS donner CONSENSUS

    @pytest.mark.asyncio
    async def test_prompt_tour_2_autorise_consensus(self):
        """Le prompt du tour 2+ autorise le consensus."""
        agents = {
            "coder": self._make_mock_agent(),
            "security": self._make_mock_agent(),
        }
        council = Council(agents, ["coder", "security"], "test mission", max_rounds=3)
        prompt = council._build_prompt("coder", MIN_ROUNDS_BEFORE_CONSENSUS)

        assert "CRITIQUE OBLIGATOIRE" not in prompt
        assert "CONSENSUS" in prompt  # Mentionne qu'on PEUT donner CONSENSUS

    @pytest.mark.asyncio
    async def test_prompt_contient_contexte_projet(self):
        """Le prompt injecte le contexte projet PROMÉTHÉE."""
        agents = {
            "coder": self._make_mock_agent(),
            "security": self._make_mock_agent(),
        }
        council = Council(agents, ["coder", "security"], "test", max_rounds=3)
        prompt = council._build_prompt("coder", 1)

        assert "PROMÉTHÉE" in prompt
        assert "Kubernetes" in prompt  # Dans la liste des exclusions

    @pytest.mark.asyncio
    async def test_min_rounds_before_consensus_value(self):
        """La constante MIN_ROUNDS_BEFORE_CONSENSUS vaut 3."""
        assert MIN_ROUNDS_BEFORE_CONSENSUS == 3

    @pytest.mark.asyncio
    async def test_consensus_partiel_ne_suffit_pas(self):
        """Un seul agent en consensus ne suffit pas, il faut tous."""
        _S = TestConsensus._SUBSTANCE
        coder_responses = [f"CONSENSUS : {_S}"] * 3
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
        _S = TestConsensus._SUBSTANCE
        mock_agent_1.generate_content = AsyncMock(return_value=f"CONSENSUS : {_S}")
        mock_agent_2 = MagicMock()
        mock_agent_2.generate_content = AsyncMock(return_value=f"CONSENSUS : {_S}")

        await orch.register_agent("coder", mock_agent_1)
        await orch.register_agent("security", mock_agent_2)

        result = await orch.dispatch_council(["coder", "security"], "test mission")
        assert result["status"] == "consensus"
        # Consensus ignoré avant MIN_ROUNDS_BEFORE_CONSENSUS, accepté au tour MIN
        assert result["rounds_used"] == MIN_ROUNDS_BEFORE_CONSENSUS


# ============================================================
# TESTS STRUCTURE PROJET (Task #13)
# ============================================================

class TestProjectStructure:
    """Vérifie l'injection de la structure projet réelle dans les prompts."""

    def setup_method(self):
        # Reset le cache pour chaque test
        import core.council
        core.council._PROJECT_STRUCTURE_CACHE = None

    def test_get_project_structure_returns_string(self):
        result = _get_project_structure()
        assert isinstance(result, str)
        assert "FICHIERS RÉELS DU PROJET" in result

    def test_project_structure_contains_core(self):
        result = _get_project_structure()
        assert "core/" in result

    def test_project_structure_contains_agents(self):
        result = _get_project_structure()
        assert "Agents/" in result

    def test_project_structure_contains_python_files(self):
        result = _get_project_structure()
        assert ".py" in result

    def test_project_structure_cached(self):
        """La 2e appel utilise le cache."""
        result1 = _get_project_structure()
        result2 = _get_project_structure()
        assert result1 is result2  # Même objet (cache)

    def test_council_prompt_contains_project_files(self):
        """Le prompt Council contient la structure projet."""
        agents = {
            "coder": MagicMock(),
            "security": MagicMock(),
        }
        council = Council(agents, ["coder", "security"], "test", max_rounds=3)
        prompt = council._build_prompt("coder", 1)
        assert "FICHIERS RÉELS" in prompt
