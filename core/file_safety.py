"""Helpers de validation de chemins (anti path traversal).

Centralisé pour defense-in-depth dans Architect/Formatter/Factory.
Utilise pathlib pour validation canonique : résout symlinks, encodages
alternatifs, normalise paths, capture null-byte injection.

Audit security 17/05 06:49 — agent SECURITY a détecté vulnérabilité HAUTE
dans architect_agent.py:196-199 (extraction target_file sans validation).
"""
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def is_safe_target_path(candidate, base: Path = None) -> bool:
    """Valide qu'un chemin candidat reste strictement dans la sandbox projet.

    Bloque par résolution canonique :
    - Path traversal (`..`, encodages alternatifs, doubles slashes)
    - Paths absolus hors sandbox (`/etc/passwd`, `C:\\Windows\\...`)
    - Symlinks pointant hors sandbox
    - Paths UNC Windows (`\\\\?\\...`, `\\\\server\\share`)
    - Null-byte injection (`safe.py\\x00../../etc/passwd`)
    - Types non-string (None, int, etc.)

    Args:
        candidate: chemin candidat (relatif attendu, type str)
        base: répertoire racine sandbox (défaut: racine du projet PROMETHEE)

    Returns:
        True si le chemin résolu est strictement contenu dans base.
        False si vide, invalide, hors sandbox, ou exception de résolution.

    Examples:
        >>> is_safe_target_path("core/body_schema.py")
        True
        >>> is_safe_target_path("../../etc/passwd")
        False
        >>> is_safe_target_path("/etc/passwd")
        False
        >>> is_safe_target_path("C:\\\\Windows\\\\System32")
        False
        >>> is_safe_target_path("")
        False
        >>> is_safe_target_path(None)
        False
    """
    if not candidate or not isinstance(candidate, str):
        return False
    # Check null-byte explicite : Windows Python 3.11 tronque silencieusement
    # au lieu de lever ValueError, ce qui masquerait l'intention malveillante.
    if "\x00" in candidate:
        return False
    if base is None:
        base = _PROJECT_ROOT
    try:
        base_resolved = base.resolve()
        target = (base_resolved / candidate).resolve()
        return target.is_relative_to(base_resolved)
    except (ValueError, OSError, RuntimeError):
        # ValueError : null-byte injection, paths invalides
        # OSError : symlinks loop, accès disque refusé
        # RuntimeError : récursion infinie sur résolution
        return False
