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
import json
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
from typing import Any, Dict, List, Optional, Tuple

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


def get_sandbox_constraints_block() -> str:
    """P15.2 (2026-05-14) — Sandbox Honesty.

    Retourne un bloc de contraintes en langage naturel à injecter dans les
    prompts génératifs (WORKSHOP, CREATION). Avant ce helper, le LLM ignorait
    que `os`, `open`, `subprocess` etc. étaient interdits par le sandbox V16
    et générait du code idiomatique qui crashait à l'AST lint — cascade de
    retries dégradés observée le 11/05 sur memory_gatekeeper.py.

    Les listes sont lues dynamiquement depuis _BANNED_MODULES et _BANNED_NAMES
    pour que toute mise à jour du sandbox propage automatiquement au prompt.
    """
    modules_sorted = sorted(_BANNED_MODULES)
    names_sorted = sorted(_BANNED_NAMES)
    return (
        "\n[CONTRAINTES SANDBOX — LIS AVANT D'ECRIRE]\n"
        "Ton code sera execute dans un sandbox AST-linte. Les modules et "
        "builtins suivants sont INTERDITS et provoqueront un rejet immediat :\n"
        f"  - Modules bannis : {', '.join(modules_sorted)}\n"
        f"  - Builtins bannis : {', '.join(names_sorted)}\n"
        "Consequences si tu utilises l'un d'eux :\n"
        "  - L'AST lint pre-execution rejette le code (banned_import detecte).\n"
        "  - Le pipeline declenche un retry, et chaque retry degrade la qualite.\n"
        "Strategies sures :\n"
        "  - Pas d'I/O fichier (os, open, pathlib non en standard). Travaille\n"
        "    sur des structures de donnees en memoire ou des chaines de caracteres.\n"
        "  - Pas de reseau (urllib, requests, socket, httpx). Simule via des\n"
        "    dictionnaires/listes Python natifs.\n"
        "  - Pas de subprocess / threading / multiprocessing. Code sequentiel pur.\n"
        "  - Pas de eval/exec/compile/__import__/input/open.\n"
        "Modules autorises : math, re, json, collections, itertools, functools,\n"
        "dataclasses, typing, enum, datetime (parsing/format uniquement),\n"
        "random, statistics, hashlib, base64, decimal, fractions, copy.\n"
    )


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
        checklist: Optional[Dict[str, Any]] = None,  # V29
    ) -> "PatchResult":
        """V30 — applique un patch JSON Exosquelette en environnement isolé.

        Étapes :
          1. Détecte [PATCH_IMPOSSIBLE: ...] → return early
          2. parse_v30_patch(surgeon_output) → dict {anchor, action, new_code}
          3. Lit le source réel (project_root / target_file)
          4. apply_v30_patch(source, patch, checklist) (V29 valide replace_line)
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

        # 2. V30 — Parse JSON Exosquelette
        try:
            patch = parse_v30_patch(surgeon_output or "")
        except _V30InvalidJSONError as exc:
            return PatchResult(
                status="invalid_json",
                surgeon_output=surgeon_output,
                target_file=target_file,
                iteration=iteration,
                error_message=str(exc),
                duration_s=time.time() - t0,
            )
        except _V30InvalidActionError as exc:
            return PatchResult(
                status="invalid_action",
                surgeon_output=surgeon_output,
                target_file=target_file,
                iteration=iteration,
                error_message=str(exc),
                duration_s=time.time() - t0,
            )

        # V32 (2026-04-26) — DETECTION FORMAT MULTI-FICHIERS.
        # Si le patch est au format {"files": [...]}, deleguer a la
        # methode dediee (orchestre N create_file/append_block en
        # transactionnel : si un seul fichier echoue, tous sont
        # rollbacked).
        if patch.get("is_multi_files"):
            return self.apply_multi_files_in_sandbox(
                surgeon_output=surgeon_output,
                parsed_patch=patch,
                run_full_tests=run_full_tests,
                project_root=project_root,
                regression_timeout_s=regression_timeout_s,
                iteration=iteration,
                t0=t0,
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

        # 4. V30 — Application du patch JSON (avec checklist V29)
        try:
            patched_source = apply_v30_patch(
                original_source, patch, checklist=checklist
            )
        except _V30AnchorNotFoundError as exc:
            return PatchResult(
                status="anchor_not_found",
                surgeon_output=surgeon_output,
                blocks_applied=0,
                target_file=target_file,
                iteration=iteration,
                failed_block_search=exc.anchor_line,
                error_message=str(exc),
                duration_s=time.time() - t0,
            )
        except _V30AnchorAmbiguousError as exc:
            return PatchResult(
                status="anchor_ambiguous",
                surgeon_output=surgeon_output,
                blocks_applied=0,
                target_file=target_file,
                iteration=iteration,
                failed_block_search=exc.anchor_line,
                error_message=str(exc),
                duration_s=time.time() - t0,
            )
        except _V30AnchorFunctionNotFoundError as exc:
            return PatchResult(
                status="anchor_function_not_found",
                surgeon_output=surgeon_output,
                blocks_applied=0,
                target_file=target_file,
                iteration=iteration,
                error_message=str(exc),
                duration_s=time.time() - t0,
            )
        except _ChecklistViolationError as exc:
            # V29 + V30 : replace_line cible une ligne lines_to_preserve
            return PatchResult(
                status="checklist_violation",
                surgeon_output=surgeon_output,
                blocks_applied=0,
                target_file=target_file,
                iteration=iteration,
                checklist_violations=exc.violations,
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
                    blocks_applied=1,
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
                    blocks_applied=1,
                    target_file=target_file,
                    iteration=iteration,
                    compile_stderr=f"py_compile timeout après {DEFAULT_COMPILE_TIMEOUT_S}s",
                    duration_s=time.time() - t0,
                )
            if compile_proc.returncode != 0:
                return PatchResult(
                    status="syntax_error",
                    surgeon_output=surgeon_output,
                    blocks_applied=1,
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
                blocks_applied=1,
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

    def apply_multi_files_in_sandbox(
        self,
        surgeon_output: str,
        parsed_patch: Dict[str, Any],
        run_full_tests: bool = True,
        project_root: Optional[str] = None,
        regression_timeout_s: int = DEFAULT_REGRESSION_TIMEOUT_S,
        iteration: int = 0,
        t0: Optional[float] = None,
    ) -> "PatchResult":
        """V32 (2026-04-26) — Applique un patch multi-fichiers en sandbox.

        Format JSON attendu (deja parse par parse_v30_patch) :
            {"is_multi_files": True,
             "files": [
                {"target_file": "core/utils/x.py",
                 "action": "create_file" | "append_block" | <V30 action>,
                 "new_code": "...",
                 "anchor_line": "..." (si action V30)},
                ...
             ]}

        Pipeline :
          1. Build sandbox layout standard (copie complete du projet)
          2. Pour chaque entry :
             - create_file : apply_v32_create_file (echoue si existe)
             - append_block : apply_v32_append_block (echoue si manquant)
             - action V30 : apply_v30_patch sur le source existant
          3. py_compile sur chaque fichier touche
          4. pytest regression complete (catch les regressions latentes)

        Atomicite : la sandbox est un tempdir isole, donc echec d'un
        fichier laisse le projet reel intact. Si un fichier echoue, on
        retourne PatchResult avec error sans toucher au projet.
        """
        if t0 is None:
            t0 = time.time()
        if project_root is None:
            project_root = str(Path(__file__).resolve().parents[2])
        project_root_p = Path(project_root)

        files = parsed_patch.get("files", [])
        if not files:
            return PatchResult(
                status="invalid_json",
                surgeon_output=surgeon_output,
                target_file="<multi>",
                iteration=iteration,
                error_message="V32 multi-files : tableau 'files' vide",
                duration_s=time.time() - t0,
            )

        # 1. Construire sandbox layout (copie complete du projet)
        # V32.3 (2026-04-26) — FIX file_exists faux positif.
        # _build_sandbox_layout pre-creait le first_target en sandbox
        # avec patched_source="". Quand apply_v32_create_file arrivait
        # ensuite, le fichier "existait deja" -> _V32FileExistsError.
        # Fix : utiliser config.py (top-level, existe toujours) comme
        # proxy pour le layout. Le sandbox copiera tout le projet sans
        # toucher aux target_file V32. Les create_file s'occupent de
        # creer les fichiers eux-memes.
        tmpdir = tempfile.mkdtemp(prefix=_TEMPDIR_MEDIC_PREFIX)
        try:
            try:
                _proxy_target = "config.py"
                _proxy_path = project_root_p / _proxy_target
                if not _proxy_path.exists():
                    # Fallback : prendre le premier fichier .py top-level
                    for _entry in project_root_p.iterdir():
                        if _entry.is_file() and _entry.suffix == ".py":
                            _proxy_target = _entry.name
                            _proxy_path = _entry
                            break
                _proxy_source = (
                    _proxy_path.read_text(encoding="utf-8")
                    if _proxy_path.exists() else ""
                )
                _build_sandbox_layout(
                    project_root_p, Path(tmpdir), _proxy_target, _proxy_source,
                )
            except Exception as exc:
                first_target = files[0].get("target_file", "<multi>") if files else "<multi>"
                return PatchResult(
                    status="internal_error",
                    surgeon_output=surgeon_output,
                    target_file=first_target,
                    iteration=iteration,
                    error_message=f"V32 layout sandbox: {type(exc).__name__}: {exc}",
                    duration_s=time.time() - t0,
                )

            # 2. Appliquer chaque fichier dans la sandbox
            applied_files: List[str] = []
            for i, entry in enumerate(files):
                tgt = entry["target_file"]
                act = entry["action"]
                code = entry["new_code"]
                try:
                    if act == "create_file":
                        apply_v32_create_file(tgt, code, str(tmpdir))
                    elif act == "append_block":
                        apply_v32_append_block(tgt, code, str(tmpdir))
                    elif act in _V30_VALID_ACTIONS:
                        # Action V30 : lire le source existant en sandbox,
                        # apply_v30_patch, ecrire.
                        sandbox_path = Path(tmpdir) / tgt
                        if not sandbox_path.exists():
                            return PatchResult(
                                status="internal_error",
                                surgeon_output=surgeon_output,
                                target_file=tgt,
                                iteration=iteration,
                                error_message=(
                                    f"V32 files[{i}] action={act} mais "
                                    f"target_file={tgt} introuvable en sandbox"
                                ),
                                duration_s=time.time() - t0,
                            )
                        sub_source = sandbox_path.read_text(encoding="utf-8")
                        sub_patched = apply_v30_patch(sub_source, entry)
                        sandbox_path.write_text(sub_patched, encoding="utf-8")
                    else:
                        return PatchResult(
                            status="invalid_action",
                            surgeon_output=surgeon_output,
                            target_file=tgt,
                            iteration=iteration,
                            error_message=f"V32 files[{i}] action={act!r} inconnue",
                            duration_s=time.time() - t0,
                        )
                    applied_files.append(tgt)
                except _V32FileExistsError as exc:
                    return PatchResult(
                        status="file_exists",
                        surgeon_output=surgeon_output,
                        target_file=tgt,
                        iteration=iteration,
                        error_message=str(exc),
                        duration_s=time.time() - t0,
                    )
                except FileNotFoundError as exc:
                    return PatchResult(
                        status="file_not_found",
                        surgeon_output=surgeon_output,
                        target_file=tgt,
                        iteration=iteration,
                        error_message=str(exc),
                        duration_s=time.time() - t0,
                    )
                except Exception as exc:
                    return PatchResult(
                        status="internal_error",
                        surgeon_output=surgeon_output,
                        target_file=tgt,
                        iteration=iteration,
                        error_message=(
                            f"V32 files[{i}] {act} sur {tgt} echoue: "
                            f"{type(exc).__name__}: {exc}"
                        ),
                        duration_s=time.time() - t0,
                    )

            # 3. py_compile sur chaque fichier touche
            for tgt in applied_files:
                if not tgt.endswith(".py"):
                    continue
                full = Path(tmpdir) / tgt
                if not full.exists():
                    continue
                try:
                    proc = subprocess.run(
                        [sys.executable, "-m", "py_compile", str(full)],
                        capture_output=True, text=True,
                        encoding="utf-8", errors="replace",
                        timeout=DEFAULT_COMPILE_TIMEOUT_S,
                    )
                except subprocess.TimeoutExpired:
                    return PatchResult(
                        status="syntax_error",
                        surgeon_output=surgeon_output,
                        target_file=tgt,
                        iteration=iteration,
                        compile_stderr=f"py_compile timeout sur {tgt}",
                        duration_s=time.time() - t0,
                    )
                if proc.returncode != 0:
                    return PatchResult(
                        status="syntax_error",
                        surgeon_output=surgeon_output,
                        target_file=tgt,
                        iteration=iteration,
                        compile_stderr=proc.stderr or proc.stdout,
                        duration_s=time.time() - t0,
                    )

            # 4. pytest regression complete
            if run_full_tests:
                regression = _run_regression_tests(
                    sandbox_cwd=tmpdir,
                    project_root=project_root,
                    timeout_s=regression_timeout_s,
                )
                if regression["timed_out"] or regression["tests_failed"] > 0:
                    status = "test_failed"
                elif regression["returncode"] != 0 and regression["tests_passed"] == 0:
                    status = "test_failed"
                else:
                    status = "patched"
            else:
                regression = {
                    "stdout": "[run_full_tests=False]",
                    "tests_passed": 0,
                    "tests_failed": 0,
                    "test_failures": [],
                    "strategy": "skipped",
                }
                status = "patched"

            return PatchResult(
                status=status,
                surgeon_output=surgeon_output,
                blocks_applied=len(applied_files),
                target_file=", ".join(applied_files),
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
# V30 (2026-04-25) — Exosquelette Syntaxique JSON
# Burn the boats : remplace integralement le format SEARCH/REPLACE V21-V29.
# Le SURGEON sort un JSON {anchor_line, action, new_code}, le MEDIC calcule
# l'indentation mathematiquement et applique l'edit (insert/replace).
# Le 14b ne gere plus la syntaxe — uniquement la cible et l'action.
# ═══════════════════════════════════════════════════════════════════════

# Marqueur "patch impossible" retourné par le SURGEON si l'audit ne fournit
# pas assez d'informations pour un patch chirurgical.
_PATCH_IMPOSSIBLE_RE = re.compile(r"\[PATCH_IMPOSSIBLE:\s*(.+?)\]", re.DOTALL)

# V30 — Actions valides pour l'Exosquelette JSON
_V30_VALID_ACTIONS = frozenset({"insert_before", "insert_after", "replace_line", "replace_line_all"})

# V32 (2026-04-26) — Actions de creation/expansion. Differencient le
# patching (V30 chirurgical) de la creation (V32 architectural).
_V32_VALID_ACTIONS = frozenset({"create_file", "append_block"})

# Tous les actions valides (V30 + V32)
_V30_V32_ALL_ACTIONS = _V30_VALID_ACTIONS | _V32_VALID_ACTIONS

# V30 — Regex pour extraire le bloc JSON principal d'une sortie LLM
_V30_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)

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

# V30.3 (2026-04-25) — Quarantaine de tests flaky individuels.
# Tests qui passent en standalone mais echouent dans le sandbox V21 a
# cause d'effets de bord (mock async non isole entre modules, singleton
# state pollution, etc.). On les exclut chirurgicalement avec --deselect
# (pytest) pour ne pas perdre le verdict legitime du patch.
# Format : "<chemin>::<Class>::<test>"
_PYTEST_DESELECT_TESTS = (
    # V30.3 : tests TestBusIntegration de cardiac_engine. Toute la classe
    # est polluee par un mock async non isole entre modules dans le sandbox.
    # Les 5 tests passent en standalone (verifie au tir 20:07:28). Le bug
    # est un effet de bord du sandbox layout (singleton bus), pas du patch
    # SURGEON sur bullshit_detector. On deselectionne la CLASSE ENTIERE
    # pour eviter le whack-a-mole sur chaque test individuel.
    # Tirs precedents :
    #   - 18:35:43 : test_on_routine_complete_success FAILED -> deselect
    #   - 20:07:28 : test_on_routine_complete_failure FAILED (autre test
    #                  de la meme classe) -> deselect classe entiere
    "tests/test_cardiac_engine.py::TestBusIntegration",
    # V30.15 (2026-04-26) — RESOLUE : la quarantaine de
    # TestOrchestratorForceLocal a ete LEVEE definitivement.
    # Trace de la chasse :
    # 1. V30.12 — mock_leak_detector.py (anticorps deterministe AST)
    #    detecte 584 fuites dans 70 fichiers.
    # 2. V30.13 — multi-ablation forgee (replace_line_all + format
    #    {"patches": [...]}). SURGEON 14b LOCAL fixe 6 occurrences de
    #    @patch(new_callable=MagicMock) -> AsyncMock dans
    #    tests/auto/test_divine_infra.py en une passe.
    # 3. V30.14 — tolerance indent gauche pour replace_line_all.
    # 4. V30.15 — auto_bisect_pytest.py (recherche dichotomique log2(N)
    #    pytest runs). Identifie le VRAI pollueur :
    #    tests/test_sauna_mode.py:12-13 — sys.modules["core.event_bus"]
    #    = MagicMock() au NIVEAU MODULE (s'execute a la collection
    #    pytest, pollue sys.modules globalement). Eradique via V30.13
    #    multi-patches (commentaire de tracabilite).
    # test_internal_context_sets_flag + test_user_mission_no_flag
    # passent maintenant en suite complete. Quarantaine inutile.
)


# ─── Exceptions V30 (Exosquelette JSON) ───────────────────────────────

class _V30InvalidJSONError(ValueError):
    """V30 — Sortie SURGEON ne contient pas de JSON parsable."""


class _V30InvalidActionError(ValueError):
    """V30 — Champ 'action' absent ou hors {insert_before, insert_after, replace_line}."""


class _V32FileExistsError(ValueError):
    """V32 — action='create_file' refuse car le fichier existe deja.

    Securite vitale : on ne permet jamais a un script de creation
    d'ecraser silencieusement un fichier existant. Le caller doit
    utiliser action='append_block' ou un patch V30 (Surgeon) a la place.
    """
    def __init__(self, message: str, target_file: str):
        super().__init__(message)
        self.target_file = target_file


class _V32InvalidMultiFilesError(ValueError):
    """V32 — Format {"files": [...]} malforme (champ manquant, type invalide)."""


class _V30AnchorNotFoundError(ValueError):
    """V30 — anchor_line introuvable dans la zone (fonction ou fichier entier)."""
    def __init__(self, message: str, anchor_line: str, anchor_function: Optional[str]):
        super().__init__(message)
        self.anchor_line = anchor_line
        self.anchor_function = anchor_function


class _V30AnchorAmbiguousError(ValueError):
    """V30 — anchor_line apparaît plusieurs fois dans la zone."""
    def __init__(self, message: str, anchor_line: str, count: int, line_numbers: List[int]):
        super().__init__(message)
        self.anchor_line = anchor_line
        self.count = count
        self.line_numbers = line_numbers


class _V30AnchorFunctionNotFoundError(ValueError):
    """V30 — anchor_function n'existe pas dans le source AST."""
    def __init__(self, message: str, anchor_function: str):
        super().__init__(message)
        self.anchor_function = anchor_function


