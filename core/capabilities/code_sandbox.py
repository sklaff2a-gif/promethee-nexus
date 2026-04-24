"""V16 (2026-04-24) — Sandbox Dynamique.

Execute du code Python dans un subprocess isole avec securite en couches.
Usage : les agents appellent sandbox.run_python(code) pour tester leur
livrable avant de le livrer au professeur. Si le code crash, le traceback
est injecte dans un re-prompt pour correction (boucle Code -> Run ->
Traceback -> Fix).

Securite en 6 couches :
  1. AST lint pre-run : rejette imports dangereux (os, subprocess, socket, ...)
  2. Rejet builtins risques : eval, exec, compile, __import__, open
  3. Subprocess isole (nouveau process, pas thread)
  4. Tempdir work dir (pas de cwd=project_root, nettoye post-run)
  5. Env minimal : PATH + PYTHONIOENCODING seulement, pas de cles API
  6. Timeout dur : 5s default, 30s max (tue le process au-dela)

Objectif architectural : permettre l emergence d'une boucle de correction
agentique. L'agent ecrit -> il teste -> il lit son traceback -> il corrige.
C'est la difference entre un LLM qui genere et un agent qui raisonne.
"""
from __future__ import annotations

import ast
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# Constantes securite
# ═══════════════════════════════════════════════════════════════════════

# Modules bannis (AST lint pre-run). Le subprocess ne pourra pas les
# importer via `import X` ou `from X import Y`.
_BANNED_MODULES = frozenset({
    # Acces systeme et shell
    "os", "subprocess", "shutil", "sys",
    # Reseau
    "socket", "urllib", "urllib3", "requests", "httpx", "http",
    "ftplib", "telnetlib", "smtplib", "paramiko", "asyncio",
    # Serialisation dangereuse (RCE via pickle.load)
    "pickle", "marshal", "dill", "shelve",
    # Acces bas niveau
    "ctypes", "_ctypes", "cffi",
    # Concurrency (evite fork bomb, race conditions)
    "multiprocessing", "threading", "concurrent",
    # Importlib dynamique (contourne la liste noire)
    "importlib",
})

# Builtins bannis — meme via `from builtins import exec`.
_BANNED_NAMES = frozenset({
    "eval", "exec", "compile", "__import__", "open", "input",
})

DEFAULT_TIMEOUT_S = 5
MAX_TIMEOUT_S = 30
MAX_CODE_CHARS = 20_000  # protection payload
_TEMPDIR_PREFIX = "promethee_sandbox_"


# ═══════════════════════════════════════════════════════════════════════
# Data class resultat
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class SandboxResult:
    """Resultat d'une execution sandbox."""
    success: bool
    stdout: str
    stderr: str
    return_code: int
    duration_ms: int
    exception: Optional[str] = None  # Nom de la classe d'exception si crash
    banned_import: Optional[str] = None  # Module rejete par lint
    timed_out: bool = False

    def format_traceback(self, max_chars: int = 2000) -> str:
        """Formate un message d'erreur lisible pour re-injection LLM.

        A injecter dans le prompt de correction. Vide si success=True.
        """
        if self.success:
            return ""
        if self.banned_import:
            return (
                f"IMPORT INTERDIT: '{self.banned_import}'. Ce module est banni"
                f" par le sandbox pour raisons de securite. Reecris la solution"
                f" sans utiliser ce module."
            )
        if self.timed_out:
            return (
                f"TIMEOUT: le code a depasse la limite de {DEFAULT_TIMEOUT_S}s."
                f" Simplifie la logique (boucle infinie? recursion?)."
            )
        if self.stderr:
            truncated = self.stderr[-max_chars:] if len(self.stderr) > max_chars else self.stderr
            return f"TRACEBACK A CORRIGER:\n{truncated}"
        return f"ECHEC inconnu (return_code={self.return_code})"


# ═══════════════════════════════════════════════════════════════════════
# AST auditor (lint pre-run)
# ═══════════════════════════════════════════════════════════════════════

class _ImportAuditor(ast.NodeVisitor):
    """Visite l'AST pour detecter imports et builtins bannis.

    Arrete a la premiere violation trouvee (attribut `banned`).
    """

    def __init__(self) -> None:
        self.banned: Optional[str] = None

    def visit_Import(self, node: ast.Import) -> None:
        if self.banned:
            return
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top in _BANNED_MODULES:
                self.banned = top
                return
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self.banned:
            return
        if node.module:
            top = node.module.split(".")[0]
            if top in _BANNED_MODULES:
                self.banned = top
                return
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self.banned:
            return
        # eval(), exec(), compile(), __import__(), open(), input() directs
        if isinstance(node.func, ast.Name) and node.func.id in _BANNED_NAMES:
            self.banned = node.func.id
            return
        # getattr(__builtins__, "exec") — bonus paranoia
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in _BANNED_NAMES
        ):
            self.banned = f"getattr->{node.args[1].value}"
            return
        self.generic_visit(node)


# ═══════════════════════════════════════════════════════════════════════
# Sandbox (singleton)
# ═══════════════════════════════════════════════════════════════════════

