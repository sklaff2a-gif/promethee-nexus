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
import difflib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# Constantes securite
# ═══════════════════════════════════════════════════════════════════════

# Modules bannis (AST lint pre-run). Le subprocess ne pourra pas les
# importer via `import X` ou `from X import Y`.
# V16.1 (2026-04-24) : `sys` retire de la liste noire. Les scripts Python
# normaux utilisent sys.argv / sys.exit / sys.version en permanence ; les
# risques reels (sys.modules manipulation, sys.setrecursionlimit DoS) sont
# marginaux et le subprocess isole ne peut pas atteindre le parent via sys.
_BANNED_MODULES = frozenset({
    # Acces systeme et shell
    "os", "subprocess", "shutil",
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

# V21 — constantes du pipeline self-healing (référencées dans CodeSandbox)
_TEMPDIR_MEDIC_PREFIX = "promethee_medic_"
DEFAULT_REGRESSION_TIMEOUT_S = 300
DEFAULT_COMPILE_TIMEOUT_S = 30

# V16.2 (2026-04-24) — Regex du "stethoscope" d'exception.
# Un traceback Python standard termine toujours par une ligne de la forme :
#   NomDeLErreur: message optionnel
# On accepte tout identifiant CamelCase terminant par Error/Exception/Warning
# ou les 3 cas particuliers pas toujours suffixes : Exit (SystemExit),
# Interrupt (KeyboardInterrupt), StopIteration, GeneratorExit. Le `^` force
# le match debut de ligne pour eviter de matcher du texte narratif.
_EXCEPTION_FATAL_LINE = re.compile(
    r"^([A-Z][A-Za-z0-9_]*"
    r"(?:Error|Exception|Warning|Exit|Interrupt|Iteration|SystemExit))"
    r"\s*(?::|$)"
)

# V16.3 (2026-04-24) — Detection de pseudo-code illustratif.
# Un bloc ```python``` peut etre soit du code executable, soit une
# illustration annotee (ex: "L26: def foo():", "...") dans un CODE_REVIEW.
# Le sandbox plante sur pseudo-code avec SyntaxError et la boucle brule
# 3 iter LLM pour rien. On detecte et on skip.
_PSEUDO_LINE_PREFIX = re.compile(r"^\s*L\d+\s*:", re.MULTILINE)
_ELLIPSIS_SOLO_LINE = re.compile(r"^\s*\.\.\.\s*$", re.MULTILINE)


def is_pseudo_code(code: str) -> bool:
    """V16.3 — True si le bloc ressemble a du pseudo-code annote, pas du
    Python executable.

    Heuristiques :
      - Presence de "Lxx:" en debut de ligne (numero de ligne typique des
        CODE_REVIEW Markdown)
      - Ellipsis seul sur une ligne (illustration tronquee volontairement)
      - Ratio commentaires/logique > 0.7 (documentation uniquement)
    """
    if not code or not isinstance(code, str):
        return False
    if _PSEUDO_LINE_PREFIX.search(code):
        return True
    if _ELLIPSIS_SOLO_LINE.search(code):
        return True
    return False


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
        """V16.2 — Extracteur d'exception ameliore ("stethoscope").

        Un traceback Python standard crache toujours sa ligne fatale en
        toute derniere position (non vide) sous la forme :
            NomDeLErreur: message
        On lit stderr a l'envers et on retourne le PREMIER match de la regex
        _EXCEPTION_FATAL_LINE. Gere :
          - Exceptions standards : ValueError, TypeError, AttributeError, ...
          - Exceptions custom utilisateur qui finissent par Error/Exception
          - SystemExit, KeyboardInterrupt, StopIteration (pas de suffixe Error)
          - Traceback chaines (multiple 'During handling...') : seul le
            dernier match compte = la root cause visible par le process.

        Si aucun match : retourne None (ex: stderr vide, crash via signal OS,
        stderr de debug pur sans exception formee).
        """
        if not stderr:
            return None
        for line in reversed(stderr.splitlines()):
            stripped = line.strip()
            if not stripped:
                continue
            m = _EXCEPTION_FATAL_LINE.match(stripped)
            if m:
                return m.group(1)
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

            # V16.2 : fallback sys.exit(N) — le process finit non-zero sans
            # traceback formate. On infere SystemExit plutot que "unknown".
            if (
                not success
                and exception_name is None
                and not (proc.stderr or "").strip()
            ):
                exception_name = "SystemExit"

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

    # ───────────────────────────────────────────────────────────────────
    # V21 — apply_patch_in_sandbox (MEDIC)
    # ───────────────────────────────────────────────────────────────────

    def apply_patch_in_sandbox(
        self,
        surgeon_output: str,
        target_file: str,
        run_full_tests: bool = True,
        project_root: Optional[str] = None,
        regression_timeout_s: int = DEFAULT_REGRESSION_TIMEOUT_S,
        iteration: int = 0,
    ) -> "PatchResult":
        """V21 — applique des blocs SEARCH/REPLACE en environnement isolé.

        Étapes :
          1. Détecte [PATCH_IMPOSSIBLE: ...] → return early
          2. parse_search_replace_blocks(surgeon_output)
          3. Lit le source réel (project_root / target_file)
          4. apply_search_replace(source, blocks)
          5. Construit le sandbox layout (symlinks + copie réelle target_file)
          6. py_compile <patched_file>
          7. Régression globale (testmon ou full_suite)
          8. Génère unified_diff post-hoc
          9. Cleanup tempdir

        Aucune écriture sur le projet réel. Le caller est responsable de
        l'éventuel `git apply` après review humaine.
        """
        t0 = time.time()

        # Inférer project_root si non fourni : 3 niveaux au-dessus de ce module
        # (core/capabilities/code_sandbox.py → projet root)
        if project_root is None:
            project_root = str(Path(__file__).resolve().parents[2])
        project_root_p = Path(project_root)

        target_path = project_root_p / target_file

        # 1. PATCH_IMPOSSIBLE
        m_imp = _PATCH_IMPOSSIBLE_RE.search(surgeon_output or "")
        if m_imp:
            return PatchResult(
                status="patch_impossible",
                surgeon_output=surgeon_output,
                target_file=target_file,
                iteration=iteration,
                error_message=m_imp.group(1).strip(),
                duration_s=time.time() - t0,
            )

        # 2. Parse blocs
        try:
            blocks = parse_search_replace_blocks(surgeon_output or "")
        except ValueError as exc:
            return PatchResult(
                status="no_blocks",
                surgeon_output=surgeon_output,
                target_file=target_file,
                iteration=iteration,
                error_message=str(exc),
                duration_s=time.time() - t0,
            )

        # 3. Lecture source
        if not target_path.exists():
            return PatchResult(
                status="internal_error",
                surgeon_output=surgeon_output,
                target_file=target_file,
                iteration=iteration,
                error_message=f"Target file not found: {target_path}",
                duration_s=time.time() - t0,
            )
        try:
            original_source = target_path.read_text(encoding="utf-8")
        except Exception as exc:
            return PatchResult(
                status="internal_error",
                surgeon_output=surgeon_output,
                target_file=target_file,
                iteration=iteration,
                error_message=f"Erreur lecture source: {type(exc).__name__}: {exc}",
                duration_s=time.time() - t0,
            )

        # 4. Application des blocs
        try:
            patched_source = apply_search_replace(original_source, blocks)
        except _SearchNotFoundError as exc:
            return PatchResult(
                status="search_not_found",
                surgeon_output=surgeon_output,
                blocks_applied=exc.applied_count,
                target_file=target_file,
                iteration=iteration,
                failed_block_index=exc.block_index,
                failed_block_search=exc.search_text,
                error_message=str(exc),
                duration_s=time.time() - t0,
            )
        except _SearchAmbiguousError as exc:
            return PatchResult(
                status="search_ambiguous",
                surgeon_output=surgeon_output,
                blocks_applied=exc.applied_count,
                target_file=target_file,
                iteration=iteration,
                failed_block_index=exc.block_index,
                failed_block_search=exc.search_text,
                error_message=str(exc),
                duration_s=time.time() - t0,
            )
        except _SearchReplacedWithoutContextError as exc:
            # V27 — Guide-Lame : le LLM a réécrit au lieu d'augmenter.
            # On bloque AVANT l'apply pour économiser le subprocess pytest
            # et donner un feedback précis au SURGEON.
            return PatchResult(
                status="replaced_without_context",
                surgeon_output=surgeon_output,
                blocks_applied=exc.applied_count,
                target_file=target_file,
                iteration=iteration,
                failed_block_index=exc.block_index,
                failed_block_search=exc.search_text,
                failed_block_replace=exc.replace_text,
                error_message=str(exc),
                duration_s=time.time() - t0,
            )

        # 5-8. Sandbox + compile + régression + diff
        tmpdir = tempfile.mkdtemp(prefix=_TEMPDIR_MEDIC_PREFIX)
        try:
            try:
                _build_sandbox_layout(
                    project_root_p, Path(tmpdir), target_file, patched_source
                )
            except Exception as exc:
                return PatchResult(
                    status="internal_error",
                    surgeon_output=surgeon_output,
                    blocks_applied=len(blocks),
                    target_file=target_file,
                    iteration=iteration,
                    error_message=f"Erreur layout sandbox: {type(exc).__name__}: {exc}",
                    duration_s=time.time() - t0,
                )

            patched_path = Path(tmpdir) / target_file

            # 6. py_compile
            try:
                compile_proc = subprocess.run(
                    [sys.executable, "-m", "py_compile", str(patched_path)],
                    capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    timeout=DEFAULT_COMPILE_TIMEOUT_S,
                )
            except subprocess.TimeoutExpired:
                return PatchResult(
                    status="syntax_error",
                    surgeon_output=surgeon_output,
                    blocks_applied=len(blocks),
                    target_file=target_file,
                    iteration=iteration,
                    compile_stderr=f"py_compile timeout après {DEFAULT_COMPILE_TIMEOUT_S}s",
                    duration_s=time.time() - t0,
                )
            if compile_proc.returncode != 0:
                return PatchResult(
                    status="syntax_error",
                    surgeon_output=surgeon_output,
                    blocks_applied=len(blocks),
                    target_file=target_file,
                    iteration=iteration,
                    compile_stderr=compile_proc.stderr or compile_proc.stdout,
                    duration_s=time.time() - t0,
                )

            # 7. Régression globale (testmon si dispo, sinon full suite)
            #    Si run_full_tests=False : on saute la régression entièrement.
            if run_full_tests:
                regression = _run_regression_tests(
                    sandbox_cwd=tmpdir,
                    project_root=project_root,
                    timeout_s=regression_timeout_s,
                )
                if regression["timed_out"] or regression["tests_failed"] > 0:
                    status = "test_failed"
                elif regression["returncode"] != 0 and regression["tests_passed"] == 0:
                    # pytest a planté avant de rien collecter
                    status = "test_failed"
                else:
                    status = "success"
            else:
                regression = {
                    "stdout": "[run_full_tests=False — régression skippée]",
                    "tests_passed": 0,
                    "tests_failed": 0,
                    "test_failures": [],
                    "strategy": "skipped",
                }
                status = "success"

            # 8. Unified diff post-hoc
            unified_diff = ""
            if status == "success":
                unified_diff = _generate_unified_diff(
                    original_source, patched_source, target_file
                )

            return PatchResult(
                status=status,
                surgeon_output=surgeon_output,
                blocks_applied=len(blocks),
                unified_diff=unified_diff,
                target_file=target_file,
                iteration=iteration,
                test_output=regression["stdout"][-5000:] if regression.get("stdout") else "",
                test_strategy=regression.get("strategy", ""),
                tests_passed=regression.get("tests_passed", 0),
                tests_failed=regression.get("tests_failed", 0),
                test_failures=regression.get("test_failures", []),
                duration_s=time.time() - t0,
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# V21 (2026-04-25) — Pipeline d'auto-correction (Self-Healing)
# Format SEARCH/REPLACE + régression globale
# ═══════════════════════════════════════════════════════════════════════

# Format SEARCH/REPLACE (style Aider/Cline). Le LLM 14b cite verbatim le
# code, le MEDIC fait le str.replace en mémoire et génère le diff post-hoc.
_SEARCH_REPLACE_RE = re.compile(
    r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE",
    re.DOTALL,
)

# Marqueur "patch impossible" retourné par le SURGEON si l'audit ne fournit
# pas assez d'informations pour un patch chirurgical.
_PATCH_IMPOSSIBLE_RE = re.compile(r"\[PATCH_IMPOSSIBLE:\s*(.+?)\]", re.DOTALL)

# Parsing de la sortie pytest : "X passed, Y failed in 1.23s"
_PYTEST_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped)")

# Dossiers à exclure du sandbox layout (volumineux ou non pertinents pytest)
_SANDBOX_EXCLUDE_TOP = frozenset({
    ".git", ".github", "__pycache__", ".pytest_cache", ".mypy_cache",
    "BACKUPS", "datasets", "lora_adapters", "unsloth_compiled_cache",
    "USER_DROPZONE", "USER_KNOWLEDGE", "node_modules", ".venv", "venv",
    "PROMETHEE_V11_restructuration2026",  # éventuel sous-clone récursif
})

# V26 (2026-04-25) — Quarantaine de tests orphelins.
# Tests qui referencent des modules non encore implementes (WIP TDD-inverse).
# On les EXCLUT de la regression V21 sans les supprimer du disque : le jour
# ou le module sera implemente, le test redevient actif automatiquement.
# Les paths sont relatifs au sandbox_cwd (= projet_root miroir).
_PYTEST_IGNORE_PATHS = (
    # WIP : test ecrit AVANT core/resource_monitor.py (non implemente)
    "tests/auto/test_resource_monitor.py",
)


# ─── Exceptions internes V21 ──────────────────────────────────────────

class _SearchNotFoundError(ValueError):
    """Bloc SEARCH absent du source patché."""
    def __init__(self, message: str, block_index: int, search_text: str, applied_count: int):
        super().__init__(message)
        self.block_index = block_index
        self.search_text = search_text
        self.applied_count = applied_count


class _SearchAmbiguousError(ValueError):
    """Bloc SEARCH apparaît plusieurs fois — non unique."""
    def __init__(self, message: str, block_index: int, search_text: str,
                 applied_count: int, count: int):
        super().__init__(message)
        self.block_index = block_index
        self.search_text = search_text
        self.applied_count = applied_count
        self.count = count


class _SearchReplacedWithoutContextError(ValueError):
    """V27 — REPLACE n'inclut aucune ligne significative du SEARCH.
    Le LLM a réécrit au lieu d'augmenter (violation Règle de l'Insertion)."""
    def __init__(self, message: str, block_index: int, search_text: str,
                 replace_text: str, applied_count: int):
        super().__init__(message)
        self.block_index = block_index
        self.search_text = search_text
        self.replace_text = replace_text
        self.applied_count = applied_count


def _v27_is_significant_line(line: str) -> bool:
    """V27 — Une ligne significative a >=4 caractères alphanumériques.
    Élimine les `:`, `else:`, `pass`, lignes blanches, etc.
    """
    stripped = line.strip()
    if not stripped:
        return False
    return sum(1 for c in stripped if c.isalnum()) >= 4


# ─── Parsing & application des blocs SEARCH/REPLACE ───────────────────

def parse_search_replace_blocks(text: str) -> List[Tuple[str, str]]:
    """V21 — Parse les blocs SEARCH/REPLACE depuis la sortie du SURGEON.

    Retourne une liste de (search_text, replace_text). Lève ValueError si
    aucun bloc valide n'est trouvé (le caller doit gérer ce cas comme
    `no_blocks` ou laisser l'erreur remonter).
    """
    if not text or not isinstance(text, str):
        raise ValueError("Sortie SURGEON vide ou non-string")
    blocks = _SEARCH_REPLACE_RE.findall(text)
    if not blocks:
        raise ValueError(
            "Aucun bloc <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE trouvé"
        )
    return blocks


def apply_search_replace(source: str, blocks: List[Tuple[str, str]]) -> str:
    """V21 — Applique les blocs successivement sur source.

    Lève _SearchNotFoundError si un SEARCH n'est pas trouvé.
    Lève _SearchAmbiguousError si un SEARCH apparaît plusieurs fois.
    Lève _SearchReplacedWithoutContextError (V27) si le REPLACE n'inclut
    aucune ligne significative du SEARCH (le LLM a réécrit au lieu
    d'augmenter — violation Règle de l'Insertion V26).

    Garantie chirurgicale : on n'applique JAMAIS un replace ambigu ni un
    replace qui efface tout le SEARCH.
    """
    if not isinstance(source, str):
        raise TypeError("source doit être str")
    patched = source
    applied = 0
    for i, (search, replace) in enumerate(blocks):
        count = patched.count(search)
        if count == 0:
            raise _SearchNotFoundError(
                f"Bloc {i+1}/{len(blocks)} : SEARCH introuvable dans le source. "
                f"Vérifie que tu cites le code VERBATIM (indentation, espaces).",
                block_index=i,
                search_text=search,
                applied_count=applied,
            )
        if count > 1:
            raise _SearchAmbiguousError(
                f"Bloc {i+1}/{len(blocks)} : SEARCH trouvé à {count} endroits "
                f"(non unique). Étends ton bloc avec 2-3 lignes de contexte "
                f"avant/après pour le rendre unique.",
                block_index=i,
                search_text=search,
                applied_count=applied,
                count=count,
            )
        # V27 — Guide-Lame : seuil dynamique de preservation.
        # Si SEARCH fait N lignes significatives, REPLACE doit en contenir :
        #   - 1 si N <= 2 (petit bloc : juste l'ancrage minimum)
        #   - max(2, N//2) si N >= 3 (moitie au moins de l'ancrage)
        # Sinon le LLM a reecrit au lieu d'augmenter et le code va casser
        # (IndentationError, NameError, logique perdue). On bloque AVANT
        # l'apply pour economiser le subprocess pytest et fournir un
        # feedback cible au SURGEON pour son retry.
        search_lines = [l for l in search.splitlines() if _v27_is_significant_line(l)]
        n_lines = len(search_lines)
        if n_lines >= 2:
            replace_text = replace or ""
            overlap = sum(1 for l in search_lines if l in replace_text)
            required = 1 if n_lines <= 2 else max(2, n_lines // 2)
            if overlap < required:
                raise _SearchReplacedWithoutContextError(
                    f"Bloc {i+1}/{len(blocks)} : REPLACE n'inclut que "
                    f"{overlap}/{n_lines} lignes significatives du SEARCH "
                    f"(seuil V27 : {required} requise). Violation de la "
                    f"Regle de l'Insertion : tu as REECRIT au lieu "
                    f"d'AUGMENTER.",
                    block_index=i,
                    search_text=search,
                    replace_text=replace,
                    applied_count=applied,
                )
        patched = patched.replace(search, replace, 1)
        applied += 1
    return patched


# ─── PatchResult dataclass ────────────────────────────────────────────

@dataclass
class PatchResult:
    """V21 — résultat d'un cycle MEDIC complet."""
    status: str  # success / no_blocks / patch_impossible / search_not_found /
                 # search_ambiguous / replaced_without_context (V27) /
                 # syntax_error / test_failed / internal_error
    surgeon_output: str = ""
    blocks_applied: int = 0
    unified_diff: str = ""           # diff git généré post-hoc pour stockage
    target_file: str = ""
    iteration: int = 0
    failed_block_index: int = -1
    failed_block_search: str = ""
    failed_block_replace: str = ""    # V27 : pour replaced_without_context
    compile_stderr: str = ""
    test_output: str = ""
    test_strategy: str = ""           # "testmon" ou "full_suite"
    tests_passed: int = 0
    tests_failed: int = 0
    test_failures: List[str] = field(default_factory=list)
    duration_s: float = 0.0
    error_message: str = ""

    def format_traceback_for_surgeon(self, max_chars: int = 2000) -> str:
        """Formate l'erreur pour réinjection dans le re-prompt SURGEON.

        Le message doit dire au LLM EXACTEMENT quoi corriger pour la prochaine
        itération : changer le format, étendre le contexte, fixer la syntaxe,
        etc. Vide si status == 'success' ou 'patch_impossible'.
        """
        if self.status == "success":
            return ""
        if self.status == "no_blocks":
            return (
                "FORMAT INVALIDE : aucun bloc <<<<<<< SEARCH ... ======= "
                "... >>>>>>> REPLACE trouvé dans ta sortie. Reformate "
                "STRICTEMENT selon le format demandé."
            )
        if self.status == "search_not_found":
            snippet = self.failed_block_search[:max_chars]
            return (
                f"ECHEC : Bloc {self.failed_block_index + 1} introuvable "
                f"dans le source.\n\n"
                f"DIAGNOSTIC PROBABLE (cf. règles V24) :\n"
                f"  1. Tu as altéré l'INDENTATION (espaces/tabulations)\n"
                f"     dans une ligne que tu as crue verbatim.\n"
                f"  2. Tu as oublié les 2 lignes de CONTEXTE D'ANCRAGE\n"
                f"     avant et après le bloc modifié.\n"
                f"  3. Ton bloc fait plus de 7 lignes — divise-le en\n"
                f"     plusieurs petits blocs (règle Micro-Scalpel V24).\n"
                f"  4. Tu as recopié une approximation de mémoire au lieu\n"
                f"     du code REEL du source ci-dessus.\n\n"
                f"PROCEDURE V24 OBLIGATOIRE pour corriger :\n"
                f"  a. Re-lis le bloc ---SOURCE--- ligne par ligne.\n"
                f"  b. Identifie les 2 lignes JUSTE AVANT la modification.\n"
                f"  c. Copie-les caractère par caractère (espaces inclus).\n"
                f"  d. Identifie les 2 lignes JUSTE APRES la modification.\n"
                f"  e. Copie-les caractère par caractère.\n"
                f"  f. Le SEARCH = ancrage_avant + ligne_fautive + ancrage_apres.\n"
                f"  g. Le REPLACE = ancrage_avant + correction + ancrage_apres.\n\n"
                f"Ton SEARCH précédent (qui a échoué) :\n"
                f"---\n{snippet}\n---\n\n"
                f"Recommence avec un bloc PLUS PETIT et un ancrage VERBATIM. "
                f"L'indentation est vitale en Python — compte les espaces."
            )
        if self.status == "search_ambiguous":
            snippet = self.failed_block_search[:max_chars]
            return (
                f"BLOC {self.failed_block_index + 1} : SEARCH trouvé à "
                f"plusieurs endroits (non unique). Étends ton bloc avec "
                f"2-3 lignes de contexte AVANT et/ou APRÈS pour le rendre "
                f"unique dans le fichier.\n"
                f"Ton SEARCH était :\n---\n{snippet}\n---"
            )
        if self.status == "replaced_without_context":
            half = max_chars // 2
            snippet_search = self.failed_block_search[:half]
            snippet_replace = (self.failed_block_replace or "")[:half]
            return (
                f"ECHEC V27 : Ton bloc REPLACE a EFFACE le code d'origine.\n"
                f"Tu as VIOLE la Regle de l'Insertion V26.\n\n"
                f"Ton SEARCH etait :\n---\n{snippet_search}\n---\n\n"
                f"Ton REPLACE etait :\n---\n{snippet_replace}\n---\n\n"
                f"PROBLEME : aucune ligne du SEARCH n'apparait dans le REPLACE.\n"
                f"Tu as REECRIT au lieu d'AUGMENTER.\n\n"
                f"CORRECTION : recopie INTEGRALEMENT les lignes du SEARCH dans\n"
                f"ton REPLACE, et AJOUTE ton guard/check autour. Le REPLACE\n"
                f"doit CONTENIR le SEARCH verbatim, pas le remplacer.\n\n"
                f"Exemple correct :\n"
                f"  SEARCH : x = compute()\n"
                f"  REPLACE: x = compute()           # ligne SEARCH GARDEE\n"
                f"           if x is None:           # ton ajout\n"
                f"               return False"
            )
        if self.status == "syntax_error":
            err = self.compile_stderr[-max_chars:] if self.compile_stderr else ""
            return (
                f"PYTHON SYNTAX ERROR après application du patch :\n{err}\n\n"
                f"Vérifie l'indentation et les parenthèses du REPLACE."
            )
        if self.status == "test_failed":
            failures = "\n".join(self.test_failures[:5]) if self.test_failures else ""
            tail = self.test_output[-max_chars:] if self.test_output else ""
            return (
                f"REGRESSION TESTS FAILED ({self.tests_failed} échecs sur "
                f"{self.tests_passed + self.tests_failed} via "
                f"{self.test_strategy or 'inconnu'}) :\n"
                f"Failures principaux :\n{failures}\n\n"
                f"Output (dernier {max_chars}c) :\n{tail}"
            )
        if self.status == "internal_error":
            return f"ERREUR INTERNE SANDBOX : {self.error_message}"
        if self.status == "patch_impossible":
            return ""
        return f"STATUT INCONNU : {self.status}"


# ─── Helpers internes V21 ─────────────────────────────────────────────

def _check_pytest_plugin_available(plugin_name: str) -> bool:
    """V21 — True si le plugin pytest est installé.

    Utilise `pip show <plugin>` qui retourne 0 si trouvé. Plus fiable que
    l'import direct (testmon a un nom de package distinct du module).
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "show", plugin_name],
            capture_output=True, text=True, timeout=10,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _parse_pytest_summary(stdout: str) -> Dict[str, int]:
    """V21 — extrait passed/failed/error/skipped depuis la sortie pytest."""
    result = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    if not stdout:
        return result
    for match in _PYTEST_COUNT_RE.finditer(stdout):
        n = int(match.group(1))
        kind = match.group(2)
        if kind == "errors":
            kind = "error"
        if kind in result:
            result[kind] += n
    return result


def _extract_test_failures(stdout: str, max_failures: int = 10) -> List[str]:
    """V21 — extrait les lignes 'FAILED tests/...' depuis pytest --tb=line."""
    failures: List[str] = []
    if not stdout:
        return failures
    for line in stdout.splitlines():
        if line.startswith("FAILED ") or line.startswith("ERROR "):
            failures.append(line.strip())
            if len(failures) >= max_failures:
                break
    return failures


def _build_test_env(project_root: str, sandbox_cwd: str) -> dict:
    """V21 — construit l'env subprocess pour pytest dans le sandbox.

    PYTHONPATH = sandbox d'abord, puis project_root (pour les modules
    référencés mais non patchés). Préserve PATH, SYSTEMROOT, etc.
    """
    env = dict(os.environ)  # héritage complet (pytest a besoin de plein de vars)
    pythonpath = sandbox_cwd
    if project_root and project_root != sandbox_cwd:
        sep = ";" if sys.platform == "win32" else ":"
        pythonpath = f"{sandbox_cwd}{sep}{project_root}"
    env["PYTHONPATH"] = pythonpath
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PROMETHEE_TEST_MODE"] = "1"  # cohérent avec les fixtures projet
    return env


def _build_sandbox_layout(
    project_root: Path,
    sandbox_root: Path,
    target_file: str,
    patched_source: str,
) -> None:
    """V21 — construit le sandbox layout.

    Stratégie :
      - Pour chaque entry top-level de project_root (sauf exclude list) :
        * Si entry contient le target_file → copytree complet puis écrase
          target_file avec patched_source.
        * Sinon → tente os.symlink (rapide), fallback shutil.copytree/copy
          si OSError (Windows sans Developer Mode/admin).
      - Si target_file est lui-même top-level (ex: "config.py") :
        écrit patched_source directement.
    """
    target_rel = Path(target_file)
    target_top = target_rel.parts[0] if len(target_rel.parts) >= 1 else None
    target_is_top_file = (len(target_rel.parts) == 1)

    sandbox_root.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns(*_SANDBOX_EXCLUDE_TOP)

    for entry in project_root.iterdir():
        if entry.name in _SANDBOX_EXCLUDE_TOP:
            continue
        dst = sandbox_root / entry.name

        # Cas 1 : top-level qui contient le target → copytree puis overwrite
        if entry.is_dir() and entry.name == target_top and not target_is_top_file:
            shutil.copytree(entry, dst, symlinks=False, ignore=ignore)
            patched_path = sandbox_root / target_rel
            patched_path.parent.mkdir(parents=True, exist_ok=True)
            patched_path.write_text(patched_source, encoding="utf-8")
            continue

        # Cas 2 : target_file est lui-même top-level
        if target_is_top_file and entry.name == target_top:
            dst.write_text(patched_source, encoding="utf-8")
            continue

        # Cas 3 : entry non liée au target → symlink (rapide) ou copy
        try:
            if entry.is_dir():
                os.symlink(entry, dst, target_is_directory=True)
            else:
                os.symlink(entry, dst)
        except (OSError, NotImplementedError):
            # Fallback Windows sans Developer Mode
            if entry.is_dir():
                shutil.copytree(entry, dst, symlinks=False, ignore=ignore)
            else:
                shutil.copy2(entry, dst)


def _run_regression_tests(
    sandbox_cwd: str,
    project_root: str,
    timeout_s: int = DEFAULT_REGRESSION_TIMEOUT_S,
    use_testmon: Optional[bool] = None,
) -> dict:
    """V21 — exécute la suite régression dans le sandbox.

    Stratégie en cascade :
      1. Si pytest-testmon est dispo → `pytest --testmon` (impactés seuls)
      2. Sinon fallback → `pytest tests/ --tb=line --timeout=300 -x`
         (suite complète, fail-fast au premier échec pour économiser temps)

    `use_testmon=False` force le fallback (utile pour les tests).
    """
    if use_testmon is None:
        use_testmon = _check_pytest_plugin_available("pytest-testmon")

    if use_testmon:
        cmd = [
            sys.executable, "-m", "pytest",
            "--testmon", "--tb=line", "--no-header", "-q",
        ]
        strategy = "testmon"
    else:
        cmd = [
            sys.executable, "-m", "pytest",
            "tests/", "--tb=line", "--no-header", "-q", "-x",
        ]
        strategy = "full_suite"

    # V26 — quarantaine des tests orphelins (modules non implementes)
    for _ignore in _PYTEST_IGNORE_PATHS:
        cmd.extend(["--ignore", _ignore])

    env = _build_test_env(project_root, sandbox_cwd)

    try:
        proc = subprocess.run(
            cmd, cwd=sandbox_cwd, capture_output=True, text=True,
            timeout=timeout_s, encoding="utf-8", errors="replace", env=env,
        )
        summary = _parse_pytest_summary(proc.stdout)
        failures = _extract_test_failures(proc.stdout)
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "tests_passed": summary["passed"],
            "tests_failed": summary["failed"] + summary["error"],
            "tests_skipped": summary["skipped"],
            "test_failures": failures,
            "strategy": strategy,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": -9,
            "stdout": exc.stdout if isinstance(exc.stdout, str) else "",
            "stderr": f"TimeoutExpired: pytest killed après {timeout_s}s",
            "tests_passed": 0,
            "tests_failed": 0,
            "tests_skipped": 0,
            "test_failures": [f"TIMEOUT après {timeout_s}s"],
            "strategy": strategy,
            "timed_out": True,
        }


def _generate_unified_diff(original: str, patched: str, rel_path: str) -> str:
    """V21 — génère le unified diff post-application via difflib.

    Format compatible `git apply` (préfixe a/ et b/, lignes \\n terminées).
    Stocké dans PatchResult.unified_diff pour persistance et review humaine.
    """
    diff_lines = difflib.unified_diff(
        original.splitlines(keepends=True),
        patched.splitlines(keepends=True),
        fromfile=f"a/{rel_path}",
        tofile=f"b/{rel_path}",
        n=3,
    )
    return "".join(diff_lines)


# ═══════════════════════════════════════════════════════════════════════
# Singleton public
# ═══════════════════════════════════════════════════════════════════════

sandbox = CodeSandbox()
