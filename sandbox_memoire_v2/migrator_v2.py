# -*- coding: utf-8 -*-
"""Phase 2 — Pipeline de REINDEXATION MASSIVE (migrator_v2.py).

Sandbox ISOLE. Transmute l'ancienne collection (embedder ANGLAIS) vers une collection
TEMOIN (embedder MULTILINGUE), pour ramener la semantique de Promethee "chez elle".

GARANTIES :
  - LECTURE SEULE sur l'ancienne collection (jamais .add/.delete dessus).
  - Ecrit dans une collection NEUVE (collective_wisdom_v2_test).
  - STREAMING par chunks de 500 (anti-saturation RAM : pas de .get() global).
  - TRANSMUTATION : chaque noeud passe au validateur schema_tiers (contenu+ID preserves,
    metadonnees nettoyees, defauts V2 injectes : tier_status, is_flagged, injected_label).
  - CHECKPOINTING : etat sauve apres CHAQUE chunk -> reprise instantanee a la ligne N+1.
  - UPSERT (idempotent) -> un chunk re-traite apres crash ne cree pas de doublon.
"""
import json
import os
import time

from schema_tiers import validate_metadata, TierStatus

CHUNK_SIZE = 500


# --- 1. Determination du tier a la migration ---------------------------------
# OPTION A (gravee JM 07/06) : tout le passif historique part en CHURN.
# Le scan des tags reels (scan_tags.py) a prouve qu'AUCUN marqueur de validation
# (lesson_certified/validated/premium/grave/epistemic) n'existe dans collective_wisdom.
# Le premium n'est donc PAS herite des tags : il est SEEDE separement depuis
# lessons_journal (seed_premium_lessons.py). La migration de masse ne promeut JAMAIS ;
# le premium/tampon se construit ensuite dynamiquement via le gate.
def infer_tier(meta) -> str:
    return TierStatus.CHURN.value


def transmute_metadata(meta) -> dict:
    """Nettoie + injecte les defauts V2 + valide via le chassis des tiers.
    Le contenu textuel et l'ID d'origine sont geres a part (preserves tels quels)."""
    base = dict(meta or {})
    base["tier_status"] = infer_tier(meta)
    base.setdefault("is_flagged", False)
    base.pop("injected_label", None)   # jamais fourni a la main : re-derive par le validateur
    return validate_metadata(base)


# --- 2. Checkpoint (reprise instantanee) -------------------------------------
class Checkpoint:
    def __init__(self, path):
        self.path = str(path)
        self.offset = 0
        self.migrated = 0
        self.done = False
        if os.path.exists(self.path):
            d = json.load(open(self.path, encoding="utf-8"))
            self.offset = d.get("offset", 0)
            self.migrated = d.get("migrated", 0)
            self.done = d.get("done", False)

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"offset": self.offset, "migrated": self.migrated, "done": self.done,
                       "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}, f, ensure_ascii=False)


# --- 3. La boucle de migration par chunks ------------------------------------
def migrate(old_col, new_col, checkpoint_path, chunk_size=CHUNK_SIZE, total=None, log=print):
    """Stream l'ancienne collection par fenetres -> transmute -> upsert dans la nouvelle.
    Reprend au checkpoint. Retourne le nombre total reindexe."""
    cp = Checkpoint(checkpoint_path)
    if cp.done:
        log(f"[migrator] deja termine ({cp.migrated} reindexes).")
        return cp.migrated
    if total is None:
        total = old_col.count()
    log(f"[migrator] reprise offset={cp.offset} / total={total}")

    while cp.offset < total:
        batch = old_col.get(limit=chunk_size, offset=cp.offset, include=["documents", "metadatas"])
        ids = batch.get("ids", [])
        if not ids:
            break
        docs = batch.get("documents", []) or [""] * len(ids)
        raw_metas = batch.get("metadatas") or [{} for _ in ids]
        new_metas = [transmute_metadata(m) for m in raw_metas]
        # UPSERT : idempotent -> la collection neuve recalcule les vecteurs MULTILINGUES
        new_col.upsert(documents=docs, metadatas=new_metas, ids=ids)
        cp.offset += len(ids)
        cp.migrated += len(ids)
        cp.save()   # CHECKPOINT apres chaque chunk reussi
        log(f"[migrator] +{len(ids)} (total {cp.migrated}/{total})")

    cp.done = True
    cp.save()
    log(f"[migrator] TERMINE : {cp.migrated} noeuds reindexes en multilingue.")
    return cp.migrated


if __name__ == "__main__":
    # DEMO end-to-end avec un VRAI stack ChromaDB ephemere (en memoire, pas le live)
    import chromadb
    from chromadb.utils import embedding_functions

    client = chromadb.EphemeralClient()
    old = client.create_collection("old_english")  # embedder defaut (anglais)
    ml = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="paraphrase-multilingual-MiniLM-L12-v2")
    new = client.create_collection("collective_wisdom_v2_test", embedding_function=ml)

    # ancien contenu francais + metadonnees heterogenes (dont 1 acquis "valide")
    docs = [
        "Le veto prefrontal refuse une tache hors-sujet",
        "L'honnetete est l'invariant absolu",
        "La consolidation onirique elague le bruit",
        "Le GraphRAG capte les relations entre entites",
        "La periode de Pisano cycle Fibonacci modulo n",
    ]
    metas = [
        {"source": "chat"}, {"type": "lesson_certified"}, {"source": "dream"},
        {"tags": "recherche"}, {"source": "ecole"},
    ]
    ids = [f"node_{i:03}" for i in range(len(docs))]
    old.add(documents=docs, metadatas=metas, ids=ids)

    ckpt = os.path.join(os.path.dirname(__file__), "migration_checkpoint.json")
    if os.path.exists(ckpt):
        os.remove(ckpt)

    print("=" * 64)
    print("DEMO migrator_v2 — reindexation anglais -> multilingue (chunks=2)")
    print("=" * 64)
    n = migrate(old, new, ckpt, chunk_size=2)
    print(f"\nVerifications :")
    print(f"  ancien={old.count()} | nouveau={new.count()} | migres={n}")
    sample = new.get(ids=["node_001"], include=["metadatas", "documents"])
    print(f"  node_001 (etait 'lesson_certified') -> tier={sample['metadatas'][0]['tier_status']} "
          f"label={sample['metadatas'][0]['injected_label']}")
    sample2 = new.get(ids=["node_000"], include=["metadatas"])
    print(f"  node_000 (source=chat) -> tier={sample2['metadatas'][0]['tier_status']} "
          f"label={sample2['metadatas'][0]['injected_label']}")
    # preuve de retrieval multilingue sur une reformulation
    res = new.query(query_texts=["Qu'est-ce qui taille le superflu pendant le sommeil ?"], n_results=1)
    print(f"  requete reformulee -> retrouve : {res['ids'][0]}")
    print("=" * 64)
