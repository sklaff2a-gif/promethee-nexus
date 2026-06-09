# -*- coding: utf-8 -*-
"""TDD du fix-metrique-q en SHADOW (atelier protocole Sakana). _quality_substance_shadow
calcule un score de SUBSTANCE discriminant (diversite + concretude + structure + longueur),
en lecture seule, pour mesurer si une metrique non-aveugle est possible la ou q sature."""
from core.autonomy_engine import AutonomyEngine


def _eng():
    return AutonomyEngine.__new__(AutonomyEngine)


def test_discrimine_riche_vs_pauvre():
    eng = _eng()
    riche = {"result": ("Analyse de securite : 12 fichiers audites, 3 vulnerabilites "
                        "(CVE-2024-1, CVE-2024-2). Le module AsyncTaskManager presente 5 fuites.\n"
                        "Recommandations :\n- corriger le scheduler\n- patcher le buffer\n"
                        "- ajouter 42 cas de test sur ThreadPool")}
    pauvre = {"result": "ok " * 25}  # repetitif, zero substance
    sr = eng._quality_substance_shadow(riche, "SECURITY_AUDIT")
    sp = eng._quality_substance_shadow(pauvre, "VEILLE_SILENCIEUSE")
    assert sr > sp                 # le riche discrimine vers le haut
    assert 0.0 <= sp <= 1.0 and 0.0 <= sr <= 1.0

def test_court_ou_vide_zero():
    assert _eng()._quality_substance_shadow({"result": "ok"}, "X") == 0.0
    assert _eng()._quality_substance_shadow({"result": ""}, "X") == 0.0

def test_response_non_dict_zero():
    assert _eng()._quality_substance_shadow(None, "X") == 0.0

def test_kill_switch(monkeypatch):
    monkeypatch.setenv("QUALITY_SHADOW_ENABLED", "0")
    assert _eng()._quality_substance_shadow({"result": "x " * 100}, "X") is None

def test_score_borne_0_1():
    s = _eng()._quality_substance_shadow({"result": "mot " * 300 + " 123 456 ABC Def Ghi"}, "X")
    assert 0.0 <= s <= 1.0

def test_log_shadow_off_ne_ecrit_pas_et_ne_crashe_pas(monkeypatch):
    monkeypatch.setenv("QUALITY_SHADOW_ENABLED", "0")
    # shadow=None (kill-switch) -> retour anticipe, aucun crash
    _eng()._log_quality_shadow("X", "agent", 1.0, {"result": "y " * 100})
