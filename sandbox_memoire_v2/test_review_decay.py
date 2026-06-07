# -*- coding: utf-8 -*-
"""TDD Raffinement 3 — amortisseur temporel (registre + decay + alerte)."""
import pytest
from proto_review_decay import ReviewQueue, INFLUENCE_FLOOR, SATURATION_THRESHOLD


@pytest.fixture
def q(tmp_path):
    return ReviewQueue(tmp_path / "review.md")


def test_flag_ecrit_une_ligne_normalisee(q, tmp_path):
    q.flag("node_042", "Conflit sur ChromaDB v2", "external_verification", 1.0, date_str="2026-06-07")
    txt = (tmp_path / "review.md").read_text(encoding="utf-8")
    assert "node_042" in txt
    assert "[PRIORITE: HAUTE]" in txt          # external -> haute priorite
    assert "[SRC: external_verification]" in txt
    assert "[DATE: 2026-06-07]" in txt


def test_internal_est_priorite_normale(q, tmp_path):
    q.flag("n1", "x", "internal_inference", 1.0, date_str="2026-06-07")
    assert "[PRIORITE: NORMALE]" in (tmp_path / "review.md").read_text(encoding="utf-8")


def test_decay_decremente_de_005_par_cycle(q):
    q.flag("n1", "x", "internal_inference", 1.0, date_str="d")
    q.tick()
    assert q.get_influence("n1") == pytest.approx(0.95)
    q.tick()
    assert q.get_influence("n1") == pytest.approx(0.90)


def test_influence_ne_descend_jamais_sous_le_plancher(q):
    q.flag("n1", "x", "internal_inference", 1.0, date_str="d")
    for _ in range(50):
        q.tick()
    assert q.get_influence("n1") == INFLUENCE_FLOOR   # 0.10, jamais 0 (reversibilite)


def test_poids_hebbian_reste_INTACT_malgre_le_decay(q):
    # COEUR : l'influence decote, mais le poids stocke ne bouge JAMAIS
    q.flag("n1", "x", "internal_inference", hebbian_weight=1.0, date_str="d")
    for _ in range(50):
        q.tick()
    assert q.get_influence("n1") == INFLUENCE_FLOOR
    assert q.get_hebbian("n1") == 1.0, "le poids Hebbian a ete altere -> reversibilite violee"


def test_resolve_confirme_restaure_l_influence(q):
    q.flag("n1", "x", "internal_inference", 1.0, date_str="d")
    for _ in range(5):
        q.tick()
    assert q.get_influence("n1") < 1.0
    q.resolve("n1", confirmed=True)
    assert q.get_influence("n1") == 1.0          # reversibilite totale


def test_resolve_arrete_le_decay(q):
    q.flag("n1", "x", "internal_inference", 1.0, date_str="d")
    q.resolve("n1", confirmed=False)             # traite (mais pas restaure)
    inf_avant = q.get_influence("n1")
    for _ in range(5):
        q.tick()
    assert q.get_influence("n1") == inf_avant     # un noeud resolu ne decote plus


def test_pending_count_compte_les_non_resolus(q):
    q.flag("a", "x", "internal_inference", 1.0, date_str="d")
    q.flag("b", "x", "internal_inference", 1.0, date_str="d")
    assert q.pending_count() == 2
    q.resolve("a", confirmed=True)
    assert q.pending_count() == 1


def test_alerte_saturation_au_seuil(q):
    for k in range(SATURATION_THRESHOLD - 1):
        q.flag(f"n{k}", "x", "internal_inference", 1.0, date_str="d")
    assert q.saturation_alert() is None           # sous le seuil
    q.flag("n_last", "x", "internal_inference", 1.0, date_str="d")
    alert = q.saturation_alert()
    assert alert is not None and "Arbitrage requis" in alert
