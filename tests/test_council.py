import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.council import (
    Council, parse_council_mission, _is_consensus, _strip_markdown_prefix,
    _score_argument, _parse_president_verdict,
    MIN_ROUNDS_BEFORE_CONSENSUS, MIN_ROUNDS_BEFORE_PRESIDENT,
    PRESIDENT_AGENT_NAME, _COUNCIL_PROJECT_CONTEXT,
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
        council = Council(agents, ["coder", "security"], "test mission", max_rounds=3, enable_student=False)
        result = await council.run()

        assert result["status"] == "max_rounds"
        assert result["rounds_used"] == 3
        # 2 participants * 3 rounds = 6 entrées (enable_student=False)
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
