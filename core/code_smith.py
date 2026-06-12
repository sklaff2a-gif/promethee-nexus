# core/code_smith.py — Transformations déterministes de code Python
# Remplace le LLM pour les modifications du catalogue Evolution.
# Principe : AST pour localiser, texte brut pour modifier → préserve commentaires et formatage.

import ast
import textwrap
import logging
from dataclasses import dataclass
from typing import Optional, List, Tuple

logger = logging.getLogger("CodeSmith")


class TransformType:
    ADD_IMPORT = "add_import"
    ADD_CLASS_ATTRIBUTE = "add_class_attribute"
    ADD_METHOD = "add_method"
    ADD_MODULE_FUNCTION = "add_module_function"
    ADD_MODULE_CONSTANT = "add_module_constant"
    WRAP_TRY_EXCEPT = "wrap_try_except"
    INSERT_EARLY_RETURN = "insert_early_return"
    INSERT_BEFORE_RETURN = "insert_before_return"
    REPLACE_METHOD_BODY = "replace_method_body"


@dataclass
class TransformAction:
    """Action atomique de transformation."""
    type: str                        # TransformType
    target_class: str = ""           # Classe cible (vide = module-level)
    target_method: str = ""          # Méthode cible
    code: str = ""                   # Code à insérer (indentation relative)
    import_line: str = ""            # Pour ADD_IMPORT
    except_body: str = ""            # Pour WRAP_TRY_EXCEPT


@dataclass
class TransformResult:
    success: bool
    modified_source: str = ""
    error: str = ""
    actions_applied: int = 0


