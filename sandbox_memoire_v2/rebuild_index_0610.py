# -*- coding: utf-8 -*-
"""REBUILD INDEX HNSW (10/06) — detecte par l'OPA de Promethee (RECALL-1 ❌ des le 1er run).
Pathologie : premium_lesson_011 a distance cosine 0.113 d'une paraphrase mais ABSENT du
top-100 (noeud faiblement connecte apres les upserts massifs du catchup, posterieurs au
seed premium). Self-recall global pourtant 30/30 -> defaut LOCALISE du graphe, pas des donnees.

Remede : RECONSTRUIRE l'index en un seul passage -> nouvelle collection collective_wisdom_v2
(cosine EXPLICITE, embedder multilingue persiste), transfert AVEC les embeddings stockes
(sains, cos=1.0 verifie) -> zero recalcul, rapide. L'ancienne v2_test reste en place (rollback).
A LANCER SERVEUR ARRETE."""
import sys
import chromadb
from chromadb.utils import embedding_functions

DB = "C:/MesProjets/PROMETHEE_V11_restructuration2026/memory/default/chroma_db"
SRC = "collective_wisdom_v2_test"
DST = "collective_wisdom_v2"
ML = "paraphrase-multilingual-MiniLM-L12-v2"
CHUNK = 400

ml = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=ML)
client = chromadb.PersistentClient(path=DB)
src = client.get_or_create_collection(SRC, embedding_function=ml)

# Collection neuve : cosine EXPLICITE (la config v2_test l'avait aussi, on la grave)
try:
    client.delete_collection(DST)   # rejouabilite du script (si run precedent partiel)
    print("(collection %s preexistante supprimee — rebuild propre)" % DST)
except Exception:
    pass
dst = client.create_collection(DST, embedding_function=ml, metadata={"hnsw:space": "cosine"})

total = src.count()
print("source %s : %d docs -> reconstruction dans %s" % (SRC, total, DST))
off = 0
while off < total:
    batch = src.get(limit=CHUNK, offset=off, include=["documents", "metadatas", "embeddings"])
    ids = batch["ids"]
    if not ids:
        break
    embs = batch["embeddings"]
    # upsert AVEC embeddings stockes -> AUCUN recalcul, insertion d'un seul tenant
    dst.upsert(ids=ids, documents=batch["documents"], metadatas=batch["metadatas"],
               embeddings=[list(e) for e in embs])
    off += len(ids)
    print("  +%d (%d/%d)" % (len(ids), off, total))

print("\n=== VERIFICATIONS ===")
print("counts : src=%d | dst=%d" % (src.count(), dst.count()))
prem = dst.get(where={"tier_status": "PREMIUM"})
print("premium dans dst : %d" % len(prem["ids"]))

# LE test que l'OPA a echoue : la paraphrase doit retrouver premium_lesson_011
q = ("Une boucle anti-repetition doit s appuyer sur l intention structuree stockee des "
     "l origine de l action, jamais sur une re-interpretation fragile par mots-cles")
r = dst.query(query_texts=[q], n_results=4)
ids4 = (r.get("ids") or [[]])[0]
dists4 = (r.get("distances") or [[]])[0]
print("paraphrase 12e lecon -> top-4 :")
for i, _id in enumerate(ids4):
    print("  %d. %s d=%.3f" % (i + 1, _id[:34], dists4[i]))
ok = "premium_lesson_011" in ids4
print("premium_lesson_011 dans top-4 : %s" % ok)

# la requete EXACTE de l'epreuve RECALL-1 de l'OPA
q2 = "anti-repetition intention structuree stockee a la source mots-cles"
r2 = dst.query(query_texts=[q2], n_results=4)
ids2 = (r2.get("ids") or [[]])[0]
print("requete RECALL-1 OPA -> premium_lesson_011 dans top-4 : %s" % ("premium_lesson_011" in ids2))

sys.exit(0 if ok else 1)
