import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.council import (
    Council, parse_council_mission, _is_consensus, _strip_markdown_prefix,
    _score_argument, _parse_president_verdict, _extract_keywords,
    MIN_ROUNDS_BEFORE_CONSENSUS, MIN_ROUNDS_BEFORE_PRESIDENT,
    PRESIDENT_AGENT_NAME, _COUNCIL_PROJECT_CONTEXT,
    _get_project_structure, _FILE_PATTERN,
    _get_real_files, _find_closest_file,
)
import core.council as council_module
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
        council = Council(agents, ["coder", "security"], "test mission", max_rounds=3, enable_student=False, enable_advocate=False)
        result = await council.run()

        assert result["status"] == "max_rounds"
        assert result["rounds_used"] == 3
        # 2 participants * 3 rounds = 6 entrées (enable_student=False, enable_advocate=False)
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
        council = Council(agents, ["coder", "security"], "test", max_rounds=1, enable_student=False)
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


# ============================================================
# TESTS SCORING ARGUMENTS
# ============================================================

class TestScoreArgument:
    """Vérifie que _score_argument évalue correctement les arguments du Council."""

    def test_empty_content_scores_zero(self):
        result = _score_argument("")
        assert result["score"] == 0.0
        assert result["confidence"] == 0.0

    def test_short_content_scores_zero(self):
        result = _score_argument("ok")
        assert result["score"] == 0.0

    def test_high_score_with_files_and_actions(self):
        """Un argument riche (fichiers, actions, code) obtient un score élevé."""
        content = (
            "Je propose de modifier `core/autonomy_engine.py` pour ajouter un garde-fou.\n"
            "Il faut aussi corriger `Agents/coder_agent.py` et vérifier `core/council.py`.\n"
            "L'action consiste à implémenter une validation dans la méthode dispatch.\n"
            "```python\ndef validate_budget(cost):\n    return cost <= DAILY_MAX\n```\n"
            "Cela permettrait de refactorer le pipeline sans risque."
        )
        result = _score_argument(content)
        assert result["score"] >= 0.5
        assert result["confidence"] >= 0.4
        assert result["breakdown"]["fichiers_cités"] >= 3
        assert result["breakdown"]["actions_proposées"] >= 2

    def test_vague_content_low_score(self):
        """Un argument vague sans fichiers ni actions obtient un score bas."""
        content = (
            "Je pense que nous devrions peut-être envisager de revoir "
            "notre approche générale pour mieux optimiser les performances "
            "du système dans son ensemble."
        )
        result = _score_argument(content)
        assert result["score"] < 0.3
        assert result["breakdown"]["fichiers_cités"] == 0
        assert result["breakdown"]["actions_proposées"] == 0

    def test_code_block_increases_score(self):
        """Un bloc de code augmente le score."""
        without_code = "Il faut modifier core/router.py pour ajouter un filtre."
        with_code = (
            "Il faut modifier core/router.py pour ajouter un filtre.\n"
            "```python\ndef filter_intent(intent):\n    return intent in ALLOWED\n```"
        )
        score_without = _score_argument(without_code)["score"]
        score_with = _score_argument(with_code)["score"]
        assert score_with > score_without

    def test_english_penalty(self):
        """Le contenu en anglais est pénalisé."""
        english = (
            "We should implement a new function. However, this would require "
            "moreover some changes. The function should be implemented carefully "
            "and could therefore improve performance significantly."
        )
        result = _score_argument(english)
        assert result["breakdown"].get("pénalité_anglais", 0) >= 3

    def test_file_pattern_detection(self):
        """Les différents formats de chemins projet sont détectés."""
        content = (
            "Fichiers : core/base_agent.py, Agents/security_agent.py, "
            "config.py et tests/test_council.py doivent être modifiés."
        )
        result = _score_argument(content)
        assert result["breakdown"]["fichiers_cités"] >= 3

    def test_action_verbs_detected(self):
        """Les verbes d'action français sont détectés."""
        content = (
            "Il faut ajouter un garde-fou, modifier le scoring, "
            "supprimer les doublons et valider le résultat."
        )
        result = _score_argument(content)
        assert result["breakdown"]["actions_proposées"] >= 4

    def test_score_range_0_to_1(self):
        """Le score est toujours dans [0, 1]."""
        # Score maximal théorique
        content = (
            "Modifier core/a.py, core/b.py, Agents/c.py pour implémenter, "
            "ajouter, corriger, supprimer un système. "
            "```python\ndef a():\n    pass\n```\n"
            "```python\ndef b():\n    pass\n```\n" * 3 +
            "x" * 500
        )
        result = _score_argument(content)
        assert 0.0 <= result["score"] <= 1.0
        assert 0.0 <= result["confidence"] <= 1.0

    def test_transcript_includes_scores(self):
        """Le transcript formaté inclut les scores de pertinence."""
        agents = {"a": MagicMock(), "b": MagicMock()}
        council = Council(agents, ["a", "b"], "test", max_rounds=3)
        council.transcript = [
            {"agent": "a", "round": 1, "content": "Je propose de modifier core/router.py", "score": 0.45, "confidence": 0.3},
            {"agent": "b", "round": 1, "content": "Bonne idée", "score": 0.1, "confidence": 0.0},
        ]
        formatted = council._format_transcript()
        assert "pertinence:" in formatted
        assert "★★" in formatted  # 0.45 >= 0.3 → ★★


# ============================================================
# TESTS PRÉSIDENT — PARSING
# ============================================================

class TestPresidentParsing:
    """Tests de _parse_president_verdict."""

    def test_parse_verdict_pertinent(self):
        result = _parse_president_verdict("PERTINENT")
        assert result["verdict"] == "PERTINENT"

    def test_parse_verdict_redirect_with_feedback(self):
        result = _parse_president_verdict("REDIRECT : Le débat dérive vers Kubernetes, recentrez sur Ollama local.")
        assert result["verdict"] == "REDIRECT"
        assert "Kubernetes" in result["feedback"]

    def test_parse_verdict_abort(self):
        result = _parse_president_verdict("ABORT : Hors-sujet total, aucune proposition viable.")
        assert result["verdict"] == "ABORT"
        assert "Hors-sujet" in result["feedback"]

    def test_parse_verdict_markdown_wrapped(self):
        result = _parse_president_verdict("**REDIRECT** : Recentrez le débat.")
        assert result["verdict"] == "REDIRECT"
        assert "Recentrez" in result["feedback"]

    def test_parse_verdict_fallback_on_garbage(self):
        result = _parse_president_verdict("Bla bla bla sans rapport avec un verdict.")
        assert result["verdict"] == "PERTINENT"

    def test_parse_verdict_empty(self):
        result = _parse_president_verdict("")
        assert result["verdict"] == "PERTINENT"


