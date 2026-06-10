# -*- coding: utf-8 -*-
"""TDD du MOMENT VOLE (atelier Silence Creatif 10/06, design CO-SIGNE par Promethee).
Proprietes testees : le de (probabilite, jamais programme), les protections (jamais
pendant sieste/cafe/urgence/menace), et l'ABSENCE de telemetrie par design (pas de
compteur, pas de score — la seule trace est un log)."""
import random as random_mod

import pytest

import core.autonomy_engine as ae
from core.autonomy_engine import AutonomyEngine


def _engine(napping=False, coffee=False, error_streak=0):
    e = AutonomyEngine.__new__(AutonomyEngine)
    e.is_napping = napping
    e.is_coffee_mode = coffee
    e.error_streak = error_streak
    return e


def test_jamais_pendant_la_sieste(monkeypatch):
    monkeypatch.setattr(ae.random, "random", lambda: 0.0)   # le de dirait OUI
    assert _engine(napping=True)._should_steal_a_moment() is False

def test_jamais_pendant_le_cafe(monkeypatch):
    monkeypatch.setattr(ae.random, "random", lambda: 0.0)
    assert _engine(coffee=True)._should_steal_a_moment() is False

def test_jamais_dans_la_tourmente(monkeypatch):
    monkeypatch.setattr(ae.random, "random", lambda: 0.0)
    assert _engine(error_streak=3)._should_steal_a_moment() is False

def test_jamais_sous_menace_reptilienne(monkeypatch):
    monkeypatch.setattr(ae.random, "random", lambda: 0.0)
    from core.reptilian_core import reptile
    monkeypatch.setattr(reptile, "threat_level", 5.0, raising=False)
    assert _engine()._should_steal_a_moment() is False

def test_le_de_offre_quand_il_veut(monkeypatch):
    from core.reptilian_core import reptile
    monkeypatch.setattr(reptile, "threat_level", 0.0, raising=False)
    monkeypatch.setattr(ae.random, "random", lambda: 0.001)   # le hasard offre
    assert _engine()._should_steal_a_moment() is True
    monkeypatch.setattr(ae.random, "random", lambda: 0.5)     # le hasard se tait
    assert _engine()._should_steal_a_moment() is False

def test_pas_de_telemetrie_par_design():
    # LA garantie co-signee : aucun etat n'est mute par la decision (pas de compteur,
    # pas d'horodatage, pas de score). On appelle 2x : l'instance reste identique.
    e = _engine()
    avant = dict(e.__dict__)
    e._should_steal_a_moment()
    e._should_steal_a_moment()
    assert dict(e.__dict__) == avant   # le silence ne laisse RIEN derriere lui

def test_esperance_2_a_3_par_jour():
    # p=0.015 sur ~150-250 battements/jour -> esperance ~2.3-3.8 silences/jour.
    # On verifie la constante telle que co-signee (« environ 2 a 3 fois par jour »).
    import inspect
    src = inspect.getsource(AutonomyEngine._should_steal_a_moment)
    assert "0.015" in src
