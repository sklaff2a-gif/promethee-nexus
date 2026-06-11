# -*- coding: utf-8 -*-
"""TDD du DURCISSEMENT du !run face a la TRONCATURE de generation (atelier educatif 11/06).

Le 12B local tronque parfois un long script !run AVANT ses print() -> l'instrument
rend 'aucune sortie', message indistinguable d'un simple oubli de print -> a bloque
Promethee 2 tours dans l'atelier dopamine. Deux durcissements :
  1. message 'aucune sortie' DISTINGUE 'pas de print' (par construction muet) de
     'print present mais rien affiche', et nomme la troncature comme cause possible.
  2. _truncation_hint() detecte un script coupe (EOF, brackets ouverts, ligne de
     continuation) et enrichit la trace d'echec.
"""
import re

import pytest

from core.chat_engine import ChatEngine


# ─── _truncation_hint : detection a faible faux-positif ───

def _h(code, trace=""):
    return ChatEngine._truncation_hint(code, trace)


def test_eof_inattendu_detecte():
    assert _h("for i in range(3):", "SyntaxError: unexpected EOF while parsing")
    assert "COUPE" in _h("x=(", "SyntaxError: unexpected EOF while parsing")


def test_parenthese_ouverte_detectee():
    assert _h("print(round(0.985")          # 2 ouvrantes, 0 fermante
    assert "parenthese" in _h("resultats.append(calc(x").lower()


def test_crochet_ouvert_detecte():
    assert _h("data = [1, 2, 3")             # crochet jamais ferme


def test_derniere_ligne_continuation_detectee():
    assert _h("V = 0.0\nresult =")           # se termine sur '='
    assert _h("total = a +")                 # se termine sur '+'
    assert _h("for _ in range(40):")         # se termine sur ':'


def test_script_complet_non_signale():
    """Faux positif INTERDIT : un script complet et equilibre ne doit jamais
    etre signale comme tronque."""
    assert _h("V=0.0\nfor _ in range(40):\n    V+=0.1\nprint(round(V,3))") == ""
    assert _h("x = [1, 2, 3]\nprint(sum(x))") == ""
    assert _h("print('bonjour')") == ""


def test_les_crochets_dans_les_chaines_ignores():
    """Une accolade/parenthese DANS une chaine ne compte pas (sinon faux positif)."""
    assert _h("print('resultat [ok] (fini)')") == ""
    assert _h('msg = "a ( sans fermeture en texte"\nprint(msg)') == ""


def test_results_b1_vide_non_signale_par_le_hint():
    """Le cas reel de l'atelier (`results_b1 = []`) est syntaxiquement COMPLET :
    le hint ne le signale PAS (c'est le message 'pas de print' qui doit jouer,
    pas le hint qui ne voit que la syntaxe)."""
    code = "V=0.0\nalpha=0.1\nr_learning=1.0\nresults_b1=[]"
    assert _h(code) == ""


# ─── message 'aucune sortie' : distinction print absent / present ───

class _FakeRes:
    def __init__(self, success, stdout="", trace=""):
        self.success = success
        self.stdout = stdout
        self._trace = trace
    def format_traceback(self, n):
        return self._trace


@pytest.fixture
def engine(monkeypatch):
    eng = ChatEngine.__new__(ChatEngine)
    return eng


def _patch_sandbox(monkeypatch, res):
    import core.capabilities.code_sandbox as cs
    monkeypatch.setattr(cs.sandbox, "run_python", lambda code, timeout=25: res)


def test_sans_print_message_nomme_la_troncature(engine, monkeypatch):
    """Script sans print (cas atelier) -> le message doit nommer la TRONCATURE
    comme cause possible, pas seulement 'pense a print'."""
    _patch_sandbox(monkeypatch, _FakeRes(True, stdout=""))
    out = engine._execute_run_command("V=0.0\nresults_b1=[]")
    assert "AUCUNE SORTIE" in out
    assert "COUPE" in out and "print()" in out


def test_avec_print_mais_rien_affiche_message_distinct(engine, monkeypatch):
    """Script AVEC print mais sortie vide -> message different (conditions/boucles),
    ne parle pas de troncature."""
    _patch_sandbox(monkeypatch, _FakeRes(True, stdout=""))
    out = engine._execute_run_command("if False:\n    print('x')")
    assert "aucune sortie" in out.lower()
    assert "COUPE" not in out


def test_succes_normal_inchange(engine, monkeypatch):
    _patch_sandbox(monkeypatch, _FakeRes(True, stdout="42"))
    out = engine._execute_run_command("print(6*7)")
    assert "journal" in out and "42" in out


def test_echec_tronque_enrichi(engine, monkeypatch):
    """Echec dont la trace sent l'EOF -> message enrichi du hint troncature."""
    _patch_sandbox(monkeypatch, _FakeRes(False, trace="SyntaxError: unexpected EOF while parsing"))
    out = engine._execute_run_command("for i in range(3):")
    assert "COUPE" in out and "trace" in out