class CodeSmith:
    """Transformateur déterministe pour le catalogue Evolution."""

    @staticmethod
    def apply(source: str, actions: List[TransformAction]) -> TransformResult:
        """Applique une séquence de TransformAction au code source."""
        lines = source.split("\n")
        applied = 0

        for action in actions:
            try:
                current_source = "\n".join(lines)
                if action.type == TransformType.ADD_IMPORT:
                    lines = CodeSmith._add_import(lines, current_source, action.import_line)
                elif action.type == TransformType.ADD_CLASS_ATTRIBUTE:
                    lines = CodeSmith._add_class_attribute(lines, current_source, action.target_class, action.code)
                elif action.type == TransformType.ADD_METHOD:
                    lines = CodeSmith._add_method(lines, current_source, action.target_class, action.code)
                elif action.type == TransformType.ADD_MODULE_FUNCTION:
                    lines = CodeSmith._add_module_function(lines, current_source, action.code)
                elif action.type == TransformType.ADD_MODULE_CONSTANT:
                    lines = CodeSmith._add_module_constant(lines, current_source, action.code)
                elif action.type == TransformType.WRAP_TRY_EXCEPT:
                    lines = CodeSmith._wrap_try_except(lines, current_source, action.target_class, action.target_method, action.except_body)
                elif action.type == TransformType.INSERT_EARLY_RETURN:
                    lines = CodeSmith._insert_early_return(lines, current_source, action.target_class, action.target_method, action.code)
                elif action.type == TransformType.INSERT_BEFORE_RETURN:
                    lines = CodeSmith._insert_before_return(lines, current_source, action.target_class, action.target_method, action.code)
                elif action.type == TransformType.REPLACE_METHOD_BODY:
                    lines = CodeSmith._replace_method_body(lines, current_source, action.target_class, action.target_method, action.code)
                else:
                    return TransformResult(success=False, error=f"Type inconnu: {action.type}")
                applied += 1
            except Exception as e:
                return TransformResult(
                    success=False,
                    error=f"Action {applied+1} ({action.type}) échouée: {e}",
                    actions_applied=applied,
                )

        modified = "\n".join(lines)
        # Vérification syntaxe finale
        try:
            ast.parse(modified)
        except SyntaxError as e:
            return TransformResult(
                success=False,
                modified_source=modified,
                error=f"Syntaxe invalide après transformation: {e}",
                actions_applied=applied,
            )

        return TransformResult(success=True, modified_source=modified, actions_applied=applied)

    # --- Localisateurs AST ---

    @staticmethod
    def _find_class(source: str, class_name: str) -> Optional[ast.ClassDef]:
        """Trouve un ClassDef par nom."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                return node
        return None

    @staticmethod
    def _find_method(source: str, class_name: str, method_name: str) -> Optional[Tuple[int, int, int]]:
        """(start_line, end_line, indent) — 1-based."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == method_name:
                        return (item.lineno, item.end_lineno, item.col_offset)
        return None

    @staticmethod
    def _method_body_range(source: str, class_name: str, method_name: str) -> Optional[Tuple[int, int, int]]:
        """(body_start_line, method_end_line, body_indent) — 1-based.
        body_start_line est la première ligne après docstring."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == method_name:
                        if not item.body:
                            return None
                        body_indent = item.col_offset + 4
                        # Détecter docstring
                        first = item.body[0]
                        if (isinstance(first, ast.Expr) and isinstance(first.value, (ast.Constant, ast.Str))):
                            # Body commence après la docstring
                            if len(item.body) > 1:
                                body_start = item.body[1].lineno
                            else:
                                # Méthode avec seulement une docstring
                                body_start = first.end_lineno + 1
                        else:
                            body_start = first.lineno
                        return (body_start, item.end_lineno, body_indent)
        return None

    @staticmethod
    def _find_class_body_start(source: str, class_name: str) -> Optional[Tuple[int, int]]:
        """(first_body_line, class_indent) — 1-based. Après la docstring de classe si présente."""
        cls = CodeSmith._find_class(source, class_name)
        if not cls or not cls.body:
            return None
        first = cls.body[0]
        # Détecter docstring de classe
        if isinstance(first, ast.Expr) and isinstance(first.value, (ast.Constant, ast.Str)):
            if len(cls.body) > 1:
                return (cls.body[1].lineno, cls.col_offset + 4)
            else:
                return (first.end_lineno + 1, cls.col_offset + 4)
        return (first.lineno, cls.col_offset + 4)

    @staticmethod
    def _find_class_end(source: str, class_name: str) -> Optional[int]:
        """Dernière ligne de la classe. 1-based."""
        cls = CodeSmith._find_class(source, class_name)
        if cls:
            return cls.end_lineno
        return None

    @staticmethod
    def _find_imports_end(source: str) -> int:
        """Dernière ligne du bloc d'imports (y compris commentaires inter-imports). 1-based."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return 1
        last_import_line = 0
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                last_import_line = max(last_import_line, node.end_lineno)
        return max(last_import_line, 1)

    @staticmethod
    def _find_last_return(lines: List[str], start: int, end: int) -> Optional[int]:
        """Trouve la dernière ligne contenant 'return' dans la plage [start, end] (0-based)."""
        for i in range(end - 1, start - 1, -1):
            stripped = lines[i].strip()
            if stripped.startswith("return ") or stripped == "return":
                return i
        return None

    # --- Transformateurs ---

    @staticmethod
    def _add_import(lines: List[str], source: str, import_line: str) -> List[str]:
        """Ajoute un import s'il n'existe pas déjà."""
        import_line = import_line.strip()
        if CodeSmith._has_import(source, import_line):
            return lines  # Idempotent
        insert_at = CodeSmith._find_imports_end(source)
        lines.insert(insert_at, import_line)
        return lines

    @staticmethod
    def _add_class_attribute(lines: List[str], source: str, class_name: str, code: str) -> List[str]:
        """Ajoute un attribut de classe après la docstring."""
        result = CodeSmith._find_class_body_start(source, class_name)
        if result is None:
            raise ValueError(f"Classe '{class_name}' non trouvée")
        body_start, class_indent = result
        # Vérifier si déjà présent (première ligne du code)
        first_line = code.strip().split("\n")[0].strip()
        attr_name = first_line.split("=")[0].split(":")[0].strip() if ("=" in first_line or ":" in first_line) else ""
        if attr_name:
            for line in lines:
                stripped = line.strip()
                if stripped.startswith(attr_name) and ("=" in stripped or ":" in stripped):
                    return lines  # Idempotent
        indented = CodeSmith._indent_code(code, class_indent)
        insert_lines = indented.split("\n")
        # Insérer avant le premier élément du body
        idx = body_start - 1  # 0-based
        for i, new_line in enumerate(insert_lines):
            lines.insert(idx + i, new_line)
        # Ajouter une ligne vide après si nécessaire
        after_idx = idx + len(insert_lines)
        if after_idx < len(lines) and lines[after_idx].strip():
            lines.insert(after_idx, "")
        return lines

    @staticmethod
    def _add_method(lines: List[str], source: str, class_name: str, method_code: str) -> List[str]:
        """Ajoute une méthode à la fin d'une classe."""
        # Extraire le nom de la méthode depuis le code
        method_name = CodeSmith._extract_method_name(method_code)
        if method_name and CodeSmith._has_method(source, class_name, method_name):
            return lines  # Idempotent

        cls = CodeSmith._find_class(source, class_name)
        if cls is None:
            raise ValueError(f"Classe '{class_name}' non trouvée")

        class_indent = cls.col_offset + 4
        indented = CodeSmith._indent_code(method_code, class_indent)

        # Insérer à la fin de la classe
        insert_at = cls.end_lineno  # 0-based = end_lineno (car on insère après)
        insert_lines = [""] + indented.split("\n")
        for i, new_line in enumerate(insert_lines):
            lines.insert(insert_at + i, new_line)
        return lines

    @staticmethod
    def _add_module_function(lines: List[str], source: str, code: str) -> List[str]:
        """Ajoute une fonction au niveau module (après les imports)."""
        func_name = CodeSmith._extract_method_name(code)
        if func_name and CodeSmith._has_module_function(source, func_name):
            return lines  # Idempotent
        insert_at = CodeSmith._find_imports_end(source)
        indented = CodeSmith._indent_code(code, 0)
        insert_lines = ["", ""] + indented.split("\n")
        for i, new_line in enumerate(insert_lines):
            lines.insert(insert_at + i, new_line)
        return lines

    @staticmethod
    def _add_module_constant(lines: List[str], source: str, code: str) -> List[str]:
        """Ajoute une constante après les imports."""
        # Vérifier idempotence sur la première ligne
        first_line = code.strip().split("\n")[0].strip()
        const_name = first_line.split("=")[0].strip() if "=" in first_line else ""
        if const_name:
            for line in lines:
                if line.strip().startswith(const_name) and "=" in line:
                    return lines  # Idempotent
        insert_at = CodeSmith._find_imports_end(source)
        insert_lines = [""] + code.strip().split("\n")
        for i, new_line in enumerate(insert_lines):
            lines.insert(insert_at + i, new_line)
        return lines

    @staticmethod
    def _wrap_try_except(lines: List[str], source: str, class_name: str, method_name: str, except_body: str) -> List[str]:
        """Entoure le body d'une méthode avec try/except."""
        result = CodeSmith._method_body_range(source, class_name, method_name)
        if result is None:
            raise ValueError(f"Méthode '{class_name}.{method_name}' non trouvée")
        body_start, method_end, body_indent = result
        indent_str = " " * body_indent

        # Vérifier si déjà wrappé (première instruction du body est un try:)
        for i in range(body_start - 1, method_end):
            if i < len(lines) and lines[i].strip():
                if lines[i].strip().startswith("try:"):
                    return lines  # Déjà wrappé, idempotent
                break

        # 1. Insérer try: au début du body
        lines.insert(body_start - 1, f"{indent_str}try:")

        # 2. Re-indenter le body existant de +4 espaces
        # body_start est maintenant décalé de 1 (on a inséré une ligne)
        for i in range(body_start, method_end + 1):
            if i < len(lines) and lines[i].strip():
                lines[i] = "    " + lines[i]

        # 3. Ajouter except + handler
        except_lines = [
            f"{indent_str}except Exception as e:",
        ]
        for eline in except_body.strip().split("\n"):
            except_lines.append(f"{indent_str}    {eline.strip()}")

        insert_at = method_end + 1  # +1 pour la ligne try: ajoutée
        for i, eline in enumerate(except_lines):
            lines.insert(insert_at + i, eline)
        return lines

    @staticmethod
    def _insert_early_return(lines: List[str], source: str, class_name: str, method_name: str, guard_code: str) -> List[str]:
        """Insère un guard clause au début du body (après docstring)."""
        result = CodeSmith._method_body_range(source, class_name, method_name)
        if result is None:
            raise ValueError(f"Méthode '{class_name}.{method_name}' non trouvée")
        body_start, _, body_indent = result

        # Vérifier idempotence : première ligne du guard déjà présente ?
        first_guard = guard_code.strip().split("\n")[0].strip()
        if body_start - 1 < len(lines):
            existing = lines[body_start - 1].strip()
            if existing == first_guard:
                return lines  # Idempotent

        indented = CodeSmith._indent_code(guard_code, body_indent)
        insert_lines = indented.split("\n")
        idx = body_start - 1  # 0-based
        for i, new_line in enumerate(insert_lines):
            lines.insert(idx + i, new_line)
        return lines

    @staticmethod
    def _insert_before_return(lines: List[str], source: str, class_name: str, method_name: str, code: str) -> List[str]:
        """Insère du code juste avant le dernier return de la méthode."""
        result = CodeSmith._method_body_range(source, class_name, method_name)
        if result is None:
            raise ValueError(f"Méthode '{class_name}.{method_name}' non trouvée")
        body_start, method_end, body_indent = result

        return_idx = CodeSmith._find_last_return(lines, body_start - 1, method_end)
        if return_idx is None:
            # Pas de return → insérer à la fin de la méthode
            return_idx = method_end  # 0-based (après la dernière ligne)

        # Vérifier idempotence
        first_code_line = code.strip().split("\n")[0].strip()
        if return_idx > 0:
            prev = lines[return_idx - 1].strip()
            if prev == first_code_line:
                return lines

        indented = CodeSmith._indent_code(code, body_indent)
        insert_lines = indented.split("\n")
        for i, new_line in enumerate(insert_lines):
            lines.insert(return_idx + i, new_line)
        return lines

    @staticmethod
    def _replace_method_body(lines: List[str], source: str, class_name: str, method_name: str, new_body: str) -> List[str]:
        """Remplace le body d'une méthode (préserve def + docstring)."""
        result = CodeSmith._method_body_range(source, class_name, method_name)
        if result is None:
            raise ValueError(f"Méthode '{class_name}.{method_name}' non trouvée")
        body_start, method_end, body_indent = result

        # Supprimer l'ancien body
        del lines[body_start - 1:method_end]

        # Insérer le nouveau body
        indented = CodeSmith._indent_code(new_body, body_indent)
        insert_lines = indented.split("\n")
        for i, new_line in enumerate(insert_lines):
            lines.insert(body_start - 1 + i, new_line)
        return lines

    # --- Utilitaires ---

    @staticmethod
    def _indent_code(code: str, indent_level: int) -> str:
        """Dédent puis ré-indent au niveau cible."""
        dedented = textwrap.dedent(code).strip()
        indent_str = " " * indent_level
        indented_lines = []
        for line in dedented.split("\n"):
            if line.strip():
                indented_lines.append(indent_str + line)
            else:
                indented_lines.append("")
        return "\n".join(indented_lines)

    @staticmethod
    def _has_method(source: str, class_name: str, method_name: str) -> bool:
        """Idempotence : vérifie si la méthode existe déjà."""
        return CodeSmith._find_method(source, class_name, method_name) is not None

    @staticmethod
    def _has_import(source: str, import_line: str) -> bool:
        """Idempotence : vérifie si l'import existe déjà."""
        import_line = import_line.strip()
        for line in source.split("\n"):
            if line.strip() == import_line:
                return True
        # Vérifier sémantiquement (import X vs from X import Y)
        try:
            tree = ast.parse(source)
            # Extraire le module de l'import_line
            imp_tree = ast.parse(import_line)
            for imp_node in ast.walk(imp_tree):
                if isinstance(imp_node, ast.Import):
                    for alias in imp_node.names:
                        for node in ast.walk(tree):
                            if isinstance(node, ast.Import):
                                for a in node.names:
                                    if a.name == alias.name:
                                        return True
                elif isinstance(imp_node, ast.ImportFrom):
                    for node in ast.walk(tree):
                        if isinstance(node, ast.ImportFrom) and node.module == imp_node.module:
                            return True
        except SyntaxError:
            pass
        return False

    @staticmethod
    def _has_module_function(source: str, func_name: str) -> bool:
        """Vérifie si une fonction module-level existe."""
        try:
            tree = ast.parse(source)
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                    return True
        except SyntaxError:
            pass
        return False

    @staticmethod
    def _extract_method_name(code: str) -> Optional[str]:
        """Extrait le nom d'une def/async def depuis du code."""
        try:
            tree = ast.parse(textwrap.dedent(code))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return node.name
        except SyntaxError:
            pass
        return None


