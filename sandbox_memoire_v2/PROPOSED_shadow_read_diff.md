# PROPOSITION DE GREFFE — Shadow Reader Phase 1 (V23.2_DIFF)

> ⚠️ **NON APPLIQUE AU LIVE.** Artefact de reference pour le deploiement A FROID.
> Cible : `core/vector_store.py`, methode `query_documents` (l.180-190).
> Mandat de modelisation : le fichier de production reste sous triple verrou.

## 1. En-tete de fichier (apres les imports de `core/vector_store.py`)

```python
# --- Shadow Reading (Phase 1, migration Memoire V2) — DESACTIVE PAR DEFAUT ---
SHADOW_READ_ENABLED = False   # KILL-SWITCH : False = ZERO overhead, comportement d'origine 100% intact
SHADOW_LOG_PATH = os.path.join("memory", "shadow_read_v2.jsonl")
SHADOW_COLLECTION_SUFFIX = "_v2_test"
SHADOW_EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
```

## 2. `query_documents` — AVANT / APRES

```diff
     def query_documents(self, query_texts, n_results=None, collection_name="collective_wisdom"):
         """Recherche dans une collection spécifique."""
         try:
             if n_results is None:
                 from config import Config
                 n_results = getattr(Config, "RAG_DEFAULT_N_RESULTS", 3)
             col = self._get_collection(collection_name)
-            return col.query(query_texts=query_texts, n_results=n_results)
+            result_old = col.query(query_texts=query_texts, n_results=n_results)  # MAITRE EXCLUSIF
+
+            # --- GREFFE SHADOW READING (Phase 1) — fantome passif ---
+            # (1) DISJONCTEUR : court-circuit TOTAL si coupe, AVANT toute instanciation temoin
+            if SHADOW_READ_ENABLED:
+                self._shadow_observe(query_texts, n_results, collection_name, result_old)
+
+            return result_old   # (3) INVARIANT DE FLUX : on retourne TOUJOURS l'ancien, inchange
         except Exception as e:
             print(f"❌ Erreur Mémoire (Query): {e}")
             return None
```

## 3. Nouvelles methodes (le fantome + la collection temoin)

```python
    def _shadow_observe(self, query_texts, n_results, collection_name, result_old):
        """(2) TRY/EXCEPT BORG : interroge la collection temoin multilingue, compare a
        result_old, logue l'ecart. AUCUNE exception ne remonte vers query_documents."""
        try:
            shadow_col = self._get_shadow_collection(collection_name + SHADOW_COLLECTION_SUFFIX)
            if shadow_col is None:
                return
            import time, json
            from datetime import datetime
            t0 = time.perf_counter()
            result_new = shadow_col.query(query_texts=query_texts, n_results=n_results)
            lat_new = (time.perf_counter() - t0) * 1000.0
            old_ids = ((result_old or {}).get("ids") or [[]])[0]
            new_ids = ((result_new or {}).get("ids") or [[]])[0]
            rec = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "collection": collection_name,
                "query": (query_texts[0] if query_texts else "")[:80],
                "old_ids": old_ids, "new_ids": new_ids,
                "overlap": len(set(old_ids) & set(new_ids)),
                "mismatch": set(old_ids) != set(new_ids),
                "lat_new_ms": round(lat_new, 2),
            }
            with open(SHADOW_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            try:  # le BORG ne laisse RIEN fuir, pas meme l'echec d'ecriture du log
                with open(SHADOW_LOG_PATH + ".err", "a", encoding="utf-8") as f:
                    f.write(f"[shadow] {e}\n")
            except Exception:
                pass

    def _get_shadow_collection(self, shadow_name):
        """Recupere/cree la collection temoin avec l'embedder MULTILINGUE. Retourne None
        si l'embedder n'est pas dispo (le shadow s'abstient proprement, sans casser)."""
        if not hasattr(self, "_shadow_collections"):
            self._shadow_collections = {}
        if shadow_name in self._shadow_collections:
            return self._shadow_collections[shadow_name]
        try:
            from chromadb.utils import embedding_functions
            ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=SHADOW_EMBED_MODEL)
            col = self.client.get_or_create_collection(name=shadow_name, embedding_function=ef)
            self._shadow_collections[shadow_name] = col
            return col
        except Exception:
            self._shadow_collections[shadow_name] = None  # on cache l'echec : pas de retry par requete
            return None
```

## 4. Les trois criteres d'etancheite — verifies

| Critere | Ou | Garantie |
|---|---|---|
| **(1) Disjoncteur statique** | `if SHADOW_READ_ENABLED:` | court-circuit AVANT toute instanciation temoin -> zero overhead si False |
| **(2) Try/Except Borg** | `_shadow_observe` + `_get_shadow_collection` | toute exception (VRAM, embedder absent, timeout) consignee, jamais levee |
| **(3) Invariant de flux** | `result_old` calcule en premier, retourne inchange | le fantome ne fait que LIRE result_old ; le tableau de jetons renvoye a l'inference est intact a l'octet pres |

## 5. NUANCE HONNETE (dependance a expliciter)

Le shadow compare `result_old` (anglais) a la collection temoin **multilingue**. **Cette collection doit etre PEUPLEE (reindexee) pour que la comparaison ait du sens** — c'est la Phase 2 (Dual Ingestion / reindexation des 5600 vecteurs). Tant que le temoin est vide : `new_ids=[]`, mismatch logue mais **inoffensif** (le diff reste etanche).

**Ordre de deploiement reel a froid** :
1. Appliquer ce diff avec `SHADOW_READ_ENABLED=False` -> zero effet, on valide juste que le code charge.
2. Reindexer le temoin `collective_wisdom_v2_test` en multilingue (Phase 2, script separe).
3. Passer `SHADOW_READ_ENABLED=True` -> collecte des ecarts a froid dans `shadow_read_v2.jsonl`.
4. Analyser le delta de pertinence (anglais vs multilingue) sur les vraies requetes.
5. Si concluant -> Phase 3 (Canary FREE_TIME), puis Phase 4 (Full Switch).
