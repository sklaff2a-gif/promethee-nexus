# -*- coding: utf-8 -*-
"""TDD — Ancre Sacree (seed des lecons PREMIUM)."""
import json
import pytest
from seed_premium_lessons import seed_premium, lesson_to_premium, load_lessons, LESSON_MIN_CHARS


class FakeCollection:
    def __init__(self):
        self.docs = []; self.metas = []; self.ids = []
    def upsert(self, documents, metadatas, ids):
        for d, m, i in zip(documents, metadatas, ids):
            if i in self.ids:
                k = self.ids.index(i); self.docs[k] = d; self.metas[k] = m
            else:
                self.ids.append(i); self.docs.append(d); self.metas.append(m)
    def count(self):
        return len(self.ids)


@pytest.fixture
def journal(tmp_path):
    data = [
        {"timestamp": 1.0, "lesson": "L'honnetete est l'invariant absolu de toute connaissance vraie.", "concepts": ["a", "b"], "source": "chat"},
        {"timestamp": 2.0, "lesson": "Stocker l'intention structuree a la source plutot que de la redeviner.", "concepts": ["c"], "source": "chat"},
        {"timestamp": 3.0, "lesson": "court", "concepts": [], "source": "chat"},  # trop courte -> ignoree
    ]
    p = tmp_path / "lessons_journal.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_lesson_to_premium_force_le_passeport(journal):
    lessons = load_lessons(journal)
    r = lesson_to_premium(lessons[0], 0)
    assert r is not None
    _id, text, meta = r
    assert _id == "premium_lesson_000"
    assert meta["tier_status"] == "PREMIUM"
    assert meta["injected_label"] == "[CERTIFIE]"      # derive, jamais fourni
    assert meta["is_flagged"] is False
    assert "honnetete" in text.lower()


def test_lecon_trop_courte_est_ignoree(journal):
    lessons = load_lessons(journal)
    assert lesson_to_premium(lessons[2], 2) is None     # "court" < 20 chars


def test_seed_complet_upsert_les_premiums(journal):
    col = FakeCollection()
    n = seed_premium(journal, col, log=lambda *a: None)
    assert n == 2                                        # 3 lecons, 1 ignoree
    assert col.count() == 2
    assert all(m["tier_status"] == "PREMIUM" for m in col.metas)
    assert all(m["injected_label"] == "[CERTIFIE]" for m in col.metas)


def test_seed_idempotent(journal):
    col = FakeCollection()
    seed_premium(journal, col, log=lambda *a: None)
    seed_premium(journal, col, log=lambda *a: None)     # 2e passe
    assert col.count() == 2                             # pas de doublon (upsert)


def test_label_jamais_fourni_a_la_main():
    # meme si un label parasite traine, il est re-derive
    r = lesson_to_premium({"lesson": "x" * 30, "injected_label": "[FAUX]"}, 5)
    assert r[2]["injected_label"] == "[CERTIFIE]"
