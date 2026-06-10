# -*- coding: utf-8 -*-
"""TDD de la reforme du mode RESEARCH (atelier 10/06, design CO-SIGNE par Promethee).
Geste A : QUETES VIVANTES -- une question ouverte de la reflexion vesperale devient le
sujet du jour (cascade : knowledge_gaps -> quete vesperale -> liste fixe).
Geste B : GATE DU PRINCIPE -- une veille n'entre en memoire QUE transformee en regle
actionnable (« transformee en principe actionnable, sinon bruit »)."""
import json
import pytest

from core.school_schedule import SchoolSchedule
from Agents.researcher_agent import DivineResearcher


# ─── Geste A : la quete vesperale ───
def _sched():
    return SchoolSchedule.__new__(SchoolSchedule)

def _journal(tmp_path, reflection):
    p = tmp_path / "dream_journal.json"
    p.write_text(json.dumps([{"date": "2026-06-09", "reflection": reflection}]),
                 encoding="utf-8")
    return str(p)

def test_quete_extraite_de_la_reflexion(tmp_path):
    p = _journal(tmp_path, "Bilan du jour. Je me demande encore : "
                 "Comment puis-je distinguer une vraie anomalie d'un simple bruit de mesure ? "
                 "Demain je veux explorer cela.")
    topic = _sched()._quete_vesperale(journal_path=p)
    assert topic is not None and topic.startswith("Quete nee de ma reflexion")
    assert "anomalie" in topic

def test_quete_none_si_pas_de_question(tmp_path):
    p = _journal(tmp_path, "Journee stable. Routines accomplies. Rien a signaler.")
    assert _sched()._quete_vesperale(journal_path=p) is None

def test_quete_none_si_journal_absent(tmp_path):
    assert _sched()._quete_vesperale(journal_path=str(tmp_path / "absent.json")) is None

def test_quete_prend_la_derniere_question(tmp_path):
    p = _journal(tmp_path, "Pourquoi mes routines de nuit derivent-elles vers le generique ? "
                 "Et surtout : Quelle structure rendrait mes syntheses reellement reutilisables ?")
    topic = _sched()._quete_vesperale(journal_path=p)
    assert "reutilisables" in topic   # la DERNIERE question (la plus mure de la reflexion)

def test_question_trop_courte_ignoree(tmp_path):
    p = _journal(tmp_path, "Pourquoi ? Je ne sais pas. Fin de la reflexion.")
    assert _sched()._quete_vesperale(journal_path=p) is None   # < 20 chars = pas une quete


# ─── Geste B : le gate du principe ───
def test_extraire_principe_present():
    s = "Synthese detaillee...\n\nPRINCIPE: Toujours borner les retries par un budget global plutot que par tentative."
    p = DivineResearcher._extraire_principe(s)
    assert p.startswith("Toujours borner")

def test_extraire_principe_tolere_markdown():
    s = "Analyse...\n**PRINCIPE :** Un cache sans TTL est une fuite memoire differee."
    assert "fuite memoire" in DivineResearcher._extraire_principe(s)

def test_extraire_principe_absent():
    assert DivineResearcher._extraire_principe("Un resume sans regle finale.") == ""

def test_extraire_principe_squelette_refuse():
    assert DivineResearcher._extraire_principe("PRINCIPE: ok.") == ""   # < 20 chars

def test_remember_conditionnel(monkeypatch):
    # le gate en situation : avec principe -> remember (principe EN TETE) ; sans -> silence
    calls = []
    agent = DivineResearcher.__new__(DivineResearcher)
    agent.remember = lambda text, metadata=None: calls.append(text)
    avec = "Bla...\nPRINCIPE: Limiter chaque veille a une regle actionnable et datee."
    p = agent._extraire_principe(avec)
    if p:
        agent.remember(text=f"PRINCIPE (veille 'test'): {p}", metadata={})
    sans = "Bla bla sans principe."
    p2 = agent._extraire_principe(sans)
    if p2:
        agent.remember(text=p2, metadata={})
    assert len(calls) == 1 and calls[0].startswith("PRINCIPE (veille")