# ---------------------------------------------------------------------------
# Registre des spec handlers
# ---------------------------------------------------------------------------

_SPEC_HANDLERS = {}


def _register(spec_id):
    def decorator(func):
        _SPEC_HANDLERS[spec_id] = func
        return func
    return decorator


# --- PERFORMANCE ---

@_register("PERF-001")
def _perf_001(spec, source):
    """Cache LRU Router — attributs + 2 méthodes."""
    return [
        TransformAction(
            type=TransformType.ADD_IMPORT,
            import_line="import time",
        ),
        TransformAction(
            type=TransformType.ADD_CLASS_ATTRIBUTE,
            target_class="RouterAgent",
            code='_intent_cache: dict = {}\n_CACHE_TTL = 300  # 5 minutes',
        ),
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="RouterAgent",
            code=textwrap.dedent('''\
                @classmethod
                def _get_cached_intent(cls, mission: str):
                    key = mission.strip().lower()[:200]
                    entry = cls._intent_cache.get(key)
                    if entry and (time.time() - entry["ts"]) < cls._CACHE_TTL:
                        return entry["result"]
                    return None
            '''),
        ),
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="RouterAgent",
            code=textwrap.dedent('''\
                @classmethod
                def _set_cached_intent(cls, mission: str, result: str):
                    key = mission.strip().lower()[:200]
                    cls._intent_cache[key] = {"result": result, "ts": time.time()}
                    if len(cls._intent_cache) > 500:
                        oldest = sorted(cls._intent_cache, key=lambda k: cls._intent_cache[k]["ts"])
                        for k in oldest[:100]:
                            del cls._intent_cache[k]
            '''),
        ),
    ]


@_register("PERF-003")
def _perf_003(spec, source):
    """Short-circuit complexité."""
    return [
        TransformAction(
            type=TransformType.INSERT_EARLY_RETURN,
            target_class="BaseAgent",
            target_method="_evaluate_complexity",
            code=textwrap.dedent('''\
                if len(prompt) < 100:
                    return False
                internal_markers = ("EVOLUTION_PIPELINE", "PROTOCOLE_AUTONOMIE", "DROPZONE_ANALYSIS")
                for marker in internal_markers:
                    if marker in prompt:
                        return False
            '''),
        ),
    ]


