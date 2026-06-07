# -*- coding: utf-8 -*-
"""V23.3_RECO — Scan flash READ-ONLY des metadonnees de collective_wisdom (live).

Ouvre le chroma.sqlite3 en mode `ro&immutable=1` : SQLite lit SANS lock, zero risque
pour le serveur live meme s'il ecrit en parallele. Compte les CLES de metadonnees et,
pour les cles semantiques candidates, leurs valeurs dominantes -> calibrer infer_tier.
Argument : chemin du chroma.sqlite3 live.
"""
import sqlite3
import sys

db = sys.argv[1]
con = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
cur = con.cursor()

tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("TABLES:", tables)

# table de metadonnees ChromaDB : embedding_metadata(id, key, string_value, int_value, ...)
meta_table = "embedding_metadata" if "embedding_metadata" in tables else None
if not meta_table:
    # fallback : trouver une table avec une colonne 'key'
    for t in tables:
        cols = [c[1] for c in cur.execute(f"PRAGMA table_info({t})").fetchall()]
        if "key" in cols and any("value" in c for c in cols):
            meta_table = t
            break

if not meta_table:
    print("Aucune table de metadonnees trouvee.")
    sys.exit(0)

cols = [c[1] for c in cur.execute(f"PRAGMA table_info({meta_table})").fetchall()]
print(f"\nTable metadonnees: {meta_table} | colonnes: {cols}")
total = cur.execute(f"SELECT count(*) FROM {meta_table}").fetchone()[0]
print(f"Total lignes de metadonnees: {total}")

# combien de documents distincts (par id d'embedding)
id_col = "id" if "id" in cols else cols[0]
try:
    ndocs = cur.execute(f"SELECT count(DISTINCT {id_col}) FROM {meta_table}").fetchone()[0]
    print(f"Documents distincts (approx): {ndocs}")
except Exception:
    pass

print("\n=== CLES DE METADONNEES (frequence decroissante) ===")
for k, c in cur.execute(f"SELECT key, count(*) c FROM {meta_table} GROUP BY key ORDER BY c DESC").fetchall():
    print(f"  {c:6}  {k}")

# valeurs dominantes pour les cles semantiques candidates
sval = "string_value" if "string_value" in cols else None
if sval:
    for sem in ("source", "type", "tag", "tags", "category", "intent", "origin", "agent", "kind", "doc_type"):
        rows = cur.execute(
            f"SELECT {sval}, count(*) c FROM {meta_table} WHERE key=? AND {sval} IS NOT NULL "
            f"GROUP BY {sval} ORDER BY c DESC LIMIT 12", (sem,)).fetchall()
        if rows:
            print(f"\n=== valeurs de '{sem}' (top 12) ===")
            for v, c in rows:
                vv = (v or "")[:60]
                print(f"  {c:6}  {vv}")

con.close()
