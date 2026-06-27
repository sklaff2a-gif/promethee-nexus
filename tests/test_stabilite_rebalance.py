# -*- coding: utf-8 -*-
"""TDD — Rééquilibrage STABILITÉ (desire_engine, 24/06).

Règle A+B (en 'active') : la montée honore le TEMPO (slow ×0.5) ET pas de ×1.5 frustration pour slow.
SHADOW (défaut) : un counterfactuel _stab_shadow_depriv évolue sous la règle A+B + reçoit les MÊMES
satisfactions/frustrations -> mesure la trajectoire sans toucher le réel. NE TOUCHE PAS DIP/refractory/tolérance.
"""
import time

import pytest

import core.desire_engine as de


@pytest.fixture(autouse=True)
def _setup(monkeypatch, tmp_path):
    e = de.desires
    monkeypatch.setattr(de, "_STAB_REBALANCE_SHADOW_LOG", str(tmp_path / "sr.jsonl"))
    monkeypatch.setattr(e, "_get_traits_avg", lambda: {})   # résonance PSYCHE neutre (déterministe)
    e._stab_rebalance_last_log = 0.0
    yield


def _rise_stab(e, mode, deprivation=40.0, streak=0, monkeypatch=None):
    """Fait monter STABILITÉ d'1h sous `mode` et retourne le delta réel."""
    de.STABILITE_REBALANCE_MODE = mode
    e.drives["STABILITE"].deprivation = deprivation
    e.drives["STABILITE"].frustration_streak = streak
    e._stab_shadow_depriv = deprivation
    e._last_tick = time.time() - 3600.0
    e.tick()
    return e.drives["STABILITE"].deprivation - deprivation


# ── Levier A : tempo-aware rise ────────────────────────────────────────────
def test_active_tempo_ralentit_stabilite():
    e = de.desires
    off = _rise_stab(e, "off")
    act = _rise_stab(e, "active")
    assert act == pytest.approx(off * 0.5, rel=0.02)   # slow ×0.5
    de.STABILITE_REBALANCE_MODE = "shadow"


# ── Levier B : cap du ×1.5 frustration pour slow ───────────────────────────
def test_active_casse_le_cercle_vicieux_frustration():
    e = de.desires
    act_calme = _rise_stab(e, "active", streak=0)
    act_frustre = _rise_stab(e, "active", streak=5)
    assert act_frustre == pytest.approx(act_calme, rel=0.02)   # PAS de ×1.5 en active/slow
    off_frustre = _rise_stab(e, "off", streak=5)
    assert off_frustre > act_frustre   # en off, le ×1.5 s'applique (cercle vicieux préservé)
    de.STABILITE_REBALANCE_MODE = "shadow"


# ── SHADOW : le counterfactuel monte moins vite que le réel ────────────────
def test_shadow_trajectoire_plus_basse():
    e = de.desires
    de.STABILITE_REBALANCE_MODE = "shadow"
    e.drives["STABILITE"].deprivation = 50.0
    e.drives["STABILITE"].frustration_streak = 0
    e._stab_shadow_depriv = 50.0
    e._last_tick = time.time() - 3600.0
    e.tick()
    assert e.drives["STABILITE"].deprivation == pytest.approx(53.0, abs=0.3)   # réel : +3.0 (ancienne règle)
    assert e._stab_shadow_depriv == pytest.approx(51.5, abs=0.3)               # shadow : +1.5 (règle A+B)
    assert e._stab_shadow_depriv < e.drives["STABILITE"].deprivation


# ── Le shadow reçoit les mêmes satisfactions/frustrations ──────────────────
def test_mirror_satisfaction():
    e = de.desires
    de.STABILITE_REBALANCE_MODE = "shadow"
    e._stab_shadow_depriv = 60.0
    e._mirror_stab_shadow("STABILITE", -10.0)
    assert e._stab_shadow_depriv == 50.0
    e._mirror_stab_shadow("CURIOSITE", -10.0)   # no-op hors STABILITÉ
    assert e._stab_shadow_depriv == 50.0


def test_mirror_off_inerte():
    e = de.desires
    de.STABILITE_REBALANCE_MODE = "off"
    e._stab_shadow_depriv = 60.0
    e._mirror_stab_shadow("STABILITE", -10.0)
    assert e._stab_shadow_depriv == 60.0
    de.STABILITE_REBALANCE_MODE = "shadow"


# ── Persistance ────────────────────────────────────────────────────────────
def test_persistance_round_trip(monkeypatch, tmp_path):
    e = de.desires
    monkeypatch.setattr(de, "STATE_FILE", str(tmp_path / "desire.json"))
    e._stab_shadow_depriv = 42.0
    e.save()
    e._stab_shadow_depriv = 99.0
    e._load()
    assert e._stab_shadow_depriv == 42.0


def test_defaut_active():
    # 27/06 : promu shadow -> active. Le défaut module (os.getenv) est 'active'.
    import os
    assert os.getenv("STABILITE_REBALANCE_MODE", "active") == "active"
