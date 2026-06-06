# -*- coding: utf-8 -*-
"""Tests V24.0 — commande !calc (delegation de calcul via sandbox securise).

Donne a Promethee un outil FIABLE pour deleguer son talon (le calcul), la ou
l'agent !code refuse "hors perimetre". Execute dans le sandbox AST-lint et
retourne le resultat REEL (pas de confabulation : on rapporte ce que le sandbox
produit, ou son echec).
"""
import pytest
from unittest.mock import patch

from core.chat_engine import ChatEngine


@pytest.fixture(autouse=True)
def reset_engine():
    ChatEngine.reset_singleton()
    yield
    ChatEngine.reset_singleton()


@pytest.fixture
def engine():
    with patch.object(ChatEngine, "_load"):
        e = ChatEngine()
    return e


@pytest.mark.asyncio
async def test_calc_expression_simple(engine):
    res = await engine._execute_command("calc", ["2**10"])
    assert "1024" in res, res


@pytest.mark.asyncio
async def test_calc_somme_generateur(engine):
    # expression simple -> wrap print() automatique ; sum(range(1,11)) = 55
    res = await engine._execute_command("calc", ["sum(range(1,11))"])
    assert "55" in res, res


@pytest.mark.asyncio
async def test_calc_vide_montre_usage(engine):
    res = await engine._execute_command("calc", [])
    assert "Usage" in res


@pytest.mark.asyncio
async def test_calc_code_multiligne_avec_print(engine):
    code = "s=0\nfor k in range(1,5):\n    s+=k\nprint(s)"  # 1+2+3+4 = 10
    res = await engine._execute_command("calc", [code])
    assert "10" in res, res


@pytest.mark.asyncio
async def test_calc_rejette_code_dangereux(engine):
    # le sandbox doit refuser un import/builtin interdit -> echec rapporte
    res = await engine._execute_command("calc", ["__import__('os').getcwd()"])
    low = res.lower()
    assert "echec" in low or "interdit" in low or "import" in low, res


@pytest.mark.asyncio
async def test_calc_gere_newlines_litteraux(engine):
    # CAS REEL : Promethee ecrit son script sur une ligne avec des \n LITTERAUX
    code = "def f(n):\\n    return sum(range(n))\\nprint(f(5))"  # 0+1+2+3+4 = 10
    res = await engine._execute_command("calc", [code])
    assert "10" in res, res


@pytest.mark.asyncio
async def test_calc_enleve_guillemets_entourants(engine):
    res = await engine._execute_command("calc", ['"2+3"'])
    assert "5" in res, res


def test_calc_dans_whitelist_auto_action():
    # sans ca, !calc emis par Promethee dans SES reponses ne s'execute pas
    assert "calc" in ChatEngine._AUTO_ACTION_WHITELIST


# ---------- V24.2 : collapse d'un !calc multi-ligne ----------

def test_collapse_calc_multiligne(engine):
    # un script ecrit sur plusieurs lignes (vrais newlines) doit devenir UNE ligne
    response = "!calc def f(n):\n    return n*2\nprint(f(5))"
    out = engine._collapse_multiline_calc(response)
    assert "\n" not in out, f"vrais newlines restants : {out!r}"
    assert "\\n" in out  # convertis en litteraux
    assert out.startswith("!calc ")


def test_collapse_calc_sarrete_avant_autre_commande(engine):
    response = "!calc a=1\nprint(a)\n!read foo.py"
    out = engine._collapse_multiline_calc(response)
    assert "!read foo.py" in out          # l'autre commande reste intacte
    assert out.startswith("!calc a=1\\nprint(a)")


def test_collapse_calc_une_ligne_inchange(engine):
    response = "!calc 2+2"
    assert engine._collapse_multiline_calc(response) == "!calc 2+2"


def test_collapse_sans_calc_inchange(engine):
    response = "du texte normal\n!read foo.py"
    assert engine._collapse_multiline_calc(response) == response