@_register("PERF-004")
def _perf_004(spec, source):
    """Cache recall RAG — méthodes helper + guard en début de recall + cache après résultat."""
    return [
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="BaseAgent",
            code=textwrap.dedent('''\
                def _get_recall_cache(self, cache_key: str):
                    """Retourne le résultat caché ou None."""
                    if not hasattr(self, '_recall_cache'):
                        self._recall_cache = {}
                    cached = self._recall_cache.get(cache_key)
                    if cached and (time.time() - cached["ts"]) < 300:
                        return cached["result"]
                    return None
            '''),
        ),
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="BaseAgent",
            code=textwrap.dedent('''\
                def _set_recall_cache(self, cache_key: str, result: str):
                    """Met en cache un résultat recall."""
                    if not hasattr(self, '_recall_cache'):
                        self._recall_cache = {}
                    self._recall_cache[cache_key] = {"result": result, "ts": time.time()}
                    if len(self._recall_cache) > 20:
                        oldest_key = min(self._recall_cache, key=lambda k: self._recall_cache[k]["ts"])
                        del self._recall_cache[oldest_key]
            '''),
        ),
        TransformAction(
            type=TransformType.INSERT_EARLY_RETURN,
            target_class="BaseAgent",
            target_method="recall",
            code=textwrap.dedent('''\
                _cache_key = query.strip().lower()[:200]
                _cached = self._get_recall_cache(_cache_key)
                if _cached is not None:
                    return _cached
            '''),
        ),
    ]


@_register("PERF-008")
def _perf_008(spec, source):
    """Cache listing projet Strategist."""
    return [
        TransformAction(
            type=TransformType.ADD_IMPORT,
            import_line="import time",
        ),
        TransformAction(
            type=TransformType.ADD_CLASS_ATTRIBUTE,
            target_class="DivineStrategist",
            code='_project_files_cache: str = ""\n_project_files_ts: float = 0.0\n_PROJECT_FILES_TTL = 60',
        ),
    ]


@_register("PERF-010")
def _perf_010(spec, source):
    """Skip binaires Dropzone."""
    return [
        TransformAction(
            type=TransformType.ADD_MODULE_FUNCTION,
            code=textwrap.dedent('''\
                def _is_binary(filepath: str) -> bool:
                    """Détecte les fichiers binaires (octets nuls dans les 512 premiers bytes)."""
                    try:
                        with open(filepath, 'rb') as f:
                            chunk = f.read(512)
                            return b'\\x00' in chunk
                    except Exception:
                        return True
            '''),
        ),
    ]


# --- RESILIENCE ---

@_register("RES-003")
def _res_003(spec, source):
    """Dégradation gracieuse mémoire — wrap remember() avec try/except."""
    return [
        TransformAction(
            type=TransformType.WRAP_TRY_EXCEPT,
            target_class="BaseAgent",
            target_method="remember",
            except_body='logger.warning(f"[{self.name}] remember() failed (graceful degradation): {e}")',
        ),
    ]


@_register("RES-005")
def _res_005(spec, source):
    """Heartbeat Ollama — constante + méthode async pour publier OLLAMA_UNRESPONSIVE."""
    return [
        TransformAction(
            type=TransformType.ADD_MODULE_CONSTANT,
            code='_OLLAMA_HEALTH_URL = "http://localhost:11434/api/tags"',
        ),
        TransformAction(
            type=TransformType.ADD_MODULE_FUNCTION,
            code=textwrap.dedent('''\
                async def _check_ollama_heartbeat():
                    """Vérifie si Ollama répond. Publie OLLAMA_UNRESPONSIVE si non."""
                    import httpx
                    try:
                        async with httpx.AsyncClient(timeout=5.0) as client:
                            resp = await client.get(_OLLAMA_HEALTH_URL)
                            return resp.status_code == 200
                    except Exception:
                        try:
                            from core.event_bus.bus import bus
                            from datetime import datetime
                            await bus.publish("OLLAMA_UNRESPONSIVE", {
                                "timestamp": datetime.now().isoformat(),
                                "message": "Ollama ne répond pas au health check"
                            })
                        except Exception:
                            pass
                        return False
            '''),
        ),
    ]


@_register("RES-008")
def _res_008(spec, source):
    """Auto-trim journal stratégique — constante + méthode _auto_trim."""
    return [
        TransformAction(
            type=TransformType.ADD_MODULE_CONSTANT,
            code='_MAX_JOURNAL_ENTRIES = 100',
        ),
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="StrategicJournal",
            code=textwrap.dedent('''\
                def _auto_trim(self):
                    """Supprime les entrées anciennes si > _MAX_JOURNAL_ENTRIES."""
                    if len(self._entries) > _MAX_JOURNAL_ENTRIES:
                        overflow = len(self._entries) - _MAX_JOURNAL_ENTRIES
                        self._entries = self._entries[overflow:]
                        logger.info(f"[JOURNAL] auto-trim: {overflow} entrées anciennes supprimées")
                        self._save()
            '''),
        ),
    ]


# --- INTELLIGENCE ---

@_register("INT-001")
def _int_001(spec, source):
    """Trimming contexte RAG."""
    return [
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="BaseAgent",
            code=textwrap.dedent('''\
                def _trim_context(self, context: str, max_len: int = 4000) -> str:
                    """Tronque le contexte RAG si trop long."""
                    if len(context) <= max_len:
                        return context
                    head = context[:500]
                    tail = context[-500:]
                    return f"{head}\\n[... {len(context) - 1000} chars tronqués ...]\\n{tail}"
            '''),
        ),
    ]


@_register("INT-005")
def _int_005(spec, source):
    """Résumé échecs CI/CD."""
    return [
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="DivineEvolution",
            code=textwrap.dedent('''\
                def _get_recent_failures(self) -> str:
                    """Consulte les derniers échecs CI/CD pour enrichir le prompt."""
                    try:
                        from core.ci_pipeline import ci_memory
                        failures = ci_memory.get_recent_failures(limit=5)
                        if failures:
                            summary = "\\n".join(f"- {f['file']}: {f['reason']}" for f in failures)
                            return f"\\nÉVITE ces erreurs passées :\\n{summary}\\n"
                    except Exception:
                        pass
                    return ""
            '''),
        ),
    ]