class CodeSandbox:
    """Execute du code Python en isolation. Singleton."""

    _instance: Optional["CodeSandbox"] = None

    def __new__(cls) -> "CodeSandbox":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_done = False
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_init_done", False):
            return
        self._init_done = True
        self._runs_count = 0
        self._crashes_count = 0

    @classmethod
    def reset_singleton(cls) -> None:
        """Pour les tests : reset du singleton."""
        cls._instance = None

    @property
    def stats(self) -> dict:
        return {
            "runs": self._runs_count,
            "crashes": self._crashes_count,
            "success_rate": (
                (self._runs_count - self._crashes_count) / self._runs_count
                if self._runs_count > 0 else 1.0
            ),
        }

    def lint(self, code: str) -> Optional[str]:
        """Retourne le nom du module/builtin banni, ou None si OK.

        Si le code a une SyntaxError, retourne None et laisse le subprocess
        le capturer avec un traceback lisible (num ligne).
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return None
        auditor = _ImportAuditor()
        auditor.visit(tree)
        return auditor.banned

    def _build_minimal_env(self, tmpdir: str) -> dict:
        """Env minimal : expose SEULEMENT ce qui est necessaire a python.exe."""
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
        if sys.platform == "win32":
            # Windows requiert SYSTEMROOT pour les DLLs systeme
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
            env["TEMP"] = tmpdir
            env["TMP"] = tmpdir
        else:
            env["TMPDIR"] = tmpdir
        # PAS de PYTHONPATH (evite import du projet)
        # PAS de HOME / USER / USERPROFILE (evite leak user vars)
        # PAS de ANTHROPIC_API_KEY, OPENAI_API_KEY, etc. (evite leak secrets)
        return env

    def _extract_exception_name(self, stderr: str) -> Optional[str]:
        """Extrait le nom de l'exception depuis la derniere ligne du traceback."""
        if not stderr:
            return None
        for line in stderr.strip().split("\n")[::-1]:
            line = line.strip()
            if ":" in line and not line.startswith((" ", "\t", "File ", "^")):
                name = line.split(":")[0].strip()
                # Validation : un nom d'exception contient des lettres et commence par maj
                if name and name[0].isupper() and all(c.isalnum() or c == "_" for c in name):
                    return name
        return None

    def run_python(self, code: str, timeout: int = DEFAULT_TIMEOUT_S) -> SandboxResult:
        """Execute le code Python dans un subprocess isole.

        Retourne un SandboxResult sans jamais lever d'exception. Si le code
        plante, stderr/return_code le reflete mais l'appelant peut continuer.
        """
        if not isinstance(code, str) or not code.strip():
            return SandboxResult(
                success=False, stdout="", stderr="Code vide ou invalide",
                return_code=-1, duration_ms=0,
            )
        if len(code) > MAX_CODE_CHARS:
            return SandboxResult(
                success=False, stdout="",
                stderr=f"Code trop long : {len(code)} chars > {MAX_CODE_CHARS}",
                return_code=-1, duration_ms=0,
            )

        timeout = max(1, min(timeout, MAX_TIMEOUT_S))

        # Couche 1-2 : lint AST
        banned = self.lint(code)
        if banned:
            self._runs_count += 1
            self._crashes_count += 1
            logger.warning(f"[SANDBOX] Import/builtin banni rejete: {banned}")
            return SandboxResult(
                success=False, stdout="",
                stderr=f"Import/builtin interdit par le sandbox: '{banned}'",
                return_code=-1, duration_ms=0, banned_import=banned,
            )

        # Couche 3-5 : tempdir + env minimal + subprocess
        tmpdir = tempfile.mkdtemp(prefix=_TEMPDIR_PREFIX)
        env = self._build_minimal_env(tmpdir)

        t0 = time.time()
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout, cwd=tmpdir, env=env,
            )
            duration_ms = int((time.time() - t0) * 1000)
            success = proc.returncode == 0
            exception_name = None if success else self._extract_exception_name(proc.stderr)

            self._runs_count += 1
            if not success:
                self._crashes_count += 1

            logger.info(
                f"[SANDBOX] run {len(code)}c -> "
                f"{'OK' if success else 'CRASH:' + (exception_name or 'unknown')}"
                f" ({duration_ms}ms)"
            )
            return SandboxResult(
                success=success,
                stdout=proc.stdout,
                stderr=proc.stderr,
                return_code=proc.returncode,
                duration_ms=duration_ms,
                exception=exception_name,
            )
        except subprocess.TimeoutExpired as e:
            duration_ms = int((time.time() - t0) * 1000)
            self._runs_count += 1
            self._crashes_count += 1
            logger.warning(f"[SANDBOX] timeout apres {timeout}s")
            return SandboxResult(
                success=False,
                stdout=e.stdout if isinstance(e.stdout, str) else "",
                stderr=f"TimeoutExpired: killed apres {timeout}s",
                return_code=-9, duration_ms=duration_ms, timed_out=True,
            )
        except Exception as exc:
            # Garde-fou : le sandbox ne doit JAMAIS lever vers l'appelant
            duration_ms = int((time.time() - t0) * 1000)
            self._runs_count += 1
            self._crashes_count += 1
            logger.error(f"[SANDBOX] erreur interne: {type(exc).__name__}: {exc}")
            return SandboxResult(
                success=False, stdout="",
                stderr=f"Sandbox internal error: {type(exc).__name__}: {exc}",
                return_code=-1, duration_ms=duration_ms,
            )
        finally:
            # Couche 6 : nettoyage tempdir
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════
# Singleton public
# ═══════════════════════════════════════════════════════════════════════

sandbox = CodeSandbox()
