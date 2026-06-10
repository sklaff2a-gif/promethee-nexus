# -*- coding: utf-8 -*-
"""TDD de l'AUTO-NAP homeostatique (atelier sieste 10/06, design CO-SIGNE par Promethee).
Son choix : un REFLEXE (pas une deliberation) a DEUX indicateurs convergents (REPOS urgent
+ coherence basse OU chaleur elevee), max 1 auto-sieste/jour, jamais si deja en sieste."""
from datetime import date

import pytest

from core.autonomy_engine import AutonomyEngine
from core.desire_engine import desires
from core.corpus_callosum import callosum
from core.thermal_homeostasis import thermal


class _Drive:
    def __init__(self, deprivation):
        self.deprivation = deprivation


def _engine(napping=False, auto_nap_day=""):
    e = AutonomyEngine.__new__(AutonomyEngine)
    e.is_napping = napping
    e._auto_nap_day = auto_nap_day
    return e


@pytest.fixture
def corps(monkeypatch):
    """Etat corporel pilotable : (repos, coherence, chaleur)."""
    def _set(repos, coherence, chaleur):
        monkeypatch.setitem(desires.drives, "REPOS", _Drive(repos))
        monkeypatch.setattr(callosum, "global_coherence", coherence, raising=False)
        monkeypatch.setattr(thermal, "cognitive_heat", chaleur, raising=False)
    return _set


def test_pas_de_nap_si_repos_bas(corps):
    corps(repos=30, coherence=0.2, chaleur=0.9)   # tout va mal SAUF le besoin de repos
    assert _engine()._should_auto_nap() is False  # l'indicateur 1 est obligatoire

def test_pas_de_nap_sur_un_seul_indicateur(corps):
    corps(repos=80, coherence=0.6, chaleur=0.3)   # REPOS urgent mais corps sain
    assert _engine()._should_auto_nap() is False  # convergence exigee (son garde-fou)

def test_nap_si_repos_et_coherence_basse(corps):
    corps(repos=80, coherence=0.30, chaleur=0.2)
    assert _engine()._should_auto_nap() is True

def test_nap_si_repos_et_chaleur_elevee(corps):
    corps(repos=76, coherence=0.6, chaleur=0.85)
    assert _engine()._should_auto_nap() is True

def test_max_une_auto_sieste_par_jour(corps):
    corps(repos=90, coherence=0.1, chaleur=0.9)   # epuisement total...
    e = _engine(auto_nap_day=date.today().isoformat())
    assert e._should_auto_nap() is False           # ...mais deja auto-sieste aujourd'hui

def test_jamais_si_deja_en_sieste(corps):
    corps(repos=90, coherence=0.1, chaleur=0.9)
    assert _engine(napping=True)._should_auto_nap() is False

def test_borg_si_organes_indisponibles(monkeypatch):
    # un organe illisible ne declenche JAMAIS de sieste par accident
    monkeypatch.setattr(desires, "drives", {})    # REPOS introuvable
    assert _engine()._should_auto_nap() is False