@_register("INT-007")
def _int_007(spec, source):
    """Métriques lisibilité Writer."""
    return [
        TransformAction(
            type=TransformType.ADD_IMPORT,
            import_line="import re",
        ),
        TransformAction(
            type=TransformType.ADD_MODULE_FUNCTION,
            code=textwrap.dedent('''\
                def _compute_readability(text: str) -> dict:
                    """Métriques de lisibilité basiques."""
                    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
                    words = text.split()
                    paragraphs = [p.strip() for p in text.split('\\n\\n') if p.strip()]
                    avg_sentence_len = len(words) / max(len(sentences), 1)
                    return {
                        "word_count": len(words),
                        "sentence_count": len(sentences),
                        "paragraph_count": len(paragraphs),
                        "avg_sentence_length": round(avg_sentence_len, 1),
                    }
            '''),
        ),
    ]


# --- OBSERVABILITY ---

@_register("OBS-001")
def _obs_001(spec, source):
    """Métriques par agent dans SelfAwareness."""
    return [
        TransformAction(
            type=TransformType.ADD_CLASS_ATTRIBUTE,
            target_class="SelfAwarenessEngine",
            code='_per_agent_metrics: dict = {}',
        ),
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="SelfAwarenessEngine",
            code=textwrap.dedent('''\
                async def _on_agent_response(self, event: dict):
                    """Tracking calls/successes/latency par agent."""
                    self._mission_count += 1
                    agent = event.get("agent", "unknown")
                    metrics = self._per_agent_metrics.setdefault(
                        agent, {"calls": 0, "successes": 0, "total_latency": 0.0}
                    )
                    metrics["calls"] += 1
                    if event.get("status") == "success":
                        self._mission_success += 1
                        metrics["successes"] += 1
                    metrics["total_latency"] += event.get("duration", 0.0)
            '''),
        ),
    ]


@_register("OBS-002")
def _obs_002(spec, source):
    """Compteur Cloud par agent."""
    return [
        TransformAction(
            type=TransformType.ADD_CLASS_ATTRIBUTE,
            target_class="BaseAgent",
            code='_cloud_calls_by_agent: dict = {}',
        ),
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="BaseAgent",
            code=textwrap.dedent('''\
                @classmethod
                def get_cloud_usage(cls) -> dict:
                    """Retourne le compteur d'appels Cloud par agent."""
                    return dict(cls._cloud_calls_by_agent)
            '''),
        ),
    ]


@_register("OBS-004")
def _obs_004(spec, source):
    """Dashboard santé enrichi — injection métriques per-agent dans get_self_context."""
    return [
        TransformAction(
            type=TransformType.INSERT_BEFORE_RETURN,
            target_class="SelfAwarenessEngine",
            target_method="get_self_context",
            code=textwrap.dedent('''\
                per_agent = snap.get("per_agent_metrics", {})
                if per_agent:
                    top3 = sorted(per_agent.items(), key=lambda x: x[1].get("calls", 0), reverse=True)[:3]
                    agent_info = ", ".join(f"{a}({m['calls']}appels)" for a, m in top3)
                    parts.append(f"Top agents: {agent_info}.")
            '''),
        ),
    ]


@_register("OBS-003")
def _obs_003(spec, source):
    """Profiling pipelines — import + méthode de publication timing."""
    return [
        TransformAction(
            type=TransformType.ADD_IMPORT,
            import_line="import time",
        ),
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="Orchestrator",
            code=textwrap.dedent('''\
                async def _publish_pipeline_timing(self, agent_name: str, start_time: float):
                    """Publie un événement PIPELINE_TIMING avec la durée du dispatch."""
                    duration_ms = (time.time() - start_time) * 1000
                    try:
                        from core.event_bus.bus import bus
                        from datetime import datetime
                        await bus.publish("PIPELINE_TIMING", {
                            "agent": agent_name,
                            "duration_ms": round(duration_ms, 1),
                            "timestamp": datetime.now().isoformat(),
                        })
                    except Exception:
                        pass
                    logger.info(f"Pipeline {agent_name}: {duration_ms:.0f}ms")
            '''),
        ),
    ]


# --- SECURITY ---

@_register("SEC-001")
def _sec_001(spec, source):
    """Sanitization output Factory."""
    return [
        TransformAction(
            type=TransformType.ADD_IMPORT,
            import_line="import re",
        ),
        TransformAction(
            type=TransformType.ADD_MODULE_CONSTANT,
            code=textwrap.dedent('''\
                _DANGEROUS_PATTERNS = [
                    r'\\bos\\.system\\s*\\(',
                    r'\\bsubprocess\\.',
                    r'\\beval\\s*\\(',
                    r'\\bexec\\s*\\(',
                    r'\\b__import__\\s*\\(',
                ]
            '''),
        ),
        TransformAction(
            type=TransformType.ADD_MODULE_FUNCTION,
            code=textwrap.dedent('''\
                def _scan_code_safety(code: str) -> list:
                    """Retourne la liste des patterns dangereux détectés."""
                    findings = []
                    for pattern in _DANGEROUS_PATTERNS:
                        if re.search(pattern, code):
                            findings.append(pattern)
                    return findings
            '''),
        ),
    ]


@_register("SEC-002")
def _sec_002(spec, source):
    """Détection injection prompt."""
    return [
        TransformAction(
            type=TransformType.ADD_MODULE_CONSTANT,
            code=textwrap.dedent('''\
                _INJECTION_PATTERNS = [
                    "ignore previous", "ignore all", "system prompt",
                    "jailbreak", "bypass", "override instructions",
                    "forget your", "new instructions",
                ]
            '''),
        ),
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="RouterAgent",
            code=textwrap.dedent('''\
                @classmethod
                def _check_injection(cls, mission: str) -> bool:
                    """Détecte les tentatives d'injection de prompt."""
                    mission_lower = mission.lower()
                    for pattern in _INJECTION_PATTERNS:
                        if pattern in mission_lower:
                            logger.warning(f"Router: possible injection prompt détectée: '{pattern}'")
                            return True
                    return False
            '''),
        ),
    ]


@_register("SEC-005")
def _sec_005(spec, source):
    """Whitelist extensions Dropzone."""
    return [
        TransformAction(
            type=TransformType.ADD_MODULE_CONSTANT,
            code='_ALLOWED_EXTENSIONS = {".py", ".txt", ".md", ".json", ".csv", ".log", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".html", ".js"}',
        ),
        TransformAction(
            type=TransformType.ADD_MODULE_FUNCTION,
            code=textwrap.dedent('''\
                def _is_allowed_file(filepath: str) -> bool:
                    """Vérifie que l'extension est dans la whitelist."""
                    ext = os.path.splitext(filepath)[1].lower()
                    return ext in _ALLOWED_EXTENSIONS
            '''),
        ),
    ]


