# core/impact_analyzer.py
"""
IMPACT GRAPH — Analyseur de dépendances et santé des modules.

Scan AST des imports top-level, construction du graphe de dépendances,
collecte de l'état de santé de chaque module. 0 LLM, pur déterministe.
"""
import ast
import os
import time
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Répertoire racine du projet (parent de core/)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Répertoires à scanner pour les modules
_SCAN_DIRS = {
    "core": ("core", "core"),
    "agents": ("Agents", "agent"),
    "grimoire": (os.path.join("core", "grimoire"), "grimoire"),
    "capabilities": (os.path.join("core", "capabilities"), "capability"),
}

# Fichiers racine à inclure
_ROOT_FILES = ["main.py", "config.py", "guardian.py", "start_nexus.py"]

# Seuils de santé
_DEGRADED_THRESHOLD = 2  # erreurs → degraded
_ERROR_THRESHOLD = 5     # erreurs → error

# Cache TTL
_CACHE_TTL = 120  # secondes

# Mapping agent_name → module_id pour attribuer les erreurs
_AGENT_MODULE_MAP = {
    "strategist": "Agents.strategist_agent",
    "coder": "Agents.coder_agent",
    "architect": "Agents.architect_agent",
    "factory": "Agents.factory_agent",
    "evolution": "Agents.evolution_agent",
    "infra": "Agents.infra_agent",
    "security": "Agents.security_agent",
    "writer": "Agents.writer_agent",
    "researcher": "Agents.researcher_agent",
    "formatter": "Agents.formatter_agent",
}


