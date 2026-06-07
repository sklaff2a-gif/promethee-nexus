# -*- coding: utf-8 -*-
"""Brique B (ii) — Moteur de contextualisation : FUSION A + B.

Sandbox ISOLE (ne touche PAS au ChromaDB live). Chaine cinetique integree :
  ECRITURE -> douane (validate_metadata, Brique B-i) -> META[id] avec injected_label
  LECTURE  -> routeur (etincelle locale, proto A) -> assemble le contexte en LISANT
              injected_label des metadonnees (0 re-derivation -> pas de desalignement)
  ASSEMBLAGE HYBRIDE -> 1 consigne systeme globale (legende) + sources prefixees du label

Demontre le doute metacognitif : un PREMIUM drapote voyage avec son blame.
"""
import numpy as np
from sentence_transformers import SentenceTransformer
from schema_tiers import validate_metadata, flag_contradiction, derive_label

# --- ECRITURE : chaque acquis passe la DOUANE (validate_metadata) ---
RAW = [
    ("p_intention", "Une logique d'anti-repetition s'appuie sur l'intention structuree stockee a la source", {"tier_status": "PREMIUM"}),
    ("p_honnete",   "L'honnetete est l'invariant : ne jamais confabuler une preuve",                          {"tier_status": "PREMIUM"}),
    # un PREMIUM DRAPOTE : etait certifie, mais une contradiction interne a ete signalee
    ("p_compact",   "Le compactage du chat conserve l'integralite des tours anciens sans aucune perte",        flag_contradiction({"tier_status": "PREMIUM"}, "internal_inference")),
    ("t_graphrag",  "Le GraphRAG retrouve les relations entre entites, pas seulement la similarite de texte", {"tier_status": "TAMPON"}),
    ("t_neuromorph","Un substrat neuromorphique abolirait le mur de von Neumann",                              {"tier_status": "TAMPON"}),
    ("c_dream",     "La consolidation onirique elague le bruit la nuit",                                        {"tier_status": "CHURN"}),
    ("c_pisano",    "La periode de Pisano donne le cycle de Fibonacci modulo n",                               {"tier_status": "CHURN"}),
    ("c_gpu",       "La RTX 5070 Ti est bridee a 250W",                                                        {"tier_status": "CHURN"}),
]
TEXT = {r[0]: r[1] for r in RAW}
META = {r[0]: validate_metadata(r[2]) for r in RAW}   # la douane force le marquage + derive le label
ids = list(TEXT.keys())

print("Chargement embedder multilingue...")
MODEL = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
emb = MODEL.encode([TEXT[i] for i in ids], normalize_embeddings=True)
EMB = {ids[i]: emb[i] for i in range(len(ids))}

def cos(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))

# graphe de voisinage (aretes de similarite)
SIM_EDGE = 0.40
ADJ = {i: [] for i in ids}
for a in range(len(ids)):
    for b in range(a + 1, len(ids)):
        if cos(emb[a], emb[b]) > SIM_EDGE:
            ADJ[ids[a]].append(ids[b]); ADJ[ids[b]].append(ids[a])

# --- LECTURE : la consigne systeme globale (legende, hybride) ---
CONSIGNE = (
    "INSTRUCTION DE LECTURE DU CONTEXTE (a respecter strictement) :\n"
    "  [CERTIFIE]                         -> savoir verifie, fiable.\n"
    "  [PISTE NON VERIFIEE]               -> hypothese exploratoire : NE PAS l'affirmer comme vraie, l'explorer avec prudence.\n"
    "  [CERTIFIE - CONTRADICTION SIGNALEE]-> etait fiable, mais une contradiction a ete signalee : a NUANCER, arbitrage en attente.\n"
)
ORDRE = {"[CERTIFIE]": 0, "[CERTIFIE - CONTRADICTION SIGNALEE]": 1, "[PISTE NON VERIFIEE]": 2, "[memoire courante]": 3}

def route_and_assemble(query, topk=2):
    qv = MODEL.encode([query], normalize_embeddings=True)[0]
    seeds = sorted(ids, key=lambda i: cos(qv, EMB[i]), reverse=True)[:topk]
    touched = set(seeds)
    for s in seeds:
        touched.update(ADJ[s])
    # ASSEMBLAGE : on LIT injected_label des metadonnees (pas de re-derivation !)
    lignes = sorted(touched, key=lambda i: ORDRE[META[i]["injected_label"]])
    bloc = [f"{META[i]['injected_label']} {TEXT[i]}" for i in lignes]
    return CONSIGNE + "\n--- SOURCES ASSEMBLEES ---\n" + "\n".join(bloc), touched

# ---------------------------------------------------------------------------
# DEMONSTRATION + MESURES
# ---------------------------------------------------------------------------
print("\n" + "=" * 72)
print("CHAINE A+B INTEGREE — la douane a marque chaque acquis a l'ecriture :")
for i in ids:
    print(f"  {i:14} {META[i]['tier_status']:8} flag={str(META[i]['is_flagged']):5} -> {META[i]['injected_label']}")
print("=" * 72)

q = "Le compactage des anciens messages garde-t-il vraiment toute l'integralite sans perte ?"
contexte, touched = route_and_assemble(q, topk=3)
print(f"\nREQUETE : {q}\n")
print(contexte)

# Verifications mesurables
print("\n--- VERIFICATIONS ---")
# 1. le label vient bien des metadonnees (pas re-calcule a la volee)
src_ok = all(META[i]["injected_label"] == derive_label(META[i]["tier_status"], META[i]["is_flagged"]) for i in ids)
print(f"  [1] Labels lus depuis la douane, coherents avec le schema : {src_ok}")
# 2. le premium drapote voyage avec son blame — UNIQUEMENT dans les sources (pas la legende)
sources_part = contexte.split("--- SOURCES ASSEMBLEES ---", 1)[1]
drapote_visible = any("CONTRADICTION SIGNALEE" in l for l in sources_part.splitlines())
print(f"  [2] Le PREMIUM drapote est RECUPERE et porte son doute dans les SOURCES : {drapote_visible}")
# 3. aucune piste tampon n'est presentee comme verite
pistes = [l for l in contexte.splitlines() if l.startswith("[PISTE")]
print(f"  [3] Pistes en quarantaine (etiquetees, jamais comme verite) : {len(pistes)}")
# 4. le premium drapote garde son tier (faille 2)
print(f"  [4] p_compact reste tier={META['p_compact']['tier_status']} malgre le flag (faille 2 respectee)")
print("\n" + "=" * 72)
