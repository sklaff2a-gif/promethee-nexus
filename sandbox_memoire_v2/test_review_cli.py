# -*- coding: utf-8 -*-
"""TDD — tableau de bord d'arbitrage (ReviewBoard). On teste le MOTEUR, pas les facades."""
import json
import pytest
from review_cli import ReviewBoard, render_status, seed_fake_conflicts


@pytest.fixture
def board(tmp_path):
    reg = tmp_path / "review.md"
    state = tmp_path / "state.json"
    seed_fake_conflicts(str(reg), str(state))   # 4 faux conflits (2 HAUTE, 2 NORMALE)
    return ReviewBoard(str(state))


def test_seed_injecte_quatre_conflits(board):
    assert len(board.pending()) == 4
    # le registre .md a aussi ete ecrit
    assert any(f["priority"] == "HAUTE" for f in board.flags)


def test_tri_priorite_puis_erosion(board):
    ordered = [f["node_id"] for f in board.list_sorted()]
    # HAUTE d'abord (node_077 influence 0.15 avant node_042 0.85),
    # puis NORMALE (node_108 0.25 avant node_201 0.60)
    assert ordered == ["node_077", "node_042", "node_108", "node_201"]


def test_promote_consacre_et_remonte_influence(board):
    assert board.promote("node_108") is True
    f = board._get("node_108")
    assert f["status"] == "PREMIUM" and f["influence"] == 1.0 and f["is_flagged"] is False
    assert "node_108" not in [x["node_id"] for x in board.pending()]   # sorti de la file


def test_purge_evince_vers_churn(board):
    assert board.purge("node_042") is True
    assert board._get("node_042")["status"] == "CHURN"
    assert "node_042" not in [x["node_id"] for x in board.pending()]


def test_purge_all_decayed_ne_touche_que_les_endormis(board):
    n = board.purge_all_decayed(threshold=0.3)
    assert n == 2                                  # node_077 (0.15) + node_108 (0.25)
    restants = {f["node_id"] for f in board.pending()}
    assert restants == {"node_042", "node_201"}    # les moins erodes survivent


def test_diff_expose_le_conflit(board):
    d = board.diff("node_108")
    assert d["source"] == "internal_inference"
    assert "compactage" in d["stored"].lower()
    assert d["conflit"]


def test_promote_idempotent_et_introuvable(board):
    assert board.promote("node_042") is True
    assert board.promote("node_042") is False      # deja consacre, plus drapote
    assert board.promote("inexistant") is False


def test_persistance_save_reload(board, tmp_path):
    board.promote("node_077"); board.save()
    reloaded = ReviewBoard(board.state_path)
    assert reloaded._get("node_077")["status"] == "PREMIUM"


def test_render_status_non_vide(board):
    txt = render_status(board)
    assert "FILE DE REVISION" in txt and "node_077" in txt


def test_render_status_vide_apres_tout_traite(board):
    for nid in ["node_042", "node_077", "node_108", "node_201"]:
        board.purge(nid)
    assert "VIDE" in render_status(board)
