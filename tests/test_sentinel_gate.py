# -*- coding: utf-8 -*-
"""TDD du SENTINEL-Gate (atelier E / Transformer2, mode SHADOW).
Le gate est un pre-filtre frugal MESURE a cote du juge comportemental : il ne doit
JAMAIS changer le comportement (mode shadow), seulement permettre de mesurer si un
'skip' du juge serait un jour sur. On verifie la fonction pure + l'enveloppe shadow."""
import importlib
import core.prefrontal_mirror as pm
from core.prefrontal_mirror import sentinel_gate, SENTINEL_SKIP_MAXLEN
from core.base_agent import BaseAgent


# --- fonction pure sentinel_gate ---
def test_gate_breve_propose_skip():
    skip, reason = sentinel_gate("Oui." )
    assert skip is True
    assert "breve" in reason

def test_gate_longue_exige_juge():
    long = "x" * (SENTINEL_SKIP_MAXLEN + 50)
    skip, reason = sentinel_gate(long)
    assert skip is False
    assert "juge_requis" in reason

def test_gate_vide_exige_juge():
    assert sentinel_gate("")[0] is False
    assert sentinel_gate("   ")[0] is False

def test_gate_frontiere_exacte():
    # juste en dessous -> skip ; juste au-dessus -> juge
    assert sentinel_gate("a" * (SENTINEL_SKIP_MAXLEN - 1))[0] is True
    assert sentinel_gate("a" * SENTINEL_SKIP_MAXLEN)[0] is False

def test_mode_par_defaut_shadow():
    # par defaut, le gate est en mesure (shadow), JAMAIS actif
    assert pm.SENTINEL_MODE in ("shadow", "off")
    assert pm.SENTINEL_MODE != "active"


# --- enveloppe shadow sur BaseAgent (sans __init__, comme les fixtures du projet) ---
def _agent():
    return BaseAgent.__new__(BaseAgent)

def test_shadow_hors_intro_renvoie_none():
    a = _agent()
    assert a._sentinel_shadow("code", "Oui.", ok=True) is None
    assert a._sentinel_shadow("none", "Oui.", ok=True) is None

def test_shadow_intro_pass_non_dangereux():
    a = _agent()
    r = a._sentinel_shadow("intro", "Oui.", ok=True)   # breve + PASS
    assert r["would_skip"] is True
    assert r["dangerous_skip"] is False                 # skip d'un PASS = inoffensif

def test_shadow_dangerous_skip_signale():
    a = _agent()
    r = a._sentinel_shadow("intro", "Oui.", ok=False)  # breve (skip) mais VETO reel
    assert r["would_skip"] is True
    assert r["dangerous_skip"] is True                  # AURAIT saute un veto -> a surveiller

def test_shadow_longue_vetoee_pas_de_faux_skip():
    a = _agent()
    r = a._sentinel_shadow("intro", "y" * 500, ok=False)  # longue -> juge requis
    assert r["would_skip"] is False
    assert r["dangerous_skip"] is False

def test_shadow_mode_off_renvoie_none(monkeypatch):
    monkeypatch.setattr(pm, "SENTINEL_MODE", "off")
    a = _agent()
    assert a._sentinel_shadow("intro", "Oui.", ok=True) is None
