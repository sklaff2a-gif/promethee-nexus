# -*- coding: utf-8 -*-
"""TDD Phase 1 — Shadow Reading (Dual-Retriever passif).

Faux embedder deterministe (pas de MiniLM) -> tests rapides et reproductibles.
On valide la PLOMBERIE et l'INVARIANT DE SECURITE, pas la qualite semantique.
"""
import json
import numpy as np
import pytest
from proto_shadow_reader import ShadowRetriever, _hash_id


class FakeModel:
    """Encode en sac-de-mots sur quelques mots-cles -> vecteurs deterministes."""
    KEYS = ["memoire", "veto", "compactage", "graphe", "sommeil", "reformule"]

    def encode(self, texts, normalize_embeddings=True):
        out = []
        for t in texts:
            v = np.array([1.0 if k in t.lower() else 0.0 for k in self.KEYS])
            if v.sum() == 0:
                v[0] = 0.01
            out.append(v)
        return np.array(out)


CORPUS = [
    ("a", "la memoire reformule"),
    ("b", "le veto prefrontal"),
    ("c", "le compactage du chat"),
    ("d", "le graphe associatif"),
]


@pytest.fixture
def sr(tmp_path):
    return ShadowRetriever(CORPUS, tmp_path / "shadow.jsonl", FakeModel(), enabled=True)


def test_retrieve_retourne_TOUJOURS_l_ancien(sr):
    # INVARIANT PHASE 1 : meme si le nouveau trouve mieux, on retourne l'ancien
    q = "la memoire reformule"            # match hash exact -> ancien = ['a']
    assert sr.retrieve(q, ts="t") == ["a"]
    q2 = "souvenir reformule autrement"   # pas de match hash -> ancien = [] (aveugle)
    assert sr.retrieve(q2, ts="t") == []  # le nouveau n'est JAMAIS injecte


def test_kill_switch_zero_overhead_pas_de_log(tmp_path):
    sr = ShadowRetriever(CORPUS, tmp_path / "s.jsonl", FakeModel(), enabled=False)
    sr.retrieve("la memoire reformule", ts="t")
    # kill-switch off -> aucun fichier de log ecrit
    assert not (tmp_path / "s.jsonl").exists()


def test_log_jsonl_contient_les_champs_attendus(sr, tmp_path):
    sr.retrieve("souvenir reformule", ts="2026-06-07")
    lines = (tmp_path / "shadow.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    for champ in ("ts", "query", "old", "new", "overlap", "mismatch", "lat_old_ms", "lat_new_ms"):
        assert champ in rec
    assert isinstance(rec["lat_new_ms"], float) and rec["lat_new_ms"] >= 0


def test_mismatch_detecte_quand_old_diverge_du_new(sr):
    # 'souvenir reformule' : ancien aveugle ([]) mais nouveau trouve qqch -> mismatch=True
    sr.retrieve("souvenir reformule", ts="t")
    # relit la derniere ligne
    rec = json.loads(open(sr.log_path, encoding="utf-8").read().strip().splitlines()[-1])
    assert rec["mismatch"] is True
    assert rec["old"] == [] and len(rec["new"]) > 0


def test_match_exact_pas_de_mismatch(sr):
    # requete == texte stocke : ancien=['a'], nouveau devrait aussi avoir 'a' en tete
    sr.retrieve("la memoire reformule", ts="t")
    rec = json.loads(open(sr.log_path, encoding="utf-8").read().strip().splitlines()[-1])
    assert rec["old"] == ["a"]
    assert "a" in rec["new"]
