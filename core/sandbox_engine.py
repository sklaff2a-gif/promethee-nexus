# core/sandbox_engine.py — SandboxEngine : Environnement de test isole
# Promethe teste ses modifications sur une copie de lui-meme
# avant de les deployer en production.
# 0 LLM, pur deterministe.

import asyncio
import hashlib
import json
import os
import shutil
import time
import logging
from dataclasses import dataclass
from typing import Optional, List

from core.test_runner import (
    parse_pytest_output,
    run_pytest,
    pre_validate_ast,
    select_tests_for_file,
    derive_test_file,
)

logger = logging.getLogger("SandboxEngine")

# --- Repertoires ---

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SANDBOX_DIR = os.path.join(os.path.dirname(PROJECT_ROOT), "PROMETHEE_sandbox")

# --- Filtres ---

EXCLUDE_DIRS = {
    "memory", "logs", ".git", "__pycache__", ".pytest_cache",
    "chroma_db", ".claude", "PROMETHEE_sandbox", "datasets",
    "node_modules", "static", "USER_DROPZONE", "htmlcov",
    ".mypy_cache", "venv", ".venv",
}
EXCLUDE_EXTENSIONS = {".bak", ".tmp", ".log", ".pyc"}


# --- Constantes ---

TEST_TIMEOUT = 120  # secondes
FRESHNESS_THRESHOLD = 3600  # 1 heure

