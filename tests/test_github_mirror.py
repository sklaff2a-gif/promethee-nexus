# tests/test_github_mirror.py — Tests pour GitHubMirror (lecture seule GitHub)
import json
import pytest
from unittest.mock import patch, MagicMock

from core.capabilities.github_mirror import GitHubMirror, _run_gh, MAX_FILE_CHARS, MAX_COMMITS, MAX_ISSUES


class TestRunGh:
    """Tests pour le helper _run_gh."""

    @patch("core.capabilities.github_mirror.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
        result = _run_gh(["auth", "status"])
        assert result == "ok\n"

    @patch("core.capabilities.github_mirror.subprocess.run")
    def test_nonzero_exit(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        result = _run_gh(["api", "foo"])
        assert result is None

    @patch("core.capabilities.github_mirror.subprocess.run", side_effect=FileNotFoundError)
    def test_gh_not_found(self, mock_run):
        result = _run_gh(["auth", "status"])
        assert result is None

    @patch("core.capabilities.github_mirror.subprocess.run")
    def test_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        result = _run_gh(["api", "foo"], timeout=30)
        assert result is None

    @patch("core.capabilities.github_mirror.subprocess.run", side_effect=OSError("boom"))
    def test_unexpected_error(self, mock_run):
        result = _run_gh(["api", "foo"])
        assert result is None


class TestGitHubMirror:
    """Tests pour la classe GitHubMirror."""

    def setup_method(self):
        self.mirror = GitHubMirror("test-owner/test-repo")

    @patch("core.capabilities.github_mirror._run_gh")
    def test_is_available_true(self, mock_gh):
        mock_gh.return_value = "Logged in"
        assert self.mirror.is_available() is True

    @patch("core.capabilities.github_mirror._run_gh")
    def test_is_available_false(self, mock_gh):
        mock_gh.return_value = None
        assert self.mirror.is_available() is False

    @patch("core.capabilities.github_mirror._run_gh")
    def test_read_file_raw(self, mock_gh):
        mock_gh.return_value = "print('hello')\n"
        result = self.mirror.read_file_raw("main.py")
        assert result == "print('hello')\n"

    @patch("core.capabilities.github_mirror._run_gh")
    def test_read_file_raw_truncation(self, mock_gh):
        mock_gh.return_value = "x" * (MAX_FILE_CHARS + 500)
        result = self.mirror.read_file_raw("big.py")
        assert len(result) < MAX_FILE_CHARS + 200
        assert "tronqué" in result

    @patch("core.capabilities.github_mirror._run_gh")
    def test_read_file_raw_none(self, mock_gh):
        mock_gh.return_value = None
        result = self.mirror.read_file_raw("missing.py")
        assert result is None

    @patch("core.capabilities.github_mirror._run_gh")
    def test_list_files(self, mock_gh):
        mock_gh.return_value = "main.py\ncore/\ntests/\n"
        result = self.mirror.list_files()
        assert result == ["main.py", "core/", "tests/"]

    @patch("core.capabilities.github_mirror._run_gh")
    def test_list_files_none(self, mock_gh):
        mock_gh.return_value = None
        assert self.mirror.list_files() is None

    @patch("core.capabilities.github_mirror._run_gh")
    def test_recent_commits(self, mock_gh):
        lines = [
            json.dumps({"sha": "abc12345", "message": "fix bug", "date": "2026-03-01", "author": "dev"}),
            json.dumps({"sha": "def67890", "message": "add feature", "date": "2026-03-02", "author": "dev"}),
        ]
        mock_gh.return_value = "\n".join(lines) + "\n"
        result = self.mirror.recent_commits(5)
        assert len(result) == 2
        assert result[0]["sha"] == "abc12345"

    @patch("core.capabilities.github_mirror._run_gh")
    def test_recent_commits_clamped(self, mock_gh):
        mock_gh.return_value = ""
        self.mirror.recent_commits(100)
        # Vérifier que n est clampé
        call_args = mock_gh.call_args[0][0]
        assert f"per_page={MAX_COMMITS}" in " ".join(call_args)

    @patch("core.capabilities.github_mirror._run_gh")
    def test_recent_commits_none(self, mock_gh):
        mock_gh.return_value = None
        assert self.mirror.recent_commits() is None

    @patch("core.capabilities.github_mirror._run_gh")
    def test_recent_commits_bad_json(self, mock_gh):
        mock_gh.return_value = "not json\n{bad\n"
        result = self.mirror.recent_commits()
        assert result == []

    @patch("core.capabilities.github_mirror._run_gh")
    def test_read_issues(self, mock_gh):
        issues = [{"number": 1, "title": "Bug", "state": "open"}]
        mock_gh.return_value = json.dumps(issues)
        result = self.mirror.read_issues()
        assert len(result) == 1
        assert result[0]["number"] == 1

    @patch("core.capabilities.github_mirror._run_gh")
    def test_read_issues_none(self, mock_gh):
        mock_gh.return_value = None
        assert self.mirror.read_issues() is None

    @patch("core.capabilities.github_mirror._run_gh")
    def test_read_issues_bad_json(self, mock_gh):
        mock_gh.return_value = "not json"
        assert self.mirror.read_issues() is None

    @patch("core.capabilities.github_mirror._run_gh")
    def test_search_code(self, mock_gh):
        lines = [json.dumps({"path": "core/foo.py", "name": "foo.py"})]
        mock_gh.return_value = "\n".join(lines)
        result = self.mirror.search_code("def hello")
        assert len(result) == 1
        assert result[0]["path"] == "core/foo.py"

    @patch("core.capabilities.github_mirror._run_gh")
    def test_search_code_none(self, mock_gh):
        mock_gh.return_value = None
        assert self.mirror.search_code("x") is None

    @patch("core.capabilities.github_mirror._run_gh")
    def test_repo_info(self, mock_gh):
        info = {"name": "test-repo", "stars": 5, "forks": 2, "open_issues": 3}
        mock_gh.return_value = json.dumps(info)
        result = self.mirror.repo_info()
        assert result["name"] == "test-repo"
        assert result["stars"] == 5

    @patch("core.capabilities.github_mirror._run_gh")
    def test_repo_info_none(self, mock_gh):
        mock_gh.return_value = None
        assert self.mirror.repo_info() is None

    @patch("core.capabilities.github_mirror._run_gh")
    def test_get_self_summary_unavailable(self, mock_gh):
        mock_gh.return_value = None
        result = self.mirror.get_self_summary()
        assert "indisponible" in result

    @patch("core.capabilities.github_mirror._run_gh")
    def test_get_self_summary_success(self, mock_gh):
        # repo_info, commits, issues
        def side_effect(args, timeout=30):
            joined = " ".join(args)
            if "repos/test-owner/test-repo\"" in joined or "repos/test-owner/test-repo\n" in joined:
                return json.dumps({"name": "test-repo", "stars": 10, "forks": 3, "open_issues": 2})
            if "commits" in joined:
                return json.dumps({"sha": "abc", "message": "fix", "date": "2026-03-01", "author": "dev"})
            if "issue" in joined:
                return json.dumps([{"number": 1, "title": "Bug"}])
            return None
        mock_gh.side_effect = side_effect
        # Simplifier: mocker directement les méthodes
        with patch.object(self.mirror, "repo_info", return_value={"name": "test-repo", "stars": 10, "forks": 3, "open_issues": 2}), \
             patch.object(self.mirror, "recent_commits", return_value=[{"sha": "abc", "message": "fix"}]), \
             patch.object(self.mirror, "read_issues", return_value=[{"number": 1, "title": "Bug report"}]):
            result = self.mirror.get_self_summary()
            assert "test-repo" in result
            assert "abc" in result
            assert "#1" in result

    def test_default_repo(self):
        m = GitHubMirror()
        assert m.repo == "sklaff2a-gif/promethee-nexus"

    @patch("core.capabilities.github_mirror._run_gh")
    def test_read_file_primary_works(self, mock_gh):
        """read_file avec le premier endpoint raw qui fonctionne."""
        mock_gh.return_value = "contenu du fichier"
        result = self.mirror.read_file("README.md")
        assert result == "contenu du fichier"

    @patch("core.capabilities.github_mirror._run_gh")
    def test_read_file_fallback_base64(self, mock_gh):
        """read_file fallback sur base64 quand le premier call échoue."""
        import base64
        encoded = base64.b64encode(b"Hello World").decode() + "\n"
        mock_gh.side_effect = [None, encoded]
        result = self.mirror.read_file("test.py")
        assert result == "Hello World"

    @patch("core.capabilities.github_mirror._run_gh")
    def test_read_file_both_fail(self, mock_gh):
        mock_gh.return_value = None
        result = self.mirror.read_file("missing.py")
        assert result is None


class TestSelfInspectIntegration:
    """Tests pour _execute_self_inspect et _choose_inspect_target dans autonomy_engine."""

    def _make_engine(self):
        """Crée un AutonomyEngine minimal sans effets de bord."""
        from core.autonomy_engine import AutonomyEngine
        engine = AutonomyEngine.__new__(AutonomyEngine)
        engine.routine_history = []
        engine.daily_count = 0
        engine.daily_budget_used = 0
        engine._council_degraded = False
        engine.error_streak = 0
        engine.total_routines_executed = 0
        engine._last_routine_time = 0
        engine._last_grimoire_slug = None
        engine._current_council_subject = ""
        engine._weekly_ritual_pending = False
        return engine

    def test_choose_inspect_target_first_call(self):
        """Premier appel → issues."""
        engine = self._make_engine()
        from core.capabilities.github_mirror import GitHubMirror
        mirror = GitHubMirror()
        target = engine._choose_inspect_target(mirror)
        assert target is not None
        assert target["action"] == "issues"

    def test_choose_inspect_target_after_issues(self):
        """Après issues → commits."""
        engine = self._make_engine()
        engine.routine_history = [
            {"intent": "SELF_INSPECT", "result_preview": "Inspection: Issues ouvertes sur GitHub"}
        ]
        from core.capabilities.github_mirror import GitHubMirror
        mirror = GitHubMirror()
        target = engine._choose_inspect_target(mirror)
        assert target["action"] == "commits"

    def test_choose_inspect_target_after_issues_and_commits(self):
        """Après issues + commits → summary."""
        engine = self._make_engine()
        engine.routine_history = [
            {"intent": "SELF_INSPECT", "result_preview": "Inspection: Issues ouvertes"},
            {"intent": "SELF_INSPECT", "result_preview": "Inspection: Derniers Commits"},
        ]
        from core.capabilities.github_mirror import GitHubMirror
        mirror = GitHubMirror()
        target = engine._choose_inspect_target(mirror)
        assert target["action"] == "summary"

    def test_choose_inspect_target_file_rotation(self):
        """Après summary → lecture d'un fichier."""
        engine = self._make_engine()
        engine.routine_history = [
            {"intent": "SELF_INSPECT", "result_preview": "Inspection: Issues ouvertes"},
            {"intent": "SELF_INSPECT", "result_preview": "Inspection: Commits récents"},
            {"intent": "SELF_INSPECT", "result_preview": "Résumé du repo"},
        ]
        from core.capabilities.github_mirror import GitHubMirror
        mirror = GitHubMirror()
        target = engine._choose_inspect_target(mirror)
        assert target["action"] == "read_file"
        assert "path" in target

    @pytest.mark.asyncio
    async def test_execute_self_inspect_gh_unavailable(self):
        """Si gh CLI indisponible → skip."""
        engine = self._make_engine()
        with patch("core.capabilities.github_mirror.GitHubMirror.is_available", return_value=False):
            result = await engine._execute_self_inspect()
            assert result["status"] == "skipped"
            assert "non disponible" in result["result"]

    @pytest.mark.asyncio
    async def test_execute_self_inspect_success_issues(self):
        """SELF_INSPECT lit les issues avec succès."""
        engine = self._make_engine()
        mock_issues = [{"number": 42, "title": "Improve scoring"}]
        with patch("core.capabilities.github_mirror.GitHubMirror.is_available", return_value=True), \
             patch("core.capabilities.github_mirror.GitHubMirror.read_issues", return_value=mock_issues), \
             patch.object(engine, "_choose_inspect_target", return_value={
                 "action": "issues", "label": "Issues ouvertes sur GitHub"
             }), \
             patch("core.event_bus.bus.bus.publish", return_value=None) as mock_pub, \
             patch("core.base_agent.BaseAgent.remember", return_value=None):
            result = await engine._execute_self_inspect()
            assert result["status"] == "success"
            assert "#42" in result["result"]
            mock_pub.assert_called_once()
            assert mock_pub.call_args[0][0] == "SELF_INSPECT_RESULT"

    @pytest.mark.asyncio
    async def test_execute_self_inspect_success_commits(self):
        """SELF_INSPECT lit les commits avec succès."""
        engine = self._make_engine()
        mock_commits = [{"sha": "abc12345", "date": "2026-03-09T10:00:00", "message": "fix scoring"}]
        with patch("core.capabilities.github_mirror.GitHubMirror.is_available", return_value=True), \
             patch("core.capabilities.github_mirror.GitHubMirror.recent_commits", return_value=mock_commits), \
             patch.object(engine, "_choose_inspect_target", return_value={
                 "action": "commits", "label": "Derniers commits"
             }), \
             patch("core.event_bus.bus.bus.publish", return_value=None), \
             patch("core.base_agent.BaseAgent.remember", return_value=None):
            result = await engine._execute_self_inspect()
            assert result["status"] == "success"
            assert "abc12345" in result["result"]

    @pytest.mark.asyncio
    async def test_execute_self_inspect_success_summary(self):
        """SELF_INSPECT affiche le résumé du repo."""
        engine = self._make_engine()
        with patch("core.capabilities.github_mirror.GitHubMirror.is_available", return_value=True), \
             patch("core.capabilities.github_mirror.GitHubMirror.get_self_summary", return_value="[GITHUB] promethee-nexus\nStars: 5"), \
             patch.object(engine, "_choose_inspect_target", return_value={
                 "action": "summary", "label": "Résumé du repo GitHub"
             }), \
             patch("core.event_bus.bus.bus.publish", return_value=None), \
             patch("core.base_agent.BaseAgent.remember", return_value=None):
            result = await engine._execute_self_inspect()
            assert result["status"] == "success"
            assert "promethee-nexus" in result["result"]

    @pytest.mark.asyncio
    async def test_execute_self_inspect_read_file(self):
        """SELF_INSPECT lit un fichier spécifique."""
        engine = self._make_engine()
        with patch("core.capabilities.github_mirror.GitHubMirror.is_available", return_value=True), \
             patch("core.capabilities.github_mirror.GitHubMirror.read_file_raw", return_value="class NeuralTissue:\n    pass\n"), \
             patch.object(engine, "_choose_inspect_target", return_value={
                 "action": "read_file", "path": "core/neural_tissue.py", "label": "Lecture core/neural_tissue.py"
             }), \
             patch("core.event_bus.bus.bus.publish", return_value=None), \
             patch("core.base_agent.BaseAgent.remember", return_value=None):
            result = await engine._execute_self_inspect()
            assert result["status"] == "success"
            assert "neural_tissue.py" in result["result"]
            assert "NeuralTissue" in result["result"]

    @pytest.mark.asyncio
    async def test_execute_self_inspect_list_files(self):
        """SELF_INSPECT liste les fichiers d'un répertoire."""
        engine = self._make_engine()
        with patch("core.capabilities.github_mirror.GitHubMirror.is_available", return_value=True), \
             patch("core.capabilities.github_mirror.GitHubMirror.list_files", return_value=["main.py", "core/", "tests/"]), \
             patch.object(engine, "_choose_inspect_target", return_value={
                 "action": "list_files", "path": "", "label": "Contenu racine"
             }), \
             patch("core.event_bus.bus.bus.publish", return_value=None), \
             patch("core.base_agent.BaseAgent.remember", return_value=None):
            result = await engine._execute_self_inspect()
            assert result["status"] == "success"
            assert "main.py" in result["result"]

    @pytest.mark.asyncio
    async def test_execute_self_inspect_github_failure(self):
        """Si GitHub retourne None → error."""
        engine = self._make_engine()
        with patch("core.capabilities.github_mirror.GitHubMirror.is_available", return_value=True), \
             patch("core.capabilities.github_mirror.GitHubMirror.read_issues", return_value=None), \
             patch.object(engine, "_choose_inspect_target", return_value={
                 "action": "issues", "label": "Issues ouvertes"
             }):
            result = await engine._execute_self_inspect()
            assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_execute_self_inspect_exception(self):
        """Exception → error propre."""
        engine = self._make_engine()
        with patch("core.capabilities.github_mirror.GitHubMirror.is_available", side_effect=RuntimeError("boom")):
            result = await engine._execute_self_inspect()
            assert result["status"] == "error"
            assert "boom" in result["result"]

    def test_self_inspect_in_post_budget_intents(self):
        """SELF_INSPECT est dans POST_BUDGET_INTENTS (routine gratuite)."""
        from core.autonomy_engine import POST_BUDGET_INTENTS
        assert "SELF_INSPECT" in POST_BUDGET_INTENTS

    def test_self_inspect_in_context_keywords(self):
        """SELF_INSPECT a des keywords de contexte."""
        from core.autonomy_engine import CONTEXT_KEYWORDS
        assert "SELF_INSPECT" in CONTEXT_KEYWORDS
        assert "github" in CONTEXT_KEYWORDS["SELF_INSPECT"]

    def test_self_inspect_in_resource_costs(self):
        """SELF_INSPECT a un coût défini dans resource_costs.json."""
        import json
        import os
        costs_path = os.path.join(os.path.dirname(__file__), "..", "config", "resource_costs.json")
        with open(costs_path, "r", encoding="utf-8") as f:
            costs = json.load(f)
        assert "SELF_INSPECT" in costs
        assert costs["SELF_INSPECT"]["cost"] == 0

