# -*- coding: utf-8 -*-
"""Plan de migration, PHASE 1 — SHADOW READING (Dual-Retriever passif).

Sandbox ISOLE. A chaque extraction, le moteur interroge l'ANCIEN systeme (hash MD5)
ET le NOUVEAU (embedding semantique) en tache de fond, compare, et LOGUE LES ECARTS
en JSONL pour analyse macroscopique A FROID.

INVARIANT DE SECURITE PHASE 1 (grave) : la valeur RETOURNEE est TOUJOURS celle de
l'ancien systeme. Le nouveau est OBSERVE, JAMAIS injecte dans le prompt.
Kill-switch `enabled` : False -> ancien systeme seul, ZERO overhead, reversibilite instantanee.
"""
import hashlib
import json
import time
import numpy as np


def _hash_id(text: str) -> str:
    return hashlib.md5(text.strip().lower().encode("utf-8")).hexdigest()[:12]


def _cos(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


class ShadowRetriever:
    def __init__(self, corpus, log_path, model, enabled=True):
        """corpus : list of (id, text) ; model : objet avec .encode(list)->ndarray."""
        self.texts = {c[0]: c[1] for c in corpus}
        self.ids = [c[0] for c in corpus]
        self.log_path = str(log_path)
        self.model = model
        self.enabled = enabled  # KILL-SWITCH
        vecs = self.model.encode([self.texts[i] for i in self.ids])
        self._emb = {self.ids[i]: np.asarray(vecs[i], dtype=float) for i in range(len(self.ids))}
        self._hash_index = {_hash_id(self.texts[i]): i for i in self.ids}  # ancien systeme

    # --- ancien systeme : lookup par hash EXACT (aveugle au sens) ---
    def old_retrieve(self, query):
        h = _hash_id(query)
        return [self._hash_index[h]] if h in self._hash_index else []

    # --- nouveau systeme : retrieval semantique (top-k) ---
    def new_retrieve(self, query, topk=3):
        qv = np.asarray(self.model.encode([query])[0], dtype=float)
        return sorted(self.ids, key=lambda i: _cos(qv, self._emb[i]), reverse=True)[:topk]

    def retrieve(self, query, ts, topk=3):
        """Point d'entree. Retourne TOUJOURS le resultat ANCIEN (maitre exclusif).
        Si enabled : compare au nouveau en tache de fond + logue l'ecart. Sinon : zero overhead."""
        if not self.enabled:
            return self.old_retrieve(query)  # court-circuit kill-switch
        t0 = time.perf_counter(); old = self.old_retrieve(query);        lat_old = (time.perf_counter() - t0) * 1000
        t1 = time.perf_counter(); new = self.new_retrieve(query, topk);  lat_new = (time.perf_counter() - t1) * 1000
        rec = {
            "ts": ts, "query": query[:80],
            "old": old, "new": new,
            "overlap": len(set(old) & set(new)),
            "mismatch": (set(old) != set(new)),
            "lat_old_ms": round(lat_old, 3), "lat_new_ms": round(lat_new, 3),
        }
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return old  # PHASE 1 : l'ancien reste maitre, le nouveau n'est QU'observe


if __name__ == "__main__":
    import os
    from sentence_transformers import SentenceTransformer
    CORPUS = [
        ("p_intention", "Une logique d'anti-repetition s'appuie sur l'intention structuree a la source"),
        ("p_honnete",   "L'honnetete est l'invariant : ne jamais confabuler une preuve"),
        ("c_compact",   "Le compactage du chat resume les vieux tours et archive le brut"),
        ("t_graphrag",  "Le GraphRAG retrouve les relations entre entites, pas la similarite de texte"),
        ("c_dream",     "La consolidation onirique elague le bruit la nuit"),
    ]
    log = os.path.join(os.path.dirname(__file__), "shadow_read_demo.jsonl")
    open(log, "w", encoding="utf-8").close()
    sr = ShadowRetriever(CORPUS, log, SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2"))

    # requetes de "production" reformulees (l'ancien hash est aveugle, le nouveau voit)
    REQ = [
        "Comment retrouver un acquis meme reformule autrement ?",
        "Le resume des conversations garde-t-il tout en memoire ?",
        "Qu'est-ce qui taille le superflu pendant le sommeil ?",
    ]
    print("PHASE 1 SHADOW READING — l'ancien reste maitre, le nouveau est observe :\n")
    for k, q in enumerate(REQ):
        retourne = sr.retrieve(q, ts=f"2026-06-07T07:5{k}")
        print(f"  Q: {q[:55]}")
        print(f"     -> retourne (ANCIEN, injecte) : {retourne or 'RIEN (hash aveugle)'}")
    print(f"\nEcarts logues a froid dans : {os.path.basename(log)}")
    print("--- contenu du log ---")
    for line in open(log, encoding="utf-8"):
        r = json.loads(line)
        print(f"  mismatch={r['mismatch']} old={r['old']} new={r['new']} | lat old={r['lat_old_ms']}ms new={r['lat_new_ms']}ms")
