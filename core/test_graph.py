# core/test_graph.py — Graphe de dependances AST pour tests cibles
# Parse les fichiers de tests pour construire le mapping source → tests.
# Permet d'executer uniquement les tests affectes par un changement.
# 0 LLM, pur deterministe.

import ast
import os
import logging
from typing import Dict, Set, Optional, List

logger = logging.getLogger("TestGraph")

# Repertoire projet
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Prefixes de modules sources du projet
_SOURCE_PREFIXES = ("core.", "Agents.", "tools.")

# Modules "hub" — importes par quasi tous les tests.
# Un changement dans ces modules necessite un run complet.
_HUB_MODULES = frozenset({
    "core.event_bus.bus",
    "core.base_agent",
    "core.orchestrator",
})


class TestGraph:
    """Graphe de dependances source → tests, construit par analyse AST.

    Analyse les imports de chaque fichier test pour savoir quels tests
    sont affectes par un changement dans un module source.
    """
    __test__ = False  # Pas une classe de test pytest

    def __init__(self, tests_dir: str = None, project_root: str = None):
        self._tests_dir = tests_dir or os.path.join(PROJECT_ROOT, "tests")
        self._project_root = project_root or PROJECT_ROOT
        self._graph: Dict[str, Set[str]] = {}   # "core.thalamus" → {"tests/test_thalamus.py", ...}
        self._mtimes: Dict[str, float] = {}      # "test_thalamus.py" → mtime au dernier parse
        self._built = False

    def build(self):
        """Construit ou met a jour le graphe en parsant les fichiers de tests.

        Utilise un cache mtime pour ne re-parser que les fichiers modifies.
        Cout : ~50ms pour 87 fichiers de tests.
        """
        if not os.path.isdir(self._tests_dir):
            logger.warning(f"[TEST_GRAPH] Repertoire tests introuvable: {self._tests_dir}")
            self._built = True
            return

        for fname in os.listdir(self._tests_dir):
            if not fname.startswith("test_") or not fname.endswith(".py"):
                continue

            filepath = os.path.join(self._tests_dir, fname)
            try:
                current_mtime = os.path.getmtime(filepath)
            except OSError:
                continue

            # Skip si deja parse et pas modifie
            if fname in self._mtimes and self._mtimes[fname] >= current_mtime:
                continue

            self._parse_test_file(fname, filepath)
            self._mtimes[fname] = current_mtime

        self._built = True
        total_links = sum(len(v) for v in self._graph.values())
        logger.info(f"[TEST_GRAPH] Graphe: {len(self._graph)} modules, "
                    f"{total_links} liens, {len(self._mtimes)} tests parses")

    def _parse_test_file(self, fname: str, filepath: str):
        """Parse un fichier test et extrait ses imports source."""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError, OSError):
            return

        test_path = f"tests/{fname}"

        # Retirer les anciens liens de ce fichier test
        for module in list(self._graph.keys()):
            self._graph[module].discard(test_path)
            if not self._graph[module]:
                del self._graph[module]

        # Extraire les imports
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if any(node.module.startswith(p) for p in _SOURCE_PREFIXES):
                    self._graph.setdefault(node.module, set()).add(test_path)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name.startswith(p) for p in _SOURCE_PREFIXES):
                        self._graph.setdefault(alias.name, set()).add(test_path)

    def get_relevant_tests(self, source_file: str) -> Optional[List[str]]:
        """Retourne les fichiers de test pertinents pour un fichier source.

        Args:
            source_file: Chemin relatif (ex: 'core/thalamus.py')

        Returns:
            - None si module hub (→ run complet necessaire)
            - Liste de chemins relatifs de tests (ex: ['tests/test_thalamus.py'])
            - Liste vide si aucun test trouve
        """
        if not self._built:
            self.build()

        # Convertir chemin fichier en module Python (core/thalamus.py → core.thalamus)
        normalized = source_file.replace("\\", "/").replace("/", ".")
        if normalized.endswith(".py"):
            normalized = normalized[:-3]

        # Module hub → run complet
        if normalized in _HUB_MODULES:
            return None
        # Parent d'un module hub (ex: core.event_bus → core.event_bus.bus)
        for hub in _HUB_MODULES:
            if hub.startswith(normalized + "."):
                return None

        # Chercher les tests lies (exact match)
        tests = set(self._graph.get(normalized, set()))

        # Chercher aussi les sous-modules (ex: core.event_bus → core.event_bus.bus)
        for module, test_files in self._graph.items():
            if module.startswith(normalized + ".") and module not in _HUB_MODULES:
                tests |= test_files

        return sorted(tests) if tests else []

    def get_stats(self) -> dict:
        """Stats du graphe pour debug et endpoint API."""
        return {
            "modules_tracked": len(self._graph),
            "test_files_parsed": len(self._mtimes),
            "total_links": sum(len(v) for v in self._graph.values()),
            "built": self._built,
        }