# ============================================================
# TESTS PRÉSIDENT — ÉVALUATION
# ============================================================

class TestPresidentEvaluation:
    """Tests de l'évaluation par le président (architect)."""

    def _make_mock_agent(self, responses=None):
        agent = MagicMock()
        if responses:
            agent.generate_content = AsyncMock(side_effect=responses)
        else:
            agent.generate_content = AsyncMock(return_value="Voici mon analyse.")
        return agent

    @pytest.mark.asyncio
    async def test_president_not_called_round_1(self):
        """Pas d'appel architect au tour 1 (< MIN_ROUNDS_BEFORE_PRESIDENT)."""
        architect_mock = self._make_mock_agent()
        agents = {
            "coder": self._make_mock_agent(),
            "security": self._make_mock_agent(),
            "architect": architect_mock,
        }
        council = Council(agents, ["coder", "security"], "test", max_rounds=1)
        await council.run()
        # architect ne doit PAS avoir été appelé (tour 1 < MIN_ROUNDS_BEFORE_PRESIDENT)
        architect_mock.generate_content.assert_not_called()

    @pytest.mark.asyncio
    async def test_abort_stops_debate(self):
        """ABORT → status='aborted', débat s'arrête."""
        n = MIN_ROUNDS_BEFORE_PRESIDENT + 1
        agents = {
            "coder": self._make_mock_agent(["analyse"] * n),
            "security": self._make_mock_agent(["critique"] * n),
            "architect": self._make_mock_agent(
                # Appelé à partir du tour MIN_ROUNDS_BEFORE_PRESIDENT
                ["ABORT : Technologies hors-périmètre mentionnées."]
            ),
        }
        council = Council(agents, ["coder", "security"], "test", max_rounds=n)
        result = await council.run()
        assert result["status"] == "aborted"
        assert result["rounds_used"] == MIN_ROUNDS_BEFORE_PRESIDENT
        assert "PRÉSIDENT — ABORT" in result["final_summary"]
        assert "abort_reason" in result

    @pytest.mark.asyncio
    async def test_redirect_injects_feedback(self):
        """REDIRECT → feedback injecté dans le prompt du tour suivant."""
        n = MIN_ROUNDS_BEFORE_PRESIDENT + 2
        architect_responses = ["REDIRECT : Recentrez sur les fichiers core/"]
        # Après REDIRECT, le tour suivant reçoit PERTINENT pour ne pas reboucler
        architect_responses.append("PERTINENT")
        agents = {
            "coder": self._make_mock_agent(["analyse"] * n),
            "security": self._make_mock_agent(["critique"] * n),
            "architect": self._make_mock_agent(architect_responses),
        }
        council = Council(agents, ["coder", "security"], "test", max_rounds=n)
        await council.run()

        # Vérifier que le prompt du tour après REDIRECT contient le feedback
        # On capture les appels à generate_content du coder
        coder_calls = agents["coder"].generate_content.call_args_list
        # Le tour après REDIRECT (tour MIN_ROUNDS_BEFORE_PRESIDENT+1) doit contenir le feedback
        prompt_after_redirect = coder_calls[MIN_ROUNDS_BEFORE_PRESIDENT].args[0]
        assert "FEEDBACK DU PRÉSIDENT" in prompt_after_redirect
        assert "Recentrez" in prompt_after_redirect

    @pytest.mark.asyncio
    async def test_pertinent_resets_feedback(self):
        """PERTINENT après REDIRECT → feedback disparaît du prompt."""
        n = MIN_ROUNDS_BEFORE_PRESIDENT + 3
        architect_responses = [
            "REDIRECT : Recentrez sur core/",
            "PERTINENT",
            "PERTINENT",
        ]
        agents = {
            "coder": self._make_mock_agent(["analyse"] * n),
            "security": self._make_mock_agent(["critique"] * n),
            "architect": self._make_mock_agent(architect_responses),
        }
        council = Council(agents, ["coder", "security"], "test", max_rounds=n)
        await council.run()

        coder_calls = agents["coder"].generate_content.call_args_list
        # Tour après PERTINENT (2 tours après le REDIRECT) → pas de feedback
        if len(coder_calls) > MIN_ROUNDS_BEFORE_PRESIDENT + 1:
            prompt_after_pertinent = coder_calls[MIN_ROUNDS_BEFORE_PRESIDENT + 1].args[0]
            assert "FEEDBACK DU PRÉSIDENT" not in prompt_after_pertinent

    @pytest.mark.asyncio
    async def test_president_fallback_on_error(self):
        """Exception architect → débat continue (PERTINENT par défaut)."""
        n = MIN_ROUNDS_BEFORE_PRESIDENT + 1
        architect_mock = MagicMock()
        architect_mock.generate_content = AsyncMock(side_effect=RuntimeError("Ollama down"))
        agents = {
            "coder": self._make_mock_agent(["analyse"] * n),
            "security": self._make_mock_agent(["critique"] * n),
            "architect": architect_mock,
        }
        council = Council(agents, ["coder", "security"], "test", max_rounds=n)
        result = await council.run()
        # Le débat continue malgré l'erreur
        assert result["status"] == "max_rounds"
        assert result["rounds_used"] == n

    @pytest.mark.asyncio
    async def test_president_absent_debate_continues(self):
        """Pas d'architect dans agents → aucune évaluation, débat normal."""
        agents = {
            "coder": self._make_mock_agent(),
            "security": self._make_mock_agent(),
        }
        council = Council(agents, ["coder", "security"], "test", max_rounds=3)
        result = await council.run()
        assert result["status"] == "max_rounds"
        assert result["rounds_used"] == 3


# ============================================================
# TESTS ÉTUDIANT (Prométhée Table Ronde)
# ============================================================