@_register("MEM-005")
def _mem_005(spec, source):
    """Mémoire structurée Researcher."""
    return [
        TransformAction(
            type=TransformType.ADD_MODULE_FUNCTION,
            code=textwrap.dedent('''\
                def _format_research_memory(topic: str, source: str, findings: str) -> str:
                    """Formate une découverte pour une meilleure recherche future."""
                    first_sentence = findings.split('.')[0].strip() if '.' in findings else findings[:200]
                    return f"TOPIC: {topic[:100]} | SOURCE: {source} | INSIGHT: {first_sentence}"
            '''),
        ),
    ]


@_register("MEM-006")
def _mem_006(spec, source):
    """Snapshot traits Psyche — constante + méthode snapshot + insertion dans save()."""
    return [
        TransformAction(
            type=TransformType.ADD_MODULE_CONSTANT,
            code='_MAX_TRAIT_HISTORY = 10',
        ),
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="PsycheEngine",
            code=textwrap.dedent('''\
                def _take_trait_snapshot(self):
                    """Prend un snapshot des traits dominants pour le suivi historique."""
                    snapshot = {}
                    for agent_name, traits in self.agents.items():
                        if traits:
                            dominant = max(traits.items(), key=lambda x: x[1])
                            snapshot[agent_name] = {"trait": dominant[0], "value": round(dominant[1], 1)}
                    if not hasattr(self, '_trait_history'):
                        self._trait_history = []
                    self._trait_history.append({
                        "ts": datetime.now().isoformat(),
                        "dominant_traits": snapshot,
                    })
                    self._trait_history = self._trait_history[-_MAX_TRAIT_HISTORY:]
            '''),
        ),
        TransformAction(
            type=TransformType.INSERT_BEFORE_RETURN,
            target_class="PsycheEngine",
            target_method="save",
            code='self._take_trait_snapshot()',
        ),
    ]


# ===========================================================================
# Phase B — 15 handlers supplémentaires (difficulté 1–2)
# ===========================================================================

# --- PERFORMANCE (Phase B) ---

@_register("PERF-002")
def _perf_002(spec, source):
    """Timeout Ollama — constante + méthode wrapper avec asyncio.wait_for."""
    return [
        TransformAction(
            type=TransformType.ADD_IMPORT,
            import_line="import asyncio",
        ),
        TransformAction(
            type=TransformType.ADD_MODULE_CONSTANT,
            code='_OLLAMA_TIMEOUT = 60.0',
        ),
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="BaseAgent",
            code=textwrap.dedent('''\
                async def _call_ollama_with_timeout(self, prompt: str, model: str = ""):
                    """Appelle Ollama avec un timeout asyncio."""
                    try:
                        result = await asyncio.wait_for(
                            self._raw_ollama_call(prompt, model),
                            timeout=_OLLAMA_TIMEOUT
                        )
                        return result
                    except asyncio.TimeoutError:
                        logger.warning(f"[{self.name}] Ollama timeout après {_OLLAMA_TIMEOUT}s")
                        return ""
            '''),
        ),
    ]


@_register("PERF-009")
def _perf_009(spec, source):
    """Lazy init WebSurfer Researcher — accesseurs lazy."""
    return [
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="DivineResearcher",
            code=textwrap.dedent('''\
                def _get_surfer(self):
                    """Accesseur lazy pour WebSurfer."""
                    if not hasattr(self, '_surfer') or self._surfer is None:
                        try:
                            from core.capabilities.web_surfer import WebSurfer
                            self._surfer = WebSurfer()
                        except Exception as e:
                            logger.warning(f"[Researcher] WebSurfer init failed: {e}")
                            self._surfer = None
                    return self._surfer
            '''),
        ),
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="DivineResearcher",
            code=textwrap.dedent('''\
                def _get_ingestor(self):
                    """Accesseur lazy pour KnowledgeIngestor."""
                    if not hasattr(self, '_ingestor') or self._ingestor is None:
                        try:
                            from core.capabilities.knowledge_ingestor import KnowledgeIngestor
                            self._ingestor = KnowledgeIngestor()
                        except Exception as e:
                            logger.warning(f"[Researcher] KnowledgeIngestor init failed: {e}")
                            self._ingestor = None
                    return self._ingestor
            '''),
        ),
    ]


# --- RESILIENCE (Phase B) ---

@_register("RES-002")
def _res_002(spec, source):
    """Circuit breaker Ollama — attributs classe + 3 méthodes."""
    return [
        TransformAction(
            type=TransformType.ADD_IMPORT,
            import_line="import time",
        ),
        TransformAction(
            type=TransformType.ADD_CLASS_ATTRIBUTE,
            target_class="BaseAgent",
            code='_ollama_circuit_open = False\n_ollama_circuit_until = 0.0\n_ollama_consecutive_failures = 0\n_OLLAMA_CIRCUIT_THRESHOLD = 3\n_OLLAMA_CIRCUIT_COOLDOWN = 60',
        ),
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="BaseAgent",
            code=textwrap.dedent('''\
                @classmethod
                def _check_ollama_circuit(cls) -> bool:
                    """Retourne True si le circuit est ouvert (Ollama indisponible)."""
                    if cls._ollama_circuit_open:
                        if time.time() >= cls._ollama_circuit_until:
                            cls._ollama_circuit_open = False
                            cls._ollama_consecutive_failures = 0
                            logger.info("Circuit breaker Ollama: FERMÉ (reset)")
                            return False
                        return True
                    return False
            '''),
        ),
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="BaseAgent",
            code=textwrap.dedent('''\
                @classmethod
                def _record_ollama_failure(cls):
                    """Enregistre un échec Ollama et ouvre le circuit si seuil atteint."""
                    cls._ollama_consecutive_failures += 1
                    if cls._ollama_consecutive_failures >= cls._OLLAMA_CIRCUIT_THRESHOLD:
                        cls._ollama_circuit_open = True
                        cls._ollama_circuit_until = time.time() + cls._OLLAMA_CIRCUIT_COOLDOWN
                        logger.warning(f"Circuit breaker Ollama: OUVERT pour {cls._OLLAMA_CIRCUIT_COOLDOWN}s")
            '''),
        ),
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="BaseAgent",
            code=textwrap.dedent('''\
                @classmethod
                def _record_ollama_success(cls):
                    """Reset le compteur d'échecs Ollama."""
                    cls._ollama_consecutive_failures = 0
            '''),
        ),
    ]


