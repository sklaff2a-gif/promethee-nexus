"""Phase 1 — boucle auto-alimentée : le researcher nourrit l'immersion.

VEILLE_IA dépose les EXTRAITS WEB RÉELS (ancrés URL) dans raw_flux/post_mortems/
pour que l'immersion digère du vrai savoir externe (→ pulsion:maitrise_epistemic).

Cœur testé : le VERROU ANTI-FICTION — on n'écrit de la nourriture QUE si de
vraies URLs ont été fetchées (marqueur 'LIEN: http' du WebSurfer). Jamais de
nourriture inventée dans le flux d'immersion.
"""

import re

import pytest

from core.autonomy_engine import autonomy


# Format réel d'un retour WebSurfer réussi (Google/DDG) : titres + LIEN: url + INFO:
_WEB_RAW_OK = (
    "- [DDG] Mixture of Experts breakthrough\n"
    "  LIEN: https://example.com/moe\n"
    "  INFO: New MoE routing achieves 4x speedup.\n\n"
    "- [GOOGLE] FlashAttention 3\n"
    "  LIEN: https://example.com/fa3\n"
    "  INFO: Faster attention kernels for inference.\n"
)


def _food_dir(tmp_path):
    return tmp_path / "data" / "raw_flux" / "post_mortems"


def test_grounded_food_is_written(tmp_path):
    """Avec de vraies URLs : la nourriture ancrée est déposée dans raw_flux."""
    p = autonomy._write_immersion_food(
        "Mixture of Experts", "MoE 2026", _WEB_RAW_OK, base_dir=str(tmp_path)
    )
    assert p is not None
    assert p.parent == _food_dir(tmp_path)
    assert p.name.startswith("veille_web_") and p.name.endswith(".txt")
    txt = p.read_text(encoding="utf-8")
    assert "[GROUNDED:web]" in txt                       # marqueur de provenance
    assert "## Extraits sources" in txt                  # format post-mortem digérable
    assert "https://example.com/moe" in txt              # vrai contenu + source PRÉSERVÉE
    assert "4x speedup" in txt                            # extrait externe réel conservé


def test_anti_fiction_error_string_no_write(tmp_path):
    """Chaîne d'erreur WebSurfer (pas de 'LIEN: http') → AUCUNE nourriture écrite."""
    p = autonomy._write_immersion_food(
        "focus", "q", "Aucun résultat trouvé (ni Google, ni DDG).", base_dir=str(tmp_path)
    )
    assert p is None
    d = _food_dir(tmp_path)
    assert (not d.exists()) or list(d.glob("*.txt")) == []   # rien créé


def test_anti_fiction_empty_no_write(tmp_path):
    """web_raw vide → None, pas de fichier."""
    assert autonomy._write_immersion_food("f", "q", "", base_dir=str(tmp_path)) is None


def test_anti_fiction_llm_prose_without_url_no_write(tmp_path):
    """Même une belle prose SANS URL fetchée n'entre pas (anti-confabulation)."""
    prose = "Les MoE offrent un gain de 4x. FlashAttention 3 accelere l'inference."
    assert autonomy._write_immersion_food("f", "q", prose, base_dir=str(tmp_path)) is None


def test_backlog_cap_throttle(tmp_path):
    """Anti-noyade : au-delà de MAX_PENDING_IMMERSION_FOOD repas en attente, on saute.
    VEILLE_SILENCIEUSE (60-120×/j) ne doit pas inonder une immersion qui digère 3-8×/j."""
    from core.autonomy_engine import MAX_PENDING_IMMERSION_FOOD
    dest = _food_dir(tmp_path)
    dest.mkdir(parents=True)
    # Remplir le backlog jusqu'au cap avec des repas factices
    for i in range(MAX_PENDING_IMMERSION_FOOD):
        (dest / f"veille_web_pending_{i}.txt").write_text("x", encoding="utf-8")
    # Le dépôt suivant (pourtant ancré) doit être SAUTÉ
    p = autonomy._write_immersion_food("focus", "q", _WEB_RAW_OK, base_dir=str(tmp_path))
    assert p is None
    assert len(list(dest.glob("veille_web_*.txt"))) == MAX_PENDING_IMMERSION_FOOD  # pas de 6e


def test_under_cap_still_writes(tmp_path):
    """Sous le cap : le dépôt ancré passe normalement."""
    dest = _food_dir(tmp_path)
    dest.mkdir(parents=True)
    (dest / "veille_web_pending_0.txt").write_text("x", encoding="utf-8")  # 1 < cap
    p = autonomy._write_immersion_food("focus", "q", _WEB_RAW_OK, base_dir=str(tmp_path))
    assert p is not None


def test_filename_sanitized(tmp_path):
    """Le focus avec caractères spéciaux/accents donne un nom de fichier sûr."""
    p = autonomy._write_immersion_food(
        "Mémoire & RAG: v2!!", "q", _WEB_RAW_OK, base_dir=str(tmp_path)
    )
    assert p is not None
    assert re.match(r"^veille_web_[A-Za-z0-9_]+_\d{8}_\d{6}\.txt$", p.name), p.name
