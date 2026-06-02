"""TDD — fuite catch-all du mentor dans les créneaux AUTO-SUJET (BULLETIN, FREE_TIME).

Prouvé in-vivo 31/05 : un défi de code rangé dans '*' était ramassé par un
BULLETIN (via le fallback catch-all) -> hors-sujet vs 'rédige ton bilan' ->
D5 subject_drift flag (à raison) -> note < 4 -> F1 skip -> famine BULLETIN.
Le même trou existe sur consume_direction. Fix : _SELF_SUBJECT_SLOTS.
"""
import pytest

from core.mentor import mentor


@pytest.fixture(autouse=True)
def _isolate_mentor():
    """Isole le singleton : état propre + aucune écriture disque pendant les tests."""
    saved_c = dict(mentor._pending_challenges)
    saved_d = dict(mentor._pending_directions)
    saved_save = mentor._save
    mentor._pending_challenges = {}
    mentor._pending_directions = {}
    mentor._save = lambda *a, **k: None
    yield
    mentor._pending_challenges = saved_c
    mentor._pending_directions = saved_d
    mentor._save = saved_save


# ── DÉFIS (challenge) ──────────────────────────────────────────────────────
def test_catch_all_defi_ne_fuit_pas_dans_bulletin():
    """Le cœur du fix : un défi générique '*' ne doit PAS polluer un BULLETIN,
    et doit rester disponible pour un slot de travail."""
    mentor._pending_challenges["*"] = "Implemente un logger asynchrone."
    assert mentor.consume_challenge(slot="BULLETIN") == ""
    assert mentor.consume_challenge(slot="CODE_REVIEW") == "Implemente un logger asynchrone."


def test_catch_all_defi_ne_fuit_pas_dans_free_time():
    mentor._pending_challenges["*"] = "Defi generique."
    assert mentor.consume_challenge(slot="FREE_TIME") == ""


def test_defi_cible_bulletin_est_bien_servi():
    """Non-régression : une directive EXPLICITEMENT ciblée BULLETIN reste servie."""
    mentor._pending_challenges["BULLETIN"] = "Bulletin demain : ouvre tel fichier."
    assert mentor.consume_challenge(slot="BULLETIN") == "Bulletin demain : ouvre tel fichier."


def test_non_regression_code_review_ramasse_le_catch_all():
    """Non-régression : les slots de travail gardent l'accès au catch-all."""
    mentor._pending_challenges["*"] = "Defi generique de travail."
    assert mentor.consume_challenge(slot="CODE_REVIEW") == "Defi generique de travail."


# ── DIRECTIONS (même trou) ─────────────────────────────────────────────────
def test_catch_all_direction_ne_fuit_pas_dans_bulletin():
    mentor._pending_directions["*"] = "Concentre-toi sur la performance."
    assert mentor.consume_direction(slot="BULLETIN") == ""
    assert mentor.consume_direction(slot="WORKSHOP") == "Concentre-toi sur la performance."


def test_direction_ciblee_bulletin_est_servie():
    mentor._pending_directions["BULLETIN"] = "Bulletin : sois honnete sur tes echecs."
    assert mentor.consume_direction(slot="BULLETIN") == "Bulletin : sois honnete sur tes echecs."
