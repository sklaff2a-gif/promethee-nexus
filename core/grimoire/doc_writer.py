import ast
import re
import os
import logging
from core.base_agent import BaseAgent

logger = logging.getLogger("doc_writer")

# Répertoire racine du projet
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _extract_python_file(text: str) -> str:
    """Extrait un chemin de fichier .py mentionné dans le texte."""
    match = re.search(r'([\w/\\]+\.py)\b', text)
    return match.group(1) if match else ""


def _analyze_ast(source_code: str) -> dict:
    """Analyse AST d'un fichier Python : classes, fonctions, imports."""
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return {"parsed": False}

    classes = []
    functions = []
    imports = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            bases = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    bases.append(base.id)
                elif isinstance(base, ast.Attribute):
                    bases.append(f"{base.value.id}.{base.attr}" if isinstance(base.value, ast.Name) else base.attr)
            classes.append({"name": node.name, "bases": bases, "methods": methods, "line": node.lineno})
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Fonctions top-level uniquement (pas les méthodes de classe)
            if isinstance(node, ast.AsyncFunctionDef):
                prefix = "async "
            else:
                prefix = ""
            # Vérifier si c'est une fonction top-level
            for parent in ast.walk(tree):
                if isinstance(parent, ast.ClassDef) and node in ast.walk(parent):
                    break
            else:
                args = [a.arg for a in node.args.args if a.arg != "self"]
                functions.append({"name": f"{prefix}{node.name}", "args": args, "line": node.lineno})
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append(f"{module}.{alias.name}")

    return {
        "parsed": True,
        "classes": classes,
        "functions": functions,
        "imports": imports[:20],  # Limiter
    }


def _build_doc_skeleton(ast_info: dict, filename: str) -> str:
    """Génère un squelette de documentation basé sur l'AST."""
    lines = [f"# {filename}\n"]

    if ast_info.get("classes"):
        lines.append("## Classes\n")
        for cls in ast_info["classes"]:
            bases = f"({', '.join(cls['bases'])})" if cls["bases"] else ""
            lines.append(f"### `{cls['name']}{bases}` (ligne {cls['line']})\n")
            if cls["methods"]:
                lines.append("Méthodes :")
                for m in cls["methods"]:
                    lines.append(f"- `{m}()`")
            lines.append("")

    if ast_info.get("functions"):
        lines.append("## Fonctions\n")
        for func in ast_info["functions"]:
            args_str = ", ".join(func["args"]) if func["args"] else ""
            lines.append(f"- `{func['name']}({args_str})` (ligne {func['line']})")
        lines.append("")

    if ast_info.get("imports"):
        lines.append("## Dépendances\n")
        for imp in ast_info["imports"]:
            lines.append(f"- `{imp}`")

    return "\n".join(lines)


class DocWriter(BaseAgent):
    def __init__(self):
        super().__init__(
            name="doc_writer",
            role="Rédacteur de documentation",
            description="Rédige de la documentation technique : README, docstrings, guides API, changelogs"
        )

    async def process_task(self, task_payload):
        mission = task_payload.get("mission", "")
        context = task_payload.get("context", "")
        full_text = f"{mission}\n{context}"

        # Pré-traitement : si un fichier .py est mentionné, analyser sa structure
        enrichment = ""
        ast_info = {"parsed": False}
        target_file = _extract_python_file(full_text)

        if target_file:
            # Tenter de lire le fichier depuis le projet
            filepath = target_file
            if not os.path.isabs(filepath):
                filepath = os.path.join(_PROJECT_ROOT, filepath.replace("/", os.sep))

            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    source_code = f.read()
                ast_info = _analyze_ast(source_code)
                if ast_info["parsed"]:
                    skeleton = _build_doc_skeleton(ast_info, target_file)
                    enrichment = (
                        f"\n\n--- ANALYSE AST AUTOMATIQUE DE {target_file} ---\n"
                        f"{skeleton}\n"
                        f"--- FIN ANALYSE ---\n"
                    )
            except FileNotFoundError:
                enrichment = f"\n\n(Fichier {target_file} non trouvé dans le projet)\n"
            except Exception as e:
                logger.warning(f"Erreur lecture {target_file}: {e}")

        prompt = (
            "Tu es un rédacteur de documentation technique. "
            "Rédige de la documentation claire, structurée en Markdown, "
            "avec des exemples de code pertinents.\n\n"
            f"{mission}"
            f"{enrichment}"
        )
        response = await self.generate_content(prompt)
        return {"status": "success", "result": response, "ast_info": ast_info}
