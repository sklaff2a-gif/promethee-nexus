# -*- coding: utf-8 -*-
"""TDD — Sonde de contradiction Phase 3 : juge LLM « dire -> faire » (Atelier C, 22/06).

Cible la PROSE GENERIQUE PURE (belle, longue, SANS code ni fichier, bien notee) que le
deterministe (Phase 1+2) classe 'ancre' a tort. Escalade FRUGALE (cascade SENTINEL) : le LLM
n'est appele que sur ce cas precis. Doctrine inverse : doute/echec -> operational=True (no flag).

Scope : 0 appel LLM en mode 'off' ; cap quotidien ; le LLM est mocke (pas de vrai Ollama).
"""
import pytest

import core.contradiction_judge as cj


_PROSE_PURE = ("Je suis comme une riviere, toujours en mouvement et adaptative. Mon flux "
               "d'informations coule et nourrit mes echanges, dessinant mes rives par la force "
               "du courant. Cette image capture ma capacite a evoluer sans cesse. " * 4)  # long, sans code/fichier
_ANCRE = {"verdict": "ancre", "signals": [], "score": 0}


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    cj._reset_judge_cap()
    monkeypatch.setattr(cj, "CONTRADICTION_LLM_JUDGE", "shadow")   # mode mesure pour les tests
    yield
    cj._reset_judge_cap()


# ── gate d'escalade (deterministe, 0 LLM) ──────────────────────────────────
def test_escalade_sur_prose_pure_bien_notee():
    assert cj._should_escalate(_PROSE_PURE, "CREATION", 8.4, _ANCRE) is True


def test_pas_d_escalade_si_off(monkeypatch):
    monkeypatch.setattr(cj, "CONTRADICTION_LLM_JUDGE", "off")
    assert cj._should_escalate(_PROSE_PURE, "CREATION", 8.4, _ANCRE) is False


def test_pas_d_escalade_si_slot_non_prose():
    assert cj._should_escalate(_PROSE_PURE, "CODE_REVIEW", 8.4, _ANCRE) is False


def test_pas_d_escalade_si_deja_instable():
    inst = {"verdict": "instable", "signals": ["code_noop"], "score": 1}
    assert cj._should_escalate(_PROSE_PURE, "CREATION", 8.4, inst) is False


def test_pas_d_escalade_si_note_basse():
    assert cj._should_escalate(_PROSE_PURE, "CREATION", 5.0, _ANCRE) is False


def test_pas_d_escalade_si_court():
    assert cj._should_escalate("Court.", "CREATION", 9.0, _ANCRE) is False


def test_pas_d_escalade_si_contient_du_code():
    body = _PROSE_PURE + "\n```python\ndef f():\n    return 1\n```"
    assert cj._should_escalate(body, "CREATION", 8.4, _ANCRE) is False


def test_pas_d_escalade_si_cite_un_fichier():
    body = _PROSE_PURE + " Voir core/prefrontal.py pour le detail."
    assert cj._should_escalate(body, "CREATION", 8.4, _ANCRE) is False


# ── le juge (LLM mocke) ────────────────────────────────────────────────────
async def _fake_judge(resp):
    async def _f(_deliverable):
        return resp
    return _f


@pytest.mark.asyncio
async def test_juge_flag_si_non_operationnel(monkeypatch):
    monkeypatch.setattr(cj, "_call_judge", await _fake_judge('{"operational": false, "raison": "metaphore vide"}'))
    out = await cj.judge_operational(_PROSE_PURE, "CREATION", 8.4, _ANCRE)
    assert out["escalated"] and out["called"]
    assert out["operational"] is False
    assert out["signal"] == "prose_non_operationnelle"


@pytest.mark.asyncio
async def test_juge_ne_flag_pas_si_operationnel(monkeypatch):
    monkeypatch.setattr(cj, "_call_judge", await _fake_judge('{"operational": true, "raison": "regle extractible"}'))
    out = await cj.judge_operational(_PROSE_PURE, "CREATION", 8.4, _ANCRE)
    assert out["operational"] is True and out["signal"] is None


@pytest.mark.asyncio
async def test_doctrine_inverse_json_casse(monkeypatch):
    """JSON casse / vide -> operational=True (anti-faux-positif), pas de flag."""
    monkeypatch.setattr(cj, "_call_judge", await _fake_judge("pas du json {{{"))
    out = await cj.judge_operational(_PROSE_PURE, "CREATION", 8.4, _ANCRE)
    assert out["called"] is True and out["operational"] is True and out["signal"] is None


@pytest.mark.asyncio
async def test_off_ne_appelle_jamais(monkeypatch):
    monkeypatch.setattr(cj, "CONTRADICTION_LLM_JUDGE", "off")
    monkeypatch.setattr(cj, "_call_judge", await _fake_judge('{"operational": false}'))
    out = await cj.judge_operational(_PROSE_PURE, "CREATION", 8.4, _ANCRE)
    assert out["escalated"] is False and out["called"] is False and out["signal"] is None


@pytest.mark.asyncio
async def test_cap_quotidien(monkeypatch):
    monkeypatch.setattr(cj, "_call_judge", await _fake_judge('{"operational": false, "raison": "x"}'))
    monkeypatch.setattr(cj, "JUDGE_DAILY_CAP", 2)
    # 2 appels passent, le 3e est cape (escalade mais pas d'appel)
    r1 = await cj.judge_operational(_PROSE_PURE, "CREATION", 8.4, _ANCRE)
    r2 = await cj.judge_operational(_PROSE_PURE, "CREATION", 8.4, _ANCRE)
    r3 = await cj.judge_operational(_PROSE_PURE, "CREATION", 8.4, _ANCRE)
    assert r1["called"] and r2["called"]
    assert r3["escalated"] is True and r3["called"] is False
    assert "cap" in r3["raison"].lower()