class _ChecklistViolationError(ValueError):
    """V29 — La checklist Scrub Nurse exige qu'une ligne soit présente
    dans le source patché final, mais elle n'y est plus.
    Maintenant levee dans V30 quand action=replace_line cible une ligne
    listee dans lines_to_preserve."""
    def __init__(self, message: str, missing_line: str, reason: str,
                 violations: List[Dict[str, str]]):
        super().__init__(message)
        self.missing_line = missing_line
        self.reason = reason
        self.violations = violations


# ─── V30 Parsing & application JSON Exosquelette ──────────────────────

def _v30_clean_llm_json(raw: str) -> str:
    """V30.1 — Nettoie les coquilles typographiques courantes des LLMs en JSON.

    Le qwen2.5-coder:14b crache parfois du JSON syntaxiquement invalide :
      - apostrophes echappees facon Python : `n\\'a pas` (invalide en JSON)
      - virgules trainantes : `[1, 2,]` ou `{"a": 1,}`
      - quotes simples au lieu de doubles (rare)

    On applique des transformations conservatrices avant de re-tenter
    json.loads. Si tout echoue, le caller fallback sur ast.literal_eval.
    """
    cleaned = raw
    # Echappement Python `\'` -> `'` (legitime en JSON sans backslash)
    cleaned = cleaned.replace("\\'", "'")
    # Virgules trainantes avant `]` ou `}`
    cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
    return cleaned


