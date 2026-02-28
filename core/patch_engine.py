"""
Moteur de micro-patchs SEARCH/REPLACE pour LLMs locaux.

Quand le Cloud est indisponible, les LLMs locaux (14B) n'ont pas assez
de tokens de sortie pour retourner un fichier complet. Ce moteur leur
demande uniquement les lignes modifiées au format SEARCH/REPLACE, puis
applique les modifications de façon déterministe sur le source original.

Format attendu :

    <<<< SEARCH
    lignes exactes copiées du source
    ====
    lignes de remplacement
    >>>> REPLACE
"""

import re
import logging
from dataclasses import dataclass, field
from core.prompt_templates import CODE_GENERATION_GUARDRAIL

logger = logging.getLogger("patch_engine")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PatchHunk:
    """Un bloc SEARCH/REPLACE."""
    search: str
    replace: str


@dataclass
class PatchResult:
    """Résultat de l'application d'un patch."""
    success: bool
    patched_source: str
    hunks_applied: int
    hunks_total: int
    error: str = ""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# Marqueurs (tolère espaces trailing)
_RE_SEARCH_START = re.compile(r"^<{4}\s*SEARCH\s*$")
_RE_SEPARATOR = re.compile(r"^={4}\s*$")
_RE_REPLACE_END = re.compile(r"^>{4}\s*REPLACE\s*$")


def parse_patch(raw_text: str) -> tuple[list[PatchHunk], list[str]]:
    """Parse la sortie LLM en liste de PatchHunk.

    Retourne (hunks, errors). Les erreurs sont non-fatales (avertissements).
    """
    # Nettoyer les blocs markdown wrapping
    text = _strip_markdown_fences(raw_text)
    lines = text.split("\n")

    hunks: list[PatchHunk] = []
    errors: list[str] = []

    # Machine à états
    state = "IDLE"  # IDLE → SEARCH → REPLACE → IDLE
    search_lines: list[str] = []
    replace_lines: list[str] = []

    for i, line in enumerate(lines):
        if state == "IDLE":
            if _RE_SEARCH_START.match(line.rstrip()):
                state = "SEARCH"
                search_lines = []
                replace_lines = []
        elif state == "SEARCH":
            if _RE_SEPARATOR.match(line.rstrip()):
                state = "REPLACE"
            elif _RE_REPLACE_END.match(line.rstrip()):
                # SEARCH sans séparateur → erreur
                errors.append(f"Ligne {i+1}: >>>> REPLACE sans séparateur ====")
                state = "IDLE"
            else:
                search_lines.append(line)
        elif state == "REPLACE":
            if _RE_REPLACE_END.match(line.rstrip()):
                # Valider et enregistrer le hunk
                search_text = "\n".join(search_lines)
                replace_text = "\n".join(replace_lines)
                if not search_text.strip():
                    errors.append(f"Ligne {i+1}: bloc SEARCH vide")
                else:
                    hunks.append(PatchHunk(search=search_text, replace=replace_text))
                state = "IDLE"
            else:
                replace_lines.append(line)

    # Bloc incomplet en fin de texte
    if state != "IDLE":
        errors.append(f"Bloc incomplet en fin de texte (état: {state})")

    return hunks, errors


def _strip_markdown_fences(text: str) -> str:
    """Retire les blocs ```python ... ``` qui wrappent les marqueurs."""
    # Retirer les lignes qui sont uniquement des fences markdown
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

