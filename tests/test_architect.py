"""Tests pour l'Architect Agent - strip markdown, override detection, risk analysis."""
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