def parse_v30_patch(text: str) -> Dict[str, Any]:
    """V30 — Parse la sortie SURGEON au format JSON Exosquelette.

    Retourne un dict avec les clefs :
      target_bug (str), anchor_function (str | None), anchor_line (str),
      action (str ∈ {insert_before, insert_after, replace_line}),
      new_code (str)

    Lève _V30InvalidJSONError, _V30InvalidActionError.

    V30.1 — Strategies de parsing en cascade :
      1. json.loads direct
      2. Strip markdown ```json ... ``` puis json.loads
      3. Extraction du premier {...} via regex puis json.loads
      4. Nettoyage coquilles LLM (\\' -> ', virgules trainantes) puis json.loads
      5. Fallback ast.literal_eval (parse Python plus tolerant)
      6. Echec total -> _V30InvalidJSONError
    """
    if not text or not isinstance(text, str):
        raise _V30InvalidJSONError("Sortie SURGEON vide ou non-string")

    raw = text.strip()
    # Strip markdown code fences si présents
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)

    parsed = None
    candidates: List[str] = []

    # Tentative 1 : parse direct
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Tentative 2 : extraction du premier bloc {...}
        match = _V30_JSON_BLOCK_RE.search(raw)
        if match:
            candidates.append(match.group(0))
        else:
            raise _V30InvalidJSONError(
                f"Aucun JSON detectable dans la sortie. Preview: {raw[:200]!r}"
            )

        # Tentatives 3-5 : nettoyage progressif des candidats
        if parsed is None:
            for cand in candidates:
                # Tentative directe sur le candidat extrait
                try:
                    parsed = json.loads(cand)
                    break
                except json.JSONDecodeError:
                    pass
                # V30.1 : nettoyage coquilles LLM
                cleaned = _v30_clean_llm_json(cand)
                try:
                    parsed = json.loads(cleaned)
                    break
                except json.JSONDecodeError:
                    pass
                # V30.1 : fallback ast.literal_eval (plus tolerant que json)
                try:
                    parsed = ast.literal_eval(cleaned)
                    if isinstance(parsed, dict):
                        break
                    parsed = None
                except (ValueError, SyntaxError):
                    pass

        if parsed is None:
            raise _V30InvalidJSONError(
                f"JSON malforme apres toutes les tentatives de nettoyage. "
                f"Preview brute: {raw[:200]!r}"
            )

    if not isinstance(parsed, dict):
        raise _V30InvalidJSONError(
            f"JSON parse mais n'est pas un objet (type={type(parsed).__name__})"
        )

    # V32 (2026-04-26) — FORMAT MULTI-FICHIERS.
    # L'Architecte (V32) produit du code from-scratch ET des tests
    # associes en une seule reponse atomique. Le format :
    #   {"files": [
    #     {"target_file": "core/utils/x.py", "action": "create_file",
    #      "new_code": "..."},
    #     {"target_file": "tests/auto/test_x.py", "action": "create_file",
    #      "new_code": "..."},
    #   ]}
    # Difference avec le format multi-patches V30.13 (clef "patches") :
    # ici chaque entree cible un FICHIER different et utilise les actions
    # V32 (create_file / append_block) en plus des actions V30.
    # Sandbox MEDIC l'applique de maniere atomique : si un seul fichier
    # echoue, on rollback les autres.
    if isinstance(parsed.get("files"), list):
        sub_files: List[Dict[str, Any]] = []
        for i, entry in enumerate(parsed["files"]):
            if not isinstance(entry, dict):
                raise _V32InvalidMultiFilesError(
                    f"files[{i}] n'est pas un objet (type={type(entry).__name__})"
                )
            try:
                sub_files.append(_v32_validate_file_entry(entry, index=i))
            except (_V30InvalidJSONError, _V30InvalidActionError, _V32InvalidMultiFilesError) as e:
                raise _V32InvalidMultiFilesError(
                    f"files[{i}] invalide : {e}"
                ) from e
        if not sub_files:
            raise _V32InvalidMultiFilesError("Champ 'files' fourni mais vide")
        return {
            "is_multi_files": True,
            "files": sub_files,
            "target_bug": str(parsed.get("target_bug", ""))[:300],
            "feature_name": str(parsed.get("feature_name", ""))[:120],
        }

    # V30.13 (2026-04-26) — FORMAT MULTI-PATCHES.
    # Le SURGEON peut produire un JSON avec une clef "patches" contenant
    # un tableau de sous-patches. Permet une intervention multi-ablations
    # en un seul appel (ex : replace_line sur 6 lignes identiques dans 6
    # fonctions distinctes). Chaque sous-patch est valide individuellement
    # avec la meme grammaire (anchor_line / action / new_code / anchor_function).
    if isinstance(parsed.get("patches"), list):
        sub_parsed: List[Dict[str, Any]] = []
        for i, sub in enumerate(parsed["patches"]):
            if not isinstance(sub, dict):
                raise _V30InvalidJSONError(
                    f"patches[{i}] n'est pas un objet (type={type(sub).__name__})"
                )
            try:
                sub_parsed.append(_v30_validate_single(sub, index=i))
            except (_V30InvalidJSONError, _V30InvalidActionError) as e:
                raise _V30InvalidJSONError(
                    f"patches[{i}] invalide : {e}"
                ) from e
        if not sub_parsed:
            raise _V30InvalidJSONError("Champ 'patches' fourni mais vide")
        return {
            "is_multi": True,
            "patches": sub_parsed,
            "target_bug": str(parsed.get("target_bug", ""))[:300],
        }

    # Format single (V30 standard)
    return _v30_validate_single(parsed)