def apply_patch(source: str, hunks: list[PatchHunk]) -> PatchResult:
    """Applique les hunks séquentiellement sur le source.

    Chaque hunk opère sur le source modifié par les précédents.
    Matching : exact d'abord, puis normalisé (rstrip) si échec.
    Première occurrence seulement.
    """
    if not hunks:
        return PatchResult(
            success=False, patched_source=source,
            hunks_applied=0, hunks_total=0,
            error="Aucun hunk à appliquer"
        )

    current = source
    applied = 0
    total = len(hunks)
    errors = []

    for idx, hunk in enumerate(hunks):
        # Tentative 1 : matching exact
        if hunk.search in current:
            current = current.replace(hunk.search, hunk.replace, 1)
            applied += 1
            continue

        # Tentative 2 : matching normalisé (rstrip chaque ligne)
        norm_search = _normalize_trailing(hunk.search)
        norm_current = _normalize_trailing(current)
        if norm_search in norm_current:
            # Trouver la position dans le texte normalisé
            pos = norm_current.index(norm_search)
            # Recalculer la position dans le texte original
            original_pos = _find_original_position(current, hunk.search)
            if original_pos is not None:
                end_pos = original_pos + len(_get_original_span(current, original_pos, hunk.search))
                current = current[:original_pos] + hunk.replace + current[end_pos:]
                applied += 1
                continue

        errors.append(f"Hunk {idx+1}/{total}: SEARCH introuvable")

    success = applied == total
    error_msg = "; ".join(errors) if errors else ""
    return PatchResult(
        success=success, patched_source=current,
        hunks_applied=applied, hunks_total=total,
        error=error_msg
    )


def _normalize_trailing(text: str) -> str:
    """Normalise en retirant les espaces trailing de chaque ligne."""
    return "\n".join(line.rstrip() for line in text.split("\n"))


def _find_original_position(source: str, search: str) -> int | None:
    """Trouve la position dans le source original en matchant ligne par ligne avec rstrip."""
    search_lines = search.split("\n")
    source_lines = source.split("\n")
    search_count = len(search_lines)

    for i in range(len(source_lines) - search_count + 1):
        match = True
        for j in range(search_count):
            if source_lines[i + j].rstrip() != search_lines[j].rstrip():
                match = False
                break
        if match:
            # Calculer la position en caractères
            pos = sum(len(source_lines[k]) + 1 for k in range(i))  # +1 pour \n
            return pos
    return None


def _get_original_span(source: str, start_pos: int, search: str) -> str:
    """Retourne le texte original correspondant au span du search."""
    search_line_count = len(search.split("\n"))
    source_lines = source.split("\n")

    # Trouver la ligne de départ
    line_idx = source[:start_pos].count("\n")

    # Reconstruire le span original
    span_lines = source_lines[line_idx:line_idx + search_line_count]
    return "\n".join(span_lines)


# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

MICRO_PATCH_PROMPT = (
    "Tu dois modifier le fichier {target_file} pour appliquer cette amélioration.\n"
    "AMÉLIORATION [{spec_id}] : {spec_name}\n"
    "DESCRIPTION : {spec_description}\n"
    "TEMPLATE DE CODE À INTÉGRER :\n{code_template}\n\n"
    "CODE SOURCE ACTUEL (extrait pertinent) :\n{source_excerpt}\n\n"
    "INSTRUCTIONS :\n"
    "- Retourne UNIQUEMENT des blocs SEARCH/REPLACE\n"
    "- Chaque bloc remplace des lignes EXACTES du source par la version modifiée\n"
    "- Copie les lignes du source EXACTEMENT (même indentation, même espaces)\n"
    "- NE retourne PAS le fichier complet, seulement les blocs de modification\n\n"
    "FORMAT OBLIGATOIRE (un ou plusieurs blocs) :\n\n"
    "<<<< SEARCH\n"
    "lignes exactes copiées du source\n"
    "====\n"
    "lignes de remplacement\n"
    ">>>> REPLACE\n\n"
    "EXEMPLE :\n\n"
    "<<<< SEARCH\n"
    "    def process(self, data):\n"
    "        return data\n"
    "====\n"
    "    def process(self, data):\n"
    "        if not data:\n"
    "            return None\n"
    "        return data\n"
    ">>>> REPLACE\n\n"
    "Donne UNIQUEMENT les blocs SEARCH/REPLACE, RIEN d'autre."
    "{guardrail}"
)
