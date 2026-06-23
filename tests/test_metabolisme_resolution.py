# -*- coding: utf-8 -*-
"""TDD — Métabolisme de la Résolution (desire_engine, chantier endurance #3, 23/06).

La satisfaction d'une pulsion est MODULÉE par une qualité de résolution q_res lue sur des signaux
déjà calculés (verdict sonde de contradiction, note, stérilité council) — 0 LLM. Vraie résolution →
plein drop ; coquille vide/stérile → drop atténué (la faim demeure). SHADOW par défaut (bit-identique).
Plancher anti-famine Q_RES_FLOOR. Ne touche ni le DIP, ni measure_tension, ni refractory/tolérance.
"""
import pytest

import core.desire_engine as de


# ── q_res (fonction pure, 0 LLM) ───────────────────────────────────────────
def test_q_res_neutre_sans_signal():
    assert de.resolution_quality("X", {}) == 1.0


def test_q_res_ancre_plein():
    assert de.resolution_quality("X", {"verdict": "ancre"}) == 1.0


def test_q_res_instable_attenue():
    assert de.resolution_quality("X", {"verdict": "instable"}) == 0.5


def test_q_res_note_basse_vers_plancher():
    # note 2/10 → 2/5 = 0.4 (= plancher)
    assert de.resolution_quality("X", {"grade": 2.0}) == pytest.approx(0.4)


def test_q_res_note_haute_pas_de_penalite():
    assert de.resolution_quality("X", {"grade": 9.0}) == 1.0


def test_q_res_quality_routine_normalisee():
    # quality 0.2 (0-1) → 2.0/10 → 0.4
    assert de.resolution_quality("X", {"quality": 0.2}) == pytest.approx(0.4)


def test_q_res_plancher_jamais_zero():
    assert de.resolution_quality("X", {"grade": 0.0}) == de.Q_RES_FLOOR


def test_q_res_sterile_council():
    assert de.resolution_quality("X", {"sterile": True}) == 0.5


def test_q_res_cumul_prend_le_min():
    # instable (0.5) ET note 2/10 (0.4 plancher) → min = 0.4
    assert de.resolution_quality("X", {"verdict": "instable", "grade": 2.0}) == pytest.approx(0.4)


def test_q_res_borg_lecture_ratee():
    # grade illisible → exception → 1.0 (jamais de pénalité sur un doute)
    assert de.resolution_quality("X", {"grade": "abc"}) == 1.0


# ── modulation dans on_event ───────────────────────────────────────────────
def _engine_drive(monkeypatch, mode, tmp_path):
    monkeypatch.setattr(de, "METABOLISME_RESOLUTION_MODE", mode)
    monkeypatch.setattr(de, "_METAB_RES_SHADOW_LOG", str(tmp_path / "mr.jsonl"))
    eng = de.DesireEngine()
    monkeypatch.setattr(eng, "_resolve_impacts", lambda et, ctx: {"CREATION": -10.0})
    d = eng.drives["CREATION"]
    d.deprivation = 50.0          # < bypass(80) → voie tolérance
    d.last_satisfied = 0.0        # pas de refractory
    d.tolerance_accumulator = 0.0 # tolérance = 1.0
    return eng, d


def test_shadow_bit_identique(monkeypatch, tmp_path):
    """SHADOW : la satisfaction applique le delta ORIGINAL (50 → 40), malgré q_res=0.5."""
    eng, d = _engine_drive(monkeypatch, "shadow", tmp_path)
    eng.on_event("ARTIFACT_CREATED", {"verdict": "instable"})
    assert d.deprivation == pytest.approx(40.0)   # drop plein de 10


def test_active_module_la_satisfaction(monkeypatch, tmp_path):
    """ACTIVE : q_res=0.5 → drop atténué (50 → 45), la faim demeure."""
    eng, d = _engine_drive(monkeypatch, "active", tmp_path)
    eng.on_event("ARTIFACT_CREATED", {"verdict": "instable"})
    assert d.deprivation == pytest.approx(45.0)   # drop de 5 seulement


def test_active_resolution_reelle_plein_drop(monkeypatch, tmp_path):
    """ACTIVE mais q_res=1.0 (ancré, note haute) → plein drop (50 → 40)."""
    eng, d = _engine_drive(monkeypatch, "active", tmp_path)
    eng.on_event("ARTIFACT_CREATED", {"verdict": "ancre", "grade": 9.0})
    assert d.deprivation == pytest.approx(40.0)


def test_off_aucune_modulation(monkeypatch, tmp_path):
    """OFF : aucune lecture q_res, drop plein, pas de log."""
    eng, d = _engine_drive(monkeypatch, "off", tmp_path)
    eng.on_event("ARTIFACT_CREATED", {"verdict": "instable"})
    assert d.deprivation == pytest.approx(40.0)
    import os
    assert not os.path.exists(str(tmp_path / "mr.jsonl"))


def test_defaut_shadow():
    assert de.METABOLISME_RESOLUTION_MODE == "shadow"