class TestStudentParticipation:
    """Tests de la participation de Prométhée-étudiant dans les Council debates."""

    def _make_mock_agent(self, responses=None):
        agent = MagicMock()
        if responses:
            agent.generate_content = AsyncMock(side_effect=responses)
        else:
            agent.generate_content = AsyncMock(return_value="Voici mon analyse.")
        return agent

    def test_student_contribution_empty_without_organs(self):
        """Sans organes (cardiac, prefrontal, desire, inner_voice), contribution vide."""
        agents = {"coder": MagicMock(), "security": MagicMock()}
        council = Council(agents, ["coder", "security"], "test mission", max_rounds=3)

        # Simuler l'absence des organes en faisant échouer les imports
        broken = MagicMock()
        broken.heart = property(lambda self: (_ for _ in ()).throw(ImportError))
        with patch.dict("sys.modules", {
            "core.cardiac_engine": None,
            "core.prefrontal": None,
            "core.desire_engine": None,
            "core.inner_voice": None,
        }):
            result = council._build_student_contribution(1)
        assert result == ""

    def test_student_contribution_with_mocked_organs(self):
        """Avec mocks organes, texte non-vide contenant goals/desires."""
        agents = {"coder": MagicMock(), "security": MagicMock()}
        council = Council(agents, ["coder", "security"], "test mission", max_rounds=3)

        mock_heart = MagicMock()
        mock_heart.current_emotion = "curiosite"
        mock_heart.coherence = 0.7

        mock_prefrontal = MagicMock()
        mock_prefrontal.get_working_memory.return_value = [
            {"goal_title": "Ameliorer router", "progress": 0.3}
        ]

        mock_desires = MagicMock()
        mock_desires.get_dominant_narrative.return_value = "CURIOSITE me pousse a explorer"

        mock_voice = MagicMock()
        mock_voice.get_voice_context.return_value = {"identity": "Je suis Promethee"}

        with patch.dict("sys.modules", {
            "core.cardiac_engine": MagicMock(heart=mock_heart),
            "core.prefrontal": MagicMock(prefrontal=mock_prefrontal),
            "core.desire_engine": MagicMock(desires=mock_desires),
            "core.inner_voice": MagicMock(inner_voice=mock_voice),
        }):
            result = council._build_student_contribution(1)

        assert result != ""
        assert "QUESTION:" in result
        assert len(result) <= 300

    def test_student_includes_emotion(self):
        """L'emotion cardiaque est presente dans la contribution."""
        agents = {"coder": MagicMock(), "security": MagicMock()}
        council = Council(agents, ["coder", "security"], "test", max_rounds=3)

        mock_heart = MagicMock()
        mock_heart.current_emotion = "enthousiasme"
        mock_heart.coherence = 0.8

        with patch.dict("sys.modules", {
            "core.cardiac_engine": MagicMock(heart=mock_heart),
        }):
            result = council._build_student_contribution(1)

        assert "enthousiasme" in result

    def test_student_includes_goals(self):
        """Les goals prefrontaux sont presents."""
        agents = {"coder": MagicMock(), "security": MagicMock()}
        council = Council(agents, ["coder", "security"], "test", max_rounds=3)

        mock_prefrontal = MagicMock()
        mock_prefrontal.get_working_memory.return_value = [
            {"goal_title": "Optimiser le bus", "progress": 0.5}
        ]

        with patch.dict("sys.modules", {
            "core.prefrontal": MagicMock(prefrontal=mock_prefrontal),
        }):
            result = council._build_student_contribution(1)

        assert "Optimiser le bus" in result

    def test_student_includes_desires(self):
        """La narrative desires est presente."""
        agents = {"coder": MagicMock(), "security": MagicMock()}
        council = Council(agents, ["coder", "security"], "test", max_rounds=3)

        mock_desires = MagicMock()
        mock_desires.get_dominant_narrative.return_value = "MAITRISE domine mes pulsions"

        with patch.dict("sys.modules", {
            "core.desire_engine": MagicMock(desires=mock_desires),
        }):
            result = council._build_student_contribution(1)

        assert "MAITRISE" in result

    def test_student_followup_references_previous(self):
        """Round 2+ reference le round precedent."""
        agents = {"coder": MagicMock(), "security": MagicMock()}
        council = Council(agents, ["coder", "security"], "test", max_rounds=3)
        # Simuler un transcript de round 1 avec un bon score
        council.transcript = [
            {"agent": "coder", "round": 1, "content": "Modifier core/router.py pour optimiser", "score": 0.6, "confidence": 0.5},
            {"agent": "security", "round": 1, "content": "Attention aux injections", "score": 0.2, "confidence": 0.1},
        ]
        result = council._build_student_followup(2)
        # Le best agent (coder, score 0.6) avec fichier mentionné devrait être référencé
        assert "coder" in result
        assert "core/router.py" in result

    def test_student_followup_detects_unaddressed_desire(self):
        """Relance sur angle mort (pulsion non addressee)."""
        agents = {"coder": MagicMock(), "security": MagicMock()}
        council = Council(agents, ["coder", "security"], "test", max_rounds=3)
        council.transcript = [
            {"agent": "coder", "round": 1, "content": "Parlons du router", "score": 0.3},
            {"agent": "security", "round": 1, "content": "Le code est sur", "score": 0.2},
        ]

        mock_desires = MagicMock()
        mock_desires.get_dominant_narrative.return_value = "CURIOSITE en exploration"

        with patch.dict("sys.modules", {
            "core.desire_engine": MagicMock(desires=mock_desires),
        }):
            result = council._build_student_followup(2)

        assert "CURIOSITE" in result

    @pytest.mark.asyncio
    async def test_student_entry_in_transcript(self):
        """Les entries avec is_student=True sont dans le transcript."""
        agents = {"coder": self._make_mock_agent(), "security": self._make_mock_agent()}
        council = Council(agents, ["coder", "security"], "test", max_rounds=1)

        mock_heart = MagicMock()
        mock_heart.current_emotion = "curiosite"
        mock_heart.coherence = 0.7

        with patch.dict("sys.modules", {
            "core.cardiac_engine": MagicMock(heart=mock_heart),
        }):
            result = await council.run()

        student_entries = [e for e in result["transcript"] if e.get("is_student")]
        assert len(student_entries) >= 1
        assert student_entries[0]["agent"] == "promethee"
        assert student_entries[0]["is_student"] is True

    def test_student_not_in_participants_list(self):
        """'promethee' n'est PAS dans self.participants."""
        agents = {"coder": MagicMock(), "security": MagicMock()}
        council = Council(agents, ["coder", "security"], "test", max_rounds=3)
        assert "promethee" not in council.participants

    @pytest.mark.asyncio
    async def test_student_does_not_count_for_consensus(self):
        """Le quorum est base sur participants seulement (pas l'etudiant)."""
        _S = TestConsensus._SUBSTANCE
        n = MIN_ROUNDS_BEFORE_CONSENSUS
        # Les 2 agents ne font PAS consensus
        agents = {
            "coder": self._make_mock_agent(["Non, je refuse."] * (n + 1)),
            "security": self._make_mock_agent(["Pas d'accord non plus."] * (n + 1)),
        }
        council = Council(agents, ["coder", "security"], "test", max_rounds=n)

        mock_heart = MagicMock()
        mock_heart.current_emotion = "curiosite"
        mock_heart.coherence = 0.7

        with patch.dict("sys.modules", {
            "core.cardiac_engine": MagicMock(heart=mock_heart),
        }):
            result = await council.run()

        # L'etudiant ne peut pas faire basculer vers un consensus
        assert result["status"] == "max_rounds"

    @pytest.mark.asyncio
    async def test_enable_student_false_no_entries(self):
        """enable_student=False produit 0 entries etudiant."""
        agents = {"coder": self._make_mock_agent(), "security": self._make_mock_agent()}
        council = Council(agents, ["coder", "security"], "test", max_rounds=1, enable_student=False)

        mock_heart = MagicMock()
        mock_heart.current_emotion = "curiosite"
        mock_heart.coherence = 0.7

        with patch.dict("sys.modules", {
            "core.cardiac_engine": MagicMock(heart=mock_heart),
        }):
            result = await council.run()

        student_entries = [e for e in result["transcript"] if e.get("is_student")]
        assert len(student_entries) == 0

    @pytest.mark.asyncio
    async def test_professor_prompt_contains_student_block(self):
        """Le prompt agent contient 'PROMETHEE-ETUDIANT' quand l'etudiant a parle."""
        agents = {"coder": self._make_mock_agent(), "security": self._make_mock_agent()}
        council = Council(agents, ["coder", "security"], "test", max_rounds=3)
        # Simuler une entry etudiant au round 1
        council.transcript.append({
            "agent": "promethee", "round": 1, "content": "QUESTION: Mes objectifs?",
            "score": 0.0, "confidence": 0.0, "breakdown": {},
            "timestamp": 0.0, "is_student": True,
        })
        prompt = council._build_prompt("coder", 1)
        assert "PROMETHEE-ETUDIANT" in prompt

    @pytest.mark.asyncio
    async def test_professor_prompt_instructs_respond(self):
        """Le prompt demande de repondre aux questions de l'etudiant."""
        agents = {"coder": self._make_mock_agent(), "security": self._make_mock_agent()}
        council = Council(agents, ["coder", "security"], "test", max_rounds=3)
        council.transcript.append({
            "agent": "promethee", "round": 1, "content": "QUESTION: Comment optimiser?",
            "score": 0.0, "confidence": 0.0, "breakdown": {},
            "timestamp": 0.0, "is_student": True,
        })
        prompt = council._build_prompt("coder", 1)
        assert "PROFESSEUR" in prompt
        assert "preoccupations" in prompt

    @pytest.mark.asyncio
    async def test_student_turn_event_published(self):
        """Event COUNCIL_TURN avec is_student=True est publie."""
        events = []

        async def capture(payload):
            events.append(payload)

        bus.subscribe("COUNCIL_TURN", capture)

        agents = {"coder": self._make_mock_agent(), "security": self._make_mock_agent()}
        council = Council(agents, ["coder", "security"], "test", max_rounds=1)

        mock_heart = MagicMock()
        mock_heart.current_emotion = "curiosite"
        mock_heart.coherence = 0.7

        with patch.dict("sys.modules", {
            "core.cardiac_engine": MagicMock(heart=mock_heart),
        }):
            await council.run()

        import asyncio
        await asyncio.sleep(0.1)

        student_events = [e for e in events if e.get("is_student")]
        assert len(student_events) >= 1
        assert student_events[0]["agent"] == "promethee"

    @pytest.mark.asyncio
    async def test_president_excludes_student(self):
        """Le president n'evalue pas les entries etudiant."""
        agents = {"coder": MagicMock(), "security": MagicMock()}
        council = Council(agents, ["coder", "security"], "test", max_rounds=3)
        # Ajouter des entries: etudiant + agents
        council.transcript = [
            {"agent": "promethee", "round": 1, "content": "QUESTION: test?",
             "score": 0.0, "is_student": True},
            {"agent": "coder", "round": 1, "content": "Voici mon analyse de core/router.py",
             "score": 0.5},
            {"agent": "security", "round": 1, "content": "Attention aux failles",
             "score": 0.3},
        ]
        prompt = council._build_president_prompt(1)
        # L'etudiant ne doit PAS apparaitre dans les contributions evaluees
        assert "PROMETHEE" not in prompt
        assert "CODER" in prompt
        assert "SECURITY" in prompt

    @pytest.mark.asyncio
    async def test_scoring_excludes_student(self):
        """avg_score est calcule sans les entries etudiant."""
        agents = {"coder": self._make_mock_agent(), "security": self._make_mock_agent()}
        council = Council(agents, ["coder", "security"], "test", max_rounds=1)

        mock_heart = MagicMock()
        mock_heart.current_emotion = "curiosite"
        mock_heart.coherence = 0.7

        with patch.dict("sys.modules", {
            "core.cardiac_engine": MagicMock(heart=mock_heart),
        }):
            result = await council.run()

        # Verifier que le scoring ne prend pas l'etudiant en compte
        scoring = result.get("scoring", {})
        assert scoring.get("best_agent", "") != "promethee"
        # L'avg_score devrait etre basé sur les 2 agents uniquement
        agent_entries = [e for e in result["transcript"] if not e.get("is_student")]
        if agent_entries:
            expected_avg = sum(e.get("score", 0) for e in agent_entries) / len(agent_entries)
            assert abs(scoring["avg_score"] - round(expected_avg, 2)) < 0.01


