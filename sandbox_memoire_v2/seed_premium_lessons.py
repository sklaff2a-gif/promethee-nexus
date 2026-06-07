# -*- coding: utf-8 -*-
"""Ancre Sacree — seed des 12 lecons PREMIUM dans la collection temoin.

Sandbox ISOLE. La migration de masse (migrator_v2) envoie TOUT le passif en CHURN ;
ce script garantit l'immunite immediate du noyau dur : il extrait les invariants
structurels de `lessons_journal.json`, leur force le passeport PREMIUM + le label
[CERTIFIE], et les upsert dans la collection temoin (embedder multilingue).

Le premium n'est PAS herite des tags (ils n'existent pas, cf scan) : il est SEEDE
explicitement depuis le seul registre certifie par le gate humain.
"""
import json

from schema_tiers import validate_metadata, TierStatus

LESSON_MIN_CHARS = 20


def load_lessons(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def lesson_to_premium(lesson, idx):
    """(id, texte, metadata PREMIUM) — ou None si la lecon est trop courte/vide."""
    text = (lesson.get("lesson") or lesson.get("lecon") or "").strip()
    if len(text) < LESSON_MIN_CHARS:
        return None
    meta = validate_metadata({
        "tier_status": TierStatus.PREMIUM.value,   # passeport force
        "is_flagged": False,
        "source": "lesson_certified",
        "origin_ts": str(lesson.get("timestamp", "")),
        "concepts": ",".join(lesson.get("concepts", []) or [])[:200],
    })
    # validate_metadata derive injected_label = [CERTIFIE]
    return (f"premium_lesson_{idx:03}", text, meta)


def seed_premium(lessons_path, collection, log=print):
    """Charge les lecons, force PREMIUM, upsert dans la collection temoin.
    Retourne le nombre de lecons seedees."""
    lessons = load_lessons(lessons_path)
    ids, docs, metas = [], [], []
    skipped = 0
    for i, l in enumerate(lessons):
        r = lesson_to_premium(l, i)
        if r is None:
            skipped += 1
            continue
        ids.append(r[0]); docs.append(r[1]); metas.append(r[2])
    if ids:
        collection.upsert(documents=docs, metadatas=metas, ids=ids)
    log(f"[seed] {len(ids)} lecons PREMIUM ancrees, {skipped} ignorees (trop courtes).")
    return len(ids)


if __name__ == "__main__":
    import os
    import chromadb
    from chromadb.utils import embedding_functions

    # registre reel (live, lecture seule) si dispo, sinon original
    paths = [
        r"C:\MesProjets\PROMETHEE_V11_restructuration2026\memory\lessons_journal.json",
        os.path.join(os.path.dirname(__file__), "..", "memory", "lessons_journal.json"),
    ]
    lj = next((p for p in paths if os.path.exists(p)), None)
    if not lj:
        print("lessons_journal.json introuvable — demo annulee.")
    else:
        client = chromadb.EphemeralClient()
        ml = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="paraphrase-multilingual-MiniLM-L12-v2")
        col = client.create_collection("collective_wisdom_v2_test", embedding_function=ml)
        n = seed_premium(lj, col)
        print(f"\nVerification : collection={col.count()} lecons premium")
        s = col.get(ids=["premium_lesson_000"], include=["metadatas", "documents"])
        print(f"  premium_lesson_000 -> tier={s['metadatas'][0]['tier_status']} "
              f"label={s['metadatas'][0]['injected_label']}")
        print(f"  texte: {s['documents'][0][:70]}...")
