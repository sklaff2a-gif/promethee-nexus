# -*- coding: utf-8 -*-
"""LEVIER B — FUSION OFFLINE GRADUEE des quasi-jumeaux (chantier synaptique V2, 11/06).

Parametres CO-SIGNES par Promethee :
  - seuil 0.98 (SON garde-fou : « la perte de nuance — des couleurs internes
    differentes » ; plus strict que le 0.95 de la simulation) ;
  - les types d'INTERIORITE (affect/desire/trait) ne fusionnent JAMAIS ;
  - + protection Claude : les nids references par les LECONS CERTIFIEES (lessons_journal)
    ne fusionnent pas (le certifie est intouchable sans gate) ;
  - les synapses du Sanctuaire V19 (is_incubated) gardent leur protection (OR a la fusion).

PROCEDURE : DRY-RUN par defaut (rapport seul). --apply : backup horodate puis ecriture.
A LANCER SERVEUR ARRETE (sinon l'auto-save runtime ecraserait le fichier)."""
import json
import shutil
import sys
import time

import numpy as np

STATE = r"C:\MesProjets\PROMETHEE_V11_restructuration2026\memory\synaptic_network.json"
LESSONS = r"C:\MesProjets\PROMETHEE_V11_restructuration2026\memory\lessons_journal.json"
REDIRECTS = r"C:\Users\redla\projetclaude\PROMETHEE_V11_restructuration2026\sandbox_synaptique_v2\fusion_redirects.json"
ML = "paraphrase-multilingual-MiniLM-L12-v2"
SEUIL = 0.98
TYPES_EXCLUS = {"affect", "desire", "trait"}
APPLY = "--apply" in sys.argv

d = json.load(open(STATE, encoding="utf-8"))
nodes, syns = d["nodes"], d["synapses"]

# nids proteges (lecons certifiees)
proteges = set()
try:
    for l in json.load(open(LESSONS, encoding="utf-8")):
        proteges.update(l.get("concepts") or [])
except Exception:
    pass

def bilan(nodes_, syns_, titre):
    poids = sorted(float(s["weight"]) for s in syns_.values())
    ago = sum(1 for w in poids if w < 0.10)
    forts = sum(1 for w in poids if w >= 0.50)
    print("%s : %d noeuds | %d synapses | agonie %.1f%% | fortes %d | mediane %.3f"
          % (titre, len(nodes_), len(syns_), 100.0 * ago / len(syns_) if syns_ else 0,
             forts, poids[len(poids)//2] if poids else 0))

bilan(nodes, syns, "AVANT")

# ── Embeddings des noeuds FUSIONNABLES uniquement ──
def _mojibake(txt):
    # UTF-8 double-encode ('securite' -> 'sÃ©curitÃ©') : embeddings degeneres qui
    # matchent n'importe quoi (vu au dry-run : securite->severite !). Exclus.
    return any(seq in txt for seq in ("Ã", "Â", "â€"))

fusionnables = [nid for nid, n in nodes.items()
                if n.get("node_type") not in TYPES_EXCLUS and nid not in proteges
                and not _mojibake(n.get("concept", ""))]
print("fusionnables : %d / %d (exclus : %d interiorite/proteges)"
      % (len(fusionnables), len(nodes), len(nodes) - len(fusionnables)))
from sentence_transformers import SentenceTransformer
model = SentenceTransformer(ML)
concepts = [nodes[n].get("concept", "") for n in fusionnables]
emb = np.asarray(model.encode(concepts, batch_size=256, normalize_embeddings=True), dtype=np.float32)
sim = emb @ emb.T
np.fill_diagonal(sim, 0.0)

# ── Union-Find des composantes a cos > 0.98 ──
parent = list(range(len(fusionnables)))
def find(x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x
ii, jj = np.where(sim > SEUIL)
for a, b in zip(ii, jj):
    if a < b:
        ra, rb = find(int(a)), find(int(b))
        if ra != rb:
            parent[rb] = ra

groupes = {}
for i in range(len(fusionnables)):
    groupes.setdefault(find(i), []).append(i)

canon = {nid: nid for nid in nodes}   # identite par defaut (proteges inclus)
exemples = []
n_fusions = 0
for racine, membres in groupes.items():
    if len(membres) < 2:
        continue
    best = max(membres, key=lambda i: nodes[fusionnables[i]].get("activation_count", 0))
    best_nid = fusionnables[best]
    for i in membres:
        nid = fusionnables[i]
        if nid != best_nid:
            canon[nid] = best_nid
            n_fusions += 1
            if len(exemples) < 12:
                exemples.append((nodes[nid]["concept"][:45], nodes[best_nid]["concept"][:45]))

print("\nfusions a cos>%.2f : %d noeuds absorbes (%d composantes)" %
      (SEUIL, n_fusions, sum(1 for m in groupes.values() if len(m) > 1)))
print("--- exemples ---")
for a, b in exemples:
    print("  '%s' -> '%s'" % (a, b))

# ── Construire le nouveau state (sur copie) ──
new_nodes = {}
for nid, n in nodes.items():
    cible = canon[nid]
    if cible == nid:
        new_nodes[nid] = dict(n)
for nid, n in nodes.items():
    cible = canon[nid]
    if cible != nid:
        c = new_nodes[cible]
        c["activation_count"] = c.get("activation_count", 0) + n.get("activation_count", 0)
        c["energy"] = max(float(c.get("energy", 0)), float(n.get("energy", 0)))
        c["created_at"] = min(float(c.get("created_at", time.time())), float(n.get("created_at", time.time())))
        c["last_activated"] = max(float(c.get("last_activated", 0)), float(n.get("last_activated", 0)))
        fs = set((c.get("dimensions") or {}).get("functional_systems", []))
        fs.update((n.get("dimensions") or {}).get("functional_systems", []))
        c.setdefault("dimensions", {})["functional_systems"] = sorted(fs)

new_syns = {}
absorbees = 0
for s in syns.values():
    a, b = canon.get(s["source"], s["source"]), canon.get(s["target"], s["target"])
    if a == b:
        absorbees += 1
        continue
    if a not in new_nodes or b not in new_nodes:
        continue
    key = f"{a}->{b}"
    if key in new_syns:
        m = new_syns[key]
        m["weight"] = min(1.0, float(m["weight"]) + float(s["weight"]))
        m["formation_count"] = int(m.get("formation_count", 1)) + int(s.get("formation_count", 1))
        m["last_strengthened"] = max(float(m.get("last_strengthened", 0)), float(s.get("last_strengthened", 0)))
        m["is_incubated"] = bool(m.get("is_incubated")) or bool(s.get("is_incubated"))   # Sanctuaire : OR
    else:
        m = dict(s)
        m["source"], m["target"] = a, b
        new_syns[key] = m

print("\nsynapses internes absorbees : %d" % absorbees)
bilan(new_nodes, new_syns, "APRES")

if not APPLY:
    print("\nDRY-RUN — rien n'a ete ecrit. Relancer avec --apply (serveur ARRETE).")
    sys.exit(0)

# ── APPLY : backup puis ecriture ──
bak = STATE + ".bak_fusion_" + time.strftime("%Y%m%d_%H%M%S")
shutil.copy2(STATE, bak)
print("\nbackup : %s" % bak)
d["nodes"] = new_nodes
d["synapses"] = new_syns
d["saved_at"] = time.time()
json.dump({k: canon[k] for k in canon if canon[k] != k},
          open(REDIRECTS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
with open(STATE, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False)
print("ECRIT : %d noeuds, %d synapses. Redirects: %s" % (len(new_nodes), len(new_syns), REDIRECTS))