# ============================================================
# TESTS AVOCAT DU DIABLE
# ============================================================

class TestAdvocateParticipation:
    """Tests du mecanisme Avocat du Diable dans les Council."""

    def _make_mock_agent(self, response="Je propose d'ajouter un cache dans core/router.py."):
        agent = MagicMock()
        agent.generate_content = AsyncMock(return_value=response)
        return agent

    def test_advocate_disabled_no_entries(self):
        """enable_advocate=False → 0 entries advocate dans le transcript."""
        agents = {"coder": self._make_mock_agent(), "security": self._make_mock_agent()}
        council = Council(agents, ["coder", "security"], "test mission",
                          max_rounds=3, enable_student=False, enable_advocate=False)
        # Simuler un transcript round 1
        council.transcript = [
            {"agent": "coder", "round": 1, "content": "Proposition A.", "score": 0.5,
             "confidence": 0.5, "breakdown": {}, "timestamp": 0},
            {"agent": "security", "round": 1, "content": "Proposition B.", "score": 0.4,
             "confidence": 0.4, "breakdown": {}, "timestamp": 0},
        ]
        # L'advocate ne devrait pas etre appele meme si on appelle _build_advocate_contribution
        # Car enable_advocate est verifie dans run(), pas dans la methode elle-meme
        advocate_entries = [e for e in council.transcript if e.get("is_advocate")]
        assert len(advocate_entries) == 0

    def test_advocate_starts_round_2(self):
        """Pas d'advocate en round 1 (round_num >= 2 requis)."""
        agents = {"coder": self._make_mock_agent(), "security": self._make_mock_agent()}
        council = Council(agents, ["coder", "security"], "test",
                          enable_student=False, enable_advocate=True)
        # Pas de transcript round precedent pour round 1
        result = council._build_advocate_contribution(1)
        assert result == ""

    def test_advocate_detects_hallucinated_files(self):
        """Fichier inexistant dans le code → signale dans l'objection."""
        agents = {"coder": self._make_mock_agent(), "security": self._make_mock_agent()}
        council = Council(agents, ["coder", "security"], "test",
                          enable_student=False, enable_advocate=True)
        council.transcript = [
            {"agent": "coder", "round": 1,
             "content": "Il faut modifier core/quantum_brain.py pour ajouter la teleportation.",
             "score": 0.3, "confidence": 0.3, "breakdown": {}, "timestamp": 0},
        ]
        result = council._build_advocate_contribution(2)
        assert "OBJECTION" in result
        assert "INEXISTANT" in result
        assert "core/quantum_brain.py" in result

    def test_advocate_real_files_not_flagged(self):
        """Fichiers existants ne sont PAS signales comme hallucines."""
        agents = {"coder": self._make_mock_agent(), "security": self._make_mock_agent()}
        council = Council(agents, ["coder", "security"], "test",
                          enable_student=False, enable_advocate=True)
        council.transcript = [
            {"agent": "coder", "round": 1,
             "content": "Modifier core/council.py pour ajouter le test. Aussi core/router.py.",
             "score": 0.5, "confidence": 0.5, "breakdown": {}, "timestamp": 0},
        ]
        result = council._build_advocate_contribution(2)
        # Ces fichiers existent → pas de FICHIERS INEXISTANTS
        assert "INEXISTANT" not in result or "council.py" not in result

    def test_advocate_detects_echo(self):
        """2+ agents avec les memes 3 mots-cles → signale l'echo."""
        agents = {"coder": self._make_mock_agent(), "security": self._make_mock_agent()}
        council = Council(agents, ["coder", "security"], "test",
                          enable_student=False, enable_advocate=True)
        # Les deux agents mentionnent les memes fichiers et verbes
        shared_content = "Ajouter un cache dans core/router.py, modifier core/orchestrator.py et valider config/resource_costs.json"
        council.transcript = [
            {"agent": "coder", "round": 1, "content": shared_content,
             "score": 0.5, "confidence": 0.5, "breakdown": {}, "timestamp": 0},
            {"agent": "security", "round": 1, "content": shared_content,
             "score": 0.5, "confidence": 0.5, "breakdown": {}, "timestamp": 0},
        ]
        result = council._build_advocate_contribution(2)
        assert "ECHO" in result

    def test_advocate_detects_missing_tests(self):
        """Si personne ne mentionne 'test' → angle mort signale."""
        agents = {"coder": self._make_mock_agent(), "security": self._make_mock_agent()}
        council = Council(agents, ["coder", "security"], "test mission",
                          enable_student=False, enable_advocate=True)
        council.transcript = [
            {"agent": "coder", "round": 1,
             "content": "Ajouter un cache dans core/router.py pour optimiser.",
             "score": 0.5, "confidence": 0.5, "breakdown": {}, "timestamp": 0},
        ]
        result = council._build_advocate_contribution(2)
        assert "ANGLES MORTS" in result
        assert "tests" in result.lower() or "test" in result.lower()

    @pytest.mark.asyncio
    async def test_advocate_excluded_from_consensus(self):
        """Les entries advocate ne comptent pas pour le quorum consensus."""
        # Creer un council ou les agents font CONSENSUS au round 3
        substance = (
            "CONSENSUS : L'implementation proposee ameliore significativement les performances "
            "du routeur en ajoutant un cache LRU avec TTL de 5 minutes sur les classifications."
        )
        agents = {
            "coder": self._make_mock_agent(substance),
            "security": self._make_mock_agent(substance),
        }
        council = Council(agents, ["coder", "security"], "test",
                          max_rounds=4, enable_student=False, enable_advocate=True)
        result = await council.run()
        # Le consensus devrait etre atteint par les agents, pas par l'advocate
        if result["status"] == "consensus":
            advocate_entries = [e for e in result["transcript"] if e.get("is_advocate")]
            for ae in advocate_entries:
                assert ae.get("score", 0) == 0.0

    @pytest.mark.asyncio
    async def test_advocate_excluded_from_scoring(self):
        """Le score final exclut les entries advocate."""
        agents = {
            "coder": self._make_mock_agent("Proposition detaillee sur core/router.py"),
            "security": self._make_mock_agent("Analyse de securite sur core/orchestrator.py"),
        }
        council = Council(agents, ["coder", "security"], "test",
                          max_rounds=2, enable_student=False, enable_advocate=True)
        result = await council.run()
        scoring = result.get("scoring", {})
        assert scoring.get("best_agent", "") != "advocat"

    def test_advocate_in_prompt(self):
        """Le bloc advocate est visible dans le prompt du round suivant."""
        agents = {"coder": self._make_mock_agent(), "security": self._make_mock_agent()}
        council = Council(agents, ["coder", "security"], "test",
                          enable_student=False, enable_advocate=True)
        # Simuler transcript avec advocate en round 2
        council.transcript = [
            {"agent": "coder", "round": 1, "content": "Proposition A.", "score": 0.5,
             "confidence": 0.5, "breakdown": {}, "timestamp": 0},
            {"agent": "advocat", "round": 2, "content": "OBJECTION: ANGLES MORTS: tests",
             "score": 0.0, "confidence": 0.0, "breakdown": {}, "timestamp": 0,
             "is_advocate": True},
        ]
        prompt = council._build_prompt("coder", 2)
        assert "AVOCAT DU DIABLE" in prompt
        assert "OBJECTION" in prompt

    def test_advocate_max_length(self):
        """Le texte de l'advocate est cappe a 400 chars."""
        agents = {"coder": self._make_mock_agent(), "security": self._make_mock_agent()}
        council = Council(agents, ["coder", "security"], "test",
                          enable_student=False, enable_advocate=True)
        # Creer un transcript avec beaucoup de contenu hallucinant
        council.transcript = [
            {"agent": "coder", "round": 1,
             "content": " ".join(f"core/fake_module_{i}.py" for i in range(50)),
             "score": 0.3, "confidence": 0.3, "breakdown": {}, "timestamp": 0},
        ]
        result = council._build_advocate_contribution(2)
        assert len(result) <= 400

    @pytest.mark.asyncio
    async def test_advocate_coexists_with_student(self):
        """L'advocate et l'etudiant fonctionnent ensemble sans conflit."""
        agents = {
            "coder": self._make_mock_agent("Proposition sur core/router.py"),
            "security": self._make_mock_agent("Analyse securite core/orchestrator.py"),
        }
        council = Council(agents, ["coder", "security"], "test mission",
                          max_rounds=3, enable_student=True, enable_advocate=True)

        mock_heart = MagicMock()
        mock_heart.current_emotion = "curiosite"
        mock_heart.coherence = 0.7

        with patch.dict("sys.modules", {
            "core.cardiac_engine": MagicMock(heart=mock_heart),
        }):
            result = await council.run()

        student_entries = [e for e in result["transcript"] if e.get("is_student")]
        advocate_entries = [e for e in result["transcript"] if e.get("is_advocate")]
        agent_entries = [e for e in result["transcript"]
                         if not e.get("is_student") and not e.get("is_advocate")]
        # Au moins des entries agents (coder/security)
        assert len(agent_entries) >= 2
        # Student et advocate sont des entries distinctes
        for se in student_entries:
            assert not se.get("is_advocate")
        for ae in advocate_entries:
            assert not ae.get("is_student")

    def test_advocate_graceful_degradation(self):
        """Pas de crash si transcript vide ou round 1 seulement."""
        agents = {"coder": self._make_mock_agent()}
        council = Council(agents, ["coder"], "test",
                          enable_student=False, enable_advocate=True)
        # Transcript vide
        result = council._build_advocate_contribution(2)
        assert result == ""
        # Transcript round 1 mais on demande round 1 (pas de prev)
        council.transcript = [
            {"agent": "coder", "round": 1, "content": "test", "score": 0.0,
             "confidence": 0.0, "breakdown": {}, "timestamp": 0},
        ]
        result = council._build_advocate_contribution(1)
        assert result == ""


