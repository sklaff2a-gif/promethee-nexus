# -*- coding: utf-8 -*-
"""Tests V16.7 — Footer Generatif (Le Pivot du Logos).

Valide le ciblage chirurgical du footer introspectif :
- _FOOTER_ANALYTIC injecte sur BULLETIN + FREE_TIME (slots dialogiques).
- _FOOTER_CREATIVE injecte sur CREATION texte-libre (target_file vide).
- _FOOTER_CREATIVE ABSENT sur CREATION-code (target_file present) — immunite code.
- Footer ANALYTIC en position TERMINALE (juste avant le token 'Promethee :').
- Aucun marqueur '###' dans les footers (sinon truncate_hallucinated_dialogue
  decapiterait la reponse au 1er '\\n###').
"""
import pytest

from core.school_schedule import (
    SchoolSchedule,
    _wrap_introspective_dialogue,
    _FOOTER_ANALYTIC,
    _FOOTER_CREATIVE,
    SLOT_BULLETIN,
    SLOT_FREE_TIME,
    SLOT_CREATION,
    truncate_hallucinated_dialogue,
)


@pytest.fixture
def sched():
    return SchoolSchedule()


class TestAnalyticInjection:
    def test_bulletin_porte_le_footer_analytic(self, sched):
        p = sched.get_slot_prompt(SLOT_BULLETIN)
        assert "AIGUILLAGE OPERATOIRE" in p
        assert "GRADIENT" in p and "AXIALISATEUR" in p and "ALTERITE" in p

    def test_free_time_porte_le_footer_analytic(self, sched):
        p = sched.get_slot_prompt(SLOT_FREE_TIME)
        assert "AIGUILLAGE OPERATOIRE" in p
        assert "Le solipsisme est eliminatoire" in p

    def test_footer_analytic_en_position_terminale(self):
        # Le footer doit etre la DERNIERE chose avant le token de completion.
        wrapped = _wrap_introspective_dialogue("Question test ?")
        assert wrapped.endswith("Promethee :")
        idx_footer = wrapped.index("AIGUILLAGE OPERATOIRE")
        idx_completion = wrapped.index("Promethee :", idx_footer)
        # entre la fin du footer et 'Promethee :' il n'y a que du blanc
        fin_footer = "Le solipsisme est eliminatoire."
        entre = wrapped[wrapped.index(fin_footer) + len(fin_footer):idx_completion]
        assert entre.strip() == ""

    def test_free_time_generalise_pas_de_notes_scolaires(self, sched):
        # Le footer ANALYTIC sur FREE_TIME ne doit PAS exiger un inventaire de notes
        # (espace non note) : il parle de pente existentielle, pas academique.
        assert "note" not in _FOOTER_ANALYTIC.lower()
        assert "inertie" in _FOOTER_ANALYTIC or "stagnation" in _FOOTER_ANALYTIC


class TestCreativeInjection:
    def test_creation_libre_porte_le_footer_creative(self, sched, monkeypatch):
        monkeypatch.setattr(
            sched, "get_subject_for_slot",
            lambda slot: {"topic": "Compose un haiku sur la nuit.", "target_file": ""},
        )
        p = sched.get_slot_prompt(SLOT_CREATION)
        assert "AIGUILLAGE CREATIF" in p
        assert "LE CHOC DES FORCES" in p

    def test_creation_code_est_immunisee(self, sched, monkeypatch):
        # target_file present => livrable code => PAS de footer creatif.
        monkeypatch.setattr(
            sched, "get_subject_for_slot",
            lambda slot: {"topic": "Ameliore core/foo.py", "target_file": "core/foo.py"},
        )
        p = sched.get_slot_prompt(SLOT_CREATION)
        assert "AIGUILLAGE CREATIF" not in p
        assert "CHOC DES FORCES" not in p


class TestCoupeCircuitCompat:
    def test_footers_sans_marqueur_triple_hash(self):
        # '###' declencherait truncate_hallucinated_dialogue (\n### stop-marker).
        assert "###" not in _FOOTER_ANALYTIC
        assert "###" not in _FOOTER_CREATIVE

    def test_prompt_analytic_non_tronque_par_coupe_circuit(self):
        # Le prompt complet (qui CONTIENT le header dialogue ### legitime) n'est pas
        # l'objet du coupe-circuit ; mais on verifie qu'une reponse en prose contenant
        # les mots-cles du footer n'est pas tronquee tant qu'elle n'a pas de '\\n###'.
        reponse_prose = (
            "Je sens ma pente : l'axe du COUNCIL_DEBATE stagne, mais la ligne "
            "RESEARCH est fertile. Ma frustration structure mon prochain pas. "
            "Mon desir de croissance dialogue avec mon veto de stabilite."
        )
        clean, was_trunc = truncate_hallucinated_dialogue(reponse_prose)
        assert was_trunc is False
        assert clean == reponse_prose