def _v32_validate_file_entry(entry: Dict[str, Any], index: int = 0) -> Dict[str, Any]:
    """V32 — Valide une entree du tableau "files" du format multi-fichiers.

    Champs obligatoires :
      target_file : str (chemin relatif au project_root)
      action      : str (parmi V30+V32 actions valides)
      new_code    : str ou list[str] (le contenu)

    Champs optionnels selon l'action :
      anchor_line, anchor_function : pour insert_before/insert_after/replace_line
      (ignores si action in {create_file, append_block})
    """
    target_file = entry.get("target_file")
    action = entry.get("action")
    new_code = entry.get("new_code")

    if not target_file or not isinstance(target_file, str):
        raise _V32InvalidMultiFilesError(
            f"files[{index}].target_file manquant ou non-string"
        )
    # V32.2 (2026-04-26) — ELASTICITE ACTION DEFAUT.
    # Le 14b oublie souvent le champ "action" sur le 2eme fichier d'un
    # multi-files (cas observe au tir 16:41 sur extract_markdown_blocks :
    # files[0] avait action='create_file', files[1] omettait le champ).
    # En multi-files, le cas dominant est CREATION, donc default='create_file'.
    # Le caller peut quand meme expliciter une autre action (replace_line,
    # append_block) — seule la valeur None / absent declenche le default.
    if not action:
        action = "create_file"
    if action not in _V30_V32_ALL_ACTIONS:
        raise _V30InvalidActionError(
            f"files[{index}].action={action!r} invalide. "
            f"Valeurs acceptees: {sorted(_V30_V32_ALL_ACTIONS)}"
        )
    # V30.7 elasticite : new_code peut etre liste ou string
    if isinstance(new_code, list):
        if not all(isinstance(item, str) for item in new_code):
            raise _V30InvalidJSONError(
                f"files[{index}].new_code liste contient non-string"
            )
        new_code = "\n".join(new_code)
    if new_code is None or not isinstance(new_code, str):
        raise _V30InvalidJSONError(
            f"files[{index}].new_code manquant ou non-string"
        )

    out = {
        "target_file": target_file,
        "action": action,
        "new_code": new_code,
    }
    # Champs optionnels pour actions V30 (anchor)
    if action in _V30_VALID_ACTIONS:
        anchor_line = entry.get("anchor_line")
        if not anchor_line or not isinstance(anchor_line, str):
            raise _V30InvalidJSONError(
                f"files[{index}].anchor_line requis pour action={action}"
            )
        out["anchor_line"] = anchor_line
        anchor_function = entry.get("anchor_function")
        if anchor_function and isinstance(anchor_function, str):
            out["anchor_function"] = anchor_function
    return out


