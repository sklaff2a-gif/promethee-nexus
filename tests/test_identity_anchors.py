# -*- coding: utf-8 -*-
"""TDD des ANCRES D'IDENTITE (atelier chat<->autonomie 10/06, design CO-SIGNE par Promethee).
Garanties testees : stockee jamais executee, file bornee 3 (FIFO), TTL 72h (extinction
naturelle), bloc contexte = SUGGESTION (libelle veto souverain), transparence !ancres."""
import pytest

import core.identity_anchors as ia
from core.chat_engine import ChatEngine


@pytest.fixture
def tmp_anchors(tmp_path, monkeypatch):
    p = str(tmp_path / "anchors.json")
    monkeypatch.setattr(ia, "ANCHORS_PATH", p)
    return p


def _eng():
    return ChatEngine.__new__(ChatEngine)


# ─── module : depot, cap, TTL ───
def test_depot_et_lecture(tmp_anchors):
    ia.deposer_ancre("rester Promethee : verifier avant d'affirmer", source="chat")
    actives = ia.ancres_actives()
    assert len(actives) == 1 and "verifier avant" in actives[0]["texte"]

def test_file_bornee_fifo(tmp_anchors):
    for i in range(5):
        ia.deposer_ancre(f"ancre {i}", now=1000.0 + i)
    actives = ia.ancres_actives(now=1010.0)
    assert len(actives) == ia.MAX_ANCRES == 3
    assert [a["texte"] for a in actives] == ["ancre 2", "ancre 3", "ancre 4"]  # les plus fraiches

def test_ttl_extinction_naturelle(tmp_anchors):
    ia.deposer_ancre("ancre fraiche", now=1000.0)
    ia.deposer_ancre("ancre vieille", now=1000.0 - ia.TTL_SECONDS - 10)  # deja expiree
    actives = ia.ancres_actives(now=1000.0)
    assert [a["texte"] for a in actives] == ["ancre fraiche"]

def test_ancre_vide_refusee(tmp_anchors):
    with pytest.raises(ValueError):
        ia.deposer_ancre("   ")


# ─── le bloc contexte (l'injection douce) ───
def test_bloc_contexte_vide_si_aucune(tmp_anchors):
    assert ia.bloc_contexte() == ""   # zero pollution du contexte par defaut

def test_bloc_contexte_libelle_souverain(tmp_anchors):
    ia.deposer_ancre("honnetete d'abord")
    bloc = ia.bloc_contexte()
    assert "ANCRES D'IDENTITE" in bloc and "honnetete d'abord" in bloc
    # LA garantie du design : suggestion, jamais imperative
    assert "SUGGESTIONS" in bloc and "veto" in bloc and "souverains" in bloc


# ─── cablage chat ───
def test_parse_ancre_brut_apostrophes():
    cmd, args = _eng()._parse_command("!ancre verifier avant d'affirmer, l'honnetete d'abord")
    assert cmd == "ancre" and "d'affirmer" in args[0]

def test_ancre_et_ancres_dans_whitelist():
    wl = ChatEngine._AUTO_ACTION_WHITELIST
    assert "ancre" in wl and "ancres" in wl

def test_execute_ancre_depose(tmp_anchors):
    out = _eng()._execute_ancre_command("rester moi-meme cette nuit")
    assert "✅" in out and "souverain" in out
    assert len(ia.ancres_actives()) == 1

def test_execute_ancre_usage_si_vide(tmp_anchors):
    assert "Usage" in _eng()._execute_ancre_command("")

def test_execute_ancres_listing(tmp_anchors):
    _eng()._execute_ancre_command("premiere intention")
    out = _eng()._execute_ancres_command()
    assert "premiere intention" in out and "s'eteint dans" in out

def test_execute_ancres_vide(tmp_anchors):
    assert "Aucune ancre" in _eng()._execute_ancres_command()
