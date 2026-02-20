# core/code_smith.py — Transformations déterministes de code Python
# Remplace le LLM pour les modifications du catalogue Evolution.
# Principe : AST pour localiser, texte brut pour modifier → préserve commentaires et formatage.

import ast
import textwrap
import logging
from dataclasses import dataclass, field
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
    """Heartbeat Ollama — constante + méthode async pour publier OLLAMA_DOWN."""
    return [
        TransformAction(
            type=TransformType.ADD_MODULE_CONSTANT,
            code='_OLLAMA_HEALTH_URL = "http://localhost:11434/api/tags"',
        ),
        TransformAction(
            type=TransformType.ADD_MODULE_FUNCTION,
            code=textwrap.dedent('''\
                async def _check_ollama_heartbeat():
                    """Vérifie si Ollama répond. Publie OLLAMA_DOWN si non."""
                    import httpx
                    try:
                        async with httpx.AsyncClient(timeout=5.0) as client:
                            resp = await client.get(_OLLAMA_HEALTH_URL)
                            return resp.status_code == 200
                    except Exception:
                        try:
                            from core.event_bus.bus import bus
                            from datetime import datetime
                            await bus.publish("OLLAMA_DOWN", {
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
