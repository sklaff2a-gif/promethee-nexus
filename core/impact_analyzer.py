# core/impact_analyzer.py
"""
IMPACT GRAPH — Analyseur de dépendances et santé des modules.

Scan AST des imports top-level, construction du graphe de dépendances,
connexions event bus (publish/subscribe), collecte santé modules.
0 LLM, pur déterministe.
"""
import ast
import os
import re
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

# Regex pour détecter bus.publish / bus.subscribe dans le source
_RE_BUS_PUBLISH = re.compile(
    r'''bus\.publish\(\s*["']([A-Z_]+)["']''',
    re.MULTILINE,
)
_RE_BUS_SUBSCRIBE = re.compile(
    r'''bus\.subscribe\(\s*["']([A-Z_*]+)["']''',
    re.MULTILINE,
)


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
            bus.subscribe("ARTIFACT_CREATED", self._on_invalidate)
            bus.subscribe("SMART_RESTART_REQUESTED", self._on_invalidate)
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

    # --- EXTRACTION DES IMPORTS ---

    def _read_source(self, filepath: str) -> str:
        """Lit le source d'un fichier Python. Retourne '' si erreur."""
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except (OSError, IOError):
            return ""

    def _extract_top_level_imports(self, filepath: str, known_modules: set) -> list:
        """Extrait les imports top-level d'un fichier qui matchent des modules du projet."""
        source = self._read_source(filepath)
        if not source:
            return []

        try:
            tree = ast.parse(source, filename=filepath)
        except SyntaxError:
            return []

        imports = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._match_import(alias.name, known_modules, imports)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    self._match_import(node.module, known_modules, imports)

        return sorted(imports)

    def _count_local_imports(self, filepath: str, known_modules: set) -> int:
        """Compte les imports locaux (dans les fonctions) qui matchent le projet.

        Ne compte que les imports DANS des fonctions/méthodes (pas top-level).
        """
        source = self._read_source(filepath)
        if not source:
            return 0

        try:
            tree = ast.parse(source, filename=filepath)
        except SyntaxError:
            return 0

        # Collecter les imports top-level pour les exclure
        top_level_nodes = set()
        for node in tree.body:
            top_level_nodes.add(id(node))

        local_imports = set()
        for node in ast.walk(tree):
            if id(node) in top_level_nodes:
                continue
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._match_import(alias.name, known_modules, local_imports)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    self._match_import(node.module, known_modules, local_imports)

        return len(local_imports)

    def _match_import(self, import_name: str, known_modules: set, result: set):
        """Tente de matcher un import avec un module connu du projet."""
        if import_name in known_modules:
            result.add(import_name)
            return

        parts = import_name.split(".")
        for i in range(len(parts), 0, -1):
            candidate = ".".join(parts[:i])
            if candidate in known_modules:
                result.add(candidate)
                return

    # --- CONNEXIONS EVENT BUS ---

    def _extract_bus_connections(self, filepath: str) -> dict:
        """Scanne un fichier pour trouver les bus.publish() et bus.subscribe().

        Returns:
            {"publishes": ["EVENT_A", ...], "subscribes": ["EVENT_B", ...]}
        """
        source = self._read_source(filepath)
        if not source:
            return {"publishes": [], "subscribes": []}

        publishes = sorted(set(_RE_BUS_PUBLISH.findall(source)))
        subscribes = sorted(set(_RE_BUS_SUBSCRIBE.findall(source)))
        # Exclure le wildcard "*" des subscribes
        subscribes = [s for s in subscribes if s != "*"]

        return {"publishes": publishes, "subscribes": subscribes}

    def _build_bus_graph(self, modules: dict) -> tuple:
        """Construit le graphe de connexions event bus.

        Returns:
            (bus_links, bus_info) :
            - bus_links: list de {source, target, event, type:"bus"}
            - bus_info: dict module_id → {publishes: [...], subscribes: [...]}
        """
        bus_info = {}
        # event → list of module_ids qui le publient
        publishers = {}
        # event → list of module_ids qui y souscrivent
        subscribers = {}

        for mid, info in modules.items():
            conn = self._extract_bus_connections(info["path"])
            bus_info[mid] = conn

            for evt in conn["publishes"]:
                publishers.setdefault(evt, []).append(mid)
            for evt in conn["subscribes"]:
                subscribers.setdefault(evt, []).append(mid)

        # Créer les liens : publisher → subscriber pour chaque event
        bus_links = []
        seen = set()
        for evt in publishers:
            if evt not in subscribers:
                continue
            for pub_mid in publishers[evt]:
                for sub_mid in subscribers[evt]:
                    if pub_mid == sub_mid:
                        continue  # pas d'auto-lien
                    key = (pub_mid, sub_mid, evt)
                    if key not in seen:
                        seen.add(key)
                        bus_links.append({
                            "source": pub_mid,
                            "target": sub_mid,
                            "event": evt,
                            "type": "bus",
                        })

        return bus_links, bus_info

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
        Invalidé par CI_PIPELINE_RESULT, HALLUCINATION_DETECTED,
        ARTIFACT_CREATED, SMART_RESTART_REQUESTED.
        """
        now = time.time()
        if self._cache and (now - self._cache_time) < _CACHE_TTL:
            return self._cache

        modules = self._discover_modules()
        known = set(modules.keys())
        imports_of, imported_by = self._build_dependency_graph(modules)
        bus_links, bus_info = self._build_bus_graph(modules)
        health = self._collect_health_data(modules)

        # Construire les nodes
        nodes = []
        for mid, info in modules.items():
            h = health.get(mid, {})
            bi = bus_info.get(mid, {})
            local_count = self._count_local_imports(info["path"], known)
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
                "local_import_count": local_count,
                "publishes": bi.get("publishes", []),
                "subscribes": bi.get("subscribes", []),
            })

        # Construire les links (imports structurels)
        links = []
        for mid, deps in imports_of.items():
            for dep in deps:
                links.append({
                    "source": mid,
                    "target": dep,
                    "type": "imports",
                })

        # Ajouter les liens bus (dédupliqués par paire source→target)
        bus_pairs_seen = set()
        for bl in bus_links:
            pair = (bl["source"], bl["target"])
            if pair not in bus_pairs_seen:
                bus_pairs_seen.add(pair)
                links.append({
                    "source": bl["source"],
                    "target": bl["target"],
                    "type": "bus",
                    "event": bl["event"],
                })

        # Stats
        statuses = [n["status"] for n in nodes]
        import_links = [l for l in links if l["type"] == "imports"]
        bus_link_list = [l for l in links if l["type"] == "bus"]
        stats = {
            "total_modules": len(nodes),
            "healthy": statuses.count("healthy"),
            "degraded": statuses.count("degraded"),
            "error": statuses.count("error"),
            "import_links": len(import_links),
            "bus_links": len(bus_link_list),
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
