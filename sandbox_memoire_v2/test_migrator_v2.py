# -*- coding: utf-8 -*-
"""TDD Phase 2 — pipeline de reindexation (streaming + transmutation + checkpoint).

FakeCollection en memoire (zero ChromaDB, zero embedder) -> tests rapides/deterministes.
On valide la MECANIQUE et les GARANTIES, pas la qualite des vecteurs (couverte par la demo).
"""
import json
import pytest
from migrator_v2 import migrate, transmute_metadata, infer_tier, Checkpoint, CHUNK_SIZE


class FakeCollection:
    """Imite l'API ChromaDB utilisee : count / get(limit,offset,include) / upsert / add."""
    def __init__(self, docs=None, metas=None, ids=None):
        self.docs = list(docs or []); self.metas = list(metas or []); self.ids = list(ids or [])

    def count(self):
        return len(self.ids)

    def get(self, limit=None, offset=0, include=None):
        sl = slice(offset, offset + limit if limit else None)
        return {"ids": self.ids[sl], "documents": self.docs[sl], "metadatas": self.metas[sl]}

    def upsert(self, documents, metadatas, ids):
        for d, m, i in zip(documents, metadatas, ids):
            if i in self.ids:                       # idempotent : remplace
                k = self.ids.index(i); self.docs[k] = d; self.metas[k] = m
            else:
                self.ids.append(i); self.docs.append(d); self.metas.append(m)
    add = upsert


def _old(n=12):
    return FakeCollection(
        docs=[f"document francais numero {i}" for i in range(n)],
        metas=[{"type": "lesson_certified"} if i == 3 else {"source": "chat"} for i in range(n)],
        ids=[f"node_{i:03}" for i in range(n)],
    )


# --- transmutation des metadonnees ---
def test_tier_churn_par_defaut():
    assert infer_tier({"source": "chat"}) == "CHURN"

def test_option_A_migration_tout_en_churn():
    # OPTION A : meme un tag qui ressemble a une validation -> CHURN. Le premium ne
    # s'herite jamais a la migration (il est seede separement depuis lessons_journal).
    assert infer_tier({"type": "lesson_certified"}) == "CHURN"
    assert infer_tier({"tags": "grave epistemic"}) == "CHURN"
    assert infer_tier({"source": "consolidation"}) == "CHURN"

def test_transmute_injecte_les_defauts_et_le_label():
    out = transmute_metadata({"source": "chat", "vieux_champ": "x"})
    assert out["tier_status"] == "CHURN"
    assert out["is_flagged"] is False
    assert out["injected_label"] == "[memoire courante]"
    assert out["vieux_champ"] == "x"   # les champs d'origine NON obsoletes sont preserves

def test_transmute_label_jamais_fourni_a_la_main():
    # le label fourni est toujours re-derive ; sous Option A le tier est CHURN
    out = transmute_metadata({"source": "chat", "injected_label": "[FAUX]"})
    assert out["tier_status"] == "CHURN"
    assert out["injected_label"] == "[memoire courante]"   # re-derive, ecrase le faux


# --- streaming + migration complete ---
def test_migration_complete_preserve_ids_et_contenu(tmp_path):
    old = _old(12); new = FakeCollection()
    n = migrate(old, new, tmp_path / "ck.json", chunk_size=5, log=lambda *a: None)
    assert n == 12
    assert new.count() == 12
    assert set(new.ids) == set(old.ids)             # IDs preserves
    assert "document francais numero 7" in new.docs  # contenu preserve
    # tier injecte partout
    assert all("tier_status" in m for m in new.metas)


def test_checkpoint_progresse_par_chunk(tmp_path):
    ck = tmp_path / "ck.json"
    migrate(_old(10), FakeCollection(), ck, chunk_size=4, log=lambda *a: None)
    d = json.loads(ck.read_text(encoding="utf-8"))
    assert d["done"] is True and d["migrated"] == 10


def test_reprise_apres_crash_ne_recommence_pas_de_zero(tmp_path):
    ck = tmp_path / "ck.json"
    old = _old(10)

    # collection qui CRASHE au 2e chunk (simule throttle GPU / coupure)
    class Crashing(FakeCollection):
        def __init__(self): super().__init__(); self.calls = 0
        def upsert(self, documents, metadatas, ids):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("VRAM saturee (simule)")
            super().upsert(documents, metadatas, ids)

    crash = Crashing()
    with pytest.raises(RuntimeError):
        migrate(old, crash, ck, chunk_size=4, log=lambda *a: None)
    # checkpoint a sauve le 1er chunk (offset=4)
    d = json.loads(ck.read_text(encoding="utf-8"))
    assert d["offset"] == 4 and d["done"] is False

    # RELANCE sur une collection saine -> reprend a l'offset 4, complete sans doublon
    new = FakeCollection()
    new.upsert(old.docs[:4], [transmute_metadata(m) for m in old.metas[:4]], old.ids[:4])  # le 1er chunk deja ecrit
    total = migrate(old, new, ck, chunk_size=4, log=lambda *a: None)
    assert total == 10
    assert new.count() == 10                         # pas de doublon (upsert idempotent)
    assert len(set(new.ids)) == 10


def test_relance_quand_deja_done_ne_refait_rien(tmp_path):
    ck = tmp_path / "ck.json"
    migrate(_old(6), FakeCollection(), ck, chunk_size=3, log=lambda *a: None)
    new2 = FakeCollection()
    n = migrate(_old(6), new2, ck, chunk_size=3, log=lambda *a: None)  # done=True
    assert n == 6 and new2.count() == 0              # court-circuit : rien re-ecrit