def apply_v32_create_file(
    target_file: str,
    new_code: str,
    project_root: str,
) -> str:
    """V32 — Cree un fichier neuf. Echoue si le fichier existe deja
    (securite vitale : pas d'ecrasement silencieux).

    Returns : chemin absolu du fichier cree.
    Raises  : _V32FileExistsError si target_file existe deja.
    """
    import os as _os
    full_path = _os.path.join(project_root, target_file)
    if _os.path.exists(full_path):
        raise _V32FileExistsError(
            f"V32 create_file refuse : {target_file} existe deja. "
            f"Utilise action='append_block' ou un patch V30 a la place.",
            target_file=target_file,
        )
    # Cree les repertoires intermediaires si necessaire
    parent = _os.path.dirname(full_path)
    if parent and not _os.path.isdir(parent):
        _os.makedirs(parent, exist_ok=True)
    # Garantir une terminaison \n
    content = new_code if new_code.endswith("\n") else new_code + "\n"
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)
    return full_path


def apply_v32_append_block(
    target_file: str,
    new_code: str,
    project_root: str,
) -> str:
    """V32 — Ajoute un bloc a la fin d'un fichier existant. Pas d'anchor
    requis. Si le fichier n'existe pas, leve FileNotFoundError (le caller
    doit utiliser create_file a la place).

    Garantit une separation \n\n avant le bloc ajoute pour respecter
    la convention Python (PEP 8 : 2 lignes blanches entre top-level
    definitions).
    """
    import os as _os
    full_path = _os.path.join(project_root, target_file)
    if not _os.path.exists(full_path):
        raise FileNotFoundError(
            f"V32 append_block : {target_file} n'existe pas. "
            f"Utilise action='create_file' a la place."
        )
    with open(full_path, "r", encoding="utf-8") as f:
        existing = f.read()
    # Separation propre
    sep = "\n\n" if existing and not existing.endswith("\n\n") else ""
    suffix = "\n" if not new_code.endswith("\n") else ""
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(existing + sep + new_code + suffix)
    return full_path


