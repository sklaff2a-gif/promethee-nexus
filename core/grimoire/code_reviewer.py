import ast
import re
import logging
from core.base_agent import BaseAgent

logger = logging.getLogger("code_reviewer")

# Mêmes patterns critiques que l'Architect (_analyze_risk)
_CRITICAL_PATTERNS = [
    "os.remove", "shutil.rmtree", "format c:", "drop database", "system32",
    "rm -rf", "rm -r ", "rmdir /s",
    "subprocess.run", "subprocess.call", "subprocess.popen", "os.system", "os.popen",
    "setuid", "setgid", "chmod 777", "chmod +s",
    "eval(", "exec(", "__import__(",
    "pickle.loads", "marshal.loads",
    "os.environ[", "os.putenv",
]

# Types de transformations détectables par patterns AST
_TRANSFORM_PATTERNS = {
    "ajout_import": r"^\s*(?:import |from \S+ import )",
    "ajout_methode": r"^\s+(?:async\s+)?def \w+\(",
    "ajout_classe": r"^class \w+",
    "ajout_decorateur": r"^\s+@\w+",
    "try_except_wrap": r"^\s+try:",
    "ajout_logging": r"(?:logger|logging)\.\w+\(",
    "ajout_docstring": r'^\s+"""',
}


def analyze_code(code: str) -> dict:
    """Analyse déterministe du code — zéro appel LLM.

    Retourne un dict avec :
    - ast_valid (bool) : syntaxe Python valide
    - ast_error (str|None) : message d'erreur AST si invalide
    - critical_danger (bool) : patterns critiques détectés
    - danger_details (str) : détail des patterns trouvés
    - complete (bool) : code non tronqué (heuristique)
    - line_count (int) : nombre de lignes
    - transforms (list[str]) : types de transformations détectées
    - has_def_or_class (bool) : contient def ou class
    """
    result = {
        "ast_valid": False,
        "ast_error": None,
        "critical_danger": False,
        "danger_details": "",
        "complete": True,
        "line_count": 0,
        "transforms": [],
        "has_def_or_class": False,
    }

    if not code or not code.strip():
        result["ast_error"] = "Code vide"
        result["complete"] = False
        return result

    lines = code.strip().splitlines()
    result["line_count"] = len(lines)

    # 1. Validation AST
    try:
        ast.parse(code)
        result["ast_valid"] = True
    except SyntaxError as e:
        result["ast_error"] = f"SyntaxError ligne {e.lineno}: {e.msg}"
        return result

    # 2. Patterns critiques
    code_lower = code.lower()
    found_dangers = [p for p in _CRITICAL_PATTERNS if p in code_lower]
    if found_dangers:
        result["critical_danger"] = True
        result["danger_details"] = ", ".join(found_dangers)

    # 3. Complétude (heuristique : pas de troncature évidente)
    last_line = lines[-1].strip()
    if last_line.endswith("...") or last_line == "# truncated":
        result["complete"] = False

    # 4. Types de transformations
    for name, pattern in _TRANSFORM_PATTERNS.items():
        if re.search(pattern, code, re.MULTILINE):
            result["transforms"].append(name)

    # 5. Contient def ou class
    result["has_def_or_class"] = bool(
        re.search(r"(?:^|\n)\s*(?:(?:async\s+)?def |class )\w+", code)
    )

    return result


def build_review_prompt(mission: str, code: str, analysis: dict) -> str:
    """Construit le prompt spécialisé pour la revue de code."""
    transforms_str = ", ".join(analysis["transforms"]) if analysis["transforms"] else "aucune détectée"
    dangers_str = analysis["danger_details"] if analysis["critical_danger"] else "aucun"

    return (
        "Tu es un reviewer de code spécialisé dans les transformations déterministes.\n"
        "Un outil CodeSmith (AST-based, pas de LLM) a généré le code ci-dessous.\n"
        "L'Architect l'a refusé. Ton rôle : analyser objectivement si le code est sûr.\n\n"
        f"--- CONTEXTE ---\n{mission}\n\n"
        f"--- ANALYSE DETERMINISTE ---\n"
        f"Syntaxe AST : {'VALIDE' if analysis['ast_valid'] else 'INVALIDE — ' + str(analysis['ast_error'])}\n"
        f"Lignes : {analysis['line_count']}\n"
        f"Transformations détectées : {transforms_str}\n"
        f"Patterns dangereux : {dangers_str}\n"
        f"Code complet : {'OUI' if analysis['complete'] else 'NON (troncature détectée)'}\n"
        f"Contient def/class : {'OUI' if analysis['has_def_or_class'] else 'NON'}\n\n"
        f"--- CODE À REVIEWER ---\n```python\n{code}\n```\n\n"
        "IMPORTANT : Ce code provient d'une transformation déterministe (AST), pas d'un LLM.\n"
        "Les transformations CodeSmith sont : ajout de méthodes, ajout d'imports, wrapping try/except, "
        "ajout de logging, ajout de docstrings — toutes prévisibles et testées.\n\n"
        "Réponds EXACTEMENT par :\n"
        "VERDICT: SAFE — [justification courte]\n"
        "ou\n"
        "VERDICT: UNSAFE — [justification courte]\n"
    )


def parse_verdict(response: str) -> bool:
    """Parse SAFE/UNSAFE depuis la réponse LLM."""
    if not response:
        return False
    response_upper = response.upper()
    # Chercher le pattern VERDICT: SAFE/UNSAFE
    match = re.search(r"VERDICT\s*:\s*(SAFE|UNSAFE)", response_upper)
    if match:
        return match.group(1) == "SAFE"
    # Fallback : chercher SAFE ou UNSAFE en début de ligne
    if re.search(r"^\s*SAFE\b", response_upper, re.MULTILINE):
        return True
    return False


class CodeReviewer(BaseAgent):
    """Agent Grimoire spécialisé dans la revue de diffs CodeSmith pour le pipeline Evolution."""

    def __init__(self):
        super().__init__(
            name="code_reviewer",
            role="Reviewer de code spécialisé",
            description="Revue de diffs CodeSmith pour le pipeline Evolution"
        )

    async def process_task(self, task_payload):
        mission = task_payload.get("mission", "")
        context = task_payload.get("context", "")
        code = context if context else mission

        # 1. Analyse déterministe
        analysis = analyze_code(code)

        # 2. Si AST invalide → UNSAFE sans LLM
        if not analysis["ast_valid"]:
            return {
                "status": "error",
                "result": f"UNSAFE — syntaxe invalide : {analysis['ast_error']}",
                "review": analysis,
                "verdict": "UNSAFE",
            }

        # 3. Si danger critique → UNSAFE sans LLM
        if analysis["critical_danger"]:
            return {
                "status": "error",
                "result": f"UNSAFE — patterns critiques détectés : {analysis['danger_details']}",
                "review": analysis,
                "verdict": "UNSAFE",
            }

        # 4. Prompt LLM spécialisé pour verdict final
        prompt = build_review_prompt(mission, code, analysis)
        response = await self.generate_content(prompt)

        # 5. Parser verdict
        is_safe = parse_verdict(response)

        return {
            "status": "success" if is_safe else "error",
            "result": response,
            "review": analysis,
            "verdict": "SAFE" if is_safe else "UNSAFE",
        }
