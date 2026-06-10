# -*- coding: utf-8 -*-
"""PHASE 0bis — SIMULATION DE FUSION SEMANTIQUE (sur COPIE RAM, rien n'est ecrit).
La mesure decisive : si on fusionne les quasi-doublons (cos > seuil) en re-routant leurs
synapses vers un noeud canonique (poids additionnes, cap 1.0), quel reseau obtient-on ?
C'est le chiffre qui dit si la fusion guerit l'osteoporose."""
import json
import time

import numpy as np

STATE = r"C:\MesProjets\PROMETHEE_V11_restructuration2026\memory\synaptic_network.json"
ML = "paraphrase-multilingual-MiniLM-L12-v2"

d = json.load(open(STATE, encoding="utf-8"))
nodes = d["nodes"]
syns = d["synapses"]
print("Etat initial : %d noeuds, %d synapses, agonie %.1f%%"
      % (len(nodes), len(syns),
         100.0 * sum(1 for s in syns.values() if float(s["weight"]) < 0.10) / len(syns)))

from sentence_transformers import SentenceTransformer
model = SentenceTransformer(ML)
nids = list(nodes.keys())
concepts = [nodes[n].get("concept", "") for n in nids]
emb = np.asarray(model.encode(concepts, batch_size=256, normalize_embeddings=True), dtype=np.float32)
sim = emb @ emb.T
np.fill_diagonal(sim, 0.0)

for SEUIL in (0.95, 0.90):
    # Union-Find des composantes de quasi-doublons
    parent = list(range(len(nids)))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    ii, jj = np.where(sim > SEUIL)
    for a, b in zip(ii, jj):
        if a < b:
            union(int(a), int(b))
    # canonique = le noeud le plus ACTIVE de sa composante (le plus vivant gagne)
    groupes = {}
    for i in range(len(nids)):
        groupes.setdefault(find(i), []).append(i)
    canon = {}
    fusionnes = 0
    for racine, membres in groupes.items():
        best = max(membres, key=lambda i: nodes[nids[i]].get("activation_count", 0))
        for i in membres:
            canon[nids[i]] = nids[best]
        if len(membres) > 1:
            fusionnes += len(membres) - 1
    n_final = len(nids) - fusionnes

    # Re-router les synapses vers les canoniques, ADDITIONNER les poids (cap 1.0)
    merged = {}
    for s in syns.values():
        a, b = canon[s["source"]], canon[s["target"]]
        if a == b:
            continue   # synapse interne a une composante -> absorbee par la fusion
        key = (a, b) if a < b else (b, a)
        if key in merged:
            m = merged[key]
            m["weight"] = min(1.0, m["weight"] + float(s["weight"]))
            m["formation_count"] += int(s.get("formation_count", 1))
        else:
            merged[key] = {"weight": float(s["weight"]),
                           "formation_count": int(s.get("formation_count", 1))}

    poids = sorted(m["weight"] for m in merged.values())
    n_ago = sum(1 for w in poids if w < 0.10)
    n_fort = sum(1 for w in poids if w >= 0.50)
    deg = {}
    for (a, b) in merged.keys():
        deg[a] = deg.get(a, 0) + 1
        deg[b] = deg.get(b, 0) + 1
    canoniques = set(canon.values())
    orphelins = sum(1 for n in canoniques if n not in deg)
    print("\n--- FUSION cos > %.2f ---" % SEUIL)
    print("  noeuds   : %d -> %d (-%d fusionnes)" % (len(nids), n_final, fusionnes))
    print("  synapses : %d -> %d (dont absorbees internes)" % (len(syns), len(merged)))
    print("  agonie   : %.1f%% -> %.1f%%" % (82.8, 100.0 * n_ago / len(merged)))
    print("  mediane  : 0.080 -> %.3f" % poids[len(poids) // 2])
    print("  fortes (>=0.5) : 320 -> %d" % n_fort)
    print("  orphelins : %d" % orphelins)
    print("  saturation : %.0f%% -> %.0f%% (cap 20000)"
          % (100.0 * len(syns) / 20000, 100.0 * len(merged) / 20000))

print("\n(COPIE RAM uniquement — le reseau reel n'a pas ete touche.)")
