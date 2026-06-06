# -*- coding: utf-8 -*-
"""Tests V22.0 — WORKSHOP ex-nihilo (verrou famine WORKSHOP).

Le fallback "ameliorer un fichier au hasard" collait un target aleatoire ->
audit AST -> veto systematique (meme sur note 8.0) -> puits de famine. Remplace
par un atelier de code ex-nihilo (target_file="") qui route vers
compute_code_factuality (branche V20.0 enfin atteignable). Le cas spec evolution
(target legitime) reste inchange.
"""
import pytest
import core.evolution_catalog as ec
from core.school_schedule import SchoolSchedule, SLOT_WORKSHOP


@pytest.fixture
def sched(monkeypatch):
    s = SchoolSchedule()
    # playground (jeu Physics) deja consomme -> on est en mode "vrai WORKSHOP"
    s._playground_done_tonight = True

    # par defaut : AUCUNE spec evolution -> on doit tomber sur l'ex-nihilo
    class _EmptyCat:
        def get_all_specs(self):
            return []
    monkeypatch.setattr(ec, "EvolutionCatalog", _EmptyCat)
    return s


def test_workshop_sans_spec_est_ex_nihilo(sched):
    subj = sched.get_subject_for_slot(SLOT_WORKSHOP)
    # plus de target aleatoire : ex-nihilo -> route vers compute_code_factuality
    assert subj["target_file"] == "", f"target devrait etre vide, recu: {subj['target_file']!r}"
    assert "ATELIER" in subj["topic"]
    assert "code" in subj["topic"].lower()


def test_workshop_sans_spec_plus_de_ameliorer_aleatoire(sched):
    # le mot-cle du puits de famine ne doit plus apparaitre
    subj = sched.get_subject_for_slot(SLOT_WORKSHOP)
    assert "Ameliorer :" not in subj["topic"]


def test_workshop_avec_spec_garde_son_target(sched, monkeypatch):
    # une spec evolution legitime -> on garde le target_file (audit AST justifie)
    class _Cat:
        def get_all_specs(self):
            return [{"status": "approved", "title": "Fix X", "target_file": "core/foo.py"}]
    monkeypatch.setattr(ec, "EvolutionCatalog", _Cat)
    subj = sched.get_subject_for_slot(SLOT_WORKSHOP)
    assert subj["target_file"] == "core/foo.py"
    assert "Implementer" in subj["topic"]


def test_workshop_playground_intact(monkeypatch):
    # le 1er WORKSHOP/jour reste le jeu Physics (is_playground), on n'y touche pas
    s = SchoolSchedule()
    s._playground_done_tonight = False
    subj = s.get_subject_for_slot(SLOT_WORKSHOP)
    assert subj.get("is_playground") is True
    assert subj["target_file"] == ""