# --- Cache MD5 (Niveau 3) ---
SANDBOX_CACHE_FILE = os.path.join(PROJECT_ROOT, "memory", "sandbox_cache.json")
CACHE_FULL_RUN_INTERVAL = 6 * 3600  # 6h — filet de securite
CACHE_ENTRY_TTL = 24 * 3600  # 24h — eviction des entrees anciennes


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
    predicted: bool = False  # True si le resultat est une prediction (pas de run reel)


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
        self._test_graph = None
        self._cache = {}
        self._last_full_run = 0.0
        self._last_was_predicted = False  # Garde-fou promote()
        self._load_cache()
        self._stats = {
            "total_tests_run": 0,
            "total_passed": 0,
            "total_failed": 0,
            "total_promotions": 0,
            "total_discards": 0,
            "pre_validation_catches": 0,
            "graph_targeted_runs": 0,
            "cache_hits": 0,
            "prediction_hits": 0,
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

            # Etape 0: Pre-validation AST (syntaxe + imports, <50ms)
            if target_file:
                pre_result = self._pre_validate(target_file)
                if pre_result is not None:
                    self._stats["pre_validation_catches"] += 1
                    return pre_result

            # Determiner quels tests lancer (graphe + fallback)
            pytest_args = ["-x", "--tb=short", "-q"]
            pair_hash = None
            selected_tests = None
            is_full_run = True
            if target_file:
                test_files = self._select_tests(target_file)
                if test_files is None:
                    # Module hub → run complet
                    pytest_args.append("tests/")
                elif test_files:
                    self._stats["graph_targeted_runs"] += 1
                    selected_tests = test_files
                    pytest_args.extend(test_files)
                    is_full_run = False
                else:
                    pytest_args.append("tests/")
            else:
                pytest_args.append("tests/")

            # Prediction compilee (Niveau 5) : si confiance elevee, skip sandbox
            if target_file and not is_full_run:
                predicted = self._try_prediction(target_file)
                if predicted is not None:
                    self._last_was_predicted = True
                    return predicted

            # Cache MD5 : verifier avant de lancer pytest
            if selected_tests and target_file:
                pair_hash = self._compute_pair_hash(target_file, selected_tests)
                cached = self._cache_lookup(pair_hash)
                if cached is not None:
                    return cached

            # Execution via test_runner unifie
            success, output, passed, failed, errors = await run_pytest(
                pytest_args, cwd=SANDBOX_DIR, timeout=TEST_TIMEOUT,
            )
            duration = time.time() - start

            # Run reel termine → reset le flag prediction
            self._last_was_predicted = False

            # Mettre a jour les stats
            self._stats["total_tests_run"] += passed + failed + errors
            self._stats["total_passed"] += passed
            self._stats["total_failed"] += failed

            result = SandboxResult(
                success=success, tests_passed=passed, tests_failed=failed,
                tests_errors=errors, output=output,
                duration_seconds=duration,
            )

            # Cache MD5 : stocker le resultat
            if pair_hash:
                self._cache_update(pair_hash, result)

            # Feedback loop sandbox prediction (Niveau 5)
            if target_file:
                self._record_sandbox_feedback(target_file, success)

            # Tracker le dernier run complet (filet de securite 6h)
            if is_full_run:
                self._last_full_run = time.time()
                self._save_cache()

            return result

    def promote(self, relative_path: str) -> bool:
        """Copie un fichier sandbox vers la production avec backup .bak.

        IMPORTANT: L'appelant DOIT avoir fait un run_tests() reel AVANT d'appeler
        promote(). Si le dernier run_tests() etait une prediction (predicted=True),
        promote() refuse la promotion et demande un run reel.

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

        # GARDE-FOU: verifier que le dernier run n'etait pas une prediction
        if self._last_was_predicted:
            logger.warning(
                f"[SANDBOX] Promotion REFUSEE pour {relative_path}: "
                "dernier run etait une prediction. Relancer run_tests() en mode reel."
            )
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

    # --- CACHE MD5 ---

    def _load_cache(self):
        """Charge le cache depuis le disque et evince les entrees > 24h."""
        try:
            with open(SANDBOX_CACHE_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self._cache = {}
            return
        self._last_full_run = raw.pop("_last_full_run", 0.0)
        # Eviction : supprimer les entrees plus vieilles que CACHE_ENTRY_TTL
        now = time.time()
        self._cache = {
            k: v for k, v in raw.items()
            if isinstance(v, dict) and now - v.get("timestamp", 0) < CACHE_ENTRY_TTL
        }

    def _save_cache(self):
        """Persiste le cache sur disque."""
        try:
            data = dict(self._cache)
            data["_last_full_run"] = self._last_full_run
            os.makedirs(os.path.dirname(SANDBOX_CACHE_FILE), exist_ok=True)
            with open(SANDBOX_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError as e:
            logger.warning(f"[SANDBOX_CACHE] Erreur sauvegarde: {e}")

    def _compute_pair_hash(self, source_file: str, test_files: List[str]) -> Optional[str]:
        """Calcule le hash MD5 de la paire source + tests.

        Returns:
            'md5_source:md5_test1,md5_test2' ou None si fichier illisible.
        """
        # Hash du fichier source
        source_path = os.path.join(SANDBOX_DIR, source_file)
        try:
            with open(source_path, "rb") as f:
                source_hash = hashlib.md5(f.read()).hexdigest()
        except OSError:
            return None

        # Hash de chaque fichier test (tries pour determinisme)
        test_hashes = []
        for tf in sorted(test_files):
            test_path = os.path.join(SANDBOX_DIR, tf)
            try:
                with open(test_path, "rb") as f:
                    test_hashes.append(hashlib.md5(f.read()).hexdigest())
            except OSError:
                return None

        return source_hash + ":" + ",".join(test_hashes)

    def _cache_lookup(self, pair_hash: str) -> Optional[SandboxResult]:
        """Verifie le cache pour un hash donne.

        Retourne SandboxResult si cache hit succes, None sinon.
        Cache hit echec → retourne None (on relance, le fix a pu atterrir).
        """
        if not pair_hash or pair_hash not in self._cache:
            return None

        entry = self._cache[pair_hash]

        # Seuls les succes sont caches (echecs toujours relances)
        if not entry.get("success", False):
            return None

        # Filet de securite : bypass si aucun run complet depuis 6h
        if time.time() - self._last_full_run > CACHE_FULL_RUN_INTERVAL:
            return None

        self._stats["cache_hits"] += 1
        return SandboxResult(
            success=True,
            tests_passed=entry.get("tests_passed", 0),
            tests_failed=0,
            tests_errors=0,
            output=f"[CACHE HIT] Tests identiques deja valides (hash: {pair_hash[:16]}...)",
            duration_seconds=0.0,
        )

    def _cache_update(self, pair_hash: str, result: SandboxResult):
        """Met a jour le cache apres un run reel."""
        if not pair_hash:
            return
        self._cache[pair_hash] = {
            "success": result.success,
            "tests_passed": result.tests_passed,
            "timestamp": time.time(),
        }
        self._save_cache()

    # --- PREDICTION COMPILEE (Niveau 5) ---

    def _try_prediction(self, target_file: str) -> Optional[SandboxResult]:
        """Tente de predire le resultat sandbox via le neural_compiler.

        Returns:
            SandboxResult(predicted=True) si prediction succes, None sinon.
        """
        try:
            from core.neural_compiler import compiler
            agent, change_type = self._extract_change_metadata(target_file)
            predicted = compiler.predict_sandbox_outcome(
                agent=agent,
                target_file=target_file,
                change_type=change_type,
            )
            if predicted is True:
                self._stats["prediction_hits"] += 1
                return SandboxResult(
                    success=True,
                    tests_passed=0,
                    tests_failed=0,
                    tests_errors=0,
                    output=f"[PREDICTION] Succes predit par neural_compiler (agent={agent}, type={change_type})",
                    duration_seconds=0.0,
                    predicted=True,
                )
        except Exception as e:
            logger.debug(f"[SANDBOX] Prediction echouee: {e}")
        return None

    def _record_sandbox_feedback(self, target_file: str, success: bool):
        """Enregistre le resultat reel d'un run pour la boucle d'apprentissage."""
        try:
            from core.neural_compiler import compiler
            agent, change_type = self._extract_change_metadata(target_file)
            compiler.record_sandbox_outcome(agent, target_file, change_type, success)
        except Exception as e:
            logger.debug(f"[SANDBOX] Feedback prediction echoue: {e}")

    def _verify_prediction(self, target_file: str, actual_success: bool):
        """Verifie la prediction lors d'un promote() (feedback loop)."""
        try:
            from core.neural_compiler import compiler
            agent, change_type = self._extract_change_metadata(target_file)
            compiler.verify_sandbox_prediction(agent, target_file, change_type, actual_success)
        except Exception:
            pass

    def _extract_change_metadata(self, target_file: str) -> tuple:
        """Extrait l'agent et le type de changement depuis le contexte.

        Heuristique basee sur le chemin du fichier.
        Returns:
            (agent_name, change_type)
        """
        normalized = target_file.replace("\\", "/")

        # Deviner l'agent depuis le chemin
        agent = "unknown"
        if "Agents/" in normalized:
            basename = os.path.basename(normalized)
            name = os.path.splitext(basename)[0]
            if name.endswith("_agent"):
                agent = name.replace("_agent", "")
        elif "core/" in normalized:
            agent = "evolution"  # La plupart des changements core viennent d'evolution

        # Deviner le type de changement
        change_type = "evolution"  # defaut
        if "test" in normalized.lower():
            change_type = "test"
        elif "config" in normalized.lower():
            change_type = "config"
        elif "grimoire" in normalized.lower():
            change_type = "grimoire"

        return agent, change_type

    # --- METHODES PRIVEES ---

    def _get_test_graph(self):
        """Initialisation lazy du graphe de dependances."""
        if self._test_graph is None:
            from core.test_graph import TestGraph
            self._test_graph = TestGraph()
            self._test_graph.build()
        return self._test_graph

    def _pre_validate(self, target_file: str):
        """Pre-validation AST du fichier cible (<50ms).

        Delegue a test_runner.pre_validate_ast() pour la logique partagee.
        Retourne SandboxResult si erreur, None si OK.
        """
        file_path = os.path.join(SANDBOX_DIR, target_file)
        if not os.path.isfile(file_path):
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                source = f.read()
        except OSError:
            return None

        error = pre_validate_ast(
            source,
            check_project_imports=True,
            search_dirs=[SANDBOX_DIR, PROJECT_ROOT],
        )
        if error:
            return SandboxResult(
                success=False, tests_passed=0, tests_failed=0,
                tests_errors=1,
                output=f"[PRE-VALIDATION] {error}",
                duration_seconds=0.0,
            )
        return None

    def _select_tests(self, target_file: str) -> Optional[List[str]]:
        """Selectionne les tests pertinents via le graphe de dependances.

        Delegue a test_runner.select_tests_for_file() pour la logique partagee.

        Returns:
            None si module hub (run complet), liste de chemins tests, ou liste vide.
        """
        graph = self._get_test_graph()
        return select_tests_for_file(
            target_file,
            test_graph=graph,
            verify_existence=True,
            verify_dir=SANDBOX_DIR,
        )

    def _derive_test_file(self, target_file: str) -> Optional[str]:
        """Derive le fichier de test associe a un fichier source."""
        return derive_test_file(target_file)

    def _parse_pytest_output(self, output: str) -> tuple:
        """Parse la ligne summary de pytest."""
        return parse_pytest_output(output)


# Singleton
sandbox = SandboxEngine()