def _v30_validate_single(parsed: Dict[str, Any], index: int = 0) -> Dict[str, Any]:
    """V30.13 — Valide un dict de patch single (extrait de parse_v30_patch
    pour reutilisation dans le format multi-patches).
    """
    # Validation des champs requis
    anchor_line = parsed.get("anchor_line")
    action = parsed.get("action")
    new_code = parsed.get("new_code")

    if not anchor_line or not isinstance(anchor_line, str):
        raise _V30InvalidJSONError("Champ 'anchor_line' manquant ou non-string")
    if not action or action not in _V30_VALID_ACTIONS:
        raise _V30InvalidActionError(
            f"Champ 'action'={action!r} invalide. "
            f"Valeurs acceptees: {sorted(_V30_VALID_ACTIONS)}"
        )
    # V30.7 (2026-04-26) — ELASTICITE NEW_CODE.
    # Diagnostic : SURGEON 14b a produit new_code comme array de strings
    # (une ligne par element) au lieu d'une string avec \n. Semantiquement
    # equivalent, syntaxiquement rejete avant V30.7. Meme philosophie que
    # V30.6 (agnosticisme quotes) : le script absorbe la variation
    # stylistique du LLM.
    if isinstance(new_code, list):
        if not all(isinstance(item, str) for item in new_code):
            raise _V30InvalidJSONError(
                "Champ 'new_code' est une liste mais contient des elements non-string"
            )
        new_code = "\n".join(new_code)
    if new_code is None or not isinstance(new_code, str):
        raise _V30InvalidJSONError("Champ 'new_code' manquant ou non-string")

    anchor_function = parsed.get("anchor_function")
    if anchor_function is not None and not isinstance(anchor_function, str):
        anchor_function = None
    if not anchor_function:  # filtre vide
        anchor_function = None

    return {
        "target_bug": str(parsed.get("target_bug", ""))[:300],
        "anchor_function": anchor_function,
        "anchor_line": anchor_line,
        "action": action,
        "new_code": new_code,
    }


def _v30_extract_function_range(source: str, function_name: str) -> Tuple[int, int]:
    """V30 — Retourne (start_line_0based, end_line_0based_exclusive) du corps
    de la fonction dans le source via AST. Raise _V30AnchorFunctionNotFoundError
    si non trouvee.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise _V30AnchorFunctionNotFoundError(
            f"Source AST parse echoue: {exc}. "
            f"anchor_function='{function_name}' non resoluable.",
            anchor_function=function_name,
        )
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == function_name:
                start = node.lineno - 1  # 0-based inclusive
                end = getattr(node, "end_lineno", None)
                if end is None:
                    # Fallback : on prend jusqu'a la fin du source (peu de risque)
                    end = len(source.splitlines())
                return start, end  # end est inclusive en 1-based, exclusive en 0-based
    raise _V30AnchorFunctionNotFoundError(
        f"Fonction '{function_name}' introuvable dans le source",
        anchor_function=function_name,
    )


def _v30_compute_indent(line: str) -> str:
    """V30 — Retourne le prefixe d'espaces (ou tabs) de la ligne."""
    stripped = line.lstrip(" \t")
    return line[: len(line) - len(stripped)]


def _v30_indent_new_code(new_code: str, indent_prefix: str) -> str:
    """V30 — Applique le prefixe d'indentation a chaque ligne NON VIDE de
    new_code. Les lignes vides restent vides (convention Python).

    V30.8 (2026-04-26) — DEDENT PARASITAIRE.
    Diagnostic 26/04 08h : SURGEON 14b a prefixe son new_code avec 8
    espaces parasites (croyant peut-etre se placer dans le scope de la
    methode). _v30_compute_indent ramassait l'indent de l'anchor (12) et
    on additionnait : 12 + 8 = 20 espaces => IndentationError.
    Fix : passer le new_code par textwrap.dedent AVANT d'appliquer
    indent_prefix. Le LLM peut prefixer son code comme il veut (zero ou
    n espaces communs), Python normalise.
    L'indentation RELATIVE entre lignes (ex : if x: + return False indente
    de 4 supplementaires) est preservee parce que dedent ne retire que
    l'indent commun a toutes les lignes non vides.
    """
    import textwrap
    if not new_code:
        return ""
    dedented = textwrap.dedent(new_code)
    out = []
    for line in dedented.splitlines():
        if line.strip():
            out.append(indent_prefix + line)
        else:
            out.append("")
    return "\n".join(out)


