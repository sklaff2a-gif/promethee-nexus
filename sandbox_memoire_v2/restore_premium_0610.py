# -*- coding: utf-8 -*-
"""RESTAURATION (10/06 apres-midi) — incident MEMORY_CLEANUP 11:06 : la purge (routee vers
la canonique par le Full Switch) a supprime 100 docs SANS datation, dont 17/18 lecons
PREMIUM (timestamp absent -> ts=0 -> 'infiniment vieux'). Le fix d'immunite est code ;
ici on restaure le DIFFERENTIEL depuis collective_wisdom_v2_test (sauvegarde naturelle,
intacte) avec les embeddings stockes (zero recalcul). A LANCER SERVEUR ARRETE."""
import chromadb

DB = "C:/MesProjets/PROMETHEE_V11_restructuration2026/memory/default/chroma_db"
client = chromadb.PersistentClient(path=DB)
src = client.get_or_create_collection("collective_wisdom_v2_test")
dst = client.get_or_create_collection("collective_wisdom_v2")

print("AVANT : src=%d | dst=%d" % (src.count(), dst.count()))
dst_ids = set(dst.get()["ids"])
print("ids dans dst :", len(dst_ids))

restaures = 0
prem_restaures = 0
off = 0
CHUNK = 400
total = src.count()
while off < total:
    batch = src.get(limit=CHUNK, offset=off, include=["documents", "metadatas", "embeddings"])
    ids = batch["ids"]
    if not ids:
        break
    manquants = [i for i, _id in enumerate(ids) if _id not in dst_ids]
    if manquants:
        m_ids = [ids[i] for i in manquants]
        m_docs = [batch["documents"][i] for i in manquants]
        m_metas = [batch["metadatas"][i] for i in manquants]
        m_embs = [list(batch["embeddings"][i]) for i in manquants]
        dst.upsert(ids=m_ids, documents=m_docs, metadatas=m_metas, embeddings=m_embs)
        restaures += len(m_ids)
        prem_restaures += sum(1 for m in m_metas if (m or {}).get("tier_status") == "PREMIUM")
    off += len(ids)

print("restaures : %d docs (dont %d PREMIUM)" % (restaures, prem_restaures))
prem = dst.get(where={"tier_status": "PREMIUM"})
print("APRES : dst=%d | premium=%d" % (dst.count(), len(prem["ids"])))

# Verif sentinelle : la 12e lecon est-elle a nouveau trouvable (l'epreuve RECALL-1 de l'OPA) ?
from chromadb.utils import embedding_functions
ml = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="paraphrase-multilingual-MiniLM-L12-v2")
dst2 = client.get_or_create_collection("collective_wisdom_v2", embedding_function=ml)
r = dst2.query(query_texts=["anti-repetition intention structuree stockee a la source mots-cles"], n_results=4)
ids4 = (r.get("ids") or [[]])[0]
print("RECALL-1 sentinelle -> premium_lesson_011 dans top-4 :", "premium_lesson_011" in ids4)
