# core/capabilities/github_mirror.py — Miroir GitHub lecture seule
# Permet à Prométhée de lire son propre code source, son historique,
# et ses issues sur GitHub. Accès READ-ONLY uniquement.

import logging
import subprocess
import json

logger = logging.getLogger("GitHubMirror")

# Repo par défaut — le propre repo de Prométhée
DEFAULT_REPO = "sklaff2a-gif/promethee-nexus"

# Limites de sécurité
MAX_FILE_CHARS = 8000      # Tronquer les fichiers longs
MAX_COMMITS = 20           # Max commits à récupérer
MAX_ISSUES = 10            # Max issues à lister


def _run_gh(args: list[str], timeout: int = 30) -> str | None:
    """Exécute une commande gh CLI et retourne stdout, ou None en cas d'erreur."""
    cmd = ["gh"] + args
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            logger.warning(f"gh {' '.join(args[:3])}... → exit {result.returncode}: {result.stderr[:200]}")
            return None
        return result.stdout
    except FileNotFoundError:
        logger.error("gh CLI non trouvé — installer GitHub CLI (gh)")
        return None
    except subprocess.TimeoutExpired:
        logger.warning(f"gh {' '.join(args[:3])}... → timeout {timeout}s")
        return None
    except Exception as e:
        logger.error(f"gh erreur inattendue: {e}")
        return None


class GitHubMirror:
    """Accès lecture seule au repo GitHub de Prométhée.

    Toutes les méthodes sont synchrones (bloquer dans run_in_executor si async).
    Nécessite `gh` CLI installé et authentifié.
    """

    def __init__(self, repo: str = DEFAULT_REPO):
        self.repo = repo

    def read_file(self, path: str, branch: str = "master") -> str | None:
        """Lit un fichier du repo. Retourne le contenu tronqué ou None."""
        raw = _run_gh(["api", f"repos/{self.repo}/contents/{path}",
                        "-q", ".content", "--jq", ".content",
                        "-H", "Accept: application/vnd.github.raw+json",
                        ])
        if raw is None:
            # Fallback : essayer avec le endpoint raw
            raw = _run_gh(["api", f"repos/{self.repo}/contents/{path}",
                            "--jq", ".content"])
            if raw is None:
                return None
            # Le contenu est en base64
            import base64
            try:
                raw = base64.b64decode(raw.strip()).decode("utf-8", errors="replace")
            except Exception:
                return None

        if len(raw) > MAX_FILE_CHARS:
            raw = raw[:MAX_FILE_CHARS] + f"\n\n[... tronqué à {MAX_FILE_CHARS} caractères]"
        return raw

    def read_file_raw(self, path: str, branch: str = "master") -> str | None:
        """Lit un fichier via l'API raw content."""
        raw = _run_gh([
            "api", f"repos/{self.repo}/contents/{path}?ref={branch}",
            "-H", "Accept: application/vnd.github.raw+json",
        ], timeout=15)
        if raw and len(raw) > MAX_FILE_CHARS:
            raw = raw[:MAX_FILE_CHARS] + f"\n\n[... tronqué à {MAX_FILE_CHARS} caractères]"
        return raw

    def list_files(self, path: str = "", branch: str = "master") -> list[str] | None:
        """Liste les fichiers d'un répertoire du repo."""
        out = _run_gh([
            "api", f"repos/{self.repo}/contents/{path}?ref={branch}",
            "--jq", ".[].name",
        ])
        if out is None:
            return None
        return [line.strip() for line in out.strip().split("\n") if line.strip()]

    def recent_commits(self, n: int = 10) -> list[dict] | None:
        """Retourne les n derniers commits (hash, message, date, auteur)."""
        n = min(n, MAX_COMMITS)
        out = _run_gh([
            "api", f"repos/{self.repo}/commits?per_page={n}",
            "--jq", '.[] | {sha: .sha[0:8], message: .commit.message[0:120], date: .commit.author.date, author: .commit.author.name}',
        ])
        if out is None:
            return None
        commits = []
        for line in out.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                commits.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return commits

    def read_issues(self, state: str = "open", n: int = 10) -> list[dict] | None:
        """Liste les issues du repo."""
        n = min(n, MAX_ISSUES)
        out = _run_gh([
            "issue", "list", "--repo", self.repo,
            "--state", state, "--limit", str(n),
            "--json", "number,title,state,createdAt,labels",
        ])
        if out is None:
            return None
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return None

    def search_code(self, query: str, max_results: int = 5) -> list[dict] | None:
        """Recherche dans le code du repo."""
        out = _run_gh([
            "api", "search/code",
            "-f", f"q={query} repo:{self.repo}",
            "--jq", f".items[:{ max_results}] | .[] | {{path: .path, name: .name}}",
        ])
        if out is None:
            return None
        results = []
        for line in out.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return results

    def repo_info(self) -> dict | None:
        """Informations générales sur le repo."""
        out = _run_gh([
            "api", f"repos/{self.repo}",
            "--jq", '{name: .name, description: .description, stars: .stargazers_count, forks: .forks_count, open_issues: .open_issues_count, language: .language, updated_at: .updated_at}',
        ])
        if out is None:
            return None
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return None

    def is_available(self) -> bool:
        """Vérifie que gh CLI est installé et authentifié."""
        result = _run_gh(["auth", "status"], timeout=10)
        return result is not None

    def get_self_summary(self) -> str:
        """Résumé formaté du repo pour injection dans le contexte."""
        info = self.repo_info()
        if not info:
            return "[GITHUB] Miroir indisponible."
        commits = self.recent_commits(3)
        commit_lines = ""
        if commits:
            commit_lines = "\n".join(
                f"  - {c.get('sha', '?')} {c.get('message', '?')[:80]}"
                for c in commits[:3]
            )
        issues = self.read_issues(n=3)
        issue_lines = ""
        if issues:
            issue_lines = "\n".join(
                f"  - #{i.get('number', '?')} {i.get('title', '?')[:60]}"
                for i in issues[:3]
            )
        parts = [
            f"[GITHUB] {info.get('name', self.repo)}",
            f"Stars: {info.get('stars', 0)} | Forks: {info.get('forks', 0)} | Issues: {info.get('open_issues', 0)}",
        ]
        if commit_lines:
            parts.append(f"Derniers commits:\n{commit_lines}")
        if issue_lines:
            parts.append(f"Issues ouvertes:\n{issue_lines}")
        return "\n".join(parts)
