# -*- coding: utf-8 -*-
"""TDD — Phase 2 du Métabolisme de la Résolution : le verdict de la sonde dire->faire transite
jusqu'au context de satisfaction via SCHOOL_GRADE_RECEIVED (professor_agent).

But : qu'un livrable BIEN NOTE mais 'instable' (coquille vide) abaisse q_res (que la note seule rate).
resolution_quality lit déjà context['verdict'] → 0 changement desire_engine. try/except : ne casse
jamais la notation. Le Métabolisme reste shadow (Phase 2 n'enrichit que le signal)."""
import pytest

import core.event_bus.bus as bus_mod
from Agents.professor_agent import ProfessorAgent


def test_feed_desire_porte_le_verdict(monkeypatch):
    captured = {}
    monkeypatch.setattr(bus_mod, "publish_from_sync",
                        lambda et, ev, label=None: captured.update(et=et, ev=ev))
    p = ProfessorAgent.__new__(ProfessorAgent)
    p._feed_desire_engine("CREATION", 8.4, "instable")
    assert captured["et"] == "SCHOOL_GRADE_RECEIVED"
    assert captured["ev"]["verdict"] == "instable"
    assert captured["ev"]["grade"] == 8.4


def test_feed_desire_sans_verdict_si_none(monkeypatch):
    captured = {}
    monkeypatch.setattr(bus_mod, "publish_from_sync",
                        lambda et, ev, label=None: captured.update(ev=ev))
    p = ProfessorAgent.__new__(ProfessorAgent)
    p._feed_desire_engine("MATH", 5.0, None)
    assert "verdict" not in captured["ev"]   # backward-compatible : clé absente


@pytest.mark.asyncio
async def test_evaluate_calcule_le_verdict_instable(monkeypatch):
    """evaluate lance la sonde et passe 'instable' sur une CREATION bien notée à code no-op."""
    p = ProfessorAgent.__new__(ProfessorAgent)
    p._grades = []
    monkeypatch.setattr(p, "_save_grades", lambda: None)
    monkeypatch.setattr(p, "_score_deterministic",
                        lambda d, c, s: {"score": 7.0, "comments": []})

    async def _qual(d, c, s):
        return {"score": 1.5, "feedback": "", "challenge": ""}
    monkeypatch.setattr(p, "_score_qualitative", _qual)
    monkeypatch.setattr(p, "_adjust_difficulty", lambda c, g: False)

    captured = {}
    monkeypatch.setattr(p, "_feed_desire_engine",
                        lambda ct, g, v=None: captured.update(ct=ct, g=g, v=v))

    # CREATION (slot prose), bien notée (~8.5), avec un bloc python DECORATIF -> code_noop -> instable
    deliverable = ("Je suis comme une riviere qui coule sans fin, fluide et adaptative. "
                   "```python\ndef moi():\n    return 'je coule'\n```\n") + ("texte " * 60)
    await p.evaluate(deliverable, "CREATION", "sujet libre")
    assert captured["v"] == "instable"


@pytest.mark.asyncio
async def test_evaluate_verdict_none_si_non_prose(monkeypatch):
    """Un course_type non-prose (CODE_REVIEW) -> sonde non applicable -> verdict None (neutre)."""
    p = ProfessorAgent.__new__(ProfessorAgent)
    p._grades = []
    monkeypatch.setattr(p, "_save_grades", lambda: None)
    monkeypatch.setattr(p, "_score_deterministic",
                        lambda d, c, s: {"score": 6.0, "comments": []})

    async def _qual(d, c, s):
        return {"score": 1.0, "feedback": "", "challenge": ""}
    monkeypatch.setattr(p, "_score_qualitative", _qual)
    monkeypatch.setattr(p, "_adjust_difficulty", lambda c, g: False)

    captured = {}
    monkeypatch.setattr(p, "_feed_desire_engine",
                        lambda ct, g, v=None: captured.update(v=v))
    await p.evaluate("def f(): return 1", "CODE_REVIEW", "sujet")
    assert captured["v"] is None
