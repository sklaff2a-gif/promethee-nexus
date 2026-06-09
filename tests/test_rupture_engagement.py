# -*- coding: utf-8 -*-
"""TDD du Signal d'Engagement de Rupture en SHADOW (atelier arbitrage protocole Sakana).
Tague les moments de RUPTURE et mesure leur valeur d'experience (substance), sans rien
elever du budget memoire reel. Lecture seule, kill-switch, borg."""
import json
from core.autonomy_engine import AutonomyEngine

RICHE = {"result": ("Exploration creative : 12 pistes, le paradoxe de Russell relie a CVE-2024-1, "
                    "le module AsyncTaskManager ouvre 5 voies nouvelles.\n- briser la symetrie\n"
                    "- questionner le fondement avec 42 contre-exemples\n- inventer une regle")}
PAUVRE = {"result": "ok " * 25}


def _eng():
    return AutonomyEngine.__new__(AutonomyEngine)


def _run(monkeypatch, tmp_path, intent, response):
    (tmp_path / "memory").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    _eng()._rupture_engagement_shadow(intent, response)
    p = tmp_path / "memory" / "rupture_engagement_shadow.jsonl"
    if not p.exists():
        return None
    lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return json.loads(lines[-1]) if lines else None


def test_rupture_taguee_avec_valeur_et_seuil(monkeypatch, tmp_path):
    e = _run(monkeypatch, tmp_path, "SCHOOL_CREATION", RICHE)
    assert e["is_rupture"] is True
    assert 0.0 <= e["valeur_experience"] <= 1.0
    # le drapeau d'elevation suit le seuil 0.4
    assert e["would_elevate_budget"] == (e["valeur_experience"] >= 0.4)

def test_riche_vaut_plus_que_pauvre(monkeypatch, tmp_path):
    er = _run(monkeypatch, tmp_path, "SCHOOL_CREATION", RICHE)
    ep = _run(monkeypatch, tmp_path, "SCHOOL_RESEARCH", PAUVRE)
    assert er["valeur_experience"] > ep["valeur_experience"]

def test_non_rupture_pas_de_log(monkeypatch, tmp_path):
    assert _run(monkeypatch, tmp_path, "MEMORY_CONSOLIDATION", RICHE) is None
    assert _run(monkeypatch, tmp_path, "SECURITY_AUDIT", RICHE) is None
    assert _run(monkeypatch, tmp_path, "VEILLE_SILENCIEUSE", RICHE) is None

def test_kill_switch(monkeypatch, tmp_path):
    monkeypatch.setenv("RUPTURE_ENGAGEMENT_ENABLED", "0")
    assert _run(monkeypatch, tmp_path, "SCHOOL_CREATION", RICHE) is None

def test_substance_coupee_pas_de_log(monkeypatch, tmp_path):
    # si la sonde substance est off, valeur=None -> pas de log (rupture reste active)
    monkeypatch.setenv("QUALITY_SHADOW_ENABLED", "0")
    assert _run(monkeypatch, tmp_path, "SCHOOL_CREATION", RICHE) is None