def apply_v30_patch(
    source: str,
    patch: Dict[str, Any],
    checklist: Optional[Dict[str, Any]] = None,
) -> str:
    """V30 — Applique un patch JSON Exosquelette sur le source.

    patch est un dict (cf. parse_v30_patch). Lève les exceptions V30 selon
    les modes d'echec. Si checklist fournie ET action=replace_line cible
    une ligne dans lines_to_preserve -> _ChecklistViolationError (V29
    integre dans la mecanique replace_line).

    L'indentation est CALCULEE MATHEMATIQUEMENT cote Python : on lit
    l'indentation de l'anchor_line et on l'applique a chaque ligne de
    new_code. Le LLM ne gere plus la syntaxe.
    """
    if not isinstance(source, str):
        raise TypeError("source doit etre str")

    # V30.13 (2026-04-26) — FORMAT MULTI-PATCHES.
    # Si patch est en format multi (is_multi=True, patches=[...]), itere
    # sequentiellement chaque sous-patch en accumulant les modifications.
    # Permet d'appliquer N ablations en un seul appel (ex : 6 lignes
    # @patch(...) a remplacer dans 6 fonctions test_xxx distinctes).
    if patch.get("is_multi") and isinstance(patch.get("patches"), list):
        current_source = source
        for sub in patch["patches"]:
            current_source = apply_v30_patch(current_source, sub, checklist=checklist)
        return current_source

    anchor_function = patch.get("anchor_function")
    anchor_line = patch["anchor_line"]
    action = patch["action"]
    new_code = patch["new_code"]

    source_lines = source.splitlines(keepends=True)

    # Etape 1 : determiner la zone de recherche
    if anchor_function:
        try:
            zone_start, zone_end = _v30_extract_function_range(source, anchor_function)
        except _V30AnchorFunctionNotFoundError:
            raise
    else:
        zone_start = 0
        zone_end = len(source_lines)

    # Etape 2 : trouver anchor_line dans la zone (matching tolerant aux
    # espaces de fin de ligne et aux quotes simples vs doubles).
    # V30.6 (2026-04-26) — AGNOSTICISME TYPOGRAPHIQUE.
    # Diagnostic 07:30 : SURGEON a produit anchor_line avec apostrophes
    # simples (marker['intensity']) alors que le source utilise des
    # guillemets doubles (marker["intensity"]). Match strict echouait
    # alors que la ligne EXISTE bel et bien. Le LLM ne doit pas etre
    # puni pour un detail typographique sans valeur semantique.
    # Fix : normaliser ' -> " sur les deux chaines avant comparaison.
    # L'indentation reste calculee sur la ligne SOURCE originale (pas
    # la ligne normalisee), donc pas de corruption du calcul d'indent.
    # V30.14 (2026-04-26) — TOLERANCE INDENT POUR replace_line_all.
    # Pour replace_line_all, l'anchor_line peut etre fournie SANS indentation
    # gauche (le SURGEON ne peut pas connaitre l'indent de N occurrences
    # potentiellement differentes). Le strip complet permet le match. Pour
    # les autres actions (insert_*/replace_line single), on garde le match
    # strict avec indent pour preserver la distinction entre lignes
    # similaires a des indents differents.
    _strip_indent = (action == "replace_line_all")

    def _v30_norm_for_match(s: str, strip_all: bool = _strip_indent) -> str:
        if strip_all:
            return s.strip().replace("'", '"')
        return s.rstrip("\r\n").replace("'", '"')

    anchor_clean_norm = _v30_norm_for_match(anchor_line)
    matches: List[int] = []
    for i in range(zone_start, min(zone_end, len(source_lines))):
        if _v30_norm_for_match(source_lines[i]) == anchor_clean_norm:
            matches.append(i)

    if len(matches) == 0:
        zone_label = repr(anchor_function) if anchor_function else "all"
        anchor_preview = repr(anchor_line[:120])
        raise _V30AnchorNotFoundError(
            f"anchor_line introuvable dans la zone "
            f"(function={zone_label}). anchor_line={anchor_preview}",
            anchor_line=anchor_line,
            anchor_function=anchor_function,
        )
    # V30.13 — replace_line_all accepte plusieurs matches (intentionnel)
    if len(matches) > 1 and action != "replace_line_all":
        raise _V30AnchorAmbiguousError(
            f"anchor_line trouvee a {len(matches)} endroits dans la zone "
            f"(lignes {[m + 1 for m in matches]}). "
            f"Ajoute 'anchor_function' pour restreindre, ou utilise "
            f"action='replace_line_all' pour remplacer toutes les occurrences.",
            anchor_line=anchor_line,
            count=len(matches),
            line_numbers=[m + 1 for m in matches],
        )

    anchor_idx = matches[0]
    anchor_full_line = source_lines[anchor_idx]

    # V29 + V30 — validation checklist sur replace_line
    if (checklist and not checklist.get("fallback")
            and action == "replace_line"
            and checklist.get("lines_to_preserve")):
        for entry in checklist["lines_to_preserve"]:
            line_text = entry.get("line_text", "") if isinstance(entry, dict) else ""
            if not line_text:
                continue
            # On compare avec la ligne entiere ou avec son contenu strippe
            if line_text in anchor_full_line or anchor_clean.strip() == line_text.strip():
                raise _ChecklistViolationError(
                    f"V30 + V29 : action=replace_line viserait l'ancre "
                    f"{anchor_line[:60]!r} qui apparait dans "
                    f"lines_to_preserve de la Scrub Nurse. Refus.",
                    missing_line=line_text,
                    reason=entry.get("reason", "preservation requise"),
                    violations=[{
                        "line_text": line_text,
                        "reason": entry.get("reason", ""),
                    }],
                )

    # Etape 3 : calculer l'indentation et indenter new_code
    indent_prefix = _v30_compute_indent(anchor_full_line)
    indented = _v30_indent_new_code(new_code, indent_prefix)

    # Garantir une terminaison \n pour pouvoir l'inserer comme ligne
    if indented and not indented.endswith("\n"):
        indented += "\n"

    # Etape 4 : appliquer l'action
    if action == "insert_before":
        new_lines = (
            source_lines[:anchor_idx] + [indented] + source_lines[anchor_idx:]
        )
    elif action == "insert_after":
        new_lines = (
            source_lines[: anchor_idx + 1] + [indented]
            + source_lines[anchor_idx + 1:]
        )
    elif action == "replace_line":
        new_lines = (
            source_lines[:anchor_idx] + [indented]
            + source_lines[anchor_idx + 1:]
        )
    elif action == "replace_line_all":
        # V30.13 — remplace TOUTES les occurrences. Chaque match recoit
        # son propre indent calcule sur sa ligne d'origine (les anchors
        # peuvent avoir des indentations differentes meme avec la meme
        # ligne semantique). On parcourt en arriere pour preserver les
        # indices apres mutation.
        new_lines = list(source_lines)
        for idx in sorted(matches, reverse=True):
            line_indent = _v30_compute_indent(new_lines[idx])
            this_indented = _v30_indent_new_code(new_code, line_indent)
            if this_indented and not this_indented.endswith("\n"):
                this_indented += "\n"
            new_lines = new_lines[:idx] + [this_indented] + new_lines[idx + 1:]
    else:
        # Defensif (parse_v30_patch a deja valide)
        raise _V30InvalidActionError(f"Action inconnue: {action}")

    return "".join(new_lines)