@_register("RES-006")
def _res_006(spec, source):
    """Fallback recherche locale Researcher."""
    return [
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="DivineResearcher",
            code=textwrap.dedent('''\
                async def _safe_web_search(self, query: str) -> str:
                    """Recherche web avec fallback sur mémoire locale."""
                    try:
                        surfer = self._get_surfer() if hasattr(self, '_get_surfer') else getattr(self, 'surfer', None)
                        if surfer:
                            return await surfer.search(query)
                    except Exception as e:
                        logger.warning(f"[Researcher] WebSurfer failed, fallback RAG local: {e}")
                    local = self.recall(query) if hasattr(self, 'recall') else ""
                    if local:
                        return f"(Résultats mémoire locale)\\n{local}"
                    return ""
            '''),
        ),
    ]


@_register("RES-007")
def _res_007(spec, source):
    """Timeout sub-checks Infra — fonction helper avec timeout."""
    return [
        TransformAction(
            type=TransformType.ADD_IMPORT,
            import_line="import asyncio",
        ),
        TransformAction(
            type=TransformType.ADD_MODULE_FUNCTION,
            code=textwrap.dedent('''\
                async def _safe_check(func, timeout=5.0, default=None):
                    """Exécute un check synchrone avec timeout asyncio."""
                    try:
                        loop = asyncio.get_event_loop()
                        return await asyncio.wait_for(
                            loop.run_in_executor(None, func),
                            timeout=timeout
                        )
                    except asyncio.TimeoutError:
                        logger.warning(f"Health check timeout après {timeout}s")
                        return default
                    except Exception as e:
                        logger.warning(f"Health check error: {e}")
                        return default
            '''),
        ),
    ]


# --- INTELLIGENCE (Phase B) ---

@_register("INT-004")
def _int_004(spec, source):
    """Prompt adaptatif PSYCHE — méthode _get_psyche_context."""
    return [
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="BaseAgent",
            code=textwrap.dedent('''\
                def _get_psyche_context(self) -> str:
                    """Injecte le mood et le trait dominant dans le prompt."""
                    try:
                        from core.psyche import psyche
                        traits = psyche.get_agent_traits(self.name)
                        if traits:
                            dominant = max(traits.items(), key=lambda x: x[1])
                            from core.self_awareness import awareness
                            snap = awareness.get_latest_snapshot()
                            mood = snap.get("mood", "équilibré") if snap else "équilibré"
                            return f"[Mode {mood}. Trait dominant: {dominant[0]} ({dominant[1]:.0f}/100)]"
                    except Exception:
                        pass
                    return ""
            '''),
        ),
    ]


@_register("INT-006")
def _int_006(spec, source):
    """Post-filtre code Coder — vérification structure Python."""
    return [
        TransformAction(
            type=TransformType.ADD_IMPORT,
            import_line="import re",
        ),
        TransformAction(
            type=TransformType.ADD_MODULE_FUNCTION,
            code=textwrap.dedent('''\
                def _has_code_structure(text: str) -> bool:
                    """Vérifie la présence de structures Python (def, class, import)."""
                    patterns = [r'^\\s*def\\s+', r'^\\s*class\\s+', r'^\\s*import\\s+', r'^\\s*from\\s+']
                    count = sum(1 for p in patterns if re.search(p, text, re.MULTILINE))
                    return count >= 2
            '''),
        ),
    ]


@_register("INT-008")
def _int_008(spec, source):
    """Sonde latence Ollama Infra — fonction async de probe."""
    return [
        TransformAction(
            type=TransformType.ADD_IMPORT,
            import_line="import time",
        ),
        TransformAction(
            type=TransformType.ADD_MODULE_FUNCTION,
            code=textwrap.dedent('''\
                async def _probe_ollama_latency() -> dict:
                    """Mesure la latence Ollama via /api/tags."""
                    import httpx
                    try:
                        start = time.time()
                        async with httpx.AsyncClient(timeout=5.0) as client:
                            resp = await client.get("http://localhost:11434/api/tags")
                            latency_ms = (time.time() - start) * 1000
                            models = len(resp.json().get("models", []))
                            return {"status": "up", "latency_ms": round(latency_ms, 1), "models_loaded": models}
                    except Exception as e:
                        return {"status": "down", "error": str(e)[:100]}
            '''),
        ),
    ]


@_register("INT-009")
def _int_009(spec, source):
    """Évolution traits par résultat Psyche."""
    return [
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="PsycheEngine",
            code=textwrap.dedent('''\
                def apply_routine_result(self, agent: str, status: str):
                    """Ajuste les traits selon le résultat d'une routine."""
                    if agent not in self.agents:
                        return
                    if status == "success":
                        deltas = {"créativité": 1.5, "prudence": -0.5}
                    elif status == "error":
                        deltas = {"prudence": 1.5, "créativité": -0.5}
                    else:
                        return
                    for trait, delta in deltas.items():
                        if trait in self.agents[agent]:
                            self.agents[agent][trait] = max(0, min(100, self.agents[agent][trait] + delta))
            '''),
        ),
    ]


# --- OBSERVABILITY (Phase B) ---

@_register("OBS-005")
def _obs_005(spec, source):
    """Talk log structuré JSON — fonction de formatage."""
    return [
        TransformAction(
            type=TransformType.ADD_IMPORT,
            import_line="import json",
        ),
        TransformAction(
            type=TransformType.ADD_MODULE_FUNCTION,
            code=textwrap.dedent('''\
                def _format_structured_entry(event_type: str, data: dict) -> str:
                    """Format structuré : ligne JSON + texte lisible."""
                    meta = {
                        "type": event_type,
                        "ts": data.get("timestamp", ""),
                        "agent": data.get("agent", "system"),
                    }
                    json_line = json.dumps(meta, ensure_ascii=False)
                    content = data.get("content", str(data))[:500]
                    return f"[JSON]{json_line}[/JSON]\\n{content}\\n"
            '''),
        ),
    ]


@_register("OBS-006")
def _obs_006(spec, source):
    """Dédup recherches journal — méthode de vérification."""
    return [
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="StrategicJournal",
            code=textwrap.dedent('''\
                def _is_duplicate_topic(self, topic: str) -> bool:
                    """Vérifie si le topic existe dans les 5 dernières entrées research."""
                    topic_lower = topic.strip().lower()
                    recent = self._entries[-5:] if len(self._entries) >= 5 else self._entries
                    for entry in recent:
                        if entry.get("type") == "research":
                            if entry.get("topic", "").strip().lower() == topic_lower:
                                return True
                    return False
            '''),
        ),
    ]


