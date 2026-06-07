# -*- coding: utf-8 -*-
"""TDD Chantier B — moteur d'anticipation frugale (operationnel)."""
import pytest
from anticipation_engine import anticipate, mirror, MAX_ANTICIPATION_RETRIES


def make_generator(sequence):
    """Faux agent : retourne les ebauches de `sequence` une par une (la derniere si epuise).
    Enregistre les (attempt, last_rejection) recus -> verifie l'Error Ingestion."""
    calls = []
    def gen(attempt, last_rejection):
        calls.append((attempt, last_rejection))
        return sequence[attempt - 1] if attempt - 1 < len(sequence) else sequence[-1]
    gen.calls = calls
    return gen


# --- le miroir deterministe ---
def test_mirror_accepte_code_valide():
    ok, rej = mirror("x = sum(range(10))")
    assert ok is True and rej is None

def test_mirror_rejette_syntaxe_et_donne_la_ligne():
    ok, rej = mirror("x = (1 + 2")
    assert ok is False
    assert "[PREFRONTAL_REJECTION]" in rej and "Line" in rej

def test_mirror_intercepte_construct_dangereux():
    ok, rej = mirror("y = eval('2+2')")
    assert ok is False and "eval" in rej


# --- la boucle prefrontale ---
def test_ebauche_valide_livree_immediatement_zero_cout():
    # FRUGALITE : regime nominal -> 1 tentative, 0 reorientation
    gen = make_generator(["resultat = sum(range(10))"])
    r = anticipate(gen)
    assert r["status"] == "delivered"
    assert r["attempts"] == 1
    assert r["rejections"] == []

def test_parenthese_orpheline_corrigee_au_second_coup():
    gen = make_generator(["x = (1 + 2", "x = (1 + 2)"])
    r = anticipate(gen)
    assert r["status"] == "delivered"
    assert r["attempts"] == 2
    assert len(r["rejections"]) == 1
    assert "[PREFRONTAL_REJECTION]" in r["rejections"][0]
    assert r["code"] == "x = (1 + 2)"

def test_indentation_corrompue_interceptee_puis_corrigee():
    gen = make_generator(["def f():\nx = 1", "def f():\n    x = 1"])
    r = anticipate(gen)
    assert r["status"] == "delivered" and r["attempts"] == 2

def test_double_echec_leve_veto_et_coupe_le_canal():
    gen = make_generator(["x = (", "y = )"])     # deux ebauches corrompues
    r = anticipate(gen)
    assert r["status"] == "veto"
    assert r["code"] is None
    assert "VETO" in r["reason"]
    assert r["attempts"] == MAX_ANTICIPATION_RETRIES

def test_error_ingestion_la_trace_est_reinjectee():
    gen = make_generator(["x = (", "x = 1"])
    anticipate(gen)
    # 1er appel : pas de rejection ; 2e appel : recoit la trace brute
    assert gen.calls[0][1] is None
    assert gen.calls[1][1] is not None
    assert "[PREFRONTAL_REJECTION]" in gen.calls[1][1] and "Line" in gen.calls[1][1]

def test_micro_lint_dangereux_puis_purifie():
    gen = make_generator(["resultat = eval('2+2')", "resultat = 2 + 2"])
    r = anticipate(gen)
    assert r["status"] == "delivered" and r["attempts"] == 2
    assert "interdit" in r["rejections"][0].lower()

def test_on_veto_callback_consigne_l_echec():
    logged = []
    gen = make_generator(["x = (", "y = )"])
    anticipate(gen, on_veto=lambda v: logged.append(v))
    assert len(logged) == 1 and logged[0]["status"] == "veto"

def test_interface_jamais_polluee_par_brouillon():
    # le code rejete n'est JAMAIS dans le livrable (None en cas de veto, propre en cas de succes)
    gen = make_generator(["bad syntax ((", "ok = 1"])
    r = anticipate(gen)
    assert r["code"] == "ok = 1"          # le brouillon defectueux est mort en stase
