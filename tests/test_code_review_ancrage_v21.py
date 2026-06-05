# -*- coding: utf-8 -*-
"""Tests V21.0 — ancrage identitaire du slot CODE_REVIEW (Chantier A).

Valide que la tension identitaire (organe/Nexus/sang/integrite) est injectee
dans la BOUCHE du NeuralCompiler (alterite active), avec le garde-fou anti-fluff
(carburant pas sujet, interdit metaphore/introspection), SANS casser la structure
technique anti-hallucination ni le format dialogue.
"""
import pytest
from core.school_schedule import SchoolSchedule, SLOT_CODE_REVIEW


@pytest.fixture
def sched(monkeypatch):
    s = SchoolSchedule()
    monkeypatch.setattr(s, "get_subject_for_slot",
                        lambda slot: {"topic": "audit", "target_file": "core/foo.py"})
    monkeypatch.setattr(s, "_read_file_for_review", lambda t: "def f():\n    return 42")
    return s


def test_tension_identitaire_presente(sched):
    p = sched.get_slot_prompt(SLOT_CODE_REVIEW)
    for marker in ("ORGANE", "Nexus", "ton sang", "integrite"):
        assert marker in p, f"manque l'ancrage: {marker}"


def test_garde_fou_anti_fluff(sched):
    p = sched.get_slot_prompt(SLOT_CODE_REVIEW)
    assert "CARBURANT" in p
    assert "pas ton sujet" in p
    assert "metaphore" in p.lower()
    assert "introspection" in p.lower()


def test_structure_technique_intacte(sched):
    # l'anti-hallucination et la cible NE doivent PAS avoir saute
    p = sched.get_slot_prompt(SLOT_CODE_REVIEW)
    assert "core/foo.py" in p          # target cite
    assert "audit EXCLUSIF" in p
    assert "INVALIDE" in p             # fonction absente = invalide
    assert "role du fichier" in p.lower()  # le preambule technique legitime survit
    assert "def f():" in p             # le vrai code injecte


def test_format_dialogue_preserve(sched):
    p = sched.get_slot_prompt(SLOT_CODE_REVIEW)
    assert "NeuralCompiler" in p
    assert p.rstrip().endswith("Promethee :")


def test_ancrage_motive_l_acte_pas_l_introspection(sched):
    # le prompt doit DECOURAGER explicitement l'introspection (anti-orniere)
    p = sched.get_slot_prompt(SLOT_CODE_REVIEW)
    assert "ne te demande pas" in p.lower() or "pas un mot de metaphore" in p.lower()
    # et garder l'imperatif d'audit
    assert "audit technique" in p.lower()