@_register("OBS-007")
def _obs_007(spec, source):
    """Détection cycles debug SelfAwareness."""
    return [
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="SelfAwarenessEngine",
            code=textwrap.dedent('''\
                def _detect_debug_loops(self, recent_events: list) -> list:
                    """Détecte les boucles error→success→error sur un même agent."""
                    patterns = []
                    by_agent = {}
                    for ev in recent_events[-50:]:
                        agent = ev.get("agent", "unknown")
                        by_agent.setdefault(agent, []).append(ev)
                    for agent, events in by_agent.items():
                        alternances = 0
                        for i in range(1, len(events)):
                            prev_ok = events[i-1].get("status") == "success"
                            curr_ok = events[i].get("status") == "success"
                            if prev_ok != curr_ok:
                                alternances += 1
                        if alternances >= 3:
                            patterns.append({
                                "type": "debug_loop",
                                "agent": agent,
                                "alternances": alternances,
                                "severity": "warning",
                            })
                    return patterns
            '''),
        ),
    ]


@_register("OBS-008")
def _obs_008(spec, source):
    """Métriques processing Dropzone — fonction de stats."""
    return [
        TransformAction(
            type=TransformType.ADD_IMPORT,
            import_line="import time",
        ),
        TransformAction(
            type=TransformType.ADD_MODULE_FUNCTION,
            code=textwrap.dedent('''\
                def _compute_dropzone_stats(files_count: int, total_chars: int, start_time: float) -> dict:
                    """Calcule les métriques d'un run Dropzone."""
                    duration = time.time() - start_time
                    return {
                        "files_processed": files_count,
                        "total_chars": total_chars,
                        "duration_s": round(duration, 1),
                        "avg_chars_per_file": round(total_chars / max(files_count, 1)),
                    }
            '''),
        ),
    ]


# --- SECURITY (Phase B) ---

@_register("SEC-003")
def _sec_003(spec, source):
    """Rate limit par agent — attributs + méthode check."""
    return [
        TransformAction(
            type=TransformType.ADD_IMPORT,
            import_line="import time",
        ),
        TransformAction(
            type=TransformType.ADD_CLASS_ATTRIBUTE,
            target_class="Orchestrator",
            code='_dispatch_counts: dict = {}\n_RATE_LIMIT = 10\n_RATE_WINDOW = 300',
        ),
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="Orchestrator",
            code=textwrap.dedent('''\
                def _check_rate_limit(self, agent_name: str) -> bool:
                    """Vérifie le rate limit par agent (max 10/5min)."""
                    now = time.time()
                    timestamps = self._dispatch_counts.get(agent_name, [])
                    timestamps = [ts for ts in timestamps if now - ts < self._RATE_WINDOW]
                    self._dispatch_counts[agent_name] = timestamps
                    if len(timestamps) >= self._RATE_LIMIT:
                        logger.warning(f"Rate limit atteint pour {agent_name}: {len(timestamps)}/{self._RATE_LIMIT}")
                        return True
                    timestamps.append(now)
                    return False
            '''),
        ),
    ]


@_register("SEC-004")
def _sec_004(spec, source):
    """Sanitization secrets Coder — constantes + fonction."""
    return [
        TransformAction(
            type=TransformType.ADD_IMPORT,
            import_line="import re",
        ),
        TransformAction(
            type=TransformType.ADD_MODULE_CONSTANT,
            code=textwrap.dedent('''\
                _SECRET_PATTERNS = [
                    (r'["\\'](?:sk-[a-zA-Z0-9]{20,})["\\']', '"<API_KEY>"'),
                    (r'["\\'](?:AIza[a-zA-Z0-9_-]{30,})["\\']', '"<GOOGLE_API_KEY>"'),
                    (r'password\\s*=\\s*["\\']([^"\\']*)["\\']', 'password = "<REDACTED>"'),
                    (r'token\\s*=\\s*["\\']([^"\\']*)["\\']', 'token = "<REDACTED>"'),
                ]
            '''),
        ),
        TransformAction(
            type=TransformType.ADD_MODULE_FUNCTION,
            code=textwrap.dedent('''\
                def _sanitize_secrets(code: str) -> str:
                    """Remplace les patterns de secrets par des placeholders."""
                    for pattern, replacement in _SECRET_PATTERNS:
                        code = re.sub(pattern, replacement, code)
                    return code
            '''),
        ),
    ]


# --- MEMORY (Phase B) ---

@_register("MEM-004")
def _mem_004(spec, source):
    """Mémoire partagée inter-agents — 2 méthodes."""
    return [
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="BaseAgent",
            code=textwrap.dedent('''\
                def share_insight(self, content: str, metadata: dict = None):
                    """Partage un insight dans la mémoire partagée."""
                    try:
                        memory = self._get_memory() if hasattr(self, '_get_memory') else getattr(self, '_memory_manager', None)
                        if memory:
                            from datetime import datetime
                            meta = {"agent": self.name, "ts": datetime.now().isoformat()}
                            if metadata:
                                meta.update(metadata)
                            memory.add_to_collection("shared_insights", content, meta)
                    except Exception as e:
                        logger.warning(f"[{self.name}] share_insight failed: {e}")
            '''),
        ),
        TransformAction(
            type=TransformType.ADD_METHOD,
            target_class="BaseAgent",
            code=textwrap.dedent('''\
                def recall_shared(self, query: str, limit: int = 3) -> str:
                    """Rappelle des insights partagés par d'autres agents."""
                    try:
                        memory = self._get_memory() if hasattr(self, '_get_memory') else getattr(self, '_memory_manager', None)
                        if memory:
                            results = memory.query_collection("shared_insights", query, n_results=limit)
                            if results and results.get("documents"):
                                return "\\n".join(results["documents"][0])
                    except Exception as e:
                        logger.warning(f"[{self.name}] recall_shared failed: {e}")
                    return ""
            '''),
        ),
    ]


# --- API publique ---

def spec_to_actions(spec, source: str) -> Optional[List[TransformAction]]:
    """Convertit une spec en actions. Retourne None si non supportée (→ fallback LLM)."""
    handler = _SPEC_HANDLERS.get(spec.id)
    if handler:
        try:
            return handler(spec, source)
        except Exception as e:
            logger.warning(f"CodeSmith handler {spec.id} failed: {e}")
    return None


def get_supported_specs() -> list:
    """Retourne la liste des spec_ids supportés par CodeSmith."""
    return sorted(_SPEC_HANDLERS.keys())