# ─── PatchResult dataclass ────────────────────────────────────────────

@dataclass
class PatchResult:
    """V30 — résultat d'un cycle MEDIC complet (Exosquelette JSON)."""
    status: str  # V30 statuses :
                 #   success / patch_impossible / invalid_json / invalid_action /
                 #   anchor_not_found / anchor_ambiguous / anchor_function_not_found /
                 #   checklist_violation (V29) / syntax_error / test_failed /
                 #   internal_error
    surgeon_output: str = ""
    blocks_applied: int = 0
    unified_diff: str = ""           # diff git généré post-hoc pour stockage
    target_file: str = ""
    iteration: int = 0
    failed_block_index: int = -1
    failed_block_search: str = ""
    failed_block_replace: str = ""    # V27 : pour replaced_without_context
    checklist_violations: List[Dict[str, str]] = field(default_factory=list)  # V29
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
        if self.status == "invalid_json":
            return (
                "ECHEC V30 : Ta sortie n'est PAS du JSON parsable.\n"
                "Sors UNIQUEMENT un objet JSON avec les clefs :\n"
                "  target_bug, anchor_line, action, new_code, anchor_function (optionnel)\n"
                "Pas de markdown, pas de balise ```json, pas de narration.\n"
                f"Erreur exacte : {self.error_message[:max_chars]}"
            )
        if self.status == "invalid_action":
            return (
                "ECHEC V30 : ton champ 'action' est invalide.\n"
                "Valeurs ACCEPTEES : 'insert_before', 'insert_after', 'replace_line'.\n"
                "Pour un guard de validation (le plus frequent), utilise 'insert_before'\n"
                "avec anchor_line = la premiere ligne de la zone a proteger.\n"
                f"Erreur exacte : {self.error_message[:max_chars]}"
            )
        if self.status == "anchor_not_found":
            snippet = self.failed_block_search[:max_chars]
            return (
                f"ECHEC V30 : anchor_line introuvable dans le source.\n\n"
                f"Ton anchor_line :\n  {snippet}\n\n"
                f"DIAGNOSTIC :\n"
                f"  1. Tu as approche le texte au lieu de copier verbatim.\n"
                f"  2. Tu as ajoute/retire des espaces ou modifie l'indentation.\n"
                f"  3. La ligne n'existe pas dans le source.\n\n"
                f"CORRECTION : copie INTEGRALEMENT une ligne du source\n"
                f"caractere par caractere (espaces, tabulations, ponctuation\n"
                f"identiques). Verifie ton anchor_function si fourni."
            )
        if self.status == "anchor_ambiguous":
            snippet = self.failed_block_search[:max_chars]
            return (
                f"ECHEC V30 : anchor_line trouvee a PLUSIEURS endroits\n"
                f"dans le source.\n\n"
                f"Ton anchor_line :\n  {snippet}\n\n"
                f"CORRECTION : ajoute le champ 'anchor_function' au JSON\n"
                f"avec le nom de la fonction qui contient la ligne ciblee.\n"
                f"Cela restreindra la recherche au corps de cette fonction.\n"
                f"Exemple : \"anchor_function\": \"strip_header\""
            )
        if self.status == "anchor_function_not_found":
            return (
                f"ECHEC V30 : anchor_function ne correspond a aucune fonction\n"
                f"definie dans le source.\n\n"
                f"CORRECTION : verifie l'orthographe exacte du nom de fonction.\n"
                f"Le source AST n'a pas trouve de def/async def avec ce nom.\n"
                f"Erreur exacte : {self.error_message[:max_chars]}"
            )
        if self.status == "checklist_violation":
            lines_block = "\n".join(
                f"  - {v['line_text'][:120]}\n    (raison : {v.get('reason','')[:120]})"
                for v in (self.checklist_violations or [])[:5]
            )
            return (
                f"ECHEC V29 + V30 CHECKLIST : ton action 'replace_line' viserait\n"
                f"une ligne EXIGEE en preservation par la Scrub Nurse.\n\n"
                f"Lignes inviolables :\n{lines_block}\n\n"
                f"CORRECTION : utilise plutot 'insert_before' ou 'insert_after'\n"
                f"avec un guard prealable. Exemple :\n"
                f"  action: insert_before\n"
                f"  anchor_line: <la ligne risquee>\n"
                f"  new_code: if not <var>:\\n    return False"
            )
        if self.status == "syntax_error":
            err = self.compile_stderr[-max_chars:] if self.compile_stderr else ""
            return (
                f"PYTHON SYNTAX ERROR après application V30 du patch :\n{err}\n\n"
                f"V30 calcule l'indentation mathematiquement, donc l'erreur\n"
                f"vient probablement de la SYNTAXE INTERNE de ton new_code\n"
                f"(parenthese non fermee, indentation interne, mots-cles)."
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
    # V30.3 — quarantaine chirurgicale des tests flaky (passent en standalone,
    # echouent dans le sandbox a cause d'effets de bord non isoles)
    for _deselect in _PYTEST_DESELECT_TESTS:
        cmd.extend(["--deselect", _deselect])

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