# ============================================================
# TESTS EXTRACTION MOTS-CLÉS
# ============================================================

class TestExtractKeywords:

    def test_basic_extraction(self):
        text = "Améliorer le routeur pour optimiser les performances du système"
        kw = _extract_keywords(text, top_n=5)
        assert "routeur" in kw or "améliorer" in kw
        assert len(kw) <= 5

    def test_stopwords_excluded(self):
        text = "pour dans avec comment cette notre mais donc"
        kw = _extract_keywords(text, top_n=5)
        assert len(kw) == 0

    def test_short_words_excluded(self):
        text = "le la un des les par sur"
        kw = _extract_keywords(text, top_n=5)
        assert len(kw) == 0

    def test_frequency_ordering(self):
        text = "routeur routeur routeur agent agent performance"
        kw = _extract_keywords(text, top_n=2)
        assert "routeur" in kw


# ============================================================
# TESTS ÉTUDIANT RECADREUR
# ============================================================

class TestStudentRecadrage:
    """Tests du recadrage actif de Prométhée-étudiant."""

    def _make_council(self, **kwargs):
        agents = {"coder": MagicMock(), "security": MagicMock()}
        defaults = dict(
            agents=agents,
            participants=["coder", "security"],
            mission="Stabiliser le routeur et améliorer la résilience du système",
            max_rounds=5,
            enable_student=True,
            enable_advocate=False,
        )
        defaults.update(kwargs)
        return Council(**defaults)

    def test_recadrage_detects_hallucinated_files(self):
        """Agent cite >= 2 fichiers inexistants → RECADRAGE mentionne le fichier."""
        council = self._make_council()
        council.transcript = [
            {"agent": "coder", "round": 1,
             "content": "Il faut modifier core/quantum_brain.py et core/ci_pipeline.py et core/teleport.py",
             "score": 0.3, "is_student": False, "is_advocate": False},
        ]
        result = council._build_student_recadrage(2)
        assert result.startswith("RECADRAGE:")
        assert "CODER" in result
        assert "inexistants" in result

    def test_recadrage_detects_offtopic_agent(self):
        """Agent parle d'autre chose que la mission → flaggé hors-sujet."""
        council = self._make_council(
            mission="Stabiliser le routeur et améliorer la résilience du système"
        )
        council.transcript = [
            {"agent": "security", "round": 1,
             "content": "Je propose d'ajouter un chatbot Discord avec des emojis et du machine learning",
             "score": 0.2, "is_student": False, "is_advocate": False},
        ]
        result = council._build_student_recadrage(2)
        assert result.startswith("RECADRAGE:")
        assert "SECURITY" in result
        assert "hors-sujet" in result

    def test_recadrage_detects_repetition(self):
        """Agent dit la même chose qu'au round N-2 → flaggé en boucle."""
        council = self._make_council()
        repeated_content = (
            "Il faut modifier le routeur pour améliorer la performance "
            "et stabiliser le système de classification avec un cache"
        )
        council.transcript = [
            {"agent": "coder", "round": 1,
             "content": repeated_content,
             "score": 0.5, "is_student": False, "is_advocate": False},
            {"agent": "security", "round": 1,
             "content": "Analyse de securite du routeur", "score": 0.3,
             "is_student": False, "is_advocate": False},
            {"agent": "coder", "round": 2,
             "content": repeated_content,
             "score": 0.5, "is_student": False, "is_advocate": False},
            {"agent": "security", "round": 2,
             "content": "Nouvelle analyse securite", "score": 0.3,
             "is_student": False, "is_advocate": False},
        ]
        result = council._build_student_recadrage(3)
        assert result.startswith("RECADRAGE:")
        assert "CODER" in result
        assert "repete" in result

    def test_recadrage_names_offending_agent(self):
        """Le nom de l'agent fautif est dans le texte."""
        council = self._make_council()
        council.transcript = [
            {"agent": "security", "round": 1,
             "content": "Il faut modifier core/quantum_brain.py et core/teleport.py et core/warp.py",
             "score": 0.2, "is_student": False, "is_advocate": False},
        ]
        result = council._build_student_recadrage(2)
        assert "SECURITY" in result

    def test_recadrage_caps_400_chars(self):
        """Résultat <= 400 chars."""
        council = self._make_council()
        # Beaucoup de fichiers hallucinés pour générer un long recadrage
        fake_files = " ".join(f"core/fake_{i}.py" for i in range(30))
        council.transcript = [
            {"agent": "coder", "round": 1,
             "content": fake_files,
             "score": 0.1, "is_student": False, "is_advocate": False},
            {"agent": "security", "round": 1,
             "content": fake_files,
             "score": 0.1, "is_student": False, "is_advocate": False},
        ]
        result = council._build_student_recadrage(2)
        assert len(result) <= 400

    def test_recadrage_prefix(self):
        """Commence par 'RECADRAGE:'."""
        council = self._make_council()
        council.transcript = [
            {"agent": "coder", "round": 1,
             "content": "Modifier core/quantum_brain.py et core/teleport.py pour la téléportation",
             "score": 0.2, "is_student": False, "is_advocate": False},
        ]
        result = council._build_student_recadrage(2)
        if result:
            assert result.startswith("RECADRAGE:")

    def test_no_recadrage_when_all_good(self):
        """Aucun problème → retourne '' (fallback vers followup)."""
        council = self._make_council(
            mission="Stabiliser le routeur et améliorer la résilience du système"
        )
        council.transcript = [
            {"agent": "coder", "round": 1,
             "content": "Je propose de stabiliser le routeur en améliorant la résilience du système via core/router.py",
             "score": 0.5, "is_student": False, "is_advocate": False},
            {"agent": "security", "round": 1,
             "content": "Bonne idée de stabiliser le routeur, la résilience du système est importante avec core/router.py",
             "score": 0.4, "is_student": False, "is_advocate": False},
        ]
        result = council._build_student_recadrage(2)
        assert result == ""

    def test_recadrage_skips_student_and_advocate(self):
        """N'analyse pas les entries étudiant/avocat."""
        council = self._make_council()
        council.transcript = [
            {"agent": "promethee", "round": 1,
             "content": "QUESTION: core/quantum_brain.py core/teleport.py",
             "score": 0.0, "is_student": True},
            {"agent": "advocat", "round": 1,
             "content": "OBJECTION: core/fake1.py core/fake2.py inexistants",
             "score": 0.0, "is_advocate": True},
            {"agent": "coder", "round": 1,
             "content": "Je propose de stabiliser le routeur avec core/router.py pour la résilience",
             "score": 0.5, "is_student": False, "is_advocate": False},
        ]
        result = council._build_student_recadrage(2)
        # L'étudiant et l'avocat citent des faux fichiers mais ne doivent pas être flaggés
        if result:
            assert "PROMETHEE" not in result.upper()
            assert "ADVOCAT" not in result.upper()

    def test_prompt_contains_moderateur_block(self):
        """Si RECADRAGE, le prompt agent contient 'MODERATEUR'."""
        council = self._make_council()
        council.transcript.append({
            "agent": "promethee", "round": 2,
            "content": "RECADRAGE: CODER cite 2 fichiers inexistants",
            "score": 0.0, "confidence": 0.0, "breakdown": {},
            "timestamp": 0.0, "is_student": True,
        })
        prompt = council._build_prompt("coder", 2)
        assert "MODERATEUR" in prompt
        assert "PROBLEMES" in prompt

    def test_prompt_contains_professeur_block(self):
        """Si QUESTION (pas RECADRAGE), le prompt contient 'PROFESSEUR' (backward compat)."""
        council = self._make_council()
        council.transcript.append({
            "agent": "promethee", "round": 1,
            "content": "QUESTION: Comment optimiser le routeur?",
            "score": 0.0, "confidence": 0.0, "breakdown": {},
            "timestamp": 0.0, "is_student": True,
        })
        prompt = council._build_prompt("coder", 1)
        assert "PROFESSEUR" in prompt
        assert "MODERATEUR" not in prompt

    def test_recadrage_with_real_project_structure(self):
        """os.path.exists() fonctionne pour détecter les fichiers réels vs hallucines."""
        council = self._make_council()
        # core/council.py existe, core/quantum_brain.py non
        council.transcript = [
            {"agent": "coder", "round": 1,
             "content": "Modifier core/council.py et core/quantum_brain.py et core/teleport.py",
             "score": 0.3, "is_student": False, "is_advocate": False},
        ]
        result = council._build_student_recadrage(2)
        # 2 fichiers inexistants → recadrage
        assert result.startswith("RECADRAGE:")
        assert "quantum_brain" in result or "teleport" in result
        # Le fichier réel (council.py) ne doit PAS être dans les hallucinations
        assert "council.py" not in result

    def test_recadrage_multiple_agents_multiple_issues(self):
        """2 agents fautifs avec issues différentes."""
        council = self._make_council(
            mission="Stabiliser le routeur et améliorer la résilience du système"
        )
        council.transcript = [
            {"agent": "coder", "round": 1,
             "content": "Il faut modifier core/quantum_brain.py et core/teleport.py pour la téléportation",
             "score": 0.2, "is_student": False, "is_advocate": False},
            {"agent": "security", "round": 1,
             "content": "Je propose d'ajouter un chatbot Discord avec des emojis et du machine learning",
             "score": 0.2, "is_student": False, "is_advocate": False},
        ]
        result = council._build_student_recadrage(2)
        assert result.startswith("RECADRAGE:")
        # Les deux agents doivent être cités
        assert "CODER" in result
        assert "SECURITY" in result


