# -*- coding: utf-8 -*-
"""CHANTIER RESEAU SYNAPTIQUE V2 — PHASE 0 : les 3 MESURES FONDATRICES (10/06 soir).
READ-ONLY absolu (on lit le state JSON persiste, on ne touche ni au fichier ni au runtime).

Contexte : osteoporose AGGRAVEE 66% (06/06) -> 82.8% (10/06, vision representative).
Blueprint V2 : trou n.1 = hash MD5 exact -> synonymes = noeuds etrangers -> DILUTION
hebbienne (cause suspectee de l'osteoporose). Avant tout geste : MESURER.

M1. DUPLICATION SEMANTIQUE : combien de noeuds sont des quasi-synonymes (cos > seuils) ?
M2. AUTOPSIE DE L'AGONIE : les ~16k synapses < 0.10 — jamais formees ou mortes-vivantes ?
M3. SIMULATION D'ELAGAGE FRANC (copie RAM) : quel reseau resterait si l'oubli devenait
    une feature (elague < 0.10 jamais consolidees) ?"""
import json
import time

import numpy as np

STATE = r"C:\MesProjets\PROMETHEE_V11_restructuration2026\memory\synaptic_network.json"
ML = "paraphrase-multilingual-MiniLM-L12-v2"

d = json.load(open(STATE, encoding="utf-8"))
nodes = d["nodes"]
syns = d["synapses"]
print("Etat : %d noeuds, %d synapses" % (len(nodes), len(syns)))
now = time.time()

# ════════ M1 — DUPLICATION SEMANTIQUE ════════
print("\n===== M1 — DUPLICATION SEMANTIQUE (le trou n.1 du blueprint) =====")
from sentence_transformers import SentenceTransformer
model = SentenceTransformer(ML)
nids = list(nodes.keys())
concepts = [nodes[n].get("concept", "") for n in nids]
t0 = time.time()
emb = model.encode(concepts, batch_size=256, show_progress_bar=False, normalize_embeddings=True)
print("embeddings : %d concepts en %.1fs" % (len(concepts), time.time() - t0))
emb = np.asarray(emb, dtype=np.float32)
# matrice de similarite par blocs (4315x4315 ~ 74MB float32 : ok d'un coup)
sim = emb @ emb.T
np.fill_diagonal(sim, 0.0)
for seuil in (0.95, 0.90, 0.85, 0.80):
    pairs = int((sim > seuil).sum()) // 2
    # noeuds impliques dans au moins un doublon
    impliques = int(((sim > seuil).any(axis=1)).sum())
    print("  cos > %.2f : %6d paires | %5d noeuds impliques (%.1f%%)"
          % (seuil, pairs, impliques, 100.0 * impliques / len(nids)))
# top-10 exemples de quasi-doublons a 0.90 (la preuve par l'exemple)
print("  --- exemples (cos > 0.90) ---")
idx = np.argwhere(sim > 0.90)
vus = set()
shown = 0
for i, j in idx:
    if i < j and (i, j) not in vus:
        vus.add((i, j))
        print("    %.3f | %-42s <> %s" % (sim[i, j], concepts[i][:42], concepts[j][:48]))
        shown += 1
        if shown >= 10:
            break

# ════════ M2 — AUTOPSIE DE L'AGONIE ════════
print("\n===== M2 — AUTOPSIE DE L'AGONIE (les synapses < 0.10) =====")
agonie = [s for s in syns.values() if float(s.get("weight", 0)) < 0.10]
print("agonie : %d / %d (%.1f%%)" % (len(agonie), len(syns), 100.0 * len(agonie) / len(syns)))
fc = [int(s.get("formation_count", 1)) for s in agonie]
jamais_formees = sum(1 for c in fc if c <= 2)
age_j = [(now - float(s.get("created_at", now))) / 86400 for s in agonie]
stale_j = [(now - float(s.get("last_strengthened", s.get("created_at", now)))) / 86400 for s in agonie]
incubees = sum(1 for s in agonie if s.get("is_incubated"))
print("  formation_count <= 2 (jamais vraiment formees) : %d (%.1f%%)"
      % (jamais_formees, 100.0 * jamais_formees / len(agonie)))
print("  formation_count median : %d | max : %d" % (sorted(fc)[len(fc)//2], max(fc)))
print("  age median : %.1f j | sans renforcement depuis (median) : %.1f j"
      % (sorted(age_j)[len(age_j)//2], sorted(stale_j)[len(stale_j)//2]))
print("  incubees (Sanctuaire V19, protegees) : %d" % incubees)
par_type = {}
for s in agonie:
    par_type[s.get("synapse_type", "?")] = par_type.get(s.get("synapse_type", "?"), 0) + 1
print("  par type :", dict(sorted(par_type.items(), key=lambda kv: -kv[1])))

# ════════ M3 — SIMULATION D'ELAGAGE FRANC (copie RAM, rien n'est ecrit) ════════
print("\n===== M3 — SIMULATION 'OUBLI = FEATURE' (sur copie) =====")
for (w_max, fc_max, stale_min_j) in [(0.10, 2, 7), (0.10, 3, 3), (0.12, 2, 14)]:
    survivants = {}
    for k, s in syns.items():
        w = float(s.get("weight", 0))
        f = int(s.get("formation_count", 1))
        stale = (now - float(s.get("last_strengthened", s.get("created_at", now)))) / 86400
        if s.get("is_incubated"):
            survivants[k] = s; continue   # Sanctuaire V19 : jamais touche
        if w < w_max and f <= fc_max and stale >= stale_min_j:
            continue                       # elague (bruit isole, jamais consolide, froid)
        survivants[k] = s
    poids = sorted(float(s["weight"]) for s in survivants.values())
    n_ago = sum(1 for w in poids if w < 0.10)
    deg = {}
    for s in survivants.values():
        for nid in (s["source"], s["target"]):
            deg[nid] = deg.get(nid, 0) + 1
    orphelins = sum(1 for n in nodes if n not in deg)
    print("  regle (w<%.2f, fc<=%d, froid>=%dj) : reste %5d synapses (-%d) | agonie %4.1f%% | "
          "mediane %.3f | orphelins %d"
          % (w_max, fc_max, stale_min_j, len(survivants), len(syns) - len(survivants),
             100.0 * n_ago / len(survivants) if survivants else 0,
             poids[len(poids)//2] if poids else 0, orphelins))

print("\n(READ-ONLY : rien n'a ete ecrit. Ces chiffres fondent le plan de chantier.)")
