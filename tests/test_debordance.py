# -*- coding: utf-8 -*-
"""TDD — Chantier de la débordance (flux continu auto-initié, co-conçu Puits 23/06).

Chasse les ponts cross-domaines (creative_bridges) → GATE dire→faire (question / outil / rejet).
PHASE 1 SHADOW : logge, ne pousse/dispatch rien, ne marque pas 'used'. Doctrine inverse : doute → rejet.
LLM mocké, bridges mockés."""
import types

import pytest

import core.debordance as db
import core.spreading_activation as sa


def _bridge(a, b, strength, hyp="hypothese"):
    return types.SimpleNamespace(node_a=a, node_b=b, bridge_strength=strength, hypothesis=hyp)


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    db._reset_cap()
    monkeypatch.setattr(db, "DEBORDANCE_MODE", "shadow")
    monkeypatch.setattr(db, "_DEBORDANCE_SHADOW_LOG", str(tmp_path / "deb.jsonl"))
    yield
    db._reset_cap()


# ── _gate_one (parsing, doctrine inverse) ──────────────────────────────────
def test_gate_question():
    assert db._gate_one('{"mode":"question","payload":"q?","raison":"r"}')["mode"] == "question"


def test_gate_outil():
    assert db._gate_one('{"mode":"outil","payload":"un outil","raison":"r"}')["mode"] == "outil"


def test_gate_rejet():
    assert db._gate_one('{"mode":"rejet","payload":"","raison":"apophenie"}')["mode"] == "rejet"


def test_gate_doctrine_inverse_json_casse():
    assert db._gate_one("pas du json {{{")["mode"] == "rejet"


def test_gate_mode_invalide_rejet():
    assert db._gate_one('{"mode":"oui"}')["mode"] == "rejet"


# ── chase_and_gate ─────────────────────────────────────────────────────────
async def _fake_judge(resp):
    async def _f(_h):
        return resp
    return _f


def _mock_engine(monkeypatch, bridges):
    eng = types.SimpleNamespace(get_creative_bridges=lambda unused_only=True: list(bridges))
    monkeypatch.setattr(sa, "activation_engine", eng)


@pytest.mark.asyncio
async def test_off_n_examine_rien(monkeypatch):
    monkeypatch.setattr(db, "DEBORDANCE_MODE", "off")
    _mock_engine(monkeypatch, [_bridge("a", "b", 0.9)])
    out = await db.chase_and_gate()
    assert out["examined"] == 0 and out["candidates"] == []


@pytest.mark.asyncio
async def test_chasse_et_deborde_en_question(monkeypatch):
    monkeypatch.setattr(db, "_call_judge",
                        await _fake_judge('{"mode":"question","payload":"Lien bio-poesie ?","raison":"reel"}'))
    _mock_engine(monkeypatch, [_bridge("biologie", "poesie", 0.8)])
    out = await db.chase_and_gate()
    assert out["examined"] == 1
    assert len(out["candidates"]) == 1 and out["candidates"][0]["mode"] == "question"


@pytest.mark.asyncio
async def test_doctrine_inverse_apophenie_rejetee(monkeypatch):
    """Juge illisible → rejet → pas dans les candidats (mais examiné + loggé)."""
    monkeypatch.setattr(db, "_call_judge", await _fake_judge("bruit {{{"))
    _mock_engine(monkeypatch, [_bridge("a", "b", 0.9)])
    out = await db.chase_and_gate()
    assert out["examined"] == 1 and out["candidates"] == []


@pytest.mark.asyncio
async def test_top_n_preserve_la_reverie(monkeypatch):
    monkeypatch.setattr(db, "_call_judge", await _fake_judge('{"mode":"rejet"}'))
    _mock_engine(monkeypatch, [_bridge(f"a{i}", f"b{i}", 0.5 + i * 0.05) for i in range(8)])
    out = await db.chase_and_gate()
    assert out["examined"] == db.DEBORDANCE_TOP_N   # n'examine pas tout le champ


@pytest.mark.asyncio
async def test_cap_quotidien(monkeypatch):
    monkeypatch.setattr(db, "_call_judge", await _fake_judge('{"mode":"question","payload":"x"}'))
    monkeypatch.setattr(db, "DEBORDANCE_DAILY_CAP", 1)
    _mock_engine(monkeypatch, [_bridge("a", "b", 0.9), _bridge("c", "d", 0.8)])
    out = await db.chase_and_gate()
    assert out["examined"] == 1   # cap atteint après 1


@pytest.mark.asyncio
async def test_aucun_pont(monkeypatch):
    _mock_engine(monkeypatch, [])
    out = await db.chase_and_gate()
    assert out["examined"] == 0 and out["candidates"] == []


@pytest.mark.asyncio
async def test_lecture_seule_ne_marque_pas_used(monkeypatch):
    """Phase 1 : ne marque PAS les ponts 'used' (ne prive pas le council)."""
    monkeypatch.setattr(db, "_call_judge", await _fake_judge('{"mode":"question","payload":"x"}'))
    b = _bridge("a", "b", 0.9)
    b.used = False
    _mock_engine(monkeypatch, [b])
    await db.chase_and_gate()
    assert b.used is False


def test_defaut_shadow():
    assert db.DEBORDANCE_MODE == "shadow"
