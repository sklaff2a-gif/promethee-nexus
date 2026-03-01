# core/sandbox_engine.py — SandboxEngine : Environnement de test isole
# Promethe teste ses modifications sur une copie de lui-meme
# avant de les deployer en production.
# 0 LLM, pur deterministe.

import asyncio
import json
import os
import re
import shutil
import time
import logging
from dataclasses import dataclass, asdict
from typing import Optional

logger = logging.getLogger("SandboxEngine")

# --- Repertoires ---

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SANDBOX_DIR = os.path.join(os.path.dirname(PROJECT_ROOT), "PROMETHEE_sandbox")

# --- Filtres ---

EXCLUDE_DIRS = {"memory", "logs", ".git", "__pycache__", ".pytest_cache", "chroma_db", ".claude"}
EXCLUDE_EXTENSIONS = {".bak", ".tmp", ".log"}

# --- Constantes ---

TEST_TIMEOUT = 120  # secondes
FRESHNESS_THRESHOLD = 3600  # 1 heure


# --- Resultat ---

@dataclass
class SandboxResult:
    """Resultat d'une execution de tests dans le sandbox."""
    success: bool
    tests_passed: int
    tests_failed: int
    tests_errors: int
    output: str
    duration_seconds: float


class SandboxEngine:
    """Singleton — Environnement de test isole pour les evolutions."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._last_refresh = 0.0
        self._lock = asyncio.Lock()
        self._stats = {
            "total_tests_run": 0,
            "total_passed": 0,
            "total_failed": 0,
            "total_promotions": 0,
            "total_discards": 0,
        }

    def init(self):
        """Initialise le sandbox engine (appele depuis main.py lifespan)."""
        logger.info("[SANDBOX] Moteur de test securise actif.")

    @classmethod
    def reset_singleton(cls):
        """Reset pour les tests."""
        cls._instance = None

    # --- METHODES PUBLIQUES ---

    def create_or_refresh(self) -> bool:
        """Cree ou rafraichit le sandbox depuis la production.

        Utilise shutil.copytree avec ignore function pour exclure
        les repertoires et extensions non necessaires.
        Retourne True si le sandbox a ete cree/rafraichi.
        """
        if self.is_fresh():
            return False

        def _ignore_filter(directory, contents):
            """Filtre les repertoires et fichiers exclus."""
            ignored = set()
            for item in contents:
                # Exclure les repertoires
                if item in EXCLUDE_DIRS:
                    ignored.add(item)
                    continue
                # Exclure les extensions
                _, ext = os.path.splitext(item)
                if ext.lower() in EXCLUDE_EXTENSIONS:
                    ignored.add(item)
            return ignored

        try:
            shutil.copytree(
                PROJECT_ROOT,
                SANDBOX_DIR,
                ignore=_ignore_filter,
                dirs_exist_ok=True,
            )
            self._last_refresh = time.time()
            logger.info(f"[SANDBOX] Sandbox cree/rafraichi dans {SANDBOX_DIR}")
            return True
        except OSError as e:
            logger.warning(f"[SANDBOX] Erreur copytree: {e}")
            return False

    def apply_change(self, relative_path: str, new_content: str) -> bool:
        """Ecrit un fichier modifie dans le sandbox.

        Args:
            relative_path: Chemin relatif depuis la racine projet (ex: core/foo.py)
            new_content: Contenu du fichier modifie

        Returns:
            True si l'ecriture a reussi
        """
        if not os.path.isdir(SANDBOX_DIR):
            logger.warning("[SANDBOX] Sandbox inexistant — appeler create_or_refresh() d'abord.")
            return False

        target = os.path.join(SANDBOX_DIR, relative_path)
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(new_content)
            return True
        except OSError as e:
            logger.warning(f"[SANDBOX] Erreur ecriture {relative_path}: {e}")
            return False

    async def run_tests(self, target_file: Optional[str] = None) -> SandboxResult:
        """Execute les tests dans le sandbox.

        Args:
            target_file: Si specifie, derive le fichier de test associe
                         et n'execute que celui-ci.

        Returns:
            SandboxResult avec le resultat de l'execution
        """
        async with self._lock:
            start = time.time()

            # Determiner quels tests lancer
            test_args = ["python", "-m", "pytest", "-x", "--tb=short", "-q"]
            if target_file:
                test_file = self._derive_test_file(target_file)
                if test_file:
                    test_path = os.path.join(SANDBOX_DIR, test_file)
                    if os.path.isfile(test_path):
                        test_args.append(test_file)
                    else:
                        test_args.append("tests/")
                else:
                    test_args.append("tests/")
            else:
                test_args.append("tests/")

            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"

            try:
                proc = await asyncio.create_subprocess_exec(
                    *test_args,
                    cwd=SANDBOX_DIR,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=env,
                )
                try:
                    stdout, _ = await asyncio.wait_for(
                        proc.communicate(), timeout=TEST_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.communicate()
                    duration = time.time() - start
                    return SandboxResult(
                        success=False, tests_passed=0, tests_failed=0,
                        tests_errors=0, output="TIMEOUT après {}s".format(TEST_TIMEOUT),
                        duration_seconds=duration,
                    )

                output = stdout.decode("utf-8", errors="replace")
                # Tronquer a 2000 chars
                if len(output) > 2000:
                    output = output[:2000] + "\n... (tronque)"

                duration = time.time() - start
                passed, failed, errors = self._parse_pytest_output(output)
                success = proc.returncode == 0

                # Mettre a jour les stats
                self._stats["total_tests_run"] += passed + failed + errors
                self._stats["total_passed"] += passed
                self._stats["total_failed"] += failed

                return SandboxResult(
                    success=success, tests_passed=passed, tests_failed=failed,
                    tests_errors=errors, output=output,
                    duration_seconds=duration,
                )

            except FileNotFoundError:
                duration = time.time() - start
                return SandboxResult(
                    success=False, tests_passed=0, tests_failed=0,
                    tests_errors=0, output="python non trouve",
                    duration_seconds=duration,
                )

    def promote(self, relative_path: str) -> bool:
        """Copie un fichier sandbox vers la production avec backup .bak.

        Args:
            relative_path: Chemin relatif (ex: core/foo.py)

        Returns:
            True si la promotion a reussi
        """
        sandbox_file = os.path.join(SANDBOX_DIR, relative_path)
        prod_file = os.path.join(PROJECT_ROOT, relative_path)

        if not os.path.isfile(sandbox_file):
            logger.warning(f"[SANDBOX] Fichier inexistant dans sandbox: {relative_path}")
            return False

        try:
            # Backup prealable si le fichier prod existe
            if os.path.isfile(prod_file):
                shutil.copy2(prod_file, prod_file + ".bak")

            os.makedirs(os.path.dirname(prod_file), exist_ok=True)
            shutil.copy2(sandbox_file, prod_file)
            self._stats["total_promotions"] += 1
            logger.info(f"[SANDBOX] Promu {relative_path} sandbox → production")
            return True
        except OSError as e:
            logger.warning(f"[SANDBOX] Erreur promotion {relative_path}: {e}")
            return False

    def discard(self, relative_path: str):
        """Restaure le fichier original (production vers sandbox).

        Args:
            relative_path: Chemin relatif (ex: core/foo.py)
        """
        prod_file = os.path.join(PROJECT_ROOT, relative_path)
        sandbox_file = os.path.join(SANDBOX_DIR, relative_path)

        try:
            if os.path.isfile(prod_file):
                shutil.copy2(prod_file, sandbox_file)
                self._stats["total_discards"] += 1
                logger.info(f"[SANDBOX] Restaure {relative_path} production → sandbox")
        except OSError as e:
            logger.warning(f"[SANDBOX] Erreur discard {relative_path}: {e}")

    def is_fresh(self) -> bool:
        """Verifie si le sandbox est suffisamment recent."""
        return time.time() - self._last_refresh < FRESHNESS_THRESHOLD

    def get_stats(self) -> dict:
        """Retourne les statistiques du sandbox."""
        return {
            "sandbox_exists": os.path.isdir(SANDBOX_DIR),
            "is_fresh": self.is_fresh(),
            "last_refresh": self._last_refresh,
            **self._stats,
        }

    # --- METHODES PRIVEES ---

    def _derive_test_file(self, target_file: str) -> Optional[str]:
        """Derive le fichier de test associe a un fichier source.

        Ex: core/foo.py → tests/test_foo.py
            Agents/bar_agent.py → tests/test_bar.py
        """
        # Normaliser les separateurs
        normalized = target_file.replace("\\", "/")
        basename = os.path.basename(normalized)
        name, _ = os.path.splitext(basename)

        # Agents/bar_agent.py → test_bar.py
        if name.endswith("_agent"):
            name = name  # garder tel quel pour test_bar_agent → test_evolution etc.

        return f"tests/test_{name}.py"

    def _parse_pytest_output(self, output: str) -> tuple:
        """Parse la ligne summary de pytest pour extraire passed/failed/errors.

        Returns:
            (passed, failed, errors)
        """
        passed = 0
        failed = 0
        errors = 0

        # Pattern: "X passed", "X failed", "X error"
        m_passed = re.search(r"(\d+) passed", output)
        m_failed = re.search(r"(\d+) failed", output)
        m_errors = re.search(r"(\d+) error", output)

        if m_passed:
            passed = int(m_passed.group(1))
        if m_failed:
            failed = int(m_failed.group(1))
        if m_errors:
            errors = int(m_errors.group(1))

        return passed, failed, errors


# Singleton
sandbox = SandboxEngine()
