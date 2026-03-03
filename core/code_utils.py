"""Utilitaires partagés d'analyse de code Python.
M05: source unique pour _contains_python_code (ex-dupliqué dans orchestrator + architect).
"""
import re


def contains_python_code(text: str) -> bool:
    """Détecte du vrai code Python structurel (pas une simple mention dans du texte).
    Retourne True si au moins 2 patterns structurels (import/class/def/decorator) sont trouvés.
    """
    code_patterns = re.findall(r'^(import |from \w+ import|class \w+|def \w+\(|@\w+)', text, re.MULTILINE)
    return len(code_patterns) >= 2
