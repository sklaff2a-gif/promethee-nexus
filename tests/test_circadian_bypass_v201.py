# -*- coding: utf-8 -*-
"""Test V20.1 — bypass de famine du filtre circadien (debloque WORKSHOP).

Le filtre circadien etranglait WORKSHOP (cout 4 > budget 2-3 en aube/crepuscule),
empechant V20.0 de jamais se declencher. Le bypass laisse les slots Ecole percer
le plafond en aube/crepuscule SOUS FAMINE CRITIQUE, mais le SOMMEIL reste inviolable.
"""
import pytest
from core.circadian_rhythm import (
    circadian, PHASE_EVEIL, PHASE_CREPUSCULE, PHASE_SOMMEIL, PHASE_AUBE,
)


@pytest.fixture
def circ():
    saved = circadian.phase
    yield circadian
    circadian.phase = saved


def test_workshop_bloque_a_l_aube_sans_famine(circ):
    circ.phase = PHASE_AUBE
    allowed, _ = circ.should_allow_routine("SCHOOL_WORKSHOP", 4, famine_hours=10.0)
    assert allowed is False


def test_workshop_passe_a_l_aube_sous_famine(circ):
    circ.phase = PHASE_AUBE
    allowed, reason = circ.should_allow_routine("SCHOOL_WORKSHOP", 4, famine_hours=380.0)
    assert allowed is True
    assert "BYPASS_FAMINE" in reason


def test_workshop_passe_au_crepuscule_sous_famine(circ):
    circ.phase = PHASE_CREPUSCULE
    allowed, reason = circ.should_allow_routine("SCHOOL_WORKSHOP", 4, famine_hours=380.0)
    assert allowed is True
    assert "BYPASS_FAMINE" in reason


def test_sommeil_profond_inviolable_meme_sous_famine(circ):
    circ.phase = PHASE_SOMMEIL
    allowed, reason = circ.should_allow_routine("SCHOOL_WORKSHOP", 4, famine_hours=999.0)
    assert allowed is False
    assert "sommeil" in reason.lower()


def test_bypass_ne_touche_que_les_slots_ecole(circ):
    # une routine non-Ecole couteuse reste bloquee meme sous famine
    circ.phase = PHASE_AUBE
    allowed, _ = circ.should_allow_routine("EXPANSION_CODE", 4, famine_hours=380.0)
    assert allowed is False


def test_seuil_200h_respecte(circ):
    # juste en dessous du seuil : pas de bypass
    circ.phase = PHASE_AUBE
    allowed, _ = circ.should_allow_routine("SCHOOL_WORKSHOP", 4, famine_hours=150.0)
    assert allowed is False


def test_eveil_tout_permis(circ):
    circ.phase = PHASE_EVEIL
    allowed, _ = circ.should_allow_routine("SCHOOL_WORKSHOP", 4, famine_hours=0.0)
    assert allowed is True