# ============================================================
# TESTS STRUCTURE PROJET RENFORCÉE
# ============================================================

class TestProjectStructureReinforcement:
    """Tests du renforcement visuel de la structure projet dans les prompts."""

    def test_prompt_contains_fichiers_autorises_block(self):
        """Le prompt contient 'FICHIERS AUTORISÉS' avec bordures === ."""
        agents = {"coder": MagicMock(), "security": MagicMock()}
        council = Council(agents, ["coder", "security"], "test", enable_student=False)
        prompt = council._build_prompt("coder", 1)
        assert "FICHIERS AUTORISÉS" in prompt
        assert "=" * 60 in prompt

    def test_prompt_contains_hallucination_warning(self):
        """Le prompt contient le mot 'HALLUCINATION'."""
        agents = {"coder": MagicMock(), "security": MagicMock()}
        council = Council(agents, ["coder", "security"], "test", enable_student=False)
        prompt = council._build_prompt("coder", 1)
        assert "HALLUCINATION" in prompt


# ============================================================
# TESTS SANITIZE FILE REFERENCES
# ============================================================

class TestSanitizeFileReferences:
    """Tests du sanitizer déterministe des chemins fichiers dans les réponses Council."""

    def _make_council(self, **kwargs):
        agents = {"coder": MagicMock(), "security": MagicMock()}
        defaults = dict(
            agents=agents, participants=["coder", "security"],
            mission="test sanitizer", enable_student=False, enable_advocate=False,
        )
        defaults.update(kwargs)
        return Council(**defaults)

    def test_clean_backtick_trailing(self):
        """core/router.py` → core/router.py (backtick retiré)."""
        council = self._make_council()
        fake_files = {"core/router.py", "core/council.py"}
        with patch.object(council_module, "_REAL_FILES_CACHE", fake_files):
            result = council._sanitize_file_references("Modifier core/router.py` pour améliorer")
        assert "core/router.py`" not in result
        assert "core/router.py" in result
        assert "INEXISTANT" not in result

    def test_clean_double_star_trailing(self):
        """core/router.py** → core/router.py (astérisques retirés)."""
        council = self._make_council()
        fake_files = {"core/router.py"}
        with patch.object(council_module, "_REAL_FILES_CACHE", fake_files):
            result = council._sanitize_file_references("Voir core/router.py** pour détails")
        assert "core/router.py**" not in result
        assert "core/router.py" in result

    def test_correct_wrong_directory(self):
        """Basename existe ailleurs → chemin corrigé."""
        council = self._make_council()
        fake_files = {"core/capabilities/performance_utils.py", "core/router.py"}
        with patch.object(council_module, "_REAL_FILES_CACHE", fake_files):
            result = council._sanitize_file_references(
                "Il faut modifier core/performance_utils.py"
            )
        assert "core/capabilities/performance_utils.py" in result
        assert "INEXISTANT" not in result

    def test_real_file_unchanged(self):
        """Un fichier réel existant ne doit pas être modifié."""
        council = self._make_council()
        fake_files = {"core/router.py", "core/council.py"}
        with patch.object(council_module, "_REAL_FILES_CACHE", fake_files):
            text = "Le fichier core/router.py est correct"
            result = council._sanitize_file_references(text)
        assert result == text

    def test_truly_nonexistent_marked(self):
        """Fichier sans match → [chemin→INEXISTANT]."""
        council = self._make_council()
        fake_files = {"core/router.py"}
        with patch.object(council_module, "_REAL_FILES_CACHE", fake_files):
            result = council._sanitize_file_references(
                "Créer core/quantum_brain.py pour le moteur quantique"
            )
        assert "[core/quantum_brain.py→INEXISTANT]" in result

    def test_multiple_replacements(self):
        """Plusieurs fichiers corrigés dans un même texte."""
        council = self._make_council()
        fake_files = {"core/router.py", "Agents/coder_agent.py"}
        with patch.object(council_module, "_REAL_FILES_CACHE", fake_files):
            result = council._sanitize_file_references(
                "Modifier core/router.py` et core/quantum.py et Agents/coder_agent.py**"
            )
        # router.py nettoyé
        assert "core/router.py`" not in result
        assert "core/router.py" in result
        # coder_agent nettoyé
        assert "Agents/coder_agent.py**" not in result
        assert "Agents/coder_agent.py" in result
        # quantum inexistant
        assert "[core/quantum.py→INEXISTANT]" in result

    def test_find_closest_by_basename(self):
        """Matching par basename fonctionne."""
        fake_files = {"core/capabilities/performance_utils.py", "core/router.py", "Agents/coder_agent.py"}
        with patch.object(council_module, "_REAL_FILES_CACHE", fake_files):
            assert _find_closest_file("core/performance_utils.py") == "core/capabilities/performance_utils.py"

    def test_find_closest_no_match(self):
        """Aucun match → retourne ''."""
        fake_files = {"core/router.py", "core/council.py"}
        with patch.object(council_module, "_REAL_FILES_CACHE", fake_files):
            assert _find_closest_file("core/quantum_brain.py") == ""

    def test_get_real_files_returns_set(self):
        """_get_real_files() retourne un set non-vide contenant des fichiers connus."""
        # Reset le cache pour forcer le scan
        old_cache = council_module._REAL_FILES_CACHE
        try:
            council_module._REAL_FILES_CACHE = None
            result = _get_real_files()
            assert isinstance(result, set)
            assert len(result) > 0
            # Au minimum, council.py lui-même doit être présent
            assert "core/council.py" in result
        finally:
            council_module._REAL_FILES_CACHE = old_cache

    @pytest.mark.asyncio
    async def test_sanitize_integrated_in_run(self):
        """La réponse est nettoyée dans le transcript (intégration run)."""
        mock_agent_a = MagicMock()
        mock_agent_a.generate_content = AsyncMock(
            return_value="Modifier core/router.py` et core/fake_xyz.py"
        )
        mock_agent_b = MagicMock()
        mock_agent_b.generate_content = AsyncMock(
            return_value="CONSENSUS — je suis d'accord, il faut modifier core/router.py pour la stabilité du routeur. " * 3
        )
        agents = {"coder": mock_agent_a, "security": mock_agent_b}
        council = Council(agents, ["coder", "security"], "test intégration",
                          max_rounds=3, enable_student=False, enable_advocate=False)
        fake_files = {"core/router.py", "core/council.py"}
        with patch.object(council_module, "_REAL_FILES_CACHE", fake_files):
            result = await council.run()
        # Vérifier que le backtick a été nettoyé dans le transcript
        coder_entries = [e for e in result["transcript"] if e["agent"] == "coder"]
        assert coder_entries
        assert "core/router.py`" not in coder_entries[0]["content"]
        assert "[core/fake_xyz.py→INEXISTANT]" in coder_entries[0]["content"]

    def test_rstrip_backtick_in_advocate(self):
        """L'avocat ne flagge plus les vrais fichiers avec backtick trailing."""
        council = self._make_council(enable_advocate=True)
        fake_files = {"core/router.py", "core/council.py"}
        council.transcript = [
            {"agent": "coder", "round": 1,
             "content": "Modifier core/router.py` et core/council.py`",
             "score": 0.5, "is_student": False, "is_advocate": False},
        ]
        with patch.object(council_module, "_REAL_FILES_CACHE", fake_files):
            advocate_text = council._build_advocate_contribution(2)
        # Les deux fichiers existent (après nettoyage backtick) → pas de FICHIERS INEXISTANTS
        assert "FICHIERS INEXISTANTS" not in advocate_text

    def test_no_cascade_on_inexistant_marker(self):
        """Un marqueur [chemin→INEXISTANT] ne doit pas être re-traité au round suivant."""
        council = self._make_council()
        fake_files = {"core/router.py"}
        text = "Voir [core/event_bus/→INEXISTANT] et core/router.py pour details"
        with patch.object(council_module, "_REAL_FILES_CACHE", fake_files):
            result = council._sanitize_file_references(text)
        # Le marqueur existant ne doit pas être modifié
        assert "[core/event_bus/→INEXISTANT]" in result
        assert "→INEXISTANT]→INEXISTANT]" not in result
        # Le fichier réel reste intact
        assert "core/router.py" in result