class ImpactAnalyzer:
    """Singleton — Analyseur de dépendances et santé des modules Prométhée."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def init(self):
        """Initialise l'analyseur et souscrit aux events bus."""
        self._cache = None
        self._cache_time = 0
        self._initialized = True
        try:
            from core.event_bus.bus import bus
            bus.subscribe("CI_PIPELINE_RESULT", self._on_invalidate)
            bus.subscribe("HALLUCINATION_DETECTED", self._on_invalidate)
            logger.info("[IMPACT] Analyseur de dépendances initialisé.")
        except Exception:
            pass  # Bus pas encore dispo, pas grave

    async def _on_invalidate(self, data: dict):
        """Invalide le cache quand un événement pertinent arrive."""
        self._cache = None
        self._cache_time = 0

    @classmethod
    def reset_singleton(cls):
        """Reset pour les tests."""
        cls._instance = None

    # --- DÉCOUVERTE DES MODULES ---

    def _discover_modules(self) -> dict:
        """Scanne les répertoires du projet et retourne les modules Python.

        Returns:
            dict: module_id → {path, type, name, display}
        """
        modules = {}

        for key, (rel_dir, mod_type) in _SCAN_DIRS.items():
            full_dir = os.path.join(_PROJECT_ROOT, rel_dir)
            if not os.path.isdir(full_dir):
                continue
            for fname in os.listdir(full_dir):
                if not fname.endswith(".py") or fname.startswith("__"):
                    continue
                name = fname[:-3]  # retirer .py
                # Construire le module_id comme un import Python
                if key == "core":
                    module_id = f"core.{name}"
                elif key == "agents":
                    module_id = f"Agents.{name}"
                elif key == "grimoire":
                    module_id = f"core.grimoire.{name}"
                elif key == "capabilities":
                    module_id = f"core.capabilities.{name}"
                else:
                    module_id = name

                modules[module_id] = {
                    "path": os.path.join(full_dir, fname),
                    "type": mod_type,
                    "name": name,
                    "display": name,
                }

        # Fichiers racine
        for fname in _ROOT_FILES:
            fpath = os.path.join(_PROJECT_ROOT, fname)
            if os.path.isfile(fpath):
                name = fname[:-3]
                modules[name] = {
                    "path": fpath,
                    "type": "root",
                    "name": name,
                    "display": name,
                }

        # event_bus est un package
        bus_init = os.path.join(_PROJECT_ROOT, "core", "event_bus", "bus.py")
        if os.path.isfile(bus_init):
            modules["core.event_bus.bus"] = {
                "path": bus_init,
                "type": "core",
                "name": "bus",
                "display": "event_bus",
            }

        return modules

    # --- EXTRACTION DES IMPORTS TOP-LEVEL ---

    def _extract_top_level_imports(self, filepath: str, known_modules: set) -> list:
        """Extrait les imports top-level d'un fichier qui matchent des modules du projet.

        Seuls les imports au niveau ast.Module.body (pas ast.walk) sont pris.

        Args:
            filepath: Chemin absolu du fichier Python.
            known_modules: Set des module_ids connus du projet.

        Returns:
            Liste de module_ids importés.
        """
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                source = f.read()
        except (OSError, IOError):
            return []

        try:
            tree = ast.parse(source, filename=filepath)
        except SyntaxError:
            return []

        imports = set()
        # Seulement les statements top-level
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._match_import(alias.name, known_modules, imports)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    self._match_import(node.module, known_modules, imports)

        return sorted(imports)

    def _match_import(self, import_name: str, known_modules: set, result: set):
        """Tente de matcher un import avec un module connu du projet.

        Essaie le nom complet, puis des préfixes de plus en plus courts.
        """
        # Match exact
        if import_name in known_modules:
            result.add(import_name)
            return

        # Match partiel : "core.event_bus.bus" → cherche "core.event_bus.bus"
        # ou "core.event_bus" → cherche si un module commence par ça
        parts = import_name.split(".")
        for i in range(len(parts), 0, -1):
            candidate = ".".join(parts[:i])
            if candidate in known_modules:
                result.add(candidate)
                return

    # --- GRAPHE DE DÉPENDANCES ---

    def _build_dependency_graph(self, modules: dict) -> tuple:
        """Construit le graphe de dépendances bidirectionnel.

        Args:
            modules: dict module_id → info

        Returns:
            (imports_of, imported_by) — deux dicts de listes
        """
        known = set(modules.keys())
        imports_of = {}   # module → [modules qu'il importe]
        imported_by = {}  # module → [modules qui l'importent]

        for mid in modules:
            imports_of[mid] = []
            imported_by[mid] = []

        for mid, info in modules.items():
            deps = self._extract_top_level_imports(info["path"], known)
            # Pas d'auto-import
            deps = [d for d in deps if d != mid]
            imports_of[mid] = deps
            for dep in deps:
                if dep in imported_by:
                    imported_by[dep].append(mid)

        return imports_of, imported_by

    # --- SANTÉ DES MODULES ---

    def _collect_health_data(self, modules: dict) -> dict:
        """Collecte l'état de santé de chaque module.

        Sources : routine_history (autonomy), reptilian stats, mtime fichiers.

        Returns:
            dict: module_id → {status, error_count, last_error, last_modified}
        """
        health = {}

        # Imports locaux pour éviter deps circulaires
        error_counts = {}
        hallucination_modules = set()
        try:
            from core.autonomy_engine import autonomy
            for entry in getattr(autonomy, "routine_history", []):
                if entry.get("status") == "error":
                    agent_name = entry.get("agent", "")
                    mid = _AGENT_MODULE_MAP.get(agent_name, "")
                    if mid and mid in modules:
                        error_counts[mid] = error_counts.get(mid, 0) + 1
        except Exception:
            pass

        try:
            from core.reptilian_core import reptile
            stats = reptile.get_stats()
            # hallucination storm → degrade evolution + coder
            if stats.get("threat_level", 0) >= 4:
                for mid in ("Agents.evolution_agent", "Agents.coder_agent"):
                    if mid in modules:
                        hallucination_modules.add(mid)
        except Exception:
            pass

        for mid, info in modules.items():
            err_count = error_counts.get(mid, 0)
            is_hallucinated = mid in hallucination_modules

            if is_hallucinated:
                err_count = max(err_count, _DEGRADED_THRESHOLD)

            if err_count >= _ERROR_THRESHOLD:
                status = "error"
            elif err_count >= _DEGRADED_THRESHOLD:
                status = "degraded"
            else:
                status = "healthy"

            # Date de modification
            try:
                mtime = os.path.getmtime(info["path"])
            except OSError:
                mtime = 0.0

            health[mid] = {
                "status": status,
                "error_count": err_count,
                "last_error": "",
                "last_modified": mtime,
            }

        return health

    # --- CONSTRUCTION DU GRAPHE COMPLET ---

    def build_graph(self) -> dict:
        """Point d'entrée principal. Retourne le graphe complet pour l'API.

        Résultat mis en cache pour _CACHE_TTL secondes.
        Invalidé par CI_PIPELINE_RESULT et HALLUCINATION_DETECTED.
        """
        now = time.time()
        if self._cache and (now - self._cache_time) < _CACHE_TTL:
            return self._cache

        modules = self._discover_modules()
        imports_of, imported_by = self._build_dependency_graph(modules)
        health = self._collect_health_data(modules)

        # Construire les nodes
        nodes = []
        for mid, info in modules.items():
            h = health.get(mid, {})
            nodes.append({
                "id": mid,
                "name": info["name"],
                "type": info["type"],
                "display": info["display"],
                "status": h.get("status", "healthy"),
                "error_count": h.get("error_count", 0),
                "last_error": h.get("last_error", ""),
                "last_modified": h.get("last_modified", 0.0),
                "import_count": len(imports_of.get(mid, [])),
                "imported_by_count": len(imported_by.get(mid, [])),
            })

        # Construire les links
        links = []
        for mid, deps in imports_of.items():
            for dep in deps:
                links.append({
                    "source": mid,
                    "target": dep,
                    "type": "imports",
                })

        # Stats
        statuses = [n["status"] for n in nodes]
        stats = {
            "total_modules": len(nodes),
            "healthy": statuses.count("healthy"),
            "degraded": statuses.count("degraded"),
            "error": statuses.count("error"),
            "computed_at": datetime.now().isoformat(timespec="seconds"),
        }

        self._cache = {"nodes": nodes, "links": links, "stats": stats}
        self._cache_time = now
        return self._cache

    def get_cascade(self, module_id: str) -> list:
        """Retourne tous les modules affectés si module_id a un problème.

        Parcours en largeur de imported_by (qui dépend de ce module).
        """
        modules = self._discover_modules()
        _, imported_by = self._build_dependency_graph(modules)

        visited = set()
        queue = [module_id]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            for dep in imported_by.get(current, []):
                if dep not in visited:
                    queue.append(dep)

        visited.discard(module_id)  # ne pas inclure le module source
        return sorted(visited)


# Singleton
analyzer = ImpactAnalyzer()
